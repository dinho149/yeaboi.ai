"""Per-model LLM pricing and cost estimation.

One shared table for everything that turns token counts into dollars: the Usage
page's lifetime estimate (previously a hardcoded $3/$15 formula) and the
agentwatch cost pipeline that prices monitored agent sessions (Claude Code).
Rates are a dated snapshot — ``PRICING_AS_OF`` travels with every
estimate so a rendered number can always be traced to the table that produced
it. Unknown models fall back to a Sonnet-tier estimate with ``known_model``
False, so callers can surface honesty flags instead of silently guessing.

Matching is longest-prefix over a normalised model id (lowercased, provider
prefixes like ``anthropic.``/``us.anthropic.`` stripped), which absorbs dated
snapshots (``claude-sonnet-4-5-20250929``), regional Bedrock ids and Mistral's
``-latest`` aliases without a row per variant.
"""

from __future__ import annotations

from dataclasses import dataclass

# Date the rate table below was last transcribed from provider pricing pages.
# Surfaced on artifacts so stale numbers are visible rather than silent.
PRICING_AS_OF = "2026-09-05"

# Anthropic cache economics (per the API docs): a 5-minute-TTL cache write
# bills at 1.25x the input rate, a 1-hour write at 2x, and a cache read at
# 0.1x. Claude Code uses both TTLs, so the two write kinds are priced apart.
_CACHE_READ_MULT = 0.10
_CACHE_WRITE_5M_MULT = 1.25
_CACHE_WRITE_1H_MULT = 2.0
# These multipliers are Anthropic's and are applied to every row, but they
# stay inert off Anthropic: only Claude Code session logs report a cache
# token split, so every other provider prices with those counts at zero.

# Long-context premium: a request whose prompt exceeds 200K tokens bills its
# uncached input at 2x and its output at 1.5x on the Anthropic families that
# offer the 1M window. The collector counts such a request's input/output
# under ``premium_*``; the surcharge below is the *extra* over the base rate.
LONG_CONTEXT_THRESHOLD = 200_000
_LONG_CONTEXT_INPUT_MULT = 2.0
_LONG_CONTEXT_OUTPUT_MULT = 1.5

# Server-side tools Claude Code can call inside a request, priced per call
# (web search: $10 per 1,000; web fetch: no per-call charge).
WEB_SEARCH_USD_PER_CALL = 0.01
WEB_FETCH_USD_PER_CALL = 0.0


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens for one model family."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    @property
    def cache_read_per_mtok(self) -> float:
        return self.input_per_mtok * _CACHE_READ_MULT

    @property
    def cache_write_5m_per_mtok(self) -> float:
        return self.input_per_mtok * _CACHE_WRITE_5M_MULT

    @property
    def cache_write_1h_per_mtok(self) -> float:
        return self.input_per_mtok * _CACHE_WRITE_1H_MULT


@dataclass(frozen=True)
class CostEstimate:
    """A priced token bundle plus the honesty metadata callers surface."""

    usd: float = 0.0
    known_model: bool = True
    matched_prefix: str = ""
    pricing_as_of: str = PRICING_AS_OF
    cache_usd: float = 0.0  # the share of ``usd`` that was cache reads + writes
    tools_usd: float = 0.0  # per-call server tools (web search / fetch)


