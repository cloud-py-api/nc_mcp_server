"""Integration tests for notification tools against a real Nextcloud instance.

Tests call MCP tools by name to exercise the full tool stack.
"""

import json

import pytest

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


class TestListNotifications:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_notifications")
        data = json.loads(result)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_empty_when_no_notifications(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_notifications")
        data = json.loads(result)
        assert data == []

    @pytest.mark.asyncio
    async def test_shows_generated_notification(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.generate_notification(subject="test-visible", message="should appear")
        result = await nc_mcp.call("list_notifications")
        data = json.loads(result)
        assert len(data) >= 1
        subjects = [n["subject"] for n in data]
        assert "test-visible" in subjects

    @pytest.mark.asyncio
    async def test_notification_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.generate_notification(subject="fields-check")
        result = await nc_mcp.call("list_notifications")
        data = json.loads(result)
        notif = data[0]
        required_fields = ["notification_id", "app", "user", "datetime", "subject", "message"]
        for field in required_fields:
            assert field in notif, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_multiple_notifications_ordered_newest_first(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.generate_notification(subject="first")
        await nc_mcp.generate_notification(subject="second")
        await nc_mcp.generate_notification(subject="third")

        result = await nc_mcp.call("list_notifications")
        data = json.loads(result)
        assert len(data) >= 3
        # Newest should be first
        subjects = [n["subject"] for n in data[:3]]
        assert subjects == ["third", "second", "first"]

    @pytest.mark.asyncio
    async def test_notification_message_content(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.generate_notification(subject="msg-test", message="detailed body text")
        result = await nc_mcp.call("list_notifications")
        data = json.loads(result)
        notif = next(n for n in data if n["subject"] == "msg-test")
        assert notif["message"] == "detailed body text"


class TestDismissNotification:
    @pytest.mark.asyncio
    async def test_dismiss_removes_single(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.generate_notification(subject="to-dismiss")
        await nc_mcp.generate_notification(subject="to-keep")

        data = json.loads(await nc_mcp.call("list_notifications"))
        target = next(n for n in data if n["subject"] == "to-dismiss")
        nid = target["notification_id"]

        result = await nc_mcp.call("dismiss_notification", notification_id=nid)
        assert str(nid) in result

        remaining = json.loads(await nc_mcp.call("list_notifications"))
        remaining_ids = [n["notification_id"] for n in remaining]
        assert nid not in remaining_ids
        # The other one should still be there
        assert any(n["subject"] == "to-keep" for n in remaining)

    @pytest.mark.asyncio
    async def test_dismiss_nonexistent_is_idempotent(self, nc_mcp: McpTestHelper) -> None:
        # Nextcloud returns 200 for nonexistent IDs
        result = await nc_mcp.call("dismiss_notification", notification_id=999999)
        assert "999999" in result

    @pytest.mark.asyncio
    async def test_dismiss_confirmation_message(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.generate_notification(subject="confirm-test")
        data = json.loads(await nc_mcp.call("list_notifications"))
        nid = data[0]["notification_id"]

        result = await nc_mcp.call("dismiss_notification", notification_id=nid)
        assert "dismissed" in result.lower()


class TestDismissAllNotifications:
    @pytest.mark.asyncio
    async def test_clears_all(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.generate_notification(subject="clear-1")
        await nc_mcp.generate_notification(subject="clear-2")
        await nc_mcp.generate_notification(subject="clear-3")

        data = json.loads(await nc_mcp.call("list_notifications"))
        assert len(data) >= 3

        result = await nc_mcp.call("dismiss_all_notifications")
        assert "dismissed" in result.lower()

        remaining = json.loads(await nc_mcp.call("list_notifications"))
        assert remaining == []

    @pytest.mark.asyncio
    async def test_dismiss_all_when_empty(self, nc_mcp: McpTestHelper) -> None:
        # Should succeed without error even when empty
        result = await nc_mcp.call("dismiss_all_notifications")
        assert "dismissed" in result.lower()

        remaining = json.loads(await nc_mcp.call("list_notifications"))
        assert remaining == []
