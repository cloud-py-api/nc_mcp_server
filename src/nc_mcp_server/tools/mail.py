"""Mail tools - accounts, mailboxes, messages, send, move, flags and tags via the Mail app's OCS and JSON APIs."""

import json
import re
from typing import Any, cast
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, READONLY
from ..client import NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

MAIL_OCS = "apps/mail"
# Moving, flagging and tagging have no OCS endpoints, only the Mail web UI's JSON routes.
MAIL_API = "mail/api"
MAX_TAG_NAME_LENGTH = 128
TAG_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


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


def _format_tag(tag: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": tag.get("id"),
        "display_name": tag.get("displayName"),
        "imap_label": tag.get("imapLabel"),
        "color": tag.get("color"),
    }


def _format_tags(tags: object) -> list[dict[str, Any]]:
    """Format message tags, which Mail serializes as an object keyed by IMAP label, or [] when there are none."""
    if isinstance(tags, dict):
        items: list[object] = list(cast(dict[str, object], tags).values())
    elif isinstance(tags, list):
        items = cast(list[object], tags)
    else:
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            tag = cast(dict[str, Any], item)
            result.append({"display_name": tag.get("displayName"), "imap_label": tag.get("imapLabel")})
    return result


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
    tags = _format_tags(msg.get("tags"))
    if tags:
        result["tags"] = tags
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


async def _mail_api(method: str, path: str, forbidden: str, json_data: dict[str, Any] | None = None) -> Any:
    """Call a Mail JSON route, replacing Mail's bare 403 with a message that says what was not found.

    Mail answers 403 with an empty body for message IDs that do not exist or belong to someone else,
    and for tag labels the user has no tag for.
    """
    try:
        return await get_client().app_request_json(method, f"{MAIL_API}/{path}", json_data=json_data)
    except NextcloudError as e:
        if e.status_code == 403:
            raise NextcloudError(forbidden, 403) from e
        raise


def _message_not_found(message_id: int) -> str:
    return f"Message {message_id} was not found or is not accessible."


