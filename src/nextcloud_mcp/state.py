"""Global state — holds the Nextcloud client and config singletons."""

from .client import NextcloudClient
from .config import Config

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


def set_state(client: NextcloudClient, config: Config) -> None:
    """Set the global state. Called once at server startup."""
    global _client, _config
    _client = client
    _config = config
