// Package foundations builds the __dump-foundations document for cmd/yeaboi.
//
// Python twin: tests/parity/foundations/dump.py build_dump() — the same
// constants, helpers, keyed helpers, safe_key vectors, config getters and
// set_key scenarios, over internal/home, internal/config and internal/dotenv,
// so `yeaboi __dump-foundations` under a fixture's environment reproduces
// the Python dump byte-for-byte (tests/parity/foundations/
// test_foundations_parity.py::test_go_binary_matches_python_dump is the
// gate). The vectors and scenario corpus are duplicated from dump.py on
// purpose: dump.py is the reference, and the subprocess gate is what keeps
// this copy honest — the same way the golden replays in internal/home and
// internal/config keep their own maps honest.
package foundations

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/yeaboi-ai/yeaboi/go/internal/config"
	"github.com/yeaboi-ai/yeaboi/go/internal/dotenv"
	"github.com/yeaboi-ai/yeaboi/go/internal/home"
)

// safeKeyVectors mirrors dump.SAFE_KEY_VECTORS.
var safeKeyVectors = []string{
	"Team/Alpha",
	"İstanbul CI",
	"  padded  ",
	"a\\b\\c",
	"../../../etc/passwd",
	"a//b",
	"",
	"...",
	"PROJ.1",
	".",
	"a/./b/../c",
	"🚀 Launch/Q3",
	" nbsp ",
	"MiXeD CASE",
	"trailing/",
}

// keyedSamples mirrors dump.KEYED_SAMPLES.
var keyedSamples = []string{"Team/Alpha", "İZMİR \\ Ops", ""}

