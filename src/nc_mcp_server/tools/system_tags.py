"""System tag tools — list, create, assign, and delete tags via WebDAV API."""

import json
import xml.etree.ElementTree as ET
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

OC_NS = "http://owncloud.org/ns"
DAV_NS = "DAV:"

TAG_PROPFIND_BODY = """\
<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:prop>
    <oc:id/>
    <oc:display-name/>
    <oc:user-visible/>
    <oc:user-assignable/>
  </d:prop>
</d:propfind>"""


def _parse_tags_xml(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)  # noqa: S314
    tags: list[dict[str, Any]] = []
    for response in root.findall(f"{{{DAV_NS}}}response"):
        propstat = response.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue
        status_el = propstat.find(f"{{{DAV_NS}}}status")
        if status_el is None or "200" not in (status_el.text or ""):
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            continue
        tag_id_el = prop.find(f"{{{OC_NS}}}id")
        if tag_id_el is None or not tag_id_el.text:
            continue
        name_el = prop.find(f"{{{OC_NS}}}display-name")
        visible_el = prop.find(f"{{{OC_NS}}}user-visible")
        assignable_el = prop.find(f"{{{OC_NS}}}user-assignable")
        tags.append(
            {
                "id": int(tag_id_el.text),
                "name": name_el.text if name_el is not None and name_el.text else "",
                "user_visible": (visible_el.text or "").lower() == "true" if visible_el is not None else True,
                "user_assignable": (assignable_el.text or "").lower() == "true" if assignable_el is not None else True,
            }
        )
    return tags


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_tags(limit: int = 50, offset: int = 0) -> str:
        """List system tags available in this Nextcloud instance.

        System tags are shared labels that can be assigned to files for
        organization and filtering.

        Args:
            limit: Maximum number of tags to return (1-500, default 50).
            offset: Number of tags to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of tags with id, name, user_visible,
            user_assignable) and "pagination" (count, offset, limit, has_more).
        """
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        client = get_client()
        response = await client.dav_request(
            "PROPFIND",
            "systemtags/",
            body=TAG_PROPFIND_BODY,
            headers={"Content-Type": "application/xml; charset=utf-8"},
            context="List system tags",
        )
        all_tags = _parse_tags_xml(response.text or "")
        page = all_tags[offset : offset + limit]
        has_more = offset + limit < len(all_tags)
        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_file_tags(file_id: int) -> str:
        """Get all system tags assigned to a file.

        Use list_directory to find a file's numeric ID (file_id field).

        Args:
            file_id: The numeric file ID.

        Returns:
            JSON list of tags assigned to this file.
        """
        client = get_client()
        response = await client.dav_request(
            "PROPFIND",
            f"systemtags-relations/files/{file_id}/",
            body=TAG_PROPFIND_BODY,
            headers={"Content-Type": "application/xml; charset=utf-8"},
            context=f"Get tags for file {file_id}",
        )
        tags = _parse_tags_xml(response.text or "")
        return json.dumps(tags)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_tag(name: str, user_visible: bool = True, user_assignable: bool = True) -> str:
        """Create a new system tag. Requires admin privileges for non-visible/non-assignable tags.

        Args:
            name: Tag display name.
            user_visible: Whether regular users can see this tag (default: true).
            user_assignable: Whether regular users can assign this tag to files (default: true).

        Returns:
            JSON with the created tag ID and name.
        """
        client = get_client()
        response = await client.dav_request(
            "POST",
            "systemtags/",
            body=json.dumps(
                {
                    "name": name,
                    "userVisible": user_visible,
                    "userAssignable": user_assignable,
                }
            ),
            headers={"Content-Type": "application/json"},
            context=f"Create tag '{name}'",
        )
        location = str(response.headers.get("Content-Location", ""))
        tag_id_str = location.rstrip("/").split("/")[-1] if location else "0"
        tag_id = int(tag_id_str) if tag_id_str.isdigit() else 0
        return json.dumps({"id": tag_id, "name": name})

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def assign_tag(file_id: int, tag_id: int) -> str:
        """Assign a system tag to a file.

        Use list_tags to find available tag IDs and list_directory to find file IDs.
        Assigning an already-assigned tag has no effect.

        Args:
            file_id: The numeric file ID.
            tag_id: The tag ID to assign.

        Returns:
            Confirmation message.
        """
        client = get_client()
        try:
            await client.dav_request(
                "PUT",
                f"systemtags-relations/files/{file_id}/{tag_id}",
                context=f"Assign tag {tag_id} to file {file_id}",
            )
        except NextcloudError as e:
            if e.status_code == 409:
                return f"Tag {tag_id} already assigned to file {file_id}."
            raise
        return f"Tag {tag_id} assigned to file {file_id}."


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def unassign_tag(file_id: int, tag_id: int) -> str:
        """Remove a system tag from a file.

        Args:
            file_id: The numeric file ID.
            tag_id: The tag ID to remove.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.dav_request(
            "DELETE",
            f"systemtags-relations/files/{file_id}/{tag_id}",
            context=f"Unassign tag {tag_id} from file {file_id}",
        )
        return f"Tag {tag_id} removed from file {file_id}."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_tag(tag_id: int) -> str:
        """Permanently delete a system tag.

        This removes the tag from all files it was assigned to.
        This action is irreversible.

        Args:
            tag_id: The tag ID to delete.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.dav_request(
            "DELETE",
            f"systemtags/{tag_id}",
            context=f"Delete tag {tag_id}",
        )
        return f"Tag {tag_id} deleted."


def register(mcp: FastMCP) -> None:
    """Register system tag tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
