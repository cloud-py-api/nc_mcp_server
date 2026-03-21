"""Permission model for controlling what operations AI clients can perform."""

import enum
import functools
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class PermissionLevel(enum.Enum):
    """Permission levels — each includes all lower levels.

    READ: List and retrieve data. Safe, no side effects.
    WRITE: Create and modify data. Has side effects but non-destructive.
    DESTRUCTIVE: Delete, remove, and dangerous operations.
    """

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

    def includes(self, required: "PermissionLevel") -> bool:
        """Check if this permission level includes the required level."""
        order = {
            PermissionLevel.READ: 0,
            PermissionLevel.WRITE: 1,
            PermissionLevel.DESTRUCTIVE: 2,
        }
        return order[self] >= order[required]


class PermissionDeniedError(Exception):
    """Raised when an operation requires a higher permission level."""

    def __init__(self, tool_name: str, required: PermissionLevel, current: PermissionLevel) -> None:
        self.tool_name = tool_name
        self.required = required
        self.current = current
        super().__init__(
            f"Tool '{tool_name}' requires '{required.value}' permission, "
            f"but current level is '{current.value}'. "
            f"Set NEXTCLOUD_MCP_PERMISSIONS={required.value} (or higher) to enable this tool."
        )


# Global permission level — set at server startup
_current_level: PermissionLevel = PermissionLevel.READ


def set_permission_level(level: PermissionLevel) -> None:
    """Set the global permission level. Called once at server startup."""
    global _current_level
    _current_level = level


def get_permission_level() -> PermissionLevel:
    """Get the current global permission level."""
    return _current_level


def require_permission(level: PermissionLevel) -> Callable[[F], F]:
    """Decorator that checks permission before executing a tool.

    Usage:
        @mcp.tool()
        @require_permission(PermissionLevel.WRITE)
        async def upload_file(...) -> str:
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current = get_permission_level()
            if not current.includes(level):
                raise PermissionDeniedError(func.__name__, level, current)
            return await func(*args, **kwargs)

        # Store the required level on the function for introspection
        wrapper._required_permission = level  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
