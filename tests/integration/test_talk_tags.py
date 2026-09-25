"""Integration tests for conversation tags, presets and preserving conversations."""

import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


@pytest.fixture
async def room(nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
    data = json.loads(await nc_mcp.call("create_conversation", room_type=2, name="mcp-test-tags"))
    token = str(data["token"])
    yield token
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{token}/preserve")
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{token}")


@pytest.fixture
async def tags(nc_mcp: McpTestHelper) -> AsyncGenerator[list[dict[str, Any]]]:
    """Two fresh tags; any tag named mcp-test-* is deleted afterwards."""
    created = [
        json.loads(await nc_mcp.call("create_conversation_tag", name=f"mcp-test-{label}-{uuid.uuid4().hex[:6]}"))
        for label in ("work", "later")
    ]
    yield created
    mine = {tag["id"] for tag in created}
    for tag in json.loads(await nc_mcp.call("list_conversation_tags")):
        if tag["id"] in mine or tag["name"].startswith("mcp-test-"):
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_conversation_tag", tag_id=tag["id"])


async def _features(nc_mcp: McpTestHelper) -> list[str]:
    capabilities = await nc_mcp.client.ocs_get("cloud/capabilities")
    features: list[str] = capabilities["capabilities"]["spreed"]["features"]
    return features


class TestTags:
    @pytest.mark.asyncio
    async def test_lifecycle(self, nc_mcp: McpTestHelper, room: str, tags: list[dict[str, Any]]) -> None:
        work, later = tags
        assert work["type"] == "custom"
        listed = json.loads(await nc_mcp.call("list_conversation_tags"))
        assert {work["id"], later["id"]} <= {t["id"] for t in listed}
        assert "favorites" in [t["type"] for t in listed]

        tagged = json.loads(await nc_mcp.call("set_conversation_tags", token=room, tag_ids=[work["id"], later["id"]]))
        assert sorted(tagged["tag_ids"]) == sorted([work["id"], later["id"]])
        tagged = json.loads(await nc_mcp.call("set_conversation_tags", token=room, tag_ids=[later["id"]]))
        assert tagged["tag_ids"] == [later["id"]]
        assert json.loads(await nc_mcp.call("get_conversation", token=room))["tag_ids"] == [later["id"]]

        renamed = json.loads(await nc_mcp.call("rename_conversation_tag", tag_id=later["id"], name="mcp-test-renamed"))
        assert (renamed["id"], renamed["name"]) == (later["id"], "mcp-test-renamed")
        assert json.loads(await nc_mcp.call("get_conversation", token=room))["tag_ids"] == [later["id"]]

        await nc_mcp.call("delete_conversation_tag", tag_id=later["id"])
        assert later["id"] not in [t["id"] for t in json.loads(await nc_mcp.call("list_conversation_tags"))]
        assert json.loads(await nc_mcp.call("get_conversation", token=room))["tag_ids"] == []

    @pytest.mark.asyncio
    async def test_clear(self, nc_mcp: McpTestHelper, room: str, tags: list[dict[str, Any]]) -> None:
        await nc_mcp.call("set_conversation_tags", token=room, tag_ids=[tags[0]["id"]])
        cleared = json.loads(await nc_mcp.call("set_conversation_tags", token=room, tag_ids=[]))
        assert cleared["tag_ids"] == []


class TestPresets:
    @pytest.mark.asyncio
    async def test_create_from_a_preset(self, nc_mcp: McpTestHelper) -> None:
        presets = json.loads(await nc_mcp.call("list_conversation_presets"))
        default = next(p for p in presets if p["identifier"] == "default")
        assert default["settings"]["roomType"] == 2
        assert "forced" not in [p["identifier"] for p in presets]
        created = json.loads(
            await nc_mcp.call(
                "create_conversation",
                room_type=2,
                name="mcp-test-preset",
                description="From a preset",
                preset="default",
            )
        )
        try:
            assert (created["type"], created["description"]) == ("group", "From a preset")
        finally:
            await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{created['token']}")


class TestPreserve:
    @pytest.mark.asyncio
    async def test_preserve_and_release(self, nc_mcp: McpTestHelper, room: str) -> None:
        if "preserve-conversation" not in await _features(nc_mcp):
            with pytest.raises(ToolError, match=r"needs Talk 25 \(Nextcloud 35\)"):
                await nc_mcp.call("update_conversation", token=room, preserved=True)
            return
        preserved = json.loads(await nc_mcp.call("update_conversation", token=room, preserved=True))
        assert (preserved["is_preserved"], preserved["can_delete"]) == (True, False)
        with pytest.raises(ToolError, match="preserved"):
            await nc_mcp.call("delete_conversation", token=room)
        with pytest.raises(ToolError, match="preserved"):
            await nc_mcp.call("update_conversation", token=room, public=True)
        # Released first, so the public change in the same call goes through
        released = json.loads(await nc_mcp.call("update_conversation", token=room, preserved=False, public=True))
        assert (released["is_preserved"], released["can_delete"], released["type"]) == (False, True, "public")


class TestTagPermissions:
    @pytest.mark.asyncio
    async def test_read_only_blocks_changes(self, nc_mcp_read_only: McpTestHelper) -> None:
        no_tags: list[str] = []
        calls: tuple[tuple[str, dict[str, Any]], ...] = (
            ("create_conversation_tag", {"name": "x"}),
            ("rename_conversation_tag", {"tag_id": "1", "name": "x"}),
            ("set_conversation_tags", {"token": "x", "tag_ids": no_tags}),
        )
        for tool, args in calls:
            with pytest.raises(ToolError, match=r"[Pp]ermission"):
                await nc_mcp_read_only.call(tool, **args)

    @pytest.mark.asyncio
    async def test_write_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_conversation_tag", tag_id="1")
