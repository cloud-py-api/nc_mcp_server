"""Integration tests for Circles (Teams) tools against a real Nextcloud instance."""

import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Any

import niquests
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient, NextcloudError
from nc_mcp_server.config import Config
from nc_mcp_server.state import get_client, get_config, set_state

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

CIRCLE_TEST_USER = "mcp-circle-test-user"
CIRCLE_TEST_PWD = "mcp-Circle-Test-PWD-9X!"


@pytest.fixture(scope="session")
def _circles_available(_cleanup_config: Config) -> bool:
    """Probe whether the Circles app is enabled on the target Nextcloud.

    Sync-only check so we can return a plain bool at session scope without
    fighting pytest-asyncio's per-test event loop.
    """
    try:
        resp = niquests.get(
            f"{_cleanup_config.nextcloud_url}/ocs/v2.php/apps/circles/circles",
            auth=(_cleanup_config.user, _cleanup_config.password),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=5,
        )
    except (OSError, niquests.exceptions.RequestException):
        return False
    return resp.ok


@pytest.fixture(autouse=True)
def _skip_if_no_circles(_circles_available: bool) -> None:
    """Skip every Circles test when the app isn't enabled on the target instance."""
    if not _circles_available:
        pytest.skip("Circles app is not enabled on this Nextcloud instance")


@pytest.fixture
async def circle_peer(nc_config: Config) -> AsyncGenerator[str]:
    """Ensure a second user exists for membership tests. Yields the userid."""
    client = NextcloudClient(nc_config)
    with contextlib.suppress(Exception):
        await client.ocs_post(
            "cloud/users",
            data={"userid": CIRCLE_TEST_USER, "password": CIRCLE_TEST_PWD},
        )
    yield CIRCLE_TEST_USER
    with contextlib.suppress(Exception):
        await client.ocs_delete(f"cloud/users/{CIRCLE_TEST_USER}")
    await client.close()


@contextlib.asynccontextmanager
async def _as_peer(user_id: str, password: str) -> AsyncGenerator[None]:
    """Temporarily swap the global state so tool calls authenticate as the given user.

    Restores the prior state on exit. Tools (`create_server`, `get_client`) read the
    client from module-global state, so swapping it lets the existing McpTestHelper
    exercise real tools under a different user's credentials without needing a
    second server instance.
    """
    admin_client = get_client()
    admin_config = get_config()
    peer_config = Config(
        nextcloud_url=admin_config.nextcloud_url,
        user=user_id,
        password=password,
        permission_level=admin_config.permission_level,
    )
    peer_config.validate()
    peer_client = NextcloudClient(peer_config)
    set_state(peer_client, peer_config)
    try:
        yield
    finally:
        set_state(admin_client, admin_config)
        await peer_client.close()


async def _make_circle(nc_mcp: McpTestHelper, name: str) -> dict[str, Any]:
    """Create a circle and return its dict."""
    created: dict[str, Any] = json.loads(await nc_mcp.call("create_circle", name=name))
    return created


