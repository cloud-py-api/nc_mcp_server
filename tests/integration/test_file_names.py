"""Integration tests for file names that are not URL-safe, through the Files tools.

Each test checks the directory listing, which reports the names the server actually stored,
so a request that went to a different file than the one asked for cannot pass by reading
back its own mistake.
"""

import asyncio
import json
import secrets
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.state import get_client, get_config, set_state

from .conftest import TEST_BASE_DIR, McpTestHelper

pytestmark = pytest.mark.integration

NAMES = [
    "with space.txt",
    "hash #1.txt",
    "#leading hash.md",
    "question?.txt",
    "100% done.txt",
    "literal %41.txt",
    "literal %2F slash.txt",
    "plus+and&.txt",
    "semi;colon=eq,comma.txt",
    "tick 'quote' @at.txt",
    "brackets [x] {y} (z).txt",
    "Ünïcödé 世界 🙂.txt",
]


async def _listed(nc_mcp: McpTestHelper, path: str) -> list[str]:
    result = json.loads(await nc_mcp.call("list_directory", path=path, limit=500))
    return sorted(e["path"] for e in result["data"])


class TestSpecialCharacterNames:
    @pytest.mark.parametrize("name", NAMES)
    @pytest.mark.asyncio
    async def test_upload_lands_under_its_exact_name(self, nc_mcp: McpTestHelper, name: str) -> None:
        await nc_mcp.create_test_dir()
        path = f"{TEST_BASE_DIR}/{name}"
        await nc_mcp.call("upload_file", path=path, content=f"body of {name}")
        assert await _listed(nc_mcp, TEST_BASE_DIR) == [path]
        assert await nc_mcp.call("get_file", path=path) == f"body of {name}"

    @pytest.mark.asyncio
    async def test_listed_paths_work_as_tool_input(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        for name in NAMES:
            await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/{name}", f"body of {name}")
        listed = await _listed(nc_mcp, TEST_BASE_DIR)
        assert listed == sorted(f"{TEST_BASE_DIR}/{name}" for name in NAMES)
        for path in listed:
            assert await nc_mcp.call("get_file", path=path) == f"body of {path.rsplit('/', 1)[1]}"

    @pytest.mark.asyncio
    async def test_directory_with_special_name(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        folder = f"{TEST_BASE_DIR}/dir #1 %41 Ü?"
        await nc_mcp.call("create_directory", path=folder)
        await nc_mcp.call("upload_file", path=f"{folder}/inner #2.txt", content="inner")
        entries = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR))["data"]
        assert [(e["path"], e["is_directory"]) for e in entries] == [(folder, True)]
        # The folder itself must be recognised and left out of its own listing
        assert await _listed(nc_mcp, folder) == [f"{folder}/inner #2.txt"]

    @pytest.mark.asyncio
    async def test_copy_and_move_to_special_names(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        source = f"{TEST_BASE_DIR}/plain.txt"
        copied = f"{TEST_BASE_DIR}/copy #2 Ü.txt"
        moved = f"{TEST_BASE_DIR}/moved ?%41 世界.txt"
        await nc_mcp.upload_test_file(source, "payload")
        await nc_mcp.call("copy_file", source=source, destination=copied)
        await nc_mcp.call("move_file", source=source, destination=moved)
        assert await _listed(nc_mcp, TEST_BASE_DIR) == sorted([copied, moved])
        await nc_mcp.call("move_file", source=moved, destination=f"{TEST_BASE_DIR}/back #3.txt")
        assert await _listed(nc_mcp, TEST_BASE_DIR) == sorted([copied, f"{TEST_BASE_DIR}/back #3.txt"])
        assert await nc_mcp.call("get_file", path=f"{TEST_BASE_DIR}/back #3.txt") == "payload"

    @pytest.mark.asyncio
    async def test_delete_removes_only_the_named_file(self, nc_mcp: McpTestHelper) -> None:
        """Sent raw, "%41" is decoded to "A" by the server, so the wrong file would go."""
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/a%41.txt", "percent")
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/aA.txt", "letter")
        await nc_mcp.call("delete_file", path=f"{TEST_BASE_DIR}/a%41.txt")
        assert await _listed(nc_mcp, TEST_BASE_DIR) == [f"{TEST_BASE_DIR}/aA.txt"]

    @pytest.mark.asyncio
    async def test_search_inside_special_folder(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.create_test_dir()
        folder = f"{TEST_BASE_DIR}/search #1 %41 Ü"
        await nc_mcp.call("create_directory", path=folder)
        await nc_mcp.upload_test_file(f"{folder}/needle #9.txt", "x")
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/needle outside.txt", "x")
        result = json.loads(await nc_mcp.call("search_files", query="needle", path=folder))
        assert [e["path"] for e in result["data"]] == [f"{folder}/needle #9.txt"]

    @pytest.mark.asyncio
    async def test_upload_from_path_to_special_name(self, nc_mcp_uploads: tuple[McpTestHelper, Path]) -> None:
        helper, root = nc_mcp_uploads
        local = root / "local.txt"
        local.write_bytes(b"streamed")
        await helper.create_test_dir()
        remote = f"{TEST_BASE_DIR}/streamed #1 %41 Ü.txt"
        await helper.call("upload_file_from_path", local_path=str(local), remote_path=remote)
        assert await _listed(helper, TEST_BASE_DIR) == [remote]
        assert await helper.call("get_file", path=remote) == "streamed"


@pytest.fixture
async def nc_mcp_odd_user(nc_mcp: McpTestHelper) -> AsyncGenerator[McpTestHelper]:
    """Run the tools as a user whose ID has a space, a quote and an "@", all valid in Nextcloud."""
    user_id = f"mcp dav o'user {uuid.uuid4().hex[:6]}@test"
    password = f"Mcp-{secrets.token_hex(12)}!"
    await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": password})
    admin_client, admin_config = get_client(), get_config()
    config = Config(
        nextcloud_url=admin_config.nextcloud_url,
        user=user_id,
        password=password,
        permission_level=PermissionLevel.DESTRUCTIVE,
    )
    client = NextcloudClient(config)
    set_state(client, config)
    try:
        yield McpTestHelper(nc_mcp.mcp, client)
    finally:
        set_state(admin_client, admin_config)
        await client.close()
        await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")


class TestUserIdWithSpecialCharacters:
    @pytest.mark.asyncio
    async def test_listing_reports_paths_relative_to_the_users_root(self, nc_mcp_odd_user: McpTestHelper) -> None:
        nc_mcp = nc_mcp_odd_user
        await nc_mcp.call("create_directory", path="work #1")
        await nc_mcp.call("upload_file", path="work #1/notes.md", content="notes")
        root = await _listed(nc_mcp, "/")
        assert "work #1" in root
        assert not [p for p in root if "remote.php" in p]
        assert await _listed(nc_mcp, "work #1") == ["work #1/notes.md"]
        assert await nc_mcp.call("get_file", path="work #1/notes.md") == "notes"

    @pytest.mark.asyncio
    async def test_versions_and_trash(self, nc_mcp_odd_user: McpTestHelper) -> None:
        nc_mcp = nc_mcp_odd_user
        await nc_mcp.call("upload_file", path="doc #1.txt", content="v1")
        await asyncio.sleep(1.5)  # versions are keyed by the second they were made in
        await nc_mcp.call("upload_file", path="doc #1.txt", content="v2")
        entries = json.loads(await nc_mcp.call("list_directory", path="/", limit=500))["data"]
        file_id = int(next(e for e in entries if e["path"] == "doc #1.txt")["file_id"])
        versions = json.loads(await nc_mcp.call("list_versions", file_id=file_id))["data"]
        oldest = min(versions, key=lambda v: int(v["version_id"]))
        await nc_mcp.call("restore_version", file_id=file_id, version_id=oldest["version_id"])
        assert await nc_mcp.call("get_file", path="doc #1.txt") == "v1"

        await nc_mcp.call("delete_file", path="doc #1.txt")
        trash = json.loads(await nc_mcp.call("list_trash"))["data"]
        assert [t["original_name"] for t in trash] == ["doc #1.txt"]
        await nc_mcp.call("restore_trash_item", trash_path=trash[0]["trash_path"])
        assert "doc #1.txt" in await _listed(nc_mcp, "/")
        assert json.loads(await nc_mcp.call("list_trash"))["data"] == []
