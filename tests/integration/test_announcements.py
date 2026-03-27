"""Integration tests for Announcement Center tools against a real Nextcloud instance."""

import contextlib
import json
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

UNIQUE = "mcp-test-ann"


async def _create_announcement(nc_mcp: McpTestHelper, suffix: str = "") -> dict[str, Any]:
    """Helper: create an announcement and return the parsed response."""
    subject = f"{UNIQUE}-{suffix}" if suffix else UNIQUE
    result = await nc_mcp.call(
        "create_announcement",
        subject=subject,
        message=f"Body of {subject}",
    )
    return json.loads(result)


async def _cleanup_announcements(nc_mcp: McpTestHelper) -> None:
    """Delete all test announcements, paginating through all pages."""
    while True:
        result = await nc_mcp.call("list_announcements")
        data = json.loads(result)["data"]
        deleted = False
        for a in data:
            if str(a.get("subject", "")).startswith(UNIQUE):
                await nc_mcp.call("delete_announcement", announcement_id=a["id"])
                deleted = True
        if not deleted:
            break


class TestListAnnouncements:
    @pytest.mark.asyncio
    async def test_returns_json_with_data_and_pagination(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_announcements")
        parsed = json.loads(result)
        assert "data" in parsed
        assert "pagination" in parsed
        assert isinstance(parsed["data"], list)
        assert "count" in parsed["pagination"]
        assert "has_more" in parsed["pagination"]

    @pytest.mark.asyncio
    async def test_empty_list_when_no_announcements(self, nc_mcp: McpTestHelper) -> None:
        await _cleanup_announcements(nc_mcp)
        result = await nc_mcp.call("list_announcements")
        parsed = json.loads(result)
        test_announcements = [a for a in parsed["data"] if str(a.get("subject", "")).startswith(UNIQUE)]
        assert len(test_announcements) == 0

    @pytest.mark.asyncio
    async def test_lists_created_announcement(self, nc_mcp: McpTestHelper) -> None:
        await _cleanup_announcements(nc_mcp)
        created = await _create_announcement(nc_mcp, "list")
        result = await nc_mcp.call("list_announcements")
        parsed = json.loads(result)
        ids = [a["id"] for a in parsed["data"]]
        assert created["id"] in ids
        await nc_mcp.call("delete_announcement", announcement_id=created["id"])

    @pytest.mark.asyncio
    async def test_announcement_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        created = await _create_announcement(nc_mcp, "fields")
        try:
            result = await nc_mcp.call("list_announcements")
            parsed = json.loads(result)
            matches = [a for a in parsed["data"] if a["id"] == created["id"]]
            assert len(matches) == 1
            ann = matches[0]
            assert "id" in ann
            assert "author_id" in ann
            assert "author" in ann
            assert "time" in ann
            assert "subject" in ann
            assert "message" in ann
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=created["id"])

    @pytest.mark.asyncio
    async def test_pagination_offset(self, nc_mcp: McpTestHelper) -> None:
        await _cleanup_announcements(nc_mcp)
        a1 = await _create_announcement(nc_mcp, "page1")
        a2 = await _create_announcement(nc_mcp, "page2")
        try:
            result_all = await nc_mcp.call("list_announcements")
            all_ids = [a["id"] for a in json.loads(result_all)["data"]]
            assert a1["id"] in all_ids
            assert a2["id"] in all_ids
            newest_id = max(a1["id"], a2["id"])
            result_offset = await nc_mcp.call("list_announcements", offset=newest_id)
            offset_ids = [a["id"] for a in json.loads(result_offset)["data"]]
            assert newest_id not in offset_ids
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=a1["id"])
            await nc_mcp.call("delete_announcement", announcement_id=a2["id"])

    @pytest.mark.asyncio
    async def test_multi_page_cleanup(self, nc_mcp: McpTestHelper) -> None:
        """Create >7 announcements (server page size) and verify paginated cleanup catches all."""
        await _cleanup_announcements(nc_mcp)
        created_ids: list[int] = []
        try:
            for i in range(9):
                ann = await _create_announcement(nc_mcp, f"bulk{i}")
                created_ids.append(ann["id"])
            first_page = json.loads(await nc_mcp.call("list_announcements"))
            assert first_page["pagination"]["has_more"] is True
            await _cleanup_announcements(nc_mcp)
            result = await nc_mcp.call("list_announcements")
            remaining = [a for a in json.loads(result)["data"] if str(a.get("subject", "")).startswith(UNIQUE)]
            assert len(remaining) == 0, f"Leftover test announcements: {[a['subject'] for a in remaining]}"
        finally:
            for ann_id in created_ids:
                with contextlib.suppress(Exception):
                    await nc_mcp.call("delete_announcement", announcement_id=ann_id)

    @pytest.mark.asyncio
    async def test_pagination_info(self, nc_mcp: McpTestHelper) -> None:
        created = await _create_announcement(nc_mcp, "paginfo")
        try:
            result = await nc_mcp.call("list_announcements")
            pagination = json.loads(result)["pagination"]
            assert pagination["count"] >= 1
            assert isinstance(pagination["has_more"], bool)
            assert pagination["offset"] == 0
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=created["id"])

    @pytest.mark.asyncio
    async def test_newest_first_ordering(self, nc_mcp: McpTestHelper) -> None:
        await _cleanup_announcements(nc_mcp)
        a1 = await _create_announcement(nc_mcp, "order1")
        a2 = await _create_announcement(nc_mcp, "order2")
        try:
            result = await nc_mcp.call("list_announcements")
            data = json.loads(result)["data"]
            times = [a["time"] for a in data if str(a.get("subject", "")).startswith(UNIQUE)]
            assert len(times) == 2
            assert times[0] >= times[1]
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=a1["id"])
            await nc_mcp.call("delete_announcement", announcement_id=a2["id"])


