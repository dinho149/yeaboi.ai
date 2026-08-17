// Port of src/yeaboi/paths.py's helper functions (_safe_key and the
// get_* dir/path getters — mkdir + permission hardening + key
// normalisation) — keep in lockstep; the Python module is the reference
// implementation and tests/parity/foundations/ diffs whole-surface output.
package home

import (
	"os"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// mkdirAll mirrors Path.mkdir(parents=True, exist_ok=True): mode 0o777,
// masked by the process umask exactly as Python's default is.
func mkdirAll(path string) error {
	return os.MkdirAll(path, 0o777)
}

// touch mirrors Path.touch(mode=0o600) for the only case paths.py uses it:
// the file does not exist yet, so the mode applies at creation.
func touch(path string, mode os.FileMode) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY, mode)
	if err != nil {
		return err
	}
	return f.Close()
}

// exists mirrors Path.exists(): follows symlinks (a broken link is "absent").
func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// GetDBPath ports get_db_path: returns the sessions DB path, migrating from
// the legacy location if needed, and hardens permissions (0o700 dir, 0o600
// file) on every call.
//
// W9: the both-exist branch's sqlite merge (team_profiles/token_usage copied
// from the legacy DB, which is then removed) is NOT ported — it needs the
// persistence wave's sqlite plumbing. Until then Go returns the path with
// permissions hardened and leaves both files alone; Python remains the only
// writer of that migration.
func (p *Paths) GetDBPath() (string, error) {
	if err := mkdirAll(p.DataDir); err != nil {
		return "", err
	}
	restrictPermissions(p.DataDir, 0o700)
	if exists(p.DBPath) {
		restrictPermissions(p.DBPath, 0o600)
	} else if !exists(p.LegacyDBPath) {
		if err := touch(p.DBPath, 0o600); err != nil {
			return "", err
		}
	}

	if exists(p.DBPath) && exists(p.LegacyDBPath) {
		// W9: merge legacy data into the new DB, then remove the legacy file.
		return p.DBPath, nil
	}

	if !exists(p.DBPath) && exists(p.LegacyDBPath) {
		if err := os.Rename(p.LegacyDBPath, p.DBPath); err != nil {
			return "", err
		}
		return p.DBPath, nil
	}

	return p.DBPath, nil
}

// GetReportingThemesPath ports get_reporting_themes_path (may not exist yet).
func (p *Paths) GetReportingThemesPath() (string, error) {
	if err := mkdirAll(p.DataDir); err != nil {
		return "", err
	}
	return p.ReportingThemesFile, nil
}

// GetReportingPrefsPath ports get_reporting_prefs_path (may not exist yet).
func (p *Paths) GetReportingPrefsPath() (string, error) {
	if err := mkdirAll(p.DataDir); err != nil {
		return "", err
	}
	return p.ReportingPrefsFile, nil
}

// GetVoiceInstallPath ports get_voice_install_path (may not exist yet).
func (p *Paths) GetVoiceInstallPath() (string, error) {
	if err := mkdirAll(p.DataDir); err != nil {
		return "", err
	}
	return p.VoiceInstallFile, nil
}

// SafeKey ports _safe_key: normalise a project/engineer key into a single
// safe directory name. Keys are app-derived, but defense in depth: a key
// containing separators or ".." must never escape its export root.
// str.lower() and str.strip() are unicode-aware, hence pysem.
func SafeKey(key, fallback string) string {
	cleaned := strings.ReplaceAll(pysem.Strip(pysem.Lower(key)), "\\", "/")
	var parts []string
	for _, part := range strings.Split(cleaned, "/") {
		if part == "" || part == "." || part == ".." {
			continue
		}
		parts = append(parts, part)
	}
	joined := strings.Join(parts, "-")
	if joined == "" {
		return fallback
	}
	return joined
}

// exportDir is the shared body of the per-mode export getters.
func exportDir(base, key, fallback string) (string, error) {
	d := joinPath(base, SafeKey(key, fallback))
	if err := mkdirAll(d); err != nil {
		return "", err
	}
	return d, nil
}

// GetAnalysisExportDir ports get_analysis_export_dir.
func (p *Paths) GetAnalysisExportDir(projectKey string) (string, error) {
	return exportDir(p.AnalysisExportsDir, projectKey, "project")
}

// GetPlanningExportDir ports get_planning_export_dir.
func (p *Paths) GetPlanningExportDir(projectKey string) (string, error) {
	return exportDir(p.PlanningExportsDir, projectKey, "project")
}

// GetStandupExportDir ports get_standup_export_dir.
func (p *Paths) GetStandupExportDir(projectKey string) (string, error) {
	return exportDir(p.StandupExportsDir, projectKey, "project")
}

// GetRetroExportDir ports get_retro_export_dir.
func (p *Paths) GetRetroExportDir(projectKey string) (string, error) {
	return exportDir(p.RetroExportsDir, projectKey, "project")
}

// GetPokerExportDir ports get_poker_export_dir.
func (p *Paths) GetPokerExportDir(projectKey string) (string, error) {
	return exportDir(p.PokerExportsDir, projectKey, "project")
}

