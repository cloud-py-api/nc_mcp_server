"""Integration tests for the group tools against a real Nextcloud instance."""

import contextlib
import json
import secrets
import uuid
from collections.abc import AsyncGenerator

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.tools import groups

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


def _group_id(label: str = "") -> str:
    return f"mcp-test-grp-{label}{uuid.uuid4().hex[:8]}"


async def _drop_group(nc_mcp: McpTestHelper, group_id: str) -> None:
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(groups._group_path(group_id))


@pytest.fixture
async def group(nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
    group_id = _group_id()
    await nc_mcp.client.ocs_post("cloud/groups", data={"groupid": group_id})
    yield group_id
    await _drop_group(nc_mcp, group_id)


@pytest.fixture
async def members(nc_mcp: McpTestHelper, group: str) -> AsyncGenerator[list[str]]:
    """Three fresh users, all in the group."""
    users = [f"mcp-test-u-{uuid.uuid4().hex[:8]}" for _ in range(3)]
    for user_id in users:
        await nc_mcp.client.ocs_post(
            "cloud/users", data={"userid": user_id, "password": f"Mcp-{secrets.token_hex(10)}!", "groups[]": group}
        )
    yield sorted(users)
    for user_id in users:
        with contextlib.suppress(Exception):
            await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")


async def _find(nc_mcp: McpTestHelper, group_id: str) -> dict[str, object] | None:
    groups = json.loads(await nc_mcp.call("list_groups", search=group_id))["data"]
    return next((g for g in groups if g["id"] == group_id), None)


class TestListGroups:
    @pytest.mark.asyncio
    async def test_lists_admin_group(self, nc_mcp: McpTestHelper) -> None:
        admin = await _find(nc_mcp, "admin")
        assert admin is not None
        assert admin["user_count"] >= 1  # type: ignore[operator]
        assert isinstance(admin["disabled_user_count"], int)

    @pytest.mark.asyncio
    async def test_counts_members(self, nc_mcp: McpTestHelper, group: str, members: list[str]) -> None:
        found = await _find(nc_mcp, group)
        assert found == {"id": group, "display_name": group, "user_count": 3, "disabled_user_count": 0}
        await nc_mcp.call("set_user_enabled", user_id=members[0], enabled=False)
        found = await _find(nc_mcp, group)
        assert found is not None
        assert (found["user_count"], found["disabled_user_count"]) == (3, 1)

    @pytest.mark.asyncio
    async def test_pagination(self, nc_mcp: McpTestHelper, group: str) -> None:
        first = json.loads(await nc_mcp.call("list_groups", limit=1))
        assert first["pagination"] == {"count": 1, "offset": 0, "limit": 1, "has_more": True}
        second = json.loads(await nc_mcp.call("list_groups", limit=1, offset=1))
        assert second["data"] != first["data"]

    @pytest.mark.asyncio
    async def test_search_without_match(self, nc_mcp: McpTestHelper) -> None:
        result = json.loads(await nc_mcp.call("list_groups", search="mcp-test-grp-no-such-group"))
        assert result["data"] == []


class TestListGroupMembers:
    @pytest.mark.asyncio
    async def test_lists_members(self, nc_mcp: McpTestHelper, group: str, members: list[str]) -> None:
        result = json.loads(await nc_mcp.call("list_group_members", group_id=group))
        assert sorted(result["data"]) == members
        assert result["pagination"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_pagination(self, nc_mcp: McpTestHelper, group: str, members: list[str]) -> None:
        pages = [
            json.loads(await nc_mcp.call("list_group_members", group_id=group, limit=2, offset=offset))
            for offset in (0, 2)
        ]
        assert pages[0]["pagination"]["has_more"] is True
        assert pages[1]["pagination"]["has_more"] is False
        assert sorted(pages[0]["data"] + pages[1]["data"]) == members

    @pytest.mark.asyncio
    async def test_empty_group(self, nc_mcp: McpTestHelper, group: str) -> None:
        assert json.loads(await nc_mcp.call("list_group_members", group_id=group))["data"] == []

    @pytest.mark.asyncio
    async def test_nonexistent_group_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="group could not be found"):
            await nc_mcp.call("list_group_members", group_id="mcp-test-grp-no-such-group")


class TestCreateAndDeleteGroup:
    @pytest.mark.asyncio
    async def test_create_with_display_name(self, nc_mcp: McpTestHelper) -> None:
        group_id = _group_id()
        try:
            assert "created" in await nc_mcp.call("create_group", group_id=group_id, display_name="Sales Team")
            found = await _find(nc_mcp, group_id)
            assert found is not None
            assert found["display_name"] == "Sales Team"
        finally:
            await _drop_group(nc_mcp, group_id)

    @pytest.mark.asyncio
    async def test_special_characters_in_group_id(self, nc_mcp: McpTestHelper) -> None:
        """Group IDs may hold characters that break a raw URL path, and "+" and "%" that a second decode mangles."""
        group_id = _group_id("#1 & co + 50%41 a/b ")
        user_id = f"mcp-test-u-{uuid.uuid4().hex[:8]}"
        password = f"Mcp-{secrets.token_hex(10)}!"
        await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": password})
        try:
            await nc_mcp.call("create_group", group_id=group_id)
            await nc_mcp.call("update_user", user_id=user_id, groups=[group_id])
            members = json.loads(await nc_mcp.call("list_group_members", group_id=group_id))["data"]
            assert members == [user_id]
            await nc_mcp.call("delete_group", group_id=group_id)
            assert await _find(nc_mcp, group_id) is None
            assert json.loads(await nc_mcp.call("get_user", user_id=user_id))["groups"] == []
        finally:
            await _drop_group(nc_mcp, group_id)
            with contextlib.suppress(Exception):
                await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, nc_mcp: McpTestHelper, group: str) -> None:
        with pytest.raises(ToolError, match="group exists"):
            await nc_mcp.call("create_group", group_id=group)

    @pytest.mark.asyncio
    async def test_admin_group_cannot_be_deleted(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="the group does not exist, it is the admin group, or its backend"):
            await nc_mcp.call("delete_group", group_id="admin")
        assert await _find(nc_mcp, "admin") is not None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="the group does not exist, it is the admin group, or its backend"):
            await nc_mcp.call("delete_group", group_id="mcp-test-grp-no-such-group")


class TestGroupPermissions:
    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("create_group", group_id="blocked")

    @pytest.mark.asyncio
    async def test_write_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_group", group_id="admin")
