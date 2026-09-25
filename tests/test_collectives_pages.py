"""Unit tests for reading a collective page: the file path behind it and its content."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import collectives

PAGE: dict[str, Any] = {
    "id": 42,
    "title": "Notes",
    "emoji": None,
    "timestamp": 1700000000,
    "size": 18,
    "fileName": "Notes.md",
    "filePath": "",
    "collectivePath": ".Collectives/Team",
    "lastUserId": "admin",
    "tags": [],
}


@pytest.fixture
def mcp_with_mock_client(monkeypatch: pytest.MonkeyPatch) -> tuple[FastMCP, MagicMock]:
    set_permission_level(PermissionLevel.READ)
    mock_client = MagicMock()
    mock_client.ocs_get = AsyncMock()
    mock_client.dav_get = AsyncMock(return_value=(b"# Hello", "text/markdown"))
    monkeypatch.setattr(collectives, "get_client", lambda: mock_client)
    mcp = FastMCP("test-collectives")
    collectives.register(mcp)
    return mcp, mock_client


async def _call(mcp: FastMCP, tool: str, **args: Any) -> str:
    return await mcp._tool_manager.call_tool(tool, args)


class TestPageDavPath:
    def test_top_level_page(self) -> None:
        assert collectives._page_dav_path(PAGE) == ".Collectives/Team/Notes.md"

    def test_page_inside_a_parent_page(self) -> None:
        page = {**PAGE, "filePath": "Parent", "fileName": "Child.md"}
        assert collectives._page_dav_path(page) == ".Collectives/Team/Parent/Child.md"

    def test_page_with_subpages_is_a_readme_in_its_own_folder(self) -> None:
        page = {**PAGE, "filePath": "Parent", "fileName": "Readme.md"}
        assert collectives._page_dav_path(page) == ".Collectives/Team/Parent/Readme.md"

    def test_stray_slashes_do_not_double_up(self) -> None:
        page = {**PAGE, "collectivePath": ".Collectives/Team/", "filePath": "/Parent/"}
        assert collectives._page_dav_path(page) == ".Collectives/Team/Parent/Notes.md"

    def test_missing_parts_are_skipped(self) -> None:
        assert collectives._page_dav_path({"fileName": "Readme.md"}) == "Readme.md"


class TestGetCollectivePage:
    async def test_returns_the_markdown_content(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = {"page": PAGE}
        client.dav_get.return_value = (b"# Hello\n\nbody text", "text/markdown")
        result = json.loads(await _call(mcp, "get_collective_page", collective_id=1, page_id=42))
        assert result["content"] == "# Hello\n\nbody text"
        assert result["title"] == "Notes"
        client.dav_get.assert_awaited_once_with(".Collectives/Team/Notes.md")

    async def test_empty_page_has_empty_content(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = {"page": {**PAGE, "size": 0}}
        client.dav_get.return_value = (b"", "text/markdown")
        result = json.loads(await _call(mcp, "get_collective_page", collective_id=1, page_id=42))
        assert result["content"] == ""
        assert "content_error" not in result

    async def test_unreadable_file_keeps_the_metadata(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = {"page": PAGE}
        client.dav_get.side_effect = NextcloudError("Get file '.Collectives/Team/Notes.md' failed: Not found.", 404)
        result = json.loads(await _call(mcp, "get_collective_page", collective_id=1, page_id=42))
        assert result["content"] is None
        assert "Not found." in result["content_error"]
        assert result["id"] == 42

    async def test_undecodable_bytes_do_not_fail_the_call(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = {"page": PAGE}
        client.dav_get.return_value = (b"caf\xe9", "text/markdown")
        result = json.loads(await _call(mcp, "get_collective_page", collective_id=1, page_id=42))
        assert result["content"] == "caf�"

    async def test_page_listing_does_not_read_files(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = {"pages": [PAGE]}
        await _call(mcp, "get_collective_pages", collective_id=1)
        client.dav_get.assert_not_awaited()


def _edit_client(client: MagicMock) -> None:
    """Page reads and touches answer with PAGE, renames and emoji changes with an updated copy."""
    client.ocs_get.return_value = {"page": {**PAGE, "parentId": 7}}
    client.ocs_put_json = AsyncMock(return_value={"page": {**PAGE, "title": "New"}})
    client.ocs_post_json = AsyncMock(return_value={"page": PAGE})
    client.ocs_delete = AsyncMock(return_value={})
    client.dav_put = AsyncMock()


class TestUpdatePage:
    async def test_content_title_and_emoji(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.WRITE)
        _edit_client(client)
        await _call(mcp, "update_collective_page", collective_id=1, page_id=42, content="text", title="New", emoji="")
        base = "apps/collectives/api/v1.0/collectives/1/pages/42"
        # Content goes to the file while it still has its old name, then the rename; an empty emoji clears it
        assert client.method_calls == [
            call.ocs_get(base),
            call.dav_put(".Collectives/Team/Notes.md", b"text", content_type="text/markdown; charset=utf-8"),
            call.ocs_get(f"{base}/touch"),
            call.ocs_put_json(base, json_data={"title": "New"}),
            call.ocs_put_json(f"{base}/emoji", json_data={"emoji": ""}),
        ]

    async def test_landing_page_rename_is_refused_before_writing(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.WRITE)
        _edit_client(client)
        client.ocs_get.return_value = {"page": {**PAGE, "parentId": 0}}
        with pytest.raises(ToolError, match="landing page cannot be renamed"):
            await _call(mcp, "update_collective_page", collective_id=1, page_id=42, content="text", title="New")
        client.dav_put.assert_not_awaited()
        client.ocs_put_json.assert_not_awaited()

    def test_empty_emoji_reads_as_none(self) -> None:
        assert collectives._format_page({**PAGE, "emoji": ""})["emoji"] is None

    async def test_needs_a_change(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.WRITE)
        with pytest.raises(ToolError, match="at least one of content, title or emoji"):
            await _call(mcp, "update_collective_page", collective_id=1, page_id=42)
        client.ocs_get.assert_not_awaited()

    async def test_create_with_content_writes_the_file(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.WRITE)
        _edit_client(client)
        await _call(mcp, "create_collective_page", collective_id=1, parent_id=7, title="Notes", content="# Hi")
        client.dav_put.assert_awaited_once_with(
            ".Collectives/Team/Notes.md", b"# Hi", content_type="text/markdown; charset=utf-8"
        )

    async def test_create_without_content_writes_nothing(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.WRITE)
        _edit_client(client)
        await _call(mcp, "create_collective_page", collective_id=1, parent_id=7, title="Notes")
        client.dav_put.assert_not_awaited()


class TestMovePage:
    async def test_within_the_collective(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.WRITE)
        _edit_client(client)
        await _call(mcp, "move_collective_page", collective_id=1, page_id=42, parent_id=7, copy=True)
        client.ocs_put_json.assert_awaited_once_with(
            "apps/collectives/api/v1.0/collectives/1/pages/42", json_data={"parentId": 7, "copy": True}
        )

    async def test_move_to_another_collective_keeps_the_id(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.WRITE)
        _edit_client(client)
        client.ocs_get.return_value = {"pages": [{**PAGE, "id": 5, "parentId": 0}, {**PAGE, "parentId": 5}]}
        result = json.loads(
            await _call(mcp, "move_collective_page", collective_id=1, page_id=42, parent_id=5, to_collective_id=2)
        )
        client.ocs_put_json.assert_awaited_once_with(
            "apps/collectives/api/v1.0/collectives/1/pages/42/to/2", json_data={"parentId": 5, "copy": False}
        )
        assert (result["id"], result["parent_id"]) == (42, 5)

    async def test_copy_to_another_collective_finds_the_copy(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        """The endpoint answers with nothing; the copy is the newest page with the title under the parent."""
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.WRITE)
        _edit_client(client)
        target = {
            "pages": [
                {**PAGE, "id": 5, "parentId": 0, "title": "Landing"},
                {**PAGE, "id": 60, "parentId": 5},
                {**PAGE, "id": 61, "parentId": 5},
                {**PAGE, "id": 70, "parentId": 60},
            ]
        }
        client.ocs_get.side_effect = [{"page": {**PAGE, "parentId": 3}}, target]
        result = json.loads(
            await _call(
                mcp, "move_collective_page", collective_id=1, page_id=42, parent_id=0, to_collective_id=2, copy=True
            )
        )
        assert result["id"] == 61


class TestDeleteCollective:
    @pytest.mark.parametrize(("delete_team", "suffix"), [(False, ""), (True, "?circle=1")])
    async def test_team(self, mcp_with_mock_client: tuple[FastMCP, MagicMock], delete_team: bool, suffix: str) -> None:
        mcp, client = mcp_with_mock_client
        set_permission_level(PermissionLevel.DESTRUCTIVE)
        client.ocs_delete = AsyncMock(return_value={})
        await _call(mcp, "delete_collective", collective_id=3, delete_team=delete_team)
        client.ocs_delete.assert_awaited_once_with(f"apps/collectives/api/v1.0/collectives/trash/3{suffix}")


class TestSearch:
    async def test_content_search(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = {"pages": [PAGE]}
        result = json.loads(await _call(mcp, "search_collective_pages", collective_id=1, query="zebra"))
        client.ocs_get.assert_awaited_once_with(
            "apps/collectives/api/v1.0/collectives/1/search", params={"searchString": "zebra"}
        )
        assert [p["id"] for p in result] == [42]

    async def test_recent_pages_name_their_collective(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        recent = {**PAGE, "collectivePath": "/Team-7", "collectiveNameWithEmoji": "📚 Team"}
        client.ocs_get.return_value = {"pages": [recent, {**PAGE, "collectivePath": ""}]}
        result = json.loads(await _call(mcp, "list_recent_collective_pages", query="No", limit=500))
        client.ocs_get.assert_awaited_once_with(
            "apps/collectives/api/v1.0/collectives/search/recent", params={"limit": 100, "query": "No"}
        )
        assert [(p["collective"], p["collective_id"]) for p in result] == [("📚 Team", 7), (None, None)]
