"""Integration tests for Tasks tools against a real Nextcloud instance."""

import contextlib
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

LIST_ID = "tasks"

MKCALENDAR_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<cal:mkcalendar xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
    "<d:set><d:prop>"
    "<d:displayname>Tasks</d:displayname>"
    "<cal:supported-calendar-component-set>"
    '<cal:comp name="VTODO"/>'
    "</cal:supported-calendar-component-set>"
    "</d:prop></d:set>"
    "</cal:mkcalendar>"
)


@pytest.fixture(autouse=True)
async def _ensure_task_list_and_cleanup(nc_mcp: McpTestHelper) -> None:
    """Ensure the 'tasks' CalDAV list exists and clean up test tasks."""
    with contextlib.suppress(Exception):
        await nc_mcp.client.dav_request(
            "MKCALENDAR",
            f"calendars/{nc_mcp.client._config.user}/{LIST_ID}/",
            body=MKCALENDAR_BODY,
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
    result = await nc_mcp.call("get_tasks", list_id=LIST_ID, limit=500)
    for task in json.loads(result)["data"]:
        uid = task["uid"]
        if "mcp-test" in task.get("summary", "") or "mcp-test" in uid:
            with contextlib.suppress(ToolError):
                await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=uid)


class TestListTaskLists:
    @pytest.mark.asyncio
    async def test_returns_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_task_lists")
        task_lists = json.loads(result)
        assert isinstance(task_lists, list)
        assert task_lists

    @pytest.mark.asyncio
    async def test_tasks_list_exists(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_task_lists")
        task_lists = json.loads(result)
        ids = [tl["id"] for tl in task_lists]
        assert LIST_ID in ids

    @pytest.mark.asyncio
    async def test_task_list_has_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_task_lists")
        task_lists = json.loads(result)
        tasks_list = next(tl for tl in task_lists if tl["id"] == LIST_ID)
        for field in ["id", "name", "components", "writable"]:
            assert field in tasks_list, f"Missing field: {field}"
        assert tasks_list["writable"] is True
        assert "VTODO" in tasks_list["components"]

    @pytest.mark.asyncio
    async def test_skips_inbox_outbox_trashbin(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_task_lists")
        task_lists = json.loads(result)
        ids = [tl["id"] for tl in task_lists]
        for skip in ["inbox", "outbox", "trashbin"]:
            assert skip not in ids

    @pytest.mark.asyncio
    async def test_excludes_event_only_calendars(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_task_lists")
        task_lists = json.loads(result)
        for tl in task_lists:
            assert "VTODO" in tl["components"]


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_create_basic_task(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-basic")
        created = json.loads(result)
        assert "uid" in created
        assert created["summary"] == "mcp-test-basic"

        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["summary"] == "mcp-test-basic"
        assert task["status"] == "NEEDS-ACTION"

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_create_with_due_date(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_task",
            list_id=LIST_ID,
            summary="mcp-test-due",
            due="2026-06-15T18:00:00Z",
        )
        created = json.loads(result)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["due"] is not None
        assert "2026-06-15T18:00:00" in task["due"]

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_create_with_description(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_task",
            list_id=LIST_ID,
            summary="mcp-test-desc",
            description="Detailed task notes with special chars: <>&",
        )
        created = json.loads(result)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["description"] == "Detailed task notes with special chars: <>&"

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_create_with_priority(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_task",
            list_id=LIST_ID,
            summary="mcp-test-priority",
            priority=1,
        )
        created = json.loads(result)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["priority"] == 1

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_create_with_categories(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_task",
            list_id=LIST_ID,
            summary="mcp-test-cats",
            categories="Work, Urgent, Important",
        )
        created = json.loads(result)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert "categories" in task
        assert "Work" in task["categories"]
        assert "Urgent" in task["categories"]
        assert "Important" in task["categories"]

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_create_with_in_process_status(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_task",
            list_id=LIST_ID,
            summary="mcp-test-inprocess",
            status="IN-PROCESS",
            percent_complete=50,
        )
        created = json.loads(result)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["status"] == "IN-PROCESS"
        assert task["percent_complete"] == 50

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_create_with_start_and_due(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call(
            "create_task",
            list_id=LIST_ID,
            summary="mcp-test-startdue",
            start="2026-06-01T09:00:00Z",
            due="2026-06-15T18:00:00Z",
        )
        created = json.loads(result)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert "2026-06-01T09:00:00" in task["dtstart"]
        assert "2026-06-15T18:00:00" in task["due"]

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_create_invalid_status_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-bad-status",
                status="INVALID",
            )

    @pytest.mark.asyncio
    async def test_create_invalid_priority_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-bad-priority",
                priority=10,
            )

    @pytest.mark.asyncio
    async def test_create_in_nonexistent_list_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call(
                "create_task",
                list_id="nonexistent-list-xyz",
                summary="mcp-test-bad-list",
            )


class TestGetTasks:
    @pytest.mark.asyncio
    async def test_get_empty_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_tasks", list_id=LIST_ID, limit=500)
        data = json.loads(result)
        assert isinstance(data["data"], list)
        assert "pagination" in data

    @pytest.mark.asyncio
    async def test_get_tasks_returns_created(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-list"))
        result = await nc_mcp.call("get_tasks", list_id=LIST_ID, limit=500)
        tasks = json.loads(result)["data"]
        uids = [t["uid"] for t in tasks]
        assert created["uid"] in uids

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_pagination(self, nc_mcp: McpTestHelper) -> None:
        uid1 = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-page1"))["uid"]
        uid2 = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-page2"))["uid"]
        uid3 = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-page3"))["uid"]

        result = await nc_mcp.call("get_tasks", list_id=LIST_ID, limit=2, offset=0)
        data = json.loads(result)
        assert data["pagination"]["count"] == 2
        assert data["pagination"]["has_more"] is True

        result2 = await nc_mcp.call("get_tasks", list_id=LIST_ID, limit=2, offset=2)
        data2 = json.loads(result2)
        assert data2["pagination"]["count"] >= 1

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=uid1)
        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=uid2)
        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=uid3)

    @pytest.mark.asyncio
    async def test_task_has_etag(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-etag"))
        result = await nc_mcp.call("get_tasks", list_id=LIST_ID, limit=500)
        tasks = json.loads(result)["data"]
        matching = [t for t in tasks if t["uid"] == created["uid"]]
        assert len(matching) == 1
        assert matching[0]["etag"]

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])


