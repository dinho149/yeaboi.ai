package exports

import (
	"fmt"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

func TestSafeURL(t *testing.T) {
	cases := []struct {
		url  any
		want string
	}{
		{"https://jira.example.com/browse/K-1", "https://jira.example.com/browse/K-1"},
		{"mailto:ada@example.com", "mailto:ada@example.com"},
		{"MAILTO:ada@example.com", "MAILTO:ada@example.com"}, // scheme match is case-insensitive, value untouched
		{"javascript:alert(1)", ""},
		{"JAVA\tSCRIPT:alert(1)", ""}, // browsers strip interior tabs before parsing — so must the allowlist
		{"java\nscript:alert(1)", ""},
		{"//evil.example.com/x", ""},                         // protocol-relative resolves to a bogus origin under file://
		{"example.com/browse/K-1", "example.com/browse/K-1"}, // no scheme — relative, inert
		{"/browse/K-1", "/browse/K-1"},
		{"  https://x.example  ", "https://x.example"},
		{"\x00https://x.example\x7f", "https://x.example"},
		{"", ""},
		{nil, ""}, // guard first: str(None) would yield the literal "None"
		{"\t\n", ""},
		{"vbscript:msgbox", ""},
	}
	for _, c := range cases {
		if got := SafeURL(c.url); got != c.want {
			t.Errorf("SafeURL(%q) = %q, want %q", c.url, got, c.want)
		}
	}
}

func historyRows(t *testing.T, raw string) []any {
	t.Helper()
	decoded, err := pysem.DecodeOrdered([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	rows, ok := decoded.([]any)
	if !ok {
		t.Fatal("history fixture must be a list")
	}
	return rows
}

func TestTrendNilUnderTwoPoints(t *testing.T) {
	rows := historyRows(t, `[{"d": "2026-01-01", "v": 3}]`)
	trend, err := trendPayload(rows, "d", "v", "T", "L", "2026-01-01", "2026-01-01", 3)
	if err != nil {
		t.Fatal(err)
	}
	if trend != nil {
		t.Errorf("one run is not a trend — want nil, got %v", trend)
	}
}

func TestTrendNormalisation(t *testing.T) {
	// Newest-first rows: a future row past the cutoff, a same-date reversal
	// (newest wins), a null value, an empty date, then two good points.
	rows := historyRows(t, `[
		{"d": "2026-02-01", "v": 9},
		{"d": "2026-01-08", "v": 4},
		{"d": "2026-01-08", "v": 999},
		{"d": "2026-01-07", "v": null},
		{"d": "", "v": 2},
		{"d": "2026-01-01", "v": 1.5}
	]`)
	trend, err := trendPayload(rows, "d", "v", "T", "L", "2026-01-15", "2026-01-15", 7)
	if err != nil {
		t.Fatal(err)
	}
	obj := pysem.AsObj(trend)
	if obj == nil {
		t.Fatal("expected a trend object")
	}
	got := pysem.JSONDumps(obj.Get("points"))
	want := `[["2026-01-01", 1.5], ["2026-01-08", 4.0], ["2026-01-15", 7.0]]`
	if got != want {
		t.Errorf("points = %s, want %s", got, want)
	}
	if label := obj.Get("label"); label != "L — last 3 runs" {
		t.Errorf("label = %v", label)
	}
}

func TestTrendCurrentNotDuplicated(t *testing.T) {
	rows := historyRows(t, `[{"d": "2026-01-02", "v": 5}, {"d": "2026-01-01", "v": 3}]`)
	trend, err := trendPayload(rows, "d", "v", "T", "L", "2026-01-02", "2026-01-02", 99)
	if err != nil {
		t.Fatal(err)
	}
	got := pysem.JSONDumps(pysem.AsObj(trend).Get("points"))
	if want := `[["2026-01-01", 3.0], ["2026-01-02", 5.0]]`; got != want {
		t.Errorf("current point with an already-seen date must not append: %s", got)
	}
}

func TestTrendCapsAtFourteenPoints(t *testing.T) {
	raw := "["
	for i := 20; i >= 1; i-- {
		if i < 20 {
			raw += ", "
		}
		raw += fmt.Sprintf(`{"d": "2026-01-%02d", "v": %d}`, i, i)
	}
	raw += "]"
	trend, err := trendPayload(historyRows(t, raw), "d", "v", "T", "L", "2026-01-21", "2026-01-21", 21)
	if err != nil {
		t.Fatal(err)
	}
	points := pysem.AsObj(trend).Get("points").([]any)
	if len(points) != 14 {
		t.Fatalf("points capped at 14, got %d", len(points))
	}
	first := points[0].([]any)
	if first[0] != "2026-01-08" {
		t.Errorf("cap keeps the TRAILING window (current point included), first day = %v", first[0])
	}
}
