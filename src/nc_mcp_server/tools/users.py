"""User management tools: get, create, update, enable/disable and delete users via OCS API."""

import json
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, DESTRUCTIVE, READONLY
from ..client import NextcloudClient, NextcloudError
from ..permissions import (
    PermissionDeniedError,
    PermissionLevel,
    get_permission_level,
    require_permission,
)
from ..state import get_client

# Nextcloud 34 and 35 turn away regular users from PATCH cloud/users/{userId} with this message, even
# for their own account: the endpoint lacks the #[NoSubAdminRequired] attribute the per-key PUT has.
_SUBADMIN_ONLY = "Logged in account must be at least a sub admin"

# Fields a user may set on their own account through the per-key PUT, with the key that endpoint
# takes. The password is left out here and set last, because changing it can end the session.
_OWN_ACCOUNT_KEYS = {"displayName": "displayname", "email": "email", "language": "language"}


# update_user's arguments by the field names the PATCH endpoint takes
_TOOL_ARGS = {
    "displayName": "display_name",
    "email": "email",
    "password": "password",
    "quota": "quota",
    "language": "language",
    "manager": "manager",
    "groups": "groups",
    "subadminGroups": "subadmin_groups",
}


def _user_path(user_id: str) -> str:
    """OCS path of a user; the ID is encoded because it may hold a space, quote or "@"."""
    return f"cloud/users/{quote(user_id, safe='')}"


async def _update_own_account(client: NextcloudClient, user_id: str, body: dict[str, Any]) -> Any:
    """Apply the fields one at a time through the per-key endpoint, for a regular user's own account."""
    admin_only = [_TOOL_ARGS[key] for key in body if key not in _OWN_ACCOUNT_KEYS and key != "password"]
    if admin_only:
        raise NextcloudError(
            f"Only admins and sub-admins can change {', '.join(admin_only)}; on your own account you can"
            " change display_name, email, language and password.",
            403,
        )
    path = _user_path(user_id)

    async def put(field: str, key: str, value: Any) -> None:
        try:
            await client.ocs_put(path, data={"key": key, "value": value})
        except NextcloudError as e:
            # This endpoint often answers a bad value with an empty message, so name the field
            raise NextcloudError(f"{_TOOL_ARGS[field]} was rejected ({e})", e.status_code) from e

    for field, key in _OWN_ACCOUNT_KEYS.items():
        if field in body:
            value = body[field]
            if field == "displayName" and not value:
                value = user_id  # what the PATCH endpoint makes of an empty name; the PUT one refuses it
            await put(field, key, value)
    data = await client.ocs_get(path)
    if "password" in body:
        await put("password", "password", body["password"])
    return data


async def _refuse_own_admin_removal(client: NextcloudClient, user_id: str, groups: list[str]) -> None:
    """Stop an admin from dropping their own "admin" membership by leaving it out of a complete group list.

    The per-group endpoint refuses this ("Cannot remove yourself from the admin group"); the PATCH one
    does not, and a list meant as "add me to X" would quietly cost this server its admin rights.
    """
    if "admin" in groups:
        return
    me = await client.ocs_get("cloud/user")
    if str(me["id"]).lower() == user_id.lower() and "admin" in me.get("groups", []):
        raise NextcloudError(
            'Refusing to take your own account out of the "admin" group: groups is the complete list of the'
            ' user\'s groups, so include "admin" to keep it. Another admin has to remove you if that is intended.',
            400,
        )