class TestListCircles:
    @pytest.mark.asyncio
    async def test_list_includes_created(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-list")
        circles: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circles"))
        assert isinstance(circles, list)
        assert any(c["id"] == circle["id"] for c in circles)

    @pytest.mark.asyncio
    async def test_list_pagination(self, nc_mcp: McpTestHelper) -> None:
        for i in range(3):
            await _make_circle(nc_mcp, f"mcp-test-circle-page-{i}")
        circles: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circles", limit=2))
        assert len(circles) == 2

    @pytest.mark.asyncio
    async def test_list_offset_skips_entries(self, nc_mcp: McpTestHelper) -> None:
        """limit=1 + offset=1 skips the first entry — sanity-check pagination offset."""
        for i in range(3):
            await _make_circle(nc_mcp, f"mcp-test-circle-offset-{i}")
        page_one: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circles", limit=1))
        page_two: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circles", limit=1, offset=1))
        assert len(page_one) == 1
        assert len(page_two) == 1
        assert page_one[0]["id"] != page_two[0]["id"]


class TestCircleLifecycle:
    @pytest.mark.asyncio
    async def test_create_sets_owner_to_caller(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_circle", name="mcp-test-circle-create"))
        assert created["name"] == "mcp-test-circle-create"
        assert created["initiator"]["userId"] == get_config().user
        assert created["initiator"]["level"] == 9  # owner

    @pytest.mark.asyncio
    async def test_get_returns_circle(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-get")
        fetched = json.loads(await nc_mcp.call("get_circle", circle_id=circle["id"]))
        assert fetched["id"] == circle["id"]
        assert fetched["name"] == "mcp-test-circle-get"

    @pytest.mark.asyncio
    async def test_get_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("get_circle", circle_id="nonexistent-id-xxxxxxxxxxxxxxxx")

    @pytest.mark.asyncio
    async def test_update_name(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-rename-old")
        await nc_mcp.call(
            "update_circle_name",
            circle_id=circle["id"],
            name="mcp-test-circle-rename-new",
        )
        fetched = json.loads(await nc_mcp.call("get_circle", circle_id=circle["id"]))
        assert fetched["name"] == "mcp-test-circle-rename-new"

    @pytest.mark.asyncio
    async def test_update_description(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-desc")
        await nc_mcp.call(
            "update_circle_description",
            circle_id=circle["id"],
            description="hello world",
        )
        fetched = json.loads(await nc_mcp.call("get_circle", circle_id=circle["id"]))
        assert fetched["description"] == "hello world"

    @pytest.mark.asyncio
    async def test_update_config_flags(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-config")
        # 24 = VISIBLE (8) | OPEN (16)
        await nc_mcp.call("update_circle_config", circle_id=circle["id"], config=24)
        fetched = json.loads(await nc_mcp.call("get_circle", circle_id=circle["id"]))
        assert fetched["config"] == 24

    @pytest.mark.asyncio
    async def test_delete_removes_circle(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-delete")
        result = json.loads(await nc_mcp.call("delete_circle", circle_id=circle["id"]))
        assert result == {"deleted_circle_id": circle["id"]}
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("get_circle", circle_id=circle["id"])


class TestMembers:
    @pytest.mark.asyncio
    async def test_owner_present_in_members(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-owner")
        members: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle["id"]))
        assert isinstance(members, list)
        owner_id = get_config().user
        owner_member = next((m for m in members if m.get("userId") == owner_id), None)
        assert owner_member is not None, "owner should be in members"
        assert owner_member["level"] == 9

    @pytest.mark.asyncio
    async def test_add_and_list_member(self, nc_mcp: McpTestHelper, circle_peer: str) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-add-member")
        added = json.loads(await nc_mcp.call("add_circle_member", circle_id=circle["id"], user_id=circle_peer))
        assert added["userId"] == circle_peer
        members: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle["id"]))
        assert any(m.get("userId") == circle_peer for m in members)

    @pytest.mark.asyncio
    async def test_add_member_rejects_bad_type(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-bad-type")
        with pytest.raises((ToolError, ValueError), match=r"[Ii]nvalid member_type"):
            await nc_mcp.call(
                "add_circle_member",
                circle_id=circle["id"],
                user_id="anyone",
                member_type="bogus",
            )

    @pytest.mark.asyncio
    async def test_update_member_level(self, nc_mcp: McpTestHelper, circle_peer: str) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-promote")
        added = json.loads(await nc_mcp.call("add_circle_member", circle_id=circle["id"], user_id=circle_peer))
        await nc_mcp.call(
            "update_circle_member_level",
            circle_id=circle["id"],
            member_id=added["id"],
            level="moderator",
        )
        members: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle["id"]))
        peer = next(m for m in members if m.get("userId") == circle_peer)
        assert peer["level"] == 4

    @pytest.mark.asyncio
    async def test_update_level_rejects_bad_value(self, nc_mcp: McpTestHelper, circle_peer: str) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-bad-level")
        added = json.loads(await nc_mcp.call("add_circle_member", circle_id=circle["id"], user_id=circle_peer))
        with pytest.raises((ToolError, ValueError), match=r"[Ii]nvalid level"):
            await nc_mcp.call(
                "update_circle_member_level",
                circle_id=circle["id"],
                member_id=added["id"],
                level="superuser",
            )

    @pytest.mark.asyncio
    async def test_remove_member(self, nc_mcp: McpTestHelper, circle_peer: str) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-kick")
        added = json.loads(await nc_mcp.call("add_circle_member", circle_id=circle["id"], user_id=circle_peer))
        result = json.loads(
            await nc_mcp.call(
                "remove_circle_member",
                circle_id=circle["id"],
                member_id=added["id"],
            )
        )
        assert result == {"removed_member_id": added["id"]}
        members: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle["id"]))
        assert not any(m.get("userId") == circle_peer for m in members)

    @pytest.mark.asyncio
    async def test_full_details_adds_circle_field(self, nc_mcp: McpTestHelper) -> None:
        """full_details=True returns the extra `circle` field on each member entry."""
        circle = await _make_circle(nc_mcp, "mcp-test-circle-full-details")
        default_members: list[dict[str, Any]] = json.loads(
            await nc_mcp.call("list_circle_members", circle_id=circle["id"])
        )
        full_members: list[dict[str, Any]] = json.loads(
            await nc_mcp.call("list_circle_members", circle_id=circle["id"], full_details=True)
        )
        assert default_members, "owner should be present in default response"
        assert full_members, "owner should be present in full-details response"
        assert "circle" not in default_members[0]
        assert "circle" in full_members[0]

    @pytest.mark.asyncio
    async def test_promote_to_owner_transfers_and_demotes_caller(self, nc_mcp: McpTestHelper, circle_peer: str) -> None:
        """Promoting a member to owner transfers ownership; previous owner becomes admin (level=8)."""
        circle = await _make_circle(nc_mcp, "mcp-test-circle-xfer-owner")
        added = json.loads(await nc_mcp.call("add_circle_member", circle_id=circle["id"], user_id=circle_peer))
        await nc_mcp.call(
            "update_circle_member_level",
            circle_id=circle["id"],
            member_id=added["id"],
            level="owner",
        )
        members: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle["id"]))
        peer_level = next(m["level"] for m in members if m.get("userId") == circle_peer)
        caller_level = next(m["level"] for m in members if m.get("userId") == get_config().user)
        assert peer_level == 9, "promoted member should be owner (9)"
        assert caller_level == 8, "previous owner should be demoted to admin (8)"
        # peer is now the only owner — admin cleanup can't destroy it. Tear down as peer.
        async with _as_peer(circle_peer, CIRCLE_TEST_PWD):
            await nc_mcp.call("delete_circle", circle_id=circle["id"])


