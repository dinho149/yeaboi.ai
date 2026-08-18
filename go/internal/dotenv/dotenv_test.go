package dotenv

import (
	"os"
	"path/filepath"
	"testing"
)

func sp(s string) *string { return &s }

// The expectations below were captured by running the pinned python-dotenv
// against the same inputs (see tests/parity/foundations/ for the
// golden-driven corpus; these are the parser's own edge-case spot checks).
func TestParseEdgeCases(t *testing.T) {
	cases := []struct {
		name string
		src  string
		want []Binding
	}{
		{"trailing backslash backtracks", `A='\\'`, []Binding{{Key: sp("A"), Value: sp(`\`), Original: `A='\\'`}}},
		{"escaped quote inside", `B='a\'b'`, []Binding{{Key: sp("B"), Value: sp("a'b"), Original: `B='a\'b'`}}},
		{"escaped quote then EOF", `C='a\'`, []Binding{{Key: sp("C"), Value: sp(`a\`), Original: `C='a\'`}}},
		{"double-quote escapes", `D="x\n\t\"y\a"`, []Binding{{Key: sp("D"), Value: sp("x\n\t\"y\a"), Original: `D="x\n\t\"y\a"`}}},
		{"export prefix", "export E=1", []Binding{{Key: sp("E"), Value: sp("1"), Original: "export E=1"}}},
		{"export glued to key", "exportF=2", []Binding{{Key: sp("exportF"), Value: sp("2"), Original: "exportF=2"}}},
		{"bare export", "export", []Binding{{Key: sp("export"), Original: "export"}}},
		{"quoted key", "'quoted key'=v", []Binding{{Key: sp("quoted key"), Value: sp("v"), Original: "'quoted key'=v"}}},
		{"key alone", "G", []Binding{{Key: sp("G"), Original: "G"}}},
		{"empty value", "H=", []Binding{{Key: sp("H"), Value: sp(""), Original: "H="}}},
		{"spaces and tail comment", "I=  spaced value  # tail comment",
			[]Binding{{Key: sp("I"), Value: sp("spaced value"), Original: "I=  spaced value  # tail comment"}}},
		{"hash without space kept", "J=val#nospace", []Binding{{Key: sp("J"), Value: sp("val#nospace"), Original: "J=val#nospace"}}},
		{"no key", "=bad", []Binding{{Original: "=bad", Err: true}}},
		{"unterminated quote", "L='unterminated", []Binding{{Original: "L='unterminated", Err: true}}},
		{"two quoted chunks", "R='a''b'", []Binding{{Original: "R='a''b'", Err: true}}},
		{"crlf lines", "M=ok\r\nN=crlf\r",
			[]Binding{{Key: sp("M"), Value: sp("ok"), Original: "M=ok\r\n"}, {Key: sp("N"), Value: sp("crlf"), Original: "N=crlf\r"}}},
		{"multiline quoted value", "O='multi\nline'\nP=q",
			[]Binding{{Key: sp("O"), Value: sp("multi\nline"), Original: "O='multi\nline'\n"}, {Key: sp("P"), Value: sp("q"), Original: "P=q"}}},
		{"leading blank lines", "  \n\n  Q=indented", []Binding{{Key: sp("Q"), Value: sp("indented"), Original: "  \n\n  Q=indented"}}},
		{"comment line", "# note\nA=1",
			[]Binding{{Original: "# note\n"}, {Key: sp("A"), Value: sp("1"), Original: "A=1"}}},
		{"value then junk errors", "A='x' junk", []Binding{{Original: "A='x' junk", Err: true}}},
		{"nbsp is whitespace", "A=v # c", []Binding{{Key: sp("A"), Value: sp("v"), Original: "A=v # c"}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Parse(tc.src)
			if len(got) != len(tc.want) {
				t.Fatalf("Parse(%q) = %d bindings, want %d: %+v", tc.src, len(got), len(tc.want), got)
			}
			for i := range got {
				assertBinding(t, tc.src, got[i], tc.want[i])
			}
		})
	}
}

func assertBinding(t *testing.T, src string, got, want Binding) {
	t.Helper()
	if (got.Key == nil) != (want.Key == nil) || (got.Key != nil && *got.Key != *want.Key) {
		t.Errorf("Parse(%q) key = %v, want %v", src, strp(got.Key), strp(want.Key))
	}
	if (got.Value == nil) != (want.Value == nil) || (got.Value != nil && *got.Value != *want.Value) {
		t.Errorf("Parse(%q) value = %v, want %v", src, strp(got.Value), strp(want.Value))
	}
	if got.Original != want.Original {
		t.Errorf("Parse(%q) original = %q, want %q", src, got.Original, want.Original)
	}
	if got.Err != want.Err {
		t.Errorf("Parse(%q) err = %v, want %v", src, got.Err, want.Err)
	}
}

func strp(p *string) string {
	if p == nil {
		return "<nil>"
	}
	return *p
}

func TestResolveInterpolation(t *testing.T) {
	environ := func(k string) (string, bool) {
		if k == "IN_ENV" {
			return "env-wins", true
		}
		return "", false
	}
	pairs := Pairs("A=${IN_ENV}\nB=${A:-fb}\nC=${MISSING:-fallback}\nD=${MISSING}\nE=${A:x}\nA=${A}-again")
	got := Resolve(pairs, environ, false)
	want := map[string]string{
		"A": "env-wins-again", // last occurrence wins in the dict; earlier A visible to it
		"B": "env-wins",
		"C": "fallback",
		"D": "",
		"E": "${A:x}", // ':' without '-' is not a variable token
	}
	final := map[string]string{}
	for _, p := range got {
		if p.Value != nil {
			final[p.Key] = *p.Value
		}
	}
	for k, w := range want {
		if final[k] != w {
			t.Errorf("Resolve %s = %q, want %q", k, final[k], w)
		}
	}
}

func TestLoadIntoSkipsExistingAndNilValues(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	if err := os.WriteFile(path, []byte("SET=1\nBARE\nNEW=2\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	environ := func(k string) (string, bool) {
		if k == "SET" {
			return "already", true
		}
		return "", false
	}
	got := LoadInto(path, environ)
	if _, ok := got["SET"]; ok {
		t.Errorf("LoadInto overrode an existing key")
	}
	if _, ok := got["BARE"]; ok {
		t.Errorf("LoadInto added a value-less key")
	}
	if got["NEW"] != "2" {
		t.Errorf("NEW = %q, want 2", got["NEW"])
	}
}

func TestFindWalksToRoot(t *testing.T) {
	root := t.TempDir()
	nested := filepath.Join(root, "a", "b")
	if err := os.MkdirAll(nested, 0o755); err != nil {
		t.Fatal(err)
	}
	envPath := filepath.Join(root, ".env")
	if err := os.WriteFile(envPath, []byte("A=1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := Find(nested); got != envPath {
		t.Errorf("Find = %q, want %q", got, envPath)
	}
}

func TestSetKeySemantics(t *testing.T) {
	t.Run("creates missing file at 0600", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), ".env")
		if err := SetKey(path, "FOO", "bar"); err != nil {
			t.Fatal(err)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if string(data) != "FOO='bar'\n" {
			t.Errorf("content = %q", data)
		}
		fi, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		if fi.Mode().Perm() != 0o600 {
			t.Errorf("mode = %o, want 600", fi.Mode().Perm())
		}
	})

	t.Run("replaces every occurrence, preserves the rest", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), ".env")
		initial := "# comment\nFOO=old\nOTHER=1\nexport FOO=older\n=junk\n"
		if err := os.WriteFile(path, []byte(initial), 0o640); err != nil {
			t.Fatal(err)
		}
		if err := SetKey(path, "FOO", "new"); err != nil {
			t.Fatal(err)
		}
		data, _ := os.ReadFile(path)
		want := "# comment\nFOO='new'\nOTHER=1\nFOO='new'\n=junk\n"
		if string(data) != want {
			t.Errorf("content = %q, want %q", data, want)
		}
		fi, _ := os.Stat(path)
		if fi.Mode().Perm() != 0o640 {
			t.Errorf("mode = %o, want the original 640", fi.Mode().Perm())
		}
	})

	t.Run("appends after a newline-less tail", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), ".env")
		if err := os.WriteFile(path, []byte("A=1"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := SetKey(path, "B", "it's"); err != nil {
			t.Fatal(err)
		}
		data, _ := os.ReadFile(path)
		want := "A=1\nB='it\\'s'\n"
		if string(data) != want {
			t.Errorf("content = %q, want %q", data, want)
		}
	})
}
