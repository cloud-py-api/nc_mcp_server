"""Unit tests for the Flow tools: settings-page parsing, request bodies, merging and error wording."""

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import flows

API = "apps/workflowengine/api/v1/workflows"
TALK = "OCA\\Talk\\Flow\\Operation"
FILE_NAME = "OCA\\WorkflowEngine\\Check\\FileName"
FILE = "OCA\\WorkflowEngine\\Entity\\File"
CREATED = "\\OCP\\Files::postCreate"

RAW_FLOW = {
    "id": 7,
    "class": TALK,
    "name": "PDFs",
    "checks": [{"class": FILE_NAME, "operator": "is", "value": "a.pdf", "invalid": False}],
    "operation": '{"m":1,"t":"abc"}',
    "entity": FILE,
    "events": ["\\OCP\\Files::postCreate"],
    "scope_type": 1,
    "scope_actor_id": "admin",
}


def _state_input(key: str, value: Any, id_first: bool = True) -> str:
    encoded = base64.b64encode(json.dumps(value).encode()).decode()
    attrs = [f'id="initial-state-workflowengine-{key}"', f'value="{encoded}"']
    return f'<input type="hidden" {" ".join(attrs if id_first else attrs[::-1])}>'


PAGE = "\n".join(
    [
        "<html><body>",
        _state_input(
            "entities",
            [{"id": FILE, "name": "File", "events": [{"eventName": CREATED, "displayName": "made"}]}],
        ),
        _state_input(
            "operators",
            {TALK: {"id": TALK, "name": "Write", "description": "d", "fixedEntity": "", "isComplex": False}},
            id_first=False,
        ),
        _state_input(
            "checks",
            {
                "0": {"id": FILE_NAME, "supportedEntities": [FILE]},
                "2": {"id": "OCA\\App\\Check", "supportedEntities": []},
            },
        ),
        '<input type="hidden" id="initial-state-workflowengine-broken" value="not base64!">',
        '<input type="hidden" id="initial-state-other-app-thing" value="e30=">',
        "</body></html>",
    ]
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock = MagicMock()
    mock.ocs_get = AsyncMock(return_value={TALK: [RAW_FLOW]})
    mock.ocs_post_json = AsyncMock(return_value=RAW_FLOW)
    mock.ocs_put_json = AsyncMock(return_value=RAW_FLOW)
    mock.ocs_delete = AsyncMock(return_value=True)
    mock.web_page = AsyncMock(return_value=PAGE)
    monkeypatch.setattr(flows, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-flows")
    flows.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


CHECK = {"class": FILE_NAME, "operator": "is", "value": "x"}


class TestInitialState:
    def test_reads_workflowengine_state_in_any_attribute_order(self) -> None:
        state = flows._initial_state(PAGE)
        assert sorted(state) == ["checks", "entities", "operators"]

    def test_undecodable_values_are_skipped(self) -> None:
        assert "broken" not in flows._initial_state(PAGE)

    def test_html_entities_in_the_value_are_decoded(self) -> None:
        encoded = base64.b64encode(b'{"a": 1}').decode().replace("=", "&#61;")
        page = f'<input id="initial-state-workflowengine-scope" value="{encoded}">'
        assert flows._initial_state(page) == {"scope": {"a": 1}}


class TestGetFlowOptions:
    async def test_formats_operations_entities_and_checks(self, mcp: FastMCP, client: MagicMock) -> None:
        options = json.loads(await _call(mcp, "get_flow_options"))
        client.web_page.assert_awaited_once_with("settings/user/workflow")
        assert options["operations"] == [
            {
                "class": TALK,
                "name": "Write",
                "description": "d",
                "entity": None,
                "events_fixed": False,
                "trigger": None,
            }
        ]
        assert options["entities"] == [
            {"class": FILE, "name": "File", "events": [{"event": "\\OCP\\Files::postCreate", "name": "made"}]}
        ]
        file_name, app_check = options["checks"]
        assert file_name["operators"] == ["is", "!is", "matches", "!matches"]
        assert "regular expression" in file_name["value"]
        assert app_check == {"class": "OCA\\App\\Check", "entities": []}  # no guide for app checks

    async def test_global_scope_reads_the_admin_page(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "get_flow_options", scope="global")
        client.web_page.assert_awaited_once_with("settings/admin/workflow")

    async def test_page_without_state_is_an_error(self, mcp: FastMCP, client: MagicMock) -> None:
        client.web_page.return_value = "<html>login page</html>"
        with pytest.raises(ToolError, match="did not contain the list of operations"):
            await _call(mcp, "get_flow_options")

    async def test_invalid_scope(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Must be one of: user, global"):
            await _call(mcp, "get_flow_options", scope="admin")
        client.web_page.assert_not_awaited()


class TestListFlows:
    async def test_flattens_groups_and_sorts_by_id(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {TALK: [{**RAW_FLOW, "id": 9}], "OCA\\X\\Op": [{**RAW_FLOW, "id": 3}]}
        result = json.loads(await _call(mcp, "list_flows"))
        client.ocs_get.assert_awaited_once_with(f"{API}/user")
        assert [f["id"] for f in result["data"]] == [3, 9]

    async def test_formats_a_flow(self, mcp: FastMCP, client: MagicMock) -> None:
        flow = json.loads(await _call(mcp, "list_flows", scope="global"))["data"][0]
        client.ocs_get.assert_awaited_once_with(f"{API}/global")
        assert flow == {
            "id": 7,
            "name": "PDFs",
            "operation_class": TALK,
            "operation_config": '{"m":1,"t":"abc"}',
            "entity": FILE,
            "events": ["\\OCP\\Files::postCreate"],
            "checks": [{"class": FILE_NAME, "operator": "is", "value": "a.pdf"}],
        }

    async def test_no_flows(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = []  # what Nextcloud sends when there are none
        result = json.loads(await _call(mcp, "list_flows"))
        assert result["data"] == []
        assert result["pagination"]["has_more"] is False

    async def test_pagination(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {TALK: [{**RAW_FLOW, "id": i} for i in range(1, 6)]}
        result = json.loads(await _call(mcp, "list_flows", limit=2, offset=2))
        assert [f["id"] for f in result["data"]] == [3, 4]
        assert result["pagination"] == {"count": 2, "offset": 2, "limit": 2, "has_more": True}


class TestCreateFlow:
    async def test_request_body(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(
            mcp,
            "create_flow",
            operation_class=TALK,
            checks=[CHECK],
            name="n",
            operation_config='{"m":1,"t":"abc"}',
            events=["\\OCP\\Files::postCreate"],
        )
        client.ocs_post_json.assert_awaited_once_with(
            f"{API}/user",
            json_data={
                "class": TALK,
                "name": "n",
                "checks": [CHECK],
                "operation": '{"m":1,"t":"abc"}',
                "entity": FILE,
                "events": ["\\OCP\\Files::postCreate"],
            },
        )

    async def test_config_object_is_sent_as_a_compact_string(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "create_flow", operation_class=TALK, checks=[CHECK], operation_config={"m": 2, "t": "abc"})
        assert client.ocs_post_json.await_args.kwargs["json_data"]["operation"] == '{"m":2,"t":"abc"}'

    async def test_no_events_sends_an_empty_list(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "create_flow", operation_class=TALK, checks=[CHECK])
        assert client.ocs_post_json.await_args.kwargs["json_data"]["events"] == []

    async def test_incomplete_check_is_refused(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="lacks operator, value"):
            await _call(mcp, "create_flow", operation_class=TALK, checks=[{"class": FILE_NAME}])
        client.ocs_post_json.assert_not_awaited()

    async def test_no_checks_is_refused(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="at least one check"):
            await _call(mcp, "create_flow", operation_class=TALK, checks=[])
        client.ocs_post_json.assert_not_awaited()

    async def test_global_flows_need_the_destructive_level(self, mcp: FastMCP, client: MagicMock) -> None:
        set_permission_level(PermissionLevel.WRITE)
        with pytest.raises(ToolError, match="requires 'destructive' permission"):
            await _call(mcp, "create_flow", operation_class=TALK, checks=[CHECK], scope="global")
        client.ocs_post_json.assert_not_awaited()
        await _call(mcp, "create_flow", operation_class=TALK, checks=[CHECK])
        client.ocs_post_json.assert_awaited_once()

    @pytest.mark.parametrize(
        "operation_class",
        ["OCA\\WorkflowScript\\Operation", "\\OCA\\WorkflowScript\\Operation", "oca\\workflowscript\\OPERATION"],
    )
    async def test_command_operations_are_refused(self, mcp: FastMCP, client: MagicMock, operation_class: str) -> None:
        with pytest.raises(ToolError, match="runs commands on the Nextcloud server"):
            await _call(mcp, "create_flow", operation_class=operation_class, checks=[CHECK], scope="global")
        client.ocs_post_json.assert_not_awaited()

    async def test_ocr_custom_arguments_are_refused(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Leave customCliArgs empty"):
            await _call(
                mcp,
                "create_flow",
                operation_class="OCA\\WorkflowOcr\\Operation",
                checks=[CHECK],
                operation_config={"languages": ["eng"], "customCliArgs": "--plugin x"},
            )
        client.ocs_post_json.assert_not_awaited()

    @pytest.mark.parametrize("config", ['{"languages":["eng"]}', '{"customCliArgs":"  "}', "", "not json"])
    async def test_ocr_without_custom_arguments_is_allowed(self, mcp: FastMCP, client: MagicMock, config: str) -> None:
        await _call(
            mcp, "create_flow", operation_class="OCA\\WorkflowOcr\\Operation", checks=[CHECK], operation_config=config
        )
        client.ocs_post_json.assert_awaited_once()

    async def test_server_error_names_the_likely_cause(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.side_effect = NextcloudError("OCS POST x: Internal Server Error", 500)
        with pytest.raises(ToolError, match="unknown operation, check or entity class"):
            await _call(mcp, "create_flow", operation_class="OCA\\Nope", checks=[CHECK])

    async def test_validation_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json.side_effect = NextcloudError("OCS POST x: No events are chosen.", 400)
        with pytest.raises(ToolError, match=r"No events are chosen\.$"):
            await _call(mcp, "create_flow", operation_class=TALK, checks=[CHECK])


class TestUpdateFlow:
    async def test_unset_fields_keep_their_values(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "update_flow", flow_id=7, name="New name")
        client.ocs_put_json.assert_awaited_once_with(
            f"{API}/user/7",
            json_data={
                "name": "New name",
                "checks": [{"class": FILE_NAME, "operator": "is", "value": "a.pdf"}],
                "operation": '{"m":1,"t":"abc"}',
                "entity": FILE,
                "events": ["\\OCP\\Files::postCreate"],
            },
        )

    async def test_every_field_can_change(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(
            mcp,
            "update_flow",
            flow_id=7,
            name="",
            checks=[CHECK],
            operation_config={"m": 3, "t": "abc"},
            entity="OCA\\X\\Entity",
            events=[],
        )
        assert client.ocs_put_json.await_args.kwargs["json_data"] == {
            "name": "",
            "checks": [CHECK],
            "operation": '{"m":3,"t":"abc"}',
            "entity": "OCA\\X\\Entity",
            "events": [],
        }

    async def test_unknown_flow(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="No flow with ID 8 in the user scope"):
            await _call(mcp, "update_flow", flow_id=8, name="x")
        client.ocs_put_json.assert_not_awaited()

    async def test_global_flows_need_the_destructive_level(self, mcp: FastMCP, client: MagicMock) -> None:
        set_permission_level(PermissionLevel.WRITE)
        with pytest.raises(ToolError, match="requires 'destructive' permission"):
            await _call(mcp, "update_flow", flow_id=7, scope="global", name="x")
        client.ocs_get.assert_not_awaited()

    async def test_ocr_custom_arguments_are_refused_on_update(self, mcp: FastMCP, client: MagicMock) -> None:
        ocr = "OCA\\WorkflowOcr\\Operation"
        client.ocs_get.return_value = {ocr: [{**RAW_FLOW, "class": ocr, "operation": '{"languages":["eng"]}'}]}
        await _call(mcp, "update_flow", flow_id=7, name="renamed")
        with pytest.raises(ToolError, match="Leave customCliArgs empty"):
            await _call(mcp, "update_flow", flow_id=7, operation_config={"customCliArgs": "--plugin x"})
        client.ocs_put_json.assert_awaited_once()

    async def test_existing_command_flows_are_not_changed(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {
            "OCA\\WorkflowScript\\Operation": [{**RAW_FLOW, "class": "OCA\\WorkflowScript\\Operation"}]
        }
        with pytest.raises(ToolError, match="runs commands on the Nextcloud server"):
            await _call(mcp, "update_flow", flow_id=7, scope="global", name="x")
        client.ocs_put_json.assert_not_awaited()


class TestDisabledUserFlows:
    async def test_list_explains_the_forbidden_answer(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = NextcloudError("OCS GET x: User not logged in", 403)
        with pytest.raises(ToolError, match="User flows are probably disabled on this instance"):
            await _call(mcp, "list_flows")

    async def test_global_scope_keeps_the_original_error(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = NextcloudError("OCS GET x: Logged in account must be an admin", 403)
        with pytest.raises(ToolError, match=r"must be an admin$"):
            await _call(mcp, "list_flows", scope="global")


class TestDeleteFlow:
    async def test_deletes_in_the_given_scope(self, mcp: FastMCP, client: MagicMock) -> None:
        assert await _call(mcp, "delete_flow", flow_id=7, scope="global") == "Flow 7 deleted."
        client.ocs_delete.assert_awaited_once_with(f"{API}/global/7")

    async def test_out_of_scope_answer_becomes_not_found(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.side_effect = NextcloudError("OCS DELETE x: Target operation not within scope", 403)
        with pytest.raises(ToolError, match="No flow with ID 7 in the user scope"):
            await _call(mcp, "delete_flow", flow_id=7)

    async def test_other_forbidden_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.side_effect = NextcloudError("OCS DELETE x: Password confirmation is required", 403)
        with pytest.raises(ToolError, match="Password confirmation is required"):
            await _call(mcp, "delete_flow", flow_id=7)
