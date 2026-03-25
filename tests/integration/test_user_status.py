"""Integration tests for User Status tools against a real Nextcloud instance."""

import contextlib
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nextcloud_mcp.config import Config
from nextcloud_mcp.server import create_server
from nextcloud_mcp.state import get_client

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


class TestGetUserStatus:
    @pytest.mark.asyncio
    async def test_get_own_status(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("set_user_status", status_type="online")
        result = await nc_mcp.call("get_user_status")
        data = json.loads(result)
        assert data["user_id"] == "admin"
        assert data["status"] in {"online", "away", "dnd", "invisible", "offline"}

    @pytest.mark.asyncio
    async def test_get_own_status_has_all_fields(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("set_user_status", status_type="online")
        result = await nc_mcp.call("get_user_status")
        data = json.loads(result)
        for field in ["user_id", "status", "message", "icon", "clear_at"]:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_get_other_user_status(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("set_user_status", status_type="away")
        result = await nc_mcp.call("get_user_status", user_id="admin")
        data = json.loads(result)
        assert data["user_id"] == "admin"
        assert data["status"] == "away"

    @pytest.mark.asyncio
    async def test_get_own_status_when_never_set(self, nc_mcp: McpTestHelper) -> None:
        """A fresh user who never set a status gets a 404 from the API.
        The tool should handle this gracefully and return a default offline status."""
        try:
            await nc_mcp.call("create_user", user_id="mcp-fresh-status", password="t3St*Pw!xQ9#mK2z")
            fresh_config = Config(
                nextcloud_url=nc_mcp.client._base_url,
                user="mcp-fresh-status",
                password="t3St*Pw!xQ9#mK2z",
                permission_level=nc_mcp.client._config.permission_level,
            )
            fresh_mcp = create_server(fresh_config)
            fresh_helper = McpTestHelper(fresh_mcp, get_client())
            try:
                result = await fresh_helper.call("get_user_status")
                data = json.loads(result)
                assert data["user_id"] == "mcp-fresh-status"
                assert data["status"] == "offline"
                assert data["message"] is None
            finally:
                await fresh_helper.client.close()
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-fresh-status")

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_status(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_user_status", user_id="nonexistent-user-xyz-99999")


class TestSetUserStatus:
    @pytest.mark.asyncio
    async def test_set_status_type(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("set_user_status", status_type="dnd")
        data = json.loads(result)
        assert data["status"] == "dnd"

    @pytest.mark.asyncio
    async def test_set_all_status_types(self, nc_mcp: McpTestHelper) -> None:
        for status_type in ["online", "away", "dnd", "invisible", "offline"]:
            result = await nc_mcp.call("set_user_status", status_type=status_type)
            data = json.loads(result)
            assert data["status"] == status_type

    @pytest.mark.asyncio
    async def test_set_custom_message(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("set_user_status", message="Working from home")
        data = json.loads(result)
        assert data["message"] == "Working from home"

    @pytest.mark.asyncio
    async def test_set_message_with_icon(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("set_user_status", message="On vacation", icon="🌴")
        data = json.loads(result)
        assert data["message"] == "On vacation"
        assert data["icon"] == "🌴"

    @pytest.mark.asyncio
    async def test_set_status_type_and_message(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("set_user_status", status_type="away", message="In a meeting", icon="📅")
        data = json.loads(result)
        assert data["message"] == "In a meeting"
        assert data["icon"] == "📅"
        verify = json.loads(await nc_mcp.call("get_user_status"))
        assert verify["status"] == "away"
        assert verify["message"] == "In a meeting"

    @pytest.mark.asyncio
    async def test_invalid_status_type(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("set_user_status", status_type="invalid")

    @pytest.mark.asyncio
    async def test_no_args_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("set_user_status")


class TestClearUserStatus:
    @pytest.mark.asyncio
    async def test_clear_message(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("set_user_status", status_type="away", message="Busy")
        result = await nc_mcp.call("clear_user_status")
        assert "cleared" in result.lower()
        verify = json.loads(await nc_mcp.call("get_user_status"))
        assert verify["message"] is None
        assert verify["status"] == "away"

    @pytest.mark.asyncio
    async def test_clear_when_no_message(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("set_user_status", status_type="online")
        await nc_mcp.call("clear_user_status")
        result = await nc_mcp.call("clear_user_status")
        assert "cleared" in result.lower()


class TestUserStatusPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_get(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("get_user_status")
        data = json.loads(result)
        assert "user_id" in data

    @pytest.mark.asyncio
    async def test_read_only_blocks_set(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("set_user_status", status_type="away")

    @pytest.mark.asyncio
    async def test_read_only_blocks_clear(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("clear_user_status")

    @pytest.mark.asyncio
    async def test_write_allows_clear(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call("clear_user_status")
        assert "cleared" in result.lower()

    @pytest.mark.asyncio
    async def test_write_allows_set(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call("set_user_status", status_type="online")
        data = json.loads(result)
        assert data["status"] == "online"
