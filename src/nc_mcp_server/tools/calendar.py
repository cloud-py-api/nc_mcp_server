"""Calendar tools — list calendars, query/create/update/delete events via CalDAV."""

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from icalendar import Calendar as ICal
from icalendar import Event as IEvent
from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import DAV_NS, NextcloudError
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
        parts.append(f"<cal:text-match>{escaped}</cal:text-match>")
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

        propstat = response.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
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


def _format_event(ical_text: str) -> dict[str, Any]:
    """Parse iCalendar text and extract VEVENT fields into a dict."""
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
        if component.get("RRULE"):
            result["rrule"] = component["RRULE"].to_ical().decode()
        if component.get("CATEGORIES"):
            cats = component["CATEGORIES"]
            if isinstance(cats, list):
                result["categories"] = [str(c) for group in cats for c in group.cats]
            else:
                result["categories"] = [str(c) for c in cats.cats]
        return result
    msg = "No VEVENT found in calendar data"
    raise ValueError(msg)


def _parse_dt(value: str, all_day: bool = False) -> date | datetime:
    """Parse an ISO date or datetime string."""
    if all_day or len(value) == 10:
        return date.fromisoformat(value)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


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
) -> str:
    """Build a minimal iCalendar VEVENT string."""
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
        event.add("rrule", _parse_rrule(rrule))
    cal.add_component(event)
    return cal.to_ical().decode()


