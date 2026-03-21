"""Integration tests for user operations against a real Nextcloud instance."""

import pytest

from nextcloud_mcp.client import NextcloudClient

pytestmark = pytest.mark.integration


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_returns_user_data(self, nc_client: NextcloudClient) -> None:
        data = await nc_client.ocs_get("cloud/user")
        assert "id" in data
        assert data["id"] == "admin"

    @pytest.mark.asyncio
    async def test_has_display_name(self, nc_client: NextcloudClient) -> None:
        data = await nc_client.ocs_get("cloud/user")
        assert "displayname" in data


class TestListUsers:
    @pytest.mark.asyncio
    async def test_list_includes_admin(self, nc_client: NextcloudClient) -> None:
        data = await nc_client.ocs_get("cloud/users")
        assert "users" in data
        assert "admin" in data["users"]


class TestGetUser:
    @pytest.mark.asyncio
    async def test_get_admin(self, nc_client: NextcloudClient) -> None:
        data = await nc_client.ocs_get("cloud/users/admin")
        assert data["id"] == "admin"
        assert "displayname" in data
