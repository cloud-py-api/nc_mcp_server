"""Unit tests for Circles member level changes."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import circles

LOCK_ERROR = NextcloudError(
    "OCS PUT x: An exception occurred while executing a query: SQLSTATE[0A000]: Feature not supported: 7 ERROR:"
    "  FOR UPDATE cannot be applied to the nullable side of an outer join",
    400,
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.WRITE)
    mock = MagicMock()
    mock.ocs_put_json = AsyncMock(return_value={"id": "m1", "level": 9})
    monkeypatch.setattr(circles, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-circles")
    circles.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


class TestMemberLevel:
    async def test_owner_transfer(self, mcp: FastMCP, client: MagicMock) -> None:
        result = await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")
        assert '"level": 9' in result
        client.ocs_put_json.assert_awaited_once_with("apps/circles/circles/c/members/m1/level", json_data={"level": 9})

    async def test_owner_transfer_the_database_refuses(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = LOCK_ERROR
        with pytest.raises(ToolError, match=r"cannot transfer ownership.*Nothing was changed"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")

    async def test_other_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = NextcloudError("OCS PUT x: Member not found", 404)
        with pytest.raises(ToolError, match="Member not found"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")

    async def test_sqlite_refusal(self, mcp: FastMCP, client: MagicMock) -> None:
        message = "OCS PUT x: Operation 'FOR UPDATE' is not supported by platform."
        client.ocs_put_json.side_effect = NextcloudError(message, 500)
        with pytest.raises(ToolError, match="cannot transfer ownership"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")

    async def test_other_for_update_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = NextcloudError("OCS PUT x: deadlock detected while waiting FOR UPDATE", 500)
        with pytest.raises(ToolError, match="deadlock detected"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")

    async def test_the_lock_message_only_applies_to_owner(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = LOCK_ERROR
        with pytest.raises(ToolError, match="FOR UPDATE cannot be applied"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="admin")
