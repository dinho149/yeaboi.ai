// Unit tests for the corners the golden replay reaches only indirectly —
// the parity goldens (tests/parity/goldens/foundations/, replayed by
// internal/foundations) remain the byte-level gate.
package logfile

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func noEnv(string) (string, bool) { return "", false }

func envOf(m map[string]string) EnvLookup {
	return func(key string) (string, bool) {
		v, ok := m[key]
		return v, ok
	}
}

func TestResolveLevel(t *testing.T) {
	cases := map[string]int{
		"DEBUG": 10, "info": 20, "Warning": 30, "warn": 30, "ERROR": 40,
		"fatal": 50, "CRITICAL": 50, "notset": 0, "VERBOSE": 30, "": 30, " warning ": 30,
	}
	for in, want := range cases {
		if got := ResolveLevel(in); got != want {
			t.Errorf("ResolveLevel(%q) = %d, want %d", in, got, want)
		}
	}
}

func TestLogSafeBoundaries(t *testing.T) {
	if got := LogSafe("a\r\nb\tc"); got != "a  b c" {
		t.Errorf("collapse: %q", got)
	}
	if got := LogSafe(strings.Repeat("x", 200)); len([]rune(got)) != 200 || strings.Contains(got, "…") {
		t.Errorf("exactly-200 must pass through: %q", got[:20])
	}
	got := LogSafe(strings.Repeat("é", 230))
	runes := []rune(got)
	if len(runes) != 200 || runes[199] != '…' || runes[0] != 'é' {
		t.Errorf("truncation must slice code points and end with …: len=%d", len(runes))
	}
}

func TestRedactValueLayer(t *testing.T) {
	env := envOf(map[string]string{
		"NOTION_TOKEN":      "hush-hush-value-123-extended",
		"ANTHROPIC_API_KEY": "hush-hush-value-123",
		"GITHUB_TOKEN":      "tiny", // below _MIN_SECRET_LEN — never matched
	})
	got := Redact("a hush-hush-value-123-extended b hush-hush-value-123 c tiny", env)
	if got != "a [REDACTED] b [REDACTED] c tiny" {
		t.Errorf("longest-first value redaction: %q", got)
	}
}

func TestRedactURLCredentialLookaround(t *testing.T) {
	// The lookbehind: no "://" prefix, no match.
	if got := Redact("svc:longpassword@host", noEnv); got != "svc:longpassword@host" {
		t.Errorf("no-scheme creds must survive: %q", got)
	}
	// The lookahead: scanning resumes AT the "@", not after it.
	if got := Redact("x://user:longpass@host", noEnv); got != "x://[REDACTED]@host" {
		t.Errorf("url creds: %q", got)
	}
	// Positional preference: a token pattern listed before the URL rule
	// wins a same-offset tie, exactly like Python's single alternation.
	got := Redact("ftp://ghp_ABCDEFGHIJKLMNOPQRST:pw12@x", noEnv)
	if got != "ftp://[REDACTED]:pw12@x" {
		t.Errorf("alternation order: %q", got)
	}
}

func TestRotationDropsTheOldestBackup(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "rot.log")
	f := &Formatter{Loc: time.UTC, Lookup: noEnv}
	h, err := NewHandler(path, 80, 2, LevelNotSet, f)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 8; i++ {
		rec := Record{Name: "yeaboi.rot", Level: LevelInfo, Message: strings.Repeat("m", 30), Created: int64(1755500000 + i)}
		if err := h.Emit(rec); err != nil {
			t.Fatal(err)
		}
	}
	if err := h.Close(); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"rot.log", "rot.log.1", "rot.log.2"} {
		info, err := os.Stat(filepath.Join(dir, name))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if perm := info.Mode().Perm(); perm != 0o600 {
			t.Errorf("%s: mode %o, want 600 (rename preserves, open re-hardens)", name, perm)
		}
	}
	if _, err := os.Stat(filepath.Join(dir, "rot.log.3")); !os.IsNotExist(err) {
		t.Error("backupCount=2 must never leave a .3")
	}
}

func TestRegistryIdempotenceAndSessionReplace(t *testing.T) {
	dir := t.TempDir()
	f := &Formatter{Loc: time.UTC, Lookup: noEnv}
	reg := NewRegistry(func() string { return "WARNING" }, f)
	tui := filepath.Join(dir, "tui", "yeaboi.log")
	if err := reg.Attach("tui", tui); err != nil {
		t.Fatal(err)
	}
	if err := reg.Attach("tui", filepath.Join(dir, "elsewhere.log")); err != nil {
		t.Fatal(err)
	}
	if err := reg.AttachSession(filepath.Join(dir, "planning", "a.log")); err != nil {
		t.Fatal(err)
	}
	if err := reg.AttachSession(filepath.Join(dir, "planning", "b.log")); err != nil {
		t.Fatal(err)
	}
	keys := reg.Keys()
	if len(keys) != 2 || keys[0] != "session" || keys[1] != "tui" {
		t.Fatalf("keys: %v", keys)
	}
	if err := reg.Emit(Record{Name: "yeaboi.x", Level: LevelError, Message: "boom", Created: 1755500000}); err != nil {
		t.Fatal(err)
	}
	reg.Close()
	if raw, _ := os.ReadFile(filepath.Join(dir, "elsewhere.log")); len(raw) != 0 {
		t.Error("re-attaching an existing key must be a no-op")
	}
	b, err := os.ReadFile(filepath.Join(dir, "planning", "b.log"))
	if err != nil || !strings.Contains(string(b), "boom") {
		t.Errorf("the replacing session file must receive records: %v %q", err, b)
	}
	if a, _ := os.ReadFile(filepath.Join(dir, "planning", "a.log")); len(a) != 0 {
		t.Error("the replaced session file must stop receiving records")
	}
	info, err := os.Stat(filepath.Join(dir, "tui"))
	if err != nil || info.Mode().Perm() != 0o700 {
		t.Errorf("attach must harden the parent dir to 0700: %v", err)
	}
}
