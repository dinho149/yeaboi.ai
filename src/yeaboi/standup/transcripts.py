"""Find, read and parse standup meeting transcripts.

Everything here is deterministic and offline — no LLM, no network. The job is
to turn "a file somebody dropped in a folder" into ``(TranscriptSource, turns)``
that the review pipeline can reason over, and to be honest about what was
skipped rather than silently reviewing nothing.

Two sources, by design:

- the **managed** folder ``~/.yeaboi/transcripts`` (``paths.get_transcripts_dir``).
  It sits under ``ROOT_DIR``, so ``fs_policy`` already allows it and dropping a
  file there needs no consent prompt.
- an optional **external** folder from ``standup_config.transcript_dir`` (a
  Zoom/Teams/Granola recordings folder). That one is outside the sandbox, so it
  goes through ``fs_policy.resolve_and_check`` and can legitimately be denied —
  notably on the unattended scheduled run, where nobody is present to consent.
  A denial degrades to "managed folder only" plus a warning; it is never fatal.

Attribution is by DATE, not by directory: a transcript is matched to the standup
it discusses, so the managed folder stays flat and the user has nothing to get
right beyond dropping the file.

The "already reviewed" ledger is keyed by **content hash**, not path, so
renaming or re-dropping a file never re-spends an LLM call while genuinely
editing it does. Files are never moved or rewritten — the database is the
bookkeeping precisely so the user's folder stays theirs.

# See docs: "Guardrails" — the filesystem sandbox layer (fs_policy)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from yeaboi.agent.state import TranscriptNudge, TranscriptSource
from yeaboi.timeparse import parse_date

logger = logging.getLogger(__name__)

TRANSCRIPT_SUFFIXES: tuple[str, ...] = (".txt", ".md", ".vtt", ".srt", ".json")

# Read ceiling per file. Mirrors analysis/doc_quality._READ_CHARS: an explicit
# limit that is REPORTED (source.truncated) rather than a silent trim.
_MAX_CHARS = 100_000
# Refuse to even open anything larger — a 200 MB "transcript" is a mistake, and
# stat() is free next to reading it.
_MAX_BYTES = 5_000_000
_MAX_FILES_PER_SWEEP = 10
# How far back an automatic sweep will look. Long enough to cover a holiday
# weekend or a week off, short enough that dropping an old archive folder in
# doesn't trigger a month of reviews.
_LOOKBACK_DAYS = 14
# Bounds on walking an EXTERNAL folder. That folder is somebody's real
# recordings directory — legitimately ~/Downloads or a Zoom tree years deep —
# and this walk runs on the critical path of a standup. Hitting a bound degrades
# the sweep (and says so) rather than stalling the run that is about to happen.
_MAX_SCAN_ENTRIES = 2_000
_MAX_SCAN_DEPTH = 4

# Below this share of speaker-labelled lines we stop trusting attribution and
# mark the source "unlabelled" — the review then restricts what such a
# transcript is allowed to conclude (see transcript_review).
_LABELLED_LINE_RATIO = 0.6
_MIN_LABELLED_LINES = 2

# "Alice:", "Alice Smith (00:14):", "  Bob Jones  :" — a speaker label opening a
# turn. Bounded name length and a required colon keep it from eating prose lines
# that merely contain a colon ("Note: we shipped").
_SPEAKER_RE = re.compile(r"^\s*([^\s:][^:\n]{0,48}?)\s*(?:\(\d{1,2}:\d{2}(?::\d{2})?\))?\s*:\s+(.*)$")
# A speaker label must look like a name, not a sentence — reject anything with
# terminal punctuation or too many words.
_MAX_SPEAKER_WORDS = 5

# A WebVTT/SRT cue timing line: "00:00:01.000 --> 00:00:04.000". Anchored on the
# leading timestamp rather than matching a bare arrow anywhere in the line, for
# two reasons. A spoken line — "we went from manual --> automated" — is not a
# timing line, and dropping it loses somebody's actual standup update. And a bare
# `-->` reads to a static analyser as a half-written HTML-comment matcher (CodeQL
# py/bad-tag-filter: it parses `-->` but not `--!>`), which is a false positive
# here — nothing in this module filters HTML — but an alert nobody can act on is
# worth spending a more precise regex to retire.
_VTT_TIMING_RE = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?\s*-{2}>\s*\d{1,2}:\d{2}")
_VTT_VOICE_RE = re.compile(r"<v\s+([^>]+?)\s*>(.*?)(?:</v>)?\s*$", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SRT_INDEX_RE = re.compile(r"^\d+$")

_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_COMPACT_DATE_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
_TEXT_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTHS = {
    m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}
# Only the head of the file is scanned for a date — a date deep in the body is
# far more likely to be someone saying "let's ship by 2026-09-01".
_DATE_SCAN_CHARS = 2_000

_MD_PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s+|#{1,6}\s+|>\s?)")


@dataclass(frozen=True)
class TranscriptTurn:
    """One contiguous stretch of speech attributed to one raw speaker label."""

    speaker: str = ""  # the raw label from the file; "" when unattributed
    text: str = ""
    index: int = 0


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def content_hash(path: Path) -> str:
    """Return a stable content hash for the "already reviewed" ledger.

    Content-keyed rather than path-keyed on purpose: a rename must not re-spend
    an LLM call, and an edit genuinely is new material.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:32]


