// insights.go — port of src/yeaboi/standup/insights.py. Keep in lockstep: the Python module is the reference implementation; tests/parity/test_standup_parity.py diffs whole-pipeline output.
//
// Deterministic day-over-day standup insights: blocker evidence + yesterday
// context. Two jobs, both pure (no I/O, no LLM):
//
//   - detectBlockerSignals scans the grouped activity for evidence a member is
//     blocked — a ticket sitting in a blocked-ish column, a PR still open since
//     the previous standup, or unusually heavy comment traffic on one ticket.
//     The signals are passed to the LLM as verified evidence it must reflect in
//     "blockers" and, in the no-LLM fallback, become the blockers text
//     directly.
//   - yesterdayContext distills the previous standup report (loaded from
//     StandupStore.get_previous_report) into per-member comparison context so
//     the LLM can write a "since last standup" progress note and the day-ahead
//     outlook.
//
// Precision over recall, mirroring automation.py: a false "you look blocked"
// erodes trust faster than a missed blocker, so statuses match a narrow list,
// the cross-standup PR rule needs the exact URL to reappear, and comment churn
// needs several comments from several people before it fires.
//
// Not ported: insights.corrected_members. It parses artifact edit paths with
// yeaboi.artifacts.paths, which never crosses the wire — the aggregate seam
// pre-parses the edit log in Python and hands yesterdayContext the already
// resolved correctedFields mapping instead (the Python `corrections` keyword
// therefore has no Go counterpart).

package standup

