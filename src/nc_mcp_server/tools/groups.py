"""Group management tools: list groups and their members, create and delete groups via OCS API."""

import json
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, DESTRUCTIVE, READONLY
from ..client import NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client


def _group_path(group_id: str) -> str:
    """OCS path of a group, for the members and delete endpoints.

    Those two run urldecode() on an ID the router has already decoded, so it is encoded twice:
    encoded once, a "+" arrives as a space, "%41" as "A", and an encoded "/" is refused by Apache.
    """
    return f"cloud/groups/{quote(quote(group_id, safe=''), safe='')}"


def _format_group(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": group.get("id", ""),
        "display_name": group.get("displayname", ""),
        "user_count": group.get("usercount", 0),
        "disabled_user_count": group.get("disabled", 0),
    }


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_groups(search: str = "", limit: int = 50, offset: int = 0) -> str:
        """List Nextcloud groups. Requires admin rights (or delegated user administration).

        Args:
            search: Optional text to filter groups by ID or display name.
            limit: Maximum number of groups to return (1-200, default 50).
            offset: Number of groups to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of groups with id, display_name, user_count,
            disabled_user_count) and "pagination" (count, offset, limit, has_more).
            The "id" is what update_user and the other group tools expect.
        """
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        client = get_client()
        params = {"search": search, "limit": str(limit), "offset": str(offset)}
        data = await client.ocs_get("cloud/groups/details", params=params)
        groups = [_format_group(g) for g in data.get("groups", [])]
        return json.dumps(
            {
                "data": groups,
                "pagination": {
                    "count": len(groups),
                    "offset": offset,
                    "limit": limit,
                    "has_more": len(groups) == limit,
                },
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_group_members(group_id: str, limit: int = 100, offset: int = 0) -> str:
        """List the users in a group.

        Admins, sub-admins of the group and the group's own members may do this.

        Args:
            group_id: The group ID. Use list_groups to find it.
            limit: Maximum number of user IDs to return (1-500, default 100).
            offset: Number of user IDs to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of user IDs) and "pagination" (count, offset,
            limit, has_more). Use get_user for details on a member.
        """
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        client = get_client()
        data = await client.ocs_get(f"{_group_path(group_id)}/users")
        users: list[str] = data.get("users", [])
        page = users[offset : offset + limit]
        return json.dumps(
            {
                "data": page,
                "pagination": {
                    "count": len(page),
                    "offset": offset,
                    "limit": limit,
                    "has_more": offset + limit < len(users),
                },
            },
            default=str,
        )


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_group(group_id: str, display_name: str = "") -> str:
        """Create a new group. Requires admin rights (or delegated user administration).

        Add users to it with update_user (its "groups" field).

        Args:
            group_id: The group ID, used by the other tools to refer to it.
                Cannot be changed later.
            display_name: Name shown in the web interface. Defaults to the ID.

        Returns:
            Confirmation message.
        """
        client = get_client()
        data = {"groupid": group_id}
        if display_name:
            data["displayname"] = display_name
        await client.ocs_post("cloud/groups", data=data)
        return f"Group '{group_id}' created."


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_group(group_id: str) -> str:
        """Delete a group. Requires admin rights (or delegated user administration).

        The members keep their accounts, but lose the group's memberships and
        anything shared with the group. The "admin" group cannot be deleted.

        Args:
            group_id: The group ID to delete.

        Returns:
            Confirmation message.
        """
        client = get_client()
        try:
            await client.ocs_delete(_group_path(group_id))
        except NextcloudError as e:
            if e.status_code != 400:
                raise
            # Nextcloud answers all three cases with an error that has no message
            raise NextcloudError(
                f"{e}: the group does not exist, it is the admin group, or its backend (LDAP, for one)"
                " cannot delete groups",
                400,
            ) from e
        return f"Group '{group_id}' deleted."


def register(mcp: FastMCP) -> None:
    """Register group tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
