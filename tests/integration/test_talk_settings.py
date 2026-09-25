"""Integration tests for personal conversation settings, pinned messages and message reminders."""

import contextlib
import json
import secrets
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.state import get_client, get_config, set_state

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


@pytest.fixture
async def room(nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
    data = json.loads(await nc_mcp.call("create_conversation", room_type=2, name="mcp-test-settings"))
    token = str(data["token"])
    yield token
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{token}")


@pytest.fixture
async def reminders_cleanup(nc_mcp: McpTestHelper, room: str) -> AsyncGenerator[None]:
    """Remove reminders a test leaves in its room, so they do not crowd the 10 Talk lists."""
    yield
    with contextlib.suppress(Exception):
        for r in json.loads(await nc_mcp.call("list_message_reminders")):
            if r["token"] == room:
                await nc_mcp.call("remove_message_reminder", token=room, message_id=r["message_id"])


def _in(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


async def _send(nc_mcp: McpTestHelper, token: str, message: str) -> int:
    data: dict[str, Any] = json.loads(await nc_mcp.call("send_message", token=token, message=message))
    return int(data["id"])


async def _conversation(nc_mcp: McpTestHelper, token: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(await nc_mcp.call("get_conversation", token=token))
    return data


class TestConversationPreferences:
    @pytest.mark.asyncio
    async def test_set_and_clear(self, nc_mcp: McpTestHelper, room: str) -> None:
        result = json.loads(
            await nc_mcp.call(
                "set_conversation_preferences",
                token=room,
                favorite=True,
                important=True,
                sensitive=True,
                notification_level="mention",
                call_notifications=False,
            )
        )
        flags = ("is_favorite", "is_important", "is_sensitive", "notification_level", "call_notifications")
        assert [result[f] for f in flags] == [True, True, True, "mention", False]
        stored = await _conversation(nc_mcp, room)
        assert [stored[f] for f in flags] == [True, True, True, "mention", False]

        await nc_mcp.call(
            "set_conversation_preferences",
            token=room,
            favorite=False,
            important=False,
            sensitive=False,
            notification_level="always",
            call_notifications=True,
        )
        stored = await _conversation(nc_mcp, room)
        assert [stored[f] for f in flags] == [False, False, False, "always", True]

    @pytest.mark.asyncio
    async def test_only_passed_settings_change(self, nc_mcp: McpTestHelper, room: str) -> None:
        await nc_mcp.call("set_conversation_preferences", token=room, favorite=True)
        await nc_mcp.call("set_conversation_preferences", token=room, notification_level="never")
        stored = await _conversation(nc_mcp, room)
        assert (stored["is_favorite"], stored["notification_level"]) == (True, "never")

    @pytest.mark.asyncio
    async def test_archive(self, nc_mcp: McpTestHelper, room: str) -> None:
        await nc_mcp.call("set_conversation_preferences", token=room, archived=True)
        listed = json.loads(await nc_mcp.call("list_conversations", limit=200))["data"]
        assert next(c for c in listed if c["token"] == room)["is_archived"] is True
        await nc_mcp.call("set_conversation_preferences", token=room, archived=False)
        assert (await _conversation(nc_mcp, room))["is_archived"] is False

    @pytest.mark.asyncio
    async def test_invalid_input(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="at least one setting"):
            await nc_mcp.call("set_conversation_preferences", token=room)
        for level in ("loud", "default"):
            with pytest.raises(ToolError, match="Must be one of: always, mention, never"):
                await nc_mcp.call("set_conversation_preferences", token=room, notification_level=level)


class TestListConversationsModifiedSince:
    @pytest.mark.asyncio
    async def test_filters_by_activity(self, nc_mcp: McpTestHelper, room: str) -> None:
        await _send(nc_mcp, room, "fresh")
        recent = json.loads(await nc_mcp.call("list_conversations", limit=200, modified_since=_in(-5)))["data"]
        assert room in [c["token"] for c in recent]
        later = json.loads(await nc_mcp.call("list_conversations", limit=200, modified_since=_in(60)))["data"]
        assert room not in [c["token"] for c in later]

    @pytest.mark.asyncio
    async def test_needs_a_time_zone(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="needs a time zone"):
            await nc_mcp.call("list_conversations", modified_since="2026-01-01T00:00:00")


class TestPins:
    @pytest.mark.asyncio
    async def test_pin_hide_and_unpin(self, nc_mcp: McpTestHelper, room: str) -> None:
        message_id = await _send(nc_mcp, room, "pin me")
        pinned = json.loads(await nc_mcp.call("pin_message", token=room, message_id=message_id, until=_in(60)))
        assert (pinned["id"], pinned["message"]) == (message_id, "pin me")
        assert (await _conversation(nc_mcp, room))["pinned_message_id"] == message_id
        shared = json.loads(await nc_mcp.call("list_shared_items", token=room, item_type="pinned"))
        assert shared["pinned"] == [f"[{message_id}] admin: pin me"]

        again = await nc_mcp.call("pin_message", token=room, message_id=message_id)
        assert again.startswith(f"Message {message_id} was already pinned")

        hidden = await nc_mcp.call("unpin_message", token=room, message_id=message_id, for_everyone=False)
        assert hidden == f"Pinned message {message_id} hidden for you."
        assert (await _conversation(nc_mcp, room))["pinned_message_id"] == 0
        # Hidden only for the caller: the pin itself stays for everyone
        shared = json.loads(await nc_mcp.call("list_shared_items", token=room, item_type="pinned"))
        assert shared["pinned"] == [f"[{message_id}] admin: pin me"]

        assert (
            await nc_mcp.call("unpin_message", token=room, message_id=message_id) == f"Message {message_id} unpinned."
        )
        assert json.loads(await nc_mcp.call("list_shared_items", token=room, item_type="pinned")) == {}
        again = await nc_mcp.call("unpin_message", token=room, message_id=message_id)
        assert again == f"Message {message_id} was not pinned."

    @pytest.mark.asyncio
    async def test_past_expiry_is_refused(self, nc_mcp: McpTestHelper, room: str) -> None:
        message_id = await _send(nc_mcp, room, "pin me")
        with pytest.raises(ToolError, match="must be in the future"):
            await nc_mcp.call("pin_message", token=room, message_id=message_id, until=_in(-5))

    @pytest.mark.asyncio
    async def test_members_cannot_pin(self, nc_mcp: McpTestHelper, room: str) -> None:
        user_id, password = f"mcp-test-u-{uuid.uuid4().hex[:8]}", f"Mcp-{secrets.token_hex(10)}!"
        admin_client, admin_config = get_client(), get_config()
        config = Config(
            nextcloud_url=admin_config.nextcloud_url,
            user=user_id,
            password=password,
            permission_level=PermissionLevel.DESTRUCTIVE,
        )
        member = NextcloudClient(config)
        try:
            await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": password})
            await nc_mcp.client.ocs_post(
                f"apps/spreed/api/v4/room/{room}/participants", data={"newParticipant": user_id, "source": "users"}
            )
            message_id = await _send(nc_mcp, room, "not for members")
            set_state(member, config)
            with pytest.raises(ToolError, match=r"403|[Ff]orbidden"):
                await McpTestHelper(nc_mcp.mcp, member).call("pin_message", token=room, message_id=message_id)
        finally:
            set_state(admin_client, admin_config)
            await member.close()
            with contextlib.suppress(Exception):
                await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")


class TestReminders:
    @pytest.mark.asyncio
    async def test_set_list_replace_and_remove(self, nc_mcp: McpTestHelper, room: str, reminders_cleanup: None) -> None:
        message_id = await _send(nc_mcp, room, "remind me")
        first = json.loads(
            await nc_mcp.call("set_message_reminder", token=room, message_id=message_id, remind_at=_in(60))
        )
        assert (first["token"], first["message_id"]) == (room, message_id)
        listed = json.loads(await nc_mcp.call("list_message_reminders"))
        mine = [r for r in listed if r["token"] == room]
        assert [(r["message_id"], r["remind_at"]) for r in mine] == [(message_id, first["remind_at"])]

        second = json.loads(
            await nc_mcp.call("set_message_reminder", token=room, message_id=message_id, remind_at=_in(120))
        )
        assert second["remind_at"] > first["remind_at"]
        mine = [r for r in json.loads(await nc_mcp.call("list_message_reminders")) if r["token"] == room]
        assert [r["remind_at"] for r in mine] == [second["remind_at"]]

        await nc_mcp.call("remove_message_reminder", token=room, message_id=message_id)
        assert all(r["token"] != room for r in json.loads(await nc_mcp.call("list_message_reminders")))

    @pytest.mark.asyncio
    async def test_invalid_times(self, nc_mcp: McpTestHelper, room: str) -> None:
        message_id = await _send(nc_mcp, room, "remind me")
        with pytest.raises(ToolError, match="must be in the future"):
            await nc_mcp.call("set_message_reminder", token=room, message_id=message_id, remind_at=_in(-1))
        with pytest.raises(ToolError, match="needs a time zone"):
            await nc_mcp.call(
                "set_message_reminder", token=room, message_id=message_id, remind_at="2030-01-01T09:00:00"
            )
        with pytest.raises(ToolError, match="ISO 8601"):
            await nc_mcp.call("set_message_reminder", token=room, message_id=message_id, remind_at="tomorrow")


class TestSettingsPermissions:
    @pytest.mark.asyncio
    async def test_read_only_blocks_changes(self, nc_mcp_read_only: McpTestHelper) -> None:
        for tool, args in (
            ("set_conversation_preferences", {"token": "x", "favorite": True}),
            ("pin_message", {"token": "x", "message_id": 1}),
            ("set_message_reminder", {"token": "x", "message_id": 1, "remind_at": _in(60)}),
        ):
            with pytest.raises(ToolError, match=r"[Pp]ermission"):
                await nc_mcp_read_only.call(tool, **args)

    @pytest.mark.asyncio
    async def test_write_blocks_removals(self, nc_mcp_write: McpTestHelper) -> None:
        for tool in ("unpin_message", "remove_message_reminder"):
            with pytest.raises(ToolError, match=r"[Pp]ermission"):
                await nc_mcp_write.call(tool, token="x", message_id=1)
