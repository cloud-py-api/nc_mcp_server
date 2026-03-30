"""App management tools — list, enable, and disable Nextcloud apps via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

APPS_API = "cloud/apps"


def _format_app(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "summary": a.get("summary"),
        "version": a.get("version"),
        "author": a.get("author"),
    }


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_apps(app_filter: str = "enabled") -> str:
        """List installed Nextcloud apps. Requires admin privileges.

        Returns the list of app IDs matching the filter criteria.

        Args:
            app_filter: Filter apps by status. Options:
                        "enabled" (default) — only enabled apps
                        "disabled" — only disabled apps
                        "all" — all installed apps

        Returns:
            JSON list of app ID strings.
        """
        valid = {"enabled", "disabled", "all"}
        if app_filter not in valid:
            raise ValueError(f"Invalid filter '{app_filter}'. Must be one of: {', '.join(sorted(valid))}")
        client = get_client()
        params = {} if app_filter == "all" else {"filter": app_filter}
        data = await client.ocs_get(APPS_API, params=params)
        return json.dumps(data["apps"])

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_app_info(app_id: str) -> str:
        """Get detailed information about an installed Nextcloud app. Requires admin privileges.

        Returns app metadata including name, version, description, and author.

        Args:
            app_id: The app identifier (e.g. "spreed", "mail", "collectives").
                    Use list_apps to find available app IDs.

        Returns:
            JSON object with app details: id, name, summary, version, author.
        """
        client = get_client()
        data = await client.ocs_get(f"{APPS_API}/{app_id}")
        return json.dumps(_format_app(data), default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def enable_app(app_id: str) -> str:
        """Enable a Nextcloud app. Requires admin privileges.

        Activates a previously disabled or newly installed app. This makes
        the app's features available to users.

        Args:
            app_id: The app identifier to enable (e.g. "spreed", "mail").

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_post(f"{APPS_API}/{app_id}")
        return f"App '{app_id}' enabled."


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def disable_app(app_id: str) -> str:
        """Disable a Nextcloud app. Requires admin privileges.

        Deactivates the app, making its features unavailable. The app data
        is preserved and can be re-enabled later.

        Be careful: disabling core apps (like files, dav) can break
        Nextcloud functionality.

        Args:
            app_id: The app identifier to disable (e.g. "weather_status").

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{APPS_API}/{app_id}")
        return f"App '{app_id}' disabled."


def register(mcp: FastMCP) -> None:
    """Register app management tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
