"""Integration tests for Talk tools against a real Nextcloud instance.

Tests call MCP tools by name through the full tool stack including
permission checks, argument parsing, and JSON serialization.
"""

import contextlib
import json
import re
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


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


def _parse_compact_messages(result: str) -> list[str]:
    """Parse compact message lines, excluding the pagination footer."""
    return [line for line in result.strip().split("\n") if line and not line.startswith("---")]


def _extract_message_id(line: str) -> int:
    """Extract the message ID from a compact line like '[123] admin: hello'."""
    match = re.match(r"\[(\d+)\]", line)
    assert match, f"Cannot extract message ID from: {line}"
    return int(match.group(1))


class TestListConversations:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_conversations", limit=200)
        data = json.loads(result)["data"]
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_includes_created_conversation(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-list-conv")
        try:
            result = await nc_mcp.call("list_conversations", limit=200)
            data = json.loads(result)["data"]
            tokens = [c["token"] for c in data]
            assert room["token"] in tokens
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_conversation_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-fields")
        try:
            result = await nc_mcp.call("list_conversations", limit=200)
            data = json.loads(result)["data"]
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
            result = await nc_mcp.call("list_conversations", limit=200)
            data = json.loads(result)["data"]
            group = next(c for c in data if c["token"] == group_room["token"])
            public = next(c for c in data if c["token"] == public_room["token"])
            assert group["type"] == "group"
            assert public["type"] == "public"
        finally:
            await _delete_room(nc_mcp, str(group_room["token"]))
            await _delete_room(nc_mcp, str(public_room["token"]))


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


class TestGetMessages:
    @pytest.mark.asyncio
    async def test_returns_compact_format(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-compact")
        try:
            await _send_msg(nc_mcp, str(room["token"]), "hello world")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            lines = _parse_compact_messages(result)
            assert len(lines) >= 1
            # Each line should match [id] author: message
            assert re.match(r"\[\d+\] .+: .+", lines[0])
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_message_content_in_compact_line(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-content")
        try:
            await _send_msg(nc_mcp, str(room["token"]), "specific test message")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            assert "specific test message" in result
            assert "admin:" in result
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
            lines = _parse_compact_messages(result)
            # Messages are newest first, so "third" should be before "first"
            texts = [line.split(": ", 1)[1] for line in lines]
            assert texts == ["third", "second", "first"]
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_system_messages_filtered_by_default(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-no-system")
        try:
            await _send_msg(nc_mcp, str(room["token"]), "user message")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            # System messages like "You created the conversation" should NOT appear
            assert "created the conversation" not in result
            assert "user message" in result
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_system_messages_included_when_requested(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-with-system")
        try:
            result = await nc_mcp.call("get_messages", token=str(room["token"]), include_system=True)
            # System message "You created the conversation" should appear
            assert "created the conversation" in result
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_limit_parameter(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-msg-limit")
        try:
            for i in range(5):
                await _send_msg(nc_mcp, str(room["token"]), f"msg-{i}")
            result = await nc_mcp.call("get_messages", token=str(room["token"]), limit=2)
            lines = _parse_compact_messages(result)
            assert len(lines) == 2
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_pagination_info_in_footer(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-pagination-info")
        try:
            await _send_msg(nc_mcp, str(room["token"]), "test msg")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            assert "before_message_id=" in result
            assert "messages" in result
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_pagination_with_before_message_id(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-paginate")
        try:
            # Send 6 messages
            for i in range(6):
                await _send_msg(nc_mcp, str(room["token"]), f"page-msg-{i}")

            # Get first batch (3 newest)
            result1 = await nc_mcp.call("get_messages", token=str(room["token"]), limit=3)
            lines1 = _parse_compact_messages(result1)
            assert len(lines1) == 3

            # Get oldest ID from first batch for pagination
            oldest_id = min(_extract_message_id(line) for line in lines1)

            # Get next batch using before_message_id
            result2 = await nc_mcp.call("get_messages", token=str(room["token"]), limit=3, before_message_id=oldest_id)
            lines2 = _parse_compact_messages(result2)
            assert len(lines2) >= 1

            # Messages should not overlap
            ids1 = {_extract_message_id(line) for line in lines1}
            ids2 = {_extract_message_id(line) for line in lines2}
            assert ids1.isdisjoint(ids2), "Paginated results should not overlap"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_nonexistent_conversation_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_messages", token="nonexistent-xyz-12345")

    @pytest.mark.asyncio
    async def test_empty_conversation_no_chat_messages(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-empty-chat")
        try:
            # With system messages filtered, a new conversation has no messages
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            lines = _parse_compact_messages(result)
            assert len(lines) == 0
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_compact_format_saves_space(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-compact-size")
        try:
            for i in range(10):
                await _send_msg(nc_mcp, str(room["token"]), f"message number {i}")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            # Compact format should be much smaller than JSON — well under 2KB for 10 msgs
            assert len(result) < 2000
        finally:
            await _delete_room(nc_mcp, str(room["token"]))


class TestGetParticipants:
    @pytest.mark.asyncio
    async def test_returns_participants_list(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-participants")
        try:
            result = await nc_mcp.call("get_participants", token=str(room["token"]), limit=200)
            data: list[Any] = json.loads(result)["data"]
            assert isinstance(data, list)
            assert len(data) >= 1
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_creator_is_owner(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-owner")
        try:
            result = await nc_mcp.call("get_participants", token=str(room["token"]), limit=200)
            data = json.loads(result)["data"]
            admin = next(p for p in data if p["actor_id"] == "admin")
            assert admin["participant_type"] == "owner"
        finally:
            await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_participant_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-part-fields")
        try:
            result = await nc_mcp.call("get_participants", token=str(room["token"]), limit=200)
            data = json.loads(result)["data"]
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
            await _send_msg(nc_mcp, str(room["token"]), "verify me")
            result = await nc_mcp.call("get_messages", token=str(room["token"]))
            assert "verify me" in result
        finally:
            await _delete_room(nc_mcp, str(room["token"]))


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
            list_result = await nc_mcp.call("list_conversations", limit=200)
            conversations = json.loads(list_result)["data"]
            tokens = [c["token"] for c in conversations]
            assert data["token"] in tokens
        finally:
            await _delete_room(nc_mcp, str(data["token"]))

    @pytest.mark.asyncio
    async def test_creator_is_participant(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_conversation", room_type=2, name="test-creator-part")
        data = json.loads(result)
        try:
            raw = await nc_mcp.call("get_participants", token=str(data["token"]), limit=200)
            actor_ids = [p["actor_id"] for p in json.loads(raw)["data"]]
            assert "admin" in actor_ids
        finally:
            await _delete_room(nc_mcp, str(data["token"]))


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
    async def test_deleted_message_replaced_in_chat(self, nc_mcp: McpTestHelper) -> None:
        room = await _create_room(nc_mcp, "test-del-replaced")
        try:
            sent = await _send_msg(nc_mcp, str(room["token"]), "delete me")
            await nc_mcp.call("delete_message", token=str(room["token"]), message_id=int(sent["id"]))
            # With include_system, we should see "message_deleted" system message
            result = await nc_mcp.call("get_messages", token=str(room["token"]), include_system=True)
            assert "deleted" in result.lower()
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
            list_result = await nc_mcp.call("list_conversations", limit=200)
            conversations = json.loads(list_result)["data"]
            tokens = [c["token"] for c in conversations]
            assert room["token"] not in tokens
        finally:
            with contextlib.suppress(Exception):
                await _delete_room(nc_mcp, str(room["token"]))

    @pytest.mark.asyncio
    async def test_leave_nonexistent_conversation_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("leave_conversation", token="nonexistent-xyz-12345")


class TestTalkPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list_conversations(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_conversations", limit=200)
        data = json.loads(result)["data"]
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
