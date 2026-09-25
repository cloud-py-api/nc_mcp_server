"""Integration tests for the Talk chat actions: edits, reactions, read state, context, shared items, mentions.

Also covers how messages read back: Talk leaves placeholders such as {mention-user1} in the text.
"""

import contextlib
import json
import secrets
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.state import get_client, get_config, set_state

from .conftest import TEST_BASE_DIR, McpTestHelper

pytestmark = pytest.mark.integration


@pytest.fixture
async def room(nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
    data = json.loads(await nc_mcp.call("create_conversation", room_type=2, name="mcp-test-chat"))
    token = str(data["token"])
    yield token
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{token}")


@pytest.fixture
async def member(nc_mcp: McpTestHelper, room: str) -> AsyncGenerator[tuple[str, str]]:
    """A regular user who is a plain participant of the room, as (user_id, password)."""
    user_id, password = f"mcp-test-u-{uuid.uuid4().hex[:8]}", f"Mcp-{secrets.token_hex(10)}!"
    await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": password})
    await nc_mcp.client.ocs_post(
        f"apps/spreed/api/v4/room/{room}/participants", data={"newParticipant": user_id, "source": "users"}
    )
    yield user_id, password
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")


@contextlib.asynccontextmanager
async def _as_user(nc_mcp: McpTestHelper, user_id: str, password: str) -> AsyncGenerator[McpTestHelper]:
    admin_client, admin_config = get_client(), get_config()
    config = Config(
        nextcloud_url=admin_config.nextcloud_url,
        user=user_id,
        password=password,
        permission_level=PermissionLevel.DESTRUCTIVE,
    )
    client = NextcloudClient(config)
    set_state(client, config)
    try:
        yield McpTestHelper(nc_mcp.mcp, client)
    finally:
        set_state(admin_client, admin_config)
        await client.close()


async def _send(nc_mcp: McpTestHelper, token: str, message: str) -> int:
    data: dict[str, Any] = json.loads(await nc_mcp.call("send_message", token=token, message=message))
    return int(data["id"])


class TestMessageText:
    @pytest.mark.asyncio
    async def test_mentions_read_back_as_names(self, nc_mcp: McpTestHelper, room: str) -> None:
        await _send(nc_mcp, room, "ping @admin please")
        chat = await nc_mcp.call("get_messages", token=room)
        assert "ping @admin please" in chat
        assert "{mention-" not in chat

    @pytest.mark.asyncio
    async def test_everyone_mention_reads_back_as_all(self, nc_mcp: McpTestHelper, room: str) -> None:
        await _send(nc_mcp, room, "heads up @all")
        assert "heads up @all" in await nc_mcp.call("get_messages", token=room)

    @pytest.mark.asyncio
    async def test_shared_file_reads_back_as_its_name(self, nc_mcp: McpTestHelper, room: str) -> None:
        name = await _share_file(nc_mcp, room)
        chat = await nc_mcp.call("get_messages", token=room)
        assert name in chat
        assert "{file}" not in chat


async def _share_file(nc_mcp: McpTestHelper, token: str) -> str:
    """Share a new file into the conversation and return its name."""
    name = f"shared-{uuid.uuid4().hex[:6]}.txt"
    await nc_mcp.create_test_dir()
    await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/{name}", "content")
    await nc_mcp.call("create_share", path=f"{TEST_BASE_DIR}/{name}", share_type=10, share_with=token)
    return name


async def _talk_notifications(helper: McpTestHelper) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = await helper.client.ocs_get("apps/notifications/api/v2/notifications") or []
    return [n for n in data if n.get("app") == "spreed"]


class TestEditMessage:
    @pytest.mark.asyncio
    async def test_edit_own_message(self, nc_mcp: McpTestHelper, room: str) -> None:
        message_id = await _send(nc_mcp, room, "first draft")
        edited = json.loads(
            await nc_mcp.call("edit_message", token=room, message_id=message_id, message="final @admin")
        )
        assert (edited["id"], edited["message"]) == (message_id, "final @admin")
        chat = await nc_mcp.call("get_messages", token=room)
        assert f"[{message_id}] admin: final @admin" in chat
        assert "first draft" not in chat

    @pytest.mark.asyncio
    async def test_file_share_edit_becomes_its_caption(self, nc_mcp: McpTestHelper, room: str) -> None:
        name = await _share_file(nc_mcp, room)
        line = next(line for line in (await nc_mcp.call("get_messages", token=room)).splitlines() if name in line)
        message_id = int(line.split("]", 1)[0].lstrip("["))
        edited = json.loads(await nc_mcp.call("edit_message", token=room, message_id=message_id, message="see this"))
        assert edited["message"] == f"see this [{name}]"
        assert f"[{message_id}] admin: see this [{name}]" in await nc_mcp.call("get_messages", token=room)

    @pytest.mark.asyncio
    async def test_read_only_conversation(self, nc_mcp: McpTestHelper, room: str) -> None:
        message_id = await _send(nc_mcp, room, "before locking")
        await nc_mcp.client.ocs_put(f"apps/spreed/api/v4/room/{room}/read-only", data={"state": 1})
        with pytest.raises(ToolError, match="may be read-only"):
            await nc_mcp.call("edit_message", token=room, message_id=message_id, message="after")

    @pytest.mark.asyncio
    async def test_system_messages_cannot_be_edited(self, nc_mcp: McpTestHelper, room: str) -> None:
        chat = await nc_mcp.call("get_messages", token=room, include_system=True)
        system_id = int(chat.split("]", 1)[0].lstrip("["))
        with pytest.raises(ToolError, match="system messages and shared objects other than files"):
            await nc_mcp.call("edit_message", token=room, message_id=system_id, message="x")

    @pytest.mark.asyncio
    async def test_moderator_may_edit_others_but_members_may_not(
        self, nc_mcp: McpTestHelper, room: str, member: tuple[str, str]
    ) -> None:
        admin_message = await _send(nc_mcp, room, "from the moderator")
        async with _as_user(nc_mcp, *member) as as_member:
            member_message = await _send(as_member, room, "from a member")
            with pytest.raises(ToolError, match="only your own messages can be edited"):
                await as_member.call("edit_message", token=room, message_id=admin_message, message="hijacked")
        edited = json.loads(
            await nc_mcp.call("edit_message", token=room, message_id=member_message, message="moderated")
        )
        assert edited["message"] == "moderated"

    @pytest.mark.asyncio
    async def test_unknown_message(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="Not found"):
            await nc_mcp.call("edit_message", token=room, message_id=999999999, message="x")


class TestReactions:
    @pytest.mark.asyncio
    async def test_add_list_and_remove(self, nc_mcp: McpTestHelper, room: str, member: tuple[str, str]) -> None:
        message_id = await _send(nc_mcp, room, "react to me")
        assert json.loads(await nc_mcp.call("add_reaction", token=room, message_id=message_id, reaction="👍")) == {
            "👍": ["admin"]
        }
        # A second time changes nothing
        await nc_mcp.call("add_reaction", token=room, message_id=message_id, reaction="👍")
        await nc_mcp.call("add_reaction", token=room, message_id=message_id, reaction="🎉")
        async with _as_user(nc_mcp, *member) as as_member:
            await as_member.call("add_reaction", token=room, message_id=message_id, reaction="👍")
        reactions = json.loads(await nc_mcp.call("get_reactions", token=room, message_id=message_id))
        assert sorted(reactions) == ["🎉", "👍"]
        assert sorted(reactions["👍"]) == sorted(["admin", member[0]])
        only = json.loads(await nc_mcp.call("get_reactions", token=room, message_id=message_id, reaction="🎉"))
        assert only == {"🎉": ["admin"]}

        left = json.loads(await nc_mcp.call("remove_reaction", token=room, message_id=message_id, reaction="👍"))
        assert left == {"👍": [member[0]], "🎉": ["admin"]}

    @pytest.mark.asyncio
    async def test_no_reactions(self, nc_mcp: McpTestHelper, room: str) -> None:
        message_id = await _send(nc_mcp, room, "quiet")
        assert json.loads(await nc_mcp.call("get_reactions", token=room, message_id=message_id)) == {}


class TestReadState:
    @pytest.mark.asyncio
    async def test_mark_unread_and_read(self, nc_mcp: McpTestHelper, room: str) -> None:
        first = await _send(nc_mcp, room, "one")
        await _send(nc_mcp, room, "two")
        last = await _send(nc_mcp, room, "three")
        unread = json.loads(await nc_mcp.call("mark_conversation_unread", token=room))
        assert unread["unread_messages"] >= 1
        assert unread["last_read_message"] < last
        partly = json.loads(await nc_mcp.call("mark_conversation_read", token=room, message_id=first))
        assert partly["last_read_message"] == first
        assert partly["unread_messages"] >= 2
        done = json.loads(await nc_mcp.call("mark_conversation_read", token=room))
        assert (done["last_read_message"], done["unread_messages"]) == (last, 0)


class TestMessageContext:
    @pytest.mark.asyncio
    async def test_messages_around_one(self, nc_mcp: McpTestHelper, room: str) -> None:
        ids = [await _send(nc_mcp, room, f"line {i}") for i in range(5)]
        lines = (await nc_mcp.call("get_message_context", token=room, message_id=ids[2], limit=1)).splitlines()
        assert lines == [
            f"[{ids[1]}] admin: line 1",
            f">> [{ids[2]}] admin: line 2",
            f"[{ids[3]}] admin: line 3",
        ]

    @pytest.mark.asyncio
    async def test_latest_message_has_nothing_after_it(self, nc_mcp: McpTestHelper, room: str) -> None:
        ids = [await _send(nc_mcp, room, f"line {i}") for i in range(2)]
        lines = (await nc_mcp.call("get_message_context", token=room, message_id=ids[1], limit=5)).splitlines()
        assert lines[-2:] == [f"[{ids[0]}] admin: line 0", f">> [{ids[1]}] admin: line 1"]
        assert all("created the conversation" not in line for line in lines)  # system messages hidden

    @pytest.mark.asyncio
    async def test_reading_context_keeps_notifications(
        self, nc_mcp: McpTestHelper, room: str, member: tuple[str, str]
    ) -> None:
        """Talk's own context endpoint clears the reader's mention notifications; this tool must not."""
        message_id = await _send(nc_mcp, room, f'look @"{member[0]}"')
        async with _as_user(nc_mcp, *member) as as_member:
            before = await _talk_notifications(as_member)
            assert before, "the mention should have notified the member"
            await as_member.call("get_message_context", token=room, message_id=message_id)
            await as_member.call("get_messages", token=room)
            assert len(await _talk_notifications(as_member)) == len(before)


class TestSharedItems:
    @pytest.mark.asyncio
    async def test_overview_and_one_type(self, nc_mcp: McpTestHelper, room: str) -> None:
        name = await _share_file(nc_mcp, room)
        overview = json.loads(await nc_mcp.call("list_shared_items", token=room))
        assert list(overview) == ["file"]
        assert name in overview["file"][0]
        files = json.loads(await nc_mcp.call("list_shared_items", token=room, item_type="file"))
        assert name in files["file"][0]
        assert json.loads(await nc_mcp.call("list_shared_items", token=room, item_type="poll")) == {}

    @pytest.mark.asyncio
    async def test_invalid_type(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="Must be one of: audio, deckcard"):
            await nc_mcp.call("list_shared_items", token=room, item_type="pictures")


class TestSearchMentions:
    @pytest.mark.asyncio
    async def test_finds_participants(self, nc_mcp: McpTestHelper, room: str, member: tuple[str, str]) -> None:
        found = json.loads(await nc_mcp.call("search_mentions", token=room, search=member[0]))
        match = next(m for m in found if m["id"] == member[0])
        assert match["mention"] == f'@"{member[0]}"'
        assert match["source"] == "users"

    @pytest.mark.asyncio
    async def test_mention_text_works_in_a_message(
        self, nc_mcp: McpTestHelper, room: str, member: tuple[str, str]
    ) -> None:
        found = json.loads(await nc_mcp.call("search_mentions", token=room, search=member[0]))
        mention = next(m["mention"] for m in found if m["id"] == member[0])
        await _send(nc_mcp, room, f"hello {mention}")
        assert f"hello @{member[0]}" in await nc_mcp.call("get_messages", token=room)


class TestChatPermissions:
    @pytest.mark.asyncio
    async def test_read_only_blocks_writes(self, nc_mcp_read_only: McpTestHelper) -> None:
        for tool, args in (
            ("edit_message", {"token": "x", "message_id": 1, "message": "y"}),
            ("add_reaction", {"token": "x", "message_id": 1, "reaction": "👍"}),
            ("mark_conversation_read", {"token": "x"}),
            ("mark_conversation_unread", {"token": "x"}),
        ):
            with pytest.raises(ToolError, match=r"[Pp]ermission"):
                await nc_mcp_read_only.call(tool, **args)

    @pytest.mark.asyncio
    async def test_write_blocks_remove_reaction(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("remove_reaction", token="x", message_id=1, reaction="👍")
