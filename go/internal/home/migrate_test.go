// Tests for migrate.go's file-move surface, mirroring the shapes
// tests/unit/test_paths.py exercises on the Python side: the pre-rebrand
// tree move, the flat→organised migration, move_data_tree's skip rules and
// exact status strings, and get_db_path's legacy rename.
package home

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

// resolveAt builds a Paths rooted inside tmp (HOME=tmp/home,
// YEABOI_HOME=tmp/root unless overridden by extra).
func resolveAt(t *testing.T, tmp string, extra map[string]string) *Paths {
	t.Helper()
	env := map[string]string{
		"HOME":        filepath.Join(tmp, "home"),
		"YEABOI_HOME": filepath.Join(tmp, "root"),
	}
	for k, v := range extra {
		env[k] = v
	}
	p, err := Resolve(mapEnv(env))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func write(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestMigrateRootDir(t *testing.T) {
	tmp := t.TempDir()
	p := resolveAt(t, tmp, nil)
	write(t, joinPath(p.LegacyRootDir, "sessions.db"), "legacy")

	p.MigrateRootDir()
	if exists(p.LegacyRootDir) {
		t.Error("legacy tree should have moved")
	}
	if !exists(joinPath(p.RootDir, "sessions.db")) {
		t.Error("legacy contents should now live under the root")
	}

	// Idempotent: with the root present, a fresh legacy tree stays put.
	write(t, joinPath(p.LegacyRootDir, "again"), "x")
	p.MigrateRootDir()
	if !exists(joinPath(p.LegacyRootDir, "again")) {
		t.Error("migrate must do nothing once the root exists")
	}
}

func TestGetDBPathLegacyRename(t *testing.T) {
	tmp := t.TempDir()
	p := resolveAt(t, tmp, nil)
	write(t, p.LegacyDBPath, "legacy-db")

	got, err := p.GetDBPath()
	if err != nil {
		t.Fatal(err)
	}
	if got != p.DBPath {
		t.Errorf("GetDBPath = %q, want %q", got, p.DBPath)
	}
	if exists(p.LegacyDBPath) {
		t.Error("legacy DB should have been renamed into data/")
	}
	body, err := os.ReadFile(p.DBPath)
	if err != nil || string(body) != "legacy-db" {
		t.Errorf("renamed DB content = %q, %v", body, err)
	}
}

func TestGetDBPathCreatesAndHardens(t *testing.T) {
	tmp := t.TempDir()
	p := resolveAt(t, tmp, nil)
	if _, err := p.GetDBPath(); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(p.DBPath)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Errorf("DB perms = %o, want 600", fi.Mode().Perm())
	}
	di, err := os.Stat(p.DataDir)
	if err != nil {
		t.Fatal(err)
	}
	if di.Mode().Perm() != 0o700 {
		t.Errorf("data dir perms = %o, want 700", di.Mode().Perm())
	}
}

func TestMoveDataTree(t *testing.T) {
	tmp := t.TempDir()
	p := resolveAt(t, tmp, nil)
	src := joinPath(tmp, "root")
	write(t, joinPath(src, ".env"), "KEEP=1")
	write(t, joinPath(src, "data", "sessions.db"), "db")
	write(t, joinPath(src, "repl-history"), "h")
	dst := joinPath(tmp, "new-root")
	write(t, joinPath(dst, "repl-history"), "already-there")

	ok, msg := p.MoveDataTree(dst)
	if !ok {
		t.Fatalf("MoveDataTree failed: %s", msg)
	}
	want := fmt.Sprintf("Moved 1 item(s) to %s, skipped 2", dst)
	if msg != want {
		t.Errorf("msg = %q, want %q", msg, want)
	}
	if !exists(joinPath(src, ".env")) {
		t.Error(".env must never move")
	}
	if body, _ := os.ReadFile(joinPath(dst, "repl-history")); string(body) != "already-there" {
		t.Error("an existing destination child must not be overwritten")
	}
	if !exists(joinPath(dst, "data", "sessions.db")) {
		t.Error("the data tree should have moved")
	}
}

func TestMoveDataTreeQuietOutcomes(t *testing.T) {
	tmp := t.TempDir()
	p := resolveAt(t, tmp, nil)

	ok, msg := p.MoveDataTree(joinPath(tmp, "root"))
	if !ok || msg != "Data already lives there — nothing to move" {
		t.Errorf("same-root: ok=%v msg=%q", ok, msg)
	}

	ok, msg = p.MoveDataTree(joinPath(tmp, "elsewhere"))
	if !ok || msg != "No existing data to move" {
		t.Errorf("missing-source: ok=%v msg=%q", ok, msg)
	}
}

func TestMigrateLegacyPaths(t *testing.T) {
	tmp := t.TempDir()
	p := resolveAt(t, tmp, nil)
	write(t, p.LegacyDBPath, "db")
	write(t, p.LegacyProjectsFile, "{}")
	write(t, joinPath(p.LegacyStatesDir, "s.json"), "{}")
	write(t, p.LegacyTUILog, "log")
	write(t, joinPath(p.RootDir, "scrum-agent.log.1"), "rot1")
	write(t, joinPath(p.LogsDir, "team-analysis-2025.log"), "a")
	write(t, joinPath(p.LogsDir, "0123abcd-89ab-cdef.log"), "planning")
	write(t, joinPath(p.LogsDir, "not-a-uuid.log"), "stay")
	write(t, joinPath(p.ExportsDir, "proj", "team-profile.md"), "profile")
	write(t, joinPath(p.ExportsDir, "other", "notes.md"), "n")

	if err := p.MigrateLegacyPaths(); err != nil {
		t.Fatal(err)
	}

	checks := map[string]bool{
		p.DBPath:                                                  true,
		p.ProjectsFile:                                            true,
		joinPath(p.StatesDir, "s.json"):                           true,
		joinPath(p.TUILogsDir, "yeaboi.log"):                      true,
		joinPath(p.TUILogsDir, "yeaboi.log.1"):                    true,
		joinPath(p.AnalysisLogsDir, "team-analysis-2025.log"):     true,
		joinPath(p.PlanningLogsDir, "0123abcd-89ab-cdef.log"):     true,
		joinPath(p.LogsDir, "not-a-uuid.log"):                     true, // non-UUID stays put
		joinPath(p.AnalysisExportsDir, "proj", "team-profile.md"): true,
		joinPath(p.ExportsDir, "other", "notes.md"):               true, // no team-profile ⇒ stays
		p.LegacyDBPath:                                            false,
		p.LegacyTUILog:                                            false,
		joinPath(p.ExportsDir, "proj"):                            false,
	}
	for path, want := range checks {
		if exists(path) != want {
			t.Errorf("%s: exists=%v, want %v", path, !want, want)
		}
	}

	// Safe to call again — nothing left to move, nothing errors.
	if err := p.MigrateLegacyPaths(); err != nil {
		t.Fatal(err)
	}
}
