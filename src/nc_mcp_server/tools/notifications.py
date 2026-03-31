"""Notification tools — list and dismiss notifications via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client


def _format_notification(n: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from a raw notification object."""
    result: dict[str, Any] = {
        "notification_id": n.get("notification_id"),
        "app": n.get("app"),
        "user": n.get("user"),
        "datetime": n.get("datetime"),
        "object_type": n.get("object_type"),
        "object_id": n.get("object_id"),
        "subject": n.get("subject"),
        "message": n.get("message"),
    }
    if n.get("link"):
        result["link"] = n["link"]
    if n.get("actions"):
        result["actions"] = n["actions"]
    return result


def register(mcp: FastMCP) -> None:
    """Register notification tools with the MCP server."""

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_notifications(limit: int = 25, offset: int = 0) -> str:
        """List notifications for the current Nextcloud user.

        Returns notifications sorted by newest first. Note: the Nextcloud
        server returns at most 25 notifications and does not support
        server-side pagination, so the total available is capped by the
        server regardless of the limit value.

        Args:
            limit: Maximum number of notifications to return (1-25, default 25).
            offset: Number of notifications to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of notification objects with notification_id, app,
            user, datetime, subject, message, and optionally link and actions) and
            "pagination" (count, offset, limit, has_more).
        """
        limit = max(1, min(25, limit))
        offset = max(0, offset)
        client = get_client()
        data = await client.ocs_get("apps/notifications/api/v2/notifications")
        all_notifs = [_format_notification(n) for n in data]
        page = all_notifs[offset : offset + limit]
        has_more = offset + limit < len(all_notifs)

        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def dismiss_notification(notification_id: int) -> str:
        """Dismiss (permanently delete) a single notification by its ID.

        Use list_notifications first to find the notification_id to dismiss.

        Args:
            notification_id: The numeric ID of the notification to dismiss.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(
            f"apps/notifications/api/v2/notifications/{notification_id}",
        )
        return f"Notification {notification_id} dismissed."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def dismiss_all_notifications() -> str:
        """Dismiss (permanently delete) ALL notifications for the current user.

        This cannot be undone. Use list_notifications first to review
        what will be dismissed.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete("apps/notifications/api/v2/notifications")
        return "All notifications dismissed."
