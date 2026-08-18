// Port of src/yeaboi/paths.py (the ~/.yeaboi directory structure: root
// resolution, every path constant, _safe_key, the get_* dir/path helpers,
// move_data_tree, migrate_root_dir and migrate_legacy_paths) — keep in
// lockstep; the Python module is the reference implementation and
// tests/parity/foundations/ diffs whole-surface output against committed
// goldens.
//
// Python resolves the root once at import time; Go mirrors that as one
// Resolve() per process (the W8 spec pins this equivalence). The sqlite merge
// inside get_db_path is NOT ported — see the W9 marker in GetDBPath.
// paths.py's warning/debug logs ride internal/logfile when it lands (W8
// phase 5); until then failed chmods and best-effort moves stay silent here,
// which the gate never observes (it diffs paths, not logs).
package home

import (
	"os"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// Paths carries every constant paths.py defines at module level, resolved
// from one environment read, as normalised path strings.
type Paths struct {
	env Env

	// Root — DEFAULT_ROOT_DIR is the bootstrap home: ~/.yeaboi/.env is
	// always read from there, because it can itself set YEABOI_HOME.
	DefaultRootDir string // DEFAULT_ROOT_DIR
	RootDir        string // ROOT_DIR ($YEABOI_HOME when set, else ~/.yeaboi)
	LegacyRootDir  string // LEGACY_ROOT_DIR (pre-rebrand ~/.scrum-agent)

	// Data (DB, states, project metadata)
	DataDir             string // DATA_DIR
	DBPath              string // DB_PATH
	StatesDir           string // STATES_DIR
	ProjectsFile        string // PROJECTS_FILE
	ReportingThemesFile string // REPORTING_THEMES_FILE
	ReportingPrefsFile  string // REPORTING_PREFS_FILE
	VoiceInstallFile    string // VOICE_INSTALL_FILE
	LegacyDBPath        string // LEGACY_DB_PATH
	LegacyStatesDir     string // LEGACY_STATES_DIR
	LegacyProjectsFile  string // LEGACY_PROJECTS_FILE

	// Exports
	ExportsDir            string // EXPORTS_DIR
	AnalysisExportsDir    string // ANALYSIS_EXPORTS_DIR
	PlanningExportsDir    string // PLANNING_EXPORTS_DIR
	StandupExportsDir     string // STANDUP_EXPORTS_DIR
	RetroExportsDir       string // RETRO_EXPORTS_DIR
	PokerExportsDir       string // POKER_EXPORTS_DIR
	PerformanceExportsDir string // PERFORMANCE_EXPORTS_DIR
	ReportingExportsDir   string // REPORTING_EXPORTS_DIR
	RoadmapExportsDir     string // ROADMAP_EXPORTS_DIR
	AnonymizeExportsDir   string // ANONYMIZE_EXPORTS_DIR
	AgentwatchExportsDir  string // AGENTWATCH_EXPORTS_DIR

	// Ship (supervised coding-agent runs)
	ShipDir              string // SHIP_DIR
	ShipWorktreesDir     string // SHIP_WORKTREES_DIR
	ShipWorktreeRegistry string // SHIP_WORKTREE_REGISTRY
	ShipBudgetFile       string // SHIP_BUDGET_FILE
	ShipBudgetLock       string // SHIP_BUDGET_LOCK
	ShipBudgetReceipts   string // SHIP_BUDGET_RECEIPTS

	// Logs
	LogsDir            string // LOGS_DIR
	TUILogsDir         string // TUI_LOGS_DIR
	StandupLogsDir     string // STANDUP_LOGS_DIR
	RetroLogsDir       string // RETRO_LOGS_DIR
	PokerLogsDir       string // POKER_LOGS_DIR
	PerformanceLogsDir string // PERFORMANCE_LOGS_DIR
	ReportingLogsDir   string // REPORTING_LOGS_DIR
	RoadmapLogsDir     string // ROADMAP_LOGS_DIR
	AnalysisLogsDir    string // ANALYSIS_LOGS_DIR
	PlanningLogsDir    string // PLANNING_LOGS_DIR
	MCPLogsDir         string // MCP_LOGS_DIR
	AgentwatchLogsDir  string // AGENTWATCH_LOGS_DIR
	ShipLogsDir        string // SHIP_LOGS_DIR
	CeremoniesLogsDir  string // CEREMONIES_LOGS_DIR
	LegacyTUILog       string // LEGACY_TUI_LOG

	// Other
	ScrumDocsDir   string // SCRUM_DOCS_DIR
	EnvFile        string // ENV_FILE (pinned to DEFAULT_ROOT_DIR on purpose)
	ReplHistory    string // REPL_HISTORY
	BinDir         string // BIN_DIR
	AttachmentsDir string // ATTACHMENTS_DIR
	TranscriptsDir string // TRANSCRIPTS_DIR
}

// OSEnv is the production Env: the process environment.
func OSEnv(key string) (string, bool) { return os.LookupEnv(key) }

// HomeDir exposes Path.home() to sibling ports (config.py's live
// Path.home() reads in get_config_dir and the ~/.aws/config autodetect).
func HomeDir(env Env) (string, error) { return homeDir(env) }

// Join exposes the Path `/` operator to sibling ports.
func Join(base string, names ...string) string { return joinPath(base, names...) }

// resolveRoot ports _resolve_root: $YEABOI_HOME (stripped, expanduser-ed)
// when set, else the default home. str.strip() strips unicode whitespace,
// hence pysem.Strip.
func resolveRoot(env Env, defaultRoot string) (string, error) {
	raw, _ := env("YEABOI_HOME")
	raw = pysem.Strip(raw)
	if raw == "" {
		return defaultRoot, nil
	}
	return expandUser(normPath(raw), env)
}

// Resolve reads the environment once and derives every path constant, the way
// importing yeaboi.paths does. The error mirrors the RuntimeError pathlib
// raises when no home directory can be determined.
func Resolve(env Env) (*Paths, error) {
	home, err := homeDir(env)
	if err != nil {
		return nil, err
	}
	defaultRoot := joinPath(home, ".yeaboi")
	root, err := resolveRoot(env, defaultRoot)
	if err != nil {
		return nil, err
	}

	p := &Paths{
		env:            env,
		DefaultRootDir: defaultRoot,
		RootDir:        root,
		LegacyRootDir:  joinPath(home, ".scrum-agent"),
	}

	p.DataDir = joinPath(root, "data")
	p.DBPath = joinPath(p.DataDir, "sessions.db")
	p.StatesDir = joinPath(p.DataDir, "states")
	p.ProjectsFile = joinPath(p.DataDir, "projects.json")
	p.ReportingThemesFile = joinPath(p.DataDir, "reporting_themes.json")
	p.ReportingPrefsFile = joinPath(p.DataDir, "reporting_prefs.json")
	p.VoiceInstallFile = joinPath(p.DataDir, "voice_install.json")

	// Legacy paths (for backward compatibility / migration)
	p.LegacyDBPath = joinPath(root, "sessions.db")
	p.LegacyStatesDir = joinPath(root, "states")
	p.LegacyProjectsFile = joinPath(root, "projects.json")

	p.ExportsDir = joinPath(root, "exports")
	p.AnalysisExportsDir = joinPath(p.ExportsDir, "analysis")
	p.PlanningExportsDir = joinPath(p.ExportsDir, "planning")
	p.StandupExportsDir = joinPath(p.ExportsDir, "standup")
	p.RetroExportsDir = joinPath(p.ExportsDir, "retro")
	p.PokerExportsDir = joinPath(p.ExportsDir, "poker")
	p.PerformanceExportsDir = joinPath(p.ExportsDir, "performance")
	p.ReportingExportsDir = joinPath(p.ExportsDir, "reporting")
	p.RoadmapExportsDir = joinPath(p.ExportsDir, "roadmap")
	p.AnonymizeExportsDir = joinPath(p.ExportsDir, "anonymize")
	p.AgentwatchExportsDir = joinPath(p.ExportsDir, "agentwatch")

	p.ShipDir = joinPath(root, "ship")
	p.ShipWorktreesDir = joinPath(p.ShipDir, "worktrees")
	p.ShipWorktreeRegistry = joinPath(p.ShipDir, "worktrees.json")
	p.ShipBudgetFile = joinPath(p.ShipDir, "ai-budget.json")
	p.ShipBudgetLock = joinPath(p.ShipDir, "ai-budget.lock")
	p.ShipBudgetReceipts = joinPath(p.ShipDir, "ai-budget-receipts.jsonl")

	p.LogsDir = joinPath(root, "logs")
	p.TUILogsDir = joinPath(p.LogsDir, "tui")
	p.StandupLogsDir = joinPath(p.LogsDir, "standup")
	p.RetroLogsDir = joinPath(p.LogsDir, "retro")
	p.PokerLogsDir = joinPath(p.LogsDir, "poker")
	p.PerformanceLogsDir = joinPath(p.LogsDir, "performance")
	p.ReportingLogsDir = joinPath(p.LogsDir, "reporting")
	p.RoadmapLogsDir = joinPath(p.LogsDir, "roadmap")
	p.AnalysisLogsDir = joinPath(p.LogsDir, "analysis")
	p.PlanningLogsDir = joinPath(p.LogsDir, "planning")
	p.MCPLogsDir = joinPath(p.LogsDir, "mcp")
	p.AgentwatchLogsDir = joinPath(p.LogsDir, "agentwatch")
	p.ShipLogsDir = joinPath(p.LogsDir, "ship")
	p.CeremoniesLogsDir = joinPath(p.LogsDir, "ceremonies")
	p.LegacyTUILog = joinPath(root, "scrum-agent.log")

	p.ScrumDocsDir = joinPath(root, "scrum-docs")
	// Pinned to the default home on purpose — this file can set YEABOI_HOME,
	// so it can't live inside the directory it relocates.
	p.EnvFile = joinPath(defaultRoot, ".env")
	p.ReplHistory = joinPath(root, "repl-history")
	p.BinDir = joinPath(root, "bin")
	p.AttachmentsDir = joinPath(root, "attachments")
	p.TranscriptsDir = joinPath(root, "transcripts")

	return p, nil
}

// restrictPermissions ports config.restrict_permissions: best-effort chmod so
// on-disk secrets aren't group/other readable. Never fails the caller — a
// filesystem that rejects chmod must not break a path helper.
func restrictPermissions(path string, mode os.FileMode) {
	_ = os.Chmod(path, mode)
}
