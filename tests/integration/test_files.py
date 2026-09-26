"""Integration tests for file tools against a real Nextcloud instance.

Tests call MCP tools by name, not the raw client, to exercise the full
tool stack including permission checks, argument parsing, and JSON serialization.
"""

import base64
import json
import os
import secrets
from pathlib import Path
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
        result = await nc_mcp.call("list_directory", path="/", limit=200)
        entries: list[dict[str, object]] = json.loads(result)["data"]
        assert isinstance(entries, list)
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_entries_have_required_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_directory", path="/", limit=200)
        entries = json.loads(result)["data"]
        for entry in entries:
            assert "path" in entry
            assert "is_directory" in entry

    @pytest.mark.asyncio
    async def test_list_created_directory(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        await nc_mcp.call("upload_file", path=f"{TEST_BASE_DIR}/hello.txt", content="hello")

        result = await nc_mcp.call("list_directory", path=TEST_BASE_DIR, limit=200)
        entries = json.loads(result)["data"]
        paths = [e["path"] for e in entries]
        assert any("hello.txt" in p for p in paths)

    @pytest.mark.asyncio
    async def test_list_empty_directory(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        result = await nc_mcp.call("list_directory", path=TEST_BASE_DIR, limit=200)
        entries = json.loads(result)["data"]
        assert entries == []

    @pytest.mark.asyncio
    async def test_list_nonexistent_directory_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"Not found"):
            await nc_mcp.call("list_directory", path="nonexistent-dir-xyz-12345")

    @pytest.mark.asyncio
    async def test_default_path_is_root(self, nc_mcp: McpTestHelper) -> None:
        result_default = await nc_mcp.call("list_directory", limit=200)
        result_root = await nc_mcp.call("list_directory", path="/", limit=200)
        assert json.loads(result_default)["data"] == json.loads(result_root)["data"]


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


class TestUploadFileBinary:
    """Binary upload round-trips through Nextcloud. Payloads are fetched back with
    raw WebDAV (bypassing `get_file`) so we can assert byte-for-byte equality.
    """

    @pytest.mark.asyncio
    async def test_upload_png_round_trip(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/pixel.png",
            content_base64=_TINY_PNG_B64,
            content_type="image/png",
        )
        got, ct = await nc_mcp.client.dav_get(f"{TEST_BASE_DIR}/pixel.png")
        assert got == _TINY_PNG
        assert ct == "image/png"

    @pytest.mark.asyncio
    async def test_upload_pdf_round_trip(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
            b"xref\n0 3\n0000000000 65535 f\n"
            b"0000000009 00000 n\n0000000052 00000 n\n"
            b"trailer<</Size 3/Root 1 0 R>>\nstartxref\n91\n%%EOF\n"
        )
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
        await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/sample.pdf",
            content_base64=pdf_b64,
        )
        got, ct = await nc_mcp.client.dav_get(f"{TEST_BASE_DIR}/sample.pdf")
        assert got == pdf_bytes
        assert ct == "application/pdf"

    @pytest.mark.asyncio
    async def test_upload_full_byte_range(self, nc_mcp: McpTestHelper) -> None:
        """Bytes 0x00-0xFF cover the full range that plain upload_file cannot send."""
        await nc_mcp.create_test_dir()
        raw = bytes(range(256)) * 8  # 2 KiB of every byte value
        b64 = base64.b64encode(raw).decode("ascii")
        await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/all-bytes.bin",
            content_base64=b64,
            content_type="application/octet-stream",
        )
        got, _ = await nc_mcp.client.dav_get(f"{TEST_BASE_DIR}/all-bytes.bin")
        assert got == raw

    @pytest.mark.asyncio
    async def test_content_type_inferred_from_extension(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/auto.png",
            content_base64=_TINY_PNG_B64,
        )
        _, ct = await nc_mcp.client.dav_get(f"{TEST_BASE_DIR}/auto.png")
        assert ct == "image/png"

    @pytest.mark.asyncio
    async def test_overwrites_existing_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        first = base64.b64encode(b"first-version").decode("ascii")
        second = base64.b64encode(b"second-version-longer").decode("ascii")
        await nc_mcp.call("upload_file_binary", path=f"{TEST_BASE_DIR}/ow.bin", content_base64=first)
        await nc_mcp.call("upload_file_binary", path=f"{TEST_BASE_DIR}/ow.bin", content_base64=second)
        got, _ = await nc_mcp.client.dav_get(f"{TEST_BASE_DIR}/ow.bin")
        assert got == b"second-version-longer"

    @pytest.mark.asyncio
    async def test_empty_content_creates_empty_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/empty.bin",
            content_base64="",
            content_type="application/octet-stream",
        )
        got, _ = await nc_mcp.client.dav_get(f"{TEST_BASE_DIR}/empty.bin")
        assert got == b""

    @pytest.mark.asyncio
    async def test_result_message_reports_bytes_and_type(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        result = await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/reported.png",
            content_base64=_TINY_PNG_B64,
            content_type="image/png",
        )
        assert str(len(_TINY_PNG)) in result
        assert "image/png" in result

    @pytest.mark.asyncio
    async def test_invalid_base64_raises(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        with pytest.raises(ToolError, match=r"not valid base64"):
            await nc_mcp.call(
                "upload_file_binary",
                path=f"{TEST_BASE_DIR}/bad.bin",
                content_base64="!!!not-base64!!!",
            )

    @pytest.mark.asyncio
    async def test_mime_wrapped_base64_accepted(self, nc_mcp: McpTestHelper) -> None:
        """MIME-style base64 (76-char line wraps with \\r\\n) should decode cleanly."""
        await nc_mcp.create_test_dir()
        raw = bytes(range(200))
        wrapped = base64.encodebytes(raw).decode("ascii")  # always adds \n every 76 chars
        assert "\n" in wrapped
        await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/wrapped.bin",
            content_base64=wrapped,
        )
        got, _ = await nc_mcp.client.dav_get(f"{TEST_BASE_DIR}/wrapped.bin")
        assert got == raw

    @pytest.mark.asyncio
    async def test_base64_with_stray_whitespace_accepted(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        raw = b"payload with stray whitespace"
        encoded = base64.b64encode(raw).decode("ascii")
        dirty = f"  {encoded[:10]} \t \n{encoded[10:]}\r\n"
        await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/dirty.bin",
            content_base64=dirty,
        )
        got, _ = await nc_mcp.client.dav_get(f"{TEST_BASE_DIR}/dirty.bin")
        assert got == raw

    @pytest.mark.asyncio
    async def test_uploaded_image_readable_via_get_file(self, nc_mcp: McpTestHelper) -> None:
        """Binary upload integrates with existing get_file image handling."""
        await nc_mcp.create_test_dir()
        await nc_mcp.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/readable.png",
            content_base64=_TINY_PNG_B64,
        )
        result = await nc_mcp.mcp._tool_manager.call_tool("get_file", {"path": f"{TEST_BASE_DIR}/readable.png"})
        assert isinstance(result, list)
        item = result[0]  # type: ignore[index]
        assert item.type == "image"  # type: ignore[union-attr]
        assert item.mimeType == "image/png"  # type: ignore[union-attr]
        assert base64.b64decode(item.data) == _TINY_PNG  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_read_only_blocks(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call(
                "upload_file_binary",
                path=f"{TEST_BASE_DIR}/denied.png",
                content_base64=_TINY_PNG_B64,
            )

    @pytest.mark.asyncio
    async def test_write_permission_allows(self, nc_mcp_write: McpTestHelper) -> None:
        await nc_mcp_write.create_test_dir()
        result = await nc_mcp_write.call(
            "upload_file_binary",
            path=f"{TEST_BASE_DIR}/write-ok.png",
            content_base64=_TINY_PNG_B64,
            content_type="image/png",
        )
        assert "uploaded successfully" in result


class TestCreateDirectory:
    @pytest.mark.asyncio
    async def test_create_directory(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_directory", path=TEST_BASE_DIR)
        assert "created" in result.lower()

        root = json.loads(await nc_mcp.call("list_directory", path="/", limit=200))["data"]
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

        entries = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR, limit=200))["data"]
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
        entries = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR, limit=200))["data"]
        assert entries == []

        await nc_mcp.call("delete_file", path=TEST_BASE_DIR)


