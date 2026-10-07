"""Global state — holds the Nextcloud client and config singletons.

In multi-user mode (``NEXTCLOUD_MCP_MULTIUSER=true``) there is no global client: ``get_client`` and
``get_config`` return the client and config of the login that sent the running request
(see :mod:`nc_mcp_server.multiuser`).
"""

from .client import NextcloudClient
from .config import Config
from .multiuser import current_binding

_client: NextcloudClient | None = None
_config: Config | None = None


def get_client() -> NextcloudClient:
    """Get the Nextcloud client of this call. Raises if server not initialized."""
    if _config is not None and _config.multiuser:
        return current_binding().client
    if _client is None:
        raise RuntimeError("Server not initialized. Call create_server() first.")
    return _client


def get_config() -> Config:
    """Get the config of this call. Raises if server not initialized."""
    if _config is None:
        raise RuntimeError("Server not initialized. Call create_server() first.")
    if _config.multiuser:
        return current_binding().config
    return _config


def get_server_config() -> Config:
    """The server's own config (in multi-user mode: without any login)."""
    if _config is None:
        raise RuntimeError("Server not initialized. Call create_server() first.")
    return _config


def set_state(client: NextcloudClient | None, config: Config) -> None:
    """Set the global state. Called once at server startup (client is None in multi-user mode)."""
    global _client, _config
    _client = client
    _config = config
