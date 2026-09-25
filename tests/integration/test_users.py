"""Integration tests for user tools against a real Nextcloud instance.

Tests call MCP tools by name to exercise the full tool stack.
"""

import contextlib
import json
import secrets
import uuid
from collections.abc import AsyncGenerator

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudClient, NextcloudError
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.state import get_client, get_config, set_state
from nc_mcp_server.tools import groups as group_tools

from .conftest import McpTestHelper

pytestmark = pytest.mark.integration


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_returns_valid_json(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_current_user")
        data = json.loads(result)
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_returns_admin_user(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_current_user")
        data = json.loads(result)
        assert data["id"] == "admin"

    @pytest.mark.asyncio
    async def test_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_current_user")
        data = json.loads(result)
        assert "id" in data
        assert "displayname" in data
        assert "email" in data

    @pytest.mark.asyncio
    async def test_has_quota_info(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_current_user")
        data = json.loads(result)
        assert "quota" in data


class TestListUsers:
    @pytest.mark.asyncio
    async def test_returns_valid_json(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", limit=200)
        data = json.loads(result)
        assert isinstance(data, dict)
        assert "data" in data
        assert "pagination" in data

    @pytest.mark.asyncio
    async def test_includes_admin(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", limit=200)
        users = json.loads(result)["data"]
        assert "admin" in users

    @pytest.mark.asyncio
    async def test_search_filter(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", search="admin", limit=200)
        users = json.loads(result)["data"]
        assert "admin" in users

    @pytest.mark.asyncio
    async def test_search_no_match(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", search="nonexistent-user-xyz-12345", limit=200)
        users = json.loads(result)["data"]
        assert users == []

    @pytest.mark.asyncio
    async def test_limit_parameter(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users", limit=1)
        data = json.loads(result)
        assert len(data["data"]) <= 1

    @pytest.mark.asyncio
    async def test_default_parameters(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_users")
        data = json.loads(result)
        assert "data" in data
        assert "pagination" in data


class TestGetUser:
    @pytest.mark.asyncio
    async def test_get_admin(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_user", user_id="admin")
        data = json.loads(result)
        assert data["id"] == "admin"
        assert "displayname" in data

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_user", user_id="nonexistent-user-xyz-12345")

    @pytest.mark.asyncio
    async def test_user_has_detailed_fields(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("get_user", user_id="admin")
        data = json.loads(result)
        expected_fields = ["id", "displayname", "email", "enabled", "groups"]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_create_and_verify(self, nc_mcp: McpTestHelper) -> None:
        try:
            result = await nc_mcp.call("create_user", user_id="mcp-test-user", password="t3St*Pw!xQ9#mK2z")
            data = json.loads(result)
            assert data["id"] == "mcp-test-user"
            verify = json.loads(await nc_mcp.call("get_user", user_id="mcp-test-user"))
            assert verify["id"] == "mcp-test-user"
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-test-user")

    @pytest.mark.asyncio
    async def test_create_with_display_name(self, nc_mcp: McpTestHelper) -> None:
        try:
            await nc_mcp.call(
                "create_user", user_id="mcp-test-dn", password="t3St*Pw!xQ9#mK2z", display_name="Test Display"
            )
            verify = json.loads(await nc_mcp.call("get_user", user_id="mcp-test-dn"))
            assert verify["displayname"] == "Test Display"
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-test-dn")

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, nc_mcp: McpTestHelper) -> None:
        try:
            await nc_mcp.call("create_user", user_id="mcp-test-dup", password="t3St*Pw!xQ9#mK2z")
            with pytest.raises(ToolError):
                await nc_mcp.call("create_user", user_id="mcp-test-dup", password="t3St*Pw!xQ9#mK2z")
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-test-dup")


class TestDeleteUser:
    @pytest.mark.asyncio
    async def test_delete_user(self, nc_mcp: McpTestHelper) -> None:
        try:
            await nc_mcp.call("create_user", user_id="mcp-test-del", password="t3St*Pw!xQ9#mK2z")
            result = await nc_mcp.call("delete_user", user_id="mcp-test-del")
            assert "deleted" in result.lower()
            with pytest.raises(ToolError):
                await nc_mcp.call("get_user", user_id="mcp-test-del")
        finally:
            with contextlib.suppress(Exception):
                await nc_mcp.call("delete_user", user_id="mcp-test-del")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("delete_user", user_id="nonexistent-user-xyz-99999")


class TestUserPermissions:
    @pytest.mark.asyncio
    async def test_read_only_blocks_create(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("create_user", user_id="blocked", password="Test123!")

    @pytest.mark.asyncio
    async def test_write_blocks_delete(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_user", user_id="nobody")


def _password() -> str:
    return f"Mcp-{secrets.token_hex(10)}!"


@pytest.fixture
async def new_user(nc_mcp: McpTestHelper) -> AsyncGenerator[tuple[str, str]]:
    """A fresh regular user, as (user_id, password); deleted afterwards."""
    user_id, password = f"mcp-test-u-{uuid.uuid4().hex[:8]}", _password()
    await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": password})
    yield user_id, password
    with contextlib.suppress(Exception):
        await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")


@pytest.fixture
async def two_groups(nc_mcp: McpTestHelper) -> AsyncGenerator[tuple[str, str]]:
    """Two fresh groups; deleted afterwards."""
    suffix = uuid.uuid4().hex[:8]
    groups = (f"mcp-test-grp-a-{suffix}", f"mcp-test-grp-b-{suffix}")
    for group in groups:
        await nc_mcp.client.ocs_post("cloud/groups", data={"groupid": group})
    yield groups
    for group in groups:
        with contextlib.suppress(Exception):
            await nc_mcp.client.ocs_delete(group_tools._group_path(group))


def _config_for(user_id: str, password: str) -> Config:
    return Config(
        nextcloud_url=get_config().nextcloud_url,
        user=user_id,
        password=password,
        permission_level=PermissionLevel.DESTRUCTIVE,
    )


@contextlib.asynccontextmanager
async def _as_user(nc_mcp: McpTestHelper, user_id: str, password: str) -> AsyncGenerator[McpTestHelper]:
    """Run tools as another user by swapping the global client, restoring the admin one afterwards."""
    admin_client, admin_config = get_client(), get_config()
    config = _config_for(user_id, password)
    client = NextcloudClient(config)
    set_state(client, config)
    try:
        yield McpTestHelper(nc_mcp.mcp, client)
    finally:
        set_state(admin_client, admin_config)
        await client.close()


async def _can_log_in(user_id: str, password: str) -> bool:
    client = NextcloudClient(_config_for(user_id, password))
    try:
        await client.ocs_get("cloud/user")
    except NextcloudError as e:
        if e.status_code != 401:
            raise
        return False
    else:
        return True
    finally:
        await client.close()


async def _user(nc_mcp: McpTestHelper, user_id: str) -> dict[str, object]:
    data: dict[str, object] = json.loads(await nc_mcp.call("get_user", user_id=user_id))
    return data


class TestUpdateUser:
    @pytest.mark.asyncio
    async def test_several_fields_in_one_call(self, nc_mcp: McpTestHelper, new_user: tuple[str, str]) -> None:
        user_id, _ = new_user
        result = json.loads(
            await nc_mcp.call(
                "update_user",
                user_id=user_id,
                display_name="Ada Lovelace",
                email="Ada@Example.com",
                quota="1 GB",
                language="de",
            )
        )
        assert result["displayname"] == "Ada Lovelace"
        assert result["language"] == "de"
        stored = await _user(nc_mcp, user_id)
        assert stored["displayname"] == "Ada Lovelace"
        assert stored["email"] == "ada@example.com"  # Nextcloud stores addresses lower-cased
        assert stored["quota"]["quota"] == 1024**3  # type: ignore[index]
        assert stored["language"] == "de"

    @pytest.mark.asyncio
    async def test_fields_not_passed_are_left_alone(self, nc_mcp: McpTestHelper, new_user: tuple[str, str]) -> None:
        user_id, _ = new_user
        await nc_mcp.call("update_user", user_id=user_id, display_name="Kept Name", language="fr")
        await nc_mcp.call("update_user", user_id=user_id, email="kept@example.com")
        stored = await _user(nc_mcp, user_id)
        assert (stored["displayname"], stored["language"], stored["email"]) == ("Kept Name", "fr", "kept@example.com")

    @pytest.mark.asyncio
    async def test_one_invalid_field_applies_nothing(self, nc_mcp: McpTestHelper, new_user: tuple[str, str]) -> None:
        user_id, _ = new_user
        with pytest.raises(ToolError) as exc:
            await nc_mcp.call(
                "update_user",
                user_id=user_id,
                display_name="Should Not Stick",
                email="not-an-email",
                groups=["mcp-test-grp-missing"],
            )
        assert "email: Invalid email address" in str(exc.value)
        assert "groups: Group mcp-test-grp-missing does not exist" in str(exc.value)
        assert (await _user(nc_mcp, user_id))["displayname"] == user_id

    @pytest.mark.asyncio
    async def test_empty_strings_clear_values(self, nc_mcp: McpTestHelper, new_user: tuple[str, str]) -> None:
        user_id, _ = new_user
        await nc_mcp.call("update_user", user_id=user_id, display_name="Temp", email="t@example.com", manager="admin")
        assert (await _user(nc_mcp, user_id))["manager"] == "admin"
        await nc_mcp.call("update_user", user_id=user_id, display_name="", email="", manager="")
        stored = await _user(nc_mcp, user_id)
        assert stored["displayname"] == user_id
        assert not stored["email"]
        assert stored["manager"] == ""

    @pytest.mark.asyncio
    async def test_groups_is_the_complete_list(
        self, nc_mcp: McpTestHelper, new_user: tuple[str, str], two_groups: tuple[str, str]
    ) -> None:
        user_id, _ = new_user
        first, second = two_groups
        await nc_mcp.call("update_user", user_id=user_id, groups=[first, second])
        assert sorted((await _user(nc_mcp, user_id))["groups"]) == sorted([first, second])  # type: ignore[arg-type]
        await nc_mcp.call("update_user", user_id=user_id, groups=[second])
        assert (await _user(nc_mcp, user_id))["groups"] == [second]
        await nc_mcp.call("update_user", user_id=user_id, groups=[])
        assert (await _user(nc_mcp, user_id))["groups"] == []

    @pytest.mark.asyncio
    async def test_subadmin_groups(
        self, nc_mcp: McpTestHelper, new_user: tuple[str, str], two_groups: tuple[str, str]
    ) -> None:
        user_id, _ = new_user
        first, _second = two_groups
        await nc_mcp.call("update_user", user_id=user_id, subadmin_groups=[first])
        assert (await _user(nc_mcp, user_id))["subadmin"] == [first]
        await nc_mcp.call("update_user", user_id=user_id, subadmin_groups=[])
        assert (await _user(nc_mcp, user_id))["subadmin"] == []

    @pytest.mark.asyncio
    async def test_quota_forms(self, nc_mcp: McpTestHelper, new_user: tuple[str, str]) -> None:
        user_id, _ = new_user
        await nc_mcp.call("update_user", user_id=user_id, quota="500 MB")
        assert (await _user(nc_mcp, user_id))["quota"]["quota"] == 500 * 1024**2  # type: ignore[index]
        await nc_mcp.call("update_user", user_id=user_id, quota="none")
        assert (await _user(nc_mcp, user_id))["quota"]["quota"] == -3  # type: ignore[index]  # -3 means unlimited
        with pytest.raises(ToolError, match="quota: "):
            await nc_mcp.call("update_user", user_id=user_id, quota="lots")

    @pytest.mark.asyncio
    async def test_password_change(self, nc_mcp: McpTestHelper, new_user: tuple[str, str]) -> None:
        user_id, old_password = new_user
        new_password = _password()
        await nc_mcp.call("update_user", user_id=user_id, password=new_password)
        assert await _can_log_in(user_id, new_password)
        assert not await _can_log_in(user_id, old_password)

    @pytest.mark.asyncio
    async def test_user_changes_own_account(self, nc_mcp: McpTestHelper, new_user: tuple[str, str]) -> None:
        """No admin rights and no old password are needed for one's own profile and password.

        Nextcloud 34 and 35 refuse the PATCH endpoint to regular users, so this goes through the
        per-key fallback; the password is changed last since the session ends with it.
        """
        user_id, password = new_user
        new_password = _password()
        async with _as_user(nc_mcp, user_id, password) as as_user:
            with pytest.raises(ToolError, match="Only admins and sub-admins can change quota, groups"):
                await as_user.call("update_user", user_id=user_id, display_name="Not Yet", quota="1 GB", groups=[])
            with pytest.raises(ToolError, match="at least a sub admin"):
                await as_user.call("update_user", user_id="admin", display_name="Hijacked")
            with pytest.raises(ToolError, match="email was rejected"):
                await as_user.call("update_user", user_id=user_id, email="not-an-email")
            result = json.loads(
                await as_user.call(
                    "update_user",
                    user_id=user_id,
                    display_name="Self Named",
                    email="self@example.com",
                    language="fr",
                    password=new_password,
                )
            )
        assert (result["displayname"], result["email"], result["language"]) == ("Self Named", "self@example.com", "fr")
        stored = await _user(nc_mcp, user_id)
        assert (stored["displayname"], stored["email"], stored["language"]) == ("Self Named", "self@example.com", "fr")
        assert (await _user(nc_mcp, "admin"))["displayname"] != "Hijacked"
        assert await _can_log_in(user_id, new_password)
        assert not await _can_log_in(user_id, password)

    @pytest.mark.asyncio
    async def test_own_account_with_login_name_in_other_case(self, nc_mcp: McpTestHelper) -> None:
        """Nextcloud accepts a login in any case, so the configured user can differ from the user ID."""
        user_id, password = f"mcp-test-U-{uuid.uuid4().hex[:8]}", _password()
        await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": password})
        try:
            async with _as_user(nc_mcp, user_id.lower(), password) as as_user:
                assert json.loads(await as_user.call("get_current_user"))["id"] == user_id
                result = json.loads(await as_user.call("update_user", user_id=user_id, display_name="Cased"))
            assert result["displayname"] == "Cased"
        finally:
            await nc_mcp.client.ocs_delete(f"cloud/users/{user_id}")

    @pytest.mark.asyncio
    async def test_own_empty_display_name_resets_to_user_id(
        self, nc_mcp: McpTestHelper, new_user: tuple[str, str]
    ) -> None:
        user_id, password = new_user
        async with _as_user(nc_mcp, user_id, password) as as_user:
            await as_user.call("update_user", user_id=user_id, display_name="Temporary")
            result = json.loads(await as_user.call("update_user", user_id=user_id, display_name=""))
        assert result["displayname"] == user_id

    @pytest.mark.asyncio
    async def test_app_password_survives_own_password_change(
        self, nc_mcp: McpTestHelper, new_user: tuple[str, str]
    ) -> None:
        user_id, password = new_user
        async with _as_user(nc_mcp, user_id, password) as as_user:
            app_password = (await as_user.client.ocs_get("core/getapppassword"))["apppassword"]
        async with _as_user(nc_mcp, user_id, app_password) as as_user:
            await as_user.call("update_user", user_id=user_id, password=_password())
            assert json.loads(await as_user.call("get_current_user"))["id"] == user_id

    @pytest.mark.asyncio
    async def test_admin_cannot_leave_admin_by_omission(
        self, nc_mcp: McpTestHelper, new_user: tuple[str, str], two_groups: tuple[str, str]
    ) -> None:
        """Nextcloud's PATCH would drop it; the tool refuses, as the per-group endpoint does."""
        user_id, password = new_user
        first, _second = two_groups
        await nc_mcp.call("update_user", user_id=user_id, groups=["admin"])
        async with _as_user(nc_mcp, user_id, password) as as_admin:
            with pytest.raises(ToolError, match='Refusing to take your own account out of the "admin" group'):
                await as_admin.call("update_user", user_id=user_id, groups=[first])
            assert (await _user(nc_mcp, user_id))["groups"] == ["admin"]
            await as_admin.call("update_user", user_id=user_id, groups=["admin", first])
        assert sorted((await _user(nc_mcp, user_id))["groups"]) == sorted(["admin", first])  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_subadmin_keeps_groups_they_do_not_administer(
        self, nc_mcp: McpTestHelper, new_user: tuple[str, str], two_groups: tuple[str, str]
    ) -> None:
        """Nextcloud skips those removals without an error, as the docstring warns."""
        subadmin_id, subadmin_password = new_user
        first, second = two_groups
        target = f"mcp-test-u-{uuid.uuid4().hex[:8]}"
        await nc_mcp.client.ocs_post("cloud/users", data={"userid": target, "password": _password()})
        try:
            await nc_mcp.call("update_user", user_id=target, groups=[first, second])
            await nc_mcp.call("update_user", user_id=subadmin_id, subadmin_groups=[first])
            async with _as_user(nc_mcp, subadmin_id, subadmin_password) as as_subadmin:
                result = json.loads(
                    await as_subadmin.call("update_user", user_id=target, display_name="By Subadmin", groups=[first])
                )
            assert result["displayname"] == "By Subadmin"
            assert sorted(result["groups"]) == sorted([first, second])
        finally:
            await nc_mcp.client.ocs_delete(f"cloud/users/{target}")

    @pytest.mark.asyncio
    async def test_user_id_with_space(self, nc_mcp: McpTestHelper) -> None:
        user_id = f"mcp test u {uuid.uuid4().hex[:8]}"
        await nc_mcp.client.ocs_post("cloud/users", data={"userid": user_id, "password": _password()})
        try:
            await nc_mcp.call("update_user", user_id=user_id, display_name="Spaced")
            assert (await _user(nc_mcp, user_id))["displayname"] == "Spaced"
        finally:
            await nc_mcp.call("delete_user", user_id=user_id)

    @pytest.mark.asyncio
    async def test_requires_a_field(self, nc_mcp: McpTestHelper, new_user: tuple[str, str]) -> None:
        with pytest.raises(ToolError, match="at least one field"):
            await nc_mcp.call("update_user", user_id=new_user[0])

    @pytest.mark.asyncio
    async def test_nonexistent_user_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"404|[Nn]ot found|does not exist"):
            await nc_mcp.call("update_user", user_id="mcp-test-nobody-xyz", display_name="x")


class TestSetUserEnabled:
    @pytest.mark.asyncio
    async def test_disable_blocks_login_and_app_passwords(
        self, nc_mcp: McpTestHelper, new_user: tuple[str, str]
    ) -> None:
        user_id, password = new_user
        async with _as_user(nc_mcp, user_id, password) as as_user:
            app_password = (await as_user.client.ocs_get("core/getapppassword"))["apppassword"]
        assert await _can_log_in(user_id, app_password)

        assert "disabled" in await nc_mcp.call("set_user_enabled", user_id=user_id, enabled=False)
        assert (await _user(nc_mcp, user_id))["enabled"] is False
        assert not await _can_log_in(user_id, password)
        assert not await _can_log_in(user_id, app_password)

        assert "enabled" in await nc_mcp.call("set_user_enabled", user_id=user_id, enabled=True)
        assert (await _user(nc_mcp, user_id))["enabled"] is True
        assert await _can_log_in(user_id, password)

    @pytest.mark.asyncio
    async def test_nonexistent_user_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="the user does not exist, or it is your own account"):
            await nc_mcp.call("set_user_enabled", user_id="mcp-test-nobody-xyz", enabled=False)

    @pytest.mark.asyncio
    async def test_own_account_raises(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="the user does not exist, or it is your own account"):
            await nc_mcp.call("set_user_enabled", user_id="admin", enabled=True)


class TestUserAdminPermissions:
    @pytest.mark.asyncio
    async def test_write_level_blocks_risky_changes(self, nc_mcp_write: McpTestHelper) -> None:
        """Refused before any request, which is why a user that does not exist is enough here."""
        no_groups: list[str] = []
        risky: dict[str, object] = {"password": "Mcp-Unused-1!", "groups": no_groups, "subadmin_groups": no_groups}
        for field, value in risky.items():
            with pytest.raises(ToolError, match="requires 'destructive' permission"):
                await nc_mcp_write.call("update_user", user_id="mcp-test-nobody-xyz", **{field: value})

    @pytest.mark.asyncio
    async def test_read_only_blocks_update(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("update_user", user_id="admin", display_name="x")

    @pytest.mark.asyncio
    async def test_write_level_blocks_disable(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match="requires 'destructive' permission"):
            await nc_mcp_write.call("set_user_enabled", user_id="mcp-test-nobody-xyz", enabled=False)

    @pytest.mark.asyncio
    async def test_read_only_blocks_enable(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("set_user_enabled", user_id="admin", enabled=True)
