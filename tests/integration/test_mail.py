"""Integration tests for Mail tools against a real Nextcloud instance with smtp4dev."""

import asyncio
import json
import os
import smtplib
import subprocess
import time
import urllib.request
from email.mime.text import MIMEText
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

SMTP4DEV_HOST = os.environ.get("SMTP4DEV_HOST", "smtp4dev.ncmcp")
SMTP4DEV_HTTP_PORT = int(os.environ.get("SMTP4DEV_HTTP_PORT", "80"))
SMTP4DEV_API = f"http://{SMTP4DEV_HOST}:{SMTP4DEV_HTTP_PORT}/smtp4dev/api"
SMTP_HOST = SMTP4DEV_HOST
SMTP_PORT = int(os.environ.get("SMTP4DEV_SMTP_PORT", "25"))
MAIL_RECIPIENT = os.environ.get("MAIL_RECIPIENT", f"test@{SMTP4DEV_HOST}")
UNIQUE = "mcp-test-mail"


def _smtp4dev_delete_all() -> None:
    """Delete all messages from smtp4dev via its REST API."""
    req = urllib.request.Request(f"{SMTP4DEV_API}/messages/*", method="DELETE")
    urllib.request.urlopen(req, timeout=10)


def _smtp4dev_list_messages() -> list[dict[str, Any]]:
    """List all messages in smtp4dev via its REST API."""
    data = json.loads(urllib.request.urlopen(f"{SMTP4DEV_API}/messages", timeout=10).read())
    return data.get("results", [])


