"""Integration tests for Talk tools against a real Nextcloud instance.

Tests call MCP tools by name through the full tool stack including
permission checks, argument parsing, and JSON serialization.
"""

import contextlib
import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_room(nc_mcp: McpTestHelper, name: str, room_type: int = 2) -> dict[str, Any]:
    """Create a conversation and return the parsed result."""
    result = await nc_mcp.call("create_conversation", room_type=room_type, name=name)
    return json.loads(result)


async def _delete_room(nc_mcp: McpTestHelper, token: str) -> None:
    """Delete a conversation via the client (bypasses MCP permission checks)."""
    await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{token}")


async def _send_msg(nc_mcp: McpTestHelper, token: str, message: str) -> dict[str, Any]:
    """Send a message and return the parsed result."""
    result = await nc_mcp.call("send_message", token=token, message=message)
    return json.loads(result)


# ---------------------------------------------------------------------------
# list_conversations
# ---------------------------------------------------------------------------


class TestListConversations:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_conversations")
        data = json.loads(result)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_includes_created_conversation(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-list-conv")
        try:
            result = await nc_mcp.call("list_conversations")
            data = json.loads(result)
            tokens = [c["token"] for c in data]
            assert room["token"] in tokens
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_conversation_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-fields")
        try:
            result = await nc_mcp.call("list_conversations")
            data = json.loads(result)
            conv = next(c for c in data if c["token"] == room["token"])
            required = ["token", "type", "name", "read_only", "unread_messages", "last_activity"]
            for field in required:
                assert field in conv, f"Missing field: {field}"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_conversation_type_labels(self, nc_mcp: McpTestHelper) -> None:
        group_room = await _create_room(nc_mcp, "test-type-group", room_type=2)
        public_room = await _create_room(nc_mcp, "test-type-public", room_type=3)
        try:
            result = await nc_mcp.call("list_conversations")
            data = json.loads(result)
            group = next(c for c in data if c["token"] == group_room["token"])
            public = next(c for c in data if c["token"] == public_room["token"])
            assert group["type"] == "group"
            assert public["type"] == "public"
        finally:
            await _delete_room(nc_mcp, str(group_room["token"]))
            await _delete_room(nc_mcp, str(public_room["token"]))


# ---------------------------------------------------------------------------
# get_conversation
# ---------------------------------------------------------------------------


class TestGetConversation:
    @pytest.mark.asyncio
    async def test_returns_conversation_details(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-get-conv")
        try:
            result = await nc_mcp.call("get_conversation", token=str(room["token"]))
            data = json.loads(result)
            assert data["token"] == room["token"]
            assert data["name"] == "test-get-conv"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_nonexistent_token_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_conversation", token="nonexistent-token-xyz")

    @pytest.mark.asyncio
    async def test_returns_read_only_status(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-readonly-check")
        try:
            result = await nc_mcp.call("get_conversation", token=str(room["token"]))
            data = json.loads(result)
            assert data["read_only"] is False
        finally:
            await _delete_room(nc_mcp, str(room["token"]))


# ---------------------------------------------------------------------------
# get_messages
# ---------------------------------------------------------------------------


class TestGetMessages:
    @pytest.mark.asyncio
    async def test_returns_messages_list(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-get-msgs")
        try:
            await _send_msg(nc_mcp, str(room["token"]), "hello world")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            data: list[Any] = json.loads(result)
            assert isinstance(data, list)
            assert len(data) >= 1
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_message_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-msg-fields")
        try:
            await _send_msg(nc_mcp, str(room["token"]), "field test")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            data = json.loads(result)
            user_msgs = [m for m in data if m["message_type"] == "comment"]
            assert len(user_msgs) >= 1
            msg = user_msgs[0]
            required = ["id", "actor_display_name", "message", "timestamp", "message_type", "is_replyable"]
            for field in required:
                assert field in msg, f"Missing field: {field}"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_messages_ordered_newest_first(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-msg-order")
        try:
            await _send_msg(nc_mcp, str(room["token"]), "first")
            await _send_msg(nc_mcp, str(room["token"]), "second")
            await _send_msg(nc_mcp, str(room["token"]), "third")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            data = json.loads(result)
            user_msgs = [m for m in data if m["message_type"] == "comment"]
            messages = [m["message"] for m in user_msgs]
            assert messages == ["third", "second", "first"]
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_limit_parameter(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-msg-limit")
        try:
            for i in range(5):
                await _send_msg(nc_mcp, str(room["token"]), f"msg-{i}")
            result = await nc_mcp.call("get_messages", token=str(room["token"]), limit=2)
            data = json.loads(result)
            assert len(data) <= 2
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_system_messages_included(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-sys-msgs")
        try:
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            data = json.loads(result)
            system_msgs = [m for m in data if m["system_message"] != ""]
            assert len(system_msgs) >= 1, "Should include 'conversation_created' system message"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_nonexistent_conversation_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_messages", token="nonexistent-xyz-12345")

    @pytest.mark.asyncio
    async def test_empty_conversation_has_system_message(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-empty-chat")
        try:
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            data = json.loads(result)
            # New conversation always has at least the "conversation_created" system message
            assert len(data) >= 1
        finally:
            await _delete_room(nc_mcp, str(room["token"]))


# ---------------------------------------------------------------------------
# get_participants
# ---------------------------------------------------------------------------


class TestGetParticipants:
    @pytest.mark.asyncio
    async def test_returns_participants_list(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-participants")
        try:
            result = await nc_mcp.call("get_participants", token=str(room["token"]))
            data: list[Any] = json.loads(result)
            assert isinstance(data, list)
            assert len(data) >= 1
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_creator_is_owner(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-owner")
        try:
            result = await nc_mcp.call("get_participants", token=str(room["token"]))
            data = json.loads(result)
            admin = next(p for p in data if p["actor_id"] == "admin")
            assert admin["participant_type"] == "owner"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_participant_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-part-fields")
        try:
            result = await nc_mcp.call("get_participants", token=str(room["token"]))
            data = json.loads(result)
            p = data[0]
            required = ["attendee_id", "actor_type", "actor_id", "display_name", "participant_type", "in_call"]
            for field in required:
                assert field in p, f"Missing field: {field}"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_nonexistent_conversation_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_participants", token="nonexistent-xyz-12345")


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_basic_message(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-send-msg")
        try:
            result = await nc_mcp.call("send_message", token=str(room["token"]), message="hello!")
            data = json.loads(result)
            assert data["message"] == "hello!"
            assert data["actor_id"] == "admin"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_send_returns_message_id(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-send-id")
        try:
            result = await nc_mcp.call("send_message", token=str(room["token"]), message="test")
            data = json.loads(result)
            assert "id" in data
            assert isinstance(data["id"], int)
            assert data["id"] > 0
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_send_markdown_message(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-send-md")
        try:
            md_msg = "**bold** and *italic* and `code`"
            result = await nc_mcp.call("send_message", token=str(room["token"]), message=md_msg)
            data = json.loads(result)
            assert data["message"] == md_msg
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_send_utf8_message(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-send-utf8")
        try:
            utf8_msg = "Привет мир! 你好世界! 🌍"
            result = await nc_mcp.call("send_message", token=str(room["token"]), message=utf8_msg)
            data = json.loads(result)
            assert data["message"] == utf8_msg
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_conversation_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("send_message", token="nonexistent-xyz-12345", message="nope")

    @pytest.mark.asyncio
    async def test_send_reply(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-reply")
        try:
            original = await _send_msg(nc_mcp, str(room["token"]), "original message")
            reply_result = await nc_mcp.call(
                "send_message",
                token=str(room["token"]),
                message="this is a reply",
                reply_to=int(original["id"]),
            )
            data = json.loads(reply_result)
            assert data["message"] == "this is a reply"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_sent_message_appears_in_get_messages(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-send-verify")
        try:
            sent = await _send_msg(nc_mcp, str(room["token"]), "verify me")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            messages = json.loads(result)
            msg_ids = [m["id"] for m in messages]
            assert sent["id"] in msg_ids
        finally:
            await _delete_room(nc_mcp, str(room["token"]))


# ---------------------------------------------------------------------------
# create_conversation
# ---------------------------------------------------------------------------


class TestCreateConversation:
    @pytest.mark.asyncio
    async def test_create_group_conversation(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_conversation", room_type=2, name="test-create-group")
        data = json.loads(result)
        try:
            assert data["type"] == "group"
            assert data["name"] == "test-create-group"
            assert "token" in data
        finally:
            await _delete_room(nc_mcp, str(data["token"]))

    @pytest.mark.asyncio
    async def test_create_public_conversation(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_conversation", room_type=3, name="test-create-public")
        data = json.loads(result)
        try:
            assert data["type"] == "public"
            assert data["name"] == "test-create-public"
        finally:
            await _delete_room(nc_mcp, str(data["token"]))

    @pytest.mark.asyncio
    async def test_create_returns_token(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_conversation", room_type=2, name="test-create-token")
        data = json.loads(result)
        try:
            assert isinstance(data["token"], str)
            assert len(str(data["token"])) > 0
        finally:
            await _delete_room(nc_mcp, str(data["token"]))

    @pytest.mark.asyncio
    async def test_invalid_room_type_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("create_conversation", room_type=99, name="invalid")

    @pytest.mark.asyncio
    async def test_created_conversation_appears_in_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_conversation", room_type=2, name="test-create-list-check")
        data = json.loads(result)
        try:
            list_result = await nc_mcp.call("list_conversations")
            conversations = json.loads(list_result)
            tokens = [c["token"] for c in conversations]
            assert data["token"] in tokens
        finally:
            await _delete_room(nc_mcp, str(data["token"]))

    @pytest.mark.asyncio
    async def test_creator_is_participant(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_conversation", room_type=2, name="test-creator-part")
        data = json.loads(result)
        try:
            participants = json.loads(await nc_mcp.call("get_participants", token=str(data["token"])))
            actor_ids = [p["actor_id"] for p in participants]
            assert "admin" in actor_ids
        finally:
            await _delete_room(nc_mcp, str(data["token"]))


# ---------------------------------------------------------------------------
# delete_message
# ---------------------------------------------------------------------------


class TestDeleteMessage:
    @pytest.mark.asyncio
    async def test_delete_own_message(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-delete-msg")
        try:
            sent = await _send_msg(nc_mcp, str(room["token"]), "to be deleted")
            result = await nc_mcp.call("delete_message", token=str(room["token"]), message_id=int(sent["id"]))
            assert "deleted" in result.lower()
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_deleted_message_shows_system_message(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-del-system")
        try:
            sent = await _send_msg(nc_mcp, str(room["token"]), "delete me")
            await nc_mcp.call("delete_message", token=str(room["token"]), message_id=int(sent["id"]))
            messages = json.loads(await nc_mcp.call("get_messages", token=str(room["token"])))
            # After deletion, a system message "message_deleted" should appear
            system_msgs = [m for m in messages if m["system_message"] == "message_deleted"]
            assert len(system_msgs) >= 1
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_delete_nonexistent_message_raises(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-del-nonexist")
        try:
            with pytest.raises(ToolError):
                await nc_mcp.call("delete_message", token=str(room["token"]), message_id=999999999)
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_delete_in_nonexistent_conversation_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("delete_message", token="nonexistent-xyz-12345", message_id=1)


# ---------------------------------------------------------------------------
# leave_conversation
# ---------------------------------------------------------------------------


class TestLeaveConversation:
    @pytest.mark.asyncio
    async def test_leave_group_conversation(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-leave", room_type=3)
        try:
            result = await nc_mcp.call("leave_conversation", token=str(room["token"]))
            assert "left" in result.lower()
        finally:
            with contextlib.suppress(Exception):
                await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_leave_removes_from_list(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-leave-list", room_type=3)
        try:
            await nc_mcp.call("leave_conversation", token=str(room["token"]))
            list_result = await nc_mcp.call("list_conversations")
            conversations = json.loads(list_result)
            tokens = [c["token"] for c in conversations]
            assert room["token"] not in tokens
        finally:
            with contextlib.suppress(Exception):
                await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_leave_nonexistent_conversation_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("leave_conversation", token="nonexistent-xyz-12345")


# ---------------------------------------------------------------------------
# Permission enforcement
# ---------------------------------------------------------------------------


class TestTalkPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list_conversations(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_conversations")
        data = json.loads(result)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_send_message(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("send_message", token="x", message="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_create_conversation(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("create_conversation", room_type=2, name="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_delete_message(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("delete_message", token="x", message_id=1)

    @pytest.mark.asyncio
    async def test_read_only_blocks_leave_conversation(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("leave_conversation", token="x")

    @pytest.mark.asyncio
    async def test_write_allows_send_but_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        room = await _create_room(nc_mcp_write, "test-perm-write", room_type=2)
        try:
            # WRITE should allow creating and sending
            sent = await _send_msg(nc_mcp_write, str(room["token"]), "write-ok")
            assert sent["message"] == "write-ok"

            # But block deleting messages
            with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
                await nc_mcp_write.call("delete_message", token=str(room["token"]), message_id=1)
        finally:
            await _delete_room(nc_mcp_write, str(room["token"]))
