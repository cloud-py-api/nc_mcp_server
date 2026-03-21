"""Shared test fixtures."""

import pytest

from nextcloud_mcp.config import Config
from nextcloud_mcp.permissions import PermissionLevel


@pytest.fixture
def read_config() -> Config:
    """Config with READ permissions for unit tests."""
    return Config(
        nextcloud_url="http://localhost:8080",
        user="admin",
        password="admin",
        permission_level=PermissionLevel.READ,
    )


@pytest.fixture
def write_config() -> Config:
    """Config with WRITE permissions for unit tests."""
    return Config(
        nextcloud_url="http://localhost:8080",
        user="admin",
        password="admin",
        permission_level=PermissionLevel.WRITE,
    )


@pytest.fixture
def destructive_config() -> Config:
    """Config with DESTRUCTIVE permissions for unit tests."""
    return Config(
        nextcloud_url="http://localhost:8080",
        user="admin",
        password="admin",
        permission_level=PermissionLevel.DESTRUCTIVE,
    )
