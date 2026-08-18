// Golden-driven parity for the config surface: replays every committed
// fixture under tests/parity/goldens/foundations/ (written by the Python
// dumper — see tests/parity/foundations/) against this package. The Python
// freeze test keeps those files honest against config.py, so passing here
// is Python ↔ Go parity without a binary in the loop — the
// subprocess-vs-binary diff arms in W8 phase 3.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/dotenv"
	"github.com/yeaboi-ai/yeaboi/go/internal/home"
)

const goldensDir = "../../../tests/parity/goldens/foundations"

type golden struct {
	Env   map[string]string `json:"env"`
	Files map[string]string `json:"files"`
	Dump  goldenDump        `json:"dump"`
}

type goldenDump struct {
	Config      map[string]any            `json:"config"`
	ConfigKeyed map[string]map[string]any `json:"config_keyed"`
	SetKey      map[string]setKeyScenario `json:"set_key"`
}

type setKeyScenario struct {
	Initial *string    `json:"initial"`
	Ops     [][]string `json:"ops"`
	Text    string     `json:"text"`
	Mode    string     `json:"mode"`
}

// configFields maps every getter name the dumper emits to its Go twin —
// two-way: a golden key with no entry here fails, and vice versa. Values
// are canonicalised through JSON, so typed Go results and the golden's
// decoded floats/nils compare cleanly.
var configFields = map[string]func(*Config) any{
	"get_config_dir":                                func(c *Config) any { return errToNil(c.GetConfigDir()) },
	"get_config_file":                               func(c *Config) any { return errToNil(c.GetConfigFile()) },
	"get_sessions_db":                               func(c *Config) any { return errToNil(c.GetSessionsDB()) },
	"get_anthropic_api_key":                         func(c *Config) any { return errToNil(c.GetAnthropicAPIKey()) },
	"is_langsmith_enabled":                          func(c *Config) any { return c.IsLangsmithEnabled() },
	"is_tips_enabled":                               func(c *Config) any { return c.IsTipsEnabled() },
	"is_beta_notice_enabled":                        func(c *Config) any { return c.IsBetaNoticeEnabled() },
	"get_last_category":                             func(c *Config) any { return c.GetLastCategory() },
	"is_duck_enabled":                               func(c *Config) any { return c.IsDuckEnabled() },
	"is_music_enabled":                              func(c *Config) any { return c.IsMusicEnabled() },
	"get_music_channel":                             func(c *Config) any { return c.GetMusicChannel() },
	"beta_notices_acked":                            func(c *Config) any { return c.BetaNoticesAcked() },
	"is_voice_install_offer_enabled":                func(c *Config) any { return c.IsVoiceInstallOfferEnabled() },
	"voice_extra_was_installed":                     func(c *Config) any { return c.VoiceExtraWasInstalled() },
	"detect_proxy":                                  func(c *Config) any { return c.DetectProxy() },
	"get_github_token":                              func(c *Config) any { return c.GetGithubToken() },
	"get_azure_devops_token":                        func(c *Config) any { return c.GetAzureDevopsToken() },
	"get_azure_devops_org_url":                      func(c *Config) any { return c.GetAzureDevopsOrgURL() },
	"get_azure_devops_project":                      func(c *Config) any { return c.GetAzureDevopsProject() },
	"get_azure_devops_team":                         func(c *Config) any { return c.GetAzureDevopsTeam() },
	"get_jira_base_url":                             func(c *Config) any { return c.GetJiraBaseURL() },
	"get_jira_email":                                func(c *Config) any { return c.GetJiraEmail() },
	"get_jira_token":                                func(c *Config) any { return c.GetJiraToken() },
	"get_jira_project_key":                          func(c *Config) any { return c.GetJiraProjectKey() },
	"get_ac_format":                                 func(c *Config) any { return c.GetACFormat() },
	"get_confluence_base_url":                       func(c *Config) any { return c.GetConfluenceBaseURL() },
	"get_confluence_email":                          func(c *Config) any { return c.GetConfluenceEmail() },
	"get_confluence_token":                          func(c *Config) any { return c.GetConfluenceToken() },
	"get_confluence_space_key":                      func(c *Config) any { return c.GetConfluenceSpaceKey() },
	"get_anonymize_mask_terms":                      func(c *Config) any { return c.GetAnonymizeMaskTerms() },
	"get_notion_token":                              func(c *Config) any { return c.GetNotionToken() },
	"get_notion_root_page_id":                       func(c *Config) any { return c.GetNotionRootPageID() },
	"get_data_dir":                                  func(c *Config) any { return c.GetDataDir() },
	"get_allowed_paths":                             func(c *Config) any { return c.GetAllowedPaths() },
	"get_notion_export_parent_page_id":              func(c *Config) any { return c.GetNotionExportParentPageID() },
	"get_confluence_export_parent_page_id":          func(c *Config) any { return c.GetConfluenceExportParentPageID() },
	"get_standup_github_repo":                       func(c *Config) any { return c.GetStandupGithubRepo() },
	"get_team_analysis_github_owners":               func(c *Config) any { return c.GetTeamAnalysisGithubOwners() },
	"get_team_analysis_azdo_projects":               func(c *Config) any { return c.GetTeamAnalysisAzdoProjects() },
	"get_team_analysis_confluence_spaces":           func(c *Config) any { return c.GetTeamAnalysisConfluenceSpaces() },
	"get_team_analysis_notion_roots":                func(c *Config) any { return c.GetTeamAnalysisNotionRoots() },
	"get_team_analysis_enrichment_timeout_seconds":  func(c *Config) any { return c.GetTeamAnalysisEnrichmentTimeoutSeconds() },
	"get_team_analysis_fast_model":                  func(c *Config) any { return c.GetTeamAnalysisFastModel() },
	"get_team_analysis_llm_target_seconds":          func(c *Config) any { return c.GetTeamAnalysisLLMTargetSeconds() },
	"get_team_analysis_llm_max_concurrency":         func(c *Config) any { return c.GetTeamAnalysisLLMMaxConcurrency() },
	"get_team_analysis_doc_request_timeout_seconds": func(c *Config) any { return c.GetTeamAnalysisDocRequestTimeoutSeconds() },
	"get_team_analysis_doc_max_concurrency":         func(c *Config) any { return c.GetTeamAnalysisDocMaxConcurrency() },
	"get_team_analysis_code_max_concurrency":        func(c *Config) any { return c.GetTeamAnalysisCodeMaxConcurrency() },
	"get_team_analysis_tracker_max_concurrency":     func(c *Config) any { return c.GetTeamAnalysisTrackerMaxConcurrency() },
	"get_team_analysis_max_change_lookups":          func(c *Config) any { return c.GetTeamAnalysisMaxChangeLookups() },
	"get_retro_server_port":                         func(c *Config) any { return c.GetRetroServerPort() },
	"get_poker_server_port":                         func(c *Config) any { return c.GetPokerServerPort() },
	"get_ship_server_port":                          func(c *Config) any { return c.GetShipServerPort() },
	"get_ship_board_enabled":                        func(c *Config) any { return c.GetShipBoardEnabled() },
	"tunnels_disabled":                              func(c *Config) any { return c.TunnelsDisabled() },
	"get_tunnel_timeout_minutes":                    func(c *Config) any { return c.GetTunnelTimeoutMinutes() },
	"get_slack_webhook_url":                         func(c *Config) any { return c.GetSlackWebhookURL() },
	"get_smtp_host":                                 func(c *Config) any { return c.GetSmtpHost() },
	"get_smtp_port":                                 func(c *Config) any { return c.GetSmtpPort() },
	"get_smtp_user":                                 func(c *Config) any { return c.GetSmtpUser() },
	"get_smtp_password":                             func(c *Config) any { return c.GetSmtpPassword() },
	"get_smtp_sender":                               func(c *Config) any { return c.GetSmtpSender() },
	"get_standup_email_recipients":                  func(c *Config) any { return c.GetStandupEmailRecipients() },
	"get_standup_user_name":                         func(c *Config) any { return c.GetStandupUserName() },
	"get_performance_framework_path":                func(c *Config) any { return c.GetPerformanceFrameworkPath() },
	"get_llm_provider":                              func(c *Config) any { return c.GetLLMProvider() },
	"get_llm_model":                                 func(c *Config) any { return c.GetLLMModel() },
	"get_bedrock_region":                            func(c *Config) any { return c.GetBedrockRegion() },
	"get_aws_profile":                               func(c *Config) any { return c.GetAWSProfile() },
	"get_openai_api_key":                            func(c *Config) any { return c.GetOpenaiAPIKey() },
	"get_google_api_key":                            func(c *Config) any { return c.GetGoogleAPIKey() },
	"get_ollama_base_url":                           func(c *Config) any { return c.GetOllamaBaseURL() },
	"get_ollama_num_ctx":                            func(c *Config) any { return c.GetOllamaNumCtx() },
	"is_llm_configured": func(c *Config) any {
		ok, msg := c.IsLLMConfigured()
		return []any{ok, msg}
	},
	"get_voice_model":                            func(c *Config) any { return c.GetVoiceModel() },
	"get_voice_device":                           func(c *Config) any { return c.GetVoiceDevice() },
	"get_session_prune_days":                     func(c *Config) any { return c.GetSessionPruneDays() },
	"get_log_level":                              func(c *Config) any { return c.GetLogLevel() },
	"is_team_analysis_jira_dev_links_enabled":    func(c *Config) any { return c.IsTeamAnalysisJiraDevLinksEnabled() },
	"is_team_analysis_azdo_pr_search_enabled":    func(c *Config) any { return c.IsTeamAnalysisAzdoPRSearchEnabled() },
	"get_team_analysis_azdo_pr_search_max_repos": func(c *Config) any { return c.GetTeamAnalysisAzdoPRSearchMaxRepos() },
	"get_team_analysis_azdo_pr_search_top":       func(c *Config) any { return c.GetTeamAnalysisAzdoPRSearchTop() },
	"get_team_analysis_azdo_repo_allowlist":      func(c *Config) any { return c.GetTeamAnalysisAzdoRepoAllowlist() },
}

