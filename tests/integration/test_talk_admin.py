"""Integration tests for managing Talk conversations: settings, participants, roles and deletion."""

import contextlib
import json
import secrets
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


@pytest.fixture
async def room(nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
    data = json.loads(await nc_mcp.call("create_conversation", room_type=2, name="mcp-test-admin"))
    token = str(data["token"])
    yield token
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{token}")


@pytest.fixture
async def user(nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
    user_id = f"mcp-test-u-{uuid.uuid4().hex[:8]}"
    await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": f"Mcp-{secrets.token_hex(10)}!"})
    yield user_id
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")


async def _participants(nc_mcp: McpTestHelper, token: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(await nc_mcp.call("get_participants", token=token, limit=200))["data"]
    return data


async def _attendee(nc_mcp: McpTestHelper, token: str, actor_id: str) -> dict[str, Any]:
    return next(p for p in await _participants(nc_mcp, token) if p["actor_id"] == actor_id)


async def _supports_owners(nc_mcp: McpTestHelper) -> bool:
    capabilities = await nc_mcp.client.ocs_get("cloud/capabilities")
    return "promote-demote-owner" in capabilities["capabilities"]["spreed"]["features"]


class TestUpdateConversation:
    @pytest.mark.asyncio
    async def test_every_field(self, nc_mcp: McpTestHelper, room: str) -> None:
        result = json.loads(
            await nc_mcp.call(
                "update_conversation",
                token=room,
                name="mcp-test renamed",
                description="About",
                read_only=True,
                public=True,
            )
        )
        assert (result["name"], result["description"], result["read_only"], result["type"]) == (
            "mcp-test renamed",
            "About",
            True,
            "public",
        )
        await nc_mcp.call("update_conversation", token=room, description="", read_only=False, public=False)
        stored = json.loads(await nc_mcp.call("get_conversation", token=room))
        assert (stored["name"], stored["description"], stored["read_only"], stored["type"]) == (
            "mcp-test renamed",
            "",
            False,
            "group",
        )

    @pytest.mark.asyncio
    async def test_needs_a_field(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="at least one field"):
            await nc_mcp.call("update_conversation", token=room)


class TestParticipants:
    @pytest.mark.asyncio
    async def test_add_users_groups_and_guests(self, nc_mcp: McpTestHelper, room: str, user: str) -> None:
        group = f"mcp-test-grp-{uuid.uuid4().hex[:6]}"
        await nc_mcp.client.ocs_post("cloud/groups", data={"groupid": group})
        try:
            assert "Added user" in await nc_mcp.call("add_participant", token=room, participant=user)
            await nc_mcp.call("add_participant", token=room, participant=user)  # twice changes nothing
            await nc_mcp.call("add_participant", token=room, participant=group, source="groups")
            await nc_mcp.call("add_participant", token=room, participant="guest@example.com", source="emails")
            kinds = [(p["actor_type"], p["participant_type"]) for p in await _participants(nc_mcp, room)]
            assert kinds.count(("users", "user")) == 1
            assert ("groups", "user") in kinds
            assert ("emails", "guest") in kinds
        finally:
            await nc_mcp.client.ocs_delete(f"cloud/groups/{group}")

    @pytest.mark.asyncio
    async def test_invalid_source(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="Must be one of: users, groups"):
            await nc_mcp.call("add_participant", token=room, participant="x", source="phones")

    @pytest.mark.asyncio
    async def test_last_moderator_cannot_remove_themselves(self, nc_mcp: McpTestHelper, room: str, user: str) -> None:
        await nc_mcp.call("add_participant", token=room, participant=user)
        me = await _attendee(nc_mcp, room, "admin")
        with pytest.raises(ToolError, match="last-moderator"):
            await nc_mcp.call("remove_participant", token=room, attendee_id=me["attendee_id"])
        assert "admin" in [p["actor_id"] for p in await _participants(nc_mcp, room)]

    @pytest.mark.asyncio
    async def test_remove(self, nc_mcp: McpTestHelper, room: str, user: str) -> None:
        await nc_mcp.call("add_participant", token=room, participant=user)
        attendee = await _attendee(nc_mcp, room, user)
        await nc_mcp.call("remove_participant", token=room, attendee_id=attendee["attendee_id"])
        assert user not in [p["actor_id"] for p in await _participants(nc_mcp, room)]


class TestRoles:
    @pytest.mark.asyncio
    async def test_moderator_and_back(self, nc_mcp: McpTestHelper, room: str, user: str) -> None:
        await nc_mcp.call("add_participant", token=room, participant=user)
        attendee_id = (await _attendee(nc_mcp, room, user))["attendee_id"]
        promoted = json.loads(
            await nc_mcp.call("set_participant_role", token=room, attendee_id=attendee_id, role="moderator")
        )
        assert promoted["participant_type"] == "moderator"
        # Asking for the role someone already has changes nothing
        again = json.loads(
            await nc_mcp.call("set_participant_role", token=room, attendee_id=attendee_id, role="moderator")
        )
        assert again["participant_type"] == "moderator"
        demoted = json.loads(
            await nc_mcp.call("set_participant_role", token=room, attendee_id=attendee_id, role="user")
        )
        assert demoted["participant_type"] == "user"

    @pytest.mark.asyncio
    async def test_owner(self, nc_mcp: McpTestHelper, room: str, user: str) -> None:
        await nc_mcp.call("add_participant", token=room, participant=user)
        attendee_id = (await _attendee(nc_mcp, room, user))["attendee_id"]
        if not await _supports_owners(nc_mcp):
            with pytest.raises(ToolError, match=r"needs Talk 25 \(Nextcloud 35\) or newer"):
                await nc_mcp.call("set_participant_role", token=room, attendee_id=attendee_id, role="owner")
            # Refused before any request: Talk 24 would have made them moderator instead
            assert (await _attendee(nc_mcp, room, user))["participant_type"] == "user"
            return
        owner = json.loads(await nc_mcp.call("set_participant_role", token=room, attendee_id=attendee_id, role="owner"))
        assert owner["participant_type"] == "owner"
        with pytest.raises(ToolError, match="owner"):
            await nc_mcp.call("remove_participant", token=room, attendee_id=attendee_id)
        moderator = json.loads(
            await nc_mcp.call("set_participant_role", token=room, attendee_id=attendee_id, role="moderator")
        )
        assert moderator["participant_type"] == "moderator"
        await nc_mcp.call("set_participant_role", token=room, attendee_id=attendee_id, role="owner")
        user_again = json.loads(
            await nc_mcp.call("set_participant_role", token=room, attendee_id=attendee_id, role="user")
        )
        assert user_again["participant_type"] == "user"

    @pytest.mark.asyncio
    async def test_guest_roles(self, nc_mcp: McpTestHelper, room: str) -> None:
        await nc_mcp.call("add_participant", token=room, participant="guest@example.com", source="emails")
        guest = next(p for p in await _participants(nc_mcp, room) if p["actor_type"] == "emails")
        promoted = json.loads(
            await nc_mcp.call("set_participant_role", token=room, attendee_id=guest["attendee_id"], role="moderator")
        )
        assert promoted["participant_type"] == "guest-moderator"
        with pytest.raises(ToolError, match="Only users of this Nextcloud can be owners"):
            await nc_mcp.call("set_participant_role", token=room, attendee_id=guest["attendee_id"], role="owner")
        back = json.loads(
            await nc_mcp.call("set_participant_role", token=room, attendee_id=guest["attendee_id"], role="user")
        )
        assert back["participant_type"] == "guest"

    @pytest.mark.asyncio
    async def test_unknown_attendee_and_role(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="No participant with attendee ID 999999"):
            await nc_mcp.call("set_participant_role", token=room, attendee_id=999999, role="moderator")
        with pytest.raises(ToolError, match="Must be one of: owner, moderator, user"):
            await nc_mcp.call("set_participant_role", token=room, attendee_id=1, role="admin")


class TestOneToOneAndDeletion:
    @pytest.mark.asyncio
    async def test_one_to_one(self, nc_mcp: McpTestHelper, user: str) -> None:
        first = json.loads(await nc_mcp.call("create_conversation", room_type=1, name="", invite=user))
        try:
            assert first["type"] == "one-to-one"
            again = json.loads(await nc_mcp.call("create_conversation", room_type=1, name="", invite=user))
            assert again["token"] == first["token"]
            with pytest.raises(ToolError, match="cannot be deleted, only left"):
                await nc_mcp.call("delete_conversation", token=first["token"])
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("leave_conversation", token=first["token"])

    @pytest.mark.asyncio
    async def test_one_to_one_needs_someone(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="needs invite"):
            await nc_mcp.call("create_conversation", room_type=1, name="")

    @pytest.mark.asyncio
    async def test_delete(self, nc_mcp: McpTestHelper, room: str) -> None:
        assert "deleted" in await nc_mcp.call("delete_conversation", token=room)
        with pytest.raises(ToolError, match=r"404|[Nn]ot found"):
            await nc_mcp.call("get_conversation", token=room)


class TestAdminPermissions:
    @pytest.mark.asyncio
    async def test_read_only_blocks_changes(self, nc_mcp_read_only: McpTestHelper) -> None:
        for tool, args in (
            ("update_conversation", {"token": "x", "name": "y"}),
            ("add_participant", {"token": "x", "participant": "y"}),
            ("set_participant_role", {"token": "x", "attendee_id": 1, "role": "user"}),
        ):
            with pytest.raises(ToolError, match=r"[Pp]ermission"):
                await nc_mcp_read_only.call(tool, **args)

    @pytest.mark.asyncio
    async def test_write_blocks_removals(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("remove_participant", token="x", attendee_id=1)
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_conversation", token="x")
