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
        await nc_mcp.call("delete_collective", collective_id=collective_id, delete_team=True)


async def _teams(nc_mcp: McpTestHelper) -> list[dict[str, Any]]:
    teams: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circles", limit=200))
    return teams


async def _team_names(nc_mcp: McpTestHelper) -> list[str]:
    return [str(team.get("name")) for team in await _teams(nc_mcp)]


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

    @pytest.mark.asyncio
    async def test_title_with_url_characters(self, nc_mcp: McpTestHelper) -> None:
        """A "#" or "%" in the title ends up in the file name, which must still be read."""
        coll = await _create_collective(nc_mcp, "urlchars")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Release #5 at 100%"
                )
            )
            await _write_page(nc_mcp, coll["id"], page["id"], "release notes")
            result = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=page["id"]))
            assert result["content"] == "release notes"
            assert result["size"] == len("release notes")
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
    async def test_permanent_delete_with_team(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "permdel")
        try:
            assert coll["name"] in await _team_names(nc_mcp)
            await nc_mcp.call("trash_collective", collective_id=coll["id"])
            result = await nc_mcp.call("delete_collective", collective_id=coll["id"], delete_team=True)
            assert result.endswith("deleted permanently with its team.")
            assert coll["name"] not in await _team_names(nc_mcp)
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_permanent_delete_keeps_the_team_by_default(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "keepteam")
        try:
            await nc_mcp.call("trash_collective", collective_id=coll["id"])
            await nc_mcp.call("delete_collective", collective_id=coll["id"])
            assert coll["name"] in await _team_names(nc_mcp)
        finally:
            await _destroy_collective(nc_mcp, coll["id"])
            for team in await _teams(nc_mcp):
                if team.get("name") == coll["name"]:
                    with contextlib.suppress(Exception):
                        await nc_mcp.call("delete_circle", circle_id=team["id"])


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


class TestEditPages:
    @pytest.mark.asyncio
    async def test_create_with_content(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "createtext")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page",
                    collective_id=coll["id"],
                    parent_id=landing_id,
                    title="Notes",
                    content="# Hi",
                )
            )
            assert (page["parent_id"], page["size"]) == (landing_id, len("# Hi"))
            read = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=page["id"]))
            assert read["content"] == "# Hi"
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_update_content_title_and_emoji(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "edit")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Draft"
                )
            )
            updated = json.loads(
                await nc_mcp.call(
                    "update_collective_page",
                    collective_id=coll["id"],
                    page_id=page["id"],
                    content="Grüße 🙂",
                    title="Final",
                    emoji="📘",
                )
            )
            assert (updated["title"], updated["emoji"], updated["file_name"]) == ("Final", "📘", "Final.md")
            assert updated["last_user_id"] == "admin"
            read = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=page["id"]))
            assert read["content"] == "Grüße 🙂"
            cleared = json.loads(
                await nc_mcp.call("update_collective_page", collective_id=coll["id"], page_id=page["id"], emoji="")
            )
            assert cleared["emoji"] is None
            reread = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=page["id"]))
            assert reread["emoji"] is None
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_update_landing_page_and_a_parent(self, nc_mcp: McpTestHelper) -> None:
        """Pages with subpages are Readme.md files in their own folder."""
        coll = await _create_collective(nc_mcp, "readme")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            parent = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Parent"
                )
            )
            await nc_mcp.call("create_collective_page", collective_id=coll["id"], parent_id=parent["id"], title="Child")
            for page_id, text in ((landing_id, "welcome"), (parent["id"], "parent text")):
                await nc_mcp.call("update_collective_page", collective_id=coll["id"], page_id=page_id, content=text)
                read = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=page_id))
                assert read["content"] == text
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_invalid_updates(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "badedit")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            with pytest.raises(ToolError, match="at least one of content, title or emoji"):
                await nc_mcp.call("update_collective_page", collective_id=coll["id"], page_id=landing_id)
            with pytest.raises(ToolError, match="title cannot be empty"):
                await nc_mcp.call("update_collective_page", collective_id=coll["id"], page_id=landing_id, title=" ")
            with pytest.raises(ToolError, match="landing page cannot be renamed"):
                await nc_mcp.call(
                    "update_collective_page", collective_id=coll["id"], page_id=landing_id, content="x", title="New"
                )
            read = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=landing_id))
            assert read["content"] != "x"  # refused before anything was written
        finally:
            await _destroy_collective(nc_mcp, coll["id"])


