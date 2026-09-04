"""The settings engine: every configurable value, read masked and written allowlisted.

The registry below (:data:`SETTING_FIELDS`) is the settings vocabulary — the
same inventory the TUI settings page renders, declared once as data so a wire
surface can serve it. ``tests/unit/test_settings_engine.py`` cross-checks it
against the TUI's own tables, so the two cannot drift apart silently.

Reads (:func:`get_settings`) mask secrets — a secret's real value never leaves
this module; over the wire it is write-only. Writes (:func:`set_setting`) are
allowlisted to the registry and go through ``config.apply_config_value`` so the
running process and ~/.yeaboi/.env stay in step. ``YEABOI_HOME`` and
``YEABOI_ALLOWED_PATHS`` have dedicated writers because each carries a decision
a bare key=value cannot (moving the data tree; a path list).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _connector_secret_envs() -> frozenset[str]:
    """The secrets the connector registry declares.

    Derived rather than restated so a new connector cannot ship unmasked — the
    descriptor is the only place its secrets are named.
    """
    from yeaboi.connectors import registry

    return registry.secret_envs()


#: env vars whose value is a credential: masked on read, write-only over a wire.
SECRET_ENVS: frozenset[str] = (
    frozenset(
        {
            "ANTHROPIC_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "XAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "MOONSHOT_API_KEY",
            "MISTRAL_API_KEY",
            "DASHSCOPE_API_KEY",
            "ZAI_API_KEY",
            "JIRA_API_TOKEN",
            "AZURE_DEVOPS_TOKEN",
            "GITHUB_TOKEN",
            "NOTION_TOKEN",
            "SLACK_WEBHOOK_URL",
            "SLACK_BOT_TOKEN",
            "STANDUP_SMTP_PASSWORD",
            "ELEVENLABS_API_KEY",
            "TAVUS_API_KEY",
        }
    )
    | _connector_secret_envs()
)


@dataclass(frozen=True)
class SettingField:
    """One configurable env var: where it belongs and how it behaves."""

    env: str
    label: str
    section: str
    secret: bool = False
    choices: tuple[str, ...] = ()
    choice_labels: dict[str, str] = field(default_factory=dict)
    default: str = ""
    # A non-empty action names a dedicated flow instead of a plain text write:
    # 'signin' (subscription sign-in), 'data-dir' (set_data_dir), 'allowed-paths'
    # (set_allowed_paths), 'voice-device' (picker over the snapshot's device list).
    action: str = ""


def _provider_tables() -> tuple[tuple[str, ...], dict[str, str], dict[str, str]]:
    """``(provider values, provider -> credential env, provider -> display name)``.

    Derived from the setup wizard's cards so the engine, the wizard and the TUI
    can never disagree about which providers exist. Data-only import.
    """
    from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS

    order = tuple(c["provider_val"] for c in _PROVIDER_CARDS)
    envs = {c["provider_val"]: c["env_var"] for c in _PROVIDER_CARDS}
    names = {c["provider_val"]: c["name"] for c in _PROVIDER_CARDS}
    return order, envs, names


def _build_fields() -> tuple[SettingField, ...]:
    providers, _envs, _names = _provider_tables()
    from yeaboi.ambience import DEFAULT_SAVER_STYLE, SAVER_STYLES
    from yeaboi.config import VALID_LOG_LEVELS

    on_off = {"true": "on", "false": "off"}
    return (
        # -- provider ------------------------------------------------------
        SettingField("LLM_PROVIDER", "Provider", "provider", choices=providers, default="anthropic"),
        SettingField("LLM_MODEL", "Model", "provider"),
        SettingField(
            "ANTHROPIC_AUTH_MODE",
            "Auth",
            "provider",
            choices=("api_key", "subscription"),
            choice_labels={"api_key": "api key", "subscription": "subscription"},
            default="api_key",
        ),
        SettingField("ANTHROPIC_API_KEY", "Anthropic Key", "provider", secret=True),
        SettingField("CLAUDE_CODE_OAUTH_TOKEN", "Subscription", "provider", secret=True, action="signin"),
        SettingField("OPENAI_API_KEY", "OpenAI Key", "provider", secret=True),
        SettingField("GOOGLE_API_KEY", "Gemini Key", "provider", secret=True),
        SettingField("XAI_API_KEY", "Grok Key", "provider", secret=True),
        SettingField("DEEPSEEK_API_KEY", "DeepSeek Key", "provider", secret=True),
        SettingField("MOONSHOT_API_KEY", "Kimi Key", "provider", secret=True),
        SettingField("MISTRAL_API_KEY", "Mistral Key", "provider", secret=True),
        SettingField("DASHSCOPE_API_KEY", "Qwen Key", "provider", secret=True),
        SettingField("ZAI_API_KEY", "GLM Key", "provider", secret=True),
        SettingField("AWS_REGION", "AWS Region", "provider"),
        SettingField("AWS_PROFILE", "AWS Profile", "provider"),
        SettingField("OLLAMA_BASE_URL", "Ollama URL", "provider"),
        SettingField("OLLAMA_NUM_CTX", "Ollama Context", "provider"),
        # -- jira ----------------------------------------------------------
        SettingField("JIRA_BASE_URL", "Base URL", "jira"),
        SettingField("JIRA_EMAIL", "Email", "jira"),
        SettingField("JIRA_API_TOKEN", "API Token", "jira", secret=True),
        SettingField("JIRA_PROJECT_KEY", "Project Key", "jira"),
        SettingField("CONFLUENCE_SPACE_KEY", "Confluence Space", "jira"),
        # -- azure ---------------------------------------------------------
        SettingField("AZURE_DEVOPS_ORG_URL", "Org URL", "azure"),
        SettingField("AZURE_DEVOPS_PROJECT", "Project", "azure"),
        SettingField("AZURE_DEVOPS_TOKEN", "PAT", "azure", secret=True),
        SettingField("AZURE_DEVOPS_TEAM", "Team", "azure"),
        # -- github --------------------------------------------------------
        SettingField("GITHUB_TOKEN", "Token", "github", secret=True),
        SettingField("TEAM_ANALYSIS_GITHUB_OWNERS", "Analysis Owners", "github"),
        # -- notion --------------------------------------------------------
        SettingField("NOTION_TOKEN", "Token", "notion", secret=True),
        SettingField("NOTION_ROOT_PAGE_ID", "Root Page/DB", "notion"),
        # -- slack ---------------------------------------------------------
        SettingField("SLACK_WEBHOOK_URL", "Webhook URL", "slack", secret=True),
        SettingField("SLACK_BOT_TOKEN", "Bot Token", "slack", secret=True),
        SettingField("SLACK_CHANNEL_ID", "Channel ID", "slack"),
        SettingField("SLACK_ALLOWED_MEMBER_IDS", "Who may act", "slack"),
        # -- sharing -------------------------------------------------------
        SettingField("TUNNEL_TIMEOUT_MINUTES", "Tunnel Timeout (min)", "sharing", default="60"),
        SettingField(
            "YEABOI_SHARE_MODE",
            "Share Mode",
            "sharing",
            choices=("quick", "access"),
            choice_labels={"quick": "quick (code-gated)", "access": "access (verified users)"},
            default="quick",
        ),
        SettingField("CLOUDFLARE_TUNNEL_ID", "Tunnel ID", "sharing"),
        SettingField("CLOUDFLARE_TUNNEL_CREDENTIALS", "Tunnel Credentials", "sharing"),
        SettingField("CLOUDFLARE_ACCESS_HOSTNAME", "Access Hostname", "sharing"),
        SettingField("CLOUDFLARE_ACCESS_TEAM", "Access Team", "sharing"),
        SettingField("CLOUDFLARE_ACCESS_AUD", "Access AUD", "sharing"),
        SettingField("CLOUDFLARE_ACCESS_ADMIN_EMAILS", "Access Admins", "sharing"),
        # -- storage -------------------------------------------------------
        SettingField("YEABOI_HOME", "Data Directory", "storage", action="data-dir"),
        SettingField("YEABOI_ALLOWED_PATHS", "Allowed Paths", "storage", action="allowed-paths"),
        # -- standup -------------------------------------------------------
        SettingField("STANDUP_GITHUB_REPO", "GitHub Repo", "standup"),
        SettingField("STANDUP_SMTP_HOST", "SMTP Host", "standup"),
        SettingField("STANDUP_SMTP_USER", "SMTP User", "standup"),
        SettingField("STANDUP_SMTP_PASSWORD", "SMTP Password", "standup", secret=True),
        SettingField("STANDUP_EMAIL_RECIPIENTS", "Email Recipients", "standup"),
        # -- voice ---------------------------------------------------------
        SettingField("VOICE_INSTALL_OFFER", "Install Offer", "voice"),
        SettingField("VOICE_DEVICE", "Input Device", "voice", action="voice-device"),
        SettingField("VOICE_MODEL", "Model Size", "voice"),
        # Cloud voice/video keys the desktop's call features read from the shared env.
        SettingField("ELEVENLABS_API_KEY", "ElevenLabs Key", "voice", secret=True),
        SettingField("ELEVENLABS_VOICE_ID", "ElevenLabs Voice", "voice"),
        SettingField("ELEVENLABS_MODEL_ID", "ElevenLabs Model", "voice", default="eleven_turbo_v2_5"),
        SettingField("TAVUS_API_KEY", "Tavus Key", "voice", secret=True),
        # -- privacy -------------------------------------------------------
        # The switches the privacy page's egress table names. Disclosure and
        # control side by side: every row here maps to an EGRESS_DISCLOSURES
        # entry in yeaboi.privacy, and the defaults state current behavior —
        # this section changes nothing by existing.
        SettingField(
            "YEABOI_TELEMETRY",
            "Telemetry",
            "privacy",
            choices=("true", "false"),
            choice_labels=on_off,
            default="false",
        ),
        SettingField(
            "YEABOI_UPDATE_CHECK",
            "Update Check",
            "privacy",
            choices=("true", "false"),
            choice_labels=on_off,
            default="true",
        ),
        SettingField(
            "YEABOI_NEWS",
            "Front Page News",
            "privacy",
            choices=("true", "false"),
            choice_labels=on_off,
            default="true",
        ),
        SettingField(
            "YEABOI_NO_TUNNEL",
            "Board Sharing Off-Switch",
            "privacy",
            choices=("true", "false"),
            choice_labels={"true": "tunnels off", "false": "tunnels allowed"},
            default="false",
        ),
        # -- advanced ------------------------------------------------------
        SettingField("LOG_LEVEL", "Log Level", "advanced", choices=VALID_LOG_LEVELS, default="WARNING"),
        SettingField("SESSION_PRUNE_DAYS", "Session Prune Days", "advanced", default="30"),
        SettingField(
            "TIPS_ENABLED", "Tips", "advanced", choices=("true", "false"), choice_labels=on_off, default="true"
        ),
        SettingField(
            "DUCK_ENABLED", "Duck", "advanced", choices=("true", "false"), choice_labels=on_off, default="true"
        ),
        # The front page's yeaboi column reads the channel's public feed; empty means none.
        SettingField("NEWS_YOUTUBE_CHANNEL", "News YouTube Channel", "advanced"),
        SettingField(
            "SAVER_STYLE",
            "Screensaver",
            "advanced",
            choices=tuple(SAVER_STYLES),
            choice_labels=dict(SAVER_STYLES),
            default=DEFAULT_SAVER_STYLE,
        ),
        SettingField(
            "LANGSMITH_TRACING",
            "LangSmith",
            "advanced",
            choices=("true", "false"),
            choice_labels={"true": "enabled", "false": "disabled"},
            default="false",
        ),
        # -- connections ----------------------------------------------------
        # Derived from the connector registry rather than listed: a descriptor
        # is the only place a connector's fields are named.
        *_connector_fields(),
    )


def _connector_fields() -> tuple[SettingField, ...]:
    """One :class:`SettingField` per connector field, from the descriptors."""
    from yeaboi.connectors import registry

    return tuple(
        SettingField(
            f.env,
            f.label if len(c.fields) == 1 else f"{c.label} {f.label}",
            c.section,
            secret=f.secret,
            choices=f.choices,
            default=f.default,
        )
        for c in registry.all_connectors()
        for f in c.fields
    )


_FIELDS_CACHE: tuple[SettingField, ...] | None = None


def _fields() -> tuple[SettingField, ...]:
    global _FIELDS_CACHE
    if _FIELDS_CACHE is None:
        _FIELDS_CACHE = _build_fields()
    return _FIELDS_CACHE


def _invalidate_fields_cache() -> None:
    """Rebuild the field registry on next read.

    A user-created connection changes the write-allowlist and the masking
    table at runtime — the one thing the import-time cache cannot see. The
    custom store calls this after every save/delete. Underscored on purpose:
    it is an infrastructure hook, not a settings capability, and the parity
    suite force-registers every public name in this module.
    """
    global _FIELDS_CACHE
    _FIELDS_CACHE = None


#: section render order — mirrors the TUI's tab/section arrangement.
SECTIONS: tuple[str, ...] = (
    "provider",
    "jira",
    "azure",
    "github",
    "notion",
    "slack",
    "connections",
    "sharing",
    "storage",
    "standup",
    "voice",
    "privacy",
    "advanced",
)


def _mask(value: str) -> str:
    """The TUI's masking rule: keep a 4-char prefix, dot out (at most 12 of) the rest."""
    if not value:
        return ""
    if len(value) > 4:
        return value[:4] + "•" * min(12, len(value) - 4)
    return "•" * len(value)


