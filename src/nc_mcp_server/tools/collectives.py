"""Collectives tools — manage collectives and pages via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, DESTRUCTIVE, READONLY
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
    result: dict[str, Any] = {
        "id": p["id"],
        "title": p.get("title", ""),
        "emoji": p.get("emoji"),
        "timestamp": p.get("timestamp"),
        "size": p.get("size"),
        "file_name": p.get("fileName"),
        "file_path": p.get("filePath"),
        "last_user_id": p.get("lastUserId"),
    }
    if p.get("content") is not None:
        result["content"] = p["content"]
    if p.get("tags"):
        result["tags"] = p["tags"]
    return result


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_collectives() -> str:
        """List all collectives the current user has access to.

        Collectives are shared knowledge bases with wiki-style pages.
        Each collective has a landing page and may contain nested subpages.

        Returns:
            JSON list of collectives with id, name, emoji, permissions.
        """
        client = get_client()
        data = await client.ocs_get(f"{API}/collectives")
        collectives = [_format_collective(c) for c in data.get("collectives", data if isinstance(data, list) else [])]
        return json.dumps(collectives, indent=2, default=str)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_collective_pages(collective_id: int) -> str:
        """List all pages in a collective.

        Returns the full page tree including the landing page and all subpages.
        Each page has a title, emoji, timestamp, size, and file path.

        Args:
            collective_id: The numeric collective ID. Use list_collectives to find IDs.

        Returns:
            JSON list of pages with id, title, emoji, timestamp, size, file_name, file_path.
        """
        client = get_client()
        data = await client.ocs_get(f"{API}/collectives/{collective_id}/pages")
        pages = [_format_page(p) for p in data.get("pages", data if isinstance(data, list) else [])]
        return json.dumps(pages, indent=2, default=str)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_collective_page(collective_id: int, page_id: int) -> str:
        """Get a single page from a collective, including its content.

        Returns full page details with the Markdown content of the page.

        Args:
            collective_id: The numeric collective ID.
            page_id: The numeric page ID. Use get_collective_pages to find IDs.

        Returns:
            JSON object with page details including content (Markdown).
        """
        client = get_client()
        data = await client.ocs_get(f"{API}/collectives/{collective_id}/pages/{page_id}")
        page = data.get("page", data)
        return json.dumps(_format_page(page), indent=2, default=str)


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
        collective = data.get("collective", data)
        return json.dumps(_format_collective(collective), indent=2, default=str)

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
        page = data.get("page", data)
        return json.dumps(_format_page(page), indent=2, default=str)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_collective(collective_id: int) -> str:
        """Delete a collective permanently (trash + permanent delete).

        This moves the collective to trash, then permanently deletes it
        along with all its pages. This action is irreversible.

        Args:
            collective_id: The numeric collective ID.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{API}/collectives/{collective_id}")
        await client.ocs_delete(f"{API}/collectives/trash/{collective_id}")
        return f"Collective {collective_id} deleted permanently."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_collective_page(collective_id: int, page_id: int) -> str:
        """Delete a page from a collective permanently (trash + permanent delete).

        This removes the page and its content. This action is irreversible.
        The landing page of a collective cannot be deleted.

        Args:
            collective_id: The numeric collective ID.
            page_id: The numeric page ID to delete.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{API}/collectives/{collective_id}/pages/{page_id}")
        await client.ocs_delete(f"{API}/collectives/{collective_id}/pages/trash/{page_id}")
        return f"Page {page_id} deleted permanently from collective {collective_id}."


def register(mcp: FastMCP) -> None:
    """Register Collectives tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
