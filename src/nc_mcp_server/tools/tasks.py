"""Tasks tools — list task lists, query/create/update/complete/delete tasks via CalDAV."""

import json
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from icalendar import Calendar as ICal
from icalendar import Todo as ITodo
from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import DAV_NS, NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client, get_config

CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
APPLE_NS = "http://apple.com/ns/ical/"
CS_NS = "http://calendarserver.org/ns/"

TASK_LIST_PROPFIND = (
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

SKIP_COLLECTIONS = {"inbox", "outbox", "trashbin"}

VALID_STATUSES = {"NEEDS-ACTION", "IN-PROCESS", "COMPLETED", "CANCELLED"}


def _caldav_path(user: str, list_id: str = "", resource: str = "") -> str:
    path = f"calendars/{user}/"
    if list_id:
        path += f"{list_id}/"
    if resource:
        path += resource
    return path


def _href_to_dav_path(href: str) -> str:
    return href.split("/remote.php/dav/", 1)[-1] if "/remote.php/dav/" in href else href


def _el_text(prop: ET.Element, ns: str, tag: str) -> str | None:
    el = prop.find(f"{{{ns}}}{tag}")
    return el.text if el is not None and el.text else None


def _parse_task_list_entry(prop: ET.Element, list_id: str) -> dict[str, Any] | None:
    """Parse a single CalDAV response into a task list dict, or None if not a VTODO list."""
    components: list[str] = []
    comp_set = prop.find(f"{{{CALDAV_NS}}}supported-calendar-component-set")
    if comp_set is not None:
        for comp in comp_set.findall(f"{{{CALDAV_NS}}}comp"):
            name = comp.get("name", "")
            if name:
                components.append(name)
    if "VTODO" not in components:
        return None

    writable = False
    privs = prop.find(f"{{{DAV_NS}}}current-user-privilege-set")
    if privs is not None:
        writable = any(p.tag == f"{{{DAV_NS}}}write" for p in privs.findall(f".//{{{DAV_NS}}}privilege/*"))

    return {
        "id": list_id,
        "name": _el_text(prop, DAV_NS, "displayname") or list_id,
        "color": _el_text(prop, APPLE_NS, "calendar-color"),
        "components": components,
        "writable": writable,
        "ctag": _el_text(prop, CS_NS, "getctag"),
    }


def _parse_task_lists_xml(xml_text: str, user: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)  # noqa: S314
    task_lists: list[dict[str, Any]] = []
    base_href = f"/remote.php/dav/calendars/{user}"

    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_el = response.find(f"{{{DAV_NS}}}href")
        if href_el is None or href_el.text is None:
            continue
        href = href_el.text.rstrip("/")
        if href == base_href:
            continue
        list_id = href.rsplit("/", 1)[-1]
        if list_id in SKIP_COLLECTIONS:
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

        entry = _parse_task_list_entry(prop, list_id)
        if entry is not None:
            task_lists.append(entry)

    return task_lists


def _build_task_query_xml(uid: str | None = None) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<cal:calendar-query xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">',
        "<d:prop><d:getetag/><cal:calendar-data/></d:prop>",
        '<cal:filter><cal:comp-filter name="VCALENDAR">',
        '<cal:comp-filter name="VTODO">',
    ]
    if uid:
        escaped = xml_escape(uid)
        parts.append('<cal:prop-filter name="UID">')
        parts.append(f"<cal:text-match>{escaped}</cal:text-match>")
        parts.append("</cal:prop-filter>")
    parts.append("</cal:comp-filter></cal:comp-filter></cal:filter>")
    parts.append("</cal:calendar-query>")
    return "".join(parts)


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


def _format_task(ical_text: str) -> dict[str, Any]:
    """Parse iCalendar text and extract VTODO fields into a dict."""
    cal = ICal.from_ical(ical_text)
    for component in cal.walk():
        if component.name != "VTODO":
            continue
        result: dict[str, Any] = {
            "uid": str(component.get("UID", "")),
            "summary": str(component.get("SUMMARY", "")),
            "description": str(component.get("DESCRIPTION", "")),
            "status": str(component.get("STATUS", "")),
            "priority": int(component.get("PRIORITY", 0)),
            "percent_complete": int(component.get("PERCENT-COMPLETE", 0)),
            "dtstart": _dt_to_str(component.get("DTSTART")),
            "due": _dt_to_str(component.get("DUE")),
            "completed": _dt_to_str(component.get("COMPLETED")),
        }
        if component.get("CATEGORIES"):
            cats = component["CATEGORIES"]
            if isinstance(cats, list):
                result["categories"] = [str(c) for group in cats for c in group.cats]
            else:
                result["categories"] = [str(c) for c in cats.cats]
        return result
    msg = "No VTODO found in calendar data"
    raise ValueError(msg)


