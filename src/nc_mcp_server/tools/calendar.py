"""Calendar tools — list calendars, query/create/update/delete events via CalDAV."""

import json
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from urllib.parse import unquote
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Calendar as ICal
from icalendar import Event as IEvent
from icalendar import vRecur
from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import DAV_NS, NextcloudError, find_ok_prop
from ..permissions import PermissionLevel, require_permission
from ..state import get_client, get_config

CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
APPLE_NS = "http://apple.com/ns/ical/"
CS_NS = "http://calendarserver.org/ns/"

CALENDAR_PROPFIND = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav"'
    ' xmlns:apple="http://apple.com/ns/ical/"'
    ' xmlns:cs="http://calendarserver.org/ns/"'
    ' xmlns:oc="http://owncloud.org/ns">'
    "<d:prop>"
    "<d:displayname/>"
    "<d:resourcetype/>"
    "<cal:supported-calendar-component-set/>"
    "<d:current-user-privilege-set/>"
    "<apple:calendar-color/>"
    "<cs:getctag/>"
    "</d:prop>"
    "</d:propfind>"
)

SKIP_CALENDARS = {"inbox", "outbox", "trashbin"}

# Marks events created through this server with the creating user's ID. update_event refuses
# events without the caller's mark (or with attendees) unless allow_foreign=true, so a client
# can ask its user before changing someone else's event.
CREATED_BY_PROP = "X-NC-MCP-CREATED-BY"
CONFERENCE_LABEL = "Video call"
VIDEO_LINE_PREFIX = "Video call: "


def _caldav_path(user: str, calendar_id: str = "", resource: str = "") -> str:
    path = f"calendars/{user}/"
    if calendar_id:
        path += f"{calendar_id}/"
    if resource:
        path += resource
    return path


def _href_to_dav_path(href: str) -> str:
    return href.split("/remote.php/dav/", 1)[-1] if "/remote.php/dav/" in href else href


def _build_event_query_xml(
    start: str | None = None,
    end: str | None = None,
    uid: str | None = None,
) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<cal:calendar-query xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">',
        "<d:prop><d:getetag/><cal:calendar-data/></d:prop>",
        '<cal:filter><cal:comp-filter name="VCALENDAR">',
        '<cal:comp-filter name="VEVENT">',
    ]
    if uid:
        escaped = xml_escape(uid)
        parts.append('<cal:prop-filter name="UID">')
        parts.append(f'<cal:text-match match-type="equals">{escaped}</cal:text-match>')
        parts.append("</cal:prop-filter>")
    if start and end:
        parts.append(f'<cal:time-range start="{xml_escape(start)}" end="{xml_escape(end)}"/>')
    parts.append("</cal:comp-filter></cal:comp-filter></cal:filter>")
    parts.append("</cal:calendar-query>")
    return "".join(parts)


def _el_text(prop: ET.Element, ns: str, tag: str) -> str | None:
    el = prop.find(f"{{{ns}}}{tag}")
    return el.text if el is not None and el.text else None


def _parse_calendar_entry(prop: ET.Element, cal_id: str) -> dict[str, Any]:
    components: list[str] = []
    comp_set = prop.find(f"{{{CALDAV_NS}}}supported-calendar-component-set")
    if comp_set is not None:
        for comp in comp_set.findall(f"{{{CALDAV_NS}}}comp"):
            name = comp.get("name", "")
            if name:
                components.append(name)

    writable = False
    privs = prop.find(f"{{{DAV_NS}}}current-user-privilege-set")
    if privs is not None:
        writable = any(p.tag == f"{{{DAV_NS}}}write" for p in privs.findall(f".//{{{DAV_NS}}}privilege/*"))

    return {
        "id": cal_id,
        "name": _el_text(prop, DAV_NS, "displayname") or cal_id,
        "color": _el_text(prop, APPLE_NS, "calendar-color"),
        "components": components,
        "writable": writable,
        "ctag": _el_text(prop, CS_NS, "getctag"),
    }


def _parse_calendars_xml(xml_text: str, user: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)  # noqa: S314
    calendars: list[dict[str, Any]] = []
    base_href = f"/remote.php/dav/calendars/{user}"

    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_el = response.find(f"{{{DAV_NS}}}href")
        if href_el is None or href_el.text is None:
            continue
        href = href_el.text.rstrip("/")
        if href == base_href:
            continue
        cal_id = href.rsplit("/", 1)[-1]
        if cal_id in SKIP_CALENDARS:
            continue

        prop = find_ok_prop(response)
        if prop is None:
            continue
        rt = prop.find(f"{{{DAV_NS}}}resourcetype")
        if rt is None or rt.find(f"{{{CALDAV_NS}}}calendar") is None:
            continue

        calendars.append(_parse_calendar_entry(prop, cal_id))

    return calendars


def _parse_report_xml(xml_text: str) -> list[tuple[str, str, str]]:
    """Parse a REPORT response. Returns list of (href, etag, ical_data)."""
    root = ET.fromstring(xml_text)  # noqa: S314
    results: list[tuple[str, str, str]] = []
    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_el = response.find(f"{{{DAV_NS}}}href")
        if href_el is None or href_el.text is None:
            continue
        href = href_el.text
        etag = ""
        ical_data = ""
        for propstat in response.findall(f"{{{DAV_NS}}}propstat"):
            prop = propstat.find(f"{{{DAV_NS}}}prop")
            if prop is None:
                continue
            etag_el = prop.find(f"{{{DAV_NS}}}getetag")
            if etag_el is not None and etag_el.text:
                etag = etag_el.text.strip('"')
            data_el = prop.find(f"{{{CALDAV_NS}}}calendar-data")
            if data_el is not None and data_el.text:
                ical_data = data_el.text
        if ical_data:
            results.append((href, etag, ical_data))
    return results