def _resolve_choice(fld: SettingField, stored: str) -> str:
    """The option a stored value behaves as — same fold as the TUI's resolver."""
    folded = {opt.lower(): opt for opt in fld.choices}
    return folded.get((stored or "").strip().lower(), fld.default)


@dataclass(frozen=True)
class SettingValue:
    """One field with its current (masked when secret) value."""

    env: str
    label: str
    section: str
    secret: bool
    value: str
    is_set: bool
    choices: tuple[str, ...]
    choice_labels: dict[str, str]
    active_choice: str
    default: str
    action: str
    help_url: str
    help_scope: str


@dataclass(frozen=True)
class SettingsSnapshot:
    """Everything a settings surface renders: fields, order, ambient status."""

    fields: tuple[SettingValue, ...]
    sections: tuple[str, ...]
    config_path: str
    voice: dict


@dataclass(frozen=True)
class SettingWrite:
    """The outcome of one write."""

    ok: bool
    key: str
    message: str
    restart_required: bool = False


def _voice_status() -> dict:
    """The voice block: availability worded from the shared vocabulary + devices."""
    try:
        from yeaboi.voice import (
            backend_label,
            list_input_devices,
            unsupported_blocker,
            voice_install_command,
            voice_state,
        )

        state = voice_state()
        if state == "ready":
            detail = f"available — {backend_label()}"
        elif state == "installable":
            detail = "not installed — install from the TUI, or: " + voice_install_command()
        elif state == "unsupported":
            detail = f"unavailable — {unsupported_blocker()}"
        else:
            detail = f"not installed — offer dismissed; {voice_install_command()}"
        return {"state": state, "detail": detail, "devices": list_input_devices()}
    except Exception:  # noqa: BLE001 - voice status must never break the settings read
        logger.warning("settings: voice status probe failed", exc_info=True)
        return {"state": "unknown", "detail": "", "devices": []}


