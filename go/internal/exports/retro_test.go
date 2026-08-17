package exports

import (
	"os"
	"strings"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// The testdata goldens are produced by the Python reference implementation
// (retro/export.py build_retro_export, poker/export.py build_poker_export)
// over a fixture report with pinned timestamps, json.dumps-ed with
// ensure_ascii — so a golden compare here is a byte compare against the
// reference's own wire output. The live cross-check over a nastier corpus is
// tests/parity/test_exports_parity.py.

func loadGolden(t *testing.T, name string) string {
	t.Helper()
	raw, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatalf("golden %s: %v", name, err)
	}
	return strings.TrimSuffix(string(raw), "\n")
}

func decodeInputs(t *testing.T, name string) *pysem.Obj {
	t.Helper()
	decoded, err := pysem.DecodeOrdered([]byte(loadGolden(t, name)))
	if err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	obj := pysem.AsObj(decoded)
	if obj == nil {
		t.Fatalf("decode %s: not an object", name)
	}
	return obj
}

func TestRetroBuildExportGolden(t *testing.T) {
	params := decodeInputs(t, "retro_inputs.json")
	result, err := RunRetroBuildExport(params)
	if err != nil {
		t.Fatalf("RunRetroBuildExport: %v", err)
	}
	if got, ok := result.Get("contract_version").(int64); !ok || got != 1 {
		t.Fatalf("contract_version = %v", result.Get("contract_version"))
	}
	if got, want := pysem.JSONDumps(result.Get("markdown")), loadGolden(t, "retro_markdown.json"); got != want {
		t.Errorf("markdown drifted from the reference:\n got %s\nwant %s", got, want)
	}
	if got, want := pysem.JSONDumps(result.Get("args")), loadGolden(t, "retro_args.json"); got != want {
		t.Errorf("args drifted from the reference:\n got %s\nwant %s", got, want)
	}
}

func TestRetroNonEditableOmitsAnchors(t *testing.T) {
	params := decodeInputs(t, "retro_inputs.json")
	params.Set("editable", false)
	result, err := RunRetroBuildExport(params)
	if err != nil {
		t.Fatalf("RunRetroBuildExport: %v", err)
	}
	dump := pysem.JSONDumps(result.Get("args"))
	// No card may carry an edit map (annotations legitimately keep their own
	// "anchor" field, so the probe is the edit key alone).
	if strings.Contains(dump, `"edit"`) {
		t.Error(`non-editable args must not carry "edit" — a downloaded report stays byte-identical`)
	}
	// The markdown twin never carries edit machinery either way.
	if got, want := pysem.JSONDumps(result.Get("markdown")), loadGolden(t, "retro_markdown.json"); got != want {
		t.Errorf("markdown must not depend on editable:\n got %s\nwant %s", got, want)
	}
}

func TestCarriedStatusLabel(t *testing.T) {
	cases := []struct {
		status any
		want   string
	}{
		{"pending", "Pending"},
		{"done", "Done"},
		{"in_progress", "In Progress"},
		{"carried_over", "Carried Over"},
		{"not_relevant", "Not Relevant"},
		{"", "Pending"},    // falsy → "pending" → its label
		{nil, "Pending"},   // same
		{"weird", "weird"}, // unknown status echoes itself
	}
	for _, c := range cases {
		if got := carriedStatusLabel(c.status); got != c.want {
			t.Errorf("carriedStatusLabel(%v) = %q, want %q", c.status, got, c.want)
		}
	}
}

func TestReactionCountCoercions(t *testing.T) {
	// report_from_dict applies int() per count and str() per emoji; rows that
	// are not two elements long are dropped.
	raw := `{"cards": [{"id": "x", "grid": "went_well", "text": "t",` +
		` "reactions": [["a", "3"], ["b", 2.9], ["c", true], ["d", 1, 9], ["e"]]}]}`
	decoded, err := pysem.DecodeOrdered([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	report, err := retroReportFrom(pysem.AsObj(decoded))
	if err != nil {
		t.Fatalf("retroReportFrom: %v", err)
	}
	got := reactionsStr(report.cards[0])
	if want := "a 3  b 2  c 1"; got != want {
		t.Errorf("reactionsStr = %q, want %q", got, want)
	}
}

func TestUnknownGridCountsButDoesNotRender(t *testing.T) {
	raw := `{"date": "2026-01-02", "cards": [{"id": "x", "grid": "nope", "text": "lost"}]}`
	decoded, _ := pysem.DecodeOrdered([]byte(raw))
	report, err := retroReportFrom(pysem.AsObj(decoded))
	if err != nil {
		t.Fatal(err)
	}
	args, err := retroExportArgs(report, []any{}, false, "2026-01-02")
	if err != nil {
		t.Fatal(err)
	}
	dump := pysem.JSONDumps(args)
	if strings.Contains(dump, "lost") {
		t.Errorf("unknown-grid card leaked into columns: %s", dump)
	}
	if !strings.Contains(dump, `["CARDS", "1"]`) {
		t.Errorf("unknown-grid card must still count in the CARDS fact: %s", dump)
	}
}
