"""Integration test fixtures — require a running Nextcloud instance."""

import contextlib
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from nc_mcp_server.client import NextcloudClient
from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_server
from nc_mcp_server.state import get_client

pytestmark = pytest.mark.integration

# Test data constants
TEST_BASE_DIR = "mcp-test-suite"


class McpTestHelper:
    """Helper that wraps FastMCP for easy tool calling in tests."""

    def __init__(self, mcp: FastMCP, client: NextcloudClient) -> None:
        self.mcp = mcp
        self.client = client

    async def call(self, tool_name: str, **kwargs: object) -> str:
        """Call an MCP tool by name and return its string result."""
        result = await self.mcp._tool_manager.call_tool(tool_name, dict(kwargs))
        if not isinstance(result, list):
            return str(result)
        items: list[TextContent | ImageContent] = result  # type: ignore[assignment]
        parts: list[str] = []
        for item in items:
            if isinstance(item, TextContent):
                parts.append(item.text)
            else:
                parts.append(f"[Image: {item.mimeType}]")
        return parts[0] if len(parts) == 1 else "\n".join(parts)

    def tool_names(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(t.name for t in self.mcp._tool_manager.list_tools())

    async def generate_notification(self, subject: str = "test", message: str = "body") -> None:
        """Create a test notification via the admin_notifications API."""
        await self.client.ocs_post(
            "apps/notifications/api/v1/admin_notifications/admin",
            data={"shortMessage": subject, "longMessage": message},
        )

    async def create_test_dir(self, path: str = TEST_BASE_DIR) -> None:
        """Create the test directory, ignoring if it already exists."""
        with contextlib.suppress(Exception):
            await self.client.dav_mkcol(path)

    async def upload_test_file(self, path: str, content: str = "test content") -> None:
        """Upload a test file via the client (bypasses MCP permission checks)."""
        await self.client.dav_put(path, content.encode("utf-8"), content_type="text/plain; charset=utf-8")


def _get_integration_config(
    permission: PermissionLevel = PermissionLevel.DESTRUCTIVE,
    upload_root: str = "",
) -> Config:
    """Build config from environment, with defaults for local dev."""
    return Config(
        nextcloud_url=os.environ.get("NEXTCLOUD_URL", "http://nextcloud.ncmcp"),
        user=os.environ.get("NEXTCLOUD_USER", "admin"),
        password=os.environ.get("NEXTCLOUD_PASSWORD", "admin"),
        permission_level=permission,
        is_app_password=os.environ.get("NEXTCLOUD_MCP_APP_PASSWORD", "").lower() in ("true", "1", "yes"),
        upload_root=upload_root,
    )


@pytest.fixture
def nc_config() -> Config:
    """Nextcloud config for integration tests."""
    config = _get_integration_config()
    config.validate()
    return config


@pytest.fixture
async def nc_client(nc_config: Config) -> AsyncGenerator[NextcloudClient]:
    """Nextcloud HTTP client for integration tests. Closes after test."""
    client = NextcloudClient(nc_config)
    yield client
    await client.close()


@pytest.fixture
async def nc_mcp(nc_config: Config) -> AsyncGenerator[McpTestHelper]:
    """Full MCP server with all tools registered (DESTRUCTIVE permissions)."""
    mcp = create_server(nc_config)
    helper = McpTestHelper(mcp, get_client())
    yield helper
    await helper.client.close()


@pytest.fixture
async def nc_mcp_read_only() -> AsyncGenerator[McpTestHelper]:
    """MCP server with READ-only permissions for permission enforcement tests."""
    config = _get_integration_config(PermissionLevel.READ)
    config.validate()
    mcp = create_server(config)
    helper = McpTestHelper(mcp, get_client())
    yield helper
    await helper.client.close()


@pytest.fixture
async def nc_mcp_write() -> AsyncGenerator[McpTestHelper]:
    """MCP server with WRITE permissions for permission enforcement tests."""
    config = _get_integration_config(PermissionLevel.WRITE)
    config.validate()
    mcp = create_server(config)
    helper = McpTestHelper(mcp, get_client())
    yield helper
    await helper.client.close()


def _build_upload_config(root: Path, permission: PermissionLevel) -> Config:
    """Resolve an upload root outside the async fixture to satisfy ASYNC240."""
    return _get_integration_config(permission, upload_root=str(root.resolve()))


@pytest.fixture
async def nc_mcp_uploads(tmp_path: Path) -> AsyncGenerator[tuple[McpTestHelper, Path]]:
    """MCP server with NEXTCLOUD_MCP_UPLOAD_ROOT=tmp_path — enables upload_file_from_path."""
    config = _build_upload_config(tmp_path, PermissionLevel.DESTRUCTIVE)
    config.validate()
    mcp = create_server(config)
    helper = McpTestHelper(mcp, get_client())
    yield helper, Path(config.upload_root)
    await helper.client.close()


@pytest.fixture
async def nc_mcp_uploads_read_only(tmp_path: Path) -> AsyncGenerator[tuple[McpTestHelper, Path]]:
    """Read-only MCP server with upload_root set — verifies permission gate on upload_file_from_path."""
    config = _build_upload_config(tmp_path, PermissionLevel.READ)
    config.validate()
    mcp = create_server(config)
    helper = McpTestHelper(mcp, get_client())
    yield helper, Path(config.upload_root)
    await helper.client.close()


@pytest.fixture(scope="session")
def _cleanup_config() -> Config:
    """Config for cleanup client — session-scoped to avoid repeated creation."""
    config = _get_integration_config()
    config.validate()
    return config


@pytest.fixture(autouse=True)
async def _clean_test_data(_cleanup_config: Config) -> AsyncGenerator[None]:
    """Clean up test data before and after each test."""
    client = NextcloudClient(_cleanup_config)
    await _cleanup(client)
    yield
    await _cleanup(client)
    await client.close()


async def _cleanup_shares(client: NextcloudClient) -> None:
    with contextlib.suppress(Exception):
        shares = await client.ocs_get("apps/files_sharing/api/v1/shares")
        for share in shares:
            share_path = str(share.get("path", ""))
            if share_path != f"/{TEST_BASE_DIR}" and not share_path.startswith(f"/{TEST_BASE_DIR}/"):
                continue
            with contextlib.suppress(Exception):
                await client.ocs_delete(f"apps/files_sharing/api/v1/shares/{share['id']}")


async def _cleanup_announcements(client: NextcloudClient) -> None:
    with contextlib.suppress(Exception):
        while True:
            announcements = await client.ocs_get("apps/announcementcenter/api/v1/announcements")
            if not announcements:
                break
            deleted = False
            for ann in announcements:
                subject = str(ann.get("subject", ""))
                if subject.startswith("mcp-test-ann"):
                    with contextlib.suppress(Exception):
                        await client.ocs_delete(f"apps/announcementcenter/api/v1/announcements/{ann['id']}")
                    deleted = True
            if not deleted:
                break


async def _cleanup_forms(client: NextcloudClient) -> None:
    with contextlib.suppress(Exception):
        forms: list[dict[str, object]] = await client.ocs_get("apps/forms/api/v3/forms", params={"type": "owned"})
        for form in forms or []:
            title = str(form.get("title", ""))
            if title.startswith("mcp-test-"):
                with contextlib.suppress(Exception):
                    await client.ocs_delete(f"apps/forms/api/v3/forms/{form['id']}")


async def _cleanup_circles(client: NextcloudClient) -> None:
    with contextlib.suppress(Exception):
        circles: list[dict[str, object]] = await client.ocs_get("apps/circles/circles")
        for circle in circles or []:
            name = str(circle.get("name", ""))
            if name.startswith("mcp-test-circle-"):
                with contextlib.suppress(Exception):
                    await client.ocs_delete(f"apps/circles/circles/{circle['id']}")


async def _cleanup_cospend(client: NextcloudClient) -> None:
    with contextlib.suppress(Exception):
        projects: list[dict[str, object]] = await client.ocs_get("apps/cospend/api/v1/projects")
        for project in projects or []:
            project_id = str(project.get("id", ""))
            if project_id.startswith("mcp-test"):
                with contextlib.suppress(Exception):
                    await client.ocs_delete(f"apps/cospend/api/v1/projects/{quote(project_id, safe='')}")


async def cleanup_flows(client: NextcloudClient) -> None:
    """Delete Flow rules whose name starts with mcp-test; a stray global one would act on later tests' files."""
    for scope in ("user", "global"):
        with contextlib.suppress(Exception):
            grouped: dict[str, list[dict[str, Any]]] | list[Any] = await client.ocs_get(
                f"apps/workflowengine/api/v1/workflows/{scope}"
            )
            flows = [flow for group in grouped.values() for flow in group] if isinstance(grouped, dict) else []
            for flow in flows:
                if str(flow.get("name", "")).startswith("mcp-test"):
                    with contextlib.suppress(Exception):
                        await client.ocs_delete(f"apps/workflowengine/api/v1/workflows/{scope}/{flow['id']}")


async def _cleanup(client: NextcloudClient) -> None:
    """Remove all test artifacts from Nextcloud."""
    await _cleanup_shares(client)
    with contextlib.suppress(Exception):
        await client.dav_delete(TEST_BASE_DIR)
    with contextlib.suppress(Exception):
        await client.ocs_delete("apps/notifications/api/v2/notifications")
    with contextlib.suppress(Exception):
        await client.ocs_delete("apps/user_status/api/v1/user_status/message")
    with contextlib.suppress(Exception):
        await client.ocs_put("apps/user_status/api/v1/user_status/status", data={"statusType": "online"})
    await _cleanup_announcements(client)
    await _cleanup_forms(client)
    await _cleanup_circles(client)
    await _cleanup_cospend(client)