// constantFields mirrors dump.CONSTANT_NAMES over home.Paths.
var constantFields = map[string]func(*home.Paths) string{
	"DEFAULT_ROOT_DIR":        func(p *home.Paths) string { return p.DefaultRootDir },
	"ROOT_DIR":                func(p *home.Paths) string { return p.RootDir },
	"LEGACY_ROOT_DIR":         func(p *home.Paths) string { return p.LegacyRootDir },
	"DATA_DIR":                func(p *home.Paths) string { return p.DataDir },
	"DB_PATH":                 func(p *home.Paths) string { return p.DBPath },
	"STATES_DIR":              func(p *home.Paths) string { return p.StatesDir },
	"PROJECTS_FILE":           func(p *home.Paths) string { return p.ProjectsFile },
	"REPORTING_THEMES_FILE":   func(p *home.Paths) string { return p.ReportingThemesFile },
	"REPORTING_PREFS_FILE":    func(p *home.Paths) string { return p.ReportingPrefsFile },
	"VOICE_INSTALL_FILE":      func(p *home.Paths) string { return p.VoiceInstallFile },
	"LEGACY_DB_PATH":          func(p *home.Paths) string { return p.LegacyDBPath },
	"LEGACY_STATES_DIR":       func(p *home.Paths) string { return p.LegacyStatesDir },
	"LEGACY_PROJECTS_FILE":    func(p *home.Paths) string { return p.LegacyProjectsFile },
	"EXPORTS_DIR":             func(p *home.Paths) string { return p.ExportsDir },
	"ANALYSIS_EXPORTS_DIR":    func(p *home.Paths) string { return p.AnalysisExportsDir },
	"PLANNING_EXPORTS_DIR":    func(p *home.Paths) string { return p.PlanningExportsDir },
	"STANDUP_EXPORTS_DIR":     func(p *home.Paths) string { return p.StandupExportsDir },
	"RETRO_EXPORTS_DIR":       func(p *home.Paths) string { return p.RetroExportsDir },
	"POKER_EXPORTS_DIR":       func(p *home.Paths) string { return p.PokerExportsDir },
	"PERFORMANCE_EXPORTS_DIR": func(p *home.Paths) string { return p.PerformanceExportsDir },
	"REPORTING_EXPORTS_DIR":   func(p *home.Paths) string { return p.ReportingExportsDir },
	"ROADMAP_EXPORTS_DIR":     func(p *home.Paths) string { return p.RoadmapExportsDir },
	"ANONYMIZE_EXPORTS_DIR":   func(p *home.Paths) string { return p.AnonymizeExportsDir },
	"AGENTWATCH_EXPORTS_DIR":  func(p *home.Paths) string { return p.AgentwatchExportsDir },
	"SHIP_DIR":                func(p *home.Paths) string { return p.ShipDir },
	"SHIP_WORKTREES_DIR":      func(p *home.Paths) string { return p.ShipWorktreesDir },
	"SHIP_WORKTREE_REGISTRY":  func(p *home.Paths) string { return p.ShipWorktreeRegistry },
	"SHIP_BUDGET_FILE":        func(p *home.Paths) string { return p.ShipBudgetFile },
	"SHIP_BUDGET_LOCK":        func(p *home.Paths) string { return p.ShipBudgetLock },
	"SHIP_BUDGET_RECEIPTS":    func(p *home.Paths) string { return p.ShipBudgetReceipts },
	"LOGS_DIR":                func(p *home.Paths) string { return p.LogsDir },
	"TUI_LOGS_DIR":            func(p *home.Paths) string { return p.TUILogsDir },
	"STANDUP_LOGS_DIR":        func(p *home.Paths) string { return p.StandupLogsDir },
	"RETRO_LOGS_DIR":          func(p *home.Paths) string { return p.RetroLogsDir },
	"POKER_LOGS_DIR":          func(p *home.Paths) string { return p.PokerLogsDir },
	"PERFORMANCE_LOGS_DIR":    func(p *home.Paths) string { return p.PerformanceLogsDir },
	"REPORTING_LOGS_DIR":      func(p *home.Paths) string { return p.ReportingLogsDir },
	"ROADMAP_LOGS_DIR":        func(p *home.Paths) string { return p.RoadmapLogsDir },
	"ANALYSIS_LOGS_DIR":       func(p *home.Paths) string { return p.AnalysisLogsDir },
	"PLANNING_LOGS_DIR":       func(p *home.Paths) string { return p.PlanningLogsDir },
	"MCP_LOGS_DIR":            func(p *home.Paths) string { return p.MCPLogsDir },
	"AGENTWATCH_LOGS_DIR":     func(p *home.Paths) string { return p.AgentwatchLogsDir },
	"SHIP_LOGS_DIR":           func(p *home.Paths) string { return p.ShipLogsDir },
	"CEREMONIES_LOGS_DIR":     func(p *home.Paths) string { return p.CeremoniesLogsDir },
	"LEGACY_TUI_LOG":          func(p *home.Paths) string { return p.LegacyTUILog },
	"SCRUM_DOCS_DIR":          func(p *home.Paths) string { return p.ScrumDocsDir },
	"ENV_FILE":                func(p *home.Paths) string { return p.EnvFile },
	"REPL_HISTORY":            func(p *home.Paths) string { return p.ReplHistory },
	"BIN_DIR":                 func(p *home.Paths) string { return p.BinDir },
	"ATTACHMENTS_DIR":         func(p *home.Paths) string { return p.AttachmentsDir },
	"TRANSCRIPTS_DIR":         func(p *home.Paths) string { return p.TranscriptsDir },
}

// helperFuncs mirrors dump.ZERO_ARG_HELPERS.
var helperFuncs = map[string]func(*home.Paths) (string, error){
	"get_db_path":               (*home.Paths).GetDBPath,
	"get_reporting_themes_path": (*home.Paths).GetReportingThemesPath,
	"get_reporting_prefs_path":  (*home.Paths).GetReportingPrefsPath,
	"get_voice_install_path":    (*home.Paths).GetVoiceInstallPath,
	"get_tui_log_path":          (*home.Paths).GetTUILogPath,
	"get_analysis_log_dir":      (*home.Paths).GetAnalysisLogDir,
	"get_planning_log_dir":      (*home.Paths).GetPlanningLogDir,
	"get_standup_log_dir":       (*home.Paths).GetStandupLogDir,
	"get_retro_log_dir":         (*home.Paths).GetRetroLogDir,
	"get_poker_log_dir":         (*home.Paths).GetPokerLogDir,
	"get_performance_log_dir":   (*home.Paths).GetPerformanceLogDir,
	"get_reporting_log_dir":     (*home.Paths).GetReportingLogDir,
	"get_roadmap_log_dir":       (*home.Paths).GetRoadmapLogDir,
	"get_mcp_log_dir":           (*home.Paths).GetMCPLogDir,
	"get_agentwatch_log_dir":    (*home.Paths).GetAgentwatchLogDir,
	"get_ceremonies_log_dir":    (*home.Paths).GetCeremoniesLogDir,
	"get_ship_log_dir":          (*home.Paths).GetShipLogDir,
	"get_ship_dir":              (*home.Paths).GetShipDir,
	"get_bin_dir":               (*home.Paths).GetBinDir,
	"get_transcripts_dir":       (*home.Paths).GetTranscriptsDir,
}

