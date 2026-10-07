"""CLI entry point for the Nextcloud MCP server."""

import argparse

import uvicorn

from .config import Config
from .server import create_http_app, create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Nextcloud MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode: stdio (default, for local use) or http (for remote/container)",
    )
    args = parser.parse_args()

    config = Config.from_env()
    if config.multiuser and args.transport != "http":
        parser.error("NEXTCLOUD_MCP_MULTIUSER=true needs --transport http (logins come from HTTP headers)")
    mcp = create_server(config)

    if args.transport == "http":
        if config.multiuser:
            uvicorn.run(create_http_app(mcp, config), host=config.host, port=config.port, log_level="info")
        else:
            mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
