# Nextcloud MCP Server — Development Progress

## Current Phase: 3 — Groupware

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
- [x] Files Versions tools: list_versions, restore_version (2026-03-27)
- [x] Mail tools: list_mail_accounts, list_mailboxes, list_mail_messages, get_mail_message, send_mail (2026-03-28)
- [x] Collectives tools: list_collectives, get_collective_pages, get_collective_page (2026-03-29)
- [x] App Management tools: list_apps, get_app_info, enable_app, disable_app (2026-03-30)
- [x] User-permission integration tests: non-admin error handling validation (2026-03-30)
- [x] Calendar tools: list_calendars, get_events, get_event, create_event, update_event, delete_event (2026-03-30)
- [x] Contacts tools: list_addressbooks, get_contacts, get_contact, create_contact, update_contact, delete_contact (2026-03-31)
- [x] Tasks tools: list_task_lists, get_tasks, get_task, create_task, update_task, complete_task, delete_task (2026-04-08)
- [x] Unified Search tools: list_search_providers, unified_search (2026-04-12)
- [x] upload_file_binary tool: base64-encoded binary upload with MIME inference (2026-04-21)
- [x] upload_file_from_path tool: stream a local file to Nextcloud. Off by default; enabled via NEXTCLOUD_MCP_UPLOAD_ROOT, restricted to files inside that root (symlinks resolved) (2026-04-21)
- [x] File Reminders tools: get_file_reminder, set_file_reminder, remove_file_reminder (2026-04-22)

### In Progress

### Blocked
(none)

### Next Up
- Tables, Forms, Weather Status (all with full OCS coverage)

## Phases

| Phase | Focus | Status |
|-------|-------|--------|
| 1 | Core (Files, Users, Notifications, Activity) | Complete |
| 2 | Communication (Talk, Announcements, Mail) | Complete |
| 3 | Groupware (Calendar, Contacts, Tasks, Deck, Notes) | In Progress |
| 4 | Collaboration (Collectives, Forms, Polls, Tables) | Not Started |
| 5 | Storage & Search (Files Reminders, Unified Search done) | In Progress |
| 6 | Media & Data | Not Started |
| 7 | Advanced & Admin (App Management, etc.) | In Progress |

## Test Coverage

| Module | Tools | Tests |
|--------|-------|-------|
| Files | 10 | 77 |
| Users | 5 | 20 |
| Notifications | 3 | 11 |
| Talk | 8 | 48 |
| Talk Polls | 4 | 32 |
| Activity | 1 | 20 |
| Comments | 4 | 29 |
| User Status | 3 | 19 |
| Announcements | 3 | 29 |
| Trashbin | 3 | 22 |
| Versions | 2 | 18 |
| Shares | 5 | 40 |
| System Tags | 6 | 22 |
| Mail | 5 | 29 |
| Collectives | 3 | 22 |
| App Management | 4 | 14 |
| Calendar | 6 | 44 |
| Contacts | 6 | 36 |
| Tasks | 7 | 48 |
| Search | 2 | 17 |
| User Permissions | — | 15 |
| Server | — | 8 |
| Permissions | — | 34 |
| Errors | — | 16 |
| Client | — | 29 |
| Config | — | 24 |
| State | — | 2 |
| File Helpers | — | 26 |
| File Reminders | 3 | 20 |
| **Total** | **102** | **771** |

Files shows 10, but one (`upload_file_from_path`) is only registered when
`NEXTCLOUD_MCP_UPLOAD_ROOT` is configured. Default deployments expose 101 tools.
