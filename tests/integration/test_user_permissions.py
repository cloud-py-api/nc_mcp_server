"""Integration tests verifying error handling when tools are called by a non-admin user.

These tests run against the same Nextcloud but authenticate as a regular user
(no admin privileges). They verify that admin-only operations return clear
errors instead of crashing or leaking data.
"""

import json
import os
from collections.abc import AsyncGenerator

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient, NextcloudError
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_server
from nc_mcp_server.state import get_client

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

TEST_USER = "mcp-ci-user"
TEST_PASS = "t3St*Pw!xQ9#mK2z"


@pytest.fixture(scope="module")
async def _ensure_test_user() -> None:
    """Create the test user via admin client if it doesn't exist."""
    admin_config = Config(
        nextcloud_url=os.environ.get("NEXTCLOUD_URL", "http://nextcloud.ncmcp"),
        user=os.environ.get("NEXTCLOUD_USER", "admin"),
        password=os.environ.get("NEXTCLOUD_PASSWORD", "admin"),
    )
    client = NextcloudClient(admin_config)
    try:
        try:
            await client.ocs_get(f"cloud/users/{TEST_USER}")
        except NextcloudError:
            await client.ocs_post("cloud/users", data={"userid": TEST_USER, "password": TEST_PASS})
    finally:
        await client.close()


@pytest.fixture
async def user_mcp(_ensure_test_user: None) -> AsyncGenerator[McpTestHelper]:
    """MCP server authenticated as a regular (non-admin) user with DESTRUCTIVE permissions."""
    config = Config(
        nextcloud_url=os.environ.get("NEXTCLOUD_URL", "http://nextcloud.ncmcp"),
        user=TEST_USER,
        password=TEST_PASS,
        permission_level=PermissionLevel.DESTRUCTIVE,
        is_app_password=os.environ.get("NEXTCLOUD_MCP_APP_PASSWORD", "").lower() in ("true", "1", "yes"),
    )
    config.validate()
    mcp = create_server(config)
    helper = McpTestHelper(mcp, get_client())
    yield helper
    await helper.client.close()


class TestUserCanAccessOwnData:
    @pytest.mark.asyncio
    async def test_get_current_user(self, user_mcp: McpTestHelper) -> None:
        result = await user_mcp.call("get_current_user")
        user = json.loads(result)
        assert user["id"] == TEST_USER

    @pytest.mark.asyncio
    async def test_list_directory(self, user_mcp: McpTestHelper) -> None:
        result = await user_mcp.call("list_directory")
        entries = json.loads(result)
        assert isinstance(entries, list)

    @pytest.mark.asyncio
    async def test_get_user_status(self, user_mcp: McpTestHelper) -> None:
        result = await user_mcp.call("get_user_status")
        status = json.loads(result)
        assert status["user_id"] == TEST_USER

    @pytest.mark.asyncio
    async def test_upload_and_get_file(self, user_mcp: McpTestHelper) -> None:
        await user_mcp.call("upload_file", path="user-test.txt", content="hello from user")
        result = await user_mcp.call("get_file", path="user-test.txt")
        assert "hello from user" in result
        await user_mcp.call("delete_file", path="user-test.txt")

    @pytest.mark.asyncio
    async def test_list_shares(self, user_mcp: McpTestHelper) -> None:
        result = await user_mcp.call("list_shares")
        shares = json.loads(result)
        assert isinstance(shares, list)

    @pytest.mark.asyncio
    async def test_list_conversations(self, user_mcp: McpTestHelper) -> None:
        result = await user_mcp.call("list_conversations")
        convs = json.loads(result)
        assert isinstance(convs, list)

    @pytest.mark.asyncio
    async def test_get_activity(self, user_mcp: McpTestHelper) -> None:
        result = await user_mcp.call("get_activity")
        data = json.loads(result)
        assert "data" in data

    @pytest.mark.asyncio
    async def test_list_notifications(self, user_mcp: McpTestHelper) -> None:
        result = await user_mcp.call("list_notifications")
        notifs = json.loads(result)
        assert isinstance(notifs, list)


class TestAdminOnlyToolsReturnErrors:
    @pytest.mark.asyncio
    async def test_list_users_forbidden(self, user_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"must be.*admin|403|[Ff]orbidden"):
            await user_mcp.call("list_users")

    @pytest.mark.asyncio
    async def test_create_user_forbidden(self, user_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"must be.*admin|403|[Ff]orbidden"):
            await user_mcp.call("create_user", user_id="hacker", password=TEST_PASS)

    @pytest.mark.asyncio
    async def test_delete_user_forbidden(self, user_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"must be.*admin|403|[Ff]orbidden"):
            await user_mcp.call("delete_user", user_id="admin")

    @pytest.mark.asyncio
    async def test_list_apps_forbidden(self, user_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"must be.*admin|403|[Ff]orbidden"):
            await user_mcp.call("list_apps")

    @pytest.mark.asyncio
    async def test_enable_app_forbidden(self, user_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"must be.*admin|403|[Ff]orbidden"):
            await user_mcp.call("enable_app", app_id="weather_status")

    @pytest.mark.asyncio
    async def test_disable_app_forbidden(self, user_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"must be.*admin|403|[Ff]orbidden"):
            await user_mcp.call("disable_app", app_id="weather_status")

    @pytest.mark.asyncio
    async def test_get_app_info_forbidden(self, user_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"must be.*admin|403|[Ff]orbidden"):
            await user_mcp.call("get_app_info", app_id="files")
