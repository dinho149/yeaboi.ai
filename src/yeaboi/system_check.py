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

import logging
import os
import shutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# One loopback HTTP probe (the Ollama tags endpoint) — bounded so the page
# opening never stalls on a wedged server.
_PROBE_TIMEOUT = 2

# Below this much free space under ~/.yeaboi, local models and exports start
# failing in confusing ways — say so before they do.
_LOW_DISK_BYTES = 1_000_000_000

_STATUSES = ("ok", "missing", "unsupported", "unknown")


@dataclass(frozen=True)
class CheckResult:
    """One prerequisite's verdict, worded for a person."""

    key: str  # stable id, e.g. "ollama-server"
    label: str  # "Local model server (Ollama)"
    status: str  # one of _STATUSES
    detail: str = ""  # what was found: "3 models pulled" / "not on PATH"
    hint: str = ""  # what would fix it: "install from ollama.com"
    feature: str = ""  # what it unlocks: "Fully local AI", "Board sharing", …


@dataclass(frozen=True)
class SystemReport:
    """Every check, plus the one-line summary the page headers render."""

    checks: tuple[CheckResult, ...]

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "ok")

    @property
    def summary(self) -> str:
        return f"{self.ok_count} of {len(self.checks)} optional features ready — the app itself needs none of them"


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
    if ok:
        return CheckResult("music", "Music (ffplay)", "ok", detail="ffplay on PATH", feature="Background music")
    return CheckResult(
        "music", "Music (ffplay)", "missing", detail="ffplay not on PATH", hint=reason, feature="Background music"
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


# Order is presentation order: the one thing every mode needs first, then the
# fully-local stack, then per-feature extras.
_CHECKS = (
    _check_provider,
    _check_ollama_installed,
    _check_ollama_server,
    _check_voice,
    _check_music,
    _check_charts,
    _check_cloudflared,
    _check_access,
    _check_coding_agent,
    _check_git,
    _check_disk,
)


def run_system_check() -> SystemReport:
    """Run every probe; a crashing probe reports ``unknown``, never raises."""
    results = []
    for probe in _CHECKS:
        try:
            results.append(probe())
        except Exception:
            key = probe.__name__.removeprefix("_check_").replace("_", "-")
            logger.warning("system check: %s probe failed", key, exc_info=True)
            results.append(CheckResult(key, key.replace("-", " ").title(), "unknown", detail="probe failed"))
    report = SystemReport(checks=tuple(results))
    logger.info("system check: %d/%d ready", report.ok_count, len(report.checks))
    return report