def get_settings() -> SettingsSnapshot:
    """The full settings inventory with current values. Secrets come back masked."""
    from yeaboi.config import get_config_file
    from yeaboi.ui.provider_select._constants import TOKEN_HELP

    values: list[SettingValue] = []
    for fld in _fields():
        raw = os.environ.get(fld.env, "")
        help_entry = TOKEN_HELP.get(fld.env, {})
        values.append(
            SettingValue(
                env=fld.env,
                label=fld.label,
                section=fld.section,
                secret=fld.secret,
                value=_mask(raw) if fld.secret else raw,
                is_set=bool(raw),
                choices=fld.choices,
                choice_labels=dict(fld.choice_labels),
                active_choice=_resolve_choice(fld, raw) if fld.choices else "",
                default=fld.default,
                action=fld.action,
                help_url=help_entry.get("url", ""),
                help_scope=help_entry.get("scope", ""),
            )
        )
    logger.info("settings: snapshot served (%d fields)", len(values))
    return SettingsSnapshot(
        fields=tuple(values), sections=SECTIONS, config_path=str(get_config_file()), voice=_voice_status()
    )


def set_setting(key: str, value: str) -> SettingWrite:
    """Persist one allowlisted ``key=value`` (empty clears it). Raises ValueError
    for keys outside the registry, keys owning a dedicated writer, and values a
    choice field does not offer."""
    from yeaboi import config

    fld = next((f for f in _fields() if f.env == key), None)
    if fld is None:
        raise ValueError(f"unknown setting: {key}")
    if fld.env == "YEABOI_HOME":
        raise ValueError("the data directory is set via set_data_dir — moving the tree is a decision, not a value")
    if fld.env == "YEABOI_ALLOWED_PATHS":
        raise ValueError("the sandbox whitelist is set via set_allowed_paths")
    value = value.strip()
    if value and fld.choices:
        folded = {opt.lower(): opt for opt in fld.choices}
        if value.lower() not in folded:
            raise ValueError(f"{key} must be one of {', '.join(fld.choices)}")
        value = folded[value.lower()]
    if key == "LOG_LEVEL" and value:
        # The one write with a live side effect beyond the env: retune the
        # attached handlers, exactly as the TUI's Log Level cycle does.
        from yeaboi.logging_setup import apply_level

        config.set_log_level(value)
        apply_level(value)
    else:
        config.apply_config_value(key, value)
    # Key names only — the value may be a credential.
    logger.info("settings: %s %s", key, "updated" if value else "cleared")
    if key == "YEABOI_TELEMETRY":
        # telemetry.TELEMETRY_ENABLED is baked at import — the flip is
        # persisted now but only read at the next launch.
        return SettingWrite(
            ok=True,
            key=key,
            message=f"{fld.label} {'updated' if value else 'cleared'} — takes effect at next launch",
            restart_required=True,
        )
    return SettingWrite(ok=True, key=key, message=f"{fld.label} {'updated' if value else 'cleared'}")


