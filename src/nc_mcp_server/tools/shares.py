"""File sharing tools — list, get, create, update, and delete shares via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

SHARES_API = "apps/files_sharing/api/v1/shares"


def _format_share(share: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from a raw share object."""
    result: dict[str, Any] = {
        "id": share.get("id"),
        "share_type": share.get("share_type"),
        "path": share.get("path"),
        "item_type": share.get("item_type"),
        "permissions": share.get("permissions"),
        "uid_owner": share.get("uid_owner"),
        "share_with": share.get("share_with"),
        "share_with_displayname": share.get("share_with_displayname"),
        "expiration": share.get("expiration"),
        "note": share.get("note"),
        "label": share.get("label"),
    }
    if share.get("token"):
        result["token"] = share["token"]
    if share.get("url"):
        result["url"] = share["url"]
    if share.get("password"):
        result["has_password"] = True
    if "hide_download" in share:
        result["hide_download"] = share["hide_download"]
    return result


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_shares(
        path: str = "",
        reshares: bool = False,
        subfiles: bool = False,
    ) -> str:
        """List file/folder shares from Nextcloud.

        Without arguments, returns all shares owned by the current user.
        With a path, returns shares for that specific file or folder.

        Args:
            path: Optional file/folder path to filter shares (e.g. "/Documents/report.pdf").
            reshares: If true, include shares by other users on the same files.
            subfiles: If true and path is a folder, list shares of files inside it (not the folder itself).

        Returns:
            JSON list of share objects with: id, share_type, path, permissions, share_with, etc.
            share_type values: 0=user, 1=group, 3=public link, 4=email, 6=federated, 10=talk room.
        """
        client = get_client()
        params: dict[str, str] = {}
        if path:
            params["path"] = path
        if reshares:
            params["reshares"] = "true"
        if subfiles:
            params["subfiles"] = "true"
        data = await client.ocs_get(SHARES_API, params=params)
        shares = [_format_share(s) for s in data]
        return json.dumps(shares, default=str)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_share(share_id: int) -> str:
        """Get details of a specific share by its ID.

        Args:
            share_id: The numeric share ID.

        Returns:
            JSON object with share details: id, share_type, path, permissions, share_with,
            url (for link shares), expiration, note, label, etc.
        """
        client = get_client()
        data = await client.ocs_get(f"{SHARES_API}/{share_id}")
        share = _format_share(data[0])
        return json.dumps(share, default=str)


_SUPPORTED_SHARE_TYPES = {0, 1, 3, 4, 6, 10}
_RECIPIENT_SHARE_TYPES = {0, 1, 4, 6, 10}
_PASSWORD_SHARE_TYPES = {3, 4}


def _validate_create_share(share_type: int, share_with: str, password: str, label: str, public_upload: bool) -> None:
    if share_type not in _SUPPORTED_SHARE_TYPES:
        msg = f"Unsupported share_type {share_type}. Valid: 0=user, 1=group, 3=link, 4=email, 6=federated, 10=talk."
        raise ValueError(msg)
    if share_type in _RECIPIENT_SHARE_TYPES and not share_with:
        raise ValueError("share_with is required for user, group, email, federated, and talk room shares.")
    if password and share_type not in _PASSWORD_SHARE_TYPES:
        raise ValueError("password is only valid for link (3) and email (4) shares.")
    if label and share_type != 3:
        raise ValueError("label is only valid for public link (3) shares.")
    if public_upload and share_type != 3:
        raise ValueError("public_upload is only valid for public link (3) shares.")


def _register_create_share(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_share(
        path: str,
        share_type: int,
        share_with: str = "",
        permissions: int = 0,
        password: str = "",
        expire_date: str = "",
        note: str = "",
        label: str = "",
        public_upload: bool = False,
    ) -> str:
        """Create a new share for a file or folder.

        Args:
            path: Path to the file or folder to share (e.g. "/Documents/report.pdf").
            share_type: Type of share: 0=user, 1=group, 3=public link, 4=email, 6=federated, 10=talk room.
            share_with: Recipient — required for all types except link (3).
                User share (0): username. Group share (1): group name.
                Email share (4): email address. Federated (6): user@remote.server.
                Talk room (10): room token.
            permissions: Bitwise permission flags. 1=read, 2=update, 4=create, 8=delete, 16=share.
                Common values: 1 (read-only), 15 (full, no reshare), 31 (all).
                Default: all permissions (31) for user/group, read-only (1) for links.
                Note: file shares automatically strip create (4) and delete (8) flags.
            password: Optional password for link (3) or email (4) shares.
            expire_date: Optional expiration date in "YYYY-MM-DD" format.
            note: Optional note/message for the share recipient.
            label: Optional display label for link shares (max 255 chars).
            public_upload: Enable public upload on shared folders (link shares only).

        Returns:
            JSON object with the created share details including id, url (for links), token, etc.
        """
        _validate_create_share(share_type, share_with, password, label, public_upload)
        client = get_client()
        data: dict[str, str | int] = {"path": path, "shareType": share_type}
        if share_with:
            data["shareWith"] = share_with
        if permissions > 0:
            data["permissions"] = permissions
        if password:
            data["password"] = password
        if expire_date:
            data["expireDate"] = expire_date
        if note:
            data["note"] = note
        if label:
            data["label"] = label
        if public_upload:
            data["publicUpload"] = "true"
        result = await client.ocs_post(SHARES_API, data=data)
        return json.dumps(_format_share(result), default=str)


def _register_update_share(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_share(
        share_id: int,
        permissions: int | None = None,
        password: str | None = None,
        expire_date: str | None = None,
        note: str | None = None,
        label: str | None = None,
        public_upload: bool | None = None,
        hide_download: bool | None = None,
    ) -> str:
        """Update properties of an existing share.

        Only provided parameters are changed. Omitted parameters keep their current value.

        Args:
            share_id: The numeric share ID to update.
            permissions: New permission flags (1=read, 2=update, 4=create, 8=delete, 16=share).
            password: Set or change password (link/email shares only). Pass "" to remove password.
            expire_date: Set expiration in "YYYY-MM-DD" format. Pass "" to remove expiration.
            note: Set or update the share note. Pass "" to clear.
            label: Set or update the share label. Pass "" to clear.
            public_upload: Enable (true) or disable (false) public upload on shared folders (link shares only).
            hide_download: Show (false) or hide (true) the download button on public link shares.

        Returns:
            JSON object with the updated share details.
        """
        client = get_client()
        data: dict[str, str | int] = {}
        if permissions is not None:
            data["permissions"] = permissions
        if password is not None:
            data["password"] = password
        if expire_date is not None:
            data["expireDate"] = expire_date
        if note is not None:
            data["note"] = note
        if label is not None:
            data["label"] = label
        if public_upload is not None:
            data["publicUpload"] = "true" if public_upload else "false"
        if hide_download is not None:
            data["hideDownload"] = "true" if hide_download else "false"
        result = await client.ocs_put(f"{SHARES_API}/{share_id}", data=data)
        return json.dumps(_format_share(result), default=str)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_share(share_id: int) -> str:
        """Delete (unshare) a share by its ID.

        This revokes access for the share recipient. The file/folder itself is not deleted.

        Args:
            share_id: The numeric share ID to delete. Use list_shares to find share IDs.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{SHARES_API}/{share_id}")
        return f"Share {share_id} deleted."


def register(mcp: FastMCP) -> None:
    """Register file sharing tools with the MCP server."""
    _register_read_tools(mcp)
    _register_create_share(mcp)
    _register_update_share(mcp)
    _register_destructive_tools(mcp)