// keyedFuncs mirrors dump.KEYED_HELPERS.
var keyedFuncs = map[string]func(*home.Paths, string) (string, error){
	"get_analysis_export_dir":    (*home.Paths).GetAnalysisExportDir,
	"get_planning_export_dir":    (*home.Paths).GetPlanningExportDir,
	"get_standup_export_dir":     (*home.Paths).GetStandupExportDir,
	"get_retro_export_dir":       (*home.Paths).GetRetroExportDir,
	"get_poker_export_dir":       (*home.Paths).GetPokerExportDir,
	"get_performance_export_dir": (*home.Paths).GetPerformanceExportDir,
	"get_reporting_export_dir":   (*home.Paths).GetReportingExportDir,
	"get_roadmap_export_dir":     (*home.Paths).GetRoadmapExportDir,
	"get_anonymize_export_dir":   (*home.Paths).GetAnonymizeExportDir,
	"get_agentwatch_export_dir":  (*home.Paths).GetAgentwatchExportDir,
	"get_attachments_dir":        (*home.Paths).GetAttachmentsDir,
}

// errToNil mirrors the dumper's OSError → null convention.
func errToNil(v string, err error) any {
	if err != nil {
		return nil
	}
	return v
}

// configFields mirrors dump.CONFIG_GETTERS.
var configFields = map[string]func(*config.Config) any{
	"get_config_dir":                                func(c *config.Config) any { return errToNil(c.GetConfigDir()) },
	"get_config_file":                               func(c *config.Config) any { return errToNil(c.GetConfigFile()) },
	"get_sessions_db":                               func(c *config.Config) any { return errToNil(c.GetSessionsDB()) },
	"get_anthropic_api_key":                         func(c *config.Config) any { return errToNil(c.GetAnthropicAPIKey()) },
	"is_langsmith_enabled":                          func(c *config.Config) any { return c.IsLangsmithEnabled() },
	"is_tips_enabled":                               func(c *config.Config) any { return c.IsTipsEnabled() },
	"is_beta_notice_enabled":                        func(c *config.Config) any { return c.IsBetaNoticeEnabled() },
	"get_last_category":                             func(c *config.Config) any { return c.GetLastCategory() },
	"is_duck_enabled":                               func(c *config.Config) any { return c.IsDuckEnabled() },
	"is_music_enabled":                              func(c *config.Config) any { return c.IsMusicEnabled() },
	"get_music_channel":                             func(c *config.Config) any { return c.GetMusicChannel() },
	"beta_notices_acked":                            func(c *config.Config) any { return c.BetaNoticesAcked() },
	"is_voice_install_offer_enabled":                func(c *config.Config) any { return c.IsVoiceInstallOfferEnabled() },
	"voice_extra_was_installed":                     func(c *config.Config) any { return c.VoiceExtraWasInstalled() },
	"detect_proxy":                                  func(c *config.Config) any { return c.DetectProxy() },
	"get_github_token":                              func(c *config.Config) any { return c.GetGithubToken() },
	"get_azure_devops_token":                        func(c *config.Config) any { return c.GetAzureDevopsToken() },
	"get_azure_devops_org_url":                      func(c *config.Config) any { return c.GetAzureDevopsOrgURL() },
	"get_azure_devops_project":                      func(c *config.Config) any { return c.GetAzureDevopsProject() },
	"get_azure_devops_team":                         func(c *config.Config) any { return c.GetAzureDevopsTeam() },
	"get_jira_base_url":                             func(c *config.Config) any { return c.GetJiraBaseURL() },
	"get_jira_email":                                func(c *config.Config) any { return c.GetJiraEmail() },
	"get_jira_token":                                func(c *config.Config) any { return c.GetJiraToken() },
	"get_jira_project_key":                          func(c *config.Config) any { return c.GetJiraProjectKey() },
	"get_ac_format":                                 func(c *config.Config) any { return c.GetACFormat() },
	"get_confluence_base_url":                       func(c *config.Config) any { return c.GetConfluenceBaseURL() },
	"get_confluence_email":                          func(c *config.Config) any { return c.GetConfluenceEmail() },
	"get_confluence_token":                          func(c *config.Config) any { return c.GetConfluenceToken() },
	"get_confluence_space_key":                      func(c *config.Config) any { return c.GetConfluenceSpaceKey() },
	"get_anonymize_mask_terms":                      func(c *config.Config) any { return c.GetAnonymizeMaskTerms() },
	"get_notion_token":                              func(c *config.Config) any { return c.GetNotionToken() },
	"get_notion_root_page_id":                       func(c *config.Config) any { return c.GetNotionRootPageID() },
	"get_data_dir":                                  func(c *config.Config) any { return c.GetDataDir() },
	"get_allowed_paths":                             func(c *config.Config) any { return c.GetAllowedPaths() },
	"get_notion_export_parent_page_id":              func(c *config.Config) any { return c.GetNotionExportParentPageID() },
	"get_confluence_export_parent_page_id":          func(c *config.Config) any { return c.GetConfluenceExportParentPageID() },
	"get_standup_github_repo":                       func(c *config.Config) any { return c.GetStandupGithubRepo() },
	"get_team_analysis_github_owners":               func(c *config.Config) any { return c.GetTeamAnalysisGithubOwners() },
	"get_team_analysis_azdo_projects":               func(c *config.Config) any { return c.GetTeamAnalysisAzdoProjects() },
	"get_team_analysis_confluence_spaces":           func(c *config.Config) any { return c.GetTeamAnalysisConfluenceSpaces() },
	"get_team_analysis_notion_roots":                func(c *config.Config) any { return c.GetTeamAnalysisNotionRoots() },
	"get_team_analysis_enrichment_timeout_seconds":  func(c *config.Config) any { return c.GetTeamAnalysisEnrichmentTimeoutSeconds() },
	"get_team_analysis_fast_model":                  func(c *config.Config) any { return c.GetTeamAnalysisFastModel() },
	"get_team_analysis_llm_target_seconds":          func(c *config.Config) any { return c.GetTeamAnalysisLLMTargetSeconds() },
	"get_team_analysis_llm_max_concurrency":         func(c *config.Config) any { return c.GetTeamAnalysisLLMMaxConcurrency() },
	"get_team_analysis_doc_request_timeout_seconds": func(c *config.Config) any { return c.GetTeamAnalysisDocRequestTimeoutSeconds() },
	"get_team_analysis_doc_max_concurrency":         func(c *config.Config) any { return c.GetTeamAnalysisDocMaxConcurrency() },
	"get_team_analysis_code_max_concurrency":        func(c *config.Config) any { return c.GetTeamAnalysisCodeMaxConcurrency() },
	"get_team_analysis_tracker_max_concurrency":     func(c *config.Config) any { return c.GetTeamAnalysisTrackerMaxConcurrency() },
	"get_team_analysis_max_change_lookups":          func(c *config.Config) any { return c.GetTeamAnalysisMaxChangeLookups() },
	"get_retro_server_port":                         func(c *config.Config) any { return c.GetRetroServerPort() },
	"get_poker_server_port":                         func(c *config.Config) any { return c.GetPokerServerPort() },
	"tunnels_disabled":                              func(c *config.Config) any { return c.TunnelsDisabled() },
	"get_tunnel_timeout_minutes":                    func(c *config.Config) any { return c.GetTunnelTimeoutMinutes() },
	"get_slack_webhook_url":                         func(c *config.Config) any { return c.GetSlackWebhookURL() },
	"get_smtp_host":                                 func(c *config.Config) any { return c.GetSmtpHost() },
	"get_smtp_port":                                 func(c *config.Config) any { return c.GetSmtpPort() },
	"get_smtp_user":                                 func(c *config.Config) any { return c.GetSmtpUser() },
	"get_smtp_password":                             func(c *config.Config) any { return c.GetSmtpPassword() },
	"get_smtp_sender":                               func(c *config.Config) any { return c.GetSmtpSender() },
	"get_standup_email_recipients":                  func(c *config.Config) any { return c.GetStandupEmailRecipients() },
	"get_standup_user_name":                         func(c *config.Config) any { return c.GetStandupUserName() },
	"get_performance_framework_path":                func(c *config.Config) any { return c.GetPerformanceFrameworkPath() },
	"get_llm_provider":                              func(c *config.Config) any { return c.GetLLMProvider() },
	"get_llm_model":                                 func(c *config.Config) any { return c.GetLLMModel() },
	"get_bedrock_region":                            func(c *config.Config) any { return c.GetBedrockRegion() },
	"get_aws_profile":                               func(c *config.Config) any { return c.GetAWSProfile() },
	"get_openai_api_key":                            func(c *config.Config) any { return c.GetOpenaiAPIKey() },
	"get_google_api_key":                            func(c *config.Config) any { return c.GetGoogleAPIKey() },
	"get_ollama_base_url":                           func(c *config.Config) any { return c.GetOllamaBaseURL() },
	"get_ollama_num_ctx":                            func(c *config.Config) any { return c.GetOllamaNumCtx() },
	"is_llm_configured": func(c *config.Config) any {
		ok, msg := c.IsLLMConfigured()
		return []any{ok, msg}
	},
	"get_voice_model":                            func(c *config.Config) any { return c.GetVoiceModel() },
	"get_voice_device":                           func(c *config.Config) any { return c.GetVoiceDevice() },
	"get_session_prune_days":                     func(c *config.Config) any { return c.GetSessionPruneDays() },
	"get_log_level":                              func(c *config.Config) any { return c.GetLogLevel() },
	"is_team_analysis_jira_dev_links_enabled":    func(c *config.Config) any { return c.IsTeamAnalysisJiraDevLinksEnabled() },
	"is_team_analysis_azdo_pr_search_enabled":    func(c *config.Config) any { return c.IsTeamAnalysisAzdoPRSearchEnabled() },
	"get_team_analysis_azdo_pr_search_max_repos": func(c *config.Config) any { return c.GetTeamAnalysisAzdoPRSearchMaxRepos() },
	"get_team_analysis_azdo_pr_search_top":       func(c *config.Config) any { return c.GetTeamAnalysisAzdoPRSearchTop() },
	"get_team_analysis_azdo_repo_allowlist":      func(c *config.Config) any { return c.GetTeamAnalysisAzdoRepoAllowlist() },
}

