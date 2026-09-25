"""Unit tests for received shares: listing, accepting, declining and leaving them."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from nc_mcp_server.client import NextcloudError
from nc_mcp_server.permissions import PermissionLevel, set_permission_level
from nc_mcp_server.tools import shares

API = "apps/files_sharing/api/v1/shares"
REMOTE = "apps/files_sharing/api/v1/remote_shares"
DELETED = "apps/files_sharing/api/v1/deletedshares"
FED_ID = "133560228880171009"

LOCAL = {
    "id": "12",
    "share_type": 1,
    "uid_owner": "alice",
    "displayname_owner": "Alice",
    "permissions": 0,
    "path": "/report.txt",
    "item_type": "file",
    "mimetype": "text/plain",
    "share_with": "team",
    "share_with_displayname": "Team",
    "expiration": None,
    "note": "",
    "label": "",
    "token": None,
}
REMOTE_PENDING = {
    "id": FED_ID,
    "share_type": 0,
    "remote": "https://other.example/",
    "remote_id": "1150",
    "refresh_token": "secret-token",
    "share_token": "nc34-secret-token",
    "name": "/f1.txt",
    "owner": "bob",
    "user": "me",
    "mountpoint": "{{TemporaryMountPointName#/f1.txt}}",
    "accepted": 0,
    "mimetype": None,
    "permissions": None,
    "type": None,
}
REMOTE_ACCEPTED = {**REMOTE_PENDING, "accepted": 1, "mountpoint": "/Shared/f1.txt", "type": "file", "permissions": 27}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    set_permission_level(PermissionLevel.DESTRUCTIVE)
    mock = MagicMock()
    for method in ("ocs_get", "ocs_post", "ocs_delete", "renew_session"):
        setattr(mock, method, AsyncMock(return_value=[]))
    monkeypatch.setattr(shares, "get_client", lambda: mock)
    return mock


@pytest.fixture
def mcp(client: MagicMock) -> FastMCP:
    server = FastMCP("test-shares")
    shares.register(server)
    return server


async def _call(mcp: FastMCP, tool: str, **args: Any) -> Any:
    return await mcp._tool_manager.call_tool(tool, args)


def _get(answers: dict[str, list[dict[str, Any]]]) -> AsyncMock:
    async def get(path: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        return answers[path]

    return AsyncMock(side_effect=get)


class TestSharedWithMe:
    async def test_merges_federated_shares(self, mcp: FastMCP, client: MagicMock) -> None:
        received = {**LOCAL, "permissions": 19}
        client.ocs_get = _get({API: [received], REMOTE: [REMOTE_ACCEPTED]})
        result = json.loads(await _call(mcp, "list_shares", shared_with_me=True))
        assert client.ocs_get.await_args_list == [call(API, params={"shared_with_me": "true"}), call(REMOTE)]
        local, federated = result["data"]
        assert (local["id"], local["displayname_owner"], local["permissions"]) == ("12", "Alice", 19)
        assert federated == {
            "id": FED_ID,
            "share_type": 6,
            "path": "/Shared/f1.txt",
            "item_type": "file",
            "mimetype": None,
            "uid_owner": "bob",
            "remote": "https://other.example/",
            "federated": True,
            "permissions": 27,
        }
        assert "secret-token" not in json.dumps(result)

    async def test_path_of_a_mount_the_session_does_not_see(self, mcp: FastMCP, client: MagicMock) -> None:
        missing = NextcloudError("Wrong path, file/folder does not exist", 404)
        client.ocs_get.side_effect = [missing, [REMOTE_ACCEPTED]]
        result = json.loads(await _call(mcp, "list_shares", shared_with_me=True, path="/Shared/f1.txt"))
        assert [s["id"] for s in result["data"]] == [FED_ID]

    async def test_missing_path(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = [NextcloudError("Wrong path, file/folder does not exist", 404), []]
        with pytest.raises(ToolError, match="No file or folder at '/nope'"):
            await _call(mcp, "list_shares", shared_with_me=True, path="/nope")

    async def test_other_errors_are_raised(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = NextcloudError("Server error", 500)
        with pytest.raises(ToolError, match="Server error"):
            await _call(mcp, "list_shares", shared_with_me=True, path="/Shared/f1.txt")

    async def test_path_filters_federated_shares_by_mountpoint(self, mcp: FastMCP, client: MagicMock) -> None:
        other = {**REMOTE_ACCEPTED, "id": "7", "mountpoint": "/other.txt"}
        client.ocs_get = _get({API: [], REMOTE: [REMOTE_ACCEPTED, other]})
        result = json.loads(await _call(mcp, "list_shares", shared_with_me=True, path="Shared/f1.txt/"))
        params = {"path": "Shared/f1.txt/", "shared_with_me": "true"}
        assert client.ocs_get.await_args_list[0] == call(API, params=params)
        assert [s["id"] for s in result["data"]] == [FED_ID]

    async def test_remote_group_share_type(self) -> None:
        assert shares._format_remote_share({**REMOTE_ACCEPTED, "share_type": 1})["share_type"] == 9

    @pytest.mark.parametrize("flag", ["reshares", "subfiles"])
    async def test_cannot_combine(self, mcp: FastMCP, client: MagicMock, flag: str) -> None:
        with pytest.raises(ToolError, match="cannot be combined"):
            await _call(mcp, "list_shares", shared_with_me=True, **{flag: True})
        client.ocs_get.assert_not_awaited()

    async def test_own_shares_skip_federated(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get = _get({API: []})
        await _call(mcp, "list_shares")
        assert client.ocs_get.await_args_list == [call(API, params={})]


class TestPending:
    async def test_lists_local_and_federated(self, mcp: FastMCP, client: MagicMock) -> None:
        declined = {**LOCAL, "id": "13"}
        client.ocs_get = _get(
            {
                f"{API}/pending": [LOCAL, declined],
                f"{REMOTE}/pending": [REMOTE_PENDING],
                DELETED: [{"id": "ocinternal:13"}, {"id": "ocRoomShare:12"}],
            }
        )
        local, gone, federated = json.loads(await _call(mcp, "list_pending_shares"))
        assert "permissions" not in local
        assert (local["id"], local["federated"], local["declined"]) == ("12", False, False)
        assert local["mimetype"] == "text/plain"
        assert (gone["id"], gone["declined"]) == ("13", True)
        assert federated["path"] == "/f1.txt"
        assert (federated["federated"], federated["share_type"], federated["id"]) == (True, 6, FED_ID)
        assert "permissions" not in federated
        assert "token" not in json.dumps(federated)

    async def test_declined_lookup_is_best_effort(self, mcp: FastMCP, client: MagicMock) -> None:
        async def get(path: str) -> list[dict[str, Any]]:
            if path == DELETED:
                raise NextcloudError("Could not find the file", 404)
            return [LOCAL] if path == f"{API}/pending" else []

        client.ocs_get = AsyncMock(side_effect=get)
        [share] = json.loads(await _call(mcp, "list_pending_shares"))
        assert share["declined"] is False

    async def test_no_deleted_lookup_without_local_shares(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get = _get({f"{API}/pending": [], f"{REMOTE}/pending": [REMOTE_PENDING]})
        assert len(json.loads(await _call(mcp, "list_pending_shares"))) == 1
        assert call(DELETED) not in client.ocs_get.await_args_list


class TestAccept:
    async def test_local(self, mcp: FastMCP, client: MagicMock) -> None:
        result = await _call(mcp, "accept_share", share_id=" 12 ")
        client.ocs_post.assert_awaited_once_with(f"{API}/pending/12")
        assert result.startswith("Share 12 accepted.")

    async def test_federated_logs_in_again(self, mcp: FastMCP, client: MagicMock) -> None:
        other = {**REMOTE_ACCEPTED, "id": "5", "mountpoint": "/x"}
        client.ocs_get.side_effect = [[other], [other, REMOTE_ACCEPTED]]
        result = await _call(mcp, "accept_share", share_id=FED_ID, federated=True)
        assert client.method_calls == [
            call.ocs_get(REMOTE),
            call.ocs_post(f"{REMOTE}/pending/{FED_ID}"),
            call.ocs_get(REMOTE, fresh_login=True),
            call.renew_session(),
        ]
        assert result == f"Share {FED_ID} accepted, it is at /Shared/f1.txt in your files."

    async def test_federated_group_share_gets_a_new_id(self, mcp: FastMCP, client: MagicMock) -> None:
        mine = {**REMOTE_ACCEPTED, "id": "900", "parent": FED_ID, "share_type": 1}
        client.ocs_get.side_effect = [[], [mine]]
        result = await _call(mcp, "accept_share", share_id=FED_ID, federated=True)
        assert result == f"Share {FED_ID} accepted, it is at /Shared/f1.txt in your files."

    async def test_federated_already_accepted(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.return_value = [REMOTE_ACCEPTED]
        result = await _call(mcp, "accept_share", share_id=FED_ID, federated=True)
        assert result == f"Share {FED_ID} was already accepted, it is at /Shared/f1.txt in your files."
        client.ocs_post.assert_not_awaited()
        client.renew_session.assert_not_awaited()

    async def test_federated_lookup_failure_still_renews(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get.side_effect = [[], NextcloudError("Unauthorized", 401)]
        assert await _call(mcp, "accept_share", share_id="7", federated=True) == "Share 7 accepted."
        client.renew_session.assert_awaited_once()

    async def test_federated_without_a_mountpoint(self, mcp: FastMCP, client: MagicMock) -> None:
        assert await _call(mcp, "accept_share", share_id="7", federated=True) == "Share 7 accepted."

    async def test_declined_group_share_is_restored_first(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post.side_effect = [NextcloudError("Wrong share ID, share does not exist", 404), [], []]
        client.ocs_get = _get({DELETED: [{"id": "ocinternal:12"}]})
        await _call(mcp, "accept_share", share_id="12")
        assert client.ocs_post.await_args_list == [
            call(f"{API}/pending/12"),
            call(f"{DELETED}/ocinternal:12"),
            call(f"{API}/pending/12"),
        ]

    async def test_unknown_share_fails(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post.side_effect = NextcloudError("Wrong share ID, share does not exist", 404)
        client.ocs_get = _get({DELETED: [{"id": "ocinternal:99"}]})
        with pytest.raises(ToolError, match="does not exist"):
            await _call(mcp, "accept_share", share_id="12")
        client.ocs_post.assert_awaited_once()

    async def test_other_errors_skip_the_restore(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_post.side_effect = NextcloudError("Failed to accept share.", 400)
        with pytest.raises(ToolError, match="Failed to accept"):
            await _call(mcp, "accept_share", share_id="12")
        client.ocs_get.assert_not_awaited()

    @pytest.mark.parametrize("share_id", ["../1", "", "1 2", "²", "-1"])
    async def test_invalid_ids(self, mcp: FastMCP, client: MagicMock, share_id: str) -> None:
        with pytest.raises(ToolError, match="Invalid share_id"):
            await _call(mcp, "accept_share", share_id=share_id)
        client.ocs_post.assert_not_awaited()


class TestDecline:
    async def test_pending_local_share(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get = _get({f"{API}/pending": [LOCAL]})
        assert await _call(mcp, "decline_share", share_id="12") == "Share 12 declined."
        client.ocs_delete.assert_awaited_once_with(f"{API}/12")

    async def test_refuses_a_share_that_is_not_pending(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get = _get({f"{API}/pending": [LOCAL]})
        with pytest.raises(ToolError, match="not waiting to be accepted by you"):
            await _call(mcp, "decline_share", share_id="5")
        client.ocs_delete.assert_not_awaited()

    async def test_federated(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get = _get({f"{REMOTE}/pending": [REMOTE_PENDING]})
        await _call(mcp, "decline_share", share_id=FED_ID, federated=True)
        client.ocs_delete.assert_awaited_once_with(f"{REMOTE}/pending/{FED_ID}")

    async def test_federated_refuses_an_accepted_share(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get = _get({f"{REMOTE}/pending": []})
        with pytest.raises(ToolError, match="not waiting to be accepted by you"):
            await _call(mcp, "decline_share", share_id=FED_ID, federated=True)
        client.ocs_delete.assert_not_awaited()


class TestDelete:
    async def test_accepts_numbers_and_strings(self, mcp: FastMCP, client: MagicMock) -> None:
        await _call(mcp, "delete_share", share_id=12)
        await _call(mcp, "delete_share", share_id="13")
        assert client.ocs_delete.await_args_list == [call(f"{API}/12"), call(f"{API}/13")]

    async def test_leaving_a_federated_share_uses_a_fresh_login(self, mcp: FastMCP, client: MagicMock) -> None:
        assert await _call(mcp, "delete_share", share_id=FED_ID, federated=True) == f"Share {FED_ID} deleted."
        client.ocs_delete.assert_awaited_once_with(f"{REMOTE}/{FED_ID}", fresh_login=True)

    async def test_invalid_id(self, mcp: FastMCP, client: MagicMock) -> None:
        with pytest.raises(ToolError, match="Invalid share_id"):
            await _call(mcp, "delete_share", share_id="1/../2")
        client.ocs_delete.assert_not_awaited()


class TestPermissions:
    async def test_levels(self, mcp: FastMCP, client: MagicMock) -> None:
        client.ocs_get = _get({f"{API}/pending": [], f"{REMOTE}/pending": []})
        set_permission_level(PermissionLevel.READ)
        await _call(mcp, "list_pending_shares")
        with pytest.raises(ToolError, match="write"):
            await _call(mcp, "accept_share", share_id="1")
        set_permission_level(PermissionLevel.WRITE)
        await _call(mcp, "accept_share", share_id="1")
        with pytest.raises(ToolError, match="destructive"):
            await _call(mcp, "decline_share", share_id="1")