def _parse_rrule(rrule_str: str) -> dict[str, list[Any]]:
    """Parse an RRULE string like 'FREQ=WEEKLY;COUNT=4;BYDAY=MO,WE' into a dict."""
    result: dict[str, list[Any]] = {}
    for part in rrule_str.split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip()
        if key == "UNTIL":
            result[key] = [datetime.fromisoformat(val.strip())]
        elif key in {"COUNT", "INTERVAL"}:
            result[key] = [int(val.strip())]
        else:
            result[key] = [v.strip() for v in val.split(",")]
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
) -> None:
    if summary is not None:
        _set_prop(component, "SUMMARY", summary)
    if start is not None:
        _set_prop(component, "DTSTART", _parse_dt(start, _is_all_day(component.get("DTSTART"))))
    if end is not None:
        ref = component.get("DTEND") or component.get("DTSTART")
        _set_prop(component, "DTEND", _parse_dt(end, _is_all_day(ref)))
    if description is not None:
        _set_prop(component, "DESCRIPTION", description, clear_if_empty=True)
    if location is not None:
        _set_prop(component, "LOCATION", location, clear_if_empty=True)
    if status is not None:
        _set_prop(component, "STATUS", status)
    if categories is not None:
        _set_prop(component, "CATEGORIES", categories, clear_if_empty=True)
    _set_prop(component, "DTSTAMP", datetime.now(UTC))


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
    ) -> str:
        """Get events from a calendar, optionally filtered by time range.

        Without start/end, returns all events in the calendar.
        With start and end, returns only events overlapping that range
        (including recurring event instances).

        Args:
            calendar_id: Calendar identifier (default "personal"). Use list_calendars to find IDs.
            start: Optional range start in ISO 8601 UTC format: "2026-04-01T00:00:00Z".
                   Required if end is provided.
            end: Optional range end in ISO 8601 UTC format: "2026-04-30T23:59:59Z".
                 Required if start is provided.

        Returns:
            JSON list of event objects with: uid, summary, dtstart, dtend, location,
            description, status, all_day, and optionally rrule and categories.
        """
        if bool(start) != bool(end):
            raise ValueError("Both start and end are required for time-range filtering, or omit both.")
        caldav_start = start.replace("-", "").replace(":", "").replace(".", "") if start else None
        caldav_end = end.replace("-", "").replace(":", "").replace(".", "") if end else None
        if caldav_start:
            caldav_start = caldav_start[:15] + "Z" if not caldav_start.endswith("Z") else caldav_start
        if caldav_end:
            caldav_end = caldav_end[:15] + "Z" if not caldav_end.endswith("Z") else caldav_end

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
        events = []
        for _href, etag, ical_data in results:
            event = _format_event(ical_data)
            event["etag"] = etag
            events.append(event)
        return json.dumps(events)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_event(calendar_id: str, event_uid: str) -> str:
        """Get full details of a specific calendar event by its UID.

        Args:
            calendar_id: Calendar identifier (e.g. "personal").
            event_uid: The event's UID. Use get_events to find UIDs.

        Returns:
            JSON object with full event details: uid, summary, dtstart, dtend,
            description, location, status, all_day, etag, and optionally rrule, categories.
        """
        _href, etag, ical_data = await _find_event(calendar_id, event_uid)
        event = _format_event(ical_data)
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
    ) -> str:
        """Create a new calendar event.

        Args:
            calendar_id: Calendar identifier (e.g. "personal").
            summary: Event title/summary.
            start: Start date or datetime in ISO 8601 format.
                   For timed events: "2026-04-01T10:00:00Z" or "2026-04-01T10:00:00".
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

        Returns:
            JSON object with the created event's uid and summary.
        """
        status_upper = _validate_status(status)
        cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else None
        dtstart = _parse_dt(start, all_day)
        if end:
            dtend = _parse_dt(end, all_day)
        elif all_day or not isinstance(dtstart, datetime):
            dtend = dtstart + timedelta(days=1)
        else:
            dtend = dtstart + timedelta(hours=1)

        uid = str(uuid.uuid4())
        ical_data = _build_ical(uid, summary, dtstart, dtend, description, location, status_upper, cat_list, rrule)
        client = get_client()
        user = get_config().user
        path = _caldav_path(user, calendar_id, f"{uid}.ics")
        await client.dav_request(
            "PUT",
            path,
            body=ical_data,
            headers={"Content-Type": "text/calendar; charset=utf-8"},
            context=f"Create event in '{calendar_id}'",
        )
        return json.dumps({"uid": uid, "summary": summary})


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
    ) -> str:
        """Update an existing calendar event. Only provided fields are changed.

        Uses the event's ETag for safe concurrent updates — if the event was
        modified since it was last read, the update will fail with a conflict error.

        Args:
            calendar_id: Calendar identifier (e.g. "personal").
            event_uid: The event's UID to update. Use get_events to find UIDs.
            summary: New event title.
            start: New start date/datetime in ISO 8601 format.
            end: New end date/datetime in ISO 8601 format.
            description: New description. Pass "" to clear.
            location: New location. Pass "" to clear.
            status: New status: "CONFIRMED", "TENTATIVE", or "CANCELLED".
            categories: New categories as comma-separated string. Pass "" to clear.

        Returns:
            Confirmation message with the updated event UID.
        """
        validated_status = _validate_status(status) if status is not None else None
        cat_list: list[str] | None = None
        if categories is not None:
            cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else []
        href, etag, ical_data = await _find_event(calendar_id, event_uid)
        cal = ICal.from_ical(ical_data)
        for component in cal.walk():
            if component.name == "VEVENT":
                _apply_event_updates(component, summary, start, end, description, location, validated_status, cat_list)
                break

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
    async def delete_event(calendar_id: str, event_uid: str) -> str:
        """Delete a calendar event by its UID.

        The event is moved to the calendar trashbin and can be restored
        from the Nextcloud web interface within the retention period.

        Args:
            calendar_id: Calendar identifier (e.g. "personal").
            event_uid: The event's UID to delete. Use get_events to find UIDs.

        Returns:
            Confirmation message.
        """
        href, _etag, _ical = await _find_event(calendar_id, event_uid)
        client = get_client()
        await client.dav_request("DELETE", _href_to_dav_path(href), context=f"Delete event '{event_uid}'")
        return f"Event '{event_uid}' deleted."


def register(mcp: FastMCP) -> None:
    """Register calendar tools with the MCP server."""
    _register_read_tools(mcp)
    _register_create_event(mcp)
    _register_update_event(mcp)
    _register_destructive_tools(mcp)
