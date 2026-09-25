"""Tests for HTTP client error handling — _raise_for_status and _raise_for_ocs_status."""

import json
from typing import Any

import niquests
import pytest

from nc_mcp_server.client import (
    NextcloudError,
    _raise_for_ocs_status,
    _raise_for_status,
)


def _fake_response(status_code: int, body: dict[str, Any] | str | None = None) -> niquests.Response:
    """Build a minimal niquests.Response with the given status and optional JSON body."""
    resp = niquests.Response()
    resp.status_code = status_code
    if body is not None:
        text = json.dumps(body) if isinstance(body, dict) else body
        resp._content = text.encode("utf-8")
        resp.headers["Content-Type"] = "application/json"
    return resp


def _ocs_error_body(message: str, statuscode: int = 404) -> dict[str, Any]:
    return {"ocs": {"meta": {"status": "failure", "statuscode": statuscode, "message": message}, "data": []}}


class TestRaiseForStatus:
    def test_ok_response_does_not_raise(self) -> None:
        _raise_for_status(_fake_response(200))

    def test_404_generic_message(self) -> None:
        with pytest.raises(NextcloudError, match="Not found") as exc_info:
            _raise_for_status(_fake_response(404))
        assert exc_info.value.status_code == 404

    def test_401_auth_message(self) -> None:
        with pytest.raises(NextcloudError, match="Authentication failed"):
            _raise_for_status(_fake_response(401))

    def test_403_forbidden_message(self) -> None:
        with pytest.raises(NextcloudError, match="Forbidden"):
            _raise_for_status(_fake_response(403))

    def test_409_conflict_message(self) -> None:
        with pytest.raises(NextcloudError, match="Conflict"):
            _raise_for_status(_fake_response(409))

    def test_423_locked_message(self) -> None:
        with pytest.raises(NextcloudError, match="Locked"):
            _raise_for_status(_fake_response(423))

    def test_unmapped_code_shows_http_number(self) -> None:
        with pytest.raises(NextcloudError, match="HTTP 418"):
            _raise_for_status(_fake_response(418))

    def test_context_prefix(self) -> None:
        with pytest.raises(NextcloudError, match=r"Delete 'file\.txt': Not found"):
            _raise_for_status(_fake_response(404), context="Delete 'file.txt'")


class TestRaiseForOcsStatus:
    def test_ok_response_does_not_raise(self) -> None:
        _raise_for_ocs_status(_fake_response(200))

    def test_extracts_ocs_meta_message(self) -> None:
        with pytest.raises(NextcloudError, match="User does not exist") as exc_info:
            _raise_for_ocs_status(_fake_response(404, _ocs_error_body("User does not exist")))
        assert exc_info.value.status_code == 404

    def test_ocs_message_with_context_prefix(self) -> None:
        body = _ocs_error_body("User already exists", 102)
        with pytest.raises(NextcloudError, match=r"OCS POST cloud/users: User already exists") as exc_info:
            _raise_for_ocs_status(_fake_response(400, body), "OCS POST cloud/users")
        assert exc_info.value.status_code == 400

    def test_share_not_found_message(self) -> None:
        body = _ocs_error_body("Wrong share ID, share does not exist")
        with pytest.raises(NextcloudError, match="Wrong share ID, share does not exist"):
            _raise_for_ocs_status(_fake_response(404, body))

    def test_share_wrong_path_message(self) -> None:
        body = _ocs_error_body("Wrong path, file/folder does not exist")
        with pytest.raises(NextcloudError, match="Wrong path"):
            _raise_for_ocs_status(_fake_response(404, body))

    def test_falls_back_when_ocs_message_empty(self) -> None:
        with pytest.raises(NextcloudError, match="HTTP 400"):
            _raise_for_ocs_status(_fake_response(400, _ocs_error_body("", 400)))

    def test_falls_back_when_body_not_json(self) -> None:
        with pytest.raises(NextcloudError, match="Not found"):
            _raise_for_ocs_status(_fake_response(404, "<html>404</html>"))

    def test_falls_back_when_body_missing_ocs_key(self) -> None:
        with pytest.raises(NextcloudError, match="Not found"):
            _raise_for_ocs_status(_fake_response(404, {"error": "something"}))

    def test_falls_back_when_body_missing_meta(self) -> None:
        with pytest.raises(NextcloudError, match="HTTP 400"):
            _raise_for_ocs_status(_fake_response(400, {"ocs": {"data": []}}))

    def test_falls_back_when_no_body(self) -> None:
        with pytest.raises(NextcloudError, match="HTTP 500"):
            _raise_for_ocs_status(_fake_response(500))

    def test_preserves_status_code(self) -> None:
        with pytest.raises(NextcloudError) as exc_info:
            _raise_for_ocs_status(_fake_response(400, _ocs_error_body("User already exists", 102)))
        assert exc_info.value.status_code == 400