async def _patch_user(client: NextcloudClient, user_id: str, body: dict[str, Any]) -> Any:
    """Send the PATCH, falling back to per-key updates when a regular user edits their own account."""
    try:
        return await client.ocs_patch_json(_user_path(user_id), json_data=body)
    except NextcloudError as e:
        if _SUBADMIN_ONLY not in str(e):
            raise
        # The login name can differ from the user ID in case, or be an email or LDAP name
        own_id: str = (await client.ocs_get("cloud/user"))["id"]
        if own_id.lower() != user_id.lower():
            raise
        return await _update_own_account(client, own_id, body)


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
        """List Nextcloud users. Uses server-side pagination.

        Args:
            search: Optional search string to filter users by name/email.
            limit: Maximum number of users to return (1-200, default 25).
            offset: Number of users to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of user ID strings) and "pagination"
            (count, offset, limit, has_more).
        """
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        client = get_client()
        params = {"search": search, "limit": str(limit), "offset": str(offset)}
        data = await client.ocs_get("cloud/users", params=params)
        raw = data["users"] if isinstance(data, dict) and "users" in data else data
        users: list[str] = list(raw) if not isinstance(raw, list) else raw
        has_more = len(users) == limit
        return json.dumps(
            {
                "data": users,
                "pagination": {"count": len(users), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

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
        data = await client.ocs_get(_user_path(user_id))
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

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.WRITE)
    async def update_user(
        user_id: str,
        display_name: str | None = None,
        email: str | None = None,
        password: str | None = None,
        quota: str | None = None,
        language: str | None = None,
        manager: str | None = None,
        groups: list[str] | None = None,
        subadmin_groups: list[str] | None = None,
    ) -> str:
        """Change several fields of a user account in one call. Needs Nextcloud 34 or newer.

        Only the fields you pass are changed. Nextcloud validates all of them first and
        applies none if one fails; the error then names each rejected field. A few
        requests are skipped without an error, so compare the returned "groups" and
        "subadmin" with what you asked for: a sub-admin cannot take the user out of
        groups they do not administer, only full admins can add someone to "admin",
        and nobody can be made sub-admin of "admin".

        Changing another user needs admin rights, or sub-admin rights over one of
        their groups; sub-admin groups need admin rights. Any user can change their
        own display name (if the instance allows it), email, language and password.
        Nextcloud only accepts this call from admins and sub-admins, though, so for a
        regular user's own account the fields are set one by one instead, and a
        rejected field then no longer undoes the ones set before it.

        After changing your own password, the old one stops working, including for
        this server if it logs in with it; app passwords keep working.

        Args:
            user_id: The user to change. Example: "john.doe"
            display_name: New display name. An empty string resets it to the user ID.
            email: New primary email address. An empty string removes it.
            password: New password. Must satisfy the instance's password policy.
            quota: Storage quota, e.g. "5 GB", "500 MB", a byte count, "none"
                (unlimited) or "default".
            language: Language code, e.g. "en", "de", "fr".
            manager: User ID of the user's manager (Nextcloud does not check that it
                exists). An empty string removes it.
            groups: The complete list of group IDs the user should be in: groups
                missing from it are left, new ones joined, and [] leaves every group.
                Get the current ones with get_user and IDs with list_groups. Needs the
                destructive permission level, since it can remove memberships. Taking
                your own account out of "admin" this way is refused.
            subadmin_groups: The complete list of groups the user should administer
                as a sub-admin; [] removes all. Needs the destructive permission level.

        Returns:
            JSON with the user's details after the change, as get_user returns them.
        """
        values: dict[str, Any] = {
            "display_name": display_name,
            "email": email,
            "password": password,
            "quota": quota,
            "language": language,
            "manager": manager,
            "groups": groups,
            "subadmin_groups": subadmin_groups,
        }
        body = {field: values[arg] for field, arg in _TOOL_ARGS.items() if values[arg] is not None}
        if not body:
            raise ValueError("Pass at least one field to change.")
        current = get_permission_level()
        if ("groups" in body or "subadminGroups" in body) and not current.includes(PermissionLevel.DESTRUCTIVE):
            raise PermissionDeniedError("update_user with groups", PermissionLevel.DESTRUCTIVE, current)
        client = get_client()
        if groups is not None:
            await _refuse_own_admin_removal(client, user_id, groups)
        data = await _patch_user(client, user_id, body)
        return json.dumps(data, default=str)


def _register_account_state_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.WRITE)
    async def set_user_enabled(user_id: str, enabled: bool) -> str:
        """Enable or disable a user account. Requires admin (or sub-admin) privileges.

        A disabled user cannot log in and their sessions and app passwords stop
        working, but the account, its files and shares are kept. Enable it again
        to restore access. Nobody can enable or disable their own account.
        Disabling needs the destructive permission level, enabling the write level.

        Args:
            user_id: The user to enable or disable.
            enabled: True to enable the account, false to disable it.

        Returns:
            Confirmation message.
        """
        current = get_permission_level()
        if not enabled and not current.includes(PermissionLevel.DESTRUCTIVE):
            raise PermissionDeniedError("set_user_enabled with enabled=false", PermissionLevel.DESTRUCTIVE, current)
        client = get_client()
        action = "enable" if enabled else "disable"
        try:
            await client.ocs_put(f"{_user_path(user_id)}/{action}")
        except NextcloudError as e:
            if e.status_code != 400:
                raise
            # Nextcloud answers both cases with the same empty-message error
            raise NextcloudError(f"{e}: the user does not exist, or it is your own account", 400) from e
        return f"User '{user_id}' {action}d."


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_user(user_id: str) -> str:
        """Permanently delete a Nextcloud user. Requires admin privileges.

        This cannot be undone. The user's data and files will be removed.
        To only block access, disable the account with set_user_enabled instead.

        Args:
            user_id: The user ID to delete.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(_user_path(user_id))
        return f"User '{user_id}' deleted."


def register(mcp: FastMCP) -> None:
    """Register user tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_account_state_tools(mcp)
    _register_destructive_tools(mcp)