class TestGetTask:
    @pytest.mark.asyncio
    async def test_get_single_task(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-single",
                description="Single task test",
                priority=5,
            )
        )
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["uid"] == created["uid"]
        assert task["summary"] == "mcp-test-single"
        assert task["description"] == "Single task test"
        assert task["priority"] == 5
        assert task["etag"]

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_get_nonexistent_task_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="not found"):
            await nc_mcp.call("get_task", list_id=LIST_ID, task_uid="nonexistent-uid-xyz")


class TestUpdateTask:
    @pytest.mark.asyncio
    async def test_update_summary(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-update-orig"))
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], summary="mcp-test-updated")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["summary"] == "mcp-test-updated"

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_description(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-update-desc",
                description="Original",
            )
        )
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], description="Updated desc")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["description"] == "Updated desc"

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_clear_description(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-clear-desc",
                description="To be cleared",
            )
        )
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], description="")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["description"] == ""

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_due_date(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-update-due"))
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], due="2026-12-25T18:00:00Z")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert "2026-12-25T18:00:00" in task["due"]

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_clear_due_date(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-clear-due",
                due="2026-12-25T18:00:00Z",
            )
        )
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], due="")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["due"] is None

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_status_to_in_process(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-status"))
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], status="IN-PROCESS")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["status"] == "IN-PROCESS"

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_status_to_completed_sets_fields(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-complete-via-update"))
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], status="COMPLETED")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["status"] == "COMPLETED"
        assert task["percent_complete"] == 100
        assert task["completed"] is not None

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_priority(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-update-pri"))
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], priority=1)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["priority"] == 1

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_percent_complete(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-update-pct"))
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], percent_complete=75)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["percent_complete"] == 75

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_categories(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-update-cats"))
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], categories="Personal,Travel")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert "categories" in task
        assert "Personal" in task["categories"]
        assert "Travel" in task["categories"]

        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], categories="")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert "categories" not in task or task.get("categories") == []

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_preserves_unchanged_fields(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-preserve",
                description="Original desc",
                priority=3,
                due="2026-08-01T18:00:00Z",
            )
        )
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], summary="mcp-test-new-title")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["summary"] == "mcp-test-new-title"
        assert task["description"] == "Original desc"
        assert task["priority"] == 3
        assert "2026-08-01T18:00:00" in task["due"]

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="not found"):
            await nc_mcp.call("update_task", list_id=LIST_ID, task_uid="nonexistent-uid", summary="x")

    @pytest.mark.asyncio
    async def test_update_invalid_status_raises(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-bad-status-upd"))
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], status="BADSTATUS")

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])


