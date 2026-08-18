// Engine-level tests for the argparse behaviours the cli.py tree cannot
// reach: real mutually-exclusive conflicts (the tree's one group has a
// single member), combined short options, and the Python float() edges.
// Everything the tree does exercise is covered by the golden replay in
// go/cmd/yeaboi/args_golden_test.go.
package argparse

import (
	"strings"
	"testing"
)

func twoFlagParser() *Parser {
	p := NewParser("prog")
	a := p.Add(&Action{OptionStrings: []string{"--alpha"}, Dest: "alpha", Kind: KindStoreTrue, Default: false})
	b := p.Add(&Action{OptionStrings: []string{"--beta"}, Dest: "beta", Kind: KindStoreTrue, Default: false})
	p.MutuallyExclusive(a, b)
	return p
}

func TestMutuallyExclusiveConflict(t *testing.T) {
	r := twoFlagParser().ParseArgs([]string{"--alpha", "--beta"})
	if r.Kind != ResultError {
		t.Fatalf("expected error, got %+v", r)
	}
	want := "argument --beta: not allowed with argument --alpha"
	if r.Message != want || r.Prog != "prog" {
		t.Fatalf("got %q/%q, want %q", r.Prog, r.Message, want)
	}
	if r := twoFlagParser().ParseArgs([]string{"--beta"}); r.Kind != ResultOk || r.Ns["beta"] != true {
		t.Fatalf("lone member should parse: %+v", r)
	}
}

func TestCombinedShortOptions(t *testing.T) {
	p := NewParser("prog")
	p.Add(&Action{OptionStrings: []string{"-x"}, Dest: "x", Kind: KindStoreTrue, Default: false})
	p.Add(&Action{OptionStrings: []string{"-v"}, Dest: "v", Kind: KindStoreTrue, Default: false})
	r := p.ParseArgs([]string{"-xv"})
	if r.Kind != ResultOk || r.Ns["x"] != true || r.Ns["v"] != true {
		t.Fatalf("-xv should set both flags: %+v", r)
	}
	// a short STORE option consumes the glued remainder as its value.
	p2 := NewParser("prog")
	p2.Add(&Action{OptionStrings: []string{"-o"}, Dest: "o"})
	if r := p2.ParseArgs([]string{"-oval"}); r.Kind != ResultOk || r.Ns["o"] != "val" {
		t.Fatalf("-oval should store %q: %+v", "val", r)
	}
}

func TestParsePyFloat(t *testing.T) {
	for _, tc := range []struct {
		in   string
		want float64
	}{
		{"2.5", 2.5},
		{" 3 ", 3},
		{"1e3", 1000},
		{"1_000.5", 1000.5},
		{"-0.25", -0.25},
	} {
		got, err := parsePyFloat(tc.in)
		if err != nil || got != tc.want {
			t.Errorf("parsePyFloat(%q) = %v, %v; want %v", tc.in, got, err, tc.want)
		}
	}
	for _, bad := range []string{"", "abc", "0x1p3", "1__0", "_1", "1_", "5,0"} {
		if _, err := parsePyFloat(bad); err == nil {
			t.Errorf("parsePyFloat(%q) should fail", bad)
		}
	}
}

func TestAmbiguousAbbreviationListsRegistrationOrder(t *testing.T) {
	p := NewParser("prog")
	p.Add(&Action{OptionStrings: []string{"--export-questionnaire"}, Dest: "eq"})
	p.Add(&Action{OptionStrings: []string{"--export-only"}, Dest: "eo", Kind: KindStoreTrue, Default: false})
	r := p.ParseArgs([]string{"--export"})
	if r.Kind != ResultError || !strings.HasSuffix(r.Message, "--export-questionnaire, --export-only") {
		t.Fatalf("ambiguity must list options in registration order: %+v", r)
	}
}
