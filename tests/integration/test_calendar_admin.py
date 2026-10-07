"""Calendar administration against a real Nextcloud: create (optionally shared with a group),
share/unshare, public link, delete; shares are visible to the owner only."""

import contextlib
import json
import secrets
from collections.abc import AsyncGenerator

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_server
from nc_mcp_server.state import get_client

from .conftest import McpTestHelper, _get_integration_config

pytestmark = pytest.mark.integration


def _server(config: Config) -> McpTestHelper:
    return McpTestHelper(create_server(config), get_client())


@pytest.fixture
async def member_and_group() -> AsyncGenerator[tuple[Config, Config, str]]:
    """admin (owner) and a member in a fresh group."""
    admin_config = _get_integration_config()
    admin = NextcloudClient(admin_config)
    suffix = secrets.token_hex(4)
    group = f"mcp-grp-{suffix}"
    user_id = f"mcp-member-{suffix}"
    password = f"Mcp-{secrets.token_hex(10)}-x"
    try:
        await admin.ocs_post("cloud/groups", data={"groupid": group})
        await admin.ocs_post("cloud/users", data={"userid": user_id, "password": password, "groups[]": group})
        member = Config(
            nextcloud_url=admin_config.nextcloud_url,
            user=user_id,
            password=password,
            permission_level=PermissionLevel.DESTRUCTIVE,
        )
        yield admin_config, member, group
    finally:
        with contextlib.suppress(Exception):
            await admin.ocs_delete(f"cloud/users/{user_id}")
        with contextlib.suppress(Exception):
            await admin.ocs_delete(f"cloud/groups/{group}")
        await admin.close()


class TestCalendarAdmin:
    @pytest.mark.asyncio
    async def test_lifecycle(self, member_and_group: tuple[Config, Config, str]) -> None:
        admin_config, member, group = member_and_group
        owner = _server(admin_config)
        created = json.loads(
            await owner.call("create_calendar", name="Sommerfest Ü 2027", share_with_group=group, group_write=True)
        )
        cal_id = created["id"]
        try:
            assert cal_id.startswith("sommerfest-ue-2027")
            assert created["share"] == {"type": "group", "share_with": group, "write": True}
            again = json.loads(await owner.call("create_calendar", name="Sommerfest Ü 2027"))
            assert again["id"] != cal_id, "unique URI"
            await owner.call("delete_calendar", calendar_id=again["id"])

            info = json.loads(await owner.call("get_calendar_shares", calendar_id=cal_id))
            assert info["owned_by_me"] is True
            assert info["shares"] == [{"type": "group", "share_with": group, "write": True}]
            assert info["public_url"] is None

            published = json.loads(await owner.call("publish_calendar", calendar_id=cal_id))
            assert published["public_url"]
            await owner.call("unpublish_calendar", calendar_id=cal_id)
            assert json.loads(await owner.call("get_calendar_shares", calendar_id=cal_id))["public_url"] is None

            await owner.call("share_calendar", calendar_id=cal_id, share_with=member.user, share_type="user")
            shares = json.loads(await owner.call("get_calendar_shares", calendar_id=cal_id))["shares"]
            assert {"type": "user", "share_with": member.user, "write": False} in shares
            await owner.call("unshare_calendar", calendar_id=cal_id, share_with=member.user, share_type="user")
            shares = json.loads(await owner.call("get_calendar_shares", calendar_id=cal_id))["shares"]
            assert all(s["share_with"] != member.user for s in shares)
        finally:
            await owner.client.close()

        sharee = _server(member)
        shared_id = f"{cal_id}_shared_by_{admin_config.user}"
        try:
            ids = [c["id"] for c in json.loads(await sharee.call("list_calendars"))]
            assert shared_id in ids, "group share visible to the member"
            info = json.loads(await sharee.call("get_calendar_shares", calendar_id=shared_id))
            assert info["owned_by_me"] is False
            assert info["shares"] is None, "shares hidden from sharees"
            with pytest.raises(ToolError):
                await sharee.call("share_calendar", calendar_id=shared_id, share_with="admin", share_type="user")
        finally:
            await sharee.client.close()

        owner = _server(admin_config)
        try:
            with pytest.raises(ToolError, match="Nothing was deleted"):
                await owner.call("delete_calendar", calendar_id=cal_id, expected_name="Anderer")
            await owner.call("delete_calendar", calendar_id=cal_id, expected_name="sommerfest ü 2027")
            ids = [c["id"] for c in json.loads(await owner.call("list_calendars"))]
            assert cal_id not in ids
        finally:
            await owner.client.close()

    @pytest.mark.asyncio
    async def test_rejects_bad_input(self, nc_mcp: McpTestHelper) -> None:
        for kwargs in ({"name": ""}, {"name": "x", "color": "red"}, {"name": "x", "share_with_group": "a/b"}):
            with pytest.raises(ToolError):
                await nc_mcp.call("create_calendar", **kwargs)
        for cal in ("../x", "inbox", "a/b"):
            with pytest.raises(ToolError, match="Invalid calendar_id"):
                await nc_mcp.call("delete_calendar", calendar_id=cal)
        with pytest.raises(ToolError, match="share_type"):
            await nc_mcp.call("share_calendar", calendar_id="personal", share_with="x", share_type="email")