// configKeyedFields maps the key-taking getters.
var configKeyedFields = map[string]func(*Config, string) any{
	"is_beta_notice_seen": func(c *Config, key string) any { return c.IsBetaNoticeSeen(key) },
}

// errToNil mirrors the dumper's OSError → null convention for the one
// getter that raises.
func errToNil(v string, err error) any {
	if err != nil {
		return nil
	}
	return v
}

// canonical round-trips a Go value through JSON so it compares cleanly
// against the golden's decoded shape (float64 numbers, nil, []any).
func canonical(t *testing.T, v any) any {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal %v: %v", v, err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("unmarshal %s: %v", raw, err)
	}
	return out
}

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

func TestConfigGoldenParity(t *testing.T) {
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
			// Materialise the fixture the way matrix.run_dump does: the
			// fixture files, then the realized HOME (config's mkdirs are
			// non-recursive).
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
				t.Fatalf("home.Resolve: %v", err)
			}
			c, err := Load(env, tmp, paths.EnvFile)
			if err != nil {
				t.Fatalf("Load: %v", err)
			}

			for name, want := range g.Dump.Config {
				getter, ok := configFields[name]
				if !ok {
					t.Errorf("config getter %s has no Go twin in configFields", name)
					continue
				}
				got := canonical(t, getter(c))
				if s, isString := want.(string); isString {
					want = fill(s)
				}
				if !reflect.DeepEqual(got, want) {
					t.Errorf("%s = %#v, want %#v", name, got, want)
				}
			}
			for name := range configFields {
				if _, ok := g.Dump.Config[name]; !ok {
					t.Errorf("configFields has %s but the golden does not — dump.py dropped it?", name)
				}
			}

			for name, cases := range g.Dump.ConfigKeyed {
				fn, ok := configKeyedFields[name]
				if !ok {
					t.Errorf("keyed config getter %s has no Go twin", name)
					continue
				}
				for key, want := range cases {
					if got := canonical(t, fn(c, key)); !reflect.DeepEqual(got, want) {
						t.Errorf("%s(%q) = %#v, want %#v", name, key, got, want)
					}
				}
			}
			for name := range configKeyedFields {
				if _, ok := g.Dump.ConfigKeyed[name]; !ok {
					t.Errorf("configKeyedFields has %s but the golden does not — dump.py dropped it?", name)
				}
			}

			runSetKeyScenarios(t, c, tmp, g.Dump.SetKey)
		})
	}
}

