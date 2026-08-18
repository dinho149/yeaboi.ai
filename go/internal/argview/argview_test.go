// The help goldens in go/cmd/yeaboi/help_golden_test.go are this package's
// real gate; what lives here are the textwrap edge rules a screen might not
// happen to exercise after a cli.py edit — each vector's expectation is a
// captured CPython 3.11 textwrap.wrap() result.
package argview

import (
	"reflect"
	"testing"
)

func TestWrapCollapsedMatchesCPython(t *testing.T) {
	cases := []struct {
		text  string
		width int
		want  []string
	}{
		// hyphenated-word breaks, the em-dash run, and the short co-op /
		// e-mail-address lookaround shapes, straight from the oracle:
		// textwrap.wrap('a very-long--word chain co-op e-mail-address', 10)
		{"a very-long--word chain co-op e-mail-address", 10,
			[]string{"a very-", "long--word", "chain co-", "op e-mail-", "address"}},
		// break_long_words at the window edge.
		{"supercalifragilistic", 8, []string{"supercal", "ifragili", "stic"}},
		// leading "--" flags break after an interior hyphen, never inside
		// the dashes.
		{"non-interactive runs use the --no-doc-quality flag", 14,
			[]string{"non-", "interactive", "runs use the", "--no-doc-", "quality flag"}},
		// a unicode em-dash is an ordinary word.
		{"estimates — not an invoice", 12, []string{"estimates —", "not an", "invoice"}},
		{"", 10, nil},
	}
	for _, c := range cases {
		if got := wrapCollapsed(c.text, c.width); !reflect.DeepEqual(got, c.want) {
			t.Errorf("wrap(%q, %d) = %q, want %q", c.text, c.width, got, c.want)
		}
	}
}

func TestWidthReadsColumns(t *testing.T) {
	t.Setenv("COLUMNS", "100")
	if got := Width(); got != 98 {
		t.Errorf("Width() with COLUMNS=100 = %d, want 98", got)
	}
	t.Setenv("COLUMNS", "not-a-number")
	if got := Width(); got != 78 {
		t.Errorf("Width() with invalid COLUMNS = %d, want the 80-col fallback 78", got)
	}
}

func TestSplitUsagePartsKeepsBracketGroups(t *testing.T) {
	parts := splitUsageParts("[-h] [--images PATH [PATH ...]] [--resume [SESSION_ID]] STORY ...")
	want := []string{"[-h]", "[--images PATH [PATH ...]]", "[--resume [SESSION_ID]]", "STORY", "..."}
	if !reflect.DeepEqual(parts, want) {
		t.Errorf("splitUsageParts = %q, want %q", parts, want)
	}
}
