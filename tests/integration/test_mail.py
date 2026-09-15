"""Integration tests for Mail tools against a real Nextcloud instance.

The test Mail account reads mail from Dovecot over IMAP (STARTTLS) and sends through smtp4dev.
Incoming test messages are put straight into Dovecot's INBOX with IMAP APPEND, and smtp4dev's
REST API shows what send_mail delivered. smtp4dev's own IMAP server can't be used for reading:
it has no MOVE, no folders, and drops \\Flagged and keywords (tags).
"""

import asyncio
import contextlib
import email.utils
import imaplib
import json
import os
import ssl
import subprocess
import time
import urllib.request
import uuid
from collections.abc import AsyncGenerator
from email.mime.text import MIMEText
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

SMTP4DEV_HOST = os.environ.get("SMTP4DEV_HOST", "smtp4dev.ncmcp")
SMTP4DEV_HTTP_PORT = int(os.environ.get("SMTP4DEV_HTTP_PORT", "80"))
SMTP4DEV_API = f"http://{SMTP4DEV_HOST}:{SMTP4DEV_HTTP_PORT}/smtp4dev/api"
IMAP_HOST = os.environ.get("MAIL_IMAP_HOST", "dovecot.ncmcp")
IMAP_PORT = int(os.environ.get("MAIL_IMAP_PORT", "31143"))
IMAP_USER = os.environ.get("MAIL_IMAP_USER", "test")
IMAP_PASSWORD = os.environ.get("MAIL_IMAP_PASSWORD", "test")
MAIL_RECIPIENT = os.environ.get("MAIL_RECIPIENT", "test@localhost")
UNIQUE = "mcp-test-mail"
ARCHIVE_MAILBOX = "mcp-test-archive"


def _smtp4dev_delete_all() -> None:
    """Delete all messages from smtp4dev via its REST API."""
    req = urllib.request.Request(f"{SMTP4DEV_API}/messages/*", method="DELETE")
    urllib.request.urlopen(req, timeout=10)


def _smtp4dev_list_messages() -> list[dict[str, Any]]:
    """List all messages in smtp4dev via its REST API."""
    data = json.loads(urllib.request.urlopen(f"{SMTP4DEV_API}/messages", timeout=10).read())
    return data.get("results", [])


