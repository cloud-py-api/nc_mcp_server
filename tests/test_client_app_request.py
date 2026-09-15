"""Tests for NextcloudClient.app_request_json and _raise_for_app_status (non-OCS app JSON routes)."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import niquests
import pytest

from nc_mcp_server.client import NextcloudClient, NextcloudError, _raise_for_app_status
from nc_mcp_server.config import Config


def _response(status_code: int, body: object = None, raw: bytes | None = None) -> niquests.Response:
    resp = niquests.Response()
    resp.status_code = status_code
    if raw is not None:
        resp._content = raw
    elif body is not None:
        resp._content = json.dumps(body).encode("utf-8")
        resp.headers["Content-Type"] = "application/json"
    else:
        resp._content = b""
    return resp


def _client_returning(*responses: niquests.Response) -> tuple[NextcloudClient, MagicMock]:
    client = NextcloudClient(Config(nextcloud_url="http://nc.test", user="admin", password="secret"))
    session = MagicMock()
    session.request = AsyncMock(side_effect=list(responses))
    session.auth = ("admin", "secret")
    client._session = session
    return client, session


def _mail_fail(message: str) -> dict[str, Any]:
    return {"status": "fail", "data": {"message": message, "type": "OCA\\Mail\\Exception\\ClientException"}}


class TestAppRequestJson:
    @pytest.mark.asyncio
    async def test_calls_index_php_route_with_ocs_header_and_json_body(self) -> None:
        client, session = _client_returning(_response(200, {"id": 1}))
        await client.app_request_json("POST", "mail/api/tags", json_data={"displayName": "x", "color": "#000000"})
        args, kwargs = session.request.call_args
        assert args == ("POST", "http://nc.test/index.php/apps/mail/api/tags")
        assert kwargs["headers"]["OCS-APIRequest"] == "true"
        assert kwargs["json"] == {"displayName": "x", "color": "#000000"}

    @pytest.mark.asyncio
    async def test_omits_json_and_params_when_not_given(self) -> None:
        client, session = _client_returning(_response(200, {"id": 1}))
        await client.app_request_json("GET", "mail/api/messages/5")
        _, kwargs = session.request.call_args
        assert "json" not in kwargs
        assert "params" not in kwargs

    @pytest.mark.asyncio
    async def test_forwards_params(self) -> None:
        client, session = _client_returning(_response(200, []))
        await client.app_request_json("GET", "mail/api/messages", params={"mailboxId": "3"})
        _, kwargs = session.request.call_args
        assert kwargs["params"] == {"mailboxId": "3"}

    @pytest.mark.asyncio
    async def test_returns_decoded_json_without_ocs_envelope(self) -> None:
        tag = {"id": 7, "displayName": "Needs Reply", "imapLabel": "$needs_reply"}
        client, _ = _client_returning(_response(200, tag))
        assert await client.app_request_json("PUT", "mail/api/messages/5/tags/%24needs_reply") == tag

    @pytest.mark.asyncio
    async def test_empty_body_returns_none(self) -> None:
        client, _ = _client_returning(_response(200))
        assert await client.app_request_json("POST", "mail/api/messages/5/move", json_data={"destFolderId": 9}) is None

    @pytest.mark.asyncio
    async def test_empty_json_list_is_returned_as_is(self) -> None:
        client, _ = _client_returning(_response(200, []))
        assert await client.app_request_json("PUT", "mail/api/messages/5/flags", json_data={"flags": {}}) == []

    @pytest.mark.asyncio
    async def test_error_uses_app_message_and_context(self) -> None:
        client, _ = _client_returning(_response(400, _mail_fail("Mailbox 99 does not exist")))
        with pytest.raises(NextcloudError, match=r"^POST apps/mail/api/messages/5/move: Mailbox 99 does not exist$"):
            await client.app_request_json("POST", "mail/api/messages/5/move", json_data={"destFolderId": 99})

    @pytest.mark.asyncio
    async def test_retries_once_when_cached_session_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, session = _client_returning(_response(401), _response(200, {"ok": True}))
        session.auth = None
        monkeypatch.setattr(client, "_reset_session", AsyncMock())
        assert await client.app_request_json("GET", "mail/api/messages/5") == {"ok": True}
        assert session.request.await_count == 2


class TestRaiseForAppStatus:
    def test_ok_response_does_not_raise(self) -> None:
        _raise_for_app_status(_response(200, {"id": 1}))

    def test_fail_body_message(self) -> None:
        with pytest.raises(NextcloudError, match="The maximum length for displayName is 128") as exc_info:
            _raise_for_app_status(_response(400, _mail_fail("The maximum length for displayName is 128")))
        assert exc_info.value.status_code == 400

    def test_error_body_top_level_message(self) -> None:
        body: dict[str, Any] = {"status": "error", "message": "Could not load message", "data": {}, "code": 0}
        with pytest.raises(NextcloudError, match="Could not load message") as exc_info:
            _raise_for_app_status(_response(500, body))
        assert exc_info.value.status_code == 500

    def test_empty_list_body_falls_back_to_status_message(self) -> None:
        with pytest.raises(NextcloudError, match="Forbidden") as exc_info:
            _raise_for_app_status(_response(403, []))
        assert exc_info.value.status_code == 403

    def test_non_json_body_falls_back_to_status_message(self) -> None:
        with pytest.raises(NextcloudError, match="Not found"):
            _raise_for_app_status(_response(404, raw=b"<html>404</html>"))

    def test_no_body_falls_back_to_http_code(self) -> None:
        with pytest.raises(NextcloudError, match="HTTP 502"):
            _raise_for_app_status(_response(502))

    def test_non_string_message_is_ignored(self) -> None:
        with pytest.raises(NextcloudError, match="HTTP 400"):
            _raise_for_app_status(_response(400, {"status": "fail", "data": {"message": ["not", "a", "string"]}}))

    def test_context_prefix(self) -> None:
        with pytest.raises(NextcloudError, match=r"^PUT apps/mail/api/messages/1/flags: Forbidden"):
            _raise_for_app_status(_response(403, []), "PUT apps/mail/api/messages/1/flags")
