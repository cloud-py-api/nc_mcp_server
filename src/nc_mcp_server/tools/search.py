"""Unified Search tools — search across all Nextcloud apps via the Unified Search OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

SEARCH_API = "search/providers"


def _format_provider(p: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": p.get("id", ""),
        "name": p.get("name", ""),
        "app": p.get("appId", ""),
    }
    filters = p.get("filters", {})
    if filters:
        result["filters"] = filters
    return result


def _format_entry(e: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "title": e.get("title", ""),
        "subline": e.get("subline", ""),
    }
    attrs = e.get("attributes", {})
    if isinstance(attrs, dict) and attrs:
        result["attributes"] = attrs
    return result


def register(mcp: FastMCP) -> None:
    """Register unified search tools with the MCP server."""

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_search_providers() -> str:
        """List available Unified Search providers.

        Each Nextcloud app can register search providers. Use the provider id
        with unified_search to search that provider. The filters field shows
        what extra filters each provider supports beyond the search term.

        Common filter types:
          - since/until: ISO 8601 datetime to bound results by date
          - person: user ID to filter by author/participant
          - title-only: boolean, search titles only

        Returns:
            JSON list of providers with: id, name, app, and optionally filters.
        """
        client = get_client()
        data = await client.ocs_get(SEARCH_API)
        return json.dumps([_format_provider(p) for p in data])

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def unified_search(
        provider: str,
        term: str,
        limit: int = 25,
        cursor: str = "",
        filters: str = "",
    ) -> str:
        """Search a Nextcloud app using the Unified Search API.

        Searches a specific provider (e.g. "files", "calendar", "mail",
        "talk-message", "contacts", "notes"). Use list_search_providers
        to see all available providers and their supported filters.

        Results are cursor-paginated. When has_more is true, pass the
        returned cursor value to the next call to get more results.

        Args:
            provider: Search provider ID (e.g. "files", "calendar", "mail").
                      Use list_search_providers to see available providers.
            term: Search query string.
            limit: Maximum results to return (1-25, default 25).
                   Server may cap this further based on configuration.
            cursor: Pagination cursor from a previous search response.
                    Omit for the first page.
            filters: Optional JSON object of provider-specific filters.
                     Example for files: {"since": "2026-01-01T00:00:00Z", "mime": "text"}
                     Example for talk: {"conversation": "abc123", "person": "admin"}
                     Use list_search_providers to see supported filters per provider.

        Returns:
            JSON object with: provider (name), entries (list of results with
            title, subline, attributes), has_more (boolean), and cursor
            (pass to next call for pagination).
        """
        limit = max(1, min(25, limit))
        params: dict[str, Any] = {"term": term, "limit": limit}
        if cursor:
            params["cursor"] = cursor

        if filters:
            extra = json.loads(filters)
            params.update(extra)

        client = get_client()
        data = await client.ocs_get(f"{SEARCH_API}/{provider}/search", params=params)

        entries = data.get("entries", [])

        return json.dumps(
            {
                "provider": data.get("name", provider),
                "entries": [_format_entry(e) for e in entries],
                "has_more": bool(data.get("isPaginated", False)),
                "cursor": data.get("cursor"),
            }
        )
