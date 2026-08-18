// Port of src/yeaboi/paths.py's file-move surface: move_data_tree,
// migrate_root_dir and migrate_legacy_paths — keep in lockstep; the Python
// module is the reference implementation. The one W9 deferral (get_db_path's
// sqlite merge) is marked in helpers.go; everything here is pure filesystem.
package home

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// moveAny mirrors shutil.move: os.rename when the filesystem allows it, else
// copy (recursively, preserving modes) and delete the source. Python's copy2
// also preserves timestamps; parity for the gate is by path and content, so
// mtimes are left to the filesystem here.
func moveAny(src, dst string) error {
	if err := os.Rename(src, dst); err == nil {
		return nil
	}
	info, err := os.Lstat(src)
	if err != nil {
		return err
	}
	switch {
	case info.Mode()&os.ModeSymlink != 0:
		target, err := os.Readlink(src)
		if err != nil {
			return err
		}
		if err := os.Symlink(target, dst); err != nil {
			return err
		}
		return os.Remove(src)
	case info.IsDir():
		if err := copyDir(src, dst); err != nil {
			return err
		}
		return os.RemoveAll(src)
	default:
		if err := copyFile(src, dst, info.Mode()); err != nil {
			return err
		}
		return os.Remove(src)
	}
}

func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode.Perm())
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

func copyDir(src, dst string) error {
	info, err := os.Lstat(src)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dst, info.Mode().Perm()); err != nil {
		return err
	}
	entries, err := os.ReadDir(src)
	if err != nil {
		return err
	}
	for _, e := range entries {
		s, d := filepath.Join(src, e.Name()), filepath.Join(dst, e.Name())
		ei, err := e.Info()
		if err != nil {
			return err
		}
		switch {
		case ei.Mode()&os.ModeSymlink != 0:
			target, err := os.Readlink(s)
			if err != nil {
				return err
			}
			if err := os.Symlink(target, d); err != nil {
				return err
			}
		case ei.IsDir():
			if err := copyDir(s, d); err != nil {
				return err
			}
		default:
			if err := copyFile(s, d, ei.Mode()); err != nil {
				return err
			}
		}
	}
	return nil
}

// MoveDataTree ports move_data_tree: best-effort move of the current data
// tree into newRoot. The source is the *currently effective* home (re-read
// from the environment, not the Resolve-time RootDir, so a second change in
// one process moves from the right place). ".env" is skipped — it always
// stays at ~/.yeaboi/.env — and so is any child that already exists at the
// destination. Never fails; returns (ok, status message) with the exact
// message strings the Python TUI shows.
func (p *Paths) MoveDataTree(newRoot string) (bool, string) {
	raw, _ := p.env("YEABOI_HOME")
	raw = pysem.Strip(raw)
	srcRoot := p.DefaultRootDir
	if raw != "" {
		expanded, err := expandUser(normPath(raw), p.env)
		if err != nil {
			return false, "Could not determine home directory"
		}
		srcRoot = expanded
	}
	expanded, err := expandUser(normPath(newRoot), p.env)
	if err != nil {
		return false, "Could not determine home directory"
	}
	newRoot = expanded
	if srcRoot == newRoot {
		return true, "Data already lives there — nothing to move"
	}
	if !exists(srcRoot) {
		return true, "No existing data to move"
	}
	moved, skipped, failed := 0, 0, 0
	if err := mkdirAll(newRoot); err != nil {
		return false, fmt.Sprintf("Could not create %s: %s", newRoot, err)
	}
	entries, err := os.ReadDir(srcRoot)
	if err != nil {
		return false, fmt.Sprintf("Could not create %s: %s", newRoot, err)
	}
	for _, child := range entries {
		if child.Name() == ".env" {
			skipped++
			continue
		}
		target := joinPath(newRoot, child.Name())
		if exists(target) {
			skipped++
			continue
		}
		if err := moveAny(joinPath(srcRoot, child.Name()), target); err != nil {
			failed++
		} else {
			moved++
		}
	}
	msg := fmt.Sprintf("Moved %d item(s) to %s", moved, newRoot)
	if skipped > 0 {
		msg += fmt.Sprintf(", skipped %d", skipped)
	}
	if failed > 0 {
		msg += fmt.Sprintf(", failed %d (see log)", failed)
	}
	return failed == 0, msg
}

// MigrateRootDir ports migrate_root_dir: move the whole pre-rebrand
// ~/.scrum-agent tree to the current root, once, best-effort — does nothing
// when the root already exists, and never fails.
func (p *Paths) MigrateRootDir() {
	if exists(p.RootDir) || !exists(p.LegacyRootDir) {
		return
	}
	_ = moveAny(p.LegacyRootDir, p.RootDir)
}

