"""Collectives tools — manage collectives and pages via OCS API."""

import json
import re
from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
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
        "parent_id": p.get("parentId"),
        "emoji": p.get("emoji") or None,
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


async def _moved_page(page_id: int, collective_id: int, parent_id: int, copied_title: str | None) -> dict[str, Any]:
    """Find a page just moved or copied into another collective, whose endpoint answers with nothing.

    A move keeps the page ID; a copy is the newest page under the target parent with the original's title.
    """
    pages: list[dict[str, Any]] = (await get_client().ocs_get(f"{API}/collectives/{collective_id}/pages"))["pages"]
    if copied_title is None:
        return next((p for p in pages if p.get("id") == page_id), {"id": page_id})
    parent = parent_id or next((p["id"] for p in pages if not p.get("parentId")), None)
    candidates = [p for p in pages if p.get("parentId") == parent and p.get("title") == copied_title]
    return max(candidates, key=lambda p: int(p["id"])) if candidates else {"id": None, "parentId": parent}


def _collective_id(page: dict[str, Any]) -> int | None:
    """Recent pages carry no collective ID, but their collectivePath ends in it: "/<name>-<id>"."""
    match = re.search(r"-(\d+)$", str(page.get("collectivePath") or ""))
    return int(match.group(1)) if match else None


async def _write_page(collective_id: int, page: dict[str, Any], content: str) -> dict[str, Any]:
    """Replace a page's Markdown file, then tell Collectives, which records who changed it and when."""
    client = get_client()
    await client.dav_put(_page_dav_path(page), content.encode("utf-8"), content_type="text/markdown; charset=utf-8")
    data = await client.ocs_get(f"{API}/collectives/{collective_id}/pages/{page['id']}/touch")
    return cast(dict[str, Any], data["page"])


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


