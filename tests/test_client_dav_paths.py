"""Tests for how WebDAV file paths travel: encoded on the way out, decoded on the way back.

Regression for paths being sent raw: a "#" or "?" in a name cut the URL short and a "%"
was decoded by the server, so uploads, reads, moves and deletes silently hit a different
file. Listings in turn returned the still-encoded hrefs.
"""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import niquests
import pytest

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config

BASE = "http://localhost/remote.php/dav/files/admin"

# (literal path, how it must appear in the URL)
PATHS = [
    ("with space.txt", "with%20space.txt"),
    ("hash #1.txt", "hash%20%231.txt"),
    ("question?.txt", "question%3F.txt"),
    ("100% done.txt", "100%25%20done.txt"),
    ("literal %41.txt", "literal%20%2541.txt"),
    ("plus+and&.txt", "plus%2Band%26.txt"),
    ("semi;colon=eq.txt", "semi%3Bcolon%3Deq.txt"),
    ("Ünï 世界.txt", "%C3%9Cn%C3%AF%20%E4%B8%96%E7%95%8C.txt"),
    ("dir #1/inner ?.txt", "dir%20%231/inner%20%3F.txt"),
    ("plain/nested.txt", "plain/nested.txt"),
]


def _make_client(user: str = "admin", url: str = "http://localhost") -> NextcloudClient:
    return NextcloudClient(Config(nextcloud_url=url, user=user, password="secret"))


def _make_response(status_code: int, text: str = "") -> niquests.Response:
    resp = niquests.Response()
    resp.status_code = status_code
    resp._content = text.encode("utf-8")
    return resp


def _mock_requests(client: NextcloudClient, status_code: int = 201, text: str = "") -> AsyncMock:
    do_request = AsyncMock(return_value=_make_response(status_code, text))
    client._do_request = do_request  # type: ignore[method-assign]
    return do_request


def _last_call(mock: AsyncMock) -> Any:
    call = mock.await_args
    assert call is not None
    return call


def _propfind_xml(*hrefs: str) -> str:
    responses = "".join(
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype/></d:prop>"
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        for href in hrefs
    )
    return f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{responses}</d:multistatus>'


class TestRequestUrls:
    @pytest.mark.parametrize(("path", "encoded"), PATHS)
    @pytest.mark.asyncio
    async def test_get(self, path: str, encoded: str) -> None:
        client = _make_client()
        do_request = _mock_requests(client, 200)
        await client.dav_get(path)
        assert _last_call(do_request).args == ("GET", f"{BASE}/{encoded}")

    @pytest.mark.parametrize(("path", "encoded"), PATHS)
    @pytest.mark.asyncio
    async def test_put(self, path: str, encoded: str) -> None:
        client = _make_client()
        do_request = _mock_requests(client)
        await client.dav_put(path, b"x")
        assert _last_call(do_request).args == ("PUT", f"{BASE}/{encoded}")

    @pytest.mark.parametrize(("path", "encoded"), PATHS)
    @pytest.mark.asyncio
    async def test_delete_mkcol_and_propfind(self, path: str, encoded: str) -> None:
        client = _make_client()
        do_request = _mock_requests(client, 207, _propfind_xml())
        await client.dav_delete(path)
        await client.dav_mkcol(path)
        await client.dav_propfind(path)
        calls = [c.args for c in do_request.await_args_list]
        assert calls == [
            ("DELETE", f"{BASE}/{encoded}"),
            ("MKCOL", f"{BASE}/{encoded}"),
            ("PROPFIND", f"{BASE}/{encoded}"),
        ]

    @pytest.mark.parametrize(("path", "encoded"), PATHS)
    @pytest.mark.asyncio
    async def test_put_stream(self, path: str, encoded: str) -> None:
        client = _make_client()
        session = MagicMock()
        session.request = AsyncMock(return_value=_make_response(201))
        session.auth = ("admin", "secret")
        client._session = session

        async def chunks() -> AsyncIterator[bytes]:
            yield b"x"

        await client.dav_put_stream(path, chunks)
        assert _last_call(session.request).args == ("PUT", f"{BASE}/{encoded}")

    @pytest.mark.parametrize("method", ["dav_copy", "dav_move"])
    @pytest.mark.asyncio
    async def test_copy_and_move_encode_the_destination_too(self, method: str) -> None:
        """Header values are never re-quoted, so a raw non-ASCII destination could not even be sent."""
        client = _make_client()
        do_request = _mock_requests(client)
        await getattr(client, method)("from #1.txt", "to ?%41 Ü.txt")
        assert _last_call(do_request).args[1] == f"{BASE}/from%20%231.txt"
        destination = _last_call(do_request).kwargs["headers"]["Destination"]
        assert destination == f"{BASE}/to%20%3F%2541%20%C3%9C.txt"
        assert destination.isascii()

    @pytest.mark.asyncio
    async def test_leading_slash_is_dropped_and_trailing_slash_kept(self) -> None:
        client = _make_client()
        do_request = _mock_requests(client)
        await client.dav_mkcol("/dir #1/")
        assert _last_call(do_request).args == ("MKCOL", f"{BASE}/dir%20%231/")

    @pytest.mark.asyncio
    async def test_user_id_is_encoded(self) -> None:
        client = _make_client(user="jane o'doe@example.com")
        do_request = _mock_requests(client, 200)
        await client.dav_get("a b.txt")
        assert _last_call(do_request).args[1] == (
            "http://localhost/remote.php/dav/files/jane%20o%27doe%40example.com/a%20b.txt"
        )


