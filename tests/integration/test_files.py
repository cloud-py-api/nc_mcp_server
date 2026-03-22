"""Integration tests for file tools against a real Nextcloud instance.

Tests call MCP tools by name, not the raw client, to exercise the full
tool stack including permission checks, argument parsing, and JSON serialization.
"""

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import TEST_BASE_DIR, McpTestHelper

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
        assert "256 bytes" in result


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
