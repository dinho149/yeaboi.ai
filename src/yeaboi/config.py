"""Configuration and environment variable handling."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()


# ---------------------------------------------------------------------------
# User config directory (~/.yeaboi/)
# ---------------------------------------------------------------------------


def restrict_permissions(path: Path, *, mode: int) -> None:
    """Best-effort ``chmod`` so on-disk secrets aren't group/other readable.

    The config dir (``0o700``) and ``.env`` (``0o600``) hold API keys and tokens
    in plaintext, so they must not be readable by other local accounts. This is a
    POSIX concept: on Windows ``chmod`` can't express these bits and the call is a
    harmless no-op. Never raises — a filesystem that rejects ``chmod`` must not
    break a config write.
    """
    try:
        path.chmod(mode)
    except OSError as e:  # pragma: no cover - platform/filesystem dependent
        logger.debug("could not chmod %s to %o: %s", path, mode, e)


def get_config_dir() -> Path:
    """Return ~/.yeaboi/, creating it if necessary.

    Kept as a live ``Path.home()`` computation (rather than importing
    ``paths.ROOT_DIR``) so it stays test-monkeypatchable and matches
    ``paths.ROOT_DIR``. Callers must ensure ``paths.migrate_root_dir()`` has run
    first (done at the top of ``cli.main``) so a pre-rebrand ~/.scrum-agent tree
    is moved over before this mkdir creates an empty ~/.yeaboi.
    """
    d = Path.home() / ".yeaboi"
    d.mkdir(exist_ok=True)
    # Repair perms on every call (cheap): the dir holds .env + sessions.db, both secret-bearing.
    restrict_permissions(d, mode=0o700)
    return d


def set_config_value(key: str, value: str) -> Path:
    """Persist a single ``key=value`` to ~/.yeaboi/.env and lock the file to 0o600.

    Central choke point for every ``set_key``-based setter so the credential file
    is re-hardened after each write (``dotenv.set_key`` may recreate it at
    umask-default perms). Does not touch ``os.environ`` — callers still do that.
    """
    from dotenv import set_key

    config_file = get_config_file()
    set_key(str(config_file), key, value)
    restrict_permissions(config_file, mode=0o600)
    return config_file


def apply_config_value(key: str, value: str) -> Path:
    """Persist ``key=value`` AND mirror it into ``os.environ`` for this process.

    :func:`set_config_value` only writes the .env file, so a running session keeps
    its old ``os.environ`` — anything reading config (including the Settings page,
    which re-reads the environment) would show the previous value until a restart.
    Every other setter in this module pairs the write with an ``os.environ`` update;
    this is that pair as one reusable call. An empty ``value`` clears the variable.
    """
    config_file = set_config_value(key, value)
    if value:
        os.environ[key] = value
    else:
        os.environ.pop(key, None)
    return config_file


def get_config_file() -> Path:
    """Return path to ~/.yeaboi/.env."""
    return get_config_dir() / ".env"


def get_sessions_db() -> Path:
    """Return path to ~/.yeaboi/sessions.db (SQLite session store).

    Legacy location — most stores use ``paths.get_db_path()`` (data/sessions.db)
    instead; this getter is still used by ceremony history and performance
    context. Both apply the same 0o600 hardening as the .env file.
    """
    db = get_config_dir() / "sessions.db"
    if db.exists():
        restrict_permissions(db, mode=0o600)
    return db


def load_user_config() -> None:
    """Load ~/.yeaboi/.env without overriding existing env vars.

    Called once at CLI startup before any credential reads.
    dotenv's override=False means shell env vars and project .env always win
    — safe for CI/CD and developer overrides.
    """
    config_path = get_config_file()
    logger.info("Loading user config from %s", config_path)
    load_dotenv(config_path, override=False)
    logger.debug(
        "API keys — ANTHROPIC_API_KEY: %s, OPENAI_API_KEY: %s, GOOGLE_API_KEY: %s",
        "set" if os.getenv("ANTHROPIC_API_KEY") else "missing",
        "set" if os.getenv("OPENAI_API_KEY") else "missing",
        "set" if os.getenv("GOOGLE_API_KEY") else "missing",
    )
    logger.debug(
        "Integrations — GITHUB_TOKEN: %s, JIRA_API_TOKEN: %s, AZURE_DEVOPS_TOKEN: %s",
        "set" if os.getenv("GITHUB_TOKEN") else "missing",
        "set" if os.getenv("JIRA_API_TOKEN") else "missing",
        "set" if os.getenv("AZURE_DEVOPS_TOKEN") else "missing",
    )


def get_anthropic_api_key() -> str:
    """Return the Anthropic API key or raise if not set."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise OSError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
    return key


