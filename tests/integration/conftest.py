"""Integration test fixtures — require a running Nextcloud instance."""

import contextlib
import os
from collections.abc import AsyncGenerator

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from nextcloud_mcp.client import NextcloudClient
from nextcloud_mcp.config import Config
from nextcloud_mcp.permissions import PermissionLevel
from nextcloud_mcp.server import create_server
from nextcloud_mcp.state import get_client

pytestmark = pytest.mark.integration

# Test data constants
TEST_BASE_DIR = "mcp-test-suite"


class McpTestHelper:
    """Helper that wraps FastMCP for easy tool calling in tests."""

    def __init__(self, mcp: FastMCP, client: NextcloudClient) -> None:
        self.mcp = mcp
        self.client = client

    async def call(self, tool_name: str, **kwargs: object) -> str:
        """Call an MCP tool by name and return its string result."""
        result = await self.mcp._tool_manager.call_tool(tool_name, dict(kwargs))
        if not isinstance(result, list):
            return str(result)
        items: list[TextContent | ImageContent] = result  # type: ignore[assignment]
        parts: list[str] = []
        for item in items:
            if isinstance(item, TextContent):
                parts.append(item.text)
            else:
                parts.append(f"[Image: {item.mimeType}]")
        return parts[0] if len(parts) == 1 else "\n".join(parts)

    def tool_names(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(t.name for t in self.mcp._tool_manager.list_tools())

    async def generate_notification(self, subject: str = "test", message: str = "body") -> None:
        """Create a test notification via the admin_notifications API."""
        await self.client.ocs_post(
            "apps/notifications/api/v1/admin_notifications/admin",
            data={"shortMessage": subject, "longMessage": message},
        )

    async def create_test_dir(self, path: str = TEST_BASE_DIR) -> None:
        """Create the test directory, ignoring if it already exists."""
        with contextlib.suppress(Exception):
            await self.client.dav_mkcol(path)

    async def upload_test_file(self, path: str, content: str = "test content") -> None:
        """Upload a test file via the client (bypasses MCP permission checks)."""
        await self.client.dav_put(path, content.encode("utf-8"), content_type="text/plain; charset=utf-8")


def _get_integration_config(permission: PermissionLevel = PermissionLevel.DESTRUCTIVE) -> Config:
    """Build config from environment, with defaults for local dev."""
    return Config(
        nextcloud_url=os.environ.get("NEXTCLOUD_URL", "http://nextcloud.ncmcp"),
        user=os.environ.get("NEXTCLOUD_USER", "admin"),
        password=os.environ.get("NEXTCLOUD_PASSWORD", "admin"),
        permission_level=permission,
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


@pytest.fixture
async def nc_mcp(nc_config: Config) -> AsyncGenerator[McpTestHelper]:
    """Full MCP server with all tools registered (DESTRUCTIVE permissions)."""
    mcp = create_server(nc_config)
    helper = McpTestHelper(mcp, get_client())
    yield helper
    await helper.client.close()


@pytest.fixture
async def nc_mcp_read_only() -> AsyncGenerator[McpTestHelper]:
    """MCP server with READ-only permissions for permission enforcement tests."""
    config = _get_integration_config(PermissionLevel.READ)
    config.validate()
    mcp = create_server(config)
    helper = McpTestHelper(mcp, get_client())
    yield helper
    await helper.client.close()


@pytest.fixture
async def nc_mcp_write() -> AsyncGenerator[McpTestHelper]:
    """MCP server with WRITE permissions for permission enforcement tests."""
    config = _get_integration_config(PermissionLevel.WRITE)
    config.validate()
    mcp = create_server(config)
    helper = McpTestHelper(mcp, get_client())
    yield helper
    await helper.client.close()


@pytest.fixture(scope="session")
def _cleanup_config() -> Config:
    """Config for cleanup client — session-scoped to avoid repeated creation."""
    config = _get_integration_config()
    config.validate()
    return config


@pytest.fixture(autouse=True)
async def _clean_test_data(_cleanup_config: Config) -> AsyncGenerator[None]:
    """Clean up test data before and after each test."""
    client = NextcloudClient(_cleanup_config)
    await _cleanup(client)
    yield
    await _cleanup(client)
    await client.close()


async def _cleanup(client: NextcloudClient) -> None:
    """Remove all test artifacts from Nextcloud."""
    with contextlib.suppress(Exception):
        shares = await client.ocs_get("apps/files_sharing/api/v1/shares")
        for share in shares:
            share_path = str(share.get("path", ""))
            if share_path != f"/{TEST_BASE_DIR}" and not share_path.startswith(f"/{TEST_BASE_DIR}/"):
                continue
            with contextlib.suppress(Exception):
                await client.ocs_delete(f"apps/files_sharing/api/v1/shares/{share['id']}")
    with contextlib.suppress(Exception):
        await client.dav_delete(TEST_BASE_DIR)
    with contextlib.suppress(Exception):
        await client.ocs_delete("apps/notifications/api/v2/notifications")
    with contextlib.suppress(Exception):
        await client.ocs_delete("apps/user_status/api/v1/user_status/message")
    with contextlib.suppress(Exception):
        await client.ocs_put("apps/user_status/api/v1/user_status/status", data={"statusType": "online"})
