"""The system check: which optional features are ready on this machine.

The app itself needs none of this — the desktop build ships its own Python and
the terminal app runs anywhere Python 3.10 does. Every row here is an
*optional* capability (local models, dictation, board sharing, …) and the
check says whether its prerequisite is present and, when it is not, what would
make it so.

Offline by policy: every probe is a filesystem, PATH, or config read, or a
loopback-only socket. This module never opens a connection to a non-loopback
host and never calls :func:`yeaboi.retro.tunnel.ensure_cloudflared`, which
downloads ~38 MB on first use — a health check that causes egress would
falsify the privacy page that links to it. ``tests/unit/test_system_check.py``
enforces both.

Aggregates existing probes rather than re-deciding anything: each check wraps
the same function its feature already trusts, so the doctor and the feature
can never disagree.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

# One loopback HTTP probe (the Ollama tags endpoint) — bounded so the page
# opening never stalls on a wedged server.
_PROBE_TIMEOUT = 2

# Below this much free space under ~/.yeaboi, local models and exports start
# failing in confusing ways — say so before they do.
_LOW_DISK_BYTES = 1_000_000_000

# The floor pyproject.toml declares; below it nothing here is expected to work.
_PYTHON_FLOOR = (3, 10)

_STATUSES = ("ok", "missing", "unsupported", "unknown")

# The areas the checks group under, in render order. Surfaces render these titles
# and map their own icons onto the keys — a glyph is presentation and never
# crosses the wire.
CHECK_CATEGORIES: tuple[dict, ...] = (
    {"key": "ai", "title": "AI & models", "blurb": "The provider a mode talks to, and the local stack"},
    {"key": "integrations", "title": "Integrations", "blurb": "Trackers and channels you have given yeaboi a key to"},
    {"key": "tools", "title": "Tools on PATH", "blurb": "Programs yeaboi shells out to"},
    {"key": "packages", "title": "Packages & extras", "blurb": "Optional installs that unlock a feature"},
    {"key": "machine", "title": "This machine", "blurb": "Room to work and a runtime to work in"},
)

_CATEGORY_KEYS = tuple(category["key"] for category in CHECK_CATEGORIES)


@dataclass(frozen=True)
class CheckResult:
    """One prerequisite's verdict, worded for a person."""

    key: str  # stable id, e.g. "ollama-server"
    label: str  # "Local model server (Ollama)"
    status: str  # one of _STATUSES
    detail: str = ""  # what was found: "3 models pulled" / "not on PATH"
    hint: str = ""  # what would fix it: "install from ollama.com"
    feature: str = ""  # what it unlocks: "Fully local AI", "Board sharing", …
    category: str = ""  # one of _CATEGORY_KEYS — the section it renders under


@dataclass(frozen=True)
class SystemReport:
    """Every check, plus the one-line summary the page headers render."""

    checks: tuple[CheckResult, ...]

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "ok")

    @property
    def summary(self) -> str:
        return f"{self.ok_count} of {len(self.checks)} checks ready — almost all of them are optional"

    def by_category(self) -> tuple[tuple[dict, tuple[CheckResult, ...]], ...]:
        """The checks grouped for rendering: ``(category, rows)`` in declared order.

        Each category dict carries ``ok``/``total`` beside its key and title. Empty
        categories are dropped; a row with an unrecognised category falls into the
        last one.
        """
        buckets: dict[str, list[CheckResult]] = {key: [] for key in _CATEGORY_KEYS}
        for check in self.checks:
            buckets[check.category if check.category in buckets else _CATEGORY_KEYS[-1]].append(check)
        grouped = []
        for category in CHECK_CATEGORIES:
            rows = tuple(buckets[category["key"]])
            if not rows:
                continue
            ok = sum(1 for row in rows if row.status == "ok")
            grouped.append(({**category, "ok": ok, "total": len(rows)}, rows))
        return tuple(grouped)


def _check_provider() -> CheckResult:
    from yeaboi.config import get_llm_provider, is_llm_configured

    ok, message = is_llm_configured()
    provider = get_llm_provider()
    if ok:
        detail = "Ollama — fully local, no credentials needed" if provider == "ollama" else f"{provider} configured"
        return CheckResult("provider", "AI provider", "ok", detail=detail, feature="Every mode")
    return CheckResult(
        "provider",
        "AI provider",
        "missing",
        detail=message,
        hint="Add a credential in Settings ▸ Credentials, or pick Ollama for a local model",
        feature="Every mode",
    )


