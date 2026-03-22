"""Integration tests for permission enforcement against a real Nextcloud instance.

These tests verify that the permission model works end-to-end: a server
configured with READ permissions must reject WRITE and DESTRUCTIVE tools,
and a WRITE server must reject DESTRUCTIVE tools. Each test calls the
actual MCP tool against a live Nextcloud — not just the decorator.
"""

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import TEST_BASE_DIR, McpTestHelper

pytestmark = pytest.mark.integration

# Tools grouped by their required permission level
READ_TOOLS = ["list_directory", "get_current_user", "list_users", "get_user", "list_notifications"]
WRITE_TOOLS = ["upload_file", "create_directory"]
DESTRUCTIVE_TOOLS = ["delete_file", "move_file", "dismiss_notification", "dismiss_all_notifications"]


class TestReadOnlyPermissions:
    """A READ-only server should allow READ tools and block everything else."""

    @pytest.mark.asyncio
    async def test_list_directory_allowed(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_directory", path="/")
        entries = json.loads(result)
        assert isinstance(entries, list)

    @pytest.mark.asyncio
    async def test_get_current_user_allowed(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("get_current_user")
        data = json.loads(result)
        assert data["id"] == "admin"

    @pytest.mark.asyncio
    async def test_list_notifications_allowed(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_notifications")
        data = json.loads(result)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_upload_file_blocked(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("upload_file", path="blocked.txt", content="no")

    @pytest.mark.asyncio
    async def test_create_directory_blocked(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("create_directory", path="blocked-dir")

    @pytest.mark.asyncio
    async def test_delete_file_blocked(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("delete_file", path="blocked.txt")

    @pytest.mark.asyncio
    async def test_move_file_blocked(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("move_file", source="a.txt", destination="b.txt")

    @pytest.mark.asyncio
    async def test_dismiss_notification_blocked(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("dismiss_notification", notification_id=1)

    @pytest.mark.asyncio
    async def test_dismiss_all_blocked(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("dismiss_all_notifications")

    @pytest.mark.asyncio
    async def test_error_message_includes_instructions(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"NEXTCLOUD_MCP_PERMISSIONS=write"):
            await nc_mcp_read_only.call("upload_file", path="x.txt", content="x")


class TestWritePermissions:
    """A WRITE server should allow READ + WRITE tools and block DESTRUCTIVE."""

    @pytest.mark.asyncio
    async def test_list_directory_allowed(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call("list_directory", path="/")
        entries = json.loads(result)
        assert isinstance(entries, list)

    @pytest.mark.asyncio
    async def test_upload_file_allowed(self, nc_mcp_write: McpTestHelper) -> None:
        await nc_mcp_write.create_test_dir()
        result = await nc_mcp_write.call("upload_file", path=f"{TEST_BASE_DIR}/write-ok.txt", content="ok")
        assert "uploaded" in result.lower()

    @pytest.mark.asyncio
    async def test_create_directory_allowed(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call("create_directory", path=TEST_BASE_DIR)
        assert "created" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_file_blocked(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_write.call("delete_file", path="x.txt")

    @pytest.mark.asyncio
    async def test_move_file_blocked(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_write.call("move_file", source="a.txt", destination="b.txt")

    @pytest.mark.asyncio
    async def test_dismiss_notification_blocked(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_write.call("dismiss_notification", notification_id=1)

    @pytest.mark.asyncio
    async def test_error_message_includes_instructions(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"NEXTCLOUD_MCP_PERMISSIONS=destructive"):
            await nc_mcp_write.call("delete_file", path="x.txt")


class TestDestructivePermissions:
    """A DESTRUCTIVE server should allow everything."""

    @pytest.mark.asyncio
    async def test_all_read_tools_work(self, nc_mcp: McpTestHelper) -> None:
        for tool in READ_TOOLS:
            kwargs = {}
            if tool == "get_user":
                kwargs = {"user_id": "admin"}
            result = await nc_mcp.call(tool, **kwargs)
            assert result, f"Tool '{tool}' returned empty result"

    @pytest.mark.asyncio
    async def test_write_tools_work(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        result = await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/d-test.txt", content="ok")
        assert "uploaded" in result.lower()

    @pytest.mark.asyncio
    async def test_destructive_tools_work(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/to-delete.txt", content="bye")
        result = await nc_mcp.call("delete_file", path=f"{TEST_BASE_DIR}/to-delete.txt")
        assert "Deleted" in result
