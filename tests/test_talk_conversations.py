"""Unit tests for the conversation list: what Talk is asked for and what is reported back."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import talk

ROOM = {
    "token": "abc12xyz",
    "type": 2,
    "displayName": "Team",
    "description": "",
    "readOnly": 0,
    "hasCall": False,
    "unreadMessages": 0,
    "unreadMention": False,
    "lastActivity": 1700000000,
    "isFavorite": False,
    "isArchived": False,
    "notificationLevel": 0,
    "participantCount": 3,
    "canLeaveConversation": True,
    "canDeleteConversation": False,
}


@pytest.fixture
def mcp_with_mock_client(monkeypatch: pytest.MonkeyPatch) -> tuple[FastMCP, MagicMock]:
    set_permission_level(PermissionLevel.READ)
    mock_client = MagicMock()
    mock_client.ocs_get = AsyncMock()
    monkeypatch.setattr(talk, "get_client", lambda: mock_client)
    mcp = FastMCP("test-talk-conversations")
    talk.register(mcp)
    return mcp, mock_client


async def _call(mcp: FastMCP, tool: str, **args: Any) -> str:
    return await mcp._tool_manager.call_tool(tool, args)


class TestFormatConversation:
    def test_reports_mute_and_archive_state(self) -> None:
        data = talk._format_conversation({**ROOM, "isArchived": True, "notificationLevel": 3})
        assert data["is_archived"] is True
        assert data["notification_level"] == "never"

    def test_defaults_when_talk_omits_the_fields(self) -> None:
        data = talk._format_conversation({"token": "t", "type": 2})
        assert data["is_archived"] is False
        assert data["notification_level"] == "default"

    def test_unknown_level_is_reported_verbatim(self) -> None:
        data = talk._format_conversation({**ROOM, "notificationLevel": 9})
        assert data["notification_level"] == "unknown(9)"


class TestListConversations:
    async def test_asks_talk_not_to_touch_the_users_presence(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = [ROOM]
        await _call(mcp, "list_conversations")
        assert client.ocs_get.await_args.kwargs["params"] == {"noStatusUpdate": "1"}

    async def test_muted_and_archived_conversations_are_listed(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = [
            {**ROOM, "token": "muted", "notificationLevel": 3},
            {**ROOM, "token": "archived", "isArchived": True},
        ]
        result = json.loads(await _call(mcp, "list_conversations"))
        assert [c["token"] for c in result["data"]] == ["muted", "archived"]
        assert result["data"][0]["notification_level"] == "never"
        assert result["data"][1]["is_archived"] is True

    async def test_pagination_is_applied_to_the_full_list(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = [{**ROOM, "token": f"room{i}"} for i in range(5)]
        result = json.loads(await _call(mcp, "list_conversations", limit=2, offset=1))
        assert [c["token"] for c in result["data"]] == ["room1", "room2"]
        assert result["pagination"] == {"count": 2, "offset": 1, "limit": 2, "has_more": True}

    def test_no_filter_argument_is_advertised(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, _ = mcp_with_mock_client
        tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "list_conversations")
        assert sorted(tool.parameters["properties"]) == ["limit", "offset"]
