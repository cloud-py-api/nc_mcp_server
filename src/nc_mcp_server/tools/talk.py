"""Nextcloud Talk tools - conversations, messages, threads, participants, and polls via OCS API."""

import contextlib
import json
import re
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

# Conversation type IDs used by Nextcloud Talk
_CONVERSATION_TYPES: dict[int, str] = {
    1: "one-to-one",
    2: "group",
    3: "public",
    4: "changelog",
    5: "former-one-to-one",
    6: "note-to-self",
}

# Room types accepted when creating conversations
_VALID_ROOM_TYPES = {2: "group", 3: "public"}

# Participant type IDs used by Nextcloud Talk
_PARTICIPANT_TYPES: dict[int, str] = {
    1: "owner",
    2: "moderator",
    3: "user",
    4: "guest",
    5: "user-following-public-link",
    6: "guest-moderator",
}


# Poll status codes
_POLL_STATUS: dict[int, str] = {
    0: "open",
    1: "closed",
    2: "draft",
}

_RESULT_MODES: dict[int, str] = {
    0: "public",
    1: "hidden",
}

# Notification levels, used both per conversation and per thread
_NOTIFICATION_LEVELS: dict[int, str] = {
    0: "default",
    1: "always",
    2: "mention",
    3: "never",
}
_NOTIFICATION_LEVEL_IDS: dict[str, int] = {name: level for level, name in _NOTIFICATION_LEVELS.items()}


def _notification_level_name(level: Any) -> str:
    """Name a notification level, keeping the raw value visible if Talk adds one."""
    return _NOTIFICATION_LEVELS.get(level, f"unknown({level})")


def _format_poll(poll: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from a raw poll object."""
    result: dict[str, Any] = {
        "id": poll["id"],
        "question": poll["question"],
        "options": poll.get("options", []),
        "status": _POLL_STATUS.get(poll.get("status", 0), f"unknown({poll.get('status')})"),
        "result_mode": _RESULT_MODES.get(poll.get("resultMode", 0), f"unknown({poll.get('resultMode')})"),
        "max_votes": poll.get("maxVotes", 0),
        "actor_id": poll.get("actorId", ""),
        "actor_display_name": poll.get("actorDisplayName", ""),
        "num_voters": poll.get("numVoters", 0),
        "voted_self": poll.get("votedSelf", []),
    }
    votes = poll.get("votes")
    if votes:
        result["votes"] = votes
    details = poll.get("details")
    if details:
        result["details"] = details
    return result


def _format_conversation(room: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from a raw room object."""
    # The latest pin, unless the user hid it for themselves
    pinned = room.get("lastPinnedId", 0)
    if pinned and room.get("hiddenPinnedId") == pinned:
        pinned = 0
    return {
        "token": room["token"],
        "type": _CONVERSATION_TYPES.get(room.get("type", 0), f"unknown({room.get('type')})"),
        "name": room.get("displayName", room.get("name", "")),
        "description": room.get("description", ""),
        "read_only": room.get("readOnly", 0) == 1,
        "has_call": room.get("hasCall", False),
        "unread_messages": room.get("unreadMessages", 0),
        "unread_mention": room.get("unreadMention", False),
        "last_activity": room.get("lastActivity", 0),
        "is_favorite": room.get("isFavorite", False),
        "is_archived": room.get("isArchived", False),
        "is_important": room.get("isImportant", False),
        "is_sensitive": room.get("isSensitive", False),
        "notification_level": _notification_level_name(room.get("notificationLevel", 0)),
        "call_notifications": room.get("notificationCalls", 1) == 1,
        "pinned_message_id": pinned,
        "participant_count": room.get("participantCount", 0),
        "can_leave": room.get("canLeaveConversation", False),
        "can_delete": room.get("canDeleteConversation", False),
    }


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_-]+)\}")


def _message_text(msg: dict[str, Any]) -> str:
    """Fill in the placeholders Talk leaves in message text, such as {mention-user1} or {file}.

    Talk sends mentions, shared files and other objects as placeholders plus a messageParameters map.
    Mentions become "@name" ("@all" for the whole conversation), other objects their name, and a file
    shared with a caption gets its name after the caption. A placeholder without a parameter stays.
    """
    text = str(msg.get("message", ""))
    params = msg.get("messageParameters")
    if not isinstance(params, dict) or not params:  # Talk sends [] when there are none
        return text
    parameters = cast(dict[str, Any], params)

    def render(match: re.Match[str]) -> str:
        param = parameters.get(match.group(1))
        if not isinstance(param, dict):
            return match.group(0)
        fields = cast(dict[str, Any], param)
        if not match.group(1).startswith("mention-"):
            return str(fields.get("name") or fields.get("id") or match.group(0))
        # A mention of the whole conversation carries the conversation's name; it is written @all
        if fields.get("type") == "call":
            return "@all"
        return f"@{fields.get('name') or fields.get('id') or match.group(0)}"

    rendered = _PLACEHOLDER.sub(render, text)
    shared = parameters.get("file")
    if "{file}" not in text and isinstance(shared, dict) and cast(dict[str, Any], shared).get("name"):
        # A file shared with a caption: Talk puts the caption in place of the {file} placeholder
        rendered += f" [{cast(dict[str, Any], shared)['name']}]"
    return rendered


def _format_message_compact(msg: dict[str, Any]) -> str:
    """Format a message as a compact single line: [id] author: text.

    A message inside a thread gets a marker after the author: '[thread <id> "<title>"]'
    on the thread's first message and "[thread <id>]" on the rest of the thread.
    """
    msg_id = msg.get("id", 0)
    author = msg.get("actorDisplayName", "unknown")
    text = _message_text(msg)
    if not msg.get("isThread"):
        return f"[{msg_id}] {author}: {text}"
    thread_id = msg.get("threadId", msg_id)
    marker = f"thread {thread_id}"
    if thread_id == msg_id:
        marker += " " + json.dumps(msg.get("threadTitle", ""), ensure_ascii=False)
    return f"[{msg_id}] {author} [{marker}]: {text}"


