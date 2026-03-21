"""Tests for the permission model."""

import pytest

from nextcloud_mcp.permissions import (
    PermissionDeniedError,
    PermissionLevel,
    require_permission,
    set_permission_level,
)


class TestPermissionLevel:
    def test_read_includes_read(self) -> None:
        assert PermissionLevel.READ.includes(PermissionLevel.READ)

    def test_read_excludes_write(self) -> None:
        assert not PermissionLevel.READ.includes(PermissionLevel.WRITE)

    def test_read_excludes_destructive(self) -> None:
        assert not PermissionLevel.READ.includes(PermissionLevel.DESTRUCTIVE)

    def test_write_includes_read(self) -> None:
        assert PermissionLevel.WRITE.includes(PermissionLevel.READ)

    def test_write_includes_write(self) -> None:
        assert PermissionLevel.WRITE.includes(PermissionLevel.WRITE)

    def test_write_excludes_destructive(self) -> None:
        assert not PermissionLevel.WRITE.includes(PermissionLevel.DESTRUCTIVE)

    def test_destructive_includes_all(self) -> None:
        assert PermissionLevel.DESTRUCTIVE.includes(PermissionLevel.READ)
        assert PermissionLevel.DESTRUCTIVE.includes(PermissionLevel.WRITE)
        assert PermissionLevel.DESTRUCTIVE.includes(PermissionLevel.DESTRUCTIVE)


class TestRequirePermission:
    @pytest.fixture(autouse=True)
    def _reset_level(self) -> None:
        """Reset permission level before each test."""
        set_permission_level(PermissionLevel.READ)

    @pytest.mark.asyncio
    async def test_read_tool_with_read_permission(self) -> None:
        set_permission_level(PermissionLevel.READ)

        @require_permission(PermissionLevel.READ)
        async def read_tool() -> str:
            return "ok"

        assert await read_tool() == "ok"

    @pytest.mark.asyncio
    async def test_write_tool_with_read_permission_raises(self) -> None:
        set_permission_level(PermissionLevel.READ)

        @require_permission(PermissionLevel.WRITE)
        async def write_tool() -> str:
            return "ok"

        with pytest.raises(PermissionDeniedError) as exc_info:
            await write_tool()
        assert "write" in str(exc_info.value)
        assert "NEXTCLOUD_MCP_PERMISSIONS" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_destructive_tool_with_write_permission_raises(self) -> None:
        set_permission_level(PermissionLevel.WRITE)

        @require_permission(PermissionLevel.DESTRUCTIVE)
        async def delete_tool() -> str:
            return "ok"

        with pytest.raises(PermissionDeniedError):
            await delete_tool()

    @pytest.mark.asyncio
    async def test_destructive_tool_with_destructive_permission(self) -> None:
        set_permission_level(PermissionLevel.DESTRUCTIVE)

        @require_permission(PermissionLevel.DESTRUCTIVE)
        async def delete_tool() -> str:
            return "ok"

        assert await delete_tool() == "ok"

    @pytest.mark.asyncio
    async def test_write_tool_with_destructive_permission(self) -> None:
        set_permission_level(PermissionLevel.DESTRUCTIVE)

        @require_permission(PermissionLevel.WRITE)
        async def write_tool() -> str:
            return "ok"

        assert await write_tool() == "ok"


class TestPermissionDeniedError:
    def test_message_includes_tool_name(self) -> None:
        err = PermissionDeniedError("upload_file", PermissionLevel.WRITE, PermissionLevel.READ)
        assert "upload_file" in str(err)

    def test_message_includes_instructions(self) -> None:
        err = PermissionDeniedError("delete_file", PermissionLevel.DESTRUCTIVE, PermissionLevel.READ)
        assert "NEXTCLOUD_MCP_PERMISSIONS=destructive" in str(err)
