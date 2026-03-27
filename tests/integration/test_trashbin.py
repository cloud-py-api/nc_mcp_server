"""Integration tests for Files Trashbin tools against a real Nextcloud instance."""

import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

TRASH_PREFIX = "mcp-trash-test"


async def _trash_file(nc_mcp: McpTestHelper, name: str, content: str = "trash me") -> None:
    """Create a file and immediately delete it so it lands in the trash."""
    path = f"{TRASH_PREFIX}-{name}.txt"
    await nc_mcp.client.dav_put(path, content.encode(), content_type="text/plain")
    await nc_mcp.client.dav_delete(path)


async def _find_in_trash(nc_mcp: McpTestHelper, name_contains: str) -> dict[str, Any] | None:
    """Find a trashed item whose original_name contains the given substring."""
    result = await nc_mcp.call("list_trash")
    items = json.loads(result)
    for item in items:
        if name_contains in str(item.get("original_name", "")):
            return item
    return None


class TestListTrash:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_trash")
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_empty_trash_returns_empty_list(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        result = await nc_mcp.call("list_trash")
        assert json.loads(result) == []

    @pytest.mark.asyncio
    async def test_deleted_file_appears_in_trash(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        await _trash_file(nc_mcp, "appears")
        result = await nc_mcp.call("list_trash")
        items = json.loads(result)
        names = [i.get("original_name") for i in items]
        assert f"{TRASH_PREFIX}-appears.txt" in names

    @pytest.mark.asyncio
    async def test_trash_item_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        await _trash_file(nc_mcp, "fields")
        result = await nc_mcp.call("list_trash")
        items = json.loads(result)
        assert len(items) >= 1
        item = items[0]
        assert "trash_path" in item
        assert "original_name" in item
        assert "original_location" in item
        assert "deletion_time" in item
        assert "is_directory" in item

    @pytest.mark.asyncio
    async def test_trash_path_contains_timestamp(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        await _trash_file(nc_mcp, "timestamp")
        result = await nc_mcp.call("list_trash")
        items = json.loads(result)
        assert len(items) >= 1
        assert ".d" in items[0]["trash_path"]

    @pytest.mark.asyncio
    async def test_deletion_time_is_int(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        await _trash_file(nc_mcp, "deltime")
        result = await nc_mcp.call("list_trash")
        items = json.loads(result)
        assert isinstance(items[0]["deletion_time"], int)
        assert items[0]["deletion_time"] > 0

    @pytest.mark.asyncio
    async def test_size_is_int(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        await _trash_file(nc_mcp, "size", content="hello world")
        result = await nc_mcp.call("list_trash")
        items = json.loads(result)
        assert isinstance(items[0]["size"], int)
        assert items[0]["size"] == 11

    @pytest.mark.asyncio
    async def test_directory_in_trash(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        dir_name = f"{TRASH_PREFIX}-dir"
        await nc_mcp.client.dav_mkcol(dir_name)
        await nc_mcp.client.dav_put(f"{dir_name}/child.txt", b"inside", content_type="text/plain")
        await nc_mcp.client.dav_delete(dir_name)
        result = await nc_mcp.call("list_trash")
        items = json.loads(result)
        dirs = [i for i in items if i.get("original_name") == dir_name]
        assert len(dirs) == 1
        assert dirs[0]["is_directory"] is True

    @pytest.mark.asyncio
    async def test_multiple_deletions_all_appear(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        await _trash_file(nc_mcp, "multi1")
        await _trash_file(nc_mcp, "multi2")
        await _trash_file(nc_mcp, "multi3")
        result = await nc_mcp.call("list_trash")
        items = json.loads(result)
        names = [i.get("original_name") for i in items]
        assert f"{TRASH_PREFIX}-multi1.txt" in names
        assert f"{TRASH_PREFIX}-multi2.txt" in names
        assert f"{TRASH_PREFIX}-multi3.txt" in names

    @pytest.mark.asyncio
    async def test_original_location_set(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        await _trash_file(nc_mcp, "location")
        result = await nc_mcp.call("list_trash")
        items = json.loads(result)
        assert items[0]["original_location"] == f"{TRASH_PREFIX}-location.txt"


class TestRestoreTrashItem:
    @pytest.mark.asyncio
    async def test_restore_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        filename = f"{TRASH_PREFIX}-restore.txt"
        await nc_mcp.client.dav_put(filename, b"restore me", content_type="text/plain")
        await nc_mcp.client.dav_delete(filename)
        item = await _find_in_trash(nc_mcp, "restore")
        assert item is not None
        result = await nc_mcp.call("restore_trash_item", trash_path=item["trash_path"])
        assert "Restored" in result
        content, _ = await nc_mcp.client.dav_get(filename)
        assert content == b"restore me"
        await nc_mcp.client.dav_delete(filename)

    @pytest.mark.asyncio
    async def test_restore_removes_from_trash(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        await _trash_file(nc_mcp, "vanish")
        item = await _find_in_trash(nc_mcp, "vanish")
        assert item is not None
        await nc_mcp.call("restore_trash_item", trash_path=item["trash_path"])
        after = await _find_in_trash(nc_mcp, "vanish")
        assert after is None
        await nc_mcp.client.dav_delete(f"{TRASH_PREFIX}-vanish.txt")

    @pytest.mark.asyncio
    async def test_restore_directory(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        dir_name = f"{TRASH_PREFIX}-restoredir"
        await nc_mcp.client.dav_mkcol(dir_name)
        await nc_mcp.client.dav_put(f"{dir_name}/nested.txt", b"nested", content_type="text/plain")
        await nc_mcp.client.dav_delete(dir_name)
        item = await _find_in_trash(nc_mcp, "restoredir")
        assert item is not None
        await nc_mcp.call("restore_trash_item", trash_path=item["trash_path"])
        entries = await nc_mcp.client.dav_propfind(dir_name, depth=1)
        child_names = [e["path"] for e in entries if e["path"] != dir_name]
        assert any("nested.txt" in n for n in child_names)
        await nc_mcp.client.dav_delete(dir_name)

    @pytest.mark.asyncio
    async def test_restore_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("restore_trash_item", trash_path="nonexistent.d9999999999")

    @pytest.mark.asyncio
    async def test_restore_preserves_content(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        filename = f"{TRASH_PREFIX}-content.txt"
        original_content = "Specific content to verify: 42 \u00e9\u00e8\u00ea"
        await nc_mcp.client.dav_put(filename, original_content.encode("utf-8"), content_type="text/plain")
        await nc_mcp.client.dav_delete(filename)
        item = await _find_in_trash(nc_mcp, "content")
        assert item is not None
        await nc_mcp.call("restore_trash_item", trash_path=item["trash_path"])
        content, _ = await nc_mcp.client.dav_get(filename)
        assert content.decode("utf-8") == original_content
        await nc_mcp.client.dav_delete(filename)


class TestEmptyTrash:
    @pytest.mark.asyncio
    async def test_empty_trash_clears_all(self, nc_mcp: McpTestHelper) -> None:
        await _trash_file(nc_mcp, "empty1")
        await _trash_file(nc_mcp, "empty2")
        result = await nc_mcp.call("empty_trash")
        assert "emptied" in result.lower()
        items = json.loads(await nc_mcp.call("list_trash"))
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_empty_already_empty_trash(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("empty_trash")
        result = await nc_mcp.call("empty_trash")
        assert "emptied" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_trash_is_permanent(self, nc_mcp: McpTestHelper) -> None:
        await _trash_file(nc_mcp, "permanent")
        await nc_mcp.call("empty_trash")
        items = json.loads(await nc_mcp.call("list_trash"))
        names = [i.get("original_name", "") for i in items]
        assert f"{TRASH_PREFIX}-permanent.txt" not in names


class TestTrashbinPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_trash")
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_restore(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("restore_trash_item", trash_path="x.d1")

    @pytest.mark.asyncio
    async def test_read_only_blocks_empty(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("empty_trash")

    @pytest.mark.asyncio
    async def test_write_allows_restore_but_blocks_empty(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_write.call("empty_trash")
