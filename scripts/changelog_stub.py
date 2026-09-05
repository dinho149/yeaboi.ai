#!/usr/bin/env python3
"""The changelog entry auto-version writes when Claude is not there to write one.

``auto-version.yml`` asks Claude to choose the semver level and write the
release notes. When that call fails (a dead credential, an outage) the workflow
still has to land a bump, or every release-worthy PR reds until someone fixes the
secret by hand. This writes a placeholder entry the copy contract accepts, and
``is-stub`` lets the workflow recognise it later so the next run with a working
Claude rewrites it in place (its changelog-only mode).

    python scripts/changelog_stub.py write 3.42.0 --pr 362
    python scripts/changelog_stub.py is-stub 3.42.0      # exit 0 when the entry is the stub
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

STUB_HEADLINE = "Release notes to follow"
STUB_SUMMARY = (
    "This release went out while the release-notes writer was away. The pull request that made it says what changed."
)
STUB_HIGHLIGHT = "Read the pull request for this release to see what changed"

_DEFAULT_DATA = Path(__file__).resolve().parent.parent / "src" / "yeaboi" / "changelog_data.json"


def stub_entry(version: str, date: str) -> dict:
    return {
        "version": version,
        "date": date,
        "headline": STUB_HEADLINE,
        "summary": STUB_SUMMARY,
        "highlights": [{"text": STUB_HIGHLIGHT, "areas": ["general"]}],
    }


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# The bundled file is hand- and bot-written over many releases and no one
# serializer reproduces it, so a stub is spliced into the text at the top of the
# array and every other byte stays put. Short string lists fold onto one line,
# the way the recent entries have them.
_INLINE_LIST = re.compile(r'\[\s+("[^"]*"(?:,\s+"[^"]*")*)\s+\]')
_ENTRIES = '"entries": ['


def render_entry(entry: dict, *, indent: int = 4) -> str:
    text = json.dumps(entry, indent=2, ensure_ascii=False)
    text = _INLINE_LIST.sub(lambda m: "[" + ", ".join(part.strip() for part in m.group(1).split(",")) + "]", text)
    pad = " " * indent
    return "\n".join(pad + line for line in text.splitlines())


def _entry(data: dict, version: str) -> dict | None:
    return next((entry for entry in data.get("entries", []) if entry.get("version") == version), None)


def write(version: str, *, date: str | None = None, path: Path | None = None) -> bool:
    """Prepend the stub for ``version`` unless the version already has an entry. True when written."""
    path = path or _DEFAULT_DATA
    text = path.read_text(encoding="utf-8")
    if _entry(json.loads(text), version) is not None:
        return False
    today = date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    at = text.index(_ENTRIES) + len(_ENTRIES)
    out = text[:at] + "\n" + render_entry(stub_entry(version, today)) + "," + text[at:]
    json.loads(out)  # a splice that does not parse must never reach the disk
    path.write_text(out, encoding="utf-8")
    return True


def is_stub(version: str, *, path: Path | None = None) -> bool:
    entry = _entry(_load(path or _DEFAULT_DATA), version)
    return entry is not None and entry.get("headline") == STUB_HEADLINE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=None, help="the changelog file; the bundled one when omitted")
    sub = parser.add_subparsers(dest="command", required=True)
    writer = sub.add_parser("write", help="prepend the stub entry for a version")
    writer.add_argument("version")
    writer.add_argument("--pr", type=int, default=0, help="the pull request, for the log line")
    writer.add_argument("--date", default=None, help="YYYY-MM-DD; today (UTC) when omitted")
    checker = sub.add_parser("is-stub", help="exit 0 when the version's entry is the stub")
    checker.add_argument("version")
    args = parser.parse_args(argv)
    if args.command == "write":
        written = write(args.version, date=args.date, path=args.data)
        print(f"{'wrote' if written else 'kept'} the changelog entry for {args.version} (PR #{args.pr})")
        return 0
    return 0 if is_stub(args.version, path=args.data) else 1


if __name__ == "__main__":
    sys.exit(main())
