package agentwatch

import "testing"

// TestRound4MatchesPython pins round4 to CPython round(x, 4) outputs
// (expected values captured from a real CPython 3.11 run) — banker's rounding
// over the float's exact decimal expansion, per contract rule 6.
func TestRound4MatchesPython(t *testing.T) {
	cases := []struct {
		in   float64
		want float64
	}{
		{5e-05, 0.0001},
		{0.00015, 0.0001}, // stored value is just below the tie — rounds down
		{0.00025, 0.0003}, // stored value is just above the tie — rounds up
		{0.123450000001, 0.1235},
		{2.5e-05, 0.0},
		{7.4999999999e-05, 0.0001},
		{0.12345, 0.1235},
		{0.123455, 0.1235},
		{1234.56785, 1234.5678},
		{0.1, 0.1},
		{1e-09, 0.0},
		{123.456789, 123.4568},
		{0.66665, 0.6666},
		{0.999949999, 0.9999},
		{0.9999500001, 1.0},
		{3.00015, 3.0002},
		{0.09375, 0.0938},   // exact dyadic tie — half-even goes to the even digit
		{-0.09375, -0.0938}, // and symmetrically for negatives
		{2.00005, 2.0},
		{0.0042725, 0.0043},
		{0.0, 0.0},
	}
	for _, c := range cases {
		if got := round4(c.in); got != c.want {
			t.Errorf("round4(%v) = %v, want %v", c.in, got, c.want)
		}
	}
}

func TestEstimateCostKnownExample(t *testing.T) {
	// The collector fixture's session totals on claude-opus-5:
	// (12*5 + 150*25 + 10*(5*1.25) + 30*(5*2) + 200*(5*0.1)) / 1e6
	est := EstimateCost("claude-opus-5", 12, 150, 10, 30, 200, "")
	if est.USD != 0.0042725 {
		t.Errorf("USD = %v, want 0.0042725", est.USD)
	}
	if !est.KnownModel || est.MatchedPrefix != "claude-opus-5" {
		t.Errorf("expected known claude-opus-5 match, got %+v", est)
	}
	if est.PricingAsOf != "2026-06-24" {
		t.Errorf("PricingAsOf = %q", est.PricingAsOf)
	}
}

func TestLookupPriceLongestPrefixAndNormalisation(t *testing.T) {
	// Bedrock-style prefix strips, dated snapshot matches by prefix.
	if _, matched := LookupPrice("us.anthropic.claude-opus-5-20260101-v1:0"); matched != "claude-opus-5" {
		t.Errorf("matched %q, want claude-opus-5", matched)
	}
	// The legacy opus-4-1 id must win over the shorter claude-opus-4 prefix.
	price, matched := LookupPrice("claude-opus-4-1-20250805")
	if matched != "claude-opus-4-1" || price.InputPerMtok != 15.0 || price.OutputPerMtok != 75.0 {
		t.Errorf("got %+v via %q, want legacy pricing via claude-opus-4-1", price, matched)
	}
	// claude-opus-4-2025* (dated snapshot) is legacy; claude-opus-4-5 is not.
	if _, matched := LookupPrice("claude-opus-4-20250514"); matched != "claude-opus-4-2025" {
		t.Errorf("matched %q, want claude-opus-4-2025", matched)
	}
	price, matched = LookupPrice("claude-opus-4-5")
	if matched != "claude-opus-4" || price.InputPerMtok != 5.0 {
		t.Errorf("got %+v via %q, want current opus via claude-opus-4", price, matched)
	}
}

func TestEstimateCostUnknownAndFree(t *testing.T) {
	est := EstimateCost("acme-llm-9000", 1_000_000, 1_000_000, 0, 0, 0, "")
	if est.KnownModel || est.MatchedPrefix != "" {
		t.Errorf("expected unknown model, got %+v", est)
	}
	if est.USD != 18.0 { // Sonnet-tier fallback: 3 + 15
		t.Errorf("fallback USD = %v, want 18.0", est.USD)
	}
	if est := EstimateCost("", 1000, 1000, 0, 0, 0, ""); est.KnownModel {
		t.Errorf("empty model must be unknown, got %+v", est)
	}
	if est := EstimateCost("llama3", 1_000_000, 0, 0, 0, 0, "ollama"); est.USD != 0.0 || !est.KnownModel {
		t.Errorf("free provider must price to zero and count as known, got %+v", est)
	}
}
