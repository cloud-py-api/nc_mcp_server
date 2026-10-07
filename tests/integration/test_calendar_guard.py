"""Calendar against a real Nextcloud: time zones, video links, the creator guard and shared calendars.

A calendar shared with another account shows up there as ``<uri>_shared_by_<owner>``; events
written there carry the writer's mark, and update_event refuses other people's events unless
allow_foreign=true.
"""

import contextlib
import json
import secrets
import uuid
from collections.abc import AsyncGenerator

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_server
from nc_mcp_server.state import get_client

from .conftest import McpTestHelper, _get_integration_config

pytestmark = pytest.mark.integration

CAL_ID = "personal"
VIDEO = "https://cloud.example.org/index.php/call/mcptest1"


def _server(config: Config) -> McpTestHelper:
    mcp = create_server(config)
    return McpTestHelper(mcp, get_client())


async def _delete_quietly(helper: McpTestHelper, calendar_id: str, uid: str) -> None:
    with contextlib.suppress(ToolError):
        await helper.call("delete_event", calendar_id=calendar_id, event_uid=uid)


class TestTimezone:
    @pytest.mark.asyncio
    async def test_wall_time_in_zone(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_event",
                calendar_id=CAL_ID,
                summary="mcp-test-tz",
                start="2027-03-20T19:00:00",
                end="2027-03-20T21:00:00",
                timezone="Europe/Berlin",
                rrule="FREQ=WEEKLY;UNTIL=20270410",
            )
        )
        try:
            assert created["dtstart"] == "2027-03-20T19:00:00+01:00"
            event = json.loads(await nc_mcp.call("get_event", calendar_id=CAL_ID, event_uid=created["uid"]))
            assert event["dtstart"] == "2027-03-20T19:00:00+01:00"
            assert event["created_by_me"] is True
            assert "UNTIL=20270410T215959Z" in event["rrule"]
            # Range given with an offset finds it (18:00 UTC is inside 19:00-21:00 CET).
            found = json.loads(
                await nc_mcp.call(
                    "get_events", calendar_id=CAL_ID, start="2027-03-20T19:30:00+01:00", end="2027-03-20T19:45:00+01:00"
                )
            )["data"]
            assert created["uid"] in [e["uid"] for e in found]
            # A later move keeps the event's zone; summer time gives +02:00.
            await nc_mcp.call("update_event", calendar_id=CAL_ID, event_uid=created["uid"], start="2027-04-03T19:00:00")
            moved = json.loads(await nc_mcp.call("get_event", calendar_id=CAL_ID, event_uid=created["uid"]))
            assert moved["dtstart"] == "2027-04-03T19:00:00+02:00"
        finally:
            await _delete_quietly(nc_mcp, CAL_ID, created["uid"])

    @pytest.mark.asyncio
    async def test_bad_rrule_creates_nothing(self, nc_mcp: McpTestHelper) -> None:
        before = json.loads(await nc_mcp.call("get_events", calendar_id=CAL_ID, limit=500))["pagination"]["count"]
        with pytest.raises((ToolError, ValueError)):
            await nc_mcp.call(
                "create_event",
                calendar_id=CAL_ID,
                summary="mcp-test-bad",
                start="2027-01-01T10:00:00Z",
                rrule="FREQ=MINUTELY",
            )
        after = json.loads(await nc_mcp.call("get_events", calendar_id=CAL_ID, limit=500))["pagination"]["count"]
        assert after == before


class TestVideoLink:
    @pytest.mark.asyncio
    async def test_link_set_replaced_removed(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call(
                "create_event",
                calendar_id=CAL_ID,
                summary="mcp-test-video",
                start="2027-02-01T18:00:00Z",
                conference_url=VIDEO,
            )
        )
        try:
            event = json.loads(await nc_mcp.call("get_event", calendar_id=CAL_ID, event_uid=created["uid"]))
            assert event["conference"] == VIDEO
            assert event["location"] == VIDEO
            await nc_mcp.call("update_event", calendar_id=CAL_ID, event_uid=created["uid"], conference_url="")
            event = json.loads(await nc_mcp.call("get_event", calendar_id=CAL_ID, event_uid=created["uid"]))
            assert "conference" not in event
            assert event["location"] == ""
        finally:
            await _delete_quietly(nc_mcp, CAL_ID, created["uid"])