def _validate_status(status: str) -> str:
    upper = status.upper()
    if upper not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}")
    return upper


def _parse_iso_dt(value: str) -> date | datetime:
    """Parse an ISO date or datetime string."""
    if len(value) == 10:
        return date.fromisoformat(value)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _build_task_ical(
    uid: str,
    summary: str,
    description: str = "",
    due: str = "",
    start: str = "",
    status: str = "NEEDS-ACTION",
    priority: int = 0,
    percent_complete: int = 0,
    categories: list[str] | None = None,
) -> str:
    cal = ICal()
    cal.add("prodid", "-//nc-mcp-server//EN")
    cal.add("version", "2.0")
    todo = ITodo()
    todo.add("uid", uid)
    todo.add("dtstamp", datetime.now(UTC))
    todo.add("summary", summary)
    todo.add("status", status)
    if status == "COMPLETED":
        todo.add("percent-complete", 100)
        todo.add("completed", datetime.now(UTC))
    elif percent_complete:
        todo.add("percent-complete", percent_complete)
    if description:
        todo.add("description", description)
    if due:
        todo.add("due", _parse_iso_dt(due))
    if start:
        todo.add("dtstart", _parse_iso_dt(start))
    if priority:
        todo.add("priority", priority)
    if categories:
        todo.add("categories", categories)
    cal.add_component(todo)
    return cal.to_ical().decode()


def _set_prop(component: Any, name: str, value: Any, clear_if_empty: bool = False) -> None:
    component.pop(name, None)
    if clear_if_empty and not value:
        return
    component.add(name.lower(), value)


