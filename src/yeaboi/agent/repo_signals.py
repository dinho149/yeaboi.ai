"""Deterministic repository / tech signals for the smart intake.

# See docs: "Tools" — read-only tool pattern
# See docs: "Project Intake Questionnaire" — smart intake

The `project_analyzer` node already scans a repository (the URL from Q17) and
feeds the raw text to the LLM to ground `tech_stack` / `project_type`. This
module turns that same scan into **structured, deterministic signals** so the
smart intake can:

1. **Suggest a tech stack** (`detected_stack`) and **integrations**
   (`integrations`) — pre-filled into Q11 / Q12 via the normal extraction flow,
   editable at the confirmation summary.
2. **Detect "low-code" projects** (`low_code`) — mostly configuration / content /
   no-code-platform work — so the plan can be scaled lighter downstream.

Design: the scan fans out across sources with **graceful per-source skip**,
mirroring `standup/collector.py` — a missing SDK, bad credentials, or any error
degrades a source to "skipped" and never raises. The pure `analyze_context()`
core parses the tool output strings (github_read_repo / read_codebase share one
`Languages:` / `Key files detected:` format), so it is trivially unit-testable
without any live API calls.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Marker vocabularies. Kept here (not in prompts/intake.py) because they drive
# deterministic detection, not prompt text. LAN peers / repo contents are
# untrusted free text, so every match is a plain case-insensitive word match.
# ---------------------------------------------------------------------------

# Low-code / no-code platform + content-shop markers. When any appears in the
# project description, the stated tech stack, or the repo's key files, the
# project is flagged low-code (mostly configuration / content, little
# engineering). Multi-word markers are matched as phrases.
LOW_CODE_MARKERS: frozenset[str] = frozenset(
    {
        "salesforce",
        "apex",
        "force.com",
        "power apps",
        "powerapps",
        "power automate",
        "power platform",
        "sharepoint",
        "dynamics 365",
        "zapier",
        "make.com",
        "integromat",
        "workato",
        "tray.io",
        "boomi",
        "retool",
        "webflow",
        "wordpress",
        "wix",
        "squarespace",
        "shopify",
        "magento",
        "bigcommerce",
        "airtable",
        "bubble.io",
        "mendix",
        "outsystems",
        "appsheet",
        "glide",
        "softr",
        "servicenow",
        "hubspot",
        "no-code",
        "no code",
        "low-code",
        "low code",
        "headless cms",
        "contentful",
        "sanity",
        "drupal",
        "content only",
        "configuration only",
        "config-only",
    }
)

# Dependency-name substring → integration display name. Applied to fetched
# manifest contents (package.json / pyproject.toml). Richer than the
# description-keyword scan in _keyword_extract_fallback because it sees the
# project's *actual* declared dependencies. Third-party services only — infra
# libraries (pg, redis) are intentionally omitted to keep this to integrations.
INTEGRATION_SDK_MARKERS: dict[str, str] = {
    "stripe": "Stripe",
    "twilio": "Twilio",
    "sendgrid": "SendGrid",
    "mailgun": "Mailgun",
    "boto3": "AWS",
    "aws-sdk": "AWS",
    "firebase": "Firebase",
    "auth0": "Auth0",
    "okta": "Okta",
    "clerk": "Clerk",
    "plaid": "Plaid",
    "algolia": "Algolia",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "segment": "Segment",
    "analytics-node": "Segment",
    "datadog": "Datadog",
    "dd-trace": "Datadog",
    "sentry": "Sentry",
    "pusher": "Pusher",
    "launchdarkly": "LaunchDarkly",
    "contentful": "Contentful",
    "shopify": "Shopify",
    "slack": "Slack",
}

# Dependency-name substring → framework/library display name. Applied to
# manifest contents to enrich `detected_stack` beyond the language breakdown.
FRAMEWORK_MARKERS: dict[str, str] = {
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "@angular": "Angular",
    "svelte": "Svelte",
    "express": "Express",
    "fastify": "Fastify",
    "nestjs": "NestJS",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "rails": "Rails",
    "spring-boot": "Spring Boot",
    "laravel": "Laravel",
    "tailwindcss": "Tailwind CSS",
    "prisma": "Prisma",
    "sqlalchemy": "SQLAlchemy",
    "graphql": "GraphQL",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "langchain": "LangChain",
}

# Key-file basename → tool/platform display name, folded into `detected_stack`.
KEY_FILE_TOOLS: dict[str, str] = {
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker",
    "docker-compose.yaml": "Docker",
    "terraform.tf": "Terraform",
    "main.tf": "Terraform",
    "Chart.yaml": "Kubernetes",
    "vite.config.ts": "Vite",
    "vite.config.js": "Vite",
    "webpack.config.js": "Webpack",
}

# Manifest files whose *contents* we fetch for framework / integration inference.
MANIFEST_FILES: tuple[str, ...] = ("package.json", "pyproject.toml", "requirements.txt", "Gemfile", "composer.json")

# A repo with at most this many source files and ≤1 detected language reads as
# "barely any code" — a low-code signal when combined with an actual scan.
LOW_CODE_MAX_SOURCE_FILES = 6


@dataclass
class RepoSignals:
    """Structured, deterministic signals derived from a repository scan.

    Transient — never persisted directly. `detected_stack` / `integrations` are
    folded into the questionnaire as Q11 / Q12 suggestions; `low_code` is
    reconciled into `ProjectAnalysis.is_low_code` at analysis time.
    """

    detected_stack: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    low_code: bool = False
    low_code_reasons: list[str] = field(default_factory=list)
    source: str = ""  # "github" | "azdevops" | "local" | "" (nothing scanned)


# ---------------------------------------------------------------------------
# Pure parsing helpers — operate on the formatted tool output string, so they
# are unit-testable without any live API/filesystem access.
# ---------------------------------------------------------------------------


def _parse_section(raw: str, header: str) -> list[str]:
    """Return the indented lines under a `header:` section of a tool summary.

    Both github_read_repo and read_codebase emit sections like::

        Languages:
          Python: 80.0%
          TypeScript: 20.0%

    Parsing stops at the first blank line or the next unindented header.
    """
    lines = raw.splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        if line.strip() == header:
            in_section = True
            continue
        if in_section:
            if not line.strip():
                break
            if line.startswith((" ", "\t")):
                out.append(line.strip())
            else:
                break
    return out


def _parse_languages(raw: str) -> list[str]:
    """Extract language names (in order) from the `Languages:` section."""
    langs: list[str] = []
    for entry in _parse_section(raw, "Languages:"):
        # "Python: 80.0%" → "Python"
        name = entry.split(":", 1)[0].strip()
        if name:
            langs.append(name)
    return langs


def _parse_key_files(raw: str) -> list[str]:
    """Extract key-file paths from the `Key files detected:` section."""
    return [entry.strip() for entry in _parse_section(raw, "Key files detected:") if entry.strip()]


def _parse_total_files(raw: str) -> int | None:
    """Extract the source-file count from a `Total files scanned: N` line."""
    m = re.search(r"Total files scanned:\s*(\d+)", raw)
    return int(m.group(1)) if m else None


def _word_match(marker: str, haystack: str) -> bool:
    """Case-insensitive whole-word/phrase match of `marker` in `haystack`."""
    return re.search(rf"(?<![\w-]){re.escape(marker)}(?![\w-])", haystack, re.IGNORECASE) is not None


def _markers_in_text(haystack: str, markers) -> list[str]:
    """Return the markers that appear as whole words/phrases in `haystack`."""
    return [m for m in markers if _word_match(m, haystack)]


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _detect_low_code(
    *,
    description: str,
    tech_stack: str,
    languages: list[str],
    key_files: list[str],
    total_files: int | None,
) -> tuple[bool, list[str]]:
    """Deterministically decide whether a project is low-code, with reasons.

    Three independent signals (any one flags it):
    1. A low-code / no-code platform or content-shop marker appears in the
       description, the stated stack, or the repo's key files.
    2. A repo was scanned but no source-code language was detected (all config /
       content / docs).
    3. A scanned repo has barely any source files (≤ LOW_CODE_MAX_SOURCE_FILES)
       and at most one language.
    """
    reasons: list[str] = []

    # 1) Platform / content markers (most reliable — works even with no repo).
    haystack = " ".join([description, tech_stack, " ".join(_basename(f) for f in key_files)])
    hits = _markers_in_text(haystack, LOW_CODE_MARKERS)
    if hits:
        # De-duplicate while preserving order; report up to three.
        seen: list[str] = []
        for h in hits:
            if h not in seen:
                seen.append(h)
        reasons.append("mentions " + ", ".join(seen[:3]) + " (low-code / no-code platform or content tooling)")

    # 2) A repo was scanned (we have key files or a file count) but no code.
    scanned = bool(key_files) or total_files is not None
    if scanned and not languages:
        reasons.append("no source-code languages detected in the repository")

    # 3) Barely any source files.
    if total_files is not None and total_files <= LOW_CODE_MAX_SOURCE_FILES and len(languages) <= 1:
        reasons.append(f"very small codebase ({total_files} source file(s))")

    return bool(reasons), reasons


def _stack_from_manifests(manifests: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return (frameworks, integrations) inferred from fetched manifest contents.

    Manifest bodies are just text (JSON / TOML / plain deps); a whole-word match
    on the declared dependency names is enough to recognise well-known SDKs.
    """
    blob = "\n".join(manifests.values())
    frameworks = _dedupe([FRAMEWORK_MARKERS[m] for m in FRAMEWORK_MARKERS if _word_match(m, blob)])
    integrations = _dedupe([INTEGRATION_SDK_MARKERS[m] for m in INTEGRATION_SDK_MARKERS if _word_match(m, blob)])
    return frameworks, integrations


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-duplication."""
    out: list[str] = []
    for it in items:
        if it not in out:
            out.append(it)
    return out


def analyze_context(
    raw_context: str,
    *,
    description: str = "",
    tech_stack: str = "",
    manifests: dict[str, str] | None = None,
    source: str = "",
) -> RepoSignals:
    """Derive structured signals from a repo scan string (pure, no I/O).

    Args:
        raw_context: The github_read_repo / read_codebase summary text.
        description: Q1 project description (for low-code marker matching).
        tech_stack: Q11 stated tech stack, if any (for marker matching).
        manifests: Optional {filename: contents} for framework / integration
            inference. When omitted, only the language breakdown drives the stack.
        source: Which source produced the scan ("github" / "azdevops" / "local").

    Returns:
        A populated RepoSignals. Empty/degenerate input yields an all-empty
        result (low_code may still be True from description markers alone).
    """
    raw = raw_context or ""
    languages = _parse_languages(raw)
    key_files = _parse_key_files(raw)
    total_files = _parse_total_files(raw)

    frameworks: list[str] = []
    integrations: list[str] = []
    if manifests:
        frameworks, integrations = _stack_from_manifests(manifests)

    tools = _dedupe([KEY_FILE_TOOLS[_basename(f)] for f in key_files if _basename(f) in KEY_FILE_TOOLS])
    detected_stack = _dedupe([*languages, *frameworks, *tools])

    low_code, reasons = _detect_low_code(
        description=description,
        tech_stack=tech_stack,
        languages=languages,
        key_files=key_files,
        total_files=total_files,
    )

    return RepoSignals(
        detected_stack=detected_stack,
        integrations=integrations,
        low_code=low_code,
        low_code_reasons=reasons,
        source=source,
    )


# ---------------------------------------------------------------------------
# I/O entry point — graceful per-source fan-out (mirrors standup/collector.py).
# ---------------------------------------------------------------------------


def _fetch_manifests_github(url: str, key_files: list[str]) -> dict[str, str]:
    """Best-effort fetch of manifest file contents from a GitHub repo."""
    from yeaboi.tools.github import github_read_file

    out: dict[str, str] = {}
    for path in key_files:
        if _basename(path) in MANIFEST_FILES:
            try:
                content = github_read_file.invoke({"repo_url": url, "file_path": path})
                if content and not content.startswith(("Error:", "GitHub rate limit")):
                    out[path] = content
            except Exception:  # noqa: BLE001 — best-effort; a bad manifest never aborts the scan
                logger.debug("Manifest fetch failed for %s (non-fatal)", path, exc_info=True)
    return out


def _fetch_manifests_local(root: str, key_files: list[str]) -> dict[str, str]:
    """Best-effort fetch of manifest file contents from a local repo."""
    from yeaboi.tools.codebase import read_local_file

    out: dict[str, str] = {}
    for path in key_files:
        if _basename(path) in MANIFEST_FILES:
            try:
                content = read_local_file.invoke({"repo_path": root, "file_path": path})
                if content and not content.startswith("Error:"):
                    out[path] = content
            except Exception:  # noqa: BLE001 — best-effort
                logger.debug("Local manifest fetch failed for %s (non-fatal)", path, exc_info=True)
    return out


def _resolve_repo_target(questionnaire) -> tuple[str, str]:
    """Return (url_or_path, platform) to scan, or ("", "") if none is available.

    Prefers the URL the user gave in Q17; otherwise falls back to a configured
    GitHub repo (STANDUP_GITHUB_REPO + a token). Azure DevOps has no single-repo
    env var, so it is only scanned when the user supplies a Q17 URL.
    """
    from yeaboi.config import get_github_token, get_standup_github_repo
    from yeaboi.prompts.intake import QUESTION_DEFAULTS

    url = (questionnaire.answers.get(17, "") or "").strip()
    if url and url != QUESTION_DEFAULTS.get(17):
        platform = questionnaire.answers.get(16, "GitHub") or "GitHub"
        return url, platform

    # Fallback: a configured GitHub repo (owner/repo) scanned for context.
    gh_repo = get_standup_github_repo().strip()
    if gh_repo and get_github_token():
        return gh_repo, "GitHub"

    return "", ""


def scan_repo_signals(questionnaire) -> tuple[str | None, RepoSignals, dict]:
    """Scan the project's repository and return (raw_context, signals, status).

    Graceful: any missing SDK / credential / API error degrades to
    (None, empty-but-marker-aware RepoSignals, status) — it never raises. Even
    when nothing is scanned, description-based low-code markers are still applied
    so a "Zapier + Webflow" project is flagged without any repo.

    # See docs: "Tools" — read-only tool pattern
    """
    description = questionnaire.answers.get(1, "") or ""
    tech_stack = questionnaire.answers.get(11, "") or ""

    target, platform = _resolve_repo_target(questionnaire)

    raw_context: str | None = None
    manifests: dict[str, str] = {}
    source = ""
    status = {"name": "Repository", "status": "skipped", "detail": "no repo to scan"}

    if target:
        logger.info("repo_signals: scanning %s (%s)", target, platform)
        try:
            if platform == "GitHub":
                from yeaboi.tools.github import github_read_repo

                result = github_read_repo.invoke({"repo_url": target})
                if result and not result.startswith(("Error:", "GitHub rate limit")):
                    raw_context = result
                    source = "github"
                    manifests = _fetch_manifests_github(target, _parse_key_files(result))
            elif platform == "Azure DevOps":
                from yeaboi.tools.azure_devops import azdevops_read_repo

                result = azdevops_read_repo.invoke({"repo_url": target})
                if result and not result.startswith("Error:"):
                    raw_context = result
                    source = "azdevops"
            elif not target.startswith(("http://", "https://")):
                from yeaboi.tools.codebase import read_codebase

                result = read_codebase.invoke({"path": target})
                if result and not result.startswith("Error:"):
                    raw_context = result
                    source = "local"
                    manifests = _fetch_manifests_local(target, _parse_key_files(result))
        except Exception as e:  # noqa: BLE001 — never let a scan error abort intake
            logger.warning("repo_signals scan failed for %s: %s", target, e)
            status = {"name": "Repository", "status": "error", "detail": str(e)[:80]}

    signals = analyze_context(
        raw_context or "",
        description=description,
        tech_stack=tech_stack,
        manifests=manifests,
        source=source,
    )

    if raw_context is not None:
        detail = f"{platform} — {len(signals.detected_stack)} stack item(s)"
        if signals.low_code:
            detail += ", low-code"
        status = {"name": "Repository", "status": "success", "detail": detail}
        logger.info(
            "repo_signals: source=%s stack=%s integrations=%s low_code=%s",
            source,
            signals.detected_stack,
            signals.integrations,
            signals.low_code,
        )

    return raw_context, signals, status
