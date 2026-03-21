"""File management tools — list, read, upload, delete, move files via WebDAV."""

import json

from mcp.server.fastmcp import FastMCP

from ..permissions import PermissionLevel, require_permission
from ..state import get_client


def register(mcp: FastMCP) -> None:
    """Register file tools with the MCP server."""

    @mcp.tool()
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
        return json.dumps(entries, indent=2, default=str)

    @mcp.tool()
    @require_permission(PermissionLevel.READ)
    async def get_file(path: str) -> str:
        """Read a file's content from Nextcloud.

        Best for text files (txt, md, json, csv, xml, etc.).
        For binary files, returns a message with the file size instead.

        Args:
            path: File path relative to user's root. Example: "Documents/notes.md"

        Returns:
            The file content as text, or a description for binary files.
        """
        client = get_client()
        content = await client.dav_get(path)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return f"[Binary file, {len(content)} bytes. Use download tools for binary content.]"

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
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

    @mcp.tool()
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