async def _find_task(list_id: str, task_uid: str) -> tuple[str, str, str]:
    """Find a task by UID. Returns (href, etag, ical_data) or raises."""
    client = get_client()
    user = get_config().user
    path = _caldav_path(user, list_id)
    body = _build_task_query_xml(uid=task_uid)
    response = await client.dav_request(
        "REPORT",
        path,
        body=body,
        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        context=f"Find task '{task_uid}' in '{list_id}'",
    )
    results = _parse_report_xml(response.text or "")
    if not results:
        raise NextcloudError(f"Task '{task_uid}' not found in list '{list_id}'", 404)
    return results[0]


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_task_lists() -> str:
        """List all task lists (CalDAV calendars with VTODO support) for the current user.

        Returns task lists with their properties including name, color,
        supported component types, and write access status.

        Returns:
            JSON list of task list objects with: id, name, color, components, writable, ctag.
            Use the id value with other task tools (e.g. "tasks").
        """
        client = get_client()
        user = get_config().user
        path = _caldav_path(user)
        response = await client.dav_request(
            "PROPFIND",
            path,
            body=TASK_LIST_PROPFIND,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context="List task lists",
        )
        task_lists = _parse_task_lists_xml(response.text or "", user)
        return json.dumps(task_lists)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_tasks(
        list_id: str = "tasks",
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Get tasks from a task list.

        Returns all tasks in the list, ordered as stored in CalDAV.

        Args:
            list_id: Task list identifier (default "tasks"). Use list_task_lists to find IDs.
            limit: Maximum number of tasks to return (1-500, default 50).
            offset: Number of tasks to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of task objects) and "pagination"
            (count, offset, limit, has_more). Each task has: uid, summary,
            description, status, priority, percent_complete, dtstart, due,
            completed, etag, and optionally categories.
        """
        limit = max(1, min(500, limit))
        offset = max(0, offset)

        client = get_client()
        user = get_config().user
        path = _caldav_path(user, list_id)
        body = _build_task_query_xml()
        response = await client.dav_request(
            "REPORT",
            path,
            body=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            context=f"Get tasks from '{list_id}'",
        )
        results = _parse_report_xml(response.text or "")
        all_tasks = []
        for _href, etag, ical_data in results:
            task = _format_task(ical_data)
            task["etag"] = etag
            all_tasks.append(task)
        page = all_tasks[offset : offset + limit]
        has_more = offset + limit < len(all_tasks)

        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            }
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_task(list_id: str, task_uid: str) -> str:
        """Get full details of a specific task by its UID.

        Args:
            list_id: Task list identifier (e.g. "tasks").
            task_uid: The task's UID. Use get_tasks to find UIDs.

        Returns:
            JSON object with full task details: uid, summary, description,
            status, priority, percent_complete, dtstart, due, completed, etag,
            and optionally categories.
        """
        _href, etag, ical_data = await _find_task(list_id, task_uid)
        task = _format_task(ical_data)
        task["etag"] = etag
        return json.dumps(task)


def _update_dt_prop(component: Any, name: str, value: str | None) -> None:
    """Update or clear a datetime property on a VTODO component."""
    if value is None:
        return
    if value:
        _set_prop(component, name, _parse_iso_dt(value))
    else:
        component.pop(name, None)


def _normalize_status(component: Any, status: str, reset_percent: bool) -> None:
    """Set STATUS and ensure PERCENT-COMPLETE/COMPLETED are consistent."""
    _set_prop(component, "STATUS", status)
    if status == "COMPLETED":
        _set_prop(component, "PERCENT-COMPLETE", 100)
        if component.get("COMPLETED") is None:
            _set_prop(component, "COMPLETED", datetime.now(UTC))
    elif status in ("NEEDS-ACTION", "IN-PROCESS"):
        component.pop("COMPLETED", None)
        if reset_percent:
            _set_prop(component, "PERCENT-COMPLETE", 0)


def _apply_task_updates(
    component: Any,
    summary: str | None,
    description: str | None,
    due: str | None,
    start: str | None,
    status: str | None,
    priority: int | None,
    percent_complete: int | None,
    categories: list[str] | None,
) -> None:
    if summary is not None:
        _set_prop(component, "SUMMARY", summary)
    if description is not None:
        _set_prop(component, "DESCRIPTION", description, clear_if_empty=True)
    _update_dt_prop(component, "DUE", due)
    _update_dt_prop(component, "DTSTART", start)
    if priority is not None:
        _set_prop(component, "PRIORITY", priority)
    if percent_complete is not None:
        _set_prop(component, "PERCENT-COMPLETE", percent_complete)
    if status is not None:
        _normalize_status(component, status, reset_percent=percent_complete is None)
    if categories is not None:
        _set_prop(component, "CATEGORIES", categories, clear_if_empty=True)
    _set_prop(component, "DTSTAMP", datetime.now(UTC))


def _register_create_task(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_task(
        list_id: str,
        summary: str,
        description: str = "",
        due: str = "",
        start: str = "",
        status: str = "NEEDS-ACTION",
        priority: int = 0,
        percent_complete: int = 0,
        categories: str = "",
    ) -> str:
        """Create a new task in a task list.

        Args:
            list_id: Task list identifier (e.g. "tasks").
            summary: Task title/summary.
            description: Optional task description/notes.
            due: Optional due date/time in ISO 8601 format (e.g. "2026-04-10T18:00:00Z").
            start: Optional start date/time in ISO 8601 format.
            status: Task status: "NEEDS-ACTION" (default), "IN-PROCESS", "COMPLETED", or "CANCELLED".
            priority: Priority 0-9 (0=undefined, 1=highest, 5=medium, 9=lowest). Default 0.
            percent_complete: Completion percentage 0-100. Default 0.
            categories: Optional comma-separated category names (e.g. "Work,Urgent").

        Returns:
            JSON object with the created task's uid and summary.
        """
        status_upper = _validate_status(status)
        if not 0 <= priority <= 9:
            raise ValueError("Priority must be 0-9 (0=undefined, 1=highest, 9=lowest)")
        if not 0 <= percent_complete <= 100:
            raise ValueError("Percent complete must be 0-100")
        cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else None

        uid = str(uuid.uuid4())
        ical_data = _build_task_ical(
            uid,
            summary,
            description,
            due,
            start,
            status_upper,
            priority,
            percent_complete,
            cat_list,
        )
        client = get_client()
        user = get_config().user
        path = _caldav_path(user, list_id, f"{uid}.ics")
        await client.dav_request(
            "PUT",
            path,
            body=ical_data,
            headers={"Content-Type": "text/calendar; charset=utf-8"},
            context=f"Create task in '{list_id}'",
        )
        return json.dumps({"uid": uid, "summary": summary})


def _register_update_task(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_task(
        list_id: str,
        task_uid: str,
        summary: str | None = None,
        description: str | None = None,
        due: str | None = None,
        start: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        percent_complete: int | None = None,
        categories: str | None = None,
        etag: str = "",
    ) -> str:
        """Update an existing task. Only provided fields are changed.

        For optimistic locking, pass the etag from a previous get_task or
        get_tasks call. If the task was modified since that read, the update
        will fail with a 412 Precondition Failed error. If omitted, the
        current etag is fetched automatically (still safe against concurrent
        writes during this call, but won't detect earlier external changes).

        Args:
            list_id: Task list identifier (e.g. "tasks").
            task_uid: The task's UID to update. Use get_tasks to find UIDs.
            summary: New task title.
            description: New description. Pass "" to clear.
            due: New due date/time in ISO 8601 format. Pass "" to clear.
            start: New start date/time in ISO 8601 format. Pass "" to clear.
            status: New status: "NEEDS-ACTION", "IN-PROCESS", "COMPLETED", or "CANCELLED".
            priority: New priority 0-9 (0=undefined, 1=highest, 9=lowest).
            percent_complete: New completion percentage 0-100.
            categories: New categories as comma-separated string. Pass "" to clear.
            etag: Optional ETag from a previous read for optimistic locking.

        Returns:
            Confirmation message with the updated task UID.
        """
        validated_status = _validate_status(status) if status is not None else None
        if priority is not None and not 0 <= priority <= 9:
            raise ValueError("Priority must be 0-9 (0=undefined, 1=highest, 9=lowest)")
        if percent_complete is not None and not 0 <= percent_complete <= 100:
            raise ValueError("Percent complete must be 0-100")
        cat_list: list[str] | None = None
        if categories is not None:
            cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else []

        href, fetched_etag, ical_data = await _find_task(list_id, task_uid)
        match_etag = etag or fetched_etag
        cal = ICal.from_ical(ical_data)
        for component in cal.walk():
            if component.name == "VTODO":
                _apply_task_updates(
                    component,
                    summary,
                    description,
                    due,
                    start,
                    validated_status,
                    priority,
                    percent_complete,
                    cat_list,
                )
                break

        client = get_client()
        await client.dav_request(
            "PUT",
            _href_to_dav_path(href),
            body=cal.to_ical().decode(),
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-Match": f'"{match_etag}"'},
            context=f"Update task '{task_uid}'",
        )
        return f"Task '{task_uid}' updated."


def _register_complete_task(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def complete_task(list_id: str, task_uid: str, etag: str = "") -> str:
        """Mark a task as completed.

        Sets the task's status to COMPLETED, percent-complete to 100,
        and records the completion timestamp. No-ops if already completed.

        For optimistic locking, pass the etag from a previous get_task call.

        Args:
            list_id: Task list identifier (e.g. "tasks").
            task_uid: The task's UID to complete. Use get_tasks to find UIDs.
            etag: Optional ETag from a previous read for optimistic locking.

        Returns:
            Confirmation message.
        """
        href, fetched_etag, ical_data = await _find_task(list_id, task_uid)
        match_etag = etag or fetched_etag
        cal = ICal.from_ical(ical_data)
        needs_update = False
        for component in cal.walk():
            if component.name != "VTODO":
                continue
            if str(component.get("STATUS", "")) == "COMPLETED" and component.get("COMPLETED") is not None:
                break
            _set_prop(component, "STATUS", "COMPLETED")
            _set_prop(component, "PERCENT-COMPLETE", 100)
            _set_prop(component, "COMPLETED", datetime.now(UTC))
            _set_prop(component, "DTSTAMP", datetime.now(UTC))
            needs_update = True
            break

        if needs_update:
            client = get_client()
            await client.dav_request(
                "PUT",
                _href_to_dav_path(href),
                body=cal.to_ical().decode(),
                headers={"Content-Type": "text/calendar; charset=utf-8", "If-Match": f'"{match_etag}"'},
                context=f"Complete task '{task_uid}'",
            )
        return f"Task '{task_uid}' completed."


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_task(list_id: str, task_uid: str) -> str:
        """Delete a task by its UID.

        The task is moved to the calendar trashbin and can be restored
        from the Nextcloud web interface within the retention period.

        Args:
            list_id: Task list identifier (e.g. "tasks").
            task_uid: The task's UID to delete. Use get_tasks to find UIDs.

        Returns:
            Confirmation message.
        """
        href, _etag, _ical = await _find_task(list_id, task_uid)
        client = get_client()
        await client.dav_request("DELETE", _href_to_dav_path(href), context=f"Delete task '{task_uid}'")
        return f"Task '{task_uid}' deleted."


def register(mcp: FastMCP) -> None:
    """Register tasks tools with the MCP server."""
    _register_read_tools(mcp)
    _register_create_task(mcp)
    _register_update_task(mcp)
    _register_complete_task(mcp)
    _register_destructive_tools(mcp)
