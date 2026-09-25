"""Unit tests for update_user, set_user_enabled and the group tools: what is sent and when."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import groups, users

SUBADMIN_ONLY = NextcloudError("OCS PATCH cloud/users/me: Logged in account must be at least a sub admin", 403)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock = MagicMock()
    mock.ocs_get = AsyncMock(return_value={"id": "me", "displayname": "Me"})
    mock.ocs_put = AsyncMock(return_value=[])
    mock.ocs_post = AsyncMock(return_value=[])
    mock.ocs_delete = AsyncMock(return_value=[])
    mock.ocs_patch_json = AsyncMock(return_value={"id": "someone"})
    for module in (users, groups):
        monkeypatch.setattr(module, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-user-admin")
    users.register(server)
    groups.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


class TestUpdateUser:
    async def test_sends_only_the_fields_passed(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "update_user", user_id="someone", display_name="New", groups=[], subadmin_groups=["g"])
        client.ocs_patch_json.assert_awaited_once_with(
            "cloud/users/someone", json_data={"displayName": "New", "groups": [], "subadminGroups": ["g"]}
        )

    async def test_empty_strings_are_sent_not_dropped(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "update_user", user_id="someone", email="", manager="", display_name="")
        body = client.ocs_patch_json.await_args.kwargs["json_data"]
        assert body == {"displayName": "", "email": "", "manager": ""}

    async def test_every_field_maps_to_its_api_name(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(
            mcp,
            "update_user",
            user_id="someone",
            display_name="d",
            email="e@x.org",
            password="p",
            quota="1 GB",
            language="de",
            manager="boss",
            groups=["a"],
            subadmin_groups=["b"],
        )
        body = client.ocs_patch_json.await_args.kwargs["json_data"]
        assert sorted(body) == sorted(
            ["displayName", "email", "password", "quota", "language", "manager", "groups", "subadminGroups"]
        )

    async def test_user_id_is_encoded(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "update_user", user_id="jane o'doe@x", display_name="J")
        assert client.ocs_patch_json.await_args.args == ("cloud/users/jane%20o%27doe%40x",)

    async def test_nothing_to_change_is_refused_before_any_request(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="at least one field"):
            await _call(mcp, "update_user", user_id="someone")
        client.ocs_patch_json.assert_not_awaited()

    async def test_group_changes_need_the_destructive_level(self, mcp: FastMCP, client: MagicMock) -> None:
        """Groups and sub-admin groups are complete lists, so passing them can remove memberships."""
        set_permission_level(PermissionLevel.WRITE)
        for field in ("groups", "subadmin_groups"):
            with pytest.raises(ToolError, match="requires 'destructive' permission"):
                await _call(mcp, "update_user", user_id="someone", display_name="x", **{field: []})
        client.ocs_patch_json.assert_not_awaited()
        await _call(mcp, "update_user", user_id="someone", display_name="x")
        client.ocs_patch_json.assert_awaited_once()

    async def test_returns_the_updated_user(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_patch_json.return_value = {"id": "someone", "displayname": "New"}
        result = json.loads(await _call(mcp, "update_user", user_id="someone", display_name="New"))
        assert result == {"id": "someone", "displayname": "New"}


class TestOwnAdminMembership:
    async def test_leaving_admin_out_of_own_groups_is_refused(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"id": "Me", "groups": ["admin", "staff"]}
        with pytest.raises(ToolError, match='Refusing to take your own account out of the "admin" group'):
            await _call(mcp, "update_user", user_id="me", groups=["staff", "new"])
        client.ocs_patch_json.assert_not_awaited()

    async def test_keeping_admin_needs_no_lookup(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "update_user", user_id="me", groups=["admin", "new"])
        client.ocs_get.assert_not_awaited()
        client.ocs_patch_json.assert_awaited_once()

    async def test_other_users_can_leave_admin(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"id": "me", "groups": ["admin"]}
        await _call(mcp, "update_user", user_id="someone", groups=[])
        client.ocs_patch_json.assert_awaited_once_with("cloud/users/someone", json_data={"groups": []})

    async def test_non_admin_caller_is_not_blocked(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"id": "me", "groups": ["staff"]}
        await _call(mcp, "update_user", user_id="me", groups=[])
        client.ocs_patch_json.assert_awaited_once()


class TestUpdateOwnAccountFallback:
    async def test_falls_back_to_per_key_updates_for_own_account(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_patch_json.side_effect = SUBADMIN_ONLY
        result = json.loads(
            await _call(
                mcp, "update_user", user_id="me", password="pw", language="de", email="me@x.org", display_name="Me!"
            )
        )
        assert result == {"id": "me", "displayname": "Me"}
        path = "cloud/users/me"
        assert client.method_calls[1] == call.ocs_get("cloud/user")
        # The profile fields first, then the read-back, then the password, which can end the session
        assert client.method_calls[-5:] == [
            call.ocs_put(path, data={"key": "displayname", "value": "Me!"}),
            call.ocs_put(path, data={"key": "email", "value": "me@x.org"}),
            call.ocs_put(path, data={"key": "language", "value": "de"}),
            call.ocs_get(path),
            call.ocs_put(path, data={"key": "password", "value": "pw"}),
        ]

    async def test_own_account_is_recognised_by_user_id_not_login(self, mcp: FastMCP, client: MagicMock) -> None:
        """The login name can differ from the user ID in case; the ID Nextcloud reports is used."""
        client.ocs_patch_json.side_effect = SUBADMIN_ONLY
        client.ocs_get.return_value = {"id": "Me", "displayname": "Me"}
        await _call(mcp, "update_user", user_id="me", language="de")
        client.ocs_put.assert_awaited_once_with("cloud/users/Me", data={"key": "language", "value": "de"})

    async def test_empty_display_name_becomes_the_user_id(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_patch_json.side_effect = SUBADMIN_ONLY
        await _call(mcp, "update_user", user_id="me", display_name="")
        client.ocs_put.assert_awaited_once_with("cloud/users/me", data={"key": "displayname", "value": "me"})

    async def test_admin_only_fields_are_refused_by_name(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_patch_json.side_effect = SUBADMIN_ONLY
        with pytest.raises(ToolError, match="Only admins and sub-admins can change quota, subadmin_groups"):
            await _call(mcp, "update_user", user_id="me", display_name="x", quota="1 GB", subadmin_groups=[])
        client.ocs_put.assert_not_awaited()

    async def test_rejected_value_names_the_field(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_patch_json.side_effect = SUBADMIN_ONLY
        client.ocs_put.side_effect = NextcloudError("OCS PUT cloud/users/me: HTTP 400", 400)
        with pytest.raises(ToolError, match=r"email was rejected \(OCS PUT cloud/users/me: HTTP 400\)"):
            await _call(mcp, "update_user", user_id="me", email="bad")

    async def test_no_fallback_for_other_users(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_patch_json.side_effect = SUBADMIN_ONLY
        with pytest.raises(ToolError, match="at least a sub admin"):
            await _call(mcp, "update_user", user_id="someone-else", display_name="x")
        client.ocs_put.assert_not_awaited()

    async def test_no_fallback_for_other_errors(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_patch_json.side_effect = NextcloudError("OCS PATCH cloud/users/me: email: Invalid", 422)
        with pytest.raises(ToolError, match="email: Invalid"):
            await _call(mcp, "update_user", user_id="me", email="bad")
        client.ocs_put.assert_not_awaited()


class TestSetUserEnabled:
    @pytest.mark.parametrize(("enabled", "action"), [(True, "enable"), (False, "disable")])
    async def test_calls_the_matching_endpoint(
        self, mcp: FastMCP, client: MagicMock, enabled: bool, action: str
    ) -> None:
        result = await _call(mcp, "set_user_enabled", user_id="a b", enabled=enabled)
        client.ocs_put.assert_awaited_once_with(f"cloud/users/a%20b/{action}")
        assert result == f"User 'a b' {action}d."

    async def test_disabling_needs_the_destructive_level(self, mcp: FastMCP, client: MagicMock) -> None:
        set_permission_level(PermissionLevel.WRITE)
        with pytest.raises(ToolError, match="requires 'destructive' permission"):
            await _call(mcp, "set_user_enabled", user_id="u", enabled=False)
        client.ocs_put.assert_not_awaited()
        await _call(mcp, "set_user_enabled", user_id="u", enabled=True)
        client.ocs_put.assert_awaited_once_with("cloud/users/u/enable")

    async def test_bare_400_names_the_likely_causes(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_put.side_effect = NextcloudError("OCS PUT cloud/users/me/disable: HTTP 400", 400)
        with pytest.raises(ToolError, match="HTTP 400: the user does not exist, or it is your own account"):
            await _call(mcp, "set_user_enabled", user_id="me", enabled=False)


class TestGroups:
    async def test_list_groups_formats_and_paginates(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {
            "groups": [
                {"id": "admin", "displayname": "admin", "usercount": 1, "disabled": 0, "canAdd": True},
                {"id": "sales", "displayname": "Sales", "usercount": 4, "disabled": 1, "canRemove": True},
            ]
        }
        result = json.loads(await _call(mcp, "list_groups", search="a", limit=2, offset=4))
        client.ocs_get.assert_awaited_once_with(
            "cloud/groups/details", params={"search": "a", "limit": "2", "offset": "4"}
        )
        assert result["data"] == [
            {"id": "admin", "display_name": "admin", "user_count": 1, "disabled_user_count": 0},
            {"id": "sales", "display_name": "Sales", "user_count": 4, "disabled_user_count": 1},
        ]
        assert result["pagination"] == {"count": 2, "offset": 4, "limit": 2, "has_more": True}

    async def test_group_id_is_encoded_twice(self, mcp: FastMCP, client: MagicMock) -> None:
        """Both endpoints urldecode() the already decoded ID, so a single encoding would lose "+" and "%"."""
        client.ocs_get.return_value = {"users": []}
        encoded = "R%2526D%2520%25231%252Fx%252B%252541"
        await _call(mcp, "list_group_members", group_id="R&D #1/x+%41")
        client.ocs_get.assert_awaited_once_with(f"cloud/groups/{encoded}/users")
        await _call(mcp, "delete_group", group_id="R&D #1/x+%41")
        client.ocs_delete.assert_awaited_once_with(f"cloud/groups/{encoded}")

    async def test_list_group_members_paginates_locally(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = {"users": ["u1", "u2", "u3"]}
        result = json.loads(await _call(mcp, "list_group_members", group_id="g", limit=2, offset=1))
        assert result == {"data": ["u2", "u3"], "pagination": {"count": 2, "offset": 1, "limit": 2, "has_more": False}}

    async def test_delete_group_bare_400_names_the_likely_causes(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_delete.side_effect = NextcloudError("OCS DELETE cloud/groups/admin: HTTP 400", 400)
        with pytest.raises(ToolError, match="HTTP 400: the group does not exist, it is the admin group"):
            await _call(mcp, "delete_group", group_id="admin")

    async def test_create_group_sends_display_name_only_when_given(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "create_group", group_id="g1")
        await _call(mcp, "create_group", group_id="g2", display_name="Group Two")
        assert client.ocs_post.await_args_list == [
            call("cloud/groups", data={"groupid": "g1"}),
            call("cloud/groups", data={"groupid": "g2", "displayname": "Group Two"}),
        ]
