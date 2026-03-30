"""Files Versions tools — list and restore file versions via WebDAV API."""

import contextlib
import json
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import unquote as url_unquote

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE_IDEMPOTENT, READONLY
from ..client import DAV_NS, NC_NS
from ..permissions import PermissionLevel, require_permission
from ..state import get_client, get_config

_VERSION_PROPS = [
    (f"{{{DAV_NS}}}getlastmodified", "last_modified"),
    (f"{{{DAV_NS}}}getcontentlength", "size"),
    (f"{{{DAV_NS}}}getcontenttype", "content_type"),
    (f"{{{NC_NS}}}version-author", "author"),
    (f"{{{NC_NS}}}version-label", "label"),
]


def _parse_versions_xml(xml_text: str, user: str, file_id: int) -> list[dict[str, Any]]:
    """Parse a versions PROPFIND response into a list of version dicts."""
    root = ET.fromstring(xml_text)  # noqa: S314
    entries: list[dict[str, Any]] = []
    prefix = f"/remote.php/dav/versions/{user}/versions/{file_id}/"

    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_el = response.find(f"{{{DAV_NS}}}href")
        if href_el is None or href_el.text is None:
            continue
        href = url_unquote(href_el.text)
        if href.rstrip("/") == prefix.rstrip("/"):
            continue
        version_id = href.split(prefix, 1)[1].rstrip("/") if prefix in href else ""
        if not version_id:
            continue
        propstat = response.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            continue
        entry: dict[str, Any] = {"version_id": version_id}
        for tag, key in _VERSION_PROPS:
            el = prop.find(tag)
            if el is not None and el.text:
                entry[key] = el.text
        if "size" in entry:
            with contextlib.suppress(ValueError, TypeError):
                entry["size"] = int(entry["size"])
        entries.append(entry)

    return entries


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_versions(file_id: int) -> str:
        """List all versions of a file by its file ID.

        Returns the version history including the current version.
        Use the file_id from list_directory or search_files results.

        Args:
            file_id: The numeric Nextcloud file ID.

        Returns:
            JSON list of versions, each with: version_id (unix timestamp),
            last_modified, size, content_type, author, and optionally label.
            Use version_id with restore_version to revert the file.
        """
        client = get_client()
        xml_text = await client.versions_propfind(file_id)
        entries = _parse_versions_xml(xml_text, get_config().user, file_id)
        return json.dumps(entries, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def restore_version(file_id: int, version_id: str) -> str:
        """Restore a file to a previous version.

        The file's current content is replaced with the content from the
        specified version. The pre-restore content is preserved as a new
        version in the history, so no data is lost.

        Args:
            file_id: The numeric Nextcloud file ID.
            version_id: The version identifier from list_versions
                        (a unix timestamp string, e.g. "1711000000").

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.versions_restore(file_id, version_id)
        return f"Restored file {file_id} to version {version_id}."


def register(mcp: FastMCP) -> None:
    """Register file version tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
