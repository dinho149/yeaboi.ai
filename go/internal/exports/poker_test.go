package exports

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

func TestPokerBuildExportGolden(t *testing.T) {
	params := decodeInputs(t, "poker_inputs.json")
	result, err := RunPokerBuildExport(params)
	if err != nil {
		t.Fatalf("RunPokerBuildExport: %v", err)
	}
	if got, ok := result.Get("contract_version").(int64); !ok || got != 1 {
		t.Fatalf("contract_version = %v", result.Get("contract_version"))
	}
	if got, want := pysem.JSONDumps(result.Get("markdown")), loadGolden(t, "poker_markdown.json"); got != want {
		t.Errorf("markdown drifted from the reference:\n got %s\nwant %s", got, want)
	}
	if got, want := pysem.JSONDumps(result.Get("args")), loadGolden(t, "poker_args.json"); got != want {
		t.Errorf("args drifted from the reference:\n got %s\nwant %s", got, want)
	}
}

func TestPtsStr(t *testing.T) {
	cases := []struct {
		value any
		want  string
	}{
		{nil, "—"},
		{3.0, "3"}, // ints render without the trailing .0
		{13.0, "13"},
		{0.5, "0.5"},
		{2.5, "2.5"},
		{"x", "—"}, // non-float means _float_or_none already returned None
	}
	for _, c := range cases {
		if got := ptsStr(c.value); got != c.want {
			t.Errorf("ptsStr(%v) = %q, want %q", c.value, got, c.want)
		}
	}
}

func TestFloatWideningOnTheWire(t *testing.T) {
	// A wire integer 3 becomes Python float 3.0 through _float_or_none, and
	// json.dumps renders the widened float — never an echo of the literal.
	if got := numOrNil(pyFloatOrNil(json.Number("3"))); got != json.Number("3.0") {
		t.Errorf("widened 3 must render as 3.0, got %v", got)
	}
	if got := pyFloatOrNil(json.Number("bogus")); got != nil {
		t.Errorf("unparseable number must fold to nil, got %v", got)
	}
	if got := pyFloatOrNil([]any{}); got != nil {
		t.Errorf("_float_or_none swallows TypeError — lists fold to nil, got %v", got)
	}
	if got := pyFloatOrNil("  5 "); got != 5.0 {
		t.Errorf("float() tolerates surrounding whitespace, got %v", got)
	}
}

func TestStaleFinalForcedNullWhenSkipped(t *testing.T) {
	raw := `{"tickets": [{"key": "K-1", "summary": "s", "final_points": 8.0, "estimated": false}]}`
	decoded, _ := pysem.DecodeOrdered([]byte(raw))
	report, err := pokerReportFrom(pysem.AsObj(decoded))
	if err != nil {
		t.Fatal(err)
	}
	dump := pysem.JSONDumps(ticketPayload(report.tickets[0]))
	if !strings.Contains(dump, `"final": null`) {
		t.Errorf("a skipped ticket's stale final_points must render null: %s", dump)
	}
	if !strings.Contains(dump, `"estimated": false`) {
		t.Errorf("estimated must render as a bool: %s", dump)
	}
}

func TestVotesStrFiltersEmptyValues(t *testing.T) {
	tk := &pokerTicket{votes: []pokerVote{
		{voter: "Alex", value: "5"},
		{voter: "Sam", value: ""},
		{voter: "Kim", value: "8"},
	}}
	if got, want := votesStr(tk), "Alex 5 · Kim 8"; got != want {
		t.Errorf("votesStr = %q, want %q", got, want)
	}
}

func TestNavGainsSectionsOnlyWhenPresent(t *testing.T) {
	raw := `{"tickets": [{"key": "K-1", "summary": "s"}]}`
	decoded, _ := pysem.DecodeOrdered([]byte(raw))
	report, err := pokerReportFrom(pysem.AsObj(decoded))
	if err != nil {
		t.Fatal(err)
	}
	args, err := pokerExportArgs(report, []any{}, "2026-01-02")
	if err != nil {
		t.Fatal(err)
	}
	dump := pysem.JSONDumps(args.Get("nav"))
	if want := `[["overview", "Overview"], ["tickets", "Tickets"]]`; dump != want {
		t.Errorf("nav without ai/duel sections = %s, want %s", dump, want)
	}
}
