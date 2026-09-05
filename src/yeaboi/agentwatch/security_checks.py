"""Deterministic security checks over local agent configuration and transcripts.

Everything here is a pattern scan producing :class:`SecurityFinding` /
:class:`McpServerRecord` rows — an *indicator*, not a security audit (the beta
notice says so to the user). Two invariants:

1. **Never store matched content from a transcript.** A finding carries a
   pattern label, a file path and (where meaningful) a line number — never the
   secret it matched, the command it appeared in, or any prompt/code text from
   an agent session. That is the privacy boundary, and it is test-enforced.
   The classifier below *inspects* a matched span to decide a severity, inside
   the scan, and returns only a severity word.

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
import math
import re
from collections import Counter
from pathlib import Path

from yeaboi.agent.state import McpServerRecord, SecurityFinding

logger = logging.getLogger(__name__)

# Rules an agent-settings audit flags. Severity vocabulary matches the
# collector's risky-tool patterns: critical > high > medium > info.
_BYPASS_MODES = {"bypasspermissions", "dangerouslyskippermissions"}
_WILDCARD_ALLOW = re.compile(r"^(?:\*|Bash\(\*?\)|Bash\(\*[^)]*\))$")
_BROAD_BASH_ALLOW = re.compile(r"^Bash\((?:rm|curl|wget|sudo|sh|bash|eval|chmod)\b[^)]*\)$", re.IGNORECASE)
# A network verb pinned to an https host is a scoped rule, not a broad one:
# ``Bash(curl * https://api.example/*)`` pre-approves one endpoint. Only curl
# and wget qualify, only with a URL, and never with a pipe in the rule.
_SCOPED_BASH_ALLOW = re.compile(r"^Bash\((?:curl|wget)\s+[^)|]*https://[^)|]*\)$", re.IGNORECASE)
_NETWORK_PIPE_SHELL = re.compile(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")
_SECRET_SHAPED = re.compile(
    r"sk-ant-[\w-]{10,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}"
)
_UNPINNED_NPX = re.compile(r"@latest\b")

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}
_SEVERITY_ORDER = SEVERITY_ORDER

# ---------------------------------------------------------------------------
# Transcript secret classes — one row per redaction._TOKEN_PATTERNS entry
# ---------------------------------------------------------------------------

# Keyed on the EXACT pattern text from redaction._TOKEN_PATTERNS: (stable
# label, severity, generic). A pattern that changes there loses its row and
# falls back to a derived label at medium — visible, never silent. "generic"
# marks the loose shapes (any ``sk-``, any bearer token, any ``user:pass@``)
# that produce most false positives and therefore also get the entropy floor.
# No transcript match is ever ``critical``: a credential-shaped string in a
# session log is a signal to rotate, not a checked-in secret.
SECRET_CLASSES: dict[str, tuple[str, str, bool]] = {
    r"sk-ant-[\w-]{10,}": ("secret-anthropic-key", "high", False),
    r"sk-[A-Za-z0-9_-]{20,}": ("secret-sk-generic", "medium", True),
    r"gh[pousr]_[A-Za-z0-9]{20,}": ("secret-github-token", "high", False),
    r"github_pat_[A-Za-z0-9_]{20,}": ("secret-github-pat", "high", False),
    r"xox[abprs]-[\w-]{10,}": ("secret-slack-token", "high", False),
    r"AIza[\w-]{35}": ("secret-google-api-key", "high", False),
    r"AKIA[0-9A-Z]{16}": ("secret-aws-access-key", "high", False),
    r"ATATT[\w=+/-]{20,}": ("secret-atlassian-token", "high", False),
    r"ntn_[A-Za-z0-9]{20,}": ("secret-notion-token", "high", False),
    r"secret_[A-Za-z0-9]{30,}": ("secret-generic-token", "medium", True),
    r"hooks\.slack\.com/services/[\w/]+": ("secret-slack-webhook", "high", False),
    r"(?i:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}": ("secret-http-auth-header", "medium", True),
    r"(?<=://)[^/\s:@]+:[^/\s@]{4,}(?=@)": ("secret-url-credentials", "medium", True),
    r"https?://[a-z0-9][a-z0-9-]*\.trycloudflare\.com": ("tunnel-hostname", "info", False),
}

# Labels the previous release derived from the regex text, mapped to the
# stable ones, so rows already in agent_security_findings read as the same
# finding without a reparse.
LEGACY_LABELS: dict[str, str] = {
    "secret-sk-ant": "secret-anthropic-key",
    "secret-sk": "secret-sk-generic",
    "secret-gh": "secret-github-token",
    "secret-github_pat": "secret-github-pat",
    "secret-xox": "secret-slack-token",
    "secret-aiza": "secret-google-api-key",
    "secret-akia": "secret-aws-access-key",
    "secret-atatt": "secret-atlassian-token",
    "secret-ntn": "secret-notion-token",
    "secret-secret": "secret-generic-token",
    "secret-hooks.slack.com/services/": "secret-slack-webhook",
    "secret-(?i:bearer|b": "secret-http-auth-header",
    "secret-(?<=://)[^/\\": "secret-url-credentials",
    "secret-https": "tunnel-hostname",
}

_LABEL_SEVERITY: dict[str, str] = {label: severity for label, severity, _generic in SECRET_CLASSES.values()}
_GENERIC_LABELS: frozenset[str] = frozenset(label for label, _sev, generic in SECRET_CLASSES.values() if generic)

# Severities for the risky-shell patterns the collector scans tool_use commands
# for. ``sudo`` is worth a line, not a warning banner.
RISKY_TOOL_SEVERITY: dict[str, str] = {
    "curl-pipe-shell": "high",
    "base64-decode-pipe-shell": "high",
    "rm-rf-root": "high",
    "permission-bypass-flag": "high",
    "sudo": "medium",
}

# A matched span that says it is not real. Checked case-insensitively over the
# span only, which is why the words can be this ordinary.
_PLACEHOLDER = re.compile(
    r"example|redacted|placeholder|your[-_]?|xxx|dummy|fake|sample|planted|changeme|password|hunter2|<[^>]*>|"
    r"(.)\1{5,}|0123456789|abcdefgh",
    re.IGNORECASE,
)
# Below this many bits per character a token is not random — a real API key
# sits well above 4; "sk-ant-PLANTED000FAKE111" lands around 3.
_ENTROPY_FLOOR = 3.0


def canonical_label(label: str) -> str:
    """The stable label for a stored finding, mapping the derived legacy ones."""
    return LEGACY_LABELS.get(label, label)


def secret_label(pattern: str) -> str:
    """The stable label for one redaction pattern, derived when it has no row."""
    row = SECRET_CLASSES.get(pattern)
    if row:
        return row[0]
    head = re.match(r"[\w./-]+", pattern.replace("\\.", "."))
    literal = head.group(0) if head else pattern[:12]
    return f"secret-{literal.rstrip('-_').lower()}" if literal else "secret-token"


def shannon_entropy(text: str) -> float:
    """Bits per character of ``text`` (0.0 for the empty string)."""
    if not text:
        return 0.0
    counts = Counter(text)
    length = len(text)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def classify_secret(label: str, span: str) -> str:
    """The severity for one transcript secret match.

    Table severity by class, then two downgrades to ``info``: a span that
    announces itself as a placeholder, and — for the generic shapes only — a
    span without the randomness a real credential has. ``span`` is inspected
    and discarded; the caller stores only the word this returns.
    """
    severity = _LABEL_SEVERITY.get(label, "medium")
    if severity == "info":
        return severity
    if _PLACEHOLDER.search(span):
        return "info"
    if label in _GENERIC_LABELS and shannon_entropy(span) < _ENTROPY_FLOOR:
        return "info"
    return severity


def severity_for(category: str, pattern: str, stored: str) -> str:
    """The severity a stored finding renders at.

    Rows written before the recalibration carry ``critical`` for every secret
    match; those take the class severity. Anything else (including the
    ``info`` a placeholder was filed at) is kept as recorded.
    """
    if category == "secret" and stored == "critical":
        return _LABEL_SEVERITY.get(canonical_label(pattern), "medium")
    if category == "risky_tool" and stored == "critical":
        return RISKY_TOOL_SEVERITY.get(pattern, "medium")
    return stored or "info"


# ---------------------------------------------------------------------------
# Config audit
# ---------------------------------------------------------------------------


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
        elif _BROAD_BASH_ALLOW.match(rule_s) and not _SCOPED_BASH_ALLOW.match(rule_s):
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
    # $HOME is usually a Claude "project" too, so the global file would be
    # audited twice and every finding in it reported twice.
    for path in dict.fromkeys(p.expanduser().resolve() for p in paths):
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
                    scopes=(scope,),
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
                    scopes=(scope,),
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
                    scopes=(scope,),
                )
            )
        records.append(
            McpServerRecord(name=str(name), scope=scope, transport=transport, target=target, flags=tuple(flags))
        )
    return records, findings


def _merge_mcp_findings(records: list[McpServerRecord], findings: list[SecurityFinding]) -> list[SecurityFinding]:
    """One finding per (server name, target, pattern), carrying every scope.

    The same server spec declared globally and in five projects is one thing
    to fix, not six; the scopes ride along so the reader knows where.
    """
    target_of = {(r.scope, r.name): r.target for r in records}
    merged: dict[tuple[str, str, str], SecurityFinding] = {}
    for f in findings:
        scope = f.scopes[0] if f.scopes else ""
        name = f.location.split("mcpServers[", 1)[-1].rstrip("]")
        key = (name, target_of.get((scope, name), ""), f.pattern)
        prior = merged.get(key)
        if prior is None:
            merged[key] = SecurityFinding(
                **{**_as_kwargs(f), "location": f"mcpServers[{name}]", "scopes": tuple(f.scopes)}
            )
        else:
            merged[key] = SecurityFinding(**{**_as_kwargs(prior), "scopes": (*prior.scopes, *f.scopes)})
    return list(merged.values())


def _as_kwargs(finding: SecurityFinding) -> dict:
    from dataclasses import fields

    return {fld.name: getattr(finding, fld.name) for fld in fields(finding)}


def inventory_mcp() -> tuple[list[McpServerRecord], list[SecurityFinding]]:
    """Enumerate configured MCP servers (global + per-project) with risk flags."""
    _claude_dir, claude_json = _config_roots()
    top, note = _read_json(claude_json)
    records: list[McpServerRecord] = []
    findings: list[SecurityFinding] = [note] if note else []
    server_findings: list[SecurityFinding] = []
    if isinstance(top.get("mcpServers"), dict):
        recs, finds = _mcp_records(top["mcpServers"], scope="global")
        records.extend(recs)
        server_findings.extend(finds)
    projects = top.get("projects")
    if isinstance(projects, dict):
        for project_path, project_cfg in projects.items():
            if isinstance(project_cfg, dict) and isinstance(project_cfg.get("mcpServers"), dict):
                recs, finds = _mcp_records(project_cfg["mcpServers"], scope=f"project:{project_path}")
                records.extend(recs)
                server_findings.extend(finds)
    findings.extend(_merge_mcp_findings(records, server_findings))
    # The same name with *different* specs in several scopes is worth a look
    # (which one wins depends on cwd); the same spec repeated is redundancy,
    # not ambiguity, and not a finding.
    specs: dict[str, set[str]] = {}
    for record in records:
        specs.setdefault(record.name, set()).add(f"{record.transport} {record.target}")
    for name, variants in specs.items():
        if len(variants) > 1:
            findings.append(
                SecurityFinding(
                    severity="info",
                    category="mcp",
                    title="MCP server name defined differently in several scopes",
                    location=f"mcpServers[{name}]",
                    pattern="duplicate-mcp-name",
                    detail=f"{len(variants)} different definitions; the effective one depends on the working directory",
                )
            )
    return records, findings


# ---------------------------------------------------------------------------
# Ranking, posture, keys
# ---------------------------------------------------------------------------


def finding_key(finding: SecurityFinding) -> str:
    """The aggregation + dismissal key: category:pattern:location."""
    return f"{finding.category}:{finding.pattern}:{finding.location}"


def rank_findings(findings: list[SecurityFinding]) -> tuple[SecurityFinding, ...]:
    """Deterministic ordering: severity, then category, then location."""
    return tuple(sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.category, f.location, f.pattern)))


def compute_posture(findings: tuple[SecurityFinding, ...]) -> str:
    """good / needs-attention / at-risk from the worst finding present.

    ``medium`` alone is good-with-a-note: a scoped allow rule or an unpinned
    package is something to tidy, and a posture word that flips over it
    trains the reader to ignore the word. Only ``high`` earns needs-attention
    and only ``critical`` (a bypass default) earns at-risk.
    """
    severities = {f.severity for f in findings}
    if "critical" in severities:
        return "at-risk"
    if "high" in severities:
        return "needs-attention"
    return "good"


def posture_reason(findings: tuple[SecurityFinding, ...]) -> str:
    """One sentence saying what the posture word rests on."""
    counts = Counter(f.severity for f in findings)
    if counts.get("critical"):
        worst = [f for f in findings if f.severity == "critical"]
        return f"at-risk because of {len(worst)} critical finding(s): " + ", ".join(sorted({f.pattern for f in worst}))
    if counts.get("high"):
        worst = [f for f in findings if f.severity == "high"]
        files = {f.location for f in worst}
        return f"needs attention: {len(worst)} high-severity finding(s) across {len(files)} location(s)"
    if counts.get("medium"):
        return f"good — {counts['medium']} medium finding(s) to tidy when convenient"
    return "good — no known risk pattern matched"
