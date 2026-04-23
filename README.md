# Nextcloud MCP Server

[![Lint](https://github.com/cloud-py-api/nc_mcp_server/actions/workflows/lint.yml/badge.svg)](https://github.com/cloud-py-api/nc_mcp_server/actions/workflows/lint.yml)
[![Unit Tests](https://github.com/cloud-py-api/nc_mcp_server/actions/workflows/tests-unit.yml/badge.svg)](https://github.com/cloud-py-api/nc_mcp_server/actions/workflows/tests-unit.yml)
[![Integration Tests](https://github.com/cloud-py-api/nc_mcp_server/actions/workflows/tests-integration.yml/badge.svg)](https://github.com/cloud-py-api/nc_mcp_server/actions/workflows/tests-integration.yml)
[![codecov](https://codecov.io/gh/cloud-py-api/nc_mcp_server/graph/badge.svg)](https://codecov.io/gh/cloud-py-api/nc_mcp_server)

![NextcloudVersion](https://img.shields.io/badge/Nextcloud-32%20%7C%2033-blue)
![PythonVersion](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue)
[![Python](https://img.shields.io/pypi/implementation/nc-mcp-server)](https://pypi.org/project/nc-mcp-server/)
[![PyPI](https://img.shields.io/pypi/v/nc-mcp-server.svg)](https://pypi.org/project/nc-mcp-server/)
[![License: MIT](https://img.shields.io/github/license/cloud-py-api/nc_mcp_server)](https://github.com/cloud-py-api/nc_mcp_server/blob/main/LICENSE)

> **Experimental** — This repository is fully maintained by AI (Claude). It serves as an experiment in autonomous AI-driven open-source development.

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes Nextcloud APIs as tools for AI assistants. Connect any MCP-compatible client (Claude Desktop, Claude Code, etc.) to your Nextcloud instance and let AI manage your files, calendar, contacts, conversations, and more.

## Quick Start

```bash
pip install nc-mcp-server
```

Set environment variables and connect:

```bash
export NEXTCLOUD_URL=https://your-nextcloud.example.com
export NEXTCLOUD_USER=your-username
export NEXTCLOUD_PASSWORD=your-app-password
nc-mcp-server
```

## 101 Tools Across 21 Nextcloud Apps

A 102nd tool, `upload_file_from_path`, is registered only when the operator sets
`NEXTCLOUD_MCP_UPLOAD_ROOT`. See [Files](#files) for details.

| Category | Tools | Protocol |
|----------|-------|----------|
| [Files](#files) | list, read, search, upload (text / binary / from path), copy, move, delete | WebDAV |
| [File Sharing](#file-sharing) | list, get, create, update, delete shares | OCS |
| [Trashbin](#trashbin) | list, restore, delete item, empty trash | WebDAV |
| [File Versions](#file-versions) | list, restore versions | WebDAV |
| [File Comments](#file-comments) | list, add, edit, delete comments | WebDAV |
| [File Reminders](#file-reminders) | get, set, remove per-file reminders | OCS |
| [System Tags](#system-tags) | list, create, assign, unassign, delete tags | WebDAV |
| [Users](#users) | get current, list, get, create, delete users | OCS |
| [User Status](#user-status) | get, set, clear status | OCS |
| [Notifications](#notifications) | list, dismiss one, dismiss all | OCS |
| [Activity](#activity) | get activity feed with filtering | OCS |
| [Talk](#talk) | conversations, messages, participants | OCS |
| [Talk Polls](#talk-polls) | get, create, vote, close polls | OCS |
| [Announcements](#announcements) | list, create, delete announcements | OCS |
| [Calendar](#calendar) | list calendars, CRUD events | CalDAV |
| [Contacts](#contacts) | list address books, CRUD contacts | CardDAV |
| [Tasks](#tasks) | list lists, CRUD tasks, complete | CalDAV |
| [Mail](#mail) | accounts, mailboxes, messages, send | REST |
| [Collectives](#collectives) | list, pages, create, trash, restore | REST |
| [Unified Search](#unified-search) | list providers, search across apps | OCS |
| [App Management](#app-management) | list, info, enable, disable apps | OCS |

## Security: Permission Model

Every tool has a required permission level. You control what the AI is allowed to do:

| Level | What it can do | Environment variable |
|-------|---------------|---------------------|
| `read` (default) | List files, read files, get users, view notifications | `NEXTCLOUD_MCP_PERMISSIONS=read` |
| `write` | Everything in `read` + upload files, send messages, create events | `NEXTCLOUD_MCP_PERMISSIONS=write` |
| `destructive` | Everything in `write` + delete files, remove shares, empty trash | `NEXTCLOUD_MCP_PERMISSIONS=destructive` |

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
git clone https://github.com/cloud-py-api/nc_mcp_server.git
cd nc_mcp_server
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
export NEXTCLOUD_MCP_UPLOAD_ROOT=      # unset (default). If set to an absolute directory,
                                       # enables upload_file_from_path, restricted to files
                                       # inside that directory (symlinks resolved).
```

### Getting an App Password

1. Log into your Nextcloud instance
2. Go to **Settings** > **Security**
3. Under "Devices & sessions", create a new app password
4. Use this password for `NEXTCLOUD_PASSWORD`

## Usage

### With Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nextcloud": {
      "command": "nc-mcp-server",
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
  -- nc-mcp-server
```

### As HTTP Server (for containers/remote)

```bash
nc-mcp-server --transport http
# Listens on http://0.0.0.0:8100 by default
```

### Stdio Mode (default)

```bash
nc-mcp-server
# Communicates via stdin/stdout — used by MCP clients like Claude Desktop
```

## Available Tools

### Files

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_directory` | read | List files and folders in a directory |
| `get_file` | read | Read a file's content (returns images as MCP ImageContent) |
| `search_files` | read | Search files by name, MIME type, or path pattern |
| `upload_file` | write | Upload or overwrite a text file |
| `upload_file_binary` | write | Upload or overwrite a binary file (images, PDFs, archives) from base64-encoded content |
| `upload_file_from_path` | write | Stream a local file from the server's filesystem — only registered when `NEXTCLOUD_MCP_UPLOAD_ROOT` is set |
| `create_directory` | write | Create a new directory |
| `copy_file` | write | Copy a file or directory |
| `move_file` | destructive | Move or rename a file |
| `delete_file` | destructive | Delete a file or directory (moves to trash) |

`upload_file_from_path` is off by default because it gives the AI read access
to the local filesystem. To enable it, set `NEXTCLOUD_MCP_UPLOAD_ROOT` to an
absolute directory — only files resolving inside that directory (after symlink
resolution) can be uploaded. This is the right choice when you need to upload
multi-GB files that would blow past the size limit of an inline `base64` tool
call; the body is streamed in chunks rather than loaded into memory.

### File Sharing

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_shares` | read | List shares for a file/folder or all shares |
| `get_share` | read | Get details of a specific share |
| `create_share` | write | Share a file/folder (user, group, public link, email) |
| `update_share` | write | Update share permissions, expiration, password, etc. |
| `delete_share` | destructive | Remove a share |

### Trashbin

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_trash` | read | List deleted files in the trash bin |
| `restore_trash_item` | write | Restore a file from trash to its original location |
| `delete_trash_item` | destructive | Permanently delete a single item from trash |
| `empty_trash` | destructive | Permanently delete all items in trash |

### File Versions

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_versions` | read | List version history of a file |
| `restore_version` | write | Restore a previous version of a file |

### File Comments

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_comments` | read | List comments on a file |
| `add_comment` | write | Add a comment to a file |
| `edit_comment` | write | Edit an existing comment |
| `delete_comment` | destructive | Delete a comment |

### File Reminders

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_file_reminder` | read | Get the reminder set on a file (null if none) |
| `set_file_reminder` | write | Set or replace a reminder due date (ISO 8601, must be in the future) |
| `remove_file_reminder` | destructive | Remove the reminder from a file |

### System Tags

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_tags` | read | List all available tags |
| `get_file_tags` | read | Get tags assigned to a file |
| `create_tag` | write | Create a new tag |
| `assign_tag` | write | Assign a tag to a file |
| `unassign_tag` | destructive | Remove a tag from a file |
| `delete_tag` | destructive | Delete a tag |

### Users

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_current_user` | read | Get current authenticated user info |
| `list_users` | read | List or search users |
| `get_user` | read | Get specific user details |
| `create_user` | write | Create a new user (admin only) |
| `delete_user` | destructive | Delete a user (admin only) |

### User Status

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_user_status` | read | Get a user's status (online, away, dnd, etc.) |
| `set_user_status` | write | Set your status and custom message |
| `clear_user_status` | destructive | Clear your status |

### Notifications

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_notifications` | read | List all notifications |
| `dismiss_notification` | write | Dismiss a single notification |
| `dismiss_all_notifications` | write | Dismiss all notifications |

### Activity

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_activity` | read | View recent activity with filtering, sorting, and pagination |

### Talk

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_conversations` | read | List all Talk conversations |
| `get_conversation` | read | Get conversation details |
| `get_messages` | read | Get messages from a conversation |
| `get_participants` | read | List participants in a conversation |
| `send_message` | write | Send a message to a conversation |
| `create_conversation` | write | Create a new conversation |
| `delete_message` | destructive | Delete a message |
| `leave_conversation` | destructive | Leave a conversation |

### Talk Polls

| Tool | Permission | Description |
|------|-----------|-------------|
| `get_poll` | read | Get poll details and results |
| `create_poll` | write | Create a poll in a conversation |
| `vote_poll` | write | Vote on a poll |
| `close_poll` | write | Close a poll |

### Announcements

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_announcements` | read | List announcements |
| `create_announcement` | write | Create an announcement |
| `delete_announcement` | destructive | Delete an announcement |

### Calendar

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_calendars` | read | List user's calendars |
| `get_events` | read | Get events from a calendar (with date filtering) |
| `get_event` | read | Get a single event by UID |
| `create_event` | write | Create a calendar event |
| `update_event` | write | Update an event (partial updates supported) |
| `delete_event` | destructive | Delete a calendar event |

### Contacts

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_addressbooks` | read | List user's address books |
| `get_contacts` | read | Get contacts with pagination |
| `get_contact` | read | Get a single contact by UID |
| `create_contact` | write | Create a contact (multi-value email/phone supported) |
| `update_contact` | write | Update a contact (ETag concurrency control) |
| `delete_contact` | destructive | Delete a contact |

### Tasks

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_task_lists` | read | List task lists (CalDAV VTODO collections) |
| `get_tasks` | read | List tasks in a list (with status/completed filters) |
| `get_task` | read | Get a single task by UID |
| `create_task` | write | Create a task (due date, priority, categories, etc.) |
| `update_task` | write | Update a task (partial updates supported) |
| `complete_task` | write | Mark a task as completed |
| `delete_task` | destructive | Delete a task |

### Mail

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_mail_accounts` | read | List mail accounts |
| `list_mailboxes` | read | List mailboxes (folders) for an account |
| `list_mail_messages` | read | List messages in a mailbox |
| `get_mail_message` | read | Get full message content |
| `send_mail` | write | Send an email |

### Collectives

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_collectives` | read | List all collectives |
| `get_collective_pages` | read | List pages in a collective |
| `get_collective_page` | read | Get a page's content |
| `create_collective` | write | Create a new collective |
| `create_collective_page` | write | Create a page in a collective |
| `trash_collective` | destructive | Move a collective to trash |
| `delete_collective` | destructive | Permanently delete a trashed collective |
| `trash_collective_page` | destructive | Move a page to trash |
| `delete_collective_page` | destructive | Permanently delete a trashed page |
| `restore_collective` | write | Restore a collective from trash |
| `restore_collective_page` | write | Restore a page from trash |

### Unified Search

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_search_providers` | read | List available search providers (files, mail, talk, etc.) |
| `unified_search` | read | Search across one or more providers with pagination |

### App Management

| Tool | Permission | Description |
|------|-----------|-------------|
| `list_apps` | read | List installed apps |
| `get_app_info` | read | Get detailed app information |
| `enable_app` | write | Enable an app (admin only) |
| `disable_app` | destructive | Disable an app (admin only) |

## Development

```bash
# Clone and install
git clone https://github.com/cloud-py-api/nc_mcp_server.git
cd nc_mcp_server
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
pytest tests/integration/ -v
```

CI automatically runs integration tests against Nextcloud 32 and 33 Docker containers.

## About This Project

This project is an experiment in AI-autonomous open-source development. The entire codebase — including this README — is written and maintained by Claude (Anthropic's AI assistant). Human oversight is limited to:

- High-level design decisions
- Code review of pull requests
- Resolving architectural questions

The goal is to explore how far autonomous AI development can go in building production-quality, well-tested software.
