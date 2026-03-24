# Nextcloud MCP Server — Development Progress

## Current Phase: 2 — Communication

### Completed
- [x] Project scaffold: pyproject.toml, src layout, config, permissions, client (2026-03-20)
- [x] Permission model: read/write/destructive levels with decorator (2026-03-20)
- [x] HTTP client: WebDAV + OCS support with niquests (2026-03-20)
- [x] Files tools: list_directory, get_file, upload_file, create_directory, delete_file, move_file (2026-03-20)
- [x] Users tools: get_current_user, list_users, get_user (2026-03-20)
- [x] Unit tests: permissions, config (2026-03-20)
- [x] Integration tests: files lifecycle, users (2026-03-20)
- [x] CI pipeline: lint + unit tests + integration tests with real Nextcloud (2026-03-20)
- [x] Notifications tools: list_notifications, dismiss_notification, dismiss_all_notifications (2026-03-21)
- [x] Test suite overhaul: MCP tool-level integration tests (2026-03-21)
- [x] Talk tools: list_conversations, get_conversation, get_messages, get_participants, send_message, create_conversation, delete_message, leave_conversation (2026-03-22)
- [x] Talk polls: get_poll, create_poll, vote_poll, close_poll (2026-03-24)
- [x] Activity tools: get_activity (2026-03-24)
- [x] Comments tools: list_comments, add_comment, edit_comment, delete_comment (2026-03-24)

- [x] search_files tool via WebDAV SEARCH (2026-03-24)

### In Progress

### Blocked
(none)

### Next Up
- Announcement Center
- Files Sharing, Trashbin, Versions
- Improve error handling and error messages

## Phases

| Phase | Focus | Status |
|-------|-------|--------|
| 1 | Core (Files, Users, Notifications, Activity) | In Progress |
| 2 | Communication (Talk) | In Progress |
| 3 | Collaboration (Shares, Calendar, Contacts, Deck) | Not Started |
| 4 | Advanced (Search, Status, Apps) | Not Started |

## Test Coverage

| Module | Tools | Tests |
|--------|-------|-------|
| Files | 7 | 35 |
| Users | 3 | 13 |
| Notifications | 3 | 11 |
| Talk | 8 | 48 |
| Talk Polls | 4 | 32 |
| Activity | 1 | 18 |
| Comments | 4 | 29 |
| Server | — | 6 |
| Permissions | — | 34 |
| Errors | — | 10 |
| Config | — | 12 |
| State | — | 2 |
| **Total** | **30** | **250** |
