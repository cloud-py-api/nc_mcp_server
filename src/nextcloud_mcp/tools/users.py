"""User management tools — get user info via OCS API."""

import json

from mcp.server.fastmcp import FastMCP

from ..permissions import PermissionLevel, require_permission
from ..state import get_client


def register(mcp: FastMCP) -> None:
    """Register user tools with the MCP server."""

    @mcp.tool()
    @require_permission(PermissionLevel.READ)
    async def get_current_user() -> str:
        """Get information about the currently authenticated Nextcloud user.

        Returns:
            JSON with user details: id, displayname, email, quota, groups, etc.
        """
        client = get_client()
        data = await client.ocs_get("cloud/user")
        return json.dumps(data, indent=2, default=str)

    @mcp.tool()
    @require_permission(PermissionLevel.READ)
    async def list_users(search: str = "", limit: int = 25, offset: int = 0) -> str:
        """List Nextcloud users.

        Args:
            search: Optional search string to filter users by name/email.
            limit: Maximum number of users to return (default 25).
            offset: Offset for pagination.

        Returns:
            JSON list of user IDs matching the search.
        """
        client = get_client()
        params = {"search": search, "limit": str(limit), "offset": str(offset)}
        data = await client.ocs_get("cloud/users", params=params)
        return json.dumps(data, indent=2, default=str)

    @mcp.tool()
    @require_permission(PermissionLevel.READ)
    async def get_user(user_id: str) -> str:
        """Get detailed information about a specific Nextcloud user.

        Args:
            user_id: The user ID to look up. Example: "admin", "john.doe"

        Returns:
            JSON with user details: id, displayname, email, quota, groups, language, etc.
        """
        client = get_client()
        data = await client.ocs_get(f"cloud/users/{user_id}")
        return json.dumps(data, indent=2, default=str)
