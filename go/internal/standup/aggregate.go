package standup

// aggregate.go — port of src/yeaboi/standup/aggregate.py:aggregate_standup,
// the standup.aggregate RPC entry point. Keep in lockstep: the Python module
// is the reference implementation; tests/parity/test_standup_parity.py diffs
// the two outputs whole. The result is built as ordered JSON (*pysem.Obj) so
// every object's key order — including member-keyed maps in members order —
// reaches the Python client exactly as the reference produces it.
//
// The two-pass adjudication protocol lives here exactly as in Python: the
// adjudicator seam is a stub that CAPTURES the cases habits builds and
// returns the (possibly empty) dropped_case_ids from the params;
// habits' own intersection-with-sent-ids and drop application do the rest.

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// RunStandupAggregate serves one standup.aggregate call. `params` is the
// ordered-decoded JSON params object. The returned object is the wire result;
// an error only for unusable params (bad date), mirroring the Python
// TypeError/ValueError surface.
func RunStandupAggregate(params *pysem.Obj) (*pysem.Obj, error) {
	bundle := bundleFromWire(params.Get("bundle"))
	members := strList(listOr(params, "members"))
	myName := pysem.Str(params.GetDefault("my_name", ""))
	config := configObj(params.Get("config"))

	// Identity closure: every member's own name, the user's hoisted extras,
	// then the emails observed on activity items (two-pass closure).
	aliasMap := buildAliasMap(members, myName, strList(listOr(params, "identity_extras")))
	enrichAliasesFromItems(aliasMap, bundle.Items)
	// Roster entries that are the standup user under another name — one
	// person, one card. Reported as `merged` for the engine's log line.
	myAliases := aliasMap[myName]
	merged := []string{}
	kept := members[:0:0]
	for _, m := range members {
		if m != myName && intersects(normalizeAuthor(m), myAliases) {
			merged = append(merged, m)
			delete(aliasMap, m)
			continue
		}
		kept = append(kept, m)
	}
	members = kept
	bundle = filterBundleToMembers(bundle, aliasMap)
	bundle, automationNotices := dropAutomatedActivity(bundle, config)
	enabled := map[string]bool{}
	for _, s := range strList(listOr(params, "enabled_sources")) {
		enabled[s] = true
	}
	categoryCoverage := coverageStates(enabled, bundle)

	previousReport := prevReportFromWire(params.Get("previous_report"))
	grouped := groupActivityByAuthor(bundle.Items, members, aliasMap)
	blockerSignals := detectBlockerSignals(grouped, previousReport)
	yesterday := yesterdayContext(
		previousReport,
		configObj(params.Get("transcript_corrections")),
		configObj(params.Get("corrected_fields")),
	)

	// Practice detection with the adjudicator seam replaced by the two-pass
	// protocol (see the package comment). habits owns the intersection with
	// sent ids, so a junk dropped id costs nothing.
	referenceGrouped := groupActivityByAuthor(bundle.ReferenceTickets, members, aliasMap)
	referenceItems := make([]*pysem.Obj, 0, len(bundle.ReferenceTickets))
	for _, item := range bundle.ReferenceTickets {
		referenceItems = append(referenceItems, projectedItem(item))
	}
	excused := map[[2]string]bool{}
	for _, pair := range pairList(params.Get("feedback_excused")) {
		excused[[2]string{pysem.Str(pair[0]), pysem.Str(pair[1])}] = true
	}
	droppedCaseIDs := strList(listOr(params, "dropped_case_ids"))
	var captured []adjudicationCase
	var adj adjudicator
	if pysem.Truthy(params.Get("want_adjudication")) || len(droppedCaseIDs) > 0 {
		adj = func(cases []adjudicationCase) []string {
			captured = append(captured, cases...)
			return droppedCaseIDs
		}
	}
	practices := detectPractices(
		grouped,
		config,
		categoryCoverage,
		previousReport,
		referenceGrouped,
		referenceItems,
		adj,
		func(rule, handle string) bool { return excused[[2]string{rule, handle}] },
	)

	sprint := configObj(params.Get("sprint"))
	today, err := time.Parse("2006-01-02", pysem.Str(params.GetDefault("today", "")))
	if err != nil {
		return nil, fmt.Errorf("invalid today date %q", pysem.Str(params.GetDefault("today", "")))
	}
	sprintLengthWeeks, _ := pysem.IntOrZero(sprint.GetDefault("sprint_length_weeks", json.Number("2")))
	progress := computeConfidence(
		pysem.Str(pysem.FirstTruthy(sprint.Get("sprint_name"), "")),
		pysem.Str(pysem.FirstTruthy(sprint.Get("start_date"), "")),
		int(sprintLengthWeeks),
		floatOf(sprint.GetDefault("capacity_points", json.Number("0"))),
		floatOf(sprint.GetDefault("completed_points", json.Number("0"))),
		bundle.Total("wip"),
		today,
		objList(params.Get("history")),
	)

	coverageMap := map[string]string{}
	for _, pair := range categoryCoverage {
		coverageMap[pair[0]] = pair[1]
	}

	result := pysem.EmptyObj()
	// Protocol envelope, not part of the pure function: the Python reference
	// implementation does not emit it, and the parity suite pops it before
	// diffing.
	result.Set("contract_version", int64(1))
	result.Set("members", anyList(members))
	result.Set("merged", anyList(merged))
	counts := []any{}
	for _, c := range bundle.Counts {
		counts = append(counts, []any{c.Source, c.N})
	}
	result.Set("counts", counts)
	result.Set("total_items", int64(len(bundle.Items)))
	result.Set("automation_notices", anyList(automationNotices))
	coveragePairs := []any{}
	for _, pair := range categoryCoverage {
		coveragePairs = append(coveragePairs, []any{pair[0], pair[1]})
	}
	result.Set("category_coverage", coveragePairs)
	groupedWire := pysem.EmptyObj()
	for _, name := range grouped.Names {
		items := []any{}
		for _, item := range grouped.Items[name] {
			items = append(items, item)
		}
		groupedWire.Set(name, items)
	}
	result.Set("grouped", groupedWire)
	result.Set("blocker_signals", blockerSignals)
	result.Set("yesterday", yesterday)
	result.Set("practices", practices)
	progressWire := pysem.EmptyObj()
	progressWire.Set("sprint_day", int64(progress.SprintDay))
	progressWire.Set("sprint_total_days", int64(progress.SprintTotalDays))
	progressWire.Set("confidence_pct", int64(progress.ConfidencePct))
	progressWire.Set("confidence_label", progress.ConfidenceLabel)
	progressWire.Set("confidence_rationale", progress.ConfidenceRationale)
	progressWire.Set("confidence_delta", int64(progress.ConfidenceDelta))
	progressWire.Set("confidence_trend", progress.ConfidenceTrend)
	result.Set("progress", progressWire)
	selfReported := map[string]bool{}
	for _, name := range strList(listOr(params, "self_reported_names")) {
		selfReported[name] = true
	}
	result.Set("member_skeletons", memberSkeletons(grouped, coverageMap, yesterday, selfReported))
	result.Set("fallback_team_summary", buildFallbackTeamSummary(bundle, progress))
	// Pass 2 (or adjudication off) returns no cases — the engine's re-invoke
	// is structurally single-shot, but an empty list makes it airtight.
	cases := []any{}
	if len(droppedCaseIDs) == 0 {
		for _, c := range captured {
			caseWire := pysem.EmptyObj()
			caseWire.Set("case_id", c.CaseID)
			caseWire.Set("subject", c.Subject)
			caseWire.Set("branch", c.Branch)
			caseWire.Set("paths", anyList(c.Paths))
			candidates := []any{}
			for _, cand := range c.Candidates {
				candidates = append(candidates, []any{cand[0], cand[1], cand[2]})
			}
			caseWire.Set("candidates", candidates)
			cases = append(cases, caseWire)
		}
	}
	result.Set("adjudication_cases", cases)
	return result, nil
}

// floatOf mirrors float(v) for the JSON scalar shapes the sprint params
// carry (json.Number after ordered decoding; float64/int64 in tests).
func floatOf(v any) float64 {
	switch t := v.(type) {
	case json.Number:
		f, _ := t.Float64()
		return f
	case float64:
		return t
	case int64:
		return float64(t)
	case int:
		return float64(t)
	}
	return 0
}

// configObj returns v as an ordered object, or an empty one (mirrors
// `inputs.get(key) or {}`).
func configObj(v any) *pysem.Obj {
	if o := pysem.AsObj(v); o != nil {
		return o
	}
	return pysem.EmptyObj()
}

func intersects(a, b map[string]bool) bool {
	for k := range a {
		if b[k] {
			return true
		}
	}
	return false
}
