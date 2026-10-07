"""Tests for multi-user mode (NEXTCLOUD_MCP_MULTIUSER): header parsing, client pool, middleware."""

import asyncio
import base64
import json
import threading
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

import nc_mcp_server.state as state_module
from nc_mcp_server.config import Config
from nc_mcp_server.multiuser import (
    AuthenticationRequiredError,
    Binding,
    ClientPool,
    Credentials,
    LoginRejectedError,
    NextcloudUnavailableError,
    bind,
    lowest,
    parse_basic_auth,
    parse_permission_cap,
)
from nc_mcp_server.permissions import PermissionLevel, get_permission_level, require_permission
from nc_mcp_server.server import create_http_app, create_server
from nc_mcp_server.state import get_client, get_config


def basic(login: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()


def mu_config(**overrides: Any) -> Config:
    values: dict[str, Any] = {
        "nextcloud_url": "http://nc.invalid",
        "multiuser": True,
        "permission_level": PermissionLevel.DESTRUCTIVE,
    }
    values.update(overrides)
    return Config(**values)


class TestParseBasicAuth:
    def test_valid(self) -> None:
        creds = parse_basic_auth(basic("alice", "pw:with:colons"))
        assert creds == Credentials("alice", "pw:with:colons")

    def test_scheme_case_insensitive(self) -> None:
        assert parse_basic_auth("basic " + base64.b64encode(b"a:b").decode()) == Credentials("a", "b")

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "Bearer abc",
            "Basic",
            "Basic !!!notbase64",
            "Basic " + base64.b64encode(b"no-colon").decode(),
            "Basic " + base64.b64encode(b":pw").decode(),
            "Basic " + base64.b64encode(b"user:").decode(),
            "Basic " + base64.b64encode(b"us\ner:pw").decode(),
            "Basic " + base64.b64encode(b"\xff\xfe:pw").decode(),
            "Basic " + "A" * 5000,
        ],
    )
    def test_rejects(self, value: str | None) -> None:
        assert parse_basic_auth(value) is None

    def test_repr_hides_password(self) -> None:
        assert "secret" not in repr(Credentials("alice", "secret"))


class TestPermissionCap:
    def test_parse(self) -> None:
        assert parse_permission_cap(None) is None
        assert parse_permission_cap(" ") is None
        assert parse_permission_cap("READ") is PermissionLevel.READ

    def test_parse_invalid(self) -> None:
        with pytest.raises(ValueError, match="admin"):
            parse_permission_cap("admin")

    def test_lowest_never_raises(self) -> None:
        assert lowest(PermissionLevel.READ, PermissionLevel.DESTRUCTIVE) is PermissionLevel.READ
        assert lowest(PermissionLevel.DESTRUCTIVE, PermissionLevel.READ) is PermissionLevel.READ
        assert lowest(PermissionLevel.WRITE, None) is PermissionLevel.WRITE


class TestConfig:
    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://cloud.example.com")
        monkeypatch.setenv("NEXTCLOUD_MCP_MULTIUSER", "true")
        monkeypatch.setenv("NEXTCLOUD_MCP_MULTIUSER_CACHE_SIZE", "5")
        monkeypatch.setenv("NEXTCLOUD_MCP_MULTIUSER_TTL", "30")
        monkeypatch.delenv("NEXTCLOUD_USER", raising=False)
        monkeypatch.delenv("NEXTCLOUD_PASSWORD", raising=False)
        config = Config.from_env()
        assert config.multiuser is True
        assert config.multiuser_cache_size == 5
        assert config.multiuser_ttl == 30.0
        config.validate()

    def test_default_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NEXTCLOUD_MCP_MULTIUSER", raising=False)
        assert Config.from_env().multiuser is False

    @pytest.mark.parametrize(
        ("name", "value"), [("NEXTCLOUD_MCP_MULTIUSER", "maybe"), ("NEXTCLOUD_MCP_MULTIUSER_TTL", "0")]
    )
    def test_invalid_values(self, monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError, match=name):
            Config.from_env()

    def test_validate_refuses_fallback_account(self) -> None:
        with pytest.raises(ValueError, match="unset NEXTCLOUD_USER"):
            mu_config(user="admin", password="admin").validate()

    def test_validate_needs_url(self) -> None:
        with pytest.raises(ValueError, match="NEXTCLOUD_URL"):
            mu_config(nextcloud_url="").validate()

    def test_auth_login(self) -> None:
        assert Config(user="uid").auth_login == "uid"
        assert Config(user="uid", login="a@example.com").auth_login == "a@example.com"


