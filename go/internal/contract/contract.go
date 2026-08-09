// Package contract holds the wire types of the yeaboi-core RPC contract v1.
//
// Field names (json tags) mirror contracts/v1/*.json exactly; the Python
// client and the parity suite depend on them byte-for-byte.
package contract

// Version is the contract version stamped on every successful result.
const Version = 1

// Event is one analysis_component lifecycle event (contracts/v1/progress.json,
// mirroring src/yeaboi/analysis/progress.py append_component_progress).
// Optional keys are OMITTED when unset, never null — consumers feature-detect
// by key presence. Zero-valued current/total/secondary_count are still sent
// when explicitly set, hence the pointer fields.
type Event struct {
	Kind           string `json:"kind"`
	ComponentID    string `json:"component_id"`
	Label          string `json:"label"`
	Status         string `json:"status"`
	Detail         string `json:"detail"`
	Phase          string `json:"phase,omitempty"`
	Current        *int   `json:"current,omitempty"`
	Total          *int   `json:"total,omitempty"`
	Unit           string `json:"unit,omitempty"`
	SecondaryCount *int   `json:"secondary_count,omitempty"`
	SecondaryUnit  string `json:"secondary_unit,omitempty"`
	ReadOnly       bool   `json:"read_only,omitempty"`
}

// Stats mirrors collector.IngestStats (contracts/v1/agentwatch.refresh.json).
type Stats struct {
	FilesSeen        int      `json:"files_seen"`
	FilesSkipped     int      `json:"files_skipped"`
	FilesParsed      int      `json:"files_parsed"`
	FilesPruned      int      `json:"files_pruned"`
	SessionsUpserted int      `json:"sessions_upserted"`
	FindingsAdded    int      `json:"findings_added"`
	MalformedLines   int      `json:"malformed_lines"`
	Warnings         []string `json:"warnings"`
}

// NewStats returns a Stats whose warnings serialize as [] rather than null.
func NewStats() *Stats {
	return &Stats{Warnings: []string{}}
}

// RootSpec is one (source, root) scan-root override.
type RootSpec struct {
	Source string `json:"source"`
	Root   string `json:"root"`
}

// RefreshParams are the agentwatch.refresh params.
// Roots nil (omitted or null) means the default ~/.claude/projects root.
type RefreshParams struct {
	DBPath       string      `json:"db_path"`
	Roots        *[]RootSpec `json:"roots"`
	ResetCursors bool        `json:"reset_cursors"`
}

// RefreshResult is the agentwatch.refresh result.
type RefreshResult struct {
	ContractVersion int    `json:"contract_version"`
	Stats           *Stats `json:"stats"`
}

// UsageParams are the agentwatch.usage params. Refresh defaults to true when
// omitted, hence the pointer.
type UsageParams struct {
	DBPath     string      `json:"db_path"`
	WindowDays int         `json:"window_days"`
	Project    string      `json:"project"`
	Source     string      `json:"source"`
	Today      string      `json:"today"`
	Roots      *[]RootSpec `json:"roots"`
	Refresh    *bool       `json:"refresh"`
}

// ModelRow mirrors yeaboi.agent.state.ModelUsageRow.
type ModelRow struct {
	Model            string  `json:"model"`
	InputTokens      int64   `json:"input_tokens"`
	OutputTokens     int64   `json:"output_tokens"`
	CacheWriteTokens int64   `json:"cache_write_tokens"`
	CacheReadTokens  int64   `json:"cache_read_tokens"`
	Calls            int64   `json:"calls"`
	CostUSD          float64 `json:"cost_usd"`
	KnownPricing     bool    `json:"known_pricing"`
}

// BreakdownRow mirrors yeaboi.agent.state.AgentUsageBreakdownRow.
type BreakdownRow struct {
	Key          string  `json:"key"`
	Sessions     int     `json:"sessions"`
	InputTokens  int64   `json:"input_tokens"`
	OutputTokens int64   `json:"output_tokens"`
	CostUSD      float64 `json:"cost_usd"`
}

// DailyPoint mirrors yeaboi.agent.state.DailyUsagePoint.
type DailyPoint struct {
	Date         string  `json:"date"`
	CostUSD      float64 `json:"cost_usd"`
	InputTokens  int64   `json:"input_tokens"`
	OutputTokens int64   `json:"output_tokens"`
	Sessions     int     `json:"sessions"`
}

// UsageArtifact mirrors the deterministic fields of AgentUsageReport.
// Insights/Recommendations/GeneratedAt are ALWAYS empty from Go — prose and
// time stamping stay Python-side.
type UsageArtifact struct {
	PeriodStart           string         `json:"period_start"`
	PeriodEnd             string         `json:"period_end"`
	SessionCount          int            `json:"session_count"`
	TotalCostUSD          float64        `json:"total_cost_usd"`
	TotalInputTokens      int64          `json:"total_input_tokens"`
	TotalOutputTokens     int64          `json:"total_output_tokens"`
	TotalCacheWriteTokens int64          `json:"total_cache_write_tokens"`
	TotalCacheReadTokens  int64          `json:"total_cache_read_tokens"`
	UnknownModelCostShare float64        `json:"unknown_model_cost_share"`
	PricingAsOf           string         `json:"pricing_as_of"`
	ByModel               []ModelRow     `json:"by_model"`
	ByProject             []BreakdownRow `json:"by_project"`
	BySource              []BreakdownRow `json:"by_source"`
	DailyTrend            []DailyPoint   `json:"daily_trend"`
	Insights              []string       `json:"insights"`
	Recommendations       []string       `json:"recommendations"`
	Warnings              []string       `json:"warnings"`
	GeneratedAt           string         `json:"generated_at"`
}

