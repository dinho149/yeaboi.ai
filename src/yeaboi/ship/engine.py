"""Ship engine — one supervised plan-item → PR run, as a standalone pipeline.

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
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from yeaboi.agent.state import ShipPhase, ShipRun, ShipValidation
from yeaboi.analysis.progress import send_component_progress
from yeaboi.ship import budget, costing, pipeline, scope, worktree
from yeaboi.ship.driver import ClaudeCodeDriver, DriverResult
from yeaboi.ship.scope import ShipTarget
from yeaboi.ship.store import ShipStore, driven_elsewhere

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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _report(on_progress, component_id: str, label: str, status: str, **kwargs) -> None:
    try:
        send_component_progress(on_progress, component_id=component_id, label=label, status=status, **kwargs)
    except Exception:  # a progress consumer must never kill the run
        logger.debug("progress callback failed", exc_info=True)


def _new_run_id(item_id: str, level: str = "story") -> str:
    """A run id: plan item, second-resolution stamp, and a random suffix.

    The suffix is what makes the id an identity rather than a description.
    Two runs of the same story started in the same second would otherwise
    collide — and since both surfaces now find *their* run by this id, and
    ``worktree.prepare`` is idempotent per id, a collision would hand the
    second run the first one's checkout and let one gate answer for both.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in item_id.lower()).strip("-.")[:40]
    return f"{safe or level or 'item'}-{stamp}-{secrets.token_hex(3)}"


def _failed(run: ShipRun, reason: str, *, phase: str = "") -> ShipRun:
    phases = run.phases
    if phase:
        phases = (*phases, ShipPhase(name=phase, status="failed", detail=reason[:300]))
    return replace(run, status="failed", phases=phases, warnings=(*run.warnings, reason), updated_at=_now_iso())


def _load_target(session_id: str, item_id: str, level: str, db_path: Path | None) -> tuple[ShipTarget, str, str]:
    """(target, resolved_plan_id, project_name) — raises ValueError with a plain reason.

    Resolves a plan across BOTH stores yeaboi saves plans to (the interactive
    chat's project store and the SQLite session store) via ``ship.plans`` — the
    same source the picker uses, so anything shown in the picker can always be
    loaded here — then resolves *item_id* at whichever level it lives.
    """
    from yeaboi.ship import plans

    name = ""
    if session_id:
        resolved = session_id
        state = plans.load_plan_state(session_id, db_path)
    else:
        picked = plans.latest_plan_with_work(db_path)
        if picked is None:
            raise ValueError("no saved plan — generate a plan first")
        state, resolved, name = picked
    if not state:
        raise ValueError(f"plan {resolved} has no saved state")
    return scope.find_target(state, item_id, level=level), resolved, name or str(state.get("project_name") or "")


