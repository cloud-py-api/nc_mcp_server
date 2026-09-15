"""Unit tests for the Mail triage tools: argument validation, request building and output formatting."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import mail

TAG = {"id": 4, "userId": "admin", "displayName": "Needs Reply", "imapLabel": "$needs_reply", "color": "#0082c9"}


@pytest.fixture
def mcp_with_mock_client(monkeypatch: pytest.MonkeyPatch) -> tuple[FastMCP, MagicMock]:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock_client = MagicMock()
    mock_client.app_request_json = AsyncMock()
    mock_client.ocs_get = AsyncMock()
    monkeypatch.setattr(mail, "get_client", lambda: mock_client)
    mcp = FastMCP("test-mail")
    mail.register(mcp)
    return mcp, mock_client


async def _call(mcp: FastMCP, tool: str, **args: Any) -> str:
    return await mcp._tool_manager.call_tool(tool, args)


class TestFormatTags:
    def test_object_keyed_by_label(self) -> None:
        assert mail._format_tags({"$needs_reply": TAG}) == [
            {"display_name": "Needs Reply", "imap_label": "$needs_reply"}
        ]

    def test_empty_list_when_message_has_no_tags(self) -> None:
        assert mail._format_tags([]) == []

    def test_missing_or_unexpected_value(self) -> None:
        assert mail._format_tags(None) == []
        assert mail._format_tags("oops") == []
        assert mail._format_tags(["not-a-tag"]) == []


class TestMessageOutputIncludesTags:
    @pytest.mark.asyncio
    async def test_list_mail_messages_includes_tags(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = [
            {"databaseId": 11, "subject": "tagged", "flags": {"seen": True}, "tags": {"$needs_reply": TAG}},
            {"databaseId": 10, "subject": "untagged", "flags": {}, "tags": []},
        ]
        data = json.loads(await _call(mcp, "list_mail_messages", mailbox_id=3))["data"]
        assert data[0]["tags"] == [{"display_name": "Needs Reply", "imap_label": "$needs_reply"}]
        assert "tags" not in data[1]

    @pytest.mark.asyncio
    async def test_get_mail_message_reads_tags_from_app_route(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.ocs_get.return_value = {"id": 11, "subject": "tagged", "body": "hi", "flags": {"seen": True}}
        client.app_request_json.return_value = {"databaseId": 11, "tags": {"$needs_reply": TAG}}
        result = json.loads(await _call(mcp, "get_mail_message", message_id=11))
        client.app_request_json.assert_awaited_once_with("GET", "mail/api/messages/11")
        assert result["tags"] == [{"display_name": "Needs Reply", "imap_label": "$needs_reply"}]
        assert result["flags"] == ["seen"]


class TestMoveMailMessage:
    @pytest.mark.asyncio
    async def test_sends_destination_and_warns_about_old_id(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock]
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.return_value = []
        result = await _call(mcp, "move_mail_message", message_id=5, destination_mailbox_id=9)
        client.app_request_json.assert_awaited_once_with(
            "POST", "mail/api/messages/5/move", json_data={"destFolderId": 9}
        )
        assert "no longer valid" in result

    @pytest.mark.asyncio
    async def test_forbidden_becomes_not_found_message(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.side_effect = NextcloudError("POST apps/mail/api/messages/5/move: Forbidden.", 403)
        with pytest.raises(ToolError, match=r"Message 5 or mailbox 9 was not found or is not accessible"):
            await _call(mcp, "move_mail_message", message_id=5, destination_mailbox_id=9)

    @pytest.mark.asyncio
    async def test_other_errors_pass_through(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.side_effect = NextcloudError("Mailbox 9 does not exist", 400)
        with pytest.raises(ToolError, match="Mailbox 9 does not exist"):
            await _call(mcp, "move_mail_message", message_id=5, destination_mailbox_id=9)


class TestSetMailMessageFlags:
    @pytest.mark.asyncio
    async def test_requires_at_least_one_flag(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        with pytest.raises(ToolError, match="at least one of seen, flagged or answered"):
            await _call(mcp, "set_mail_message_flags", message_id=5)
        client.app_request_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_only_passed_flags(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.return_value = []
        result = json.loads(await _call(mcp, "set_mail_message_flags", message_id=5, seen=False, answered=True))
        client.app_request_json.assert_awaited_once_with(
            "PUT", "mail/api/messages/5/flags", json_data={"flags": {"seen": False, "answered": True}}
        )
        assert result == {"message_id": 5, "flags": {"seen": False, "answered": True}}

    @pytest.mark.asyncio
    async def test_forbidden_becomes_not_found_message(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.side_effect = NextcloudError("Forbidden.", 403)
        with pytest.raises(ToolError, match="Message 5 was not found or is not accessible"):
            await _call(mcp, "set_mail_message_flags", message_id=5, flagged=True)


class TestCreateMailTag:
    @pytest.mark.asyncio
    async def test_creates_and_formats_tag(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.return_value = TAG
        result = json.loads(await _call(mcp, "create_mail_tag", display_name="  Needs Reply ", color="#0082c9"))
        client.app_request_json.assert_awaited_once_with(
            "POST", "mail/api/tags", json_data={"displayName": "Needs Reply", "color": "#0082c9"}
        )
        assert result == {"id": 4, "display_name": "Needs Reply", "imap_label": "$needs_reply", "color": "#0082c9"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("display_name", "color", "error"),
        [
            ("   ", "#0082c9", "must not be empty"),
            ("x" * 129, "#0082c9", "at most 128 characters"),
            ("Needs Reply", "red", "Invalid color 'red'"),
            ("Needs Reply", "#12345", "Invalid color"),
        ],
    )
    async def test_rejects_invalid_input(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock], display_name: str, color: str, error: str
    ) -> None:
        mcp, client = mcp_with_mock_client
        with pytest.raises(ToolError, match=error):
            await _call(mcp, "create_mail_tag", display_name=display_name, color=color)
        client.app_request_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_accepts_short_hex_color(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.return_value = {**TAG, "color": "#fff"}
        await _call(mcp, "create_mail_tag", display_name="Needs Reply", color="#fff")
        client.app_request_json.assert_awaited_once()


class TestMessageTagging:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool", "method"), [("add_mail_message_tag", "PUT"), ("remove_mail_message_tag", "DELETE")]
    )
    async def test_url_encodes_label(
        self, mcp_with_mock_client: tuple[FastMCP, MagicMock], tool: str, method: str
    ) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.return_value = TAG
        result = json.loads(await _call(mcp, tool, message_id=5, imap_label="$needs reply/x"))
        client.app_request_json.assert_awaited_once_with(
            method, "mail/api/messages/5/tags/%24needs%20reply%2Fx", json_data=None
        )
        assert result == {
            "message_id": 5,
            "tag": {"id": 4, "display_name": "Needs Reply", "imap_label": "$needs_reply", "color": "#0082c9"},
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["add_mail_message_tag", "remove_mail_message_tag"])
    async def test_rejects_empty_label(self, mcp_with_mock_client: tuple[FastMCP, MagicMock], tool: str) -> None:
        mcp, client = mcp_with_mock_client
        with pytest.raises(ToolError, match="imap_label must not be empty"):
            await _call(mcp, tool, message_id=5, imap_label="")
        client.app_request_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forbidden_points_to_create_mail_tag(self, mcp_with_mock_client: tuple[FastMCP, MagicMock]) -> None:
        mcp, client = mcp_with_mock_client
        client.app_request_json.side_effect = NextcloudError("Forbidden.", 403)
        with pytest.raises(ToolError, match=r"no tag with IMAP label '\$unknown'.*create_mail_tag"):
            await _call(mcp, "add_mail_message_tag", message_id=5, imap_label="$unknown")