def _fmt_of(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


# ---------------------------------------------------------------------------
# Parsing — one function per format, all pure, none of them raise
# ---------------------------------------------------------------------------


def _looks_like_speaker(label: str) -> bool:
    """Reject sentence-shaped 'labels' so prose with a colon isn't split."""
    label = label.strip()
    if not label or len(label.split()) > _MAX_SPEAKER_WORDS:
        return False
    return not label.endswith((".", "!", "?", ",", ";"))


def _parse_labelled_lines(text: str) -> tuple[TranscriptTurn, ...]:
    """Parse ``Speaker: said something`` lines; unlabelled lines continue the turn.

    Shared by .txt/.md and used as the fallback inside the caption formats,
    which frequently carry the same convention inside their cue payloads.
    """
    turns: list[dict] = []
    for raw_line in text.splitlines():
        line = _MD_PREFIX_RE.sub("", raw_line).strip()
        if not line:
            continue
        match = _SPEAKER_RE.match(line)
        if match and _looks_like_speaker(match.group(1)):
            speaker, said = match.group(1).strip(), match.group(2).strip()
            # Consecutive lines from one speaker read as one turn.
            if turns and turns[-1]["speaker"] == speaker:
                turns[-1]["text"] = f"{turns[-1]['text']} {said}".strip()
            else:
                turns.append({"speaker": speaker, "text": said})
        elif turns:
            turns[-1]["text"] = f"{turns[-1]['text']} {line}".strip()
        else:
            # Leading prose before any speaker label — keep it, unattributed.
            turns.append({"speaker": "", "text": line})
    return tuple(
        TranscriptTurn(speaker=t["speaker"], text=t["text"], index=i) for i, t in enumerate(turns) if t["text"]
    )


def _parse_vtt(text: str) -> tuple[TranscriptTurn, ...]:
    """WebVTT: drop the header, NOTE blocks, cue ids and timing lines."""
    lines: list[str] = []
    skipping_note = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            skipping_note = False
            continue
        if line.upper().startswith("WEBVTT"):
            continue
        if line.startswith("NOTE"):
            skipping_note = True
            continue
        if skipping_note or _VTT_TIMING_RE.search(line):
            continue
        voice = _VTT_VOICE_RE.match(line)
        if voice:
            lines.append(f"{voice.group(1).strip()}: {_TAG_RE.sub('', voice.group(2)).strip()}")
            continue
        stripped = _TAG_RE.sub("", line).strip()
        # A bare cue identifier (no spoken content) carries nothing.
        if not stripped or _SRT_INDEX_RE.match(stripped):
            continue
        lines.append(stripped)
    return _parse_labelled_lines("\n".join(lines))


def _parse_srt(text: str) -> tuple[TranscriptTurn, ...]:
    """SubRip: same as VTT minus the header, with numeric index lines."""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not _SRT_INDEX_RE.match(line.strip()) and not _VTT_TIMING_RE.search(line)
    ]
    return _parse_labelled_lines("\n".join(_TAG_RE.sub("", line) for line in lines))


