"""What the user said about a prior-art suggestion, and why.

Planning shortlists the team's own repositories as prior art for a greenfield
project. It is right often and wrong sometimes, and a wrong suggestion is
expensive in a way a wrong standup nudge is not: the same house repos get
offered on *every* new project, so one bad guess is a papercut repeated
forever. "Not relevant — that's the legacy billing service we're retiring"
has to be worth typing once.

**Two effects, deliberately different in kind.**

- *Rejection* is deterministic and permanent: the repository is filtered out of
  every future shortlist by key, by ``Ledger.is_rejected``. No model is
  involved, so the correction can never be forgotten or re-litigated.
- *Acceptance* changes nothing about the shortlist that produced it — it was
  already right. It only feeds the pitch prompt, so the model learns what this
  team considers worth reusing.

**The suppress-only property survives both.** The pitch model answers with
per-candidate bullets and a ``drop`` flag over candidates it was *handed*, so
confirmations can make it drop less and rejections can make it drop more.
Neither direction gives the feedback loop a shape in which it could nominate a
repository. Deterministic ranking over the stored inventory remains the only
thing that can put a repo in front of the user. Same argument as
``standup/practice_feedback.py``, which this module is modelled on.

**Rejection is global, not per-project.** A repo the user calls irrelevant is
almost always irrelevant because of what it *is* (retired, a spike, a fork),
not because of what they happened to be planning that day. Scoping the memory
per project would make them re-reject the same estate on every new plan, which
is the papercut this exists to stop.

# See docs: "Project Intake Questionnaire" — prior art
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.paths import get_db_path

logger = logging.getLogger(__name__)

VERDICT_UP = "up"
VERDICT_DOWN = "down"
VERDICTS = (VERDICT_UP, VERDICT_DOWN)

# What reaches the pitch prompt. Rejections outnumber acceptances because they
# are the more informative half: an acceptance says "you were already right", a
# rejection says where the line actually is. Both capped so a heavy user cannot
# grow the prompt without bound.
_MAX_REJECTIONS = 12
_MAX_ACCEPTANCES = 6
_REASON_CLIP = 200
_NAME_CLIP = 120

# The table lives here rather than in sessions.py so the migration creates
# exactly what this module reads — the same split standup/store.py uses.
PRIOR_ART_FEEDBACK_SCHEMA = """\
CREATE TABLE IF NOT EXISTS planning_prior_art_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_key   TEXT NOT NULL,
    verdict    TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    repo_name  TEXT NOT NULL DEFAULT '',
    project    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(repo_key)
);"""


@dataclasses.dataclass(frozen=True)
class FeedbackExample:
    """One prior verdict, shaped for the pitch prompt.

    Carries the repository name and the reason but no project identity — the
    prompt needs to learn *what kind of repo* this team dismisses, not which
    plan they were writing when they dismissed it.
    """

    verdict: str
    repo_name: str
    reason: str


@dataclasses.dataclass(frozen=True)
class Ledger:
    """Every verdict the user has ever cast on a prior-art suggestion."""

    rejected: frozenset[str] = frozenset()
    accepted: frozenset[str] = frozenset()
    examples: tuple[FeedbackExample, ...] = ()

    def is_rejected(self, repo_key: str) -> bool:
        """Deterministic filter — a rejected repo is never offered again."""
        return (repo_key or "").strip().lower() in self.rejected

    def corrections(self) -> tuple[dict, ...]:
        """Capped, project-free few-shot examples for the pitch prompt."""
        out: list[dict] = []
        rejections = [e for e in self.examples if e.verdict == VERDICT_DOWN][:_MAX_REJECTIONS]
        acceptances = [e for e in self.examples if e.verdict == VERDICT_UP][:_MAX_ACCEPTANCES]
        for example in (*rejections, *acceptances):
            out.append(
                {
                    "verdict": example.verdict,
                    "repo": example.repo_name[:_NAME_CLIP],
                    "reason": example.reason[:_REASON_CLIP],
                }
            )
        return tuple(out)


def _connect(db_path=None, *, create: bool = False):
    """Open the shared sessions database. Returns None when there isn't one.

    Reads never create the file: an absent database means "no verdicts yet",
    and conjuring an empty one on a read would leave stray files behind every
    dry run. A write may create it, because the alternative is losing the
    verdict the user just typed.
    """
    path = db_path or get_db_path()
    try:
        if not create and str(path) != ":memory:" and not Path(path).exists():
            return None
        return sqlite3.connect(str(path))
    except Exception:
        logger.debug("prior_art_feedback: cannot open %s", path, exc_info=True)
        return None


def load(db_path=None) -> Ledger:
    """Read every recorded verdict. Never raises.

    A prior-art step that cannot read its own ledger must still run — the cost
    of failing open is re-offering a rejected repo once, and the cost of
    failing closed is losing the whole feature to a corrupt row.
    """
    conn = _connect(db_path)
    if conn is None:
        return Ledger()
    rejected: set[str] = set()
    accepted: set[str] = set()
    examples: list[FeedbackExample] = []
    try:
        with conn:
            rows = conn.execute(
                "SELECT repo_key, verdict, reason, repo_name FROM planning_prior_art_feedback ORDER BY created_at DESC"
            ).fetchall()
    except Exception:
        logger.debug("prior_art_feedback: table unreadable — treating as empty", exc_info=True)
        return Ledger()
    finally:
        conn.close()
    for repo_key, verdict, reason, repo_name in rows:
        key = str(repo_key or "").strip().lower()
        if not key or verdict not in VERDICTS:
            continue
        (rejected if verdict == VERDICT_DOWN else accepted).add(key)
        name = str(repo_name or "").strip()
        note = str(reason or "").strip()
        # An example with neither a name nor a reason teaches the model nothing.
        if name or note:
            examples.append(FeedbackExample(verdict=verdict, repo_name=name, reason=note))
    logger.info(
        "prior_art_feedback: %d rejected, %d accepted, %d example(s)", len(rejected), len(accepted), len(examples)
    )
    return Ledger(rejected=frozenset(rejected), accepted=frozenset(accepted), examples=tuple(examples))


def apply_verdict(
    *,
    repo_key: str,
    verdict: str,
    reason: str = "",
    repo_name: str = "",
    project: str = "",
    db_path=None,
) -> bool:
    """Record one verdict. Returns True when a row was written.

    Upserts on ``repo_key`` so a re-vote flips the verdict instead of stacking
    a second row — the user changing their mind must not leave the old opinion
    half-live.
    """
    key = (repo_key or "").strip().lower()
    if not key or verdict not in VERDICTS:
        logger.warning("prior_art_feedback: refusing verdict %r for key %r", verdict, repo_key)
        return False
    conn = _connect(db_path, create=True)
    if conn is None:
        logger.warning("prior_art_feedback: no database — verdict for %s not recorded", key)
        return False
    try:
        with conn:
            conn.execute(PRIOR_ART_FEEDBACK_SCHEMA)
            conn.execute(
                "INSERT INTO planning_prior_art_feedback "
                "(repo_key, verdict, reason, repo_name, project, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(repo_key) DO UPDATE SET "
                "verdict=excluded.verdict, reason=excluded.reason, repo_name=excluded.repo_name, "
                "project=excluded.project, created_at=excluded.created_at",
                (
                    key,
                    verdict,
                    (reason or "").strip()[:_REASON_CLIP],
                    (repo_name or "").strip()[:_NAME_CLIP],
                    (project or "").strip(),
                    datetime.now(UTC).isoformat(),
                ),
            )
    except Exception:
        logger.warning("prior_art_feedback: verdict for %s not recorded", key, exc_info=True)
        return False
    finally:
        conn.close()
    logger.info("prior_art_feedback: recorded %s for %s", verdict, key)
    return True
