"""Integration tests for the Flow tools against a real Nextcloud instance.

User flows use Talk's "Write to conversation" operation, the one CI has installed; global flows use
core's "Block file versioning", which every supported Nextcloud ships. Every flow is named mcp-test-*
so the sweep around each test here removes any a failing test leaves behind; these are the only tests
that create flows, so the rest of the suite skips it.
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import TEST_BASE_DIR, McpTestHelper, cleanup_flows

pytestmark = pytest.mark.integration

TALK_OPERATION = "OCA\\Talk\\Flow\\Operation"
BLOCK_VERSIONING = "OCA\\Files_Versions\\BlockVersioningOperation"
FILE_ENTITY = "OCA\\WorkflowEngine\\Entity\\File"
FILE_NAME = "OCA\\WorkflowEngine\\Check\\FileName"
FILE_SIZE = "OCA\\WorkflowEngine\\Check\\FileSize"
POST_CREATE = "\\OCP\\Files::postCreate"
POST_WRITE = "\\OCP\\Files::postWrite"


@pytest.fixture(autouse=True)
async def _sweep_flows(nc_mcp: McpTestHelper) -> AsyncGenerator[None]:
    await cleanup_flows(nc_mcp.client)
    yield
    await cleanup_flows(nc_mcp.client)


@pytest.fixture
async def room(nc_mcp: McpTestHelper) -> AsyncGenerator[str]:
    data = await nc_mcp.client.ocs_post("apps/spreed/api/v4/room", data={"roomType": 2, "roomName": "mcp-test-flow"})
    token = str(data["token"])
    yield token
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"apps/spreed/api/v4/room/{token}")


@pytest.fixture
async def flows(nc_mcp: McpTestHelper) -> AsyncGenerator[list[tuple[str, int]]]:
    """Collects (scope, id) of flows a test creates and deletes them afterwards."""
    created: list[tuple[str, int]] = []
    yield created
    for scope, flow_id in created:
        with contextlib.suppress(Exception):
            await nc_mcp.client.ocs_delete(f"apps/workflowengine/api/v1/workflows/{scope}/{flow_id}")


def _talk_config(token: str, mode: int = 1) -> str:
    return json.dumps({"m": mode, "t": token})


async def _create_talk_flow(
    nc_mcp: McpTestHelper, flows: list[tuple[str, int]], token: str, file_name: str, **extra: Any
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "name": "mcp-test-flow",
        "operation_class": TALK_OPERATION,
        "checks": [{"class": FILE_NAME, "operator": "is", "value": file_name}],
        "operation_config": _talk_config(token),
        "events": [POST_CREATE],
        **extra,
    }
    flow: dict[str, Any] = json.loads(await nc_mcp.call("create_flow", **args))
    flows.append(("user", flow["id"]))
    return flow


async def _listed(nc_mcp: McpTestHelper, scope: str = "user") -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(await nc_mcp.call("list_flows", scope=scope, limit=200))["data"]
    return data


class TestGetFlowOptions:
    @pytest.mark.asyncio
    async def test_user_scope(self, nc_mcp: McpTestHelper) -> None:
        options = json.loads(await nc_mcp.call("get_flow_options"))
        assert options["scope"] == "user"
        operations = {op["class"]: op for op in options["operations"]}
        assert operations[TALK_OPERATION]["events_fixed"] is False
        assert BLOCK_VERSIONING not in operations  # admin scope only
        file_entity = next(e for e in options["entities"] if e["class"] == FILE_ENTITY)
        assert POST_CREATE in [e["event"] for e in file_entity["events"]]
        checks = {c["class"]: c for c in options["checks"]}
        assert checks[FILE_NAME]["operators"] == ["is", "!is", "matches", "!matches"]
        assert "OCA\\WorkflowEngine\\Check\\UserGroupMembership" not in checks  # admin scope only

    @pytest.mark.asyncio
    async def test_global_scope(self, nc_mcp: McpTestHelper) -> None:
        options = json.loads(await nc_mcp.call("get_flow_options", scope="global"))
        operations = {op["class"]: op for op in options["operations"]}
        block = operations[BLOCK_VERSIONING]
        assert (block["entity"], block["events_fixed"]) == (FILE_ENTITY, True)
        assert TALK_OPERATION not in operations  # user scope only
        checks = {c["class"]: c for c in options["checks"]}
        assert checks["OCA\\WorkflowEngine\\Check\\UserGroupMembership"]["operators"] == ["is", "!is"]

    @pytest.mark.asyncio
    async def test_invalid_scope(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="Must be one of: user, global"):
            await nc_mcp.call("get_flow_options", scope="admin")


class TestUserFlows:
    @pytest.mark.asyncio
    async def test_create_list_update_delete(
        self, nc_mcp: McpTestHelper, room: str, flows: list[tuple[str, int]]
    ) -> None:
        flow = await _create_talk_flow(nc_mcp, flows, room, "report.pdf", name="mcp-test PDF alert")
        assert flow["name"] == "mcp-test PDF alert"
        assert flow["operation_class"] == TALK_OPERATION
        assert json.loads(flow["operation_config"]) == {"m": 1, "t": room}
        assert flow["checks"] == [{"class": FILE_NAME, "operator": "is", "value": "report.pdf"}]
        assert flow["events"] == [POST_CREATE]
        assert flow in await _listed(nc_mcp)

        updated = json.loads(
            await nc_mcp.call(
                "update_flow", flow_id=flow["id"], name="mcp-test renamed", events=[POST_CREATE, POST_WRITE]
            )
        )
        assert updated["name"] == "mcp-test renamed"
        assert updated["events"] == [POST_CREATE, POST_WRITE]
        assert updated["checks"] == flow["checks"]  # untouched fields are kept
        assert updated["operation_config"] == flow["operation_config"]

        updated = json.loads(
            await nc_mcp.call(
                "update_flow",
                flow_id=flow["id"],
                checks=[{"class": FILE_SIZE, "operator": "greater", "value": "10 MB"}],
                operation_config=_talk_config(room, 2),
            )
        )
        assert updated["checks"] == [{"class": FILE_SIZE, "operator": "greater", "value": "10 MB"}]
        assert json.loads(updated["operation_config"])["m"] == 2
        assert updated["name"] == "mcp-test renamed"

        assert "deleted" in await nc_mcp.call("delete_flow", flow_id=flow["id"])
        assert all(f["id"] != flow["id"] for f in await _listed(nc_mcp))

    @pytest.mark.asyncio
    async def test_flow_runs_when_a_matching_file_is_created(
        self, nc_mcp: McpTestHelper, room: str, flows: list[tuple[str, int]]
    ) -> None:
        file_name = f"flow-trigger-{uuid.uuid4().hex[:8]}.txt"
        await _create_talk_flow(nc_mcp, flows, room, file_name)
        await nc_mcp.create_test_dir()
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/{file_name}", "hello")
        await nc_mcp.upload_test_file(f"{TEST_BASE_DIR}/not-{file_name}", "ignored")
        chat = ""
        for _ in range(10):
            chat = await nc_mcp.call("get_messages", token=room, limit=50)
            if file_name in chat:
                break
            await asyncio.sleep(1)
        assert file_name in chat
        assert f"not-{file_name}" not in chat

    @pytest.mark.asyncio
    async def test_pagination(self, nc_mcp: McpTestHelper, room: str, flows: list[tuple[str, int]]) -> None:
        for i in range(3):
            await _create_talk_flow(nc_mcp, flows, room, f"page-{i}.txt")
        mine = {flow_id for _, flow_id in flows}
        first = json.loads(await nc_mcp.call("list_flows", limit=2))
        assert first["pagination"]["count"] == 2
        assert first["pagination"]["has_more"] is True
        everything = await _listed(nc_mcp)
        assert mine <= {f["id"] for f in everything}
        assert [f["id"] for f in everything] == sorted(f["id"] for f in everything)


class TestFlowErrors:
    @pytest.mark.asyncio
    async def test_invalid_operator(self, nc_mcp: McpTestHelper, room: str, flows: list[tuple[str, int]]) -> None:
        with pytest.raises(ToolError, match="The given operator is invalid"):
            await nc_mcp.call(
                "create_flow",
                name="mcp-test-flow",
                operation_class=TALK_OPERATION,
                checks=[{"class": FILE_NAME, "operator": "bigger", "value": "x"}],
                operation_config=_talk_config(room),
                events=[POST_CREATE],
            )

    @pytest.mark.asyncio
    async def test_operation_validates_its_config(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="Room not found"):
            await nc_mcp.call(
                "create_flow",
                name="mcp-test-flow",
                operation_class=TALK_OPERATION,
                checks=[{"class": FILE_NAME, "operator": "is", "value": "x"}],
                operation_config=_talk_config("no-such-room"),
                events=[POST_CREATE],
            )

    @pytest.mark.asyncio
    async def test_events_are_required(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="No events are chosen"):
            await nc_mcp.call(
                "create_flow",
                name="mcp-test-flow",
                operation_class=TALK_OPERATION,
                checks=[{"class": FILE_NAME, "operator": "is", "value": "x"}],
                operation_config=_talk_config(room),
            )

    @pytest.mark.asyncio
    async def test_unknown_class_names_the_likely_cause(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="get_flow_options lists the ones available"):
            await nc_mcp.call(
                "create_flow",
                name="mcp-test-flow",
                operation_class=TALK_OPERATION,
                checks=[{"class": "OCA\\Nope\\Check", "operator": "is", "value": "x"}],
                operation_config=_talk_config(room),
                events=[POST_CREATE],
            )

    @pytest.mark.asyncio
    async def test_operation_from_the_other_scope(self, nc_mcp: McpTestHelper, room: str) -> None:
        with pytest.raises(ToolError, match="is invalid"):
            await nc_mcp.call(
                "create_flow",
                name="mcp-test-flow",
                operation_class=TALK_OPERATION,
                scope="global",
                checks=[{"class": FILE_NAME, "operator": "is", "value": "x"}],
                operation_config=_talk_config(room),
                events=[POST_CREATE],
            )

    @pytest.mark.asyncio
    async def test_unknown_flow_id(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="No flow with ID 999999 in the user scope"):
            await nc_mcp.call("update_flow", flow_id=999999, name="x")
        with pytest.raises(ToolError, match="No flow with ID 999999 in the user scope"):
            await nc_mcp.call("delete_flow", flow_id=999999)

    @pytest.mark.asyncio
    async def test_user_flow_is_not_found_in_global_scope(
        self, nc_mcp: McpTestHelper, room: str, flows: list[tuple[str, int]]
    ) -> None:
        flow = await _create_talk_flow(nc_mcp, flows, room, "scoped.txt")
        with pytest.raises(ToolError, match=f"No flow with ID {flow['id']} in the global scope"):
            await nc_mcp.call("delete_flow", flow_id=flow["id"], scope="global")
        assert any(f["id"] == flow["id"] for f in await _listed(nc_mcp))


class TestGlobalFlows:
    @pytest.mark.asyncio
    async def test_complex_operation_without_events(self, nc_mcp: McpTestHelper, flows: list[tuple[str, int]]) -> None:
        flow = json.loads(
            await nc_mcp.call(
                "create_flow",
                operation_class=BLOCK_VERSIONING,
                scope="global",
                name="mcp-test no versions for temp files",
                checks=[{"class": FILE_NAME, "operator": "matches", "value": "/\\.mcptmp$/"}],
            )
        )
        flows.append(("global", flow["id"]))
        assert flow["events"] == []
        assert any(f["id"] == flow["id"] for f in await _listed(nc_mcp, "global"))
        assert all(f["id"] != flow["id"] for f in await _listed(nc_mcp))
        updated = json.loads(
            await nc_mcp.call("update_flow", flow_id=flow["id"], scope="global", name="mcp-test renamed")
        )
        assert updated["name"] == "mcp-test renamed"
        await nc_mcp.call("delete_flow", flow_id=flow["id"], scope="global")
        assert all(f["id"] != flow["id"] for f in await _listed(nc_mcp, "global"))


class TestCheckGuideExamples:
    """The operators and example values get_flow_options documents must be ones Nextcloud accepts."""

    @pytest.mark.asyncio
    async def test_user_scope_checks(self, nc_mcp: McpTestHelper, room: str, flows: list[tuple[str, int]]) -> None:
        tag = json.loads(await nc_mcp.call("create_tag", name=f"mcp-test-flow-tag-{uuid.uuid4().hex[:6]}"))
        checks = [
            ("FileMimeType", "is", "application/pdf"),
            ("FileMimeType", "matches", "/^image\\//"),
            ("FileName", "is", "report.pdf"),
            ("FileName", "matches", "/\\.pdf$/i"),
            ("FileSize", "greater", "10 MB"),
            ("FileSize", "less", "2048 B"),
            ("FileSystemTags", "is", str(tag["id"])),
            ("RequestRemoteAddress", "matchesIPv4", "192.168.0.0/16"),
            ("RequestRemoteAddress", "!matchesIPv6", "2001:db8::/32"),
            ("RequestTime", "in", '["08:00 Europe/Berlin","17:00 Europe/Berlin"]'),
            ("RequestTime", "!in", '["00:00 Etc/UTC","06:00 Etc/UTC"]'),
            ("RequestUserAgent", "is", "android"),
            ("RequestUserAgent", "!is", "mail"),
        ]
        try:
            for check, operator, value in checks:
                check_class = f"OCA\\WorkflowEngine\\Check\\{check}"
                flow = json.loads(
                    await nc_mcp.call(
                        "create_flow",
                        name="mcp-test-guide",
                        operation_class=TALK_OPERATION,
                        checks=[{"class": check_class, "operator": operator, "value": value}],
                        operation_config=_talk_config(room),
                        events=[POST_CREATE],
                    )
                )
                flows.append(("user", flow["id"]))
                assert flow["checks"] == [{"class": check_class, "operator": operator, "value": value}]
        finally:
            await nc_mcp.call("delete_tag", tag_id=tag["id"])

    @pytest.mark.asyncio
    async def test_global_only_checks(self, nc_mcp: McpTestHelper, flows: list[tuple[str, int]]) -> None:
        for check, operator, value in (("RequestURL", "is", "webdav"), ("UserGroupMembership", "is", "admin")):
            flow = json.loads(
                await nc_mcp.call(
                    "create_flow",
                    name="mcp-test-guide",
                    operation_class=BLOCK_VERSIONING,
                    scope="global",
                    checks=[{"class": f"OCA\\WorkflowEngine\\Check\\{check}", "operator": operator, "value": value}],
                )
            )
            flows.append(("global", flow["id"]))


class TestDisabledUserFlows:
    @pytest.mark.asyncio
    async def test_errors_say_user_flows_are_disabled(self, nc_mcp: McpTestHelper) -> None:
        setting = "apps/provisioning_api/api/v1/config/apps/workflowengine/user_scope_disabled"
        await nc_mcp.client.ocs_post(setting, data={"value": "yes"})
        try:
            with pytest.raises(ToolError, match="User flows are probably disabled on this instance"):
                await nc_mcp.call("list_flows")
            with pytest.raises(ToolError, match="user flows may be disabled"):
                await nc_mcp.call("get_flow_options")
        finally:
            await nc_mcp.client.ocs_delete(setting)
        assert isinstance(json.loads(await nc_mcp.call("list_flows"))["data"], list)


class TestFlowPermissions:
    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call(
                "create_flow", operation_class=TALK_OPERATION, checks=[{"class": FILE_NAME, "operator": "is"}]
            )

    @pytest.mark.asyncio
    async def test_write_level_blocks_global_flows(self, nc_mcp_write: McpTestHelper) -> None:
        """Refused before any request is made."""
        with pytest.raises(ToolError, match="requires 'destructive' permission"):
            await nc_mcp_write.call(
                "create_flow",
                name="mcp-test-flow",
                operation_class=BLOCK_VERSIONING,
                scope="global",
                checks=[{"class": FILE_NAME, "operator": "is", "value": "x"}],
            )
        with pytest.raises(ToolError, match="requires 'destructive' permission"):
            await nc_mcp_write.call("update_flow", flow_id=1, scope="global", name="x")

    @pytest.mark.asyncio
    async def test_write_level_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_flow", flow_id=1)
