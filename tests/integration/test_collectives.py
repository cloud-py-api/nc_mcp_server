"""Integration tests for Collectives tools against a real Nextcloud instance."""

import contextlib
import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

UNIQUE = "mcp-test-coll"


async def _create_collective(nc_mcp: McpTestHelper, suffix: str = "") -> dict[str, Any]:
    name = f"{UNIQUE}-{suffix}" if suffix else UNIQUE
    result = await nc_mcp.call("create_collective", name=name)
    return json.loads(result)


async def _get_landing_page_id(nc_mcp: McpTestHelper, collective_id: int) -> int:
    result = await nc_mcp.call("get_collective_pages", collective_id=collective_id)
    pages = json.loads(result)
    return pages[0]["id"]


async def _cleanup_collectives(nc_mcp: McpTestHelper) -> None:
    result = await nc_mcp.call("list_collectives")
    for c in json.loads(result):
        if str(c.get("name", "")).startswith(UNIQUE):
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_collective", collective_id=c["id"])


class TestListCollectives:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_collectives")
        data = json.loads(result)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_created_collective_appears_in_list(self, nc_mcp: McpTestHelper) -> None:
        await _cleanup_collectives(nc_mcp)
        coll = await _create_collective(nc_mcp, "list")
        try:
            result = await nc_mcp.call("list_collectives")
            names = [c["name"] for c in json.loads(result)]
            assert coll["name"] in names
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])

    @pytest.mark.asyncio
    async def test_collective_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "fields")
        try:
            result = await nc_mcp.call("list_collectives")
            matches = [c for c in json.loads(result) if c["id"] == coll["id"]]
            assert len(matches) == 1
            c = matches[0]
            assert "id" in c
            assert "name" in c
            assert "level" in c
            assert "can_edit" in c
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])


class TestCreateCollective:
    @pytest.mark.asyncio
    async def test_create_returns_collective(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "create")
        try:
            assert coll["id"] > 0
            assert coll["name"] == f"{UNIQUE}-create"
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])

    @pytest.mark.asyncio
    async def test_create_with_emoji(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_collective", name=f"{UNIQUE}-emoji", emoji="\U0001f4da")
        coll = json.loads(result)
        try:
            assert coll["emoji"] == "\U0001f4da"
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])

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
            await nc_mcp.call("delete_collective", collective_id=coll["id"])


class TestGetCollectivePages:
    @pytest.mark.asyncio
    async def test_new_collective_has_landing_page(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "pages")
        try:
            result = await nc_mcp.call("get_collective_pages", collective_id=coll["id"])
            pages = json.loads(result)
            assert len(pages) >= 1
            assert pages[0]["title"] == "Landing page"
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])

    @pytest.mark.asyncio
    async def test_page_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "pgfields")
        try:
            result = await nc_mcp.call("get_collective_pages", collective_id=coll["id"])
            page = json.loads(result)[0]
            for field in ["id", "title", "timestamp", "file_name"]:
                assert field in page, f"Missing field: {field}"
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])


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
            await nc_mcp.call("delete_collective", collective_id=coll["id"])

    @pytest.mark.asyncio
    async def test_get_nonexistent_page_raises(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "nopage")
        try:
            with pytest.raises(ToolError):
                await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=999999)
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])


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
            await nc_mcp.call("delete_collective", collective_id=coll["id"])

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
            pages = json.loads(await nc_mcp.call("get_collective_pages", collective_id=coll["id"]))
            titles = [p["title"] for p in pages]
            assert "Parent" in titles
            assert "Child" in titles
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])

    @pytest.mark.asyncio
    async def test_create_empty_title_raises(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "emptytitle")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            with pytest.raises((ToolError, ValueError)):
                await nc_mcp.call("create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="")
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])


class TestDeleteCollective:
    @pytest.mark.asyncio
    async def test_delete_removes_collective(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "del")
        await nc_mcp.call("delete_collective", collective_id=coll["id"])
        result = await nc_mcp.call("list_collectives")
        ids = [c["id"] for c in json.loads(result)]
        assert coll["id"] not in ids

    @pytest.mark.asyncio
    async def test_delete_returns_confirmation(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "delconf")
        result = await nc_mcp.call("delete_collective", collective_id=coll["id"])
        assert "deleted" in result.lower()


class TestDeleteCollectivePage:
    @pytest.mark.asyncio
    async def test_delete_page(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "delpg")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="To Delete"
                )
            )
            await nc_mcp.call("delete_collective_page", collective_id=coll["id"], page_id=page["id"])
            pages = json.loads(await nc_mcp.call("get_collective_pages", collective_id=coll["id"]))
            ids = [p["id"] for p in pages]
            assert page["id"] not in ids
        finally:
            await nc_mcp.call("delete_collective", collective_id=coll["id"])


class TestCollectivePermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_collectives")
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("create_collective", name="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_delete(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("delete_collective", collective_id=1)

    @pytest.mark.asyncio
    async def test_write_allows_create_but_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call("create_collective", name=f"{UNIQUE}-perm")
        coll = json.loads(result)
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_write.call("delete_collective", collective_id=coll["id"])
        client = nc_mcp_write.client
        await client.ocs_delete(f"apps/collectives/api/v1.0/collectives/{coll['id']}")
        await client.ocs_delete(f"apps/collectives/api/v1.0/collectives/trash/{coll['id']}")
