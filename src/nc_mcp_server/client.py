"""HTTP client for Nextcloud REST/OCS/DAV APIs."""

import contextlib
import logging
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterable, Callable
from typing import Any, cast
from urllib.parse import quote as url_quote
from urllib.parse import unquote as url_unquote

import niquests
from urllib3.util import Retry, Timeout

from .config import Config

log = logging.getLogger(__name__)


class NextcloudError(Exception):
    """Human-readable error from a Nextcloud API call."""

    def __init__(self, message: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


_STATUS_MESSAGES: dict[int, str] = {
    401: "Authentication failed. Check NEXTCLOUD_USER and NEXTCLOUD_PASSWORD.",
    403: "Forbidden. The user does not have permission for this operation.",
    404: "Not found.",
    409: "Conflict. The resource may already exist or the parent directory is missing.",
    423: "Locked. The resource is currently locked by another process.",
}


def _raise_for_status(response: niquests.Response, context: str = "") -> None:
    """Raise NextcloudError with a helpful message instead of raw HTTPError."""
    if response.ok:
        return
    code = response.status_code or 0
    prefix = f"{context}: " if context else ""
    detail = _STATUS_MESSAGES.get(code, f"HTTP {code}")
    raise NextcloudError(f"{prefix}{detail}", code)


def _raise_for_ocs_status(response: niquests.Response, context: str = "") -> None:
    """Raise NextcloudError using the OCS error message from the response body when available.

    Nextcloud OCS endpoints return error details in ocs.meta.message (e.g.
    "User already exists", "Wrong share ID, share does not exist"). This
    function extracts that message for a much better error experience than
    the generic HTTP status code mapping.

    Falls back to _raise_for_status() when the OCS body cannot be parsed.
    """
    if response.ok:
        return
    code = response.status_code or 0
    prefix = f"{context}: " if context else ""
    try:
        ocs = response.json()["ocs"]
        ocs_message: str = (
            ocs["meta"]["message"] or _ocs_field_errors(ocs.get("data")) or _ocs_error_code(code, ocs.get("data"))
        )
        if ocs_message == _CONFIRMATION_REQUIRED:
            ocs_message += _CONFIRMATION_HINT
        if ocs_message:
            raise NextcloudError(f"{prefix}{ocs_message}", code)
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    detail = _STATUS_MESSAGES.get(code, f"HTTP {code}")
    raise NextcloudError(f"{prefix}{detail}", code)


_CONFIRMATION_REQUIRED = "Password confirmation is required"
_CONFIRMATION_HINT = (
    ". Nextcloud does not count an app password as a confirmed password for this action; use the"
    " account's login password, or have an admin add this server's address to"
    " 'allowed_no_password_confirmation_ranges' in config.php"
)


def _ocs_error_code(status: int, data: object) -> str:
    """The status message plus the code Talk and other apps send as {"error": "..."} with an empty message.

    Without it every refusal of an endpoint reads the same ("HTTP 400"), though the code tells them
    apart ("age", "permission", ...).
    """
    if not isinstance(data, dict):
        return ""
    error = cast(dict[str, Any], data).get("error")
    if not isinstance(error, str) or not error:
        return ""
    return f"{_STATUS_MESSAGES.get(status, f'HTTP {status}')} ({error})"


def _ocs_field_errors(data: object) -> str:
    """Join the per-field errors some OCS endpoints send in data.errors, or "" when there are none.

    Endpoints that validate several fields at once (PATCH cloud/users/{userId}, for one) answer
    422 with an empty meta message and {"errors": {"email": "Invalid email address", ...}}.
    """
    if not isinstance(data, dict):
        return ""
    errors = cast(dict[str, Any], data).get("errors")
    if not isinstance(errors, dict) or not errors:
        return ""
    return "; ".join(f"{field}: {message}" for field, message in cast(dict[str, Any], errors).items())


def _app_error_message(body: object) -> str:
    """Return the error message from an app route's JSON body, or "" when it has none.

    App routes outside OCS have no common error envelope. The Mail app, for example, answers
    client errors with {"status": "fail", "data": {"message": ...}} and server errors with
    {"status": "error", "message": ...}.
    """
    if not isinstance(body, dict):
        return ""
    payload = cast(dict[str, Any], body)
    data = payload.get("data")
    message = cast(dict[str, Any], data).get("message") if isinstance(data, dict) else None
    if message is None:
        message = payload.get("message")
    return message if isinstance(message, str) else ""


def _raise_for_app_status(response: niquests.Response, context: str = "") -> None:
    """Raise NextcloudError for a failed app route, preferring the message from its JSON body."""
    if response.ok:
        return
    code = response.status_code or 0
    prefix = f"{context}: " if context else ""
    message = ""
    with contextlib.suppress(ValueError, TypeError):
        message = _app_error_message(response.json())
    detail = message or _STATUS_MESSAGES.get(code, f"HTTP {code}")
    raise NextcloudError(f"{prefix}{detail}", code)


# XML namespaces used in WebDAV responses
DAV_NS = "DAV:"
OC_NS = "http://owncloud.org/ns"
NC_NS = "http://nextcloud.org/ns"


def find_ok_prop(response: ET.Element) -> ET.Element | None:
    """Find the <d:prop> from the first propstat with HTTP 200 status.

    WebDAV multi-status responses may contain multiple propstat elements
    with different status codes (e.g. 200 for found props, 404 for missing ones).
    The ordering is not guaranteed, so we iterate all of them.
    """
    for propstat in response.findall(f"{{{DAV_NS}}}propstat"):
        status_el = propstat.find(f"{{{DAV_NS}}}status")
        if status_el is not None and "200" not in (status_el.text or ""):
            continue
        prop = propstat.find(f"{{{DAV_NS}}}prop")
        if prop is not None:
            return prop
    return None


# Standard PROPFIND body for file listings
PROPFIND_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <d:getlastmodified/>
    <d:getetag/>
    <d:getcontenttype/>
    <d:getcontentlength/>
    <d:resourcetype/>
    <oc:fileid/>
    <oc:permissions/>
    <oc:size/>
    <nc:has-preview/>
  </d:prop>
</d:propfind>"""


# What Nextcloud answers (HTTP 403) when an action needs a confirmed password: the plain check wants a
# confirmation less than 30 minutes old, the strict one the password in the request's own header.
_NOT_CONFIRMED_MESSAGES = ("Password confirmation is required", "Required authorization header missing")


def _needs_password_confirmation(response: niquests.Response) -> bool:
    """Tell a refused password confirmation from other 403s.

    Plain JSON routes mark it with a header; OCS rebuilds the response without it, so there the
    message decides. Both messages are fixed English strings in Nextcloud, never translated.
    """
    if response.status_code != 403:
        return False
    if response.headers.get("X-NC-Auth-NotConfirmed") == "true":
        return True
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    payload = cast(dict[str, Any], body)
    ocs = payload.get("ocs")
    if isinstance(ocs, dict):
        meta = cast(dict[str, Any], ocs).get("meta")
        message = cast(dict[str, Any], meta).get("message") if isinstance(meta, dict) else None
    else:
        message = payload.get("message")
    return message in _NOT_CONFIRMED_MESSAGES


class NextcloudClient:
    """Async HTTP client for Nextcloud APIs.

    Handles authentication, OCS response parsing, and WebDAV operations.
    Uses niquests (modern requests fork with HTTP/2 and HTTP/3 support).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._base_url = config.nextcloud_url
        self._session: niquests.AsyncSession | None = None

    async def _get_session(self) -> niquests.AsyncSession:
        if self._session is None:
            self._session = self._build_session()
            await self._init_session_auth()
        return self._session

    @property
    def _session_is_cached(self) -> bool:
        return self._session is not None and self._session.auth is None

    async def _reset_session(self) -> None:
        """Discard the current session and create a fresh one with Basic Auth."""
        if self._session:
            await self._session.close()
        self._session = self._build_session()
        await self._init_session_auth()

    def _build_session(self) -> niquests.AsyncSession:
        kwargs: dict[str, object] = {
            "auth": (self._config.user, self._config.password),
            "timeout": 30,
            "headers": {
                "OCS-APIRequest": "true",
                "Accept": "application/json",
            },
        }
        if self._config.retry_max > 0:
            kwargs["retries"] = Retry(
                total=self._config.retry_max,
                connect=0,
                read=0,
                other=0,
                redirect=0,
                status_forcelist=[429, 503],
                backoff_factor=1.0,
                respect_retry_after_header=True,
                allowed_methods=None,
                raise_on_status=False,
            )
        return niquests.AsyncSession(**kwargs)  # type: ignore[arg-type]

    async def _should_retry_auth(self, response: niquests.Response) -> bool:
        """Check if a 401 response is due to an expired cached session.

        If so, resets the session (re-authenticates) and returns True so the
        caller can retry the request once.
        """
        if response.status_code == 401 and self._session_is_cached:
            log.debug("Session expired (401), re-authenticating")
            await self._reset_session()
            return True
        return False

    async def _init_session_auth(self) -> None:
        """Authenticate once and try to cache the server session.

        Nextcloud hashes the password (bcrypt) on every Basic Auth request.
        By sending ``cookie_test=test`` with the first request, we trigger NC
        to create a session token (see ``Session::supportsCookies``).
        Subsequent requests reuse the session cookie, which is validated via a
        fast DB lookup instead of bcrypt.

        When ``Config.is_app_password`` is set, session caching is skipped
        because app passwords already use a fast token lookup.
        """
        if self._config.is_app_password:
            return
        if self._session is None:
            return
        url = f"{self._base_url}/ocs/v2.php/cloud/capabilities"
        try:
            self._session.cookies.set("cookie_test", "test")  # type: ignore[union-attr]
            resp = await self._session.get(url)
            if not resp.ok:
                return
        except OSError:
            return
        saved_auth = self._session.auth
        self._session.auth = None
        try:
            probe = await self._session.get(url)
            if probe.ok:
                log.debug("Session cookie cached, disabled Basic Auth for subsequent requests")
                return
        except OSError:
            pass
        self._session.auth = saved_auth

    async def _do_request(self, method: str, url: str, **kwargs: Any) -> niquests.Response:
        """Execute an HTTP request, retrying once if a cached session expired or lacks a password confirmation."""
        session = await self._get_session()
        # Taken per request: a concurrent call can swap self._session while this one is in flight
        cached = session.auth is None
        response = await session.request(method, url, **kwargs)
        if await self._should_retry_auth(response):
            session = await self._get_session()
            cached = session.auth is None
            response = await session.request(method, url, **kwargs)
        if cached and _needs_password_confirmation(response):
            response = await self._request_with_password(method, url, **kwargs)
        return response

    async def _request_with_password(self, method: str, url: str, **kwargs: Any) -> niquests.Response:
        """Send one request as a fresh Basic Auth login, outside the cached session.

        The cached session's password confirmation expires after 30 minutes, and the session never
        carries the password header strict endpoints (enabling apps, for one) demand. A new login
        confirms the password, sends that header, and, having no session cookie, also lets
        'allowed_no_password_confirmation_ranges' apply, which Nextcloud skips for session logins.
        """
        log.debug("Password confirmation missing, repeating %s %s with a fresh login", method, url)
        session = self._build_session()
        try:
            return await session.request(method, url, **kwargs)
        finally:
            await session.close()

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    # --- OCS API ---

    async def ocs_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make an OCS GET request and return the data portion.

        Returns None for "304 Not Modified", which Talk's chat endpoint answers with an
        empty body when no message matches the query (for example when paging past the
        oldest message of a conversation or of a thread).
        """
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("GET", url, params=params or {})
        _raise_for_ocs_status(response, f"OCS GET {path}")
        if response.status_code == 304:
            return None
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """Make an OCS POST request and return the data portion."""
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("POST", url, data=data or {})
        _raise_for_ocs_status(response, f"OCS POST {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_post_json(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        """Make an OCS POST request with a JSON body and return the data portion."""
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("POST", url, json=json_data or {})
        _raise_for_ocs_status(response, f"OCS POST {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_put(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """Make an OCS PUT request and return the data portion."""
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("PUT", url, data=data or {})
        _raise_for_ocs_status(response, f"OCS PUT {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_delete(self, path: str) -> Any:
        """Make an OCS DELETE request and return the data portion (if any)."""
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("DELETE", url)
        _raise_for_ocs_status(response, f"OCS DELETE {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_patch(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """Make an OCS PATCH request and return the data portion."""
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("PATCH", url, data=data or {})
        _raise_for_ocs_status(response, f"OCS PATCH {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_patch_json(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        """Make an OCS PATCH request with a JSON body and return the data portion."""
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("PATCH", url, json=json_data or {})
        _raise_for_ocs_status(response, f"OCS PATCH {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_put_json(self, path: str, json_data: dict[str, Any] | None = None) -> Any:
        """Make an OCS PUT request with a JSON body and return the data portion."""
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("PUT", url, json=json_data or {})
        _raise_for_ocs_status(response, f"OCS PUT {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    # --- App JSON routes ---

    async def app_request_json(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call an app's non-OCS JSON route under /index.php/apps/ and return the decoded body.

        Some app actions exist only as the routes an app's web UI uses (Mail's move, flag and tag
        endpoints, for example). Those controllers require a CSRF token, and Nextcloud skips that
        check for requests carrying ``OCS-APIRequest: true``, so the header is sent explicitly.
        Responses are plain JSON without an OCS envelope; an empty body returns None.
        """
        url = f"{self._base_url}/index.php/apps/{path}"
        kwargs: dict[str, Any] = {"headers": {"OCS-APIRequest": "true", "Accept": "application/json"}}
        if json_data is not None:
            kwargs["json"] = json_data
        if params:
            kwargs["params"] = params
        response = await self._do_request(method, url, **kwargs)
        _raise_for_app_status(response, f"{method} apps/{path}")
        if not (response.content or b"").strip():
            return None
        return response.json()

    async def web_page(self, path: str) -> str:
        """GET a page of the web interface under /index.php/ and return its HTML.

        For data Nextcloud only hands to its own pages as initial state, with no API to read it from.
        """
        url = f"{self._base_url}/index.php/{path.lstrip('/')}"
        response = await self._do_request("GET", url)
        _raise_for_status(response, f"GET {path}")
        return response.text or ""

    # --- WebDAV ---

    @property
    def _dav_user(self) -> str:
        """The user ID as a URL path segment; a space, quote or "@" is valid in one."""
        return url_quote(self._config.user, safe="")

    def _files_url(self, path: str) -> str:
        """Build the WebDAV URL of a path in the user's files.

        Paths are taken literally, so every segment is percent-encoded: sent raw, a "#"
        or "?" in a name would cut the URL short and a "%" would be decoded by the
        server, and the request would silently hit a different file.
        """
        return f"{self._base_url}/remote.php/dav/files/{self._dav_user}/{url_quote(path.lstrip('/'), safe='/')}"

    async def dav_propfind(self, path: str, depth: int = 1) -> list[dict[str, Any]]:
        """PROPFIND on a WebDAV path. Returns list of file/folder entries."""
        url = self._files_url(path)
        response = await self._do_request(
            "PROPFIND",
            url,
            data=PROPFIND_BODY,
            headers={
                "Depth": str(depth),
                "Content-Type": "application/xml; charset=utf-8",
            },
        )
        _raise_for_status(response, f"List directory '{path}'")
        text = response.text or ""
        return self._parse_propfind(text, self._config.user)

    async def dav_get(self, path: str) -> tuple[bytes, str]:
        """GET a file's content via WebDAV. Returns (content, content_type)."""
        url = self._files_url(path)
        response = await self._do_request("GET", url)
        _raise_for_status(response, f"Get file '{path}'")
        ct = response.headers.get("content-type", "application/octet-stream")
        content_type = str(ct).split(";")[0].strip()
        return response.content or b"", content_type

    async def dav_put(self, path: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        """PUT (upload/overwrite) a file via WebDAV."""
        url = self._files_url(path)
        response = await self._do_request("PUT", url, data=content, headers={"Content-Type": content_type})
        _raise_for_status(response, f"Upload file '{path}'")

    async def dav_put_stream(
        self,
        path: str,
        chunks_factory: Callable[[], AsyncIterable[bytes]],
        content_type: str = "application/octet-stream",
    ) -> None:
        """PUT (upload/overwrite) a file via WebDAV, streaming body from an async iterable.

        Use this for files too large to hold fully in memory. niquests sends the body
        with Transfer-Encoding: chunked; we deliberately do not set Content-Length
        because nginx rejects (HTTP 400) requests that combine both headers.

        The body is supplied as a factory (not a single iterable) because a cached
        session cookie can expire mid-run: if the first attempt drains the iterable
        and returns 401, the generic _do_request retry would send the retry with an
        empty body — Nextcloud would happily accept the empty PUT and silently
        truncate the file. Each attempt calls the factory to get a fresh generator.

        The read timeout is disabled — a multi-GB upload can legitimately take
        minutes. Connect timeout still applies via the session default.
        """
        url = self._files_url(path)
        headers = {"Content-Type": content_type}
        timeout = Timeout(connect=30, read=None)

        session = await self._get_session()
        response = await session.request("PUT", url, data=chunks_factory(), headers=headers, timeout=timeout)
        if await self._should_retry_auth(response):
            session = await self._get_session()
            response = await session.request("PUT", url, data=chunks_factory(), headers=headers, timeout=timeout)
        _raise_for_status(response, f"Upload file '{path}'")

    async def dav_delete(self, path: str) -> None:
        """DELETE a file or folder via WebDAV."""
        url = self._files_url(path)
        response = await self._do_request("DELETE", url)
        _raise_for_status(response, f"Delete '{path}'")

    async def dav_mkcol(self, path: str) -> None:
        """MKCOL (create directory) via WebDAV."""
        url = self._files_url(path)
        response = await self._do_request("MKCOL", url)
        _raise_for_status(response, f"Create directory '{path}'")

    async def dav_copy(self, source: str, destination: str) -> None:
        """COPY a file or folder via WebDAV."""
        src_url = self._files_url(source)
        dest_url = self._files_url(destination)
        response = await self._do_request(
            "COPY",
            src_url,
            headers={"Destination": dest_url, "Overwrite": "F"},
        )
        _raise_for_status(response, f"Copy '{source}' to '{destination}'")

    async def dav_move(self, source: str, destination: str) -> None:
        """MOVE a file or folder via WebDAV."""
        src_url = self._files_url(source)
        dest_url = self._files_url(destination)
        response = await self._do_request(
            "MOVE",
            src_url,
            headers={"Destination": dest_url, "Overwrite": "F"},
        )
        _raise_for_status(response, f"Move '{source}' to '{destination}'")

    # --- Trashbin DAV ---

    async def trashbin_propfind(self) -> str:
        """PROPFIND on the trashbin root. Returns raw XML text."""
        user = self._dav_user
        url = f"{self._base_url}/remote.php/dav/trashbin/{user}/trash/"
        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">'
            "<d:prop>"
            "<d:getlastmodified/><d:getcontentlength/><d:resourcetype/>"
            "<oc:fileid/><nc:trashbin-filename/>"
            "<nc:trashbin-original-location/><nc:trashbin-deletion-time/>"
            "</d:prop></d:propfind>"
        )
        response = await self._do_request(
            "PROPFIND",
            url,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        _raise_for_status(response, "List trash")
        return response.text or ""

    async def trashbin_restore(self, trash_path: str) -> None:
        """Restore a trashed item by MOVEing it to the restore folder."""
        user = self._dav_user
        encoded = url_quote(trash_path, safe="/")
        src = f"{self._base_url}/remote.php/dav/trashbin/{user}/trash/{encoded}"
        dest = f"{self._base_url}/remote.php/dav/trashbin/{user}/restore/{encoded}"
        response = await self._do_request("MOVE", src, headers={"Destination": dest})
        _raise_for_status(response, f"Restore '{trash_path}'")

    async def versions_propfind(self, file_id: int) -> str:
        """PROPFIND on the versions collection for a file. Returns raw XML text."""
        user = self._dav_user
        url = f"{self._base_url}/remote.php/dav/versions/{user}/versions/{file_id}/"
        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">'
            "<d:prop>"
            "<d:getlastmodified/><d:getcontentlength/><d:getcontenttype/>"
            "<nc:version-author/><nc:version-label/>"
            "</d:prop></d:propfind>"
        )
        response = await self._do_request(
            "PROPFIND",
            url,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        _raise_for_status(response, f"List versions for file {file_id}")
        return response.text or ""

    async def versions_restore(self, file_id: int, version_id: str) -> None:
        """Restore a file version by MOVEing it to the restore folder."""
        user = self._dav_user
        src = f"{self._base_url}/remote.php/dav/versions/{user}/versions/{file_id}/{version_id}"
        dest = f"{self._base_url}/remote.php/dav/versions/{user}/restore/target"
        response = await self._do_request("MOVE", src, headers={"Destination": dest})
        _raise_for_status(response, f"Restore version '{version_id}' of file {file_id}")

    async def trashbin_delete(self, trash_path: str = "") -> None:
        """Delete a single item or empty the entire trash (if path is empty)."""
        user = self._dav_user
        encoded = url_quote(trash_path, safe="/") if trash_path else ""
        url = f"{self._base_url}/remote.php/dav/trashbin/{user}/trash/{encoded}"
        response = await self._do_request("DELETE", url)
        _raise_for_status(response, "Empty trash" if not trash_path else f"Delete '{trash_path}' from trash")

    # --- Generic DAV ---

    async def dav_request(
        self,
        method: str,
        path: str,
        body: str | bytes | None = None,
        headers: dict[str, str] | None = None,
        context: str = "",
    ) -> niquests.Response:
        """Make a raw DAV request and return the response."""
        url = f"{self._base_url}/remote.php/dav/{path.lstrip('/')}"
        response = await self._do_request(method, url, data=body, headers=headers or {})
        _raise_for_status(response, context or f"DAV {method} {path}")
        return response

    # --- Parsing ---

    @staticmethod
    def _parse_propfind(xml_text: str, user: str) -> list[dict[str, Any]]:
        """Parse a PROPFIND XML response into a list of file/folder dicts."""
        root = ET.fromstring(xml_text)
        entries: list[dict[str, Any]] = []
        dav_prefix = f"/remote.php/dav/files/{user}/"

        for response in root.findall(f"{{{DAV_NS}}}response"):
            href_el = response.find(f"{{{DAV_NS}}}href")
            if href_el is None or href_el.text is None:
                continue

            # Hrefs come percent-encoded; report the literal path the other tools accept
            href = url_unquote(href_el.text)
            path = (href.split(dav_prefix, 1)[1] if dav_prefix in href else href).rstrip("/")

            prop = find_ok_prop(response)
            if prop is None:
                continue

            # Determine if directory
            resource_type = prop.find(f"{{{DAV_NS}}}resourcetype")
            is_dir = resource_type is not None and resource_type.find(f"{{{DAV_NS}}}collection") is not None

            entry: dict[str, Any] = {
                "path": path or "/",
                "is_directory": is_dir,
            }

            # Optional properties
            for tag, key in [
                (f"{{{DAV_NS}}}getlastmodified", "last_modified"),
                (f"{{{DAV_NS}}}getetag", "etag"),
                (f"{{{DAV_NS}}}getcontenttype", "content_type"),
                (f"{{{DAV_NS}}}getcontentlength", "size"),
                (f"{{{OC_NS}}}fileid", "file_id"),
                (f"{{{OC_NS}}}permissions", "permissions"),
                (f"{{{OC_NS}}}size", "total_size"),
            ]:
                el = prop.find(tag)
                if el is not None and el.text:
                    entry[key] = el.text

            # Convert size to int
            for size_key in ("size", "total_size"):
                if size_key in entry:
                    with contextlib.suppress(ValueError, TypeError):
                        entry[size_key] = int(entry[size_key])

            entries.append(entry)

        return entries
