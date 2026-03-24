"""Integration tests for file comment tools against a real Nextcloud instance."""

import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


async def _get_file_id(nc_mcp: McpTestHelper, path: str) -> int:
    """Upload a test file and return its file_id."""
    await nc_mcp.client.dav_put(path, b"comment test content", content_type="text/plain")
    entries = await nc_mcp.client.dav_propfind(path, depth=0)
    for e in entries:
        if "file_id" in e:
            return int(e["file_id"])
    raise RuntimeError(f"Could not get file_id for {path}")


async def _add_comment(nc_mcp: McpTestHelper, file_id: int, message: str) -> dict[str, Any]:
    """Add a comment and return parsed result."""
    result = await nc_mcp.call("add_comment", file_id=file_id, message=message)
    return json.loads(result)


class TestListComments:
    @pytest.mark.asyncio
    async def test_empty_file_has_no_comments(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-empty.txt")
        try:
            result = await nc_mcp.call("list_comments", file_id=file_id)
            data: list[Any] = json.loads(result.split("\n\n---")[0]) if "---" in result else json.loads(result)
            assert isinstance(data, list)
            assert len(data) == 0
        finally:
            await nc_mcp.client.dav_delete("comment-empty.txt")

    @pytest.mark.asyncio
    async def test_lists_added_comment(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-list.txt")
        try:
            await _add_comment(nc_mcp, file_id, "Test comment")
            result = await nc_mcp.call("list_comments", file_id=file_id)
            data = json.loads(result.split("\n\n---")[0])
            assert len(data) >= 1
            assert data[0]["message"] == "Test comment"
        finally:
            await nc_mcp.client.dav_delete("comment-list.txt")

    @pytest.mark.asyncio
    async def test_comment_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-fields.txt")
        try:
            await _add_comment(nc_mcp, file_id, "Fields check")
            result = await nc_mcp.call("list_comments", file_id=file_id)
            data = json.loads(result.split("\n\n---")[0])
            comment = data[0]
            for field in ["id", "actor_id", "message", "created"]:
                assert field in comment, f"Missing field: {field}"
        finally:
            await nc_mcp.client.dav_delete("comment-fields.txt")

    @pytest.mark.asyncio
    async def test_multiple_comments_ordered(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-order.txt")
        try:
            await _add_comment(nc_mcp, file_id, "First")
            await _add_comment(nc_mcp, file_id, "Second")
            await _add_comment(nc_mcp, file_id, "Third")
            result = await nc_mcp.call("list_comments", file_id=file_id)
            data = json.loads(result.split("\n\n---")[0])
            messages = [c["message"] for c in data]
            assert messages == ["First", "Second", "Third"]
        finally:
            await nc_mcp.client.dav_delete("comment-order.txt")

    @pytest.mark.asyncio
    async def test_limit_parameter(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-limit.txt")
        try:
            for i in range(5):
                await _add_comment(nc_mcp, file_id, f"Comment {i}")
            result = await nc_mcp.call("list_comments", file_id=file_id, limit=2)
            data = json.loads(result.split("\n\n---")[0])
            assert len(data) == 2
        finally:
            await nc_mcp.client.dav_delete("comment-limit.txt")

    @pytest.mark.asyncio
    async def test_pagination_with_offset(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-page.txt")
        try:
            for i in range(4):
                await _add_comment(nc_mcp, file_id, f"Page comment {i}")
            page1 = await nc_mcp.call("list_comments", file_id=file_id, limit=2, offset=0)
            data1 = json.loads(page1.split("\n\n---")[0])
            page2 = await nc_mcp.call("list_comments", file_id=file_id, limit=2, offset=2)
            data2 = json.loads(page2.split("\n\n---")[0])
            ids1 = {c["id"] for c in data1}
            ids2 = {c["id"] for c in data2}
            assert ids1.isdisjoint(ids2)
        finally:
            await nc_mcp.client.dav_delete("comment-page.txt")

    @pytest.mark.asyncio
    async def test_pagination_footer_present(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-footer.txt")
        try:
            await _add_comment(nc_mcp, file_id, "Footer test")
            result = await nc_mcp.call("list_comments", file_id=file_id)
            assert "offset=" in result
        finally:
            await nc_mcp.client.dav_delete("comment-footer.txt")

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("list_comments", file_id=999999999)


class TestAddComment:
    @pytest.mark.asyncio
    async def test_add_returns_comment_id(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-add.txt")
        try:
            result = await _add_comment(nc_mcp, file_id, "New comment")
            assert "id" in result
            assert result["message"] == "New comment"
        finally:
            await nc_mcp.client.dav_delete("comment-add.txt")

    @pytest.mark.asyncio
    async def test_added_comment_appears_in_list(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-verify.txt")
        try:
            await _add_comment(nc_mcp, file_id, "Verify me")
            result = await nc_mcp.call("list_comments", file_id=file_id)
            assert "Verify me" in result
        finally:
            await nc_mcp.client.dav_delete("comment-verify.txt")

    @pytest.mark.asyncio
    async def test_empty_message_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-empty-msg.txt")
        try:
            with pytest.raises((ToolError, ValueError)):
                await nc_mcp.call("add_comment", file_id=file_id, message="")
        finally:
            await nc_mcp.client.dav_delete("comment-empty-msg.txt")

    @pytest.mark.asyncio
    async def test_whitespace_only_message_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-ws.txt")
        try:
            with pytest.raises((ToolError, ValueError)):
                await nc_mcp.call("add_comment", file_id=file_id, message="   ")
        finally:
            await nc_mcp.client.dav_delete("comment-ws.txt")

    @pytest.mark.asyncio
    async def test_long_message_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-long.txt")
        try:
            with pytest.raises((ToolError, ValueError)):
                await nc_mcp.call("add_comment", file_id=file_id, message="x" * 1001)
        finally:
            await nc_mcp.client.dav_delete("comment-long.txt")

    @pytest.mark.asyncio
    async def test_add_to_nonexistent_file_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("add_comment", file_id=999999999, message="nope")

    @pytest.mark.asyncio
    async def test_comment_shows_author(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-author.txt")
        try:
            await _add_comment(nc_mcp, file_id, "Author test")
            result = await nc_mcp.call("list_comments", file_id=file_id)
            data = json.loads(result.split("\n\n---")[0])
            assert data[0]["actor_id"] == "admin"
        finally:
            await nc_mcp.client.dav_delete("comment-author.txt")


class TestEditComment:
    @pytest.mark.asyncio
    async def test_edit_own_comment(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-edit.txt")
        try:
            added = await _add_comment(nc_mcp, file_id, "Original")
            result = await nc_mcp.call("edit_comment", file_id=file_id, comment_id=int(added["id"]), message="Edited")
            data = json.loads(result)
            assert data["message"] == "Edited"
            listed = await nc_mcp.call("list_comments", file_id=file_id)
            list_data = json.loads(listed.split("\n\n---")[0])
            assert list_data[0]["message"] == "Edited"
        finally:
            await nc_mcp.client.dav_delete("comment-edit.txt")

    @pytest.mark.asyncio
    async def test_edit_empty_message_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-edit-empty.txt")
        try:
            added = await _add_comment(nc_mcp, file_id, "Will edit")
            with pytest.raises((ToolError, ValueError)):
                await nc_mcp.call("edit_comment", file_id=file_id, comment_id=int(added["id"]), message="")
        finally:
            await nc_mcp.client.dav_delete("comment-edit-empty.txt")

    @pytest.mark.asyncio
    async def test_edit_nonexistent_comment_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-edit-404.txt")
        try:
            with pytest.raises(ToolError):
                await nc_mcp.call("edit_comment", file_id=file_id, comment_id=999999, message="nope")
        finally:
            await nc_mcp.client.dav_delete("comment-edit-404.txt")


class TestDeleteComment:
    @pytest.mark.asyncio
    async def test_delete_own_comment(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-del.txt")
        try:
            added = await _add_comment(nc_mcp, file_id, "Delete me")
            result = await nc_mcp.call("delete_comment", file_id=file_id, comment_id=int(added["id"]))
            assert "deleted" in result.lower()
            listed = await nc_mcp.call("list_comments", file_id=file_id)
            data = json.loads(listed.split("\n\n---")[0]) if "---" in listed else json.loads(listed)
            assert all(c["id"] != int(added["id"]) for c in data)
        finally:
            await nc_mcp.client.dav_delete("comment-del.txt")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_comment_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-del-404.txt")
        try:
            with pytest.raises(ToolError):
                await nc_mcp.call("delete_comment", file_id=file_id, comment_id=999999)
        finally:
            await nc_mcp.client.dav_delete("comment-del-404.txt")


class TestCommentPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp: McpTestHelper, nc_mcp_read_only: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-perm-read.txt")
        try:
            result = await nc_mcp_read_only.call("list_comments", file_id=file_id)
            data = json.loads(result.split("\n\n---")[0]) if "---" in result else json.loads(result)
            assert isinstance(data, list)
        finally:
            await nc_mcp.client.dav_delete("comment-perm-read.txt")

    @pytest.mark.asyncio
    async def test_read_only_blocks_add(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("add_comment", file_id=1, message="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_edit(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("edit_comment", file_id=1, comment_id=1, message="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_delete(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("delete_comment", file_id=1, comment_id=1)

    @pytest.mark.asyncio
    async def test_write_allows_add_but_blocks_delete(self, nc_mcp: McpTestHelper, nc_mcp_write: McpTestHelper) -> None:
        file_id = await _get_file_id(nc_mcp, "comment-perm-write.txt")
        try:
            added = await _add_comment(nc_mcp_write, file_id, "write ok")
            assert added["message"] == "write ok"
            with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
                await nc_mcp_write.call("delete_comment", file_id=file_id, comment_id=1)
        finally:
            await nc_mcp.client.dav_delete("comment-perm-write.txt")
