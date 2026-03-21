"""Notification tools — list and dismiss notifications via OCS API."""

import json

from mcp.server.fastmcp import FastMCP

from ..permissions import PermissionLevel, require_permission
from ..state import get_client


def register(mcp: FastMCP) -> None:
    """Register notification tools with the MCP server."""

    @mcp.tool()
    @require_permission(PermissionLevel.READ)
    async def list_notifications() -> str:
        """List all notifications for the current Nextcloud user.

        Returns notifications sorted by newest first. Each notification
        includes: notification_id, app, datetime, subject, message, link,
        and actions.

        Returns:
            JSON list of notification objects.
        """
        client = get_client()
        data = await client.ocs_get(
            "apps/notifications/api/v2/notifications",
        )
        return json.dumps(data, indent=2, default=str)

    @mcp.tool()
    @require_permission(PermissionLevel.WRITE)
    async def dismiss_notification(notification_id: int) -> str:
        """Dismiss (delete) a single notification by its ID.

        Use list_notifications first to find the notification_id to
        dismiss.

        Args:
            notification_id: The numeric ID of the notification to
                dismiss.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(
            f"apps/notifications/api/v2/notifications/{notification_id}",
        )
        return f"Notification {notification_id} dismissed."