class TestGuard:
    @pytest.mark.asyncio
    async def test_event_without_mark_needs_allow_foreign(self, nc_mcp: McpTestHelper) -> None:
        uid = f"mcp-test-foreign-{uuid.uuid4()}"
        ical = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20270301T090000Z\r\nDTEND:20270301T100000Z\r\n"
            "SUMMARY:mcp-test-foreign\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        user = nc_mcp.client._config.user
        await nc_mcp.client.dav_request(
            "PUT",
            f"calendars/{user}/{CAL_ID}/{uid}.ics",
            body=ical,
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        try:
            with pytest.raises(ToolError, match="allow_foreign"):
                await nc_mcp.call("update_event", calendar_id=CAL_ID, event_uid=uid, summary="mcp-test-changed")
            event = json.loads(await nc_mcp.call("get_event", calendar_id=CAL_ID, event_uid=uid))
            assert event["summary"] == "mcp-test-foreign"
            assert event["created_by_me"] is False
            await nc_mcp.call(
                "update_event", calendar_id=CAL_ID, event_uid=uid, summary="mcp-test-changed", allow_foreign=True
            )
            event = json.loads(await nc_mcp.call("get_event", calendar_id=CAL_ID, event_uid=uid))
            assert event["summary"] == "mcp-test-changed"
        finally:
            await _delete_quietly(nc_mcp, CAL_ID, uid)

    @pytest.mark.asyncio
    async def test_expected_summary_protects_delete(self, nc_mcp: McpTestHelper) -> None:
        created = json.loads(
            await nc_mcp.call("create_event", calendar_id=CAL_ID, summary="mcp-test-keep", start="2027-02-02T18:00:00Z")
        )
        try:
            with pytest.raises(ToolError, match="Nothing was changed"):
                await nc_mcp.call(
                    "delete_event", calendar_id=CAL_ID, event_uid=created["uid"], expected_summary="other"
                )
            json.loads(await nc_mcp.call("get_event", calendar_id=CAL_ID, event_uid=created["uid"]))
            await nc_mcp.call(
                "delete_event", calendar_id=CAL_ID, event_uid=created["uid"], expected_summary="MCP-test-keep"
            )
            with pytest.raises(ToolError):
                await nc_mcp.call("get_event", calendar_id=CAL_ID, event_uid=created["uid"])
        finally:
            await _delete_quietly(nc_mcp, CAL_ID, created["uid"])


@pytest.fixture
async def shared_calendar() -> AsyncGenerator[tuple[Config, Config, Config, str]]:
    """admin owns a calendar shared read-write with a new member account and read-only with another."""
    admin_config = _get_integration_config()
    admin = NextcloudClient(admin_config)
    suffix = secrets.token_hex(4)
    uri = f"mcp-test-shared-{suffix}"
    members: list[Config] = []
    try:
        for name in ("mcp-writer", "mcp-reader"):
            user_id = f"{name}-{suffix}"
            password = f"Mcp-{secrets.token_hex(10)}!"
            await admin.ocs_post("cloud/users", data={"userid": user_id, "password": password})
            members.append(
                Config(
                    nextcloud_url=admin_config.nextcloud_url,
                    user=user_id,
                    password=password,
                    permission_level=PermissionLevel.DESTRUCTIVE,
                )
            )
        await admin.dav_request(
            "MKCALENDAR",
            f"calendars/{admin_config.user}/{uri}",
            body=(
                '<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                "<d:set><d:prop><d:displayname>Shared events test</d:displayname></d:prop></d:set></c:mkcalendar>"
            ),
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        for member, right in ((members[0], "<o:read-write/>"), (members[1], "")):
            await admin.dav_request(
                "POST",
                f"calendars/{admin_config.user}/{uri}",
                body=(
                    '<?xml version="1.0"?><o:share xmlns:d="DAV:" xmlns:o="http://owncloud.org/ns"><o:set>'
                    f"<d:href>principal:principals/users/{member.user}</d:href>{right}</o:set></o:share>"
                ),
                headers={"Content-Type": "application/xml; charset=utf-8"},
            )
        yield admin_config, members[0], members[1], uri
    finally:
        with contextlib.suppress(Exception):
            await admin.dav_request("DELETE", f"calendars/{admin_config.user}/{uri}")
        for member in members:
            with contextlib.suppress(Exception):
                await admin.ocs_delete(f"cloud/users/{member.user}")
        await admin.close()


class TestSharedCalendar:
    @pytest.mark.asyncio
    async def test_member_writes_owner_needs_allow_foreign(
        self, shared_calendar: tuple[Config, Config, Config, str]
    ) -> None:
        admin_config, writer_config, reader_config, uri = shared_calendar
        shared_id = f"{uri}_shared_by_{admin_config.user}"

        member = _server(writer_config)
        try:
            calendars = {c["id"]: c for c in json.loads(await member.call("list_calendars"))}
            assert shared_id in calendars
            assert calendars[shared_id]["writable"] is True
            created = json.loads(
                await member.call(
                    "create_event",
                    calendar_id=shared_id,
                    summary="mcp-test-meetup",
                    start="2027-05-04T19:00:00",
                    timezone="Europe/Berlin",
                )
            )
            await member.call("update_event", calendar_id=shared_id, event_uid=created["uid"], description="New")
        finally:
            await member.client.close()

        owner = _server(admin_config)
        try:
            event = json.loads(await owner.call("get_event", calendar_id=uri, event_uid=created["uid"]))
            assert event["created_by_me"] is False
            with pytest.raises(ToolError, match="allow_foreign"):
                await owner.call("update_event", calendar_id=uri, event_uid=created["uid"], summary="x")
        finally:
            await owner.client.close()

        reader = _server(reader_config)
        try:
            calendars = {c["id"]: c for c in json.loads(await reader.call("list_calendars"))}
            assert calendars[shared_id]["writable"] is False
            with pytest.raises(ToolError):
                await reader.call(
                    "create_event", calendar_id=shared_id, summary="mcp-test-nope", start="2027-05-05T19:00:00Z"
                )
        finally:
            await reader.client.close()