def set_allowed_paths(paths: list[str] | tuple[str, ...]) -> SettingWrite:
    """Replace the filesystem-sandbox whitelist (deduplicated, order-preserving)."""
    from yeaboi import config

    if not isinstance(paths, (list, tuple)) or not all(isinstance(p, str) for p in paths):
        raise ValueError("paths must be a list of strings")
    config.set_allowed_paths(list(paths))
    kept = config.get_allowed_paths()
    logger.info("settings: allowed paths set (%d entries)", len(kept))
    return SettingWrite(ok=True, key="YEABOI_ALLOWED_PATHS", message=f"Allowed paths saved ({len(kept)} entries)")


def set_data_dir(value: str, *, move: bool = False) -> SettingWrite:
    """Persist the data-home override ('' clears back to ~/.yeaboi), optionally
    moving the existing tree first. Always restart-required: module-level path
    constants are baked at import, so only a fresh process fully applies it."""
    from yeaboi import config
    from yeaboi.paths import move_data_tree

    value = value.strip()
    message = "Data directory saved"
    if move:
        new_root = Path(value).expanduser() if value else Path.home() / ".yeaboi"
        ok, move_msg = move_data_tree(new_root)
        logger.info("settings: data move to %s → ok=%s (%s)", new_root, ok, move_msg)
        message = move_msg
    config.set_data_dir(value)
    logger.info("settings: data directory set to %r", value)
    return SettingWrite(
        ok=True, key="YEABOI_HOME", message=f"{message} — restart yeaboi to fully apply", restart_required=True
    )