def _send_test_email(subject: str, body: str = "test body", to: str = MAIL_RECIPIENT) -> None:
    """Send a test email directly via SMTP to smtp4dev."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = "external-sender@test.local"
    msg["To"] = to
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        smtp.sendmail("external-sender@test.local", [to], msg.as_string())


def _sync_mail_account(account_id: int) -> None:
    """Trigger a mailbox sync so new messages appear in the NC database."""
    nc_server_dir = os.environ.get("NC_SERVER_DIR", "")
    if nc_server_dir:
        args = ["php", "occ", "mail:account:sync", str(account_id)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False, cwd=nc_server_dir)
    else:
        container = os.environ.get("NC_CONTAINER", "ncmcp-nextcloud-1")
        cmd = f"php occ mail:account:sync {account_id}"
        result = subprocess.run(
            ["docker", "exec", container, "su", "-s", "/bin/bash", "www-data", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    if result.returncode != 0:
        raise AssertionError(f"mail:account:sync {account_id} failed: {result.stderr}")


async def _get_account_id(nc_mcp: McpTestHelper) -> int:
    """Get the test mail account ID."""
    result = await nc_mcp.call("list_mail_accounts")
    accounts: list[dict[str, Any]] = json.loads(result)
    if not accounts:
        pytest.skip("No mail account configured")
    configured_id = os.environ.get("MAIL_ACCOUNT_ID")
    if configured_id is not None:
        account = next((a for a in accounts if a["id"] == int(configured_id)), None)
        if account is None:
            pytest.skip(f"Configured MAIL_ACCOUNT_ID={configured_id} not found")
        return account["id"]
    return accounts[0]["id"]


async def _get_inbox_id(nc_mcp: McpTestHelper, account_id: int) -> int:
    """Get the INBOX mailbox ID for an account."""
    result = await nc_mcp.call("list_mailboxes", account_id=account_id)
    mailboxes = json.loads(result)
    inbox = next((mb for mb in mailboxes if mb["name"] == "INBOX"), None)
    if inbox is None:
        pytest.skip("INBOX not found")
    return inbox["id"]


class TestListMailAccounts:
    @pytest.mark.asyncio
    async def test_returns_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_mail_accounts")
        accounts = json.loads(result)
        assert isinstance(accounts, list)

    @pytest.mark.asyncio
    async def test_account_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_mail_accounts")
        accounts = json.loads(result)
        assert len(accounts) >= 1
        account = accounts[0]
        assert "id" in account
        assert "email" in account
        assert isinstance(account["id"], int)
        assert "@" in account["email"]

    @pytest.mark.asyncio
    async def test_account_email_matches(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_mail_accounts")
        accounts = json.loads(result)
        emails = [a["email"] for a in accounts]
        assert any("smtp4dev" in e or "test" in e for e in emails)


class TestListMailboxes:
    @pytest.mark.asyncio
    async def test_returns_list(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        result = await nc_mcp.call("list_mailboxes", account_id=account_id)
        mailboxes: list[dict[str, Any]] = json.loads(result)
        assert isinstance(mailboxes, list)
        assert len(mailboxes) >= 1

    @pytest.mark.asyncio
    async def test_inbox_exists(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        result = await nc_mcp.call("list_mailboxes", account_id=account_id)
        mailboxes = json.loads(result)
        names = [mb["name"] for mb in mailboxes]
        assert "INBOX" in names

    @pytest.mark.asyncio
    async def test_mailbox_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        result = await nc_mcp.call("list_mailboxes", account_id=account_id)
        mailboxes = json.loads(result)
        inbox = next(mb for mb in mailboxes if mb["name"] == "INBOX")
        assert "id" in inbox
        assert isinstance(inbox["id"], int)
        assert "name" in inbox
        assert "account_id" in inbox
        assert inbox["account_id"] == account_id

    @pytest.mark.asyncio
    async def test_inbox_has_special_role(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        result = await nc_mcp.call("list_mailboxes", account_id=account_id)
        mailboxes = json.loads(result)
        inbox = next(mb for mb in mailboxes if mb["name"] == "INBOX")
        assert inbox["special_role"] == "inbox"

    @pytest.mark.asyncio
    async def test_nonexistent_account_fails(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("list_mailboxes", account_id=999999)


class TestListMailMessages:
    @pytest.mark.asyncio
    async def test_returns_data_and_pagination(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        result = await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id)
        parsed = json.loads(result)
        assert "data" in parsed
        assert "pagination" in parsed
        assert isinstance(parsed["data"], list)
        assert "count" in parsed["pagination"]
        assert "has_more" in parsed["pagination"]

    @pytest.mark.asyncio
    async def test_messages_have_required_fields(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        _send_test_email(f"{UNIQUE}-fields")
        _sync_mail_account(account_id)
        result = await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id)
        parsed = json.loads(result)
        assert len(parsed["data"]) >= 1
        msg = parsed["data"][0]
        assert "id" in msg
        assert "subject" in msg
        assert "date" in msg
        assert "from" in msg
        assert "to" in msg
        assert "mailbox_id" in msg

    @pytest.mark.asyncio
    async def test_limit_parameter(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        for i in range(3):
            _send_test_email(f"{UNIQUE}-limit-{i}")
        _sync_mail_account(account_id)
        result = await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=2)
        parsed = json.loads(result)
        assert len(parsed["data"]) <= 2
        assert parsed["pagination"]["count"] <= 2

    @pytest.mark.asyncio
    async def test_limit_clamped_to_range(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        result = await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=200)
        parsed = json.loads(result)
        assert parsed["pagination"]["count"] <= 100

    @pytest.mark.asyncio
    async def test_cursor_pagination(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        for i in range(3):
            _send_test_email(f"{UNIQUE}-cursor-{i}")
        _sync_mail_account(account_id)
        first_page = json.loads(await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=2))
        if first_page["pagination"]["has_more"]:
            min_id = min(m["id"] for m in first_page["data"])
            second_page = json.loads(
                await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=2, cursor=min_id)
            )
            first_ids = {m["id"] for m in first_page["data"]}
            second_ids = {m["id"] for m in second_page["data"]}
            assert not first_ids.intersection(second_ids), "Pages should not overlap"

    @pytest.mark.asyncio
    async def test_message_from_field_structure(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        _send_test_email(f"{UNIQUE}-from-struct")
        _sync_mail_account(account_id)
        result = await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=5)
        parsed = json.loads(result)
        assert len(parsed["data"]) >= 1
        msg = parsed["data"][0]
        assert isinstance(msg["from"], list)
        assert len(msg["from"]) >= 1
        assert "email" in msg["from"][0]

    @pytest.mark.asyncio
    async def test_nonexistent_mailbox_fails(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("list_mail_messages", mailbox_id=999999)


class TestGetMailMessage:
    @pytest.mark.asyncio
    async def test_returns_full_message(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        _send_test_email(f"{UNIQUE}-get-full", body="Hello from integration test")
        _sync_mail_account(account_id)
        messages = json.loads(await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=5))
        target = next((m for m in messages["data"] if UNIQUE in str(m.get("subject", ""))), None)
        assert target is not None, "Test message not found in inbox"
        result = await nc_mcp.call("get_mail_message", message_id=target["id"])
        msg = json.loads(result)
        assert "id" in msg
        assert "subject" in msg
        assert "body" in msg
        assert "from" in msg
        assert "to" in msg

    @pytest.mark.asyncio
    async def test_body_contains_content(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        _send_test_email(f"{UNIQUE}-body-check", body="unique-body-content-12345")
        _sync_mail_account(account_id)
        messages = json.loads(await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=5))
        target = next((m for m in messages["data"] if "body-check" in str(m.get("subject", ""))), None)
        assert target is not None
        result = await nc_mcp.call("get_mail_message", message_id=target["id"])
        msg = json.loads(result)
        assert "unique-body-content-12345" in msg["body"]

    @pytest.mark.asyncio
    async def test_subject_matches(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        subject = f"{UNIQUE}-subject-match-{int(time.time())}"
        _send_test_email(subject, body="test")
        _sync_mail_account(account_id)
        messages = json.loads(await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=5))
        target = next((m for m in messages["data"] if subject in str(m.get("subject", ""))), None)
        assert target is not None
        result = await nc_mcp.call("get_mail_message", message_id=target["id"])
        msg = json.loads(result)
        assert msg["subject"] == subject

    @pytest.mark.asyncio
    async def test_from_field(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        inbox_id = await _get_inbox_id(nc_mcp, account_id)
        _send_test_email(f"{UNIQUE}-from-check")
        _sync_mail_account(account_id)
        messages = json.loads(await nc_mcp.call("list_mail_messages", mailbox_id=inbox_id, limit=5))
        target = next((m for m in messages["data"] if "from-check" in str(m.get("subject", ""))), None)
        assert target is not None
        result = await nc_mcp.call("get_mail_message", message_id=target["id"])
        msg = json.loads(result)
        assert isinstance(msg["from"], list)
        assert any("external-sender@test.local" in f.get("email", "") for f in msg["from"])

    @pytest.mark.asyncio
    async def test_nonexistent_message_fails(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_mail_message", message_id=999999)


class TestSendMail:
    @pytest.mark.asyncio
    async def test_send_basic_email_and_verify_delivery(self, nc_mcp: McpTestHelper) -> None:
        _smtp4dev_delete_all()
        account_id = await _get_account_id(nc_mcp)
        result = await nc_mcp.call(
            "send_mail",
            account_id=account_id,
            to=["recipient@test.local"],
            subject=f"{UNIQUE}-send-basic",
            body="Hello from MCP!",
        )
        assert "sent" in result.lower()
        assert "recipient@test.local" in result
        await asyncio.sleep(1)
        messages = _smtp4dev_list_messages()
        subjects = [m.get("subject", "") for m in messages]
        assert any(f"{UNIQUE}-send-basic" in s for s in subjects)

    @pytest.mark.asyncio
    async def test_send_with_cc_bcc_and_multiple_recipients(self, nc_mcp: McpTestHelper) -> None:
        _smtp4dev_delete_all()
        account_id = await _get_account_id(nc_mcp)
        result = await nc_mcp.call(
            "send_mail",
            account_id=account_id,
            to=["first@test.local", "second@test.local"],
            cc=["cc@test.local"],
            bcc=["bcc@test.local"],
            subject=f"{UNIQUE}-multi",
            body="Multi-recipient test with CC and BCC",
        )
        assert "sent" in result.lower()

    @pytest.mark.asyncio
    async def test_send_html_email(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        result = await nc_mcp.call(
            "send_mail",
            account_id=account_id,
            to=["html@test.local"],
            subject=f"{UNIQUE}-html",
            body="<h1>Hello</h1><p>HTML email</p>",
            is_html=True,
        )
        assert "sent" in result.lower()

    @pytest.mark.asyncio
    async def test_send_empty_to_raises(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        with pytest.raises(ToolError):
            await nc_mcp.call(
                "send_mail",
                account_id=account_id,
                to=[],
                subject="test",
                body="body",
            )

    @pytest.mark.asyncio
    async def test_send_nonexistent_account_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call(
                "send_mail",
                account_id=999999,
                to=["x@test.local"],
                subject="test",
                body="body",
            )


class TestMailPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list_accounts(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_mail_accounts")
        accounts = json.loads(result)
        assert isinstance(accounts, list)

    @pytest.mark.asyncio
    async def test_read_only_allows_list_mailboxes(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_mail_accounts")
        accounts = json.loads(result)
        if not accounts:
            pytest.skip("No mail accounts")
        result = await nc_mcp_read_only.call("list_mailboxes", account_id=accounts[0]["id"])
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_send(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call(
                "send_mail",
                account_id=1,
                to=["x@test.local"],
                subject="blocked",
                body="no",
            )

    @pytest.mark.asyncio
    async def test_write_allows_send(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call("list_mail_accounts")
        accounts = json.loads(result)
        if not accounts:
            pytest.skip("No mail accounts")
        result = await nc_mcp_write.call(
            "send_mail",
            account_id=accounts[0]["id"],
            to=["perm-test@test.local"],
            subject=f"{UNIQUE}-perm",
            body="permission test",
        )
        assert "sent" in result.lower()
