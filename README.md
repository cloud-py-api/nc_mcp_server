# Nextcloud MCP Server

[![Lint](https://github.com/cloud-py-api/nextcloud-mcp-server/actions/workflows/lint.yml/badge.svg)](https://github.com/cloud-py-api/nextcloud-mcp-server/actions/workflows/lint.yml)
[![Unit Tests](https://github.com/cloud-py-api/nextcloud-mcp-server/actions/workflows/tests-unit.yml/badge.svg)](https://github.com/cloud-py-api/nextcloud-mcp-server/actions/workflows/tests-unit.yml)
[![Integration Tests](https://github.com/cloud-py-api/nextcloud-mcp-server/actions/workflows/tests-integration.yml/badge.svg)](https://github.com/cloud-py-api/nextcloud-mcp-server/actions/workflows/tests-integration.yml)
[![codecov](https://codecov.io/gh/cloud-py-api/nextcloud-mcp-server/graph/badge.svg)](https://codecov.io/gh/cloud-py-api/nextcloud-mcp-server)

![PythonVersion](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue)

> **Experimental** — This repository is fully maintained by AI (Claude). It serves as an experiment in autonomous AI-driven open-source development.

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes Nextcloud APIs as tools for AI assistants. Connect any MCP-compatible client (Claude Desktop, Claude Code, etc.) to your Nextcloud instance and let AI manage files, read notifications, interact with Talk, and more.

## Features

- **File Management** — List, read, upload, move, and delete files via WebDAV
- **User Info** — Get current user, list users, view user details
- **Notifications** — List and dismiss notifications (coming soon)
- **Activity Feed** — View recent activity (coming soon)
- **Talk** — List conversations, read and send messages (coming soon)
- **Security-First** — Granular permission levels control what AI can do

## Security: Permission Model

Every tool has a required permission level. You control what the AI is allowed to do:

| Level | What it can do | Environment variable |
|-------|---------------|---------------------|
| `read` (default) | List files, read files, get users, view notifications | `NEXTCLOUD_MCP_PERMISSIONS=read` |
| `write` | Everything in `read` + upload files, send messages, create folders | `NEXTCLOUD_MCP_PERMISSIONS=write` |
| `destructive` | Everything in `write` + delete files, remove shares | `NEXTCLOUD_MCP_PERMISSIONS=destructive` |

If a tool is called without sufficient permission, it returns a clear error explaining what permission is needed — no silent failures, no accidental deletions.

## Installation

```bash
pip install nextcloud-mcp-server
```

Or from source:
```bash
git clone https://github.com/cloud-py-api/nextcloud-mcp-server.git
cd nextcloud-mcp-server
pip install -e .
```

## Configuration

Set these environment variables:

```bash
# Required
export NEXTCLOUD_URL=https://your-nextcloud.example.com
export NEXTCLOUD_USER=your-username
export NEXTCLOUD_PASSWORD=your-app-password  # Use an app password, not your main password!

# Optional
export NEXTCLOUD_MCP_PERMISSIONS=read  # read (default), write, or destructive
```

### Getting an App Password

1. Log into your Nextcloud instance
2. Go to **Settings** → **Security**
3. Under "Devices & sessions", create a new app password
4. Use this password for `NEXTCLOUD_PASSWORD`

## Usage

### With Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nextcloud": {
      "command": "nextcloud-mcp",
      "env": {
        "NEXTCLOUD_URL": "https://your-nextcloud.example.com",
        "NEXTCLOUD_USER": "your-username",
        "NEXTCLOUD_PASSWORD": "your-app-password",
        "NEXTCLOUD_MCP_PERMISSIONS": "read"
      }
    }
  }
}
```

### As HTTP Server (for containers/remote)

```bash
nextcloud-mcp --transport http
# Listens on http://0.0.0.0:8100 by default
```

### Stdio Mode (default)

```bash
nextcloud-mcp
# Communicates via stdin/stdout — used by MCP clients like Claude Desktop
```

## Available Tools

### Files (Phase 1 — available now)

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_directory(path)` | read | List files and folders |
| `get_file(path)` | read | Read a file's content |
| `upload_file(path, content)` | write | Upload or overwrite a file |
| `create_directory(path)` | write | Create a new directory |
| `delete_file(path)` | destructive | Delete a file or directory |
| `move_file(source, destination)` | destructive | Move or rename a file |

### Users (Phase 1 — available now)

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_current_user()` | read | Get current user info |
| `list_users(search, limit)` | read | List/search users |
| `get_user(user_id)` | read | Get specific user details |

### Coming Soon

- **Notifications** — list and dismiss
- **Activity** — recent activity feed
- **Talk** — conversations and messages
- **Shares** — manage file shares
- **Calendar** — events via CalDAV
- **Contacts** — contacts via CardDAV
- **Deck** — boards and cards

## Development

```bash
# Clone and install
git clone https://github.com/cloud-py-api/nextcloud-mcp-server.git
cd nextcloud-mcp-server
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest                              # Unit tests
pytest tests/integration/ -v        # Integration tests (needs running Nextcloud)

# Lint & type check
ruff check . && ruff format --check .
pyright
```

### Integration Tests

Integration tests run against a real Nextcloud instance. Set the environment variables and run:

```bash
export NEXTCLOUD_URL=http://localhost:8080
export NEXTCLOUD_USER=admin
export NEXTCLOUD_PASSWORD=admin
pytest tests/integration/ -v -m integration
```

CI automatically runs integration tests against a fresh Nextcloud Docker container.

## About This Project

This project is an experiment in AI-autonomous open-source development. The entire codebase — including this README — is written and maintained by Claude (Anthropic's AI assistant). Human oversight is limited to:

- High-level design decisions
- Code review of pull requests
- Resolving architectural questions

The goal is to explore how far autonomous AI development can go in building production-quality, well-tested software.

## License

MIT
