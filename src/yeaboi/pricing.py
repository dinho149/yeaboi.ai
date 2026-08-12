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
snapshots (``claude-sonnet-4-5-20250929``) and regional Bedrock ids without a
row per variant.
"""

from __future__ import annotations

from dataclasses import dataclass

# Date the rate table below was last transcribed from provider pricing pages.
# Surfaced on artifacts so stale numbers are visible rather than silent.
PRICING_AS_OF = "2026-06-24"

# Anthropic cache economics (per the API docs): a 5-minute-TTL cache write
# bills at 1.25x the input rate, a 1-hour write at 2x, and a cache read at
# 0.1x. Claude Code uses both TTLs, so the two write kinds are priced apart.
_CACHE_READ_MULT = 0.10
_CACHE_WRITE_5M_MULT = 1.25
_CACHE_WRITE_1H_MULT = 2.0


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
) -> CostEstimate:
    """Price a token bundle for one model.

    ``cache_write_tokens`` are 5-minute-TTL writes; pass 1-hour writes
    separately (Claude Code session logs report the split). A free provider
    (ollama/local) prices to zero and counts as known regardless of model.
    """
    if (provider or "").strip().lower() in _FREE_PROVIDERS:
        return CostEstimate(usd=0.0, known_model=True, matched_prefix="")
    price, matched = lookup_price(model)
    usd = (
        input_tokens * price.input_per_mtok
        + output_tokens * price.output_per_mtok
        + cache_write_tokens * price.cache_write_5m_per_mtok
        + cache_write_1h_tokens * price.cache_write_1h_per_mtok
        + cache_read_tokens * price.cache_read_per_mtok
    ) / 1_000_000
    return CostEstimate(usd=usd, known_model=bool(matched), matched_prefix=matched)