def _check_ollama_installed() -> CheckResult:
    from yeaboi.ollama_control import is_ollama_installed

    if is_ollama_installed():
        return CheckResult("ollama-installed", "Ollama", "ok", detail="on PATH", feature="Fully local AI")
    return CheckResult(
        "ollama-installed",
        "Ollama",
        "missing",
        detail="not on PATH",
        hint="Install from ollama.com to run models on this machine",
        feature="Fully local AI",
    )


def _check_ollama_server() -> CheckResult:
    from yeaboi.config import get_ollama_base_url
    from yeaboi.ollama_control import _is_localhost

    base = get_ollama_base_url()
    if not _is_localhost(base):
        # A remote base URL is the user's own arrangement — probing it from
        # here would be exactly the egress this module promises not to make.
        return CheckResult(
            "ollama-server",
            "Local model server (Ollama)",
            "unknown",
            detail=f"base URL is not this machine ({base}) — not probed",
            feature="Fully local AI",
        )
    import httpx

    try:
        response = httpx.get(f"{base}/api/tags", timeout=_PROBE_TIMEOUT)
        models = response.json().get("models", []) if response.status_code == 200 else None
    except Exception:
        models = None
    if models is None:
        return CheckResult(
            "ollama-server",
            "Local model server (Ollama)",
            "missing",
            detail="not answering",
            hint="Start it with `ollama serve` (or open the Ollama app)",
            feature="Fully local AI",
        )
    count = len(models)
    detail = f"{count} model{'s'[: count != 1]} pulled" if count else "running, no models pulled yet"
    hint = "" if count else "Pull one with `ollama pull qwen3:8b`"
    return CheckResult(
        "ollama-server", "Local model server (Ollama)", "ok", detail=detail, hint=hint, feature="Fully local AI"
    )


def _check_voice() -> CheckResult:
    from yeaboi.voice import unsupported_blocker, voice_state

    state = voice_state()
    if state == "ready":
        return CheckResult("voice", "Dictation", "ok", detail="on-device transcription ready", feature="Dictation")
    if state == "unsupported":
        return CheckResult("voice", "Dictation", "unsupported", detail=unsupported_blocker(), feature="Dictation")
    hint = (
        "yeaboi offers the install the first time you press the mic"
        if state == "installable"
        else "Re-enable the install offer in Settings ▸ System ▸ Voice"
    )
    return CheckResult(
        "voice", "Dictation", "missing", detail=f"not installed ({state})", hint=hint, feature="Dictation"
    )


def _check_music() -> CheckResult:
    from yeaboi.music import is_music_available

    ok, reason = is_music_available()
    # The terminal's player only: the desktop app plays the same stations
    # through an <audio> element and never needs the binary.
    feature = "Background music in the terminal"
    if ok:
        return CheckResult("music", "Music (ffplay)", "ok", detail="ffplay on PATH", feature=feature)
    return CheckResult(
        "music",
        "Music (ffplay)",
        "missing",
        detail="ffplay not on PATH",
        hint=f"{reason} — the desktop app plays radio without it",
        feature=feature,
    )


def _check_charts() -> CheckResult:
    from yeaboi.charts import charts_available

    if charts_available():
        return CheckResult(
            "charts", "Charts (matplotlib)", "ok", detail="charts extra installed", feature="Report charts"
        )
    return CheckResult(
        "charts",
        "Charts (matplotlib)",
        "missing",
        detail="matplotlib not importable",
        hint="pip install 'yeaboi[charts]'",
        feature="Report charts",
    )


def _check_cloudflared() -> CheckResult:
    from yeaboi.config import tunnels_disabled
    from yeaboi.retro.tunnel import cloudflared_cached

    feature = "Board sharing"
    note = " (sharing is switched off — YEABOI_NO_TUNNEL)" if tunnels_disabled() else ""
    override = os.getenv("CLOUDFLARED_PATH", "")
    if override and os.path.exists(override):
        return CheckResult(
            "cloudflared", "Tunnel binary (cloudflared)", "ok", detail=f"CLOUDFLARED_PATH{note}", feature=feature
        )
    if shutil.which("cloudflared"):
        return CheckResult("cloudflared", "Tunnel binary (cloudflared)", "ok", detail=f"on PATH{note}", feature=feature)
    if cloudflared_cached().exists():
        return CheckResult(
            "cloudflared", "Tunnel binary (cloudflared)", "ok", detail=f"cached copy{note}", feature=feature
        )
    return CheckResult(
        "cloudflared",
        "Tunnel binary (cloudflared)",
        "missing",
        detail=f"not present yet{note}",
        hint="Downloaded automatically (~38 MB, checksum-pinned) the first time a board is shared",
        feature=feature,
    )


