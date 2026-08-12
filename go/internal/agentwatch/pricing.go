package agentwatch

// Per-model LLM pricing — a port of src/yeaboi/pricing.py. Every model id and
// rate must match the Python table; the parity suite prices the same sessions
// through both implementations.

import (
	"sort"
	"strings"
)

// PricingAsOf is the date the rate table was last transcribed from provider
// pricing pages (pricing.PRICING_AS_OF).
const PricingAsOf = "2026-06-24"

// Anthropic cache economics: 5-minute-TTL cache writes bill at 1.25x the
// input rate, 1-hour writes at 2x, cache reads at 0.1x.
const (
	cacheReadMult    = 0.10
	cacheWrite5mMult = 1.25
	cacheWrite1hMult = 2.0
)

// ModelPrice is USD per million tokens for one model family.
type ModelPrice struct {
	InputPerMtok  float64
	OutputPerMtok float64
}

// CostEstimate is a priced token bundle plus honesty metadata.
type CostEstimate struct {
	USD           float64
	KnownModel    bool
	MatchedPrefix string
	PricingAsOf   string
}

// priceTable mirrors pricing._PRICES in source order (order only matters for
// stable tie-breaks when sorting by prefix length).
var priceTable = []struct {
	prefix string
	price  ModelPrice
}{
	// Anthropic — current
	{"claude-fable-5", ModelPrice{10.0, 50.0}},
	{"claude-mythos", ModelPrice{10.0, 50.0}},
	{"claude-opus-5", ModelPrice{5.0, 25.0}},
	{"claude-opus-4", ModelPrice{5.0, 25.0}}, // 4.5/4.6/4.7/4.8
	{"claude-sonnet-5", ModelPrice{3.0, 15.0}},
	{"claude-sonnet-4", ModelPrice{3.0, 15.0}},
	{"claude-haiku-4-5", ModelPrice{1.0, 5.0}},
	// Anthropic — legacy ids still present in old ledgers/transcripts.
	// claude-opus-4-1 / claude-opus-4-20250514 predate the Opus price drop.
	{"claude-opus-4-1", ModelPrice{15.0, 75.0}},
	{"claude-opus-4-2025", ModelPrice{15.0, 75.0}},
	{"claude-3-opus", ModelPrice{15.0, 75.0}},
	{"claude-3-7-sonnet", ModelPrice{3.0, 15.0}},
	{"claude-3-5-sonnet", ModelPrice{3.0, 15.0}},
	{"claude-3-5-haiku", ModelPrice{0.8, 4.0}},
	{"claude-3-haiku", ModelPrice{0.25, 1.25}},
	// OpenAI
	{"gpt-5-nano", ModelPrice{0.05, 0.4}},
	{"gpt-5-mini", ModelPrice{0.25, 2.0}},
	{"gpt-5", ModelPrice{1.25, 10.0}},
	{"gpt-4o-mini", ModelPrice{0.15, 0.6}},
	{"gpt-4o", ModelPrice{2.5, 10.0}},
	{"gpt-4.1-nano", ModelPrice{0.1, 0.4}},
	{"gpt-4.1-mini", ModelPrice{0.4, 1.6}},
	{"gpt-4.1", ModelPrice{2.0, 8.0}},
	{"o3", ModelPrice{2.0, 8.0}},
	// Google
	{"gemini-2.5-pro", ModelPrice{1.25, 10.0}},
	{"gemini-2.5-flash", ModelPrice{0.3, 2.5}},
	{"gemini-2.0-flash", ModelPrice{0.1, 0.4}},
}

// freeProviders are providers whose inference runs on the user's own hardware.
var freeProviders = map[string]bool{"ollama": true, "local": true}

// providerIDPrefixes are prefixes partner platforms prepend to Anthropic ids.
var providerIDPrefixes = []string{"us.anthropic.", "eu.anthropic.", "apac.anthropic.", "anthropic."}

// fallbackPrice is the mid (Sonnet) tier every non-free unknown model gets,
// flagged via KnownModel=false.
var fallbackPrice = ModelPrice{3.0, 15.0}

// lookupOrder holds priceTable indices sorted by prefix length, longest
// first, stable over table order — mirroring sorted(_PRICES, key=len,
// reverse=True) over an insertion-ordered dict.
var lookupOrder = func() []int {
	idx := make([]int, len(priceTable))
	for i := range idx {
		idx[i] = i
	}
	sort.SliceStable(idx, func(a, b int) bool {
		return len(priceTable[idx[a]].prefix) > len(priceTable[idx[b]].prefix)
	})
	return idx
}()

// NormaliseModelID lowercases and strips partner-platform prefixes.
func NormaliseModelID(model string) string {
	cleaned := strings.ToLower(strings.TrimSpace(model))
	for _, prefix := range providerIDPrefixes {
		if strings.HasPrefix(cleaned, prefix) {
			cleaned = cleaned[len(prefix):]
			break
		}
	}
	return cleaned
}

// LookupPrice longest-prefix matches a model id against the table.
// matchedPrefix is "" on a miss.
func LookupPrice(model string) (ModelPrice, string) {
	cleaned := NormaliseModelID(model)
	if cleaned == "" {
		return fallbackPrice, ""
	}
	for _, i := range lookupOrder {
		if strings.HasPrefix(cleaned, priceTable[i].prefix) {
			return priceTable[i].price, priceTable[i].prefix
		}
	}
	return fallbackPrice, ""
}

// EstimateCost prices a token bundle for one model. cacheWriteTokens are
// 5-minute-TTL writes; 1-hour writes ride separately. A free provider prices
// to zero and counts as known regardless of model.
//
// The arithmetic mirrors pricing.estimate_cost term for term — including the
// grouping of each cache rate as (input rate × multiplier) before the token
// multiplication — so float results are bit-identical to Python's.
func EstimateCost(
	model string,
	inputTokens, outputTokens int64,
	cacheWriteTokens, cacheWrite1hTokens, cacheReadTokens int64,
	provider string,
) CostEstimate {
	if freeProviders[strings.ToLower(strings.TrimSpace(provider))] {
		return CostEstimate{USD: 0.0, KnownModel: true, MatchedPrefix: "", PricingAsOf: PricingAsOf}
	}
	price, matched := LookupPrice(model)
	usd := (float64(inputTokens)*price.InputPerMtok +
		float64(outputTokens)*price.OutputPerMtok +
		float64(cacheWriteTokens)*(price.InputPerMtok*cacheWrite5mMult) +
		float64(cacheWrite1hTokens)*(price.InputPerMtok*cacheWrite1hMult) +
		float64(cacheReadTokens)*(price.InputPerMtok*cacheReadMult)) / 1_000_000
	return CostEstimate{USD: usd, KnownModel: matched != "", MatchedPrefix: matched, PricingAsOf: PricingAsOf}
}
