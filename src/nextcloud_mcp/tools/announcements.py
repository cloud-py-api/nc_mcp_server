"""Announcement Center tools — list, create, and delete announcements via OCS API."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE, DESTRUCTIVE, READONLY
from ..permissions import PermissionLevel, require_permission
from ..state import get_client

ANNOUNCEMENTS_API = "apps/announcementcenter/api/v1/announcements"


def _format_announcement(a: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": a.get("id"),
        "author_id": a.get("author_id"),
        "author": a.get("author"),
        "time": a.get("time"),
        "subject": a.get("subject"),
        "message": a.get("message"),
    }
    groups = a.get("groups")
    if groups is not None:
        result["groups"] = groups
    if "comments" in a:
        result["comments"] = a["comments"]
    if a.get("schedule_time") is not None:
        result["schedule_time"] = a["schedule_time"]
    if a.get("delete_time") is not None:
        result["delete_time"] = a["delete_time"]
    return result


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def list_announcements(offset: int = 0) -> str:
        """List announcements from the Nextcloud Announcement Center.

        Returns announcements visible to the current user, sorted by newest first.
        The server returns up to 7 announcements per page.

        Admins see all announcements. Regular users only see announcements
        targeted at their groups or at "everyone".

        Args:
            offset: Announcement ID to paginate from. Pass the smallest ID
                    from a previous call to fetch older announcements.
                    Default 0 means start from the newest.

        Returns:
            JSON object with "data" (list of announcements) and "pagination"
            (count, offset, has_more). Each announcement has: id, author_id,
            author, time (unix), subject, message (markdown), groups (admin only),
            comments (count or false if disabled).
        """
        client = get_client()
        params: dict[str, str] = {}
        if offset > 0:
            params["offset"] = str(offset)
        data = await client.ocs_get(ANNOUNCEMENTS_API, params=params)
        announcements = [_format_announcement(a) for a in data]
        page_limit = 7
        result: dict[str, Any] = {
            "data": announcements,
            "pagination": {
                "count": len(announcements),
                "offset": offset,
                "has_more": len(announcements) == page_limit,
            },
        }
        return json.dumps(result, indent=2, default=str)


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @require_permission(PermissionLevel.WRITE)
    async def create_announcement(
        subject: str,
        message: str = "",
        plain_message: str = "",
        groups: list[str] | None = None,
        activities: bool = True,
        notifications: bool = True,
        emails: bool = False,
        comments: bool = True,
    ) -> str:
        """Create a new announcement in Nextcloud. Requires admin privileges.

        Announcements are posted to the Announcement Center and optionally
        trigger activity entries, notifications, and/or emails for the
        targeted groups.

        Args:
            subject: Announcement title (1-512 characters, required).
            message: Announcement body in Markdown format.
            plain_message: Plain text version of the body. If empty, the
                          markdown message is used as fallback.
            groups: List of group IDs to target (e.g. ["admin", "staff"]).
                    Default: ["everyone"] (visible to all users).
            activities: Post to the activity stream (default: true).
            notifications: Send in-app notifications (default: true).
            emails: Send email notifications (default: false).
            comments: Allow comments on this announcement (default: true).

        Returns:
            JSON object with the created announcement details.
        """
        if not subject.strip():
            raise ValueError("Announcement subject cannot be empty.")
        if len(subject) > 512:
            raise ValueError(f"Subject too long ({len(subject)} chars). Maximum is 512.")
        client = get_client()
        target_groups = groups or ["everyone"]
        post_data: dict[str, Any] = {
            "subject": subject,
            "message": message,
            "plainMessage": plain_message or message,
            "groups[]": target_groups,
            "activities": 1 if activities else 0,
            "notifications": 1 if notifications else 0,
            "emails": 1 if emails else 0,
            "comments": 1 if comments else 0,
        }
        data = await client.ocs_post(ANNOUNCEMENTS_API, data=post_data)
        return json.dumps(_format_announcement(data), indent=2, default=str)


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def delete_announcement(announcement_id: int) -> str:
        """Delete an announcement from Nextcloud. Requires admin privileges.

        This permanently removes the announcement and all associated
        comments and notifications. This action is irreversible.

        Args:
            announcement_id: The numeric announcement ID. Use list_announcements to find IDs.

        Returns:
            Confirmation message.
        """
        client = get_client()
        await client.ocs_delete(f"{ANNOUNCEMENTS_API}/{announcement_id}")
        return f"Announcement {announcement_id} deleted."


def register(mcp: FastMCP) -> None:
    """Register announcement tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
