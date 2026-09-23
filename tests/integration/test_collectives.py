"""Integration tests for Collectives tools against a real Nextcloud instance."""

import contextlib
import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.tools import collectives

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

UNIQUE = "mcp-test-coll"


async def _create_collective(nc_mcp: McpTestHelper, suffix: str = "") -> dict[str, Any]:
    name = f"{UNIQUE}-{suffix}" if suffix else UNIQUE
    result = await nc_mcp.call("create_collective", name=name)
    return json.loads(result)


async def _get_landing_page_id(nc_mcp: McpTestHelper, collective_id: int) -> int:
    result = await nc_mcp.call("get_collective_pages", collective_id=collective_id, limit=200)
    pages = json.loads(result)["data"]
    return pages[0]["id"]


async def _write_page(nc_mcp: McpTestHelper, collective_id: int, page_id: int, text: str) -> None:
    """Write Markdown into a page's file, the way the Collectives editor does.

    The path comes from the live page object through the same helper the tool uses, so a
    change to how Collectives reports page locations fails these tests too.
    """
    data = await nc_mcp.client.ocs_get(f"apps/collectives/api/v1.0/collectives/{collective_id}/pages/{page_id}")
    path = collectives._page_dav_path(data["page"])
    await nc_mcp.client.dav_put(path, text.encode("utf-8"), content_type="text/markdown; charset=utf-8")


async def _destroy_collective(nc_mcp: McpTestHelper, collective_id: int) -> None:
    """Trash + permanently delete a collective."""
    with contextlib.suppress(Exception):
        await nc_mcp.call("trash_collective", collective_id=collective_id)
    with contextlib.suppress(Exception):
        await nc_mcp.call("delete_collective", collective_id=collective_id)


async def _cleanup_collectives(nc_mcp: McpTestHelper) -> None:
    result = await nc_mcp.call("list_collectives", limit=200)
    for c in json.loads(result)["data"]:
        if str(c.get("name", "")).startswith(UNIQUE):
            await _destroy_collective(nc_mcp, c["id"])


class TestListCollectives:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_collectives", limit=200)
        data = json.loads(result)["data"]
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_created_collective_appears_in_list(self, nc_mcp: McpTestHelper) -> None:
        await _cleanup_collectives(nc_mcp)
        coll = await _create_collective(nc_mcp, "list")
        try:
            result = await nc_mcp.call("list_collectives", limit=200)
            names = [c["name"] for c in json.loads(result)["data"]]
            assert coll["name"] in names
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_collective_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "fields")
        try:
            result = await nc_mcp.call("list_collectives", limit=200)
            matches = [c for c in json.loads(result)["data"] if c["id"] == coll["id"]]
            assert len(matches) == 1
            c = matches[0]
            assert "id" in c
            assert "name" in c
            assert "level" in c
            assert "can_edit" in c
        finally:
            await _destroy_collective(nc_mcp, coll["id"])


class TestCreateCollective:
    @pytest.mark.asyncio
    async def test_create_returns_collective(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "create")
        try:
            assert coll["id"] > 0
            assert coll["name"] == f"{UNIQUE}-create"
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_create_with_emoji(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_collective", name=f"{UNIQUE}-emoji", emoji="\U0001f4da")
        coll = json.loads(result)
        try:
            assert coll["emoji"] == "\U0001f4da"
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_create_empty_name_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("create_collective", name="")

    @pytest.mark.asyncio
    async def test_create_duplicate_name_raises(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "dup")
        try:
            with pytest.raises(ToolError):
                await nc_mcp.call("create_collective", name=coll["name"])
        finally:
            await _destroy_collective(nc_mcp, coll["id"])


class TestGetCollectivePages:
    @pytest.mark.asyncio
    async def test_new_collective_has_landing_page(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "pages")
        try:
            result = await nc_mcp.call("get_collective_pages", collective_id=coll["id"], limit=200)
            pages = json.loads(result)["data"]
            assert len(pages) >= 1
            assert pages[0]["title"] == "Landing page"
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_page_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "pgfields")
        try:
            result = await nc_mcp.call("get_collective_pages", collective_id=coll["id"], limit=200)
            page = json.loads(result)["data"][0]
            for field in ["id", "title", "timestamp", "file_name"]:
                assert field in page, f"Missing field: {field}"
        finally:
            await _destroy_collective(nc_mcp, coll["id"])


