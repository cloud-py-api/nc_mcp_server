"""File management tools — list, read, upload, copy, delete, move, search files via WebDAV."""

import asyncio
import base64
import binascii
import errno
import io
import json
import mimetypes
import os
from collections.abc import AsyncIterator
from pathlib import Path
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
_UPLOAD_CHUNK_SIZE = 256 * 1024


def _resolve_content_type(path: str, content_type: str) -> str:
    if content_type.strip():
        return content_type.strip()
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _resolve_local_upload_path(local_path: str, upload_root: str) -> Path:
    """Resolve a local path and verify it is inside the configured upload root.

    Symlinks are resolved before the containment check, so a symlink inside the
    root that points outside is rejected.

    Raises:
        ValueError: when upload_root is not configured, the path is empty, does
            not exist, is not a regular file, or resolves to a location outside
            the upload root.
    """
    if not upload_root:
        raise ValueError(
            "upload_file_from_path is not configured on this server. "
            "The administrator must set NEXTCLOUD_MCP_UPLOAD_ROOT to a local directory."
        )
    if not local_path or not local_path.strip():
        raise ValueError("local_path cannot be empty.")
    try:
        resolved = Path(local_path).expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise ValueError(f"Local file not found: {local_path}") from None
    except (OSError, RuntimeError):
        raise ValueError(f"Cannot resolve local path: {local_path}") from None
    root = Path(upload_root).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"Path '{local_path}' is outside the configured upload root.") from None
    if not resolved.is_file():
        raise ValueError(f"Path is not a regular file: {local_path}")
    return resolved


