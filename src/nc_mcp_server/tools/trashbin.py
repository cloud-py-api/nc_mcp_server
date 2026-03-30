"""Files Trashbin tools — list, restore, and empty trash via WebDAV API."""

import contextlib
import json
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import unquote as url_unquote

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import DAV_NS, NC_NS, OC_NS
from ..permissions import PermissionLevel, require_permission
from ..state import get_client, get_config

TRASHBIN_PROPFIND_BODY = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">'
    "<d:prop>"
    "<d:getlastmodified/><d:getcontentlength/><d:resourcetype/>"
    "<oc:fileid/><nc:trashbin-filename/>"
    "<nc:trashbin-original-location/><nc:trashbin-deletion-time/>"
    "</d:prop></d:propfind>"
)


_TRASH_PROPS = [
    (f"{{{NC_NS}}}trashbin-filename", "original_name"),
    (f"{{{NC_NS}}}trashbin-original-location", "original_location"),
    (f"{{{NC_NS}}}trashbin-deletion-time", "deletion_time"),
    (f"{{{DAV_NS}}}getlastmodified", "last_modified"),
    (f"{{{DAV_NS}}}getcontentlength", "size"),
    (f"{{{OC_NS}}}fileid", "file_id"),
]


def _parse_trash_entry(prop: ET.Element, trash_path: str) -> dict[str, Any]:
    """Parse a single trashbin item from its DAV prop element."""
    resource_type = prop.find(f"{{{DAV_NS}}}resourcetype")
    is_dir = resource_type is not None and resource_type.find(f"{{{DAV_NS}}}collection") is not None
    entry: dict[str, Any] = {"trash_path": trash_path, "is_directory": is_dir}
    for tag, key in _TRASH_PROPS:
        el = prop.find(tag)
        if el is not None and el.text:
            entry[key] = el.text
    for int_key in ("deletion_time", "size", "file_id"):
        if int_key in entry:
            with contextlib.suppress(ValueError, TypeError):
                entry[int_key] = int(entry[int_key])
    return entry


def _parse_trash_xml(xml_text: str, user: str) -> list[dict[str, Any]]:
    """Parse a trashbin PROPFIND response into a list of trashed item dicts."""
    root = ET.fromstring(xml_text)  # noqa: S314
    entries: list[dict[str, Any]] = []
    trash_prefix = f"/remote.php/dav/trashbin/{user}/trash/"

    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_el = response.find(f"{{{DAV_NS}}}href")
        if href_el is None or href_el.text is None:
            continue
        href = url_unquote(href_el.text)
        if href.rstrip("/") == trash_prefix.rstrip("/"):
            continue
        trash_path = href.split(trash_prefix, 1)[1].rstrip("/") if trash_prefix in href else ""
        if not trash_path:
            continue
        propstat = response.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            continue
        entries.append(_parse_trash_entry(prop, trash_path))

    return entries


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_trash() -> str:
        """List all items in the Nextcloud trash bin.

        Returns files and folders that were deleted and can be restored.
        Each item includes its original filename, original path, deletion
        time, and a trash_path identifier needed for restore/delete operations.

        Returns:
            JSON list of trashed items, each with: trash_path, original_name,
            original_location, deletion_time (unix), is_directory, size, file_id.
            Use trash_path with restore_trash_item or delete operations.
        """
        client = get_client()
        xml_text = await client.trashbin_propfind()
        entries = _parse_trash_xml(xml_text, get_config().user)
        return json.dumps(entries, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def restore_trash_item(trash_path: str) -> str:
        """Restore a file or folder from the trash bin to its original location.

        The item is restored to its original path. If the original path no
        longer exists, it is restored to the user's root folder. If a file
        with the same name already exists, a numeric suffix is added.

        Args:
            trash_path: The trash path identifier from list_trash
                        (e.g. "document.txt.d1711000000").

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.trashbin_restore(trash_path)
        name = trash_path.rsplit(".d", 1)[0] if ".d" in trash_path else trash_path
        return f"Restored '{name}' from trash."


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def empty_trash() -> str:
        """Permanently delete ALL items in the trash bin.

        This action is irreversible. All trashed files and folders will
        be permanently destroyed and cannot be recovered.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.trashbin_delete()
        return "Trash emptied."


def register(mcp: FastMCP) -> None:
    """Register trashbin tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