// runSetKeyScenarios replays the dumper's writer scenarios: scratch-file
// ones through dotenv.SetKey, the choke-point one through
// Config.SetConfigValue (which appends to the fixture's own user .env and
// re-locks it to 0600).
func runSetKeyScenarios(t *testing.T, c *Config, tmp string, scenarios map[string]setKeyScenario) {
	t.Helper()
	scratch := filepath.Join(tmp, "setkey-scratch-go")
	if err := os.MkdirAll(scratch, 0o755); err != nil {
		t.Fatal(err)
	}
	for name, sc := range scenarios {
		if name == "via-config-choke-point" {
			var configFile string
			var err error
			for _, op := range sc.Ops {
				if configFile, err = c.SetConfigValue(op[0], op[1]); err != nil {
					t.Errorf("SetConfigValue(%v): %v", op, err)
				}
			}
			data, err := os.ReadFile(configFile)
			if err != nil {
				t.Fatal(err)
			}
			if string(data) != sc.Text {
				t.Errorf("%s text = %q, want %q", name, data, sc.Text)
			}
			fi, err := os.Stat(configFile)
			if err != nil {
				t.Fatal(err)
			}
			if got := fmt.Sprintf("0o%o", fi.Mode().Perm()); got != sc.Mode {
				t.Errorf("%s mode = %s, want %s", name, got, sc.Mode)
			}
			continue
		}
		path := filepath.Join(scratch, name+".env")
		if sc.Initial != nil {
			if err := os.WriteFile(path, []byte(*sc.Initial), 0o600); err != nil {
				t.Fatal(err)
			}
		}
		for _, op := range sc.Ops {
			if err := dotenv.SetKey(path, op[0], op[1]); err != nil {
				t.Errorf("%s: SetKey(%v): %v", name, op, err)
			}
		}
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if string(data) != sc.Text {
			t.Errorf("%s text = %q, want %q", name, data, sc.Text)
		}
	}
}