def _open_no_follow(path: Path) -> io.FileIO:
    """Open a regular file for reading with O_NOFOLLOW as TOCTOU defense-in-depth.

    _resolve_local_upload_path already rejects symlinks in the caller's input by
    resolving the path before the containment check. But if another local actor
    has write access to the upload root, they could replace the validated file
    with a symlink between validation and this open. O_NOFOLLOW on the final
    component closes that race window (intermediate components are not covered;
    see the tool docstring for the expected trust model).
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise ValueError(f"Refusing to follow symlink at {path.name}: file was swapped after validation.") from exc
        raise
    return io.FileIO(fd, closefd=True)


async def _stream_local_file(path: Path, chunk_size: int = _UPLOAD_CHUNK_SIZE) -> AsyncIterator[bytes]:
    """Yield chunks from a local file without blocking the event loop."""
    f = await asyncio.to_thread(_open_no_follow, path)
    try:
        while True:
            chunk = await asyncio.to_thread(f.read, chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(f.close)


def _like_literal(text: str) -> str:
    """Escape the LIKE wildcards % and _, and the backslash escape itself, so text matches as typed.

    Nextcloud passes the search literal to the database's LIKE unchanged, and every database it
    supports takes a backslash as the escape character.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_search_xml(user: str, query: str, path: str, limit: int, offset: int, mimetype: str) -> str:
    """Build a WebDAV SEARCH request body."""
    where_parts: list[str] = []
    if query:
        q = xml_escape(_like_literal(query))
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
    async def list_directory(path: str = "/", limit: int = 50, offset: int = 0) -> str:
        """List files and folders in a Nextcloud directory.

        Args:
            path: Directory path relative to user's root (default: "/" for root).
                  Example: "Documents", "Photos/Vacation"
            limit: Maximum number of entries to return (1-500, default 50).
            offset: Number of entries to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of entries with path, is_directory, size, etc.)
            and "pagination" (count, offset, limit, has_more).
        """
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        client = get_client()
        entries = await client.dav_propfind(path, depth=1)
        if entries and entries[0]["path"].rstrip("/") == path.strip("/"):
            entries = entries[1:]
        page = entries[offset : offset + limit]
        has_more = offset + limit < len(entries)

        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

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
            query: Text to find anywhere in the file name, case-insensitive, taken literally
                   (% and _ are not wildcards).
                   Example: "report" matches "quarterly-report.pdf", "report-2026.docx".
            path: Directory to search in (default: "/" for entire user folder).
                  Example: "Documents" to only search in Documents.
            mimetype: Filter by MIME type prefix. Example: "image" for all images,
                      "application/pdf" for PDFs, "text" for all text files.
            limit: Maximum number of results (1-100, default: 20).
            offset: Number of results to skip for pagination (default: 0).

        Returns:
            JSON object with "data" (list of matching files) and "pagination"
            (count, offset, limit, has_more).
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
                "limit": limit,
                "has_more": len(results) == limit,
            },
        }
        return json.dumps(response_data, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def upload_file(path: str, content: str) -> str:
        """Upload or overwrite a text file in Nextcloud.

        Use this for plain-text content (markdown, source code, CSV, JSON, etc.).
        For binary files (images, PDFs, archives), use upload_file_binary instead.

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
    async def upload_file_binary(path: str, content_base64: str, content_type: str = "") -> str:
        """Upload or overwrite a binary file in Nextcloud.

        Use this for images, PDFs, archives, or any non-text content. The content
        must be base64-encoded. For plain-text files, use upload_file instead.

        Creates the file if it doesn't exist. Overwrites if it does.

        Args:
            path: Destination path relative to user's root. Example: "Photos/photo.png"
            content_base64: File bytes encoded as a base64 string. May be empty to
                create an empty file.
            content_type: MIME type for the upload request (e.g. "image/png",
                "application/pdf"). If omitted, inferred from the path extension;
                falls back to "application/octet-stream". Note: Nextcloud re-derives
                the stored MIME type from the filename, so this mainly controls the
                HTTP upload header.

        Returns:
            Confirmation message with the uploaded byte count.
        """
        cleaned = "".join(content_base64.split()) if content_base64 else ""
        try:
            data = base64.b64decode(cleaned, validate=True) if cleaned else b""
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"content_base64 is not valid base64: {exc}") from exc
        resolved = _resolve_content_type(path, content_type)
        client = get_client()
        await client.dav_put(path, data, content_type=resolved)
        return f"File uploaded successfully: {path} ({len(data)} bytes, {resolved})"

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


def _register_upload_from_path_tool(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def upload_file_from_path(local_path: str, remote_path: str, content_type: str = "") -> str:
        """Upload a local file from the server's filesystem to Nextcloud.

        Suitable for large files — the content is streamed in chunks rather than
        loaded fully into memory. Contrast with upload_file (text-only) and
        upload_file_binary (whole content passed inline as base64).

        The administrator must enable this tool by setting NEXTCLOUD_MCP_UPLOAD_ROOT
        to a local directory. Only files inside that directory can be uploaded
        (symlinks are resolved before the containment check). If the env var is
        not set, this tool is not registered at all.

        Trust model: NEXTCLOUD_MCP_UPLOAD_ROOT should be a directory whose ancestors
        and contents are not writable by less-privileged local users. The final
        component is opened with O_NOFOLLOW to defeat symlink-swap TOCTOU races,
        but intermediate directory components are not re-validated — pointing the
        upload root at a world-writable tree (e.g. inside /tmp) would let other
        local accounts redirect uploads via directory-level symlink races.

        Args:
            local_path: Path to the local file on the MCP server's filesystem.
                Must resolve to a regular file inside NEXTCLOUD_MCP_UPLOAD_ROOT.
            remote_path: Destination path in Nextcloud relative to the user's root.
                Example: "Photos/vacation.jpg"
            content_type: Optional MIME type for the upload request. If omitted,
                inferred from the remote_path extension; falls back to
                "application/octet-stream". Note: Nextcloud re-derives the stored
                MIME type from the filename, so this mainly controls the upload header.

        Returns:
            Confirmation message with the uploaded byte count.
        """
        config = get_config()
        resolved = _resolve_local_upload_path(local_path, config.upload_root)
        size = resolved.stat().st_size
        resolved_ct = _resolve_content_type(remote_path, content_type)
        client = get_client()
        await client.dav_put_stream(
            remote_path,
            lambda: _stream_local_file(resolved),
            content_type=resolved_ct,
        )
        return f"File uploaded successfully: {remote_path} ({size} bytes, {resolved_ct})"


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
    if get_config().upload_root:
        _register_upload_from_path_tool(mcp)
