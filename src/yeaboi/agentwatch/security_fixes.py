"""What to do about a security finding: a catalogue of fixes, and how to apply them.

Every finding used to carry one sentence of advice. This module replaces the
sentence with *buttons*: a :class:`SecurityFix` names a concrete action with
a target, and :func:`apply_fix` performs it — writing a Claude Code guard
hook, editing one settings key, opening a PR, or recording that a key was
rotated. Each applied fix leaves two traces: a dismissal with the reason
``fixed: …`` (so the finding reads as handled) and a row in the fixes audit
table.

Local writes go through ``fs_policy.request_consent`` — ``~/.claude`` is
read-only by default, so the first write asks. Repo writes never touch the
checkout: a worktree is prepared, the files committed, the branch pushed and
a PR opened, the worktree removed.

Never raises past :func:`apply_fix`; a refusal is an outcome with a reason.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from yeaboi.agent.state import SecurityFinding, SecurityFix

logger = logging.getLogger(__name__)

GUARD_SCRIPT_NAME = "yeaboi-guard.py"
GUARD_RULES_NAME = "yeaboi-guard.json"
GUARD_COMMAND = "python3 ~/.claude/hooks/yeaboi-guard.py"
GUARD_COMMAND_REPO = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/yeaboi-guard.py"'
SECRET_IN_COMMAND = "secret-in-command"  # noqa: S105 — a rule id, not a credential

# The rule a guard hook blocks per pattern. Regex text, kept as data in the
# JSON beside the script so a person can read and edit what is blocked.
_SECRET_IN_COMMAND_RE = "|".join(
    (
        r"sk-ant-[\w-]{10,}",
        r"sk-[A-Za-z0-9_-]{20,}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"xox[abprs]-[\w-]{10,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[\w-]{35}",
        r"ntn_[A-Za-z0-9]{20,}",
        r"ATATT[\w=+/-]{20,}",
    )
)


def guard_rules() -> dict[str, str]:
    """Every rule the guard can enforce: the collector's risky patterns + a secret shape."""
    from yeaboi.agentwatch.collector import _RISKY_BASH_PATTERNS

    rules = {label: regex.pattern for label, regex in _RISKY_BASH_PATTERNS}
    rules[SECRET_IN_COMMAND] = _SECRET_IN_COMMAND_RE
    return rules


