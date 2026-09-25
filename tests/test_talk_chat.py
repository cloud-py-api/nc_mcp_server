"""Unit tests for message text rendering and the Talk chat action tools."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import talk

CHAT = "apps/spreed/api/v1/chat/tok"


def _msg(msg_id: int, text: str = "hi", **extra: Any) -> dict[str, Any]:
    return {"id": msg_id, "actorDisplayName": "Ann", "message": text, "messageParameters": [], **extra}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock = MagicMock()
    for method in ("ocs_get", "ocs_post_json", "ocs_put_json", "ocs_delete"):
        setattr(mock, method, AsyncMock())
    monkeypatch.setattr(talk, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-talk-chat")
    talk.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


class TestMessageText:
    def test_mentions_become_at_names(self) -> None:
        msg = _msg(
            1,
            "hi {mention-user1} and {mention-call1}",
            messageParameters={
                "mention-user1": {"type": "user", "id": "ann", "name": "Ann Lee"},
                "mention-call1": {"type": "call", "id": "tok", "name": "Team room"},
            },
        )
        assert talk._message_text(msg) == "hi @Ann Lee and @all"

    def test_objects_become_their_names(self) -> None:
        msg = _msg(1, "{file}", messageParameters={"file": {"type": "file", "id": "12", "name": "report.pdf"}})
        assert talk._message_text(msg) == "report.pdf"

    def test_system_message_actor(self) -> None:
        msg = _msg(1, "{actor} created the conversation", messageParameters={"actor": {"type": "user", "name": "Bo"}})
        assert talk._message_text(msg) == "Bo created the conversation"

    @pytest.mark.parametrize(
        "msg",
        [
            _msg(1, "code {x} stays"),
            _msg(1, "code {x} stays", messageParameters={"other": {"name": "n"}}),
            _msg(1, "code {x} stays", messageParameters={"x": "not an object"}),
        ],
    )
    def test_unmatched_placeholders_stay(self, msg: dict[str, Any]) -> None:
        assert talk._message_text(msg) == "code {x} stays"

    def test_whole_conversation_mention_is_all(self) -> None:
        msg = _msg(1, "{mention-call1} look", messageParameters={"mention-call1": {"type": "call", "name": "Team"}})
        assert talk._message_text(msg) == "@all look"

    def test_captioned_file_share_keeps_the_file_name(self) -> None:
        msg = _msg(1, "see this", messageParameters={"file": {"type": "file", "name": "report.pdf"}})
        assert talk._message_text(msg) == "see this [report.pdf]"

    def test_name_falls_back_to_id(self) -> None:
        msg = _msg(1, "{mention-user1}", messageParameters={"mention-user1": {"type": "user", "id": "ann"}})
        assert talk._message_text(msg) == "@ann"

    def test_compact_and_full_formats_use_it(self) -> None:
        msg = _msg(5, "{file}", messageParameters={"file": {"name": "a.txt"}})
        assert talk._format_message_compact(msg) == "[5] Ann: a.txt"
        assert talk._format_message_full(msg)["message"] == "a.txt"


class TestGetMessageContext:
    async def test_reads_both_sides_without_touching_read_state(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = [[_msg(6, "m6"), _msg(5, "m5"), _msg(4, "m4")], [_msg(7, "m7"), _msg(8, "m8")]]
        result = await _call(mcp, "get_message_context", token="tok", message_id=6, limit=2)
        common = {"lastKnownMessageId": "6", "setReadMarker": "0", "markNotificationsAsRead": "0"}
        assert [c.args for c in client.ocs_get.await_args_list] == [(CHAT,), (CHAT,)]
        assert [c.kwargs["params"] for c in client.ocs_get.await_args_list] == [
            {**common, "lookIntoFuture": "0", "includeLastKnown": "1", "limit": "3"},
            {**common, "lookIntoFuture": "1", "timeout": "0", "limit": "2"},
        ]
        assert result.splitlines() == ["[4] Ann: m4", "[5] Ann: m5", ">> [6] Ann: m6", "[7] Ann: m7", "[8] Ann: m8"]

    async def test_nothing_newer(self, mcp: FastMCP, client: MagicMock) -> None:
        """Talk answers 304 with no body, which the client returns as None."""
        client.ocs_get.side_effect = [[_msg(6)], None]
        assert await _call(mcp, "get_message_context", token="tok", message_id=6) == ">> [6] Ann: hi"

    async def test_system_messages_are_hidden_unless_asked(self, mcp: FastMCP, client: MagicMock) -> None:
        older = [_msg(5, "joined", systemMessage="user_added"), _msg(6)]
        client.ocs_get.side_effect = [older, None, older, None]
        assert await _call(mcp, "get_message_context", token="tok", message_id=6) == ">> [6] Ann: hi"
        shown = await _call(mcp, "get_message_context", token="tok", message_id=6, include_system=True)
        assert shown.splitlines() == ["[5] Ann: joined", ">> [6] Ann: hi"]

    async def test_thread_filter(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = [[_msg(6)], None]
        await _call(mcp, "get_message_context", token="tok", message_id=6, thread_id=3)
        assert all(c.kwargs["params"]["threadId"] == "3" for c in client.ocs_get.await_args_list)

    async def test_missing_message_is_said(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = [[_msg(4)], [_msg(9)]]
        result = await _call(mcp, "get_message_context", token="tok", message_id=6, limit=500)
        assert client.ocs_get.await_args_list[0].kwargs["params"]["limit"] == "101"
        assert result.splitlines()[0].startswith("(Message 6 is not in this conversation or thread;")

    async def test_nothing_at_all(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = [[], None]
        assert await _call(mcp, "get_message_context", token="tok", message_id=6) == "No messages around message 6."


REACTIONS = {"👍": [{"actorType": "users", "actorId": "ann", "actorDisplayName": "Ann"}, {"actorId": "bo"}]}


class TestReactions:
    async def test_get_formats_names(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = REACTIONS
        result = json.loads(await _call(mcp, "get_reactions", token="tok", message_id=3))
        client.ocs_get.assert_awaited_once_with("apps/spreed/api/v1/reaction/tok/3", params=None)
        assert result == {"👍": ["Ann", "bo"]}

    async def test_none_is_an_empty_object(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = []
        assert json.loads(await _call(mcp, "get_reactions", token="tok", message_id=3, reaction="🎉")) == {}
        assert client.ocs_get.await_args.kwargs["params"] == {"reaction": "🎉"}

    async def test_add(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = REACTIONS
        await _call(mcp, "add_reaction", token="tok", message_id=3, reaction="👍")
        client.ocs_post_json.assert_awaited_once_with("apps/spreed/api/v1/reaction/tok/3", json_data={"reaction": "👍"})

    async def test_remove_encodes_the_emoji(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.return_value = []
        assert json.loads(await _call(mcp, "remove_reaction", token="tok", message_id=3, reaction="👍")) == {}
        client.ocs_delete.assert_awaited_once_with("apps/spreed/api/v1/reaction/tok/3?reaction=%F0%9F%91%8D")


class TestEditMessage:
    async def test_returns_the_edited_message(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.return_value = {"id": 9, "systemMessage": "message_edited", "parent": _msg(3, "new")}
        result = json.loads(await _call(mcp, "edit_message", token="tok", message_id=3, message="new"))
        client.ocs_put_json.assert_awaited_once_with(f"{CHAT}/3", json_data={"message": "new"})
        assert (result["id"], result["message"]) == (3, "new")

    @pytest.mark.parametrize(
        ("error", "status", "reason"),
        [
            ("HTTP 400 (age)", 400, "only lets messages be edited for 24 hours"),
            ("Forbidden. (permission)", 403, "only your own messages"),
            ("Forbidden.", 403, "may be read-only"),
            ("HTTP 400 (message)", 400, "empty or invalid"),
            ("Precondition failed", 412, "lobby is active"),
            ("HTTP 405 (message)", 405, "shared objects other than files"),
            ("HTTP 413 (message)", 413, "too long"),
        ],
    )
    async def test_refusals_are_explained(
        self, mcp: FastMCP, client: MagicMock, error: str, status: int, reason: str
    ) -> None:
        client.ocs_put_json.side_effect = NextcloudError(f"OCS PUT x: {error}", status)
        with pytest.raises(ToolError, match=reason):
            await _call(mcp, "edit_message", token="tok", message_id=3, message="new")

    async def test_other_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = NextcloudError("OCS PUT x: Not found.", 404)
        with pytest.raises(ToolError, match=r"Not found\.$"):
            await _call(mcp, "edit_message", token="tok", message_id=3, message="new")


ROOM = {"token": "tok", "lastReadMessage": 8, "unreadMessages": 2, "unreadMention": True}


class TestReadState:
    async def test_mark_read_up_to_a_message(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = ROOM
        result = json.loads(await _call(mcp, "mark_conversation_read", token="tok", message_id=8))
        client.ocs_post_json.assert_awaited_once_with(f"{CHAT}/read", json_data={"lastReadMessage": 8})
        assert result == {"token": "tok", "last_read_message": 8, "unread_messages": 2, "unread_mention": True}

    async def test_mark_everything_read(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = ROOM
        await _call(mcp, "mark_conversation_read", token="tok")
        assert client.ocs_post_json.await_args.kwargs["json_data"] == {}

    async def test_mark_unread(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.return_value = ROOM
        await _call(mcp, "mark_conversation_unread", token="tok")
        client.ocs_delete.assert_awaited_once_with(f"{CHAT}/read")


class TestSharedItems:
    async def test_overview_skips_empty_types_and_keeps_talks_order(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"file": [_msg(9, "b"), _msg(2, "a")], "pinned": [_msg(3), _msg(8)], "poll": []}
        result = json.loads(await _call(mcp, "list_shared_items", token="tok", limit=50))
        client.ocs_get.assert_awaited_once_with(f"{CHAT}/share/overview", params={"limit": 20})
        assert result == {"file": ["[9] Ann: b", "[2] Ann: a"], "pinned": ["[3] Ann: hi", "[8] Ann: hi"]}

    async def test_one_type(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"4": _msg(4, "x.png")}
        result = json.loads(await _call(mcp, "list_shared_items", token="tok", item_type="media"))
        client.ocs_get.assert_awaited_once_with(f"{CHAT}/share", params={"objectType": "media", "limit": 20})
        assert result == {"media": ["[4] Ann: x.png"]}

    async def test_one_type_without_items(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = []
        assert json.loads(await _call(mcp, "list_shared_items", token="tok", item_type="voice")) == {}


class TestSearchMentions:
    async def test_builds_the_mention_text(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [
            {"id": "jane doe", "label": "Jane", "source": "users", "mentionId": "jane doe"},
            {"id": "all", "label": "Team", "source": "calls", "mentionId": "all"},
            {"id": "g1", "label": "Sales", "source": "groups", "mentionId": "group/g1"},
        ]
        result = json.loads(await _call(mcp, "search_mentions", token="tok", search=""))
        client.ocs_get.assert_awaited_once_with(f"{CHAT}/mentions", params={"search": "", "limit": 20})
        assert [m["mention"] for m in result] == ['@"jane doe"', '@"all"', '@"group/g1"']

    async def test_limit_is_kept_although_talk_adds_all(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [{"id": "jane", "mentionId": "jane"}, {"id": "all", "mentionId": "all"}]
        result = json.loads(await _call(mcp, "search_mentions", token="tok", search="j", limit=0))
        assert client.ocs_get.await_args.kwargs["params"]["limit"] == 1
        assert [m["id"] for m in result] == ["jane"]
