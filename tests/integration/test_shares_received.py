"""Integration tests for shares the current user received: listing, accepting, declining and leaving them."""

import contextlib
import json
import os
import secrets
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.state import get_client, get_config, set_state

from .conftest import TEST_BASE_DIR, McpTestHelper

pytestmark = pytest.mark.integration

SHARES_API = "apps/files_sharing/api/v1/shares"


class UserMcp(McpTestHelper):
    """Calls tools as the user its client logs in as, whoever the last tool call ran as."""

    def __init__(self, helper: McpTestHelper, config: Config) -> None:
        super().__init__(helper.mcp, helper.client)
        self.config = config

    async def call(self, tool_name: str, **kwargs: object) -> str:
        set_state(self.client, self.config)
        return await super().call(tool_name, **kwargs)


@dataclass
class Recipient:
    user_id: str
    mcp: UserMcp
    admin: UserMcp


@pytest.fixture
async def recipient(nc_mcp: McpTestHelper) -> AsyncGenerator[Recipient]:
    """A fresh user who has to accept shares, with an MCP server acting as them.

    Tools called on recipient.mcp run as the recipient; nc_mcp keeps running as the admin sharing with them.
    """
    user_id, password = f"mcp-test-r-{uuid.uuid4().hex[:8]}", f"Mcp-{secrets.token_hex(10)}!"
    admin_client, admin_config = get_client(), get_config()
    config = Config(
        nextcloud_url=admin_config.nextcloud_url,
        user=user_id,
        password=password,
        permission_level=PermissionLevel.DESTRUCTIVE,
    )
    client = NextcloudClient(config)
    await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": password})
    try:
        await client.app_request_json("PUT", "files_sharing/settings/defaultAccept", {"accept": False})
        await nc_mcp.create_test_dir()
        yield Recipient(user_id, UserMcp(McpTestHelper(nc_mcp.mcp, client), config), UserMcp(nc_mcp, admin_config))
    finally:
        set_state(admin_client, admin_config)
        await client.close()
        with contextlib.suppress(Exception):
            await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")


async def _share(nc_mcp: McpTestHelper, name: str, share_type: int, share_with: str) -> str:
    """Upload a file as the admin and share it; returns the share ID."""
    await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/{name}", f"content of {name}")
    data = {"path": f"/{TEST_BASE_DIR}/{name}", "shareType": share_type, "shareWith": share_with}
    share = await nc_mcp.client.ocs_post(SHARES_API, data=data)
    return str(share["id"])


async def _pending(recipient: Recipient) -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in json.loads(await recipient.mcp.call("list_pending_shares"))}


async def _received(recipient: Recipient, **kwargs: Any) -> dict[str, dict[str, Any]]:
    listed = json.loads(await recipient.mcp.call("list_shares", shared_with_me=True, **kwargs))
    return {s["id"]: s for s in listed["data"]}


async def _admin_share_ids(nc_mcp: McpTestHelper) -> set[str]:
    return {str(s["id"]) for s in await nc_mcp.client.ocs_get(SHARES_API)}


