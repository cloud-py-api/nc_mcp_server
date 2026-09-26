"""Tests for dav_put_stream — ensures the chunk factory survives auth retries.

Regression for the silent-truncate bug: _do_request's cached-session 401 retry
would send the retry with an exhausted AsyncIterable, producing a 0-byte PUT
that Nextcloud happily accepts. The factory pattern rebuilds the body per try.
"""

from collections.abc import AsyncIterable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import niquests
import pytest

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config


async def _collect(data: AsyncIterable[bytes]) -> bytes:
    buf = bytearray()
    async for chunk in data:
        buf.extend(chunk)
    return bytes(buf)


def _make_response(status_code: int) -> niquests.Response:
    resp = niquests.Response()
    resp.status_code = status_code
    return resp


def _make_client() -> NextcloudClient:
    return NextcloudClient(Config(nextcloud_url="http://localhost", user="admin", password="admin"))


class TestDavPutStreamAuthRetry:
    @pytest.mark.asyncio
    async def test_factory_called_once_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client()
        bodies: list[bytes] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> niquests.Response:
            bodies.append(await _collect(kwargs["data"]))
            return _make_response(201)

        session = MagicMock()
        session.request = AsyncMock(side_effect=fake_request)
        session.auth = ("admin", "admin")
        client._session = session

        factory_calls = 0

        async def chunks() -> AsyncIterable[bytes]:
            yield b"hello"
            yield b" world"

        def factory() -> AsyncIterable[bytes]:
            nonlocal factory_calls
            factory_calls += 1
            return chunks()

        await client.dav_put_stream("f.bin", factory, content_type="text/plain")

        assert factory_calls == 1
        assert bodies == [b"hello world"]
        assert session.request.await_count == 1

    @pytest.mark.asyncio
    async def test_factory_rebuilt_on_cached_session_401_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The critical regression: a 401 with a cached session must not replay an exhausted body."""
        client = _make_client()
        bodies: list[bytes] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> niquests.Response:
            bodies.append(await _collect(kwargs["data"]))
            return _make_response(401 if len(bodies) == 1 else 201)

        session = MagicMock()
        session.request = AsyncMock(side_effect=fake_request)
        session.auth = None  # marks the session as cached → _should_retry_auth fires on 401
        client._session = session

        async def _noop_reset(stale: object = None) -> None:
            return None

        monkeypatch.setattr(client, "_reset_session", _noop_reset)

        factory_calls = 0

        async def chunks() -> AsyncIterable[bytes]:
            yield b"first-"
            yield b"second"

        def factory() -> AsyncIterable[bytes]:
            nonlocal factory_calls
            factory_calls += 1
            return chunks()

        await client.dav_put_stream("f.bin", factory, content_type="text/plain")

        assert factory_calls == 2, "factory must be called once per attempt"
        assert bodies[0] == b"first-second"
        assert bodies[1] == b"first-second", "retry must send a fresh body, not an exhausted iterator"
        assert session.request.await_count == 2

    @pytest.mark.asyncio
    async def test_401_without_cached_session_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When auth uses Basic Auth (no cached cookie), 401 means bad credentials — surface, don't retry."""
        client = _make_client()
        bodies: list[bytes] = []

        async def fake_request(method: str, url: str, **kwargs: Any) -> niquests.Response:
            bodies.append(await _collect(kwargs["data"]))
            return _make_response(401)

        session = MagicMock()
        session.request = AsyncMock(side_effect=fake_request)
        session.auth = ("admin", "admin")  # not cached
        client._session = session

        factory_calls = 0

        async def chunks() -> AsyncIterable[bytes]:
            yield b"x"

        def factory() -> AsyncIterable[bytes]:
            nonlocal factory_calls
            factory_calls += 1
            return chunks()

        with pytest.raises(Exception, match=r"Authentication|401"):
            await client.dav_put_stream("f.bin", factory, content_type="text/plain")

        assert factory_calls == 1
        assert session.request.await_count == 1