class TestOtherDavRoots:
    """Trash and versions live under their own DAV roots, which carry the user ID too."""

    @pytest.mark.asyncio
    async def test_trash_and_versions_encode_the_user_id(self) -> None:
        client = _make_client(user="jane o'doe@example.com")
        do_request = _mock_requests(client, 207, _propfind_xml())
        await client.trashbin_propfind()
        await client.trashbin_restore("doc #1.txt.d1700000000")
        await client.trashbin_delete("doc #1.txt.d1700000000")
        await client.versions_propfind(42)
        await client.versions_restore(42, "1700000000")
        user = "jane%20o%27doe%40example.com"
        trash = f"http://localhost/remote.php/dav/trashbin/{user}"
        versions = f"http://localhost/remote.php/dav/versions/{user}"
        calls = do_request.await_args_list
        assert [c.args[1] for c in calls] == [
            f"{trash}/trash/",
            f"{trash}/trash/doc%20%231.txt.d1700000000",
            f"{trash}/trash/doc%20%231.txt.d1700000000",
            f"{versions}/versions/42/",
            f"{versions}/versions/42/1700000000",
        ]
        assert calls[1].kwargs["headers"]["Destination"] == f"{trash}/restore/doc%20%231.txt.d1700000000"
        assert calls[4].kwargs["headers"]["Destination"] == f"{versions}/restore/target"


class TestParsePropfindPaths:
    @pytest.mark.parametrize(("path", "encoded"), PATHS)
    def test_hrefs_are_decoded_to_the_literal_path(self, path: str, encoded: str) -> None:
        entries = NextcloudClient._parse_propfind(_propfind_xml(f"/remote.php/dav/files/admin/{encoded}"), "admin")
        assert entries[0]["path"] == path

    def test_plus_stays_a_plus(self) -> None:
        """Paths are URL paths, not form data: "+" is a literal plus, never a space."""
        entries = NextcloudClient._parse_propfind(_propfind_xml("/remote.php/dav/files/admin/a+b.txt"), "admin")
        assert entries[0]["path"] == "a+b.txt"

    def test_user_id_with_space_is_stripped_from_the_path(self) -> None:
        hrefs = ("/remote.php/dav/files/jane%20doe/", "/remote.php/dav/files/jane%20doe/Docs/")
        entries = NextcloudClient._parse_propfind(_propfind_xml(*hrefs), "jane doe")
        assert [e["path"] for e in entries] == ["/", "Docs"]

    def test_install_in_a_subdirectory(self) -> None:
        href = "/nextcloud/remote.php/dav/files/admin/hash%20%231.txt"
        entries = NextcloudClient._parse_propfind(_propfind_xml(href), "admin")
        assert entries[0]["path"] == "hash #1.txt"

    @pytest.mark.parametrize(("path", "encoded"), PATHS)
    @pytest.mark.asyncio
    async def test_listed_paths_are_requested_unchanged(self, path: str, encoded: str) -> None:
        """A path taken from a listing must reach the same file when handed back to a tool."""
        client = _make_client()
        do_request = _mock_requests(client, 207, _propfind_xml(f"/remote.php/dav/files/admin/{encoded}"))
        listed = (await client.dav_propfind(""))[0]["path"]
        await client.dav_get(listed)
        assert _last_call(do_request).args == ("GET", f"{BASE}/{encoded}")