GUARD_SCRIPT = '''#!/usr/bin/env python3
"""yeaboi guard — a Claude Code PreToolUse hook that blocks risky shell commands.

Reads yeaboi-guard.json beside this file: {"rules": {"<id>": "<regex>"}}.
A Bash command matching any rule is refused (exit 2, the reason on stderr).
A heredoc redirected into a file is ignored — text an agent writes into a
file is not a command — but a heredoc fed to a program (bash <<EOF) runs, so
its body is checked like the rest. Edit the JSON to allow or add a rule; this
script never needs changing.
"""
import json
import os
import re
import sys

OPEN = re.compile(r"<<-?\\s*(['\\"]?)([A-Za-z_][\\w-]*)\\1")
TO_FILE = re.compile(r"(?:>>?|\\btee\\s+)")


def without_file_heredocs(command):
    lines = command.split("\\n")
    kept = []
    i = 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        i += 1
        for m in OPEN.finditer(line):
            tag = m.group(2)
            to_file = TO_FILE.search(line[: m.start()]) is not None or TO_FILE.search(line[m.end():]) is not None
            while i < len(lines) and lines[i].strip() != tag:
                if not to_file:
                    kept.append(lines[i])
                i += 1
            i += 1
    return "\\n".join(kept)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "yeaboi-guard.json"), encoding="utf-8") as fh:
            rules = json.load(fh).get("rules") or {}
    except Exception:
        return 0
    stripped = without_file_heredocs(command)
    for rule_id, pattern in rules.items():
        try:
            if re.search(pattern, stripped):
                print(f"yeaboi guard: blocked ({rule_id}) — edit yeaboi-guard.json beside this hook to allow it",
                      file=sys.stderr)
                return 2
        except re.error:
            continue
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

# Plain-words titles per pattern (the issue row), and why each one matters.
TITLES: dict[str, str] = {
    "curl-pipe-shell": "An agent piped a download into a shell",
    "base64-decode-pipe-shell": "An agent decoded base64 into a shell",
    "rm-rf-root": "An agent ran rm -rf on a root path",
    "permission-bypass-flag": "An agent ran Claude Code with permissions skipped",
    "sudo": "An agent used sudo",
    "secret-anthropic-key": "An Anthropic API key appeared in a session",
    "secret-sk-generic": "An sk- shaped key appeared in a session",
    "secret-github-token": "A GitHub token appeared in a session",
    "secret-github-pat": "A GitHub fine-grained token appeared in a session",
    "secret-slack-token": "A Slack token appeared in a session",
    "secret-slack-webhook": "A Slack webhook URL appeared in a session",
    "secret-google-api-key": "A Google API key appeared in a session",
    "secret-aws-access-key": "An AWS access key id appeared in a session",
    "secret-atlassian-token": "An Atlassian token appeared in a session",
    "secret-notion-token": "A Notion token appeared in a session",
    "secret-generic-token": "A secret_ shaped token appeared in a session",
    "secret-http-auth-header": "An Authorization header appeared in a session",
    "secret-url-credentials": "A user:password URL appeared in a session",
    "tunnel-hostname": "A live tunnel address appeared in a session",
    "permission-bypass-default": "Permission prompts are bypassed by default",
    "wildcard-allow": "A wildcard rule auto-approves every tool call",
    "broad-bash-allow": "A broad shell rule pre-approves a command family",
    "hook-curl-pipe-shell": "A hook pipes a download into a shell",
    "secret-in-settings-env": "A credential sits in settings env",
    "unreadable-config": "A config file could not be read",
    "plain-http-transport": "An MCP server talks over plain HTTP",
    "unpinned-package": "An MCP server runs an unpinned package",
    "inline-mcp-credential": "A credential is inlined in MCP config",
    "duplicate-mcp-name": "An MCP server name means different things per scope",
}

WHY: dict[str, str] = {
    "curl-pipe-shell": (
        "Piping a download straight into a shell runs code nobody read. Claude Code asks once per "
        "command, so an approved install script is a blind spot: the next version of it runs unread."
    ),
    "base64-decode-pipe-shell": (
        "Decoding base64 into a shell hides what runs from anyone reading the command. It is the "
        "shape of an obfuscated payload, whether or not this one was."
    ),
    "rm-rf-root": "A recursive delete on a root path removes far more than a repo. There is no undo.",
    "permission-bypass-flag": (
        "With permissions skipped, a nested Claude Code run approves its own tool calls. Every "
        "guard in your settings is off for that run."
    ),
    "sudo": "A command run as root can change the machine, not just the checkout.",
    "secret-anthropic-key": (
        "A key in a transcript is a key on disk in plain text, in every backup of that transcript, "
        "and in anything the transcript is shared with. If it is live, rotate it."
    ),
    "secret-github-token": "A GitHub token in a transcript can push, open PRs and read private repos as you.",
    "secret-github-pat": "A fine-grained token in a transcript acts as you on the repos it was scoped to.",
    "secret-slack-token": "A Slack token in a transcript can read and post as the app it belongs to.",
    "secret-slack-webhook": "A webhook URL is the whole credential: anyone holding it can post to that channel.",
    "secret-google-api-key": "A Google API key in a transcript bills your project until it is rotated.",
    "secret-aws-access-key": "An AWS key id in a transcript, with its secret, is the account. Rotate first, ask later.",
    "secret-atlassian-token": "An Atlassian token in a transcript reads and edits Jira and Confluence as you.",
    "secret-notion-token": "A Notion token in a transcript reads every page the integration can see.",
    "secret-sk-generic": (
        "sk- is the prefix many providers use. This is a shape, not a confirmed key: the replay shows "
        "whether it was a real credential or a placeholder in code."
    ),
    "secret-generic-token": "secret_ is a common token prefix; the replay shows whether this one was live.",
    "secret-http-auth-header": (
        "An Authorization header carries a bearer or basic credential. Pasted into a session it is "
        "stored in plain text until the transcript is deleted."
    ),
    "secret-url-credentials": (
        "user:password inside a URL is a credential in plain text. Package-index and webhook URLs "
        "often carry one; the replay shows which."
    ),
    "tunnel-hostname": "A quick-tunnel address is a public door to a local service; it expired with the tunnel.",
    "permission-bypass-default": (
        "Every tool call an agent makes runs without asking. One bad command in one session is enough."
    ),
    "wildcard-allow": "A wildcard allow rule approves anything that matches it, including what you have not seen yet.",
    "broad-bash-allow": (
        "Pre-approving a whole command family (rm, curl, sudo…) means the agent never asks before "
        "using it, whatever the arguments."
    ),
    "hook-curl-pipe-shell": "A hook that pipes a download into a shell runs remote code on every matching event.",
    "secret-in-settings-env": "A credential in settings.json sits in a file that is easy to share or commit.",
    "unreadable-config": "A config file the audit cannot parse cannot be checked; whatever it enables is unseen.",
    "plain-http-transport": "Tool traffic over plain HTTP, tokens included, travels unencrypted.",
    "unpinned-package": "@latest re-resolves on every start: a supply-chain change runs unreviewed.",
    "inline-mcp-credential": "A credential inlined in .mcp.json is committed with the repo and read by every clone.",
    "duplicate-mcp-name": "The same server name with different definitions means the effective one depends on cwd.",
}

_ROTATE_URLS: dict[str, tuple[str, str]] = {
    "secret-anthropic-key": ("Anthropic console", "https://console.anthropic.com/settings/keys"),
    "secret-github-token": ("GitHub tokens", "https://github.com/settings/tokens"),
    "secret-github-pat": ("GitHub tokens", "https://github.com/settings/tokens?type=beta"),
    "secret-slack-token": ("Slack apps", "https://api.slack.com/apps"),
    "secret-slack-webhook": ("Slack apps", "https://api.slack.com/apps"),
    "secret-google-api-key": ("Google Cloud credentials", "https://console.cloud.google.com/apis/credentials"),
    "secret-aws-access-key": ("AWS IAM", "https://console.aws.amazon.com/iam/home#/security_credentials"),
    "secret-atlassian-token": ("Atlassian tokens", "https://id.atlassian.com/manage-profile/security/api-tokens"),
    "secret-notion-token": ("Notion integrations", "https://www.notion.so/profile/integrations"),
}

_RISKY = ("curl-pipe-shell", "base64-decode-pipe-shell", "rm-rf-root", "permission-bypass-flag", "sudo")
_SETTINGS_EDITABLE = ("permission-bypass-default", "wildcard-allow", "broad-bash-allow")


def _mark_test_data() -> SecurityFix:
    return SecurityFix(
        id="mark-test-data",
        kind="dismiss",
        label="Mark as test data",
        detail="Sets every signal in this issue aside as fixture or example text.",
    )


def _dismiss() -> SecurityFix:
    return SecurityFix(id="dismiss", kind="dismiss", label="Dismiss with a reason", detail="Needs a reason.")


def fixes_for(finding: SecurityFinding, *, repo: str = "") -> tuple[SecurityFix, ...]:
    """The buttons for one finding, primary first."""
    if finding.verdict == "handled":
        return (SecurityFix(id="undo", kind="dismiss", label="Undo", detail="Bring the finding back."),)
    pattern, category = finding.pattern, finding.category
    fixes: list[SecurityFix] = []
    if category == "risky_tool" and pattern in _RISKY:
        fixes.append(
            SecurityFix(
                id="guard-hook",
                kind="write",
                label="Block this in Claude Code",
                target="~/.claude/settings.json",
                detail="Adds a PreToolUse guard that refuses this command family before it runs.",
                scope="user",
            )
        )
        if repo:
            fixes.append(
                SecurityFix(
                    id="guard-hook-pr",
                    kind="pr",
                    label=f"Open a PR to {Path(repo).name}",
                    target=repo,
                    detail="The same guard, committed under .claude/ so the whole team has it.",
                    scope="repo",
                )
            )
        fixes += [_mark_test_data(), _dismiss()]
    elif category == "secret":
        rotate = _ROTATE_URLS.get(pattern)
        if rotate and finding.verdict in ("needs-decision", "unsure"):
            fixes.append(
                SecurityFix(
                    id="rotate",
                    kind="link",
                    label="Rotate the key",
                    target=rotate[1],
                    detail=f"Opens {rotate[0]}. Mark it rotated when done.",
                )
            )
            fixes.append(
                SecurityFix(
                    id="mark-rotated",
                    kind="dismiss",
                    label="Mark as rotated",
                    detail="Records today's date as the rotation and sets the finding aside.",
                )
            )
            fixes.append(
                SecurityFix(
                    id="guard-hook",
                    kind="write",
                    label="Block keys in commands",
                    target="~/.claude/settings.json",
                    detail="Adds a PreToolUse guard that refuses a shell command carrying a live-shaped key.",
                    scope="user",
                )
            )
        fixes += [_mark_test_data(), _dismiss()]
    elif category == "settings":
        if pattern in _SETTINGS_EDITABLE:
            what = "the bypass default" if pattern == "permission-bypass-default" else "that one rule"
            fixes.append(
                SecurityFix(
                    id="settings-edit",
                    kind="write",
                    label="Fix the setting",
                    target=finding.location,
                    detail=f"Removes {what} from the file after writing a timestamped backup.",
                    scope="user",
                )
            )
        else:
            fixes.append(_manual(finding))
        fixes.append(_dismiss())
    elif category == "mcp":
        project = _project_scope(finding)
        if pattern == "inline-mcp-credential" and project:
            fixes.append(
                SecurityFix(
                    id="mcp-edit-pr",
                    kind="pr",
                    label=f"Open a PR to {Path(project).name}",
                    target=project,
                    detail="Replaces the inlined value in .mcp.json with an environment reference.",
                    scope="repo",
                )
            )
        else:
            fixes.append(_manual(finding))
        fixes.append(_dismiss())
    else:
        fixes.append(_dismiss())
    return tuple(fixes)


def _manual(finding: SecurityFinding) -> SecurityFix:
    return SecurityFix(
        id="manual",
        kind="manual",
        label="How to fix it",
        target=finding.location,
        detail=finding.remediation or "Review the file by hand.",
    )


def _project_scope(finding: SecurityFinding) -> str:
    for scope in finding.scopes:
        if scope.startswith("project:"):
            return scope[len("project:") :]
    return ""


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixOutcome:
    ok: bool = False
    fix_id: str = ""
    detail: str = ""
    pr_url: str = ""
    paths: tuple[str, ...] = ()
    consent_needed: str = ""  # a path the sandbox refused; the surface asks and retries
    handled_keys: tuple[str, ...] = field(default_factory=tuple)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class SettingsUnreadableError(ValueError):
    """A settings file exists but cannot be parsed; a fix must not write over it."""


def _read_json(path: Path) -> dict:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_settings_or_refuse(path: Path) -> dict:
    """The parsed settings, ``{}`` when the file is absent, SettingsUnreadableError when it is broken."""
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SettingsUnreadableError(f"{path.name} could not be parsed; fix it by hand first") from exc
    if not isinstance(parsed, dict):
        raise SettingsUnreadableError(f"{path.name} is not a JSON object; fix it by hand first")
    return parsed


def _backup(path: Path) -> Path:
    backup = path.with_name(f"{path.name}.bak-{_now_stamp()}")
    shutil.copy2(path, backup)
    return backup


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_settings_hook(settings: dict, *, event: str, matcher: str, command: str) -> bool:
    """Add one hook entry to a settings dict unless an equal one exists. True when changed."""
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return False
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("matcher", "")) != matcher:
            continue
        for hook in entry.get("hooks") or []:
            if isinstance(hook, dict) and hook.get("command") == command:
                return False
        entry.setdefault("hooks", []).append({"type": "command", "command": command})
        return True
    entries.append({"matcher": matcher, "hooks": [{"type": "command", "command": command}]})
    return True


def write_guard(claude_dir: Path, rule_ids: tuple[str, ...], *, command: str) -> tuple[list[Path], bool]:
    """Write/refresh the guard script, merge the rules, merge the settings hook.

    Returns ``(paths touched, anything changed)``. Idempotent: a second call
    with the same rules changes nothing. Raises SettingsUnreadableError rather than
    write over a settings file it cannot parse; an existing file is backed up
    before it is changed.
    """
    hooks_dir = claude_dir / "hooks"
    script = hooks_dir / GUARD_SCRIPT_NAME
    rules_path = hooks_dir / GUARD_RULES_NAME
    settings_path = claude_dir / "settings.json"
    touched: list[Path] = []
    changed = False
    hooks_dir.mkdir(parents=True, exist_ok=True)
    if not script.exists() or script.read_text(encoding="utf-8") != GUARD_SCRIPT:
        script.write_text(GUARD_SCRIPT, encoding="utf-8")
        script.chmod(0o755)
        touched.append(script)
        changed = True
    rules = _read_json(rules_path)
    current = rules.get("rules") if isinstance(rules.get("rules"), dict) else {}
    catalogue = guard_rules()
    merged = dict(current)
    for rule_id in rule_ids:
        if rule_id in catalogue and rule_id not in merged:
            merged[rule_id] = catalogue[rule_id]
    if merged != current:
        _write_json(rules_path, {"version": 1, "rules": merged})
        touched.append(rules_path)
        changed = True
    settings = _read_settings_or_refuse(settings_path)
    if merge_settings_hook(settings, event="PreToolUse", matcher="Bash", command=command):
        if settings_path.exists():
            touched.append(_backup(settings_path))
        _write_json(settings_path, settings)
        touched.append(settings_path)
        changed = True
    return touched, changed


def _consent(path: Path) -> str:
    from yeaboi import fs_policy

    return "" if fs_policy.request_consent(path, mode="write", context="security fix") else str(path)


def _apply_guard_user(finding: SecurityFinding) -> FixOutcome:
    from yeaboi.agentwatch.security_checks import _config_roots

    claude_dir, _json = _config_roots()
    refused = _consent(claude_dir / "settings.json")
    if refused:
        return FixOutcome(
            fix_id="guard-hook",
            detail=f"{claude_dir} is outside the allowed paths — allow it and try again",
            consent_needed=refused,
        )
    rule_ids = (finding.pattern,) if finding.category == "risky_tool" else (SECRET_IN_COMMAND,)
    try:
        touched, changed = write_guard(claude_dir, rule_ids, command=GUARD_COMMAND)
    except SettingsUnreadableError as exc:
        return FixOutcome(fix_id="guard-hook", detail=str(exc))
    detail = "Blocked. The guard runs before every Bash call from now on." if changed else "Already in place."
    return FixOutcome(ok=True, fix_id="guard-hook", detail=detail, paths=tuple(str(p) for p in touched))


def _edit_settings(finding: SecurityFinding) -> FixOutcome:
    path = Path(finding.location).expanduser()
    if not path.is_file():
        return FixOutcome(fix_id="settings-edit", detail=f"{path} is not there any more")
    refused = _consent(path)
    if refused:
        return FixOutcome(
            fix_id="settings-edit",
            detail=f"{path} is outside the allowed paths — allow it and try again",
            consent_needed=refused,
        )
    settings = _read_json(path)
    if not settings:
        return FixOutcome(fix_id="settings-edit", detail=f"{path.name} could not be parsed; fix it by hand")
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        return FixOutcome(ok=True, fix_id="settings-edit", detail="Already in place.")
    changed = False
    if finding.pattern == "permission-bypass-default":
        changed = permissions.pop("defaultMode", None) is not None
    else:
        allow = permissions.get("allow")
        if isinstance(allow, list) and finding.target in allow:
            permissions["allow"] = [rule for rule in allow if rule != finding.target]
            changed = True
    if not changed:
        return FixOutcome(ok=True, fix_id="settings-edit", detail="Already in place.")
    backup = _backup(path)
    _write_json(path, settings)
    return FixOutcome(
        ok=True,
        fix_id="settings-edit",
        detail=f"Fixed. The previous file is at {backup.name}.",
        paths=(str(path), str(backup)),
    )


def open_fix_pr(repo: str, writer, *, title: str, body: str, slug: str) -> FixOutcome:
    """Prepare a worktree, let ``writer(root)`` write files, commit, push, open a PR, clean up."""
    from yeaboi import fs_policy
    from yeaboi.ship import pipeline, setup, worktree
    from yeaboi.ship.engine import _new_run_id

    resolved, problem = setup.resolve_target(repo)
    if problem:
        return FixOutcome(detail=problem)
    if not fs_policy.request_consent(resolved, mode="write", context="security fix PR"):
        return FixOutcome(
            detail=f"{resolved} is outside the allowed paths — allow it and try again", consent_needed=resolved
        )
    run_id = _new_run_id(slug, "fix")
    try:
        record = worktree.prepare(run_id, resolved)
    except worktree.WorktreeError as exc:
        return FixOutcome(detail=str(exc))
    pushed = False
    try:
        written = writer(Path(record.path))
        if not written:
            return FixOutcome(ok=True, detail="Already in place — nothing to change.")
        worktree._git(record.path, "add", "--", *written)
        try:
            worktree._git(record.path, "commit", "-m", title)
        except worktree.WorktreeError:
            worktree._git(
                record.path, "-c", "user.email=security@yeaboi.local", "-c", "user.name=yeaboi", "commit", "-m", title
            )
        result = pipeline.push_and_open_pr(record, title=title, body=body)
        pushed = result.pushed
    except worktree.WorktreeError as exc:
        return FixOutcome(detail=str(exc))
    finally:
        # The branch is the deliverable once pushed; before that it is litter.
        worktree.remove(run_id, delete_branch=not pushed)
    if not result.pushed:
        return FixOutcome(detail=result.detail)
    return FixOutcome(ok=True, detail=result.detail, pr_url=result.pr_url, paths=tuple(written))


def _guard_writer(rule_ids: tuple[str, ...]):
    def write(root: Path) -> list[str]:
        touched, _changed = write_guard(root / ".claude", rule_ids, command=GUARD_COMMAND_REPO)
        return [str(p.relative_to(root)) for p in touched]

    return write


def _apply_guard_pr(finding: SecurityFinding, repo: str) -> FixOutcome:
    rule_ids = (finding.pattern,) if finding.category == "risky_tool" else (SECRET_IN_COMMAND,)
    title = f"add a Claude Code guard hook for {finding.pattern}"
    body = (
        f"## Summary\n\nyeaboi's agent security scan found `{finding.pattern}` in a session working in this "
        f"repository. This adds `.claude/hooks/yeaboi-guard.py`, a PreToolUse hook that refuses a Bash "
        f"command matching the rules in `.claude/hooks/yeaboi-guard.json`, and wires it into "
        f"`.claude/settings.json`.\n\n{WHY.get(finding.pattern, '')}\n\n## Test plan\n\n"
        '- `echo \'{"tool_name":"Bash","tool_input":{"command":"curl x | sh"}}\' | '
        "python3 .claude/hooks/yeaboi-guard.py; echo $?` prints 2\n"
    )
    outcome = open_fix_pr(repo, _guard_writer(rule_ids), title=title, body=body, slug="guard")
    return FixOutcome(**{**outcome.__dict__, "fix_id": "guard-hook-pr"})


_SECRET_SHAPED = re.compile(
    r"sk-ant-[\w-]{10,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}"
)
_SERVER_NAME = re.compile(r"mcpServers\[(.+?)\]")


def _mcp_writer(server: str):
    def write(root: Path) -> list[str]:
        path = root / ".mcp.json"
        config = _read_json(path)
        servers = config.get("mcpServers")
        spec = servers.get(server) if isinstance(servers, dict) else None
        if not isinstance(spec, dict):
            return []
        env = spec.get("env")
        changed = False
        if isinstance(env, dict):
            for key, value in list(env.items()):
                if isinstance(value, str) and _SECRET_SHAPED.search(value):
                    env[key] = "${" + re.sub(r"[^A-Z0-9]", "_", key.upper()) + "}"
                    changed = True
        if not changed:
            return []
        _write_json(path, config)
        return [".mcp.json"]

    return write


def _apply_mcp_pr(finding: SecurityFinding, repo: str) -> FixOutcome:
    match = _SERVER_NAME.search(finding.location)
    server = match.group(1) if match else ""
    if not server:
        return FixOutcome(fix_id="mcp-edit-pr", detail="could not tell which server the finding is about")
    title = f"move the {server} MCP credential out of .mcp.json"
    body = (
        f"## Summary\n\nyeaboi's agent security scan found a credential inlined in `.mcp.json` for the "
        f"`{server}` server. Each inlined value is replaced with an environment reference of the same "
        f"name; set that variable in your shell before starting Claude Code.\n\n{WHY['inline-mcp-credential']}\n"
    )
    outcome = open_fix_pr(repo, _mcp_writer(server), title=title, body=body, slug="mcp")
    return FixOutcome(**{**outcome.__dict__, "fix_id": "mcp-edit-pr"})


def apply_fix(
    key: str,
    fix_id: str,
    *,
    keys: tuple[str, ...] = (),
    reason: str = "",
    repo: str = "",
    db_path=None,
    today=None,
) -> FixOutcome:
    """Apply one fix to the finding behind ``key``; never raises.

    ``keys`` widens the dismissal to every finding of the same issue (the
    page's "Block it" answers the whole pattern, not one file). ``reason`` is
    required by ``dismiss`` and ignored by the others.
    """
    from yeaboi.agentwatch import dismissals
    from yeaboi.agentwatch.engine import _resolve_db_path, rebuild_security_report
    from yeaboi.agentwatch.store import AgentWatchStore

    logger.info("agent security fix: %s on %s", fix_id, key)
    report = rebuild_security_report(include_info=True, db_path=db_path, today=today, record=False)
    finding = next((f for f in report.findings if f.key == key), None)
    if finding is None:
        return FixOutcome(fix_id=fix_id, detail="that finding is not in the latest scan — re-run and try again")
    target_keys = tuple(dict.fromkeys((key, *keys)))
    if fix_id == "undo":
        restored = [k for k in target_keys if dismissals.undismiss(k)]
        return FixOutcome(ok=bool(restored), fix_id=fix_id, detail="Restored." if restored else "Nothing to undo.")
    fix = next((f for f in fixes_for(finding, repo=repo or _repo_hint(finding)) if f.id == fix_id), None)
    if fix is None:
        return FixOutcome(fix_id=fix_id, detail=f"{fix_id} does not apply to this finding")

    outcome: FixOutcome
    label = fix.label
    try:
        outcome = _dispatch(fix_id, finding, fix)
    except Exception as exc:  # noqa: BLE001 — a fix that blows up is an outcome, not a crash
        logger.warning("agent security fix: %s failed: %s", fix_id, exc)
        return FixOutcome(fix_id=fix_id, detail=f"Couldn't apply: {exc}")
    if outcome is not None:
        pass  # a writing fix already answered
    elif fix_id == "rotate":
        return FixOutcome(ok=True, fix_id=fix_id, detail=f"Open {fix.target} and rotate the key.", pr_url=fix.target)
    elif fix_id == "mark-rotated":
        outcome = FixOutcome(ok=True, fix_id=fix_id, detail="Marked as rotated.")
        label = f"rotated on {_today()}"
    elif fix_id == "mark-test-data":
        outcome = FixOutcome(ok=True, fix_id=fix_id, detail="Marked as test data.")
        label = TEST_DATA_REASON
    elif fix_id == "dismiss":
        if not reason.strip():
            return FixOutcome(fix_id=fix_id, detail="a dismissal needs a reason — say why this finding is expected")
        outcome = FixOutcome(ok=True, fix_id=fix_id, detail="Dismissed.")
        label = reason.strip()
    elif fix_id == "manual":
        return FixOutcome(ok=True, fix_id=fix_id, detail=fix.detail)
    else:
        return FixOutcome(fix_id=fix_id, detail=f"unknown fix {fix_id!r}")
    if not outcome.ok:
        return outcome
    why = label if fix.kind == "dismiss" else f"fixed: {label} ({_today()})"
    handled = []
    for k in target_keys:
        try:
            dismissals.dismiss(k, reason=why, by="yeaboi")
            handled.append(k)
        except ValueError as exc:
            logger.warning("agent security fix: could not record %s: %s", k, exc)
    if fix.kind != "dismiss":
        try:
            with AgentWatchStore(_resolve_db_path(db_path)) as store:
                store.record_fix(
                    fix_id=fix_id,
                    key=key,
                    pattern=finding.pattern,
                    kind=fix.kind,
                    target=fix.target,
                    outcome=outcome.detail,
                    pr_url=outcome.pr_url,
                )
        except Exception as exc:  # noqa: BLE001 — the audit row is best-effort
            logger.warning("agent security fix: could not record the fix: %s", exc)
    logger.info("agent security fix: %s applied to %d finding(s)", fix_id, len(handled))
    return FixOutcome(**{**outcome.__dict__, "handled_keys": tuple(handled)})


TEST_DATA_REASON = "test data: fixture or example text"


def _dispatch(fix_id: str, finding: SecurityFinding, fix: SecurityFix) -> FixOutcome | None:
    """The fixes that write or push; None for the ones that only record a decision."""
    if fix_id == "guard-hook":
        return _apply_guard_user(finding)
    if fix_id == "guard-hook-pr":
        return _apply_guard_pr(finding, fix.target)
    if fix_id == "settings-edit":
        return _edit_settings(finding)
    if fix_id == "mcp-edit-pr":
        return _apply_mcp_pr(finding, fix.target)
    return None


def _repo_hint(finding: SecurityFinding) -> str:
    """The repository a transcript finding's session worked in, if it is known."""
    from yeaboi.agentwatch.engine import _repo_for_finding

    return _repo_for_finding(finding)
