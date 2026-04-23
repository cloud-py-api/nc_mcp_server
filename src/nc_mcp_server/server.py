"""MCP server — registers all tools and manages the Nextcloud client lifecycle."""

from mcp.server.fastmcp import FastMCP

from .client import NextcloudClient
from .config import Config
from .permissions import set_permission_level
from .state import get_client, get_config, set_state
from .tools import (
    activity,
    announcements,
    app_management,
    calendar,
    collectives,
    comments,
    contacts,
    files,
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

__all__ = ["create_server", "get_client", "get_config"]


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

    set_state(NextcloudClient(config), config)
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
    collectives.register(mcp)
    comments.register(mcp)
    contacts.register(mcp)
    files.register(mcp)
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