// configKeyedFields mirrors dump.CONFIG_KEYED_GETTERS.
var configKeyedFields = map[string]struct {
	keys []string
	fn   func(*config.Config, string) any
}{
	"is_beta_notice_seen": {
		keys: []string{"retro", "poker", "roadmap", ""},
		fn:   func(c *config.Config, key string) any { return c.IsBetaNoticeSeen(key) },
	},
}

// setKeyScenario mirrors one dump.SET_KEY_SCENARIOS entry.
type setKeyScenario struct {
	name    string
	initial *string
	ops     [][2]string
}

func strPtr(s string) *string { return &s }

var setKeyScenarios = []setKeyScenario{
	{name: "create-missing", initial: nil, ops: [][2]string{{"FOO", "bar"}}},
	{
		name:    "replace-preserving",
		initial: strPtr("# comment\nFOO=old\nOTHER=1\nexport FOO=older\n=junk\nBARE\n"),
		ops:     [][2]string{{"FOO", "new"}},
	},
	{name: "append-after-newline-less-tail", initial: strPtr("A=1"), ops: [][2]string{{"B", "2"}}},
	{
		name:    "quote-escaping",
		initial: nil,
		ops: [][2]string{
			{"Q", "it's got 'quotes'"},
			{"NL", "line1\nline2"},
			{"UNI", "café 🚀"},
			{"EMPTY", ""},
		},
	},
	{name: "replace-then-append", initial: strPtr("KEEP='x'\n"), ops: [][2]string{{"KEEP", "y"}, {"NEW", "z"}}},
}

