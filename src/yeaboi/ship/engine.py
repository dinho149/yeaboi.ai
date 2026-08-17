"""Ship engine — one supervised story → PR run, as a standalone pipeline.

NOT a LangGraph node: like every mode engine this is a plain function the TUI,
CLI and MCP adapt thinly (# See docs: "Architecture" — four layers). There is
no LLM call anywhere in *this* process — the model runs inside the spawned
Claude Code subprocess; yeaboi's job is the deterministic frame around it:
budget, isolation, evidence bridges, validation, the human gate, and the PR.

Engine conventions (# See docs: "Agentic Blueprint Reference"):
- never raises — every failure is a ``ShipRun(status="failed")`` with the
  reason in ``warnings``;
- ``on_progress`` receives ``analysis_component`` lifecycle events so every
  existing progress screen renders it unchanged;
- ``cancel_event`` is polled between units of work, never mid-subprocess-kill.

The gate wait is a poll of the store, not an in-memory flag: resolution
arrives via ``ShipStore.resolve_gate`` from whichever surface the approver
used (TUI screen, CLI prompt — one seam, the practice-feedback pattern), and
the database CAS is the arbiter, so two surfaces racing resolve exactly once.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.agent.state import ShipPhase, ShipRun, ShipValidation
from yeaboi.analysis.progress import send_component_progress
from yeaboi.ship import budget, costing, pipeline, worktree
from yeaboi.ship.driver import ClaudeCodeDriver, DriverResult
from yeaboi.ship.store import ShipStore

logger = logging.getLogger(__name__)

MAX_REJECTION_ATTEMPTS = 3  # after this many gate rejections the run is terminal
_GATE_POLL_S = 1.0
_HEARTBEAT_S = 300.0  # budget-permit refresh cadence; well under budget.ACTIVE_STALE_S


class _RunAbortError(Exception):
    """Internal control flow: carries the terminal artifact out of a phase."""

    def __init__(self, run: ShipRun) -> None:
        super().__init__(run.status)
        self.run = run


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _report(on_progress, component_id: str, label: str, status: str, **kwargs) -> None:
    try:
        send_component_progress(on_progress, component_id=component_id, label=label, status=status, **kwargs)
    except Exception:  # a progress consumer must never kill the run
        logger.debug("progress callback failed", exc_info=True)


def _new_run_id(story_id: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in story_id.lower()).strip("-.")[:40]
    return f"{safe or 'story'}-{stamp}"


def _failed(run: ShipRun, reason: str, *, phase: str = "") -> ShipRun:
    phases = run.phases
    if phase:
        phases = (*phases, ShipPhase(name=phase, status="failed", detail=reason[:300]))
    return replace(run, status="failed", phases=phases, warnings=(*run.warnings, reason), updated_at=_now_iso())


def _load_story(session_id: str, story_id: str, db_path: Path | None):
    """(story, tasks, resolved_session_id) — raises ValueError with a plain reason."""
    from yeaboi.paths import get_db_path
    from yeaboi.sessions import SessionStore

    with SessionStore(db_path or get_db_path()) as sessions:
        resolved = session_id or sessions.get_latest_session_id()
        if not resolved:
            raise ValueError("no saved planning sessions — generate a plan first")
        state = sessions.load_state(resolved)
    if state is None:
        raise ValueError(f"session {resolved} has no saved state")
    story, tasks = pipeline.find_story(state, story_id)
    return story, tasks, resolved


def _dry_run_artifact(story_id: str, repo: str) -> ShipRun:
    """A canned, fully-shaped run: no subprocess, no git, no network."""
    phases = tuple(
        ShipPhase(name=name, status="completed", detail="dry run")
        for name in ("setup", "implement", "validate", "gate", "finalize")
    )
    return ShipRun(
        run_id="dry-run",
        story_id=story_id,
        repo=repo,
        branch="ship/dry-run",
        status="approved",
        phases=phases,
        validation=ShipValidation(configured=True, command="(dry run)", passed=True, exit_code=0),
        diff_stat="(dry run — nothing was executed)",
        gate_resolution="approved",
        created_at=_now_iso(),
        updated_at=_now_iso(),
        warnings=("dry run — no agent was launched and nothing was written",),
    )


def _save(store: ShipStore, run: ShipRun, *, expect_status: str | None = None) -> ShipRun:
    """Persist a transition; on a lost CAS adopt the stored truth."""
    if store.save_run(run, expect_status=expect_status):
        return run
    stored = store.get_run(run.run_id)
    logger.warning("Lost a status race for %s; adopting stored state %s", run.run_id, getattr(stored, "status", "?"))
    return stored or run


def _await_gate(
    store: ShipStore,
    run: ShipRun,
    *,
    cancel_event: threading.Event | None,
) -> ShipRun:
    """Poll until the gate is resolved or the run is cancelled."""
    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = replace(run, status="cancelled", updated_at=_now_iso())
            return _save(store, cancelled, expect_status="awaiting_approval")
        current = store.get_run(run.run_id) or run
        if current.gate_resolution:
            return current
        time.sleep(_GATE_POLL_S)


def run_ship(
    story_id: str,
    repo: str,
    *,
    session_id: str = "",
    check_command: str = "",
    timeout_minutes: int = 30,
    db_path: Path | None = None,
    dry_run: bool = False,
    on_progress: Callable | None = None,
    cancel_event: threading.Event | None = None,
    driver: object | None = None,
) -> ShipRun:
    """Drive one story from the saved plan to an approved PR. Never raises.

    ``driver`` is an injection seam (an ``AgentDriver``); the default is
    Claude Code headless. The human gate always resolves through
    ``ShipStore.resolve_gate`` — this function only waits for it.
    """
    if dry_run:
        return _dry_run_artifact(story_id, repo)
    agent = driver if driver is not None else ClaudeCodeDriver()

    # -- resolve inputs before spending anything ---------------------------
    try:
        story, tasks, resolved_session = _load_story(session_id, story_id, db_path)
    except Exception as exc:  # noqa: BLE001 — a broken DB is a failed run, not a crash
        return _failed(ShipRun(story_id=story_id, repo=repo, created_at=_now_iso()), f"could not load story: {exc}")

    available, detail = True, ""
    probe = getattr(agent, "available", None)
    if callable(probe):
        available, detail = probe()
    if not available:
        return _failed(
            ShipRun(story_id=story_id, repo=repo, session_id=resolved_session, created_at=_now_iso()),
            f"coding agent unavailable: {detail}",
        )

    run_id = _new_run_id(story_id)
    run = ShipRun(
        run_id=run_id,
        story_id=story_id,
        session_id=resolved_session,
        repo=repo,
        status="planned",
        created_at=_now_iso(),
    )

    # -- budget ------------------------------------------------------------
    decision = budget.reserve()
    if not decision.allowed:
        return _failed(run, f"launch budget denied: {decision.reason}")
    permit = decision.permit_id
    # A whole run (agent + validation + an unbounded gate wait) can outlive
    # the budget's stale-permit cutoff; the heartbeat keeps the concurrency
    # slot honest for as long as this run actually lives.
    stop_heartbeat = threading.Event()

    def _keep_permit_alive() -> None:
        while not stop_heartbeat.wait(_HEARTBEAT_S):
            budget.heartbeat(permit)

    threading.Thread(target=_keep_permit_alive, name="ship-budget-heartbeat", daemon=True).start()
    store: ShipStore | None = None
    try:
        # Inside the try: a locked/corrupt sessions.db must fail the run, not
        # raise past the contract — and must not leak the permit for 30 min.
        store = ShipStore(db_path)
        return _run_phases(
            store,
            run,
            story,
            tasks,
            agent,
            check_command=check_command,
            timeout_minutes=timeout_minutes,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
    except _RunAbortError as aborted:
        return aborted.run
    except Exception as exc:  # noqa: BLE001 — the engine contract: never raise
        logger.exception("Ship run %s crashed", run_id)
        failed = _failed(run, f"unexpected failure: {exc}")
        if store is not None:
            try:
                store.save_run(failed)
            except Exception:
                pass
        return failed
    finally:
        stop_heartbeat.set()
        budget.release(permit)
        if store is not None:
            store.close()


def _run_phases(
    store: ShipStore,
    run: ShipRun,
    story,
    tasks,
    agent,
    *,
    check_command: str,
    timeout_minutes: int,
    on_progress,
    cancel_event,
) -> ShipRun:
    """The phase sequence. Raises _RunAbortError with the terminal artifact."""

    def _abort(terminal: ShipRun) -> None:
        try:
            store.save_run(terminal)
        except Exception:
            logger.warning("Could not persist terminal state for %s", terminal.run_id)
        raise _RunAbortError(terminal)

    def _phase_done(name: str, detail: str, started: float) -> None:
        nonlocal run
        phase = ShipPhase(name=name, status="completed", detail=detail[:300], duration_s=time.monotonic() - started)
        run = replace(run, phases=(*run.phases, phase), updated_at=_now_iso())

    # -- setup -------------------------------------------------------------
    started = time.monotonic()
    _report(on_progress, "ship-setup", "Preparing isolated worktree", "running")
    try:
        record = worktree.prepare(run.run_id, run.repo)
    except worktree.WorktreeError as exc:
        _report(on_progress, "ship-setup", "Preparing isolated worktree", "failed", detail=str(exc))
        _abort(_failed(run, str(exc), phase="setup"))
    run = replace(run, repo=record.repo, branch=record.branch, worktree=record.path, base_sha=record.base_sha)
    _phase_done("setup", f"worktree at {record.path}", started)
    run = store.record_run(replace(run, status="running"))
    _report(on_progress, "ship-setup", "Preparing isolated worktree", "completed")

    # -- implement ---------------------------------------------------------
    prompt = pipeline.build_prompt(story, tasks)
    result = _implement(agent, prompt, record, run, timeout_minutes, on_progress, cancel_event, label="Implementing")
    if result is None:  # cancelled
        _abort(_save(store, replace(run, status="cancelled", updated_at=_now_iso())))

    # -- bridge + validate + gate loop ------------------------------------
    while True:
        started = time.monotonic()
        _report(on_progress, "ship-validate", "Validating the diff", "running")
        try:
            has_work, diff_stat = pipeline.diff_bridge(record)
        except worktree.WorktreeError as exc:
            _abort(_failed(run, f"could not inspect the worktree: {exc}", phase="validate"))
        if not has_work:
            # The bridge: an agent that declined still exited 0. The diff is
            # the evidence, and there is none.
            detail = "the agent produced no changes"
            if result is not None and result.output:
                detail += f" — it said: {result.output[:400]}"
            _report(on_progress, "ship-validate", "Validating the diff", "failed", detail="no changes")
            _abort(_failed(run, detail, phase="implement"))
        validation = pipeline.run_validation(record, check_command)
        run = replace(run, diff_stat=diff_stat, validation=validation, updated_at=_now_iso())
        _phase_done("validate", "passed" if validation.passed else "see gate screen", started)
        _report(
            on_progress,
            "ship-validate",
            "Validating the diff",
            "completed" if (not validation.configured or validation.passed) else "partial",
        )
        run = _attach_cost(run, result)

        # -- gate ----------------------------------------------------------
        _report(on_progress, "ship-gate", "Awaiting human approval", "running")
        run = replace(run, status="awaiting_approval", gate_resolution="", gate_comment="", updated_at=_now_iso())
        run = _save(store, run, expect_status="running")
        if run.status != "awaiting_approval":
            _abort(run)  # someone else moved the run; their state wins
        run = _await_gate(store, run, cancel_event=cancel_event)
        if run.status == "cancelled":
            _report(on_progress, "ship-gate", "Awaiting human approval", "failed", detail="cancelled")
            raise _RunAbortError(run)
        if run.gate_resolution == "approved":
            _report(on_progress, "ship-gate", "Awaiting human approval", "completed")
            break
        # rejected
        if run.rejection_count >= MAX_REJECTION_ATTEMPTS:
            _report(on_progress, "ship-gate", "Awaiting human approval", "failed", detail="rejected")
            terminal = replace(
                run,
                status="rejected",
                phases=(*run.phases, ShipPhase(name="gate", status="failed", detail="rejected at attempts cap")),
                updated_at=_now_iso(),
            )
            _abort(_save(store, terminal, expect_status="awaiting_approval"))
        _report(on_progress, "ship-gate", "Awaiting human approval", "partial", detail="rejected — reworking")
        rework = pipeline.rework_prompt(run.gate_comment, run.validation)
        run = _save(store, replace(run, status="running", updated_at=_now_iso()), expect_status="awaiting_approval")
        if run.status != "running":
            _abort(run)
        result = _implement(
            agent, rework, record, run, timeout_minutes, on_progress, cancel_event, label="Reworking after rejection"
        )
        if result is None:
            _abort(_save(store, replace(run, status="cancelled", updated_at=_now_iso())))

    # -- finalize ----------------------------------------------------------
    started = time.monotonic()
    _report(on_progress, "ship-finalize", "Pushing branch and opening PR", "running")
    title = f"{story.title or story.id} (via yeaboi ship)"
    summary = _pr_summary(run, story)
    outcome = pipeline.push_and_open_pr(record, title=title, body=pipeline.build_pr_body(summary, run.gate_comment))
    if not outcome.pushed:
        _report(on_progress, "ship-finalize", "Pushing branch and opening PR", "failed", detail=outcome.detail)
        _abort(_failed(run, outcome.detail, phase="finalize"))
    _phase_done("finalize", outcome.detail, started)
    run = replace(run, status="approved", pr_url=outcome.pr_url, updated_at=_now_iso())
    run = _save(store, run, expect_status="awaiting_approval")
    # The work is on origin now; the checkout has served its purpose. The
    # branch is kept (delete_branch defaults False) — it IS the record. On
    # every non-approved terminal path the checkout is kept too, for
    # inspection of what the agent actually did.
    if not worktree.remove(run.run_id):
        logger.warning("Could not clean up worktree for %s; remove it by hand", run.run_id)
    _report(on_progress, "ship-finalize", "Pushing branch and opening PR", "completed", detail=outcome.pr_url)
    logger.info("Ship run %s finished: %s", run.run_id, outcome.detail)
    return run


def _implement(
    agent,
    prompt: str,
    record,
    run: ShipRun,
    timeout_minutes: int,
    on_progress,
    cancel_event,
    *,
    label: str,
) -> DriverResult | None:
    """One driver invocation. None means cancelled; a failed run aborts here
    only on launch/quota problems — 'it ran but did nothing' is the bridge's
    call, not ours."""
    _report(on_progress, "ship-implement", label, "running")
    result = agent.run(
        prompt,
        Path(record.path),
        timeout_s=max(1, timeout_minutes) * 60,
        cancel_event=cancel_event,
        on_line=None,
    )
    if result.cancelled:
        _report(on_progress, "ship-implement", label, "failed", detail="cancelled")
        return None
    if not result.ok and budget.looks_like_quota_error(result.error):
        budget.record_quota_error(result.error[-300:])
    _report(on_progress, "ship-implement", label, "completed" if result.ok else "partial")
    return result


def _attach_cost(run: ShipRun, result: DriverResult | None) -> ShipRun:
    """Fold the transcript's cost + security findings into the artifact."""
    if result is None or not result.session_id:
        return run
    transcript = costing.locate_transcript(result.session_id)
    if transcript is None:
        return replace(run, agent_session_id=result.session_id, cost_usd=run.cost_usd + result.cost_usd)
    cost = costing.cost_transcript(transcript)
    if cost is None:
        return replace(run, agent_session_id=result.session_id, transcript_path=str(transcript))
    findings = tuple((f.kind, f.severity, f.label) for f in cost.findings)
    return replace(
        run,
        agent_session_id=result.session_id,
        transcript_path=str(transcript),
        cost_usd=run.cost_usd + (cost.usd or result.cost_usd),
        transcript_findings=(*run.transcript_findings, *findings),
    )


def _pr_summary(run: ShipRun, story) -> str:
    """The PR body's summary section — plan facts, run facts, no transcript."""
    lines = [
        "## Summary",
        "",
        f"Implements story `{run.story_id}` from the yeaboi sprint plan:",
        "",
        f"> {story.text}",
        "",
        "## Run record",
        "",
        f"- Diff: {run.diff_stat.splitlines()[-1] if run.diff_stat else 'see files changed'}",
    ]
    if run.validation.configured:
        state = "passed" if run.validation.passed else f"FAILED (exit {run.validation.exit_code})"
        lines.append(f"- Validation `{run.validation.command}`: {state}")
    else:
        lines.append("- Validation: none configured")
    if run.cost_usd:
        lines.append(f"- Agent cost: ${run.cost_usd:.2f}")
    if run.rejection_count:
        lines.append(f"- Gate rejections before approval: {run.rejection_count}")
    return "\n".join(lines)
