"""Integration tests for File Reminders tools against a real Nextcloud instance."""

import contextlib
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError

from .conftest import TEST_BASE_DIR, McpTestHelper

pytestmark = pytest.mark.integration

FUTURE_DATE = "2030-01-01T10:00:00+00:00"
FURTHER_DATE = "2031-06-15T14:30:00+00:00"
PAST_DATE = "2020-01-01T10:00:00+00:00"


async def _make_test_file(nc_mcp: McpTestHelper, name: str) -> int:
    """Upload a test file and return its Nextcloud file id."""
    await nc_mcp.create_test_dir()
    path = f"{TEST_BASE_DIR}/{name}"
    await nc_mcp.upload_test_file(path, "reminder target")
    listing = json.loads(await nc_mcp.call("list_directory", path=TEST_BASE_DIR, limit=500))["data"]
    for entry in listing:
        if entry["path"].endswith(f"/{name}"):
            return int(entry["file_id"])
    raise AssertionError(f"{name} not found after upload")


async def _clear_reminder(nc_mcp: McpTestHelper, file_id: int) -> None:
    with contextlib.suppress(Exception):
        await nc_mcp.call("remove_file_reminder", file_id=file_id)


class TestGetFileReminder:
    @pytest.mark.asyncio
    async def test_returns_null_when_no_reminder(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-null.txt")
        await _clear_reminder(nc_mcp, file_id)
        result = await nc_mcp.call("get_file_reminder", file_id=file_id)
        data = json.loads(result)
        assert data == {"file_id": file_id, "due_date": None}

    @pytest.mark.asyncio
    async def test_returns_due_date_when_set(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-set-then-get.txt")
        try:
            await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=FUTURE_DATE)
            result = await nc_mcp.call("get_file_reminder", file_id=file_id)
            data = json.loads(result)
            assert data["file_id"] == file_id
            assert data["due_date"] == FUTURE_DATE
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_null(self, nc_mcp: McpTestHelper) -> None:
        """The Nextcloud API does not distinguish 'no reminder' from 'no file' on GET."""
        result = await nc_mcp.call("get_file_reminder", file_id=999_999_999)
        data = json.loads(result)
        assert data == {"file_id": 999_999_999, "due_date": None}


class TestSetFileReminder:
    @pytest.mark.asyncio
    async def test_set_creates_reminder(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-create.txt")
        try:
            result = await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=FUTURE_DATE)
            data = json.loads(result)
            assert data == {"file_id": file_id, "due_date": FUTURE_DATE}
            verify = json.loads(await nc_mcp.call("get_file_reminder", file_id=file_id))
            assert verify["due_date"] == FUTURE_DATE
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_set_replaces_existing_reminder(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-replace.txt")
        try:
            await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=FUTURE_DATE)
            await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=FURTHER_DATE)
            verify = json.loads(await nc_mcp.call("get_file_reminder", file_id=file_id))
            assert verify["due_date"] == FURTHER_DATE
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_past_date_rejected(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-past.txt")
        try:
            with pytest.raises((ToolError, NextcloudError), match=r"[Ii]nvalid|[Ff]uture|ISO 8601"):
                await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=PAST_DATE)
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_malformed_date_rejected(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-malformed.txt")
        try:
            with pytest.raises((ToolError, NextcloudError), match=r"[Ii]nvalid|[Ff]uture|ISO 8601"):
                await nc_mcp.call("set_file_reminder", file_id=file_id, due_date="not a date")
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_missing_timezone_rejected(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-no-tz.txt")
        try:
            with pytest.raises((ToolError, NextcloudError), match=r"[Tt]imezone|[Ii]nvalid"):
                await nc_mcp.call("set_file_reminder", file_id=file_id, due_date="2030-01-01T10:00:00")
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises_404(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, NextcloudError), match=r"[Ff]ile.*not found|404"):
            await nc_mcp.call("set_file_reminder", file_id=999_999_999, due_date=FUTURE_DATE)

    @pytest.mark.asyncio
    async def test_past_date_preserves_existing_reminder(self, nc_mcp: McpTestHelper) -> None:
        """Regression: failed validation must not destroy a pre-existing reminder.

        set_file_reminder works around an NC bug by DELETE-then-PUT. If the
        PUT fails server-side validation, the old reminder is already gone.
        Client-side pre-validation prevents that data loss.
        """
        file_id = await _make_test_file(nc_mcp, "reminder-past-preserves.txt")
        try:
            await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=FUTURE_DATE)
            with pytest.raises((ToolError, NextcloudError)):
                await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=PAST_DATE)
            verify = json.loads(await nc_mcp.call("get_file_reminder", file_id=file_id))
            assert verify["due_date"] == FUTURE_DATE, "Existing reminder was destroyed by failed update"
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_malformed_date_preserves_existing_reminder(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-garbage-preserves.txt")
        try:
            await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=FUTURE_DATE)
            with pytest.raises((ToolError, NextcloudError)):
                await nc_mcp.call("set_file_reminder", file_id=file_id, due_date="not a date")
            verify = json.loads(await nc_mcp.call("get_file_reminder", file_id=file_id))
            assert verify["due_date"] == FUTURE_DATE, "Existing reminder was destroyed by failed update"
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_z_timezone_accepted(self, nc_mcp: McpTestHelper) -> None:
        """ISO 8601 'Z' (UTC) suffix must be accepted equivalently to '+00:00'."""
        file_id = await _make_test_file(nc_mcp, "reminder-z-tz.txt")
        try:
            z_date = "2030-01-01T10:00:00Z"
            await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=z_date)
            verify = json.loads(await nc_mcp.call("get_file_reminder", file_id=file_id))
            assert verify["due_date"] is not None, "Reminder wasn't stored when using 'Z' suffix"
        finally:
            await _clear_reminder(nc_mcp, file_id)


