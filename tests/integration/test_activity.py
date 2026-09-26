"""Integration tests for Activity tools against a real Nextcloud instance."""

import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.tools import activity

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


async def _generate_activity(nc_mcp: McpTestHelper) -> None:
    """Generate multiple distinct file activities to ensure enough ungrouped entries."""
    for i in range(3):
        name = f"activity-test-{i}.txt"
        await nc_mcp.client.dav_put(name, f"activity {i}".encode(), content_type="text/plain")
        await nc_mcp.client.dav_delete(name)


class TestGetActivity:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity")
        data = json.loads(result)["data"]
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_activity_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity")
        data = json.loads(result)["data"]
        assert len(data) >= 1
        entry = data[0]
        for field in ["activity_id", "app", "type", "user", "subject", "datetime"]:
            assert field in entry, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_file_activity_shows_in_feed(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.client.dav_put("activity-visible.txt", b"visible", content_type="text/plain")
        try:
            result = await nc_mcp.call("get_activity", activity_filter="files", limit=10)
            assert "activity-visible" in result
        finally:
            await nc_mcp.client.dav_delete("activity-visible.txt")

    @pytest.mark.asyncio
    async def test_default_filter_is_all(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity")
        data = json.loads(result)["data"]
        assert len(data) >= 1

    @pytest.mark.asyncio
    async def test_self_filter(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", activity_filter="self")
        data = json.loads(result)["data"]
        for entry in data:
            assert entry["user"] == "admin"

    @pytest.mark.asyncio
    async def test_files_filter(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", activity_filter="files")
        data = json.loads(result)["data"]
        for entry in data:
            assert entry["app"] == "files"

    @pytest.mark.asyncio
    async def test_limit_parameter(self, nc_mcp: McpTestHelper) -> None:
        for i in range(5):
            await nc_mcp.client.dav_put(f"activity-limit-{i}.txt", b"test", content_type="text/plain")
        try:
            result = await nc_mcp.call("get_activity", activity_filter="files", limit=3)
            data = json.loads(result)["data"]
            assert len(data) <= 3
        finally:
            for i in range(5):
                with contextlib.suppress(Exception):
                    await nc_mcp.client.dav_delete(f"activity-limit-{i}.txt")

    @pytest.mark.asyncio
    async def test_pagination_with_since(self, nc_mcp: McpTestHelper) -> None:
        for i in range(6):
            await nc_mcp.client.dav_put(f"activity-page-{i}.txt", b"test", content_type="text/plain")
        try:
            result1 = await nc_mcp.call("get_activity", activity_filter="files", limit=3)
            data1 = json.loads(result1)["data"]
            assert len(data1) >= 1

            oldest_id = min(a["activity_id"] for a in data1)
            result2 = await nc_mcp.call("get_activity", activity_filter="files", limit=3, since=oldest_id)
            data2 = json.loads(result2)["data"]

            ids1 = {a["activity_id"] for a in data1}
            ids2 = {a["activity_id"] for a in data2}
            assert ids1.isdisjoint(ids2), "Paginated results should not overlap"
        finally:
            for i in range(6):
                with contextlib.suppress(Exception):
                    await nc_mcp.client.dav_delete(f"activity-page-{i}.txt")

    @pytest.mark.asyncio
    async def test_pagination_info_present(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity")
        parsed = json.loads(result)
        assert "pagination" in parsed
        assert "since" in parsed["pagination"]
        assert "has_more" in parsed["pagination"]
        assert "count" in parsed["pagination"]

    @pytest.mark.asyncio
    async def test_sort_asc(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", sort="asc", limit=10)
        data = json.loads(result)["data"]
        assert len(data) >= 2
        ids = [a["activity_id"] for a in data]
        assert ids == sorted(ids), "Ascending sort should have IDs in order"

    @pytest.mark.asyncio
    async def test_sort_desc(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", sort="desc", limit=10)
        data = json.loads(result)["data"]
        assert len(data) >= 2
        ids = [a["activity_id"] for a in data]
        assert ids == sorted(ids, reverse=True), "Descending sort should have IDs in reverse order"

    @pytest.mark.asyncio
    async def test_pagination_since_desc_is_below_the_page(self, nc_mcp: McpTestHelper) -> None:
        """The cursor comes from Activity, which counts the entries it grouped into the page too."""
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", sort="desc", limit=5)
        parsed = json.loads(result)
        data = parsed["data"]
        assert len(data) >= 2
        assert parsed["pagination"]["since"] <= min(a["activity_id"] for a in data)

    @pytest.mark.asyncio
    async def test_walking_pages(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        seen: list[int] = []
        since = 0
        for _ in range(4):
            page = json.loads(await nc_mcp.call("get_activity", activity_filter="files", limit=3, since=since))
            ids = [a["activity_id"] for a in page["data"]]
            assert ids
            assert not set(ids) & set(seen)
            seen += ids
            if not page["pagination"]["has_more"]:
                break
            assert page["pagination"]["since"] < (since or 2**63)
            since = page["pagination"]["since"]
        assert seen == sorted(seen, reverse=True)

    @pytest.mark.asyncio
    async def test_pagination_since_asc_uses_max_id(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", sort="asc", limit=1)
        parsed = json.loads(result)
        data = parsed["data"]
        assert len(data) >= 1
        since = parsed["pagination"]["since"]
        assert since >= max(a["activity_id"] for a in data)
        result2 = await nc_mcp.call("get_activity", sort="asc", limit=5, since=since)
        data2 = json.loads(result2)["data"]
        assert len(data2) >= 1, "Paginating forward with since cursor should return more activities"
        assert all(a["activity_id"] > since for a in data2)

    @pytest.mark.asyncio
    async def test_invalid_filter_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="Unknown activity_filter 'nonexistent'"):
            await nc_mcp.call("get_activity", activity_filter="nonexistent")

    @pytest.mark.asyncio
    async def test_page_past_the_end_is_empty(self, nc_mcp: McpTestHelper) -> None:
        """Activity answers 304 with no body when nothing is left."""
        result = json.loads(await nc_mcp.call("get_activity", since=1))
        assert result == {"data": [], "pagination": {"count": 0, "has_more": False, "since": None}}

    @pytest.mark.asyncio
    async def test_object_type_needs_object_id(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="must be given together"):
            await nc_mcp.call("get_activity", object_type="files")

    @pytest.mark.asyncio
    async def test_invalid_sort_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("get_activity", sort="invalid")

    @pytest.mark.asyncio
    async def test_activity_includes_object_info(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.client.dav_put("activity-obj.txt", b"obj", content_type="text/plain")
        try:
            result = await nc_mcp.call("get_activity", activity_filter="files", limit=5)
            data = json.loads(result)["data"]
            file_activities = [a for a in data if a.get("object_type") == "files"]
            assert len(file_activities) >= 1
            a = file_activities[0]
            assert "object_id" in a
            assert "object_name" in a
        finally:
            await nc_mcp.client.dav_delete("activity-obj.txt")

    @pytest.mark.asyncio
    async def test_limit_clamped(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", limit=999)
        data = json.loads(result)["data"]
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_object_type_and_object_id_filter(self, nc_mcp: McpTestHelper) -> None:
        await nc_mcp.client.dav_put("activity-objfilter.txt", b"filter test", content_type="text/plain")
        try:
            all_result = await nc_mcp.call("get_activity", activity_filter="files", limit=5)
            all_data = json.loads(all_result)["data"]
            file_acts = [a for a in all_data if "activity-objfilter" in a.get("subject", "")]
            assert len(file_acts) >= 1
            obj_id = file_acts[0]["object_id"]
            filtered = await nc_mcp.call("get_activity", object_type="files", object_id=obj_id, limit=10)
            filtered_data = json.loads(filtered)["data"]
            assert len(filtered_data) >= 1
            assert all(a.get("object_id") == obj_id for a in filtered_data)
            with pytest.raises(ToolError, match="cannot be combined with an activity_filter"):
                await nc_mcp.call("get_activity", activity_filter="files", object_type="files", object_id=obj_id)
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.client.dav_delete("activity-objfilter.txt")

    @pytest.mark.asyncio
    async def test_activity_with_message_field(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", limit=50)
        data = json.loads(result)["data"]
        activities_with_message = [a for a in data if "message" in a]
        activities_without_message = [a for a in data if "message" not in a]
        assert len(activities_with_message) + len(activities_without_message) == len(data)


class TestActivityPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_get_activity(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("get_activity")
        data = json.loads(result)["data"]
        assert isinstance(data, list)


class TestActivityFilters:
    @pytest.mark.asyncio
    async def test_list(self, nc_mcp: McpTestHelper) -> None:
        filters = json.loads(await nc_mcp.call("list_activity_filters"))
        ids = [f["id"] for f in filters]
        assert {"all", "self", "by", "files"} <= set(ids)
        assert all(f["name"] for f in filters)
        # Every listed filter is accepted by get_activity
        for filter_id in ids:
            json.loads(await nc_mcp.call("get_activity", activity_filter=filter_id, limit=1))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


class TestActivitySearch:
    @pytest.fixture
    async def marker(self, nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
        """A unique name used in two files the admin creates, so searches have known results."""
        marker = f"actsearch{uuid.uuid4().hex[:10]}"
        await nc_mcp.create_test_dir()
        for i in range(2):
            await nc_mcp.upload_test_file(f"mcp-test-suite/{marker}-{i}.txt", "x")
        yield marker
        for i in range(2):
            with contextlib.suppress(Exception):
                await nc_mcp.client.dav_delete(f"mcp-test-suite/{marker}-{i}.txt")

    @pytest.fixture(autouse=True)
    async def supported(self, nc_mcp: McpTestHelper) -> bool:
        return await activity._supports_search(nc_mcp.client)

    @pytest.mark.asyncio
    async def test_search(self, nc_mcp: McpTestHelper, marker: str, supported: bool) -> None:
        if not supported:
            pytest.skip("Activity 8 (Nextcloud 35) needed")
        data = json.loads(await nc_mcp.call("get_activity", search=marker.upper(), limit=50))["data"]
        # Activity may group both uploads into one entry named after one of the files
        names = {a["object_name"] for a in data}
        assert names
        assert names <= {f"/mcp-test-suite/{marker}-0.txt", f"/mcp-test-suite/{marker}-1.txt"}
        assert all(marker in a["subject"] for a in data)
        with pytest.raises(ToolError, match="at least 2 characters"):
            await nc_mcp.call("get_activity", search="x")

    @pytest.mark.asyncio
    async def test_actor(self, nc_mcp: McpTestHelper, marker: str, supported: bool) -> None:
        if not supported:
            pytest.skip("Activity 8 (Nextcloud 35) needed")
        user = nc_mcp.client._config.user
        mine = json.loads(await nc_mcp.call("get_activity", search=marker, actor=user))["data"]
        assert mine
        assert {a["user"] for a in mine} == {user}
        nobody = json.loads(await nc_mcp.call("get_activity", search=marker, actor="no-such-user-xyz"))
        assert nobody["data"] == []

    @pytest.mark.asyncio
    async def test_time_range(self, nc_mcp: McpTestHelper, marker: str, supported: bool) -> None:
        if not supported:
            pytest.skip("Activity 8 (Nextcloud 35) needed")
        now = datetime.now(UTC)
        recent = await nc_mcp.call("get_activity", search=marker, start=_iso(now - timedelta(minutes=10)))
        assert json.loads(recent)["data"]
        day = now.date().isoformat()
        today = await nc_mcp.call("get_activity", search=marker, start=day, end=day)
        assert json.loads(today)["data"]
        future = await nc_mcp.call("get_activity", search=marker, start=_iso(now + timedelta(hours=1)))
        assert json.loads(future)["data"] == []
        past = await nc_mcp.call("get_activity", search=marker, end="2001-01-01")
        assert json.loads(past)["data"] == []

    @pytest.mark.asyncio
    async def test_counts(self, nc_mcp: McpTestHelper, marker: str, supported: bool) -> None:
        if not supported:
            with pytest.raises(ToolError, match="Activity app 8"):
                await nc_mcp.call("get_activity_counts")
            return
        counts = json.loads(await nc_mcp.call("get_activity_counts", days=2, search=marker))
        listed = json.loads(await nc_mcp.call("get_activity", search=marker, limit=200))["data"]
        assert counts["total"] == sum(counts["counts"].values()) >= len(listed) > 0
        assert counts["to"] >= counts["from"]
        assert json.loads(await nc_mcp.call("get_activity_counts", days=2, actor="no-such-user-xyz"))["total"] == 0
        file_id = listed[0]["object_id"]
        of_file = json.loads(await nc_mcp.call("get_activity_counts", days=2, object_type="files", object_id=file_id))
        of_listing = json.loads(await nc_mcp.call("get_activity", object_type="files", object_id=file_id))["data"]
        assert of_file["total"] >= len(of_listing) > 0
        assert of_file["total"] < json.loads(await nc_mcp.call("get_activity_counts", days=2))["total"]
        with pytest.raises(ToolError, match="Unknown activity_filter"):
            await nc_mcp.call("get_activity_counts", activity_filter="nonexistent")

    @pytest.mark.asyncio
    async def test_refused_on_older_servers(self, nc_mcp: McpTestHelper, supported: bool) -> None:
        if supported:
            pytest.skip("this server supports search")
        for kwargs in ({"search": "report"}, {"actor": "admin"}, {"start": "2026-01-01"}):
            with pytest.raises(ToolError, match="would ignore them"):
                await nc_mcp.call("get_activity", **kwargs)

    @pytest.mark.asyncio
    async def test_start_after_end(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="start must not be after end"):
            await nc_mcp.call("get_activity", start="2026-09-20", end="2026-09-19")


class TestActivityCountsPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_filters_and_counts(self, nc_mcp_read_only: McpTestHelper) -> None:
        assert json.loads(await nc_mcp_read_only.call("list_activity_filters"))
        if await activity._supports_search(nc_mcp_read_only.client):
            assert "total" in json.loads(await nc_mcp_read_only.call("get_activity_counts", days=1))