// UsageResult is the agentwatch.usage result.
type UsageResult struct {
	ContractVersion int            `json:"contract_version"`
	Stats           *Stats         `json:"stats"`
	Artifact        *UsageArtifact `json:"artifact"`
}

// StandupParams are the agentwatch.standup params. window_start/digest_date
// are computed Python-side (the previous-working-day logic is local-timezone-
// and convention-bound) and travel as YYYY-MM-DD strings.
type StandupParams struct {
	DBPath      string      `json:"db_path"`
	WindowStart string      `json:"window_start"`
	DigestDate  string      `json:"digest_date"`
	Roots       *[]RootSpec `json:"roots"`
}

// SessionSummary mirrors yeaboi.agent.state.AgentSessionSummary. TopTools
// serializes as [name, count-as-string] pairs, exactly like Python's asdict
// of the (str, str) tuples.
type SessionSummary struct {
	SessionID string     `json:"session_id"`
	Source    string     `json:"source"`
	Project   string     `json:"project"`
	Branch    string     `json:"branch"`
	Models    []string   `json:"models"`
	Turns     int        `json:"turns"`
	CostUSD   float64    `json:"cost_usd"`
	TopTools  [][]string `json:"top_tools"`
	StartedAt string     `json:"started_at"`
	EndedAt   string     `json:"ended_at"`
}

// StandupArtifact mirrors the LOCAL half of AgentStandupDigest. RepoActivity,
// the prose fields and GeneratedAt are ALWAYS empty from Go — the tracker leg
// and the LLM stay Python-side; AgentsSeen carries session sources only.
type StandupArtifact struct {
	DigestDate       string           `json:"digest_date"`
	WindowStart      string           `json:"window_start"`
	WindowEnd        string           `json:"window_end"`
	SessionsWorked   int              `json:"sessions_worked"`
	TotalCostUSD     float64          `json:"total_cost_usd"`
	AgentsSeen       []string         `json:"agents_seen"`
	SessionSummaries []SessionSummary `json:"session_summaries"`
	RepoActivity     []any            `json:"repo_activity"`
	Highlights       []string         `json:"highlights"`
	InFlight         []string         `json:"in_flight"`
	AttentionItems   []string         `json:"attention_items"`
	Narrative        string           `json:"narrative"`
	CoverageNotes    []string         `json:"coverage_notes"`
	Warnings         []string         `json:"warnings"`
	GeneratedAt      string           `json:"generated_at"`
}

// StandupResult is the agentwatch.standup result.
type StandupResult struct {
	ContractVersion int              `json:"contract_version"`
	Stats           *Stats           `json:"stats"`
	Artifact        *StandupArtifact `json:"artifact"`
}

// SecurityParams are the agentwatch.security params. The config roots are
// Python's security_checks._config_roots() results passed through, so test
// overrides and parity fixtures reach the sidecar.
type SecurityParams struct {
	DBPath       string      `json:"db_path"`
	ScanDate     string      `json:"scan_date"`
	ResetCursors bool        `json:"reset_cursors"`
	ClaudeDir    string      `json:"claude_dir"`
	ClaudeJSON   string      `json:"claude_json"`
	Roots        *[]RootSpec `json:"roots"`
}

// SecurityFinding mirrors yeaboi.agent.state.SecurityFinding. Detail may
// quote a CONFIG value (never transcript content — privacy rule 1).
type SecurityFinding struct {
	Severity    string `json:"severity"`
	Category    string `json:"category"`
	Title       string `json:"title"`
	Location    string `json:"location"`
	LineNo      int    `json:"line_no"`
	Pattern     string `json:"pattern"`
	Detail      string `json:"detail"`
	Remediation string `json:"remediation"`
}

// McpServer mirrors yeaboi.agent.state.McpServerRecord.
type McpServer struct {
	Name      string   `json:"name"`
	Scope     string   `json:"scope"`
	Transport string   `json:"transport"`
	Target    string   `json:"target"`
	Flags     []string `json:"flags"`
}

// SecurityArtifact mirrors the deterministic whole of AgentSecurityReport.
// Summary/Recommendations (LLM prose) and GeneratedAt are ALWAYS empty from
// Go — the Python side fills them.
type SecurityArtifact struct {
	ScanDate        string            `json:"scan_date"`
	Posture         string            `json:"posture"`
	SessionsScanned int               `json:"sessions_scanned"`
	FilesScanned    int               `json:"files_scanned"`
	SecretsFound    int               `json:"secrets_found"`
	Findings        []SecurityFinding `json:"findings"`
	McpServers      []McpServer       `json:"mcp_servers"`
	SettingsFlags   []string          `json:"settings_flags"`
	Summary         string            `json:"summary"`
	Recommendations []string          `json:"recommendations"`
	Warnings        []string          `json:"warnings"`
	GeneratedAt     string            `json:"generated_at"`
}

// SecurityResult is the agentwatch.security result.
type SecurityResult struct {
	ContractVersion int               `json:"contract_version"`
	Stats           *Stats            `json:"stats"`
	Artifact        *SecurityArtifact `json:"artifact"`
}

// HelloResult is the core.hello result.
type HelloResult struct {
	ContractVersion int      `json:"contract_version"`
	Name            string   `json:"name"`
	Version         string   `json:"version"`
	Methods         []string `json:"methods"`
}
