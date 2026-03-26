# Nextcloud MCP Server

[![Lint](https://github.com/cloud-py-api/nc-mcp-server/actions/workflows/lint.yml/badge.svg)](https://github.com/cloud-py-api/nc-mcp-server/actions/workflows/lint.yml)
[![Unit Tests](https://github.com/cloud-py-api/nc-mcp-server/actions/workflows/tests-unit.yml/badge.svg)](https://github.com/cloud-py-api/nc-mcp-server/actions/workflows/tests-unit.yml)
[![Integration Tests](https://github.com/cloud-py-api/nc-mcp-server/actions/workflows/tests-integration.yml/badge.svg)](https://github.com/cloud-py-api/nc-mcp-server/actions/workflows/tests-integration.yml)
[![codecov](https://codecov.io/gh/cloud-py-api/nc-mcp-server/graph/badge.svg)](https://codecov.io/gh/cloud-py-api/nc-mcp-server)

![NextcloudVersion](https://img.shields.io/badge/Nextcloud-32%20%7C%2033-blue)
![PythonVersion](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue)
[![Python](https://img.shields.io/pypi/implementation/nc-mcp-server)](https://pypi.org/project/nc-mcp-server/)
[![PyPI](https://img.shields.io/pypi/v/nc-mcp-server.svg)](https://pypi.org/project/nc-mcp-server/)
[![License: MIT](https://img.shields.io/github/license/cloud-py-api/nc-mcp-server)](https://github.com/cloud-py-api/nc-mcp-server/blob/main/LICENSE)

> **Experimental** — This repository is fully maintained by AI (Claude). It serves as an experiment in autonomous AI-driven open-source development.

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes Nextcloud APIs as tools for AI assistants. Connect any MCP-compatible client (Claude Desktop, Claude Code, etc.) to your Nextcloud instance and let AI manage files, read notifications, interact with Talk, and more.

## Features

- **File Management** — List, read, search, upload, move, and delete files via WebDAV
- **User Info** — Get current user, list users, view user details
- **Notifications** — List and dismiss notifications
- **Activity Feed** — View recent activity with filtering and pagination
- **Talk** — List conversations, read and send messages, manage polls
- **Comments** — List, add, edit, and delete file comments
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
pip install nc-mcp-server
```

Or with `pipx` / `uvx` for isolated installation:
```bash
pipx install nc-mcp-server
# or
uvx nc-mcp-server
```

Or from source:
```bash
git clone https://github.com/cloud-py-api/nc-mcp-server.git
cd nc-mcp-server
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
export NEXTCLOUD_MCP_RETRY_MAX=3       # max retries on 429/503 (default: 3, 0 to disable)
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

### With Claude Code

```bash
claude mcp add nextcloud \
  -e NEXTCLOUD_URL=https://your-nextcloud.example.com \
  -e NEXTCLOUD_USER=your-username \
  -e NEXTCLOUD_PASSWORD=your-app-password \
  -e NEXTCLOUD_MCP_PERMISSIONS=read \
  -- nextcloud-mcp
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

### Files

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_directory(path)` | read | List files and folders |
| `get_file(path)` | read | Read a file's content |
| `search_files(name, mime_type, ...)` | read | Search files by name, type, or path |
| `upload_file(path, content)` | write | Upload or overwrite a file |
| `create_directory(path)` | write | Create a new directory |
| `delete_file(path)` | destructive | Delete a file or directory |
| `move_file(source, destination)` | destructive | Move or rename a file |

### Users

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_current_user()` | read | Get current user info |
| `list_users(search, limit)` | read | List/search users |
| `get_user(user_id)` | read | Get specific user details |

### Notifications

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_notifications()` | read | List all notifications |
| `dismiss_notification(id)` | write | Dismiss a single notification |
| `dismiss_all_notifications()` | write | Dismiss all notifications |

### Activity

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_activity(filter, sort, limit, since)` | read | View recent activity with filtering and pagination |

### Talk

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_conversations()` | read | List all Talk conversations |
| `get_conversation(token)` | read | Get conversation details |
| `get_messages(token, ...)` | read | Get messages from a conversation |
| `get_participants(token)` | read | List participants in a conversation |
| `send_message(token, message)` | write | Send a message to a conversation |
| `create_conversation(name, ...)` | write | Create a new conversation |
| `delete_message(token, id)` | destructive | Delete a message |
| `leave_conversation(token)` | destructive | Leave a conversation |

### Talk Polls

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_poll(token, poll_id)` | read | Get poll details and results |
| `create_poll(token, question, options)` | write | Create a poll in a conversation |
| `vote_poll(token, poll_id, options)` | write | Vote on a poll |
| `close_poll(token, poll_id)` | write | Close a poll |

### Comments

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_comments(path, ...)` | read | List comments on a file |
| `add_comment(path, message)` | write | Add a comment to a file |
| `edit_comment(path, comment_id, message)` | write | Edit a comment |
| `delete_comment(path, comment_id)` | destructive | Delete a comment |

### Coming Soon

- **Shares** — manage file shares
- **Trashbin** — view and restore deleted files
- **File Versions** — list and restore file versions
- **Calendar** — events via CalDAV
- **Contacts** — contacts via CardDAV
- **Deck** — boards and cards
- **Notes** — manage notes

## Development

```bash
# Clone and install
git clone https://github.com/cloud-py-api/nc-mcp-server.git
cd nc-mcp-server
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

CI automatically runs integration tests against Nextcloud 32 and 33 Docker containers.

## About This Project

This project is an experiment in AI-autonomous open-source development. The entire codebase — including this README — is written and maintained by Claude (Anthropic's AI assistant). Human oversight is limited to:

- High-level design decisions
- Code review of pull requests
- Resolving architectural questions

The goal is to explore how far autonomous AI development can go in building production-quality, well-tested software.
