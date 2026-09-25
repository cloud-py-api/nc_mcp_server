"""Collectives tools — manage collectives and pages via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, DESTRUCTIVE, READONLY
from ..client import NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

API = "apps/collectives/api/v1.0"


def _format_collective(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c["id"],
        "name": c["name"],
        "emoji": c.get("emoji"),
        "level": c.get("level"),
        "can_edit": c.get("canEdit"),
        "can_share": c.get("canShare"),
        "page_mode": c.get("pageMode"),
        "user_page_order": c.get("userPageOrder"),
    }


def _format_page(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"],
        "title": p.get("title", ""),
        "emoji": p.get("emoji"),
        "timestamp": p.get("timestamp"),
        "size": p.get("size"),
        "file_name": p.get("fileName"),
        "file_path": p.get("filePath"),
        "last_user_id": p.get("lastUserId"),
        "tags": p.get("tags", []),
    }


def _page_dav_path(p: dict[str, Any]) -> str:
    """Build the WebDAV path of the Markdown file behind a page.

    Collectives stores every page as a file in the user's collectives mount, and the
    page object spells that location out in three parts: collectivePath is the
    collective's folder, filePath the page's folder inside it (empty for a page at the
    top level) and fileName the Markdown file itself. A page that has subpages becomes
    a folder of its own holding a Readme.md, which is why the path has to be read back
    from the page object instead of being derived from the title.
    """
    parts = [str(p.get(key) or "").strip("/") for key in ("collectivePath", "filePath", "fileName")]
    return "/".join(part for part in parts if part)


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_collectives(limit: int = 50, offset: int = 0) -> str:
        """List collectives the current user has access to.

        Collectives are shared knowledge bases with wiki-style pages.

        Args:
            limit: Maximum number of collectives to return (1-200, default 50).
            offset: Number of collectives to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of collectives with id, name, emoji, permissions)
            and "pagination" (count, offset, limit, has_more).
        """
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        client = get_client()
        data = await client.ocs_get(f"{API}/collectives")
        all_collectives = [_format_collective(c) for c in data["collectives"]]
        page = all_collectives[offset : offset + limit]
        has_more = offset + limit < len(all_collectives)

        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_collective_pages(collective_id: int, limit: int = 50, offset: int = 0) -> str:
        """List pages in a collective.

        Returns the page tree including the landing page and all subpages.

        Args:
            collective_id: The numeric collective ID. Use list_collectives to find IDs.
            limit: Maximum number of pages to return (1-200, default 50).
            offset: Number of pages to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of pages with id, title, emoji, timestamp, size)
            and "pagination" (count, offset, limit, has_more). Page text is not
            included here; read one page with get_collective_page to get it.
        """
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        client = get_client()
        data = await client.ocs_get(f"{API}/collectives/{collective_id}/pages")
        all_pages = [_format_page(p) for p in data["pages"]]
        page = all_pages[offset : offset + limit]
        has_more = offset + limit < len(all_pages)

        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_collective_page(collective_id: int, page_id: int) -> str:
        """Get a single page from a collective, including its content.

        Collectives serves page metadata and page text from two different places, so
        this reads the page's Markdown file after the metadata call.

        Args:
            collective_id: The numeric collective ID.
            page_id: The numeric page ID. Use get_collective_pages to find IDs.

        Returns:
            JSON object with page details and "content", the page's Markdown text.
            An empty page has an empty string. When the file cannot be read,
            "content" is null and "content_error" says why.
        """
        client = get_client()
        data = await client.ocs_get(f"{API}/collectives/{collective_id}/pages/{page_id}")
        page = data["page"]
        result = _format_page(page)
        try:
            raw, _ = await client.dav_get(_page_dav_path(page))
            result["content"] = raw.decode("utf-8", errors="replace")
        except NextcloudError as e:
            result["content"] = None
            result["content_error"] = str(e)
        return json.dumps(result, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_collective(name: str, emoji: str | None = None) -> str:
        """Create a new collective (shared knowledge base).

        A collective is a wiki-like space where team members can create and
        edit pages together. It automatically creates a landing page.

        Args:
            name: Name of the collective (required, must be unique).
            emoji: Optional emoji icon for the collective (e.g. "📚").

        Returns:
            JSON object with the created collective details.
        """
        if not name.strip():
            raise ValueError("Collective name cannot be empty.")
        client = get_client()
        post_data: dict[str, Any] = {"name": name}
        if emoji:
            post_data["emoji"] = emoji
        data = await client.ocs_post_json(f"{API}/collectives", json_data=post_data)
        collective = data["collective"]
        return json.dumps(_format_collective(collective), default=str)

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_collective_page(collective_id: int, parent_id: int, title: str) -> str:
        """Create a new page in a collective.

        Pages are Markdown documents organized in a tree structure.
        Every page must have a parent — use the landing page ID as parent
        for top-level pages.

        Args:
            collective_id: The numeric collective ID.
            parent_id: Parent page ID. Use the landing page ID from
                       get_collective_pages for top-level pages.
            title: Title of the new page (required).

        Returns:
            JSON object with the created page details.
        """
        if not title.strip():
            raise ValueError("Page title cannot be empty.")
        client = get_client()
        data = await client.ocs_post_json(
            f"{API}/collectives/{collective_id}/pages/{parent_id}",
            json_data={"title": title},
        )
        page = data["page"]
        return json.dumps(_format_page(page), default=str)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def trash_collective(collective_id: int) -> str:
        """Move a collective to the trash.

        The collective and its pages are soft-deleted. Use
        restore_collective to undo, or delete_collective to
        permanently remove it.

        Args:
            collective_id: The numeric collective ID.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{API}/collectives/{collective_id}")
        return f"Collective {collective_id} moved to trash."

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def restore_collective(collective_id: int) -> str:
        """Restore a collective from the trash.

        Args:
            collective_id: The numeric collective ID (from list_collectives or prior trash operation).

        Returns:
            JSON object with the restored collective details.
        """
        client = get_client()
        data = await client.ocs_patch(f"{API}/collectives/trash/{collective_id}")
        collective = data["collective"]
        return json.dumps(_format_collective(collective), default=str)

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_collective(collective_id: int) -> str:
        """Permanently delete a collective from the trash.

        The collective must be in the trash first (use trash_collective).
        This action is irreversible — all pages are permanently removed.

        Args:
            collective_id: The numeric collective ID.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{API}/collectives/trash/{collective_id}")
        return f"Collective {collective_id} deleted permanently."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def trash_collective_page(collective_id: int, page_id: int) -> str:
        """Move a page to the collective's trash.

        The page is soft-deleted. Use restore_collective_page to undo,
        or delete_collective_page to permanently remove it.
        The landing page cannot be trashed.

        Args:
            collective_id: The numeric collective ID.
            page_id: The numeric page ID.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{API}/collectives/{collective_id}/pages/{page_id}")
        return f"Page {page_id} moved to trash."

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def restore_collective_page(collective_id: int, page_id: int) -> str:
        """Restore a page from the collective's trash.

        Args:
            collective_id: The numeric collective ID.
            page_id: The numeric page ID.

        Returns:
            JSON object with the restored page details.
        """
        client = get_client()
        data = await client.ocs_patch(f"{API}/collectives/{collective_id}/pages/trash/{page_id}")
        page = data["page"]
        return json.dumps(_format_page(page), default=str)

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_collective_page(collective_id: int, page_id: int) -> str:
        """Permanently delete a page from the collective's trash.

        The page must be in the trash first (use trash_collective_page).
        This action is irreversible.

        Args:
            collective_id: The numeric collective ID.
            page_id: The numeric page ID.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{API}/collectives/{collective_id}/pages/trash/{page_id}")
        return f"Page {page_id} deleted permanently."


def register(mcp: FastMCP) -> None:
    """Register Collectives tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
