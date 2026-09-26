"""File sharing tools — list, get, create, update, delete, accept and decline shares via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import NextcloudClient, NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

SHARES_API = "apps/files_sharing/api/v1/shares"
REMOTE_SHARES_API = "apps/files_sharing/api/v1/remote_shares"
DELETED_SHARES_API = "apps/files_sharing/api/v1/deletedshares"


def _format_share(share: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from a raw share object."""
    result: dict[str, Any] = {
        "id": share.get("id"),
        "share_type": share.get("share_type"),
        "path": share.get("path"),
        "item_type": share.get("item_type"),
        "permissions": share.get("permissions"),
        "uid_owner": share.get("uid_owner"),
        "displayname_owner": share.get("displayname_owner"),
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


def _format_pending_share(share: dict[str, Any], declined: set[str]) -> dict[str, Any]:
    """Format a share waiting for the current user to accept it.

    Nextcloud reports 0 permissions until a share is accepted, so they are left out.
    """
    result = _format_share(share)
    del result["permissions"]
    result["mimetype"] = share.get("mimetype")
    result["federated"] = False
    result["declined"] = str(share.get("id")) in declined
    return result


def _share_id(share_id: int | str) -> str:
    """Check a share ID that may be passed as a string.

    Federated shares have 18-digit IDs since Nextcloud 33, more than a JSON number keeps exactly in
    many MCP clients, so the tools that handle them take the ID as the string the listings show.
    """
    share_id = str(share_id).strip()
    if not (share_id.isascii() and share_id.isdigit()):
        raise ValueError(f"Invalid share_id {share_id!r}: expected the numeric share id.")
    return share_id


async def _declined_share_ids(client: NextcloudClient) -> set[str]:
    """IDs of the group shares the current user declined or left.

    Declining a group share only hides it from the user, who then finds it among their deleted shares as
    "ocinternal:<id>"; Nextcloud still lists it as pending but refuses to accept it until it is restored.
    """
    try:
        deleted = await client.ocs_get(DELETED_SHARES_API)
    except NextcloudError:
        # The listing fails as a whole when the file of any share the user left is gone
        return set()
    prefix = "ocinternal:"
    return {str(s["id"])[len(prefix) :] for s in deleted if str(s.get("id", "")).startswith(prefix)}


# Nextcloud numbers the shares it received from other servers 0 (from a user) and 1 (to a group); report
# them with the share_type values of federated shares instead.
_REMOTE_SHARE_TYPES = {0: 6, 1: 9}


def _format_remote_share(share: dict[str, Any]) -> dict[str, Any]:
    """Format a federated share received from another server.

    The raw share also carries the token that gives access to the file on the other server, which is left out.
    """
    accepted = bool(share.get("accepted"))
    result: dict[str, Any] = {
        "id": share.get("id"),
        "share_type": _REMOTE_SHARE_TYPES.get(share.get("share_type", 0), share.get("share_type")),
        "path": share.get("mountpoint") if accepted else "/" + str(share.get("name", "")).lstrip("/"),
        "item_type": share.get("type"),
        "mimetype": share.get("mimetype"),
        "uid_owner": share.get("owner"),
        "remote": share.get("remote"),
        "federated": True,
    }
    if accepted:
        result["permissions"] = share.get("permissions")
    return result


def _find_remote_share(shares: list[dict[str, Any]], share_id: str) -> dict[str, Any] | None:
    """Find a federated share by the id it had while pending.

    Accepting a federated group share creates the user's own copy with a new id and the pending id as parent.
    """
    return next((s for s in shares if share_id in (str(s.get("id")), str(s.get("parent")))), None)


async def _received_shares(client: NextcloudClient, path: str) -> list[dict[str, Any]]:
    """The shares the current user received, from this server and federated, optionally for one path."""
    params = {"shared_with_me": "true"}
    if path:
        params["path"] = path
    try:
        local: list[dict[str, Any]] | None = await client.ocs_get(SHARES_API, params=params)
    except NextcloudError as e:
        # A session that does not see a federated share's mount yet cannot find its path
        if not (path and e.status_code == 404):
            raise
        local = None
    wanted = "/" + path.strip("/") if path else None
    federated = [
        _format_remote_share(s)
        for s in await client.ocs_get(REMOTE_SHARES_API)
        if wanted is None or str(s.get("mountpoint", "")).rstrip("/") == wanted
    ]
    if local is None and not federated:
        raise NextcloudError(f"No file or folder at {path!r}.", 404)
    return [_format_share(s) for s in local or []] + federated


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_shares(
        path: str = "",
        reshares: bool = False,
        subfiles: bool = False,
        shared_with_me: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List file/folder shares from Nextcloud.

        Without arguments, returns all shares owned by the current user.
        With a path, returns shares for that specific file or folder.
        With shared_with_me, returns the shares other users gave the current user instead.

        Args:
            path: Optional file/folder path to filter shares (e.g. "/Documents/report.pdf").
            reshares: If true, include shares by other users on the same files.
            subfiles: If true and path is a folder, list shares of files inside it (not the folder itself).
            shared_with_me: If true, list the shares the current user received from others (directly, through a
                group, a team or a Talk conversation, or from another server). "path" is then where the item
                shows up in the current user's files and uid_owner/displayname_owner say who shared it;
                federated shares have federated: true and the other server in "remote". Shares still waiting
                to be accepted are not included (see list_pending_shares). Cannot be combined with reshares or
                subfiles.
            limit: Maximum number of shares to return (1-200, default 50).
            offset: Number of shares to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of share objects) and "pagination" (count, offset, limit, has_more).
            share_type values: 0=user, 1=group, 3=public link, 4=email, 6=federated, 7=team, 9=federated group,
            10=talk room, 12=deck card.
        """
        if shared_with_me and (reshares or subfiles):
            raise ValueError("shared_with_me cannot be combined with reshares or subfiles.")
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        client = get_client()
        if shared_with_me:
            all_shares = await _received_shares(client, path)
        else:
            params: dict[str, str] = {}
            if path:
                params["path"] = path
            if reshares:
                params["reshares"] = "true"
            if subfiles:
                params["subfiles"] = "true"
            all_shares = [_format_share(s) for s in await client.ocs_get(SHARES_API, params=params)]
        page = all_shares[offset : offset + limit]
        has_more = offset + limit < len(all_shares)

        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

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

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_pending_shares() -> str:
        """List shares other users offered the current user that are waiting to be accepted.

        Shares from users on this server wait here when the user turned off accepting shares automatically (a
        personal sharing setting) or the admin made accepting them necessary; federated shares from other
        servers wait here unless they come from a trusted server, whose shares are accepted automatically by
        default. Accept one with accept_share or decline it with decline_share, passing its id and "federated".

        Returns:
            JSON list of pending shares: id, federated, share_type, path (the item's name), item_type, mimetype
            (both unknown for federated shares until accepted), uid_owner (the sharer; for federated shares a
            user on the "remote" server), plus for shares from this server displayname_owner, share_with (a
            group for group shares), expiration, note, label and declined. Nextcloud keeps offering a group
            share the user declined, so it stays on this list with declined: true; accept_share still accepts
            it. A declined federated group share also stays here.
        """
        client = get_client()
        local = await client.ocs_get(f"{SHARES_API}/pending")
        remote = await client.ocs_get(f"{REMOTE_SHARES_API}/pending")
        declined: set[str] = await _declined_share_ids(client) if local else set()
        shares = [_format_pending_share(s, declined) for s in local]
        shares += [_format_remote_share(s) for s in remote]
        return json.dumps(shares, default=str)


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


def _register_accept_share(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def accept_share(share_id: int | str, federated: bool = False) -> str:
        """Accept a share offered to the current user, adding it to their files.

        Accepting a share that was already accepted changes nothing. A group share the user declined or left
        earlier is restored first, since Nextcloud refuses to accept it otherwise.

        Args:
            share_id: The pending share's id from list_pending_shares, as a string (federated share ids can be
                too long for a JSON number).
            federated: True for a federated share from another server (federated: true in list_pending_shares).
                Shares from this server and federated shares are numbered separately.

        Returns:
            Confirmation message.
        """
        share_id = _share_id(share_id)
        client = get_client()
        if federated:
            return await _accept_federated(client, share_id)
        try:
            await client.ocs_post(f"{SHARES_API}/pending/{share_id}")
        except NextcloudError as e:
            if e.status_code != 404 or share_id not in await _declined_share_ids(client):
                raise
            await client.ocs_post(f"{DELETED_SHARES_API}/ocinternal:{share_id}")
            await client.ocs_post(f"{SHARES_API}/pending/{share_id}")
        return f"Share {share_id} accepted. Use list_shares with shared_with_me to see where it is in your files."


async def _accept_federated(client: NextcloudClient, share_id: str) -> str:
    # Nextcloud accepts a federated share again when asked, under a new name if the old one is taken, and
    # tells the other server again, so an accepted share is only reported.
    share = _find_remote_share(await client.ocs_get(REMOTE_SHARES_API), share_id)
    if share is not None:
        return f"Share {share_id} was already accepted, it is at {share.get('mountpoint')} in your files."
    await client.ocs_post(f"{REMOTE_SHARES_API}/pending/{share_id}")
    # Nextcloud only shows a federated share to sessions started after a login set it up, so log in once to
    # set it up (and learn where it landed) and continue in a new session.
    try:
        share = _find_remote_share(await client.ocs_get(REMOTE_SHARES_API, fresh_login=True), share_id)
    except (NextcloudError, OSError):
        # The share is accepted either way; only where it landed is unknown
        share = None
    finally:
        await client.renew_session()
    if share is None:
        return f"Share {share_id} accepted."
    return f"Share {share_id} accepted, it is at {share.get('mountpoint')} in your files."


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
    async def delete_share(share_id: int | str, federated: bool = False) -> str:
        """Delete (unshare) a share by its ID.

        This revokes access for the share recipient. The file/folder itself is not deleted.
        Called by the recipient on a share they received (list_shares with shared_with_me), it removes the
        share from their files instead: a share made directly to them is deleted for the owner too, a group
        share only stops showing up for them. Leaving a federated share tells the other server, except for a
        federated group share, which goes back to list_pending_shares instead, under the id it had in
        list_shares.

        Args:
            share_id: The share ID to delete. Use list_shares to find share IDs. Pass federated share IDs as
                strings: they can be too long for a JSON number.
            federated: True to leave a federated share received from another server (federated: true in
                list_shares with shared_with_me). Those are numbered separately from this server's shares.

        Returns:
            Confirmation message.
        """
        share_id = _share_id(share_id)
        client = get_client()
        if federated:
            # Nextcloud finds the share through its mount, which a session may not know about (one started
            # before the share was accepted, in the web interface for one) and then fails with "Could not unshare".
            await client.ocs_delete(f"{REMOTE_SHARES_API}/{share_id}", fresh_login=True)
        else:
            await client.ocs_delete(f"{SHARES_API}/{share_id}")
        return f"Share {share_id} deleted."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def decline_share(share_id: int | str, federated: bool = False) -> str:
        """Decline a share offered to the current user that is waiting to be accepted.

        Declining a share made directly to the user deletes it, so the owner has to share again to offer it
        again. A declined group share is only hidden from this user: Nextcloud keeps it in list_pending_shares
        (marked declined) and accept_share can still take it. A declined federated share is removed and the
        other server is told, except for a federated group share, which Nextcloud keeps offering (under a new
        id once the user had accepted it before).

        Args:
            share_id: The pending share's id from list_pending_shares, as a string (federated share ids can be
                too long for a JSON number).
            federated: True for a federated share from another server (federated: true in list_pending_shares).

        Returns:
            Confirmation message.
        """
        share_id = _share_id(share_id)
        client = get_client()
        # Nextcloud declines a share by deleting it, which works on shares the user already accepted too (and,
        # for shares from this server, on their own shares), so check that this one is really waiting.
        api = REMOTE_SHARES_API if federated else SHARES_API
        pending = await client.ocs_get(f"{api}/pending")
        if not any(str(s.get("id")) == share_id for s in pending):
            msg = (
                f"Share {share_id} is not waiting to be accepted by you. Use list_pending_shares for the ids; "
                "to leave a share you already accepted or to remove your own share, use delete_share."
            )
            raise ValueError(msg)
        await client.ocs_delete(f"{REMOTE_SHARES_API}/pending/{share_id}" if federated else f"{SHARES_API}/{share_id}")
        return f"Share {share_id} declined."


def register(mcp: FastMCP) -> None:
    """Register file sharing tools with the MCP server."""
    _register_read_tools(mcp)
    _register_create_share(mcp)
    _register_accept_share(mcp)
    _register_update_share(mcp)
    _register_destructive_tools(mcp)
