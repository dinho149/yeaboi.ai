"""The deterministic halves of a ship run: prompt, bridges, validation, PR.

The pipeline's honesty pattern is archon's precondition bridge: **an agent
that declines a task still exits 0**, so after the implement phase a cheap
deterministic check — is there actually a diff on disk? — is what decides
whether the run proceeds. Exit codes from a model are not evidence of work; a
commit is.

Everything here is subprocess/git/HTTP plumbing with no LLM anywhere; the
engine sequences these pieces and owns status transitions.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import urllib.parse
from dataclasses import dataclass

from yeaboi.agent.state import ShipValidation, Task, UserStory
from yeaboi.ship.scope import ShipTarget
from yeaboi.ship.worktree import WorktreeError, WorktreeRecord, _git
from yeaboi.tools.local_git import git_subprocess_env

logger = logging.getLogger(__name__)

VALIDATION_TIMEOUT_S = 15 * 60
DIFF_TEXT_CAP = 20_000  # the gate shows the patch; a runaway diff is capped, never dropped
_DIFF_REAP_S = 10.0
_TAIL_CHARS = 4000

_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


# ---------------------------------------------------------------------------
# Plan item → prompt
# ---------------------------------------------------------------------------

_RUN_CONTRACT = (
    "",
    "Run contract:",
    "- Work only inside this repository checkout.",
    "- Follow the repository's existing conventions and test layout.",
    "- You have file-edit permissions and no shell: edit files only. The",
    "  pipeline runs validation, commits your work, and pushes only after",
    "  a human approves the diff — do not attempt git or gh commands.",
    "- If you cannot complete the work, say why plainly and change nothing.",
)

_HEADLINE = {
    "epic": "Implement the following epic in this repository",
    "story": "Implement the following user story in this repository",
    "task": "Carry out the following implementation task in this repository",
}


def _criteria_block(story: UserStory, *, heading: str = "Acceptance criteria:") -> list[str]:
    """A story's acceptance criteria as flat sentences.

    ``flat_text`` is what renders both shapes the plan can carry — the
    Given/When/Then triple and the free-text criterion a "bullets" plan writes.
    Reading the triple directly turns every free-text criterion into
    "Given , when , then ." by the time it reaches the agent.
    """
    # Emptiness is a property of the fields, not of the rendered sentence: an
    # all-blank criterion still renders as "Given , when , then".
    filled = [c for c in story.acceptance_criteria or () if (c.text or c.given or c.when or c.then).strip()]
    lines = [c.flat_text.rstrip(".") for c in filled]
    if not lines:
        return []
    return ["", heading, *[f"{i}. {line}." for i, line in enumerate(lines, start=1)]]


def _task_block(task: Task, *, heading_level: str = "###") -> list[str]:
    lines = ["", f"{heading_level} {task.title or task.id}", task.ai_prompt or task.description]
    if task.test_plan:
        lines.append(f"Test plan: {task.test_plan}")
    return lines


def build_prompt(target: ShipTarget, *, project_name: str = "") -> str:
    """The coding agent's instruction for one plan item, at any level.

    ``Task.ai_prompt`` is already an ARC-structured instruction written for AI
    coding assistants (see the task decomposer); this frames the item around
    whichever of them are in scope and adds the run contract: work in place,
    commit, never push — the pipeline owns everything after the diff exists.
    """
    lines: list[str] = [f"{_HEADLINE.get(target.level, _HEADLINE['story'])}: {target.title or target.id}"]
    context = [bit for bit in (f"Project: {project_name}" if project_name else "", _parent_line(target)) if bit]
    if context:
        lines += ["", *context]
    lines += _body(target)
    lines += list(_RUN_CONTRACT)
    return "\n".join(lines)


def _parent_line(target: ShipTarget) -> str:
    """Where this item sits — an item handed over with no context is a guess."""
    if not target.parent_title:
        return ""
    label = {"story": "Epic", "task": "User story"}.get(target.level, "")
    if not label:
        return ""
    detail = f" — {target.parent_summary}" if target.parent_summary else ""
    return f"{label}: {target.parent_title}{detail}"


def _body(target: ShipTarget) -> list[str]:
    if target.level == "task":
        return _task_body(target)
    if target.level == "epic":
        return _epic_body(target)
    return _story_body(target)


def _story_body(target: ShipTarget) -> list[str]:
    lines = ["", target.summary]
    story = target.stories[0] if target.stories else None
    if story is not None:
        lines += _criteria_block(story)
    if target.tasks:
        lines += ["", "Tasks (in order):"]
        for task in target.tasks:
            lines += _task_block(task)
    return lines


def _task_body(target: ShipTarget) -> list[str]:
    task = target.tasks[0] if target.tasks else None
    lines: list[str] = []
    if task is not None:
        lines += ["", task.ai_prompt or task.description or task.title]
        if task.test_plan:
            lines += ["", f"Test plan: {task.test_plan}"]
    # A task says what to change; only its story says what "correct" means.
    story = target.stories[0] if target.stories else None
    if story is not None:
        lines += _criteria_block(story, heading="Acceptance criteria for the story this task belongs to:")
    lines += ["", "Only this task is in scope — do not implement the rest of the story."]
    return lines


def _epic_body(target: ShipTarget) -> list[str]:
    lines = ["", target.summary] if target.summary else []
    if not target.stories:
        lines += ["", "This epic has no stories in the plan; implement it from the description above."]
        return lines
    tasks_by_story: dict[str, list[Task]] = {}
    for task in target.tasks:
        tasks_by_story.setdefault(task.story_id, []).append(task)
    lines += [
        "",
        f"All {len(target.stories)} user stories below are in scope. Implement them in order:",
    ]
    for index, story in enumerate(target.stories, start=1):
        lines += ["", f"## {index}. {story.title or story.id}", story.text]
        lines += _criteria_block(story)
        for task in tasks_by_story.get(story.id, []):
            lines += _task_block(task, heading_level="####")
    return lines


# ---------------------------------------------------------------------------
# Bridges — deterministic evidence checks between phases
# ---------------------------------------------------------------------------


def ensure_committed(record: WorktreeRecord) -> None:
    """Commit anything the agent left uncommitted, so the branch is the work.

    A no-op on a clean tree. The commit identity falls back to a run-local
    one when the repo has none configured, because failing the whole run on
    a missing user.email would punish the wrong party.
    """
    if not _git(record.path, "status", "--porcelain"):
        return
    _git(record.path, "add", "-A")
    try:
        _git(record.path, "commit", "-m", f"ship: agent output for {record.run_id}")
    except WorktreeError:
        _git(
            record.path,
            "-c",
            "user.email=ship@yeaboi.local",
            "-c",
            "user.name=yeaboi ship",
            "commit",
            "-m",
            f"ship: agent output for {record.run_id}",
        )


def diff_bridge(record: WorktreeRecord) -> tuple[bool, str]:
    """(the agent produced work, `git diff --stat` vs the base).

    This is the precondition bridge: it runs after implement and before
    anything expensive, and an empty diff fails the run — whatever the agent's
    exit code said.
    """
    ensure_committed(record)
    stat = _git(record.path, "diff", "--stat", f"{record.base_sha}..HEAD")
    return bool(stat.strip()), stat.strip()


def diff_text(record: WorktreeRecord, *, max_chars: int = DIFF_TEXT_CAP) -> str:
    """The patch itself, capped — what the approver is actually asked to approve.

    A ``--stat`` is a file count, and the gate is the only control between
    agent-authored code and a pushed branch. Capping rather than paging keeps
    this a pure function of the artifact; the trailer names the exact command
    for reading the rest out of band. Never raises — a diff we cannot read is
    an empty string, and the gate says so rather than the run dying.

    The read is bounded, not sliced after the fact: an agent that commits a
    generated lockfile produces a patch of megabytes, and this runs on the
    TUI's worker thread. Only ``max_chars`` are ever pulled off the pipe.
    """
    argv = ["git", "-C", str(record.path), "diff", f"{record.base_sha}..HEAD"]
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed binary, whitelisted-id args
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=git_subprocess_env(),
            cwd=str(record.path),
        )
    except OSError as exc:
        logger.warning("Could not read the diff for %s: %s", record.run_id, exc)
        return ""
    try:
        assert proc.stdout is not None
        patch = proc.stdout.read(max_chars + 1)
    except OSError as exc:
        logger.warning("Could not read the diff for %s: %s", record.run_id, exc)
        patch = ""
    finally:
        # Stop git writing the rest into a pipe nobody drains, then reap it —
        # including after the kill, or the dead child lingers as a zombie.
        if proc.stdout is not None:
            proc.stdout.close()
        try:
            proc.wait(timeout=_DIFF_REAP_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=_DIFF_REAP_S)
            except subprocess.TimeoutExpired:
                logger.error("git diff for %s would not die", record.run_id)
    if proc.returncode not in (0, None) and not patch:
        logger.warning("git diff for %s exited %s", record.run_id, proc.returncode)
        return ""
    if len(patch) <= max_chars:
        return patch
    return (
        patch[:max_chars].rstrip()
        + f"\n\n… truncated at {max_chars} characters — read the rest with:\n"
        + f"  git -C {record.path} diff {record.base_sha}..HEAD"
    )


def run_validation(record: WorktreeRecord, command: str) -> ShipValidation:
    """Run the configured validation command in the worktree. Deterministic.

    No command is a *visible* state (``configured=False``), never a silent
    pass — the approval screen shows exactly what was and wasn't proven.
    """
    if not command.strip():
        return ShipValidation(configured=False)
    try:
        # Popen + its own process group, not subprocess.run: on timeout run()
        # kills only the shell, and then blocks in communicate() on the pipes
        # the orphaned grandchildren (pytest under make) still hold open —
        # the "timed out" path would hang the worker forever.
        proc = subprocess.Popen(  # noqa: S602 — the user's own check command (e.g. `make test`), by design
            command,
            shell=True,
            cwd=record.path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=git_subprocess_env(),
            start_new_session=True,
        )
    except OSError as exc:
        return ShipValidation(configured=True, command=command, passed=False, exit_code=-1, output_tail=str(exc))
    try:
        out, _ = proc.communicate(timeout=VALIDATION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            logger.error("Validation command would not die: %s", command)
        return ShipValidation(
            configured=True,
            command=command,
            passed=False,
            exit_code=-1,
            output_tail=f"validation timed out after {VALIDATION_TIMEOUT_S}s",
        )
    return ShipValidation(
        configured=True,
        command=command,
        passed=proc.returncode == 0,
        exit_code=proc.returncode,
        output_tail=(out or "")[-_TAIL_CHARS:],
    )


# ---------------------------------------------------------------------------
# Finalize — push + PR
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalizeResult:
    pushed: bool = False
    pr_url: str = ""
    detail: str = ""


def _github_slug(remote_url: str) -> tuple[str, str] | None:
    match = _GITHUB_REMOTE_RE.search(remote_url or "")
    if not match:
        return None
    return match.group(1), match.group(2)


def rework_prompt(comment: str, validation: ShipValidation) -> str:
    """The follow-up instruction after a gate rejection, comment included."""
    lines = [
        "A human reviewed your changes on this branch and rejected them.",
        f"Reviewer's feedback: {comment or '(no comment was given)'}",
    ]
    if validation.configured and not validation.passed:
        lines.append(f"The validation command also failed: `{validation.command}` (exit {validation.exit_code}).")
        lines.append(f"Its output ended with:\n{validation.output_tail[-1500:]}")
    lines += [
        "",
        "Address the feedback by editing the files on this branch. You have",
        "file-edit permissions and no shell; the pipeline re-validates and",
        "commits your work — do not attempt git or gh commands.",
    ]
    return "\n".join(lines)


def build_pr_body(run_summary: str, gate_comment: str) -> str:
    """The PR body, scrubbed the way every published text is."""
    from yeaboi.standup.gap_issues import leak_check, scrub

    parts = [run_summary]
    if gate_comment:
        parts += ["", f"Approved with: {gate_comment}"]
    parts += ["", "---", "🦆 Shipped by yeaboi's supervised agent pipeline; a human approved this diff."]
    body = scrub("\n".join(parts), {})
    leak = leak_check(body)
    if leak:
        # Publishing is the one irreversible step; a suspicious body loses
        # its prose rather than the run losing its PR.
        logger.warning("PR body looked like it contained %s; sending the minimal body", leak)
        return "Shipped by yeaboi's supervised agent pipeline; a human approved this diff."
    return body


def push_and_open_pr(record: WorktreeRecord, *, title: str, body: str, base: str = "") -> FinalizeResult:
    """Push the branch and open a PR (API when a token exists, else a URL).

    ``base`` targets the PR at another branch instead of the default one — a
    batch stacks each story on the one before it, and a stacked PR opened
    against the default branch would show every earlier story's diff too.
    It must already be pushed; the batch pushes in order, so it is.

    Never raises. On a non-GitHub remote the push still happens and the
    detail says what to do next — the branch is the deliverable, the PR is
    the convenience.
    """
    try:
        _git(record.path, "push", "-u", "origin", record.branch)
    except WorktreeError as exc:
        return FinalizeResult(detail=f"push failed: {exc}")
    try:
        remote = _git(record.repo, "remote", "get-url", "origin")
    except WorktreeError:
        remote = ""
    slug = _github_slug(remote)
    if slug is None:
        return FinalizeResult(pushed=True, detail="branch pushed; origin is not GitHub, open the PR by hand")
    owner, name = slug
    from yeaboi.config import get_github_token

    if get_github_token():
        try:
            from yeaboi.tools.github import _get_github_client

            gh_repo = _get_github_client().get_repo(f"{owner}/{name}")
            pr = gh_repo.create_pull(title=title, body=body, head=record.branch, base=base or gh_repo.default_branch)
            logger.info("Opened PR %s", pr.html_url)
            return FinalizeResult(pushed=True, pr_url=pr.html_url, detail=f"opened PR #{pr.number}")
        except Exception as exc:
            logger.warning("PR creation failed: %s", exc)
            # Fall through to the compare URL — the push already succeeded.
    head = urllib.parse.quote(record.branch, safe="")
    spec = f"{urllib.parse.quote(base, safe='')}...{head}" if base else head
    compare = f"https://github.com/{owner}/{name}/compare/{spec}?expand=1&title={urllib.parse.quote(title)}"
    return FinalizeResult(pushed=True, pr_url=compare, detail="branch pushed; open the PR from this URL")