def _dry_run_artifact(item_id: str, repo: str, level: str) -> ShipRun:
    """A canned, fully-shaped run: no subprocess, no git, no network."""
    phases = tuple(
        ShipPhase(name=name, status="completed", detail="dry run")
        for name in ("setup", "implement", "validate", "gate", "finalize")
    )
    return ShipRun(
        run_id="dry-run",
        item_id=item_id,
        level=level or "story",
        repo=repo,
        branch="ship/dry-run",
        status="approved",
        phases=phases,
        validation=ShipValidation(configured=True, command="(dry run)", passed=True, exit_code=0),
        diff_stat="(dry run — nothing was executed)",
        gate_resolution="approved",
        created_at=_now_iso(),
        updated_at=_now_iso(),
        warnings=(
            "dry run — no agent was launched and nothing was written",
            # A dry run never reads a plan, so it cannot know what F1 is.
            *(() if level else ("the item's level was assumed to be a story — pass --level to be explicit",)),
        ),
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
    item_id: str,
    repo: str,
    *,
    level: str = "",
    session_id: str = "",
    check_command: str = "",
    timeout_minutes: int = 30,
    db_path: Path | None = None,
    dry_run: bool = False,
    on_progress: Callable | None = None,
    on_run_id: Callable[[str], None] | None = None,
    on_agent_line: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    driver: object | None = None,
    base_ref: str = "HEAD",
    pr_base: str = "",
    batch_id: str = "",
    batch_item_id: str = "",
    batch_index: int = 0,
    batch_total: int = 0,
) -> ShipRun:
    """Drive one plan item from the saved plan to an approved PR. Never raises.

    *item_id* names an epic, a story or a task; ``level`` disambiguates a
    colliding id and is otherwise inferred. ``base_ref``/``pr_base`` branch and
    target this run somewhere other than the repo default — a batch stacks each
    story on the one before it. The ``batch_*`` fields are bookkeeping only:
    a member run is an ordinary run in every other respect.

    ``driver`` is an injection seam (an ``AgentDriver``); the default is
    Claude Code headless. The human gate always resolves through
    ``ShipStore.resolve_gate`` — this function only waits for it.

    ``on_run_id`` fires once, as soon as the id exists, because the surfaces
    poll a shared store for the gate: without it they can only identify "my
    run" as "one that was not there when I started", which stops being true
    the moment two runs are allowed at once — and then a user is asked to
    approve, and push, a diff they have never seen.

    ``on_agent_line`` receives one raw JSON event per line while the agent
    works (it selects the driver's ``stream-json`` mode). It is the live-board
    seam; a caller that omits it keeps the unchanged one-shot ``json`` path, so
    a plain CLI/TUI run is entirely unaffected. Filtering these events down to
    what is safe to show a remote watcher is the board's job, not the engine's.
    """
    if dry_run:
        return _dry_run_artifact(item_id, repo, level)
    agent = driver if driver is not None else ClaudeCodeDriver()

    # -- resolve inputs before spending anything ---------------------------
    try:
        target, resolved_session, project_name = _load_target(session_id, item_id, level, db_path)
    except Exception as exc:  # noqa: BLE001 — a broken DB is a failed run, not a crash
        return _failed(
            ShipRun(item_id=item_id, level=level or "story", repo=repo, created_at=_now_iso()),
            f"could not load {level or 'plan item'}: {exc}",
        )

    available, detail = True, ""
    probe = getattr(agent, "available", None)
    if callable(probe):
        available, detail = probe()
    if not available:
        return _failed(
            ShipRun(
                item_id=item_id,
                level=target.level,
                repo=repo,
                session_id=resolved_session,
                created_at=_now_iso(),
            ),
            f"coding agent unavailable: {detail}",
        )

    run_id = _new_run_id(item_id, target.level)
    if on_run_id is not None:
        try:
            on_run_id(run_id)
        except Exception:  # a surface's bookkeeping must never kill the run
            logger.debug("on_run_id callback failed", exc_info=True)
    run = ShipRun(
        run_id=run_id,
        item_id=item_id,
        level=target.level,
        session_id=resolved_session,
        repo=repo,
        status="planned",
        batch_id=batch_id,
        batch_item_id=batch_item_id,
        batch_index=batch_index,
        batch_total=batch_total,
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
            target,
            agent,
            project_name=project_name,
            check_command=check_command,
            timeout_minutes=timeout_minutes,
            on_progress=on_progress,
            on_agent_line=on_agent_line,
            cancel_event=cancel_event,
            base_ref=base_ref,
            pr_base=pr_base,
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


def _new_batch_id(item_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in item_id.lower()).strip("-.")[:24]
    return f"batch-{safe or 'epic'}-{stamp}-{secrets.token_hex(2)}"


def run_ship_batch(
    item_id: str,
    repo: str,
    *,
    level: str = "",
    session_id: str = "",
    check_command: str = "",
    timeout_minutes: int = 30,
    db_path: Path | None = None,
    on_progress: Callable | None = None,
    on_run_id: Callable[[str], None] | None = None,
    on_agent_line: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    driver: object | None = None,
) -> tuple[ShipRun, ...]:
    """Ship an epic as one PR per story, each stacked on the one before it.

    Same contract as :func:`run_ship`: never raises. Every outcome is a
    ``ShipRun`` in the returned tuple, in plan order.

    Three rules make a batch predictable:

    * **Stacked.** Story *k* branches off story *k-1*'s branch and its PR targets
      it. Later stories in an epic build on earlier ones; off a shared base every
      diff would re-show the same files and conflict.
    * **Stops at the first member that does not end approved.** Stories already
      shipped keep their PRs; the rest come back ``planned`` carrying the reason
      and are never persisted, because they were never attempted.
    * **Relaunching continues.** An unfinished batch for this item is adopted and
      its approved stories skipped, so a batch stopped by the launch budget needs
      no separate resume — the fuse allows two launches an hour, so a large epic
      *will* stop partway, and that is the intended shape, not a failure.
    """
    logger.info("Ship batch requested: %s (level=%s) in %s", item_id, level or "inferred", repo)
    try:
        target, resolved_session, _name = _load_target(session_id, item_id, level, db_path)
    except Exception as exc:  # noqa: BLE001
        blank = ShipRun(item_id=item_id, level=level or "epic", repo=repo, created_at=_now_iso())
        return (_failed(blank, f"could not load {level or 'plan item'}: {exc}"),)

    story_ids = scope.split_story_ids(target)
    if not story_ids:
        blank = ShipRun(item_id=item_id, level=target.level, repo=repo, created_at=_now_iso())
        reason = (
            f"{target.level} {item_id!r} has no stories to split over — ship it as one run instead"
            if target.level == "epic"
            else f"only an epic can be shipped one PR per story ({item_id!r} is a {target.level})"
        )
        return (_failed(blank, reason),)

    store: ShipStore | None = None
    try:
        store = ShipStore(db_path)
        batch_id, members = store.open_batch(item_id, repo, story_ids)
        adopted = bool(batch_id)
        batch_id = batch_id or _new_batch_id(item_id)
        shipped = {m.item_id: m for m in members if m.status == "approved"}
    except Exception as exc:  # noqa: BLE001 — a locked store is a failed batch, not a crash
        logger.warning("Could not open the ship store for a batch: %s", exc)
        blank = ShipRun(item_id=item_id, level=target.level, repo=repo, created_at=_now_iso())
        return (_failed(blank, f"could not read past runs: {exc}"),)
    finally:
        if store is not None:
            store.close()

    total = len(story_ids)
    logger.info(
        "Batch %s: %s %s over %d stories, %d already shipped",
        batch_id,
        "continuing" if adopted else "opening",
        item_id,
        total,
        len(shipped),
    )
    results: list[ShipRun] = []
    previous_branch = ""
    stop_reason = ""
    for index, story_id in enumerate(story_ids, start=1):
        if stop_reason:
            results.append(_unstarted(story_id, repo, (batch_id, item_id), index, total, stop_reason))
            continue
        done = shipped.get(story_id)
        if done is not None:
            previous_branch = done.branch or previous_branch
            results.append(done)
            continue
        if cancel_event is not None and cancel_event.is_set():
            stop_reason = "the batch was cancelled"
            results.append(_unstarted(story_id, repo, (batch_id, item_id), index, total, stop_reason))
            continue
        previous_branch = _stack_base(repo, previous_branch)
        member = run_ship(
            story_id,
            repo,
            level="story",
            # The plan is pinned once: with the caller's session_id often empty,
            # each member would otherwise re-resolve "the latest plan with work"
            # and a batch could straddle two plans.
            session_id=resolved_session,
            check_command=check_command,
            timeout_minutes=timeout_minutes,
            db_path=db_path,
            on_progress=on_progress,
            on_run_id=on_run_id,
            on_agent_line=on_agent_line,
            cancel_event=cancel_event,
            driver=driver,
            base_ref=previous_branch or "HEAD",
            pr_base=previous_branch,
            batch_id=batch_id,
            batch_item_id=item_id,
            batch_index=index,
            batch_total=total,
        )
        results.append(member)
        if member.status != "approved":
            stop_reason = _stop_reason(member)
            logger.info("Batch %s stopped at %s: %s", batch_id, story_id, stop_reason)
            continue
        previous_branch = member.branch or previous_branch
    logger.info(
        "Batch %s finished: %d of %d approved%s",
        batch_id,
        sum(1 for r in results if r.status == "approved"),
        total,
        f" (stopped: {stop_reason})" if stop_reason else "",
    )
    return tuple(results)


def _stack_base(repo: str, branch: str) -> str:
    """The branch to stack the next member on, or "" for the repo default.

    A parent branch that is gone was almost certainly merged, so its work is in
    HEAD already and branching there is right — and it beats failing the rest of
    the batch on "unknown revision".
    """
    if not branch:
        return ""
    try:
        if worktree._branch_exists(worktree.resolve_repo(repo), branch):
            return branch
    except Exception:  # noqa: BLE001 — an unreadable repo fails at prepare, with a better message
        return ""
    logger.info("Stack base %s is gone (merged?); the next story branches from HEAD", branch)
    return ""


def _unstarted(story_id: str, repo: str, batch: tuple[str, str], index: int, total: int, reason: str) -> ShipRun:
    """A story the batch never reached. Deliberately not persisted."""
    return ShipRun(
        item_id=story_id,
        level="story",
        repo=repo,
        status="planned",
        batch_id=batch[0],
        batch_item_id=batch[1],
        batch_index=index,
        batch_total=total,
        created_at=_now_iso(),
        warnings=(reason,),
    )


def _stop_reason(member: ShipRun) -> str:
    """Why the batch stopped, in the stopping run's own words where it has any."""
    detail = member.warnings[-1] if member.warnings else ""
    if member.status == "rejected":
        detail = detail or "the diff was rejected at the gate"
    elif member.status == "cancelled":
        detail = detail or "the run was cancelled"
    return f"stopped at {member.item_id}: {detail or member.status}"


def _resumable_reason(run: ShipRun) -> str:
    """ "" when *run* can be resumed, else why it cannot. Never raises.

    A run is resumable when it is parked at the gate, its checkout is still on
    disk, and the process that was driving it is gone. The pid check is what
    stops two processes finalizing the same branch; a reused pid reads as "still
    owned" and refuses, which is the harmless direction.

    A gate that was already *answered* still counts as resumable: only the engine
    writes ``approved`` and opens the PR, so a run whose approver died between the
    CAS and the push is stranded with a resolution nobody acted on. Resume re-asks
    that gate rather than acting on the stale answer — the approver is gone, and a
    push is the one irreversible step.
    """
    if run.status != "awaiting_approval":
        return f"only a run waiting at the gate can be resumed (this one is {run.status})"
    if driven_elsewhere(run):
        return f"another yeaboi process (pid {run.owner_pid}) is still driving this run"
    try:
        record = worktree.get_record(run.run_id)
    except Exception:  # noqa: BLE001 — an unreadable registry is "cannot resume", not a crash
        return "the worktree registry could not be read"
    if record is None or not record.path:
        return "the worktree was removed — nothing is left to push"
    if not Path(record.path).is_dir():
        return "the worktree was removed — nothing is left to push"
    return ""


def resume_ship(
    run_id: str,
    *,
    db_path: Path | None = None,
    check_command: str = "",
    timeout_minutes: int = 30,
    on_progress: Callable | None = None,
    on_agent_line: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    driver: object | None = None,
) -> ShipRun:
    """Re-attach to a run abandoned at the approval gate and finish it. Never raises.

    The gate wait lives on the engine's own thread, so a run whose process died is
    stranded: its branch and worktree are kept, its diff is stored, and nothing
    anywhere will ever push it. This is the way back in — it reclaims the stored
    worktree record and re-enters :func:`_gate_and_finalize`, so approve pushes and
    opens the PR, and reject re-runs the agent, exactly as they would have.

    The launch budget is charged **lazily**: approving is a ``git push`` and must
    not spend one of the (default two) hourly agent launches. Only a rejection,
    which re-runs the coding agent, reserves a permit.
    """
    store: ShipStore | None = None
    permit_box: list[str] = [""]
    stop_heartbeat = threading.Event()
    try:
        store = ShipStore(db_path)
        run = store.get_run(run_id)
        if run is None:
            return _failed(ShipRun(run_id=run_id, created_at=_now_iso()), f"no ship run named {run_id!r}")
        reason = _resumable_reason(run)
        if reason:
            return _failed(run, f"cannot resume: {reason}")
        record = worktree.get_record(run.run_id)
        agent = driver if driver is not None else ClaudeCodeDriver()

        # Claiming the run and re-opening its gate are ONE guarded write, and the
        # CAS's own boolean is what decides who owns it. Stamping the pid without
        # moving the status would let two resumers both "win": the predicate is on
        # status, so the second write would still match, and both would go on to
        # push the same branch. _save is deliberately not used here — it hides a
        # lost CAS by adopting the stored row, which reads as success.
        staged = replace(run, status="running", owner_pid=os.getpid(), updated_at=_now_iso())
        if not store.save_run(staged, expect_status="awaiting_approval"):
            stored = store.get_run(run_id)
            logger.info("Resume of %s lost the claim; %s owns it", run_id, getattr(stored, "owner_pid", "?"))
            return stored or run
        run = staged
        logger.info("Resuming ship run %s (branch %s)", run.run_id, run.branch)
        _report(on_progress, "ship-setup", "Preparing isolated worktree", "completed", detail="resumed")
        _report(on_progress, "ship-implement", "Implementing", "completed", detail="from the earlier run")

        def _ensure_budget() -> str:
            """Reserve a launch slot for a rework, once. "" allows, else the reason."""
            if permit_box[0]:
                return ""
            decision = budget.reserve()
            if not decision.allowed:
                return decision.reason
            permit_box[0] = decision.permit_id

            def _keep_permit_alive() -> None:
                while not stop_heartbeat.wait(_HEARTBEAT_S):
                    budget.heartbeat(permit_box[0])

            threading.Thread(target=_keep_permit_alive, name="ship-budget-heartbeat", daemon=True).start()
            return ""

        title, summary = _resumed_context(run, db_path)
        return _gate_and_finalize(
            store,
            run,
            record,
            agent,
            result=None,
            title=title,
            summary=summary,
            check_command=check_command,
            timeout_minutes=timeout_minutes,
            on_progress=on_progress,
            on_agent_line=on_agent_line,
            cancel_event=cancel_event,
            pr_base=run.pr_base,
            ensure_budget=_ensure_budget,
            revalidate=bool(check_command),
        )
    except _RunAbortError as aborted:
        return aborted.run
    except Exception as exc:  # noqa: BLE001 — the engine contract: never raise
        logger.exception("Ship resume %s crashed", run_id)
        return _failed(ShipRun(run_id=run_id, created_at=_now_iso()), f"unexpected failure: {exc}")
    finally:
        stop_heartbeat.set()
        if permit_box[0]:
            budget.release(permit_box[0])
        if store is not None:
            store.close()


def _resumed_context(run: ShipRun, db_path: Path | None) -> tuple[str, str]:
    """(title, summary) for the PR, best-effort — the plan may be long gone.

    A missing plan must not cost the run its PR: the diff is the deliverable, so a
    degraded title and an honest body beat a failure.
    """
    try:
        target, _resolved, _name = _load_target(run.session_id, run.item_id, run.level, db_path)
    except Exception as exc:  # noqa: BLE001
        logger.info("Resume %s could not reload the plan item: %s", run.run_id, exc)
        return run.item_id, "The plan this came from is no longer available; see the diff."
    return target.title or target.id, target.summary


def _abort(store: ShipStore, terminal: ShipRun) -> None:
    """Persist a terminal artifact, then unwind out of the phase sequence."""
    try:
        store.save_run(terminal)
    except Exception:
        logger.warning("Could not persist terminal state for %s", terminal.run_id)
    raise _RunAbortError(terminal)


def _with_phase(run: ShipRun, name: str, detail: str, started: float) -> ShipRun:
    """*run* with one completed phase appended."""
    phase = ShipPhase(name=name, status="completed", detail=detail[:300], duration_s=time.monotonic() - started)
    return replace(run, phases=(*run.phases, phase), updated_at=_now_iso())


def _run_phases(
    store: ShipStore,
    run: ShipRun,
    target: ShipTarget,
    agent,
    *,
    project_name: str,
    check_command: str,
    timeout_minutes: int,
    on_progress,
    on_agent_line,
    cancel_event,
    base_ref: str = "HEAD",
    pr_base: str = "",
) -> ShipRun:
    """The phase sequence. Raises _RunAbortError with the terminal artifact."""
    # -- setup -------------------------------------------------------------
    started = time.monotonic()
    _report(on_progress, "ship-setup", "Preparing isolated worktree", "running")
    try:
        record = worktree.prepare(run.run_id, run.repo, base_ref=base_ref)
    except worktree.WorktreeError as exc:
        _report(on_progress, "ship-setup", "Preparing isolated worktree", "failed", detail=str(exc))
        _abort(store, _failed(run, str(exc), phase="setup"))
    run = replace(
        run, repo=record.repo, branch=record.branch, worktree=record.path, base_sha=record.base_sha, pr_base=pr_base
    )
    run = _with_phase(run, "setup", f"worktree at {record.path}", started)
    run = store.record_run(replace(run, status="running"))
    _report(on_progress, "ship-setup", "Preparing isolated worktree", "completed")

    # -- implement ---------------------------------------------------------
    prompt = pipeline.build_prompt(target, project_name=project_name)
    result = _implement(
        agent, prompt, record, run, timeout_minutes, on_progress, on_agent_line, cancel_event, label="Implementing"
    )
    if result is None:  # cancelled
        _abort(store, _save(store, replace(run, status="cancelled", updated_at=_now_iso())))

    return _gate_and_finalize(
        store,
        run,
        record,
        agent,
        result=result,
        title=target.title or target.id,
        summary=target.summary,
        pr_base=pr_base,
        check_command=check_command,
        timeout_minutes=timeout_minutes,
        on_progress=on_progress,
        on_agent_line=on_agent_line,
        cancel_event=cancel_event,
    )


def _gate_and_finalize(
    store: ShipStore,
    run: ShipRun,
    record,
    agent,
    *,
    result: DriverResult | None,
    title: str,
    summary: str,
    check_command: str,
    timeout_minutes: int,
    on_progress,
    on_agent_line,
    cancel_event,
    ensure_budget: Callable[[], str] | None = None,
    revalidate: bool = True,
    pr_base: str = "",
) -> ShipRun:
    """Validate → gate → (rework | push + PR). Raises _RunAbortError to unwind.

    The back half of a run, shared by ``run_ship`` and ``resume_ship``: everything
    from "there is a diff on disk" to "a human answered and the branch is on
    origin". Resume exists because this half is reachable from a stored artifact
    plus a worktree record — nothing here needs the process that started the run.

    ``ensure_budget`` is consulted only before a *rework*, which re-runs the coding
    agent. It returns "" to allow or a denial reason. ``run_ship`` already holds a
    permit for its whole run and passes None; ``resume_ship`` reserves lazily, so
    approving an abandoned run costs no launch budget.

    ``revalidate=False`` keeps the stored verdict for the first pass instead of
    re-running the check command: on a resume it is the same commit, and silently
    spending fifteen minutes on ``make test`` because someone pressed Resume would
    be a surprise. A rework produces new code, so every later pass revalidates.
    """
    while True:
        started = time.monotonic()
        _report(on_progress, "ship-validate", "Validating the diff", "running")
        try:
            has_work, diff_stat = pipeline.diff_bridge(record)
        except worktree.WorktreeError as exc:
            _abort(store, _failed(run, f"could not inspect the worktree: {exc}", phase="validate"))
        if not has_work:
            # The bridge: an agent that declined still exited 0. The diff is
            # the evidence, and there is none.
            detail = "the agent produced no changes"
            if result is not None and result.output:
                detail += f" — it said: {result.output[:400]}"
            _report(on_progress, "ship-validate", "Validating the diff", "failed", detail="no changes")
            _abort(store, _failed(run, detail, phase="implement"))
        validation = pipeline.run_validation(record, check_command) if revalidate else run.validation
        revalidate = True  # only the resumed first pass reuses a stored verdict
        # The patch travels with the artifact, so every gate surface shows the
        # change itself rather than a file count.
        run = replace(
            run,
            diff_stat=diff_stat,
            diff_text=pipeline.diff_text(record),
            validation=validation,
            updated_at=_now_iso(),
        )
        run = _with_phase(run, "validate", "passed" if validation.passed else "see gate screen", started)
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
            _abort(store, run)  # someone else moved the run; their state wins
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
            _abort(store, _save(store, terminal, expect_status="awaiting_approval"))
        denial = ensure_budget() if ensure_budget is not None else ""
        if denial:
            _report(on_progress, "ship-gate", "Awaiting human approval", "failed", detail=denial)
            _abort(store, _failed(run, f"rework needs a launch slot: {denial}", phase="implement"))
        _report(on_progress, "ship-gate", "Awaiting human approval", "partial", detail="rejected — reworking")
        rework = pipeline.rework_prompt(run.gate_comment, run.validation)
        run = _save(store, replace(run, status="running", updated_at=_now_iso()), expect_status="awaiting_approval")
        if run.status != "running":
            _abort(store, run)
        result = _implement(
            agent,
            rework,
            record,
            run,
            timeout_minutes,
            on_progress,
            on_agent_line,
            cancel_event,
            label="Reworking after rejection",
        )
        if result is None:
            _abort(store, _save(store, replace(run, status="cancelled", updated_at=_now_iso())))

    # -- finalize ----------------------------------------------------------
    started = time.monotonic()
    _report(on_progress, "ship-finalize", "Pushing branch and opening PR", "running")
    pr_title = f"{title or run.item_id} (via yeaboi ship)"
    body = pipeline.build_pr_body(_pr_summary(run, summary), run.gate_comment)
    outcome = pipeline.push_and_open_pr(record, title=pr_title, body=body, base=pr_base)
    if not outcome.pushed:
        _report(on_progress, "ship-finalize", "Pushing branch and opening PR", "failed", detail=outcome.detail)
        _abort(store, _failed(run, outcome.detail, phase="finalize"))
    run = _with_phase(run, "finalize", outcome.detail, started)
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
    on_agent_line,
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
        on_line=on_agent_line,
        stream=on_agent_line is not None,
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


_PR_NOUN = {"epic": "epic", "story": "story", "task": "task"}


def _pr_summary(run: ShipRun, summary: str) -> str:
    """The PR body's summary section — plan facts, run facts, no transcript."""
    noun = _PR_NOUN.get(run.level, "story")
    lines = [
        "## Summary",
        "",
        f"Implements {noun} `{run.item_id}` from the yeaboi plan:",
        "",
        # One blockquote per line: a multi-line epic description would otherwise
        # quote only its first line and render the rest as body text.
        *[f"> {line}" for line in (summary or "(no description in the plan)").splitlines()],
        "",
        "## Run record",
        "",
        f"- Diff: {run.diff_stat.splitlines()[-1] if run.diff_stat else 'see files changed'}",
    ]
    if run.batch_total:
        lines.append(f"- Batch: story {run.batch_index} of {run.batch_total}")
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
