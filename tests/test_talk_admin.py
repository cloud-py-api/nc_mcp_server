"""Unit tests for Talk conversation management: which requests each change needs."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import talk

ROOM = "apps/spreed/api/v4/room/tok"
MODERATORS = f"{ROOM}/moderators"
OWNERS_SUPPORTED = {"capabilities": {"spreed": {"features": ["promote-demote-owner"]}}}
OWNERS_UNSUPPORTED = {"capabilities": {"spreed": {"features": ["chat-v2"]}}}


def _attendee(participant_type: int, actor_type: str = "users") -> dict[str, Any]:
    return {"attendeeId": 7, "actorType": actor_type, "actorId": "bo", "participantType": participant_type}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock = MagicMock()
    for method in ("ocs_get", "ocs_post", "ocs_put", "ocs_delete"):
        setattr(mock, method, AsyncMock(return_value={"token": "tok", "type": 2}))
    monkeypatch.setattr(talk, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-talk-admin")
    talk.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


def _participants_then(client: MagicMock, before: dict[str, Any], after: dict[str, Any], capabilities: Any) -> None:
    """ocs_get answers the participant list, the capabilities (when asked) and the list again."""

    async def get(path: str, **_: Any) -> Any:
        if path == "cloud/capabilities":
            return capabilities
        get.calls += 1  # type: ignore[attr-defined]
        return [before if get.calls == 1 else after]  # type: ignore[attr-defined]

    get.calls = 0  # type: ignore[attr-defined]
    client.ocs_get.side_effect = get


class TestSetParticipantRole:
    @pytest.mark.parametrize(
        ("current", "role", "expected"),
        [
            (3, "moderator", call.ocs_post(MODERATORS, data={"attendeeId": 7})),
            (5, "moderator", call.ocs_post(MODERATORS, data={"attendeeId": 7})),
            (2, "user", call.ocs_delete(f"{MODERATORS}?attendeeId=7")),
            (3, "owner", call.ocs_post(MODERATORS, data={"attendeeId": 7, "participantType": 1})),
            (2, "owner", call.ocs_post(MODERATORS, data={"attendeeId": 7, "participantType": 1})),
            (1, "moderator", call.ocs_delete(f"{MODERATORS}?attendeeId=7&participantType=2")),
            (1, "user", call.ocs_delete(f"{MODERATORS}?attendeeId=7&participantType=3")),
        ],
    )
    async def test_request_for_each_change(
        self, mcp: FastMCP, client: MagicMock, current: int, role: str, expected: Any
    ) -> None:
        _participants_then(client, _attendee(current), _attendee(talk._ROLE_TYPES[role]), OWNERS_SUPPORTED)
        result = json.loads(await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role=role))
        writes = [c for c in client.method_calls if c[0] in ("ocs_post", "ocs_delete")]
        assert writes == [expected]
        assert result["participant_type"] == role

    @pytest.mark.parametrize(("current", "role"), [(2, "moderator"), (3, "user"), (5, "user"), (1, "owner")])
    async def test_same_role_sends_nothing(self, mcp: FastMCP, client: MagicMock, current: int, role: str) -> None:
        _participants_then(client, _attendee(current), _attendee(current), OWNERS_SUPPORTED)
        await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role=role)
        assert not [c for c in client.method_calls if c[0] in ("ocs_post", "ocs_delete")]

    @pytest.mark.parametrize(("current", "role"), [(3, "owner"), (1, "moderator"), (1, "user")])
    async def test_owners_need_talk_25_and_nothing_is_sent_before(
        self, mcp: FastMCP, client: MagicMock, current: int, role: str
    ) -> None:
        _participants_then(client, _attendee(current), _attendee(current), OWNERS_UNSUPPORTED)
        with pytest.raises(ToolError, match=r"needs Talk 25 \(Nextcloud 35\)"):
            await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role=role)
        assert not [c for c in client.method_calls if c[0] in ("ocs_post", "ocs_delete")]

    async def test_capabilities_not_asked_without_owners(self, mcp: FastMCP, client: MagicMock) -> None:
        _participants_then(client, _attendee(3), _attendee(2), OWNERS_UNSUPPORTED)
        await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role="moderator")
        assert call.ocs_get("cloud/capabilities") not in client.method_calls

    @pytest.mark.parametrize(
        ("current", "role", "expected"),
        [
            (4, "moderator", call.ocs_post(MODERATORS, data={"attendeeId": 7})),
            (6, "user", call.ocs_delete(f"{MODERATORS}?attendeeId=7")),
            (6, "moderator", None),
            (4, "user", None),
        ],
    )
    async def test_guests(self, mcp: FastMCP, client: MagicMock, current: int, role: str, expected: Any) -> None:
        _participants_then(client, _attendee(current, "emails"), _attendee(current, "emails"), OWNERS_SUPPORTED)
        await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role=role)
        writes = [c for c in client.method_calls if c[0] in ("ocs_post", "ocs_delete")]
        assert writes == ([expected] if expected else [])

    async def test_guests_cannot_own(self, mcp: FastMCP, client: MagicMock) -> None:
        _participants_then(client, _attendee(4, "guests"), _attendee(4, "guests"), OWNERS_SUPPORTED)
        with pytest.raises(ToolError, match="Only users of this Nextcloud can be owners"):
            await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role="owner")

    @pytest.mark.parametrize(
        ("current", "role", "expected"),
        [
            (3, "moderator", call.ocs_post(MODERATORS, data={"attendeeId": 7})),
            (2, "user", call.ocs_delete(f"{MODERATORS}?attendeeId=7")),
        ],
    )
    async def test_federated_users_follow_the_user_rules(
        self, mcp: FastMCP, client: MagicMock, current: int, role: str, expected: Any
    ) -> None:
        """They are not local users, but promote and demote like them; only guests use types 4 and 6."""
        before, after = _attendee(current, "federated_users"), _attendee(current, "federated_users")
        _participants_then(client, before, after, OWNERS_SUPPORTED)
        await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role=role)
        assert [c for c in client.method_calls if c[0] in ("ocs_post", "ocs_delete")] == [expected]

    async def test_federated_users_cannot_own(self, mcp: FastMCP, client: MagicMock) -> None:
        before = _attendee(3, "federated_users")
        _participants_then(client, before, before, OWNERS_SUPPORTED)
        with pytest.raises(ToolError, match="Only users of this Nextcloud can be owners"):
            await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role="owner")

    @pytest.mark.parametrize("actor_type", ["groups", "circles"])
    async def test_groups_have_no_role(self, mcp: FastMCP, client: MagicMock, actor_type: str) -> None:
        _participants_then(client, _attendee(3, actor_type), _attendee(3, actor_type), OWNERS_SUPPORTED)
        with pytest.raises(ToolError, match="Groups and teams in a conversation have no role"):
            await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role="moderator")
        assert not [c for c in client.method_calls if c[0] in ("ocs_post", "ocs_delete")]

    async def test_unknown_attendee(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [_attendee(3) | {"attendeeId": 8}]
        with pytest.raises(ToolError, match="No participant with attendee ID 7 in conversation tok"):
            await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role="user")

    async def test_invalid_role(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Must be one of: owner, moderator, user"):
            await _call(mcp, "set_participant_role", token="tok", attendee_id=7, role="admin")
        client.ocs_get.assert_not_awaited()


class TestUpdateConversation:
    async def test_every_field(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "update_conversation", token="tok", name="N", description="", read_only=True, public=False)
        assert client.method_calls == [
            call.ocs_put(ROOM, data={"roomName": "N"}),
            call.ocs_put(f"{ROOM}/description", data={"description": ""}),
            call.ocs_put(f"{ROOM}/read-only", data={"state": 1}),
            call.ocs_delete(f"{ROOM}/public"),
        ]

    async def test_making_private_needs_the_destructive_level(self, mcp: FastMCP, client: MagicMock) -> None:
        """It removes everyone who joined through the link."""
        set_permission_level(PermissionLevel.WRITE)
        with pytest.raises(ToolError, match="requires 'destructive' permission"):
            await _call(mcp, "update_conversation", token="tok", name="N", public=False)
        assert client.method_calls == []
        await _call(mcp, "update_conversation", token="tok", public=True)
        assert client.method_calls == [call.ocs_post(f"{ROOM}/public")]

    async def test_make_public(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "update_conversation", token="tok", public=True)
        assert client.method_calls == [call.ocs_post(f"{ROOM}/public")]

    async def test_failure_names_what_was_changed(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put.side_effect = [{"token": "tok"}, NextcloudError("OCS PUT x: HTTP 400 (description)", 400)]
        with pytest.raises(ToolError, match=r"Failed at description \(name already changed\)"):
            await _call(mcp, "update_conversation", token="tok", name="N", description="x" * 3000, public=True)
        client.ocs_post.assert_not_awaited()

    async def test_needs_a_field(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="at least one field"):
            await _call(mcp, "update_conversation", token="tok")


class TestParticipants:
    @pytest.mark.parametrize(
        ("source", "who", "said"),
        [
            ("users", "bo", "user"),
            ("groups", "staff", "group"),
            ("emails", "bo@example.com", "email"),
            ("federated_users", "bo@cloud.example.com", "federated user"),
        ],
    )
    async def test_add(self, mcp: FastMCP, client: MagicMock, source: str, who: str, said: str) -> None:
        result = await _call(mcp, "add_participant", token="tok", participant=who, source=source)
        client.ocs_post.assert_awaited_once_with(f"{ROOM}/participants", data={"newParticipant": who, "source": source})
        assert result == f"Added {said} {who} to tok."

    @pytest.mark.parametrize("address", ["not-an-email", "a@b", "a b@c.de", "@example.com"])
    async def test_email_is_checked(self, mcp: FastMCP, client: MagicMock, address: str) -> None:
        with pytest.raises(ToolError, match="is not an email address"):
            await _call(mcp, "add_participant", token="tok", participant=address, source="emails")
        client.ocs_post.assert_not_awaited()

    async def test_invalid_source(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Must be one of"):
            await _call(mcp, "add_participant", token="tok", participant="p", source="phones")
        client.ocs_post.assert_not_awaited()

    async def test_remove(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "remove_participant", token="tok", attendee_id=7)
        client.ocs_delete.assert_awaited_once_with(f"{ROOM}/attendees?attendeeId=7")


class TestConversationLifecycle:
    async def test_one_to_one_needs_invite(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="needs invite"):
            await _call(mcp, "create_conversation", room_type=1, name="")
        client.ocs_post.assert_not_awaited()

    async def test_one_to_one(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "create_conversation", room_type=1, name="", invite="bo")
        client.ocs_post.assert_awaited_once_with(
            "apps/spreed/api/v4/room", data={"roomType": 1, "roomName": "", "invite": "bo"}
        )

    async def test_delete(self, mcp: FastMCP, client: MagicMock) -> None:
        assert await _call(mcp, "delete_conversation", token="tok") == "Conversation tok deleted."
        client.ocs_delete.assert_awaited_once_with(ROOM)

    async def test_delete_one_to_one_explained(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.side_effect = NextcloudError("OCS DELETE x: HTTP 400", 400)
        with pytest.raises(ToolError, match="one-to-one conversations cannot be deleted, only left"):
            await _call(mcp, "delete_conversation", token="tok")
