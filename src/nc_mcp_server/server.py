"""MCP server — registers all tools and manages the Nextcloud client lifecycle."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import NextcloudClient
from .config import Config
from .multiuser import ClientPool, MultiUserAuthMiddleware
from .permissions import set_permission_level
from .state import get_client, get_config, set_state
from .tools import (
    activity,
    announcements,
    app_management,
    calendar,
    circles,
    collectives,
    comments,
    contacts,
    cospend,
    files,
    flows,
    forms,
    groups,
    mail,
    notifications,
    reminders,
    search,
    shares,
    system_tags,
    talk,
    tasks,
    trashbin,
    user_status,
    users,
    versions,
)

__all__ = ["create_http_app", "create_server", "get_client", "get_config"]


def create_server(config: Config | None = None) -> FastMCP:
    """Create and configure the MCP server with all tools registered.

    Args:
        config: Optional config override. If None, loads from environment.

    Returns:
        Configured FastMCP instance ready to run.
    """
    if config is None:
        config = Config.from_env()
    config.validate()

    # Multi-user mode: no client of its own; each request brings its login (multiuser.py).
    set_state(None if config.multiuser else NextcloudClient(config), config)
    set_permission_level(config.permission_level)

    mcp = FastMCP(
        "nc-mcp-server",
        stateless_http=True,
        host=config.host,
        port=config.port,
    )

    activity.register(mcp)
    announcements.register(mcp)
    app_management.register(mcp)
    calendar.register(mcp)
    circles.register(mcp)
    collectives.register(mcp)
    comments.register(mcp)
    contacts.register(mcp)
    cospend.register(mcp)
    files.register(mcp)
    flows.register(mcp)
    forms.register(mcp)
    groups.register(mcp)
    mail.register(mcp)
    notifications.register(mcp)
    reminders.register(mcp)
    search.register(mcp)
    shares.register(mcp)
    system_tags.register(mcp)
    talk.register(mcp)
    tasks.register(mcp)
    trashbin.register(mcp)
    user_status.register(mcp)
    versions.register(mcp)
    users.register(mcp)

    return mcp


def create_http_app(mcp: FastMCP, config: Config) -> Any:
    """The Streamable HTTP ASGI app; in multi-user mode wrapped in the login middleware."""
    app = mcp.streamable_http_app()
    if not config.multiuser:
        return app
    pool = ClientPool(config)
    return MultiUserAuthMiddleware(app, pool, config.permission_level)