class TestUserShares:
    @pytest.mark.asyncio
    async def test_accept_pending_share(self, nc_mcp: McpTestHelper, recipient: Recipient) -> None:
        share_id = await _share(nc_mcp, "offer.txt", 0, recipient.user_id)
        pending = await _pending(recipient)
        assert set(pending) == {share_id}
        offer = pending[share_id]
        assert offer["federated"] is False
        assert offer["declined"] is False
        assert offer["path"] == "/offer.txt"
        assert (offer["uid_owner"], offer["share_with"], offer["item_type"]) == ("admin", recipient.user_id, "file")
        assert offer["mimetype"] == "text/plain"
        assert "permissions" not in offer
        assert await _received(recipient) == {}

        accepted = await recipient.mcp.call("accept_share", share_id=share_id)
        assert accepted.startswith(f"Share {share_id} accepted.")
        assert await _pending(recipient) == {}
        received = (await _received(recipient))[share_id]
        assert (received["path"], received["uid_owner"], received["displayname_owner"]) == (
            "/offer.txt",
            "admin",
            "admin",
        )
        assert received["permissions"] > 0
        content = await recipient.mcp.call("get_file", path="/offer.txt")
        assert "content of offer.txt" in content

        # Accepting again changes nothing
        assert (await recipient.mcp.call("accept_share", share_id=share_id)).startswith(f"Share {share_id} accepted.")
        assert set(await _received(recipient)) == {share_id}

    @pytest.mark.asyncio
    async def test_decline_deletes_the_share(self, nc_mcp: McpTestHelper, recipient: Recipient) -> None:
        share_id = await _share(nc_mcp, "unwanted.txt", 0, recipient.user_id)
        assert await recipient.mcp.call("decline_share", share_id=share_id) == f"Share {share_id} declined."
        assert await _pending(recipient) == {}
        assert await _received(recipient) == {}
        assert share_id not in await _admin_share_ids(nc_mcp)

    @pytest.mark.asyncio
    async def test_decline_refuses_shares_that_are_not_pending(
        self, nc_mcp: McpTestHelper, recipient: Recipient
    ) -> None:
        accepted_id = await _share(nc_mcp, "kept.txt", 0, recipient.user_id)
        await recipient.mcp.call("accept_share", share_id=accepted_id)
        with pytest.raises(ToolError, match="not waiting to be accepted by you"):
            await recipient.mcp.call("decline_share", share_id=accepted_id)
        assert set(await _received(recipient)) == {accepted_id}

        # The admin's own share is not theirs to decline either
        with pytest.raises(ToolError, match="not waiting to be accepted by you"):
            await recipient.admin.call("decline_share", share_id=accepted_id)
        assert accepted_id in await _admin_share_ids(nc_mcp)

    @pytest.mark.asyncio
    async def test_leave_accepted_share(self, nc_mcp: McpTestHelper, recipient: Recipient) -> None:
        share_id = await _share(nc_mcp, "leave.txt", 0, recipient.user_id)
        await recipient.mcp.call("accept_share", share_id=share_id)
        assert await recipient.mcp.call("delete_share", share_id=share_id) == f"Share {share_id} deleted."
        assert await _received(recipient) == {}
        assert share_id not in await _admin_share_ids(nc_mcp)

    @pytest.mark.asyncio
    async def test_path_filter(self, nc_mcp: McpTestHelper, recipient: Recipient) -> None:
        first = await _share(nc_mcp, "first.txt", 0, recipient.user_id)
        second = await _share(nc_mcp, "second.txt", 0, recipient.user_id)
        for share_id in (first, second):
            await recipient.mcp.call("accept_share", share_id=share_id)
        assert set(await _received(recipient)) == {first, second}
        assert set(await _received(recipient, path="/second.txt")) == {second}

    @pytest.mark.asyncio
    async def test_accept_unknown_share_fails(self, recipient: Recipient) -> None:
        with pytest.raises(ToolError, match=r"404|does not exist"):
            await recipient.mcp.call("accept_share", share_id="999999999")

    @pytest.mark.asyncio
    async def test_invalid_share_id_is_refused(self, recipient: Recipient) -> None:
        with pytest.raises(ToolError, match="Invalid share_id"):
            await recipient.mcp.call("accept_share", share_id="../1")


class TestGroupShares:
    @pytest.fixture
    async def group(self, nc_mcp: McpTestHelper, recipient: Recipient) -> AsyncGenerator[str]:
        group_id = f"mcp-test-g-{uuid.uuid4().hex[:8]}"
        await nc_mcp.client.ocs_post("cloud/groups", data={"groupid": group_id})
        await nc_mcp.client.ocs_post(f"cloud/users/{recipient.user_id}/groups", data={"groupid": group_id})
        yield group_id
        with contextlib.suppress(Exception):
            await nc_mcp.client.ocs_delete(f"cloud/groups/{group_id}")

    @pytest.mark.asyncio
    async def test_declined_group_share_can_still_be_accepted(
        self, nc_mcp: McpTestHelper, recipient: Recipient, group: str
    ) -> None:
        share_id = await _share(nc_mcp, "team.txt", 1, group)
        offer = (await _pending(recipient))[share_id]
        assert (offer["share_type"], offer["share_with"], offer["declined"]) == (1, group, False)

        assert await recipient.mcp.call("decline_share", share_id=share_id) == f"Share {share_id} declined."
        # Nextcloud keeps offering a declined group share, and the group keeps it
        assert (await _pending(recipient))[share_id]["declined"] is True
        assert await _received(recipient) == {}
        assert share_id in await _admin_share_ids(nc_mcp)

        await recipient.mcp.call("accept_share", share_id=share_id)
        assert await _pending(recipient) == {}
        assert set(await _received(recipient)) == {share_id}
        assert "content of team.txt" in await recipient.mcp.call("get_file", path="/team.txt")


