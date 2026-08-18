// Golden-driven parity for the paths surface: replays every committed
// fixture under tests/parity/goldens/foundations/ (written by the Python
// dumper — see tests/parity/foundations/) against this package. The Python
// freeze test keeps those files honest against paths.py, so passing here is
// Python ↔ Go parity without a binary in the loop — the subprocess-vs-binary
// diff arms in W8 phase 3.
package home

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const goldensDir = "../../../tests/parity/goldens/foundations"

type golden struct {
	Env  map[string]string `json:"env"`
	Dump goldenDump        `json:"dump"`
}

type goldenDump struct {
	Constants map[string]string            `json:"constants"`
	Helpers   map[string]string            `json:"helpers"`
	Keyed     map[string]map[string]string `json:"keyed_helpers"`
	SafeKey   map[string]string            `json:"safe_key"`
}

// constantFields maps every Python constant name the dumper emits to its Go
// field — two-way: a golden key with no entry here fails, and vice versa.
var constantFields = map[string]func(*Paths) string{
	"DEFAULT_ROOT_DIR":        func(p *Paths) string { return p.DefaultRootDir },
	"ROOT_DIR":                func(p *Paths) string { return p.RootDir },
	"LEGACY_ROOT_DIR":         func(p *Paths) string { return p.LegacyRootDir },
	"DATA_DIR":                func(p *Paths) string { return p.DataDir },
	"DB_PATH":                 func(p *Paths) string { return p.DBPath },
	"STATES_DIR":              func(p *Paths) string { return p.StatesDir },
	"PROJECTS_FILE":           func(p *Paths) string { return p.ProjectsFile },
	"REPORTING_THEMES_FILE":   func(p *Paths) string { return p.ReportingThemesFile },
	"REPORTING_PREFS_FILE":    func(p *Paths) string { return p.ReportingPrefsFile },
	"VOICE_INSTALL_FILE":      func(p *Paths) string { return p.VoiceInstallFile },
	"LEGACY_DB_PATH":          func(p *Paths) string { return p.LegacyDBPath },
	"LEGACY_STATES_DIR":       func(p *Paths) string { return p.LegacyStatesDir },
	"LEGACY_PROJECTS_FILE":    func(p *Paths) string { return p.LegacyProjectsFile },
	"EXPORTS_DIR":             func(p *Paths) string { return p.ExportsDir },
	"ANALYSIS_EXPORTS_DIR":    func(p *Paths) string { return p.AnalysisExportsDir },
	"PLANNING_EXPORTS_DIR":    func(p *Paths) string { return p.PlanningExportsDir },
	"STANDUP_EXPORTS_DIR":     func(p *Paths) string { return p.StandupExportsDir },
	"RETRO_EXPORTS_DIR":       func(p *Paths) string { return p.RetroExportsDir },
	"POKER_EXPORTS_DIR":       func(p *Paths) string { return p.PokerExportsDir },
	"PERFORMANCE_EXPORTS_DIR": func(p *Paths) string { return p.PerformanceExportsDir },
	"REPORTING_EXPORTS_DIR":   func(p *Paths) string { return p.ReportingExportsDir },
	"ROADMAP_EXPORTS_DIR":     func(p *Paths) string { return p.RoadmapExportsDir },
	"ANONYMIZE_EXPORTS_DIR":   func(p *Paths) string { return p.AnonymizeExportsDir },
	"AGENTWATCH_EXPORTS_DIR":  func(p *Paths) string { return p.AgentwatchExportsDir },
	"SHIP_DIR":                func(p *Paths) string { return p.ShipDir },
	"SHIP_WORKTREES_DIR":      func(p *Paths) string { return p.ShipWorktreesDir },
	"SHIP_WORKTREE_REGISTRY":  func(p *Paths) string { return p.ShipWorktreeRegistry },
	"SHIP_BUDGET_FILE":        func(p *Paths) string { return p.ShipBudgetFile },
	"SHIP_BUDGET_LOCK":        func(p *Paths) string { return p.ShipBudgetLock },
	"SHIP_BUDGET_RECEIPTS":    func(p *Paths) string { return p.ShipBudgetReceipts },
	"LOGS_DIR":                func(p *Paths) string { return p.LogsDir },
	"TUI_LOGS_DIR":            func(p *Paths) string { return p.TUILogsDir },
	"STANDUP_LOGS_DIR":        func(p *Paths) string { return p.StandupLogsDir },
	"RETRO_LOGS_DIR":          func(p *Paths) string { return p.RetroLogsDir },
	"POKER_LOGS_DIR":          func(p *Paths) string { return p.PokerLogsDir },
	"PERFORMANCE_LOGS_DIR":    func(p *Paths) string { return p.PerformanceLogsDir },
	"REPORTING_LOGS_DIR":      func(p *Paths) string { return p.ReportingLogsDir },
	"ROADMAP_LOGS_DIR":        func(p *Paths) string { return p.RoadmapLogsDir },
	"ANALYSIS_LOGS_DIR":       func(p *Paths) string { return p.AnalysisLogsDir },
	"PLANNING_LOGS_DIR":       func(p *Paths) string { return p.PlanningLogsDir },
	"MCP_LOGS_DIR":            func(p *Paths) string { return p.MCPLogsDir },
	"AGENTWATCH_LOGS_DIR":     func(p *Paths) string { return p.AgentwatchLogsDir },
	"SHIP_LOGS_DIR":           func(p *Paths) string { return p.ShipLogsDir },
	"CEREMONIES_LOGS_DIR":     func(p *Paths) string { return p.CeremoniesLogsDir },
	"LEGACY_TUI_LOG":          func(p *Paths) string { return p.LegacyTUILog },
	"SCRUM_DOCS_DIR":          func(p *Paths) string { return p.ScrumDocsDir },
	"ENV_FILE":                func(p *Paths) string { return p.EnvFile },
	"REPL_HISTORY":            func(p *Paths) string { return p.ReplHistory },
	"BIN_DIR":                 func(p *Paths) string { return p.BinDir },
	"ATTACHMENTS_DIR":         func(p *Paths) string { return p.AttachmentsDir },
	"TRANSCRIPTS_DIR":         func(p *Paths) string { return p.TranscriptsDir },
}

