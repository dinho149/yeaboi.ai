"""Detect automation/service-hook activity posted under a human's identity.

Motivating incident: a Wiz security-scanner service hook posted PR review
comments using a team member's PAT, and the standup credited "review comments
across 18 pull requests" to the human. Author-based bot detection cannot catch
this — the author IS the human — so this module inspects item *content*
(scanner signatures, service-hook boilerplate), provider metadata
(``author_type == "bot"``), and *volume patterns* (bursts of near-identical
comments across many repositories).

Pure module, no I/O. Precision over recall, mirroring
``analysis/ai_usage.py``'s convention: a false "excluded your real work" is
worse than a missed bot, so default markers are attribution-shaped phrases
(never bare product names) and the burst heuristic needs several corroborating
items before it fires. Detection applies ONLY to review/comment kinds — a
human commit titled "fix Wiz finding" is never touched. Exclusions are always
surfaced as Notices (see ``notice_lines``), never silent.

# See docs: "Daily Standup" — activity attribution
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Config values for standup_config.automation_handling.
VALID_AUTOMATION_HANDLING = ("exclude", "off")

# Only conversational kinds can be service-hook noise; commits/PRs/work items
# are attributed from provider history and stay untouched.
_DETECTABLE_KINDS = frozenset({"review", "comment"})

# --- Layer a: content markers -------------------------------------------------
# Attribution-shaped signatures of security/quality scanners that post PR
# comments via service hooks. Deliberately NOT bare product names ("wiz" alone
# would match "wizard") — org-specific hooks go in the user's
# automation_markers config instead.
_SCANNER_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "wiz",
        re.compile(
            r"\bwiz\.io\b|\bwiz(?:cli|-bot)\b|\bwiz (?:scan(?:ner)?|security|iac|guardrail)s?\b"
            r"|reported by wiz\b|wiz found\b|powered by wiz\b",
            re.I,
        ),
    ),
    ("sonar", re.compile(r"\bsonar(?:qube|cloud|lint|source)\b|quality gate (?:passed|failed|is red)", re.I)),
    ("snyk", re.compile(r"\bsnyk\b", re.I)),
    ("checkmarx", re.compile(r"\bcheckmarx\b|\bcx(?:one|sast|flow)\b", re.I)),
    ("veracode", re.compile(r"\bveracode\b", re.I)),
    ("codeql", re.compile(r"\bcodeql\b|github advanced security", re.I)),
    ("semgrep", re.compile(r"\bsemgrep\b", re.I)),
    ("trivy", re.compile(r"\btrivy\b|aquasec(?:urity)?/trivy", re.I)),
    ("fortify", re.compile(r"\bfortify (?:sast|sca|scan|on demand)\b|micro ?focus fortify", re.I)),
    ("blackduck", re.compile(r"\bblack ?duck\b|\bcoverity\b", re.I)),
    ("prisma", re.compile(r"\bprisma cloud\b|\btwistlock\b", re.I)),
    ("dependency", re.compile(r"\bdependabot\b|\brenovate\b|\bwhitesource\b|\bmend\.io\b", re.I)),
)

_SERVICE_HOOK_BOILERPLATE = re.compile(
    r"this (?:comment|message|review) (?:was|is) (?:auto-?generated|automatically (?:generated|posted))"
    r"|automated (?:security|code|vulnerability|compliance) (?:scan|review|analysis|finding)"
    r"|do not (?:reply|respond) to this (?:automated )?(?:comment|message)"
    r"|posted (?:automatically|by (?:an )?automation|via (?:a )?service hook)"
    r"|this is an automated (?:comment|message|review)",
    re.I,
)

# --- Layer b: provider metadata ------------------------------------------------
_BOT_AUTHOR_RE = re.compile(r"\[bot\]$", re.I)

# --- Layer c: burst heuristic ---------------------------------------------------
# A masquerading hook posts many near-identical comments in one sweep. Humans
# rarely paste the same ≥40-char text across 3+ repositories in one window.
_BURST_MIN_CLUSTER = 5  # K near-identical items…
_BURST_MIN_REPOS = 3  # …across at least M distinct repositories
_BURST_SINGLE_REPO_MIN = 10  # template spam confined to one repo needs more evidence
_MIN_FINGERPRINT_CHARS = 40  # "lgtm" / "nit: typo" repeats never cluster

_URL_RE = re.compile(r"https?://\S+")
_HEX_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_PATH_RE = re.compile(r"[\w.-]+(?:/[\w.-]+)+")


@dataclass(frozen=True)
class AutomationCluster:
    """One detected group of automated items, for logging + Notices."""

    # "marker:<id>" | "boilerplate" | "bot-author" | "burst-cross-repo" | "burst-template" | "burst-same-second"
    reason: str
    label: str  # human-readable, e.g. "matched 'wiz'" / "near-identical bodies"
    author: str  # the identity the items were posted under
    count: int
    kind: str
    repositories: tuple[str, ...]
    keys: tuple[str, ...]  # item keys, for debug logging only


def parse_custom_markers(raw: str) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Compile the user's comma-separated ``automation_markers`` config.

    Each token becomes a word-bounded case-insensitive pattern. A bare word
    ("wiz") is acceptable HERE because the user explicitly opted in for their
    own org's hook signature.
    """
    markers: list[tuple[str, re.Pattern[str]]] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if token:
            markers.append((token, re.compile(rf"\b{re.escape(token)}\b", re.I)))
    return tuple(markers)


