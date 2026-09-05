"""What a security finding *means*: a verdict word and the reason for it.

A regex tells you a shape matched; it cannot tell you whether the agent ran
``curl | sh`` or typed it into a test fixture. The collector now records where
each match sat (its ``context``: the command that ran, a heredoc body, a
``Write`` input, a file the agent read…) and the file that context pointed at.
This module turns that into one of five words the page groups by:

    needs-decision  the command actually ran, or a live-looking key sat in a
                    command or in what the person typed
    unsure          a generic shape, or a row scanned before context existed
    test-data       written into or read from a test/fixture/docs file
    handled         dismissed, fixed, or rotated — the reason is the dismissal's
    info            an informational severity

Pure functions; the engine applies them and the page renders them.
"""

from __future__ import annotations

import re

NEEDS_DECISION = "needs-decision"
UNSURE = "unsure"
TEST_DATA = "test-data"
HANDLED = "handled"
INFO = "info"

VERDICTS: tuple[str, ...] = (NEEDS_DECISION, UNSURE, TEST_DATA, HANDLED, INFO)
VERDICT_ORDER: dict[str, int] = {v: i for i, v in enumerate(VERDICTS)}

# Paths whose contents are text about secrets and commands rather than the
# things themselves: tests, fixtures, docs, plans, the redaction module.
_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|fixtures?|__fixtures__|specs?|docs?|examples?|samples?|testdata)(?:/|$)"
    r"|(?:^|/)(?:test_[^/]*\.py|[^/]*_test\.(?:go|py|rs)|[^/]*\.(?:test|spec)\.[jt]sx?|conftest\.py)$"
    r"|(?:^|/)redaction[^/]*\.py$|\.md$|\.rst$|\.plan$|\.cast(?:\.gz)?$|/\.claude/plans/|/scratchpad/",
    re.IGNORECASE,
)
# Text an agent wrote somewhere (a Write tool input, a heredoc redirected to a
# file). An inline script (``bash -c``, ``python -c``) and a heredoc fed to an
# interpreter both RAN, so they are never in this set.
_WRITTEN = frozenset({"heredoc", "write-input"})
_RAN = frozenset({"command", "inline-script", "tool-input"})


def looks_like_test_path(path: str) -> bool:
    """True when ``path`` is somewhere text about secrets belongs."""
    return bool(path) and _TEST_PATH.search(path) is not None


def verdict(
    *,
    category: str,
    severity: str,
    context: str,
    target: str = "",
    generic: bool = False,
    dismissed_reason: str | None = None,
) -> tuple[str, str]:
    """``(verdict, reason)`` for one grouped finding."""
    if dismissed_reason is not None:
        return HANDLED, dismissed_reason or "set aside"
    if severity == "info":
        return INFO, "informational"
    if category in ("settings", "mcp"):
        return NEEDS_DECISION, "a fact about your configuration, not a transcript match"
    if not context:
        return UNSURE, "scanned before the context was recorded — re-run to classify"
    if category == "risky_tool":
        if context == "heredoc" and not target:
            return NEEDS_DECISION, "fed to a program through a heredoc — it ran"
        if context == "inline-script":
            return NEEDS_DECISION, "ran as an inline script"
        if context in _WRITTEN:
            return TEST_DATA, "written into a file, not run"
        if context == "tool-result":
            return TEST_DATA, "seen in a file the agent read, not run"
        return NEEDS_DECISION, "the command actually ran"
    # secrets
    if context in _WRITTEN or context == "tool-result":
        verb = "wrote" if context in _WRITTEN else "read"
        if looks_like_test_path(target):
            return TEST_DATA, f"in a test or docs file the agent {verb}"
        if generic:
            return UNSURE, f"a generic shape in a file the agent {verb} — open the replay"
        if not target:
            return UNSURE, f"a live-looking key in text the agent {verb}, file unknown — open the replay"
        return NEEDS_DECISION, f"a live-looking key in a file the agent {verb}"
    if generic:
        return UNSURE, "a generic shape — open the replay to see what it was"
    where = {
        "user-prompt": "what you typed",
        "command": "a command",
        "inline-script": "an inline script that ran",
        "tool-input": "a tool call",
    }.get(context, "the reply")
    return NEEDS_DECISION, f"a live-looking key in {where}"


def worst(verdicts) -> str:
    """The verdict that decides a group's placement: needs-decision beats everything."""
    ordered = sorted((v for v in verdicts if v in VERDICT_ORDER), key=VERDICT_ORDER.__getitem__)
    return ordered[0] if ordered else UNSURE


def _count_phrase(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def verdict_line(counts: dict[str, int]) -> str:
    """The one sentence at the top of the page, from finding counts per verdict."""
    decide = counts.get(NEEDS_DECISION, 0)
    if decide == 0:
        head = "Nothing needs a decision."
    elif decide == 1:
        head = "One thing needs a decision."
    else:
        words = {2: "Two", 3: "Three", 4: "Four", 5: "Five"}
        head = f"{words.get(decide, str(decide))} things need a decision."
    rest = []
    if counts.get(UNSURE):
        rest.append(_count_phrase(counts[UNSURE], "is worth a look", "are worth a look"))
    if counts.get(TEST_DATA):
        rest.append(_count_phrase(counts[TEST_DATA], "looks like test data", "look like test data"))
    if counts.get(HANDLED):
        rest.append(_count_phrase(counts[HANDLED], "is handled", "are handled"))
    if counts.get(INFO):
        rest.append(_count_phrase(counts[INFO], "is informational", "are informational"))
    if not rest:
        return head
    if len(rest) == 1:
        return f"{head} {rest[0][0].upper()}{rest[0][1:]}."
    tail = ", ".join(rest[:-1]) + f" and {rest[-1]}"
    return f"{head} {tail[0].upper()}{tail[1:]}."