class TestGetCollectivePage:
    @pytest.mark.asyncio
    async def test_get_landing_page(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "getpg")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            result = await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=landing_id)
            page = json.loads(result)
            assert page["id"] == landing_id
            assert page["title"] == "Landing page"
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_get_page_includes_tags(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "tags")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            result = await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=landing_id)
            page = json.loads(result)
            assert "tags" in page
            assert isinstance(page["tags"], list)
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_get_nonexistent_page_raises(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "nopage")
        try:
            with pytest.raises(ToolError):
                await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=999999)
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_new_page_has_empty_content(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "emptypg")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Empty"
                )
            )
            result = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=page["id"]))
            assert result["content"] == ""
            assert "content_error" not in result
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_page_content_is_returned(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "content")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            await _write_page(nc_mcp, coll["id"], landing_id, "# Landing\n\nWelcome to the team wiki.")
            result = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=landing_id))
            assert result["content"] == "# Landing\n\nWelcome to the team wiki."
            assert result["size"] == len("# Landing\n\nWelcome to the team wiki.")
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_subpage_content_is_read_from_its_own_file(self, nc_mcp: McpTestHelper) -> None:
        """A page with children turns into a folder, so parent and child must not be mixed up."""
        coll = await _create_collective(nc_mcp, "subcontent")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            parent = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Parent"
                )
            )
            child = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=parent["id"], title="Child"
                )
            )
            await _write_page(nc_mcp, coll["id"], parent["id"], "parent text")
            await _write_page(nc_mcp, coll["id"], child["id"], "child text")
            for page_id, expected in ((parent["id"], "parent text"), (child["id"], "child text")):
                result = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=page_id))
                assert result["content"] == expected
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_content_survives_non_ascii(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "utf8")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            await _write_page(nc_mcp, coll["id"], landing_id, "Grüße, 世界 🙂")
            result = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=landing_id))
            assert result["content"] == "Grüße, 世界 🙂"
        finally:
            await _destroy_collective(nc_mcp, coll["id"])


class TestCreateCollectivePage:
    @pytest.mark.asyncio
    async def test_create_page(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "newpg")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            result = await nc_mcp.call(
                "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="New Page"
            )
            page = json.loads(result)
            assert page["title"] == "New Page"
            assert page["id"] > 0
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_create_subpage(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "subpg")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            parent = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Parent"
                )
            )
            child = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=parent["id"], title="Child"
                )
            )
            assert child["title"] == "Child"
            pages = json.loads(await nc_mcp.call("get_collective_pages", collective_id=coll["id"], limit=200))["data"]
            titles = [p["title"] for p in pages]
            assert "Parent" in titles
            assert "Child" in titles
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_create_empty_title_raises(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "emptytitle")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            with pytest.raises((ToolError, ValueError)):
                await nc_mcp.call("create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="")
        finally:
            await _destroy_collective(nc_mcp, coll["id"])


class TestTrashAndRestoreCollective:
    @pytest.mark.asyncio
    async def test_trash_removes_from_list(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "trash")
        try:
            await nc_mcp.call("trash_collective", collective_id=coll["id"])
            result = await nc_mcp.call("list_collectives", limit=200)
            ids = [c["id"] for c in json.loads(result)["data"]]
            assert coll["id"] not in ids
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_restore_brings_back(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "restcoll")
        try:
            await nc_mcp.call("trash_collective", collective_id=coll["id"])
            result = await nc_mcp.call("restore_collective", collective_id=coll["id"])
            restored = json.loads(result)
            assert restored["name"] == coll["name"]
            listed = json.loads(await nc_mcp.call("list_collectives", limit=200))["data"]
            assert coll["id"] in [c["id"] for c in listed]
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_permanent_delete(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "permdel")
        await nc_mcp.call("trash_collective", collective_id=coll["id"])
        result = await nc_mcp.call("delete_collective", collective_id=coll["id"])
        assert "deleted" in result.lower()


class TestTrashAndRestorePage:
    @pytest.mark.asyncio
    async def test_trash_page_removes_from_list(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "trashpg")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Trash Me"
                )
            )
            await nc_mcp.call("trash_collective_page", collective_id=coll["id"], page_id=page["id"])
            pages = json.loads(await nc_mcp.call("get_collective_pages", collective_id=coll["id"], limit=200))["data"]
            assert page["id"] not in [p["id"] for p in pages]
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_restore_page(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "restpg")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Restore Me"
                )
            )
            await nc_mcp.call("trash_collective_page", collective_id=coll["id"], page_id=page["id"])
            result = await nc_mcp.call("restore_collective_page", collective_id=coll["id"], page_id=page["id"])
            restored = json.loads(result)
            assert restored["title"] == "Restore Me"
            pages = json.loads(await nc_mcp.call("get_collective_pages", collective_id=coll["id"], limit=200))["data"]
            assert page["id"] in [p["id"] for p in pages]
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_permanent_delete_page(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "permdelpg")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Delete Me"
                )
            )
            await nc_mcp.call("trash_collective_page", collective_id=coll["id"], page_id=page["id"])
            result = await nc_mcp.call("delete_collective_page", collective_id=coll["id"], page_id=page["id"])
            assert "deleted" in result.lower()
        finally:
            await _destroy_collective(nc_mcp, coll["id"])


class TestCollectivePermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_collectives", limit=200)
        assert isinstance(json.loads(result)["data"], list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("create_collective", name="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_trash(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("trash_collective", collective_id=1)

    @pytest.mark.asyncio
    async def test_write_allows_create_but_blocks_trash(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call("create_collective", name=f"{UNIQUE}-perm")
        coll = json.loads(result)
        try:
            with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
                await nc_mcp_write.call("trash_collective", collective_id=coll["id"])
        finally:
            client = nc_mcp_write.client
            with contextlib.suppress(Exception):
                await client.ocs_delete(f"apps/collectives/api/v1.0/collectives/{coll['id']}")
            with contextlib.suppress(Exception):
                await client.ocs_delete(f"apps/collectives/api/v1.0/collectives/trash/{coll['id']}")