def _dt_to_str(dt: Any) -> str | None:
    """Convert an icalendar datetime/date to an ISO string."""
    if dt is None:
        return None
    val = dt.dt if hasattr(dt, "dt") else dt
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return str(val)


def _is_all_day(dt_prop: Any) -> bool:
    return dt_prop is not None and isinstance(dt_prop.dt, date) and not isinstance(dt_prop.dt, datetime)


def _format_event(ical_text: str, me: str | None = None) -> dict[str, Any]:
    """Parse iCalendar text and extract VEVENT fields into a dict.

    Attendee and organizer addresses are never returned, only whether there are attendees.
    With ``me`` (the caller's user ID) the result says whether the caller created the event here.
    """
    cal = ICal.from_ical(ical_text)
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        result: dict[str, Any] = {
            "uid": str(component.get("UID", "")),
            "summary": str(component.get("SUMMARY", "")),
            "dtstart": _dt_to_str(component.get("DTSTART")),
            "dtend": _dt_to_str(component.get("DTEND")),
            "description": str(component.get("DESCRIPTION", "")),
            "location": str(component.get("LOCATION", "")),
            "status": str(component.get("STATUS", "")),
            "all_day": _is_all_day(component.get("DTSTART")),
        }
        rrule = component.get("RRULE")
        if isinstance(rrule, vRecur) and rrule:
            result["rrule"] = rrule.to_ical().decode()
        if isinstance(component, IEvent) and component.categories:
            result["categories"] = [str(c) for c in component.categories]
        conference = _conference_url(component)
        if conference:
            result["conference"] = conference
        result["has_attendees"] = _has_attendees(component)
        if me is not None:
            result["created_by_me"] = _created_by(component) == me
        return result
    msg = "No VEVENT found in calendar data"
    raise ValueError(msg)


def _zone(name: str) -> ZoneInfo | None:
    """IANA zone for ``name``; None for ""."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown timezone '{name}'. Use an IANA name like 'Europe/Berlin'.") from None


def _effective_zone(timezone: str, fallback: tzinfo | None = None) -> tzinfo | None:
    """Zone for times without an offset: the timezone argument, else the fallback
    (the event's own zone on updates), else NEXTCLOUD_MCP_TIMEZONE, else None (UTC)."""
    if timezone:
        return _zone(timezone)
    if fallback is not None:
        return fallback
    return _zone(get_config().timezone)


def _parse_dt(value: str, all_day: bool = False, tz: tzinfo | None = None) -> date | datetime:
    """Parse an ISO date or datetime string.

    A datetime without an offset is wall time in ``tz`` (UTC without ``tz``). A datetime with an
    offset keeps its instant and is shown in ``tz`` when given, so the event carries that TZID.
    """
    if all_day or len(value) == 10:
        return date.fromisoformat(value)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz or UTC)
    return dt.astimezone(tz) if tz is not None else dt


def _zone_of(dt_prop: Any) -> tzinfo | None:
    """The IANA zone of an existing DTSTART (None for UTC, floating or all-day values)."""
    val = getattr(dt_prop, "dt", None)
    if isinstance(val, datetime) and isinstance(val.tzinfo, ZoneInfo) and val.tzinfo.key != "UTC":
        return val.tzinfo
    return None


def _to_caldav_utc(value: str, tz: tzinfo | None) -> str:
    """ISO date/datetime -> CalDAV UTC time (YYYYMMDDTHHMMSSZ) for time-range filters."""
    value = value.strip()
    try:
        if len(value) == 10:
            dt = datetime.combine(date.fromisoformat(value), time(), tzinfo=tz or UTC)
        else:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz or UTC)
    except ValueError:
        raise ValueError(f"Invalid date/time '{value}'. Use ISO 8601, e.g. 2026-04-01T00:00:00Z.") from None
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _created_by(component: Any) -> str:
    return str(component.get(CREATED_BY_PROP, "")).strip()


def _has_attendees(component: Any) -> bool:
    return component.get("ATTENDEE") is not None


def _conference_url(component: Any) -> str:
    conf = component.get("CONFERENCE")
    if conf is None:
        return ""
    if isinstance(conf, list):
        conf = conf[0] if conf else ""
    return str(conf)


def _validate_url(url: str) -> str:
    url = url.strip()
    if not re.fullmatch(r"https?://[^\s<>\"]+", url):
        raise ValueError(f"Invalid conference_url '{url}'. Expected an http(s) URL.")
    return url


def _build_ical(
    uid: str,
    summary: str,
    dtstart: date | datetime,
    dtend: date | datetime,
    description: str = "",
    location: str = "",
    status: str = "CONFIRMED",
    categories: list[str] | None = None,
    rrule: str = "",
    conference_url: str = "",
    created_by: str = "",
) -> str:
    """Build a minimal iCalendar VEVENT string (with VTIMEZONE for zoned times)."""
    cal = ICal()
    cal.add("prodid", "-//nc-mcp-server//EN")
    cal.add("version", "2.0")
    event = IEvent()
    event.add("uid", uid)
    event.add("dtstamp", datetime.now(UTC))
    event.add("dtstart", dtstart)
    event.add("dtend", dtend)
    event.add("summary", summary)
    if description:
        event.add("description", description)
    if location:
        event.add("location", location)
    if status:
        event.add("status", status)
    if categories:
        event.add("categories", categories)
    if rrule:
        event.add("rrule", _parse_rrule(rrule, dtstart))
    if conference_url:
        _set_conference(event, conference_url)
    if created_by:
        event.add(CREATED_BY_PROP, created_by)
    cal.add_component(event)
    cal.add_missing_timezones()
    return cal.to_ical().decode()


