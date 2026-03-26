"""Integration tests for file sharing tools against a real Nextcloud instance."""

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from .conftest import TEST_BASE_DIR, McpTestHelper

pytestmark = pytest.mark.integration

SHARE_DIR = f"{TEST_BASE_DIR}/share-test"
SHARE_FILE = f"{SHARE_DIR}/shared.txt"


async def _setup_share_file(nc_mcp: McpTestHelper) -> None:
    """Create the test directory and file for sharing tests."""
    await nc_mcp.create_test_dir()
    await nc_mcp.create_test_dir(SHARE_DIR)
    await nc_mcp.upload_test_file(SHARE_FILE, "share test content")


class TestListShares:
    @pytest.mark.asyncio
    async def test_returns_json_list(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_shares")
        data = json.loads(result)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_empty_when_no_shares(self, nc_mcp: McpTestHelper) -> None:
        result = await nc_mcp.call("list_shares")
        data = json.loads(result)
        assert data == []

    @pytest.mark.asyncio
    async def test_shows_created_share(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3)
        result = await nc_mcp.call("list_shares")
        data = json.loads(result)
        assert len(data) >= 1
        assert any(s["share_type"] == 3 for s in data)

    @pytest.mark.asyncio
    async def test_filter_by_path(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3)
        result = await nc_mcp.call("list_shares", path=f"/{SHARE_FILE}")
        data = json.loads(result)
        assert len(data) >= 1
        assert all(SHARE_FILE.rsplit("/", maxsplit=1)[-1] in str(s.get("path", "")) for s in data)

    @pytest.mark.asyncio
    async def test_share_has_required_fields(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3)
        result = await nc_mcp.call("list_shares")
        data = json.loads(result)
        share = data[0]
        for field in ["id", "share_type", "path", "permissions", "uid_owner"]:
            assert field in share, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_subfiles_lists_shares_inside_folder(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3)
        result = await nc_mcp.call("list_shares", path=f"/{SHARE_DIR}", subfiles=True)
        data = json.loads(result)
        assert len(data) >= 1


class TestGetShare:
    @pytest.mark.asyncio
    async def test_get_existing_share(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])

        result = await nc_mcp.call("get_share", share_id=share_id)
        share = json.loads(result)
        assert share["id"] == str(share_id)
        assert share["share_type"] == 3

    @pytest.mark.asyncio
    async def test_get_share_returns_link_url(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])

        result = await nc_mcp.call("get_share", share_id=share_id)
        share = json.loads(result)
        assert "url" in share
        assert "token" in share

    @pytest.mark.asyncio
    async def test_get_nonexistent_share_fails(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("get_share", share_id=999999)


class TestCreateShare:
    @pytest.mark.asyncio
    async def test_create_public_link(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        result = await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3)
        share = json.loads(result)
        assert share["share_type"] == 3
        assert "url" in share
        assert "token" in share
        assert share["id"] is not None

    @pytest.mark.asyncio
    async def test_create_link_with_password(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        result = await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, password="s3Cr3t!Pw9#xK")
        share = json.loads(result)
        assert share["share_type"] == 3
        assert share.get("has_password") is True

    @pytest.mark.asyncio
    async def test_create_link_with_expiration(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        result = await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, expire_date="2099-12-31")
        share = json.loads(result)
        assert share["expiration"] is not None
        assert "2099-12-31" in str(share["expiration"])

    @pytest.mark.asyncio
    async def test_create_link_with_label(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        result = await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, label="My Link")
        share = json.loads(result)
        assert share["label"] == "My Link"

    @pytest.mark.asyncio
    async def test_create_link_with_note(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        result = await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, note="Please review")
        share = json.loads(result)
        assert share["note"] == "Please review"

    @pytest.mark.asyncio
    async def test_create_link_read_only_permissions(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        result = await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, permissions=1)
        share = json.loads(result)
        assert share["permissions"] & 1, "Should have read permission"

    @pytest.mark.asyncio
    async def test_create_link_with_all_options(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        result = await nc_mcp.call(
            "create_share",
            path=f"/{SHARE_FILE}",
            share_type=3,
            password="s3Cr3t!Pw9#xK",
            expire_date="2099-12-31",
            note="Review this",
            label="Full options link",
        )
        share = json.loads(result)
        assert share["share_type"] == 3
        assert share.get("has_password") is True
        assert "2099-12-31" in str(share["expiration"])
        assert share["note"] == "Review this"
        assert share["label"] == "Full options link"

    @pytest.mark.asyncio
    async def test_create_share_nonexistent_path_fails(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("create_share", path="/nonexistent-file-abc.txt", share_type=3)

    @pytest.mark.asyncio
    async def test_create_folder_link_with_public_upload(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        result = await nc_mcp.call("create_share", path=f"/{SHARE_DIR}", share_type=3, public_upload=True)
        share = json.loads(result)
        assert share["share_type"] == 3
        perms = share["permissions"]
        assert perms & 4, "Should have create permission for public upload"

    @pytest.mark.asyncio
    async def test_create_multiple_links_same_file(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        s1 = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        s2 = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        assert s1["id"] != s2["id"]
        assert s1["token"] != s2["token"]


class TestUpdateShare:
    @pytest.mark.asyncio
    async def test_update_permissions(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_DIR}", share_type=3, public_upload=True))
        share_id = int(created["id"])
        original_perms = created["permissions"]
        assert original_perms & 4, "Should have create permission from public_upload"
        result = await nc_mcp.call("update_share", share_id=share_id, permissions=1)
        updated = json.loads(result)
        assert not (updated["permissions"] & 4), "Create permission should be removed"

    @pytest.mark.asyncio
    async def test_update_note(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])
        result = await nc_mcp.call("update_share", share_id=share_id, note="Updated note")
        updated = json.loads(result)
        assert updated["note"] == "Updated note"

    @pytest.mark.asyncio
    async def test_update_expire_date(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])
        result = await nc_mcp.call("update_share", share_id=share_id, expire_date="2099-06-15")
        updated = json.loads(result)
        assert updated["expiration"] is not None
        assert "2099-06-15" in str(updated["expiration"])

    @pytest.mark.asyncio
    async def test_update_label(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])
        result = await nc_mcp.call("update_share", share_id=share_id, label="New Label")
        updated = json.loads(result)
        assert updated["label"] == "New Label"

    @pytest.mark.asyncio
    async def test_update_password(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])
        result = await nc_mcp.call("update_share", share_id=share_id, password="n3wP@ss!xK7#mZ")
        updated = json.loads(result)
        assert updated.get("has_password") is True

    @pytest.mark.asyncio
    async def test_update_remove_password(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(
            await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, password="s3Cr3t!Pw9#xK")
        )
        share_id = int(created["id"])
        assert created.get("has_password") is True
        result = await nc_mcp.call("update_share", share_id=share_id, password="")
        updated = json.loads(result)
        assert updated.get("has_password") is not True

    @pytest.mark.asyncio
    async def test_update_remove_expiration(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(
            await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, expire_date="2099-12-31")
        )
        share_id = int(created["id"])
        assert created["expiration"] is not None
        result = await nc_mcp.call("update_share", share_id=share_id, expire_date="")
        updated = json.loads(result)
        assert updated["expiration"] is None

    @pytest.mark.asyncio
    async def test_update_clear_note(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(
            await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, note="initial note")
        )
        share_id = int(created["id"])
        assert created["note"] == "initial note"
        result = await nc_mcp.call("update_share", share_id=share_id, note="")
        updated = json.loads(result)
        assert updated["note"] == ""

    @pytest.mark.asyncio
    async def test_update_clear_label(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(
            await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3, label="Initial Label")
        )
        share_id = int(created["id"])
        assert created["label"] == "Initial Label"
        result = await nc_mcp.call("update_share", share_id=share_id, label="")
        updated = json.loads(result)
        assert updated["label"] == ""

    @pytest.mark.asyncio
    async def test_update_enable_hide_download(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])
        result = await nc_mcp.call("update_share", share_id=share_id, hide_download=True)
        updated = json.loads(result)
        assert updated.get("hide_download") == 1

    @pytest.mark.asyncio
    async def test_update_disable_hide_download(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])
        await nc_mcp.call("update_share", share_id=share_id, hide_download=True)
        result = await nc_mcp.call("update_share", share_id=share_id, hide_download=False)
        updated = json.loads(result)
        assert updated["hide_download"] == 0

    @pytest.mark.asyncio
    async def test_update_disable_public_upload(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_DIR}", share_type=3, public_upload=True))
        share_id = int(created["id"])
        assert created["permissions"] & 4, "Should have create permission"
        result = await nc_mcp.call("update_share", share_id=share_id, public_upload=False)
        updated = json.loads(result)
        assert not (updated["permissions"] & 4), "Create permission should be removed"

    @pytest.mark.asyncio
    async def test_update_nonexistent_share_fails(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("update_share", share_id=999999, note="test")


class TestDeleteShare:
    @pytest.mark.asyncio
    async def test_delete_share(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])

        result = await nc_mcp.call("delete_share", share_id=share_id)
        assert str(share_id) in result

        remaining = json.loads(await nc_mcp.call("list_shares"))
        assert not any(str(s.get("id")) == str(share_id) for s in remaining)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_share_fails(self, nc_mcp: McpTestHelper) -> None:
        with pytest.raises(ToolError):
            await nc_mcp.call("delete_share", share_id=999999)

    @pytest.mark.asyncio
    async def test_delete_does_not_delete_file(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        created = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        share_id = int(created["id"])
        await nc_mcp.call("delete_share", share_id=share_id)
        file_result = await nc_mcp.call("get_file", path=f"/{SHARE_FILE}")
        assert "share test content" in file_result

    @pytest.mark.asyncio
    async def test_delete_one_share_keeps_others(self, nc_mcp: McpTestHelper) -> None:
        await _setup_share_file(nc_mcp)
        s1 = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        s2 = json.loads(await nc_mcp.call("create_share", path=f"/{SHARE_FILE}", share_type=3))
        await nc_mcp.call("delete_share", share_id=int(s1["id"]))

        remaining = json.loads(await nc_mcp.call("list_shares"))
        remaining_ids = [str(s.get("id")) for s in remaining]
        assert str(s1["id"]) not in remaining_ids
        assert str(s2["id"]) in remaining_ids


class TestSharePermissionEnforcement:
    @pytest.mark.asyncio
    async def test_list_shares_allowed_read_only(self, nc_mcp_read_only: McpTestHelper) -> None:
        result = await nc_mcp_read_only.call("list_shares")
        data = json.loads(result)
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_create_share_denied_read_only(self, nc_mcp_read_only: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_read_only.call("create_share", path="/test.txt", share_type=3)

    @pytest.mark.asyncio
    async def test_delete_share_denied_write(self, nc_mcp_write: McpTestHelper) -> None:
        with pytest.raises(ToolError, match=r"[Pp]ermission"):
            await nc_mcp_write.call("delete_share", share_id=1)

    @pytest.mark.asyncio
    async def test_create_share_allowed_write(self, nc_mcp_write: McpTestHelper) -> None:
        await nc_mcp_write.client.dav_mkcol(TEST_BASE_DIR)
        await nc_mcp_write.client.dav_put(f"{TEST_BASE_DIR}/perm-test.txt", b"test", content_type="text/plain")
        result = await nc_mcp_write.call("create_share", path=f"/{TEST_BASE_DIR}/perm-test.txt", share_type=3)
        share = json.loads(result)
        assert share["share_type"] == 3
