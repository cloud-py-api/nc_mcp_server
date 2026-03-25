"""HTTP client for Nextcloud REST/OCS/DAV APIs."""

import contextlib
import xml.etree.ElementTree as ET
from typing import Any

import niquests

from .config import Config


class NextcloudError(Exception):
    """Human-readable error from a Nextcloud API call."""

    def __init__(self, message: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


def _raise_for_status(response: niquests.Response, context: str = "") -> None:
    """Raise NextcloudError with a helpful message instead of raw HTTPError."""
    if response.ok:
        return
    code = response.status_code or 0
    prefix = f"{context}: " if context else ""
    messages: dict[int, str] = {
        401: "Authentication failed. Check NEXTCLOUD_USER and NEXTCLOUD_PASSWORD.",
        403: "Forbidden. The user does not have permission for this operation.",
        404: "Not found.",
        409: "Conflict. The resource may already exist or the parent directory is missing.",
        423: "Locked. The resource is currently locked by another process.",
    }
    detail = messages.get(code, f"HTTP {code}")
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
            self._session = niquests.AsyncSession(
                auth=(self._config.user, self._config.password),
                timeout=30,
                headers={
                    "OCS-APIRequest": "true",
                    "Accept": "application/json",
                },
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    # --- OCS API ---

    async def ocs_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make an OCS GET request and return the data portion."""
        session = await self._get_session()
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await session.get(url, params=params or {})
        _raise_for_status(response, f"OCS GET {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """Make an OCS POST request and return the data portion."""
        session = await self._get_session()
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await session.post(url, data=data or {})
        _raise_for_status(response, f"OCS POST {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_put(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """Make an OCS PUT request and return the data portion."""
        session = await self._get_session()
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await session.put(url, data=data or {})
        _raise_for_status(response, f"OCS PUT {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    async def ocs_delete(self, path: str) -> Any:
        """Make an OCS DELETE request and return the data portion (if any)."""
        session = await self._get_session()
        url = f"{self._base_url}/ocs/v2.php/{path}"
        response = await session.delete(url)
        _raise_for_status(response, f"OCS DELETE {path}")
        result: dict[str, Any] = response.json()  # type: ignore[assignment]
        return result["ocs"]["data"]

    # --- WebDAV ---

    async def dav_propfind(self, path: str, depth: int = 1) -> list[dict[str, Any]]:
        """PROPFIND on a WebDAV path. Returns list of file/folder entries."""
        session = await self._get_session()
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await session.request(
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

    async def dav_get(self, path: str) -> bytes:
        """GET a file's content via WebDAV."""
        session = await self._get_session()
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await session.get(url)
        _raise_for_status(response, f"Get file '{path}'")
        return response.content or b""

    async def dav_put(self, path: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        """PUT (upload/overwrite) a file via WebDAV."""
        session = await self._get_session()
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await session.put(url, data=content, headers={"Content-Type": content_type})
        _raise_for_status(response, f"Upload file '{path}'")

    async def dav_delete(self, path: str) -> None:
        """DELETE a file or folder via WebDAV."""
        session = await self._get_session()
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await session.delete(url)
        _raise_for_status(response, f"Delete '{path}'")

    async def dav_mkcol(self, path: str) -> None:
        """MKCOL (create directory) via WebDAV."""
        session = await self._get_session()
        user = self._config.user
        url = f"{self._base_url}/remote.php/dav/files/{user}/{path.lstrip('/')}"
        response = await session.request("MKCOL", url)
        _raise_for_status(response, f"Create directory '{path}'")

    async def dav_move(self, source: str, destination: str) -> None:
        """MOVE a file or folder via WebDAV."""
        session = await self._get_session()
        user = self._config.user
        src_url = f"{self._base_url}/remote.php/dav/files/{user}/{source.lstrip('/')}"
        dest_url = f"{self._base_url}/remote.php/dav/files/{user}/{destination.lstrip('/')}"
        response = await session.request(
            "MOVE",
            src_url,
            headers={"Destination": dest_url, "Overwrite": "F"},
        )
        _raise_for_status(response, f"Move '{source}' to '{destination}'")

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
        session = await self._get_session()
        url = f"{self._base_url}/remote.php/dav/{path.lstrip('/')}"
        response = await session.request(method, url, data=body, headers=headers or {})
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
