"""First-run setup wizard for yeaboi.ai credentials.

# See docs: "Architecture" — the CLI layer is responsible for user-facing
# chrome. The wizard runs once before any REPL loop starts, collecting
# credentials and storing them in ~/.yeaboi/.env for future sessions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from prompt_toolkit import prompt
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from yeaboi.config import get_config_file, restrict_permissions
from yeaboi.ui.provider_select import select_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

# Each entry: display name, env var for the API key, value written to
# LLM_PROVIDER, expected key prefix (for soft format validation), and
# a URL hint so users know where to get the key.
_PROVIDERS: dict[str, dict[str, str]] = {
    "1": {
        "name": "Anthropic (Claude)",
        "env_var": "ANTHROPIC_API_KEY",
        "provider_val": "anthropic",
        "prefix": "sk-ant-",
        "instructions": "Get yours at: https://console.anthropic.com → API Keys",
    },
    "2": {
        "name": "OpenAI (GPT)",
        "env_var": "OPENAI_API_KEY",
        "provider_val": "openai",
        "prefix": "sk-",
        "instructions": "Get yours at: https://platform.openai.com → API keys",
    },
    "3": {
        "name": "Google (Gemini)",
        "env_var": "GOOGLE_API_KEY",
        "provider_val": "google",
        "prefix": "AIza",
        "instructions": "Get yours at: https://aistudio.google.com → Get API key",
    },
    "4": {
        "name": "AWS (Bedrock)",
        "env_var": "AWS_REGION",
        "provider_val": "bedrock",
        "prefix": "",
        "instructions": "Uses IAM credentials from instance role, ~/.aws/credentials, or env vars",
    },
    "5": {
        "name": "Ollama (Local & Free — no API key)",
        "env_var": "OLLAMA_BASE_URL",
        "provider_val": "ollama",
        "prefix": "",
        "instructions": "Free, private, no API key. Install: https://ollama.com then run: ollama pull qwen3:8b",
        # Not a secret — the local server URL; Enter accepts this default.
        "default_input": "http://localhost:11434",
    },
}


def _detect_openclaw_bedrock_model() -> str | None:
    """Try to detect the Bedrock model ID from OpenClaw's models.json.

    Returns the model ID string (e.g. 'global.anthropic.claude-sonnet-4-6')
    or None if OpenClaw is not installed or no Bedrock model is configured.
    """
    import json

    models_json = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "models.json"
    if not models_json.exists():
        return None

    try:
        config = json.loads(models_json.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    providers = config.get("providers", {})
    bedrock = providers.get("bedrock") or providers.get("amazon-bedrock") or {}
    models = bedrock.get("models", [])

    if models:
        model_id = models[0].get("id", "")
        if model_id:
            return model_id

    # Fallback: scan all providers for any model with "anthropic" or "claude"
    for prov in providers.values():
        if isinstance(prov, dict):
            for m in prov.get("models", []):
                mid = m.get("id", "")
                if "anthropic" in mid or "claude" in mid:
                    return mid

    return None


def is_first_run() -> bool:
    """Return True if ~/.yeaboi/.env is missing or has no key=value entries.

    A file with only whitespace or blank lines is treated as empty — this
    handles the case where save_config() writes a trailing newline but no
    actual credentials (e.g. user cancelled mid-wizard).
    """
    config = get_config_file()
    if not config.exists():
        return True
    content = config.read_text().strip()
    return len(content) == 0


def save_config(data: dict[str, str]) -> Path:
    """Write key=value pairs to ~/.yeaboi/.env.

    Overwrites the file — safe because we read existing values first
    and merge them in run_setup_wizard() before calling save_config().
    Returns the path written.
    """
    config_file = get_config_file()
    lines = [f"{k}={v}\n" for k, v in data.items() if v]
    config_file.write_text("".join(lines))
    # This file holds API keys/tokens in plaintext — lock it to owner-only (0o600).
    restrict_permissions(config_file, mode=0o600)
    logger.info("Config saved to %s (keys: %s)", config_file, ", ".join(data.keys()))
    return config_file


def _read_existing_config(config_file: Path) -> dict[str, str]:
    """Parse key=value pairs from an existing config file."""
    if not config_file.exists():
        return {}
    result: dict[str, str] = {}
    for line in config_file.read_text().splitlines():
        stripped = line.strip()
        if stripped and "=" in stripped and not stripped.startswith("#"):
            k, _, v = stripped.partition("=")
            result[k.strip()] = v.strip()
    return result


def _collect_provider(console: Console) -> dict[str, str]:
    """Show full-screen provider selection and return the chosen provider info dict.

    # See docs: "Architecture" — the CLI layer owns user-facing chrome.
    # This delegates to the full-screen Rich Live provider selector for an
    # interactive arrow-key selection experience.

    Falls back to inline text prompts if the terminal doesn't support raw mode
    (e.g. during testing when stdin isn't a real TTY).
    """
    result = select_provider(console)
    if result is not None:
        return result

    # User cancelled (q/Esc) — fall back to default (Anthropic)
    return _PROVIDERS["1"]


def _collect_api_key(console: Console, provider: dict[str, str]) -> str | None:
    """Prompt for the API key with format validation and retry loop.

    - Empty input → prints error, returns None (caller should return False)
    - Wrong prefix → warns and asks to re-enter; typing 'n' accepts the key as-is
    - Correct format → returns key immediately

    Returns the key string, or None if user provided an empty key.
    """
    console.print("\n[bold]Step 2/3[/bold] API Key [required]")
    console.print(f"  {provider['instructions']}")

    # Keyless providers (Ollama): the value is a server URL with a sensible
    # default, not a secret — Enter accepts the default, input is unmasked.
    default_input = provider.get("default_input", "")
    if default_input:
        # langchain-ollama is an optional extra — warn here rather than letting
        # the first planning run crash with an ImportError after a green setup.
        if provider.get("provider_val") == "ollama":
            import importlib.util

            if importlib.util.find_spec("langchain_ollama") is None:
                console.print(
                    "[yellow]Ollama support isn't installed — run: uv sync --extra ollama "
                    "(or: pip install langchain-ollama) before your first planning run.[/yellow]"
                )
        value = prompt(f"  {provider['env_var']} [{default_input}]: ").strip()
        return value or default_input

    while True:
        key = prompt(f"  {provider['env_var']}: ", is_password=True).strip()
        if not key:
            console.print(f"[red]{provider['env_var']} is required. Exiting setup.[/red]")
            return None
        if not key.startswith(provider["prefix"]):
            console.print(
                f"[yellow]Warning: key doesn't look like a {provider['name']} key "
                f"(expected prefix: {provider['prefix']}...).[/yellow]"
            )
            retry = prompt("  Re-enter key? [Y/n]: ").strip().lower()
            if retry != "n":
                continue  # re-prompt for key
        return key  # valid format, or user explicitly declined retry


def run_setup_wizard(console: Console) -> bool:
    """Interactive credential setup wizard.

    Returns True if setup completed successfully, False if user cancelled.
    Collected values are written to ~/.yeaboi/.env and then loaded
    into the current process via os.environ so they're immediately active.
    """
    logger.info("Setup wizard started")
    config_file = get_config_file()

    # Welcome panel
    body = Text.from_markup(
        "[bold cyan]Welcome to yeaboi.ai — First-Time Setup[/bold cyan]\n\n"
        "We'll set up your AI provider now — cloud (API key) or free local\n"
        "(Ollama, no key needed). Everything is stored locally in\n"
        "[cyan]~/.yeaboi/.env[/cyan] — never sent anywhere else."
    )
    console.print(Panel(body, border_style="cyan", padding=(1, 2)))

    collected: dict[str, str] = {}

    # ── Steps 1 & 2: Provider selection + API key (full-screen UI) ──────────
    existing = _read_existing_config(config_file)
    result = select_provider(console, existing_config=existing)
    if result is None:
        logger.info("Setup wizard cancelled by user")
        return False

    # If the full-screen UI returned an api_key, use it directly.
    # Otherwise fall back to the inline prompt.
    provider = result
    collected["LLM_PROVIDER"] = provider["provider_val"]

    api_key = result.get("api_key")
    if api_key:
        collected[provider["env_var"]] = api_key
    else:
        key = _collect_api_key(console, provider)
        if key is None:
            return False
        collected[provider["env_var"]] = key

    # Model choice from the full-screen model-selection step (LLM_MODEL is also
    # persisted at selection time by _save_progress; this keeps the path explicit).
    llm_model = result.get("llm_model")
    if llm_model:
        collected["LLM_MODEL"] = llm_model

    # ── Step 3: Version control (collected in full-screen UI) ─────────────
    vc_env_var = result.get("vc_env_var")
    vc_token = result.get("vc_token")
    if vc_env_var and vc_token:
        collected[vc_env_var] = vc_token

    # ── Step 4: Issue tracking (collected in full-screen UI) ────────────
    issue_tracking = result.get("issue_tracking", {})
    collected.update(issue_tracking)

    # ── Docs (optional, collected in full-screen UI) ────────────────────
    # Notion has its own token; Confluence adds only CONFLUENCE_SPACE_KEY on top of
    # the Jira Atlassian creds already merged from `issue_tracking` above.
    notion = result.get("notion", {})
    collected.update(notion)
    confluence = result.get("confluence", {})
    collected.update(confluence)

    # ── Bedrock: auto-detect model from OpenClaw if available ───────────────
    # OpenClaw's models.json has the exact Bedrock model ID (e.g.
    # global.anthropic.claude-sonnet-4-6). Without this, yeaboi falls
    # back to a hardcoded default that may not exist in the user's region.
    if collected.get("LLM_PROVIDER") == "bedrock" and "LLM_MODEL" not in collected:
        detected_model = _detect_openclaw_bedrock_model()
        if detected_model:
            collected["LLM_MODEL"] = detected_model

    # ── Merge with existing config and save ─────────────────────────────────
    # collected values win over existing so --setup re-runs update keys
    existing = _read_existing_config(config_file)
    merged = {**existing, **collected}
    # Switching provider without an explicit model drops the old provider's
    # model — a stale pair like LLM_PROVIDER=anthropic + LLM_MODEL=qwen3:8b
    # would surface as a bogus "(current)" entry on the next reconfigure.
    if (
        collected.get("LLM_PROVIDER")
        and collected["LLM_PROVIDER"] != existing.get("LLM_PROVIDER")
        and "LLM_MODEL" not in collected
    ):
        merged.pop("LLM_MODEL", None)
        os.environ.pop("LLM_MODEL", None)
    save_config(merged)

    # Load into current process so keys are immediately active for this session
    os.environ.update({k: v for k, v in merged.items() if v})

    console.print(f"\n[green]Setup complete! Config saved to {config_file}[/green]")

    # Onboarding tip: voice input is optional and off by default. Mention it so
    # new users discover they can dictate answers instead of typing. Skipped
    # entirely when the user has switched tips off.
    from yeaboi.config import is_tips_enabled
    from yeaboi.voice import is_voice_available, voice_install_command

    if is_tips_enabled():
        if is_voice_available():
            console.print("[dim]🎤 Voice input is ready — double-tap Space in any text field to dictate.[/dim]")
        else:
            cmd = voice_install_command()
            console.print(
                "[dim]🎤 Tip: enable voice input to dictate answers — "
                f"run [/dim][cyan]{cmd}[/cyan][dim] (works offline, any LLM provider).[/dim]"
            )

    logger.info("Setup wizard completed successfully")
    return True
