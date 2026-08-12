package agentwatch

// The agentwatch.standup pipeline — a port of the LOCAL half of
// engine.run_agent_standup (engine._deterministic_standup_digest): refresh,
// summarise the window's sessions, total them. The tracker leg, all prose,
// delivery and history stay Python-side, so the repo/prose fields leave here
// empty and agents_seen carries session sources only.

import (
	"fmt"
	"sort"
	"strconv"

	"github.com/yeaboi-ai/yeaboi/go/internal/contract"
)

// noLocalSessionsNote is engine._deterministic_standup_digest's coverage note,
// verbatim.
const noLocalSessionsNote = "No local agent sessions in the window — this environment has no agent session history, " +
	"so the digest covers tracker activity only."

// summariseSessions mirrors engine._summarise_sessions: one summary per
// rollup row, costliest first (stable over the ended_at DESC list order).
func summariseSessions(sessions []SessionRow) []contract.SessionSummary {
	out := make([]contract.SessionSummary, 0, len(sessions))
	for _, s := range sessions {
		// Top tools: Python stable-sorts dict items by count descending; the
		// stored JSON was dumped with sort_keys=True, so the tie-breaking base
		// order is sorted-by-name.
		names := sortedKeys(s.ToolCounts)
		sort.SliceStable(names, func(i, j int) bool { return s.ToolCounts[names[i]] > s.ToolCounts[names[j]] })
		if len(names) > 3 {
			names = names[:3]
		}
		topTools := make([][]string, 0, len(names))
		for _, n := range names {
			topTools = append(topTools, []string{n, strconv.FormatInt(s.ToolCounts[n], 10)})
		}
		out = append(out, contract.SessionSummary{
			SessionID: s.SessionID,
			Source:    s.Source,
			Project:   projectLabel(s.ProjectPath),
			Branch:    s.GitBranch,
			Models:    sortedKeys(s.ModelUsage),
			Turns:     int(s.Turns),
			CostUSD:   round4(sessionCost(s.ModelUsage)),
			TopTools:  topTools,
			StartedAt: s.StartedAt,
			EndedAt:   s.EndedAt,
		})
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].CostUSD > out[j].CostUSD })
	return out
}

// RunAgentStandup services the agentwatch.standup method.
func RunAgentStandup(p *contract.StandupParams, emit func(*contract.Event)) (*contract.StandupResult, error) {
	emitPhase(emit, "scan", "running", "Scan agent sessions", "")
	store, err := OpenStore(p.DBPath)
	if err != nil {
		return nil, err
	}
	stats, err := Refresh(store, resolveRoots(p.Roots), emit)
	if err != nil {
		store.Close()
		return nil, err
	}
	emitPhase(emit, "scan", "completed", "Scan agent sessions",
		fmt.Sprintf("%d parsed · %d cached", stats.FilesParsed, stats.FilesSkipped))
	sessions, err := store.ListSessions(p.WindowStart)
	store.Close()
	if err != nil {
		return nil, err
	}

	summaries := summariseSessions(sessions)
	// Python sums the per-summary ROUNDED costs in cost-descending order —
	// float accumulation order matters for bit-identity.
	total := 0.0
	sourceSet := map[string]bool{}
	for _, s := range summaries {
		total += s.CostUSD
		sourceSet[s.Source] = true
	}
	coverageNotes := []string{}
	if len(sessions) == 0 {
		coverageNotes = append(coverageNotes, noLocalSessionsNote)
	}

	artifact := &contract.StandupArtifact{
		DigestDate:       p.DigestDate,
		WindowStart:      p.WindowStart,
		WindowEnd:        p.DigestDate,
		SessionsWorked:   len(summaries),
		TotalCostUSD:     round4(total),
		AgentsSeen:       sortedKeys(sourceSet),
		SessionSummaries: summaries,
		RepoActivity:     []any{},
		Highlights:       []string{},
		InFlight:         []string{},
		AttentionItems:   []string{},
		Narrative:        "",
		CoverageNotes:    coverageNotes,
		Warnings:         append([]string{}, stats.Warnings...),
		GeneratedAt:      "",
	}
	return &contract.StandupResult{ContractVersion: contract.Version, Stats: stats, Artifact: artifact}, nil
}
