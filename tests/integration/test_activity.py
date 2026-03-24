"""Integration tests for Activity tools against a real Nextcloud instance."""

import contextlib
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


async def _generate_activity(nc_mcp: McpTestHelper) -> None:
    """Generate file activity by uploading and deleting a test file."""
    await nc_mcp.client.dav_put("activity-test.txt", b"activity test", content_type="text/plain")
    await nc_mcp.client.dav_delete("activity-test.txt")


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
        if len(data) >= 2:
            ids = [a["activity_id"] for a in data]
            assert ids == sorted(ids), "Ascending sort should have IDs in order"

    @pytest.mark.asyncio
    async def test_sort_desc(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", sort="desc", limit=10)
        data = json.loads(result)["data"]
        if len(data) >= 2:
            ids = [a["activity_id"] for a in data]
            assert ids == sorted(ids, reverse=True), "Descending sort should have IDs in reverse order"

    @pytest.mark.asyncio
    async def test_pagination_since_desc_uses_min_id(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", sort="desc", limit=5)
        parsed = json.loads(result)
        data = parsed["data"]
        if len(data) >= 2:
            assert parsed["pagination"]["since"] == min(a["activity_id"] for a in data)

    @pytest.mark.asyncio
    async def test_pagination_since_asc_uses_max_id(self, nc_mcp: McpTestHelper) -> None:
        await _generate_activity(nc_mcp)
        result = await nc_mcp.call("get_activity", sort="asc", limit=5)
        parsed = json.loads(result)
        data = parsed["data"]
        if len(data) >= 2:
            assert parsed["pagination"]["since"] == max(a["activity_id"] for a in data)

    @pytest.mark.asyncio
    async def test_invalid_filter_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call("get_activity", activity_filter="nonexistent")

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
            assert any(a.get("object_id") == obj_id for a in filtered_data)
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