def _set_conference(component: Any, url: str) -> None:
    """Video link per RFC 7986 (CONFERENCE) plus where common clients show it: LOCATION when it
    is empty (as Nextcloud Calendar does for Talk rooms), else a line in DESCRIPTION."""
    old = _conference_url(component)
    _clear_conference(component, old)
    if not url:
        return
    component.add(
        "conference", url, parameters={"VALUE": "URI", "FEATURE": ["AUDIO", "VIDEO"], "LABEL": CONFERENCE_LABEL}
    )
    if not str(component.get("LOCATION", "")).strip():
        _set_prop(component, "LOCATION", url)
    else:
        desc = str(component.get("DESCRIPTION", "")).rstrip()
        line = f"{VIDEO_LINE_PREFIX}{url}"
        _set_prop(component, "DESCRIPTION", f"{desc}\n\n{line}" if desc else line)


def _clear_conference(component: Any, old: str) -> None:
    """Remove the video link this server set (CONFERENCE, LOCATION equal to it, its DESCRIPTION line)."""
    component.pop("CONFERENCE", None)
    if not old:
        return
    if str(component.get("LOCATION", "")).strip() == old:
        component.pop("LOCATION", None)
    desc = str(component.get("DESCRIPTION", ""))
    line = f"{VIDEO_LINE_PREFIX}{old}"
    if line in desc:
        _set_prop(component, "DESCRIPTION", desc.replace(line, "").strip(), clear_if_empty=True)


RRULE_FREQ = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
RRULE_BYDAY = re.compile(r"^([+-]?[1-9][0-9]?)?(MO|TU|WE|TH|FR|SA|SU)$")
RRULE_INT_RANGES = {
    "BYMONTHDAY": (-31, 31),
    "BYMONTH": (1, 12),
    "BYSETPOS": (-366, 366),
    "BYWEEKNO": (-53, 53),
    "BYYEARDAY": (-366, 366),
}
RRULE_MAX = 1000


def _rrule_int(key: str, raw: str, low: int, high: int, zero_ok: bool = False) -> int:
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"RRULE {key}={raw}: expected a number.") from None
    if n < low or n > high or (n == 0 and not zero_ok):
        raise ValueError(f"RRULE {key}={raw}: out of range {low}..{high}.")
    return n


def _rrule_until(raw: str, dtstart: date | datetime | None) -> date | datetime:
    """UNTIL as RFC 5545 wants it: a date for all-day events, else a UTC datetime. A value
    without offset is wall time in the event's zone; a date means the end of that day there."""
    compact = raw.strip()
    try:
        if re.fullmatch(r"\d{8}", compact):
            day = date(int(compact[:4]), int(compact[4:6]), int(compact[6:]))
            parsed: date | datetime = day
        elif re.fullmatch(r"\d{8}T\d{6}Z?", compact):
            parsed = datetime.strptime(compact[:15] + "+0000", "%Y%m%dT%H%M%S%z")
            if not compact.endswith("Z"):
                parsed = parsed.replace(tzinfo=None)
        elif len(compact) == 10:
            parsed = date.fromisoformat(compact)
        else:
            parsed = datetime.fromisoformat(compact)
    except ValueError:
        raise ValueError(f"RRULE UNTIL={raw}: expected a date like 20261231 or 20261231T235959Z.") from None
    if isinstance(dtstart, datetime):
        zone = dtstart.tzinfo or UTC
        if not isinstance(parsed, datetime):
            parsed = datetime.combine(parsed, time(23, 59, 59), tzinfo=zone)
        elif parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(UTC)
    if isinstance(dtstart, date):
        return parsed.date() if isinstance(parsed, datetime) else parsed
    return parsed


WEEKDAYS = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}


def _rrule_value(key: str, val: str, dtstart: date | datetime | None) -> list[Any]:
    """One checked RRULE part as icalendar wants it (a list of values)."""
    if key == "FREQ":
        if val.upper() not in RRULE_FREQ:
            raise ValueError(f"RRULE FREQ={val}: use one of {', '.join(sorted(RRULE_FREQ))}.")
        return [val.upper()]
    if key == "UNTIL":
        return [_rrule_until(val, dtstart)]
    if key in {"COUNT", "INTERVAL"}:
        return [_rrule_int(key, val, 1, RRULE_MAX)]
    if key == "BYDAY":
        days = [d.strip().upper() for d in val.split(",")]
        if not all(RRULE_BYDAY.match(d) for d in days):
            raise ValueError(f"RRULE BYDAY={val}: expected days like MO,WE or 1MO,-1FR.")
        return days
    if key in RRULE_INT_RANGES:
        low, high = RRULE_INT_RANGES[key]
        return [_rrule_int(key, v.strip(), low, high) for v in val.split(",")]
    if key == "WKST" and val.upper() in WEEKDAYS:
        return [val.upper()]
    raise ValueError(f"RRULE part '{key}={val}' is not supported.")


