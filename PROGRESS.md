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
- [x] User Status tools: get_user_status, set_user_status, clear_user_status (2026-03-25)
- [x] Users tools: create_user, delete_user (2026-03-25)
- [x] copy_file tool via WebDAV COPY (2026-03-25)
- [x] get_file returns MCP ImageContent for images (PNG, JPEG, GIF, WebP, BMP, SVG) (2026-03-25)
- [x] System Tags tools: list_tags, get_file_tags, create_tag, assign_tag, unassign_tag, delete_tag (2026-03-25)
- [x] Files Sharing tools: list_shares, get_share, create_share, update_share, delete_share (2026-03-26)
- [x] OCS error message extraction: surface Nextcloud error messages instead of generic HTTP codes (2026-03-27)
- [x] Announcement Center tools: list_announcements, create_announcement, delete_announcement (2026-03-27)
- [x] Files Trashbin tools: list_trash, restore_trash_item, empty_trash (2026-03-27)

### In Progress

### Blocked
(none)

### Next Up
- Files Versions

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
| Files | 8 | 47 |
| Users | 5 | 20 |
| Notifications | 3 | 11 |
| Talk | 8 | 48 |
| Talk Polls | 4 | 32 |
| Activity | 1 | 20 |
| Comments | 4 | 29 |
| User Status | 3 | 19 |
| Announcements | 3 | 29 |
| Trashbin | 3 | 22 |
| Shares | 5 | 40 |
| System Tags | 6 | 22 |
| Server | — | 7 |
| Permissions | — | 34 |
| Errors | — | 16 |
| Client | — | 29 |
| Config | — | 17 |
| State | — | 2 |
| **Total** | **53** | **444** |