// helperFuncs maps the dumper's zero-argument helpers to their Go twins.
var helperFuncs = map[string]func(*Paths) (string, error){
	"get_db_path":               (*Paths).GetDBPath,
	"get_reporting_themes_path": (*Paths).GetReportingThemesPath,
	"get_reporting_prefs_path":  (*Paths).GetReportingPrefsPath,
	"get_voice_install_path":    (*Paths).GetVoiceInstallPath,
	"get_tui_log_path":          (*Paths).GetTUILogPath,
	"get_analysis_log_dir":      (*Paths).GetAnalysisLogDir,
	"get_planning_log_dir":      (*Paths).GetPlanningLogDir,
	"get_standup_log_dir":       (*Paths).GetStandupLogDir,
	"get_retro_log_dir":         (*Paths).GetRetroLogDir,
	"get_poker_log_dir":         (*Paths).GetPokerLogDir,
	"get_performance_log_dir":   (*Paths).GetPerformanceLogDir,
	"get_reporting_log_dir":     (*Paths).GetReportingLogDir,
	"get_roadmap_log_dir":       (*Paths).GetRoadmapLogDir,
	"get_mcp_log_dir":           (*Paths).GetMCPLogDir,
	"get_agentwatch_log_dir":    (*Paths).GetAgentwatchLogDir,
	"get_ceremonies_log_dir":    (*Paths).GetCeremoniesLogDir,
	"get_ship_log_dir":          (*Paths).GetShipLogDir,
	"get_ship_dir":              (*Paths).GetShipDir,
	"get_bin_dir":               (*Paths).GetBinDir,
	"get_transcripts_dir":       (*Paths).GetTranscriptsDir,
}