_JSON_SPEAKER_KEYS = ("speaker", "participant", "name", "from", "speaker_name", "user")
_JSON_TEXT_KEYS = ("text", "content", "words", "value", "transcript", "message")


def _json_entry_to_turn(entry: dict) -> tuple[str, str]:
    speaker = next((str(entry[k]).strip() for k in _JSON_SPEAKER_KEYS if entry.get(k)), "")
    text = ""
    for key in _JSON_TEXT_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            break
        # Deepgram/AssemblyAI style: a list of word objects.
        if isinstance(value, list):
            words = [str(w.get("text", w.get("word", ""))) for w in value if isinstance(w, dict)]
            if words:
                text = " ".join(w for w in words if w).strip()
                break
    return speaker, text


def _parse_json(text: str) -> tuple[TranscriptTurn, ...]:
    """Three concrete shapes plus a dump-to-text fallback.

    "JSON" is not a transcript format, so this handles the shapes vendors
    actually emit and degrades to unlabelled text rather than pretending to
    support arbitrary JSON.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("transcripts: JSON did not parse — falling back to raw text")
        return _parse_labelled_lines(text)

    entries: list[dict] = []
    # "Recognised" is tracked separately from "produced turns": an empty
    # segments list is a valid EMPTY transcript, and dumping "[]" back through
    # the text parser would invent a turn out of punctuation.
    recognised = False
    if isinstance(data, list):
        entries = [e for e in data if isinstance(e, dict)]
        recognised = True
    elif isinstance(data, dict):
        for key in ("segments", "transcript", "monologues", "results", "utterances", "entries"):
            value = data.get(key)
            if isinstance(value, list):
                entries = [e for e in value if isinstance(e, dict)]
                recognised = True
                break

    turns: list[TranscriptTurn] = []
    for entry in entries:
        speaker, said = _json_entry_to_turn(entry)
        # Rev.ai "monologues" nest their content under "elements".
        if not said and isinstance(entry.get("elements"), list):
            said = " ".join(str(el.get("value", "")) for el in entry["elements"] if isinstance(el, dict)).strip()
        if said:
            turns.append(TranscriptTurn(speaker=speaker, text=said, index=len(turns)))
    if turns or recognised:
        return tuple(turns)

    logger.warning("transcripts: JSON had no recognised transcript shape — falling back to raw text")
    return _parse_labelled_lines(text if not isinstance(data, str) else data)


def parse(text: str, fmt: str) -> tuple[TranscriptTurn, ...]:
    """Parse transcript text into turns. Never raises; unknown format → text rules."""
    try:
        if fmt == "vtt":
            return _parse_vtt(text)
        if fmt == "srt":
            return _parse_srt(text)
        if fmt == "json":
            return _parse_json(text)
        return _parse_labelled_lines(text)
    except Exception as exc:  # a malformed file must never break a standup
        logger.warning("transcripts: parse failed for fmt=%s: %s", fmt, exc)
        return ()


# ---------------------------------------------------------------------------
# Date attribution
# ---------------------------------------------------------------------------


def _valid_date(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _scan_date(text: str) -> str:
    match = _ISO_DATE_RE.search(text)
    if match:
        found = _valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if found:
            return found
    match = _TEXT_DATE_RE.search(text)
    if match:
        month = _MONTHS.get(match.group(1)[:3].lower(), 0)
        if month:
            found = _valid_date(int(match.group(3)), month, int(match.group(2)))
            if found:
                return found
    return ""


def _clamp_date(value: str, today: date) -> str:
    """Return ``value`` only if it is a real date no later than ``today``.

    A date in the future is not a standup that happened — it is somebody saying
    "let's ship by 2026-09-01" — so it is discarded rather than trusted.
    """
    if not value:
        return ""
    try:
        return value if parse_date(value) <= today else ""
    except ValueError:
        return ""


def infer_date_from_text(text: str, *, fmt: str = "txt", today: date | None = None) -> str:
    """Which standup does this transcript TEXT cover? "" when it doesn't say.

    Steps 2–3 of :func:`infer_date`, split out because pasted or piped text has
    no ``Path`` to take a filename or an mtime from. ``infer_date`` calls this,
    so the two can never drift.
    """
    today = today or date.today()

    # A structured date field, for the JSON shapes that carry one.
    if fmt == "json":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            for key in ("date", "start_time", "meeting_date", "created_at", "started_at"):
                value = data.get(key)
                if isinstance(value, str):
                    found = _clamp_date(_scan_date(value), today)
                    if found:
                        return found

    # A date in the head of the file (a VTT NOTE block, a markdown title).
    return _clamp_date(_scan_date(text[:_DATE_SCAN_CHARS]), today)


def infer_date(path: Path, text: str, *, today: date | None = None) -> str:
    """Work out which standup a transcript covers, most reliable signal first.

    Filename → structured JSON field → head of the content → file mtime.
    """
    today = today or date.today()

    # 1. The filename — what an export tool and a human both tend to get right.
    stem = path.stem
    match = _ISO_DATE_RE.search(stem) or _COMPACT_DATE_RE.search(stem)
    if match:
        found = _clamp_date(_valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3))), today)
        if found:
            return found

    # 2-3. Anything the content itself says.
    found = infer_date_from_text(text, fmt=_fmt_of(path), today=today)
    if found:
        return found

    # 4. mtime. Weakest signal, so it is logged — a wrong date reviews the wrong
    #    report, and that is worth being able to trace afterwards.
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError as exc:
        # Every other degrade path in this module says so; a transcript that
        # ends up with NO date at all is the one most worth being able to trace.
        logger.warning("transcripts: %s has no date and no usable mtime: %s", path.name, exc)
        return ""
    inferred = min(stamp, today).isoformat()
    logger.info("transcripts: %s has no date — using mtime %s", path.name, inferred)
    return inferred


# ---------------------------------------------------------------------------
# Reading one file
# ---------------------------------------------------------------------------


def read_transcript(
    path: Path, *, external: bool = False, today: date | None = None
) -> tuple[TranscriptSource, tuple[TranscriptTurn, ...]]:
    """Read and parse one transcript. Returns an empty turn tuple for empty text.

    **Raises** ``OSError`` if the file cannot be read and ``SandboxViolationError``
    if it resolves outside the allowed tree — deliberately, and unlike ``parse``,
    which swallows its own errors. The caller needs the reason: the sweep turns
    it into "Could not read X: …" in ``report.warnings``, and a transcript that
    vanished between the scan and the read (a real TOCTOU window) has to be
    reported rather than silently counted as reviewed-and-empty.

    The sandbox check runs even for the managed folder: a symlink inside
    ``~/.yeaboi/transcripts`` pointing at ``~/.ssh/id_rsa`` resolves outside the
    allowed tree, and ``resolve_and_check`` is what catches that.
    """
    from yeaboi.fs_policy import resolve_and_check

    resolved = resolve_and_check(path, mode="read", context="Standup — transcript review")
    raw = resolved.read_text(encoding="utf-8", errors="replace")
    truncated = len(raw) > _MAX_CHARS
    if truncated:
        logger.warning("transcripts: %s exceeded %d chars — reviewing the head only", path.name, _MAX_CHARS)
        raw = raw[:_MAX_CHARS]

    fmt = _fmt_of(resolved)
    turns = parse(raw, fmt)
    labelled = sum(1 for t in turns if t.speaker)
    attribution = (
        "labelled"
        if turns and labelled >= _MIN_LABELLED_LINES and labelled / len(turns) >= _LABELLED_LINE_RATIO
        else "unlabelled"
    )
    speakers = tuple(dict.fromkeys(t.speaker for t in turns if t.speaker))

    source = TranscriptSource(
        path=str(resolved),
        filename=resolved.name,
        fmt=fmt,
        covered_date=infer_date(resolved, raw, today=today),
        char_count=len(raw),
        truncated=truncated,
        speakers=speakers,
        attribution=attribution,
        external=external,
    )
    logger.info(
        "transcripts: read %s (fmt=%s date=%s turns=%d speakers=%d attribution=%s)",
        source.filename,
        fmt,
        source.covered_date,
        len(turns),
        len(speakers),
        attribution,
    )
    return source, turns


def to_prompt_text(turns: tuple[TranscriptTurn, ...], *, limit: int) -> str:
    """Render turns as ``Speaker: text`` lines, keeping the TAIL within the limit.

    The tail, not the head: the end of a standup is where the corrections land
    ("oh, and I also did…"), which is exactly the material this feature exists
    to catch.
    """
    lines = [f"{t.speaker}: {t.text}" if t.speaker else t.text for t in turns]
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    clipped = text[-limit:]
    return "…(earlier discussion omitted)…\n" + clipped[clipped.find("\n") + 1 :]


# ---------------------------------------------------------------------------
# "You never checked that standup against its meeting"
# ---------------------------------------------------------------------------

# Standups a brand-new user gets before we mention transcripts at all. Greeting
# somebody's first week with a chore is how a feature gets switched off.
_NUDGE_GRACE_RUNS = 3
# Consecutive unchecked standups before the wording firms up and the line starts
# reaching Slack and email. Comfortably above the grace period on purpose: the
# first thing anyone hears must be the quiet, TUI-only invite, never a broadcast.
_NUDGE_REMINDER_STREAK = 5
# …and before it stops asking altogether and offers the off switch instead.
# Reachable inside the _LOOKBACK_DAYS window (~10 weekday standups), or the
# escalated wording could never actually fire.
_NUDGE_ESCALATE_STREAK = 8


def missing_transcript_dates(
    session_id: str,
    *,
    db_path: Path | None = None,
    before_date: str = "",
    today: date | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Standups with no transcript. Returns ``(missed, ran, ever_reviewed)``.

    Both tuples are newest-first, so the caller can walk ``ran`` to find the
    consecutive-miss streak. A pure set difference over two indexed queries — no
    LLM, no file I/O, no network — because the TUI calls this on every hub
    refresh.

    The window is the same ``_LOOKBACK_DAYS`` the sweep uses, and the population
    is *dates a standup ran*, not calendar days, so weekends, holidays and a
    week off cost nothing.
    """
    from yeaboi.paths import get_db_path
    from yeaboi.standup.store import StandupStore

    today = today or date.today()
    horizon = before_date or (today + timedelta(days=1)).isoformat()
    try:
        since = (parse_date(horizon) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
    except ValueError:
        return (), (), False

    with StandupStore(db_path or get_db_path()) as store:
        ran = store.run_dates(session_id, since=since, before=horizon)
        reviewed = store.reviewed_dates(session_id)
    missed = tuple(sorted(ran - reviewed, reverse=True))
    return missed, tuple(sorted(ran, reverse=True)), bool(reviewed)


def transcript_nudge(
    session_id: str,
    *,
    config: dict | None = None,
    db_path: Path | None = None,
    before_date: str = "",
    today: date | None = None,
) -> TranscriptNudge:
    """Should we say anything about missing transcripts? Usually not.

    Rate limiting here is STRUCTURAL rather than stateful: a nudge only ever
    rides on a report, there is at most one report per run, and ``level`` is a
    pure function of the streak — so re-running standup twice in a day produces
    the identical line instead of ratcheting. And because the sweep runs before
    the report's warnings are assembled, a transcript dropped this morning is
    already recorded by the time this is computed, so it self-clears in the same
    run that would otherwise have complained.

    ``before_date`` is what makes the first of those two claims true. A run is
    recorded when it finishes, so a SECOND standup on the same day would see the
    first one sitting in history with no transcript against it — for a meeting
    that has only just happened — and count it as a miss, ratcheting a quiet
    TUI-only ``invite`` into a ``reminder`` that broadcasts to Slack and email.
    ``run_standup`` therefore passes its own date and the day is excluded. The
    reminder job wants the opposite (it fires *after* the meeting, and today is
    exactly what it is asking about), so it leaves this blank.
    """
    if config is not None and not config.get("transcript_review_enabled", True):
        # The opt-out already exists; a nudge for a feature you turned off is
        # just noise.
        logger.debug("transcript nudge: review disabled for session=%s", session_id)
        return TranscriptNudge(session_id=session_id)

    missed, ran, ever = missing_transcript_dates(session_id, db_path=db_path, before_date=before_date, today=today)
    standup_count = len(ran)
    if not missed or not standup_count:
        logger.debug("transcript nudge: nothing missed for session=%s", session_id)
        return TranscriptNudge(session_id=session_id, standup_count=standup_count, ever_reviewed=ever)

    # Consecutive misses counting back from the MOST RECENT standup: a team that
    # transcribed yesterday is not behind, whatever last month looked like.
    missed_set = set(missed)
    streak = 0
    for day in ran:
        if day not in missed_set:
            break
        streak += 1

    # ``ever`` gates only the GRACE PERIOD, not the ladder. Someone who has never
    # reviewed a transcript is exactly who most needs to reach the escalated
    # wording eventually — that is where the off switch is offered — so they
    # climb the same rungs as everybody else once the grace period is over.
    if not ever and standup_count < _NUDGE_GRACE_RUNS:
        logger.debug("transcript nudge: still in the grace period for session=%s", session_id)
        return TranscriptNudge(session_id=session_id, standup_count=standup_count, ever_reviewed=ever)

    if streak < _NUDGE_REMINDER_STREAK:
        level = "invite"
        message = (
            f"No transcript for the {missed[0]} standup — drop the recording in ~/.yeaboi/transcripts "
            "and the next run will check what it missed."
        )
    elif streak < _NUDGE_ESCALATE_STREAK:
        level = "reminder"
        message = (
            f"{len(missed)} standups since {missed[-1]} were never checked against their meetings — "
            "drop transcripts in ~/.yeaboi/transcripts, or point Standup at your recordings folder."
        )
    else:
        level = "escalated"
        message = (
            f"{len(missed)} standups have gone unchecked. If your team doesn't record standups, turn this "
            "off in Standup › Review › Change my transcript folders."
        )

    logger.info(
        "transcript nudge: session=%s level=%s streak=%d missed=%d of %d standup(s)",
        session_id,
        level,
        streak,
        len(missed),
        standup_count,
    )
    return TranscriptNudge(
        session_id=session_id,
        missed_dates=missed,
        streak=streak,
        standup_count=standup_count,
        ever_reviewed=ever,
        level=level,
        message=message,
    )


# ---------------------------------------------------------------------------
# Importing text that never was a file (a paste, a pipe, an MCP argument)
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")
# macOS Terminal drops a *quoted* path; iTerm2 drops a *backslash-escaped* one.
_DRAG_ESCAPE_RE = re.compile(r"\\(.)")


def _slug(label: str) -> str:
    out = _SLUG_RE.sub("-", label.strip().lower()).strip("-")
    return out[:40] or "pasted"


def normalize_dropped_path(raw: str) -> str:
    """Clean up a path the user dragged from a file manager into a prompt.

    macOS Terminal produces ``'/Users/me/My Meetings/a.vtt' `` (quoted, trailing
    space); iTerm2 produces ``/Users/me/My\\ Meetings/a.vtt`` (escaped). Both
    otherwise reach ``Path()`` verbatim and fail as "not found", which reads like
    the file is missing rather than like the prompt mangled it.
    """
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    else:
        # Only unescape an unquoted path: inside quotes a backslash is literal.
        value = _DRAG_ESCAPE_RE.sub(r"\1", value)
    return str(Path(value).expanduser()) if value else ""


def import_text(
    text: str,
    *,
    covered_date: str = "",
    label: str = "",
    today: date | None = None,
) -> Path:
    """Write pasted/piped transcript text into the managed folder; return the path.

    The resolved date goes in the FILENAME, and that is the whole trick:
    :func:`infer_date` reads the filename first, so a later sweep attributes the
    import to the right standup without any sidecar metadata and without knowing
    it was ever a paste.

    The text is written VERBATIM — no header, no "imported by yeaboi" banner. A
    banner line survives ``_MD_PREFIX_RE``, fails ``_SPEAKER_RE``, and lands as an
    unattributed leading turn; on a short transcript that alone can drag the
    labelled share under ``_LABELLED_LINE_RATIO`` and silently flip attribution to
    "unlabelled", which narrows what the review is then allowed to conclude.

    Raises ``ValueError`` on empty text, oversized text, or a malformed
    ``covered_date``.
    """
    from yeaboi.fs_policy import resolve_and_check
    from yeaboi.paths import get_transcripts_dir

    body = text.strip("\n")
    if not body.strip():
        raise ValueError("Nothing to import — the transcript text is empty.")
    payload = body.encode("utf-8") + b"\n"
    if len(payload) > _MAX_BYTES:
        raise ValueError(f"Transcript is larger than {_MAX_BYTES:,} bytes — save it to a file and use --transcript.")

    today = today or date.today()
    resolved_date = ""
    if covered_date.strip():
        try:
            given = parse_date(covered_date.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid covered_date {covered_date!r} — expected YYYY-MM-DD.") from exc
        resolved_date = min(given, today).isoformat()  # a future date is not a standup that happened
    resolved_date = resolved_date or infer_date_from_text(body, today=today) or today.isoformat()

    directory = get_transcripts_dir()
    stem = f"{resolved_date}-{_slug(label)}"

    def _dest(name: str) -> Path:
        # Not ceremony: this catches a symlinked ~/.yeaboi/transcripts pointing
        # somewhere it shouldn't, exactly as read_transcript's check does.
        return resolve_and_check(directory / name, mode="write", context="Standup — transcript import")

    dest = _dest(f"{stem}.txt")
    counter = 2
    while dest.exists():
        if dest.read_bytes() == payload:
            # Re-pasting the same text is idempotent rather than littering.
            logger.info("transcripts: import already present as %s", dest.name)
            return dest
        dest = _dest(f"{stem}-{counter}.txt")
        counter += 1

    dest.write_bytes(payload)
    # The user never chose this location, and the file holds real names.
    dest.chmod(0o600)
    logger.info("transcripts: imported %d chars as %s (date=%s)", len(body), dest.name, resolved_date)
    return dest


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _scan_entries(directory: Path, *, recurse: bool) -> list[Path]:
    """List file entries under ``directory``, bounded in depth and count.

    Replaces ``rglob("*")``, which is unbounded in both directions. Dot-directories
    are skipped wholesale (a ``.git`` or ``.Trash`` tree holds no transcripts and
    can hold a hundred thousand files).
    """
    try:
        top = sorted(directory.iterdir())
    except OSError as exc:
        logger.warning("transcripts: cannot list %s: %s", directory, exc)
        return []
    if not recurse:
        return top

    out: list[Path] = []
    visited = 0
    queue: list[tuple[list[Path], int]] = [(top, 0)]
    while queue:
        entries, depth = queue.pop(0)
        for entry in entries:
            if visited >= _MAX_SCAN_ENTRIES:
                logger.warning(
                    "transcripts: stopped scanning %s after %d entries — point transcript_dir at a narrower folder",
                    directory,
                    _MAX_SCAN_ENTRIES,
                )
                return out
            visited += 1
            try:
                is_dir = entry.is_dir()
            except OSError as exc:
                logger.warning("transcripts: cannot stat %s: %s", entry, exc)
                continue
            if not is_dir:
                out.append(entry)
                continue
            if depth >= _MAX_SCAN_DEPTH or entry.name.startswith("."):
                continue
            try:
                queue.append((sorted(entry.iterdir()), depth + 1))
            except OSError as exc:
                logger.warning("transcripts: cannot list %s: %s", entry, exc)
    return out


def _candidate_files(directory: Path, *, recurse: bool) -> list[Path]:
    """List plausible transcript files in a directory, newest name order last."""
    out: list[Path] = []
    for entry in _scan_entries(directory, recurse=recurse):
        if entry.name.startswith("."):
            continue
        try:
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in TRANSCRIPT_SUFFIXES:
                logger.debug("transcripts: skipping %s (unsupported suffix)", entry.name)
                continue
            if entry.stat().st_size > _MAX_BYTES:
                logger.warning("transcripts: skipping %s (larger than %d bytes)", entry.name, _MAX_BYTES)
                continue
        except OSError as exc:
            logger.warning("transcripts: cannot stat %s: %s", entry, exc)
            continue
        out.append(entry)
    return out


def external_dir(config: dict | None) -> str:
    """The configured external transcript folder, or "" when unset."""
    return str((config or {}).get("transcript_dir") or "").strip()


def discover(
    session_id: str,
    *,
    config: dict | None = None,
    before_date: str = "",
    db_path: Path | None = None,
    today: date | None = None,
    include_reviewed: bool = False,
) -> tuple[list[tuple[Path, bool]], list[str]]:
    """Find unreviewed transcripts worth reviewing.

    Returns ``([(path, is_external), ...], warnings)``. Deliberately returns
    warnings rather than raising: a denied external folder, an unreadable file
    or a missing directory must degrade the sweep, never fail the standup that
    is about to run.

    ``before_date`` restricts to transcripts covering EARLIER standups (the
    automatic pre-standup sweep); blank means "any date" (an on-demand review).
    """
    from yeaboi.paths import get_db_path, get_transcripts_dir
    from yeaboi.standup.store import StandupStore

    warnings: list[str] = []
    today = today or date.today()

    directories: list[tuple[Path, bool]] = [(get_transcripts_dir(), False)]
    configured = external_dir(config)
    if configured:
        from yeaboi.fs_policy import SandboxViolationError, resolve_and_check

        try:
            directories.append(
                (resolve_and_check(configured, mode="read", context="Standup — transcript folder"), True)
            )
        except SandboxViolationError as exc:
            # The scheduled run is non-interactive, so it cannot consent. Say so
            # once, with the exception's own actionable message, instead of
            # quietly reviewing nothing week after week.
            logger.warning("transcripts: external folder denied: %s", exc)
            warnings.append(f"Transcript folder skipped — {exc}")

    seen_hashes: set[str] = set()
    if not include_reviewed:
        with StandupStore(db_path or get_db_path()) as store:
            seen_hashes = store.reviewed_transcript_hashes(session_id)

    cutoff = ""
    if before_date:
        try:
            cutoff = (parse_date(before_date) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        except ValueError:
            cutoff = ""

    dated: list[tuple[str, Path, bool]] = []
    for directory, is_external in directories:
        if not directory.is_dir():
            if is_external:
                warnings.append(f"Transcript folder not found: {directory}")
            continue
        for path in _candidate_files(directory, recurse=is_external):
            try:
                digest = content_hash(path)
            except OSError as exc:
                logger.warning("transcripts: cannot hash %s: %s", path, exc)
                continue
            if digest in seen_hashes:
                logger.debug("transcripts: skipping %s (already reviewed)", path.name)
                continue
            # JSON needs the WHOLE document, every other format only its head.
            # infer_date's structured-field step runs json.loads, and the first
            # 2 000 chars of a real segments array is almost never valid JSON —
            # so a head-only scan here would fall through to mtime and window the
            # file out on a date that read_transcript (which parses the full
            # text) would never have given it. The file then silently never gets
            # reviewed, in a feature whose whole point is not missing one.
            # _MAX_CHARS is the same bound read_transcript applies, so the two
            # see byte-identical input and cannot disagree.
            limit = _MAX_CHARS if path.suffix.lower() == ".json" else _DATE_SCAN_CHARS
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:limit]
            except OSError as exc:
                logger.warning("transcripts: cannot read %s: %s", path, exc)
                continue
            covered = infer_date(path, head, today=today)
            if before_date and not (cutoff <= covered < before_date):
                logger.debug("transcripts: skipping %s (covers %s, outside window)", path.name, covered)
                continue
            dated.append((covered, path, is_external))

    dated.sort(key=lambda item: (item[0], item[1].name))
    if len(dated) > _MAX_FILES_PER_SWEEP:
        dropped = len(dated) - _MAX_FILES_PER_SWEEP
        # WHICH end of the cap we keep depends on whether there is a window.
        # The automatic sweep is bounded to the last _LOOKBACK_DAYS, so oldest
        # first drains a backlog in order. An on-demand review has NO window —
        # there, oldest-first would spend the entire budget on the oldest files
        # on disk, which is exactly what happens the first time someone points
        # transcript_dir at a folder holding a year of recordings.
        if before_date:
            dated, direction = dated[:_MAX_FILES_PER_SWEEP], "oldest"
        else:
            dated, direction = dated[-_MAX_FILES_PER_SWEEP:], "newest"
        logger.warning(
            "transcripts: %d transcript(s) beyond the per-sweep cap were deferred (kept the %s %d)",
            dropped,
            direction,
            _MAX_FILES_PER_SWEEP,
        )
        warnings.append(
            f"{dropped} more transcript(s) found — reviewing the {direction} {_MAX_FILES_PER_SWEEP}; "
            "the rest wait for the next run."
        )

    logger.info(
        "transcripts: discovered %d transcript(s) for session=%s before=%s",
        len(dated),
        session_id,
        before_date or "-",
    )
    return [(path, is_external) for _covered, path, is_external in dated], warnings
