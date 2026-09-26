"""Print the errors from a Nextcloud log (JSON lines on stdin), one compact entry each.

Used by CI when integration tests fail: the full log is too long to read and its tail is usually
unrelated noise from the last tests, while the error behind a failure is often much earlier.

    docker exec <container> cat /var/www/html/data/nextcloud.log | python scripts/nextcloud_errors.py
"""

import json
import sys
from collections import Counter

# Errors every run logs that say nothing about a failure
NOISE = ("Could not find important tag for",)
MIN_LEVEL = 3


def _origin(exception: dict) -> str:
    """Where the exception was thrown, or the first frame that has a file."""
    if exception.get("File"):
        return f"{exception['File']}:{exception.get('Line', '?')}"
    for frame in exception.get("Trace") or []:
        if frame.get("file"):
            return f"{frame['file']}:{frame.get('line', '?')}"
    return ""


def _level(entry: dict) -> int:
    try:
        return int(entry.get("level", 0))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    noise: Counter[str] = Counter()
    shown = 0
    entries = 0
    for line in sys.stdin:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        entries += 1
        if _level(entry) < MIN_LEVEL:
            continue
        message = str(entry.get("message", ""))
        known = next((n for n in NOISE if message.startswith(n)), None)
        if known:
            noise[known] += 1
            continue
        shown += 1
        request = f"{entry.get('method', '')} {entry.get('url', '')}".strip()
        print(f"{entry.get('time', '')} [{entry.get('app', '')}] {request} (user {entry.get('user', '')})")
        print(f"  {message[:500]}")
        exception = entry.get("exception")
        while isinstance(exception, dict):
            text = str(exception.get("Message", ""))[:500]
            print(f"  {exception.get('Exception', '')}: {text} @ {_origin(exception)}")
            exception = exception.get("Previous")
    for text, count in noise.items():
        print(f"(skipped {count} x '{text} ...')")
    if not entries:
        print("The Nextcloud log is empty or could not be read.")
        return 1
    if not shown:
        print("No errors in the Nextcloud log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
