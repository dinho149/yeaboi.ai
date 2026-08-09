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

// HelloResult is the core.hello result.
type HelloResult struct {
	ContractVersion int      `json:"contract_version"`
	Name            string   `json:"name"`
	Version         string   `json:"version"`
	Methods         []string `json:"methods"`
}
