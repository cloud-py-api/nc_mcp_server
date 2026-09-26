"""Unit tests for the activity feed's search parameters, filters and daily counts."""

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import activity

API = "apps/activity/api/v2/activity"
HISTOGRAM_PROBE = call(f"{API}/all/histogram", params={"days": "1"})
ACTIVITY = {"activity_id": 7, "app": "files", "type": "file_created", "user": "alice", "object_name": "/a.txt"}
NEXT = {"X-Activity-Last-Given": "5", "Link": '<https://cloud.example/next>; rel="next"'}
NO_ROUTE = NextcloudError("Invalid query, please check the syntax.", 404)


@pytest.fixture(autouse=True)
def _forget_servers() -> Iterator[None]:
    activity._SEARCH_SUPPORT.clear()
    yield
    activity._SEARCH_SUPPORT.clear()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.READ)
    mock = MagicMock()
    mock.base_url = "https://cloud.example"
    mock.ocs_get = AsyncMock(return_value={"counts": []})
    mock.ocs_get_with_headers = AsyncMock(return_value=([ACTIVITY], NEXT))
    monkeypatch.setattr(activity, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-activity")
    activity.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


class TestTimestamps:
    def test_date_is_a_utc_day(self) -> None:
        assert activity._timestamp("2026-09-20", "start") == 1789862400
        assert activity._timestamp("2026-09-20", "end", end_of_day=True) == 1789862400 + 86399

    def test_time_with_zone(self) -> None:
        assert activity._timestamp("2026-09-20T02:00:00+02:00", "start") == 1789862400

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("2026-09-20T02:00:00", "needs a time zone"),
            ("yesterday", "must be an ISO 8601"),
            ("2026-13-01", "must be an ISO 8601"),
            ("1970-01-01", "after 1970"),
        ],
    )
    def test_invalid(self, value: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            activity._timestamp(value, "start")


class TestFeed:
    async def test_plain_request_does_not_probe(self, mcp: FastMCP, client: MagicMock) -> None:
        result = json.loads(await _call(mcp, "get_activity", limit=5, object_type="files", object_id=9))
        # Activity only narrows the feed to an object in its "filter" stream
        client.ocs_get_with_headers.assert_awaited_once_with(
            f"{API}/filter", params={"limit": "5", "sort": "desc", "object_type": "files", "object_id": "9"}
        )
        client.ocs_get.assert_not_awaited()
        assert result["pagination"] == {"count": 1, "has_more": True, "since": 5}

    async def test_last_page(self, mcp: FastMCP, client: MagicMock) -> None:
        """A short page is not the end by itself: grouping shortens pages. The missing next link is."""
        client.ocs_get_with_headers.return_value = ([ACTIVITY], {"X-Activity-Last-Given": "7"})
        result = json.loads(await _call(mcp, "get_activity", limit=1))
        assert result["pagination"] == {"count": 1, "has_more": False, "since": 7}

    async def test_object_with_another_filter(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="cannot be combined with an activity_filter"):
            await _call(mcp, "get_activity", activity_filter="files", object_type="files", object_id=9)
        client.ocs_get_with_headers.assert_not_awaited()

    async def test_empty_page_with_a_next_one(self, mcp: FastMCP, client: MagicMock) -> None:
        """Activity drops entries it cannot show, so a page can be empty and still have a successor."""
        client.ocs_get_with_headers.return_value = (None, {"X-Activity-Last-Given": "40", "Link": "<u>; rel=next"})
        result = json.loads(await _call(mcp, "get_activity", since=50))
        assert result["pagination"] == {"count": 0, "has_more": True, "since": 40}

    async def test_search_parameters(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(
            mcp,
            "get_activity",
            activity_filter="files",
            search="report",
            start="2026-09-20",
            end="2026-09-20",
            actor="alice",
        )
        assert client.ocs_get.await_args_list == [HISTOGRAM_PROBE]
        client.ocs_get_with_headers.assert_awaited_once_with(
            f"{API}/files",
            params={
                "limit": "30",
                "sort": "desc",
                "search": "report",
                "from": "1789862400",
                "to": str(1789862400 + 86399),
                "actor": "alice",
            },
        )

    async def test_support_is_remembered(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "get_activity", search="report")
        await _call(mcp, "get_activity", actor="alice")
        assert client.ocs_get.await_args_list == [HISTOGRAM_PROBE]

    async def test_refused_when_the_app_would_ignore_them(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = [NO_ROUTE, [], NO_ROUTE, []]
        for kwargs in ({"search": "report"}, {"start": "2026-09-20"}):
            with pytest.raises(ToolError, match="would ignore them"):
                await _call(mcp, "get_activity", **kwargs)
        # Missing support is checked again each time: the server may be upgraded meanwhile
        assert client.ocs_get.await_args_list == [HISTOGRAM_PROBE, call(f"{API}/filters")] * 2
        client.ocs_get_with_headers.assert_not_awaited()

    async def test_app_not_available(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = NO_ROUTE
        with pytest.raises(ToolError, match="Activity app is not available"):
            await _call(mcp, "get_activity", search="report")
        client.ocs_get_with_headers.side_effect = NO_ROUTE
        with pytest.raises(ToolError, match="Activity app is not available"):
            await _call(mcp, "get_activity")

    async def test_probe_errors_are_raised(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = NextcloudError("Server error", 500)
        with pytest.raises(ToolError, match="Server error"):
            await _call(mcp, "get_activity", search="report")
        assert not activity._SEARCH_SUPPORT

    async def test_start_after_end(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="start must not be after end"):
            await _call(mcp, "get_activity", start="2026-09-21", end="2026-09-20")
        client.ocs_get.assert_not_awaited()

    async def test_nothing_left_is_an_empty_page(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get_with_headers.return_value = (None, {})
        result = json.loads(await _call(mcp, "get_activity", since=3))
        assert result == {"data": [], "pagination": {"count": 0, "has_more": False, "since": None}}

    async def test_unknown_filter(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get_with_headers.side_effect = NextcloudError("OCS GET x: Not found", 404)
        with pytest.raises(ToolError, match=r"Unknown activity_filter 'nope'\. list_activity_filters"):
            await _call(mcp, "get_activity", activity_filter="nope")

    async def test_other_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get_with_headers.side_effect = NextcloudError("Search term must be at least 2", 400)
        with pytest.raises(ToolError, match="at least 2"):
            await _call(mcp, "get_activity", search="x")

    @pytest.mark.parametrize(("object_type", "object_id"), [("files", 0), ("", 5)])
    async def test_object_needs_both(self, mcp: FastMCP, object_type: str, object_id: int) -> None:
        with pytest.raises(ToolError, match="must be given together"):
            await _call(mcp, "get_activity", object_type=object_type, object_id=object_id)


class TestFiltersAndCounts:
    async def test_list_filters(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [{"id": "all", "name": "All activities", "icon": "x", "priority": 0}]
        assert json.loads(await _call(mcp, "list_activity_filters")) == [{"id": "all", "name": "All activities"}]
        client.ocs_get.assert_awaited_once_with(f"{API}/filters")

    async def test_counts(self, mcp: FastMCP, client: MagicMock) -> None:
        histogram = {
            "from": "2026-09-19",
            "to": "2026-09-20",
            "counts": {"2026-09-20": 3},
            "max": 3,
            "total": 3,
            "partial_before": None,
        }
        client.ocs_get.return_value = histogram
        result = json.loads(
            await _call(
                mcp, "get_activity_counts", days=500, search="report", actor="bob", object_type="files", object_id=4
            )
        )
        client.ocs_get.assert_awaited_once_with(
            f"{API}/filter/histogram",
            params={"days": "366", "object_type": "files", "object_id": "4", "search": "report", "actor": "bob"},
        )
        assert result == histogram

    async def test_counts_of_an_empty_window(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"from": "a", "to": "b", "counts": [], "max": 0, "total": 0}
        result = json.loads(await _call(mcp, "get_activity_counts", days=0))
        assert client.ocs_get.await_args.kwargs["params"]["days"] == "1"
        assert (result["counts"], result["total"], result["partial_before"]) == ({}, 0, None)

    async def test_counts_on_older_servers(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = [NO_ROUTE, []]
        with pytest.raises(ToolError, match="need the Activity app 8"):
            await _call(mcp, "get_activity_counts")

    async def test_counts_without_the_app(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = NO_ROUTE
        with pytest.raises(ToolError, match="Activity app is not available"):
            await _call(mcp, "get_activity_counts")

    async def test_counts_unknown_filter(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = NextcloudError("", 404)
        with pytest.raises(ToolError, match="Unknown activity_filter 'nope'"):
            await _call(mcp, "get_activity_counts", activity_filter="nope")
