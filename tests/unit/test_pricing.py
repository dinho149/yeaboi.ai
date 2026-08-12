"""Tests for src/yeaboi/pricing.py — the shared per-model cost table."""

from yeaboi.pricing import PRICING_AS_OF, CostEstimate, estimate_cost, lookup_price, normalise_model_id


class TestNormalise:
    def test_strips_partner_prefixes(self):
        assert normalise_model_id("anthropic.claude-opus-5") == "claude-opus-5"
        assert normalise_model_id("us.anthropic.claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_lowercases_and_trims(self):
        assert normalise_model_id("  Claude-Opus-5 ") == "claude-opus-5"

    def test_empty(self):
        assert normalise_model_id("") == ""


class TestLookup:
    def test_exact_current_models(self):
        price, matched = lookup_price("claude-opus-5")
        assert (price.input_per_mtok, price.output_per_mtok) == (5.0, 25.0)
        assert matched == "claude-opus-5"

    def test_dated_snapshot_matches_family(self):
        price, matched = lookup_price("claude-sonnet-4-5-20250929")
        assert (price.input_per_mtok, price.output_per_mtok) == (3.0, 15.0)
        assert matched == "claude-sonnet-4"

    def test_longest_prefix_wins_for_legacy_opus(self):
        # claude-opus-4-1 predates the Opus price drop; claude-opus-4-8 does not.
        old, _ = lookup_price("claude-opus-4-1")
        assert (old.input_per_mtok, old.output_per_mtok) == (15.0, 75.0)
        dated, _ = lookup_price("claude-opus-4-20250514")
        assert (dated.input_per_mtok, dated.output_per_mtok) == (15.0, 75.0)
        new, _ = lookup_price("claude-opus-4-8")
        assert (new.input_per_mtok, new.output_per_mtok) == (5.0, 25.0)

    def test_bedrock_prefixed_id(self):
        price, matched = lookup_price("anthropic.claude-opus-5")
        assert matched == "claude-opus-5"
        assert price.input_per_mtok == 5.0

    def test_unknown_model_falls_back(self):
        price, matched = lookup_price("totally-new-model-9000")
        assert matched == ""
        assert (price.input_per_mtok, price.output_per_mtok) == (3.0, 15.0)


class TestEstimateCost:
    def test_known_model_exact_math(self):
        est = estimate_cost("claude-opus-5", 1_000_000, 1_000_000)
        assert est.usd == 30.0
        assert est.known_model is True
        assert est.pricing_as_of == PRICING_AS_OF

    def test_cache_columns(self):
        # Sonnet 5: input $3 → read 0.3, 5m write 3.75, 1h write 6.0 per MTok.
        est = estimate_cost(
            "claude-sonnet-5",
            0,
            0,
            cache_write_tokens=1_000_000,
            cache_write_1h_tokens=1_000_000,
            cache_read_tokens=1_000_000,
        )
        assert round(est.usd, 4) == round(3.75 + 6.0 + 0.3, 4)

    def test_unknown_model_flagged(self):
        est = estimate_cost("mystery-model", 1_000_000, 0)
        assert est.known_model is False
        assert est.usd == 3.0

    def test_free_provider_prices_to_zero(self):
        est = estimate_cost("llama3.3:70b", 5_000_000, 5_000_000, provider="ollama")
        assert est == CostEstimate(usd=0.0, known_model=True, matched_prefix="")

    def test_legacy_cloud_cost_regression(self):
        # The Usage page's old hardcoded formula was (in*3 + out*15)/1e6; an
        # unknown/blank model must keep pricing at exactly that estimate.
        est = estimate_cost("", 123_456, 78_900)
        assert est.usd == (123_456 * 3.0 + 78_900 * 15.0) / 1_000_000

    def test_haiku_and_fable(self):
        assert estimate_cost("claude-haiku-4-5", 1_000_000, 0).usd == 1.0
        assert estimate_cost("claude-fable-5", 1_000_000, 1_000_000).usd == 60.0