class TestSearchFiles:
    @pytest.mark.asyncio
    async def test_wildcard_characters_are_literal(self, nc_mcp: McpTestHelper) -> None:
        folder = f"{TEST_BASE_DIR}/wildcards"
        await nc_mcp.create_test_dir()
        await nc_mcp.create_test_dir(folder)
        for name in ("a_b.txt", "axb.txt", "50%off.txt", "50xoff.txt"):
            await nc_mcp.upload_test_file(f"{folder}/{name}", "x")

        async def names(query: str) -> list[str]:
            result = json.loads(await nc_mcp.call("search_files", query=query, path=folder))
            return sorted(e["path"].rsplit("/", 1)[-1] for e in result["data"])

        assert await names("a_b") == ["a_b.txt"]
        assert await names("50%OFF") == ["50%off.txt"]
        assert await names("off") == ["50%off.txt", "50xoff.txt"]

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


class TestUploadFileFromPath:
    """Upload via local filesystem path, requires NEXTCLOUD_MCP_UPLOAD_ROOT."""

    @pytest.mark.asyncio
    async def test_tool_not_registered_without_upload_root(self, nc_mcp: McpTestHelper) -> None:
        """The default nc_mcp fixture has no upload_root — tool is absent."""
        assert "upload_file_from_path" not in nc_mcp.tool_names()

    @pytest.mark.asyncio
    async def test_tool_registered_when_upload_root_set(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, _ = nc_mcp_uploads
        assert "upload_file_from_path" in helper.tool_names()

    @pytest.mark.asyncio
    async def test_upload_small_text_file_round_trip(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        payload = b"hello from local disk\n"
        local = root / "note.txt"
        local.write_bytes(payload)
        await helper.create_test_dir()

        result = await helper.call(
            "upload_file_from_path",
            local_path=str(local),
            remote_path=f"{TEST_BASE_DIR}/note.txt",
        )
        assert "uploaded successfully" in result
        assert str(len(payload)) in result

        content, content_type = await helper.client.dav_get(f"{TEST_BASE_DIR}/note.txt")
        assert content == payload
        assert content_type == "text/plain"

    @pytest.mark.asyncio
    async def test_upload_binary_file_round_trip(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        local = root / "pixel.png"
        local.write_bytes(_TINY_PNG)
        await helper.create_test_dir()

        await helper.call(
            "upload_file_from_path",
            local_path=str(local),
            remote_path=f"{TEST_BASE_DIR}/pixel.png",
        )
        content, content_type = await helper.client.dav_get(f"{TEST_BASE_DIR}/pixel.png")
        assert content == _TINY_PNG
        assert content_type == "image/png"

    @pytest.mark.asyncio
    async def test_upload_large_file_streams_correctly(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        """Upload a 2 MiB file — larger than the 256 KiB chunk size, so streaming is exercised."""
        helper, root = nc_mcp_uploads
        payload = secrets.token_bytes(2 * 1024 * 1024)
        local = root / "big.bin"
        local.write_bytes(payload)
        await helper.create_test_dir()

        result = await helper.call(
            "upload_file_from_path",
            local_path=str(local),
            remote_path=f"{TEST_BASE_DIR}/big.bin",
        )
        assert str(len(payload)) in result
        content, _ = await helper.client.dav_get(f"{TEST_BASE_DIR}/big.bin")
        assert content == payload

    @pytest.mark.asyncio
    async def test_upload_overwrites_existing_remote(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        await helper.create_test_dir()
        await helper.client.dav_put(f"{TEST_BASE_DIR}/reuse.txt", b"original", content_type="text/plain")
        local = root / "reuse.txt"
        local.write_bytes(b"replaced")
        await helper.call(
            "upload_file_from_path",
            local_path=str(local),
            remote_path=f"{TEST_BASE_DIR}/reuse.txt",
        )
        content, _ = await helper.client.dav_get(f"{TEST_BASE_DIR}/reuse.txt")
        assert content == b"replaced"

    @pytest.mark.asyncio
    async def test_upload_empty_file(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        local = root / "empty.txt"
        local.write_bytes(b"")
        await helper.create_test_dir()

        await helper.call(
            "upload_file_from_path",
            local_path=str(local),
            remote_path=f"{TEST_BASE_DIR}/empty.txt",
        )
        content, _ = await helper.client.dav_get(f"{TEST_BASE_DIR}/empty.txt")
        assert content == b""

    @pytest.mark.asyncio
    async def test_upload_from_nested_subdirectory(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        sub = root / "nested" / "deep"
        sub.mkdir(parents=True)
        local = sub / "x.txt"
        local.write_bytes(b"deep content")
        await helper.create_test_dir()

        await helper.call(
            "upload_file_from_path",
            local_path=str(local),
            remote_path=f"{TEST_BASE_DIR}/deep.txt",
        )
        content, _ = await helper.client.dav_get(f"{TEST_BASE_DIR}/deep.txt")
        assert content == b"deep content"

    @pytest.mark.asyncio
    async def test_rejects_file_outside_upload_root(
        self, nc_mcp_uploads: tuple[McpTestHelper, Path], tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        helper, _root = nc_mcp_uploads
        other = tmp_path_factory.mktemp("outside")
        outside_file = other / "secret.txt"
        outside_file.write_bytes(b"secret")
        with pytest.raises(ToolError, match="outside the configured upload root"):
            await helper.call(
                "upload_file_from_path",
                local_path=str(outside_file),
                remote_path=f"{TEST_BASE_DIR}/secret.txt",
            )

    @pytest.mark.asyncio
    async def test_rejects_symlink_pointing_outside(
        self, nc_mcp_uploads: tuple[McpTestHelper, Path], tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        helper, root = nc_mcp_uploads
        other = tmp_path_factory.mktemp("outside-target")
        target = other / "secret.txt"
        target.write_bytes(b"secret")
        link = root / "link.txt"
        link.symlink_to(target)
        with pytest.raises(ToolError, match="outside the configured upload root"):
            await helper.call(
                "upload_file_from_path",
                local_path=str(link),
                remote_path=f"{TEST_BASE_DIR}/leaked.txt",
            )

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_file(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        missing = root / "does-not-exist.txt"
        with pytest.raises(ToolError, match="not found"):
            await helper.call(
                "upload_file_from_path",
                local_path=str(missing),
                remote_path=f"{TEST_BASE_DIR}/x.txt",
            )

    @pytest.mark.asyncio
    async def test_rejects_directory_path(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        sub = root / "a-dir"
        sub.mkdir()
        with pytest.raises(ToolError, match="not a regular file"):
            await helper.call(
                "upload_file_from_path",
                local_path=str(sub),
                remote_path=f"{TEST_BASE_DIR}/dir.bin",
            )

    @pytest.mark.asyncio
    async def test_rejects_parent_traversal(
        self, nc_mcp_uploads: tuple[McpTestHelper, Path], tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        helper, root = nc_mcp_uploads
        other = tmp_path_factory.mktemp("sibling")
        outside = other / "escape.txt"
        outside.write_bytes(b"escape")
        traversal = root / ".." / other.name / "escape.txt"
        with pytest.raises(ToolError, match="outside the configured upload root"):
            await helper.call(
                "upload_file_from_path",
                local_path=str(traversal),
                remote_path=f"{TEST_BASE_DIR}/escape.txt",
            )

    @pytest.mark.asyncio
    async def test_rejects_fifo(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        fifo = root / "pipe"
        os.mkfifo(fifo)
        with pytest.raises(ToolError, match="not a regular file"):
            await helper.call(
                "upload_file_from_path",
                local_path=str(fifo),
                remote_path=f"{TEST_BASE_DIR}/pipe.bin",
            )

    @pytest.mark.asyncio
    async def test_rejects_empty_local_path(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, _root = nc_mcp_uploads
        with pytest.raises(ToolError, match="cannot be empty"):
            await helper.call(
                "upload_file_from_path",
                local_path="",
                remote_path=f"{TEST_BASE_DIR}/x.txt",
            )

    @pytest.mark.asyncio
    async def test_content_type_inferred_from_remote_extension(
        self, nc_mcp_uploads: tuple[McpTestHelper, Path]
    ) -> None:
        """When content_type is empty, it is inferred from the remote_path extension."""
        helper, root = nc_mcp_uploads
        local = root / "untyped"  # no extension
        local.write_bytes(_TINY_PNG)
        await helper.create_test_dir()

        result = await helper.call(
            "upload_file_from_path",
            local_path=str(local),
            remote_path=f"{TEST_BASE_DIR}/inferred.png",
        )
        assert "image/png" in result

    @pytest.mark.asyncio
    async def test_read_only_permission_blocks(self, nc_mcp_uploads_read_only: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads_read_only
        local = root / "x.txt"
        local.write_bytes(b"x")
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await helper.call(
                "upload_file_from_path",
                local_path=str(local),
                remote_path=f"{TEST_BASE_DIR}/x.txt",
            )