class FakeLookup:
    """User-ID lookup without Nextcloud: login 'bad' is rejected, 'down' is unavailable."""

    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[str] = []
        self.delay = delay

    async def __call__(self, base: Config, creds: Credentials) -> str:
        self.calls.append(creds.login)
        if self.delay:
            await asyncio.sleep(self.delay)
        if creds.login == "bad":
            raise LoginRejectedError("401")
        if creds.login == "down":
            raise NextcloudUnavailableError("HTTP 503")
        return "uid-" + creds.login.split("@")[0]


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class TestClientPool:
    async def test_one_client_per_login(self) -> None:
        pool = ClientPool(mu_config(), lookup=FakeLookup())
        a1, ca = await pool.get(Credentials("alice", "pa"))
        b1, cb = await pool.get(Credentials("bob", "pb"))
        a2, _ = await pool.get(Credentials("alice", "pa"))
        assert a1 is a2
        assert a1 is not b1
        assert (ca.user, ca.auth_login, ca.password) == ("uid-alice", "alice", "pa")
        assert (cb.user, cb.auth_login, cb.password) == ("uid-bob", "bob", "pb")
        assert ca.is_app_password is True
        await pool.close()

    async def test_other_password_is_other_client(self) -> None:
        pool = ClientPool(mu_config(), lookup=FakeLookup())
        a1, _ = await pool.get(Credentials("alice", "old"))
        a2, c2 = await pool.get(Credentials("alice", "new"))
        assert a1 is not a2
        assert c2.password == "new"
        await pool.close()

    async def test_login_name_differs_from_user_id(self) -> None:
        pool = ClientPool(mu_config(), lookup=FakeLookup())
        _, config = await pool.get(Credentials("alice@example.com", "pa"))
        assert config.user == "uid-alice"
        assert config.auth_login == "alice@example.com"
        await pool.close()

    async def test_concurrent_first_use_shares_lookup(self) -> None:
        lookup = FakeLookup(delay=0.05)
        pool = ClientPool(mu_config(), lookup=lookup)
        results = await asyncio.gather(*(pool.get(Credentials("alice", "pa")) for _ in range(5)))
        assert lookup.calls == ["alice"]
        assert len({id(c) for c, _ in results}) == 1
        await pool.close()

    async def test_rejected_login_not_cached(self) -> None:
        lookup = FakeLookup()
        pool = ClientPool(mu_config(), lookup=lookup)
        for _ in range(2):
            with pytest.raises(LoginRejectedError):
                await pool.get(Credentials("bad", "x"))
        assert lookup.calls == ["bad", "bad"]
        assert len(pool) == 0
        await pool.close()

    async def test_lru_eviction(self) -> None:
        lookup = FakeLookup()
        pool = ClientPool(mu_config(multiuser_cache_size=2), lookup=lookup, close_grace=0)
        await pool.get(Credentials("a", "1"))
        await pool.get(Credentials("b", "1"))
        await pool.get(Credentials("a", "1"))  # a is now the most recent
        await pool.get(Credentials("c", "1"))  # evicts b
        assert len(pool) == 2
        await pool.get(Credentials("a", "1"))
        await pool.get(Credentials("b", "1"))
        assert lookup.calls == ["a", "b", "c", "b"]
        await pool.close()

    async def test_idle_ttl(self) -> None:
        clock = FakeClock()
        lookup = FakeLookup()
        pool = ClientPool(mu_config(multiuser_ttl=60), lookup=lookup, clock=clock, close_grace=0)
        await pool.get(Credentials("a", "1"))
        clock.t += 30
        await pool.get(Credentials("a", "1"))  # refreshes last use
        clock.t += 59
        await pool.get(Credentials("a", "1"))
        clock.t += 61
        await pool.get(Credentials("a", "1"))
        assert lookup.calls == ["a", "a"]
        await pool.close()

    async def test_evicted_client_closed_after_grace(self) -> None:
        pool = ClientPool(mu_config(multiuser_cache_size=1), lookup=FakeLookup(), close_grace=0)
        client, _ = await pool.get(Credentials("a", "1"))
        closed = asyncio.Event()
        original = client.close

        async def close() -> None:
            closed.set()
            await original()

        client.close = close  # type: ignore[method-assign]
        await pool.get(Credentials("b", "1"))
        await asyncio.wait_for(closed.wait(), 1)
        await pool.close()

    async def test_close_closes_cached_and_retired_clients(self) -> None:
        pool = ClientPool(mu_config(multiuser_cache_size=1), lookup=FakeLookup(), close_grace=3600)
        closed: list[str] = []
        for login in ("a", "b"):
            client, _ = await pool.get(Credentials(login, "1"))
            original = client.close

            async def close(name: str = login, original: Any = original) -> None:
                closed.append(name)
                await original()

            client.close = close  # type: ignore[method-assign]
        assert len(pool) == 1  # "a" was evicted and waits out the grace period
        await pool.close()
        assert sorted(closed) == ["a", "b"]
        assert len(pool) == 0


