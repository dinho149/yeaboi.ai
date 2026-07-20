"""Configuration persistence for the provider selection wizard.

# See docs: "Architecture" — saves collected provider settings
# incrementally to ~/.scrum-agent/.env during the setup wizard.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _save_progress(data: dict[str, str]) -> None:
    """Incrementally save collected values to ~/.scrum-agent/.env.

    Merges with existing config so we never lose previously saved values.
    When Bedrock is the provider, auto-detects the model ID from OpenClaw's
    config so yeaboi uses the correct model (e.g. global.anthropic.claude-sonnet-4-6).
    """
    from yeaboi.config import get_config_file

    config_file = get_config_file()
    existing: dict[str, str] = {}
    if config_file.exists():
        for line in config_file.read_text().splitlines():
            stripped = line.strip()
            if stripped and "=" in stripped and not stripped.startswith("#"):
                k, _, v = stripped.partition("=")
                existing[k.strip()] = v.strip()
    merged = {**existing, **data}

    # A model belongs to its provider: switching provider without picking a new
    # model yet must drop the old one, or an abandoned model phase leaves e.g.
    # LLM_PROVIDER=anthropic + LLM_MODEL=qwen3:8b, which the next reconfigure
    # then offers as "(current)" under the wrong provider.
    new_provider = data.get("LLM_PROVIDER")
    if new_provider and new_provider != existing.get("LLM_PROVIDER") and "LLM_MODEL" not in data:
        merged.pop("LLM_MODEL", None)
        # update() below filters falsy values, so an explicit pop is load-bearing
        # (get_llm_model() reads os.environ directly).
        os.environ.pop("LLM_MODEL", None)
        logger.info("provider switched to %s — cleared stale LLM_MODEL", new_provider)

    # Auto-detect Bedrock model from OpenClaw if not already set
    if merged.get("LLM_PROVIDER") == "bedrock" and "LLM_MODEL" not in merged:
        from yeaboi.setup_wizard import _detect_openclaw_bedrock_model

        detected = _detect_openclaw_bedrock_model()
        if detected:
            merged["LLM_MODEL"] = detected

    lines = [f"{k}={v}\n" for k, v in merged.items() if v]
    config_file.write_text("".join(lines))
    os.environ.update({k: v for k, v in merged.items() if v})
