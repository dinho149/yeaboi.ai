"""OpenAI-wire-protocol vendors: the facts a call needs, as data.

Six vendors — xAI, DeepSeek, Moonshot, Mistral, Alibaba Qwen and Z.ai — all
serve chat completions over the OpenAI wire protocol at their own host. That
makes them one ``ChatOpenAI(base_url=...)`` branch in ``agent/llm.py`` rather
than six, and it is why none of them adds a dependency: they ride the existing
``langchain-openai`` extra.

This table is the single place a seventh such vendor is added. Everything
downstream derives from it: ``_PROVIDER_DEFAULTS`` and the factory branch in
``agent/llm.py``, the wizard cards in ``ui/provider_select/_constants.py``, the
credential accessors in ``config.py``, and the four verification chains in
``provider_verification.py``.

Model ids in this space churn fast — DeepSeek retired its ``deepseek-chat``
alias, Moonshot retired the original ``kimi-k2`` series. ``presets`` is
therefore a starting point, not a contract: the wizard merges live
``GET {base_url}/models`` results ahead of it, and a ``Custom…`` entry accepts
any id the user's key can reach.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAICompatible:
    """One vendor reachable through the OpenAI wire protocol at its own host."""

    key: str
    """The ``LLM_PROVIDER`` value, and the wizard card's ``provider_val``."""

    label: str
    """Short display name — the wizard renders it as ASCII art, so keep it one word."""

    full_name: str
    key_env: str
    base_url: str
    base_url_env: str
    """Per-install override, for a moved endpoint or a regional host."""

    default_model: str
    presets: tuple[str, ...]
    console_url: str
    key_scope: str
    tagline: str
    key_prefix: str = ""
    fast_model: str = ""
    """Cheap model for Analysis coaching and guardrail classification; "" = none."""


# Base URLs verified against a live unauthenticated GET /models (each answers
# 401, not 404). Z.ai speaks OpenAI at its PaaS v4 path, not an /openai/v1 one.
OPENAI_COMPATIBLE: dict[str, OpenAICompatible] = {
    "xai": OpenAICompatible(
        key="xai",
        label="Grok",
        full_name="xAI (Grok)",
        key_env="XAI_API_KEY",
        base_url="https://api.x.ai/v1",
        base_url_env="XAI_BASE_URL",
        default_model="grok-4.6",
        presets=("grok-4.6", "grok-4.5", "grok-4.3", "grok-4.1-fast"),
        fast_model="grok-4.1-fast",
        key_prefix="xai-",
        console_url="https://console.x.ai",
        key_scope="A default key works — every Grok model runs on it",
        tagline="Frontier cloud · API key required",
    ),
    "deepseek": OpenAICompatible(
        key="deepseek",
        label="DeepSeek",
        full_name="DeepSeek",
        key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        base_url_env="DEEPSEEK_BASE_URL",
        default_model="deepseek-v4-pro",
        presets=("deepseek-v4-pro", "deepseek-v4-flash"),
        fast_model="deepseek-v4-flash",
        key_prefix="sk-",
        console_url="https://platform.deepseek.com",
        key_scope="A default key works — off-peak requests bill less",
        tagline="Open-weight · very low cost · API key required",
    ),
    "moonshot": OpenAICompatible(
        key="moonshot",
        label="Kimi",
        full_name="Moonshot (Kimi)",
        key_env="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.ai/v1",
        base_url_env="MOONSHOT_BASE_URL",
        default_model="kimi-k2.6",
        presets=("kimi-k2.6", "kimi-k3", "kimi-k2.7-code"),
        fast_model="kimi-k2.6",
        key_prefix="sk-",
        console_url="https://platform.moonshot.ai",
        key_scope="A default key works — MOONSHOT_BASE_URL selects the .cn host",
        tagline="Open-weight · long context · API key required",
    ),
    "mistral": OpenAICompatible(
        key="mistral",
        label="Mistral",
        full_name="Mistral AI",
        key_env="MISTRAL_API_KEY",
        base_url="https://api.mistral.ai/v1",
        base_url_env="MISTRAL_BASE_URL",
        default_model="mistral-large-latest",
        presets=(
            "mistral-large-latest",
            "mistral-small-latest",
            "magistral-medium-latest",
            "codestral-latest",
        ),
        fast_model="mistral-small-latest",
        console_url="https://console.mistral.ai/api-keys",
        key_scope="A default key works — '-latest' ids track each release",
        tagline="European · open weights · API key required",
    ),
    "qwen": OpenAICompatible(
        key="qwen",
        label="Qwen",
        full_name="Alibaba (Qwen)",
        key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        base_url_env="DASHSCOPE_BASE_URL",
        default_model="qwen-max",
        presets=("qwen-max", "qwen-plus", "qwen-flash", "qwen3-max"),
        fast_model="qwen-flash",
        key_prefix="sk-",
        console_url="https://bailian.console.alibabacloud.com",
        key_scope="A Model Studio key — DASHSCOPE_BASE_URL selects the region",
        tagline="Open-weight · multilingual · API key required",
    ),
    "zai": OpenAICompatible(
        key="zai",
        label="GLM",
        full_name="Z.ai (GLM)",
        key_env="ZAI_API_KEY",
        base_url="https://api.z.ai/api/paas/v4",
        base_url_env="ZAI_BASE_URL",
        default_model="glm-5.2",
        presets=("glm-5.2", "glm-4.6"),
        console_url="https://z.ai/manage-apikey/apikey-list",
        key_scope="A default key works — ZAI_BASE_URL selects the bigmodel.cn host",
        tagline="Open-weight · long context · API key required",
    ),
}


def spec(provider: str) -> OpenAICompatible | None:
    """The spec for ``provider``, or None when it is not an OpenAI-wire vendor."""
    return OPENAI_COMPATIBLE.get((provider or "").strip().lower())


def base_url_for(provider: str) -> str:
    """The endpoint a call should use — ``<PROVIDER>_BASE_URL`` overrides the default."""
    found = spec(provider)
    if found is None:
        return ""
    return (os.getenv(found.base_url_env) or "").strip() or found.base_url