class TestFederatedShares:
    """Federated shares to the same server, addressed the way the server reaches itself.

    NEXTCLOUD_FEDERATION_URL is that address when it differs from NEXTCLOUD_URL, as in CI where the tests reach
    the container through a mapped port. The server needs allow_local_remote_servers to share with itself.
    """

    @staticmethod
    def _cloud_id(user_id: str) -> str:
        base = os.environ.get("NEXTCLOUD_FEDERATION_URL") or get_config().nextcloud_url
        return f"{user_id}@{base.rstrip('/')}"

    @pytest.mark.asyncio
    async def test_accept_and_leave(self, nc_mcp: McpTestHelper, recipient: Recipient) -> None:
        await _share(nc_mcp, "remote.txt", 6, self._cloud_id(recipient.user_id))
        pending = list((await _pending(recipient)).values())
        assert len(pending) == 1
        offer = pending[0]
        assert offer["federated"] is True
        assert (offer["share_type"], offer["path"], offer["uid_owner"]) == (6, "/remote.txt", "admin")
        assert offer["remote"]
        assert "token" not in json.dumps(offer)
        share_id = offer["id"]
        assert isinstance(share_id, str)

        with pytest.raises(ToolError, match="not waiting to be accepted by you"):
            # Without federated the ID is looked up among this server's shares
            await recipient.mcp.call("decline_share", share_id=share_id)

        accepted = await recipient.mcp.call("accept_share", share_id=share_id, federated=True)
        assert accepted == f"Share {share_id} accepted, it is at /remote.txt in your files."
        assert await _pending(recipient) == {}
        received = (await _received(recipient))[share_id]
        assert (received["federated"], received["path"], received["item_type"]) == (True, "/remote.txt", "file")
        assert received["permissions"] > 0
        assert set(await _received(recipient, path="/remote.txt")) == {share_id}
        assert "content of remote.txt" in await recipient.mcp.call("get_file", path="/remote.txt")
        again = await recipient.mcp.call("accept_share", share_id=share_id, federated=True)
        assert again == f"Share {share_id} was already accepted, it is at /remote.txt in your files."
        with pytest.raises(ToolError, match="not waiting to be accepted by you"):
            await recipient.mcp.call("decline_share", share_id=share_id, federated=True)

        assert (
            await recipient.mcp.call("delete_share", share_id=share_id, federated=True) == f"Share {share_id} deleted."
        )
        assert await _received(recipient) == {}
        listing = json.loads(await recipient.mcp.call("list_directory", path="/"))
        assert "remote.txt" not in [entry["path"] for entry in listing["data"]]

    @pytest.mark.asyncio
    async def test_decline(self, nc_mcp: McpTestHelper, recipient: Recipient) -> None:
        admin_share = await _share(nc_mcp, "declined-remote.txt", 6, self._cloud_id(recipient.user_id))
        share_id = next(iter(await _pending(recipient)))
        declined = await recipient.mcp.call("decline_share", share_id=share_id, federated=True)
        assert declined == f"Share {share_id} declined."
        assert await _pending(recipient) == {}
        assert admin_share not in await _admin_share_ids(nc_mcp)


class TestPermissions:
    @pytest.mark.asyncio
    async def test_read_only(self, nc_mcp_read_only: McpTestHelper) -> None:
        assert isinstance(json.loads(await nc_mcp_read_only.call("list_pending_shares")), list)
        assert isinstance(json.loads(await nc_mcp_read_only.call("list_shares", shared_with_me=True))["data"], list)
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("accept_share", share_id="1")

    @pytest.mark.asyncio
    async def test_write(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"404|does not exist"):
            await nc_mcp_write.call("accept_share", share_id="999999999")
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("decline_share", share_id="1")
