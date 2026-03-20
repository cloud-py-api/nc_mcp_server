# Nextcloud MCP Server — Development Progress

## Current Phase: 1 — Core

### Completed
- [x] Project scaffold: pyproject.toml, src layout, config, permissions, client (2026-03-20)
- [x] Permission model: read/write/destructive levels with decorator (2026-03-20)
- [x] HTTP client: WebDAV + OCS support with niquests (2026-03-20)
- [x] Files tools: list_directory, get_file, upload_file, create_directory, delete_file, move_file (2026-03-20)
- [x] Users tools: get_current_user, list_users, get_user (2026-03-20)
- [x] Unit tests: permissions, config (2026-03-20)
- [x] Integration tests: files lifecycle, users (2026-03-20)
- [x] CI pipeline: lint + unit tests + integration tests with real Nextcloud (2026-03-20)

### In Progress
- [ ] Notifications tools: list_notifications, dismiss_notification
- [ ] Activity tools: get_activity
- [ ] search_files tool (WebDAV SEARCH/REPORT)

### Blocked
(none)

### Next Up
- Phase 2: Talk tools (list_conversations, get_messages, send_message)
- Improve error handling and error messages
- Add more integration test coverage

## Phases

| Phase | Focus | Status |
|-------|-------|--------|
| 1 | Core (Files, Users, Notifications, Activity) | In Progress |
| 2 | Communication (Talk) | Not Started |
| 3 | Collaboration (Shares, Calendar, Contacts, Deck) | Not Started |
| 4 | Advanced (Search, Status, Apps) | Not Started |