def _check_access() -> CheckResult:
    from yeaboi.config import share_mode
    from yeaboi.sharing.access_setup import find_cert, jwt_installed, missing_config_keys

    feature = "Private board sharing (Cloudflare Access)"
    if share_mode() != "access":
        return CheckResult(
            "access",
            "Cloudflare Access",
            "ok",
            detail="not in use — boards share over quick tunnels",
            feature=feature,
        )
    missing = missing_config_keys()
    cert = find_cert()
    jwt = jwt_installed()
    if cert and jwt and not missing:
        return CheckResult("access", "Cloudflare Access", "ok", detail="logged in and configured", feature=feature)
    problems = []
    if not cert:
        problems.append("not logged in")
    if not jwt:
        problems.append("access extra not installed")
    if missing:
        problems.append(f"{len(missing)} config key{'s'[: len(missing) != 1]} unset")
    return CheckResult(
        "access",
        "Cloudflare Access",
        "missing",
        detail="; ".join(problems),
        hint="Settings ▸ Sharing walks through the remaining steps",
        feature=feature,
    )


def _check_coding_agent() -> CheckResult:
    from yeaboi.claude_auth import setup_token_available
    from yeaboi.ship.driver import ClaudeCodeDriver

    usable, detail = ClaudeCodeDriver().available()
    if usable:
        extra = "" if setup_token_available() else " (setup-token not available)"
        return CheckResult(
            "coding-agent", "Coding agent (Claude Code)", "ok", detail=detail + extra, feature="Ship mode"
        )
    return CheckResult(
        "coding-agent",
        "Coding agent (Claude Code)",
        "missing",
        detail=detail,
        hint="Install Claude Code to let Ship drive stories to pull requests",
        feature="Ship mode",
    )


def _check_git() -> CheckResult:
    if shutil.which("git"):
        return CheckResult("git", "Git", "ok", detail="on PATH", feature="Ship mode, codebase tools")
    return CheckResult(
        "git",
        "Git",
        "missing",
        detail="not on PATH",
        hint="Install git for Ship mode and local repository tools",
        feature="Ship mode, codebase tools",
    )


def _check_disk() -> CheckResult:
    from yeaboi.paths import ROOT_DIR

    probe = ROOT_DIR if ROOT_DIR.exists() else ROOT_DIR.parent
    free = shutil.disk_usage(probe).free
    detail = f"{free / 1_000_000_000:.1f} GB free beside ~/.yeaboi"
    if free >= _LOW_DISK_BYTES:
        return CheckResult("disk", "Disk space", "ok", detail=detail, feature="Local models, exports")
    return CheckResult(
        "disk",
        "Disk space",
        "missing",
        detail=detail,
        hint="Local models and exports need room — free up some space",
        feature="Local models, exports",
    )


def _check_data_dir() -> CheckResult:
    """Whether the data home exists and is writable."""
    from yeaboi.paths import ROOT_DIR

    feature = "Sessions, boards, reports"
    if ROOT_DIR.exists() and os.access(ROOT_DIR, os.W_OK):
        return CheckResult("data-dir", "Data directory", "ok", detail=str(ROOT_DIR), feature=feature)
    problem = "does not exist yet" if not ROOT_DIR.exists() else "is not writable"
    return CheckResult(
        "data-dir",
        "Data directory",
        "missing",
        detail=f"{ROOT_DIR} {problem}",
        hint="Point YEABOI_HOME somewhere writable (Settings ▸ System ▸ Storage)",
        feature=feature,
    )


def _check_python() -> CheckResult:
    version = ".".join(str(part) for part in sys.version_info[:3])
    feature = "Every mode"
    if sys.version_info >= _PYTHON_FLOOR:
        return CheckResult("python", "Python runtime", "ok", detail=f"{version} ({sys.executable})", feature=feature)
    floor = ".".join(str(part) for part in _PYTHON_FLOOR)
    return CheckResult(
        "python",
        "Python runtime",
        "unsupported",
        detail=f"{version} is below the {floor} floor",
        hint=f"Run yeaboi on Python {floor} or newer",
        feature=feature,
    )


