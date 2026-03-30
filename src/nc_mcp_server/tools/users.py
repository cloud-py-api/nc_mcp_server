"""User management tools — get, create, and delete users via OCS API."""

import json

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_current_user() -> str:
        """Get information about the currently authenticated Nextcloud user.

        Returns:
            JSON with user details: id, displayname, email, quota, groups, etc.
        """
        client = get_client()
        data = await client.ocs_get("cloud/user")
        return json.dumps(data, default=str)

    @mcp.tool(annotations=READONLY)
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
        return json.dumps(data, default=str)

    @mcp.tool(annotations=READONLY)
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
        return json.dumps(data, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_user(user_id: str, password: str, display_name: str = "", email: str = "") -> str:
        """Create a new Nextcloud user. Requires admin privileges.

        Args:
            user_id: Login name for the new user.
            password: Password for the new user.
            display_name: Display name. Defaults to user_id if empty.
            email: Email address for the new user.

        Returns:
            JSON with the created user ID.
        """
        client = get_client()
        data: dict[str, str] = {"userid": user_id, "password": password}
        if display_name:
            data["displayName"] = display_name
        if email:
            data["email"] = email
        result = await client.ocs_post("cloud/users", data=data)
        return json.dumps(result, default=str)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_user(user_id: str) -> str:
        """Permanently delete a Nextcloud user. Requires admin privileges.

        This cannot be undone. The user's data and files will be removed.

        Args:
            user_id: The user ID to delete.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"cloud/users/{user_id}")
        return f"User '{user_id}' deleted."


def register(mcp: FastMCP) -> None:
    """Register user tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