class TestCreateAnnouncement:
    @pytest.mark.asyncio
    async def test_create_returns_announcement(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_announcement",
            subject=f"{UNIQUE}-create",
            message="Test body",
        )
        parsed = json.loads(result)
        try:
            assert "id" in parsed
            assert parsed["subject"] == f"{UNIQUE}-create"
            assert parsed["message"] == "Test body"
            assert parsed["author_id"] == "admin"
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=parsed["id"])

    @pytest.mark.asyncio
    async def test_create_with_markdown_message(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_announcement",
            subject=f"{UNIQUE}-md",
            message="**bold** and *italic*",
        )
        parsed = json.loads(result)
        try:
            assert parsed["message"] == "**bold** and *italic*"
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=parsed["id"])

    @pytest.mark.asyncio
    async def test_create_with_plain_message(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_announcement",
            subject=f"{UNIQUE}-plain",
            message="**markdown**",
            plain_message="plain text",
        )
        parsed = json.loads(result)
        try:
            assert parsed["id"] > 0
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=parsed["id"])

    @pytest.mark.asyncio
    async def test_create_with_specific_groups(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_announcement",
            subject=f"{UNIQUE}-groups",
            message="For admin group",
            groups=["admin"],
        )
        parsed = json.loads(result)
        try:
            group_ids = [g["id"] for g in parsed.get("groups", [])]
            assert "admin" in group_ids
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=parsed["id"])

    @pytest.mark.asyncio
    async def test_create_default_groups_is_everyone(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_announcement",
            subject=f"{UNIQUE}-everyone",
            message="For everyone",
        )
        parsed = json.loads(result)
        try:
            group_ids = [g["id"] for g in parsed.get("groups", [])]
            assert "everyone" in group_ids
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=parsed["id"])

    @pytest.mark.asyncio
    async def test_create_with_comments_disabled(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_announcement",
            subject=f"{UNIQUE}-nocomments",
            message="No comments",
            comments=False,
        )
        parsed = json.loads(result)
        try:
            assert parsed["comments"] is False
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=parsed["id"])

    @pytest.mark.asyncio
    async def test_create_with_notifications_disabled(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_announcement",
            subject=f"{UNIQUE}-nonotif",
            message="No notifications",
            activities=False,
            notifications=False,
            emails=False,
        )
        parsed = json.loads(result)
        try:
            assert parsed["id"] > 0
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=parsed["id"])

    @pytest.mark.asyncio
    async def test_create_empty_subject_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("create_announcement", subject="", message="body")

    @pytest.mark.asyncio
    async def test_create_whitespace_subject_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("create_announcement", subject="   ", message="body")

    @pytest.mark.asyncio
    async def test_create_subject_too_long_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("create_announcement", subject="x" * 513, message="body")

    @pytest.mark.asyncio
    async def test_create_subject_at_max_length(self, nc_mcp: McpTestHelper) -> None:
        subject = f"{UNIQUE}-" + "x" * (512 - len(UNIQUE) - 1)
        result = await nc_mcp.call("create_announcement", subject=subject, message="body")
        parsed = json.loads(result)
        try:
            assert parsed["subject"] == subject
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=parsed["id"])

    @pytest.mark.asyncio
    async def test_create_appears_in_list(self, nc_mcp: McpTestHelper) -> None:
        await _cleanup_announcements(nc_mcp)
        created = await _create_announcement(nc_mcp, "inlist")
        try:
            result = await nc_mcp.call("list_announcements")
            subjects = [a["subject"] for a in json.loads(result)["data"]]
            assert f"{UNIQUE}-inlist" in subjects
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=created["id"])

    @pytest.mark.asyncio
    async def test_create_has_timestamp(self, nc_mcp: McpTestHelper) -> None:
        created = await _create_announcement(nc_mcp, "time")
        try:
            assert isinstance(created["time"], int)
            assert created["time"] > 0
        finally:
            await nc_mcp.call("delete_announcement", announcement_id=created["id"])


