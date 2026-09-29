"""Unit tests for Circles member level changes and team folder safety."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import circles

LOCK_ERROR = NextcloudError(
    "OCS PUT x: An exception occurred while executing a query: SQLSTATE[0A000]: Feature not supported: 7 ERROR:"
    "  FOR UPDATE cannot be applied to the nullable side of an outer join",
    400,
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.WRITE)
    mock = MagicMock()
    mock.ocs_put_json = AsyncMock(return_value={"id": "m1", "level": 9})
    monkeypatch.setattr(circles, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-circles")
    circles.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


class TestMemberLevel:
    async def test_owner_transfer(self, mcp: FastMCP, client: MagicMock) -> None:
        result = await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")
        assert '"level": 9' in result
        client.ocs_put_json.assert_awaited_once_with("apps/circles/circles/c/members/m1/level", json_data={"level": 9})

    async def test_owner_transfer_the_database_refuses(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = LOCK_ERROR
        with pytest.raises(ToolError, match=r"cannot transfer ownership.*Nothing was changed"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")

    async def test_other_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = NextcloudError("OCS PUT x: Member not found", 404)
        with pytest.raises(ToolError, match="Member not found"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")

    async def test_sqlite_refusal(self, mcp: FastMCP, client: MagicMock) -> None:
        message = "OCS PUT x: Operation 'FOR UPDATE' is not supported by platform."
        client.ocs_put_json.side_effect = NextcloudError(message, 500)
        with pytest.raises(ToolError, match="cannot transfer ownership"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")

    async def test_other_for_update_errors_pass_through(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = NextcloudError("OCS PUT x: deadlock detected while waiting FOR UPDATE", 500)
        with pytest.raises(ToolError, match="deadlock detected"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="owner")

    async def test_the_lock_message_only_applies_to_owner(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put_json.side_effect = LOCK_ERROR
        with pytest.raises(ToolError, match="FOR UPDATE cannot be applied"):
            await _call(mcp, "update_circle_member_level", circle_id="c", member_id="m1", level="admin")


FOLDER = {"id": 7, "quota": -3, "mountPoint": "Team A"}
NO_FOLDER = NextcloudError("OCS GET x: No team folder linked to this team", 404)
OWNER = {"id": "c", "initiator": {"id": "m-owner", "level": 9}}


def _member(member_id: str, status: str = "Member") -> dict[str, Any]:
    return {"id": member_id, "status": status}


@pytest.fixture
def destructive(client: MagicMock) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    client.ocs_get = AsyncMock()
    client.ocs_delete = AsyncMock(return_value=[])
    return client


class TestCreateTeamFolder:
    async def test_opts_out_by_default(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json = AsyncMock(return_value={"id": "c"})
        client.ocs_get = AsyncMock()
        result = json.loads(await _call(mcp, "create_circle", name="Team A"))
        client.ocs_post_json.assert_awaited_once_with(
            "apps/circles/circles", json_data={"name": "Team A", "createTeamFolder": False}
        )
        client.ocs_get.assert_not_awaited()
        assert "team_folder" not in result

    async def test_reports_the_created_folder(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json = AsyncMock(return_value={"id": "c"})
        client.ocs_get = AsyncMock(return_value=FOLDER)
        client.renew_session = AsyncMock()
        result = json.loads(await _call(mcp, "create_circle", name="Team A", team_folder=True, local=True))
        client.renew_session.assert_awaited_once_with()
        client.ocs_post_json.assert_awaited_once_with(
            "apps/circles/circles", json_data={"name": "Team A", "createTeamFolder": True, "local": True}
        )
        client.ocs_get.assert_awaited_once_with("apps/circles/teams/c/folder")
        assert result["team_folder"] == FOLDER
        assert "team_folder_note" not in result

    async def test_says_why_no_folder_came(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json = AsyncMock(return_value={"id": "c"})
        client.ocs_get = AsyncMock(side_effect=NextcloudError("OCS GET x: Invalid query", 404))
        client.renew_session = AsyncMock()
        result = json.loads(await _call(mcp, "create_circle", name="Team A", team_folder=True))
        client.renew_session.assert_not_awaited()
        assert result["team_folder"] is None
        assert "Nextcloud 35" in result["team_folder_note"]

    async def test_a_failed_folder_lookup_still_reports_the_circle(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post_json = AsyncMock(return_value={"id": "c"})
        client.ocs_get = AsyncMock(side_effect=NextcloudError("OCS GET x: Internal Server Error", 500))
        client.renew_session = AsyncMock()
        result = json.loads(await _call(mcp, "create_circle", name="Team A", team_folder=True))
        assert result["id"] == "c"
        assert result["team_folder"] is None
        assert result["team_folder_note"].startswith("The circle was created, but its team folder could not be read")
        client.renew_session.assert_not_awaited()


class TestDeleteCircle:
    async def test_without_a_folder(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.side_effect = NO_FOLDER
        result = json.loads(await _call(mcp, "delete_circle", circle_id="c"))
        assert result == {"deleted_circle_id": "c"}
        destructive.ocs_delete.assert_awaited_once_with("apps/circles/circles/c")

    async def test_refuses_to_lose_the_folder(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.return_value = FOLDER
        with pytest.raises(ToolError, match=r"team folder 'Team A' and every file.*Nothing was changed.*owner calls"):
            await _call(mcp, "delete_circle", circle_id="c")
        destructive.ocs_delete.assert_not_awaited()

    async def test_deletes_the_folder_when_asked(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.return_value = FOLDER
        result = json.loads(await _call(mcp, "delete_circle", circle_id="c", delete_team_folder=True))
        assert result == {"deleted_circle_id": "c", "deleted_team_folder": "Team A"}
        destructive.ocs_delete.assert_awaited_once_with("apps/circles/circles/c")

    async def test_a_failed_check_deletes_nothing(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.side_effect = NextcloudError("OCS GET x: Internal Server Error", 500)
        with pytest.raises(ToolError, match=r"Nothing was changed: checking the team folder of circle c first failed"):
            await _call(mcp, "delete_circle", circle_id="c")
        destructive.ocs_delete.assert_not_awaited()

    async def test_a_forced_delete_goes_past_a_failed_check(self, mcp: FastMCP, destructive: MagicMock) -> None:
        """The caller agreed to lose the folder, so a check that cannot answer does not block the deletion."""
        destructive.ocs_get.side_effect = NextcloudError("OCS GET x: Internal Server Error", 500)
        result = json.loads(await _call(mcp, "delete_circle", circle_id="c", delete_team_folder=True))
        assert result == {"deleted_circle_id": "c"}
        destructive.ocs_delete.assert_awaited_once_with("apps/circles/circles/c")

    async def test_non_members_learn_why(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.side_effect = NextcloudError("OCS GET x: Insufficient permissions", 403)
        with pytest.raises(ToolError, match=r"Nothing was changed: you are not a member of circle c, or it does not"):
            await _call(mcp, "delete_circle", circle_id="c")
        destructive.ocs_delete.assert_not_awaited()


class TestLeaveCircle:
    async def test_members_leave_without_checks(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.return_value = {"id": "c", "initiator": {"id": "m1", "level": 1}}
        await _call(mcp, "leave_circle", circle_id="c")
        destructive.ocs_get.assert_awaited_once_with("apps/circles/circles/c")
        destructive.ocs_put_json.assert_awaited_once_with("apps/circles/circles/c/leave", json_data={})

    async def test_the_owner_hands_over_to_another_member(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.side_effect = [OWNER, FOLDER, [_member("m-owner"), _member("m2")]]
        await _call(mcp, "leave_circle", circle_id="c")
        destructive.ocs_put_json.assert_awaited_once_with("apps/circles/circles/c/leave", json_data={})

    @pytest.mark.parametrize("status", ["Invited", "Requesting"])
    async def test_a_pending_member_takes_the_circle_over(
        self, mcp: FastMCP, destructive: MagicMock, status: str
    ) -> None:
        """Circles picks any other member entry as the new owner, pending ones too, so nothing is destroyed."""
        destructive.ocs_get.side_effect = [OWNER, FOLDER, [_member("m-owner"), _member("m2", status)]]
        await _call(mcp, "leave_circle", circle_id="c")
        destructive.ocs_put_json.assert_awaited_once_with("apps/circles/circles/c/leave", json_data={})

    async def test_the_owner_without_a_folder(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.side_effect = [OWNER, NO_FOLDER]
        await _call(mcp, "leave_circle", circle_id="c")
        destructive.ocs_put_json.assert_awaited_once()

    async def test_refuses_when_the_last_member_would_destroy_the_folder(
        self, mcp: FastMCP, destructive: MagicMock
    ) -> None:
        destructive.ocs_get.side_effect = [OWNER, FOLDER, [_member("m-owner")]]
        with pytest.raises(
            ToolError, match=r"last member deletes the circle.*team folder 'Team A'.*add a member first"
        ):
            await _call(mcp, "leave_circle", circle_id="c")
        assert destructive.ocs_get.await_args_list == [
            call("apps/circles/circles/c"),
            call("apps/circles/teams/c/folder"),
            call("apps/circles/circles/c/members"),
        ]
        destructive.ocs_put_json.assert_not_awaited()

    async def test_leaves_with_the_folder_when_asked(self, mcp: FastMCP, destructive: MagicMock) -> None:
        await _call(mcp, "leave_circle", circle_id="c", delete_team_folder=True)
        destructive.ocs_get.assert_not_awaited()
        destructive.ocs_put_json.assert_awaited_once_with("apps/circles/circles/c/leave", json_data={})

    @pytest.mark.parametrize("status", [403, 404])
    async def test_pending_members_cannot_see_the_circle(
        self, mcp: FastMCP, destructive: MagicMock, status: int
    ) -> None:
        destructive.ocs_get.side_effect = NextcloudError("OCS GET x: Insufficient permissions", status)
        await _call(mcp, "leave_circle", circle_id="c")
        destructive.ocs_put_json.assert_awaited_once()

    async def test_other_check_errors_leave_nothing(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.side_effect = NextcloudError("OCS GET x: Internal Server Error", 500)
        with pytest.raises(ToolError, match=r"Nothing was changed: checking whether leaving .*Internal Server Error"):
            await _call(mcp, "leave_circle", circle_id="c")
        destructive.ocs_put_json.assert_not_awaited()

    async def test_a_failed_member_list_leaves_nothing(self, mcp: FastMCP, destructive: MagicMock) -> None:
        destructive.ocs_get.side_effect = [OWNER, FOLDER, NextcloudError("OCS GET x: Bad Gateway", 502)]
        with pytest.raises(ToolError, match=r"Nothing was changed: checking whether leaving .*Bad Gateway"):
            await _call(mcp, "leave_circle", circle_id="c")
        destructive.ocs_put_json.assert_not_awaited()
