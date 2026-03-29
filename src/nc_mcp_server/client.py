"""HTTP client for Nextcloud REST/OCS/DAV APIs."""

import contextlib
import logging
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote as url_quote

import niquests
from urllib3.util import Retry

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
        ocs_message: str = response.json()["ocs"]["meta"]["message"]
        if ocs_message:
            raise NextcloudError(f"{prefix}{ocs_message}", code)
    except (ValueError, KeyError, TypeError):
        pass
    detail = _STATUS_MESSAGES.get(code, f"HTTP {code}")
    raise NextcloudError(f"{prefix}{detail}", code)


# XML namespaces used in WebDAV responses
DAV_NS = "DAV:"
OC_NS = "http://owncloud.org/ns"
NC_NS = "http://nextcloud.org/ns"

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
        """Execute an HTTP request, retrying once if a cached session expired."""
        session = await self._get_session()
        response = await session.request(method, url, **kwargs)
        if await self._should_retry_auth(response):
            session = await self._get_session()
            response = await session.request(method, url, **kwargs)
        return response

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    # --- OCS API ---

    async def ocs_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make an OCS GET request and return the data portion."""
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await self._do_request("GET", url, params=params or {})
        _raise_for_ocs_status(response, f"OCS GET {path}")
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

    # --- WebDAV ---

    async def dav_propfind(self, path: str, depth: int = 1) -> list[dict[str, Any]]:
        """PROPFIND on a WebDAV path. Returns list of file/folder entries."""
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
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
        return self._parse_propfind(text, user)

    async def dav_get(self, path: str) -> tuple[bytes, str]:
        """GET a file's content via WebDAV. Returns (content, content_type)."""
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await self._do_request("GET", url)
        _raise_for_status(response, f"Get file '{path}'")
        ct = response.headers.get("content-type", "application/octet-stream")
        content_type = str(ct).split(";")[0].strip()
        return response.content or b"", content_type

    async def dav_put(self, path: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        """PUT (upload/overwrite) a file via WebDAV."""
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await self._do_request("PUT", url, data=content, headers={"Content-Type": content_type})
        _raise_for_status(response, f"Upload file '{path}'")

    async def dav_delete(self, path: str) -> None:
        """DELETE a file or folder via WebDAV."""
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await self._do_request("DELETE", url)
        _raise_for_status(response, f"Delete '{path}'")

    async def dav_mkcol(self, path: str) -> None:
        """MKCOL (create directory) via WebDAV."""
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await self._do_request("MKCOL", url)
        _raise_for_status(response, f"Create directory '{path}'")

    async def dav_copy(self, source: str, destination: str) -> None:
        """COPY a file or folder via WebDAV."""
        user = self._config.user
        src_url = f"{self._base_url}/remote.php/dav/files/{user}/{source.lstrip('/')}"
        dest_url = f"{self._base_url}/remote.php/dav/files/{user}/{destination.lstrip('/')}"
        response = await self._do_request(
            "COPY",
            src_url,
            headers={"Destination": dest_url, "Overwrite": "F"},
        )
        _raise_for_status(response, f"Copy '{source}' to '{destination}'")

    async def dav_move(self, source: str, destination: str) -> None:
        """MOVE a file or folder via WebDAV."""
        user = self._config.user
        src_url = f"{self._base_url}/remote.php/dav/files/{user}/{source.lstrip('/')}"
        dest_url = f"{self._base_url}/remote.php/dav/files/{user}/{destination.lstrip('/')}"
        response = await self._do_request(
            "MOVE",
            src_url,
            headers={"Destination": dest_url, "Overwrite": "F"},
        )
        _raise_for_status(response, f"Move '{source}' to '{destination}'")

    # --- Trashbin DAV ---

    async def trashbin_propfind(self) -> str:
        """PROPFIND on the trashbin root. Returns raw XML text."""
        user = self._config.user
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
        user = self._config.user
        encoded = url_quote(trash_path, safe="/")
        src = f"{self._base_url}/remote.php/dav/trashbin/{user}/trash/{encoded}"
        dest = f"{self._base_url}/remote.php/dav/trashbin/{user}/restore/{encoded}"
        response = await self._do_request("MOVE", src, headers={"Destination": dest})
        _raise_for_status(response, f"Restore '{trash_path}'")

    async def versions_propfind(self, file_id: int) -> str:
        """PROPFIND on the versions collection for a file. Returns raw XML text."""
        user = self._config.user
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
        user = self._config.user
        src = f"{self._base_url}/remote.php/dav/versions/{user}/versions/{file_id}/{version_id}"
        dest = f"{self._base_url}/remote.php/dav/versions/{user}/restore/target"
        response = await self._do_request("MOVE", src, headers={"Destination": dest})
        _raise_for_status(response, f"Restore version '{version_id}' of file {file_id}")

    async def trashbin_delete(self, trash_path: str = "") -> None:
        """Delete a single item or empty the entire trash (if path is empty)."""
        user = self._config.user
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

            href = href_el.text
            # Strip the DAV prefix to get the relative path
            path = (href.split(dav_prefix, 1)[1] if dav_prefix in href else href).rstrip("/")

            propstat = response.find(f"{{{DAV_NS}}}propstat")
            if propstat is None:
                continue
            prop = propstat.find(f"{{{DAV_NS}}}prop")
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
