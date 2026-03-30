"""Integration tests for App Management tools against a real Nextcloud instance."""

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

SAFE_APP = "weather_status"


class TestListApps:
    @pytest.mark.asyncio
    async def test_list_enabled_returns_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_apps")
        apps: list[str] = json.loads(result)
        assert isinstance(apps, list)
        assert len(apps) > 0

    @pytest.mark.asyncio
    async def test_list_enabled_contains_core_apps(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_apps", app_filter="enabled")
        apps = json.loads(result)
        assert "files" in apps
        assert "dav" in apps

    @pytest.mark.asyncio
    async def test_list_all(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_apps", app_filter="all")
        apps = json.loads(result)
        assert len(apps) > 0

    @pytest.mark.asyncio
    async def test_list_disabled(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_apps", app_filter="disabled")
        apps = json.loads(result)
        assert isinstance(apps, list)

    @pytest.mark.asyncio
    async def test_invalid_filter_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("list_apps", app_filter="invalid")


class TestGetAppInfo:
    @pytest.mark.asyncio
    async def test_get_known_app(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_app_info", app_id="files")
        app = json.loads(result)
        assert app["id"] == "files"
        assert app["name"] is not None
        assert app["version"] is not None

    @pytest.mark.asyncio
    async def test_get_app_has_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_app_info", app_id="dav")
        app = json.loads(result)
        for field in ["id", "name", "version", "author"]:
            assert field in app, f"Missing field: {field}"
            assert app[field] is not None, f"Field '{field}' should not be None"

    @pytest.mark.asyncio
    async def test_get_nonexistent_app_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_app_info", app_id="nonexistent_app_xyz")


class TestEnableDisableApp:
    @pytest.mark.asyncio
    async def test_disable_and_enable(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("disable_app", app_id=SAFE_APP)
        try:
            disabled = json.loads(await nc_mcp.call("list_apps", app_filter="disabled"))
            assert SAFE_APP in disabled
            await nc_mcp.call("enable_app", app_id=SAFE_APP)
            enabled = json.loads(await nc_mcp.call("list_apps", app_filter="enabled"))
            assert SAFE_APP in enabled
        finally:
            await nc_mcp.call("enable_app", app_id=SAFE_APP)

    @pytest.mark.asyncio
    async def test_enable_already_enabled(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("enable_app", app_id=SAFE_APP)
        assert "enabled" in result.lower()

    @pytest.mark.asyncio
    async def test_disable_returns_confirmation(self, nc_mcp: McpTestHelper) -> None:
        try:
            result = await nc_mcp.call("disable_app", app_id=SAFE_APP)
            assert "disabled" in result.lower()
        finally:
            await nc_mcp.call("enable_app", app_id=SAFE_APP)


class TestAppManagementPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_apps")
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_enable(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("enable_app", app_id=SAFE_APP)

    @pytest.mark.asyncio
    async def test_read_only_blocks_disable(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("disable_app", app_id=SAFE_APP)
