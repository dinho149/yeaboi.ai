"""Security findings a person has looked at and set aside, with the reason why.

A dismissal is the one piece of security state that is *opinion* rather than
scan output, so it lives in its own file under the data dir rather than in the
scan tables: ``security_allow.json``. Every entry carries a reason, and an
empty one is refused — a suppression nobody can explain is the thing an audit
trail exists to catch. The posture line counts dismissals so they are never
invisible.

Keys are the finding's aggregation key (``category:pattern:location``), which
is what the report shows beside each grouped finding.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "security_allow.json"
_VERSION = 1


@dataclass(frozen=True)
class Dismissal:
    key: str = ""
    reason: str = ""
    by: str = ""
    at: str = ""
    expires: str = ""  # YYYY-MM-DD, "" = never


def default_path() -> Path:
    from yeaboi.paths import get_agentwatch_data_dir

    return get_agentwatch_data_dir() / FILE_NAME


def load(path: Path | None = None) -> list[Dismissal]:
    """Every dismissal on file, expired ones included. Never raises."""
    path = path or default_path()
    try:
        if not path.exists():
            return []
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("agentwatch dismissals: cannot read %s: %s", path, exc)
        return []
    rows = parsed.get("dismissed") if isinstance(parsed, dict) else None
    out: list[Dismissal] = []
    for row in rows or []:
        if not isinstance(row, dict) or not str(row.get("key", "")).strip():
            continue
        out.append(
            Dismissal(
                key=str(row.get("key", "")).strip(),
                reason=str(row.get("reason", "")).strip(),
                by=str(row.get("by", "")).strip(),
                at=str(row.get("at", "")).strip(),
                expires=str(row.get("expires", "")).strip(),
            )
        )
    return out


def _save(rows: list[Dismissal], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": _VERSION, "dismissed": [asdict(r) for r in rows]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def active(today: str = "", path: Path | None = None) -> dict[str, Dismissal]:
    """Dismissals still in force on ``today`` (YYYY-MM-DD; default: now), by key."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    return {d.key: d for d in load(path) if not d.expires or d.expires >= today}


def dismiss(key: str, *, reason: str, by: str = "", expires: str = "", path: Path | None = None) -> Dismissal:
    """Record one dismissal. Raises ValueError on an empty key or reason."""
    key = key.strip()
    reason = reason.strip()
    if not key:
        raise ValueError("a dismissal needs the finding's key")
    if not reason:
        raise ValueError("a dismissal needs a reason — say why this finding is expected")
    if expires:
        datetime.strptime(expires, "%Y-%m-%d")
    path = path or default_path()
    rows = [d for d in load(path) if d.key != key]
    entry = Dismissal(key=key, reason=reason, by=by.strip(), at=datetime.now(timezone.utc).isoformat(), expires=expires)
    rows.append(entry)
    _save(rows, path)
    logger.info("agentwatch dismissals: %s dismissed (%s)", key, reason)
    return entry


def undismiss(key: str, *, path: Path | None = None) -> bool:
    """Drop one dismissal; False when there was none."""
    path = path or default_path()
    rows = load(path)
    kept = [d for d in rows if d.key != key.strip()]
    if len(kept) == len(rows):
        return False
    _save(kept, path)
    logger.info("agentwatch dismissals: %s restored", key)
    return True
