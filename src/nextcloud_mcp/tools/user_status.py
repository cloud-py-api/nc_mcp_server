"""User status tools — get and set user status via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client, get_config

_VALID_STATUS_TYPES = {"online", "away", "dnd", "invisible", "offline"}


def _format_status(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": data.get("userId"),
        "status": data.get("status"),
        "message": data.get("message"),
        "icon": data.get("icon"),
        "clear_at": data.get("clearAt"),
    }


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    @require_permission(PermissionLevel.READ)
    async def get_user_status(user_id: str = "") -> str:
        """Get the status of a Nextcloud user.

        Returns the user's online status (online, away, dnd, invisible, offline),
        custom status message, status icon, and when the status will be cleared.

        Args:
            user_id: User ID to look up. Leave empty to get your own status.

        Returns:
            JSON object with user_id, status, message, icon, and clear_at.
        """
        client = get_client()
        try:
            if user_id:
                data = await client.ocs_get(f"apps/user_status/api/v1/statuses/{user_id}")
            else:
                data = await client.ocs_get("apps/user_status/api/v1/user_status")
        except NextcloudError as e:
            if e.status_code == 404 and not user_id:
                user = get_config().user
                data = {"userId": user, "status": "offline", "message": None, "icon": None, "clearAt": None}
            else:
                raise
        return json.dumps(_format_status(data), indent=2, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    @require_permission(PermissionLevel.WRITE)
    async def set_user_status(
        status_type: str = "",
        message: str = "",
        icon: str = "",
        clear_at: int = 0,
    ) -> str:
        """Set the current user's status.

        You can set the online status type, a custom message, or both in a single call.
        At least one of status_type or message must be provided.

        Args:
            status_type: Online status — "online", "away", "dnd", "invisible", or "offline".
                         Leave empty to keep the current status type.
            message: Custom status message (e.g., "Working from home", "On vacation").
                     Leave empty to keep the current message.
            icon: Status icon emoji (e.g., "🏠", "🌴"). Only used with message.
            clear_at: Unix timestamp when the status message should be cleared.
                      Use 0 to never auto-clear.

        Returns:
            JSON object with the updated status.
        """
        if not status_type and not message:
            raise ValueError("At least one of status_type or message must be provided.")
        if status_type and status_type not in _VALID_STATUS_TYPES:
            valid = ", ".join(sorted(_VALID_STATUS_TYPES))
            raise ValueError(f"Invalid status_type '{status_type}'. Valid types: {valid}")
        client = get_client()
        result: dict[str, Any] = {}
        if status_type:
            result = await client.ocs_put(
                "apps/user_status/api/v1/user_status/status",
                data={"statusType": status_type},
            )
        if message:
            msg_data: dict[str, Any] = {"message": message}
            if icon:
                msg_data["statusIcon"] = icon
            if clear_at:
                msg_data["clearAt"] = clear_at
            result = await client.ocs_put(
                "apps/user_status/api/v1/user_status/message/custom",
                data=msg_data,
            )
        return json.dumps(_format_status(result), indent=2, default=str)

    @mcp.tool()
    @require_permission(PermissionLevel.WRITE)
    async def clear_user_status() -> str:
        """Clear the current user's status message and icon.

        This removes the custom status message and icon but does NOT change
        the online status type (online, away, etc.).

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete("apps/user_status/api/v1/user_status/message")
        return "Status message cleared."


def register(mcp: FastMCP) -> None:
    """Register user status tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
