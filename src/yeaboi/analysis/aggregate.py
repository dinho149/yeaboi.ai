"""Deterministic scoring core of the team analysis — the seam the Go sidecar serves.

Two invariants, same as ``standup/aggregate.py``:

1. **The wire shape is the only shape.** ``build_*_inputs`` return plain JSON
   (the ``json.loads(json.dumps(...))`` round trip is deliberate: it freezes the
   inputs into exactly what the RPC would carry, so the Python reference and the
   Go twin score byte-identical documents). ``AiAdoptionSignal`` and
   ``DocQualitySignal`` are the two dataclasses that cross; each travels through
   its ``*_to_wire`` / ``*_from_wire`` pair.
2. **No side effects behind the seam.** Progress reporting stays with the
   callers (``run_ai_adoption`` / ``run_doc_quality``), and nothing here touches
   the network, the clock, or the database. The one LLM call in this mode
   (footprint insights) is started by the caller *between* the two code methods
   — which is exactly why there are two: ``analysis.classify_markers`` must
   return before the change-metadata fetch so the insights thread can overlap
   it, and ``analysis.score_code`` needs the fetched files, so one method could
   not serve both without serializing that overlap. The docs path has no LLM
   anywhere (its insights are deterministic), so ``analysis.score_docs`` is a
   single method.

The Go twin is ``go/internal/analysis/`` (each file names its Python source);
byte parity is enforced by ``tests/parity/``. Result key order is contractual —
these dicts feed ``json.dumps`` downstream.
"""

from __future__ import annotations

import json
import logging

from yeaboi.team_profile import AiAdoptionSignal, DocQualitySignal

logger = logging.getLogger(__name__)

# The keys a sidecar result must carry to be trusted (per method); anything less
# is treated as a failed call rather than scored partially.
_CLASSIFY_KEYS = ("signal", "samples")
_SCORE_KEYS = ("member_activity", "practices", "health", "activity_counts")
_SCORE_DOCS_KEYS = ("assets", "signal", "summary", "findings", "action_plan", "insights")


# ---------------------------------------------------------------------------
# analysis.classify_markers — signal + evidence samples over the activity items
# ---------------------------------------------------------------------------


def build_classify_inputs(*, items: list[dict]) -> dict:
    """Freeze the deduped activity items into the classify-markers wire document."""
    return json.loads(json.dumps({"items": items}))


def classify_markers(inputs: dict) -> dict:
    """Python reference implementation of ``analysis.classify_markers``.

    Wraps the existing pure classifiers unchanged: one pass building the
    :class:`AiAdoptionSignal` and one collecting every AI-marked evidence sample
    (``limit=None`` — the report keeps the complete basis, never a first-N).
    """
    from yeaboi.analysis.ai_usage import _collect_samples, aggregate_ai_markers

    items = inputs["items"]
    return {
        "signal": signal_to_wire(aggregate_ai_markers(items)),
        "samples": _collect_samples(items, limit=None),
    }


def go_classify(inputs: dict) -> dict | None:
    """``analysis.classify_markers`` served by the sidecar, or None → Python computes."""
    client = _client()
    if client is None:
        return None
    from yeaboi.gocore import CoreError

    try:
        result = client.request("analysis.classify_markers", inputs)
    except CoreError as exc:
        logger.warning("gocore: analysis.classify_markers failed (%s) — falling back to Python", exc)
        return None
    if not isinstance(result, dict) or not all(key in result for key in _CLASSIFY_KEYS):
        logger.warning("gocore: analysis.classify_markers result malformed — falling back to Python")
        return None
    logger.info("gocore: analysis.classify_markers served by the sidecar")
    return result


# ---------------------------------------------------------------------------
# analysis.score_code — health + practices + per-member activity
# ---------------------------------------------------------------------------


def build_score_inputs(
    *,
    items: list[dict],
    changed_files: list[dict],
    selected_users: list[str],
    window_days: int,
    health_enabled: bool,
    changed_file_cache_hits: int,
) -> dict:
    """Freeze the score-code wire document.

    ``items`` arrive already annotated with ``changed_file_paths`` (the caller
    owns that collection step); the round trip below copies them, so later
    caller-side mutation cannot leak into what was scored.
    ``changed_file_cache_hits`` rides along because it is stamped into the
    health summaries *before* :func:`coverage_notes` reads them.
    """
    return json.loads(
        json.dumps(
            {
                "items": items,
                "changed_files": changed_files,
                "selected_users": selected_users,
                "window_days": window_days,
                "health_enabled": health_enabled,
                "changed_file_cache_hits": changed_file_cache_hits,
            }
        )
    )