// Dump mirrors dump.build_dump(): resolve the paths surface from the
// process environment, run every helper (side effects included), then load
// the config layers and dump the config surface and the dotenv writer
// scenarios. cwd is the sandbox the process was launched in.
func Dump(env home.Env, cwd string) (map[string]any, error) {
	paths, err := home.Resolve(env)
	if err != nil {
		return nil, fmt.Errorf("resolve paths: %w", err)
	}

	constants := map[string]any{}
	for name, get := range constantFields {
		constants[name] = get(paths)
	}
	helpers := map[string]any{}
	for name, fn := range helperFuncs {
		v, err := fn(paths)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
		helpers[name] = v
	}
	keyed := map[string]any{}
	for name, fn := range keyedFuncs {
		byKey := map[string]any{}
		for _, key := range keyedSamples {
			v, err := fn(paths, key)
			if err != nil {
				return nil, fmt.Errorf("%s(%q): %w", name, key, err)
			}
			byKey[key] = v
		}
		keyed[name] = byKey
	}
	safeKey := map[string]any{}
	for _, v := range safeKeyVectors {
		safeKey[v] = home.SafeKey(v, "fb")
	}

	c, err := config.Load(env, cwd, paths.EnvFile)
	if err != nil {
		return nil, fmt.Errorf("load config: %w", err)
	}
	configDump := map[string]any{}
	for name, get := range configFields {
		configDump[name] = get(c)
	}
	configKeyed := map[string]any{}
	for name, entry := range configKeyedFields {
		byKey := map[string]any{}
		for _, key := range entry.keys {
			byKey[key] = entry.fn(c, key)
		}
		configKeyed[name] = byKey
	}

	setKey, err := dumpSetKey(c, cwd)
	if err != nil {
		return nil, err
	}

	logfileDump, err := dumpLogfile(paths, c)
	if err != nil {
		return nil, err
	}

	return map[string]any{
		"constants":     constants,
		"helpers":       helpers,
		"keyed_helpers": keyed,
		"safe_key":      safeKey,
		"config":        configDump,
		"config_keyed":  configKeyed,
		"set_key":       setKey,
		"logfile":       logfileDump,
	}, nil
}

