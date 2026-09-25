"""Unit tests for conversation preferences, pins, reminders and the modified_since filter."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import talk

ROOM = "apps/spreed/api/v4/room/tok"
CHAT = "apps/spreed/api/v1/chat/tok"
RAW_ROOM = {"token": "tok", "type": 2, "isFavorite": True, "notificationLevel": 2, "notificationCalls": 0}


def _later(**delta: int) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) + timedelta(**delta)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock = MagicMock()
    mock.ocs_get = AsyncMock(return_value=[])
    mock.ocs_post_json = AsyncMock(return_value=RAW_ROOM)
    mock.ocs_delete = AsyncMock(return_value=RAW_ROOM)
    monkeypatch.setattr(talk, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-talk-settings")
    talk.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


class TestTimestamp:
    def test_converts_to_unix_time(self) -> None:
        when = _later(hours=1)
        assert talk._timestamp(when.isoformat(), "x") == int(when.timestamp())

    def test_accepts_z_suffix(self) -> None:
        when = _later(hours=1)
        assert talk._timestamp(when.strftime("%Y-%m-%dT%H:%M:%SZ"), "x") == int(when.timestamp())

    @pytest.mark.parametrize(
        ("value", "message"),
        [("tomorrow", "ISO 8601"), ("2030-01-01T09:00:00", "needs a time zone"), ("2020-01-01T09:00:00Z", "future")],
    )
    def test_refusals(self, value: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            talk._timestamp(value, "remind_at")

    def test_past_is_allowed_when_asked(self) -> None:
        assert talk._timestamp("2020-01-01T00:00:00Z", "x", future=False) == 1577836800

    def test_before_1970_is_refused(self) -> None:
        with pytest.raises(ValueError, match="before 1970"):
            talk._timestamp("1969-12-31T00:00:00Z", "modified_since", future=False)


class TestConversationFields:
    def test_new_fields(self) -> None:
        data = talk._format_conversation(
            {"token": "t", "isImportant": True, "isSensitive": True, "notificationCalls": 0, "lastPinnedId": 7}
        )
        assert (data["is_important"], data["is_sensitive"], data["call_notifications"], data["pinned_message_id"]) == (
            True,
            True,
            False,
            7,
        )

    def test_pin_hidden_for_yourself_reads_as_none(self) -> None:
        data = talk._format_conversation({"token": "t", "lastPinnedId": 7, "hiddenPinnedId": 7})
        assert data["pinned_message_id"] == 0
        data = talk._format_conversation({"token": "t", "lastPinnedId": 8, "hiddenPinnedId": 7})
        assert data["pinned_message_id"] == 8

    def test_defaults(self) -> None:
        data = talk._format_conversation({"token": "t"})
        assert (data["is_important"], data["is_sensitive"], data["call_notifications"], data["pinned_message_id"]) == (
            False,
            False,
            True,
            0,
        )


class TestConversationPreferences:
    async def test_every_setting(self, mcp: FastMCP, client: MagicMock) -> None:
        result = json.loads(
            await _call(
                mcp,
                "set_conversation_preferences",
                token="tok",
                favorite=True,
                archived=False,
                important=True,
                sensitive=False,
                notification_level="Mention",
                call_notifications=False,
            )
        )
        assert client.method_calls == [
            call.ocs_post_json(f"{ROOM}/favorite", json_data={}),
            call.ocs_delete(f"{ROOM}/archive"),
            call.ocs_post_json(f"{ROOM}/important", json_data={}),
            call.ocs_delete(f"{ROOM}/sensitive"),
            call.ocs_post_json(f"{ROOM}/notify", json_data={"level": 2}),
            call.ocs_post_json(f"{ROOM}/notify-calls", json_data={"level": 0}),
        ]
        assert (result["is_favorite"], result["notification_level"], result["call_notifications"]) == (
            True,
            "mention",
            False,
        )

    async def test_only_what_is_passed(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "set_conversation_preferences", token="tok", call_notifications=True)
        assert client.method_calls == [call.ocs_post_json(f"{ROOM}/notify-calls", json_data={"level": 1})]

    async def test_failure_names_what_was_already_changed(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.side_effect = NextcloudError("OCS DELETE x: HTTP 400 (classified)", 400)
        expected = r"\(classified\)\. Failed at sensitive \(favorite, important already changed\)"
        args = {"favorite": True, "important": True, "sensitive": False, "call_notifications": True}
        with pytest.raises(ToolError, match=expected):
            await _call(mcp, "set_conversation_preferences", token="tok", **args)
        # The call notification setting after the failure is never sent
        assert call.ocs_post_json(f"{ROOM}/notify-calls", json_data={"level": 1}) not in client.method_calls

    async def test_failure_on_the_first_setting(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.side_effect = NextcloudError("OCS POST x: Not found.", 404)
        with pytest.raises(ToolError, match=r"Not found\.\. Failed at favorite$"):
            await _call(mcp, "set_conversation_preferences", token="tok", favorite=True)

    async def test_nothing_to_change(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="at least one setting"):
            await _call(mcp, "set_conversation_preferences", token="tok")
        assert client.method_calls == []

    @pytest.mark.parametrize("level", ["default", "loud"])
    async def test_invalid_level_is_refused_before_any_request(
        self, mcp: FastMCP, client: MagicMock, level: str
    ) -> None:
        with pytest.raises(ToolError, match="Must be one of: always, mention, never"):
            await _call(mcp, "set_conversation_preferences", token="tok", favorite=True, notification_level=level)
        assert client.method_calls == []


class TestModifiedSince:
    async def test_sent_as_unix_time(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "list_conversations", modified_since="2026-01-01T00:00:00+01:00")
        client.ocs_get.assert_awaited_once_with(
            "apps/spreed/api/v4/room", params={"noStatusUpdate": "1", "modifiedSince": "1767222000"}
        )

    async def test_left_out_by_default(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "list_conversations")
        assert client.ocs_get.await_args.kwargs["params"] == {"noStatusUpdate": "1"}


class TestPins:
    async def test_pin_until(self, mcp: FastMCP, client: MagicMock) -> None:
        until = _later(days=1)
        client.ocs_post_json.return_value = {"id": 9, "parent": {"id": 5, "message": "hi", "actorDisplayName": "A"}}
        result = json.loads(await _call(mcp, "pin_message", token="tok", message_id=5, until=until.isoformat()))
        client.ocs_post_json.assert_awaited_once_with(f"{CHAT}/5/pin", json_data={"pinUntil": int(until.timestamp())})
        assert (result["id"], result["message"]) == (5, "hi")

    async def test_pin_without_expiry(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = {"id": 5, "message": "hi"}
        await _call(mcp, "pin_message", token="tok", message_id=5)
        assert client.ocs_post_json.await_args.kwargs["json_data"] == {}

    async def test_already_pinned(self, mcp: FastMCP, client: MagicMock) -> None:
        """Talk answers an empty 200 and keeps the old expiry."""
        client.ocs_post_json.return_value = None
        result = await _call(mcp, "pin_message", token="tok", message_id=5, until=_later(days=1).isoformat())
        assert result == "Message 5 was already pinned; to change when the pin expires, unpin it first."

    async def test_unpin_for_everyone(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.return_value = {"id": 9, "systemMessage": "message_unpinned"}
        assert await _call(mcp, "unpin_message", token="tok", message_id=5) == "Message 5 unpinned."
        client.ocs_delete.assert_awaited_once_with(f"{CHAT}/5/pin")

    async def test_unpin_what_was_not_pinned(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.return_value = None
        assert await _call(mcp, "unpin_message", token="tok", message_id=5) == "Message 5 was not pinned."

    async def test_hide_for_yourself(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.return_value = None
        result = await _call(mcp, "unpin_message", token="tok", message_id=5, for_everyone=False)
        client.ocs_delete.assert_awaited_once_with(f"{CHAT}/5/pin/self")
        assert result == "Pinned message 5 hidden for you."


class TestReminders:
    async def test_set(self, mcp: FastMCP, client: MagicMock) -> None:
        when = _later(hours=2)
        client.ocs_post_json.return_value = {"token": "tok", "messageId": 5, "timestamp": int(when.timestamp())}
        result = json.loads(
            await _call(mcp, "set_message_reminder", token="tok", message_id=5, remind_at=when.isoformat())
        )
        client.ocs_post_json.assert_awaited_once_with(
            f"{CHAT}/5/reminder", json_data={"timestamp": int(when.timestamp())}
        )
        assert result == {"token": "tok", "message_id": 5, "remind_at": when.isoformat()}

    async def test_list_soonest_first(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [
            {"reminderTimestamp": 2000, "roomToken": "b", "messageId": 2, "actorDisplayName": "B", "message": "late"},
            {"reminderTimestamp": 1000, "roomToken": "a", "messageId": 1, "actorDisplayName": "A", "message": "soon"},
        ]
        result = json.loads(await _call(mcp, "list_message_reminders"))
        client.ocs_get.assert_awaited_once_with("apps/spreed/api/v1/chat/upcoming-reminders")
        assert result == [
            {"token": "a", "message_id": 1, "remind_at": "1970-01-01T00:16:40+00:00", "message": "[1] A: soon"},
            {"token": "b", "message_id": 2, "remind_at": "1970-01-01T00:33:20+00:00", "message": "[2] B: late"},
        ]

    async def test_remove(self, mcp: FastMCP, client: MagicMock) -> None:
        assert (
            await _call(mcp, "remove_message_reminder", token="tok", message_id=5) == "Reminder for message 5 removed."
        )
        client.ocs_delete.assert_awaited_once_with(f"{CHAT}/5/reminder")