def _register_search_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def search_collective_pages(collective_id: int, query: str) -> str:
        """Search the text of a collective's pages.

        Uses Collectives' search index, which a background job keeps up to date,
        so pages written in the last few minutes may not be found yet. For titles,
        get_collective_pages lists every page.

        Args:
            collective_id: The numeric collective ID.
            query: Words to look for.

        Returns:
            JSON list of matching pages (id, title, parent_id, emoji, timestamp, ...).
        """
        client = get_client()
        data = await client.ocs_get(f"{API}/collectives/{collective_id}/search", params={"searchString": query})
        return json.dumps([_format_page(p) for p in data.get("pages", [])], default=str)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_recent_collective_pages(query: str = "", limit: int = 10) -> str:
        """List the most recently changed pages across all your collectives, newest first.

        Changes by anyone count; visits do not.

        Args:
            query: Only pages whose title contains this (default: all).
            limit: Maximum pages (1-100, default 10).

        Returns:
            JSON list of pages with id, title, collective (its name) and
            collective_id (null for a collective whose name has no letters or
            digits), plus the usual page fields.
        """
        params: dict[str, Any] = {"limit": max(1, min(100, limit))}
        if query:
            params["query"] = query
        data = await get_client().ocs_get(f"{API}/collectives/search/recent", params=params)
        return json.dumps(
            [
                {**_format_page(p), "collective": p.get("collectiveNameWithEmoji"), "collective_id": _collective_id(p)}
                for p in data.get("pages", [])
            ],
            default=str,
        )


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
    async def create_collective_page(collective_id: int, parent_id: int, title: str, content: str = "") -> str:
        """Create a new page in a collective.

        Pages are Markdown documents organized in a tree structure.
        Every page must have a parent — use the landing page ID as parent
        for top-level pages.

        Args:
            collective_id: The numeric collective ID.
            parent_id: Parent page ID. Use the landing page ID from
                       get_collective_pages for top-level pages.
            title: Title of the new page (required).
            content: Optional Markdown text for the page.

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
        if content:
            page = await _write_page(collective_id, page, content)
        return json.dumps(_format_page(page), default=str)


def _register_page_edit_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def update_collective_page(
        collective_id: int,
        page_id: int,
        content: str | None = None,
        title: str | None = None,
        emoji: str | None = None,
    ) -> str:
        """Change a page's text, title or emoji. Only what you pass changes.

        The text replaces the whole page; read it first with get_collective_page to
        edit part of it. Earlier versions stay in the file's version history. Someone
        editing the page in the browser at the same time may be asked which version
        to keep.

        Args:
            collective_id: The numeric collective ID.
            page_id: The numeric page ID.
            content: New Markdown text for the whole page.
            title: New title (renames the page). The landing page cannot be renamed.
            emoji: New emoji shown before the title; an empty string removes it.

        Returns:
            JSON object with the page afterwards.
        """
        if content is None and title is None and emoji is None:
            raise ValueError("Pass at least one of content, title or emoji.")
        if title is not None and not title.strip():
            raise ValueError("Page title cannot be empty.")
        client = get_client()
        page_path = f"{API}/collectives/{collective_id}/pages/{page_id}"
        page: dict[str, Any] = (await client.ocs_get(page_path))["page"]
        if title is not None and not page.get("parentId"):
            # Checked before anything is written; Collectives would refuse the rename after the content change
            raise ValueError("The landing page cannot be renamed; rename the collective instead.")
        if content is not None:
            page = await _write_page(collective_id, page, content)
        if title is not None:
            page = (await client.ocs_put_json(page_path, json_data={"title": title}))["page"]
        if emoji is not None:
            # Collectives ignores a null emoji; an empty string is what clears it
            page = (await client.ocs_put_json(f"{page_path}/emoji", json_data={"emoji": emoji}))["page"]
        return json.dumps(_format_page(page), default=str)

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def move_collective_page(
        collective_id: int,
        page_id: int,
        parent_id: int,
        to_collective_id: int = 0,
        copy: bool = False,
    ) -> str:
        """Move or copy a page, with its subpages, under another page, in this or another collective.

        The page lands first among its new siblings. Needs edit rights in both
        collectives; a page cannot go under itself or its own subpages.

        Args:
            collective_id: The collective the page is in now.
            page_id: The page to move or copy.
            parent_id: The page to put it under, in the target collective; 0 or the
                landing page ID for the top level.
            to_collective_id: Another collective to move or copy it to (default 0 =
                stay in this one).
            copy: True to copy and leave the original where it is. Every call makes
                another copy.

        Returns:
            JSON with the page afterwards (the copy, when copying).
        """
        client = get_client()
        body: dict[str, Any] = {"parentId": parent_id, "copy": copy}
        page_path = f"{API}/collectives/{collective_id}/pages/{page_id}"
        if to_collective_id and to_collective_id != collective_id:
            title = str((await client.ocs_get(page_path))["page"].get("title", "")) if copy else None
            await client.ocs_put_json(f"{page_path}/to/{to_collective_id}", json_data=body)
            moved = await _moved_page(page_id, to_collective_id, parent_id, title)
            return json.dumps(_format_page(moved), default=str)
        data = await client.ocs_put_json(page_path, json_data=body)
        return json.dumps(_format_page(data["page"]), default=str)


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
    async def delete_collective(collective_id: int, delete_team: bool = False) -> str:
        """Permanently delete a collective from the trash.

        The collective must be in the trash first (use trash_collective).
        This action is irreversible: all pages are permanently removed.

        Args:
            collective_id: The numeric collective ID.
            delete_team: Also delete the team (circle) behind the collective, with
                every membership and anything shared with the team. Needs you to
                own the team; otherwise nothing is deleted. The team goes even if it
                existed before the collective. With false (default) it stays as an
                ordinary team.

        Returns:
            Confirmation message.
        """
        client = get_client()
        suffix = "?circle=1" if delete_team else ""
        await client.ocs_delete(f"{API}/collectives/trash/{collective_id}{suffix}")
        return f"Collective {collective_id} deleted permanently" + (" with its team." if delete_team else ".")

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
    _register_search_tools(mcp)
    _register_write_tools(mcp)
    _register_page_edit_tools(mcp)
    _register_destructive_tools(mcp)
