"""Integration tests for file tools against a real Nextcloud instance.

Tests call MCP tools by name, not the raw client, to exercise the full
tool stack including permission checks, argument parsing, and JSON serialization.
"""

import base64
import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import TEST_BASE_DIR, McpTestHelper

# 1x1 red pixel PNG (70 bytes)
_TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
_TINY_PNG = base64.b64decode(_TINY_PNG_B64)

pytestmark = pytest.mark.integration


class TestListDirectory:
    @pytest.mark.asyncio
    async def test_list_root_returns_json(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_directory", path="/")
        entries: list[dict[str, object]] = json.loads(result)
        assert isinstance(entries, list)
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_entries_have_required_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_directory", path="/")
        entries = json.loads(result)
        for entry in entries:
            assert "path" in entry
            assert "is_directory" in entry

    @pytest.mark.asyncio
    async def test_list_created_directory(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/hello.txt", content="hello")

        result = await nc_mcp.call("list_directory", path=TEST_BASE_DIR)
        entries = json.loads(result)
        paths = [e["path"] for e in entries]
        assert any("hello.txt" in p for p in paths)

    @pytest.mark.asyncio
    async def test_list_empty_directory(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        result = await nc_mcp.call("list_directory", path=TEST_BASE_DIR)
        entries = json.loads(result)
        assert entries == []

    @pytest.mark.asyncio
    async def test_list_nonexistent_directory_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"Not found"):
            await nc_mcp.call("list_directory", path="nonexistent-dir-xyz-12345")

    @pytest.mark.asyncio
    async def test_default_path_is_root(self, nc_mcp: McpTestHelper) -> None:
        result_default = await nc_mcp.call("list_directory")
        result_root = await nc_mcp.call("list_directory", path="/")
        assert json.loads(result_default) == json.loads(result_root)


class TestGetFile:
    @pytest.mark.asyncio
    async def test_read_text_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        content = "Hello, Nextcloud MCP!"
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/read-test.txt", content=content)

        result = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/read-test.txt")
        assert result == content

    @pytest.mark.asyncio
    async def test_read_utf8_content(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        content = "Привет мир! 你好世界! 🌍"
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/utf8-test.txt", content=content)

        result = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/utf8-test.txt")
        assert result == content

    @pytest.mark.asyncio
    async def test_read_empty_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/empty.txt", content="")

        result = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/empty.txt")
        assert result == ""

    @pytest.mark.asyncio
    async def test_read_nonexistent_file_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"Not found"):
            await nc_mcp.call("get_file", path="nonexistent-file-xyz-12345.txt")

    @pytest.mark.asyncio
    async def test_read_binary_file_returns_description(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        binary_content = bytes(range(256))
        await nc_mcp.client.dav_put(
            f"{TEST_BASE_DIR}/binary.bin", binary_content, content_type="application/octet-stream"
        )

        result = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/binary.bin")
        assert "Binary file" in result
        assert "application/" in result


class TestUploadFile:
    @pytest.mark.asyncio
    async def test_upload_creates_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        result = await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/new.txt", content="new file")
        assert "uploaded successfully" in result

        content = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/new.txt")
        assert content == "new file"

    @pytest.mark.asyncio
    async def test_upload_overwrites_existing(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/overwrite.txt", content="v1")
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/overwrite.txt", content="v2")

        content = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/overwrite.txt")
        assert content == "v2"

    @pytest.mark.asyncio
    async def test_upload_large_text(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        large_content = "x" * 100_000
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/large.txt", content=large_content)

        result = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/large.txt")
        assert len(result) == 100_000


class TestCreateDirectory:
    @pytest.mark.asyncio
    async def test_create_directory(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        assert "created" in result.lower()

        root = json.loads(await nc_mcp.call("list_directory", path="/"))
        paths = [e["path"] for e in root]
        assert any(TEST_BASE_DIR in p for p in paths)

    @pytest.mark.asyncio
    async def test_create_nested_directory(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        nested = f"{TEST_BASE_DIR}/sub1"
        result = await nc_mcp.call("create_directory", path=nested)
        assert "created" in result.lower()

    @pytest.mark.asyncio
    async def test_create_existing_directory_raises(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        with pytest.raises(ToolError):
            await nc_mcp.call("create_directory", path=TEST_BASE_DIR)


class TestDeleteFile:
    @pytest.mark.asyncio
    async def test_delete_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/to-delete.txt", content="bye")
        result = await nc_mcp.call("delete_file", path=f"{TEST_BASE_DIR}/to-delete.txt")
        assert "Deleted" in result

        with pytest.raises(ToolError, match=r"Not found"):
            await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/to-delete.txt")

    @pytest.mark.asyncio
    async def test_delete_directory(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/inside.txt", content="data")
        result = await nc_mcp.call("delete_file", path=TEST_BASE_DIR)
        assert "Deleted" in result

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"Not found"):
            await nc_mcp.call("delete_file", path="nonexistent-xyz-12345.txt")


class TestMoveFile:
    @pytest.mark.asyncio
    async def test_move_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/original.txt", content="data")

        result = await nc_mcp.call(
            "move_file", source=f"{TEST_BASE_DIR}/original.txt", destination=f"{TEST_BASE_DIR}/moved.txt"
        )
        assert "Moved" in result

        with pytest.raises(ToolError, match=r"Not found"):
            await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/original.txt")

        content = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/moved.txt")
        assert content == "data"

    @pytest.mark.asyncio
    async def test_rename_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/old-name.txt", content="renamed")
        await nc_mcp.call(
            "move_file", source=f"{TEST_BASE_DIR}/old-name.txt", destination=f"{TEST_BASE_DIR}/new-name.txt"
        )

        content = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/new-name.txt")
        assert content == "renamed"

    @pytest.mark.asyncio
    async def test_move_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        with pytest.raises(ToolError, match=r"Not found"):
            await nc_mcp.call("move_file", source=f"{TEST_BASE_DIR}/nope.txt", destination=f"{TEST_BASE_DIR}/dest.txt")


class TestFileLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, nc_mcp: McpTestHelper) -> None:
        """End-to-end: create dir -> upload -> read -> list -> move -> delete."""
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/lifecycle.txt", content="lifecycle test")

        content = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/lifecycle.txt")
        assert content == "lifecycle test"

        entries = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR))
        assert len(entries) == 1
        assert entries[0]["is_directory"] is False

        await nc_mcp.call(
            "move_file",
            source=f"{TEST_BASE_DIR}/lifecycle.txt",
            destination=f"{TEST_BASE_DIR}/lifecycle-moved.txt",
        )
        content = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/lifecycle-moved.txt")
        assert content == "lifecycle test"

        await nc_mcp.call("delete_file", path=f"{TEST_BASE_DIR}/lifecycle-moved.txt")
        entries = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR))
        assert entries == []

        await nc_mcp.call("delete_file", path=TEST_BASE_DIR)


class TestSearchFiles:
    @pytest.mark.asyncio
    async def test_search_by_name(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/searchable-doc.txt", "content")
        result = await nc_mcp.call("search_files", query="searchable-doc")
        data = json.loads(result)["data"]
        assert len(data) >= 1
        assert any("searchable-doc" in e["path"] for e in data)

    @pytest.mark.asyncio
    async def test_search_returns_file_properties(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/search-props.txt", "hello")
        result = await nc_mcp.call("search_files", query="search-props")
        data = json.loads(result)["data"]
        assert len(data) >= 1
        entry = next(e for e in data if "search-props" in e["path"])
        for field in ["path", "is_directory", "file_id"]:
            assert field in entry, f"Missing field: {field}"
        assert entry["is_directory"] is False

    @pytest.mark.asyncio
    async def test_search_by_mimetype(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/search-mime.txt", "text file")
        result = await nc_mcp.call("search_files", mimetype="text", path=TEST_BASE_DIR)
        data = json.loads(result)["data"]
        assert len(data) >= 1
        assert any("search-mime" in e["path"] for e in data)

    @pytest.mark.asyncio
    async def test_search_combined_query_and_mimetype(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/combined-search.txt", "combined")
        result = await nc_mcp.call("search_files", query="combined-search", mimetype="text")
        data = json.loads(result)["data"]
        assert len(data) >= 1
        assert any("combined-search" in e["path"] for e in data)

    @pytest.mark.asyncio
    async def test_search_no_results(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("search_files", query="nonexistent-file-xyz-999")
        data: list[Any] = json.loads(result)["data"]
        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_search_limit(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        for i in range(5):
            await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/limit-file-{i}.txt", f"content {i}")
        result = await nc_mcp.call("search_files", query="limit-file", limit=2)
        data = json.loads(result)["data"]
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_search_with_offset(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        for i in range(4):
            await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/page-file-{i}.txt", f"content {i}")
        page1 = await nc_mcp.call("search_files", query="page-file", limit=2, offset=0)
        data1 = json.loads(page1)["data"]
        assert len(data1) == 2
        page2 = await nc_mcp.call("search_files", query="page-file", limit=2, offset=2)
        data2 = json.loads(page2)["data"]
        assert len(data2) >= 1

    @pytest.mark.asyncio
    async def test_search_pagination_info(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/footer-file.txt", "test")
        result = await nc_mcp.call("search_files", query="footer-file")
        parsed = json.loads(result)
        assert "pagination" in parsed
        assert "has_more" in parsed["pagination"]
        assert "offset" in parsed["pagination"]

    @pytest.mark.asyncio
    async def test_search_scoped_to_path(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/scoped.txt", "inside")
        result = await nc_mcp.call("search_files", query="scoped", path=TEST_BASE_DIR)
        data = json.loads(result)["data"]
        assert len(data) >= 1
        assert all(TEST_BASE_DIR in e["path"] for e in data)

    @pytest.mark.asyncio
    async def test_search_empty_query_and_mimetype_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("search_files")

    @pytest.mark.asyncio
    async def test_search_read_only_allowed(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("search_files", query="anything")
        data = json.loads(result)["data"]
        assert isinstance(data, list)


class TestGetFileImageHandling:
    @pytest.mark.asyncio
    async def test_image_returns_image_content(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.client.dav_put(f"{TEST_BASE_DIR}/test.png", _TINY_PNG, content_type="image/png")
        result = await nc_mcp.mcp._tool_manager.call_tool("get_file", {"path": f"{TEST_BASE_DIR}/test.png"})
        assert isinstance(result, list)
        item = result[0]  # type: ignore[index]
        assert item.type == "image"  # type: ignore[union-attr]
        assert item.mimeType == "image/png"  # type: ignore[union-attr]
        assert len(item.data) > 0  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_text_file_returns_text(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/hello.txt", "hello world")
        result = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/hello.txt")
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_binary_non_image_returns_metadata(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.client.dav_put(
            f"{TEST_BASE_DIR}/data.bin", b"\x00\x01\x02\xff" * 100, content_type="application/octet-stream"
        )
        result = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/data.bin")
        assert "Binary file" in result
        assert "application/" in result

    @pytest.mark.asyncio
    async def test_large_image_returns_metadata(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        large_data = _TINY_PNG + b"\x00" * (11 * 1024 * 1024)
        await nc_mcp.client.dav_put(f"{TEST_BASE_DIR}/huge.png", large_data, content_type="image/png")
        result = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/huge.png")
        assert "Binary file" in result

    @pytest.mark.asyncio
    async def test_jpeg_returns_image_content(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        jpeg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9"
        await nc_mcp.client.dav_put(f"{TEST_BASE_DIR}/test.jpg", jpeg_data, content_type="image/jpeg")
        result = await nc_mcp.mcp._tool_manager.call_tool("get_file", {"path": f"{TEST_BASE_DIR}/test.jpg"})
        assert isinstance(result, list)
        item = result[0]  # type: ignore[index]
        assert item.type == "image"  # type: ignore[union-attr]
        assert item.mimeType == "image/jpeg"  # type: ignore[union-attr]


class TestCopyFile:
    @pytest.mark.asyncio
    async def test_copy_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/original.txt", "original content")
        result = await nc_mcp.call(
            "copy_file", source=f"{TEST_BASE_DIR}/original.txt", destination=f"{TEST_BASE_DIR}/copy.txt"
        )
        assert "Copied" in result
        original = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/original.txt")
        assert original == "original content"
        copy = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/copy.txt")
        assert copy == "original content"

    @pytest.mark.asyncio
    async def test_copy_directory(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.create_test_dir(f"{TEST_BASE_DIR}/src-dir")
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/src-dir/file.txt", "inside dir")
        result = await nc_mcp.call(
            "copy_file", source=f"{TEST_BASE_DIR}/src-dir", destination=f"{TEST_BASE_DIR}/dst-dir"
        )
        assert "Copied" in result
        copy = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/dst-dir/file.txt")
        assert copy == "inside dir"

    @pytest.mark.asyncio
    async def test_copy_source_not_found(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call(
                "copy_file", source=f"{TEST_BASE_DIR}/nonexistent.txt", destination=f"{TEST_BASE_DIR}/dest.txt"
            )

    @pytest.mark.asyncio
    async def test_copy_destination_exists_fails(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/src.txt", "source")
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/dst.txt", "destination")
        with pytest.raises(ToolError):
            await nc_mcp.call("copy_file", source=f"{TEST_BASE_DIR}/src.txt", destination=f"{TEST_BASE_DIR}/dst.txt")

    @pytest.mark.asyncio
    async def test_copy_preserves_source(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/keep.txt", "keep me")
        await nc_mcp.call("copy_file", source=f"{TEST_BASE_DIR}/keep.txt", destination=f"{TEST_BASE_DIR}/kept.txt")
        source = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/keep.txt")
        assert source == "keep me"

    @pytest.mark.asyncio
    async def test_copy_read_only_blocked(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("copy_file", source="a.txt", destination="b.txt")

    @pytest.mark.asyncio
    async def test_copy_write_allowed(self, nc_mcp_write: McpTestHelper) -> None:
        await nc_mcp_write.create_test_dir()
        await nc_mcp_write.client.dav_put(f"{TEST_BASE_DIR}/w-src.txt", b"write test", content_type="text/plain")
        result = await nc_mcp_write.call(
            "copy_file", source=f"{TEST_BASE_DIR}/w-src.txt", destination=f"{TEST_BASE_DIR}/w-dst.txt"
        )
        assert "Copied" in result
