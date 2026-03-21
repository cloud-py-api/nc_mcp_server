"""Integration test fixtures — require a running Nextcloud instance."""

import os
from collections.abc import AsyncGenerator

import pytest

from nextcloud_mcp.client import NextcloudClient
from nextcloud_mcp.config import Config
from nextcloud_mcp.permissions import PermissionLevel

# Skip all integration tests if NC is not available
pytestmark = pytest.mark.integration


def _get_integration_config() -> Config:
    """Build config from environment, with defaults for local dev."""
    return Config(
        nextcloud_url=os.environ.get("NEXTCLOUD_URL", "http://nextcloud.ncmcp"),
        user=os.environ.get("NEXTCLOUD_USER", "admin"),
        password=os.environ.get("NEXTCLOUD_PASSWORD", "admin"),
        permission_level=PermissionLevel.DESTRUCTIVE,  # tests need full access
    )


@pytest.fixture
def nc_config() -> Config:
    """Nextcloud config for integration tests."""
    config = _get_integration_config()
    config.validate()
    return config


@pytest.fixture
async def nc_client(nc_config: Config) -> AsyncGenerator[NextcloudClient]:
    """Nextcloud HTTP client for integration tests. Closes after test."""
    client = NextcloudClient(nc_config)
    yield client
    await client.close()
