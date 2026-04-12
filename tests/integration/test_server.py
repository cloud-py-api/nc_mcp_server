"""Integration tests for the MCP server — tool registration, lifecycle, and configuration."""

import pytest

from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_server

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = [
    "add_comment",
    "assign_tag",
    "clear_user_status",
    "close_poll",
    "complete_task",
    "copy_file",
    "create_announcement",
    "create_collective",
    "create_collective_page",
    "create_contact",
    "create_conversation",
    "create_directory",
    "create_event",
    "create_poll",
    "create_share",
    "create_tag",
    "create_task",
    "create_user",
    "delete_announcement",
    "delete_collective",
    "delete_collective_page",
    "delete_comment",
    "delete_contact",
    "delete_event",
    "delete_file",
    "delete_message",
    "delete_share",
    "delete_tag",
    "delete_task",
    "delete_trash_item",
    "delete_user",
    "disable_app",
    "dismiss_all_notifications",
    "dismiss_notification",
    "edit_comment",
    "empty_trash",
    "enable_app",
    "get_activity",
    "get_app_info",
    "get_collective_page",
    "get_collective_pages",
    "get_contact",
    "get_contacts",
    "get_conversation",
    "get_current_user",
    "get_event",
    "get_events",
    "get_file",
    "get_file_tags",
    "get_mail_message",
    "get_messages",
    "get_participants",
    "get_poll",
    "get_share",
    "get_task",
    "get_tasks",
    "get_user",
    "get_user_status",
    "leave_conversation",
    "list_addressbooks",
    "list_announcements",
    "list_apps",
    "list_calendars",
    "list_collectives",
    "list_comments",
    "list_conversations",
    "list_directory",
    "list_mail_accounts",
    "list_mail_messages",
    "list_mailboxes",
    "list_notifications",
    "list_search_providers",
    "list_shares",
    "list_tags",
    "list_task_lists",
    "list_trash",
    "list_users",
    "list_versions",
    "move_file",
    "restore_collective",
    "restore_collective_page",
    "restore_trash_item",
    "restore_version",
    "search_files",
    "send_mail",
    "send_message",
    "set_user_status",
    "trash_collective",
    "trash_collective_page",
    "unassign_tag",
    "unified_search",
    "update_contact",
    "update_event",
    "update_share",
    "update_task",
    "upload_file",
    "vote_poll",
]


class TestServerCreation:
    @pytest.mark.asyncio
    async def test_all_tools_registered(self, nc_config: Config) -> None:
        mcp = create_server(nc_config)
        names = sorted(t.name for t in mcp._tool_manager.list_tools())
        assert names == EXPECTED_TOOLS

    @pytest.mark.asyncio
    async def test_tool_count(self, nc_config: Config) -> None:
        mcp = create_server(nc_config)
        assert len(mcp._tool_manager.list_tools()) == len(EXPECTED_TOOLS)

    @pytest.mark.asyncio
    async def test_every_tool_has_description(self, nc_config: Config) -> None:
        mcp = create_server(nc_config)
        for tool in mcp._tool_manager.list_tools():
            assert tool.description, f"Tool '{tool.name}' has no description"
            assert len(tool.description) > 20, f"Tool '{tool.name}' description too short"

    @pytest.mark.asyncio
    async def test_every_tool_has_annotations(self, nc_config: Config) -> None:
        mcp = create_server(nc_config)
        for tool in mcp._tool_manager.list_tools():
            assert tool.annotations is not None, f"Tool '{tool.name}' has no annotations"
            assert tool.annotations.readOnlyHint is not None, f"Tool '{tool.name}' missing readOnlyHint"

    @pytest.mark.asyncio
    async def test_server_name(self, nc_config: Config) -> None:
        mcp = create_server(nc_config)
        assert mcp.name == "nc-mcp-server"

    @pytest.mark.asyncio
    async def test_create_server_with_different_permissions(self) -> None:
        for level in PermissionLevel:
            config = Config(
                nextcloud_url="http://nextcloud.ncmcp",
                user="admin",
                password="admin",
                permission_level=level,
            )
            mcp = create_server(config)
            assert len(mcp._tool_manager.list_tools()) == len(EXPECTED_TOOLS)

    @pytest.mark.asyncio
    async def test_create_server_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NEXTCLOUD_URL", "http://nextcloud.ncmcp")
        monkeypatch.setenv("NEXTCLOUD_USER", "admin")
        monkeypatch.setenv("NEXTCLOUD_PASSWORD", "admin")
        mcp = create_server()
        assert len(mcp._tool_manager.list_tools()) == len(EXPECTED_TOOLS)
