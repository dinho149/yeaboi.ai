// Golden-driven parity for the logfile surface: replays the "logfile"
// section of every committed fixture under tests/parity/goldens/
// foundations/ against dumpLogfile (and therefore internal/logfile). The
// Python freeze test keeps those files honest against logging_setup.py and
// redaction.py, so passing here is Python ↔ Go parity without a binary in
// the loop — the same shape as internal/home's and internal/config's
// replays, just run from this package because the scenario spans both.
package foundations

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/config"
	"github.com/yeaboi-ai/yeaboi/go/internal/home"
)

const goldensDirTest = "../../../tests/parity/goldens/foundations"

type logfileGolden struct {
	Env   map[string]string `json:"env"`
	Files map[string]string `json:"files"`
	Dump  struct {
		Logfile map[string]any `json:"logfile"`
	} `json:"dump"`
}

func chdirTest(t *testing.T, dir string) {
	t.Helper()
	old, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(old) })
}

// canonicalJSON round-trips a Go value through JSON so it compares cleanly
// against the golden's decoded shape (float64 numbers, nil, []any).
func canonicalJSON(t *testing.T, v any) any {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return out
}

func TestLogfileGoldenParity(t *testing.T) {
	files, err := filepath.Glob(filepath.Join(goldensDirTest, "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(files) == 0 {
		t.Fatalf("no goldens under %s — run `uv run python -m tests.parity.foundations.regen`", goldensDirTest)
	}
	for _, file := range files {
		name := strings.TrimSuffix(filepath.Base(file), ".json")
		t.Run(name, func(t *testing.T) {
			raw, err := os.ReadFile(file)
			if err != nil {
				t.Fatal(err)
			}
			var g logfileGolden
			if err := json.Unmarshal(raw, &g); err != nil {
				t.Fatal(err)
			}
			if g.Dump.Logfile == nil {
				t.Fatalf("golden %s carries no logfile section — regenerate", name)
			}
			tmp := t.TempDir()
			chdirTest(t, tmp)
			fill := func(s string) string { return strings.ReplaceAll(s, "{tmp}", tmp) }
			env := func(key string) (string, bool) {
				v, ok := g.Env[key]
				if !ok {
					return "", false
				}
				return fill(v), true
			}
			for rel, content := range g.Files {
				path := filepath.Join(tmp, filepath.FromSlash(rel))
				if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			if homeEnv, ok := env("HOME"); ok {
				if err := os.MkdirAll(homeEnv, 0o755); err != nil {
					t.Fatal(err)
				}
			}
			paths, err := home.Resolve(env)
			if err != nil {
				t.Fatal(err)
			}
			cfg, err := config.Load(env, tmp, paths.EnvFile)
			if err != nil {
				t.Fatal(err)
			}
			got, err := dumpLogfile(paths, cfg)
			if err != nil {
				t.Fatal(err)
			}
			// The golden stores {tmp}-templated strings; nothing in the
			// logfile section carries sandbox paths (files are LOGS_DIR-
			// relative), so a direct canonical compare holds.
			gotC := canonicalJSON(t, got)
			wantC := canonicalJSON(t, g.Dump.Logfile)
			if !jsonEqual(gotC, wantC) {
				gb, _ := json.MarshalIndent(gotC, "", "  ")
				wb, _ := json.MarshalIndent(wantC, "", "  ")
				t.Fatalf("logfile dump disagrees with the golden\n--- got ---\n%s\n--- want ---\n%s", gb, wb)
			}
		})
	}
}

func jsonEqual(a, b any) bool {
	ab, _ := json.Marshal(a)
	bb, _ := json.Marshal(b)
	return string(ab) == string(bb)
}