class TestStateInMultiUserMode:
    def test_no_call_without_login(self) -> None:
        create_server(mu_config())
        try:
            with pytest.raises(AuthenticationRequiredError):
                get_client()
            with pytest.raises(AuthenticationRequiredError):
                get_config()
        finally:
            state_module.set_state(None, Config())

    async def test_bind(self) -> None:
        create_server(mu_config())
        pool = ClientPool(mu_config(), lookup=FakeLookup())
        try:
            client, config = await pool.get(Credentials("alice", "pa"))
            with bind(Binding(client=client, config=config, permission_cap=PermissionLevel.READ)):
                assert get_client() is client
                assert get_config().user == "uid-alice"
                assert get_permission_level() is PermissionLevel.READ
            with pytest.raises(AuthenticationRequiredError):
                get_client()
        finally:
            await pool.close()
            state_module.set_state(None, Config())


def _add_probe_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def probe_whoami() -> str:
        """Test tool: which login does this call run as?"""
        config = get_config()
        await asyncio.sleep(0.01)  # let concurrent requests interleave
        return json.dumps({"user": config.user, "login": config.auth_login, "client": id(get_client())})

    @mcp.tool()
    @require_permission(PermissionLevel.WRITE)
    async def probe_write() -> str:
        """Test tool that needs write permission."""
        return "written"


def _call(client: TestClient, tool: str, headers: dict[str, str]) -> tuple[int, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {}}}
    response = client.post(
        "/mcp",
        json=body,
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **headers},
    )
    if response.status_code != 200:
        return response.status_code, response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return 200, json.loads(line[5:])
    return 200, response.json()


def _result_text(payload: Any) -> str:
    return payload["result"]["content"][0]["text"]


@pytest.fixture
def mu_app_lookup(monkeypatch: pytest.MonkeyPatch) -> Any:
    config = mu_config()
    mcp = create_server(config)
    _add_probe_tools(mcp)
    lookup = FakeLookup()
    monkeypatch.setattr("nc_mcp_server.server.ClientPool", lambda cfg: ClientPool(cfg, lookup=lookup))  # pyright: ignore[reportUnknownLambdaType]
    app = create_http_app(mcp, config)
    with TestClient(app) as client:
        yield client, lookup
    state_module.set_state(None, Config())


@pytest.fixture
def mu_app(mu_app_lookup: Any) -> TestClient:
    return mu_app_lookup[0]


class TestMiddleware:
    def test_missing_header_is_401(self, mu_app: TestClient) -> None:
        response = mu_app.post("/mcp", json={})
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic")

    def test_malformed_header_is_401(self, mu_app: TestClient) -> None:
        status, _ = _call(mu_app, "probe_whoami", {"Authorization": "Bearer xyz"})
        assert status == 401

    def test_rejected_login_is_401(self, mu_app: TestClient) -> None:
        status, _ = _call(mu_app, "probe_whoami", {"Authorization": basic("bad", "x")})
        assert status == 401

    def test_nextcloud_down_is_502(self, mu_app: TestClient) -> None:
        status, _ = _call(mu_app, "probe_whoami", {"Authorization": basic("down", "x")})
        assert status == 502

    def test_invalid_permission_header_is_400(self, mu_app: TestClient) -> None:
        status, _ = _call(
            mu_app, "probe_whoami", {"Authorization": basic("alice", "pa"), "X-Nextcloud-MCP-Permissions": "root"}
        )
        assert status == 400

    def test_duplicate_authorization_is_401(self, mu_app: TestClient) -> None:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        response = mu_app.post(
            "/mcp",
            json=body,
            headers=[  # pyright: ignore[reportArgumentType]
                ("Authorization", basic("alice", "pa")),
                ("Authorization", basic("bob", "pb")),
                ("Accept", "application/json, text/event-stream"),
            ],
        )
        assert response.status_code == 401

    def test_tool_runs_as_request_login(self, mu_app: TestClient) -> None:
        status, payload = _call(mu_app, "probe_whoami", {"Authorization": basic("alice@example.com", "pa")})
        assert status == 200
        assert json.loads(_result_text(payload))["user"] == "uid-alice"
        assert json.loads(_result_text(payload))["login"] == "alice@example.com"

    def test_permission_cap_lowers_level(self, mu_app: TestClient) -> None:
        status, payload = _call(mu_app, "probe_write", {"Authorization": basic("alice", "pa")})
        assert (status, _result_text(payload)) == (200, "written")
        status, payload = _call(
            mu_app, "probe_write", {"Authorization": basic("alice", "pa"), "X-Nextcloud-MCP-Permissions": "read"}
        )
        assert status == 200
        assert payload["result"]["isError"] is True
        assert "requires 'write' permission" in _result_text(payload)

    def test_parallel_logins_never_swap(self, mu_app: TestClient) -> None:
        errors: list[str] = []
        seen: dict[str, set[int]] = {"alice": set(), "bob": set()}

        def worker(login: str) -> None:
            for _ in range(15):
                status, payload = _call(mu_app, "probe_whoami", {"Authorization": basic(login, "pw-" + login)})
                if status != 200:
                    errors.append(f"{login}: HTTP {status}")
                    continue
                got = json.loads(_result_text(payload))
                if got["user"] != "uid-" + login or got["login"] != login:
                    errors.append(f"{login} got {got}")
                seen[login].add(got["client"])

        threads = [threading.Thread(target=worker, args=(name,)) for name in ("alice", "bob", "alice", "bob")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(seen["alice"]) == 1
        assert len(seen["bob"]) == 1
        assert seen["alice"] != seen["bob"]


_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}