class TestMovePages:
    @pytest.mark.asyncio
    async def test_move_and_copy_within_a_collective(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "move")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page",
                    collective_id=coll["id"],
                    parent_id=landing_id,
                    title="Doc",
                    content="body",
                )
            )
            folder = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Folder"
                )
            )
            moved = json.loads(
                await nc_mcp.call(
                    "move_collective_page", collective_id=coll["id"], page_id=page["id"], parent_id=folder["id"]
                )
            )
            assert (moved["id"], moved["parent_id"]) == (page["id"], folder["id"])
            copied = json.loads(
                await nc_mcp.call(
                    "move_collective_page",
                    collective_id=coll["id"],
                    page_id=page["id"],
                    parent_id=landing_id,
                    copy=True,
                )
            )
            assert copied["id"] != page["id"]
            assert copied["parent_id"] == landing_id
            read = json.loads(await nc_mcp.call("get_collective_page", collective_id=coll["id"], page_id=copied["id"]))
            assert read["content"] == "body"
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_copy_to_another_collective(self, nc_mcp: McpTestHelper) -> None:
        source = await _create_collective(nc_mcp, "from")
        target = await _create_collective(nc_mcp, "to")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, source["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page",
                    collective_id=source["id"],
                    parent_id=landing_id,
                    title="Shared",
                    content="x",
                )
            )
            target_landing = await _get_landing_page_id(nc_mcp, target["id"])
            copied = json.loads(
                await nc_mcp.call(
                    "move_collective_page",
                    collective_id=source["id"],
                    page_id=page["id"],
                    parent_id=target_landing,
                    to_collective_id=target["id"],
                    copy=True,
                )
            )
            assert (copied["title"], copied["parent_id"]) == ("Shared", target_landing)
            assert copied["id"] != page["id"]
            read = json.loads(
                await nc_mcp.call("get_collective_page", collective_id=target["id"], page_id=copied["id"])
            )
            assert read["content"] == "x"
            still = json.loads(await nc_mcp.call("get_collective_pages", collective_id=source["id"], limit=200))["data"]
            assert "Shared" in [p["title"] for p in still]
        finally:
            await _destroy_collective(nc_mcp, source["id"])
            await _destroy_collective(nc_mcp, target["id"])

    @pytest.mark.asyncio
    async def test_move_to_another_collective(self, nc_mcp: McpTestHelper) -> None:
        source = await _create_collective(nc_mcp, "mvfrom")
        target = await _create_collective(nc_mcp, "mvto")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, source["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=source["id"], parent_id=landing_id, title="Go"
                )
            )
            moved = json.loads(
                await nc_mcp.call(
                    "move_collective_page",
                    collective_id=source["id"],
                    page_id=page["id"],
                    parent_id=0,
                    to_collective_id=target["id"],
                )
            )
            assert (moved["id"], moved["title"]) == (page["id"], "Go")
            source_pages = json.loads(await nc_mcp.call("get_collective_pages", collective_id=source["id"]))["data"]
            assert "Go" not in [p["title"] for p in source_pages]
        finally:
            await _destroy_collective(nc_mcp, source["id"])
            await _destroy_collective(nc_mcp, target["id"])

    @pytest.mark.asyncio
    async def test_page_cannot_go_under_its_own_child(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "loop")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            parent = json.loads(
                await nc_mcp.call("create_collective_page", collective_id=coll["id"], parent_id=landing_id, title="Up")
            )
            child = json.loads(
                await nc_mcp.call(
                    "create_collective_page", collective_id=coll["id"], parent_id=parent["id"], title="Down"
                )
            )
            with pytest.raises(ToolError):
                await nc_mcp.call(
                    "move_collective_page", collective_id=coll["id"], page_id=parent["id"], parent_id=child["id"]
                )
        finally:
            await _destroy_collective(nc_mcp, coll["id"])


class TestSearchPages:
    @pytest.mark.asyncio
    async def test_search_answers_with_pages(self, nc_mcp: McpTestHelper) -> None:
        """Results depend on a background indexing job, so only the request and its shape are checked."""
        coll = await _create_collective(nc_mcp, "search")
        try:
            result = json.loads(await nc_mcp.call("search_collective_pages", collective_id=coll["id"], query="welcome"))
            assert isinstance(result, list)
        finally:
            await _destroy_collective(nc_mcp, coll["id"])

    @pytest.mark.asyncio
    async def test_recent_pages_include_an_edited_one(self, nc_mcp: McpTestHelper) -> None:
        coll = await _create_collective(nc_mcp, "recent")
        try:
            landing_id = await _get_landing_page_id(nc_mcp, coll["id"])
            page = json.loads(
                await nc_mcp.call(
                    "create_collective_page",
                    collective_id=coll["id"],
                    parent_id=landing_id,
                    title="Fresh",
                    content="new",
                )
            )
            recent = json.loads(await nc_mcp.call("list_recent_collective_pages", limit=100))
            match = next(p for p in recent if p["id"] == page["id"])
            assert match["collective_id"] == coll["id"]
            only = json.loads(await nc_mcp.call("list_recent_collective_pages", query="Fresh", limit=100))
            assert page["id"] in [p["id"] for p in only]
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
