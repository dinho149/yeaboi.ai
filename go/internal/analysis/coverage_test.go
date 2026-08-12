package analysis

// coverage_test.go — port of tests/unit/test_coverage.py, plus exact-value
// pins generated from the Python reference implementation (the loose Python
// assertions are tightened to whole-string pins here so they double as
// parity fixtures).

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// dnsErr mirrors the _dns_error fixture: one mid-scan network loss, differing
// only by repo/sha.
func dnsErr(repo, sha string) string {
	return "Error occurred in request., ConnectionError: HTTPSConnectionPool(host='dev.azure.com', port=443): " +
		"Max retries exceeded with url: /org/Proj/_apis/git/repositories/" + repo + "/commits/" + sha +
		"/changes?top=2000&skip=0 (Caused by NameResolutionError(\"Failed to resolve 'dev.azure.com'\"))"
}

// dnsNormalized is the Python-reference _normalize_detail output for any
// dnsErr string: URL path collapsed to <api-path>, then truncated at 200
// code points with an ellipsis.
const dnsNormalized = "Error occurred in request., ConnectionError: HTTPSConnectionPool(host='dev.azure.com', " +
	"port=443): Max retries exceeded with url: <api-path> (Caused by NameResolutionError(\"Failed to resolve " +
	"'dev.azur\u2026"

func TestNormalizeDetailStripsURLsAPIPathsShasAndLongNumbers(t *testing.T) {
	got := normalizeDetail(dnsErr("RepoA", "493e8533e651c449c1c4a0ebf56407852f7b147f"))
	if got != dnsNormalized {
		t.Errorf("normalizeDetail = %q, want %q", got, dnsNormalized)
	}
}

func TestNormalizeDetailExactSubstitutions(t *testing.T) {
	// Reference-generated pins: <url>, <id> (7-40 hex), <n> (4+ digits, short
	// numbers kept), and unicode word boundaries blocking a substitution.
	cases := [][2]string{
		{"see https://x.test/a/b?q=1 done", "see <url> done"},
		{"id deadbeef0 and 12345 and 999 and caf\u00e9 1234", "id <id> and <n> and 999 and caf\u00e9 <n>"},
		{"  a\t\n b c  ", "a b c"},
		{"\u00e912345678\u00e9 and abcdef1234567 ok", "\u00e912345678\u00e9 and <id> ok"},
	}
	for _, c := range cases {
		if got := normalizeDetail(c[0]); got != c[1] {
			t.Errorf("normalizeDetail(%q) = %q, want %q", c[0], got, c[1])
		}
	}
}

func TestNormalizeDetailTruncatesAtCap(t *testing.T) {
	got := normalizeDetail(strings.Repeat("x", 500))
	if utf8.RuneCountInString(got) != 200 || !strings.HasSuffix(got, "\u2026") {
		t.Errorf("normalizeDetail cap: len %d, suffix ok %v", utf8.RuneCountInString(got), strings.HasSuffix(got, "\u2026"))
	}
}

func TestNormalizeDetailEmptyStaysEmpty(t *testing.T) {
	if got := normalizeDetail(""); got != "" {
		t.Errorf("normalizeDetail(\"\") = %q, want \"\"", got)
	}
}

func TestGroupedErrorsSameErrorShapeGroupsAcrossAssets(t *testing.T) {
	tracker := NewCoverageTracker("code", 120)
	tracker.Add("azdo", "Proj", "RepoA", "failed", dnsErr("RepoA", strings.Repeat("a", 40)), true)
	tracker.Add("azdo", "Proj", "RepoB", "failed", dnsErr("RepoB", strings.Repeat("b", 40)), true)
	tracker.Add("azdo", "Other", "RepoC", "failed", dnsErr("RepoC", strings.Repeat("c", 40)), true)
	grouped, _ := tracker.AsDict().Get("grouped_errors").([]any)
	if len(grouped) != 1 {
		t.Fatalf("len(grouped) = %d, want 1", len(grouped))
	}
	group := pysem.AsObj(grouped[0])
	if got := group.Get("count"); got != int64(3) {
		t.Errorf("count = %v, want 3", got)
	}
	assertDump(t, group.Get("containers"), `["Other", "Proj"]`)
	assertDump(t, group.Get("examples"), `["RepoA", "RepoB", "RepoC"]`)
}

func TestGroupedErrorsDifferentErrorShapesStaySeparate(t *testing.T) {
	tracker := NewCoverageTracker("code", 120)
	tracker.Add("azdo", "Proj", "RepoA", "failed", dnsErr("RepoA", strings.Repeat("a", 40)), true)
	tracker.Add("azdo", "Proj", "RepoB", "failed", "TF401019: The Git repository does not exist", true)
	grouped, _ := tracker.AsDict().Get("grouped_errors").([]any)
	if len(grouped) != 2 {
		t.Errorf("len(grouped) = %d, want 2", len(grouped))
	}
}

func TestGroupedErrorsEmptyDetailFallsBackToStatus(t *testing.T) {
	tracker := NewCoverageTracker("code", 120)
	tracker.Add("azdo", "Proj", "RepoA", "inaccessible", "", true)
	assertDump(t, tracker.AsDict().Get("grouped_errors"), `[{"provider": "azdo", "status": "inaccessible", "detail": "inaccessible", "count": 1, "containers": ["Proj"], "examples": ["RepoA"]}]`)
}

