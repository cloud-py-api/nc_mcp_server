"""Tests for NextcloudClient.ocs_get response handling."""

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
