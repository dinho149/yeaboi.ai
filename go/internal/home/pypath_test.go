// Tests for pypath.go's pathlib/posixpath semantics, pinned against CPython
// 3.11 behaviour (the expectations were generated with pathlib — see each
// table's comment). The whole-surface diff lives in golden_test.go; these
// pin the primitives so a failure names the exact rule that drifted.
package home

import "testing"

func mapEnv(m map[string]string) Env {
	return func(key string) (string, bool) {
		v, ok := m[key]
		return v, ok
	}
}

func TestNormPath(t *testing.T) {
	// str(PurePosixPath(x)) for each input.
	cases := map[string]string{
		"":            ".",
		".":           ".",
		"./x":         "x",
		"a//b":        "a/b",
		"a/./b":       "a/b",
		"a/b/":        "a/b",
		"a/b/.":       "a/b",
		"a/../b":      "a/../b",
		"..":          "..",
		"/":           "/",
		"//":          "//",
		"///":         "/",
		"/a/../b":     "/a/../b",
		"//x":         "//x",
		"///x":        "/x",
		"////x":       "/x",
		"/a//b/./c//": "/a/b/c",
		"~":           "~",
	}
	for in, want := range cases {
		if got := normPath(in); got != want {
			t.Errorf("normPath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestJoinPath(t *testing.T) {
	// Path(base) / name for each pair.
	cases := []struct{ base, name, want string }{
		{".", "x", "x"},
		{"/", "x", "/x"},
		{"//", "x", "//x"},
		{"/a", "x", "/a/x"},
		{"a", "x", "a/x"},
	}
	for _, c := range cases {
		if got := joinPath(c.base, c.name); got != c.want {
			t.Errorf("joinPath(%q, %q) = %q, want %q", c.base, c.name, got, c.want)
		}
	}
}

func TestExpandUser(t *testing.T) {
	env := mapEnv(map[string]string{"HOME": "/h/u"})
	cases := map[string]string{
		"~":       "/h/u",
		"~/x":     "/h/u/x",
		"~/x/y":   "/h/u/x/y",
		"/abs/~":  "/abs/~", // rooted — pathlib never expands
		"x/~/y":   "x/~/y",  // "~" not the first component
		"rel/x":   "rel/x",
		".":       ".",
		"~/../up": "/h/u/../up", // ".." stays lexical after expansion too
	}
	for in, want := range cases {
		got, err := expandUser(in, env)
		if err != nil {
			t.Errorf("expandUser(%q): %v", in, err)
			continue
		}
		if got != want {
			t.Errorf("expandUser(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestExpandUserHomeEdgeCases(t *testing.T) {
	// posixpath.expanduser rstrips "/" from the home and returns "/" when it
	// empties; the result is re-parsed, so a doubled slash collapses.
	cases := []struct{ home, in, want string }{
		{"/h/u/", "~", "/h/u"},
		{"/h//u", "~/x", "/h/u/x"},
		{"/", "~", "/"},
		{"", "~", "/"},
		{"/", "~/x", "/x"},
	}
	for _, c := range cases {
		got, err := expandUser(c.in, mapEnv(map[string]string{"HOME": c.home}))
		if err != nil {
			t.Errorf("HOME=%q expandUser(%q): %v", c.home, c.in, err)
			continue
		}
		if got != c.want {
			t.Errorf("HOME=%q expandUser(%q) = %q, want %q", c.home, c.in, got, c.want)
		}
	}
}

func TestExpandUserErrors(t *testing.T) {
	// pathlib raises RuntimeError where posixpath hands back a "~..." path.
	env := mapEnv(map[string]string{"HOME": "~oops"})
	if _, err := expandUser("~", env); err == nil {
		t.Error("HOME starting with ~ should error like pathlib's RuntimeError")
	}
	if _, err := expandUser("~no-such-user-zz/x", mapEnv(map[string]string{})); err == nil {
		t.Error("unknown ~user should error like pathlib's RuntimeError")
	}
}

func TestResolveRootPrecedence(t *testing.T) {
	// _resolve_root: stripped YEABOI_HOME wins; whitespace-only (unicode
	// whitespace included) falls back to ~/.yeaboi.
	cases := []struct{ yh, want string }{
		{"/data/yb", "/data/yb"},
		{"  /data/yb  ", "/data/yb"},
		{"\u00a0/data/yb\u2003", "/data/yb"}, // NBSP + em-space — str.strip() is unicode-aware
		{"   ", "/h/u/.yeaboi"},
		{"", "/h/u/.yeaboi"},
		{"~/yb", "/h/u/yb"},
	}
	for _, c := range cases {
		p, err := Resolve(mapEnv(map[string]string{"HOME": "/h/u", "YEABOI_HOME": c.yh}))
		if err != nil {
			t.Errorf("YEABOI_HOME=%q: %v", c.yh, err)
			continue
		}
		if p.RootDir != c.want {
			t.Errorf("YEABOI_HOME=%q → RootDir %q, want %q", c.yh, p.RootDir, c.want)
		}
		if p.EnvFile != "/h/u/.yeaboi/.env" {
			t.Errorf("YEABOI_HOME=%q → EnvFile %q, want the pinned bootstrap path", c.yh, p.EnvFile)
		}
	}
}

func TestSafeKeyFallbacks(t *testing.T) {
	// The per-mode fallback names, straight from paths.py.
	if got := SafeKey("", "project"); got != "project" {
		t.Errorf("SafeKey empty = %q", got)
	}
	if got := SafeKey("../..", "engineer"); got != "engineer" {
		t.Errorf("SafeKey traversal-only = %q", got)
	}
	if got := SafeKey("Team/Sub", "project"); got != "team-sub" {
		t.Errorf("SafeKey separators = %q", got)
	}
}