def is_langsmith_enabled() -> bool:
    """Check whether LangSmith tracing is enabled."""
    return os.getenv("LANGSMITH_TRACING", "").lower() == "true" and bool(os.getenv("LANGSMITH_API_KEY"))


def is_tips_enabled() -> bool:
    """Return True if on-screen discoverability tips should be shown (default on).

    Controls the rotating welcome-screen tip banner and the inline voice hints on
    text-entry screens. Any value other than "false" (case-insensitive) keeps tips
    on, so an unset var means enabled — the feature should be visible by default.
    """
    return os.getenv("TIPS_ENABLED", "true").strip().lower() != "false"


def is_beta_notice_enabled() -> bool:
    """Return True if CLI runs should print the beta-maturity caveat (default on).

    Mirrors :func:`is_tips_enabled`: any value other than "false" keeps it on, so
    an unset var means enabled — a maturity caveat has to be opt-out, never
    opt-in. Scripted and cron callers that capture stderr can turn it off.
    """
    return os.getenv("BETA_NOTICES_ENABLED", "true").strip().lower() != "false"


def set_tips_enabled(enabled: bool) -> None:
    """Persist the tips on/off preference to ~/.scrum-agent/.env and apply it now.

    Uses dotenv's set_key so only this one key is updated — save_config() rewrites
    the whole file and would drop any keys not passed to it. os.environ is updated
    too so the running session reflects the change immediately (no reload needed).
    """
    value = "true" if enabled else "false"
    # set_config_value creates the file if missing, preserves existing keys, and re-locks it to 0o600.
    config_file = set_config_value("TIPS_ENABLED", value)
    os.environ["TIPS_ENABLED"] = value
    logger.info("Tips %s (persisted to %s)", "enabled" if enabled else "disabled", config_file)


def is_duck_enabled() -> bool:
    """Return True if the corner duck's speech bubble may show lines (default on).

    Mirrors :func:`is_tips_enabled`: any value other than "false" keeps the
    bubble on, so an unset var means enabled. Only the bubble is gated — the
    duck himself (bob, quack, shades) always stays.
    """
    return os.getenv("DUCK_ENABLED", "true").strip().lower() != "false"


def set_duck_enabled(enabled: bool) -> None:
    """Persist the duck-bubble on/off preference to ~/.scrum-agent/.env and apply it now."""
    value = "true" if enabled else "false"
    config_file = set_config_value("DUCK_ENABLED", value)
    os.environ["DUCK_ENABLED"] = value
    logger.info("Duck bubble %s (persisted to %s)", "enabled" if enabled else "disabled", config_file)


def is_music_enabled() -> bool:
    """Return True if background music was left enabled (default off).

    Only records the on/off preference so the status bar can reflect it; playback
    itself is never auto-started (that would be surprise noise). Mirrors
    :func:`is_tips_enabled`.
    """
    return os.getenv("MUSIC_ENABLED", "false").strip().lower() == "true"


def set_music_enabled(enabled: bool) -> None:
    """Persist the music on/off preference to ~/.scrum-agent/.env and apply it now."""
    value = "true" if enabled else "false"
    config_file = set_config_value("MUSIC_ENABLED", value)
    os.environ["MUSIC_ENABLED"] = value
    logger.info("Music %s (persisted to %s)", "enabled" if enabled else "disabled", config_file)


def get_music_channel() -> int:
    """Return the persisted music channel index (defaults to 0)."""
    try:
        return int(os.getenv("MUSIC_CHANNEL", "0").strip())
    except ValueError:
        return 0


def set_music_channel(idx: int) -> None:
    """Persist the selected music channel index to ~/.scrum-agent/.env."""
    value = str(int(idx))
    config_file = set_config_value("MUSIC_CHANNEL", value)
    os.environ["MUSIC_CHANNEL"] = value
    logger.info("Music channel set to %s (persisted to %s)", value, config_file)


# Modes that ship in beta show a one-time notice the first time they're opened.
# The acknowledgement is a comma-separated list of _MODE_CARDS keys rather than a
# boolean per mode, so the next beta mode costs one card key instead of a new env
# var and a new pair of functions.
BETA_ACK_KEY = "BETA_NOTICES_ACK"
FORCE_BETA_NOTICE_ENV = "YEABOI_FORCE_BETA_NOTICE"


