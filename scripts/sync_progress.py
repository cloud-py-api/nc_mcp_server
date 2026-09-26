"""Recompute the PROGRESS.md test counts from what pytest collects, one row per module.

Run from the repository root with the project venv: python scripts/sync_progress.py [--write]
"""

import re
import subprocess
import sys
from pathlib import Path

ROWS: dict[str, list[str]] = {
    "Files": ["integration/test_files.py", "integration/test_file_names.py", "test_client_dav_paths.py"],
    "Users": ["integration/test_users.py", "test_user_admin.py"],
    "Groups": ["integration/test_groups.py"],
    "Notifications": ["integration/test_notifications.py"],
    "Talk": ["integration/test_talk.py", "test_talk_conversations.py"],
    "Talk Polls": ["integration/test_talk_polls.py"],
    "Talk Threads": ["integration/test_talk_threads.py", "test_talk_threads.py"],
    "Talk Chat": ["integration/test_talk_chat.py", "test_talk_chat.py"],
    "Talk Settings": ["integration/test_talk_settings.py", "test_talk_settings.py"],
    "Talk Admin": ["integration/test_talk_admin.py", "test_talk_admin.py"],
    "Talk Tags": ["integration/test_talk_tags.py", "test_talk_tags.py"],
    "Activity": ["integration/test_activity.py", "test_activity.py"],
    "Comments": ["integration/test_comments.py"],
    "User Status": ["integration/test_user_status.py"],
    "Announcements": ["integration/test_announcements.py"],
    "Trashbin": ["integration/test_trashbin.py"],
    "Versions": ["integration/test_versions.py"],
    "Shares": ["integration/test_shares.py", "integration/test_shares_received.py", "test_shares.py"],
    "System Tags": ["integration/test_system_tags.py"],
    "Mail": ["integration/test_mail.py", "test_mail_tools.py"],
    "Collectives": ["integration/test_collectives.py", "test_collectives_pages.py", "test_collectives_sharing.py"],
    "App Management": ["integration/test_app_management.py"],
    "Calendar": ["integration/test_calendar.py"],
    "Contacts": ["integration/test_contacts.py"],
    "Tasks": ["integration/test_tasks.py"],
    "Search": ["integration/test_search.py"],
    "User Permissions": ["integration/test_user_permissions.py"],
    "Server": ["integration/test_server.py"],
    "Permissions": ["integration/test_permissions.py", "test_permissions.py"],
    "Errors": ["integration/test_errors.py", "test_client_errors.py"],
    "Client": [
        "test_client_app_request.py",
        "test_client_ocs.py",
        "test_client_password_confirmation.py",
        "test_client_retry.py",
        "test_client_stream.py",
        "integration/test_session_cache.py",
    ],
    "Pagination": ["integration/test_pagination.py"],
    "Config": ["test_config.py"],
    "State": ["test_state.py"],
    "File Helpers": ["test_files_helpers.py"],
    "File Reminders": ["integration/test_reminders.py", "test_reminders.py"],
    "Forms": ["integration/test_forms.py"],
    "Circles": ["integration/test_circles.py"],
    "Cospend": ["integration/test_cospend.py"],
    "Flow": ["integration/test_flows.py", "test_flows.py"],
}


def collected(path: str) -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", f"tests/{path}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sum(1 for line in out.splitlines() if "::" in line)


def main() -> None:
    files = {str(p.relative_to("tests")) for p in Path("tests").rglob("test_*.py")}
    mapped = {f for paths in ROWS.values() for f in paths}
    if files - mapped or mapped - files:
        sys.exit(f"unmapped: {sorted(files - mapped)}; missing: {sorted(mapped - files)}")
    counts = {row: sum(collected(p) for p in paths) for row, paths in ROWS.items()}
    text = Path("PROGRESS.md").read_text()
    for row, count in counts.items():
        pattern = re.compile(rf"^\| {re.escape(row)} \| ([^|]+) \| \d+ \|$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda m, r=row, c=count: f"| {r} | {m.group(1).strip()} | {c} |", text)
        else:
            total_line = re.search(r"^\| \*\*Total\*\*", text, re.MULTILINE)
            if total_line is None:
                sys.exit("PROGRESS.md has no Total row")
            text = text[: total_line.start()] + f"| {row} | — | {count} |\n" + text[total_line.start() :]
    tools = sum(int(m) for m in re.findall(r"^\| [^*|][^|]* \| (\d+) \| \d+ \|$", text, re.MULTILINE))
    text = re.sub(
        r"^\| \*\*Total\*\* \| \*\*\d+\*\* \| \*\*\d+\*\* \|$",
        f"| **Total** | **{tools}** | **{sum(counts.values())}** |",
        text,
        flags=re.MULTILINE,
    )
    for row, count in counts.items():
        print(f"{row:18} {count}")
    print("total", tools, sum(counts.values()))
    if "--write" in sys.argv:
        Path("PROGRESS.md").write_text(text)


main()
