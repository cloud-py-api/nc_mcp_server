"""Activity tools — recent activity feed, its filters and daily counts via OCS API."""

import json
import re
from datetime import UTC, date, datetime, time
from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from ..annotations import READONLY
from ..client import NextcloudClient, NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

ACTIVITY_API = "apps/activity/api/v2/activity"

_VALID_SORT = {"asc", "desc"}

# Servers whose Activity app takes search, start/end and actor (Activity 8, shipped with Nextcloud 35).
# Older versions ignore those parameters and return the unfiltered feed, so they are checked first. Only
# support is remembered: a server without it may be upgraded while the MCP server runs.
_SEARCH_SUPPORT: set[str] = set()

# Nextcloud's answer to a route that does not exist, as opposed to Activity's own 404 for an unknown filter
_NO_ROUTE = "Invalid query"

_SEARCH_UNSUPPORTED = (
    "search, start, end and actor need the Activity app 8 or later (Nextcloud 35); this server's Activity app "
    "would ignore them and return the unfiltered feed."
)


def _format_activity(a: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from a raw activity object."""
    result: dict[str, Any] = {
        "activity_id": a["activity_id"],
        "app": a.get("app", ""),
        "type": a.get("type", ""),
        "user": a.get("user", ""),
        "subject": a.get("subject", ""),
        "datetime": a.get("datetime", ""),
        "link": a.get("link", ""),
        "object_type": a.get("object_type", ""),
        "object_id": a.get("object_id", 0),
        "object_name": a.get("object_name", ""),
    }
    message = a.get("message", "")
    if message:
        result["message"] = message
    return result


def _timestamp(value: str, field: str, end_of_day: bool = False) -> int:
    """Turn an ISO 8601 date (taken as UTC) or time with a time zone into a Unix timestamp."""
    try:
        if len(value) == 10:
            moment = datetime.combine(date.fromisoformat(value), time.max if end_of_day else time.min, UTC)
        else:
            moment = datetime.fromisoformat(value)
    except ValueError as e:
        msg = f"{field} must be an ISO 8601 date or time, e.g. 2026-09-20 or 2026-09-20T14:00:00+02:00; got {value!r}"
        raise ValueError(msg) from e
    if moment.tzinfo is None:
        raise ValueError(f"{field} needs a time zone, e.g. 2026-09-20T14:00:00+02:00 or 2026-09-20T12:00:00Z")
    if moment.timestamp() <= 0:
        raise ValueError(f"{field} must be after 1970")
    return int(moment.timestamp())


def _stream(activity_filter: str, object_type: str, object_id: int) -> tuple[str, dict[str, str]]:
    """The filter to request and the object parameters to send.

    Activity only narrows the feed to one object in its special "filter" stream and ignores the object with
    any other filter, while still clearing the notifications of the activities it returns.
    """
    if bool(object_type) != bool(object_id):
        # Activity silently drops one without the other
        raise ValueError("object_type and object_id must be given together.")
    if not object_type:
        return activity_filter, {}
    if activity_filter not in ("all", "filter"):
        raise ValueError("object_type and object_id cannot be combined with an activity_filter; leave it at 'all'.")
    return "filter", {"object_type": object_type, "object_id": str(object_id)}


def _has_next_page(link: str | None) -> bool:
    return bool(link and re.search(r'rel="?next"?', link))


def _no_route(e: NextcloudError) -> bool:
    return e.status_code == 404 and _NO_ROUTE in str(e)


def _explain(e: NextcloudError, activity_filter: str) -> Exception:
    if _no_route(e):
        return ValueError("The Activity app is not available: it is not installed or not enabled for this user.")
    if e.status_code == 404:
        return ValueError(f"Unknown activity_filter {activity_filter!r}. list_activity_filters lists the valid ones.")
    return e


async def _supports_search(client: NextcloudClient) -> bool:
    """Check whether the server's Activity app takes the search parameters.

    Activity 8 added them together with the histogram route, which older versions answer with
    Nextcloud's "Invalid query" for unknown routes, as they do when the app is not enabled at all.
    """
    if client.base_url in _SEARCH_SUPPORT:
        return True
    try:
        await client.ocs_get(f"{ACTIVITY_API}/all/histogram", params={"days": "1"})
    except NextcloudError as e:
        if not _no_route(e):
            raise
        await _require_app(client)
        return False
    _SEARCH_SUPPORT.add(client.base_url)
    return True


async def _require_app(client: NextcloudClient) -> None:
    """Raise a clear error when the Activity app itself is missing; every version has the filter list."""
    try:
        await client.ocs_get(f"{ACTIVITY_API}/filters")
    except NextcloudError as e:
        raise _explain(e, "all") from e


def _search_params(search: str, start: str, end: str, actor: str) -> dict[str, str]:
    params: dict[str, str] = {}
    if search:
        params["search"] = search
    if start:
        params["from"] = str(_timestamp(start, "start"))
    if end:
        params["to"] = str(_timestamp(end, "end", end_of_day=True))
    if actor:
        params["actor"] = actor
    if "from" in params and "to" in params and int(params["from"]) > int(params["to"]):
        raise ValueError("start must not be after end.")
    return params


def _register_feed(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_activity(
        activity_filter: str = "all",
        limit: int = 30,
        since: int = 0,
        object_type: str = "",
        object_id: int = 0,
        sort: str = "desc",
        search: str = "",
        start: str = "",
        end: str = "",
        actor: str = "",
    ) -> str:
        """Get the recent activity feed for the current Nextcloud user.

        Activities track what happened across Nextcloud: file changes, shares,
        calendar events, comments, and more.

        Filters include "all", "self" (your actions), "by" (others' actions),
        "files", "files_sharing", "files_favorites", "calendar", "comments" and
        more, depending on the installed apps; list_activity_filters lists them.

        To get activities for a specific file or object, provide both object_type
        and object_id (e.g., object_type="files", object_id=742), and leave
        activity_filter at "all". This also clears the returned activities'
        notifications, as the web interface does when it shows a file's activity.

        Args:
            activity_filter: Activity filter id (default: "all").
            limit: Maximum number of activities to return (1-200, default: 30).
            since: Activity ID to paginate from. Use the "since" value from the
                   previous call's pagination to fetch the next page. Default 0 = newest.
            object_type: Filter by object type (e.g., "files"). Must be used
                         together with object_id.
            object_id: Filter by object ID. Must be used together with object_type.
            sort: Sort order: "desc" (newest first, default) or "asc" (oldest first).
            search: Only activities whose file path contains this text (2-255 characters
                    after trimming, case-insensitive), which leaves out activities without a file.
            start: Only activities at or after this time: an ISO 8601 date (from its
                   start, UTC) or a time with a time zone.
            end: Only activities at or before this time: an ISO 8601 date (to its
                 end, UTC) or a time with a time zone.
            actor: Only activities done by this user ID.
            search, start, end and actor need Nextcloud 35 (Activity 8); on older
            servers they are refused rather than ignored.

        Returns:
            JSON object with "data" (list of activities) and "pagination"
            (count, has_more, since). Use since value for the next page.
        """
        if sort not in _VALID_SORT:
            raise ValueError(f"Invalid sort '{sort}'. Must be 'asc' or 'desc'.")
        limit = max(1, min(200, limit))
        activity_filter, object_params = _stream(activity_filter, object_type, object_id)
        params: dict[str, str] = {"limit": str(limit), "sort": sort, **object_params}
        if since:
            params["since"] = str(since)
        criteria = _search_params(search, start, end, actor)
        client = get_client()
        if criteria and not await _supports_search(client):
            raise ValueError(_SEARCH_UNSUPPORTED)
        params.update(criteria)
        path = f"{ACTIVITY_API}/{activity_filter}" if activity_filter != "all" else ACTIVITY_API
        try:
            data, headers = await client.ocs_get_with_headers(path, params=params)
        except NextcloudError as e:
            raise _explain(e, activity_filter) from e
        # Activity answers "304 Not Modified" when nothing matches or a page is past the end
        activities = [_format_activity(a) for a in cast(list[dict[str, Any]], data or [])]
        # Activity groups related entries, so a page can be shorter than the limit with more to come; its
        # headers say whether there is a next page and where it starts
        last_given = headers.get("X-Activity-Last-Given")
        response: dict[str, Any] = {
            "data": activities,
            "pagination": {
                "count": len(activities),
                "has_more": _has_next_page(headers.get("Link")),
                # Activity may drop entries it cannot show, so even an empty page can have a next one
                "since": int(last_given) if last_given else None,
            },
        }
        return json.dumps(response, default=str)


def _register_filters_and_counts(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_activity_filters() -> str:
        """List the activity filters get_activity accepts on this server.

        Returns:
            JSON list of filters with "id" (the value for activity_filter) and "name".
        """
        data = await get_client().ocs_get(f"{ACTIVITY_API}/filters")
        return json.dumps([{"id": f.get("id", ""), "name": f.get("name", "")} for f in data])

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_activity_counts(
        activity_filter: str = "all",
        days: int = 30,
        search: str = "",
        actor: str = "",
        object_type: str = "",
        object_id: int = 0,
    ) -> str:
        """Count the current user's activities per day over the last days, e.g. to see when something happened.

        Needs Nextcloud 35 (Activity 8). Days are the user's days in their Nextcloud time zone, ending today,
        while a date passed to get_activity's start and end is a UTC day; to list one of these days, pass
        start and end as times with the user's UTC offset (e.g. 2026-09-20T00:00:00+02:00 and
        2026-09-20T23:59:59+02:00).

        Args:
            activity_filter: Activity filter id (default: "all"); see list_activity_filters.
            days: Number of days ending today (1-366, default 30).
            search: Only count activities whose file path contains this text (2-255 characters).
            actor: Only count activities done by this user ID.
            object_type: Only count activities about this object; must be used together with object_id, and
                with activity_filter left at "all".
            object_id: The object's ID, e.g. a file ID for object_type "files".

        Returns:
            JSON object with "from" and "to" (dates), "counts" (date to count, only days with activity),
            "total" and "max". "partial_before" is set when Nextcloud stopped counting at its row limit:
            counts for that date and earlier are missing, so a shorter window gives complete numbers.
        """
        activity_filter, object_params = _stream(activity_filter, object_type, object_id)
        params: dict[str, str] = {"days": str(max(1, min(366, days))), **object_params}
        params.update(_search_params(search, "", "", actor))
        client = get_client()
        try:
            data = await client.ocs_get(f"{ACTIVITY_API}/{activity_filter}/histogram", params=params)
        except NextcloudError as e:
            if not _no_route(e):
                raise _explain(e, activity_filter) from e
            await _require_app(client)
            raise ValueError("Activity counts need the Activity app 8 or later (Nextcloud 35).") from e
        return json.dumps(
            {
                "from": data.get("from"),
                "to": data.get("to"),
                "counts": data.get("counts") or {},
                "total": data.get("total", 0),
                "max": data.get("max", 0),
                "partial_before": data.get("partial_before"),
            }
        )


def register(mcp: FastMCP) -> None:
    """Register activity tools with the MCP server."""
    _register_feed(mcp)
    _register_filters_and_counts(mcp)
