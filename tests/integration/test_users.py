"""Integration tests for user tools against a real Nextcloud instance.

Tests call MCP tools by name to exercise the full tool stack.
"""

import contextlib
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_returns_valid_json(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_current_user")
        data = json.loads(result)
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_returns_admin_user(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_current_user")
        data = json.loads(result)
        assert data["id"] == "admin"

    @pytest.mark.asyncio
    async def test_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_current_user")
        data = json.loads(result)
        assert "id" in data
        assert "displayname" in data
        assert "email" in data

    @pytest.mark.asyncio
    async def test_has_quota_info(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_current_user")
        data = json.loads(result)
        assert "quota" in data


class TestListUsers:
    @pytest.mark.asyncio
    async def test_returns_valid_json(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", limit=200)
        data = json.loads(result)
        assert isinstance(data, dict)
        assert "data" in data
        assert "pagination" in data

    @pytest.mark.asyncio
    async def test_includes_admin(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", limit=200)
        users = json.loads(result)["data"]
        assert "admin" in users

    @pytest.mark.asyncio
    async def test_search_filter(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", search="admin", limit=200)
        users = json.loads(result)["data"]
        assert "admin" in users

    @pytest.mark.asyncio
    async def test_search_no_match(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", search="nonexistent-user-xyz-12345", limit=200)
        users = json.loads(result)["data"]
        assert users == []

    @pytest.mark.asyncio
    async def test_limit_parameter(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", limit=1)
        data = json.loads(result)
        assert len(data["data"]) <= 1

    @pytest.mark.asyncio
    async def test_default_parameters(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users")
        data = json.loads(result)
        assert "data" in data
        assert "pagination" in data


class TestGetUser:
    @pytest.mark.asyncio
    async def test_get_admin(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_user", user_id="admin")
        data = json.loads(result)
        assert data["id"] == "admin"
        assert "displayname" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_user", user_id="nonexistent-user-xyz-12345")

    @pytest.mark.asyncio
    async def test_user_has_detailed_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_user", user_id="admin")
        data = json.loads(result)
        expected_fields = ["id", "displayname", "email", "enabled", "groups"]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_create_and_verify(self, nc_mcp: McpTestHelper) -> None:
        try:
            result = await nc_mcp.call("create_user", user_id="mcp-test-user", password="t3St*Pw!xQ9#mK2z")
            data = json.loads(result)
            assert data["id"] == "mcp-test-user"
            verify = json.loads(await nc_mcp.call("get_user", user_id="mcp-test-user"))
            assert verify["id"] == "mcp-test-user"
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-test-user")

    @pytest.mark.asyncio
    async def test_create_with_display_name(self, nc_mcp: McpTestHelper) -> None:
        try:
            await nc_mcp.call(
                "create_user", user_id="mcp-test-dn", password="t3St*Pw!xQ9#mK2z", display_name="Test Display"
            )
            verify = json.loads(await nc_mcp.call("get_user", user_id="mcp-test-dn"))
            assert verify["displayname"] == "Test Display"
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-test-dn")

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, nc_mcp: McpTestHelper) -> None:
        try:
            await nc_mcp.call("create_user", user_id="mcp-test-dup", password="t3St*Pw!xQ9#mK2z")
            with pytest.raises(ToolError):
                await nc_mcp.call("create_user", user_id="mcp-test-dup", password="t3St*Pw!xQ9#mK2z")
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-test-dup")


class TestDeleteUser:
    @pytest.mark.asyncio
    async def test_delete_user(self, nc_mcp: McpTestHelper) -> None:
        try:
            await nc_mcp.call("create_user", user_id="mcp-test-del", password="t3St*Pw!xQ9#mK2z")
            result = await nc_mcp.call("delete_user", user_id="mcp-test-del")
            assert "deleted" in result.lower()
            with pytest.raises(ToolError):
                await nc_mcp.call("get_user", user_id="mcp-test-del")
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-test-del")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("delete_user", user_id="nonexistent-user-xyz-99999")


class TestUserPermissions:
    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("create_user", user_id="blocked", password="Test123!")

    @pytest.mark.asyncio
    async def test_write_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_user", user_id="nobody")
