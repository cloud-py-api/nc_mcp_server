"""MCP server — registers all tools and manages the Nextcloud client lifecycle."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import Config
from .client import NextcloudClient
from .permissions import set_permission_level

# Global instances — initialized in create_server()
_client: NextcloudClient | None = None
_config: Config | None = None


def get_client() -> NextcloudClient:
    """Get the global Nextcloud client. Raises if server not initialized."""
    if _client is None:
        raise RuntimeError("Server not initialized. Call create_server() first.")
    return _client


def get_config() -> Config:
    """Get the global config. Raises if server not initialized."""
    if _config is None:
        raise RuntimeError("Server not initialized. Call create_server() first.")
    return _config


def create_server(config: Config | None = None) -> FastMCP:
    """Create and configure the MCP server with all tools registered.

    Args:
        config: Optional config override. If None, loads from environment.

    Returns:
        Configured FastMCP instance ready to run.
    """
    global _client, _config

    if config is None:
        config = Config.from_env()
    config.validate()

    _config = config
    _client = NextcloudClient(config)
    set_permission_level(config.permission_level)

    mcp = FastMCP(
        "nextcloud-mcp-server",
        stateless_http=True,
        host=config.host,
        port=config.port,
    )

    # Register all tool modules — each module calls @mcp.tool() on import
    _register_tools(mcp)

    return mcp


def _register_tools(mcp: FastMCP) -> None:
    """Import and register all tool modules."""
    # Import here to avoid circular imports — each module uses get_client()
    from .tools import files  # noqa: F401
    from .tools import users  # noqa: F401

    # Register tools from each module
    files.register(mcp)
    users.register(mcp)
