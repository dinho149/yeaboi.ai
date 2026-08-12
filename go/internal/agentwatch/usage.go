package agentwatch

// The agentwatch.refresh / agentwatch.usage pipelines. RunAgentUsage ports
// ONLY the deterministic aggregation of engine.run_agent_usage — the LLM
// call, fallback insights, report recording and export stay Python-side, so
// insights/recommendations/generated_at leave here empty.
//
// Order sensitivity is deliberate everywhere: sessions iterate in
// list_sessions order (ended_at DESC), models within a session iterate in
// sorted-key order (the JSON was dumped with sort_keys=True, and Python dicts
// preserve that document order), and every float accumulates in the same
// sequence as the Python loops so the sums are bit-identical.

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/yeaboi-ai/yeaboi/go/internal/contract"
)

// emptyWindowWarning is engine.run_agent_usage's empty-window note, verbatim.
const emptyWindowWarning = "No local agent sessions found in the window — is Claude Code used on this machine?"

// emitPhase sends one engine-level lifecycle event (engine._emit).
func emitPhase(emit func(*contract.Event), componentID, status, label, detail string) {
	if emit == nil {
		return
	}
	if label == "" {
		label = componentID
	}
	emit(&contract.Event{
		Kind:        "analysis_component",
		ComponentID: componentID,
		Label:       label,
		Status:      status,
		Detail:      detail,
	})
}

// projectLabel mirrors engine._project_label: the path's last component.
func projectLabel(projectPath string) string {
	name := pyPathName(projectPath)
	if name != "" {
		return name
	}
	if projectPath != "" {
		return projectPath
	}
	return "(unknown)"
}

// pyPathName approximates pathlib.PurePath.name for the POSIX paths stored in
// project_path: trailing slashes drop, the root and the special "." / ".."
// whole-paths have no name.
func pyPathName(p string) string {
	if p == "" || p == "." || p == ".." {
		return ""
	}
	trimmed := strings.TrimRight(p, "/")
	if trimmed == "" {
		return "" // "/" — the root has no name
	}
	if i := strings.LastIndexByte(trimmed, '/'); i >= 0 {
		return trimmed[i+1:]
	}
	return trimmed
}

// sessionCost prices one session's per-model usage (engine._session_cost).
func sessionCost(modelUsage map[string]map[string]int64) float64 {
	total := 0.0
	for _, model := range sortedKeys(modelUsage) {
		u := modelUsage[model]
		est := EstimateCost(model, u["input"], u["output"], u["cache_write_5m"], u["cache_write_1h"], u["cache_read"], "")
		total += est.USD
	}
	return total
}

// distinctSessionCount mirrors engine._distinct_session_count: logical
// sessions, not rollup rows.
func distinctSessionCount(sessions []SessionRow) int {
	seen := map[string]bool{}
	for _, s := range sessions {
		key := s.SessionID
		if key == "" {
			key = s.SourcePath
		}
		seen[key] = true
	}
	return len(seen)
}

// RunAgentRefresh services the agentwatch.refresh method.
func RunAgentRefresh(p *contract.RefreshParams, emit func(*contract.Event)) (*contract.RefreshResult, error) {
	store, err := OpenStore(p.DBPath)
	if err != nil {
		return nil, err
	}
	defer store.Close()
	if p.ResetCursors {
		if err := store.ResetCursors(); err != nil {
			return nil, err
		}
	}
	stats, err := Refresh(store, resolveRoots(p.Roots), emit)
	if err != nil {
		return nil, err
	}
	return &contract.RefreshResult{ContractVersion: contract.Version, Stats: stats}, nil
}

// resolveRoots maps the wire roots onto scan roots: nil (omitted/null) means
// the default source set; an explicit empty list scans nothing.
func resolveRoots(roots *[]contract.RootSpec) []SourceRoot {
	if roots == nil {
		return DefaultRoots()
	}
	out := make([]SourceRoot, 0, len(*roots))
	for _, r := range *roots {
		out = append(out, SourceRoot{Source: r.Source, Root: r.Root})
	}
	return out
}

type modelAccum struct {
	input, output, cacheWrite, cacheRead, calls int64
	cost                                        float64
	known                                       bool
}

type breakdownAccum struct {
	input, output int64
	cost          float64
}

type bucketKey struct{ kind, key string }