def _deliver_test_email(subject: str, body: str = "test body") -> None:
    """Put a test message straight into the test account's INBOX with IMAP APPEND."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = "external-sender@test.local"
    msg["To"] = MAIL_RECIPIENT
    msg["Date"] = email.utils.formatdate()
    msg["Message-ID"] = email.utils.make_msgid(domain="test.local")
    tls = ssl.create_default_context()
    tls.check_hostname = False
    tls.verify_mode = ssl.CERT_NONE
    imap = imaplib.IMAP4(IMAP_HOST, IMAP_PORT, timeout=10)
    try:
        imap.starttls(ssl_context=tls)
        imap.login(IMAP_USER, IMAP_PASSWORD)
        imap.append("INBOX", None, imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    finally:
        with contextlib.suppress(Exception):
            imap.logout()


def _sync_mail_account(account_id: int) -> None:
    """Trigger a mailbox sync so new messages appear in the NC database."""
    container = os.environ.get("NC_CONTAINER", "ncmcp-nextcloud-1")
    cmd = f"php occ mail:account:sync {account_id}"
    result = subprocess.run(
        ["docker", "exec", container, "su", "-s", "/bin/bash", "www-data", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"mail:account:sync {account_id} failed: {result.stderr}")


async def _sync_mailbox(nc_mcp: McpTestHelper, mailbox_id: int) -> None:
    """Sync one mailbox; mail:account:sync skips folders that have no background sync."""
    await nc_mcp.client.app_request_json(
        "POST", f"mail/api/mailboxes/{mailbox_id}/sync", json_data={"ids": [], "init": True}
    )


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


async def _find_message(nc_mcp: McpTestHelper, mailbox_id: int, subject: str) -> dict[str, Any] | None:
    """Find a message by exact subject among the newest messages of a mailbox."""
    listing = json.loads(await nc_mcp.call("list_mail_messages", mailbox_id=mailbox_id, limit=20))
    return next((m for m in listing["data"] if m.get("subject") == subject), None)


async def _deliver_and_find(nc_mcp: McpTestHelper, label: str) -> tuple[int, int, dict[str, Any]]:
    """Deliver a uniquely named message to INBOX, sync, and return (account_id, inbox_id, message)."""
    account_id = await _get_account_id(nc_mcp)
    inbox_id = await _get_inbox_id(nc_mcp, account_id)
    subject = f"{UNIQUE}-{label}-{uuid.uuid4().hex[:8]}"
    _deliver_test_email(subject)
    _sync_mail_account(account_id)
    message = await _find_message(nc_mcp, inbox_id, subject)
    assert message is not None, f"Delivered message '{subject}' not found in INBOX"
    return account_id, inbox_id, message


async def _delete_tag(nc_mcp: McpTestHelper, account_id: int, tag_id: int) -> None:
    with contextlib.suppress(Exception):
        await nc_mcp.client.app_request_json("DELETE", f"mail/api/tags/{account_id}/delete/{tag_id}")


def _tag_ref(tag: dict[str, Any]) -> dict[str, Any]:
    return {"display_name": tag["display_name"], "imap_label": tag["imap_label"]}


@pytest.fixture
async def archive_mailbox(nc_mcp: McpTestHelper) -> AsyncGenerator[int]:
    """A scratch mailbox to move messages into, deleted with its messages after the test."""
    account_id = await _get_account_id(nc_mcp)
    mailboxes = json.loads(await nc_mcp.call("list_mailboxes", account_id=account_id))
    existing = next((mb for mb in mailboxes if mb["name"] == ARCHIVE_MAILBOX), None)
    if existing is not None:
        mailbox_id: int = existing["id"]
    else:
        created = await nc_mcp.client.app_request_json(
            "POST", "mail/api/mailboxes", json_data={"accountId": account_id, "name": ARCHIVE_MAILBOX}
        )
        mailbox_id = created["databaseId"]
    yield mailbox_id
    with contextlib.suppress(Exception):
        await nc_mcp.client.app_request_json("DELETE", f"mail/api/mailboxes/{mailbox_id}")


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
        assert MAIL_RECIPIENT in emails


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
        _deliver_test_email(f"{UNIQUE}-fields")
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
            _deliver_test_email(f"{UNIQUE}-limit-{i}")
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
            _deliver_test_email(f"{UNIQUE}-cursor-{i}")
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
        _deliver_test_email(f"{UNIQUE}-from-struct")
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
        _deliver_test_email(f"{UNIQUE}-get-full", body="Hello from integration test")
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
        _deliver_test_email(f"{UNIQUE}-body-check", body="unique-body-content-12345")
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
        _deliver_test_email(subject, body="test")
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
        _deliver_test_email(f"{UNIQUE}-from-check")
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


class TestMoveMailMessage:
    @pytest.mark.asyncio
    async def test_move_to_other_mailbox(self, nc_mcp: McpTestHelper, archive_mailbox: int) -> None:
        _, inbox_id, message = await _deliver_and_find(nc_mcp, "move")
        result = await nc_mcp.call(
            "move_mail_message", message_id=message["id"], destination_mailbox_id=archive_mailbox
        )
        assert "no longer valid" in result
        assert await _find_message(nc_mcp, inbox_id, message["subject"]) is None
        await _sync_mailbox(nc_mcp, archive_mailbox)
        moved = await _find_message(nc_mcp, archive_mailbox, message["subject"])
        assert moved is not None, "Moved message not found in the destination mailbox"
        assert moved["id"] != message["id"]
        assert moved["mailbox_id"] == archive_mailbox
        full = json.loads(await nc_mcp.call("get_mail_message", message_id=moved["id"]))
        assert full["subject"] == message["subject"]
        with pytest.raises(ToolError):
            await nc_mcp.call("get_mail_message", message_id=message["id"])

    @pytest.mark.asyncio
    async def test_move_keeps_flags(self, nc_mcp: McpTestHelper, archive_mailbox: int) -> None:
        _, _, message = await _deliver_and_find(nc_mcp, "move-flags")
        await nc_mcp.call("set_mail_message_flags", message_id=message["id"], flagged=True)
        await nc_mcp.call("move_mail_message", message_id=message["id"], destination_mailbox_id=archive_mailbox)
        await _sync_mailbox(nc_mcp, archive_mailbox)
        moved = await _find_message(nc_mcp, archive_mailbox, message["subject"])
        assert moved is not None
        assert "flagged" in moved.get("flags", [])

    @pytest.mark.asyncio
    async def test_nonexistent_message_raises(self, nc_mcp: McpTestHelper, archive_mailbox: int) -> None:
        with pytest.raises(ToolError, match="was not found or is not accessible"):
            await nc_mcp.call("move_mail_message", message_id=999999999, destination_mailbox_id=archive_mailbox)

    @pytest.mark.asyncio
    async def test_nonexistent_destination_raises(self, nc_mcp: McpTestHelper) -> None:
        _, _, message = await _deliver_and_find(nc_mcp, "move-nowhere")
        with pytest.raises(ToolError, match="does not exist"):
            await nc_mcp.call("move_mail_message", message_id=message["id"], destination_mailbox_id=999999999)


class TestSetMailMessageFlags:
    @pytest.mark.asyncio
    async def test_set_and_clear_seen_and_flagged(self, nc_mcp: McpTestHelper) -> None:
        _, inbox_id, message = await _deliver_and_find(nc_mcp, "flags")
        assert "seen" not in message.get("flags", [])
        result = json.loads(
            await nc_mcp.call("set_mail_message_flags", message_id=message["id"], seen=True, flagged=True)
        )
        assert result == {"message_id": message["id"], "flags": {"seen": True, "flagged": True}}
        listed = await _find_message(nc_mcp, inbox_id, message["subject"])
        assert listed is not None
        assert {"seen", "flagged"} <= set(listed.get("flags", []))

        await nc_mcp.call("set_mail_message_flags", message_id=message["id"], seen=False, flagged=False)
        listed = await _find_message(nc_mcp, inbox_id, message["subject"])
        assert listed is not None
        assert not {"seen", "flagged"} & set(listed.get("flags", []))

    @pytest.mark.asyncio
    async def test_only_passed_flags_change(self, nc_mcp: McpTestHelper) -> None:
        _, _, message = await _deliver_and_find(nc_mcp, "flags-partial")
        await nc_mcp.call("set_mail_message_flags", message_id=message["id"], seen=True, answered=True)
        await nc_mcp.call("set_mail_message_flags", message_id=message["id"], flagged=True)
        full = json.loads(await nc_mcp.call("get_mail_message", message_id=message["id"]))
        assert {"seen", "answered", "flagged"} <= set(full.get("flags", []))

    @pytest.mark.asyncio
    async def test_requires_at_least_one_flag(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="at least one of seen, flagged or answered"):
            await nc_mcp.call("set_mail_message_flags", message_id=1)

    @pytest.mark.asyncio
    async def test_nonexistent_message_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="was not found or is not accessible"):
            await nc_mcp.call("set_mail_message_flags", message_id=999999999, seen=True)


class TestMailTags:
    @pytest.mark.asyncio
    async def test_create_same_tag_twice_returns_same_label(self, nc_mcp: McpTestHelper) -> None:
        account_id = await _get_account_id(nc_mcp)
        name = f"{UNIQUE}-tag-{uuid.uuid4().hex[:8]}"
        first = json.loads(await nc_mcp.call("create_mail_tag", display_name=name, color="#0082c9"))
        try:
            assert first["display_name"] == name
            assert first["imap_label"].startswith("$")
            assert first["color"] == "#0082c9"
            second = json.loads(await nc_mcp.call("create_mail_tag", display_name=name, color="#ff0000"))
            assert second["id"] == first["id"]
            assert second["imap_label"] == first["imap_label"]
            assert second["color"] == "#0082c9"
        finally:
            await _delete_tag(nc_mcp, account_id, first["id"])

    @pytest.mark.asyncio
    async def test_tag_and_untag_message(self, nc_mcp: McpTestHelper) -> None:
        account_id, inbox_id, message = await _deliver_and_find(nc_mcp, "tags")
        tag = json.loads(
            await nc_mcp.call("create_mail_tag", display_name=f"{UNIQUE} tag {uuid.uuid4().hex[:8]}", color="#00aa00")
        )
        try:
            added = json.loads(
                await nc_mcp.call("add_mail_message_tag", message_id=message["id"], imap_label=tag["imap_label"])
            )
            assert added["message_id"] == message["id"]
            assert added["tag"]["imap_label"] == tag["imap_label"]

            listed = await _find_message(nc_mcp, inbox_id, message["subject"])
            assert listed is not None
            assert _tag_ref(tag) in listed.get("tags", [])
            full = json.loads(await nc_mcp.call("get_mail_message", message_id=message["id"]))
            assert _tag_ref(tag) in full.get("tags", [])

            again = json.loads(
                await nc_mcp.call("add_mail_message_tag", message_id=message["id"], imap_label=tag["imap_label"])
            )
            assert again["tag"]["id"] == tag["id"]

            removed = json.loads(
                await nc_mcp.call("remove_mail_message_tag", message_id=message["id"], imap_label=tag["imap_label"])
            )
            assert removed["tag"]["imap_label"] == tag["imap_label"]
            listed = await _find_message(nc_mcp, inbox_id, message["subject"])
            assert listed is not None
            assert _tag_ref(tag) not in listed.get("tags", [])
        finally:
            await _delete_tag(nc_mcp, account_id, tag["id"])

    @pytest.mark.asyncio
    async def test_unknown_label_raises(self, nc_mcp: McpTestHelper) -> None:
        _, _, message = await _deliver_and_find(nc_mcp, "tag-unknown")
        with pytest.raises(ToolError, match="create_mail_tag"):
            await nc_mcp.call("add_mail_message_tag", message_id=message["id"], imap_label="$mcp_test_no_such_tag")

    @pytest.mark.asyncio
    async def test_invalid_color_rejected(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="Invalid color"):
            await nc_mcp.call("create_mail_tag", display_name=f"{UNIQUE}-bad-color", color="red")

    @pytest.mark.asyncio
    async def test_empty_display_name_rejected(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="must not be empty"):
            await nc_mcp.call("create_mail_tag", display_name="  ", color="#0082c9")


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
    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("move_mail_message", {"message_id": 1, "destination_mailbox_id": 2}),
            ("set_mail_message_flags", {"message_id": 1, "seen": True}),
            ("create_mail_tag", {"display_name": "blocked", "color": "#000000"}),
            ("add_mail_message_tag", {"message_id": 1, "imap_label": "$blocked"}),
            ("remove_mail_message_tag", {"message_id": 1, "imap_label": "$blocked"}),
        ],
    )
    async def test_read_only_blocks_triage_tools(
        self, nc_mcp_read_only: McpTestHelper, tool: str, args: dict[str, object]
    ) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call(tool, **args)

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

    @pytest.mark.asyncio
    async def test_write_allows_triage_tools(
        self, nc_mcp: McpTestHelper, nc_mcp_write: McpTestHelper, archive_mailbox: int
    ) -> None:
        account_id, _, message = await _deliver_and_find(nc_mcp, "perm-triage")
        tag = json.loads(
            await nc_mcp_write.call(
                "create_mail_tag", display_name=f"{UNIQUE}-perm-{uuid.uuid4().hex[:8]}", color="#aa00aa"
            )
        )
        try:
            flags = json.loads(await nc_mcp_write.call("set_mail_message_flags", message_id=message["id"], seen=True))
            assert flags["flags"] == {"seen": True}
            label = tag["imap_label"]
            await nc_mcp_write.call("add_mail_message_tag", message_id=message["id"], imap_label=label)
            await nc_mcp_write.call("remove_mail_message_tag", message_id=message["id"], imap_label=label)
            moved = await nc_mcp_write.call(
                "move_mail_message", message_id=message["id"], destination_mailbox_id=archive_mailbox
            )
            assert "moved" in moved
        finally:
            await _delete_tag(nc_mcp, account_id, tag["id"])
