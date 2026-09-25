"""Unit tests for Collectives tags, attachments and public shares."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import collectives

COLL = "apps/collectives/api/v1.0/collectives/1"
TAG = {"id": 3, "collectiveId": 1, "name": "urgent", "color": "FF0000"}
SHARE = {"id": 9, "collectiveId": 1, "pageId": 0, "token": "abc", "owner": "admin", "editable": False}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock = MagicMock()
    for method in ("ocs_get", "ocs_post_json", "ocs_put_json", "ocs_delete"):
        setattr(mock, method, AsyncMock(return_value={}))
    monkeypatch.setattr(collectives, "get_client", lambda: mock)
    config = Config(nextcloud_url="https://cloud.example.com/", user="admin", password="x")
    monkeypatch.setattr(collectives, "get_config", lambda: config)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-collectives-sharing")
    collectives.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


def _page(tags: list[int]) -> dict[str, Any]:
    return {"page": {"id": 42, "title": "P", "tags": tags}}


class TestTags:
    @pytest.mark.parametrize(("given", "sent"), [("ff8800", "FF8800"), ("#00aa00", "00AA00")])
    async def test_create_normalises_the_color(self, mcp: FastMCP, client: MagicMock, given: str, sent: str) -> None:
        client.ocs_post_json.return_value = {"tag": {**TAG, "color": sent}}
        await _call(mcp, "create_collective_tag", collective_id=1, name="urgent", color=given)
        client.ocs_post_json.assert_awaited_once_with(f"{COLL}/tags", json_data={"name": "urgent", "color": sent})

    @pytest.mark.parametrize("color", ["red", "#12345", "1234567", "GGGGGG"])
    async def test_invalid_color(self, mcp: FastMCP, client: MagicMock, color: str) -> None:
        with pytest.raises(ToolError, match="Invalid color"):
            await _call(mcp, "create_collective_tag", collective_id=1, name="x", color=color)
        client.ocs_post_json.assert_not_awaited()

    async def test_update_keeps_what_is_not_passed(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"tags": [TAG]}
        client.ocs_put_json.return_value = {"tag": {**TAG, "name": "later"}}
        await _call(mcp, "update_collective_tag", collective_id=1, tag_id=3, name="later")
        client.ocs_put_json.assert_awaited_once_with(f"{COLL}/tags/3", json_data={"name": "later", "color": "FF0000"})

    async def test_delete_takes_the_tag_off_its_pages_first(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"pages": [{"id": 5, "tags": [3, 4]}, {"id": 6, "tags": [4]}, {"id": 7}]}
        await _call(mcp, "delete_collective_tag", collective_id=1, tag_id=3)
        assert client.ocs_delete.await_args_list == [call(f"{COLL}/pages/5/tags/3"), call(f"{COLL}/tags/3")]


def _tags_and_page(client: MagicMock, tag_ids: list[int], page_tags: list[int]) -> None:
    async def get(path: str, **_: Any) -> Any:
        if path.endswith("/tags"):
            return {"tags": [{**TAG, "id": t} for t in tag_ids]}
        return _page(page_tags)

    client.ocs_get.side_effect = get


class TestPageTags:
    async def test_adds_and_removes_the_difference(self, mcp: FastMCP, client: MagicMock) -> None:
        _tags_and_page(client, [1, 2, 3], [1, 2])
        client.ocs_put_json.return_value = _page([1, 2, 3])
        client.ocs_delete.return_value = _page([2, 3])
        result = json.loads(
            await _call(mcp, "set_collective_page_tags", collective_id=1, page_id=42, tag_ids=[2, 3, 3])
        )
        assert client.ocs_put_json.await_args_list == [call(f"{COLL}/pages/42/tags/3", json_data={})]
        assert client.ocs_delete.await_args_list == [call(f"{COLL}/pages/42/tags/1")]
        assert result["tags"] == [2, 3]

    async def test_unchanged_sends_nothing(self, mcp: FastMCP, client: MagicMock) -> None:
        _tags_and_page(client, [1], [1])
        await _call(mcp, "set_collective_page_tags", collective_id=1, page_id=42, tag_ids=[1])
        client.ocs_put_json.assert_not_awaited()
        client.ocs_delete.assert_not_awaited()

    async def test_unknown_tag_is_refused_before_any_change(self, mcp: FastMCP, client: MagicMock) -> None:
        _tags_and_page(client, [1, 2], [])
        with pytest.raises(ToolError, match="No tag with ID 9 in collective 1"):
            await _call(mcp, "set_collective_page_tags", collective_id=1, page_id=42, tag_ids=[1, 9])
        client.ocs_put_json.assert_not_awaited()

    async def test_a_deleted_tags_lingering_id_is_not_removed(self, mcp: FastMCP, client: MagicMock) -> None:
        """Collectives refuses to remove an ID that no longer exists; it drops it on the next change itself."""
        _tags_and_page(client, [8], [7, 8])
        client.ocs_delete.return_value = _page([])
        await _call(mcp, "set_collective_page_tags", collective_id=1, page_id=42, tag_ids=[])
        assert client.ocs_delete.await_args_list == [call(f"{COLL}/pages/42/tags/8")]


class TestTagNames:
    @pytest.mark.parametrize("name", ["", "   ", "x" * 251])
    async def test_bad_names(self, mcp: FastMCP, client: MagicMock, name: str) -> None:
        with pytest.raises(ToolError, match="1 to 250 characters"):
            await _call(mcp, "create_collective_tag", collective_id=1, name=name)
        client.ocs_post_json.assert_not_awaited()

    async def test_rename_onto_another_tags_name(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"tags": [TAG, {**TAG, "id": 4, "name": "later"}]}
        with pytest.raises(ToolError, match="already has a tag named 'later'"):
            await _call(mcp, "update_collective_tag", collective_id=1, tag_id=3, name="later")
        client.ocs_put_json.assert_not_awaited()


class TestAttachments:
    async def test_path_is_from_the_user_root(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {
            "attachments": [
                {
                    "id": 7,
                    "name": "pic.png",
                    "filesize": 58,
                    "mimetype": "image/png",
                    "timestamp": 1,
                    "path": "/.Collectives/Team/.attachments.42/pic.png",
                    "internalPath": ".attachments.42/pic.png",
                    "type": "text",
                }
            ]
        }
        result = json.loads(await _call(mcp, "list_collective_page_attachments", collective_id=1, page_id=42))
        assert result == [
            {
                "id": 7,
                "name": "pic.png",
                "mimetype": "image/png",
                "size": 58,
                "timestamp": 1,
                "type": "text",
                "path": ".Collectives/Team/.attachments.42/pic.png",
            }
        ]


class TestShares:
    async def test_list_builds_the_link(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [SHARE, {**SHARE, "token": "def", "pageId": 42, "hasPassword": True}]
        result = json.loads(await _call(mcp, "list_collective_shares", collective_id=1))
        assert result[0]["url"] == "https://cloud.example.com/index.php/apps/collectives/p/abc"
        assert (result[0]["page_id"], result[1]["page_id"], result[1]["has_password"]) == (None, 42, True)

    async def test_share_a_page_editable_with_password(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = {**SHARE, "pageId": 42}
        client.ocs_put_json.return_value = {**SHARE, "pageId": 42, "editable": True}
        result = json.loads(
            await _call(mcp, "share_collective", collective_id=1, page_id=42, editable=True, password="pw")
        )
        client.ocs_post_json.assert_awaited_once_with(f"{COLL}/pages/42/shares", json_data={"password": "pw"})
        client.ocs_put_json.assert_awaited_once_with(f"{COLL}/pages/42/shares/abc", json_data={"editable": True})
        assert result["editable"] is True

    async def test_share_the_collective(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = SHARE
        await _call(mcp, "share_collective", collective_id=1)
        client.ocs_post_json.assert_awaited_once_with(f"{COLL}/shares", json_data={})
        client.ocs_put_json.assert_not_awaited()

    @pytest.mark.parametrize(
        ("password", "body"), [(None, {"editable": True}), ("", {"editable": True, "password": ""})]
    )
    async def test_update(self, mcp: FastMCP, client: MagicMock, password: str | None, body: dict[str, Any]) -> None:
        client.ocs_put_json.return_value = SHARE
        args: dict[str, Any] = {"collective_id": 1, "token": "abc", "editable": True, "page_id": 0}
        if password is not None:
            args["password"] = password
        await _call(mcp, "update_collective_share", **args)
        client.ocs_put_json.assert_awaited_once_with(f"{COLL}/shares/abc", json_data=body)

    async def test_page_is_looked_up_from_the_token(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [SHARE, {**SHARE, "token": "def", "pageId": 42}]
        client.ocs_put_json.return_value = SHARE
        await _call(mcp, "update_collective_share", collective_id=1, token="def", editable=False)
        await _call(mcp, "delete_collective_share", collective_id=1, token="def")
        client.ocs_put_json.assert_awaited_once_with(f"{COLL}/pages/42/shares/def", json_data={"editable": False})
        client.ocs_delete.assert_awaited_once_with(f"{COLL}/pages/42/shares/def")

    async def test_unknown_token(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [SHARE]
        with pytest.raises(ToolError, match="has the token nope"):
            await _call(mcp, "delete_collective_share", collective_id=1, token="nope")
        client.ocs_delete.assert_not_awaited()

    async def test_failed_editable_says_the_link_exists(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.return_value = SHARE
        client.ocs_put_json.side_effect = NextcloudError("OCS PUT x: Forbidden.", 403)
        with pytest.raises(ToolError, match="The link abc was created read-only"):
            await _call(mcp, "share_collective", collective_id=1, editable=True)

    async def test_delete_page_share(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "delete_collective_share", collective_id=1, token="abc", page_id=42)
        client.ocs_delete.assert_awaited_once_with(f"{COLL}/pages/42/shares/abc")