import (
	"fmt"
	"sort"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// insightsStatusKinds mirrors insights._STATUS_KINDS: ticket/work-item kinds
// whose "status" is a board column; Jira changelog "update" items carry the
// *destination* column, so a move to Blocked counts.
var insightsStatusKinds = map[string]bool{"issue": true, "wip": true, "work_item": true, "update": true}

// Narrow blocked-ish vocabulary. Exact matches plus two prefix families —
// "waiting for X" / "waiting on X" are blocked columns, but a bare "waiting"
// prefix would also catch benign columns like "Waiting Deploy Queue"; the
// space-suffixed prefixes keep it attribution-shaped.
var insightsBlockedExact = map[string]bool{"blocked": true, "on hold": true, "impeded": true, "stuck": true, "paused": true}

var insightsBlockedPrefixes = []string{"blocked", "on hold", "waiting for", "waiting on"}

// Comment-churn thresholds: N comment items on one ticket key from at least
// M distinct members before "heavy discussion" fires.
const (
	insightsChurnMinComments = 4
	insightsChurnMinMembers  = 2
)

const (
	insightsMaxSignalsPerMember = 3
	insightsTitleClip           = 60  // keep each signal line short enough for a chip/bullet
	insightsYesterdayClip       = 300 // prompt-budget cap per carried-over field
)

// insightsIsBlockedStatus mirrors insights._is_blocked_status.
func insightsIsBlockedStatus(status string) bool {
	// Python: " ".join(status.strip().lower().split()) — str.split() with no
	// argument splits on runs of Python whitespace (pysem.IsSpace).
	normalized := strings.Join(strings.FieldsFunc(pysem.Lower(pysem.Strip(status)), pysem.IsSpace), " ")
	if normalized == "" {
		return false
	}
	if insightsBlockedExact[normalized] {
		return true
	}
	for _, prefix := range insightsBlockedPrefixes {
		if strings.HasPrefix(normalized, prefix) {
			return true
		}
	}
	return false
}

// insightsClip mirrors insights._clip. Python indexes and measures by
// codepoint, so length and slicing are over runes; rstrip is a right-trim of
// Python whitespace and the ellipsis is U+2026.
func insightsClip(text string, limit int) string {
	text = pysem.Strip(text)
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return strings.TrimRightFunc(string(runes[:limit-1]), pysem.IsSpace) + "…"
}

// insightsItemLabel mirrors insights._item_label: human-readable handle for an
// item — ticket key + clipped title when available.
func insightsItemLabel(item *pysem.Obj) string {
	key := strippedOr(item, "key")
	title := insightsClip(strOr(item, "title"), insightsTitleClip)
	if key != "" && title != "" {
		return fmt.Sprintf("%s '%s'", key, title)
	}
	// Python: key or title or "an item"
	if key != "" {
		return key
	}
	if title != "" {
		return title
	}
	return "an item"
}

// insightsPreviousPrUrls mirrors insights._previous_pr_urls: per-member set of
// code-evidence URLs from the previous standup report.
func insightsPreviousPrUrls(previousReport *PrevReport) map[string]map[string]bool {
	urls := map[string]map[string]bool{}
	if previousReport == nil {
		return urls
	}
	for _, member := range previousReport.Members {
		memberUrls := map[string]bool{}
		for _, pair := range member.CodeLinks {
			if pair[1] != "" {
				memberUrls[pair[1]] = true
			}
		}
		// Legacy reports (pre category split) carried everything in `links`.
		for _, pair := range member.Links {
			if pair[1] != "" {
				memberUrls[pair[1]] = true
			}
		}
		if len(memberUrls) > 0 {
			urls[member.Name] = memberUrls
		}
	}
	return urls
}

// detectBlockerSignals mirrors insights.detect_blocker_signals: per-member
// deterministic blocker evidence strings (members with none are absent).
//
// grouped is the engine's _group_activity_by_author output: items keep
// kind, title, status, source, key, url, repository — everything the three
// rules need. The returned ordered object iterates in grouped member order;
// each value is a []any of string so it marshals as a JSON array.
func detectBlockerSignals(grouped *Grouped, previousReport *PrevReport) *pysem.Obj {
	prevUrls := insightsPreviousPrUrls(previousReport)

	// Rule 3 pre-pass — comment traffic per ticket key across the whole team.
	// The member grouping IS the author dimension: a key discussed by many
	// people appears in many members' comment lists. (These maps are lookups
	// only — no ordered output is derived from ranging them.)
	commentCounts := map[string]int{}
	commentMembers := map[string]map[string]bool{}
	for _, name := range grouped.Names {
		for _, item := range grouped.Items[name] {
			// Python compares the raw value: item.get("kind") != "comment".
			if kind, ok := item.Get("kind").(string); !ok || kind != "comment" {
				continue
			}
			key := strippedOr(item, "key")
			if key == "" {
				continue
			}
			commentCounts[key]++
			if commentMembers[key] == nil {
				commentMembers[key] = map[string]bool{}
			}
			commentMembers[key][name] = true
		}
	}
	churnKeys := map[string]bool{}
	for key, count := range commentCounts {
		if count >= insightsChurnMinComments && len(commentMembers[key]) >= insightsChurnMinMembers {
			churnKeys[key] = true
		}
	}

	signals := pysem.EmptyObj()
	for _, name := range grouped.Names {
		items := grouped.Items[name]
		found := []string{}
		seenHandles := map[string]bool{} // dedupe by ticket key / URL across rules

		for _, item := range items {
			kind := strOr(item, "kind")
			status := strOr(item, "status")

			// Rule 1: ticket sitting in (or just moved to) a blocked-ish column.
			if insightsStatusKinds[kind] && insightsIsBlockedStatus(status) {
				// Python: str(item.get("key") or item.get("url") or item.get("title") or "")
				handle := pysem.Str(pysem.FirstTruthy(item.Get("key"), item.Get("url"), item.Get("title"), ""))
				if handle != "" && seenHandles[handle] {
					continue
				}
				seenHandles[handle] = true
				found = append(found, fmt.Sprintf("%s is in %s", insightsItemLabel(item), pysem.Strip(status)))
				continue
			}

			// Rule 2: a PR that was already evidence in the previous standup and
			// is still open today — unmerged across two standups.
			if kind == "pr" && pysem.Lower(status) == "open" {
				url := strOr(item, "url")
				if url != "" && prevUrls[name][url] && !seenHandles[url] {
					seenHandles[url] = true
					found = append(found, fmt.Sprintf("PR %s still open since the last standup", insightsItemLabel(item)))
				}
			}
		}

		// Rule 3: heavy discussion — attributed to the member who OWNS the
		// ticket (holds an issue/wip/work_item with that key), not to every
		// commenter; an orphan key nobody owns is dropped (precision first).
		for _, item := range items {
			kind, ok := item.Get("kind").(string)
			if !ok || (kind != "issue" && kind != "wip" && kind != "work_item") {
				continue
			}
			key := strippedOr(item, "key")
			if churnKeys[key] && !seenHandles[key] {
				seenHandles[key] = true
				found = append(found, fmt.Sprintf("Heavy discussion on %s (%d comments)", key, commentCounts[key]))
			}
		}

		if len(found) > 0 {
			if len(found) > insightsMaxSignalsPerMember {
				found = found[:insightsMaxSignalsPerMember]
			}
			values := make([]any, 0, len(found))
			for _, s := range found {
				values = append(values, s)
			}
			signals.Set(name, values)
		}
	}
	return signals
}

// yesterdayContext mirrors insights.yesterday_context: per-member comparison
// context distilled from the previous standup report.
//
// Returns {name: {"summary": …, "blockers": …, "outlook": …}} with each value
// clipped to keep the LLM prompt bounded; members with a fully empty previous
// update are omitted. An empty object when there is no previous report.
//
// Two different kinds of correction reach this function, and they stay
// separate because they mean different things to the prompt:
//
// correctedFields is the already-parsed form of the previous run's **edit
// log** — fields the team fixed by hand, as an ordered {name: [field, …]}
// object. The aggregate seam pre-parses the edit log in Python because parsing
// needs yeaboi.artifacts.paths, which the wire never carries (so the Python
// `corrections` keyword has no counterpart here). The affected members carry a
// "corrected" list naming those fields, sorted and deduped. The corrected text
// itself already feeds forward for free — a corrected row supersedes its
// parent in get_previous_run — but that alone only stops the model repeating a
// wrong *fact*. The flag is what lets the prompt say the team looked at this
// and disagreed.
//
// transcriptCorrections is work a member stated in the last standup MEETING
// that the last report missed (see standup/transcript_review.py), as an
// ordered {name: [correction, …]} object. They are fed FORWARD rather than
// written back into yesterday's stored report: standup_history is an
// append-only record of what was said at the time, and rewriting it to make
// today tidy would falsify that record. A member with a transcript correction
// but no previous update still gets an entry — the correction is the only
// thing we know about their yesterday.
func yesterdayContext(previousReport *PrevReport, transcriptCorrections *pysem.Obj, correctedFields *pysem.Obj) *pysem.Obj {
	context := pysem.EmptyObj()
	if previousReport != nil {
		for _, member := range previousReport.Members {
			entry := pysem.EmptyObj()
			entry.Set("summary", insightsClip(member.Summary, insightsYesterdayClip))
			entry.Set("blockers", insightsClip(member.Blockers, insightsYesterdayClip))
			entry.Set("outlook", insightsClip(member.Outlook, insightsYesterdayClip))
			// Python: if any(entry.values()) — the entry holds exactly the
			// three strings at this point, so truthiness = any non-empty.
			anyValue := false
			for _, k := range entry.Keys() {
				if entry.Get(k).(string) != "" {
					anyValue = true
					break
				}
			}
			if anyValue {
				if correctedFields != nil && correctedFields.Has(member.Name) {
					// Python: sorted(set(fixed[member.name])) — dedupe, then sort.
					fields, _ := correctedFields.Get(member.Name).([]any)
					seen := map[string]bool{}
					unique := []string{}
					for _, f := range fields {
						s := pysem.Str(f)
						if !seen[s] {
							seen[s] = true
							unique = append(unique, s)
						}
					}
					sort.Strings(unique)
					corrected := make([]any, 0, len(unique))
					for _, s := range unique {
						corrected = append(corrected, s)
					}
					entry.Set("corrected", corrected)
				}
				context.Set(member.Name, entry)
			}
		}
	}
	if transcriptCorrections != nil {
		for _, name := range transcriptCorrections.Keys() {
			items, _ := transcriptCorrections.Get(name).([]any)
			// Filter AFTER clipping: a whitespace-only string is truthy but
			// clips to nothing, and an empty "correction" in the prompt is
			// worse than none. Python builds the full list then slices [:cap]
			// (_MAX_SIGNALS_PER_MEMBER is reused as the cap).
			clipped := []any{}
			for _, item := range items {
				if c := pysem.Strip(insightsClip(pysem.Str(item), insightsYesterdayClip)); c != "" {
					clipped = append(clipped, c)
				}
			}
			if len(clipped) > insightsMaxSignalsPerMember {
				clipped = clipped[:insightsMaxSignalsPerMember]
			}
			if len(clipped) == 0 {
				continue
			}
			// Python: context.setdefault(name, {"summary": "", "blockers": "", "outlook": ""})
			var entry *pysem.Obj
			if context.Has(name) {
				entry = context.Get(name).(*pysem.Obj)
			} else {
				entry = pysem.EmptyObj()
				entry.Set("summary", "")
				entry.Set("blockers", "")
				entry.Set("outlook", "")
				context.Set(name, entry)
			}
			entry.Set("corrections", clipped)
		}
	}
	return context
}
