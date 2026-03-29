"""Integration tests verifying error handling when tools are called by a non-admin user.

These tests run against the same Nextcloud but authenticate as a regular user
(no admin privileges). They verify that admin-only operations return clear
errors instead of crashing or leaking data.
"""

import json
import os
from collections.abc import AsyncGenerator

import pytest
from mcp.server.fastmcp import FastMCP

from nc_mcp_server.client import NextcloudClient, NextcloudError
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_server
from nc_mcp_server.state import get_client

pytestmark = pytest.mark.integration

TEST_USER = "mcp-ci-user"
TEST_PASS = "t3St*Pw!xQ9#mK2z"


class McpUserHelper:
    """Helper that wraps FastMCP for calling tools as a non-admin user."""

    def __init__(self, mcp: FastMCP, client: NextcloudClient) -> None:
        self.mcp = mcp
        self.client = client

    async def call(self, tool_name: str, **kwargs: object) -> str:
        result = await self.mcp._tool_manager.call_tool(tool_name, dict(kwargs))
        if not isinstance(result, list):
            return str(result)
        parts = [item.text if hasattr(item, "text") else f"[{type(item).__name__}]" for item in result]
        return parts[0] if len(parts) == 1 else "\n".join(parts)


def _get_user_url() -> str:
    return os.environ.get("NEXTCLOUD_URL", "http://nextcloud.ncmcp")


@pytest.fixture(scope="module")
async def _ensure_test_user() -> None:
    """Create the test user via admin client if it doesn't exist."""
    admin_config = Config(
        nextcloud_url=_get_user_url(),
        user=os.environ.get("NEXTCLOUD_USER", "admin"),
        password=os.environ.get("NEXTCLOUD_PASSWORD", "admin"),
    )
    client = NextcloudClient(admin_config)
    try:
        await client.ocs_get(f"cloud/users/{TEST_USER}")
    except NextcloudError:
        await client.ocs_post("cloud/users", data={"userid": TEST_USER, "password": TEST_PASS})
    await client.close()


@pytest.fixture
async def user_mcp(_ensure_test_user: None) -> AsyncGenerator[McpUserHelper]:
    """MCP server authenticated as a regular (non-admin) user with DESTRUCTIVE permissions."""
    config = Config(
        nextcloud_url=_get_user_url(),
        user=TEST_USER,
        password=TEST_PASS,
        permission_level=PermissionLevel.DESTRUCTIVE,
        is_app_password=os.environ.get("NEXTCLOUD_MCP_APP_PASSWORD", "").lower() in ("true", "1", "yes"),
    )
    config.validate()
    mcp = create_server(config)
    helper = McpUserHelper(mcp, get_client())
    yield helper
    await helper.client.close()


class TestUserCanAccessOwnData:
    @pytest.mark.asyncio
    async def test_get_current_user(self, user_mcp: McpUserHelper) -> None:
        result = await user_mcp.call("get_current_user")
        user = json.loads(result)
        assert user["id"] == TEST_USER

    @pytest.mark.asyncio
    async def test_list_directory(self, user_mcp: McpUserHelper) -> None:
        result = await user_mcp.call("list_directory")
        entries = json.loads(result)
        assert isinstance(entries, list)

    @pytest.mark.asyncio
    async def test_get_user_status(self, user_mcp: McpUserHelper) -> None:
        result = await user_mcp.call("get_user_status")
        status = json.loads(result)
        assert status["user_id"] == TEST_USER


class TestAdminOnlyToolsReturnErrors:
    @pytest.mark.asyncio
    async def test_list_users_forbidden(self, user_mcp: McpUserHelper) -> None:
        with pytest.raises(Exception, match=r"403|Forbidden|admin"):
            await user_mcp.call("list_users")

    @pytest.mark.asyncio
    async def test_create_user_forbidden(self, user_mcp: McpUserHelper) -> None:
        with pytest.raises(Exception, match=r"403|Forbidden|admin|sub admin"):
            await user_mcp.call("create_user", user_id="hacker", password=TEST_PASS)

    @pytest.mark.asyncio
    async def test_delete_user_forbidden(self, user_mcp: McpUserHelper) -> None:
        with pytest.raises(Exception, match=r"403|Forbidden|admin"):
            await user_mcp.call("delete_user", user_id="admin")

    @pytest.mark.asyncio
    async def test_list_apps_forbidden(self, user_mcp: McpUserHelper) -> None:
        with pytest.raises(Exception, match=r"403|Forbidden|admin"):
            await user_mcp.call("list_apps")

    @pytest.mark.asyncio
    async def test_enable_app_forbidden(self, user_mcp: McpUserHelper) -> None:
        with pytest.raises(Exception, match=r"403|Forbidden|admin"):
            await user_mcp.call("enable_app", app_id="weather_status")

    @pytest.mark.asyncio
    async def test_disable_app_forbidden(self, user_mcp: McpUserHelper) -> None:
        with pytest.raises(Exception, match=r"403|Forbidden|admin"):
            await user_mcp.call("disable_app", app_id="weather_status")
