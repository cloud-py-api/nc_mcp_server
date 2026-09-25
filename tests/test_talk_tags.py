"""Unit tests for conversation tags, presets and preserving conversations."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import talk

TAGS = "apps/spreed/api/v4/tags"
ROOM = "apps/spreed/api/v4/room/tok"
TAG = {"id": "1335", "name": "Work", "sortOrder": 1, "collapsed": False, "type": "custom"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock = MagicMock()
    for method in ("ocs_get", "ocs_post", "ocs_post_json", "ocs_put_json", "ocs_delete"):
        setattr(mock, method, AsyncMock(return_value={"token": "tok", "type": 2}))
    monkeypatch.setattr(talk, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-talk-tags")
    talk.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


class TestTags:
    async def test_list(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [{"id": 9, "name": "Favorites", "sortOrder": 0, "type": "favorites"}, TAG]
        result = json.loads(await _call(mcp, "list_conversation_tags"))
        client.ocs_get.assert_awaited_once_with(TAGS)
        assert result == [
            {"id": "9", "name": "Favorites", "type": "favorites", "sort_order": 0},
            {"id": "1335", "name": "Work", "type": "custom", "sort_order": 1},
        ]

    async def test_create_rename_delete(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = TAG
        client.ocs_put_json.return_value = {**TAG, "name": "Job"}
        assert json.loads(await _call(mcp, "create_conversation_tag", name="Work"))["id"] == "1335"
        assert json.loads(await _call(mcp, "rename_conversation_tag", tag_id="1335", name="Job"))["name"] == "Job"
        assert await _call(mcp, "delete_conversation_tag", tag_id="1335") == "Tag 1335 deleted."
        assert client.method_calls == [
            call.ocs_post_json(TAGS, json_data={"name": "Work"}),
            call.ocs_put_json(f"{TAGS}/1335", json_data={"name": "Job"}),
            call.ocs_delete(f"{TAGS}/1335"),
        ]

    async def test_set_on_a_conversation(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = {"token": "tok", "tagIds": ["1335"]}
        result = json.loads(await _call(mcp, "set_conversation_tags", token="tok", tag_ids=["1335"]))
        client.ocs_post_json.assert_awaited_once_with(f"{ROOM}/tags", json_data={"tagIds": ["1335"]})
        assert result["tag_ids"] == ["1335"]

    def test_conversations_report_their_tags(self) -> None:
        assert talk._format_conversation({"token": "t"})["tag_ids"] == []
        assert talk._format_conversation({"token": "t", "tagIds": ["1", "2"]})["tag_ids"] == ["1", "2"]


PRESETS: list[dict[str, Any]] = [
    {"identifier": "default", "name": "Default", "description": "d", "parameters": {"roomType": 2, "lobbyState": 0}},
    {"identifier": "webinar", "name": "Webinar", "description": "w", "parameters": {"roomType": 3, "lobbyState": 1}},
    {"identifier": "forced", "name": "forced", "description": "f", "parameters": []},
]


def _answer(client: MagicMock, features: list[str]) -> None:
    async def get(path: str, **_: Any) -> Any:
        if path == "cloud/capabilities":
            return {"capabilities": {"spreed": {"features": features}}}
        return PRESETS

    client.ocs_get.side_effect = get


class TestPresets:
    async def test_list_leaves_out_forced(self, mcp: FastMCP, client: MagicMock) -> None:
        _answer(client, [])
        result = json.loads(await _call(mcp, "list_conversation_presets"))
        assert [p["identifier"] for p in result] == ["default", "webinar"]
        assert result[1]["settings"] == {"roomType": 3, "lobbyState": 1}

    async def test_create_sends_the_preset_settings(self, mcp: FastMCP, client: MagicMock) -> None:
        """Talk only records the preset's name, so its settings go along; the explicit room type wins."""
        _answer(client, [])
        await _call(mcp, "create_conversation", room_type=2, name="N", description="D", preset="webinar")
        client.ocs_post.assert_awaited_once_with(
            "apps/spreed/api/v4/room",
            data={"roomType": 2, "lobbyState": 1, "roomName": "N", "description": "D", "preset": "webinar"},
        )

    async def test_unknown_preset(self, mcp: FastMCP, client: MagicMock) -> None:
        _answer(client, [])
        with pytest.raises(ToolError, match=r"Unknown preset 'forced'\. Available: default, webinar"):
            await _call(mcp, "create_conversation", room_type=2, name="N", preset="forced")
        client.ocs_post.assert_not_awaited()

    async def test_create_without_them(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "create_conversation", room_type=2, name="N")
        assert client.ocs_post.await_args.kwargs["data"] == {"roomType": 2, "roomName": "N"}


class TestPreserve:
    @pytest.mark.parametrize(("preserved", "method"), [(True, "ocs_post"), (False, "ocs_delete")])
    async def test_requests(self, mcp: FastMCP, client: MagicMock, preserved: bool, method: str) -> None:
        _answer(client, ["preserve-conversation"])
        await _call(mcp, "update_conversation", token="tok", preserved=preserved)
        writes = [c for c in client.method_calls if c[0] != "ocs_get"]
        assert writes == [getattr(call, method)(f"{ROOM}/preserve")]

    async def test_released_first_and_protected_last(self, mcp: FastMCP, client: MagicMock) -> None:
        """A preserved conversation refuses public changes, so order matters."""
        _answer(client, ["preserve-conversation"])
        await _call(mcp, "update_conversation", token="tok", public=True, preserved=False)
        await _call(mcp, "update_conversation", token="tok", public=False, preserved=True)
        writes = [c for c in client.method_calls if c[0] != "ocs_get"]
        assert writes == [
            call.ocs_delete(f"{ROOM}/preserve"),
            call.ocs_post(f"{ROOM}/public"),
            call.ocs_delete(f"{ROOM}/public"),
            call.ocs_post(f"{ROOM}/preserve"),
        ]

    async def test_needs_talk_25(self, mcp: FastMCP, client: MagicMock) -> None:
        _answer(client, ["chat-v2"])
        with pytest.raises(ToolError, match=r"needs Talk 25 \(Nextcloud 35\)"):
            await _call(mcp, "update_conversation", token="tok", name="N", preserved=True)
        assert [c for c in client.method_calls if c[0] != "ocs_get"] == []

    async def test_other_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        _answer(client, ["preserve-conversation"])
        client.ocs_post.side_effect = NextcloudError("OCS POST x: Forbidden. (permissions)", 403)
        with pytest.raises(ToolError, match=r"\(permissions\)\. Failed at preserved"):
            await _call(mcp, "update_conversation", token="tok", preserved=True)

    def test_preserved_state_is_reported(self) -> None:
        assert talk._format_conversation({"token": "t", "attributes": 2})["is_preserved"] is True
        assert talk._format_conversation({"token": "t", "attributes": 5})["is_preserved"] is False
        assert talk._format_conversation({"token": "t"})["is_preserved"] is False
