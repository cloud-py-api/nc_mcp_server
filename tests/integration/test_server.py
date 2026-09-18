"""Integration tests for the MCP server — tool registration, lifecycle, and configuration."""

from pathlib import Path

import pytest

from nc_mcp_server.config import Config
from nc_mcp_server.permissions import PermissionLevel
from nc_mcp_server.server import create_server

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = [
    "add_circle_member",
    "add_comment",
    "add_mail_message_tag",
    "assign_tag",
    "clear_user_status",
    "close_poll",
    "complete_task",
    "copy_file",
    "create_announcement",
    "create_circle",
    "create_collective",
    "create_collective_page",
    "create_contact",
    "create_conversation",
    "create_cospend_bill",
    "create_cospend_member",
    "create_cospend_project",
    "create_directory",
    "create_event",
    "create_form",
    "create_form_share",
    "create_mail_tag",
    "create_options",
    "create_poll",
    "create_question",
    "create_share",
    "create_tag",
    "create_task",
    "create_user",
    "delete_all_submissions",
    "delete_announcement",
    "delete_circle",
    "delete_collective",
    "delete_collective_page",
    "delete_comment",
    "delete_contact",
    "delete_cospend_bill",
    "delete_cospend_member",
    "delete_cospend_project",
    "delete_event",
    "delete_file",
    "delete_form",
    "delete_form_share",
    "delete_message",
    "delete_option",
    "delete_question",
    "delete_share",
    "delete_submission",
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
    "export_submissions",
    "get_activity",
    "get_app_info",
    "get_circle",
    "get_collective_page",
    "get_collective_pages",
    "get_contact",
    "get_contacts",
    "get_conversation",
    "get_cospend_bill",
    "get_cospend_project",
    "get_cospend_project_settlement",
    "get_cospend_project_statistics",
    "get_current_user",
    "get_event",
    "get_events",
    "get_file",
    "get_file_reminder",
    "get_file_tags",
    "get_form",
    "get_mail_message",
    "get_messages",
    "get_participants",
    "get_poll",
    "get_question",
    "get_share",
    "get_submission",
    "get_task",
    "get_tasks",
    "get_thread",
    "get_user",
    "get_user_status",
    "join_circle",
    "leave_circle",
    "leave_conversation",
    "list_addressbooks",
    "list_announcements",
    "list_apps",
    "list_calendars",
    "list_circle_members",
    "list_circles",
    "list_collectives",
    "list_comments",
    "list_conversations",
    "list_cospend_bills",
    "list_cospend_members",
    "list_cospend_projects",
    "list_directory",
    "list_forms",
    "list_mail_accounts",
    "list_mail_messages",
    "list_mailboxes",
    "list_notifications",
    "list_questions",
    "list_search_providers",
    "list_shares",
    "list_submissions",
    "list_subscribed_threads",
    "list_tags",
    "list_task_lists",
    "list_threads",
    "list_trash",
    "list_users",
    "list_versions",
    "move_file",
    "move_mail_message",
    "remove_circle_member",
    "remove_file_reminder",
    "remove_mail_message_tag",
    "rename_thread",
    "reorder_options",
    "reorder_questions",
    "restore_collective",
    "restore_collective_page",
    "restore_trash_item",
    "restore_version",
    "search_circles",
    "search_files",
    "send_mail",
    "send_message",
    "set_file_reminder",
    "set_mail_message_flags",
    "set_thread_notification_level",
    "set_user_status",
    "submit_form",
    "trash_collective",
    "trash_collective_page",
    "unassign_tag",
    "unified_search",
    "update_circle_config",
    "update_circle_description",
    "update_circle_member_level",
    "update_circle_name",
    "update_contact",
    "update_cospend_bill",
    "update_cospend_member",
    "update_cospend_project",
    "update_event",
    "update_form",
    "update_form_share",
    "update_option",
    "update_question",
    "update_share",
    "update_submission",
    "update_task",
    "upload_file",
    "upload_file_binary",
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
        monkeypatch.delenv("NEXTCLOUD_MCP_UPLOAD_ROOT", raising=False)
        mcp = create_server()
        assert len(mcp._tool_manager.list_tools()) == len(EXPECTED_TOOLS)

    @pytest.mark.asyncio
    async def test_upload_from_path_tool_gated_on_upload_root(self, nc_config: Config, tmp_path: Path) -> None:
        """upload_file_from_path is registered only when upload_root is configured."""
        mcp_no_root = create_server(nc_config)
        names_no_root = {t.name for t in mcp_no_root._tool_manager.list_tools()}
        assert "upload_file_from_path" not in names_no_root
        assert len(names_no_root) == len(EXPECTED_TOOLS)

        resolved_root = str(tmp_path)  # tmp_path is already absolute; tool resolves symlinks at call time
        config_with_root = Config(
            nextcloud_url=nc_config.nextcloud_url,
            user=nc_config.user,
            password=nc_config.password,
            permission_level=nc_config.permission_level,
            upload_root=resolved_root,
        )
        mcp_with_root = create_server(config_with_root)
        names_with_root = {t.name for t in mcp_with_root._tool_manager.list_tools()}
        assert "upload_file_from_path" in names_with_root
        assert len(names_with_root) == len(EXPECTED_TOOLS) + 1