def provider_catalog() -> dict:
    """The setup wizard's provider cards, auth modes and token help — data only.

    Served for the desktop /setup wizard so it renders the same providers, model
    presets and credential help the TUI wizard does. Contains no secrets; the
    ``color`` key is dropped (terminal presentation).
    """
    from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS, TOKEN_HELP

    providers = [{k: v for k, v in card.items() if k != "color"} for card in _PROVIDER_CARDS]
    return {
        "providers": providers,
        "anthropic_auth_modes": ["api_key", "subscription"],
        "token_help": TOKEN_HELP,
    }


def _provider_card(provider: str) -> dict:
    from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS

    card = next((c for c in _PROVIDER_CARDS if c["provider_val"] == provider), None)
    if card is None:
        raise ValueError(f"unknown provider: {provider}")
    return card


# Optional-integration verification: kind -> ((field name, env fallback), ...).
# Confluence reuses the Jira Atlassian account (see tools/confluence.py).
_CONNECTION_KINDS: dict[str, tuple[tuple[str, str], ...]] = {
    "github": (("token", "GITHUB_TOKEN"),),
    "jira": (("base_url", "JIRA_BASE_URL"), ("email", "JIRA_EMAIL"), ("token", "JIRA_API_TOKEN")),
    "confluence": (
        ("base_url", "JIRA_BASE_URL"),
        ("email", "JIRA_EMAIL"),
        ("token", "JIRA_API_TOKEN"),
        ("space_key", "CONFLUENCE_SPACE_KEY"),
    ),
    "notion": (("token", "NOTION_TOKEN"),),
    "elevenlabs": (("token", "ELEVENLABS_API_KEY"),),
    "tavus": (("token", "TAVUS_API_KEY"),),
}


