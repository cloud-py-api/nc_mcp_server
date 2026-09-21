"""Unit tests for the Talk thread helpers and the argument checks of the thread tools."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import talk

ROOT = {
    "id": 23,
    "actorDisplayName": "admin",
    "message": "root msg",
    "threadId": 23,
    "isThread": True,
    "threadTitle": "My Thread",
    "threadReplies": 1,
}
REPLY = {**ROOT, "id": 25, "message": "a reply"}
PLAIN = {"id": 26, "actorDisplayName": "admin", "message": "plain", "threadId": 26}


@pytest.fixture
def mcp_with_mock_client(monkeypatch: pytest.MonkeyPatch) -> tuple[FastMCP, MagicMock]:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock_client = MagicMock()
    mock_client.ocs_get = AsyncMock()
    mock_client.ocs_post = AsyncMock()
    mock_client.ocs_put = AsyncMock()
    monkeypatch.setattr(talk, "get_client", lambda: mock_client)
    mcp = FastMCP("test-talk")
    talk.register(mcp)
    return mcp, mock_client


async def _call(mcp: FastMCP, tool: str, **args: Any) -> str:
    return await mcp._tool_manager.call_tool(tool, args)


class TestFormatMessageCompact:
    def test_plain_message_unchanged(self) -> None:
        assert talk._format_message_compact(PLAIN) == "[26] admin: plain"

    def test_message_without_thread_fields(self) -> None:
        assert talk._format_message_compact({"id": 5, "actorDisplayName": "bob", "message": "hi"}) == "[5] bob: hi"

    def test_reply_to_non_thread_message_is_not_marked(self) -> None:
        msg = {"id": 30, "actorDisplayName": "admin", "message": "re", "threadId": 26}
        assert talk._format_message_compact(msg) == "[30] admin: re"

    def test_thread_root_shows_id_and_title(self) -> None:
        assert talk._format_message_compact(ROOT) == '[23] admin [thread 23 "My Thread"]: root msg'

    def test_thread_reply_shows_thread_id_only(self) -> None:
        assert talk._format_message_compact(REPLY) == "[25] admin [thread 23]: a reply"

    def test_title_is_quoted_safely(self) -> None:
        msg = {**ROOT, "threadTitle": 'Say "hi" ]: ü'}
        assert talk._format_message_compact(msg) == '[23] admin [thread 23 "Say \\"hi\\" ]: ü"]: root msg'


class TestFormatMessageFull:
    def test_plain_message_has_zero_thread_id(self) -> None:
        data = talk._format_message_full(PLAIN)
        assert data["thread_id"] == 0
        assert "thread_title" not in data

    def test_thread_message_has_thread_id_and_title(self) -> None:
        data = talk._format_message_full(REPLY)
        assert data["id"] == 25
        assert data["thread_id"] == 23
        assert data["thread_title"] == "My Thread"


class TestFormatThread:
    def test_full_thread_info(self) -> None:
        info = {
            "thread": {
                "id": 23,
                "roomToken": "abc123",
                "title": "My Thread",
                "lastMessageId": 25,
                "lastActivity": 1789722933,
                "numReplies": 1,
            },
            "attendee": {"notificationLevel": 2},
            "first": ROOT,
            "last": REPLY,
        }
        assert talk._format_thread(info) == {
            "thread_id": 23,
            "token": "abc123",
            "title": "My Thread",
            "num_replies": 1,
            "last_activity": 1789722933,
            "notification_level": "mention",
            "first": '[23] admin [thread 23 "My Thread"]: root msg',
            "last": "[25] admin [thread 23]: a reply",
        }

    def test_missing_messages_become_null(self) -> None:
        info = {"thread": {"id": 27, "roomToken": "abc123", "title": "t"}, "attendee": {}, "first": None, "last": None}
        data = talk._format_thread(info)
        assert data["first"] is None
        assert data["last"] is None
        assert data["notification_level"] == "default"

    def test_unknown_notification_level(self) -> None:
        info = {"thread": {"id": 1}, "attendee": {"notificationLevel": 9}, "first": None, "last": None}
        assert talk._format_thread(info)["notification_level"] == "unknown(9)"


class TestFormatMessageList:
    def test_footer_without_thread(self) -> None:
        result = talk._format_message_list([REPLY, ROOT], include_system=False, thread_id=0)
        assert result.endswith("--- 2 messages. For older messages, call with before_message_id=23 ---")

    def test_footer_repeats_thread_id(self) -> None:
        result = talk._format_message_list([REPLY, ROOT], include_system=False, thread_id=23)
        assert result.endswith("call with before_message_id=23, thread_id=23 ---")

    def test_system_messages_filtered(self) -> None:
        system = {**REPLY, "id": 24, "systemMessage": "thread_created", "message": "You created thread {title}"}
        result = talk._format_message_list([REPLY, system, ROOT], include_system=False, thread_id=0)
        assert "thread_created" not in result
        assert "{title}" not in result

    def test_empty_list(self) -> None:
        assert talk._format_message_list([], include_system=False, thread_id=23) == ""

    def test_footer_paginates_from_a_hidden_system_message(self) -> None:
        system = {**REPLY, "id": 22, "systemMessage": "thread_created", "message": "You created thread {title}"}
        result = talk._format_message_list([REPLY, system], include_system=False, thread_id=0)
        assert result.startswith("[25] admin [thread 23]: a reply\n")
        assert result.endswith("--- 1 messages. For older messages, call with before_message_id=22 ---")

    def test_page_of_only_system_messages_still_has_a_footer(self) -> None:
        """Without the footer the caller cannot tell a filtered page from the end of the history."""
        system = {**REPLY, "id": 24, "systemMessage": "thread_renamed", "message": "You renamed thread {title}"}
        result = talk._format_message_list([system], include_system=False, thread_id=23)
        assert result == "\n--- 0 messages. For older messages, call with before_message_id=24, thread_id=23 ---"


class TestBuildMessagePayload:
    def test_plain_message(self) -> None:
        assert talk._build_message_payload("hi", 0, 0, "") == {"message": "hi"}

    def test_reply(self) -> None:
        assert talk._build_message_payload("hi", 5, 0, "") == {"message": "hi", "replyTo": 5}

    def test_post_into_thread(self) -> None:
        assert talk._build_message_payload("hi", 0, 23, "") == {"message": "hi", "threadId": 23}

    def test_new_thread_title_is_stripped(self) -> None:
        assert talk._build_message_payload("hi", 0, 0, "  Plan  ") == {"message": "hi", "threadTitle": "Plan"}

    @pytest.mark.parametrize(
        ("reply_to", "thread_id", "thread_title", "match"),
        [
            (5, 0, "Plan", "cannot be combined with reply_to or thread_id"),
            (0, 23, "Plan", "cannot be combined with reply_to or thread_id"),
            (5, 23, "", "either thread_id or reply_to"),
            (0, 0, "   ", "thread_title must not be blank"),
        ],
    )
    def test_invalid_combinations(self, reply_to: int, thread_id: int, thread_title: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            talk._build_message_payload("hi", reply_to, thread_id, thread_title)

    def test_blank_message(self) -> None:
        with pytest.raises(ValueError, match="message must not be empty"):
            talk._build_message_payload("  \n", 0, 0, "Plan")


class TestParseNotificationLevel:
    @pytest.mark.parametrize(("name", "level"), [("default", 0), ("always", 1), ("mention", 2), ("never", 3)])
    def test_valid_names(self, name: str, level: int) -> None:
        assert talk._parse_thread_notification_level(name) == level

    def test_case_and_whitespace_ignored(self) -> None:
        assert talk._parse_thread_notification_level(" Always ") == 1

    def test_invalid_name(self) -> None:
        with pytest.raises(ValueError, match="Must be one of: default, always, mention, never"):
            talk._parse_thread_notification_level("loud")


class TestThreadToolsWithMockClient:
    @pytest.mark.asyncio
    async def test_send_message_rejects_combination_before_request(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        with pytest.raises(ToolError, match="either thread_id or reply_to"):
            await _call(mcp, "send_message", token="abc123", message="hi", reply_to=5, thread_id=23)
        client.ocs_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_into_unknown_thread_names_the_thread(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_post.side_effect = NextcloudError("OCS POST apps/spreed/api/v1/chat/abc123: HTTP 400", 400)
        with pytest.raises(ToolError, match="Thread 999 not found in conversation abc123"):
            await _call(mcp, "send_message", token="abc123", message="hi", thread_id=999)

    @pytest.mark.asyncio
    async def test_send_without_thread_keeps_original_400(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_post.side_effect = NextcloudError("OCS POST apps/spreed/api/v1/chat/abc123: HTTP 400", 400)
        with pytest.raises(ToolError, match="HTTP 400"):
            await _call(mcp, "send_message", token="abc123", message="hi")

    @pytest.mark.asyncio
    async def test_get_messages_passes_thread_filter(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = [REPLY, ROOT]
        result = await _call(mcp, "get_messages", token="abc123", thread_id=23)
        assert client.ocs_get.call_args.kwargs["params"]["threadId"] == "23"
        assert '[23] admin [thread 23 "My Thread"]: root msg' in result

    @pytest.mark.asyncio
    async def test_get_messages_without_thread_sends_no_filter(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = [PLAIN]
        assert await _call(mcp, "get_messages", token="abc123") == (
            "[26] admin: plain\n\n--- 1 messages. For older messages, call with before_message_id=26 ---"
        )
        assert "threadId" not in client.ocs_get.call_args.kwargs["params"]

    @pytest.mark.asyncio
    async def test_get_messages_handles_304_empty_body(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        """Paging past the oldest message of a thread makes Talk answer 304, which has no data."""
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = None
        assert await _call(mcp, "get_messages", token="abc123", thread_id=23, before_message_id=23) == ""

    @pytest.mark.asyncio
    async def test_get_thread_404_names_the_thread(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.side_effect = NextcloudError("Not found.", 404)
        with pytest.raises(ToolError, match="Thread 7 not found in conversation abc123"):
            await _call(mcp, "get_thread", token="abc123", thread_id=7)

    @pytest.mark.asyncio
    async def test_rename_thread_403_explains_who_may_rename(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_put.side_effect = NextcloudError("Forbidden.", 403)
        with pytest.raises(ToolError, match="Only the author of the first message of thread 23 or a moderator"):
            await _call(mcp, "rename_thread", token="abc123", thread_id=23, title="New")

    @pytest.mark.asyncio
    async def test_rename_thread_rejects_blank_title(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        with pytest.raises(ToolError, match="title must not be blank"):
            await _call(mcp, "rename_thread", token="abc123", thread_id=23, title="  ")
        client.ocs_put.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_level_sends_level_number(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_post.return_value = {
            "thread": {"id": 23, "roomToken": "abc123", "title": "My Thread"},
            "attendee": {"notificationLevel": 3},
            "first": ROOT,
            "last": None,
        }
        result = json.loads(
            await _call(mcp, "set_thread_notification_level", token="abc123", thread_id=23, level="never")
        )
        assert client.ocs_post.call_args.kwargs["data"] == {"level": 3}
        assert result["notification_level"] == "never"

    @pytest.mark.asyncio
    async def test_set_level_rejects_unknown_name(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        with pytest.raises(ToolError, match="Invalid level 'loud'"):
            await _call(mcp, "set_thread_notification_level", token="abc123", thread_id=23, level="loud")
        client.ocs_post.assert_not_called()
