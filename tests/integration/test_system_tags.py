"""Integration tests for System Tags tools against a real Nextcloud instance."""

import contextlib
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import TEST_BASE_DIR, McpTestHelper

pytestmark = pytest.mark.integration


async def _create_tag(nc_mcp: McpTestHelper, name: str) -> int:
    result = await nc_mcp.call("create_tag", name=name)
    return int(json.loads(result)["id"])


async def _get_test_file_id(nc_mcp: McpTestHelper) -> int:
    await nc_mcp.create_test_dir()
    await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/tagged.txt", "tag me")
    listing = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR, limit=200))["data"]
    for entry in listing:
        if "tagged.txt" in entry["path"]:
            return int(entry["file_id"])
    raise AssertionError("tagged.txt not found in listing")


class TestListTags:
    @pytest.mark.asyncio
    async def test_returns_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_tags", limit=200)
        data = json.loads(result)["data"]
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_created_tag_appears_in_list(self, nc_mcp: McpTestHelper) -> None:
        tag_id = await _create_tag(nc_mcp, "mcp-test-visible")
        try:
            result = await nc_mcp.call("list_tags", limit=200)
            tags = json.loads(result)["data"]
            ids = [t["id"] for t in tags]
            assert tag_id in ids
            tag = next(t for t in tags if t["id"] == tag_id)
            assert tag["name"] == "mcp-test-visible"
            assert tag["user_visible"] is True
            assert tag["user_assignable"] is True
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)

    @pytest.mark.asyncio
    async def test_tag_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        tag_id = await _create_tag(nc_mcp, "mcp-test-fields")
        try:
            result = await nc_mcp.call("list_tags", limit=200)
            tags = json.loads(result)["data"]
            tag = next(t for t in tags if t["id"] == tag_id)
            for field in ["id", "name", "user_visible", "user_assignable"]:
                assert field in tag, f"Missing field: {field}"
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)


class TestCreateTag:
    @pytest.mark.asyncio
    async def test_create_tag(self, nc_mcp: McpTestHelper) -> None:
        tag_id = await _create_tag(nc_mcp, "mcp-test-create")
        try:
            assert tag_id > 0
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)

    @pytest.mark.asyncio
    async def test_create_returns_int_id(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_tag", name="mcp-test-intid")
        data = json.loads(result)
        tag_id = data["id"]
        try:
            assert isinstance(tag_id, int)
            assert tag_id > 0
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)

    @pytest.mark.asyncio
    async def test_create_duplicate_name_raises(self, nc_mcp: McpTestHelper) -> None:
        tag_id = await _create_tag(nc_mcp, "mcp-test-dup")
        try:
            with pytest.raises(ToolError):
                await nc_mcp.call("create_tag", name="mcp-test-dup")
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)

    @pytest.mark.asyncio
    async def test_create_non_assignable_tag(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_tag", name="mcp-test-noa", user_assignable=False)
        tag_id = int(json.loads(result)["id"])
        try:
            tags = json.loads(await nc_mcp.call("list_tags", limit=200))["data"]
            tag = next(t for t in tags if t["id"] == tag_id)
            assert tag["user_assignable"] is False
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)


