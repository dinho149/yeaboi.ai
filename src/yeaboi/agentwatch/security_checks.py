"""Deterministic security checks over local agent configuration.

Everything here is a pattern scan producing :class:`SecurityFinding` /
:class:`McpServerRecord` rows — an *indicator*, not a security audit (the beta
notice says so to the user). Two invariants:

1. **Never store matched content from a transcript.** A finding carries a
   pattern label, a file path and (where meaningful) a line number — never the
   secret it matched, the command it appeared in, or any prompt/code text from
   an agent session. That is the privacy boundary, and it is test-enforced.

   ``detail`` is the one field that may quote a *config* value — the permission
   mode, the allow rule — because a finding that says "an allow rule
   auto-approves bash" without naming the rule cannot be acted on, and the
   user's own settings file is not session content. Never widen this to a
   transcript-derived value.
2. **Never raise.** Unreadable or malformed config contributes a note-level
   finding, not a crash — a security page that dies on a corrupt JSON file
   would hide every other finding.

Kept out of ``engine.py`` deliberately: the surface-parity discovery rule
treats every public function in an engine module as a registered entry point,
and these are internals.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from yeaboi.agent.state import McpServerRecord, SecurityFinding

logger = logging.getLogger(__name__)

# Rules an agent-settings audit flags. Severity vocabulary matches the
# collector's risky-tool patterns: critical > high > medium > info.
_BYPASS_MODES = {"bypasspermissions", "dangerouslyskippermissions"}
_WILDCARD_ALLOW = re.compile(r"^(?:\*|Bash\(\*?\)|Bash\(\*[^)]*\))$")
_BROAD_BASH_ALLOW = re.compile(r"^Bash\((?:rm|curl|wget|sudo|sh|bash|eval|chmod)\b[^)]*\)$", re.IGNORECASE)
_NETWORK_PIPE_SHELL = re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")
_SECRET_SHAPED = re.compile(
    r"sk-ant-[\w-]{10,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}"
)
_UNPINNED_NPX = re.compile(r"@latest\b")


def _config_roots() -> tuple[Path, Path]:
    """(the ~/.claude dir, the ~/.claude.json file) — overridable in tests."""
    home = Path.home()
    return home / ".claude", home / ".claude.json"


def _read_json(path: Path) -> tuple[dict, SecurityFinding | None]:
    """Parse one JSON config; a failure becomes an info finding, never a raise."""
    try:
        if not path.exists():
            return {}, None
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return (parsed, None) if isinstance(parsed, dict) else ({}, None)
    except (OSError, ValueError) as exc:
        logger.warning("agent security: cannot read %s: %s", path, exc)
        return {}, SecurityFinding(
            severity="info",
            category="settings",
            title="Unreadable agent config",
            location=str(path),
            pattern="unreadable-config",
            detail=f"could not parse: {exc.__class__.__name__}",
            remediation="Fix or remove the file so the audit can read it.",
        )


def _audit_one_settings(path: Path) -> list[SecurityFinding]:
    """Flag risky knobs in one Claude Code settings.json file."""
    settings, note = _read_json(path)
    findings: list[SecurityFinding] = [note] if note else []
    if not settings:
        return findings

    permissions = settings.get("permissions") or {}
    default_mode = str(permissions.get("defaultMode", "")).lower()
    if default_mode in _BYPASS_MODES:
        findings.append(
            SecurityFinding(
                severity="critical",
                category="settings",
                title="Permission prompts bypassed by default",
                location=str(path),
                pattern="permission-bypass-default",
                detail=f"permissions.defaultMode is {permissions.get('defaultMode')!r}",
                remediation="Remove the bypass default; approve tools per session instead.",
            )
        )
    for rule in permissions.get("allow") or []:
        rule_s = str(rule)
        if _WILDCARD_ALLOW.match(rule_s):
            findings.append(
                SecurityFinding(
                    severity="high",
                    category="settings",
                    title="Wildcard tool allow rule",
                    location=str(path),
                    pattern="wildcard-allow",
                    detail=f"allow rule {rule_s!r} auto-approves everything it matches",
                    remediation="Replace the wildcard with the specific commands you trust.",
                )
            )
        elif _BROAD_BASH_ALLOW.match(rule_s):
            findings.append(
                SecurityFinding(
                    severity="medium",
                    category="settings",
                    title="Broad shell allow rule",
                    location=str(path),
                    pattern="broad-bash-allow",
                    detail=f"allow rule {rule_s!r} pre-approves a destructive/network command family",
                    remediation="Narrow the rule to exact commands and arguments.",
                )
            )

    # Hooks run arbitrary shell on the agent's lifecycle — a network-pipe-shell
    # there executes remote code on every matching event.
    hooks_blob = json.dumps(settings.get("hooks", {}))
    if _NETWORK_PIPE_SHELL.search(hooks_blob):
        findings.append(
            SecurityFinding(
                severity="high",
                category="settings",
                title="Hook pipes a network download into a shell",
                location=str(path),
                pattern="hook-curl-pipe-shell",
                remediation="Vendor the script locally instead of piping curl/wget into sh.",
            )
        )

    for key, value in (settings.get("env") or {}).items():
        if isinstance(value, str) and _SECRET_SHAPED.search(value):
            findings.append(
                SecurityFinding(
                    severity="high",
                    category="settings",
                    title="Secret-shaped value in settings env",
                    location=str(path),
                    pattern="secret-in-settings-env",
                    detail=f"env key {key!r} holds a credential-shaped value",
                    remediation="Move the credential to a secret manager or shell profile.",
                )
            )
    return findings


def audit_settings() -> list[SecurityFinding]:
    """Audit the global + local + per-project Claude Code settings files."""
    claude_dir, claude_json = _config_roots()
    paths = [claude_dir / "settings.json", claude_dir / "settings.local.json"]
    top, _ = _read_json(claude_json)
    for project_path in (top.get("projects") or {}) if isinstance(top.get("projects"), dict) else {}:
        paths.append(Path(project_path) / ".claude" / "settings.json")
        paths.append(Path(project_path) / ".claude" / "settings.local.json")
    findings: list[SecurityFinding] = []
    for path in paths:
        findings.extend(_audit_one_settings(path))
    return findings


def _mcp_records(servers: dict, *, scope: str) -> tuple[list[McpServerRecord], list[SecurityFinding]]:
    records: list[McpServerRecord] = []
    findings: list[SecurityFinding] = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        url = str(spec.get("url", "") or "")
        command = str(spec.get("command", "") or "")
        args = [str(a) for a in (spec.get("args") or [])]
        transport = str(spec.get("type", "") or ("http" if url else "stdio"))
        target = url or " ".join([command, *args]).strip()
        flags: list[str] = []
        if url.startswith("http://"):
            flags.append("plain-http")
            findings.append(
                SecurityFinding(
                    severity="medium",
                    category="mcp",
                    title="MCP server over plain HTTP",
                    location=f"{scope} mcpServers[{name}]",
                    pattern="plain-http-transport",
                    detail="tool traffic (and any tokens in it) travels unencrypted",
                    remediation="Use https:// (or a local stdio server).",
                )
            )
        command_blob = " ".join([command, *args])
        if _UNPINNED_NPX.search(command_blob):
            flags.append("unpinned-package")
            findings.append(
                SecurityFinding(
                    severity="medium",
                    category="mcp",
                    title="MCP server runs an unpinned package",
                    location=f"{scope} mcpServers[{name}]",
                    pattern="unpinned-package",
                    detail="@latest re-resolves on every start — a supply-chain change runs unreviewed",
                    remediation="Pin the package to an exact version.",
                )
            )
        env_blob = json.dumps(spec.get("env") or {})
        if _SECRET_SHAPED.search(env_blob):
            flags.append("inline-credential")
            findings.append(
                SecurityFinding(
                    severity="medium",
                    category="mcp",
                    title="Credential inlined in MCP config",
                    location=f"{scope} mcpServers[{name}]",
                    pattern="inline-mcp-credential",
                    remediation="Reference the credential from the environment instead of the config file.",
                )
            )
        records.append(
            McpServerRecord(name=str(name), scope=scope, transport=transport, target=target, flags=tuple(flags))
        )
    return records, findings


def inventory_mcp() -> tuple[list[McpServerRecord], list[SecurityFinding]]:
    """Enumerate configured MCP servers (global + per-project) with risk flags."""
    _claude_dir, claude_json = _config_roots()
    top, note = _read_json(claude_json)
    records: list[McpServerRecord] = []
    findings: list[SecurityFinding] = [note] if note else []
    if isinstance(top.get("mcpServers"), dict):
        recs, finds = _mcp_records(top["mcpServers"], scope="global")
        records.extend(recs)
        findings.extend(finds)
    projects = top.get("projects")
    if isinstance(projects, dict):
        for project_path, project_cfg in projects.items():
            if isinstance(project_cfg, dict) and isinstance(project_cfg.get("mcpServers"), dict):
                recs, finds = _mcp_records(project_cfg["mcpServers"], scope=f"project:{project_path}")
                records.extend(recs)
                findings.extend(finds)
    # The same server name in several scopes is worth a look (which one wins
    # depends on cwd), but it is informational, not a vulnerability.
    seen: dict[str, int] = {}
    for record in records:
        seen[record.name] = seen.get(record.name, 0) + 1
    for name, count in seen.items():
        if count > 1:
            findings.append(
                SecurityFinding(
                    severity="info",
                    category="mcp",
                    title="MCP server name defined in multiple scopes",
                    location=f"mcpServers[{name}]",
                    pattern="duplicate-mcp-name",
                    detail=f"defined {count} times; the effective one depends on the working directory",
                )
            )
    return records, findings


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}


def rank_findings(findings: list[SecurityFinding]) -> tuple[SecurityFinding, ...]:
    """Deterministic ordering: severity, then category, then location."""
    return tuple(
        sorted(findings, key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.category, f.location, f.pattern))
    )


def compute_posture(findings: tuple[SecurityFinding, ...]) -> str:
    """good / needs-attention / at-risk from the worst finding present.

    ``medium`` counts as needs-attention, not good: the posture line renders
    directly above the table of findings, so reporting "good" while listing two
    medium findings reads as a contradiction and quietly trains the reader to
    ignore the word. Only ``info`` (and nothing at all) is good.
    """
    severities = {f.severity for f in findings}
    if "critical" in severities:
        return "at-risk"
    if "high" in severities or "medium" in severities:
        return "needs-attention"
    return "good"
