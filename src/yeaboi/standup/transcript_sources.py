"""Find the folder the user's meeting recordings already land in.

The transcript review only works if a transcript exists somewhere yeaboi looks,
and asking somebody to type the path to their Zoom folder is asking them to go
find it. This probes the handful of well-known locations and offers them by name
with a count, so the setup question becomes "is this the right one?" instead of
"where is it?".

Deterministic and offline: a filesystem probe, no network, no LLM. Every
candidate is *offered*, never adopted — see ``detect`` for why that is
structural rather than cautious.

Deliberately narrower than the list of tools that produce transcripts, because a
folder we cannot actually parse is worse than one we never mention:

- **Zoom** is the best case. ``GMT20260802-093000_Recording.transcript.vtt``
  hits ``_COMPACT_DATE_RE`` in the stem and ``_parse_vtt`` understands its
  ``<v Alice>`` voice spans, so attribution and dating both work with no setup.
- **Google Meet** recordings land in Drive. Only real ``.txt``/``.vtt`` files are
  counted: a native-Docs transcript syncs as a ``.gdoc`` JSON stub, so counting
  those would promise a folder that yields nothing.
- **~/Downloads** is where Teams, Otter, Fathom and Fireflies exports actually
  end up. Non-recursive, and only when a transcript is already sitting at the top
  level — recursing somebody's whole download history is slow, and a whitelist
  grant is read+write (``fs_policy._RW``), so it hands over more than it looks.
- **Granola** is excluded: its local store is a proprietary ``cache-v3.json``
  blob that ``_parse_json`` would not recognise, fall through to the line parser,
  and feed as garbage to an LLM call. Its real export is markdown to a folder the
  user picks, which has no fixed path.
- **Obsidian vaults** are excluded: every note is ``.md``, so a file count means
  nothing, and a recursive sweep would review somebody's diary.

# See docs: "Guardrails" — the filesystem sandbox layer (fs_policy)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Counting is a preview, not a sweep — it must stay fast enough to run while a
# setup screen is drawing.
_MAX_COUNT = 200

# Suffixes that make a folder worth *offering*. Narrower than
# TRANSCRIPT_SUFFIXES: .md and .json are real transcript formats but also the
# two most common non-transcript files on a disk, so a folder whose only
# evidence is "it contains markdown" is not evidence.
_STRONG_SUFFIXES = (".vtt", ".srt")


@dataclass(frozen=True)
class SourceCandidate:
    """One folder that plausibly holds meeting transcripts."""

    label: str = ""
    path: str = ""
    file_count: int = 0
    newest_date: str = ""
    allowed: bool = False  # already inside the sandbox / whitelist
    recurse: bool = False


@dataclass(frozen=True)
class _Probe:
    label: str
    patterns: tuple[str, ...]  # home-relative globs; the first that resolves wins
    recurse: bool = True
    require_strong: bool = False  # only offer when a .vtt/.srt is present


_PROBES: tuple[_Probe, ...] = (
    _Probe("Zoom recordings", ("Documents/Zoom", "Zoom"), recurse=True),
    _Probe(
        "Google Meet recordings",
        (
            "Library/CloudStorage/GoogleDrive-*/My Drive/Meet Recordings",
            "Google Drive/My Drive/Meet Recordings",
            "Google Drive/Meet Recordings",
        ),
        recurse=True,
    ),
    _Probe("Microsoft Teams recordings", ("Documents/Microsoft Teams Chat Files",), recurse=True),
    # Teams/Otter/Fathom/Fireflies all export here. Top level only, and only when
    # a transcript is already visibly sitting in it.
    _Probe("Downloads", ("Downloads",), recurse=False, require_strong=True),
)


def _expand(home: Path, pattern: str) -> list[Path]:
    """Resolve one home-relative pattern, globbing only where a ``*`` appears."""
    if "*" not in pattern:
        candidate = home / pattern
        return [candidate] if candidate.is_dir() else []
    try:
        return sorted(p for p in home.glob(pattern) if p.is_dir())
    except OSError as exc:
        logger.debug("transcript_sources: cannot glob %s: %s", pattern, exc)
        return []


def _summarise(directory: Path, *, recurse: bool) -> tuple[int, str, bool]:
    """Return ``(transcript count, newest covered date, has a .vtt/.srt)``."""
    from yeaboi.standup import transcripts as _transcripts

    try:
        files = _transcripts._candidate_files(directory, recurse=recurse)
    except OSError as exc:
        logger.debug("transcript_sources: cannot scan %s: %s", directory, exc)
        return 0, "", False

    files = files[:_MAX_COUNT]
    strong = any(p.suffix.lower() in _STRONG_SUFFIXES for p in files)
    newest = ""
    for path in files:
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:2_000]
        except OSError:
            continue
        covered = _transcripts.infer_date(path, head)
        if covered > newest:
            newest = covered
    return len(files), newest, strong


def detect(*, home: Path | None = None) -> list[SourceCandidate]:
    """Probe the well-known recording folders. Returns the ones worth offering.

    ``home`` is injectable so tests never touch a real ``$HOME``.

    Candidates come back with ``allowed`` telling the caller whether the sandbox
    already permits reading them — almost always ``False``, because these live
    outside ``~/.yeaboi``. **Never adopt one without asking.** Three reasons, all
    structural:

    1. ``resolve_and_check`` will refuse an ungranted path, so a silently saved
       ``transcript_dir`` produces a config that warns-and-does-nothing on every
       scheduled run. ``transcripts.discover`` already handles that denial
       gracefully, which is exactly why the failure would be invisible.
    2. Adopting a folder means sending meeting text containing real names to an
       LLM. That is what the consent prompt exists for.
    3. A whitelist grant is directory-wide and read+write.
    """
    from yeaboi.fs_policy import is_allowed

    home = home or Path.home()
    out: list[SourceCandidate] = []
    seen: set[str] = set()

    for probe in _PROBES:
        for pattern in probe.patterns:
            directories = _expand(home, pattern)
            if not directories:
                continue
            for directory in directories:
                key = str(directory)
                if key in seen:
                    continue
                seen.add(key)
                count, newest, strong = _summarise(directory, recurse=probe.recurse)
                if not count or (probe.require_strong and not strong):
                    continue
                out.append(
                    SourceCandidate(
                        label=probe.label,
                        path=key,
                        file_count=count,
                        newest_date=newest,
                        allowed=is_allowed(directory, mode="read"),
                        recurse=probe.recurse,
                    )
                )
            break  # the first pattern that resolved wins for this probe

    logger.info("transcript_sources: detected %d candidate folder(s)", len(out))
    return out


def describe(candidate: SourceCandidate) -> str:
    """A one-line description for an option row: how much, and how recent."""
    plural = "" if candidate.file_count == 1 else "s"
    base = f"{candidate.file_count} transcript{plural}"
    if candidate.file_count >= _MAX_COUNT:
        base = f"{_MAX_COUNT}+ transcripts"
    if candidate.newest_date:
        base += f", newest {candidate.newest_date}"
    return base