def _parse_rrule(rrule_str: str, dtstart: date | datetime | None = None) -> dict[str, list[Any]]:
    """Parse and check an RRULE like 'FREQ=WEEKLY;COUNT=4;BYDAY=MO,WE'.

    Only DAILY/WEEKLY/MONTHLY/YEARLY rules with known parts are accepted (no sub-daily
    frequencies, COUNT and INTERVAL at most 1000, never COUNT and UNTIL together).
    """
    text = rrule_str.strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    result: dict[str, list[Any]] = {}
    for part in filter(None, (p.strip() for p in text.split(";"))):
        if "=" not in part:
            raise ValueError(f"RRULE part '{part}' is not KEY=VALUE.")
        key, val = (x.strip() for x in part.split("=", 1))
        key = key.upper()
        if key in result:
            raise ValueError(f"RRULE {key} given twice.")
        result[key] = _rrule_value(key, val, dtstart)
    if "FREQ" not in result:
        raise ValueError("RRULE needs FREQ (DAILY, WEEKLY, MONTHLY or YEARLY).")
    if "COUNT" in result and "UNTIL" in result:
        raise ValueError("RRULE takes COUNT or UNTIL, not both.")
    return result


def _validate_status(status: str) -> str:
    valid = {"CONFIRMED", "TENTATIVE", "CANCELLED"}
    upper = status.upper()
    if upper not in valid:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid))}")
    return upper


def _set_prop(component: Any, name: str, value: Any, clear_if_empty: bool = False) -> None:
    component.pop(name, None)
    if clear_if_empty and not value:
        return
    component.add(name.lower(), value)


def _apply_event_updates(
    component: Any,
    summary: str | None,
    start: str | None,
    end: str | None,
    description: str | None,
    location: str | None,
    status: str | None,
    categories: list[str] | None = None,
    tz: tzinfo | None = None,
    rrule: str | None = None,
    conference_url: str | None = None,
) -> None:
    if summary is not None:
        _set_prop(component, "SUMMARY", summary)
    if start is not None or end is not None:
        _apply_times(component, start, end, tz)
    if description is not None:
        _set_prop(component, "DESCRIPTION", description, clear_if_empty=True)
    if location is not None:
        _set_prop(component, "LOCATION", location, clear_if_empty=True)
    if status is not None:
        _set_prop(component, "STATUS", status)
    if categories is not None:
        _set_prop(component, "CATEGORIES", categories, clear_if_empty=True)
    if rrule is not None:
        start_prop = component.get("DTSTART")
        _set_prop(
            component,
            "RRULE",
            _parse_rrule(rrule, start_prop.dt if start_prop else None) if rrule else "",
            clear_if_empty=True,
        )
    if conference_url is not None:
        _set_conference(component, _validate_url(conference_url) if conference_url else "")
    _set_prop(component, "DTSTAMP", datetime.now(UTC))


def _apply_times(component: Any, start: str | None, end: str | None, tz: tzinfo | None) -> None:
    if start is not None:
        old_start, old_end = component.get("DTSTART"), component.get("DTEND")
        new_start = _parse_dt(start, _is_all_day(old_start), tz)
        _set_prop(component, "DTSTART", new_start)
        # Moving only the start keeps the duration (DTEND follows), as calendar apps do.
        if end is None and old_start is not None and old_end is not None and type(old_start.dt) is type(new_start):
            _set_prop(component, "DTEND", new_start + (old_end.dt - old_start.dt))
    if end is not None:
        ref = component.get("DTEND") or component.get("DTSTART")
        _set_prop(component, "DTEND", _parse_dt(end, _is_all_day(ref), tz))


def _check_summary(component: Any, expected_summary: str, event_uid: str) -> None:
    """Guard against acting on the wrong event: the title must match when one is expected."""
    if not expected_summary:
        return
    actual = str(component.get("SUMMARY", ""))
    if actual.strip().casefold() != expected_summary.strip().casefold():
        raise ValueError(f"Event '{event_uid}' is titled '{actual}', not '{expected_summary}'. Nothing was changed.")


def _vevent(cal: Any) -> Any:
    for component in cal.walk():
        if component.name == "VEVENT":
            return component
    raise ValueError("No VEVENT found in calendar data")