def _format_message_full(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from a raw message object.

    thread_id is 0 unless the message belongs to a thread; thread messages also carry thread_title.
    """
    in_thread = bool(msg.get("isThread"))
    result: dict[str, Any] = {
        "id": msg["id"],
        "actor_type": msg.get("actorType", ""),
        "actor_id": msg.get("actorId", ""),
        "actor_display_name": msg.get("actorDisplayName", ""),
        "timestamp": msg.get("timestamp", 0),
        "message": _message_text(msg),
        "message_type": msg.get("messageType", ""),
        "system_message": msg.get("systemMessage", ""),
        "is_replyable": msg.get("isReplyable", False),
        "thread_id": msg.get("threadId", msg["id"]) if in_thread else 0,
    }
    if in_thread:
        result["thread_title"] = msg.get("threadTitle", "")
    return result


def _format_thread(info: dict[str, Any]) -> dict[str, Any]:
    """Flatten a TalkThreadInfo object; its first and last messages use the compact line format."""
    thread: dict[str, Any] = info.get("thread") or {}
    attendee: dict[str, Any] = info.get("attendee") or {}
    level = attendee.get("notificationLevel", 0)
    first = info.get("first")
    last = info.get("last")
    return {
        "thread_id": thread.get("id", 0),
        "token": thread.get("roomToken", ""),
        "title": thread.get("title", ""),
        "num_replies": thread.get("numReplies", 0),
        "last_activity": thread.get("lastActivity", 0),
        "notification_level": _notification_level_name(level),
        "first": _format_message_compact(first) if first else None,
        "last": _format_message_compact(last) if last else None,
    }


def _format_message_list(messages: list[dict[str, Any]], include_system: bool, thread_id: int) -> str:
    """Render get_messages output: one compact line per message plus a pagination footer.

    The footer counts the messages shown but paginates from the oldest message Talk returned,
    hidden system messages included, so that a page of nothing but system messages can still
    be paged past instead of looking like the end of the history.
    """
    shown = messages if include_system else [m for m in messages if not m.get("systemMessage")]
    lines = [_format_message_compact(msg) for msg in shown]
    if messages:
        oldest_id = min(m["id"] for m in messages)
        next_call = f"before_message_id={oldest_id}"
        if thread_id:
            next_call += f", thread_id={thread_id}"
        lines.append(f"\n--- {len(shown)} messages. For older messages, call with {next_call} ---")
    return "\n".join(lines)


def _build_message_payload(message: str, reply_to: int, thread_id: int, thread_title: str) -> dict[str, Any]:
    """Validate the reply and thread arguments of send_message and build the POST body."""
    if not message.strip():
        raise ValueError("message must not be empty.")
    if thread_title and (reply_to or thread_id):
        raise ValueError(
            "thread_title starts a new thread and cannot be combined with reply_to or thread_id. "
            "To post into an existing thread, pass thread_id alone."
        )
    if thread_id and reply_to:
        raise ValueError(
            "Pass either thread_id or reply_to, not both. "
            "A reply to a message that is inside a thread is posted into that thread automatically."
        )
    post_data: dict[str, Any] = {"message": message}
    if reply_to:
        post_data["replyTo"] = reply_to
    if thread_id:
        post_data["threadId"] = thread_id
    if thread_title:
        title = thread_title.strip()
        if not title:
            raise ValueError("thread_title must not be blank.")
        post_data["threadTitle"] = title
    return post_data


def _parse_thread_notification_level(level: str) -> int:
    """Map a notification level name to the integer Talk expects."""
    level_id = _NOTIFICATION_LEVEL_IDS.get(level.strip().lower())
    if level_id is None:
        valid = ", ".join(_NOTIFICATION_LEVEL_IDS)
        raise ValueError(f"Invalid level '{level}'. Must be one of: {valid}")
    return level_id


@contextlib.contextmanager
def _thread_errors(token: str, thread_id: int, not_found_status: int = 404, forbidden: str = "") -> Generator[None]:
    """Replace the generic errors of a thread request with ones that name the thread.

    Talk answers an unknown thread with an empty OCS message and just a code, which the client reports
    as "Not found. (thread)" (or a 400 when sending into one), too terse for the caller to act on.
    """
    try:
        yield
    except NextcloudError as e:
        if thread_id and e.status_code == not_found_status:
            raise NextcloudError(
                f"Thread {thread_id} not found in conversation {token} (or the conversation does not exist). "
                "A thread ID is the ID of the thread's first message; use list_threads to find them.",
                e.status_code,
            ) from e
        if forbidden and e.status_code == 403:
            raise NextcloudError(forbidden, e.status_code) from e
        raise


def _format_participant(p: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful fields from a raw participant object."""
    return {
        "attendee_id": p.get("attendeeId", 0),
        "actor_type": p.get("actorType", ""),
        "actor_id": p.get("actorId", ""),
        "display_name": p.get("displayName", ""),
        "participant_type": _PARTICIPANT_TYPES.get(p.get("participantType", 0), f"unknown({p.get('participantType')})"),
        "in_call": p.get("inCall", 0) > 0,
    }


def _register_read_tools(mcp: FastMCP) -> None:
    """Register read-only Talk tools."""

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_conversations(
        limit: int = 50,
        offset: int = 0,
        modified_since: str = "",
    ) -> str:
        """List Talk conversations the current user is part of.

        Returns every conversation the user has joined, sorted by last activity
        (newest first). Muted and archived conversations are included; Talk has
        no filter for them.

        Args:
            limit: Maximum number of conversations to return (1-200, default 50).
            offset: Number of conversations to skip for pagination (default 0).
            modified_since: Only conversations with activity since this ISO 8601
                time with time zone, e.g. to check what changed since the last look.
                Your own changes to a conversation (read state, favorite, archive,
                notifications) count as activity, and ones with a call running are
                always included.

        Returns:
            JSON with "data" (list of conversation objects) and "pagination"
            (count, offset, limit, has_more).
        """
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        client = get_client()
        # Talk reads noStatusUpdate only to decide whether to bump the user's presence
        # to online for its mobile clients, which listing from a tool must not do.
        params = {"noStatusUpdate": "1"}
        if modified_since:
            params["modifiedSince"] = str(_timestamp(modified_since, "modified_since", future=False))
        data = await client.ocs_get("apps/spreed/api/v4/room", params=params)
        all_convs = [_format_conversation(room) for room in data]
        page = all_convs[offset : offset + limit]
        has_more = offset + limit < len(all_convs)

        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_conversation(token: str) -> str:
        """Get details about a specific Talk conversation.

        Args:
            token: The conversation token (short alphanumeric ID, e.g. "abc12xyz").
                   Use list_conversations to find tokens.

        Returns:
            JSON object with conversation details.
        """
        client = get_client()
        data = await client.ocs_get(f"apps/spreed/api/v4/room/{token}")
        return json.dumps(_format_conversation(data), default=str)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_messages(
        token: str,
        limit: int = 50,
        before_message_id: int = 0,
        include_system: bool = False,
        thread_id: int = 0,
    ) -> str:
        """Get chat messages from a Talk conversation.

        Returns messages in reverse chronological order (newest first).
        Uses a compact format: "[id] author: message text" - one line per message.
        Messages inside a thread are marked after the author: the thread's first
        message as '[id] author [thread <thread_id> "<title>"]: text', the rest of
        the thread as "[id] author [thread <thread_id>]: text".

        IMPORTANT: Start with a small limit (20-50). If you need more context,
        use before_message_id with the oldest message ID from the previous call
        to paginate backwards through the history.

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            limit: Maximum number of messages to return (1-200, default: 50).
                   Start small to avoid exceeding response size limits.
            before_message_id: Fetch messages older than this message ID (for pagination).
                               Use the smallest message ID from a previous call.
                               Default 0 means start from the newest message.
            include_system: Include system messages like "User joined",
                            "Conversation created" (default: false - only chat messages).
            thread_id: Only return messages of this thread (default 0 = all messages,
                       including thread messages). Use list_threads to find thread IDs.

        Returns:
            Compact text with one message per line: "[id] author: message".
            The last line shows pagination info if more messages may exist.
            Empty when nothing matches, for example when there is nothing older
            than before_message_id.
        """
        client = get_client()
        limit = max(1, min(200, limit))
        params: dict[str, str] = {
            "lookIntoFuture": "0",
            "limit": str(limit),
            "setReadMarker": "0",
            "markNotificationsAsRead": "0",
        }
        if before_message_id:
            params["lastKnownMessageId"] = str(before_message_id)
        if thread_id:
            params["threadId"] = str(thread_id)
        with _thread_errors(token, thread_id):
            data = await client.ocs_get(f"apps/spreed/api/v1/chat/{token}", params=params)
        # Talk answers 304 with an empty body when nothing is older than before_message_id
        return _format_message_list(data or [], include_system, thread_id)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_participants(token: str, limit: int = 50, offset: int = 0) -> str:
        """List participants in a Talk conversation.

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            limit: Maximum number of participants to return (1-200, default 50).
            offset: Number of participants to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of participant objects with attendee_id,
            actor_id, display_name, participant_type, in_call) and
            "pagination" (count, offset, limit, has_more).
        """
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        client = get_client()
        data = await client.ocs_get(f"apps/spreed/api/v4/room/{token}/participants")
        all_participants = [_format_participant(p) for p in data]
        page = all_participants[offset : offset + limit]
        has_more = offset + limit < len(all_participants)
        return json.dumps(
            {
                "data": page,
                "pagination": {"count": len(page), "offset": offset, "limit": limit, "has_more": has_more},
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_poll(token: str, poll_id: int) -> str:
        """Get a poll from a Talk conversation.

        Returns poll details including question, options, current votes (if visible),
        and which options the current user voted for.

        Vote visibility depends on the poll's result_mode:
        - "public": votes are visible after you vote.
        - "hidden": votes are only visible after the poll is closed.

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            poll_id: The poll ID. Poll IDs appear in chat messages when a poll is created.

        Returns:
            JSON object with poll details: id, question, options, status,
            result_mode, max_votes, votes, num_voters, voted_self.
        """
        client = get_client()
        data = await client.ocs_get(f"apps/spreed/api/v1/poll/{token}/{poll_id}")
        return json.dumps(_format_poll(data), default=str)


def _register_poll_tools(mcp: FastMCP) -> None:
    """Register poll-related Talk tools (write + destructive)."""

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_poll(
        token: str,
        question: str,
        options: list[str],
        result_mode: int = 0,
        max_votes: int = 0,
    ) -> str:
        """Create a poll in a Talk conversation.

        Polls can only be created in group or public conversations (not one-to-one).
        A chat message is automatically posted announcing the poll.

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            question: The poll question (max 32,000 characters).
            options: List of voting options (minimum 2 options required).
                     Example: ["Yes", "No", "Maybe"]
            result_mode: 0 for public results (voters see results immediately after voting),
                         1 for hidden results (results shown only after poll is closed).
                         Default: 0 (public).
            max_votes: Maximum number of options a user can vote for.
                       0 means unlimited (user can select all options). Default: 0.

        Returns:
            JSON object with poll details: id, question, options, status, result_mode, max_votes.
        """
        if len(options) < 2:
            raise ValueError("A poll requires at least 2 options.")
        client = get_client()
        post_data: dict[str, Any] = {
            "question": question,
            "options[]": options,
            "resultMode": result_mode,
            "maxVotes": max_votes,
        }
        data = await client.ocs_post(f"apps/spreed/api/v1/poll/{token}", data=post_data)
        return json.dumps(_format_poll(data), default=str)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def vote_poll(token: str, poll_id: int, option_ids: list[int]) -> str:
        """Vote on a poll in a Talk conversation.

        Voting replaces any previous vote — calling this again with different
        option_ids changes your vote. You cannot vote on closed polls.

        Args:
            token: The conversation token.
            poll_id: The poll ID. Use get_poll to see available polls.
            option_ids: List of option indices to vote for (0-based).
                        For example, if options are ["Yes", "No", "Maybe"],
                        use [0] to vote "Yes", or [0, 2] to vote "Yes" and "Maybe".
                        The number of choices must not exceed the poll's max_votes
                        (0 means unlimited).

        Returns:
            JSON object with updated poll details including your votes (voted_self)
            and current vote counts (if visible).
        """
        if not option_ids:
            raise ValueError("You must vote for at least one option.")
        client = get_client()
        post_data: dict[str, Any] = {"optionIds[]": option_ids}
        data = await client.ocs_post(f"apps/spreed/api/v1/poll/{token}/{poll_id}", data=post_data)
        return json.dumps(_format_poll(data), default=str)

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def close_poll(token: str, poll_id: int) -> str:
        """Close a poll in a Talk conversation.

        Once closed, no more votes can be cast and results become visible
        to all participants (regardless of result_mode). Only the poll
        creator or a conversation moderator can close a poll.

        This action is irreversible — a closed poll cannot be reopened.

        Args:
            token: The conversation token.
            poll_id: The poll ID to close.

        Returns:
            JSON object with the final poll results including all votes and details.
        """
        client = get_client()
        data = await client.ocs_delete(f"apps/spreed/api/v1/poll/{token}/{poll_id}")
        return json.dumps(_format_poll(data), default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    """Register write and destructive Talk tools for conversations and messages."""

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def send_message(
        token: str,
        message: str,
        reply_to: int = 0,
        thread_id: int = 0,
        thread_title: str = "",
    ) -> str:
        """Send a chat message to a Talk conversation.

        Supports Markdown formatting. Messages can be up to 32000 characters.
        Use @mention syntax to mention users: @"user-id" or @"display name".

        Threads: pass thread_title to start a new thread with this message as its
        first message; the thread ID is that message's ID and is returned as
        thread_id, so pass it as thread_id to keep posting into the thread.
        An existing message cannot be turned into a thread. thread_title cannot be
        combined with reply_to or thread_id, and thread_id cannot be combined with
        reply_to (replying to a message inside a thread posts into that thread anyway).

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            message: The message text to send (supports Markdown).
            reply_to: Optional message ID to reply to (default: 0 = not a reply).
            thread_id: Optional ID of an existing thread to post into without quoting
                       a message (default: 0). Use list_threads to find thread IDs.
            thread_title: Optional title; when set, the message starts a new thread
                          with this title (default: "" = no new thread). Talk shortens
                          titles longer than 203 characters.

        Returns:
            JSON object of the sent message with its assigned ID, its thread_id
            (0 when the message is not in a thread) and, for thread messages, thread_title.
        """
        post_data = _build_message_payload(message, reply_to, thread_id, thread_title)
        client = get_client()
        with _thread_errors(token, thread_id, not_found_status=400):
            data = await client.ocs_post(f"apps/spreed/api/v1/chat/{token}", data=post_data)
        return json.dumps(_format_message_full(data), default=str)

    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_conversation(
        room_type: int,
        name: str,
        invite: str = "",
    ) -> str:
        """Create a new Talk conversation.

        Args:
            room_type: 2 for group conversation, 3 for public conversation.
                       Group conversations are invite-only.
                       Public conversations can be joined via link.
            name: Display name for the conversation.
            invite: Optional user ID to invite (for group conversations).

        Returns:
            JSON object with the created conversation details, including its token.
        """
        if room_type not in _VALID_ROOM_TYPES:
            valid = ", ".join(f"{k} ({v})" for k, v in _VALID_ROOM_TYPES.items())
            raise ValueError(f"Invalid room_type {room_type}. Valid types: {valid}")
        client = get_client()
        post_data: dict[str, Any] = {"roomType": room_type, "roomName": name}
        if invite:
            post_data["invite"] = invite
        data = await client.ocs_post("apps/spreed/api/v4/room", data=post_data)
        return json.dumps(_format_conversation(data), default=str)

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_message(token: str, message_id: int) -> str:
        """Delete a chat message from a Talk conversation.

        Only the message author or a moderator can delete a message.
        The message is replaced with "Message deleted" in the conversation.

        Args:
            token: The conversation token.
            message_id: The ID of the message to delete.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"apps/spreed/api/v1/chat/{token}/{message_id}")
        return f"Message {message_id} deleted from conversation {token}."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def leave_conversation(token: str) -> str:
        """Leave a Talk conversation.

        After leaving, the user will no longer receive notifications or see
        the conversation in their list. For group conversations, the user
        can be re-invited. For one-to-one conversations, this removes the
        conversation permanently for the user.

        Args:
            token: The conversation token of the conversation to leave.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"apps/spreed/api/v4/room/{token}/participants/self")
        return f"Left conversation {token}."


def _register_thread_read_tools(mcp: FastMCP) -> None:
    """Register read-only Talk thread tools."""

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_threads(token: str, limit: int = 50) -> str:
        """List the most recently active threads in a Talk conversation.

        A thread is started by sending a message with a thread_title (see send_message);
        its ID is the ID of that first message. Read a thread's messages with
        get_messages(token, thread_id=...).

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            limit: Maximum number of threads to return (1-50, default 50). Talk only
                   returns the most recently active threads and has no offset.

        Returns:
            JSON with "data" (list of threads, newest activity first) and "pagination"
            (count, limit, has_more). Each thread has thread_id, token, title,
            num_replies, last_activity (Unix timestamp), notification_level
            (default/always/mention/never) and first/last messages as compact lines
            "[id] author [thread <id>]: text" (last is null when there are no replies).
        """
        limit = max(1, min(50, limit))
        client = get_client()
        data = await client.ocs_get(f"apps/spreed/api/v1/chat/{token}/threads/recent", params={"limit": str(limit)})
        threads = [_format_thread(info) for info in data]
        return json.dumps(
            {
                "data": threads,
                "pagination": {"count": len(threads), "limit": limit, "has_more": len(threads) == limit},
            },
            default=str,
        )

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_thread(token: str, thread_id: int) -> str:
        """Get details of one thread in a Talk conversation.

        Returns the thread's summary, not its messages; use
        get_messages(token, thread_id=...) to read the messages.

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            thread_id: The thread ID, which is the ID of the thread's first message.
                       Shown as "[thread <id>]" in get_messages output.

        Returns:
            JSON object with thread_id, token, title, num_replies, last_activity,
            notification_level and first/last messages as compact lines.
        """
        client = get_client()
        with _thread_errors(token, thread_id):
            data = await client.ocs_get(f"apps/spreed/api/v1/chat/{token}/threads/{thread_id}")
        return json.dumps(_format_thread(data), default=str)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_subscribed_threads(limit: int = 100, offset: int = 0) -> str:
        """List the threads the current user follows, across all conversations.

        A user follows the threads they start or post in, and the ones whose
        notification level they set; threads set to "never" are left out.
        Sorted by last activity (newest first).

        Args:
            limit: Maximum number of threads to return (1-100, default 100).
            offset: Number of threads to skip for pagination (default 0).

        Returns:
            JSON with "data" (list of threads, same shape as list_threads; use the
            token field to know the conversation) and "pagination"
            (count, offset, limit, has_more).
        """
        limit = max(1, min(100, limit))
        offset = max(0, offset)
        client = get_client()
        data = await client.ocs_get(
            "apps/spreed/api/v1/chat/subscribed-threads",
            params={"limit": str(limit), "offset": str(offset)},
        )
        threads = [_format_thread(info) for info in data]
        return json.dumps(
            {
                "data": threads,
                "pagination": {
                    "count": len(threads),
                    "offset": offset,
                    "limit": limit,
                    "has_more": len(threads) == limit,
                },
            },
            default=str,
        )


def _register_thread_write_tools(mcp: FastMCP) -> None:
    """Register Talk thread tools that change thread settings."""

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def rename_thread(token: str, thread_id: int, title: str) -> str:
        """Rename a thread in a Talk conversation.

        Only the author of the thread's first message or a conversation moderator
        can rename a thread. Talk posts a "thread renamed" system message into the thread.

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            thread_id: The thread ID (the ID of the thread's first message).
            title: The new thread title (must not be blank). Talk shortens titles
                   longer than 203 characters.

        Returns:
            JSON object of the updated thread (same shape as get_thread).
        """
        title = title.strip()
        if not title:
            raise ValueError("title must not be blank.")
        client = get_client()
        forbidden = (
            f"Only the author of the first message of thread {thread_id} or a moderator of "
            f"conversation {token} can rename the thread."
        )
        with _thread_errors(token, thread_id, forbidden=forbidden):
            data = await client.ocs_put(
                f"apps/spreed/api/v1/chat/{token}/threads/{thread_id}",
                data={"threadTitle": title},
            )
        return json.dumps(_format_thread(data), default=str)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def set_thread_notification_level(token: str, thread_id: int, level: str) -> str:
        """Set the current user's notification level for a thread.

        Setting any level other than "never" also makes the user follow the thread,
        so it shows up in list_subscribed_threads; "never" removes it from that list.

        Args:
            token: The conversation token. Use list_conversations to find tokens.
            thread_id: The thread ID (the ID of the thread's first message).
            level: One of "default" (use the conversation's setting), "always"
                   (every message), "mention" (only when mentioned) or "never".

        Returns:
            JSON object of the updated thread (same shape as get_thread).
        """
        level_id = _parse_thread_notification_level(level)
        client = get_client()
        with _thread_errors(token, thread_id):
            data = await client.ocs_post(
                f"apps/spreed/api/v1/chat/{token}/threads/{thread_id}/notify",
                data={"level": level_id},
            )
        return json.dumps(_format_thread(data), default=str)


# What the edit endpoint's error codes mean, in words an agent can act on
_EDIT_ERRORS = {
    "age": "Talk only lets messages be edited for 24 hours (except in Note to self)",
    "permission": "only your own messages can be edited, or any message by a moderator of a group conversation",
}
# Answers that come without a code: from Talk's middleware, or with a code shared by several cases
_EDIT_STATUS_ERRORS = {
    400: "the new text is empty or invalid",
    403: "the conversation may be read-only, or you may lack chat permission",
    405: "system messages and shared objects other than files (polls, locations, ...) cannot be edited",
    412: "the conversation's lobby is active",
    413: "the new text is too long",
}
_ERROR_CODE = re.compile(r"\((\w+)\)$")

_SHARED_ITEM_TYPES = ("audio", "deckcard", "file", "location", "media", "other", "pinned", "poll", "recording", "voice")


def _chat_path(token: str, *parts: int | str) -> str:
    return "/".join(["apps/spreed/api/v1/chat", token, *(str(p) for p in parts)])


def _format_reactions(data: Any) -> dict[str, list[str]]:
    """Reaction -> display names of who reacted with it. Talk sends [] when there are none."""
    if not isinstance(data, dict):
        return {}
    return {
        str(reaction): [str(actor.get("actorDisplayName") or actor.get("actorId", "")) for actor in actors]
        for reaction, actors in cast(dict[str, list[dict[str, Any]]], data).items()
    }


def _format_unread(room: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": room.get("token", ""),
        "last_read_message": room.get("lastReadMessage", 0),
        "unread_messages": room.get("unreadMessages", 0),
        "unread_mention": room.get("unreadMention", False),
    }


def _register_chat_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_message_context(
        token: str, message_id: int, limit: int = 20, thread_id: int = 0, include_system: bool = False
    ) -> str:
        """Get the messages around one message, e.g. to read the discussion a search result or mention is in.

        Reading context leaves the read marker and notifications as they are.

        Args:
            token: The conversation token.
            message_id: The message to center on.
            limit: How many messages to fetch before and after it (1-100, default 20).
            thread_id: Only messages of this thread (default 0 = the whole conversation).
            include_system: Also show system messages such as joins and edits (default false).

        Returns:
            One line per message, oldest first, in the same "[id] author: text" form as
            get_messages; the requested message's line is marked with ">>".
        """
        limit = max(1, min(100, limit))
        # Talk's context endpoint clears the reader's mention notifications, so the chat endpoint is read
        # twice instead, with read marker and notifications left alone
        common: dict[str, str] = {"lastKnownMessageId": str(message_id), "setReadMarker": "0"}
        common["markNotificationsAsRead"] = "0"
        if thread_id:
            common["threadId"] = str(thread_id)
        path = _chat_path(token)
        client = get_client()
        older_params = {**common, "lookIntoFuture": "0", "includeLastKnown": "1", "limit": str(limit + 1)}
        newer_params = {**common, "lookIntoFuture": "1", "timeout": "0", "limit": str(limit)}
        with _thread_errors(token, thread_id):
            older: list[dict[str, Any]] = await client.ocs_get(path, params=older_params) or []
            # Talk answers 304 with no body when nothing is newer
            newer: list[dict[str, Any]] = await client.ocs_get(path, params=newer_params) or []
        messages = sorted([*older, *newer], key=lambda m: int(m.get("id", 0)))
        shown = [m for m in messages if include_system or m.get("id") == message_id or not m.get("systemMessage")]
        lines = [(">> " if m.get("id") == message_id else "") + _format_message_compact(m) for m in shown]
        if not any(m.get("id") == message_id for m in messages):
            note = f"(Message {message_id} is not in this conversation or thread; these are the messages around"
            lines.insert(0, f"{note} its position.)")
        return "\n".join(lines) if shown else f"No messages around message {message_id}."

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_reactions(token: str, message_id: int, reaction: str = "") -> str:
        """List who reacted to a message, and with what.

        Args:
            token: The conversation token.
            message_id: The message ID.
            reaction: Only this reaction, e.g. "👍" (default: all).

        Returns:
            JSON object mapping each reaction to the display names of who used it.
        """
        params = {"reaction": reaction} if reaction else None
        data = await get_client().ocs_get(f"apps/spreed/api/v1/reaction/{token}/{message_id}", params=params)
        return json.dumps(_format_reactions(data), ensure_ascii=False)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_shared_items(token: str, item_type: str = "", limit: int = 20) -> str:
        """List what was shared in a conversation: files, media, polls, locations, pinned messages and more.

        Args:
            token: The conversation token.
            item_type: One type to list: audio, deckcard, file, location, media, other,
                pinned, poll, recording or voice. Empty (default) gives the latest few
                of every type.
            limit: Maximum items (per type without item_type: 1-20, default 20;
                with item_type: 1-200).

        Returns:
            JSON object mapping each type to its items, each the chat message that
            shared it ("[id] author: text"), in Talk's order (newest first; pinned
            messages by when they were pinned).
        """
        client = get_client()
        grouped: dict[str, list[dict[str, Any]]]
        if not item_type:
            params: dict[str, Any] = {"limit": max(1, min(20, limit))}
            data = await client.ocs_get(_chat_path(token, "share", "overview"), params=params)
            grouped = cast(dict[str, list[dict[str, Any]]], data or {})
        elif item_type in _SHARED_ITEM_TYPES:
            params = {"objectType": item_type, "limit": max(1, min(200, limit))}
            data = await client.ocs_get(_chat_path(token, "share"), params=params)
            # Keyed by message ID, or an empty list when there is nothing
            items: list[dict[str, Any]] = list(cast(dict[str, dict[str, Any]], data).values()) if data else []
            grouped = {item_type: items}
        else:
            raise ValueError(f"Invalid item_type '{item_type}'. Must be one of: {', '.join(_SHARED_ITEM_TYPES)}")
        result = {kind: [_format_message_compact(m) for m in msgs] for kind, msgs in grouped.items() if msgs}
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def search_mentions(token: str, search: str, limit: int = 20) -> str:
        """Find who can be mentioned in a conversation, for writing a message that mentions them.

        Args:
            token: The conversation token.
            search: Part of a name or ID; an empty string lists the first matches.
            limit: Maximum results (1-50, default 20). Needs a conversation you can
                write in.

        Returns:
            JSON list of matches with id, label (display name), source (users, groups,
            guests, calls for everyone in the room, ...) and "mention", the text to put in
            the message, e.g. @"john.doe".
        """
        limit = max(1, min(50, limit))
        params = {"search": search, "limit": limit}
        data: list[dict[str, Any]] = await get_client().ocs_get(_chat_path(token, "mentions"), params=params)
        return json.dumps(
            [
                {
                    "id": m.get("id", ""),
                    "label": m.get("label", ""),
                    "source": m.get("source", ""),
                    "mention": f'@"{m.get("mentionId") or m.get("id", "")}"',
                }
                for m in (data or [])[:limit]
            ],
            ensure_ascii=False,
        )


def _register_chat_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def edit_message(token: str, message_id: int, message: str) -> str:
        """Replace the text of a chat message. Talk marks it as edited and tells the conversation.

        Only your own messages (or, for a moderator, any in a group conversation), only
        within 24 hours, and not system messages or shared polls and locations; for a
        shared file the new text becomes its caption. Mentions read back from
        get_messages show names; to keep a mention when editing, write it as
        search_mentions gives it (@"user-id").

        Args:
            token: The conversation token.
            message_id: The message to edit.
            message: The new text. Mentions work as in send_message.

        Returns:
            JSON with the edited message.
        """
        try:
            data = await get_client().ocs_put_json(_chat_path(token, message_id), json_data={"message": message})
        except NextcloudError as e:
            code = _ERROR_CODE.search(str(e))
            reason = _EDIT_ERRORS.get(code.group(1)) if code else None
            reason = reason or _EDIT_STATUS_ERRORS.get(e.status_code)
            raise NextcloudError(f"{e}: {reason}" if reason else str(e), e.status_code) from e
        return json.dumps(_format_message_full(data.get("parent") or data), ensure_ascii=False)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def add_reaction(token: str, message_id: int, reaction: str) -> str:
        """React to a chat message with an emoji. Reacting twice with the same one changes nothing.

        Args:
            token: The conversation token.
            message_id: The message to react to.
            reaction: One emoji, e.g. "👍".

        Returns:
            JSON object mapping each reaction on the message to who used it.
        """
        data = await get_client().ocs_post_json(
            f"apps/spreed/api/v1/reaction/{token}/{message_id}", json_data={"reaction": reaction}
        )
        return json.dumps(_format_reactions(data), ensure_ascii=False)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def mark_conversation_read(token: str, message_id: int = 0) -> str:
        """Mark a conversation as read, up to a message or completely.

        Marking it completely read also dismisses your Talk notifications for it.

        Args:
            token: The conversation token.
            message_id: The last message to count as read (default 0 = everything).

        Returns:
            JSON with last_read_message, unread_messages and unread_mention afterwards.
        """
        body = {"lastReadMessage": message_id} if message_id else {}
        data = await get_client().ocs_post_json(_chat_path(token, "read"), json_data=body)
        return json.dumps(_format_unread(data))

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def mark_conversation_unread(token: str) -> str:
        """Mark the last message of a conversation as unread again, as the "Mark as unread" menu entry does.

        Args:
            token: The conversation token.

        Returns:
            JSON with last_read_message, unread_messages and unread_mention afterwards.
        """
        data = await get_client().ocs_delete(_chat_path(token, "read"))
        return json.dumps(_format_unread(data))


def _register_chat_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def remove_reaction(token: str, message_id: int, reaction: str) -> str:
        """Take back your reaction to a chat message.

        Args:
            token: The conversation token.
            message_id: The message.
            reaction: The emoji to remove, e.g. "👍".

        Returns:
            JSON object mapping each remaining reaction to who used it.
        """
        path = f"apps/spreed/api/v1/reaction/{token}/{message_id}?reaction={quote(reaction, safe='')}"
        data = await get_client().ocs_delete(path)
        return json.dumps(_format_reactions(data), ensure_ascii=False)


_ROOM_API = "apps/spreed/api/v4/room"

# The endpoint behind each set_conversation_preferences argument
_SETTING_NAMES = {
    "favorite": "favorite",
    "archive": "archived",
    "important": "important",
    "sensitive": "sensitive",
    "notify": "notification_level",
    "notify-calls": "call_notifications",
}

# Talk takes these for a conversation; "default" (0) is only reported, for one never changed
_CONVERSATION_NOTIFICATION_LEVELS = {"always": 1, "mention": 2, "never": 3}


def _timestamp(value: str, field: str, future: bool = True) -> int:
    """Turn an ISO 8601 time with a time zone into the Unix timestamp Talk takes, by default refusing past times."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"{field} must be an ISO 8601 time, e.g. 2026-10-01T09:00:00+02:00; got {value!r}") from e
    if moment.tzinfo is None:
        raise ValueError(f"{field} needs a time zone, e.g. 2026-10-01T09:00:00+02:00 or 2026-10-01T07:00:00Z")
    if future and moment <= datetime.now(UTC):
        raise ValueError(f"{field} must be in the future")
    if moment.timestamp() < 0:
        raise ValueError(f"{field} must not be before 1970")
    return int(moment.timestamp())


def _iso(timestamp: Any) -> str:
    return datetime.fromtimestamp(int(timestamp), UTC).isoformat() if timestamp else ""


def _format_reminder(reminder: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": reminder.get("roomToken") or reminder.get("token", ""),
        "message_id": reminder.get("messageId", 0),
        "remind_at": _iso(reminder.get("reminderTimestamp") or reminder.get("timestamp")),
    }


async def _set_flag(token: str, endpoint: str, value: bool) -> dict[str, Any]:
    """Switch one of the per-user conversation flags that Talk sets with POST and clears with DELETE."""
    client = get_client()
    path = f"apps/spreed/api/v4/room/{token}/{endpoint}"
    data = await (client.ocs_post_json(path, json_data={}) if value else client.ocs_delete(path))
    return cast(dict[str, Any], data)


def _register_conversation_settings_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def set_conversation_preferences(
        token: str,
        favorite: bool | None = None,
        archived: bool | None = None,
        important: bool | None = None,
        sensitive: bool | None = None,
        notification_level: str | None = None,
        call_notifications: bool | None = None,
    ) -> str:
        """Change your own settings for a conversation; other participants are not affected.

        Only the settings you pass change, one request each; if one fails, the error
        names it and the settings already changed before it.

        Args:
            token: The conversation token.
            favorite: Pin it to the top of your conversation list.
            archived: Move it to (or out of) your archive; archived conversations
                stay listed with is_archived true.
            important: Notify you about it even when your status is Do not disturb.
            sensitive: Hide message previews for it in the conversation list and
                notifications.
            notification_level: When to notify you about messages: "always",
                "mention" (only when mentioned) or "never". Conversations you never
                changed report "default", which Talk does not accept as a setting.
            call_notifications: Whether to notify you when a call starts.

        Returns:
            JSON with the conversation afterwards, as get_conversation shows it.
        """
        changes: list[tuple[str, bool]] = [
            (endpoint, value)
            for endpoint, value in (
                ("favorite", favorite),
                ("archive", archived),
                ("important", important),
                ("sensitive", sensitive),
            )
            if value is not None
        ]
        level = None
        if notification_level is not None:
            level = _CONVERSATION_NOTIFICATION_LEVELS.get(notification_level.strip().lower())
            if level is None:
                valid = ", ".join(_CONVERSATION_NOTIFICATION_LEVELS)
                raise ValueError(f"Invalid level '{notification_level}'. Must be one of: {valid}")
        if not changes and level is None and call_notifications is None:
            raise ValueError("Pass at least one setting to change.")
        steps: list[tuple[str, str, bool | int]] = [(name, "flag", value) for name, value in changes]
        if level is not None:
            steps.append(("notify", "level", level))
        if call_notifications is not None:
            steps.append(("notify-calls", "level", int(call_notifications)))
        client = get_client()
        room: dict[str, Any] = {}
        applied: list[str] = []
        for name, kind, value in steps:
            try:
                if kind == "flag":
                    room = await _set_flag(token, name, bool(value))
                else:
                    room = await client.ocs_post_json(f"{_ROOM_API}/{token}/{name}", json_data={"level": value})
            except NextcloudError as e:
                done = f" ({', '.join(_SETTING_NAMES[n] for n in applied)} already changed)" if applied else ""
                raise NextcloudError(f"{e}. Failed at {_SETTING_NAMES[name]}{done}", e.status_code) from e
            applied.append(name)
        return json.dumps(_format_conversation(room), ensure_ascii=False)


def _register_pin_and_reminder_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def pin_message(token: str, message_id: int, until: str = "") -> str:
        """Pin a message to the top of the conversation for everyone. Needs moderator rights.

        Pinned messages are listed by list_shared_items with item_type "pinned".

        Args:
            token: The conversation token.
            message_id: The message to pin.
            until: When the pin should expire, as an ISO 8601 time with time zone
                (default: until someone unpins it). Talk removes expired pins on
                its next background job run.

        Returns:
            JSON with the pinned message, or a note that it was already pinned.
        """
        body = {"pinUntil": _timestamp(until, "until")} if until else {}
        data = await get_client().ocs_post_json(_chat_path(token, message_id, "pin"), json_data=body)
        if not data:
            # Talk answers an already pinned message with an empty 200 and keeps its expiry
            return f"Message {message_id} was already pinned; to change when the pin expires, unpin it first."
        return json.dumps(_format_message_full(data.get("parent") or data), ensure_ascii=False)

    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def set_message_reminder(token: str, message_id: int, remind_at: str) -> str:
        """Have Talk remind you about a message later with a notification. Replaces an earlier reminder.

        Args:
            token: The conversation token.
            message_id: The message to be reminded about.
            remind_at: When, as an ISO 8601 time with time zone, e.g.
                "2026-10-01T09:00:00+02:00". Must be in the future.

        Returns:
            JSON with token, message_id and remind_at (UTC).
        """
        body = {"timestamp": _timestamp(remind_at, "remind_at")}
        data = await get_client().ocs_post_json(_chat_path(token, message_id, "reminder"), json_data=body)
        return json.dumps(_format_reminder(data))

    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_message_reminders() -> str:
        """List your upcoming message reminders, soonest first.

        Talk returns at most the next 10, including ones already due but not yet
        sent, and leaves out federated conversations. In conversations you marked
        sensitive the message text is left empty.

        Returns:
            JSON list of reminders with token, message_id, remind_at (UTC) and the
            message as a compact line ("[id] author: text").
        """
        data = await get_client().ocs_get("apps/spreed/api/v1/chat/upcoming-reminders")
        reminders = sorted(data or [], key=lambda r: int(r.get("reminderTimestamp", 0)))
        return json.dumps(
            [
                {
                    **_format_reminder(r),
                    "message": _format_message_compact({**r, "id": r.get("messageId", 0)}),
                }
                for r in reminders
            ],
            ensure_ascii=False,
        )


def _register_pin_and_reminder_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def unpin_message(token: str, message_id: int, for_everyone: bool = True) -> str:
        """Unpin a message, for everyone (needs moderator rights) or only from your own view.

        Args:
            token: The conversation token.
            message_id: The pinned message.
            for_everyone: True (default) removes the pin for all participants; false
                only hides it for you. Talk remembers one hidden pin per
                conversation, so hiding another shows this one again.

        Returns:
            Confirmation message.
        """
        if not for_everyone:
            await get_client().ocs_delete(_chat_path(token, message_id, "pin", "self"))
            return f"Pinned message {message_id} hidden for you."
        data = await get_client().ocs_delete(_chat_path(token, message_id, "pin"))
        # Talk answers an empty 200 when the message was not pinned
        return f"Message {message_id} unpinned." if data else f"Message {message_id} was not pinned."

    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def remove_message_reminder(token: str, message_id: int) -> str:
        """Cancel your reminder about a message.

        Args:
            token: The conversation token.
            message_id: The message the reminder is for.

        Returns:
            Confirmation message.
        """
        await get_client().ocs_delete(_chat_path(token, message_id, "reminder"))
        return f"Reminder for message {message_id} removed."


def register(mcp: FastMCP) -> None:
    """Register Talk tools with the MCP server."""
    _register_read_tools(mcp)
    _register_poll_tools(mcp)
    _register_write_tools(mcp)
    _register_thread_read_tools(mcp)
    _register_thread_write_tools(mcp)
    _register_chat_read_tools(mcp)
    _register_chat_write_tools(mcp)
    _register_chat_destructive_tools(mcp)
    _register_conversation_settings_tools(mcp)
    _register_pin_and_reminder_tools(mcp)
    _register_pin_and_reminder_destructive_tools(mcp)
