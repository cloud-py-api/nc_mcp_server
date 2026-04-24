"""Circles (Teams) tools — OCS API for user/group/team management."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

MEMBER_TYPES = {
    "user": 1,
    "group": 2,
    "mail": 4,
    "contact": 8,
    "circle": 16,
}

MEMBER_LEVELS = {
    "member": 1,
    "moderator": 4,
    "admin": 8,
    "owner": 9,
}


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_circles(limit: int | None = None, offset: int | None = None) -> str:
        """List circles (teams) the current user can see.

        Args:
            limit: Max circles to return. Omit for server default (all).
            offset: Starting offset for pagination.

        Returns:
            JSON array of circles. Each entry includes id (the string singleId
            used for sharing), name, displayName, description, config (bitmask),
            source, population, creation, initiator (current-user membership
            info: level, status, userId).
        """
        client = get_client()
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        data = await client.ocs_get("apps/circles/circles", params=params or None)
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_circle(circle_id: str) -> str:
        """Get full details of a circle including the current user's membership info.

        Args:
            circle_id: String circle id (the hash from list_circles, e.g.
                "cUXI7OgXkF6u5jWUoE73AtmyjVE2ZRl").

        Returns:
            JSON object with the circle's name, description, config, settings,
            source, population, creation timestamp, and `initiator` object
            describing the current user's membership (id, singleId, level,
            status, userId).
        """
        client = get_client()
        data = await client.ocs_get(f"apps/circles/circles/{circle_id}")
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_circle_members(circle_id: str, full_details: bool = False) -> str:
        """List all members of a circle.

        Args:
            circle_id: String circle id.
            full_details: If True, include extended info such as circle
                memberships inherited through this one.

        Returns:
            JSON array of members. Each entry has id (memberId — use with
            remove_circle_member and update_circle_member_level), singleId
            (the user's federated id), userId, userType (1=user, 2=group,
            4=mail, 8=contact, 16=circle), level (0=none, 1=member,
            4=moderator, 8=admin, 9=owner), status ("Member", "Invited",
            "Requesting"), displayName, instance.
        """
        client = get_client()
        params = {"fullDetails": "true"} if full_details else None
        data = await client.ocs_get(f"apps/circles/circles/{circle_id}/members", params=params)
        return json.dumps(data)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def search_circles(term: str) -> str:
        """Search for circles and potential members (users, groups, emails) by term.

        Args:
            term: Search phrase. Matches against names/ids.

        Returns:
            JSON array of search results. Each entry includes id (the singleId
            for users/groups/circles), userId, displayName, instance, and
            userType (1=user, 2=group, 4=mail, 8=contact, 16=circle).
        """
        client = get_client()
        data = await client.ocs_get("apps/circles/search", params={"term": term})
        return json.dumps(data)


def _register_circle_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_circle(name: str, personal: bool = False, local: bool = False) -> str:
        """Create a new circle (team).

        Args:
            name: Display name of the new circle. Does not have to be unique.
            personal: If True, create a personal circle visible only to the
                owner (useful for private contact groups).
            local: If True, mark the circle as local (not federated to other
                instances) even when global scope is enabled.

        Returns:
            JSON of the new circle including its generated id. The caller is
            automatically added as owner (level=9).
        """
        client = get_client()
        body: dict[str, Any] = {"name": name}
        if personal:
            body["personal"] = True
        if local:
            body["local"] = True
        data = await client.ocs_post_json("apps/circles/circles", json_data=body)
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_circle_name(circle_id: str, name: str) -> str:
        """Rename a circle. Requires admin or owner level on the circle.

        Args:
            circle_id: String circle id.
            name: New name.

        Returns:
            JSON of the updated circle.
        """
        client = get_client()
        data = await client.ocs_put_json(f"apps/circles/circles/{circle_id}/name", json_data={"value": name})
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_circle_description(circle_id: str, description: str) -> str:
        """Update a circle's description. Requires admin or owner level.

        Args:
            circle_id: String circle id.
            description: New description text. Pass empty string to clear.

        Returns:
            JSON of the updated circle.
        """
        client = get_client()
        data = await client.ocs_put_json(
            f"apps/circles/circles/{circle_id}/description",
            json_data={"value": description},
        )
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_circle_config(circle_id: str, config: int) -> str:
        """Update a circle's config flags (bitmask). Requires admin or owner level.

        Args:
            circle_id: String circle id.
            config: Bitmask integer. Common flags (combine with OR):
                8=VISIBLE (listed for non-members),
                16=OPEN (anyone can join via join_circle),
                32=INVITE (adding a member creates an invitation to accept),
                64=REQUEST (join requests need moderator approval),
                128=FRIEND (members can invite friends),
                256=PROTECTED (password-protected),
                1024=HIDDEN (hidden from listings),
                4096=LOCAL (not federated),
                65536=MOUNTPOINT (auto-create Files folder).
                Pass 0 for a fully private, invite-by-admin-only circle.

        Returns:
            JSON of the updated circle.
        """
        client = get_client()
        data = await client.ocs_put_json(
            f"apps/circles/circles/{circle_id}/config",
            json_data={"value": config},
        )
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def join_circle(circle_id: str) -> str:
        """Join an open circle on behalf of the current user.

        The circle must have the OPEN config flag (16); otherwise the server
        returns an error. For invitation-only circles, the circle's admin or
        moderator must use add_circle_member instead.

        Args:
            circle_id: String circle id.

        Returns:
            JSON of the current user's new membership (id, level, status).
        """
        client = get_client()
        data = await client.ocs_put_json(f"apps/circles/circles/{circle_id}/join", json_data={})
        return json.dumps(data)

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def leave_circle(circle_id: str) -> str:
        """Leave a circle the current user is a member of.

        IMPORTANT: If the current user is the sole owner, leaving destroys
        the entire circle (server behavior — no confirmation prompt). To
        avoid this, promote another member to owner via
        update_circle_member_level(..., level="owner") first. Because of
        this implicit-destroy behavior, this tool requires DESTRUCTIVE
        permission (matching leave_conversation in Talk).

        Args:
            circle_id: String circle id.

        Returns:
            JSON of the circle the user just left (circle fields: id, name,
            config, population, initiator, …). Empty when the caller loses
            visibility on the circle after leaving (e.g. sole-owner case
            where the circle is destroyed).
        """
        client = get_client()
        data = await client.ocs_put_json(f"apps/circles/circles/{circle_id}/leave", json_data={})
        return json.dumps(data)


def _register_member_writes(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def add_circle_member(circle_id: str, user_id: str, member_type: str = "user") -> str:
        """Add a user, group, email, or nested circle as a member. Requires moderator+.

        Args:
            circle_id: String circle id.
            user_id: The identifier of the principal being added. For user:
                the uid. For group: the group id. For mail: the email address.
                For circle: the string circle id (singleId).
            member_type: One of "user" (default), "group", "mail", "contact",
                "circle". Determines how user_id is interpreted.

        Returns:
            JSON of the new member (id, singleId, userId, level, status).
            Status is "Member" for direct add and "Invited" when the circle
            has the INVITE config flag set.
        """
        if member_type not in MEMBER_TYPES:
            raise ValueError(f"Invalid member_type '{member_type}'. Must be one of: {sorted(MEMBER_TYPES)}")
        client = get_client()
        data = await client.ocs_post_json(
            f"apps/circles/circles/{circle_id}/members",
            json_data={"userId": user_id, "type": MEMBER_TYPES[member_type]},
        )
        return json.dumps(data)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_circle_member_level(circle_id: str, member_id: str, level: str) -> str:
        """Change a member's level (role) in the circle. Requires admin or owner.

        Args:
            circle_id: String circle id.
            member_id: The member-level id from list_circle_members (NOT the
                user's singleId or userId — use the "id" field from members).
            level: One of "member", "moderator", "admin", "owner". Promoting
                someone to "owner" transfers ownership; the caller becomes admin.

        Returns:
            JSON of the updated member (same shape as entries from
            list_circle_members: id, userId, level, status, circleId,
            singleId, …). Use the "level" field to confirm the new role.
            Note: this returns the member, not the circle.
        """
        if level not in MEMBER_LEVELS:
            raise ValueError(f"Invalid level '{level}'. Must be one of: {sorted(MEMBER_LEVELS)}")
        client = get_client()
        data = await client.ocs_put_json(
            f"apps/circles/circles/{circle_id}/members/{member_id}/level",
            json_data={"level": MEMBER_LEVELS[level]},
        )
        return json.dumps(data)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_circle(circle_id: str) -> str:
        """Delete a circle. Requires owner level. Removes all memberships.

        Args:
            circle_id: String circle id.

        Returns:
            Confirmation with the deleted id.
        """
        client = get_client()
        await client.ocs_delete(f"apps/circles/circles/{circle_id}")
        return json.dumps({"deleted_circle_id": circle_id})

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def remove_circle_member(circle_id: str, member_id: str) -> str:
        """Kick a member out of a circle. Requires moderator+. Cannot remove the owner.

        Args:
            circle_id: String circle id.
            member_id: The id from list_circle_members (not the userId).

        Returns:
            Confirmation with the removed member id.
        """
        client = get_client()
        await client.ocs_delete(f"apps/circles/circles/{circle_id}/members/{member_id}")
        return json.dumps({"removed_member_id": member_id})


def register(mcp: FastMCP) -> None:
    """Register Circles (Teams) tools with the MCP server."""
    _register_read_tools(mcp)
    _register_circle_writes(mcp)
    _register_member_writes(mcp)
    _register_destructive_tools(mcp)