def _marker_hit(item: dict, custom_markers: Sequence[tuple[str, re.Pattern[str]]]) -> tuple[str, str] | None:
    """Return (reason, label) when the item's BODY carries an automation signature.

    Body only, never the title: review/comment titles are synthesized by our
    fetchers from the PR title ("reviewed PR #12: fix snyk findings"), so a
    human's genuine review of a PR *about* scanner work would otherwise match a
    scanner marker and lose credit. A hook's signature lives in the text it
    posted — the body.
    """
    text = str(item.get("body", "") or "")
    for marker_id, pattern in (*custom_markers, *_SCANNER_MARKERS):
        if pattern.search(text):
            return f"marker:{marker_id}", f"matched '{marker_id}'"
    if _SERVICE_HOOK_BOILERPLATE.search(text):
        return "boilerplate", "service-hook boilerplate"
    return None


def _bot_author_hit(item: dict) -> tuple[str, str] | None:
    if item.get("author_type") == "bot" or _BOT_AUTHOR_RE.search(item.get("author", "") or ""):
        return "bot-author", "bot account"
    return None


def _fingerprint(item: dict) -> str:
    """Normalize a comment body into a template fingerprint.

    Scanner comments differ only in paths/line numbers/URLs/hashes — replacing
    those with placeholders makes a whole sweep collapse to one key. Short
    texts return "" so common human repeats ("lgtm") never cluster.
    """
    text = (item.get("body") or item.get("title") or "").lower()
    text = _URL_RE.sub("<url>", text)
    text = _HEX_RE.sub("<hex>", text)
    text = _PATH_RE.sub("<path>", text)
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < _MIN_FINGERPRINT_CHARS:
        return ""
    return text[:160]