def beta_notices_acked() -> set[str]:
    """Return the set of mode keys whose beta notice has been acknowledged."""
    raw = os.getenv(BETA_ACK_KEY, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def is_beta_notice_seen(mode_key: str) -> bool:
    """Return True if ``mode_key``'s one-time beta notice has already been shown.

    ``YEABOI_FORCE_BETA_NOTICE`` overrides the acknowledgement — either truthy
    (all modes) or a comma-separated list of specific keys. A once-ever gate is
    otherwise impossible to re-check by hand after the first click, which makes
    it impossible to demo, screenshot, or eyeball during review.
    """
    forced = os.getenv(FORCE_BETA_NOTICE_ENV, "").strip().lower()
    if forced:
        if forced in {"1", "true", "yes", "on"}:
            return False
        if mode_key in {part.strip() for part in forced.split(",") if part.strip()}:
            return False
    return mode_key in beta_notices_acked()


def mark_beta_notice_seen(mode_key: str) -> None:
    """Record that ``mode_key``'s beta notice has been acknowledged.

    Deliberately not :func:`apply_config_value` (which does the same pair): that
    helper writes to disk *first*, so a failed write would skip the ``os.environ``
    update too. Here the order is inverted — ``os.environ`` is set even when the
    disk write fails (read-only home, odd permissions), because a user who just
    dismissed the notice must not see it again in the same session. Re-showing
    it on the next restart is the lesser failure.
    """
    acked = beta_notices_acked()
    if mode_key in acked:
        return
    value = ",".join(sorted(acked | {mode_key}))
    os.environ[BETA_ACK_KEY] = value
    try:
        config_file = set_config_value(BETA_ACK_KEY, value)
    except OSError as exc:
        logger.warning("Could not persist beta notice acknowledgement for %s: %s", mode_key, exc)
        return
    logger.info("Beta notice acknowledged for %s (persisted to %s)", mode_key, config_file)


# Proxy environment variables to check (both uppercase and lowercase conventions).
_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def detect_proxy() -> str | None:
    """Return the first proxy URL found in environment variables, or None."""
    for var in _PROXY_ENV_VARS:
        value = os.getenv(var)
        if value:
            logger.debug("Proxy detected via %s", var)
            return value
    logger.debug("No proxy detected")
    return None


def get_github_token() -> str | None:
    """Return the GitHub PAT, or None if not set (tools work for public repos without a token)."""
    return os.getenv("GITHUB_TOKEN") or None


def get_azure_devops_token() -> str | None:
    """Return the Azure DevOps PAT, or None if not set."""
    return os.getenv("AZURE_DEVOPS_TOKEN") or None


def get_azure_devops_org_url() -> str | None:
    """Return the Azure DevOps organization URL (e.g. https://dev.azure.com/myorg), or None if not set.

    Normalised on read: whitespace/trailing slashes stripped and a missing scheme
    defaulted to https://. A bare "dev.azure.com/org" value otherwise reaches the
    SDK's URL joining and surfaces as MissingSchema errors with a doubled host
    ("dev.azure.com/org/dev.azure.com/org/_apis") on every AzDO surface.
    """
    raw = (os.getenv("AZURE_DEVOPS_ORG_URL") or "").strip().rstrip("/")
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    return raw


def get_azure_devops_project() -> str | None:
    """Return the Azure DevOps project name, or None if not set."""
    return os.getenv("AZURE_DEVOPS_PROJECT") or None


def get_azure_devops_team() -> str | None:
    """Return the Azure DevOps team name, or None if not set.

    Defaults to "{project} Team" when not explicitly set (AzDO's default team naming).
    """
    team = os.getenv("AZURE_DEVOPS_TEAM")
    if team:
        return team
    project = get_azure_devops_project()
    if project:
        return f"{project} Team"
    return None


def get_jira_base_url() -> str | None:
    """Return the Jira Cloud base URL (e.g. https://org.atlassian.net), or None if not set."""
    return os.getenv("JIRA_BASE_URL") or None


def get_jira_email() -> str | None:
    """Return the Atlassian account email used for Jira basic auth, or None if not set."""
    return os.getenv("JIRA_EMAIL") or None


def get_jira_token() -> str | None:
    """Return the Jira API token, or None if not set."""
    return os.getenv("JIRA_API_TOKEN") or None


def get_jira_project_key() -> str | None:
    """Return the default Jira project key (e.g. 'MYPROJ'), or None if not set."""
    return os.getenv("JIRA_PROJECT_KEY") or None


def get_confluence_base_url() -> str | None:
    """Return the Confluence Cloud base URL, or None if not set.

    Confluence shares Atlassian identity with Jira, so it historically reused the
    Jira creds. To let Confluence be configured standalone (without Jira issue
    tracking), a dedicated CONFLUENCE_BASE_URL may be set; it falls back to
    JIRA_BASE_URL so existing Jira+Confluence setups keep working unchanged.
    """
    return os.getenv("CONFLUENCE_BASE_URL") or get_jira_base_url()


def get_confluence_email() -> str | None:
    """Return the Atlassian email for Confluence basic auth, or None if not set.

    Falls back to JIRA_EMAIL (same Atlassian account) — see get_confluence_base_url.
    """
    return os.getenv("CONFLUENCE_EMAIL") or get_jira_email()


def get_confluence_token() -> str | None:
    """Return the Atlassian API token for Confluence, or None if not set.

    Falls back to JIRA_API_TOKEN (same Atlassian account) — see get_confluence_base_url.
    """
    return os.getenv("CONFLUENCE_API_TOKEN") or get_jira_token()


def get_confluence_space_key() -> str | None:
    """Return the default Confluence space key (e.g. 'MYSPACE'), or None if not set."""
    return os.getenv("CONFLUENCE_SPACE_KEY") or None


def get_anonymize_mask_terms() -> tuple[str, ...]:
    """Return extra company-specific terms to always mask in Anonymize mode.

    Read from ANONYMIZE_MASK_TERMS as a comma-separated list (e.g. "YouLend,YL,Acme").
    These seed the deterministic pre-mask pass in anonymize/engine.py so the obvious
    company identifiers are redacted even when the LLM is unavailable. Blank/whitespace
    entries are dropped; returns () when unset.
    """
    raw = os.getenv("ANONYMIZE_MASK_TERMS", "")
    return tuple(term.strip() for term in raw.split(",") if term.strip())


def get_notion_token() -> str | None:
    """Return the Notion integration token, or None if not set.

    Unlike Confluence (which reuses Jira's Atlassian auth), Notion has its own
    OAuth2 integration token — the only credential its SDK needs.
    """
    return os.getenv("NOTION_TOKEN") or None


def get_notion_root_page_id() -> str | None:
    """Return the optional Notion root page/database ID, or None if not set.

    Notion has no "space key" — search spans whatever pages the integration is
    granted. This optional ID scopes the default parent for page creation and the
    standup recent-pages feed (analogous to CONFLUENCE_SPACE_KEY).
    """
    return os.getenv("NOTION_ROOT_PAGE_ID") or None


# ---------------------------------------------------------------------------
# Storage & export destinations
# ---------------------------------------------------------------------------
# One YEABOI_HOME override relocates the whole data tree (exports, logs, DB…);
# Notion/Confluence exports publish to the destinations collected in provider
# setup, falling back to the tool's natural default (Notion root page /
# Confluence space root) when no dedicated exports page is set.


def _set_env_value(key: str, value: str) -> None:
    """Persist an env var to ~/.yeaboi/.env and apply it to the live process."""
    from dotenv import set_key

    config_file = get_config_file()
    set_key(str(config_file), key, value)
    os.environ[key] = value
    logger.info("%s persisted to %s", key, config_file)


def get_data_dir() -> str:
    """Return the configured data home (YEABOI_HOME), or '' when using ~/.yeaboi.

    The actual resolution lives in paths.py (import time); this getter exists
    for the Settings page display and the Data Dir edit flow.
    """
    return os.getenv("YEABOI_HOME", "") or ""


def set_data_dir(value: str) -> None:
    """Persist the data home override ('' clears back to ~/.yeaboi).

    Written to the pinned bootstrap ~/.yeaboi/.env (see paths.ENV_FILE) —
    module-level path constants are baked at import, so a restart fully applies.
    """
    _set_env_value("YEABOI_HOME", value.strip())


def get_allowed_paths() -> tuple[str, ...]:
    """Return the user's filesystem whitelist (YEABOI_ALLOWED_PATHS).

    Comma-separated directory/file paths the sandbox (fs_policy.py) allows
    beyond the data home. Shares _csv_config's limitation: paths containing a
    comma can't be expressed. Applies to reads and writes alike.
    """
    return _csv_config("YEABOI_ALLOWED_PATHS")


def set_allowed_paths(values: list[str] | tuple[str, ...]) -> None:
    """Persist the whitelist (deduplicated, order-preserving) and apply live."""
    deduped: list[str] = []
    for raw in values:
        value = raw.strip()
        if value and value not in deduped:
            deduped.append(value)
    _set_env_value("YEABOI_ALLOWED_PATHS", ",".join(deduped))


def add_allowed_path(value: str) -> None:
    """Append one path to the whitelist ('Always allow' in the consent popup)."""
    set_allowed_paths([*get_allowed_paths(), value])


def get_notion_export_parent_page_id() -> str | None:
    """Return the Notion page ID exports publish under, or None when unavailable.

    The dedicated exports page (NOTION_EXPORT_PARENT_PAGE_ID, optional in the
    Notion setup step) wins; otherwise falls back to NOTION_ROOT_PAGE_ID —
    where the publisher groups docs under an auto-created "yeaboi" container
    page (🤙 icon). The Notion API can't create top-level pages, so None
    (neither set) blocks Notion export with a warning pointing at Setup.
    """
    return os.getenv("NOTION_EXPORT_PARENT_PAGE_ID") or get_notion_root_page_id()


def get_confluence_export_parent_page_id() -> str | None:
    """Return the optional Confluence parent page ID for exports.

    Blank means exports group under an auto-created "🤙 yeaboi" container page
    at the root of the space (see export_targets._ensure_confluence_brand_parent).
    """
    return os.getenv("CONFLUENCE_EXPORT_PARENT_PAGE_ID") or None


# ---------------------------------------------------------------------------
# Daily Standup configuration
# ---------------------------------------------------------------------------
# Non-secret standup settings (schedule time, channels) live in the SQLite
# standup_config table, keyed by session. Secrets and single-value integration
# creds live here in .env, same as the other integrations. get_standup_* getters
# read env; the two secret-bearing setters use dotenv.set_key like set_tips_enabled.


def get_standup_github_repo() -> str:
    """Return the GitHub repo (owner/repo or URL) to scan for standup code activity."""
    return os.getenv("STANDUP_GITHUB_REPO", "") or ""


def _csv_config(name: str) -> tuple[str, ...]:
    """Return a stable, de-duplicated comma-separated configuration value."""
    values: list[str] = []
    for raw in os.getenv(name, "").split(","):
        value = raw.strip()
        if value and value not in values:
            values.append(value)
    return tuple(values)


def get_team_analysis_github_owners() -> tuple[str, ...]:
    """GitHub owners/orgs whose repositories form the analysis estate.

    Falls back to the owner of ``STANDUP_GITHUB_REPO`` for compatibility.
    """
    configured = _csv_config("TEAM_ANALYSIS_GITHUB_OWNERS")
    if configured:
        return configured
    repo = get_standup_github_repo()
    return (repo.split("/", 1)[0],) if "/" in repo else ()


def get_team_analysis_azdo_projects() -> tuple[str, ...]:
    configured = _csv_config("TEAM_ANALYSIS_AZDO_PROJECTS")
    if configured:
        return configured
    project = get_azure_devops_project() or ""
    return (project,) if project else ()


def get_team_analysis_confluence_spaces() -> tuple[str, ...]:
    configured = _csv_config("TEAM_ANALYSIS_CONFLUENCE_SPACES")
    if configured:
        return configured
    space = get_confluence_space_key() or ""
    return (space,) if space else ()


def get_team_analysis_notion_roots() -> tuple[str, ...]:
    configured = _csv_config("TEAM_ANALYSIS_NOTION_ROOTS")
    if configured:
        return configured
    root = get_notion_root_page_id() or ""
    return (root,) if root else ()


def get_team_analysis_enrichment_timeout_seconds() -> int:
    """Maximum seconds for one Analysis-mode AI enrichment request."""
    raw = os.getenv("TEAM_ANALYSIS_ENRICHMENT_TIMEOUT_SECONDS", "120")
    try:
        return max(10, min(int(raw), 600))
    except ValueError:
        return 120


def get_team_analysis_fast_model() -> str | None:
    """Optional model override for lightweight Analysis-mode enrichment calls.

    Unset selects the provider-specific fast default. ``default``/``off`` keeps
    the user's primary model instead.
    """
    raw = os.getenv("TEAM_ANALYSIS_FAST_MODEL", "").strip()
    return raw or None


def get_team_analysis_llm_target_seconds() -> int:
    """Target wall-clock time for the LLM portion of a Deep analysis run."""
    raw = os.getenv("TEAM_ANALYSIS_LLM_TARGET_SECONDS", "600")
    try:
        return max(60, min(int(raw), 7200))
    except ValueError:
        return 600


def get_team_analysis_llm_max_concurrency() -> int:
    """Maximum concurrent cloud LLM requests used by Analysis mode."""
    raw = os.getenv("TEAM_ANALYSIS_LLM_MAX_CONCURRENCY", "6")
    try:
        return max(1, min(int(raw), 12))
    except ValueError:
        return 6


def get_team_analysis_doc_request_timeout_seconds() -> int:
    """Maximum seconds for one documentation-provider request in Analysis."""
    raw = os.getenv("TEAM_ANALYSIS_DOC_REQUEST_TIMEOUT_SECONDS", "30")
    try:
        return max(5, min(int(raw), 120))
    except ValueError:
        return 30


def get_team_analysis_doc_max_concurrency() -> int:
    """Maximum concurrent documentation body reads per provider."""
    raw = os.getenv("TEAM_ANALYSIS_DOC_MAX_CONCURRENCY", "8")
    try:
        return max(1, min(int(raw), 16))
    except ValueError:
        return 8


def get_team_analysis_code_max_concurrency() -> int:
    """Maximum concurrent code-provider reads used by Analysis mode."""
    raw = os.getenv("TEAM_ANALYSIS_CODE_MAX_CONCURRENCY", "6")
    try:
        return max(1, min(int(raw), 16))
    except ValueError:
        return 6


def get_team_analysis_tracker_max_concurrency() -> int:
    """Maximum concurrent Jira/Azure-DevOps per-story reads during sprint-history fetch.

    Deliberately lower than the code knob's default: Jira Cloud's cost-based rate
    limiting throttles bursts sooner than GitHub does, and a throttled per-story
    call degrades that story's data silently (empty comments / missing changelog).
    """
    raw = os.getenv("TEAM_ANALYSIS_TRACKER_MAX_CONCURRENCY", "4")
    try:
        return max(1, min(int(raw), 12))
    except ValueError:
        return 4


def get_team_analysis_max_change_lookups() -> int:
    """Maximum per-run code-change metadata lookups (each cache miss costs one API call).

    Applied to cache misses only — warm re-runs resolve from the SQLite sha cache
    and keep full coverage; a capped cold run discloses the truncation in the
    coverage notes and prefers the newest changes.
    """
    raw = os.getenv("TEAM_ANALYSIS_MAX_CHANGE_LOOKUPS", "500")
    try:
        return max(50, min(int(raw), 5000))
    except ValueError:
        return 500


def get_retro_server_port() -> int:
    """Return the base port for the Retro collaboration server (default 5173).

    The server walks upward from this port if it is busy (see retro/server.py).
    """
    try:
        return int(os.getenv("RETRO_PORT", "5173"))
    except ValueError:
        return 5173


def get_poker_server_port() -> int:
    """Return the base port for the Poker collaboration server (default 5273).

    5273 sits clear of retro's 5173..5193 walk range so both modes can run at
    once. The server walks upward from this port if it is busy (poker/server.py).
    """
    try:
        return int(os.getenv("POKER_PORT", "5273"))
    except ValueError:
        return 5273


def tunnels_disabled() -> bool:
    """True when the live boards must not open a Cloudflare tunnel.

    The boards now start one the moment they open, which is right for a real
    ceremony and wrong for everything else: ``make run-dry`` advertises "fake
    delays, no LLM calls", and merely opening the Retro card under it would
    download a ~40 MB binary and publish a URL to the internet. Someone on a
    locked-down or offline network wants the same opt-out.

    Set ``YEABOI_NO_TUNNEL=1`` and the board still runs — the host reaches it on
    ``127.0.0.1`` — it simply has nothing to hand out, and says so.
    """
    return os.getenv("YEABOI_NO_TUNNEL", "").strip().lower() in ("1", "true", "yes")


def get_slack_webhook_url() -> str:
    """Return the Slack incoming-webhook URL for standup delivery, or '' if unset."""
    return os.getenv("SLACK_WEBHOOK_URL", "") or ""


def set_slack_webhook_url(url: str) -> None:
    """Persist the Slack webhook URL to ~/.scrum-agent/.env and apply it now."""
    config_file = set_config_value("SLACK_WEBHOOK_URL", url)
    os.environ["SLACK_WEBHOOK_URL"] = url
    logger.info("Slack webhook URL persisted to %s", config_file)


def get_smtp_host() -> str:
    """Return the SMTP host for standup email delivery, or '' if unset."""
    return os.getenv("STANDUP_SMTP_HOST", "") or ""


def get_smtp_port() -> int:
    """Return the SMTP port (default 587)."""
    try:
        return int(os.getenv("STANDUP_SMTP_PORT", "587") or "587")
    except ValueError:
        return 587


def get_smtp_user() -> str:
    """Return the SMTP username, or '' if unset."""
    return os.getenv("STANDUP_SMTP_USER", "") or ""


def get_smtp_password() -> str:
    """Return the SMTP password, or '' if unset."""
    return os.getenv("STANDUP_SMTP_PASSWORD", "") or ""


def set_smtp_password(password: str) -> None:
    """Persist the SMTP password to ~/.scrum-agent/.env and apply it now."""
    config_file = set_config_value("STANDUP_SMTP_PASSWORD", password)
    os.environ["STANDUP_SMTP_PASSWORD"] = password
    logger.info("SMTP password persisted to %s", config_file)


def get_smtp_sender() -> str:
    """Return the From address for standup emails (defaults to the SMTP user)."""
    return os.getenv("STANDUP_SMTP_SENDER", "") or get_smtp_user()


def get_standup_email_recipients() -> list[str]:
    """Return the standup email recipient list, parsed from a comma-separated env var."""
    raw = os.getenv("STANDUP_EMAIL_RECIPIENTS", "") or ""
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def get_standup_user_name() -> str:
    """Return the display name for the current user's self-reported standup update.

    Reads STANDUP_USER_NAME; defaults to "Me" so a solo user still gets a sensible
    label without configuration.
    """
    return os.getenv("STANDUP_USER_NAME", "").strip() or "Me"


def get_performance_framework_path() -> str:
    """Return an optional path to a custom competency framework / review template.

    Reads PERFORMANCE_FRAMEWORK_PATH. When set, the 6-month review uses this file's
    contents in place of the bundled default framework (performance/references/
    competency_framework.md), so a lead can drop in their org's HR template.
    Returns "" when unset.
    """
    return os.getenv("PERFORMANCE_FRAMEWORK_PATH", "").strip()


# ---------------------------------------------------------------------------
# LLM provider configuration
# ---------------------------------------------------------------------------


def get_llm_provider() -> str:
    """Return the active LLM provider name (lowercase).

    Set LLM_PROVIDER in .env to switch providers. Defaults to 'anthropic'.
    Supported values: 'anthropic', 'openai', 'google', 'bedrock', 'ollama'.
    """
    return os.getenv("LLM_PROVIDER", "anthropic").lower()


def get_llm_model() -> str | None:
    """Return the model ID override from LLM_MODEL env var, or None to use the provider default."""
    return os.getenv("LLM_MODEL") or None


def get_bedrock_region() -> str:
    """Return the AWS region for Bedrock API calls.

    Reads AWS_REGION, then AWS_DEFAULT_REGION from env. Defaults to 'us-east-1'.
    """
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def get_aws_profile() -> str | None:
    """Return the AWS profile to use for Bedrock API calls.

    Reads AWS_PROFILE from env. If not set, auto-detects from ~/.aws/config
    by finding the first profile with a region or role_arn configured.
    Returns None if only [default] is available (boto3 handles that automatically).
    """
    profile = os.getenv("AWS_PROFILE")
    if profile:
        return profile

    # Auto-detect: parse ~/.aws/config for non-default profiles
    try:
        config_path = Path.home() / ".aws" / "config"
        if config_path.exists():
            import configparser

            cfg = configparser.ConfigParser()
            cfg.read(config_path)
            for section in cfg.sections():
                # AWS config sections are [default] or [profile <name>]
                if section.startswith("profile "):
                    profile_name = section.removeprefix("profile ").strip()
                    if cfg.has_option(section, "role_arn") or cfg.has_option(section, "credential_source"):
                        return profile_name
    except Exception:
        pass

    return None


def get_openai_api_key() -> str | None:
    """Return the OpenAI API key, or None if not set."""
    return os.getenv("OPENAI_API_KEY") or None


def get_google_api_key() -> str | None:
    """Return the Google AI API key, or None if not set."""
    return os.getenv("GOOGLE_API_KEY") or None


def get_ollama_base_url() -> str:
    """Return the base URL of the local Ollama server.

    Ollama runs entirely on the user's machine — no API key, no cloud account.
    Override with OLLAMA_BASE_URL for a non-default port or a server elsewhere
    on the network. Trailing slashes are stripped so URL joining is predictable.
    """
    return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def get_ollama_num_ctx() -> int:
    """Return the context window (tokens) requested from the Ollama model.

    Ollama's server default context (2-4k tokens) is smaller than the biggest
    assembled prompts in the planning pipeline (~5k tokens plus optional repo /
    Confluence context). A too-small context silently truncates the prompt,
    which destroys the JSON output the pipeline parses — so we default to 16384
    and let power users tune it via OLLAMA_NUM_CTX (e.g. lower it on low-RAM
    machines, raise it for very large projects).
    """
    try:
        return int(os.getenv("OLLAMA_NUM_CTX", "16384"))
    except ValueError:
        return 16384


def is_llm_configured() -> tuple[bool, str]:
    """Return (ok, message) for whether the selected LLM provider has credentials.

    Cheap, no network call — just checks the env var the active provider needs.
    Callers (e.g. the standup engine) use this to surface a clear "set your API
    key" message instead of silently degrading. Bedrock uses IAM, so a configured
    AWS region/profile counts as ready.
    """
    provider = get_llm_provider()
    if provider == "anthropic":
        return (bool(os.getenv("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY not set")
    if provider == "openai":
        return (bool(get_openai_api_key()), "OPENAI_API_KEY not set")
    if provider == "google":
        return (bool(get_google_api_key()), "GOOGLE_API_KEY not set")
    if provider == "bedrock":
        ok = bool(os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or get_aws_profile())
        return (ok, "AWS credentials/region not configured for Bedrock")
    if provider == "ollama":
        # Local provider — no credentials to check. Server reachability is
        # verified at call time (a down server surfaces an actionable error).
        return (True, "")
    return (bool(os.getenv("ANTHROPIC_API_KEY")), f"No API key configured for provider '{provider}'")


def get_voice_model() -> str:
    """Return the local Whisper model size for voice input.

    Transcription runs on-device via faster-whisper, so this is a model *size*,
    not a cloud model name. Override with the VOICE_MODEL env var. Valid values:
    ``tiny``, ``base`` (default), ``small``, ``medium``, ``large-v3`` (and the
    English-only ``.en`` variants). Larger = more accurate but slower and a
    bigger one-time download.
    """
    return os.getenv("VOICE_MODEL") or "base"


def get_session_prune_days() -> int:
    """Return the number of days after which old sessions are pruned.

    Phase 8C: reads ``SESSION_PRUNE_DAYS`` env var.
    Default is 30. Set to 0 to disable pruning. Invalid values fall back to 30.
    """
    raw = os.getenv("SESSION_PRUNE_DAYS", "30")
    try:
        value = int(raw)
        return value if value >= 0 else 30
    except ValueError:
        return 30


def get_log_level() -> str:
    """Return the configured log level for the file logger.

    Reads ``LOG_LEVEL`` from .env. Defaults to ``WARNING``.
    Valid values: DEBUG, INFO, WARNING, ERROR.
    Invalid values fall back to WARNING.
    """
    raw = os.getenv("LOG_LEVEL", "WARNING").upper()
    if raw in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return raw
    return "WARNING"


# Levels settable from the Settings page cycle button. CRITICAL stays readable
# from .env for power users but is deliberately not part of the cycle.
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def set_log_level(level: str) -> None:
    """Persist LOG_LEVEL to ~/.yeaboi/.env and apply it to the running process.

    Uses dotenv's set_key so only this one key is updated (preserves other
    keys/comments). os.environ is updated too so get_log_level() reflects the
    change immediately. Callers that want live handlers retuned should also
    call yeaboi.logging_setup.apply_level().

    Raises ValueError for levels outside VALID_LOG_LEVELS.
    """
    level = level.upper()
    if level not in VALID_LOG_LEVELS:
        raise ValueError(f"invalid log level: {level}")
    # Route through set_config_value so the shared .env stays locked to 0o600.
    config_file = set_config_value("LOG_LEVEL", level)
    os.environ["LOG_LEVEL"] = level
    logger.info("Log level set to %s (persisted to %s)", level, config_file)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_team_analysis_jira_dev_links_enabled() -> bool:
    """When true, team analysis calls Jira dev-status for linked PRs/repos (extra API calls)."""
    return _env_truthy("TEAM_ANALYSIS_JIRA_DEV_LINKS")


def is_team_analysis_azdo_pr_search_enabled() -> bool:
    """When true, team analysis scans AzDO Git PRs for work item links / branch names (extra API calls)."""
    return _env_truthy("TEAM_ANALYSIS_AZDO_BRANCH_SEARCH")


def get_team_analysis_azdo_pr_search_max_repos() -> int:
    """Max Git repos to scan per analysis when branch/PR search is enabled (1–50, default 10)."""
    raw = os.getenv("TEAM_ANALYSIS_AZDO_PR_SEARCH_MAX_REPOS", "10")
    try:
        return max(1, min(int(raw), 50))
    except ValueError:
        return 10


def get_team_analysis_azdo_pr_search_top() -> int:
    """Max pull requests per repo per status when PR search is enabled (10–200, default 75)."""
    raw = os.getenv("TEAM_ANALYSIS_AZDO_PR_SEARCH_PRS_PER_REPO", "75")
    try:
        return max(10, min(int(raw), 200))
    except ValueError:
        return 75


def get_team_analysis_azdo_repo_allowlist() -> frozenset[str] | None:
    """Optional comma-separated repo names (lowercase) to limit PR search; None = all repos up to max."""
    raw = os.getenv("TEAM_ANALYSIS_AZDO_REPO_ALLOWLIST", "").strip()
    if not raw:
        return None
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def disable_langsmith_tracing() -> None:
    """Disable LangSmith by unsetting LANGSMITH_TRACING in the current process.

    LangSmith reads LANGSMITH_TRACING from os.environ at runtime, so removing
    it prevents the SDK from attempting to send traces for the rest of the process.
    """
    logger.info("Disabling LangSmith tracing for this process")
    os.environ.pop("LANGSMITH_TRACING", None)
