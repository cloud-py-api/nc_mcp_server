"""File management tools — list, read, upload, copy, delete, move, search files via WebDAV."""

import base64
import json
from xml.sax.saxutils import escape as xml_escape

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from ..annotations import (
    ADDITIVE_IDEMPOTENT,
    DESTRUCTIVE,
    DESTRUCTIVE_NON_IDEMPOTENT,
    READONLY,
)
from ..client import NextcloudClient
from ..permissions import PermissionLevel, require_permission
from ..state import get_client, get_config

_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/svg+xml"}
_MAX_IMAGE_SIZE = 10 * 1024 * 1024


def _build_search_xml(user: str, query: str, path: str, limit: int, offset: int, mimetype: str) -> str:
    """Build a WebDAV SEARCH request body."""
    where_parts: list[str] = []
    if query:
        q = xml_escape(query)
        where_parts.append(f"<d:like><d:prop><d:displayname/></d:prop><d:literal>%{q}%</d:literal></d:like>")
    if mimetype:
        m = xml_escape(mimetype if "%" in mimetype or "/" in mimetype else f"{mimetype}/%")
        where_parts.append(f"<d:like><d:prop><d:getcontenttype/></d:prop><d:literal>{m}</d:literal></d:like>")
    if not where_parts:
        where_clause = "<d:gt><d:prop><oc:fileid/></d:prop><d:literal>0</d:literal></d:gt>"
    elif len(where_parts) == 1:
        where_clause = where_parts[0]
    else:
        where_clause = "<d:and>" + "".join(where_parts) + "</d:and>"
    safe_user = xml_escape(user)
    safe_path = xml_escape(path.strip("/"))
    scope = f"/files/{safe_user}/{safe_path}" if safe_path else f"/files/{safe_user}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        "<d:basicsearch>"
        "<d:select><d:prop>"
        "<d:displayname/><d:getlastmodified/><d:getcontenttype/>"
        "<d:getcontentlength/><d:resourcetype/><oc:fileid/><oc:size/>"
        "</d:prop></d:select>"
        f"<d:from><d:scope><d:href>{scope}</d:href>"
        "<d:depth>infinity</d:depth></d:scope></d:from>"
        f"<d:where>{where_clause}</d:where>"
        "<d:orderby><d:order><d:prop><d:getlastmodified/></d:prop>"
        "<d:descending/></d:order></d:orderby>"
        f"<d:limit><d:nresults>{limit}</d:nresults>"
        f"<d:firstresult>{offset}</d:firstresult></d:limit>"
        "</d:basicsearch></d:searchrequest>"
    )


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_directory(path: str = "/") -> str:
        """List files and folders in a Nextcloud directory.

        Args:
            path: Directory path relative to user's root (default: "/" for root).
                  Example: "Documents", "Photos/Vacation"

        Returns:
            JSON list of entries, each with: path, is_directory, size, last_modified, content_type.
        """
        client = get_client()
        entries = await client.dav_propfind(path, depth=1)
        # First entry is the directory itself — skip it
        if entries and entries[0]["path"].rstrip("/") == path.strip("/"):
            entries = entries[1:]
        return json.dumps(entries, default=str)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_file(path: str) -> list[TextContent | ImageContent]:
        """Read a file's content from Nextcloud.

        Text files are returned as text. Image files (PNG, JPEG, GIF, WebP)
        are returned as viewable images. Other binary files return metadata.

        Args:
            path: File path relative to user's root. Example: "Documents/notes.md"

        Returns:
            File content as text, an image, or metadata for unsupported binary files.
        """
        client = get_client()
        content, content_type = await client.dav_get(path)
        ct = content_type.lower()
        if ct in _IMAGE_MIME_TYPES and len(content) <= _MAX_IMAGE_SIZE:
            data = base64.b64encode(content).decode("ascii")
            return [ImageContent(type="image", data=data, mimeType=ct)]
        try:
            return [TextContent(type="text", text=content.decode("utf-8"))]
        except UnicodeDecodeError:
            size_kb = len(content) / 1024
            msg = f"[Binary file: {size_kb:.1f} KB, type: {ct}. Cannot display binary content.]"
            return [TextContent(type="text", text=msg)]

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def search_files(
        query: str = "",
        path: str = "/",
        mimetype: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """Search for files in Nextcloud by name and/or MIME type.

        Searches recursively through all subdirectories of the given path.
        Results are sorted by last modified date (newest first).

        At least one of query or mimetype must be provided.

        Args:
            query: Filename search pattern. Matches anywhere in the filename.
                   Example: "report" matches "quarterly-report.pdf", "report-2026.docx".
            path: Directory to search in (default: "/" for entire user folder).
                  Example: "Documents" to only search in Documents.
            mimetype: Filter by MIME type prefix. Example: "image" for all images,
                      "application/pdf" for PDFs, "text" for all text files.
            limit: Maximum number of results (1-100, default: 20).
            offset: Number of results to skip for pagination (default: 0).

        Returns:
            JSON object with "data" (list of matching files) and "pagination"
            (count, offset, has_more).
        """
        if not query and not mimetype:
            raise ValueError("At least one of 'query' or 'mimetype' must be provided.")
        limit = max(1, min(100, limit))
        offset = max(0, offset)
        config = get_config()
        client = get_client()
        body = _build_search_xml(config.user, query, path, limit, offset, mimetype)
        response = await client.dav_request(
            "SEARCH",
            "",
            body=body,
            headers={"Content-Type": "text/xml; charset=utf-8"},
            context=f"Search files: query={query!r} mimetype={mimetype!r}",
        )
        results = NextcloudClient._parse_propfind(response.text or "", config.user)
        response_data = {
            "data": results,
            "pagination": {
                "count": len(results),
                "offset": offset,
                "has_more": len(results) == limit,
            },
        }
        return json.dumps(response_data, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def upload_file(path: str, content: str) -> str:
        """Upload or overwrite a text file in Nextcloud.

        Creates the file if it doesn't exist. Overwrites if it does.

        Args:
            path: Destination path relative to user's root. Example: "Documents/report.md"
            content: Text content to write to the file.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.dav_put(path, content.encode("utf-8"), content_type="text/plain; charset=utf-8")
        return f"File uploaded successfully: {path}"

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def copy_file(source: str, destination: str) -> str:
        """Copy a file or directory in Nextcloud.

        Creates a copy at the destination path. The source remains unchanged.
        Fails if the destination already exists.

        Args:
            source: Source path. Example: "Documents/report.md"
            destination: Destination path. Example: "Documents/report-backup.md"

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.dav_copy(source, destination)
        return f"Copied: {source} → {destination}"

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def create_directory(path: str) -> str:
        """Create a new directory in Nextcloud.

        Args:
            path: Directory path to create. Example: "Documents/Projects/NewProject"

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.dav_mkcol(path)
        return f"Directory created: {path}"


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_file(path: str) -> str:
        """Delete a file or directory from Nextcloud.

        WARNING: This permanently deletes the file/directory (moves to trash if enabled).

        Args:
            path: Path to delete. Example: "Documents/old-file.txt"

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.dav_delete(path)
        return f"Deleted: {path}"

    @mcp.tool(annotations=DESTRUCTIVE_NON_IDEMPOTENT)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def move_file(source: str, destination: str) -> str:
        """Move or rename a file/directory in Nextcloud.

        Args:
            source: Current path. Example: "Documents/old-name.txt"
            destination: New path. Example: "Documents/new-name.txt"

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.dav_move(source, destination)
        return f"Moved: {source} → {destination}"


def register(mcp: FastMCP) -> None:
    """Register file tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