def _connection_kinds() -> dict[str, tuple[tuple[str, str], ...]]:
    """The legacy table plus every connector's derived one.

    The legacy rows stay literal for now: the TUI still names those fields in
    hand-written builders, and deriving both halves at once is a change that
    cannot be reviewed in one piece.
    """
    from yeaboi.connectors import registry

    return {**_CONNECTION_KINDS, **registry.connection_kinds()}


def _verify_field_names() -> tuple[str, ...]:
    """Every field name any connection kind accepts on a verify request.

    The wire's allowlist, derived from the same table verification resolves
    against, so a connector cannot declare a field the HTTP surface silently
    refuses to carry.
    """
    return tuple(sorted({name for spec in _connection_kinds().values() for name, _ in spec}))


def _connector_registry():
    from yeaboi.connectors import registry

    return registry


def _verify_via_descriptor(connector, resolved: dict[str, str]) -> tuple[bool, str]:
    """Call a connector's probe with the arguments its descriptor names.

    ``verify_arg`` fields come from the resolved request; ``env_arg`` fields come
    from the saved environment and never from the request. That split is the
    exfiltration guard: every field that determines a host is an ``env_arg``, so
    a caller cannot pair a stored token with a host of their choosing.
    """
    from yeaboi import provider_verification

    probe = getattr(provider_verification, connector.verify)
    kwargs = {f.verify_arg: resolved[f.verify_arg] for f in connector.fields if f.verify_arg}
    kwargs.update({f.env_arg: os.environ.get(f.env, "").strip() for f in connector.fields if f.env_arg})
    if connector.key.startswith("custom_"):
        # The generic probe serves every custom connection — which one decides
        # the auth scheme and probe path, so the key rides along.
        kwargs["key"] = connector.key
    return probe(**kwargs)