# Longest-prefix table. Order does not matter (matching sorts by length), but
# keep families grouped for review. Sources: platform.claude.com pricing (via
# the claude-api reference), openai.com/api/pricing, ai.google.dev/pricing.
_PRICES: dict[str, ModelPrice] = {
    # Anthropic — current
    "claude-fable-5": ModelPrice(10.0, 50.0),
    "claude-mythos": ModelPrice(10.0, 50.0),
    "claude-opus-5": ModelPrice(5.0, 25.0),
    "claude-opus-4": ModelPrice(5.0, 25.0),  # 4.5/4.6/4.7/4.8
    "claude-sonnet-5": ModelPrice(3.0, 15.0),
    "claude-sonnet-4": ModelPrice(3.0, 15.0),
    "claude-haiku-4-5": ModelPrice(1.0, 5.0),
    # Anthropic — legacy ids still present in old ledgers/transcripts.
    # claude-opus-4-1 / claude-opus-4-20250514 predate the Opus price drop.
    "claude-opus-4-1": ModelPrice(15.0, 75.0),
    "claude-opus-4-2025": ModelPrice(15.0, 75.0),
    "claude-3-opus": ModelPrice(15.0, 75.0),
    "claude-3-7-sonnet": ModelPrice(3.0, 15.0),
    "claude-3-5-sonnet": ModelPrice(3.0, 15.0),
    "claude-3-5-haiku": ModelPrice(0.8, 4.0),
    "claude-3-haiku": ModelPrice(0.25, 1.25),
    # OpenAI
    "gpt-5-nano": ModelPrice(0.05, 0.4),
    "gpt-5-mini": ModelPrice(0.25, 2.0),
    "gpt-5": ModelPrice(1.25, 10.0),
    "gpt-4o-mini": ModelPrice(0.15, 0.6),
    "gpt-4o": ModelPrice(2.5, 10.0),
    "gpt-4.1-nano": ModelPrice(0.1, 0.4),
    "gpt-4.1-mini": ModelPrice(0.4, 1.6),
    "gpt-4.1": ModelPrice(2.0, 8.0),
    "o3": ModelPrice(2.0, 8.0),
    # Google
    "gemini-2.5-pro": ModelPrice(1.25, 10.0),
    "gemini-2.5-flash": ModelPrice(0.3, 2.5),
    "gemini-2.0-flash": ModelPrice(0.1, 0.4),
    # xAI
    "grok-4": ModelPrice(3.0, 15.0),
    # DeepSeek — peak rates, so an estimate never under-reports (off-peak is
    # roughly half, and which window a call landed in is not recorded).
    "deepseek-v4-pro": ModelPrice(1.32, 3.96),
    "deepseek-v4-flash": ModelPrice(0.44, 1.32),
    # Moonshot
    "kimi-k3": ModelPrice(3.0, 15.0),
    "kimi-k2": ModelPrice(0.95, 4.0),
    # Mistral
    "mistral-large": ModelPrice(0.5, 1.5),
    "mistral-small": ModelPrice(0.15, 0.6),
    "magistral": ModelPrice(2.0, 5.0),
    "codestral": ModelPrice(0.3, 0.9),
    "ministral-8b": ModelPrice(0.15, 0.15),
    "ministral-3b": ModelPrice(0.1, 0.1),
    "pixtral": ModelPrice(2.0, 6.0),
    # Alibaba Qwen
    "qwen3-max": ModelPrice(2.0, 6.0),
    "qwen-max": ModelPrice(2.0, 6.0),
    "qwen-plus": ModelPrice(0.4, 1.2),
    "qwen-flash": ModelPrice(0.05, 0.4),
    # Z.ai
    "glm-5": ModelPrice(0.6, 2.2),
    "glm-4": ModelPrice(0.6, 2.2),
}

# Providers whose inference runs on the user's own hardware — no per-token bill.
_FREE_PROVIDERS = frozenset({"ollama", "local"})

# Prefixes partner platforms prepend to Anthropic model ids.
_PROVIDER_ID_PREFIXES = ("us.anthropic.", "eu.anthropic.", "apac.anthropic.", "anthropic.")

# Every non-free unknown model prices at the mid (Sonnet) tier — a deliberate
# middle-of-the-road guess, flagged via known_model=False.
_FALLBACK_PRICE = ModelPrice(3.0, 15.0)


def normalise_model_id(model: str) -> str:
    """Lowercase and strip partner-platform prefixes from a model id."""
    cleaned = (model or "").strip().lower()
    for prefix in _PROVIDER_ID_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned


def lookup_price(model: str) -> tuple[ModelPrice, str]:
    """Longest-prefix match a model id against the table.

    Returns ``(price, matched_prefix)``; ``matched_prefix`` is "" on a miss.
    """
    cleaned = normalise_model_id(model)
    if not cleaned:
        return _FALLBACK_PRICE, ""
    for prefix in sorted(_PRICES, key=len, reverse=True):
        if cleaned.startswith(prefix):
            return _PRICES[prefix], prefix
    return _FALLBACK_PRICE, ""


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    *,
    cache_write_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
    cache_read_tokens: int = 0,
    provider: str = "",
    web_search_calls: int = 0,
    web_fetch_calls: int = 0,
    premium_input_tokens: int = 0,
    premium_output_tokens: int = 0,
) -> CostEstimate:
    """Price a token bundle for one model.

    ``cache_write_tokens`` are 5-minute-TTL writes; pass 1-hour writes
    separately (Claude Code session logs report the split). ``premium_*`` are
    the input/output tokens of requests whose prompt crossed the long-context
    threshold — a subset of ``input_tokens``/``output_tokens``, surcharged on
    top. A free provider (ollama/local) prices to zero and counts as known
    regardless of model.
    """
    if (provider or "").strip().lower() in _FREE_PROVIDERS:
        return CostEstimate(usd=0.0, known_model=True, matched_prefix="")
    price, matched = lookup_price(model)
    cache_usd = (
        cache_write_tokens * price.cache_write_5m_per_mtok
        + cache_write_1h_tokens * price.cache_write_1h_per_mtok
        + cache_read_tokens * price.cache_read_per_mtok
    ) / 1_000_000
    base_usd = (input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1_000_000
    premium_usd = 0.0
    if matched.startswith("claude"):
        premium_usd = (
            premium_input_tokens * price.input_per_mtok * (_LONG_CONTEXT_INPUT_MULT - 1)
            + premium_output_tokens * price.output_per_mtok * (_LONG_CONTEXT_OUTPUT_MULT - 1)
        ) / 1_000_000
    tools_usd = web_search_calls * WEB_SEARCH_USD_PER_CALL + web_fetch_calls * WEB_FETCH_USD_PER_CALL
    return CostEstimate(
        usd=base_usd + cache_usd + premium_usd + tools_usd,
        known_model=bool(matched),
        matched_prefix=matched,
        cache_usd=cache_usd,
        tools_usd=tools_usd,
    )