// keyedFuncs maps the dumper's key-taking helpers to their Go twins.
var keyedFuncs = map[string]func(*Paths, string) (string, error){
	"get_analysis_export_dir":    (*Paths).GetAnalysisExportDir,
	"get_planning_export_dir":    (*Paths).GetPlanningExportDir,
	"get_standup_export_dir":     (*Paths).GetStandupExportDir,
	"get_retro_export_dir":       (*Paths).GetRetroExportDir,
	"get_poker_export_dir":       (*Paths).GetPokerExportDir,
	"get_performance_export_dir": (*Paths).GetPerformanceExportDir,
	"get_reporting_export_dir":   (*Paths).GetReportingExportDir,
	"get_roadmap_export_dir":     (*Paths).GetRoadmapExportDir,
	"get_anonymize_export_dir":   (*Paths).GetAnonymizeExportDir,
	"get_agentwatch_export_dir":  (*Paths).GetAgentwatchExportDir,
	"get_attachments_dir":        (*Paths).GetAttachmentsDir,
}

// chdir pins the working directory for the relative-root fixtures, matching
// the Python dumper's cwd=sandbox. (go1.22 — testing.T.Chdir arrives later.)
func chdir(t *testing.T, dir string) {
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

func TestFoundationsGoldenParity(t *testing.T) {
	files, err := filepath.Glob(filepath.Join(goldensDir, "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(files) == 0 {
		t.Fatalf("no goldens under %s — run `uv run python -m tests.parity.foundations.regen`", goldensDir)
	}
	for _, file := range files {
		name := strings.TrimSuffix(filepath.Base(file), ".json")
		t.Run(name, func(t *testing.T) {
			raw, err := os.ReadFile(file)
			if err != nil {
				t.Fatal(err)
			}
			var g golden
			if err := json.Unmarshal(raw, &g); err != nil {
				t.Fatal(err)
			}
			tmp := t.TempDir()
			chdir(t, tmp)
			fill := func(s string) string { return strings.ReplaceAll(s, "{tmp}", tmp) }
			env := func(key string) (string, bool) {
				v, ok := g.Env[key]
				if !ok {
					return "", false
				}
				return fill(v), true
			}
			p, err := Resolve(env)
			if err != nil {
				t.Fatalf("Resolve: %v", err)
			}

			for name, want := range g.Dump.Constants {
				getter, ok := constantFields[name]
				if !ok {
					t.Errorf("constant %s has no Go twin in constantFields", name)
					continue
				}
				if got := getter(p); got != fill(want) {
					t.Errorf("%s = %q, want %q", name, got, fill(want))
				}
			}
			for name := range constantFields {
				if _, ok := g.Dump.Constants[name]; !ok {
					t.Errorf("constantFields has %s but the golden does not — dump.py dropped it?", name)
				}
			}

			for name, want := range g.Dump.Helpers {
				fn, ok := helperFuncs[name]
				if !ok {
					t.Errorf("helper %s has no Go twin in helperFuncs", name)
					continue
				}
				got, err := fn(p)
				if err != nil {
					t.Errorf("%s: %v", name, err)
					continue
				}
				if got != fill(want) {
					t.Errorf("%s() = %q, want %q", name, got, fill(want))
				}
			}
			for name := range helperFuncs {
				if _, ok := g.Dump.Helpers[name]; !ok {
					t.Errorf("helperFuncs has %s but the golden does not — dump.py dropped it?", name)
				}
			}

			for name, cases := range g.Dump.Keyed {
				fn, ok := keyedFuncs[name]
				if !ok {
					t.Errorf("keyed helper %s has no Go twin in keyedFuncs", name)
					continue
				}
				for key, want := range cases {
					got, err := fn(p, key)
					if err != nil {
						t.Errorf("%s(%q): %v", name, key, err)
						continue
					}
					if got != fill(want) {
						t.Errorf("%s(%q) = %q, want %q", name, key, got, fill(want))
					}
				}
			}
			for name := range keyedFuncs {
				if _, ok := g.Dump.Keyed[name]; !ok {
					t.Errorf("keyedFuncs has %s but the golden does not — dump.py dropped it?", name)
				}
			}

			for key, want := range g.Dump.SafeKey {
				if got := SafeKey(key, "fb"); got != want {
					t.Errorf("SafeKey(%q) = %q, want %q", key, got, want)
				}
			}
		})
	}
}
