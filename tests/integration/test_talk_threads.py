"""Integration tests for Talk thread support against a real Nextcloud instance.

Skipped when Talk on the target instance does not advertise the "threads" capability.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Any

import niquests
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_server
from nc_mcp_server.state import get_client

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration

THREAD_PEER_USER = "mcp-thread-peer"
THREAD_PEER_PWD = "mcp-Thread-Peer-PWD-7Q!"
MISSING_THREAD_ID = 999999999


@pytest.fixture(scope="session")
def _threads_available(_cleanup_config: Config) -> bool:
    """Probe whether Talk on the target Nextcloud advertises the "threads" feature."""
    try:
        resp = niquests.get(
            f"{_cleanup_config.nextcloud_url}/ocs/v2.php/cloud/capabilities",
            auth=(_cleanup_config.user, _cleanup_config.password),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=5,
        )
    except (OSError, niquests.exceptions.RequestException):
        return False
    if not resp.ok:
        return False
    body: dict[str, Any] = resp.json()
    spreed: dict[str, Any] = body["ocs"]["data"]["capabilities"].get("spreed") or {}
    return "threads" in spreed.get("features", [])


@pytest.fixture(autouse=True)
def _skip_if_no_threads(_threads_available: bool) -> None:
    if not _threads_available:
        pytest.skip("Talk on this Nextcloud instance does not support threads")


@pytest.fixture
async def room(nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
    """A fresh group conversation, deleted after the test."""
    result = json.loads(await nc_mcp.call("create_conversation", room_type=2, name="mcp-test-threads"))
    token = str(result["token"])
    yield token
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{token}")


async def _send(nc_mcp: McpTestHelper, token: str, message: str, **kwargs: Any) -> dict[str, Any]:
    return json.loads(await nc_mcp.call("send_message", token=token, message=message, **kwargs))


async def _start_thread(nc_mcp: McpTestHelper, token: str, title: str, message: str = "root message") -> int:
    return int((await _send(nc_mcp, token, message, thread_title=title))["id"])


def _message_lines(result: str) -> list[str]:
    return [line for line in result.strip().split("\n") if line and not line.startswith("---")]


def _line_ids(result: str) -> list[int]:
    return [int(line[1 : line.index("]")]) for line in _message_lines(result)]


class TestSendMessageThreads:
    @pytest.mark.asyncio
    async def test_thread_title_starts_thread(self, nc_mcp: McpTestHelper, room: str) -> None:
        data = await _send(nc_mcp, room, "kick-off", thread_title="  Release plan  ")
        assert data["thread_id"] == data["id"]
        assert data["thread_title"] == "Release plan"
        assert data["message"] == "kick-off"

    @pytest.mark.asyncio
    async def test_thread_id_posts_into_thread(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        data = await _send(nc_mcp, room, "in thread", thread_id=root_id)
        assert data["id"] != root_id
        assert data["thread_id"] == root_id
        assert data["thread_title"] == "Plan"

    @pytest.mark.asyncio
    async def test_reply_inside_thread_lands_in_thread(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        first = await _send(nc_mcp, room, "first reply", thread_id=root_id)
        data = await _send(nc_mcp, room, "quoted reply", reply_to=int(first["id"]))
        assert data["thread_id"] == root_id

    @pytest.mark.asyncio
    async def test_plain_message_and_plain_reply_have_no_thread(self, nc_mcp: McpTestHelper, room: str) -> None:
        plain = await _send(nc_mcp, room, "outside")
        reply = await _send(nc_mcp, room, "plain reply", reply_to=int(plain["id"]))
        for data in (plain, reply):
            assert data["thread_id"] == 0
            assert "thread_title" not in data

    @pytest.mark.asyncio
    async def test_unknown_thread_raises(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match=f"Thread {MISSING_THREAD_ID} not found in conversation {room}"):
            await _send(nc_mcp, room, "lost", thread_id=MISSING_THREAD_ID)

    @pytest.mark.asyncio
    async def test_thread_title_with_reply_to_raises(self, nc_mcp: McpTestHelper, room: str) -> None:
        plain = await _send(nc_mcp, room, "outside")
        with pytest.raises(ToolError, match="cannot be combined with reply_to or thread_id"):
            await _send(nc_mcp, room, "nope", reply_to=int(plain["id"]), thread_title="Plan")


class TestGetMessagesThreads:
    @pytest.mark.asyncio
    async def test_thread_messages_are_marked(self, nc_mcp: McpTestHelper, room: str) -> None:
        root = await _send(nc_mcp, room, "root message", thread_title="Plan")
        reply = await _send(nc_mcp, room, "in thread", thread_id=int(root["id"]))
        plain = await _send(nc_mcp, room, "outside")
        author = root["actor_display_name"]
        lines = _message_lines(await nc_mcp.call("get_messages", token=room))
        assert lines == [
            f"[{plain['id']}] {author}: outside",
            f"[{reply['id']}] {author} [thread {root['id']}]: in thread",
            f'[{root["id"]}] {author} [thread {root["id"]} "Plan"]: root message',
        ]

    @pytest.mark.asyncio
    async def test_thread_filter_returns_only_thread_messages(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        reply = await _send(nc_mcp, room, "in thread", thread_id=root_id)
        await _send(nc_mcp, room, "outside")
        await _start_thread(nc_mcp, room, "Other thread")
        result = await nc_mcp.call("get_messages", token=room, thread_id=root_id)
        assert _line_ids(result) == [reply["id"], root_id]
        assert f"thread_id={root_id}" in result

    @pytest.mark.asyncio
    async def test_thread_filter_with_system_messages(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        result = await nc_mcp.call("get_messages", token=room, thread_id=root_id, include_system=True)
        assert len(_message_lines(result)) == 2
        assert "created thread" in result

    @pytest.mark.asyncio
    async def test_thread_filter_pagination(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        reply_ids = [int((await _send(nc_mcp, room, f"reply {i}", thread_id=root_id))["id"]) for i in range(4)]
        await _send(nc_mcp, room, "outside")
        first_page = _line_ids(await nc_mcp.call("get_messages", token=room, thread_id=root_id, limit=2))
        assert first_page == [reply_ids[3], reply_ids[2]]
        second = await nc_mcp.call(
            "get_messages", token=room, thread_id=root_id, limit=10, before_message_id=min(first_page)
        )
        assert _line_ids(second) == [reply_ids[1], reply_ids[0], root_id]

    @pytest.mark.asyncio
    async def test_unknown_thread_filter_raises(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match=f"Thread {MISSING_THREAD_ID} not found"):
            await nc_mcp.call("get_messages", token=room, thread_id=MISSING_THREAD_ID)


class TestListThreads:
    @pytest.mark.asyncio
    async def test_lists_threads_with_summary(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        await _send(nc_mcp, room, "reply one", thread_id=root_id)
        last = await _send(nc_mcp, room, "reply two", thread_id=root_id)
        other_id = await _start_thread(nc_mcp, room, "Other thread", message="other root")
        result = json.loads(await nc_mcp.call("list_threads", token=room))
        threads = {t["thread_id"]: t for t in result["data"]}
        assert set(threads) == {root_id, other_id}
        plan = threads[root_id]
        assert plan["token"] == room
        assert plan["title"] == "Plan"
        assert plan["num_replies"] == 2
        assert plan["last_activity"] > 0
        assert plan["notification_level"] == "default"
        assert plan["first"].endswith(f'[thread {root_id} "Plan"]: root message')
        assert plan["last"].startswith(f"[{last['id']}] ")
        assert plan["last"].endswith(f"[thread {root_id}]: reply two")
        other = threads[other_id]
        assert other["num_replies"] == 0
        assert other["last"] is None
        assert result["pagination"] == {"count": 2, "limit": 50, "has_more": False}

    @pytest.mark.asyncio
    async def test_limit(self, nc_mcp: McpTestHelper, room: str) -> None:
        for i in range(3):
            await _start_thread(nc_mcp, room, f"Thread {i}")
        result = json.loads(await nc_mcp.call("list_threads", token=room, limit=2))
        assert len(result["data"]) == 2
        assert result["pagination"]["has_more"] is True

    @pytest.mark.asyncio
    async def test_conversation_without_threads(self, nc_mcp: McpTestHelper, room: str) -> None:
        await _send(nc_mcp, room, "no threads here")
        result = json.loads(await nc_mcp.call("list_threads", token=room))
        assert result["data"] == []
        assert result["pagination"]["has_more"] is False


class TestGetThread:
    @pytest.mark.asyncio
    async def test_returns_thread(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        await _send(nc_mcp, room, "reply", thread_id=root_id)
        data = json.loads(await nc_mcp.call("get_thread", token=room, thread_id=root_id))
        assert data["thread_id"] == root_id
        assert data["token"] == room
        assert data["title"] == "Plan"
        assert data["num_replies"] == 1
        assert data["notification_level"] == "default"
        assert data["first"].endswith(": root message")
        assert data["last"].endswith(f"[thread {root_id}]: reply")

    @pytest.mark.asyncio
    async def test_reply_id_is_not_a_thread_id(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        reply = await _send(nc_mcp, room, "reply", thread_id=root_id)
        with pytest.raises(ToolError, match=f"Thread {reply['id']} not found"):
            await nc_mcp.call("get_thread", token=room, thread_id=int(reply["id"]))

    @pytest.mark.asyncio
    async def test_unknown_thread_raises(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match=f"Thread {MISSING_THREAD_ID} not found in conversation {room}"):
            await nc_mcp.call("get_thread", token=room, thread_id=MISSING_THREAD_ID)


class TestListSubscribedThreads:
    @staticmethod
    async def _subscribed(nc_mcp: McpTestHelper, room: str) -> dict[int, dict[str, Any]]:
        result = json.loads(await nc_mcp.call("list_subscribed_threads"))
        return {t["thread_id"]: t for t in result["data"] if t["token"] == room}

    @pytest.mark.asyncio
    async def test_started_thread_is_listed(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        subscribed = await self._subscribed(nc_mcp, room)
        assert subscribed[root_id]["title"] == "Plan"
        assert subscribed[root_id]["first"].endswith(": root message")

    @pytest.mark.asyncio
    async def test_never_level_hides_thread(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        await nc_mcp.call("set_thread_notification_level", token=room, thread_id=root_id, level="never")
        assert root_id not in await self._subscribed(nc_mcp, room)
        await nc_mcp.call("set_thread_notification_level", token=room, thread_id=root_id, level="always")
        assert (await self._subscribed(nc_mcp, room))[root_id]["notification_level"] == "always"

    @pytest.mark.asyncio
    async def test_pagination(self, nc_mcp: McpTestHelper, room: str) -> None:
        await _start_thread(nc_mcp, room, "First")
        # Talk sorts by last activity (seconds) with no tie-breaker, so keep the two threads apart
        await asyncio.sleep(1.1)
        await _start_thread(nc_mcp, room, "Second")
        page1 = json.loads(await nc_mcp.call("list_subscribed_threads", limit=1, offset=0))
        page2 = json.loads(await nc_mcp.call("list_subscribed_threads", limit=1, offset=1))
        assert page1["pagination"] == {"count": 1, "offset": 0, "limit": 1, "has_more": True}
        assert page2["pagination"]["offset"] == 1
        assert page1["data"][0]["thread_id"] != page2["data"][0]["thread_id"]


class TestRenameThread:
    @pytest.mark.asyncio
    async def test_rename_updates_title(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        data = json.loads(await nc_mcp.call("rename_thread", token=room, thread_id=root_id, title="  Final plan "))
        assert data["thread_id"] == root_id
        assert data["title"] == "Final plan"
        fetched = json.loads(await nc_mcp.call("get_thread", token=room, thread_id=root_id))
        assert fetched["title"] == "Final plan"
        messages = await nc_mcp.call("get_messages", token=room)
        assert f'[thread {root_id} "Final plan"]: root message' in messages

    @pytest.mark.asyncio
    async def test_blank_title_raises(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        with pytest.raises(ToolError, match="title must not be blank"):
            await nc_mcp.call("rename_thread", token=room, thread_id=root_id, title="   ")

    @pytest.mark.asyncio
    async def test_unknown_thread_raises(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match=f"Thread {MISSING_THREAD_ID} not found"):
            await nc_mcp.call("rename_thread", token=room, thread_id=MISSING_THREAD_ID, title="x")

    @pytest.mark.asyncio
    async def test_other_participant_cannot_rename(self, nc_mcp: McpTestHelper, room: str, nc_config: Config) -> None:
        admin_client = nc_mcp.client
        with contextlib.suppress(Exception):
            await admin_client.ocs_post("cloud/users", data={"userid": THREAD_PEER_USER, "password": THREAD_PEER_PWD})
        await admin_client.ocs_post(
            f"apps/spreed/api/v4/room/{room}/participants",
            data={"newParticipant": THREAD_PEER_USER, "source": "users"},
        )
        root_id = await _start_thread(nc_mcp, room, "Plan")
        peer_config = Config(
            nextcloud_url=nc_config.nextcloud_url,
            user=THREAD_PEER_USER,
            password=THREAD_PEER_PWD,
            permission_level=PermissionLevel.DESTRUCTIVE,
        )
        peer = McpTestHelper(create_server(peer_config), get_client())
        try:
            with pytest.raises(ToolError, match=f"Only the author of the first message of thread {root_id}"):
                await peer.call("rename_thread", token=room, thread_id=root_id, title="Hijacked")
        finally:
            await peer.client.close()
            with contextlib.suppress(Exception):
                await admin_client.ocs_delete(f"cloud/users/{THREAD_PEER_USER}")


class TestSetThreadNotificationLevel:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("level", ["always", "mention", "never", "default"])
    async def test_set_level(self, nc_mcp: McpTestHelper, room: str, level: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        data = json.loads(
            await nc_mcp.call("set_thread_notification_level", token=room, thread_id=root_id, level=level)
        )
        assert data["thread_id"] == root_id
        assert data["notification_level"] == level
        fetched = json.loads(await nc_mcp.call("get_thread", token=room, thread_id=root_id))
        assert fetched["notification_level"] == level

    @pytest.mark.asyncio
    async def test_invalid_level_raises(self, nc_mcp: McpTestHelper, room: str) -> None:
        root_id = await _start_thread(nc_mcp, room, "Plan")
        with pytest.raises(ToolError, match="Invalid level 'loud'"):
            await nc_mcp.call("set_thread_notification_level", token=room, thread_id=root_id, level="loud")

    @pytest.mark.asyncio
    async def test_unknown_thread_raises(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match=f"Thread {MISSING_THREAD_ID} not found"):
            await nc_mcp.call("set_thread_notification_level", token=room, thread_id=MISSING_THREAD_ID, level="always")


class TestThreadPermissions:
    @pytest.mark.asyncio
    async def test_read_only_allows_list_subscribed_threads(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = json.loads(await nc_mcp_read_only.call("list_subscribed_threads", limit=1))
        assert isinstance(result["data"], list)

    @pytest.mark.asyncio
    async def test_read_only_blocks_rename_thread(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("rename_thread", token="x", thread_id=1, title="blocked")

    @pytest.mark.asyncio
    async def test_read_only_blocks_set_thread_notification_level(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"requires 'write' permission"):
            await nc_mcp_read_only.call("set_thread_notification_level", token="x", thread_id=1, level="never")
