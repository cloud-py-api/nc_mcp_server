"""File comment tools — list, add, edit, and delete comments via WebDAV API."""

import json
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

OC_NS = "http://owncloud.org/ns"
DAV_NS = "DAV:"

FILTER_COMMENTS_BODY = """\
<?xml version="1.0" encoding="utf-8"?>
<oc:filter-comments xmlns:oc="http://owncloud.org/ns" xmlns:D="DAV:">
  <oc:limit>{limit}</oc:limit>
  <oc:offset>{offset}</oc:offset>
</oc:filter-comments>"""


_COMMENT_FIELDS = {
    "actorType": "actor_type",
    "actorId": "actor_id",
    "actorDisplayName": "actor_display_name",
    "message": "message",
    "verb": "verb",
    "creationDateTime": "created",
    "objectType": "object_type",
    "objectId": "object_id",
    "parentId": "parent_id",
    "childrenCount": "children_count",
    "isUnread": "is_unread",
}

_INT_FIELDS = {"parent_id", "children_count", "object_id"}


def _parse_mentions(prop: ET.Element) -> list[dict[str, str]]:
    """Parse mentions from a comment's prop element."""
    mentions_el = prop.find(f"{{{OC_NS}}}mentions")
    if mentions_el is None:
        return []
    mentions: list[dict[str, str]] = []
    for m in mentions_el.findall(f"{{{OC_NS}}}mention"):
        mention: dict[str, str] = {}
        for tag in ("mentionType", "mentionId", "mentionDisplayName"):
            el = m.find(f"{{{OC_NS}}}{tag}")
            if el is not None and el.text:
                mention[tag] = el.text
        if mention:
            mentions.append(mention)
    return mentions


def _parse_comment_prop(prop: ET.Element, comment_id: int) -> dict[str, Any]:
    """Parse a single comment's properties from a DAV prop element."""
    comment: dict[str, Any] = {"id": comment_id}
    for oc_name, key in _COMMENT_FIELDS.items():
        el = prop.find(f"{{{OC_NS}}}{oc_name}")
        if el is not None and el.text is not None:
            comment[key] = el.text
    for field in _INT_FIELDS:
        if field in comment:
            comment[field] = int(comment[field])
    if "is_unread" in comment:
        comment["is_unread"] = comment["is_unread"] == "true"
    mentions = _parse_mentions(prop)
    if mentions:
        comment["mentions"] = mentions
    return comment


def _parse_comments_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse a REPORT response into a list of comment dicts."""
    root = ET.fromstring(xml_text)  # noqa: S314
    comments: list[dict[str, Any]] = []
    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_el = response.find(f"{{{DAV_NS}}}href")
        if href_el is None or href_el.text is None:
            continue
        parts = href_el.text.rstrip("/").split("/")
        comment_id = parts[-1] if parts else ""
        if not comment_id.isdigit():
            continue
        propstat = response.find(f"{{{DAV_NS}}}propstat")
        if propstat is None:
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is None:
            continue
        comments.append(_parse_comment_prop(prop, int(comment_id)))
    return comments


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_comments(file_id: int, limit: int = 20, offset: int = 0) -> str:
        """List comments on a file.

        Returns comments in chronological order (oldest first).
        Use list_directory to find a file's ID (file_id field).

        Args:
            file_id: The numeric file ID. Get this from list_directory (file_id field).
            limit: Maximum number of comments to return (1-100, default: 20).
            offset: Number of comments to skip for pagination (default: 0).

        Returns:
            JSON object with "data" (list of comments) and "pagination"
            (count, offset, has_more).
        """
        limit = max(1, min(100, limit))
        offset = max(0, offset)
        client = get_client()
        body = FILTER_COMMENTS_BODY.format(limit=limit, offset=offset)
        response = await client.dav_request(
            "REPORT",
            f"comments/files/{file_id}",
            body=body,
            headers={"Content-Type": "application/xml; charset=utf-8"},
            context=f"List comments on file {file_id}",
        )
        comments = _parse_comments_xml(response.text or "")
        response_data: dict[str, Any] = {
            "data": comments,
            "pagination": {
                "count": len(comments),
                "offset": offset,
                "has_more": len(comments) == limit,
            },
        }
        return json.dumps(response_data, indent=2, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def add_comment(file_id: int, message: str) -> str:
        """Add a comment to a file.

        Use @username in the message to mention other users.
        Maximum message length is 1000 characters.

        Args:
            file_id: The numeric file ID to comment on.
            message: The comment text (max 1000 characters). Use @username to mention users.

        Returns:
            Confirmation with the new comment ID.
        """
        if not message.strip():
            raise ValueError("Comment message cannot be empty.")
        if len(message) > 1000:
            raise ValueError(f"Comment message too long ({len(message)} chars). Maximum is 1000.")
        client = get_client()
        body = json.dumps({"actorType": "users", "verb": "comment", "message": message})
        response = await client.dav_request(
            "POST",
            f"comments/files/{file_id}",
            body=body,
            headers={"Content-Type": "application/json"},
            context=f"Add comment on file {file_id}",
        )
        location = str(response.headers.get("Content-Location", ""))
        comment_id = location.rstrip("/").split("/")[-1] if location else "unknown"
        return json.dumps({"id": comment_id, "message": message}, indent=2)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def edit_comment(file_id: int, comment_id: int, message: str) -> str:
        """Edit a comment on a file.

        Only the comment author can edit their own comment.

        Args:
            file_id: The numeric file ID.
            comment_id: The comment ID to edit.
            message: The new comment text (max 1000 characters).

        Returns:
            Confirmation message.
        """
        if not message.strip():
            raise ValueError("Comment message cannot be empty.")
        if len(message) > 1000:
            raise ValueError(f"Comment message too long ({len(message)} chars). Maximum is 1000.")
        client = get_client()
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<d:propertyupdate xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            "<d:set><d:prop>"
            f"<oc:message>{xml_escape(message)}</oc:message>"
            "</d:prop></d:set></d:propertyupdate>"
        )
        await client.dav_request(
            "PROPPATCH",
            f"comments/files/{file_id}/{comment_id}",
            body=body,
            headers={"Content-Type": "application/xml; charset=utf-8"},
            context=f"Edit comment {comment_id} on file {file_id}",
        )
        return json.dumps({"id": comment_id, "message": message}, indent=2)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_comment(file_id: int, comment_id: int) -> str:
        """Delete a comment from a file.

        Only the comment author can delete their own comment.
        This action is irreversible.

        Args:
            file_id: The numeric file ID.
            comment_id: The comment ID to delete.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.dav_request(
            "DELETE",
            f"comments/files/{file_id}/{comment_id}",
            context=f"Delete comment {comment_id} on file {file_id}",
        )
        return f"Comment {comment_id} deleted from file {file_id}."


def register(mcp: FastMCP) -> None:
    """Register comment tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
