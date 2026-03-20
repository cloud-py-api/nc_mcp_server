"""CLI entry point for the Nextcloud MCP server."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Nextcloud MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode: stdio (default, for local use) or http (for remote/container)",
    )
    args = parser.parse_args()

    from .server import create_server

    mcp = create_server()

    if args.transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