class TestCompleteTask:
    @pytest.mark.asyncio
    async def test_complete_task(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-complete"))
        result = await nc_mcp.call("complete_task", list_id=LIST_ID, task_uid=created["uid"])
        assert "completed" in result.lower()

        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["status"] == "COMPLETED"
        assert task["percent_complete"] == 100
        assert task["completed"] is not None

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_complete_already_completed(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-double-complete",
                status="COMPLETED",
            )
        )
        result = await nc_mcp.call("complete_task", list_id=LIST_ID, task_uid=created["uid"])
        assert "completed" in result.lower()

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_complete_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="not found"):
            await nc_mcp.call("complete_task", list_id=LIST_ID, task_uid="nonexistent-uid-xyz")


class TestDeleteTask:
    @pytest.mark.asyncio
    async def test_delete_task(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-delete"))
        result = await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])
        assert "deleted" in result.lower()

        with pytest.raises(ToolError, match="not found"):
            await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="not found"):
            await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid="nonexistent-uid-xyz")


class TestTaskPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_task_lists")
        assert isinstance(json.loads(result), list)

    @pytest.mark.asyncio
    async def test_read_only_allows_get_tasks(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("get_tasks", list_id=LIST_ID, limit=10)
        assert isinstance(json.loads(result)["data"], list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("create_task", list_id=LIST_ID, summary="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_update(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("update_task", list_id=LIST_ID, task_uid="any", summary="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_complete(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("complete_task", list_id=LIST_ID, task_uid="any")

    @pytest.mark.asyncio
    async def test_read_only_blocks_delete(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'destructive' permission"):
            await nc_mcp_read_only.call("delete_task", list_id=LIST_ID, task_uid="any")


class TestTaskEdgeCases:
    @pytest.mark.asyncio
    async def test_unicode_summary_and_description(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-unicode: Ünïcödé 日本語",
                description="Описание на русском",
            )
        )
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert "Ünïcödé" in task["summary"]
        assert "日本語" in task["summary"]
        assert "Описание на русском" in task["description"]

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_reopen_completed_task(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-reopen"))
        await nc_mcp.call("complete_task", list_id=LIST_ID, task_uid=created["uid"])
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], status="NEEDS-ACTION")
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert task["status"] == "NEEDS-ACTION"
        assert task["completed"] is None

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_with_valid_etag(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-etag-valid"))
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        await nc_mcp.call(
            "update_task",
            list_id=LIST_ID,
            task_uid=created["uid"],
            summary="mcp-test-etag-updated",
            etag=task["etag"],
        )
        updated = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        assert updated["summary"] == "mcp-test-etag-updated"

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_update_with_stale_etag_raises(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-etag-stale"))
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        stale_etag = task["etag"]
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], summary="mcp-test-etag-changed")
        with pytest.raises(ToolError, match="412"):
            await nc_mcp.call(
                "update_task",
                list_id=LIST_ID,
                task_uid=created["uid"],
                summary="mcp-test-etag-conflict",
                etag=stale_etag,
            )

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_complete_with_stale_etag_raises(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(await nc_mcp.call("create_task", list_id=LIST_ID, summary="mcp-test-complete-etag"))
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=created["uid"]))
        stale_etag = task["etag"]
        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=created["uid"], summary="mcp-test-etag-changed")
        with pytest.raises(ToolError, match="412"):
            await nc_mcp.call("complete_task", list_id=LIST_ID, task_uid=created["uid"], etag=stale_etag)

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=created["uid"])

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_task",
                list_id=LIST_ID,
                summary="mcp-test-lifecycle",
                description="Lifecycle test",
                priority=5,
            )
        )
        uid = created["uid"]

        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=uid))
        assert task["status"] == "NEEDS-ACTION"

        await nc_mcp.call("update_task", list_id=LIST_ID, task_uid=uid, status="IN-PROCESS", percent_complete=50)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=uid))
        assert task["status"] == "IN-PROCESS"
        assert task["percent_complete"] == 50

        await nc_mcp.call("complete_task", list_id=LIST_ID, task_uid=uid)
        task = json.loads(await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=uid))
        assert task["status"] == "COMPLETED"
        assert task["percent_complete"] == 100
        assert task["completed"] is not None

        await nc_mcp.call("delete_task", list_id=LIST_ID, task_uid=uid)
        with pytest.raises(ToolError, match="not found"):
            await nc_mcp.call("get_task", list_id=LIST_ID, task_uid=uid)