// RunAgentUsage services the agentwatch.usage method: refresh (unless
// disabled) plus the deterministic aggregation of engine.run_agent_usage.
func RunAgentUsage(p *contract.UsageParams, emit func(*contract.Event)) (*contract.UsageResult, error) {
	windowDays := p.WindowDays
	if windowDays < 1 {
		windowDays = 1
	}
	today, err := time.ParseInLocation("2006-01-02", p.Today, time.UTC)
	if err != nil {
		return nil, &pyError{class: "ValueError", msg: fmt.Sprintf("invalid today date %q", p.Today)}
	}
	periodStart := today.AddDate(0, 0, -(windowDays - 1)).Format("2006-01-02")
	periodEnd := today.Format("2006-01-02")
	doRefresh := p.Refresh == nil || *p.Refresh

	store, err := OpenStore(p.DBPath)
	if err != nil {
		return nil, err
	}
	stats := contract.NewStats()
	warnings := []string{}
	if doRefresh {
		emitPhase(emit, "scan", "running", "Scan agent sessions", "")
		if stats, err = Refresh(store, resolveRoots(p.Roots), emit); err != nil {
			store.Close()
			return nil, err
		}
		emitPhase(emit, "scan", "completed", "Scan agent sessions",
			fmt.Sprintf("%d parsed · %d cached", stats.FilesParsed, stats.FilesSkipped))
		warnings = append(warnings, stats.Warnings...)
	}
	sessions, err := store.ListSessions(periodStart)
	store.Close()
	if err != nil {
		return nil, err
	}

	if p.Project != "" {
		needle := strings.ToLower(p.Project)
		var kept []SessionRow
		for _, s := range sessions {
			if strings.Contains(strings.ToLower(projectLabel(s.ProjectPath)), needle) {
				kept = append(kept, s)
			}
		}
		sessions = kept
	}
	if p.Source != "" {
		var kept []SessionRow
		for _, s := range sessions {
			if s.Source == p.Source {
				kept = append(kept, s)
			}
		}
		sessions = kept
	}

	transcriptDetail := fmt.Sprintf("%d transcript(s)", len(sessions))
	emitPhase(emit, "price", "running", "Price usage", transcriptDetail)

	modelTotals := map[string]*modelAccum{}
	var modelOrder []string
	projectTotals := map[string]*breakdownAccum{}
	var projectOrder []string
	sourceTotals := map[string]*breakdownAccum{}
	var sourceOrder []string
	dailyTotals := map[string]*breakdownAccum{}
	var dailyOrder []string
	bucketSessions := map[bucketKey]map[string]bool{}
	unknownCost := 0.0
	totalCost := 0.0

	addBucketSession := func(kind, key, sKey string) {
		bk := bucketKey{kind, key}
		set, ok := bucketSessions[bk]
		if !ok {
			set = map[string]bool{}
			bucketSessions[bk] = set
		}
		set[sKey] = true
	}
	getBreakdown := func(m map[string]*breakdownAccum, order *[]string, key string) *breakdownAccum {
		acc, ok := m[key]
		if !ok {
			acc = &breakdownAccum{}
			m[key] = acc
			*order = append(*order, key)
		}
		return acc
	}

	// Costs sum over rollup ROWS (disjoint per transcript file); the session
	// counts beside them are distinct logical sessions.
	for _, session := range sessions {
		sCost := sessionCost(session.ModelUsage)
		pLabel := projectLabel(session.ProjectPath)
		day := session.EndedAt
		if len(day) > 10 {
			day = day[:10]
		}
		sKey := session.SessionID
		if sKey == "" {
			sKey = session.SourcePath
		}
		srcKey := session.Source
		if srcKey == "" {
			srcKey = "(unknown)"
		}
		addBucketSession("project", pLabel, sKey)
		getBreakdown(projectTotals, &projectOrder, pLabel).cost += sCost
		addBucketSession("source", srcKey, sKey)
		getBreakdown(sourceTotals, &sourceOrder, srcKey).cost += sCost
		if day != "" {
			addBucketSession("day", day, sKey)
			getBreakdown(dailyTotals, &dailyOrder, day).cost += sCost
		}
		totalCost += sCost
		for _, model := range sortedKeys(session.ModelUsage) {
			u := session.ModelUsage[model]
			est := EstimateCost(model, u["input"], u["output"], u["cache_write_5m"], u["cache_write_1h"], u["cache_read"], "")
			m, ok := modelTotals[model]
			if !ok {
				m = &modelAccum{}
				modelTotals[model] = m
				modelOrder = append(modelOrder, model)
			}
			m.input += u["input"]
			m.output += u["output"]
			m.cacheWrite += u["cache_write_5m"] + u["cache_write_1h"]
			m.cacheRead += u["cache_read"]
			m.calls += u["calls"]
			m.cost += est.USD
			m.known = est.KnownModel
			if !est.KnownModel {
				unknownCost += est.USD
			}
			pAcc := getBreakdown(projectTotals, &projectOrder, pLabel)
			pAcc.input += u["input"]
			pAcc.output += u["output"]
			srcAcc := getBreakdown(sourceTotals, &sourceOrder, srcKey)
			srcAcc.input += u["input"]
			srcAcc.output += u["output"]
			if day != "" {
				dAcc := getBreakdown(dailyTotals, &dailyOrder, day)
				dAcc.input += u["input"]
				dAcc.output += u["output"]
			}
		}
	}

	byModel := make([]contract.ModelRow, 0, len(modelOrder))
	sortStableByCostDesc(modelOrder, func(k string) float64 { return modelTotals[k].cost })
	for _, model := range modelOrder {
		t := modelTotals[model]
		byModel = append(byModel, contract.ModelRow{
			Model:            model,
			InputTokens:      t.input,
			OutputTokens:     t.output,
			CacheWriteTokens: t.cacheWrite,
			CacheReadTokens:  t.cacheRead,
			Calls:            t.calls,
			CostUSD:          round4(t.cost),
			KnownPricing:     t.known,
		})
	}

	buildBreakdown := func(m map[string]*breakdownAccum, order []string, kind string) []contract.BreakdownRow {
		sortStableByCostDesc(order, func(k string) float64 { return m[k].cost })
		rows := make([]contract.BreakdownRow, 0, len(order))
		for _, key := range order {
			t := m[key]
			rows = append(rows, contract.BreakdownRow{
				Key:          key,
				Sessions:     len(bucketSessions[bucketKey{kind, key}]),
				InputTokens:  t.input,
				OutputTokens: t.output,
				CostUSD:      round4(t.cost),
			})
		}
		return rows
	}
	byProject := buildBreakdown(projectTotals, projectOrder, "project")
	bySource := buildBreakdown(sourceTotals, sourceOrder, "source")

	sort.Strings(dailyOrder)
	dailyTrend := make([]contract.DailyPoint, 0, len(dailyOrder))
	for _, day := range dailyOrder {
		t := dailyTotals[day]
		dailyTrend = append(dailyTrend, contract.DailyPoint{
			Date:         day,
			CostUSD:      round4(t.cost),
			InputTokens:  t.input,
			OutputTokens: t.output,
			Sessions:     len(bucketSessions[bucketKey{"day", day}]),
		})
	}

	if len(sessions) == 0 {
		warnings = append(warnings, emptyWindowWarning)
	}
	priceStatus := "completed"
	if len(sessions) == 0 {
		priceStatus = "no_data"
	}
	emitPhase(emit, "price", priceStatus, "Price usage", transcriptDetail)

	var totalInput, totalOutput, totalCacheWrite, totalCacheRead int64
	for _, r := range byModel {
		totalInput += r.InputTokens
		totalOutput += r.OutputTokens
		totalCacheWrite += r.CacheWriteTokens
		totalCacheRead += r.CacheReadTokens
	}
	unknownShare := 0.0
	if totalCost > 0 {
		unknownShare = round4(unknownCost / totalCost)
	}

	artifact := &contract.UsageArtifact{
		PeriodStart:           periodStart,
		PeriodEnd:             periodEnd,
		SessionCount:          distinctSessionCount(sessions),
		TotalCostUSD:          round4(totalCost),
		TotalInputTokens:      totalInput,
		TotalOutputTokens:     totalOutput,
		TotalCacheWriteTokens: totalCacheWrite,
		TotalCacheReadTokens:  totalCacheRead,
		UnknownModelCostShare: unknownShare,
		PricingAsOf:           PricingAsOf,
		ByModel:               byModel,
		ByProject:             byProject,
		BySource:              bySource,
		DailyTrend:            dailyTrend,
		Insights:              []string{},
		Recommendations:       []string{},
		Warnings:              warnings,
		GeneratedAt:           "",
	}
	return &contract.UsageResult{ContractVersion: contract.Version, Stats: stats, Artifact: artifact}, nil
}

// sortStableByCostDesc mirrors sorted(..., key=cost, reverse=True): a stable
// descending sort, ties keeping first-seen order.
func sortStableByCostDesc(keys []string, cost func(string) float64) {
	sort.SliceStable(keys, func(i, j int) bool { return cost(keys[i]) > cost(keys[j]) })
}