async def _find_event(calendar_id: str, event_uid: str) -> tuple[str, str, str]:
    """Find an event by UID. Returns (href, etag, ical_data) or raises."""
    client = get_client()
    user = get_config().user
    path = _caldav_path(user, calendar_id)
    body = _build_event_query_xml(uid=event_uid)
    response = await client.dav_request(
        "REPORT",
        path,
        body=body,
        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        context=f"Find event '{event_uid}' in '{calendar_id}'",
    )
    results = _parse_report_xml(response.text or "")
    if not results:
        raise NextcloudError(f"Event '{event_uid}' not found in calendar '{calendar_id}'", 404)
    return results[0]


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_calendars() -> str:
        """List all calendars for the current user.

        Returns calendars with their properties including name, color,
        supported component types (VEVENT, VTODO), and write access status.

        Returns:
            JSON list of calendar objects with: id, name, color, components, writable, ctag.
            Use the id value with other calendar tools (e.g. "personal").
        """
        client = get_client()
        user = get_config().user
        path = _caldav_path(user)
        response = await client.dav_request(
            "PROPFIND",
            path,
            body=CALENDAR_PROPFIND,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context="List calendars",
        )
        calendars = _parse_calendars_xml(response.text or "", user)
        return json.dumps(calendars)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_events(
        calendar_id: str = "personal",
        start: str = "",
        end: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Get events from a calendar, optionally filtered by time range.

        Without start/end, returns all events in the calendar.
        With start and end, returns only events overlapping that range
        (including recurring event instances).

        Args:
            calendar_id: Calendar identifier (default "personal"). Use list_calendars to find IDs.
            start: Optional range start in ISO 8601, e.g. "2026-04-01T00:00:00Z" or
                   "2026-04-01T00:00:00+02:00". Without an offset the server time zone
                   (NEXTCLOUD_MCP_TIMEZONE, else UTC) applies. Required if end is provided.
            end: Optional range end, same format. Required if start is provided.
            limit: Maximum number of events to return (1-500, default 50).
            offset: Number of events to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of event objects) and "pagination"
            (count, offset, limit, has_more).
        """
        if bool(start) != bool(end):
            raise ValueError("Both start and end are required for time-range filtering, or omit both.")
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        zone = _effective_zone("")
        caldav_start = _to_caldav_utc(start, zone) if start else None
        caldav_end = _to_caldav_utc(end, zone) if end else None

        client = get_client()
        user = get_config().user
        path = _caldav_path(user, calendar_id)
        body = _build_event_query_xml(start=caldav_start, end=caldav_end)
        response = await client.dav_request(
            "REPORT",
            path,
            body=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context=f"Get events from '{calendar_id}'",
        )
        results = _parse_report_xml(response.text or "")
        all_events = []
        for _href, etag, ical_data in results:
            event = _format_event(ical_data, user)
            event["etag"] = etag
            all_events.append(event)
        page = all_events[offset : offset + limit]
        has_more = offset + limit < len(all_events)

        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_event(calendar_id: str, event_uid: str) -> str:
        """Get full details of a specific calendar event by its UID.

        Args:
            calendar_id: Calendar identifier (e.g. "personal").
            event_uid: The event's UID. Use get_events to find UIDs.

        Returns:
            JSON object with full event details: uid, summary, dtstart, dtend,
            description, location, status, all_day, has_attendees, created_by_me, etag,
            and optionally rrule, categories, conference (video link).
        """
        _href, etag, ical_data = await _find_event(calendar_id, event_uid)
        event = _format_event(ical_data, get_config().user)
        event["etag"] = etag
        return json.dumps(event)


def _register_create_event(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_event(
        calendar_id: str,
        summary: str,
        start: str,
        end: str = "",
        all_day: bool = False,
        description: str = "",
        location: str = "",
        status: str = "CONFIRMED",
        categories: str = "",
        rrule: str = "",
        timezone: str = "",
        conference_url: str = "",
    ) -> str:
        """Create a new calendar event.

        Args:
            calendar_id: Calendar identifier (e.g. "personal").
            summary: Event title/summary.
            start: Start date or datetime in ISO 8601 format.
                   For timed events: "2026-04-01T10:00:00Z" or "2026-04-01T10:00:00"
                   (without offset: wall time in `timezone`, else the server time zone, else UTC).
                   For all-day events: "2026-04-01".
            end: End date or datetime. Optional — defaults to 1 hour after start
                 for timed events, or next day for all-day events.
            all_day: Set to true for an all-day event. When true, start/end are dates only.
            description: Optional event description/notes.
            location: Optional event location.
            status: Event status: "CONFIRMED" (default), "TENTATIVE", or "CANCELLED".
            categories: Optional comma-separated category names (e.g. "Work,Meeting").
            rrule: Optional recurrence rule in iCalendar RRULE format.
                   Examples: "FREQ=DAILY;COUNT=5", "FREQ=WEEKLY;BYDAY=MO,WE,FR",
                   "FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20261231T235959Z".
                   Only DAILY/WEEKLY/MONTHLY/YEARLY; COUNT or UNTIL, not both.
            timezone: Optional IANA time zone (e.g. "Europe/Berlin") for the event. Times are
                   stored with this TZID, so recurring events keep their wall time across
                   daylight saving changes. Default: NEXTCLOUD_MCP_TIMEZONE, else UTC.
            conference_url: Optional video call link (e.g. a Talk room URL). Stored as
                   CONFERENCE (RFC 7986) and as LOCATION when no location is given,
                   otherwise as a line in the description.

        Returns:
            JSON object with the created event's uid, summary, dtstart, dtend and,
            when set, conference.
        """
        status_upper = _validate_status(status)
        cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else None
        zone = _effective_zone(timezone)
        conference = _validate_url(conference_url) if conference_url else ""
        dtstart = _parse_dt(start, all_day, zone)
        if end:
            dtend = _parse_dt(end, all_day, zone)
        elif all_day or not isinstance(dtstart, datetime):
            dtend = dtstart + timedelta(days=1)
        else:
            dtend = dtstart + timedelta(hours=1)

        if type(dtend) is type(dtstart) and dtend < dtstart:
            raise ValueError("The event ends before it starts.")

        uid = str(uuid.uuid4())
        client = get_client()
        user = get_config().user
        ical_data = _build_ical(
            uid,
            summary,
            dtstart,
            dtend,
            description,
            location,
            status_upper,
            cat_list,
            rrule,
            conference,
            created_by=user,
        )
        path = _caldav_path(user, calendar_id, f"{uid}.ics")
        await client.dav_request(
            "PUT",
            path,
            body=ical_data,
            headers={"Content-Type": "text/calendar; charset=utf-8"},
            context=f"Create event in '{calendar_id}'",
        )
        result: dict[str, Any] = {
            "uid": uid,
            "summary": summary,
            "dtstart": _dt_to_str(dtstart),
            "dtend": _dt_to_str(dtend),
        }
        if conference:
            result["conference"] = conference
        return json.dumps(result)


def _register_update_event(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_event(
        calendar_id: str,
        event_uid: str,
        summary: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        status: str | None = None,
        categories: str | None = None,
        rrule: str | None = None,
        timezone: str = "",
        conference_url: str | None = None,
        allow_foreign: bool = False,
        expected_summary: str = "",
    ) -> str:
        """Update an existing calendar event. Only provided fields are changed.

        Uses the event's ETag for safe concurrent updates — if the event was
        modified since it was last read, the update will fail with a conflict error.

        Events that were not created by the calling user through this server, or that
        have attendees (who Nextcloud would notify), are only changed with
        allow_foreign=true. Ask the user before passing it.

        Args:
            calendar_id: Calendar identifier (e.g. "personal").
            event_uid: The event's UID to update. Use get_events to find UIDs.
            summary: New event title.
            start: New start date/datetime in ISO 8601 format. Without `end` the
                   event keeps its duration.
            end: New end date/datetime in ISO 8601 format.
            description: New description. Pass "" to clear.
            location: New location. Pass "" to clear.
            status: New status: "CONFIRMED", "TENTATIVE", or "CANCELLED".
            categories: New categories as comma-separated string. Pass "" to clear.
            rrule: New recurrence rule (see create_event). Pass "" to end the recurrence.
            timezone: IANA zone for new start/end without offset. Default: the event's
                   own zone, else NEXTCLOUD_MCP_TIMEZONE, else UTC.
            conference_url: New video call link. Pass "" to remove the link set before.
            allow_foreign: Also change events created by someone else (or with attendees).
            expected_summary: Optional current title; the update is refused if it differs
                   (protects against changing the wrong event).

        Returns:
            Confirmation message with the updated event UID.
        """
        validated_status = _validate_status(status) if status is not None else None
        cat_list: list[str] | None = None
        if categories is not None:
            cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else []
        href, etag, ical_data = await _find_event(calendar_id, event_uid)
        cal = ICal.from_ical(ical_data)
        component = _vevent(cal)
        _check_summary(component, expected_summary, event_uid)
        me = get_config().user
        if not allow_foreign and (_created_by(component) != me or _has_attendees(component)):
            raise ValueError(
                f"Event '{event_uid}' was not created by you through this server, or it has attendees "
                "who would be notified. Nothing was changed. Ask the user, then call update_event "
                "again with allow_foreign=true."
            )
        zone = _effective_zone(timezone, _zone_of(component.get("DTSTART")))
        _apply_event_updates(
            component,
            summary,
            start,
            end,
            description,
            location,
            validated_status,
            cat_list,
            zone,
            rrule,
            conference_url,
        )
        new_start, new_end = component.get("DTSTART"), component.get("DTEND")
        if (
            new_start is not None
            and new_end is not None
            and type(new_start.dt) is type(new_end.dt)
            and new_end.dt < new_start.dt
        ):
            raise ValueError("The event would end before it starts. Nothing was changed.")
        cal.add_missing_timezones()

        client = get_client()
        await client.dav_request(
            "PUT",
            _href_to_dav_path(href),
            body=cal.to_ical().decode(),
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-Match": f'"{etag}"'},
            context=f"Update event '{event_uid}'",
        )
        return f"Event '{event_uid}' updated."


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_event(calendar_id: str, event_uid: str, expected_summary: str = "") -> str:
        """Delete a calendar event by its UID.

        The event is moved to the calendar trashbin and can be restored
        from the Nextcloud web interface within the retention period.

        Args:
            calendar_id: Calendar identifier (e.g. "personal").
            event_uid: The event's UID to delete. Use get_events to find UIDs.
            expected_summary: Optional current title; nothing is deleted if it differs
                   (protects against deleting the wrong event).

        Returns:
            Confirmation message.
        """
        href, _etag, ical_data = await _find_event(calendar_id, event_uid)
        _check_summary(_vevent(ICal.from_ical(ical_data)), expected_summary, event_uid)
        client = get_client()
        await client.dav_request("DELETE", _href_to_dav_path(href), context=f"Delete event '{event_uid}'")
        return f"Event '{event_uid}' deleted."


CAL_ID_RE = re.compile(r"^[A-Za-z0-9_@-][A-Za-z0-9_.@' -]{0,199}$")
SHARE_TYPES = {"user": "users", "group": "groups"}
SHARE_WITH_RE = re.compile(r"^[A-Za-z0-9_.@' -]{1,64}$")


def _check_calendar_id(calendar_id: str) -> str:
    if not CAL_ID_RE.match(calendar_id) or ".." in calendar_id or calendar_id in SKIP_CALENDARS:
        raise ValueError(f"Invalid calendar_id '{calendar_id}'.")
    return calendar_id


def _slug(name: str) -> str:
    """URI for a new calendar, like Nextcloud Calendar derives it from the name."""
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})
    slug = re.sub(r"[^a-z0-9]+", "-", name.translate(table).lower()).strip("-")
    return (slug or "calendar")[:60]


def _share_body(principal: str, write: bool | None) -> str:
    if write is None:
        inner = f"<o:remove><d:href>principal:{xml_escape(principal)}</d:href></o:remove>"
    else:
        access = "<o:read-write/>" if write else ""
        inner = f"<o:set><d:href>principal:{xml_escape(principal)}</d:href>{access}</o:set>"
    return f'<?xml version="1.0" encoding="UTF-8"?><o:share xmlns:d="DAV:" xmlns:o="http://owncloud.org/ns">{inner}</o:share>'


def _principal(share_with: str, share_type: str) -> str:
    kind = SHARE_TYPES.get(share_type.strip().lower())
    if kind is None:
        raise ValueError("share_type must be 'user' or 'group'.")
    if not SHARE_WITH_RE.match(share_with.strip()):
        raise ValueError(f"Invalid share_with '{share_with}'.")
    return f"principals/{kind}/{share_with.strip()}"


SHARES_PROPFIND = (
    '<?xml version="1.0" encoding="UTF-8"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"'
    ' xmlns:cs="http://calendarserver.org/ns/"><d:prop><d:displayname/><oc:owner-principal/><oc:invite/>'
    "<cs:publish-url/></d:prop></d:propfind>"
)
OC_NS = "http://owncloud.org/ns"


async def _calendar_info(calendar_id: str) -> dict[str, Any]:
    """Name, owner, shares and public URL of one calendar (shares only visible to the owner)."""
    client = get_client()
    user = get_config().user
    # Depth 1 on the calendar home: Nextcloud returns owner-principal only there, not with
    # Depth 0 on the calendar itself (seen on Nextcloud 34).
    response = await client.dav_request(
        "PROPFIND",
        _caldav_path(user),
        body=SHARES_PROPFIND,
        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        context=f"Calendar '{calendar_id}'",
    )
    root = ET.fromstring(response.text or "")  # noqa: S314
    prop = None
    for resp in root.findall(f"{{{DAV_NS}}}response"):
        href = resp.find(f"{{{DAV_NS}}}href")
        if href is None or not href.text or unquote(href.text.rstrip("/").rsplit("/", 1)[-1]) != calendar_id:
            continue
        prop = find_ok_prop(resp)
        break
    if prop is None:
        raise NextcloudError(f"Calendar '{calendar_id}' not found", 404)
    owner = (_el_text(prop, OC_NS, "owner-principal") or "").rsplit("/", 1)[-1]
    shares = []
    invite = prop.find(f"{{{OC_NS}}}invite")
    if invite is not None:
        for entry in invite.findall(f"{{{OC_NS}}}user"):
            href = entry.find(f"{{{DAV_NS}}}href")
            principal = (href.text or "").removeprefix("principal:") if href is not None else ""
            kind, _, name = principal.removeprefix("principals/").partition("/")
            write = entry.find(f"{{{OC_NS}}}access/{{{OC_NS}}}read-write") is not None
            shares.append({"type": "group" if kind == "groups" else "user", "share_with": name, "write": write})
    publish = prop.find(f"{{{CS_NS}}}publish-url")
    public_url = None
    if publish is not None:
        href = publish.find(f"{{{DAV_NS}}}href")
        public_url = href.text if href is not None and href.text else None
    return {
        "id": calendar_id,
        "name": _el_text(prop, DAV_NS, "displayname") or calendar_id,
        "owner": owner,
        "owned_by_me": owner == user,
        "shares": shares if owner == user else None,
        "public_url": public_url,
    }


def _register_calendar_admin(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_calendar_shares(calendar_id: str) -> str:
        """Who a calendar is shared with and whether it has a public link.

        Shares are only visible to the calendar's owner (null for everyone else).

        Args:
            calendar_id: Calendar identifier from list_calendars.

        Returns:
            JSON with id, name, owner, owned_by_me, shares ([{type, share_with, write}] or null), public_url.
        """
        return json.dumps(await _calendar_info(_check_calendar_id(calendar_id)))

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_calendar(name: str, color: str = "", share_with_group: str = "", group_write: bool = False) -> str:
        """Create a new calendar (events only) for the current user, optionally shared with a group.

        Args:
            name: Display name, e.g. "Summer party 2027".
            color: Optional color as #RRGGBB.
            share_with_group: Optional Nextcloud group ID to share the new calendar with right away.
            group_write: Whether that group may also add and change events (default: read only).

        Returns:
            JSON with id (use it as calendar_id), name and, when shared, the share.
        """
        name = name.strip()
        if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise ValueError("Calendar name must be 1-100 characters without control characters.")
        if color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValueError("color must look like #1E78C1.")
        principal = _principal(share_with_group, "group") if share_with_group else ""
        client = get_client()
        user = get_config().user
        existing = {
            c["id"]
            for c in _parse_calendars_xml(
                (
                    await client.dav_request(
                        "PROPFIND",
                        _caldav_path(user),
                        body=CALENDAR_PROPFIND,
                        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                        context="List calendars",
                    )
                ).text
                or "",
                user,
            )
        }
        base = _slug(name)
        uri = base
        n = 2
        while uri in existing or uri in SKIP_CALENDARS:
            uri = f"{base}-{n}"
            n += 1
        color_prop = f"<x:calendar-color>{color}</x:calendar-color>" if color else ""
        body = (
            '<?xml version="1.0" encoding="UTF-8"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"'
            ' xmlns:x="http://apple.com/ns/ical/"><d:set><d:prop>'
            f"<d:displayname>{xml_escape(name)}</d:displayname>{color_prop}"
            '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'
            "</d:prop></d:set></c:mkcalendar>"
        )
        # 405: the URI is taken, e.g. by a deleted calendar still in the trash bin -> next suffix.
        for _attempt in range(10):
            try:
                await client.dav_request(
                    "MKCALENDAR",
                    _caldav_path(user, uri),
                    body=body,
                    headers={"Content-Type": "application/xml; charset=utf-8"},
                    context=f"Create calendar '{name}'",
                )
                break
            except NextcloudError as err:
                if err.status_code != 405:
                    raise
                uri = f"{base}-{n}"
                n += 1
        else:
            raise NextcloudError(f"No free URI for calendar '{name}'", 409)
        result: dict[str, Any] = {"id": uri, "name": name}
        if principal:
            await client.dav_request(
                "POST",
                _caldav_path(user, uri),
                body=_share_body(principal, group_write),
                headers={"Content-Type": "application/xml; charset=utf-8"},
                context=f"Share calendar '{uri}'",
            )
            result["share"] = {"type": "group", "share_with": share_with_group.strip(), "write": group_write}
        return json.dumps(result)


def _register_calendar_sharing(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def share_calendar(calendar_id: str, share_with: str, share_type: str = "user", write: bool = False) -> str:
        """Share one of your calendars with a user or group of this Nextcloud, or change their rights.

        Args:
            calendar_id: Your calendar's identifier (only the owner can share).
            share_with: User ID or group ID.
            share_type: "user" or "group".
            write: Whether they may add and change events (default: read only).

        Returns:
            Confirmation message.
        """
        principal = _principal(share_with, share_type)
        await get_client().dav_request(
            "POST",
            _caldav_path(get_config().user, _check_calendar_id(calendar_id)),
            body=_share_body(principal, write),
            headers={"Content-Type": "application/xml; charset=utf-8"},
            context=f"Share calendar '{calendar_id}'",
        )
        rights = "read-write" if write else "read"
        return f"Calendar '{calendar_id}' shared with {share_type} '{share_with.strip()}' ({rights})."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.WRITE)
    async def unshare_calendar(calendar_id: str, share_with: str, share_type: str = "user") -> str:
        """Stop sharing one of your calendars with a user or group.

        Args:
            calendar_id: Your calendar's identifier.
            share_with: User ID or group ID.
            share_type: "user" or "group".

        Returns:
            Confirmation message.
        """
        principal = _principal(share_with, share_type)
        await get_client().dav_request(
            "POST",
            _caldav_path(get_config().user, _check_calendar_id(calendar_id)),
            body=_share_body(principal, None),
            headers={"Content-Type": "application/xml; charset=utf-8"},
            context=f"Unshare calendar '{calendar_id}'",
        )
        return f"Calendar '{calendar_id}' no longer shared with {share_type} '{share_with.strip()}'."

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def publish_calendar(calendar_id: str) -> str:
        """Create a public read-only link for one of your calendars (anyone with the link can read it).

        Args:
            calendar_id: Your calendar's identifier.

        Returns:
            JSON with id and public_url.
        """
        calendar_id = _check_calendar_id(calendar_id)
        await get_client().dav_request(
            "POST",
            _caldav_path(get_config().user, calendar_id),
            body='<?xml version="1.0" encoding="UTF-8"?><cs:publish-calendar xmlns:cs="http://calendarserver.org/ns/"/>',
            headers={"Content-Type": "application/xml; charset=utf-8"},
            context=f"Publish calendar '{calendar_id}'",
        )
        info = await _calendar_info(calendar_id)
        return json.dumps({"id": calendar_id, "public_url": info["public_url"]})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.WRITE)
    async def unpublish_calendar(calendar_id: str) -> str:
        """Remove the public link of one of your calendars.

        Args:
            calendar_id: Your calendar's identifier.

        Returns:
            Confirmation message.
        """
        calendar_id = _check_calendar_id(calendar_id)
        await get_client().dav_request(
            "POST",
            _caldav_path(get_config().user, calendar_id),
            body='<?xml version="1.0" encoding="UTF-8"?><cs:unpublish-calendar xmlns:cs="http://calendarserver.org/ns/"/>',
            headers={"Content-Type": "application/xml; charset=utf-8"},
            context=f"Unpublish calendar '{calendar_id}'",
        )
        return f"Public link of calendar '{calendar_id}' removed."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_calendar(calendar_id: str, expected_name: str = "") -> str:
        """Delete one of your calendars with all its events (Nextcloud keeps it in the trash bin
        for a while). For a calendar shared with you, this removes it from your account only.

        Args:
            calendar_id: Calendar identifier.
            expected_name: Optional current display name; nothing is deleted if it differs.

        Returns:
            Confirmation message.
        """
        calendar_id = _check_calendar_id(calendar_id)
        if expected_name:
            info = await _calendar_info(calendar_id)
            names = {
                info["name"].strip().casefold(),
                info["name"].removesuffix(f" ({info['owner']})").strip().casefold(),
            }
            if expected_name.strip().casefold() not in names:
                raise ValueError(
                    f"Calendar '{calendar_id}' is named '{info['name']}', not '{expected_name}'. Nothing was deleted."
                )
        await get_client().dav_request(
            "DELETE", _caldav_path(get_config().user, calendar_id), context=f"Delete calendar '{calendar_id}'"
        )
        return f"Calendar '{calendar_id}' deleted."


def register(mcp: FastMCP) -> None:
    """Register calendar tools with the MCP server."""
    _register_read_tools(mcp)
    _register_create_event(mcp)
    _register_update_event(mcp)
    _register_destructive_tools(mcp)
    _register_calendar_admin(mcp)
    _register_calendar_sharing(mcp)