def _payload(response: Any) -> Any:
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return response.json()


class TestLifespan:
    def test_shutdown_closes_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = mu_config()
        mcp = create_server(config)
        _add_probe_tools(mcp)
        pools: list[ClientPool] = []

        def make_pool(cfg: Config) -> ClientPool:
            pool = ClientPool(cfg, lookup=FakeLookup())
            pools.append(pool)
            return pool

        monkeypatch.setattr("nc_mcp_server.server.ClientPool", make_pool)
        closed = threading.Event()
        try:
            with TestClient(create_http_app(mcp, config)) as client:
                status, _ = _call(client, "probe_whoami", {"Authorization": basic("alice", "pa")})
                assert status == 200
                assert len(pools[0]) == 1

                original = pools[0].close

                async def close() -> None:
                    closed.set()
                    await original()

                pools[0].close = close  # type: ignore[method-assign]
            assert closed.is_set()
            assert len(pools[0]) == 0
        finally:
            state_module.set_state(None, Config())


class TestDiscoveryWithoutLogin:
    def test_initialize_and_tools_list_without_login(self, mu_app_lookup: Any) -> None:
        client, lookup = mu_app_lookup
        response = client.post("/mcp", json=_INIT, headers=_MCP_HEADERS)
        assert response.status_code == 200
        assert _payload(response)["result"]["serverInfo"]["name"] == "nc-mcp-server"
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert client.post("/mcp", json=note, headers=_MCP_HEADERS).status_code == 202
        response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=_MCP_HEADERS)
        assert response.status_code == 200
        names = {t["name"] for t in _payload(response)["result"]["tools"]}
        assert {"list_directory", "get_file", "probe_whoami"} <= names
        ping = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"}, headers=_MCP_HEADERS)
        assert ping.status_code == 200
        assert lookup.calls == []  # no Nextcloud call, no client

    def test_tools_call_without_login_is_401(self, mu_app_lookup: Any) -> None:
        client, lookup = mu_app_lookup
        status, _ = _call(client, "probe_whoami", {})
        assert status == 401
        assert lookup.calls == []

    @pytest.mark.parametrize(
        "body",
        [
            [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/call"}],
            [],
            {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "x"}},
            {"jsonrpc": "2.0", "id": 1},
            "tools/list",
        ],
    )
    def test_other_bodies_without_login_are_401(self, mu_app_lookup: Any, body: Any) -> None:
        client, _ = mu_app_lookup
        response = client.post("/mcp", json=body, headers=_MCP_HEADERS)
        assert response.status_code == 401

    def test_invalid_json_without_login_is_401(self, mu_app_lookup: Any) -> None:
        client, _ = mu_app_lookup
        response = client.post("/mcp", content=b"{not json", headers=_MCP_HEADERS)
        assert response.status_code == 401

    def test_oversized_body_without_login_is_401(self, mu_app_lookup: Any) -> None:
        client, _ = mu_app_lookup
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"pad": "x" * 70_000}}
        response = client.post("/mcp", json=body, headers=_MCP_HEADERS)
        assert response.status_code == 401

    def test_get_and_delete_without_login_are_405(self, mu_app_lookup: Any) -> None:
        client, _ = mu_app_lookup
        for method in ("GET", "DELETE"):
            response = client.request(method, "/mcp", headers=_MCP_HEADERS)
            assert response.status_code == 405
            assert response.headers["allow"] == "POST"

    @pytest.mark.parametrize("auth", ["", "Bearer xyz", "Basic !!!"])
    def test_wrong_header_is_never_downgraded_to_discovery(self, mu_app_lookup: Any, auth: str) -> None:
        client, _ = mu_app_lookup
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        response = client.post("/mcp", json=body, headers={**_MCP_HEADERS, "Authorization": auth})
        assert response.status_code == 401

    def test_rejected_login_is_not_downgraded(self, mu_app_lookup: Any) -> None:
        client, _ = mu_app_lookup
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        response = client.post("/mcp", json=body, headers={**_MCP_HEADERS, "Authorization": basic("bad", "x")})
        assert response.status_code == 401