func TestCoverageNotesRenderOneLinePerGroupWithCounts(t *testing.T) {
	tracker := NewCoverageTracker("code", 120)
	for index := 0; index < 24; index++ {
		tracker.Add("azdo", fmt.Sprintf("Proj%d", index%3), fmt.Sprintf("Repo%d", index),
			"failed", dnsErr(fmt.Sprintf("Repo%d", index), strings.Repeat("d", 40)), true)
	}
	notes := CoverageNotes(tracker.AsDict())
	want := "azdo: error (24 item(s) across 3 container(s): " + dnsNormalized + ")"
	if len(notes) != 1 || notes[0] != want {
		t.Errorf("notes = %q, want [%q]", notes, want)
	}
}

func TestAsDictFullShapeIsExact(t *testing.T) {
	tracker := NewCoverageTracker("docs", 30)
	tracker.Add("azdo", "Proj", "RepoA", "failed", dnsErr("RepoA", strings.Repeat("a", 40)), true)
	tracker.Add("azdo", "Proj", "RepoB", "failed", dnsErr("RepoB", strings.Repeat("b", 40)), true)
	tracker.Add("azdo", "Other", "RepoC", "failed", dnsErr("RepoC", strings.Repeat("c", 40)), true)
	tracker.Add("azdo", "Proj", "RepoD", "succeeded", "", true)
	tracker.Add("azdo", "Proj", "RepoE", "cached", "", true)
	coverage := tracker.AsDict()
	assets, _ := coverage.Get("assets").([]any)
	if len(assets) != 5 {
		t.Fatalf("len(assets) = %d, want 5", len(assets))
	}
	// Asset entries carry the RAW detail (normalization is grouping-only).
	if got := pysem.AsObj(assets[0]).Get("detail"); got != dnsErr("RepoA", strings.Repeat("a", 40)) {
		t.Errorf("assets[0] detail = %v", got)
	}
	coverage.Delete("assets")
	// Reference-generated pin (Python json.dumps of as_dict() minus assets).
	assertDump(t, coverage, `{"component": "docs", "status": "partial", "has_data": true, "completion_pct": 40.0, "window_days": 30, "discovered": 5, "eligible": 5, "attempted": 4, "succeeded": 1, "cached": 1, "failed": 3, "unchanged": 0, "inaccessible": 0, "truncated": 0, "completed": 2, "per_container": {"azdo:Proj": {"discovered": 4, "succeeded": 1, "cached": 1, "failed": 2, "unchanged": 0}, "azdo:Other": {"discovered": 1, "succeeded": 0, "cached": 0, "failed": 1, "unchanged": 0}}, "grouped_errors": [{"provider": "azdo", "status": "failed", "detail": "`+dnsNormalizedJSON+`", "count": 3, "containers": ["Other", "Proj"], "examples": ["RepoA", "RepoB", "RepoC"]}]}`)
}

// dnsNormalizedJSON is dnsNormalized as json.dumps renders it inside a
// document (ensure_ascii: the quote escapes stay, the ellipsis becomes
// \u2026 as its six-character escape).
const dnsNormalizedJSON = `Error occurred in request., ConnectionError: HTTPSConnectionPool(host='dev.azure.com', port=443): Max retries exceeded with url: <api-path> (Caused by NameResolutionError(\"Failed to resolve 'dev.azur\u2026`

func TestCoverageNotesRebuildsGroupsFromAssetsWhenMissing(t *testing.T) {
	// A coverage dict with no grouped_errors list is regrouped from its raw
	// assets (legacy renderer path). Reference pin.
	coverage := obj(
		"component", "code",
		"window_days", json.Number("0"),
		"assets", []any{obj(
			"provider", "gh",
			"container", "c1",
			"asset", "a1",
			"status", "truncated",
			"detail", "partial 12345 diff",
			"eligible", true,
		)},
	)
	notes := CoverageNotes(coverage)
	want := "gh: truncated (1 item(s) across 1 container(s): partial <n> diff)"
	if len(notes) != 1 || notes[0] != want {
		t.Errorf("notes = %q, want [%q]", notes, want)
	}
}

func TestCoverageNotesFalsyCountReadsAsOneAndEmptyContainersDropScope(t *testing.T) {
	coverage := obj("grouped_errors", []any{obj(
		"provider", "gh",
		"status", "inaccessible",
		"detail", "",
		"count", json.Number("0"),
		"containers", []any{},
	)})
	notes := CoverageNotes(coverage)
	want := "gh: error (1 item(s): inaccessible)"
	if len(notes) != 1 || notes[0] != want {
		t.Errorf("notes = %q, want [%q]", notes, want)
	}
}

func TestCoverageNotesCountUsesThousandsSeparators(t *testing.T) {
	coverage := obj("grouped_errors", []any{obj(
		"provider", "gh",
		"status", "failed",
		"detail", "boom",
		"count", json.Number("1234567"),
		"containers", []any{"a", "b"},
	)})
	notes := CoverageNotes(coverage)
	want := "gh: error (1,234,567 item(s) across 2 container(s): boom)"
	if len(notes) != 1 || notes[0] != want {
		t.Errorf("notes = %q, want [%q]", notes, want)
	}
}

func TestCommaInt(t *testing.T) {
	cases := map[int64]string{
		0: "0", 5: "5", 999: "999", 1000: "1,000", 24: "24",
		1234567: "1,234,567", -1234: "-1,234", 100000: "100,000",
	}
	for n, want := range cases {
		if got := commaInt(n); got != want {
			t.Errorf("commaInt(%d) = %q, want %q", n, got, want)
		}
	}
}
