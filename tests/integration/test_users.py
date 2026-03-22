"""Integration tests for user tools against a real Nextcloud instance.

Tests call MCP tools by name to exercise the full tool stack.
"""

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
        result = await nc_mcp.call("list_users")
        data = json.loads(result)
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_includes_admin(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users")
        data = json.loads(result)
        assert "users" in data
        assert "admin" in data["users"]

    @pytest.mark.asyncio
    async def test_search_filter(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", search="admin")
        data = json.loads(result)
        assert "admin" in data["users"]

    @pytest.mark.asyncio
    async def test_search_no_match(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", search="nonexistent-user-xyz-12345")
        data = json.loads(result)
        assert data["users"] == []

    @pytest.mark.asyncio
    async def test_limit_parameter(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", limit=1)
        data = json.loads(result)
        assert len(data["users"]) <= 1

    @pytest.mark.asyncio
    async def test_default_parameters(self, nc_mcp: McpTestHelper) -> None:
        # Should work with no arguments
        result = await nc_mcp.call("list_users")
        data = json.loads(result)
        assert "users" in data


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