class TestJoinLeave:
    @pytest.mark.asyncio
    async def test_join_open_circle(self, nc_mcp: McpTestHelper, circle_peer: str) -> None:
        """Admin creates an OPEN circle; peer calls join_circle and appears in members."""
        circle = await _make_circle(nc_mcp, "mcp-test-circle-open-join")
        # 24 = VISIBLE(8) | OPEN(16) — required for peer to join via join_circle
        await nc_mcp.call("update_circle_config", circle_id=circle["id"], config=24)
        async with _as_peer(circle_peer, CIRCLE_TEST_PWD):
            joined = json.loads(await nc_mcp.call("join_circle", circle_id=circle["id"]))
            assert joined["userId"] == circle_peer
            assert joined["status"] == "Member"
        members: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle["id"]))
        assert any(m.get("userId") == circle_peer for m in members)

    @pytest.mark.asyncio
    async def test_join_non_open_fails(self, nc_mcp: McpTestHelper, circle_peer: str) -> None:
        """Joining a circle without the OPEN flag is rejected by the server."""
        circle = await _make_circle(nc_mcp, "mcp-test-circle-closed-join")
        # default config (no OPEN flag) — peer must not be able to join
        async with _as_peer(circle_peer, CIRCLE_TEST_PWD):
            with pytest.raises((ToolError, NextcloudError)):
                await nc_mcp.call("join_circle", circle_id=circle["id"])

    @pytest.mark.asyncio
    async def test_leave_as_member(self, nc_mcp: McpTestHelper, circle_peer: str) -> None:
        """Admin adds peer; peer calls leave_circle; peer is gone from member list."""
        circle = await _make_circle(nc_mcp, "mcp-test-circle-leave")
        await nc_mcp.call("add_circle_member", circle_id=circle["id"], user_id=circle_peer)
        async with _as_peer(circle_peer, CIRCLE_TEST_PWD):
            await nc_mcp.call("leave_circle", circle_id=circle["id"])
        members: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle["id"]))
        assert not any(m.get("userId") == circle_peer for m in members)

    @pytest.mark.asyncio
    async def test_sole_owner_leave_destroys_circle(self, nc_mcp: McpTestHelper) -> None:
        """Sole-owner leave silently destroys the circle — documented server behavior."""
        circle = await _make_circle(nc_mcp, "mcp-test-circle-sole-leave")
        await nc_mcp.call("leave_circle", circle_id=circle["id"])
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("get_circle", circle_id=circle["id"])


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_list(self, nc_mcp: McpTestHelper) -> None:
        """Search should return a JSON list regardless of matches."""
        await _make_circle(nc_mcp, "mcp-test-circle-search")
        results = json.loads(await nc_mcp.call("search_circles", term="admin"))
        assert isinstance(results, list)


class TestCirclesPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_circles")
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("create_circle", name="mcp-test-circle-perm")

    @pytest.mark.asyncio
    async def test_read_only_blocks_delete(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("delete_circle", circle_id="doesnt-matter")

    @pytest.mark.asyncio
    async def test_write_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_circle", circle_id="doesnt-matter")

    @pytest.mark.asyncio
    async def test_write_blocks_leave(self, nc_mcp_write: McpTestHelper) -> None:
        """leave_circle is DESTRUCTIVE because sole-owner leave destroys the circle."""
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("leave_circle", circle_id="doesnt-matter")

    @pytest.mark.asyncio
    async def test_write_allows_create_and_update(self, nc_mcp_write: McpTestHelper) -> None:
        created = json.loads(await nc_mcp_write.call("create_circle", name="mcp-test-circle-perm-write"))
        await nc_mcp_write.call(
            "update_circle_description",
            circle_id=created["id"],
            description="desc",
        )
        fetched = json.loads(await nc_mcp_write.call("get_circle", circle_id=created["id"]))
        assert fetched["description"] == "desc"