def score_code(inputs: dict) -> dict:
    """Python reference implementation of ``analysis.score_code``.

    Reproduces the deterministic tail of ``run_ai_adoption`` exactly: the
    code-health pipeline (when enabled), the per-member activity tally over the
    deduped items, and the practice-hygiene scoring. Key order below is the
    wire contract.
    """
    from yeaboi.analysis.ai_usage import _classify_ai_item
    from yeaboi.analysis.code_health import analyse_changed_files, changed_file_summary, prioritize_actions
    from yeaboi.analysis.coverage import coverage_notes
    from yeaboi.analysis.practices import member_practices

    items = inputs["items"]
    selected_users = inputs["selected_users"]

    file_reports: list[dict] = []
    health_findings: list[dict] = []
    action_plan: list[dict] = []
    file_coverage: dict = {}
    repository_health: dict = {}
    notes: list[str] = []
    if inputs["health_enabled"]:
        file_reports, health_findings, file_coverage = analyse_changed_files(
            inputs["changed_files"], inputs["window_days"]
        )
        action_plan = prioritize_actions(health_findings)
        repository_health = changed_file_summary(file_reports, health_findings)
        repository_health["cached_change_lookups"] = inputs["changed_file_cache_hits"]
        file_coverage["cached_change_lookups"] = inputs["changed_file_cache_hits"]
        notes = coverage_notes(file_coverage)

    # Per-member activity over the deduped items so the footprint denominator is
    # verifiable at a glance (one member carrying thousands of automated commits
    # is visible instead of hidden in a total).
    member_rows: dict[str, dict] = {
        member: {"member": member, "commits": 0, "prs": 0, "ai_marked": 0} for member in selected_users
    }
    agent_row = {"member": "AI agent accounts", "commits": 0, "prs": 0, "ai_marked": 0}
    for item in items:
        kind = item.get("kind")
        if kind not in ("commit", "pr"):
            continue
        slot = "commits" if kind == "commit" else "prs"
        ai_marked = bool(_classify_ai_item(item))
        targets = [member_rows[m] for m in item.get("matched_members", ()) if m in member_rows]
        if not targets and item.get("agent_authored"):
            targets = [agent_row]
        for row in targets:
            row[slot] += 1
            if ai_marked:
                row["ai_marked"] += 1
    member_activity = sorted(
        (row for row in member_rows.values()),
        key=lambda row: (-(row["commits"] + row["prs"]), row["member"]),
    )
    if agent_row["commits"] or agent_row["prs"]:
        member_activity.append(agent_row)

    practices = member_practices(items, selected_users)

    return {
        "member_activity": member_activity,
        "practices": practices,
        "health": {
            "file_reports": file_reports,
            "findings": health_findings,
            "action_plan": action_plan,
            "file_coverage": file_coverage,
            "repository_health": repository_health,
            "coverage_notes": notes,
        },
        "activity_counts": {
            "commits": sum(item.get("kind") == "commit" for item in items),
            "prs": sum(item.get("kind") == "pr" for item in items),
            "reviews": sum(item.get("kind") == "review" for item in items),
            "comments": sum(item.get("kind") == "comment" for item in items),
        },
    }


def go_score(inputs: dict) -> dict | None:
    """``analysis.score_code`` served by the sidecar, or None → Python computes."""
    client = _client()
    if client is None:
        return None
    from yeaboi.gocore import CoreError

    try:
        result = client.request("analysis.score_code", inputs)
    except CoreError as exc:
        logger.warning("gocore: analysis.score_code failed (%s) — falling back to Python", exc)
        return None
    if not isinstance(result, dict) or not all(key in result for key in _SCORE_KEYS):
        logger.warning("gocore: analysis.score_code result malformed — falling back to Python")
        return None
    logger.info("gocore: analysis.score_code served by the sidecar")
    return result


# ---------------------------------------------------------------------------
# analysis.score_docs — page scoring + aggregation + findings + insights
# ---------------------------------------------------------------------------