class TestDeleteAnnouncement:
    @pytest.mark.asyncio
    async def test_delete_removes_announcement(self, nc_mcp: McpTestHelper) -> None:
        created = await _create_announcement(nc_mcp, "delete")
        ann_id = created["id"]
        result = await nc_mcp.call("delete_announcement", announcement_id=ann_id)
        assert str(ann_id) in result
        list_result = await nc_mcp.call("list_announcements")
        ids = [a["id"] for a in json.loads(list_result)["data"]]
        assert ann_id not in ids

    @pytest.mark.asyncio
    async def test_delete_returns_confirmation(self, nc_mcp: McpTestHelper) -> None:
        created = await _create_announcement(nc_mcp, "delconf")
        result = await nc_mcp.call("delete_announcement", announcement_id=created["id"])
        assert "deleted" in result.lower()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_succeeds(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("delete_announcement", announcement_id=999999)
        assert "999999" in result

    @pytest.mark.asyncio
    async def test_delete_is_idempotent(self, nc_mcp: McpTestHelper) -> None:
        created = await _create_announcement(nc_mcp, "idemp")
        ann_id = created["id"]
        await nc_mcp.call("delete_announcement", announcement_id=ann_id)
        result = await nc_mcp.call("delete_announcement", announcement_id=ann_id)
        assert str(ann_id) in result


class TestAnnouncementPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_announcements")
        parsed = json.loads(result)
        assert "data" in parsed

    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("create_announcement", subject="blocked", message="no")

    @pytest.mark.asyncio
    async def test_read_only_blocks_delete(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("delete_announcement", announcement_id=1)

    @pytest.mark.asyncio
    async def test_write_allows_create_but_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        result = await nc_mcp_write.call(
            "create_announcement",
            subject=f"{UNIQUE}-perm",
            message="permission test",
        )
        ann_id = json.loads(result)["id"]
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_write.call("delete_announcement", announcement_id=ann_id)
        # Clean up with a destructive-permission client (nc_mcp_write can't delete)
        client = nc_mcp_write.client
        await client.ocs_delete(f"apps/announcementcenter/api/v1/announcements/{ann_id}")
