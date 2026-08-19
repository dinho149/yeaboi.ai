// Port of src/yeaboi/analysis/aggregate.py (classify_markers + score_code +
// score_docs) — keep in lockstep. The RPC entrypoints behind
// analysis.classify_markers, analysis.score_code and analysis.score_docs
// (contracts/v1): params arrive as ordered JSON, results go back as
// *pysem.Obj in the reference implementation's dict-literal key order
// (contractual — they feed json.dumps downstream). Pure compute: no DB,
// no clock, no logging, and never any input content in an error.

package analysis

import (
	"sort"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// RunClassifyMarkers mirrors aggregate.py::classify_markers: the adoption
// signal plus every AI-marked evidence sample (no cap — limit=None).
func RunClassifyMarkers(params *pysem.Obj) (*pysem.Obj, error) {
	items, err := itemList(params, "items")
	if err != nil {
		return nil, err
	}
	result := pysem.EmptyObj()
	// The protocol envelope, not part of the pure function (same spelling as
	// standup's RunStandupAggregate: set first, popped by the parity harness).
	result.Set("contract_version", int64(1))
	result.Set("signal", AggregateAiMarkers(items))
	result.Set("samples", CollectSamples(items, -1))
	return result, nil
}

// RunScoreCode mirrors aggregate.py::score_code: code-change health (gated by
// health_enabled, empty scaffold when off), the per-member activity tally,
// and practice-hygiene scoring.
func RunScoreCode(params *pysem.Obj) (*pysem.Obj, error) {
	items, err := itemList(params, "items")
	if err != nil {
		return nil, err
	}
	changedFiles, err := itemList(params, "changed_files")
	if err != nil {
		return nil, err
	}
	// Every scalar param is required (schema + the reference's inputs["…"]
	// KeyError): defaulting silently would score wrong numbers on the fast
	// path while the Python path fails loudly.
	for _, key := range []string{"selected_users", "window_days", "health_enabled", "changed_file_cache_hits"} {
		if params.Get(key) == nil {
			return nil, &pysem.Error{Class: "KeyError", Msg: key + " is required"}
		}
	}
	selectedUsers := stringList(params.Get("selected_users"))
	windowDays, err := pysem.IntOrZero(params.Get("window_days"))
	if err != nil {
		return nil, err
	}
	healthEnabled := pysem.Truthy(params.Get("health_enabled"))
	cacheHits, err := pysem.IntOrZero(params.Get("changed_file_cache_hits"))
	if err != nil {
		return nil, err
	}

	fileReports := []any{}
	healthFindings := []any{}
	actionPlan := []any{}
	fileCoverage := pysem.EmptyObj()
	repositoryHealth := pysem.EmptyObj()
	notes := []string{}
	if healthEnabled {
		fileReports, healthFindings, fileCoverage = AnalyseChangedFiles(changedFiles, int(windowDays))
		actionPlan = PrioritizeActions(healthFindings)
		repositoryHealth = ChangedFileSummary(fileReports, healthFindings)
		// Appended AFTER the summaries are built, exactly like the reference —
		// the key lands last in both objects, and coverage_notes reads it.
		repositoryHealth.Set("cached_change_lookups", cacheHits)
		fileCoverage.Set("cached_change_lookups", cacheHits)
		notes = CoverageNotes(fileCoverage)
	}

	// Per-member activity over the deduped items so the footprint denominator
	// is verifiable at a glance (one member carrying thousands of automated
	// commits is visible instead of hidden in a total).
	memberRows := map[string]*pysem.Obj{}
	rowOrder := []*pysem.Obj{}
	for _, member := range selectedUsers {
		// Python's dict comprehension collapses a duplicate name to ONE row
		// at its first position (the re-created value is identical zeros), so
		// a repeat must not append a second, never-bumped row.
		if _, seen := memberRows[member]; seen {
			continue
		}
		row := activityRow(member)
		memberRows[member] = row
		rowOrder = append(rowOrder, row)
	}
	agentRow := activityRow("AI agent accounts")
	for _, raw := range items {
		item := pysem.AsObj(raw)
		if item == nil {
			continue
		}
		kind := pysem.Str(item.Get("kind"))
		if kind != "commit" && kind != "pr" {
			continue
		}
		slot := "prs"
		if kind == "commit" {
			slot = "commits"
		}
		aiMarked := len(ClassifyAiItem(item)) > 0
		targets := []*pysem.Obj{}
		for _, m := range stringList(item.Get("matched_members")) {
			if row, ok := memberRows[m]; ok {
				targets = append(targets, row)
			}
		}
		if len(targets) == 0 && pysem.Truthy(item.Get("agent_authored")) {
			targets = append(targets, agentRow)
		}
		for _, row := range targets {
			bump(row, slot)
			if aiMarked {
				bump(row, "ai_marked")
			}
		}
	}
	// Python: sorted(rows, key=lambda row: (-(commits+prs), member)) — stable.
	sort.SliceStable(rowOrder, func(i, j int) bool {
		iTotal := rowTotal(rowOrder[i])
		jTotal := rowTotal(rowOrder[j])
		if iTotal != jTotal {
			return iTotal > jTotal
		}
		return pysem.Str(rowOrder[i].Get("member")) < pysem.Str(rowOrder[j].Get("member"))
	})
	memberActivity := []any{}
	for _, row := range rowOrder {
		memberActivity = append(memberActivity, row)
	}
	if rowTotal(agentRow) > 0 {
		memberActivity = append(memberActivity, agentRow)
	}

	health := pysem.EmptyObj()
	health.Set("file_reports", fileReports)
	health.Set("findings", healthFindings)
	health.Set("action_plan", actionPlan)
	health.Set("file_coverage", fileCoverage)
	health.Set("repository_health", repositoryHealth)
	health.Set("coverage_notes", notes)

	counts := pysem.EmptyObj()
	counts.Set("commits", kindCount(items, "commit"))
	counts.Set("prs", kindCount(items, "pr"))
	counts.Set("reviews", kindCount(items, "review"))
	counts.Set("comments", kindCount(items, "comment"))

	result := pysem.EmptyObj()
	// The protocol envelope, not part of the pure function (see above).
	result.Set("contract_version", int64(1))
	result.Set("member_activity", memberActivity)
	result.Set("practices", MemberPractices(items, selectedUsers))
	result.Set("health", health)
	result.Set("activity_counts", counts)
	return result, nil
}

// RunScoreDocs mirrors aggregate.py::score_docs: page assets (cached ones pass
// through, fresh bodies are scored), the aggregated signal, the summary blob,
// findings, the action plan, and the deterministic coaching insights.
func RunScoreDocs(params *pysem.Obj) (*pysem.Obj, error) {
	pages, err := itemList(params, "pages")
	if err != nil {
		return nil, err
	}
	scored := ScoreDocs(pages)
	result := pysem.EmptyObj()
	// The protocol envelope, not part of the pure function (see above).
	result.Set("contract_version", int64(1))
	for _, key := range scored.Keys() {
		result.Set(key, scored.Get(key))
	}
	return result, nil
}

func itemList(params *pysem.Obj, key string) ([]any, error) {
	value := params.Get(key)
	if value == nil {
		return nil, &pysem.Error{Class: "KeyError", Msg: key + " is required"}
	}
	list, ok := value.([]any)
	if !ok {
		return nil, &pysem.Error{Class: "TypeError", Msg: key + " must be a list"}
	}
	return list, nil
}

func stringList(value any) []string {
	list, _ := value.([]any)
	out := make([]string, 0, len(list))
	for _, v := range list {
		out = append(out, pysem.Str(v))
	}
	return out
}

func activityRow(member string) *pysem.Obj {
	row := pysem.EmptyObj()
	row.Set("member", member)
	row.Set("commits", int64(0))
	row.Set("prs", int64(0))
	row.Set("ai_marked", int64(0))
	return row
}

func bump(row *pysem.Obj, key string) {
	current, _ := row.Get(key).(int64)
	row.Set(key, current+1)
}

func rowTotal(row *pysem.Obj) int64 {
	commits, _ := row.Get("commits").(int64)
	prs, _ := row.Get("prs").(int64)
	return commits + prs
}

func kindCount(items []any, kind string) int64 {
	var count int64
	for _, raw := range items {
		if item := pysem.AsObj(raw); item != nil && pysem.Str(item.Get("kind")) == kind {
			count++
		}
	}
	return count
}
