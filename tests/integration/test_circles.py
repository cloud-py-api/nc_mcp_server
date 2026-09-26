"""Integration tests for Circles (Teams) tools against a real Nextcloud instance."""

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncGenerator, Callable
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
# Circles applies member additions, removals, joins and leaves, and circle deletions, in a loopback
# request that runs after the API call has returned, so tests wait for the result to show up.
CIRCLES_ASYNC_TIMEOUT = 20.0


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
    """Create a second user for membership tests and yield its user ID.

    Every test gets a new user: Circles drops a deleted user's memberships asynchronously, so a
    user re-created under the same ID could resolve to the deleted one ("singleId not found").
    """
    user_id = f"{CIRCLE_TEST_USER}-{uuid.uuid4().hex[:8]}"
    client = NextcloudClient(nc_config)
    await client.ocs_post("cloud/users", data={"userid": user_id, "password": CIRCLE_TEST_PWD})
    yield user_id
    with contextlib.suppress(Exception):
        await client.ocs_delete(f"cloud/users/{user_id}")
    await client.close()


@contextlib.asynccontextmanager
async def _as_peer(user_id: str, password: str) -> AsyncGenerator[None]:
    """Temporarily swap the global state so tool calls authenticate as the given user.

    Restores the prior state on exit. Tools (`create_server`, `get_client`) read the
    client from module-global state, so swapping it lets the existing McpTestHelper
    exercise real tools under a different user's credentials without needing a
    second server instance.

    Concurrency: this mutates a process-wide singleton. Only safe under sequential
    test execution (pytest-asyncio's default per-test event loop). If this file
    is ever run under pytest-xdist or similar parallel runners, calls from other
    tests would race against the swap. Add an asyncio.Lock or a fresh
    NextcloudClient-per-call path if that ever becomes relevant.
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


def _has_user(user_id: str) -> Callable[[list[dict[str, Any]]], bool]:
    return lambda members: any(m.get("userId") == user_id for m in members)


def _lacks_user(user_id: str) -> Callable[[list[dict[str, Any]]], bool]:
    return lambda members: not _has_user(user_id)(members)


async def _wait_for_members(
    nc_mcp: McpTestHelper, circle_id: str, done: Callable[[list[dict[str, Any]]], bool]
) -> list[dict[str, Any]]:
    """Return the circle's members once done(members) holds, or the last list after the timeout."""
    deadline = time.monotonic() + CIRCLES_ASYNC_TIMEOUT
    while True:
        members: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle_id))
        if done(members) or time.monotonic() > deadline:
            return members
        await asyncio.sleep(0.5)


async def _add_member(nc_mcp: McpTestHelper, circle_id: str, user_id: str) -> dict[str, Any]:
    """Add a user to a circle and wait until Circles has stored the membership."""
    added: dict[str, Any] = json.loads(await nc_mcp.call("add_circle_member", circle_id=circle_id, user_id=user_id))
    await _wait_for_members(nc_mcp, circle_id, _has_user(user_id))
    return added


async def _wait_for_deletion(nc_mcp: McpTestHelper, circle_id: str) -> bool:
    """Return True once get_circle fails for the circle, or False if it still exists after the timeout."""
    deadline = time.monotonic() + CIRCLES_ASYNC_TIMEOUT
    while True:
        try:
            await nc_mcp.call("get_circle", circle_id=circle_id)
        except (ToolError, NextcloudError):
            return True
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.5)


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
    async def test_update_config_rejects_hidden_flag(self, nc_mcp: McpTestHelper) -> None:
        """1024 (CFG_HIDDEN) is in DEF_CFG_SYSTEM_FILTER — blocked via public OCS API.

        Guards the docstring against regressing to claim HIDDEN is user-settable.
        """
        circle = await _make_circle(nc_mcp, "mcp-test-circle-hidden-rej")
        with pytest.raises((ToolError, NextcloudError)):
            await nc_mcp.call("update_circle_config", circle_id=circle["id"], config=1024)

    @pytest.mark.asyncio
    async def test_update_config_auto_adds_open_for_request(self, nc_mcp: McpTestHelper) -> None:
        """REQUEST (64) implies OPEN (16); server stores 80, not 64.

        Documents the auto-mutation noted in the update_circle_config docstring.
        """
        circle = await _make_circle(nc_mcp, "mcp-test-circle-auto-open")
        updated = json.loads(await nc_mcp.call("update_circle_config", circle_id=circle["id"], config=64))
        assert updated["config"] == 80, f"expected server to add OPEN to REQUEST, got {updated['config']}"

    @pytest.mark.asyncio
    async def test_delete_removes_circle(self, nc_mcp: McpTestHelper) -> None:
        circle = await _make_circle(nc_mcp, "mcp-test-circle-delete")
        result = json.loads(await nc_mcp.call("delete_circle", circle_id=circle["id"]))
        assert result == {"deleted_circle_id": circle["id"]}
        assert await _wait_for_deletion(nc_mcp, circle["id"])


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
        members = await _wait_for_members(nc_mcp, circle["id"], _has_user(circle_peer))
        assert _has_user(circle_peer)(members)

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
        added = await _add_member(nc_mcp, circle["id"], circle_peer)
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
        added = await _add_member(nc_mcp, circle["id"], circle_peer)
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
        added = await _add_member(nc_mcp, circle["id"], circle_peer)
        result = json.loads(
            await nc_mcp.call(
                "remove_circle_member",
                circle_id=circle["id"],
                member_id=added["id"],
            )
        )
        assert result == {"removed_member_id": added["id"]}
        members = await _wait_for_members(nc_mcp, circle["id"], _lacks_user(circle_peer))
        assert _lacks_user(circle_peer)(members)

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
        added = await _add_member(nc_mcp, circle["id"], circle_peer)
        try:
            await nc_mcp.call(
                "update_circle_member_level",
                circle_id=circle["id"],
                member_id=added["id"],
                level="owner",
            )
        except ToolError as e:
            if "cannot transfer ownership" not in str(e):
                raise
            # The refusal must leave the circle as it was
            members = json.loads(await nc_mcp.call("list_circle_members", circle_id=circle["id"]))
            levels = {m.get("userId"): m["level"] for m in members}
            assert (levels[get_config().user], levels[circle_peer]) == (9, 1)
            pytest.skip("this server's Circles predates the ownership transfer fix (nextcloud/circles#2916)")
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
        members = await _wait_for_members(nc_mcp, circle["id"], _has_user(circle_peer))
        assert _has_user(circle_peer)(members)

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
        await _add_member(nc_mcp, circle["id"], circle_peer)
        async with _as_peer(circle_peer, CIRCLE_TEST_PWD):
            await nc_mcp.call("leave_circle", circle_id=circle["id"])
        members = await _wait_for_members(nc_mcp, circle["id"], _lacks_user(circle_peer))
        assert _lacks_user(circle_peer)(members)

    @pytest.mark.asyncio
    async def test_sole_owner_leave_destroys_circle(self, nc_mcp: McpTestHelper) -> None:
        """Sole-owner leave silently destroys the circle — documented server behavior."""
        circle = await _make_circle(nc_mcp, "mcp-test-circle-sole-leave")
        await nc_mcp.call("leave_circle", circle_id=circle["id"])
        assert await _wait_for_deletion(nc_mcp, circle["id"])


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