class TestOcsFieldErrorsAndHints:
    def test_field_errors_are_listed_when_the_message_is_empty(self) -> None:
        """PATCH cloud/users/{userId} answers 422 with an empty message and one error per field."""
        body = _ocs_error_body("", 422)
        body["ocs"]["data"] = {"errors": {"email": "Invalid email address", "groups": "Group x does not exist"}}
        with pytest.raises(NextcloudError) as exc_info:
            _raise_for_ocs_status(_fake_response(422, body), "OCS PATCH cloud/users/u")
        assert str(exc_info.value) == (
            "OCS PATCH cloud/users/u: email: Invalid email address; groups: Group x does not exist"
        )
        assert exc_info.value.status_code == 422

    def test_message_wins_over_field_errors(self) -> None:
        body = _ocs_error_body("Something specific", 400)
        body["ocs"]["data"] = {"errors": {"email": "Invalid email address"}}
        with pytest.raises(NextcloudError, match=r"^Something specific$"):
            _raise_for_ocs_status(_fake_response(400, body))

    @pytest.mark.parametrize("data", [[], {}, {"errors": {}}, {"errors": []}, {"errors": "text"}, None])
    def test_no_usable_field_errors_falls_back_to_status(self, data: Any) -> None:
        body = _ocs_error_body("", 422)
        body["ocs"]["data"] = data
        with pytest.raises(NextcloudError, match=r"^HTTP 422$"):
            _raise_for_ocs_status(_fake_response(422, body))

    def test_password_confirmation_gets_a_hint(self) -> None:
        body = _ocs_error_body("Password confirmation is required", 403)
        with pytest.raises(NextcloudError) as exc_info:
            _raise_for_ocs_status(_fake_response(403, body))
        message = str(exc_info.value)
        assert message.startswith("Password confirmation is required. ")
        assert "app password" in message
        assert "allowed_no_password_confirmation_ranges" in message


class TestOcsErrorCode:
    def test_code_is_added_to_an_empty_message(self) -> None:
        """Talk answers refusals with {"error": "age"} and no message; the code tells them apart."""
        body = _ocs_error_body("", 400)
        body["ocs"]["data"] = {"error": "age"}
        with pytest.raises(NextcloudError, match=r"^OCS PUT x: HTTP 400 \(age\)$"):
            _raise_for_ocs_status(_fake_response(400, body), "OCS PUT x")

    def test_known_status_keeps_its_wording(self) -> None:
        body = _ocs_error_body("", 403)
        body["ocs"]["data"] = {"error": "permission"}
        with pytest.raises(NextcloudError, match=r"^Forbidden\..*\(permission\)$"):
            _raise_for_ocs_status(_fake_response(403, body))

    @pytest.mark.parametrize("data", [{"error": ""}, {"error": 5}, {"other": "x"}])
    def test_no_usable_code(self, data: Any) -> None:
        body = _ocs_error_body("", 400)
        body["ocs"]["data"] = data
        with pytest.raises(NextcloudError, match=r"^HTTP 400$"):
            _raise_for_ocs_status(_fake_response(400, body))

    def test_message_wins_over_the_code(self) -> None:
        body = _ocs_error_body("Room not found", 404)
        body["ocs"]["data"] = {"error": "room"}
        with pytest.raises(NextcloudError, match=r"^Room not found$"):
            _raise_for_ocs_status(_fake_response(404, body))
