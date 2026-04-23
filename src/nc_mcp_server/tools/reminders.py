"""File Reminders tools — per-file reminder notifications via OCS API."""

import json
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from ..annotations import ADDITIVE_IDEMPOTENT, DESTRUCTIVE, READONLY
from ..client import NextcloudError
from ..permissions import PermissionLevel, require_permission
from ..state import get_client


def _validate_due_date(due_date: str) -> None:
    """Validate ISO 8601 + future-date before any API call.

    Why: set_file_reminder works around a Nextcloud bug by calling DELETE
    before PUT. If the PUT then fails server-side validation, the original
    reminder is already gone. Pre-validating here keeps failed calls from
    destroying an existing reminder.

    Known narrow edge case: if the local clock is behind Nextcloud's and
    due_date falls inside the drift window, our check passes but NC's
    rejects — DELETE happens, PUT 400s, and an existing reminder is lost.
    Accepted: real reminders are minutes-to-days ahead, not seconds.
    """
    try:
        parsed = datetime.fromisoformat(due_date)
    except ValueError as exc:
        raise NextcloudError(
            f"Invalid due_date '{due_date}': must be an ISO 8601 timestamp with "
            "timezone, e.g. '2026-05-01T10:00:00+00:00' or '2026-05-01T10:00:00Z'.",
            400,
        ) from exc
    if parsed.tzinfo is None:
        raise NextcloudError(
            f"Invalid due_date '{due_date}': timezone is required (e.g. '+00:00' or 'Z').",
            400,
        )
    if parsed <= datetime.now(UTC):
        raise NextcloudError(
            f"Invalid due_date '{due_date}': must be in the future.",
            400,
        )


def _register_read_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READONLY)
    @require_permission(PermissionLevel.READ)
    async def get_file_reminder(file_id: int) -> str:
        """Get the reminder set on a specific file.

        Returns the due date as an ISO 8601 timestamp, or null if no reminder
        is set for the file. Also returns null when the file does not exist —
        the Nextcloud API does not distinguish the two cases, so this tool
        cannot be used to check file existence.

        Args:
            file_id: Numeric Nextcloud file id. Get this from list_directory,
                search_files, or any tool that returns file metadata.

        Returns:
            JSON object with file_id and due_date. due_date is null when no
            reminder is set.
        """
        client = get_client()
        data = await client.ocs_get(f"apps/files_reminders/api/v1/{file_id}")
        return json.dumps({"file_id": file_id, "due_date": data.get("dueDate")})


def _register_write_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ADDITIVE_IDEMPOTENT)
    @require_permission(PermissionLevel.WRITE)
    async def set_file_reminder(file_id: int, due_date: str) -> str:
        """Set or update the reminder on a file.

        The due date must be an ISO 8601 timestamp including a timezone and
        must be in the future. Past timestamps are rejected. Setting a
        reminder on a file that already has one replaces the existing one.

        Args:
            file_id: Numeric Nextcloud file id.
            due_date: ISO 8601 timestamp with timezone, e.g.
                "2026-05-01T10:00:00+00:00" or "2026-05-01T10:00:00Z".

        Returns:
            JSON object with file_id and due_date confirming the value set.
        """
        _validate_due_date(due_date)
        client = get_client()
        # Nextcloud's PUT endpoint silently no-ops when a reminder already exists
        # (RichReminder composition wrapper defeats the mapper's dirty tracking).
        # Delete first so the PUT always takes the INSERT path.
        try:
            await client.ocs_delete(f"apps/files_reminders/api/v1/{file_id}")
        except NextcloudError as e:
            if e.status_code != 404:
                raise
        try:
            await client.ocs_put(
                f"apps/files_reminders/api/v1/{file_id}",
                data={"dueDate": due_date},
            )
        except NextcloudError as e:
            if e.status_code == 404:
                raise NextcloudError(f"File with id {file_id} not found.", 404) from e
            if e.status_code == 400:
                raise NextcloudError(
                    f"Nextcloud rejected due_date '{due_date}'. Must be ISO 8601 in the future.",
                    400,
                ) from e
            raise
        return json.dumps({"file_id": file_id, "due_date": due_date})


def _register_destructive_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=DESTRUCTIVE)
    @require_permission(PermissionLevel.DESTRUCTIVE)
    async def remove_file_reminder(file_id: int) -> str:
        """Remove the reminder set on a file.

        Fails with "No reminder is set" if the file has no active reminder.

        Args:
            file_id: Numeric Nextcloud file id.

        Returns:
            Confirmation message.
        """
        client = get_client()
        try:
            await client.ocs_delete(f"apps/files_reminders/api/v1/{file_id}")
        except NextcloudError as e:
            if e.status_code == 404:
                raise NextcloudError(
                    f"No reminder is set on file {file_id}, or the file does not exist.",
                    404,
                ) from e
            raise
        return f"Reminder removed from file {file_id}."


def register(mcp: FastMCP) -> None:
    """Register file reminder tools with the MCP server."""
    _register_read_tools(mcp)
    _register_write_tools(mcp)
    _register_destructive_tools(mcp)