class TestGetFileTags:
    @pytest.mark.asyncio
    async def test_file_with_no_tags(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_test_file_id(nc_mcp)
        result = await nc_mcp.call("get_file_tags", file_id=file_id)
        tags: list[dict[str, object]] = json.loads(result)
        assert isinstance(tags, list)
        assert len(tags) == 0

    @pytest.mark.asyncio
    async def test_file_with_assigned_tags(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_test_file_id(nc_mcp)
        tag_id = await _create_tag(nc_mcp, "mcp-test-assigned")
        try:
            await nc_mcp.call("assign_tag", file_id=file_id, tag_id=tag_id)
            result = await nc_mcp.call("get_file_tags", file_id=file_id)
            tags = json.loads(result)
            assert len(tags) == 1
            assert tags[0]["id"] == tag_id
            assert tags[0]["name"] == "mcp-test-assigned"
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("unassign_tag", file_id=file_id, tag_id=tag_id)
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)


class TestAssignTag:
    @pytest.mark.asyncio
    async def test_assign_and_verify(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_test_file_id(nc_mcp)
        tag_id = await _create_tag(nc_mcp, "mcp-test-assign")
        try:
            result = await nc_mcp.call("assign_tag", file_id=file_id, tag_id=tag_id)
            assert "assigned" in result.lower()
            tags = json.loads(await nc_mcp.call("get_file_tags", file_id=file_id))
            assert any(t["id"] == tag_id for t in tags)
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("unassign_tag", file_id=file_id, tag_id=tag_id)
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)

    @pytest.mark.asyncio
    async def test_assign_idempotent(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_test_file_id(nc_mcp)
        tag_id = await _create_tag(nc_mcp, "mcp-test-idem")
        try:
            await nc_mcp.call("assign_tag", file_id=file_id, tag_id=tag_id)
            await nc_mcp.call("assign_tag", file_id=file_id, tag_id=tag_id)
            tags = json.loads(await nc_mcp.call("get_file_tags", file_id=file_id))
            count = sum(1 for t in tags if t["id"] == tag_id)
            assert count == 1
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("unassign_tag", file_id=file_id, tag_id=tag_id)
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)

    @pytest.mark.asyncio
    async def test_assign_multiple_tags(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_test_file_id(nc_mcp)
        tag1 = await _create_tag(nc_mcp, "mcp-test-multi1")
        tag2 = await _create_tag(nc_mcp, "mcp-test-multi2")
        try:
            await nc_mcp.call("assign_tag", file_id=file_id, tag_id=tag1)
            await nc_mcp.call("assign_tag", file_id=file_id, tag_id=tag2)
            tags = json.loads(await nc_mcp.call("get_file_tags", file_id=file_id))
            ids = {t["id"] for t in tags}
            assert tag1 in ids
            assert tag2 in ids
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("unassign_tag", file_id=file_id, tag_id=tag1)
            with contextlib.suppress(Exception):
                await nc_mcp.call("unassign_tag", file_id=file_id, tag_id=tag2)
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag1)
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag2)


class TestUnassignTag:
    @pytest.mark.asyncio
    async def test_unassign_tag(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_test_file_id(nc_mcp)
        tag_id = await _create_tag(nc_mcp, "mcp-test-unassign")
        try:
            await nc_mcp.call("assign_tag", file_id=file_id, tag_id=tag_id)
            result = await nc_mcp.call("unassign_tag", file_id=file_id, tag_id=tag_id)
            assert "removed" in result.lower()
            tags = json.loads(await nc_mcp.call("get_file_tags", file_id=file_id))
            assert not any(t["id"] == tag_id for t in tags)
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_tag", tag_id=tag_id)

    @pytest.mark.asyncio
    async def test_unassign_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_test_file_id(nc_mcp)
        with pytest.raises(ToolError):
            await nc_mcp.call("unassign_tag", file_id=file_id, tag_id=999999)


class TestDeleteTag:
    @pytest.mark.asyncio
    async def test_delete_tag(self, nc_mcp: McpTestHelper) -> None:
        tag_id = await _create_tag(nc_mcp, "mcp-test-delete")
        result = await nc_mcp.call("delete_tag", tag_id=tag_id)
        assert "deleted" in result.lower()
        tags = json.loads(await nc_mcp.call("list_tags", limit=200))["data"]
        assert not any(t["id"] == tag_id for t in tags)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("delete_tag", tag_id=999999)

    @pytest.mark.asyncio
    async def test_delete_removes_from_files(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _get_test_file_id(nc_mcp)
        tag_id = await _create_tag(nc_mcp, "mcp-test-cascade")
        await nc_mcp.call("assign_tag", file_id=file_id, tag_id=tag_id)
        await nc_mcp.call("delete_tag", tag_id=tag_id)
        tags = json.loads(await nc_mcp.call("get_file_tags", file_id=file_id))
        assert not any(t["id"] == tag_id for t in tags)


class TestSystemTagPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_tags", limit=200)
        assert isinstance(json.loads(result)["data"], list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("create_tag", name="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_assign(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("assign_tag", file_id=1, tag_id=1)

    @pytest.mark.asyncio
    async def test_write_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_tag", tag_id=1)

    @pytest.mark.asyncio
    async def test_write_blocks_unassign(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("unassign_tag", file_id=1, tag_id=1)
