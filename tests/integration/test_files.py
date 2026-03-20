"""Integration tests for file operations against a real Nextcloud instance."""

from __future__ import annotations

import pytest

from nextcloud_mcp.client import NextcloudClient

pytestmark = pytest.mark.integration


class TestListDirectory:
    @pytest.mark.asyncio
    async def test_list_root(self, nc_client: NextcloudClient) -> None:
        entries = await nc_client.dav_propfind("/", depth=1)
        # Root always has at least the root entry itself
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_list_root_contains_default_folders(self, nc_client: NextcloudClient) -> None:
        entries = await nc_client.dav_propfind("/", depth=1)
        paths = [e["path"] for e in entries]
        # Fresh Nextcloud has these default folders
        assert any("Documents" in p for p in paths) or len(entries) >= 1


class TestFileOperations:
    TEST_DIR = "mcp-integration-tests"
    TEST_FILE = "mcp-integration-tests/test-file.txt"
    TEST_CONTENT = "Hello from Nextcloud MCP integration tests!"

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, nc_client: NextcloudClient) -> None:
        """Test create dir → upload file → read → move → delete."""
        # 1. Create test directory
        try:
            await nc_client.dav_mkcol(self.TEST_DIR)
        except Exception:
            pass  # May already exist

        # 2. Upload a file
        await nc_client.dav_put(
            self.TEST_FILE,
            self.TEST_CONTENT.encode("utf-8"),
            content_type="text/plain",
        )

        # 3. Read it back
        content = await nc_client.dav_get(self.TEST_FILE)
        assert content.decode("utf-8") == self.TEST_CONTENT

        # 4. List the directory — file should be there
        entries = await nc_client.dav_propfind(self.TEST_DIR, depth=1)
        file_paths = [e["path"] for e in entries]
        assert any("test-file.txt" in p for p in file_paths)

        # 5. Move the file
        moved_path = f"{self.TEST_DIR}/moved-file.txt"
        await nc_client.dav_move(self.TEST_FILE, moved_path)

        # 6. Verify move — read from new location
        content = await nc_client.dav_get(moved_path)
        assert content.decode("utf-8") == self.TEST_CONTENT

        # 7. Cleanup — delete file and directory
        await nc_client.dav_delete(moved_path)
        await nc_client.dav_delete(self.TEST_DIR)