def scoreable_doc_pages(pages: list[dict]) -> list[dict]:
    """The pages that produce an asset: a cached asset, or a body to score.

    Shared by the reference implementation, the dispatch validation, and the
    caller's post-seam cache write — all three must agree on which pages map
    to which returned asset, so the filter lives in exactly one place.
    """
    return [page for page in pages if isinstance(page.get("asset"), dict) or str(page.get("text", "")).strip()]


def build_score_docs_inputs(*, pages: list[dict]) -> dict:
    """Freeze the collected doc pages into the score-docs wire document.

    Each page carries either a version-matched cached ``asset`` (passed through
    unscored) or its extracted ``text`` body; bodies cross the wire but never
    appear in the result — the sidecar returns only derived assets, mirroring
    the cache's own never-store-bodies rule.
    """
    return json.loads(json.dumps({"pages": pages}))


def score_docs(inputs: dict) -> dict:
    """Python reference implementation of ``analysis.score_docs``.

    Reproduces the deterministic tail of ``run_doc_quality`` exactly: score
    every fresh page into its asset, aggregate assets into the signal, derive
    findings and the action plan, and build the coaching insights (which are
    deterministic in this mode — no LLM anywhere in the docs path). Key order
    below is the wire contract.
    """
    from yeaboi.analysis.doc_quality import (
        _aggregate_doc_assets,
        _analyse_page_asset,
        _doc_findings,
        _fallback_doc_quality_insights,
        _prioritize_doc_actions,
        doc_small_sample,
    )

    assets = [
        page["asset"] if isinstance(page.get("asset"), dict) else _analyse_page_asset(page)
        for page in scoreable_doc_pages(inputs["pages"])
    ]
    signal = _aggregate_doc_assets(assets)
    findings = _doc_findings(assets)
    return {
        "assets": assets,
        "signal": doc_signal_to_wire(signal),
        "summary": {
            "pages_scanned": signal.pages_scanned,
            "platforms_scanned": list(signal.platforms_scanned),
            "avg_clarity": signal.avg_clarity,
            "avg_usefulness": signal.avg_usefulness,
            "clear_pages": signal.clear_pages,
            "mixed_pages": signal.mixed_pages,
            "unclear_pages": signal.unclear_pages,
            "owned_pages": signal.owned_pages,
            "actionable_pages": signal.actionable_pages,
            "structured_pages": signal.structured_pages,
            "ai_marked_pages": signal.ai_marked_pages,
            "per_platform": [list(pair) for pair in signal.per_platform],
            "flagged_pages": [list(pair) for pair in signal.flagged_pages],
            "is_ai_estimate": False,
            "small_sample": doc_small_sample(signal),
        },
        "findings": findings,
        "action_plan": _prioritize_doc_actions(findings),
        "insights": _fallback_doc_quality_insights(signal, assets),
    }


def go_score_docs(inputs: dict) -> dict | None:
    """``analysis.score_docs`` served by the sidecar, or None → Python computes."""
    client = _client()
    if client is None:
        return None
    from yeaboi.gocore import CoreError

    try:
        result = client.request("analysis.score_docs", inputs)
    except CoreError as exc:
        logger.warning("gocore: analysis.score_docs failed (%s) — falling back to Python", exc)
        return None
    if not isinstance(result, dict) or not all(key in result for key in _SCORE_DOCS_KEYS):
        logger.warning("gocore: analysis.score_docs result malformed — falling back to Python")
        return None
    # The caller zips result assets against the scoreable pages to write the
    # score cache; a count mismatch would mis-key those writes, so it is a
    # malformed result, not a partial one.
    if not isinstance(result["assets"], list) or len(result["assets"]) != len(scoreable_doc_pages(inputs["pages"])):
        logger.warning("gocore: analysis.score_docs asset count mismatch — falling back to Python")
        return None
    logger.info("gocore: analysis.score_docs served by the sidecar")
    return result


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def _client():
    """The discovered sidecar client, or None; never raises (analysis must not sink)."""
    try:
        from yeaboi import gocore

        return gocore.get_client()
    except Exception as exc:  # noqa: BLE001 — dispatch must never sink an analysis run
        logger.warning("gocore: client unavailable (%s: %s) — using the Python path", type(exc).__name__, exc)
        return None


