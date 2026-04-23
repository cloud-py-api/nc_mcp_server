"""Unit tests for defensive branches in reminders.py that integration can't reach.

The PUT 400 branch is unreachable by integration because `_validate_due_date`
catches everything that would cause a 400 before the request fires; it can
only be reached in production through clock skew between our process and
Nextcloud. The non-404 DELETE branch would need a 403/500 response on the
workaround DELETE, also unreliable to induce server-side.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import reminders

FUTURE = "2099-01-01T10:00:00+00:00"


@pytest.fixture
def mcp_with_mock_client(monkeypatch: pytest.MonkeyPatch) -> tuple[FastMCP, MagicMock]:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock_client = MagicMock()
    mock_client.ocs_delete = AsyncMock()
    mock_client.ocs_put = AsyncMock()
    monkeypatch.setattr(reminders, "get_client", lambda: mock_client)
    mcp = FastMCP("test-reminders")
    reminders.register(mcp)
    return mcp, mock_client


class TestSetFileReminderDefensiveBranches:
    @pytest.mark.asyncio
    async def test_non_404_delete_error_is_reraised_put_not_called(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        """Non-404 from the workaround DELETE must propagate (reminders.py line 93)."""
        mcp, client = mcp_with_mock_client
        client.ocs_delete.side_effect = NextcloudError("Forbidden", 403)
        with pytest.raises(ToolError, match="Forbidden"):
            await mcp._tool_manager.call_tool("set_file_reminder", {"file_id": 1, "due_date": FUTURE})
        client.ocs_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_server_400_despite_pre_validation_gets_clearer_message(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        """Server 400 after our pre-validation (clock skew) → rewritten error (reminders.py lines 102-103)."""
        mcp, client = mcp_with_mock_client
        client.ocs_delete.side_effect = NextcloudError("Not found", 404)
        client.ocs_put.side_effect = NextcloudError("Bad Request", 400)
        with pytest.raises(ToolError, match="Nextcloud rejected due_date"):
            await mcp._tool_manager.call_tool("set_file_reminder", {"file_id": 1, "due_date": FUTURE})