class TestRemoveFileReminder:
    @pytest.mark.asyncio
    async def test_remove_existing_reminder(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-remove.txt")
        try:
            await nc_mcp.call("set_file_reminder", file_id=file_id, due_date=FUTURE_DATE)
            result = await nc_mcp.call("remove_file_reminder", file_id=file_id)
            assert str(file_id) in result
            verify = json.loads(await nc_mcp.call("get_file_reminder", file_id=file_id))
            assert verify["due_date"] is None
        finally:
            await _clear_reminder(nc_mcp, file_id)

    @pytest.mark.asyncio
    async def test_remove_when_no_reminder_raises(self, nc_mcp: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp, "reminder-remove-missing.txt")
        await _clear_reminder(nc_mcp, file_id)
        with pytest.raises((ToolError, NextcloudError), match=r"[Nn]o reminder|404|not exist"):
            await nc_mcp.call("remove_file_reminder", file_id=file_id)

    @pytest.mark.asyncio
    async def test_remove_nonexistent_file_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, NextcloudError), match=r"[Nn]o reminder|404|not exist"):
            await nc_mcp.call("remove_file_reminder", file_id=999_999_999)


class TestFileReminderPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_get(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("get_file_reminder", file_id=1)
        data = json.loads(result)
        assert "file_id" in data
        assert "due_date" in data

    @pytest.mark.asyncio
    async def test_read_only_blocks_set(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("set_file_reminder", file_id=1, due_date=FUTURE_DATE)

    @pytest.mark.asyncio
    async def test_read_only_blocks_remove(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("remove_file_reminder", file_id=1)

    @pytest.mark.asyncio
    async def test_write_blocks_remove(self, nc_mcp_write: McpTestHelper) -> None:
        """remove_file_reminder is DESTRUCTIVE — WRITE level alone should not authorize it."""
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("remove_file_reminder", file_id=1)

    @pytest.mark.asyncio
    async def test_write_allows_set(self, nc_mcp_write: McpTestHelper) -> None:
        file_id = await _make_test_file(nc_mcp_write, "reminder-perm-write.txt")
        try:
            result = await nc_mcp_write.call("set_file_reminder", file_id=file_id, due_date=FUTURE_DATE)
            assert json.loads(result)["due_date"] == FUTURE_DATE
        finally:
            await _clear_reminder(nc_mcp_write, file_id)