def _burst_clusters(items: Sequence[tuple[int, dict]]) -> dict[int, tuple[str, str]]:
    """Return {item index: (reason, label)} for indices caught by volume patterns."""
    hits: dict[int, tuple[str, str]] = {}

    # Template bursts: same author + kind + normalized body.
    by_template: dict[tuple[str, str, str], list[tuple[int, dict]]] = {}
    for idx, item in items:
        fp = _fingerprint(item)
        if fp:
            key = ((item.get("author") or "").lower(), item.get("kind", ""), fp)
            by_template.setdefault(key, []).append((idx, item))
    for group in by_template.values():
        repos = {i.get("repository", "") for _, i in group}
        if len(group) >= _BURST_MIN_CLUSTER and len(repos) >= _BURST_MIN_REPOS:
            reason, label = "burst-cross-repo", f"near-identical across {len(repos)} repositories"
        elif len(group) >= _BURST_SINGLE_REPO_MIN:
            reason, label = "burst-template", "near-identical template comments"
        else:
            continue
        for idx, _ in group:
            hits.setdefault(idx, (reason, label))

    # Same-second sweeps: one identity cannot humanly comment in 3+ repos in one second.
    by_second: dict[tuple[str, str, str], list[tuple[int, dict]]] = {}
    for idx, item in items:
        ts = item.get("timestamp", "") or ""
        if ts:
            key = ((item.get("author") or "").lower(), item.get("kind", ""), ts)
            by_second.setdefault(key, []).append((idx, item))
    for group in by_second.values():
        repos = {i.get("repository", "") for _, i in group}
        if len(repos) >= _BURST_MIN_REPOS:
            for idx, _ in group:
                hits.setdefault(idx, ("burst-same-second", f"same-second posts in {len(repos)} repositories"))
    return hits


def partition_automated(
    items: Iterable[dict],
    *,
    custom_markers: Sequence[tuple[str, re.Pattern[str]]] = (),
) -> tuple[list[dict], list[AutomationCluster]]:
    """Split activity items into (kept, automated-clusters).

    Kept items preserve input order. Clusters aggregate excluded items by
    (reason, author, kind) so Notices stay short even for big sweeps.
    """
    all_items = list(items)
    detectable = [(idx, item) for idx, item in enumerate(all_items) if item.get("kind") in _DETECTABLE_KINDS]

    flagged: dict[int, tuple[str, str]] = {}
    for idx, item in detectable:
        hit = _marker_hit(item, custom_markers) or _bot_author_hit(item)
        if hit:
            flagged[idx] = hit
    for idx, hit in _burst_clusters(detectable).items():
        flagged.setdefault(idx, hit)

    if not flagged:
        return all_items, []

    kept = [item for idx, item in enumerate(all_items) if idx not in flagged]
    grouped: dict[tuple[str, str, str, str], list[dict]] = {}
    for idx, (reason, label) in sorted(flagged.items()):
        item = all_items[idx]
        key = (reason, label, (item.get("author") or ""), item.get("kind", ""))
        grouped.setdefault(key, []).append(item)
    clusters = [
        AutomationCluster(
            reason=reason,
            label=label,
            author=author,
            count=len(group),
            kind=kind,
            repositories=tuple(sorted({i.get("repository", "") for i in group if i.get("repository")})),
            keys=tuple(str(i.get("key", "")) for i in group),
        )
        for (reason, label, author, kind), group in grouped.items()
    ]
    return kept, clusters


def notice_lines(clusters: Sequence[AutomationCluster]) -> list[str]:
    """Human-readable Notices explaining exactly what was excluded and how to tune it.

    One short line per cluster, plus a single shared how-to-tune tail: the
    notice recurs every run, so the config instructions must not — repeating
    them per cluster made a 16-item exclusion a paragraph.
    """
    lines: list[str] = []
    for c in clusters:
        if c.reason.startswith("burst"):
            scope = f" across {len(c.repositories)} repositories" if len(c.repositories) > 1 else ""
            lines.append(
                f"Excluded {c.count} near-identical {c.kind} item(s) posted under '{c.author}'{scope} "
                f"that look like service-hook automation."
            )
        else:
            lines.append(
                f"Excluded {c.count} {c.kind} item(s) posted under '{c.author}' that look automated "
                f"({c.label}) — not credited as personal work."
            )
    if lines:
        lines.append(
            "Tune 'automation_markers' or set 'automation_handling' to 'off' "
            "(standup_config_set via the yeaboi MCP server)."
        )
    return lines
