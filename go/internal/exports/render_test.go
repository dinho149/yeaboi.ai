package exports

import (
	"strings"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

func TestEscapeValue(t *testing.T) {
	cases := []struct{ in, want string }{
		{"Release 1.0", "Release%201%2E0"},         // the dot is grammar — always escaped
		{"a[b]=c%", "a%5Bb%5D%3Dc%25"},             // selector grammar and the escape char itself
		{"José", "Jos%C3%A9"},                      // UTF-8 bytes, uppercase hex
		{"plain-safe_~chars", "plain-safe_~chars"}, // unreserved set passes through
		{"", ""},
	}
	for _, c := range cases {
		if got := escapeValue(c.in); got != c.want {
			t.Errorf("escapeValue(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestRowAnchor(t *testing.T) {
	if got, want := rowAnchor("cards", "id", "a.b c"), "cards[id=a%2Eb%20c]"; got != want {
		t.Errorf("rowAnchor = %q, want %q", got, want)
	}
}

func TestEditMapSkipsNonStrings(t *testing.T) {
	if got := pysem.JSONDumps(editMap("cards[id=x]", int64(5))); got != "{}" {
		t.Errorf("a non-string value is not editable anywhere in the registry: %s", got)
	}
	got := pysem.JSONDumps(editMap("cards[id=x]", "hello"))
	want := `{"text": {"path": "cards[id=x].text", "value": "hello"}}`
	if got != want {
		t.Errorf("editMap = %s, want %s", got, want)
	}
}

func TestAnnotationsFromIsTolerant(t *testing.T) {
	if rows := annotationsFrom("not a list"); rows != nil {
		t.Errorf("non-list deserializes to nothing, got %v", rows)
	}
	if rows := annotationsFrom(nil); rows != nil {
		t.Errorf("missing key deserializes to nothing, got %v", rows)
	}
	decoded, err := pysem.DecodeOrdered([]byte(`[{"text": "keep"}, "not a dict", {"text": 3}, {"text": null}]`))
	if err != nil {
		t.Fatal(err)
	}
	rows := annotationsFrom(decoded)
	if len(rows) != 3 {
		t.Fatalf("non-dict rows are skipped, the rest kept: %d", len(rows))
	}
	// Every field is str()-ed — a numeric text becomes "3", an explicit null
	// becomes "None" (truthy!), exactly like the reference's str(a.get(...)).
	if rows[1].text != "3" || rows[2].text != "None" {
		t.Errorf("str() coercions drifted: %q, %q", rows[1].text, rows[2].text)
	}
	if rows[0].kind != "note" {
		t.Errorf("kind defaults to note, got %q", rows[0].kind)
	}
}

func TestAnnotationsMarkdownShapes(t *testing.T) {
	rows := []annotation{
		{kind: "note", anchor: "cards[id=a]", text: "Verified", author: "Ada"},
		{kind: "field", label: "Risk owner", text: "Ada"},
		{kind: "field", text: "no label falls back to the note shape"},
		{kind: "note", text: ""},
	}
	lines := annotationsMarkdown(rows)
	want := []string{
		"## " + notesHeading,
		"",
		"- Verified (on `cards[id=a]`) — _Ada_",
		"- **Risk owner:** Ada",
		"- no label falls back to the note shape",
		"",
	}
	if strings.Join(lines, "\n") != strings.Join(want, "\n") {
		t.Errorf("annotationsMarkdown = %q, want %q", lines, want)
	}
	if got := annotationsMarkdown([]annotation{{text: ""}}); got != nil {
		t.Errorf("all-empty annotations render nothing, got %v", got)
	}
}

func TestWithAnnotationsOmittedWhenEmpty(t *testing.T) {
	args := pysem.EmptyObj()
	report := pysem.EmptyObj()
	args.Set("report", report)
	withAnnotations(args, []annotation{{text: ""}})
	if report.Has("annotations") {
		t.Error("empty annotations must be omitted, not emitted as [] — committed wire fixtures must not move")
	}
	withAnnotations(args, []annotation{{kind: "note", text: "x"}})
	if !report.Has("annotations") {
		t.Error("non-empty annotations must attach to the report")
	}
}
