"""Integration tests for Files Versions tools against a real Nextcloud instance."""

import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import TEST_BASE_DIR, McpTestHelper

pytestmark = pytest.mark.integration

VER_PREFIX = "mcp-ver-test"


async def _create_versioned_file(nc_mcp: McpTestHelper, name: str) -> int:
    """Create a file, update it to generate a version, return its file_id."""
    await nc_mcp.create_test_dir()
    path = f"{TEST_BASE_DIR}/{VER_PREFIX}-{name}.txt"
    await nc_mcp.upload_test_file(path, "version 1")
    await asyncio.sleep(1.5)
    await nc_mcp.upload_test_file(path, "version 2")
    listing = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR, limit=200))["data"]
    for entry in listing:
        if f"{VER_PREFIX}-{name}.txt" in entry["path"]:
            return int(entry["file_id"])
    raise AssertionError(f"{VER_PREFIX}-{name}.txt not found in listing")


async def _get_file_id(nc_mcp: McpTestHelper, name: str) -> int:
    """Get file_id for a file in the test dir."""
    listing = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR, limit=200))["data"]
    for entry in listing:
        if name in entry["path"]:
            return int(entry["file_id"])
    raise AssertionError(f"{name} not found in listing")


class TestListVersions:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "list-basic")
        result = await nc_mcp.call("list_versions", file_id=file_id)
        parsed = json.loads(result)
        assert isinstance(parsed["data"], list)

    @pytest.mark.asyncio
    async def test_has_multiple_versions(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "multi")
        result = await nc_mcp.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        assert len(versions) >= 2

    @pytest.mark.asyncio
    async def test_version_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "fields")
        result = await nc_mcp.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        assert len(versions) >= 1
        for version in versions:
            assert "version_id" in version
            assert "last_modified" in version
            assert "size" in version

    @pytest.mark.asyncio
    async def test_version_id_is_numeric_string(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "id-format")
        result = await nc_mcp.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        for version in versions:
            assert version["version_id"].isdigit()

    @pytest.mark.asyncio
    async def test_size_is_int(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "size-int")
        result = await nc_mcp.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        for version in versions:
            assert isinstance(version["size"], int)

    @pytest.mark.asyncio
    async def test_content_type_present(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "ctype")
        result = await nc_mcp.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        for version in versions:
            assert "content_type" in version
            assert "text/plain" in version["content_type"]

    @pytest.mark.asyncio
    async def test_single_version_file(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        path = f"{TEST_BASE_DIR}/{VER_PREFIX}-single.txt"
        await nc_mcp.upload_test_file(path, "only one version")
        file_id = await _get_file_id(nc_mcp, f"{VER_PREFIX}-single.txt")
        result = await nc_mcp.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        assert len(versions) >= 1

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("list_versions", file_id=999999999)

    @pytest.mark.asyncio
    async def test_author_field(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "author")
        result = await nc_mcp.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        has_author = any("author" in v for v in versions)
        assert has_author

    @pytest.mark.asyncio
    async def test_versions_reflect_different_sizes(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        path = f"{TEST_BASE_DIR}/{VER_PREFIX}-sizes.txt"
        await nc_mcp.upload_test_file(path, "short")
        await asyncio.sleep(1.5)
        await nc_mcp.upload_test_file(path, "a much longer content string")
        file_id = await _get_file_id(nc_mcp, f"{VER_PREFIX}-sizes.txt")
        result = await nc_mcp.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        sizes = {v["size"] for v in versions}
        assert len(sizes) >= 2


class TestRestoreVersion:
    @pytest.mark.asyncio
    async def test_restore_reverts_content(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "restore")
        versions = json.loads(await nc_mcp.call("list_versions", file_id=file_id, limit=200))["data"]
        oldest = sorted(versions, key=lambda v: int(v["version_id"]))[0]
        await nc_mcp.call("restore_version", file_id=file_id, version_id=oldest["version_id"])
        path = f"{TEST_BASE_DIR}/{VER_PREFIX}-restore.txt"
        content = await nc_mcp.call("get_file", path=path)
        assert "version 1" in content

    @pytest.mark.asyncio
    async def test_restore_returns_confirmation(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "confirm")
        versions = json.loads(await nc_mcp.call("list_versions", file_id=file_id, limit=200))["data"]
        oldest = sorted(versions, key=lambda v: int(v["version_id"]))[0]
        result = await nc_mcp.call("restore_version", file_id=file_id, version_id=oldest["version_id"])
        assert "Restored" in result
        assert str(file_id) in result

    @pytest.mark.asyncio
    async def test_restore_preserves_version_history(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "history")
        versions_before = json.loads(await nc_mcp.call("list_versions", file_id=file_id, limit=200))["data"]
        count_before = len(versions_before)
        oldest = sorted(versions_before, key=lambda v: int(v["version_id"]))[0]
        await nc_mcp.call("restore_version", file_id=file_id, version_id=oldest["version_id"])
        versions_after = json.loads(await nc_mcp.call("list_versions", file_id=file_id, limit=200))["data"]
        assert len(versions_after) >= count_before

    @pytest.mark.asyncio
    async def test_restore_nonexistent_version_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "bad-ver")
        with pytest.raises(ToolError):
            await nc_mcp.call("restore_version", file_id=file_id, version_id="0000000000")

    @pytest.mark.asyncio
    async def test_restore_twice_same_version(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "twice")
        versions = json.loads(await nc_mcp.call("list_versions", file_id=file_id, limit=200))["data"]
        oldest = sorted(versions, key=lambda v: int(v["version_id"]))[0]
        await nc_mcp.call("restore_version", file_id=file_id, version_id=oldest["version_id"])
        versions_mid = json.loads(await nc_mcp.call("list_versions", file_id=file_id, limit=200))["data"]
        v1_entries = [v for v in versions_mid if v["size"] == oldest["size"]]
        assert len(v1_entries) >= 1
        target = v1_entries[0]
        await nc_mcp.call("restore_version", file_id=file_id, version_id=target["version_id"])
        content = await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/{VER_PREFIX}-twice.txt")
        assert "version 1" in content


class TestVersionsPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp: McpTestHelper, nc_mcp_read_only: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "perm-read")
        result = await nc_mcp_read_only.call("list_versions", file_id=file_id, limit=200)
        versions = json.loads(result)["data"]
        assert isinstance(versions, list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_restore(self, nc_mcp: McpTestHelper, nc_mcp_read_only: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "perm-block")
        versions = json.loads(await nc_mcp.call("list_versions", file_id=file_id, limit=200))["data"]
        oldest = sorted(versions, key=lambda v: int(v["version_id"]))[0]
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("restore_version", file_id=file_id, version_id=oldest["version_id"])

    @pytest.mark.asyncio
    async def test_write_allows_restore(self, nc_mcp: McpTestHelper, nc_mcp_write: McpTestHelper) -> None:
        file_id = await _create_versioned_file(nc_mcp, "perm-write")
        versions = json.loads(await nc_mcp.call("list_versions", file_id=file_id, limit=200))["data"]
        oldest = sorted(versions, key=lambda v: int(v["version_id"]))[0]
        result = await nc_mcp_write.call("restore_version", file_id=file_id, version_id=oldest["version_id"])
        assert "Restored" in result
