"""Python-side dumper for the W8 changelog gate.

``yeaboi.changelog`` is env-independent (it reads one bundled JSON file),
so it does not ride the per-fixture foundations dump. It gets two arms of
its own instead:

- **Corpus** — ``goldens/changelog/corpus.json`` holds a hand-written
  entries file full of malformed shapes; ``build_corpus_dump`` parses it
  through the real ``_parse_entry``/``build_changelog_text`` and the result
  freezes into ``goldens/changelog/parsed.json``. ``go/internal/changelog``
  replays both files, so the coercion rules are pinned without a binary.
- **Live** — ``build_live_dump`` renders the real bundled data; the
  needs-binary parity test diffs it against ``yeaboi __dump-changelog``,
  which serves the go:embed copy (its byte-sync with the Python file is a
  separate lockstep guard).

One shape the corpus deliberately omits: a ``highlights`` value that is not
iterable (an int, say) crashes ``load_changelog`` — the comprehension sits
outside its try block. That is reference behaviour, but freezing a crash
into a golden pins nothing useful, so the corpus sticks to the iterable
malformed shapes (string, dict) that legally yield zero highlights.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
GOLDENS_DIR = HERE.parent / "goldens" / "changelog"
CORPUS_PATH = GOLDENS_DIR / "corpus.json"
PARSED_GOLDEN = GOLDENS_DIR / "parsed.json"


def entries_payload(entries) -> list[dict]:
    """The __dump-changelog JSON shape (go/internal/changelog.DumpPayload
    is the twin)."""
    return [
        {
            "version": e.version,
            "date": e.date,
            "summary": e.summary,
            "highlights": [{"text": h.text, "areas": list(h.areas)} for h in e.highlights],
        }
        for e in entries
    ]


def parse_raw(data) -> list:
    """Mirror load_changelog's post-read body over already-decoded JSON."""
    from yeaboi.changelog import _parse_entry

    raw_entries = data.get("entries", []) if isinstance(data, dict) else []
    return [entry for entry in (_parse_entry(raw) for raw in raw_entries) if entry is not None]


def build_corpus_dump() -> dict:
    from yeaboi.changelog import build_changelog_text

    entries = parse_raw(json.loads(CORPUS_PATH.read_text(encoding="utf-8")))
    return {"entries": entries_payload(entries), "text": build_changelog_text(entries)}


def build_live_dump() -> dict:
    from yeaboi.changelog import build_changelog_text, load_changelog

    entries = load_changelog()
    return {"entries": entries_payload(entries), "text": build_changelog_text(entries)}


def render_golden() -> str:
    return json.dumps(build_corpus_dump(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
