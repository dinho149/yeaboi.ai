"""The standup deterministic-aggregation seam — one pure function.

``run_standup`` collects activity and reads session state (network + SQLite),
then hands EVERYTHING the deterministic middle of the pipeline consumes to
``aggregate_standup`` as one JSON-safe ``inputs`` dict: identity closure →
roster filter → automation filter → category coverage → grouping →
day-over-day insights → practice detection → confidence. The result is the
deterministic scaffold of the report — everything except LLM prose.

Two invariants shape this module:

- **The wire shape is the only shape.** ``aggregate_standup`` returns plain
  JSON types (dicts/lists/strs/numbers/bools) — never tuples or dataclasses —
  so the rest of the engine consumes one stable serialized form. The
  ``*_from_wire`` helpers rehydrate dataclasses at the consumption boundary
  instead.
- **The one LLM interleave is hoisted out by protocol, not by code.** Practice
  adjudication (habits._adjudicate) runs INSIDE detection, so the RPC is
  idempotent and two-pass: pass 1 returns ``adjudication_cases``; the engine
  runs the (Python, LLM) adjudicator on them; when it drops any, pass 2 repeats
  the identical inputs plus ``dropped_case_ids`` and returns no cases. Case ids
  are deterministic functions of the inputs, so the second pass rebuilds the
  same cases and applies the drops.

# See docs: "Daily Standup" — engine
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping

from yeaboi.agent.state import ActivityEvidence, MemberUpdate, PracticeSignal, StandupReport
from yeaboi.standup import categories, collector, confidence, habits, insights
from yeaboi.timeparse import parse_date

logger = logging.getLogger(__name__)

# The only config keys the deterministic layer reads. Everything else the
# standup config holds (channels, scopes, aliases…) is resolved in Python
# BEFORE the aggregate call and travels as its own param.
_CONFIG_KEYS = ("automation_handling", "automation_markers", "habit_detection", "habit_rules")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def build_aggregate_inputs(
    *,
    bundle: collector.ActivityBundle,
    members: list[str],
    my_name: str,
    my_aliases: str,
    repo_path: str,
    tracker_identities: Collection[str],
    self_reported_names: Collection[str],
    config: Mapping | None,
    previous_report: StandupReport | None,
    transcript_corrections: Mapping[str, list[str]] | None,
    corrections: Collection[object],
    feedback_excused: Collection[tuple[str, str]],
    enabled_sources: Collection[str],
    sprint,
    history: Collection[Mapping],
    today: str,
    want_adjudication: bool,
) -> dict:
    """Assemble the JSON-safe ``standup.aggregate`` params from run_standup scope.

    Everything DB- or network-derived is read HERE (it already was — nothing new
    is fetched) and travels as data: the sidecar stays DB-free for standup
    because every standup table is report-history state, which contract rule 5
    keeps Python-only. This dict is also the parity-fixture format.

    ``sprint`` is the ``sprint_context.SprintContext``; capacity is zeroed when
    no live committed points were found (``have_burn``), exactly as the old
    inline ``confidence.compute`` call did. ``corrections`` (the previous run's
    edit log) is pre-parsed to ``corrected_fields`` because parsing needs
    ``yeaboi.artifacts.paths`` — a Python concern the wire never carries.
    """
    from yeaboi.standup import engine

    # The standup user's alias extras, hoisted out of _build_alias_map so both
    # backends receive identical identity inputs (the git lookup is a
    # subprocess — Python-only by nature).
    identity_extras = [alias.strip() for alias in (my_aliases or "").split(",") if alias.strip()]
    identity_extras += engine._detect_git_identity(repo_path)
    identity_extras += [str(x) for x in tracker_identities if x]
    identity_extras.append("Me")  # legacy self-reports/config still match

    cfg: dict[str, str] = {}
    for key in _CONFIG_KEYS:
        value = (config or {}).get(key)
        if value is not None:
            cfg[key] = str(value)

    return {
        "bundle": {
            "items": [dict(item) for item in bundle.items],
            "counts": [[str(s), int(n)] for s, n in bundle.counts],
            "errors": [[str(s), str(m)] for s, m in bundle.errors],
            "partial_sources": [[str(s), str(m)] for s, m in bundle.partial_sources],
            "skipped": [[str(s), str(m)] for s, m in bundle.skipped],
            "reference_tickets": [dict(item) for item in bundle.reference_tickets],
        },
        "members": [str(m) for m in members],
        "my_name": str(my_name),
        "identity_extras": identity_extras,
        "self_reported_names": [str(n) for n in self_reported_names],
        "config": cfg,
        "previous_report": _previous_report_to_wire(previous_report),
        "transcript_corrections": {str(k): [str(x) for x in v] for k, v in (transcript_corrections or {}).items()},
        "corrected_fields": {k: list(v) for k, v in insights.corrected_members(corrections).items()},
        "feedback_excused": sorted([str(rule), str(handle)] for rule, handle in feedback_excused),
        "enabled_sources": sorted(str(s) for s in enabled_sources),
        "sprint": {
            "sprint_name": str(sprint.sprint_name),
            "start_date": str(sprint.start_date),
            "sprint_length_weeks": int(sprint.sprint_length_weeks),
            "capacity_points": float(sprint.capacity_points) if sprint.have_burn else 0.0,
            "completed_points": float(sprint.completed_points),
        },
        "history": [
            {
                "status": row.get("status"),
                "standup_date": row.get("standup_date"),
                "confidence_pct": row.get("confidence_pct"),
            }
            for row in history
        ],
        "today": str(today),
        "want_adjudication": bool(want_adjudication),
    }


# ---------------------------------------------------------------------------
# The reference implementation (Python backend)
# ---------------------------------------------------------------------------


def aggregate_standup(inputs: dict) -> dict:
    """The deterministic middle of the standup pipeline, as one pure function.

    It calls the same engine/insights/habits/confidence helpers the inline
    block always called — only the shapes at the boundary changed.
    """
    from yeaboi.standup import engine

    b = inputs.get("bundle") or {}
    bundle = collector.ActivityBundle(
        items=[dict(item) for item in b.get("items") or ()],
        counts=[(str(s), int(n)) for s, n in b.get("counts") or ()],
        errors=[(str(s), str(m)) for s, m in b.get("errors") or ()],
        partial_sources=[(str(s), str(m)) for s, m in b.get("partial_sources") or ()],
        skipped=[(str(s), str(m)) for s, m in b.get("skipped") or ()],
        reference_tickets=[dict(item) for item in b.get("reference_tickets") or ()],
    )
    members = [str(m) for m in inputs.get("members") or ()]
    my_name = str(inputs.get("my_name") or "")
    config = inputs.get("config") or {}

    # Identity closure: every member's own name, the user's hoisted extras,
    # then the emails observed on activity items (two-pass closure).
    alias_map = engine._build_alias_map(
        members,
        my_name=my_name,
        extra_identities=tuple(str(x) for x in inputs.get("identity_extras") or ()),
    )
    engine._enrich_aliases_from_items(alias_map, bundle.items)
    # Roster entries that are the standup user under another name — one person,
    # one card. Reported as ``merged`` so the engine can keep its log line.
    my_alias_set = alias_map.get(my_name, set())
    merged = [m for m in members if m != my_name and engine._normalize_author(m) & my_alias_set]
    for dupe in merged:
        members.remove(dupe)
        alias_map.pop(dupe, None)
    bundle = engine._filter_bundle_to_members(bundle, alias_map)
    bundle, automation_notices = engine._drop_automated_activity(bundle, config)
    category_coverage = categories.coverage_states(set(inputs.get("enabled_sources") or ()), bundle)

    previous_report = _previous_report_from_wire(inputs.get("previous_report"))
    grouped = engine._group_activity_by_author(bundle.items, members, alias_map)
    blocker_signals = insights.detect_blocker_signals(grouped, previous_report=previous_report)
    yesterday = insights.yesterday_context(
        previous_report,
        {str(k): [str(x) for x in v] for k, v in (inputs.get("transcript_corrections") or {}).items()},
        corrected_fields=inputs.get("corrected_fields") or {},
    )

    # Practice detection, with the adjudicator seam replaced by the two-pass
    # protocol: a stub that CAPTURES the cases and returns the (possibly empty)
    # drop list from the params. habits._adjudicate still owns the intersection
    # with sent ids and the drop application, so a junk id costs nothing.
    reference_grouped = engine._group_activity_by_author(bundle.reference_tickets, members, alias_map)
    reference_items = [engine._projected_item(item) for item in bundle.reference_tickets]
    excused = frozenset((str(rule), str(handle)) for rule, handle in inputs.get("feedback_excused") or ())
    dropped_case_ids = [str(case_id) for case_id in inputs.get("dropped_case_ids") or ()]
    captured: list[habits.AdjudicationCase] = []
    adjudicator = None
    if bool(inputs.get("want_adjudication")) or dropped_case_ids:

        def adjudicator(cases):
            captured.extend(cases)
            return list(dropped_case_ids)

    practices = habits.detect_practices(
        grouped,
        config=config,
        category_coverage=category_coverage,
        previous_report=previous_report,
        reference_grouped=reference_grouped,
        reference_items=reference_items,
        adjudicator=adjudicator,
        feedback=lambda rule, handle: (rule, handle) in excused,
    )

    sprint = inputs.get("sprint") or {}
    progress = confidence.compute(
        sprint_name=str(sprint.get("sprint_name") or ""),
        start_date=str(sprint.get("start_date") or ""),
        sprint_length_weeks=int(sprint.get("sprint_length_weeks", 2)),
        capacity_points=float(sprint.get("capacity_points", 0.0)),
        completed_points=float(sprint.get("completed_points", 0.0)),
        activity_count=bundle.total(exclude_kinds=("wip",)),
        today=parse_date(str(inputs["today"])),
        history=list(inputs.get("history") or ()),
    )

    coverage_map = dict(category_coverage)
    return {
        "members": members,
        "merged": merged,
        "counts": [[str(s), int(n)] for s, n in bundle.counts],
        "total_items": len(bundle.items),
        "automation_notices": list(automation_notices),
        "category_coverage": [[str(c), str(s)] for c, s in category_coverage],
        "grouped": {name: [_item_to_wire(item) for item in acts] for name, acts in grouped.items()},
        "blocker_signals": {name: list(signals) for name, signals in blocker_signals.items()},
        "yesterday": yesterday,
        "practices": {name: [_signal_to_wire(s) for s in signals] for name, signals in practices.items()},
        "progress": {
            "sprint_day": progress.sprint_day,
            "sprint_total_days": progress.sprint_total_days,
            "confidence_pct": progress.confidence_pct,
            "confidence_label": progress.confidence_label,
            "confidence_rationale": progress.confidence_rationale,
            "confidence_delta": progress.confidence_delta,
            "confidence_trend": progress.confidence_trend,
        },
        "member_skeletons": _member_skeletons(
            grouped,
            coverage=coverage_map,
            yesterday=yesterday,
            self_reported_names=set(inputs.get("self_reported_names") or ()),
        ),
        "fallback_team_summary": engine._build_fallback_team_summary(bundle, progress),
        # Pass 2 (or adjudication off) returns no cases — the engine's re-invoke
        # is structurally single-shot, but an empty list makes it airtight.
        "adjudication_cases": [] if dropped_case_ids else [_case_to_wire(c) for c in captured],
    }


def _member_skeletons(
    grouped: Mapping[str, list[dict]],
    *,
    coverage: Mapping[str, str],
    yesterday: Mapping[str, dict],
    self_reported_names: Collection[str],
) -> list[dict]:
    """The deterministic (non-prose) half of every MemberUpdate, in member order.

    ``engine._updates_from_result`` overlays LLM prose (or the fallback
    strings already carried here) on these to build the real dataclasses —
    both the LLM and no-LLM paths assemble from the same skeletons, so the
    evidence/links/counts a report shows can never depend on which path ran.
    """
    from yeaboi.standup import engine

    prefixes, work_item_ids = engine._reference_gates(grouped)
    skeletons: list[dict] = []
    for name, acts in grouped.items():
        split = categories.split_activity(acts)

        def category_block(category: str, evidence_acts: list[dict]) -> dict:
            return {
                "summary": engine._fallback_category_summary(
                    category, split[category], coverage.get(category, categories.COVERED)
                ),
                "links": [[label, url] for label, url in engine._member_links(split[category])],
                "count": len(split[category]),
                "evidence": [
                    _evidence_to_wire(row)
                    for row in engine._member_evidence(evidence_acts, prefixes=prefixes, work_item_ids=work_item_ids)
                ],
            }

        skeletons.append(
            {
                "name": name,
                "source": engine._member_source(name in self_reported_names, bool(acts)),
                "links": [[label, url] for label, url in engine._member_links(acts)],
                "activity_count": len(acts),
                "fallback_summary": engine._fallback_summary(acts),
                "fallback_progress_note": engine._fallback_progress_note(yesterday.get(name, {}), acts),
                "fallback_outlook": engine._fallback_outlook(acts),
                "ticketing": category_block(categories.CATEGORY_TICKETING, split[categories.CATEGORY_TICKETING]),
                # Commits fold under their PR for evidence only — links/counts
                # keep the flat view, exactly as the inline builders always did.
                "code": category_block(
                    categories.CATEGORY_CODE, engine._nest_pr_commits(split[categories.CATEGORY_CODE])
                ),
                "documentation": category_block(
                    categories.CATEGORY_DOCUMENTATION, split[categories.CATEGORY_DOCUMENTATION]
                ),
            }
        )
    return skeletons


# ---------------------------------------------------------------------------
# Wire ↔ dataclass converters
# ---------------------------------------------------------------------------


def _item_to_wire(item: dict) -> dict:
    """A projected activity item with its tuple fields as JSON lists."""
    out = dict(item)
    out["changed_paths"] = [str(p) for p in item.get("changed_paths") or ()]
    out["work_item_ids"] = [str(w) for w in item.get("work_item_ids") or ()]
    return out


def _evidence_to_wire(row: ActivityEvidence) -> dict:
    return {
        "kind": row.kind,
        "key": row.key,
        "title": row.title,
        "url": row.url,
        "repository": row.repository,
        "status": row.status,
        "timestamp": row.timestamp,
        "children": [_evidence_to_wire(child) for child in row.children],
        "issue_type": row.issue_type,
        "parent_key": row.parent_key,
        "subtask": bool(row.subtask),
        "ticket_keys": list(row.ticket_keys),
    }


def evidence_from_wire(payload: Mapping) -> ActivityEvidence:
    return ActivityEvidence(
        kind=str(payload.get("kind") or ""),
        key=str(payload.get("key") or ""),
        title=str(payload.get("title") or ""),
        url=str(payload.get("url") or ""),
        repository=str(payload.get("repository") or ""),
        status=str(payload.get("status") or ""),
        timestamp=str(payload.get("timestamp") or ""),
        children=tuple(evidence_from_wire(child) for child in payload.get("children") or ()),
        issue_type=str(payload.get("issue_type") or ""),
        parent_key=str(payload.get("parent_key") or ""),
        subtask=bool(payload.get("subtask")),
        ticket_keys=tuple(str(k) for k in payload.get("ticket_keys") or ()),
    )


def _signal_to_wire(signal: PracticeSignal) -> dict:
    return {
        "rule": signal.rule,
        "title": signal.title,
        "detail": signal.detail,
        "evidence": [[label, url] for label, url in signal.evidence],
        "repeat": bool(signal.repeat),
        "handles": list(signal.handles),
    }


def practices_from_wire(payload: Mapping[str, list]) -> dict[str, tuple[PracticeSignal, ...]]:
    return {
        str(name): tuple(
            PracticeSignal(
                rule=str(s.get("rule") or ""),
                title=str(s.get("title") or ""),
                detail=str(s.get("detail") or ""),
                evidence=tuple((str(label), str(url)) for label, url in s.get("evidence") or ()),
                repeat=bool(s.get("repeat")),
                handles=tuple(str(h) for h in s.get("handles") or ()),
            )
            for s in signals
        )
        for name, signals in (payload or {}).items()
    }


def progress_from_wire(payload: Mapping) -> confidence.SprintProgress:
    return confidence.SprintProgress(
        sprint_day=int(payload.get("sprint_day") or 0),
        sprint_total_days=int(payload.get("sprint_total_days") or 0),
        confidence_pct=int(payload.get("confidence_pct") or 0),
        confidence_label=str(payload.get("confidence_label") or ""),
        confidence_rationale=str(payload.get("confidence_rationale") or ""),
        confidence_delta=int(payload.get("confidence_delta") or 0),
        confidence_trend=str(payload.get("confidence_trend") or ""),
    )


def _case_to_wire(case: habits.AdjudicationCase) -> dict:
    return {
        "case_id": case.case_id,
        "subject": case.subject,
        "branch": case.branch,
        "paths": list(case.paths),
        "candidates": [[key, title, text] for key, title, text in case.candidates],
    }


def cases_from_wire(payload: Collection[Mapping]) -> tuple[habits.AdjudicationCase, ...]:
    return tuple(
        habits.AdjudicationCase(
            case_id=str(c.get("case_id") or ""),
            subject=str(c.get("subject") or ""),
            branch=str(c.get("branch") or ""),
            paths=tuple(str(p) for p in c.get("paths") or ()),
            candidates=tuple((str(k), str(t), str(x)) for k, t, x in c.get("candidates") or ()),
        )
        for c in payload
    )


def _previous_report_to_wire(previous_report: StandupReport | None) -> dict | None:
    """The narrow projection of yesterday's report the deterministic layer reads.

    Exactly three consumers, all read-only: ``insights._previous_pr_urls``
    (links/code_links), ``insights.yesterday_context`` (summary/blockers/
    outlook), and ``habits._previous_signal_rules`` (practices[].rule).
    """
    if previous_report is None:
        return None
    return {
        "member_updates": [
            {
                "name": m.name,
                "summary": m.summary,
                "blockers": m.blockers,
                "outlook": getattr(m, "outlook", ""),
                "links": [[label, url] for label, url in m.links],
                "code_links": [[label, url] for label, url in m.code_links],
                "practices": [{"rule": s.rule} for s in (getattr(m, "practices", ()) or ()) if getattr(s, "rule", "")],
            }
            for m in previous_report.member_updates
        ]
    }


def _previous_report_from_wire(payload: Mapping | None) -> StandupReport | None:
    if not payload:
        return None
    return StandupReport(
        member_updates=tuple(
            MemberUpdate(
                name=str(m.get("name") or ""),
                summary=str(m.get("summary") or ""),
                blockers=str(m.get("blockers") or ""),
                outlook=str(m.get("outlook") or ""),
                links=tuple((str(label), str(url)) for label, url in m.get("links") or ()),
                code_links=tuple((str(label), str(url)) for label, url in m.get("code_links") or ()),
                practices=tuple(PracticeSignal(rule=str(p.get("rule") or "")) for p in m.get("practices") or ()),
            )
            for m in payload.get("member_updates") or ()
        )
    )
