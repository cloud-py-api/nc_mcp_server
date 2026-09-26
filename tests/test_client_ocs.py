"""Tests for NextcloudClient.ocs_get response handling."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import niquests
import pytest

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config


def _response(status_code: int, body: object = None) -> niquests.Response:
    resp = niquests.Response()
    resp.status_code = status_code
    if body is None:
        resp._content = b""
    else:
        resp._content = json.dumps({"ocs": {"meta": {"status": "ok", "statuscode": 200}, "data": body}}).encode("utf-8")
        resp.headers["Content-Type"] = "application/json"
    return resp


def _client_returning(response: niquests.Response) -> NextcloudClient:
    client = NextcloudClient(Config(nextcloud_url="http://nc.test", user="admin", password="secret"))
    session = MagicMock()
    session.request = AsyncMock(return_value=response)
    session.auth = ("admin", "secret")
    client._session = session
    return client


class TestOcsGet:
    @pytest.mark.asyncio
    async def test_returns_data_portion(self) -> None:
        client = _client_returning(_response(200, [{"id": 1}]))
        assert await client.ocs_get("apps/spreed/api/v1/chat/abc123") == [{"id": 1}]

    @pytest.mark.asyncio
    async def test_not_modified_returns_none(self) -> None:
        """Talk answers 304 with an empty body; decoding it as JSON would fail."""
        client = _client_returning(_response(304))
        assert await client.ocs_get("apps/spreed/api/v1/chat/abc123") is None


class TestFreshLogin:
    @pytest.mark.asyncio
    async def test_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_returning(_response(500))
        fresh = MagicMock()
        fresh.request = AsyncMock(return_value=_response(200, [{"id": "1"}]))
        fresh.close = AsyncMock()
        monkeypatch.setattr(client, "_build_session", lambda: fresh)
        assert await client.ocs_get("apps/files_sharing/api/v1/remote_shares", fresh_login=True) == [{"id": "1"}]
        fresh.request.assert_awaited_once_with(
            "GET", "http://nc.test/ocs/v2.php/apps/files_sharing/api/v1/remote_shares", params={}
        )
        client._session.request.assert_not_awaited()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_renew_session_closes_an_idle_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_returning(_response(200, []))
        old = client._session
        old.close = AsyncMock()  # type: ignore[union-attr]
        renewed = MagicMock()
        init = AsyncMock()
        monkeypatch.setattr(client, "_build_session", lambda: renewed)
        monkeypatch.setattr(client, "_init_session_auth", init)
        await client.renew_session()
        init.assert_awaited_once_with(renewed)
        assert client._session is renewed
        old.close.assert_awaited_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_session_swaps_only_after_the_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_returning(_response(200, []))
        old = client._session
        old.close = AsyncMock()  # type: ignore[union-attr]
        seen: list[object] = []

        async def init(session: object) -> None:
            seen.append(client._session)

        monkeypatch.setattr(client, "_build_session", MagicMock)
        monkeypatch.setattr(client, "_init_session_auth", init)
        await client.renew_session()
        assert seen == [old]
        assert client._session is not old


class TestReplacedSessions:
    """A session replaced while other calls still wait on it is closed when their last request finishes."""

    @staticmethod
    def _client(monkeypatch: pytest.MonkeyPatch) -> tuple[NextcloudClient, MagicMock, MagicMock, asyncio.Event]:
        client = NextcloudClient(Config(nextcloud_url="http://nc.test", user="admin", password="secret"))
        release = asyncio.Event()

        async def slow(*_args: object, **_kwargs: object) -> niquests.Response:
            await release.wait()
            return _response(200, [])

        old, renewed = MagicMock(), MagicMock()
        old.request = AsyncMock(side_effect=slow)
        old.auth = None
        old.close = AsyncMock()
        renewed.request = AsyncMock(return_value=_response(200, []))
        renewed.close = AsyncMock()
        client._session = old
        monkeypatch.setattr(client, "_build_session", lambda: renewed)
        monkeypatch.setattr(client, "_init_session_auth", AsyncMock())
        return client, old, renewed, release

    @pytest.mark.asyncio
    async def test_closed_after_the_last_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, old, renewed, release = self._client(monkeypatch)
        pending = asyncio.create_task(client.ocs_get("slow"))
        await asyncio.sleep(0)
        await client.renew_session()
        assert client._session is renewed
        old.close.assert_not_awaited()
        assert await client.ocs_get("fast") == []
        release.set()
        assert await pending == []
        old.close.assert_awaited_once()
        assert client._retired_sessions == []
        assert client._in_flight == {}

    @pytest.mark.asyncio
    async def test_client_close_closes_a_busy_replaced_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, old, renewed, release = self._client(monkeypatch)
        pending = asyncio.create_task(client.ocs_get("slow"))
        await asyncio.sleep(0)
        await client.renew_session()
        await client.close()
        old.close.assert_awaited_once()
        renewed.close.assert_awaited_once()
        release.set()
        await pending
        old.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_401_on_an_already_replaced_session_retries_on_the_new_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, stale, renewed, _ = self._client(monkeypatch)
        client._session = renewed
        renewed.auth = None
        build = MagicMock()
        monkeypatch.setattr(client, "_build_session", build)
        assert await client._should_retry_auth(_response(401), stale) is True
        build.assert_not_called()
        assert client._session is renewed

    @pytest.mark.asyncio
    async def test_concurrent_401s_share_one_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, expired, renewed, _ = self._client(monkeypatch)
        login_started, finish_login = asyncio.Event(), asyncio.Event()

        async def slow_login(session: object) -> None:
            login_started.set()
            await finish_login.wait()

        build = MagicMock(return_value=renewed)
        monkeypatch.setattr(client, "_build_session", build)
        monkeypatch.setattr(client, "_init_session_auth", slow_login)
        first = asyncio.create_task(client._should_retry_auth(_response(401), expired))
        await login_started.wait()
        second = asyncio.create_task(client._should_retry_auth(_response(401), expired))
        await asyncio.sleep(0)
        finish_login.set()
        assert await first is True
        assert await second is True
        build.assert_called_once()
        assert client._session is renewed

    @pytest.mark.asyncio
    async def test_failed_login_closes_the_new_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, current, _, _ = self._client(monkeypatch)
        fresh = MagicMock()
        fresh.close = AsyncMock()
        monkeypatch.setattr(client, "_build_session", lambda: fresh)
        monkeypatch.setattr(client, "_init_session_auth", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await client.renew_session()
        fresh.close.assert_awaited_once()
        assert client._session is current
        current.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_renew_session_always_logs_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _, renewed, _ = self._client(monkeypatch)
        client._session = renewed
        fresh = MagicMock()
        fresh.close = AsyncMock()
        monkeypatch.setattr(client, "_build_session", lambda: fresh)
        await client.renew_session()
        assert client._session is fresh

    @pytest.mark.asyncio
    async def test_401_with_basic_auth_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, old, _, _ = self._client(monkeypatch)
        old.auth = ("admin", "secret")
        assert await client._should_retry_auth(_response(401), old) is False


class TestOcsDelete:
    @pytest.mark.asyncio
    async def test_uses_the_session(self) -> None:
        client = _client_returning(_response(200, []))
        assert await client.ocs_delete("apps/files_sharing/api/v1/shares/1") == []
        client._session.request.assert_awaited_once_with(  # type: ignore[union-attr]
            "DELETE", "http://nc.test/ocs/v2.php/apps/files_sharing/api/v1/shares/1"
        )

    @pytest.mark.asyncio
    async def test_fresh_login_bypasses_the_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client_returning(_response(500))
        fresh = MagicMock()
        fresh.request = AsyncMock(return_value=_response(200, []))
        fresh.close = AsyncMock()
        monkeypatch.setattr(client, "_build_session", lambda: fresh)
        assert await client.ocs_delete("apps/files_sharing/api/v1/remote_shares/7", fresh_login=True) == []
        fresh.request.assert_awaited_once_with(
            "DELETE", "http://nc.test/ocs/v2.php/apps/files_sharing/api/v1/remote_shares/7"
        )
        fresh.close.assert_awaited_once()
        client._session.request.assert_not_awaited()  # type: ignore[union-attr]
