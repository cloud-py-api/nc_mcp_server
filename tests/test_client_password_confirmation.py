"""Tests for repeating a request as a fresh login when Nextcloud wants the password confirmed.

Regression for guarded actions (creating users, enabling apps, changing flows) failing on a server that
runs longer than 30 minutes, and strict ones failing always: the cached session's confirmation expires,
and the session never sends the password header strict endpoints require.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import niquests
import pytest

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config

URL = "http://localhost/ocs/v2.php/cloud/apps/weather_status"


def _response(status_code: int, message: str = "", raw: bytes | None = None) -> niquests.Response:
    resp = niquests.Response()
    resp.status_code = status_code
    meta = {"status": "failure", "statuscode": status_code, "message": message}
    body: dict[str, Any] = {"ocs": {"meta": meta, "data": []}}
    resp._content = json.dumps(body).encode() if raw is None else raw
    return resp


def _client(cached: bool, first: niquests.Response) -> tuple[NextcloudClient, MagicMock, MagicMock]:
    client = NextcloudClient(Config(nextcloud_url="http://localhost", user="admin", password="secret"))
    session = MagicMock()
    session.request = AsyncMock(return_value=first)
    session.auth = None if cached else ("admin", "secret")
    client._session = session
    fresh = MagicMock()
    fresh.request = AsyncMock(return_value=_response(200))
    fresh.close = AsyncMock()
    client._build_session = MagicMock(return_value=fresh)  # type: ignore[method-assign]
    return client, session, fresh


class TestPasswordConfirmationRetry:
    @pytest.mark.parametrize("message", ["Password confirmation is required", "Required authorization header missing"])
    @pytest.mark.asyncio
    async def test_cached_session_repeats_the_request_as_a_fresh_login(self, message: str) -> None:
        client, session, fresh = _client(cached=True, first=_response(403, message))
        response = await client._do_request("POST", URL, json={"a": 1})
        assert response.status_code == 200
        session.request.assert_awaited_once_with("POST", URL, json={"a": 1})
        fresh.request.assert_awaited_once_with("POST", URL, json={"a": 1})
        fresh.close.assert_awaited_once()
        assert client._session is session  # the cached session stays in use for everything else

    @pytest.mark.asyncio
    async def test_fresh_session_is_closed_when_the_retry_fails(self) -> None:
        client, _, fresh = _client(cached=True, first=_response(403, "Password confirmation is required"))
        fresh.request.side_effect = OSError("connection reset")
        with pytest.raises(OSError, match="connection reset"):
            await client._do_request("DELETE", URL)
        fresh.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_basic_auth_session_is_not_retried(self) -> None:
        """Every request of an uncached session is a login already; repeating it cannot help."""
        client, session, fresh = _client(cached=False, first=_response(403, "Password confirmation is required"))
        response = await client._do_request("POST", URL)
        assert response.status_code == 403
        session.request.assert_awaited_once()
        fresh.request.assert_not_awaited()

    @pytest.mark.parametrize(
        "response",
        [_response(403, "Logged in account must be an admin"), _response(200), _response(404, "Not found")],
    )
    @pytest.mark.asyncio
    async def test_other_answers_are_returned_as_they_are(self, response: niquests.Response) -> None:
        client, _, fresh = _client(cached=True, first=response)
        assert await client._do_request("GET", URL) is response
        fresh.request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retried_confirmation_failure_is_returned(self) -> None:
        """A wrong password or an app password still fails; the caller sees that answer, not a loop."""
        client, _, fresh = _client(cached=True, first=_response(403, "Password confirmation is required"))
        fresh.request.return_value = _response(403, "Password confirmation is required")
        response = await client._do_request("POST", URL)
        assert response.status_code == 403
        fresh.request.assert_awaited_once()


class TestRecognisingTheRefusal:
    @pytest.mark.asyncio
    async def test_plain_json_route_message(self) -> None:
        """Routes outside OCS (app JSON routes) answer {"message": ...}."""
        first = _response(403, raw=b'{"message":"Required authorization header missing"}')
        client, _, fresh = _client(cached=True, first=first)
        assert (await client._do_request("POST", URL)).status_code == 200
        fresh.request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_marker_header(self) -> None:
        first = _response(403, raw=b"<html>denied</html>")
        first.headers["X-NC-Auth-NotConfirmed"] = "true"
        client, _, fresh = _client(cached=True, first=first)
        assert (await client._do_request("POST", URL)).status_code == 200
        fresh.request.assert_awaited_once()

    @pytest.mark.parametrize(
        "raw",
        [
            b"<d:error><s:message>Password confirmation is required.txt is locked</s:message></d:error>",
            b'{"ocs": {"meta": {"message": "Forbidden"}, "data": {"note": "Password confirmation is required"}}}',
            b"",
        ],
    )
    @pytest.mark.asyncio
    async def test_the_phrase_elsewhere_in_a_403_is_not_enough(self, raw: bytes) -> None:
        client, _, fresh = _client(cached=True, first=_response(403, raw=raw))
        assert (await client._do_request("GET", URL)).status_code == 403
        fresh.request.assert_not_awaited()


class TestWithTheExpiredSessionRetry:
    @pytest.mark.asyncio
    async def test_expired_session_then_missing_confirmation(self) -> None:
        """A 401 first renews the cached session; a confirmation refusal after that still gets its fresh login."""
        client, session, _ = _client(cached=True, first=_response(401))
        renewed = MagicMock()
        renewed.request = AsyncMock(return_value=_response(403, "Password confirmation is required"))
        renewed.auth = None
        renewed.close = AsyncMock()
        fresh = MagicMock()
        fresh.request = AsyncMock(return_value=_response(200))
        fresh.close = AsyncMock()
        session.close = AsyncMock()
        client._build_session = MagicMock(side_effect=[renewed, fresh])  # type: ignore[method-assign]
        client._init_session_auth = AsyncMock()  # type: ignore[method-assign]
        response = await client._do_request("POST", URL)
        assert response.status_code == 200
        assert [m.request.await_count for m in (session, renewed, fresh)] == [1, 1, 1]
        assert client._session is renewed


class TestFreshSession:
    @pytest.mark.asyncio
    async def test_sends_credentials_and_no_cookies(self) -> None:
        """The login confirms the password, and without a cookie no session token blocks the IP bypass."""
        client = NextcloudClient(Config(nextcloud_url="http://localhost", user="admin", password="secret"))
        session = client._build_session()
        try:
            assert session.auth == ("admin", "secret")
            assert len(session.cookies) == 0
        finally:
            await session.close()
