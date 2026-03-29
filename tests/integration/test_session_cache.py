"""Integration tests for session-based auth caching in NextcloudClient."""

import asyncio
import os
import subprocess

import pytest

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config

from .conftest import TEST_BASE_DIR

pytestmark = pytest.mark.integration


def _make_config(password: str = "admin") -> Config:
    config = Config(
        nextcloud_url=os.environ.get("NEXTCLOUD_URL", "http://nextcloud.ncmcp"),
        user=os.environ.get("NEXTCLOUD_USER", "admin"),
        password=password,
    )
    config.validate()
    return config


def _run_occ(command: str) -> subprocess.CompletedProcess[str]:
    """Run a Nextcloud occ command. Supports bare-metal (NC_SERVER_DIR) and Docker (NC_CONTAINER)."""
    nc_server_dir = os.environ.get("NC_SERVER_DIR", "")
    nc_container = os.environ.get("NC_CONTAINER", "")
    if nc_server_dir:
        args = ["php", "occ", *command.split()]
        return subprocess.run(args, capture_output=True, text=True, timeout=15, check=False, cwd=nc_server_dir)
    if nc_container:
        args = [
            "docker",
            "exec",
            nc_container,
            "su",
            "-s",
            "/bin/bash",
            "www-data",
            "-c",
            f"php -d xdebug.mode=off occ {command}",
        ]
    else:
        args = [
            "docker",
            "exec",
            "ncmcp-nextcloud-1",
            "sudo",
            "-u",
            "www-data",
            "php",
            "-d",
            "xdebug.mode=off",
            "occ",
            *command.split(),
        ]
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)


def _create_app_password() -> str:
    """Create a fresh app password via occ CLI."""
    result = _run_occ("user:auth-tokens:add --name pytest-session-test admin")
    for line in result.stdout.splitlines():
        token = line.strip()
        if len(token) == 72 and token.isalnum():
            return token
    pytest.skip(f"Could not create app password: {result.stdout} {result.stderr}")
    return ""


class TestSessionCacheRegularPassword:
    @pytest.mark.asyncio
    async def test_session_cached_after_init(self) -> None:
        """After init, Basic Auth should be disabled (session cookie in use)."""
        client = NextcloudClient(_make_config())
        try:
            await client.ocs_get("cloud/user")
            assert client._session is not None
            assert client._session.auth is None, "Basic Auth should be disabled after session caching"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_ocs_get_works_after_session_cache(self) -> None:
        client = NextcloudClient(_make_config())
        try:
            user = await client.ocs_get("cloud/user")
            assert user["id"] == "admin"
            caps = await client.ocs_get("cloud/capabilities")
            assert "version" in caps
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_ocs_post_works_after_session_cache(self) -> None:
        client = NextcloudClient(_make_config())
        try:
            await client.ocs_get("cloud/user")
            await client.ocs_post(
                "apps/notifications/api/v1/admin_notifications/admin",
                data={"shortMessage": "session-cache-test"},
            )
            notifications = await client.ocs_get("apps/notifications/api/v2/notifications")
            subjects = [n["subject"] for n in notifications]
            assert "session-cache-test" in subjects
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_webdav_works_after_session_cache(self) -> None:
        client = NextcloudClient(_make_config())
        try:
            await client.ocs_get("cloud/user")
            await client.dav_mkcol(TEST_BASE_DIR)
            await client.dav_put(
                f"{TEST_BASE_DIR}/session-test.txt",
                b"hello",
                content_type="text/plain",
            )
            entries = await client.dav_propfind(TEST_BASE_DIR)
            names = [e["path"] for e in entries]
            assert any("session-test.txt" in n for n in names)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_ocs_delete_works_after_session_cache(self) -> None:
        client = NextcloudClient(_make_config())
        try:
            await client.ocs_get("cloud/user")
            await client.ocs_delete("apps/notifications/api/v2/notifications")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_multiple_calls_stay_session_cached(self) -> None:
        """Auth should stay None across many requests."""
        client = NextcloudClient(_make_config())
        try:
            for _ in range(5):
                await client.ocs_get("cloud/user")
                assert client._session is not None
                assert client._session.auth is None
        finally:
            await client.close()


class TestSessionCacheAppPassword:
    @pytest.mark.asyncio
    async def test_app_password_auth_works(self) -> None:
        """With an app password, all operations should work."""
        app_pwd = _create_app_password()
        client = NextcloudClient(_make_config(password=app_pwd))
        try:
            user = await client.ocs_get("cloud/user")
            assert user["id"] == "admin"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_app_password_subsequent_calls_work(self) -> None:
        app_pwd = _create_app_password()
        client = NextcloudClient(_make_config(password=app_pwd))
        try:
            await client.ocs_get("cloud/user")
            caps = await client.ocs_get("cloud/capabilities")
            assert "version" in caps
            notifications = await client.ocs_get("apps/notifications/api/v2/notifications")
            assert isinstance(notifications, list)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_app_password_webdav_works(self) -> None:
        app_pwd = _create_app_password()
        client = NextcloudClient(_make_config(password=app_pwd))
        try:
            await client.dav_mkcol(TEST_BASE_DIR)
            await client.dav_put(
                f"{TEST_BASE_DIR}/app-pwd-test.txt",
                b"hello",
                content_type="text/plain",
            )
            entries = await client.dav_propfind(TEST_BASE_DIR)
            names = [e["path"] for e in entries]
            assert any("app-pwd-test.txt" in n for n in names)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_app_password_ocs_write_works(self) -> None:
        app_pwd = _create_app_password()
        client = NextcloudClient(_make_config(password=app_pwd))
        try:
            await client.ocs_post(
                "apps/notifications/api/v1/admin_notifications/admin",
                data={"shortMessage": "app-pwd-session-test"},
            )
            notifications = await client.ocs_get("apps/notifications/api/v2/notifications")
            subjects = [n["subject"] for n in notifications]
            assert "app-pwd-session-test" in subjects
        finally:
            await client.close()


def _occ(command: str) -> str:
    return _run_occ(command).stdout.strip()


class TestSessionExpiryRecovery:
    @pytest.mark.asyncio
    async def test_recovers_after_session_expires(self) -> None:
        """After NC session_lifetime expires, client should re-authenticate transparently."""
        _occ("config:system:set session_lifetime --value=2 --type=integer")
        try:
            client = NextcloudClient(_make_config())
            try:
                user = await client.ocs_get("cloud/user")
                assert user["id"] == "admin"
                assert client._session is not None
                assert client._session.auth is None, "Session should be cached"
                await asyncio.sleep(4)
                user = await client.ocs_get("cloud/user")
                assert user["id"] == "admin"
            finally:
                await client.close()
        finally:
            _occ("config:system:delete session_lifetime")

    @pytest.mark.asyncio
    async def test_webdav_recovers_after_session_expires(self) -> None:
        """WebDAV operations should also recover after session expiry."""
        _occ("config:system:set session_lifetime --value=2 --type=integer")
        try:
            client = NextcloudClient(_make_config())
            try:
                await client.dav_mkcol(TEST_BASE_DIR)
                await asyncio.sleep(4)
                entries = await client.dav_propfind("/")
                assert isinstance(entries, list)
                assert len(entries) >= 1
            finally:
                await client.close()
        finally:
            _occ("config:system:delete session_lifetime")
