"""Mail tools — list accounts, mailboxes, messages; get message details; send email via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

MAIL_OCS = "apps/mail"


def _format_account(account: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"id": account["id"], "email": account["email"]}
    aliases = account.get("aliases")
    if aliases:
        result["aliases"] = [{"id": a["id"], "email": a["email"], "name": a.get("name")} for a in aliases]
    return result


def _format_mailbox(mailbox: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": mailbox.get("databaseId"),
        "name": mailbox.get("name"),
        "account_id": mailbox.get("accountId"),
        "display_name": mailbox.get("displayName"),
        "unread": mailbox.get("unread"),
        "special_role": mailbox.get("specialRole"),
    }


def _format_message_summary(msg: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": msg.get("databaseId"),
        "uid": msg.get("uid"),
        "subject": msg.get("subject"),
        "date": msg.get("dateInt"),
        "from": msg.get("from"),
        "to": msg.get("to"),
        "mailbox_id": msg.get("mailboxId"),
    }
    flags = msg.get("flags", {})
    active_flags = [k for k, v in flags.items() if v and k != "$notjunk"]
    if active_flags:
        result["flags"] = active_flags
    if msg.get("cc"):
        result["cc"] = msg["cc"]
    preview = msg.get("previewText")
    if preview:
        result["preview"] = preview
    if msg.get("attachments"):
        result["attachment_count"] = len(msg["attachments"])
    return result


def _format_message_full(msg: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": msg.get("id"),
        "subject": msg.get("subject"),
        "date": msg.get("dateInt"),
        "from": msg.get("from"),
        "to": msg.get("to"),
    }
    if msg.get("cc"):
        result["cc"] = msg["cc"]
    if msg.get("bcc"):
        result["bcc"] = msg["bcc"]
    message_id = msg.get("messageId")
    if message_id:
        result["message_id"] = message_id
    body = msg.get("body")
    if body is not None:
        result["body"] = body
    flags = msg.get("flags", {})
    active_flags = [k for k, v in flags.items() if v and k != "$notjunk"]
    if active_flags:
        result["flags"] = active_flags
    if msg.get("attachments"):
        result["attachments"] = [
            {"id": a.get("id"), "filename": a.get("filename"), "mime": a.get("mime"), "size": a.get("size")}
            for a in msg["attachments"]
        ]
    return result


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_mail_accounts() -> str:
        """List all email accounts configured in Nextcloud Mail.

        Returns the accounts and their aliases for the current user.
        Use the account ID to list mailboxes and send emails.

        Returns:
            JSON list of accounts, each with: id, email, aliases.
        """
        client = get_client()
        data = await client.ocs_get(f"{MAIL_OCS}/account/list")
        accounts = [_format_account(a) for a in data]
        return json.dumps(accounts, indent=2)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_mailboxes(account_id: int) -> str:
        """List mailboxes (folders) for a mail account.

        Returns all mailboxes like INBOX, Sent, Drafts, Trash, etc.
        Use the mailbox ID to list messages in that mailbox.

        Args:
            account_id: The mail account ID. Use list_mail_accounts to find it.

        Returns:
            JSON list of mailboxes, each with: id, name, display_name, unread count, special_role.
        """
        client = get_client()
        data = await client.ocs_get(f"{MAIL_OCS}/ocs/mailboxes", params={"accountId": str(account_id)})
        mailboxes = [_format_mailbox(mb) for mb in data]
        return json.dumps(mailboxes, indent=2)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_mail_messages(mailbox_id: int, limit: int = 20, cursor: int | None = None) -> str:
        """List messages in a mailbox, newest first.

        Returns message summaries (subject, sender, date, flags) without the full body.
        Use get_mail_message with a message ID to read the full content.

        Args:
            mailbox_id: The mailbox database ID. Use list_mailboxes to find it.
            limit: Maximum number of messages to return (1-100, default 20).
            cursor: Pagination cursor. Pass the smallest message ID from a previous
                    response to fetch older messages.

        Returns:
            JSON object with "data" (list of message summaries) and "pagination" metadata.
            Each message has: id, subject, date (unix timestamp), from, to, flags, preview.
        """
        client = get_client()
        limit = max(1, min(100, limit))
        params: dict[str, str] = {"limit": str(limit)}
        if cursor is not None:
            params["cursor"] = str(cursor)
        data = await client.ocs_get(f"{MAIL_OCS}/ocs/mailboxes/{mailbox_id}/messages", params=params)
        messages = [_format_message_summary(m) for m in data]
        pagination: dict[str, Any] = {
            "count": len(messages),
            "has_more": len(messages) == limit,
        }
        if messages:
            pagination["next_cursor"] = min(m["id"] for m in messages)
        result: dict[str, Any] = {"data": messages, "pagination": pagination}
        return json.dumps(result, indent=2)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_mail_message(message_id: int) -> str:
        """Get a full email message including its body.

        Retrieves the complete message with body text, message ID, and attachment metadata.

        Args:
            message_id: The message database ID. Use list_mail_messages to find it.

        Returns:
            JSON object with: id, subject, date, from, to, cc, bcc, message_id,
            body, flags, and attachments list (if any).
        """
        client = get_client()
        data = await client.ocs_get(f"{MAIL_OCS}/message/{message_id}")
        return json.dumps(_format_message_full(data), indent=2)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def send_mail(
        account_id: int,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        is_html: bool = False,
    ) -> str:
        """Send an email through a Nextcloud Mail account.

        The email is sent via the SMTP server configured for the account.

        Args:
            account_id: The mail account ID to send from. Use list_mail_accounts to find it.
            to: List of recipient email addresses (at least one required).
            subject: Email subject line.
            body: Email body text (plain text or HTML depending on is_html).
            cc: Optional list of CC email addresses.
            bcc: Optional list of BCC email addresses.
            is_html: Set to true if the body contains HTML (default: false, plain text).

        Returns:
            Confirmation message on success.
        """
        if not to:
            raise ValueError("At least one recipient email address is required.")
        client = get_client()
        accounts = await client.ocs_get(f"{MAIL_OCS}/account/list")
        account = next((a for a in accounts if a["id"] == account_id), None)
        if account is None:
            raise ValueError(f"Mail account {account_id} not found.")
        from_email = account["email"]
        json_data: dict[str, Any] = {
            "accountId": account_id,
            "fromEmail": from_email,
            "subject": subject,
            "body": body,
            "isHtml": is_html,
            "to": [{"email": addr} for addr in to],
        }
        if cc:
            json_data["cc"] = [{"email": addr} for addr in cc]
        if bcc:
            json_data["bcc"] = [{"email": addr} for addr in bcc]
        await client.ocs_post_json(f"{MAIL_OCS}/message/send", json_data=json_data)
        to_str = ", ".join(to)
        return f"Email sent to {to_str}."


def register(mcp: FastMCP) -> None:
    """Register mail tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
