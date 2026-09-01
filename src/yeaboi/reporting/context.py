"""Gather supporting code/docs signals for a delivery report's period.

Deterministic (no LLM): reuses the standup collector for merged PRs / commits and
the analysis doc reader for recently-changed pages, then reduces both to bounded
``SupportingSignal`` rows — counts plus a few sample titles, never bodies. These
signals corroborate the delivered-ticket story ("backed by 24 merged PRs and 5
doc updates"); they are reference context, never the report's subject.

Every fetch is best-effort: a failing source becomes a warning on the report,
never a crash. Tool helpers are imported lazily (optional SDKs), same convention
as reporting/activity.py.

# See docs: "Daily Standup" — recent-activity collection (the code fetcher)
# See docs: "Reporting Mode" — supporting signals
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time

from yeaboi.agent.state import SupportingSignal
from yeaboi.analysis.progress import is_component_progress
from yeaboi.reporting.activity import DELIVERY_AZDO, _emit
from yeaboi.timeparse import parse_date

logger = logging.getLogger(__name__)

# How many sample titles each signal keeps (reference context stays bounded).
_SAMPLE_CAPS = {"pull_requests": 5, "commits": 3, "doc_updates": 6}
_TITLE_MAX = 120

# Collector item kind → signal kind (reviews etc. are ignored — corroboration
# only needs shipped code and updated docs).
_CODE_KINDS = {"pr": "pull_requests", "commit": "commits"}


SIGNAL_KIND_LABELS = {"pull_requests": "Pull requests", "commits": "Commits", "doc_updates": "Doc updates"}
SIGNAL_SOURCE_LABELS = {
    "github": "GitHub",
    "azuredevops": "Azure DevOps",
    "confluence": "Confluence",
    "notion": "Notion",
}


def signals_sentence(signals) -> str:
    """One corroboration sentence for renderers ('' when there is nothing to say).

    e.g. "Corroborated by 24 merged PRs, 87 commits and 5 doc updates" — shared by
    the CLI renderer, the Markdown export, and the deck/pptx metrics slide so every
    surface phrases the reference context identically.
    """
    prs = sum(s.count for s in signals if s.kind == "pull_requests")
    commits = sum(s.count for s in signals if s.kind == "commits")
    docs = sum(s.count for s in signals if s.kind == "doc_updates")
    parts = [
        f"{n} {label}{'s' if n != 1 else ''}"
        for n, label in ((prs, "merged PR"), (commits, "commit"), (docs, "doc update"))
        if n
    ]
    if not parts:
        return ""
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Corroborated by {joined}"


#: An ops kind → the noun a business reader recognises. Built from the closed
#: vocabulary rather than a fixed three, so a new kind cannot fall out of the
#: sentence while still printing in the export.
OPS_KIND_LABELS = {
    "incident": "incident",
    "alert": "alert",
    "error_spike": "error spike",
    "deploy": "deploy",
    "spend_change": "spend change",
}


#: Production's mark on every surface. Fixed rather than an ``emoji_theme``
#: slot: the model chooses emoji for the sections it writes, and it never sees
#: this one.
OPS_EMOJI = "🚨"


def ops_sentence(signals) -> str:
    """One production sentence for renderers ('' when there is nothing to say).

    Deliberately not "corroborated by": an incident *qualifies* delivery rather
    than supporting it, and the two must never be joined into one claim. The
    period is not restated here — every caller has already printed it.
    """
    from yeaboi.ops.events import EVENT_KINDS

    totals = {kind: sum(s.count for s in signals if s.kind == kind) for kind in EVENT_KINDS}
    parts = [f"{n} {OPS_KIND_LABELS[kind]}{'s' if n != 1 else ''}" for kind, n in totals.items() if n]
    if not parts:
        return ""
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Production saw {joined} over the same period"


def gather_ops_signals(
    *,
    period_start: str,
    period_end: str,
    on_progress=None,
) -> tuple[tuple, list[str]]:
    """Return ``(ops_signals, warnings)`` for the period — never raises.

    Reads the *report's own* window rather than a lookback from now: a report on
    a sprint that ended a fortnight ago must not count last night's incident.
    Nothing connected costs one walk of the descriptors and no network, and
    returns nothing, so a reader who has never heard of PagerDuty sees no trace
    of it anywhere in the report.
    """
    from datetime import timezone

    from yeaboi.connectors import registry

    if not registry.any_fetchable():
        return (), []

    from yeaboi.connectors.fetching import gather

    start = datetime.combine(parse_date(period_start), time.min, tzinfo=timezone.utc)
    end = datetime.combine(parse_date(period_end), time.max, tzinfo=timezone.utc)
    _emit(on_progress, "Reading production…")
    try:
        result = gather(window=(start, end))
    except Exception as e:  # noqa: BLE001 — production is reference context
        logger.warning("reporting context: ops gather failed: %s", e, exc_info=True)
        return (), [f"Production context unavailable — {e}"]

    warnings = [f"{s.label}: {s.error}" for s in result.failures if s.error]
    if result.signals:
        total = sum(s.count for s in result.signals)
        _emit(on_progress, f"Production: {total} event(s) across {len(result.signals)} signal(s)")
    logger.info("gather_ops_signals: %d signal(s), %d warning(s)", len(result.signals), len(warnings))
    return result.signals, warnings


class _ProgressProxy(list):
    """List-shaped adapter so doc discovery's ``progress.append`` reaches on_progress.

    The doc collector reports structured ``analysis_component`` lifecycle events
    (dicts) built for the analysis TUI's in-place component rows. Reporting's
    progress screen is an append-only line list, so events are flattened to their
    human ``detail`` text — never the raw dict repr — and consecutive counter
    ticks ("Reading documentation: 2/22", "3/22", …) are collapsed to one line
    via a digit-blind signature instead of spamming a row per page.
    """

    def __init__(self, on_progress) -> None:
        super().__init__()
        self._on_progress = on_progress
        self._last_signatures: dict[str, str] = {}

    def append(self, message) -> None:  # noqa: D102 — list override
        super().append(message)
        if is_component_progress(message):
            text = str(message.get("detail") or "").strip() or str(message["label"])
            # Digit-blind signature: a tick whose only change is a number is a repeat.
            blurred = re.sub(r"\d+", "#", text)
            signature = f"{message['status']}|{blurred}"
            component = str(message["component_id"])
            if self._last_signatures.get(component) == signature:
                return
            self._last_signatures[component] = signature
        else:
            text = str(message)
        _emit(self._on_progress, text)


def _truncate(title: str) -> str:
    title = (title or "").strip()
    return title if len(title) <= _TITLE_MAX else title[: _TITLE_MAX - 1] + "…"


def _sample(item_title: str, ref: str) -> str:
    """One bounded "Title (ref)" sample string."""
    title = _truncate(item_title) or "(untitled)"
    ref = (ref or "").strip()
    return f"{title} ({ref})" if ref else title


def _within_period(timestamp: str, period_start: str, period_end: str) -> bool:
    """Best-effort date clamp — the fetchers are lookback-only, so a window that
    ends in the past needs items after ``period_end`` dropped. Undated items are
    kept (reference-only fuzz beats silently losing real activity)."""
    ts = (timestamp or "").strip()
    if not ts:
        return True
    day = ts[:10]  # ISO date prefix of an ISO datetime
    try:
        parse_date(day)
    except ValueError:
        return True
    return (not period_start or day >= period_start) and (not period_end or day <= period_end)


def _code_signals(
    code_sources: list[str],
    *,
    period_start: str,
    period_end: str,
    azdo_project: str,
    db_path,
    on_progress,
) -> tuple[list[SupportingSignal], list[str]]:
    """Merged PRs / commits over the period via the standup collector."""
    from yeaboi.standup import collector

    enabled: set[str] = set()
    if "github" in code_sources:
        enabled.add(collector.SOURCE_GITHUB)
    if DELIVERY_AZDO in code_sources:
        enabled.add(collector.SOURCE_AZDO_REPOS)
    if not enabled:
        return [], []

    github_repo = ""
    try:
        from yeaboi.config import get_standup_github_repo

        github_repo = get_standup_github_repo() or ""
    except Exception:  # noqa: BLE001 — config probing is best-effort
        logger.debug("reporting context: github repo config probe failed", exc_info=True)
    if not azdo_project:
        try:
            from yeaboi.config import get_azure_devops_project

            azdo_project = get_azure_devops_project() or ""
        except Exception:  # noqa: BLE001
            logger.debug("reporting context: azdo project config probe failed", exc_info=True)

    try:
        since = datetime.combine(parse_date(period_start), time.min).astimezone()
    except (TypeError, ValueError):
        since = None

    _emit(on_progress, "Gathering supporting code activity (merged PRs & commits)…")
    bundle = collector.collect_recent_activity(
        since=since,
        days=30 if since is None else 1,
        sources=enabled,
        azdo_project=azdo_project,
        github_repo=github_repo,
        on_progress=on_progress,
        cache_db_path=db_path,
    )

    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in bundle.items:
        kind = _CODE_KINDS.get(str(item.get("kind", "")))
        if kind is None:
            continue
        # Every surface phrases the PR count as "merged PRs" — hold it to that:
        # the collector fetches all states (open/active/closed included).
        if kind == "pull_requests" and str(item.get("status", "")).lower() != "merged":
            continue
        if not _within_period(str(item.get("timestamp", "")), period_start, period_end):
            continue
        # Collector tags azdo_repos / github; canonicalize to the reporting vocabulary.
        source = DELIVERY_AZDO if item.get("source") == collector.SOURCE_AZDO_REPOS else "github"
        grouped.setdefault((kind, source), []).append(item)

    signals = [
        SupportingSignal(
            kind=kind,
            source=source,
            count=len(items),
            samples=tuple(_sample(str(i.get("title", "")), str(i.get("key", ""))) for i in items[: _SAMPLE_CAPS[kind]]),
        )
        # PRs first — they corroborate delivery better than raw commits.
        for (kind, source), items in sorted(grouped.items(), key=lambda kv: (kv[0][0] != "pull_requests", kv[0]))
    ]
    warnings = [f"Code context from {src} unavailable — {msg}" for src, msg in bundle.errors]
    return signals, warnings


def _doc_signals(
    doc_sources: list[str],
    *,
    period_start: str,
    period_end: str,
    db_path,
    on_progress,
) -> tuple[list[SupportingSignal], list[str]]:
    """Recently-updated doc pages over the period via the analysis doc reader."""
    if not doc_sources:
        return [], []
    from yeaboi.analysis.doc_quality import collect_doc_pages

    try:
        window_days = max(1, (date.today() - parse_date(period_start)).days)
    except (TypeError, ValueError):
        window_days = 30

    _emit(on_progress, "Reading recent doc updates (Confluence / Notion)…")
    pages, _platforms, coverage_notes = collect_doc_pages(
        "",
        "",
        sub_sources=list(doc_sources),
        window_days=window_days,
        progress=_ProgressProxy(on_progress),
        db_path=db_path,
    )

    grouped: dict[str, list[dict]] = {}
    for page in pages:
        if not _within_period(str(page.get("timestamp", "")), period_start, period_end):
            continue
        platform = str(page.get("platform", "")) or "docs"
        grouped.setdefault(platform, []).append(page)

    signals = [
        SupportingSignal(
            kind="doc_updates",
            source=platform,
            count=len(items),
            samples=tuple(_sample(str(p.get("title", "")), "") for p in items[: _SAMPLE_CAPS["doc_updates"]]),
        )
        for platform, items in sorted(grouped.items())
    ]
    warnings = [f"Docs context: {note}" for note in coverage_notes]
    return signals, warnings


def gather_supporting_signals(
    *,
    period_start: str,
    period_end: str,
    code_sources: list[str] | None = None,
    doc_sources: list[str] | None = None,
    azdo_project: str = "",
    db_path=None,
    on_progress=None,
) -> tuple[tuple[SupportingSignal, ...], list[str]]:
    """Return ``(signals, warnings)`` for the period — best-effort, never raises.

    Args:
        period_start / period_end: ISO dates bounding the reporting window.
        code_sources: canonical code hosts to consult ("github" / "azuredevops");
            empty/None skips the code fetch entirely.
        doc_sources: doc platforms to consult ("confluence" / "notion");
            empty/None skips the docs fetch entirely.
        azdo_project: Azure DevOps project override (resolved from config if unset).
        db_path: metadata-cache seam (standup collector cache).
        on_progress: optional callable(str) for live status lines.
    """
    signals: list[SupportingSignal] = []
    warnings: list[str] = []
    try:
        code, code_warnings = _code_signals(
            list(code_sources or ()),
            period_start=period_start,
            period_end=period_end,
            azdo_project=azdo_project,
            db_path=db_path,
            on_progress=on_progress,
        )
        signals += code
        warnings += code_warnings
    except Exception as e:  # noqa: BLE001 — signals are reference context
        logger.warning("reporting context: code signal gather failed: %s", e, exc_info=True)
        warnings.append(f"Supporting code context unavailable — {e}")
    try:
        docs, doc_warnings = _doc_signals(
            list(doc_sources or ()),
            period_start=period_start,
            period_end=period_end,
            db_path=db_path,
            on_progress=on_progress,
        )
        signals += docs
        warnings += doc_warnings
    except Exception as e:  # noqa: BLE001
        logger.warning("reporting context: doc signal gather failed: %s", e, exc_info=True)
        warnings.append(f"Supporting docs context unavailable — {e}")

    if signals:
        prs = sum(s.count for s in signals if s.kind == "pull_requests")
        commits = sum(s.count for s in signals if s.kind == "commits")
        docs_n = sum(s.count for s in signals if s.kind == "doc_updates")
        _emit(on_progress, f"Supporting signals: {prs} PR(s) · {commits} commit(s) · {docs_n} doc update(s)")
    logger.info("gather_supporting_signals: %d signal(s), %d warning(s)", len(signals), len(warnings))
    return tuple(signals), warnings
