"""Tests for HTTP client retry configuration."""

import pytest

from nextcloud_mcp.client import NextcloudClient
from nextcloud_mcp.config import Config


def _make_config(retry_max: int = 3) -> Config:
    return Config(
        nextcloud_url="http://localhost",
        user="admin",
        password="admin",
        retry_max=retry_max,
    )


class TestRetryConfiguration:
    @pytest.mark.asyncio
    async def test_retry_enabled_by_default(self) -> None:
        client = NextcloudClient(_make_config())
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.total == 3
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_status_forcelist(self) -> None:
        client = NextcloudClient(_make_config())
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert 429 in adapter.max_retries.status_forcelist
            assert 503 in adapter.max_retries.status_forcelist
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_respects_retry_after(self) -> None:
        client = NextcloudClient(_make_config())
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.respect_retry_after_header is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_custom_max(self) -> None:
        client = NextcloudClient(_make_config(retry_max=5))
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.total == 5
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_disabled_when_zero(self) -> None:
        client = NextcloudClient(_make_config(retry_max=0))
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.total == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_allows_all_methods(self) -> None:
        client = NextcloudClient(_make_config())
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.allowed_methods is None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_backoff_factor(self) -> None:
        client = NextcloudClient(_make_config())
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.backoff_factor == 1.0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_no_connect_retries(self) -> None:
        client = NextcloudClient(_make_config())
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.connect == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_no_read_retries(self) -> None:
        client = NextcloudClient(_make_config())
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.read == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_retry_raise_on_status_disabled(self) -> None:
        client = NextcloudClient(_make_config())
        session = await client._get_session()
        try:
            adapter = session.get_adapter("http://localhost")
            assert adapter.max_retries.raise_on_status is False
        finally:
            await client.close()
