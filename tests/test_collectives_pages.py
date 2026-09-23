"""Unit tests for reading a collective page: the file path behind it and its content."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

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