# One row per tracker or channel yeaboi can be given a key to. ``parts`` reads
# through the same ``config`` getters the integration itself calls, so a
# credential's fallbacks (Confluence reusing the Jira account, say) are decided
# in one place and the doctor cannot disagree with the feature. Every getter is
# an environment read: presence, never a live call.
_INTEGRATIONS: tuple[dict, ...] = (
    {
        "key": "github",
        "label": "GitHub",
        "feature": "Ship mode, standup, codebase tools",
        "section": "GitHub",
        "parts": lambda c: (("token", c.get_github_token()),),
    },
    {
        "key": "jira",
        "label": "Jira",
        "feature": "Tracker sync",
        "section": "Jira",
        "parts": lambda c: (
            ("base URL", c.get_jira_base_url()),
            ("email", c.get_jira_email()),
            ("API token", c.get_jira_token()),
        ),
    },
    {
        "key": "azure",
        "label": "Azure DevOps",
        "feature": "Tracker sync",
        "section": "Azure",
        "parts": lambda c: (
            ("org URL", c.get_azure_devops_org_url()),
            ("PAT", c.get_azure_devops_token()),
        ),
    },
    {
        "key": "confluence",
        "label": "Confluence",
        "feature": "Docs export",
        "section": "Jira",
        "parts": lambda c: (
            ("base URL", c.get_confluence_base_url()),
            ("email", c.get_confluence_email()),
            ("API token", c.get_confluence_token()),
            ("space key", c.get_confluence_space_key()),
        ),
    },
    {
        "key": "notion",
        "label": "Notion",
        "feature": "Docs export",
        "section": "Notion",
        "parts": lambda c: (("token", c.get_notion_token()),),
    },
    {
        "key": "linear",
        "label": "Linear",
        "feature": "Tracker sync",
        "section": "Integrations",
        "parts": lambda c: (("API key", c.get_linear_api_key()),),
    },
    {
        "key": "trello",
        "label": "Trello",
        "feature": "Tracker sync",
        "section": "Integrations",
        "parts": lambda c: (("API key", c.get_trello_api_key()), ("token", c.get_trello_token())),
    },
    {
        "key": "slack",
        "label": "Slack",
        "feature": "Ceremony delivery",
        "section": "Slack",
        # Either credential is enough: a webhook posts, a bot token also reads.
        "parts": lambda c: (("webhook URL or bot token", c.get_slack_webhook_url() or c.get_slack_bot_token()),),
    },
)


def _check_integration(entry: dict) -> CheckResult:
    """Whether an integration is configured — a pure environment read."""
    from yeaboi import config

    parts = entry["parts"](config)
    missing = [what for what, value in parts if not (value or "").strip()]
    if not missing:
        return CheckResult(entry["key"], entry["label"], "ok", detail="configured", feature=entry["feature"])
    complete = len(missing) == len(parts)
    detail = "not configured" if complete else f"missing {', '.join(missing)}"
    return CheckResult(
        entry["key"],
        entry["label"],
        "missing",
        detail=detail,
        hint=f"Settings ▸ {entry['section']} — add the {'credentials' if complete else 'rest'}",
        feature=entry["feature"],
    )


def _integration_probes() -> tuple[Callable[[], CheckResult], ...]:
    """One zero-argument probe per ``_INTEGRATIONS`` row, named for logging."""
    probes = []
    for entry in _INTEGRATIONS:
        probe = functools.partial(_check_integration, entry)
        probe.__name__ = f"_check_{entry['key']}"  # type: ignore[attr-defined]
        probes.append(probe)
    return tuple(probes)


# Every probe, paired with the section it renders under. Order inside a category
# is presentation order; the categories themselves order by CHECK_CATEGORIES.
_CHECKS: tuple[tuple[str, Callable[[], CheckResult]], ...] = (
    ("ai", _check_provider),
    ("ai", _check_ollama_installed),
    ("ai", _check_ollama_server),
    *(("integrations", probe) for probe in _integration_probes()),
    ("tools", _check_git),
    ("tools", _check_coding_agent),
    ("tools", _check_cloudflared),
    ("tools", _check_music),
    ("packages", _check_charts),
    ("packages", _check_voice),
    ("packages", _check_access),
    ("machine", _check_data_dir),
    ("machine", _check_disk),
    ("machine", _check_python),
)


def run_system_check() -> SystemReport:
    """Run every probe; a crashing probe reports ``unknown``, never raises.

    Categories are stamped here rather than repeated in every ``CheckResult``, so
    ``_CHECKS`` is the single place that says which section a row belongs to.
    """
    results = []
    for category, probe in _CHECKS:
        try:
            results.append(replace(probe(), category=category))
        except Exception:
            key = probe.__name__.removeprefix("_check_").replace("_", "-")
            logger.warning("system check: %s probe failed", key, exc_info=True)
            results.append(
                CheckResult(key, key.replace("-", " ").title(), "unknown", detail="probe failed", category=category)
            )
    report = SystemReport(checks=tuple(results))
    logger.info("system check: %d/%d ready", report.ok_count, len(report.checks))
    return report
