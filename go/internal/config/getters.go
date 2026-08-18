// Port of src/yeaboi/config.py's getters — keep in lockstep, one Go method
// per Python function, in the Python file's order. The traps the W8 spec
// pins: config.py deliberately uses TWO truthy conventions (`!= "false"`
// opt-out gates vs `in {"1","true","yes","on"}` opt-in flags — never a
// shared parse-bool), clamps apply only after a successful int parse, CSV
// getters dedup while the email-recipient list does not, and the fallback
// chains (Confluence→Jira, AzDO team default, org-URL scheme normalisation,
// SMTP sender→user) live in the getters, not in the env.
package config

import (
	"sort"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// GetAnthropicAPIKey ports get_anthropic_api_key: the one getter that
// raises (OSError) instead of degrading when its key is missing.
func (c *Config) GetAnthropicAPIKey() (string, error) {
	if v, ok := c.env("ANTHROPIC_API_KEY"); ok && v != "" {
		return v, nil
	}
	return "", &pysem.Error{Class: "OSError", Msg: "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."}
}

// IsLangsmithEnabled ports is_langsmith_enabled.
func (c *Config) IsLangsmithEnabled() bool {
	tracing := pysem.Lower(c.getenv("LANGSMITH_TRACING", ""))
	key, _ := c.env("LANGSMITH_API_KEY")
	return tracing == "true" && key != ""
}

// stripLower is the `.strip().lower()` normalisation the opt-out gates share.
func stripLower(s string) string { return pysem.Lower(pysem.Strip(s)) }

// IsTipsEnabled ports is_tips_enabled: anything but "false" keeps tips on.
func (c *Config) IsTipsEnabled() bool {
	return stripLower(c.getenv("TIPS_ENABLED", "true")) != "false"
}

// IsBetaNoticeEnabled ports is_beta_notice_enabled.
func (c *Config) IsBetaNoticeEnabled() bool {
	return stripLower(c.getenv("BETA_NOTICES_ENABLED", "true")) != "false"
}

// GetLastCategory ports get_last_category: unknown values fall back to
// "humans" so a hand-edited .env can't wedge the landing screen.
func (c *Config) GetLastCategory() string {
	value := stripLower(c.getenv("YEABOI_LAST_CATEGORY", "humans"))
	if value == "humans" || value == "agents" {
		return value
	}
	return "humans"
}

// IsDuckEnabled ports is_duck_enabled.
func (c *Config) IsDuckEnabled() bool {
	return stripLower(c.getenv("DUCK_ENABLED", "true")) != "false"
}

// IsMusicEnabled ports is_music_enabled: default off, so this one is == "true".
func (c *Config) IsMusicEnabled() bool {
	return stripLower(c.getenv("MUSIC_ENABLED", "false")) == "true"
}

// GetMusicChannel ports get_music_channel.
func (c *Config) GetMusicChannel() int64 {
	v, err := pysem.ParseInt(pysem.Strip(c.getenv("MUSIC_CHANNEL", "0")))
	if err != nil {
		return 0
	}
	return v
}

// splitCommaSet mirrors `{part.strip() for part in raw.split(",") if part.strip()}`.
func splitCommaSet(raw string) map[string]bool {
	out := map[string]bool{}
	for _, part := range strings.Split(raw, ",") {
		if p := pysem.Strip(part); p != "" {
			out[p] = true
		}
	}
	return out
}

// BetaNoticesAcked ports beta_notices_acked. Python returns a set; the
// parity dump sorts it, so the sorted slice is the canonical shape here.
func (c *Config) BetaNoticesAcked() []string {
	acked := splitCommaSet(c.getenv("BETA_NOTICES_ACK", ""))
	out := make([]string, 0, len(acked))
	for k := range acked {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// IsBetaNoticeSeen ports is_beta_notice_seen, including the
// YEABOI_FORCE_BETA_NOTICE override (truthy = all modes, else a CSV of keys).
func (c *Config) IsBetaNoticeSeen(modeKey string) bool {
	forced := stripLower(c.getenv("YEABOI_FORCE_BETA_NOTICE", ""))
	if forced != "" {
		switch forced {
		case "1", "true", "yes", "on":
			return false
		}
		if splitCommaSet(forced)[modeKey] {
			return false
		}
	}
	return splitCommaSet(c.getenv("BETA_NOTICES_ACK", ""))[modeKey]
}

// IsVoiceInstallOfferEnabled ports is_voice_install_offer_enabled.
func (c *Config) IsVoiceInstallOfferEnabled() bool {
	switch stripLower(c.getenv("YEABOI_FORCE_VOICE_OFFER", "")) {
	case "1", "true", "yes", "on":
		return true
	}
	switch stripLower(c.getenv("VOICE_INSTALL_OFFER", "")) {
	case "0", "off", "false", "no":
		return false
	}
	return true
}

// VoiceExtraWasInstalled ports voice_extra_was_installed.
func (c *Config) VoiceExtraWasInstalled() bool {
	switch stripLower(c.getenv("VOICE_EXTRA_INSTALLED", "")) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// proxyEnvVars mirrors _PROXY_ENV_VARS, order included.
var proxyEnvVars = []string{"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"}

// DetectProxy ports detect_proxy: the first truthy proxy variable wins.
func (c *Config) DetectProxy() *string {
	for _, name := range proxyEnvVars {
		if v, ok := c.env(name); ok && v != "" {
			return &v
		}
	}
	return nil
}

// GetGithubToken ports get_github_token.
func (c *Config) GetGithubToken() *string { return c.getenvOrNil("GITHUB_TOKEN") }

// GetAzureDevopsToken ports get_azure_devops_token.
func (c *Config) GetAzureDevopsToken() *string { return c.getenvOrNil("AZURE_DEVOPS_TOKEN") }

// GetAzureDevopsOrgURL ports get_azure_devops_org_url: whitespace and
// trailing slashes stripped, a missing scheme defaulted to https://.
func (c *Config) GetAzureDevopsOrgURL() *string {
	raw := strings.TrimRight(pysem.Strip(c.getenv("AZURE_DEVOPS_ORG_URL", "")), "/")
	if raw == "" {
		return nil
	}
	if !strings.Contains(raw, "://") {
		raw = "https://" + raw
	}
	return &raw
}

// GetAzureDevopsProject ports get_azure_devops_project.
func (c *Config) GetAzureDevopsProject() *string { return c.getenvOrNil("AZURE_DEVOPS_PROJECT") }

// GetAzureDevopsTeam ports get_azure_devops_team, with AzDO's
// "{project} Team" default naming as the fallback.
func (c *Config) GetAzureDevopsTeam() *string {
	if team := c.getenvOrNil("AZURE_DEVOPS_TEAM"); team != nil {
		return team
	}
	if project := c.GetAzureDevopsProject(); project != nil {
		team := *project + " Team"
		return &team
	}
	return nil
}

// GetJiraBaseURL ports get_jira_base_url.
func (c *Config) GetJiraBaseURL() *string { return c.getenvOrNil("JIRA_BASE_URL") }

// GetJiraEmail ports get_jira_email.
func (c *Config) GetJiraEmail() *string { return c.getenvOrNil("JIRA_EMAIL") }

// GetJiraToken ports get_jira_token.
func (c *Config) GetJiraToken() *string { return c.getenvOrNil("JIRA_API_TOKEN") }

// GetJiraProjectKey ports get_jira_project_key.
func (c *Config) GetJiraProjectKey() *string { return c.getenvOrNil("JIRA_PROJECT_KEY") }

// acFormatAliases mirrors _AC_FORMAT_ALIASES.
var acFormatAliases = map[string]string{
	"gwt":             "gwt",
	"given-when-then": "gwt",
	"given/when/then": "gwt",
	"gherkin":         "gwt",
	"bullets":         "bullets",
	"bullet":          "bullets",
	"freeform":        "bullets",
	"free-form":       "bullets",
	"checklist":       "bullets",
}

// GetACFormat ports get_ac_format: unknown values normalise to "".
func (c *Config) GetACFormat() string {
	return acFormatAliases[stripLower(c.getenv("YEABOI_AC_FORMAT", ""))]
}

// GetConfluenceBaseURL ports get_confluence_base_url (falls back to Jira).
func (c *Config) GetConfluenceBaseURL() *string {
	if v := c.getenvOrNil("CONFLUENCE_BASE_URL"); v != nil {
		return v
	}
	return c.GetJiraBaseURL()
}

// GetConfluenceEmail ports get_confluence_email (falls back to Jira).
func (c *Config) GetConfluenceEmail() *string {
	if v := c.getenvOrNil("CONFLUENCE_EMAIL"); v != nil {
		return v
	}
	return c.GetJiraEmail()
}

// GetConfluenceToken ports get_confluence_token (falls back to Jira).
func (c *Config) GetConfluenceToken() *string {
	if v := c.getenvOrNil("CONFLUENCE_API_TOKEN"); v != nil {
		return v
	}
	return c.GetJiraToken()
}

// GetConfluenceSpaceKey ports get_confluence_space_key.
func (c *Config) GetConfluenceSpaceKey() *string { return c.getenvOrNil("CONFLUENCE_SPACE_KEY") }

// splitCommaList mirrors `[x.strip() for x in raw.split(",") if x.strip()]` —
// order kept, duplicates kept.
func splitCommaList(raw string) []string {
	out := []string{}
	for _, part := range strings.Split(raw, ",") {
		if p := pysem.Strip(part); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// GetAnonymizeMaskTerms ports get_anonymize_mask_terms.
func (c *Config) GetAnonymizeMaskTerms() []string {
	return splitCommaList(c.getenv("ANONYMIZE_MASK_TERMS", ""))
}

// GetNotionToken ports get_notion_token.
func (c *Config) GetNotionToken() *string { return c.getenvOrNil("NOTION_TOKEN") }

// GetNotionRootPageID ports get_notion_root_page_id.
func (c *Config) GetNotionRootPageID() *string { return c.getenvOrNil("NOTION_ROOT_PAGE_ID") }

// GetDataDir ports get_data_dir: the raw YEABOI_HOME value for display.
func (c *Config) GetDataDir() string { return c.getenv("YEABOI_HOME", "") }

// csvConfig ports _csv_config: stable, de-duplicated, order-preserving.
func (c *Config) csvConfig(name string) []string {
	values := []string{}
	seen := map[string]bool{}
	for _, part := range strings.Split(c.getenv(name, ""), ",") {
		p := pysem.Strip(part)
		if p != "" && !seen[p] {
			seen[p] = true
			values = append(values, p)
		}
	}
	return values
}

// GetAllowedPaths ports get_allowed_paths.
func (c *Config) GetAllowedPaths() []string { return c.csvConfig("YEABOI_ALLOWED_PATHS") }

// GetNotionExportParentPageID ports get_notion_export_parent_page_id.
func (c *Config) GetNotionExportParentPageID() *string {
	if v := c.getenvOrNil("NOTION_EXPORT_PARENT_PAGE_ID"); v != nil {
		return v
	}
	return c.GetNotionRootPageID()
}

// GetConfluenceExportParentPageID ports get_confluence_export_parent_page_id.
func (c *Config) GetConfluenceExportParentPageID() *string {
	return c.getenvOrNil("CONFLUENCE_EXPORT_PARENT_PAGE_ID")
}

// GetStandupGithubRepo ports get_standup_github_repo.
func (c *Config) GetStandupGithubRepo() string { return c.getenv("STANDUP_GITHUB_REPO", "") }

// GetTeamAnalysisGithubOwners ports get_team_analysis_github_owners,
// falling back to the owner half of STANDUP_GITHUB_REPO.
func (c *Config) GetTeamAnalysisGithubOwners() []string {
	if configured := c.csvConfig("TEAM_ANALYSIS_GITHUB_OWNERS"); len(configured) > 0 {
		return configured
	}
	repo := c.GetStandupGithubRepo()
	if owner, _, found := strings.Cut(repo, "/"); found {
		return []string{owner}
	}
	return []string{}
}

// GetTeamAnalysisAzdoProjects ports get_team_analysis_azdo_projects.
func (c *Config) GetTeamAnalysisAzdoProjects() []string {
	if configured := c.csvConfig("TEAM_ANALYSIS_AZDO_PROJECTS"); len(configured) > 0 {
		return configured
	}
	if project := c.GetAzureDevopsProject(); project != nil && *project != "" {
		return []string{*project}
	}
	return []string{}
}

// GetTeamAnalysisConfluenceSpaces ports get_team_analysis_confluence_spaces.
func (c *Config) GetTeamAnalysisConfluenceSpaces() []string {
	if configured := c.csvConfig("TEAM_ANALYSIS_CONFLUENCE_SPACES"); len(configured) > 0 {
		return configured
	}
	if space := c.GetConfluenceSpaceKey(); space != nil && *space != "" {
		return []string{*space}
	}
	return []string{}
}

// GetTeamAnalysisNotionRoots ports get_team_analysis_notion_roots.
func (c *Config) GetTeamAnalysisNotionRoots() []string {
	if configured := c.csvConfig("TEAM_ANALYSIS_NOTION_ROOTS"); len(configured) > 0 {
		return configured
	}
	if root := c.GetNotionRootPageID(); root != nil && *root != "" {
		return []string{*root}
	}
	return []string{}
}

// clampedInt is the shared shape of every clamped knob: default on a failed
// parse, min/max applied only after a successful one.
func (c *Config) clampedInt(name string, def, lo, hi int64) int64 {
	v, err := pysem.ParseInt(c.getenv(name, ""))
	if err != nil {
		return def
	}
	if v > hi {
		v = hi
	}
	if v < lo {
		v = lo
	}
	return v
}

// GetTeamAnalysisEnrichmentTimeoutSeconds ports its Python namesake.
func (c *Config) GetTeamAnalysisEnrichmentTimeoutSeconds() int64 {
	return c.clampedInt("TEAM_ANALYSIS_ENRICHMENT_TIMEOUT_SECONDS", 120, 10, 600)
}

// GetTeamAnalysisFastModel ports get_team_analysis_fast_model.
func (c *Config) GetTeamAnalysisFastModel() *string {
	raw := pysem.Strip(c.getenv("TEAM_ANALYSIS_FAST_MODEL", ""))
	if raw == "" {
		return nil
	}
	return &raw
}

// GetTeamAnalysisLLMTargetSeconds ports its Python namesake.
func (c *Config) GetTeamAnalysisLLMTargetSeconds() int64 {
	return c.clampedInt("TEAM_ANALYSIS_LLM_TARGET_SECONDS", 600, 60, 7200)
}

// GetTeamAnalysisLLMMaxConcurrency ports its Python namesake.
func (c *Config) GetTeamAnalysisLLMMaxConcurrency() int64 {
	return c.clampedInt("TEAM_ANALYSIS_LLM_MAX_CONCURRENCY", 6, 1, 12)
}

// GetTeamAnalysisDocRequestTimeoutSeconds ports its Python namesake.
func (c *Config) GetTeamAnalysisDocRequestTimeoutSeconds() int64 {
	return c.clampedInt("TEAM_ANALYSIS_DOC_REQUEST_TIMEOUT_SECONDS", 30, 5, 120)
}

// GetTeamAnalysisDocMaxConcurrency ports its Python namesake.
func (c *Config) GetTeamAnalysisDocMaxConcurrency() int64 {
	return c.clampedInt("TEAM_ANALYSIS_DOC_MAX_CONCURRENCY", 8, 1, 16)
}

// GetTeamAnalysisCodeMaxConcurrency ports its Python namesake.
func (c *Config) GetTeamAnalysisCodeMaxConcurrency() int64 {
	return c.clampedInt("TEAM_ANALYSIS_CODE_MAX_CONCURRENCY", 6, 1, 16)
}

// GetTeamAnalysisTrackerMaxConcurrency ports its Python namesake.
func (c *Config) GetTeamAnalysisTrackerMaxConcurrency() int64 {
	return c.clampedInt("TEAM_ANALYSIS_TRACKER_MAX_CONCURRENCY", 4, 1, 12)
}

// GetTeamAnalysisMaxChangeLookups ports its Python namesake.
func (c *Config) GetTeamAnalysisMaxChangeLookups() int64 {
	return c.clampedInt("TEAM_ANALYSIS_MAX_CHANGE_LOOKUPS", 500, 50, 5000)
}

// unclampedInt is the port-walk pattern: parse or default, no clamp.
func (c *Config) unclampedInt(name string, def int64) int64 {
	v, err := pysem.ParseInt(c.getenv(name, ""))
	if err != nil {
		return def
	}
	return v
}

// GetRetroServerPort ports get_retro_server_port.
func (c *Config) GetRetroServerPort() int64 {
	v, err := pysem.ParseInt(c.getenv("RETRO_PORT", "5173"))
	if err != nil {
		return 5173
	}
	return v
}

// GetPokerServerPort ports get_poker_server_port.
func (c *Config) GetPokerServerPort() int64 {
	v, err := pysem.ParseInt(c.getenv("POKER_PORT", "5273"))
	if err != nil {
		return 5273
	}
	return v
}

// TunnelsDisabled ports tunnels_disabled.
func (c *Config) TunnelsDisabled() bool {
	switch stripLower(c.getenv("YEABOI_NO_TUNNEL", "")) {
	case "1", "true", "yes":
		return true
	}
	return false
}

// GetTunnelTimeoutMinutes ports get_tunnel_timeout_minutes: 0 means never,
// clamped to at most 24h.
func (c *Config) GetTunnelTimeoutMinutes() int64 {
	minutes, err := pysem.ParseInt(c.getenv("TUNNEL_TIMEOUT_MINUTES", "60"))
	if err != nil {
		return 60
	}
	if minutes > 1440 {
		minutes = 1440
	}
	if minutes < 0 {
		minutes = 0
	}
	return minutes
}

// GetSlackWebhookURL ports get_slack_webhook_url.
func (c *Config) GetSlackWebhookURL() string { return c.getenv("SLACK_WEBHOOK_URL", "") }

// GetSmtpHost ports get_smtp_host.
func (c *Config) GetSmtpHost() string { return c.getenv("STANDUP_SMTP_HOST", "") }

// GetSmtpPort ports get_smtp_port — note the `or "587"`: an empty value
// falls back to the default, unlike the plain getenv-default getters.
func (c *Config) GetSmtpPort() int64 {
	raw := c.getenv("STANDUP_SMTP_PORT", "587")
	if raw == "" {
		raw = "587"
	}
	v, err := pysem.ParseInt(raw)
	if err != nil {
		return 587
	}
	return v
}

// GetSmtpUser ports get_smtp_user.
func (c *Config) GetSmtpUser() string { return c.getenv("STANDUP_SMTP_USER", "") }

// GetSmtpPassword ports get_smtp_password.
func (c *Config) GetSmtpPassword() string { return c.getenv("STANDUP_SMTP_PASSWORD", "") }

// GetSmtpSender ports get_smtp_sender (defaults to the SMTP user).
func (c *Config) GetSmtpSender() string {
	if sender := c.getenv("STANDUP_SMTP_SENDER", ""); sender != "" {
		return sender
	}
	return c.GetSmtpUser()
}

// GetStandupEmailRecipients ports get_standup_email_recipients — no dedup,
// deliberately unlike _csv_config.
func (c *Config) GetStandupEmailRecipients() []string {
	return splitCommaList(c.getenv("STANDUP_EMAIL_RECIPIENTS", ""))
}

// GetStandupUserName ports get_standup_user_name.
func (c *Config) GetStandupUserName() string {
	if name := pysem.Strip(c.getenv("STANDUP_USER_NAME", "")); name != "" {
		return name
	}
	return "Me"
}

// GetPerformanceFrameworkPath ports get_performance_framework_path.
func (c *Config) GetPerformanceFrameworkPath() string {
	return pysem.Strip(c.getenv("PERFORMANCE_FRAMEWORK_PATH", ""))
}

// GetLLMProvider ports get_llm_provider.
func (c *Config) GetLLMProvider() string {
	return pysem.Lower(c.getenv("LLM_PROVIDER", "anthropic"))
}

// GetLLMModel ports get_llm_model.
func (c *Config) GetLLMModel() *string { return c.getenvOrNil("LLM_MODEL") }

// GetBedrockRegion ports get_bedrock_region.
func (c *Config) GetBedrockRegion() string {
	if v, ok := c.env("AWS_REGION"); ok && v != "" {
		return v
	}
	if v, ok := c.env("AWS_DEFAULT_REGION"); ok && v != "" {
		return v
	}
	return "us-east-1"
}

// GetAWSProfile ports get_aws_profile: the explicit AWS_PROFILE, else the
// first ~/.aws/config profile with a role_arn or credential_source (see
// awsconfig.go), else nil.
func (c *Config) GetAWSProfile() *string {
	if profile, ok := c.env("AWS_PROFILE"); ok && profile != "" {
		return &profile
	}
	return c.autodetectAWSProfile()
}

// GetOpenaiAPIKey ports get_openai_api_key.
func (c *Config) GetOpenaiAPIKey() *string { return c.getenvOrNil("OPENAI_API_KEY") }

// GetGoogleAPIKey ports get_google_api_key.
func (c *Config) GetGoogleAPIKey() *string { return c.getenvOrNil("GOOGLE_API_KEY") }

// GetOllamaBaseURL ports get_ollama_base_url.
func (c *Config) GetOllamaBaseURL() string {
	return strings.TrimRight(c.getenv("OLLAMA_BASE_URL", "http://localhost:11434"), "/")
}

// GetOllamaNumCtx ports get_ollama_num_ctx.
func (c *Config) GetOllamaNumCtx() int64 { return c.unclampedInt("OLLAMA_NUM_CTX", 16384) }

// IsLLMConfigured ports is_llm_configured: (ok, message) for the active
// provider's credentials, no network involved.
func (c *Config) IsLLMConfigured() (bool, string) {
	anthropicKey, _ := c.env("ANTHROPIC_API_KEY")
	switch provider := c.GetLLMProvider(); provider {
	case "anthropic":
		return anthropicKey != "", "ANTHROPIC_API_KEY not set"
	case "openai":
		return c.GetOpenaiAPIKey() != nil, "OPENAI_API_KEY not set"
	case "google":
		return c.GetGoogleAPIKey() != nil, "GOOGLE_API_KEY not set"
	case "bedrock":
		region, _ := c.env("AWS_REGION")
		defaultRegion, _ := c.env("AWS_DEFAULT_REGION")
		ok := region != "" || defaultRegion != "" || c.GetAWSProfile() != nil
		return ok, "AWS credentials/region not configured for Bedrock"
	case "ollama":
		return true, ""
	default:
		return anthropicKey != "", "No API key configured for provider '" + provider + "'"
	}
}

// GetVoiceModel ports get_voice_model.
func (c *Config) GetVoiceModel() string {
	if v, ok := c.env("VOICE_MODEL"); ok && v != "" {
		return v
	}
	return "base"
}

// GetVoiceDevice ports get_voice_device.
func (c *Config) GetVoiceDevice() string {
	return pysem.Strip(c.getenv("VOICE_DEVICE", ""))
}

// GetSessionPruneDays ports get_session_prune_days: 0 disables pruning,
// negatives and garbage fall back to 30.
func (c *Config) GetSessionPruneDays() int64 {
	v, err := pysem.ParseInt(c.getenv("SESSION_PRUNE_DAYS", "30"))
	if err != nil || v < 0 {
		return 30
	}
	return v
}

// GetLogLevel ports get_log_level. Python calls str.upper(); Go's simple
// mapping differs only on multi-rune expansions (ß→SS and friends) that
// can never produce a valid level name, so both sides land on WARNING.
func (c *Config) GetLogLevel() string {
	switch raw := strings.ToUpper(c.getenv("LOG_LEVEL", "WARNING")); raw {
	case "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL":
		return raw
	}
	return "WARNING"
}

// envTruthy ports _env_truthy.
func (c *Config) envTruthy(name string) bool {
	switch stripLower(c.getenv(name, "")) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// IsTeamAnalysisJiraDevLinksEnabled ports its Python namesake.
func (c *Config) IsTeamAnalysisJiraDevLinksEnabled() bool {
	return c.envTruthy("TEAM_ANALYSIS_JIRA_DEV_LINKS")
}

// IsTeamAnalysisAzdoPRSearchEnabled ports is_team_analysis_azdo_pr_search_enabled.
func (c *Config) IsTeamAnalysisAzdoPRSearchEnabled() bool {
	return c.envTruthy("TEAM_ANALYSIS_AZDO_BRANCH_SEARCH")
}

// GetTeamAnalysisAzdoPRSearchMaxRepos ports its Python namesake.
func (c *Config) GetTeamAnalysisAzdoPRSearchMaxRepos() int64 {
	return c.clampedInt("TEAM_ANALYSIS_AZDO_PR_SEARCH_MAX_REPOS", 10, 1, 50)
}

// GetTeamAnalysisAzdoPRSearchTop ports get_team_analysis_azdo_pr_search_top.
func (c *Config) GetTeamAnalysisAzdoPRSearchTop() int64 {
	return c.clampedInt("TEAM_ANALYSIS_AZDO_PR_SEARCH_PRS_PER_REPO", 75, 10, 200)
}

// GetTeamAnalysisAzdoRepoAllowlist ports its Python namesake. Python
// returns a frozenset or None; the parity dump sorts the set, so nil vs a
// sorted slice is the canonical shape here.
func (c *Config) GetTeamAnalysisAzdoRepoAllowlist() []string {
	raw := pysem.Strip(c.getenv("TEAM_ANALYSIS_AZDO_REPO_ALLOWLIST", ""))
	if raw == "" {
		return nil
	}
	set := map[string]bool{}
	for _, part := range strings.Split(raw, ",") {
		if p := pysem.Strip(part); p != "" {
			set[pysem.Lower(p)] = true
		}
	}
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