// dumpSetKey mirrors dump._set_key_dump.
func dumpSetKey(c *config.Config, cwd string) (map[string]any, error) {
	scratch := filepath.Join(cwd, "setkey-scratch")
	if err := os.MkdirAll(scratch, 0o755); err != nil {
		return nil, err
	}
	out := map[string]any{}
	for _, sc := range setKeyScenarios {
		path := filepath.Join(scratch, sc.name+".env")
		if sc.initial != nil {
			if err := os.WriteFile(path, []byte(*sc.initial), 0o600); err != nil {
				return nil, err
			}
		}
		ops := make([]any, len(sc.ops))
		for i, op := range sc.ops {
			if err := dotenv.SetKey(path, op[0], op[1]); err != nil {
				return nil, fmt.Errorf("%s: SetKey(%v): %w", sc.name, op, err)
			}
			ops[i] = []any{op[0], op[1]}
		}
		text, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var initial any
		if sc.initial != nil {
			initial = *sc.initial
		}
		out[sc.name] = map[string]any{"initial": initial, "ops": ops, "text": string(text)}
	}

	ops := [][2]string{{"YEABOI_SET_KEY_PROBE", "via config"}}
	var configFile string
	var err error
	for _, op := range ops {
		if configFile, err = c.SetConfigValue(op[0], op[1]); err != nil {
			return nil, fmt.Errorf("SetConfigValue(%v): %w", op, err)
		}
	}
	text, err := os.ReadFile(configFile)
	if err != nil {
		return nil, err
	}
	fi, err := os.Stat(configFile)
	if err != nil {
		return nil, err
	}
	opsOut := make([]any, len(ops))
	for i, op := range ops {
		opsOut[i] = []any{op[0], op[1]}
	}
	out["via-config-choke-point"] = map[string]any{
		"ops":  opsOut,
		"text": string(text),
		"mode": fmt.Sprintf("0o%o", fi.Mode().Perm()&fs.ModePerm),
	}
	return out, nil
}