// GetPerformanceExportDir ports get_performance_export_dir (per-engineer, so
// a lead can find one person's documents together).
func (p *Paths) GetPerformanceExportDir(engineerKey string) (string, error) {
	return exportDir(p.PerformanceExportsDir, engineerKey, "engineer")
}

// GetReportingExportDir ports get_reporting_export_dir.
func (p *Paths) GetReportingExportDir(projectKey string) (string, error) {
	return exportDir(p.ReportingExportsDir, projectKey, "report")
}

// GetRoadmapExportDir ports get_roadmap_export_dir.
func (p *Paths) GetRoadmapExportDir(roadmapKey string) (string, error) {
	return exportDir(p.RoadmapExportsDir, roadmapKey, "roadmap")
}

// GetAnonymizeExportDir ports get_anonymize_export_dir (the privacy-masked,
// shareable copies, kept apart from the un-masked exports).
func (p *Paths) GetAnonymizeExportDir(projectKey string) (string, error) {
	return exportDir(p.AnonymizeExportsDir, projectKey, "output")
}

// GetAgentwatchExportDir ports get_agentwatch_export_dir (per report kind:
// "usage", "advisor", "standup", "security").
func (p *Paths) GetAgentwatchExportDir(kindKey string) (string, error) {
	return exportDir(p.AgentwatchExportsDir, kindKey, "report")
}

// logDir is the shared body of the per-mode log getters.
func logDir(d string) (string, error) {
	if err := mkdirAll(d); err != nil {
		return "", err
	}
	return d, nil
}

// GetTUILogPath ports get_tui_log_path: the main TUI log path.
func (p *Paths) GetTUILogPath() (string, error) {
	if err := mkdirAll(p.TUILogsDir); err != nil {
		return "", err
	}
	return joinPath(p.TUILogsDir, "yeaboi.log"), nil
}

// GetAnalysisLogDir ports get_analysis_log_dir.
func (p *Paths) GetAnalysisLogDir() (string, error) { return logDir(p.AnalysisLogsDir) }

// GetPlanningLogDir ports get_planning_log_dir.
func (p *Paths) GetPlanningLogDir() (string, error) { return logDir(p.PlanningLogsDir) }

// GetStandupLogDir ports get_standup_log_dir.
func (p *Paths) GetStandupLogDir() (string, error) { return logDir(p.StandupLogsDir) }

// GetRetroLogDir ports get_retro_log_dir.
func (p *Paths) GetRetroLogDir() (string, error) { return logDir(p.RetroLogsDir) }

// GetPokerLogDir ports get_poker_log_dir.
func (p *Paths) GetPokerLogDir() (string, error) { return logDir(p.PokerLogsDir) }

// GetPerformanceLogDir ports get_performance_log_dir.
func (p *Paths) GetPerformanceLogDir() (string, error) { return logDir(p.PerformanceLogsDir) }

// GetReportingLogDir ports get_reporting_log_dir.
func (p *Paths) GetReportingLogDir() (string, error) { return logDir(p.ReportingLogsDir) }

// GetRoadmapLogDir ports get_roadmap_log_dir.
func (p *Paths) GetRoadmapLogDir() (string, error) { return logDir(p.RoadmapLogsDir) }

// GetMCPLogDir ports get_mcp_log_dir.
func (p *Paths) GetMCPLogDir() (string, error) { return logDir(p.MCPLogsDir) }

// GetAgentwatchLogDir ports get_agentwatch_log_dir.
func (p *Paths) GetAgentwatchLogDir() (string, error) { return logDir(p.AgentwatchLogsDir) }

// GetCeremoniesLogDir ports get_ceremonies_log_dir (its own directory: a
// scheduled run's log is the only trace of a fire nobody watched).
func (p *Paths) GetCeremoniesLogDir() (string, error) { return logDir(p.CeremoniesLogsDir) }

// GetShipLogDir ports get_ship_log_dir.
func (p *Paths) GetShipLogDir() (string, error) { return logDir(p.ShipLogsDir) }

// GetShipDir ports get_ship_dir: hardened like the sessions DB dir — the
// budget ledger decides whether an agent launch is allowed, so it must not be
// writable by another local user.
func (p *Paths) GetShipDir() (string, error) {
	if err := mkdirAll(p.ShipDir); err != nil {
		return "", err
	}
	restrictPermissions(p.ShipDir, 0o700)
	return p.ShipDir, nil
}

// GetBinDir ports get_bin_dir: app-managed helper binaries.
func (p *Paths) GetBinDir() (string, error) { return logDir(p.BinDir) }

// GetAttachmentsDir ports get_attachments_dir: the pasted-image directory for
// a session/project scope.
func (p *Paths) GetAttachmentsDir(scopeID string) (string, error) {
	d := joinPath(p.AttachmentsDir, SafeKey(scopeID, "misc"))
	if err := mkdirAll(d); err != nil {
		return "", err
	}
	return d, nil
}

// GetTranscriptsDir ports get_transcripts_dir: the managed standup-transcript
// drop folder (flat by design — files are matched to a standup by date).
func (p *Paths) GetTranscriptsDir() (string, error) { return logDir(p.TranscriptsDir) }