def _tag_not_found(message_id: int, imap_label: str) -> str:
    return (
        f"Message {message_id} was not found, or you have no tag with IMAP label '{imap_label}'. "
        "Use create_mail_tag to create the tag and get its label."
    )


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
        return json.dumps(accounts)

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
        return json.dumps(mailboxes)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_mail_messages(mailbox_id: int, limit: int = 20, cursor: int | None = None) -> str:
        """List messages in a mailbox, newest first.

        Returns message summaries (subject, sender, date, flags, tags) without the full body.
        Use get_mail_message with a message ID to read the full content.

        Args:
            mailbox_id: The mailbox database ID. Use list_mailboxes to find it.
            limit: Maximum number of messages to return (1-100, default 20).
            cursor: Pagination cursor. Pass the smallest message ID from a previous
                    response to fetch older messages.

        Returns:
            JSON object with "data" (list of message summaries) and "pagination" metadata.
            Each message has: id, subject, date (unix timestamp), from, to, flags, preview,
            and tags (display_name and imap_label) when it has any.
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
        return json.dumps(result)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_mail_message(message_id: int) -> str:
        """Get a full email message including its body.

        Retrieves the complete message with body text, message ID, tags, and attachment metadata.

        Args:
            message_id: The message database ID. Use list_mail_messages to find it.

        Returns:
            JSON object with: id, subject, date, from, to, cc, bcc, message_id,
            body, flags, tags (display_name and imap_label, if any), and attachments list (if any).
        """
        client = get_client()
        data = await client.ocs_get(f"{MAIL_OCS}/message/{message_id}")
        result = _format_message_full(data)
        # The OCS message endpoint leaves tags out; the Mail web UI's message route includes them.
        details = await client.app_request_json("GET", f"{MAIL_API}/messages/{message_id}")
        tags = _format_tags(details.get("tags") if details else None)
        if tags:
            result["tags"] = tags
        return json.dumps(result)


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


def _register_triage_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def move_mail_message(message_id: int, destination_mailbox_id: int) -> str:
        """Move a message to another mailbox (folder), in the same or another mail account.

        The moved message gets a new ID, so message_id is no longer valid afterwards and must not
        be reused. IMAP UIDs are per mailbox, and Nextcloud assigns the new ID when it syncs the
        destination mailbox. The inbox and mailboxes with background sync enabled are synced
        automatically; other folders are synced when opened in the Mail app.

        Args:
            message_id: The message database ID. Use list_mail_messages to find it.
            destination_mailbox_id: The target mailbox ID. Use list_mailboxes to find it.

        Returns:
            Confirmation message on success.
        """
        await _mail_api(
            "POST",
            f"messages/{message_id}/move",
            f"Message {message_id} or mailbox {destination_mailbox_id} was not found or is not accessible.",
            json_data={"destFolderId": destination_mailbox_id},
        )
        return (
            f"Message {message_id} moved to mailbox {destination_mailbox_id}. "
            "Its old ID is no longer valid; list the destination mailbox to find the message."
        )

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def set_mail_message_flags(
        message_id: int,
        seen: bool | None = None,
        flagged: bool | None = None,
        answered: bool | None = None,
    ) -> str:
        """Set or clear flags on a message: read/unread, starred, answered.

        Only the flags you pass change; the others keep their current value.

        Args:
            message_id: The message database ID. Use list_mail_messages to find it.
            seen: True marks the message as read, false as unread.
            flagged: True stars (flags) the message, false removes the star.
            answered: True marks the message as answered, false clears that mark.

        Returns:
            JSON object with message_id and the flags that were set.
        """
        requested = {"seen": seen, "flagged": flagged, "answered": answered}
        flags = {name: value for name, value in requested.items() if value is not None}
        if not flags:
            raise ValueError("Pass at least one of seen, flagged or answered.")
        await _mail_api(
            "PUT", f"messages/{message_id}/flags", _message_not_found(message_id), json_data={"flags": flags}
        )
        return json.dumps({"message_id": message_id, "flags": flags})


def _register_tag_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def create_mail_tag(display_name: str, color: str) -> str:
        """Create a mail tag, or get the existing tag with the same IMAP label.

        Tags belong to the current user and are stored on messages as IMAP keywords. Mail derives
        the IMAP label from the display name (for example "Needs Reply" becomes "$needs_reply").
        If a tag with that label already exists, it is returned unchanged, including its color.

        Args:
            display_name: Tag name shown in the Mail app (at most 128 characters).
            color: Hex color such as "#0082c9".

        Returns:
            JSON object with id, display_name, imap_label and color. Pass imap_label to
            add_mail_message_tag and remove_mail_message_tag.
        """
        name = display_name.strip()
        if not name:
            raise ValueError("display_name must not be empty.")
        if len(name) > MAX_TAG_NAME_LENGTH:
            raise ValueError(f"display_name must be at most {MAX_TAG_NAME_LENGTH} characters.")
        if not TAG_COLOR_RE.match(color):
            raise ValueError(f"Invalid color '{color}'. Use a hex color such as '#0082c9'.")
        client = get_client()
        tag = await client.app_request_json("POST", f"{MAIL_API}/tags", json_data={"displayName": name, "color": color})
        return json.dumps(_format_tag(tag))

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def add_mail_message_tag(message_id: int, imap_label: str) -> str:
        """Tag a message. Tagging a message that already has the tag changes nothing.

        Args:
            message_id: The message database ID. Use list_mail_messages to find it.
            imap_label: The tag's IMAP label (for example "$needs_reply"), as returned by
                create_mail_tag or shown in the tags of list_mail_messages.

        Returns:
            JSON object with message_id and the tag (id, display_name, imap_label, color).
        """
        if not imap_label:
            raise ValueError("imap_label must not be empty.")
        tag = await _mail_api(
            "PUT",
            f"messages/{message_id}/tags/{quote(imap_label, safe='')}",
            _tag_not_found(message_id, imap_label),
        )
        return json.dumps({"message_id": message_id, "tag": _format_tag(tag)})

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def remove_mail_message_tag(message_id: int, imap_label: str) -> str:
        """Remove a tag from a message. The tag itself is kept and can still be used on other messages.

        Args:
            message_id: The message database ID. Use list_mail_messages to find it.
            imap_label: The tag's IMAP label (for example "$needs_reply").

        Returns:
            JSON object with message_id and the removed tag (id, display_name, imap_label, color).
        """
        if not imap_label:
            raise ValueError("imap_label must not be empty.")
        tag = await _mail_api(
            "DELETE",
            f"messages/{message_id}/tags/{quote(imap_label, safe='')}",
            _tag_not_found(message_id, imap_label),
        )
        return json.dumps({"message_id": message_id, "tag": _format_tag(tag)})


def register(mcp: FastMCP) -> None:
    """Register mail tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_triage_tools(mcp)
    _register_tag_tools(mcp)
