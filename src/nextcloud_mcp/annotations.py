"""Shared MCP ToolAnnotations constants for Nextcloud tools."""

from mcp.types import ToolAnnotations

READONLY = ToolAnnotations(readOnlyHint=True)
ADDITIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
ADDITIVE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
DESTRUCTIVE_NON_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
