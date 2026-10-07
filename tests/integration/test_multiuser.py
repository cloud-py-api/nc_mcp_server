"""Multi-user mode against a real Nextcloud: two logins in parallel, never swapped; no login, no access.

Runs the real Streamable HTTP app with the login middleware (as ``--transport http`` does with
NEXTCLOUD_MCP_MULTIUSER=true). The users get app passwords from Nextcloud itself.
"""

import base64
import contextlib
import json
import secrets
import threading
from collections.abc import AsyncGenerator
from typing import Any

import niquests
import pytest
from starlette.testclient import TestClient

import nc_mcp_server.state as state_module
from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_http_app, create_server

from .conftest import _get_integration_config

pytestmark = pytest.mark.integration


class User:
    def __init__(self, user_id: str, login: str, app_password: str) -> None:
        self.user_id = user_id
        self.login = login
        self.app_password = app_password
        self.marker = f"marker-{user_id}.txt"

    def header(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.login}:{self.app_password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}


async def _app_password(base_url: str, login: str, password: str) -> str:
    async with niquests.AsyncSession(auth=(login, password), headers={"OCS-APIRequest": "true"}) as session:
        response = await session.get(f"{base_url}/ocs/v2.php/core/getapppassword", params={"format": "json"})
    assert response.ok, f"getapppassword: HTTP {response.status_code}"
    return response.json()["ocs"]["data"]["apppassword"]


@pytest.fixture
async def two_users() -> AsyncGenerator[tuple[User, User]]:
    """alice logs in with her user ID, bob with his e-mail address (login name != user ID)."""
    admin_config = _get_integration_config()
    admin = NextcloudClient(admin_config)
    base = admin_config.nextcloud_url
    suffix = secrets.token_hex(4)
    created: list[str] = []
    users: list[User] = []
    try:
        for name, by_mail in (("mu-alice", False), ("mu-bob", True)):
            user_id = f"{name}-{suffix}"
            password = f"Mcp-{secrets.token_hex(10)}!"
            email = f"{user_id}@example.com"
            await admin.ocs_post("cloud/users", data={"userid": user_id, "password": password, "email": email})
            created.append(user_id)
            login = email if by_mail else user_id
            app_password = await _app_password(base, login, password)
            user = User(user_id, login, app_password)
            own = NextcloudClient(
                Config(nextcloud_url=base, user=user_id, login=login, password=app_password, is_app_password=True)
            )
            try:
                await own.dav_put(user.marker, user_id.encode(), content_type="text/plain")
            finally:
                await own.close()
            users.append(user)
        yield users[0], users[1]
    finally:
        for user_id in created:
            with contextlib.suppress(Exception):  # best-effort cleanup
                await admin.ocs_delete(f"cloud/users/{user_id}")
        await admin.close()


@pytest.fixture
def mu_client() -> Any:
    base = _get_integration_config()
    config = Config(nextcloud_url=base.nextcloud_url, multiuser=True, permission_level=PermissionLevel.DESTRUCTIVE)
    config.validate()
    mcp = create_server(config)
    with TestClient(create_http_app(mcp, config)) as client:
        yield client
    state_module.set_state(None, Config())


def _call(client: TestClient, tool: str, arguments: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    response = client.post(
        "/mcp",
        json=body,
        headers={"Accept": "application/json, text/event-stream", **headers},
    )
    if response.status_code != 200:
        return response.status_code, None
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return 200, json.loads(line[5:])
    return 200, response.json()


def _names(payload: Any) -> set[str]:
    assert payload["result"].get("isError") is not True, payload
    data = json.loads(payload["result"]["content"][0]["text"])
    return {entry["path"].strip("/") for entry in data["data"]}


class TestMultiUser:
    def test_no_header_is_401(self, mu_client: TestClient) -> None:
        status, _ = _call(mu_client, "list_directory", {"path": "/"}, {})
        assert status == 401

    def test_wrong_app_password_is_401(self, mu_client: TestClient, two_users: tuple[User, User]) -> None:
        alice, _ = two_users
        wrong = User(alice.user_id, alice.login, "wrong-" + secrets.token_hex(8))
        status, _ = _call(mu_client, "list_directory", {"path": "/"}, wrong.header())
        assert status == 401

    def test_each_login_sees_own_files(self, mu_client: TestClient, two_users: tuple[User, User]) -> None:
        for user in two_users:
            status, payload = _call(mu_client, "list_directory", {"path": "/"}, user.header())
            assert status == 200
            names = _names(payload)
            others = {u.marker for u in two_users if u is not user}
            assert user.marker in names
            assert not names & others

    def test_parallel_logins_never_swap(self, mu_client: TestClient, two_users: tuple[User, User]) -> None:
        errors: list[str] = []

        def worker(user: User, other: User) -> None:
            for _ in range(8):
                status, payload = _call(mu_client, "list_directory", {"path": "/"}, user.header())
                if status != 200:
                    errors.append(f"{user.user_id}: HTTP {status}")
                    continue
                names = _names(payload)
                if user.marker not in names or other.marker in names:
                    errors.append(f"{user.user_id} saw {sorted(names)}")

        alice, bob = two_users
        threads = [
            threading.Thread(target=worker, args=args)
            for args in ((alice, bob), (bob, alice), (alice, bob), (bob, alice))
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_read_cap_blocks_writes(self, mu_client: TestClient, two_users: tuple[User, User]) -> None:
        alice, _ = two_users
        headers = {**alice.header(), "X-Nextcloud-MCP-Permissions": "read"}
        status, payload = _call(mu_client, "create_directory", {"path": "mu-should-not-exist"}, headers)
        assert status == 200
        assert payload["result"]["isError"] is True
        status, payload = _call(mu_client, "list_directory", {"path": "/"}, alice.header())
        assert "mu-should-not-exist" not in _names(payload)