// uuidLogRe mirrors migrate_legacy_paths' planning-log matcher.
var uuidLogRe = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-.*\.log$`)

// globNames mirrors the two shapes of Path.glob paths.py uses (a literal
// prefix + "*" tail, and "*" + literal suffix): non-recursive, files and dirs
// alike, and — like pathlib — never matching dotfiles.
func globNames(dir, prefix, suffix string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, e := range entries {
		name := e.Name()
		if strings.HasPrefix(name, ".") {
			continue
		}
		if strings.HasPrefix(name, prefix) && strings.HasSuffix(name[len(prefix):], suffix) {
			names = append(names, name)
		}
	}
	return names, nil
}

// MigrateLegacyPaths ports migrate_legacy_paths: migrate files from the
// legacy flat structure to the organised one. Called once at startup; safe to
// call multiple times — skips anything already migrated.
func (p *Paths) MigrateLegacyPaths() error {
	// First, move the whole tree over from the pre-rebrand ~/.scrum-agent dir.
	p.MigrateRootDir()

	// Migrate sessions.db
	if exists(p.LegacyDBPath) && !exists(p.DBPath) {
		if err := mkdirAll(p.DataDir); err != nil {
			return err
		}
		if err := os.Rename(p.LegacyDBPath, p.DBPath); err != nil {
			return err
		}
	}

	// Migrate states/
	if exists(p.LegacyStatesDir) && !exists(p.StatesDir) {
		if err := mkdirAll(p.DataDir); err != nil {
			return err
		}
		if err := os.Rename(p.LegacyStatesDir, p.StatesDir); err != nil {
			return err
		}
	}

	// Migrate projects.json
	if exists(p.LegacyProjectsFile) && !exists(p.ProjectsFile) {
		if err := mkdirAll(p.DataDir); err != nil {
			return err
		}
		if err := os.Rename(p.LegacyProjectsFile, p.ProjectsFile); err != nil {
			return err
		}
	}

	// Migrate main log (flat ROOT_DIR/scrum-agent.log → logs/tui/yeaboi.log)
	newTUILog := joinPath(p.TUILogsDir, "yeaboi.log")
	if exists(p.LegacyTUILog) && !exists(newTUILog) {
		if err := mkdirAll(p.TUILogsDir); err != nil {
			return err
		}
		if err := os.Rename(p.LegacyTUILog, newTUILog); err != nil {
			return err
		}
		// Also move rotated logs
		rots, err := globNames(p.RootDir, "scrum-agent.log.", "")
		if err != nil {
			return err
		}
		for _, rot := range rots {
			dst := joinPath(p.TUILogsDir, strings.ReplaceAll(rot, "scrum-agent.log", "yeaboi.log"))
			if err := os.Rename(joinPath(p.RootDir, rot), dst); err != nil {
				return err
			}
		}
	}

	// Migrate a previously-organised pre-rebrand log (logs/tui/scrum-agent.log → yeaboi.log)
	oldOrganisedLog := joinPath(p.TUILogsDir, "scrum-agent.log")
	if exists(oldOrganisedLog) && !exists(newTUILog) {
		if err := os.Rename(oldOrganisedLog, newTUILog); err != nil {
			return err
		}
		rots, err := globNames(p.TUILogsDir, "scrum-agent.log.", "")
		if err != nil {
			return err
		}
		for _, rot := range rots {
			dst := joinPath(p.TUILogsDir, strings.ReplaceAll(rot, "scrum-agent.log", "yeaboi.log"))
			if err := os.Rename(joinPath(p.TUILogsDir, rot), dst); err != nil {
				return err
			}
		}
	}

	// Migrate analysis logs (team-analysis-*.log → logs/analysis/)
	if exists(p.LogsDir) {
		names, err := globNames(p.LogsDir, "team-analysis-", ".log")
		if err != nil {
			return err
		}
		for _, name := range names {
			if err := mkdirAll(p.AnalysisLogsDir); err != nil {
				return err
			}
			if err := os.Rename(joinPath(p.LogsDir, name), joinPath(p.AnalysisLogsDir, name)); err != nil {
				return err
			}
		}
	}

	// Migrate planning session logs (UUID.log → logs/planning/)
	if exists(p.LogsDir) {
		names, err := globNames(p.LogsDir, "", ".log")
		if err != nil {
			return err
		}
		for _, name := range names {
			if !uuidLogRe.MatchString(name) {
				continue
			}
			if err := mkdirAll(p.PlanningLogsDir); err != nil {
				return err
			}
			if err := os.Rename(joinPath(p.LogsDir, name), joinPath(p.PlanningLogsDir, name)); err != nil {
				return err
			}
		}
	}

	// Migrate exports/{project_key}/ → exports/analysis/{project_key}/
	if exists(p.ExportsDir) {
		entries, err := os.ReadDir(p.ExportsDir)
		if err != nil {
			return err
		}
		for _, d := range entries {
			// Path.is_dir() follows symlinks, hence os.Stat here too.
			di, err := os.Stat(joinPath(p.ExportsDir, d.Name()))
			if err != nil || !di.IsDir() || d.Name() == "analysis" || d.Name() == "planning" {
				continue
			}
			// Check if it has team-profile files (analysis exports)
			children, err := os.ReadDir(joinPath(p.ExportsDir, d.Name()))
			if err != nil {
				return err
			}
			hasAnalysis := false
			for _, f := range children {
				// Path.is_file() follows symlinks and swallows a broken one,
				// hence os.Stat rather than the dirent's own type.
				fi, err := os.Stat(joinPath(p.ExportsDir, d.Name(), f.Name()))
				if err == nil && fi.Mode().IsRegular() && strings.HasPrefix(f.Name(), "team-profile") {
					hasAnalysis = true
					break
				}
			}
			if !hasAnalysis {
				continue
			}
			target := joinPath(p.AnalysisExportsDir, d.Name())
			if exists(target) {
				continue
			}
			if err := mkdirAll(p.AnalysisExportsDir); err != nil {
				return err
			}
			if err := moveAny(joinPath(p.ExportsDir, d.Name()), target); err != nil {
				return err
			}
		}
	}

	return nil
}