def verify_connection(kind: str, fields: dict[str, str]) -> dict:
    """Live-check one optional integration's credentials.

    Kinds: github/jira/confluence/notion, plus the desktop's voice & face keys
    (elevenlabs/tavus).

    A field omitted from ``fields`` falls back to the stored env value — secrets
    are write-only over the wire, so this is how a caller re-checks a saved
    credential without ever seeing it. A stored token only ever travels to the
    stored host: pairing it with a caller-supplied ``base_url`` or ``email``
    is refused (it would exfiltrate the saved secret in one request), and a
    caller-supplied ``base_url`` must be https. Returns ``{ok, message}``;
    network-bound (up to ~10s). Raises ValueError for an unknown kind or a
    field that is neither provided nor stored. No credential appears in the
    result or the log.
    """
    from yeaboi import provider_verification

    spec = _connection_kinds().get(kind)
    if spec is None:
        raise ValueError(f"unknown connection kind: {kind}")
    resolved: dict[str, str] = {}
    supplied: set[str] = set()
    for name, env in spec:
        value = (fields.get(name) or "").strip()
        if value:
            supplied.add(name)
        else:
            value = os.environ.get(env, "").strip()
        if not value:
            raise ValueError(f"{kind} verification needs {name}")
        resolved[name] = value
    connector = _connector_registry().by_key(kind)
    if connector is not None and connector.verify:
        # The descriptor names which verify fields are secret; pairing a
        # caller-supplied host with ANY stored secret would exfiltrate it.
        secret_args = {f.verify_arg for f in connector.fields if f.verify_arg and f.secret}
    else:
        secret_args = {"token"}
    if supplied & {"base_url", "email"} and not secret_args <= supplied:
        missing = ", ".join(sorted(secret_args - supplied))
        raise ValueError(
            f"{kind} verification with a stored secret also uses the stored base_url and email — "
            f"supply the {missing} to verify against other values"
        )
    if "base_url" in supplied and not resolved["base_url"].startswith("https://"):
        raise ValueError(f"{kind} base_url must start with https://")
    if connector is not None and connector.verify:
        ok, message = _verify_via_descriptor(connector, resolved)
    elif kind == "github":
        ok, message = provider_verification._verify_vc_token({"env_var": "GITHUB_TOKEN"}, resolved["token"])
    elif kind == "jira":
        ok, message = provider_verification._verify_jira(resolved["base_url"], resolved["email"], resolved["token"])
    elif kind == "confluence":
        ok, message = provider_verification._verify_confluence(
            resolved["base_url"], resolved["email"], resolved["token"], resolved["space_key"]
        )
    elif kind == "notion":
        ok, message = provider_verification._verify_notion(resolved["token"])
    elif kind == "elevenlabs":
        ok, message = provider_verification._verify_elevenlabs(resolved["token"])
    else:
        ok, message = provider_verification._verify_tavus(resolved["token"])
    logger.info("settings: connection verify %s → ok=%s", kind, ok)
    return {"ok": ok, "message": message}


def verify_provider(provider: str, credential: str, model: str = "") -> dict:
    """Live-check a credential (and optionally a model id) against its provider.

    Returns ``{ok, message}``. Network-bound (up to ~8s); the caller owns any
    threading. Raises ValueError only for an unknown provider name.
    """
    from yeaboi.provider_verification import _verify_api_key, _verify_model

    card = _provider_card(provider)
    ok, message = _verify_api_key(card, credential)
    logger.info("settings: provider verify %s → ok=%s", provider, ok)
    if ok and model:
        ok, message = _verify_model(card, credential, model)
        logger.info("settings: model verify %s/%s → ok=%s", provider, model, ok)
    return {"ok": ok, "message": message}


def discover_models(provider: str, credential: str) -> dict:
    """Ask the provider what this credential can run; merge in the curated presets.

    Returns ``{models, default, hints}`` where ``models`` is discovered-first with
    any recommended preset appended — the same merge the TUI wizard shows.
    """
    card = _provider_card(provider)
    models_cfg = card.get("models", {})
    curated = list(models_cfg.get("presets", []))
    discovered: list[str] = []
    if provider != "bedrock" and credential:
        from yeaboi.provider_verification import fetch_available_models

        discovered = fetch_available_models(card, credential)
    merged = list(dict.fromkeys([*discovered, *curated]))
    logger.info("settings: model discovery %s → %d discovered, %d offered", provider, len(discovered), len(merged))
    return {"models": merged, "default": models_cfg.get("default", ""), "hints": card.get("model_hints", {})}