def signal_to_wire(signal: AiAdoptionSignal) -> dict:
    """The signal's classification fields as plain JSON (pair tuples → lists).

    Provenance (``repos_scanned``/``sources_scanned`` labels) is deliberately
    caller-side — ``run_ai_adoption`` overwrites both on the reconstructed
    signal after this returns, so only ``aggregate_ai_markers``'s own fields
    are contractual on the wire.
    """
    return {
        "scanned_commits": signal.scanned_commits,
        "scanned_prs": signal.scanned_prs,
        "ai_commits": signal.ai_commits,
        "ai_prs": signal.ai_prs,
        "footprint_pct": signal.footprint_pct,
        "per_tool": [[tool, count] for tool, count in signal.per_tool],
        "per_author": [[author, count] for author, count in signal.per_author],
        "per_activity": [[bucket, count] for bucket, count in signal.per_activity],
        "per_source": [[source, count] for source, count in signal.per_source],
        "sources_scanned": list(signal.sources_scanned),
        "is_lower_bound": True,
    }


def signal_from_wire(payload: dict) -> AiAdoptionSignal:
    return AiAdoptionSignal(
        scanned_commits=int(payload.get("scanned_commits") or 0),
        scanned_prs=int(payload.get("scanned_prs") or 0),
        ai_commits=int(payload.get("ai_commits") or 0),
        ai_prs=int(payload.get("ai_prs") or 0),
        footprint_pct=float(payload.get("footprint_pct") or 0.0),
        per_tool=tuple((str(name), int(count)) for name, count in payload.get("per_tool") or ()),
        per_author=tuple((str(name), int(count)) for name, count in payload.get("per_author") or ()),
        per_activity=tuple((str(name), int(count)) for name, count in payload.get("per_activity") or ()),
        per_source=tuple((str(name), int(count)) for name, count in payload.get("per_source") or ()),
        sources_scanned=tuple(str(source) for source in payload.get("sources_scanned") or ()),
        is_lower_bound=True,
    )


def doc_signal_to_wire(signal: DocQualitySignal) -> dict:
    """The doc signal as plain JSON, every field explicit in declaration order.

    Unlike :func:`signal_to_wire` there is no caller-side provenance to hold
    back — the round trip is lossless, including the two legacy fields
    (``avg_ai_likelihood``/``likely_ai_pages``) that new analysis leaves at
    zero but old saved profiles may still carry.
    """
    return {
        "pages_scanned": signal.pages_scanned,
        "platforms_scanned": list(signal.platforms_scanned),
        "avg_clarity": signal.avg_clarity,
        "avg_usefulness": signal.avg_usefulness,
        "clear_pages": signal.clear_pages,
        "mixed_pages": signal.mixed_pages,
        "unclear_pages": signal.unclear_pages,
        "owned_pages": signal.owned_pages,
        "actionable_pages": signal.actionable_pages,
        "structured_pages": signal.structured_pages,
        "avg_ai_likelihood": signal.avg_ai_likelihood,
        "likely_ai_pages": signal.likely_ai_pages,
        "ai_marked_pages": signal.ai_marked_pages,
        "per_platform": [[platform, count] for platform, count in signal.per_platform],
        "flagged_pages": [[title, reason] for title, reason in signal.flagged_pages],
        "is_ai_estimate": bool(signal.is_ai_estimate),
    }


def doc_signal_from_wire(payload: dict) -> DocQualitySignal:
    return DocQualitySignal(
        pages_scanned=int(payload.get("pages_scanned") or 0),
        platforms_scanned=tuple(str(platform) for platform in payload.get("platforms_scanned") or ()),
        avg_clarity=float(payload.get("avg_clarity") or 0.0),
        avg_usefulness=float(payload.get("avg_usefulness") or 0.0),
        clear_pages=int(payload.get("clear_pages") or 0),
        mixed_pages=int(payload.get("mixed_pages") or 0),
        unclear_pages=int(payload.get("unclear_pages") or 0),
        owned_pages=int(payload.get("owned_pages") or 0),
        actionable_pages=int(payload.get("actionable_pages") or 0),
        structured_pages=int(payload.get("structured_pages") or 0),
        avg_ai_likelihood=float(payload.get("avg_ai_likelihood") or 0.0),
        likely_ai_pages=int(payload.get("likely_ai_pages") or 0),
        ai_marked_pages=int(payload.get("ai_marked_pages") or 0),
        per_platform=tuple((str(platform), int(count)) for platform, count in payload.get("per_platform") or ()),
        flagged_pages=tuple((str(title), str(reason)) for title, reason in payload.get("flagged_pages") or ()),
        is_ai_estimate=bool(payload.get("is_ai_estimate")),
    )
