package standup

// evidence.go — port of the deterministic helper block of
// src/yeaboi/standup/engine.py (identity closure, roster filter, grouping,
// evidence rows, fallback strings, member skeletons) plus the skeleton
// builder from src/yeaboi/standup/aggregate.py. Keep in lockstep: the Python
// module is the reference implementation; tests/parity/test_standup_parity.py
// diffs whole-pipeline output.

import (
	"sort"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// normalizeAuthor mirrors engine._normalize_author: lowercased + stripped;
// emails additionally yield their local part. Deliberately conservative —
// exact normalized strings only, no fuzzy matching.
func normalizeAuthor(s string) map[string]bool {
	s = pysem.Lower(pysem.Strip(s))
	if s == "" {
		return map[string]bool{}
	}
	out := map[string]bool{s: true}
	if strings.Contains(s, "@") {
		local := pysem.Strip(strings.SplitN(s, "@", 2)[0])
		if local != "" {
			out[local] = true
		}
	}
	return out
}

// buildAliasMap mirrors engine._build_alias_map with the identity extras
// hoisted (the git/tracker lookups happen in Python and travel as params).
// The map is only ever iterated through the members slice, so a plain map is
// order-safe.
func buildAliasMap(members []string, myName string, extraIdentities []string) map[string]map[string]bool {
	aliasMap := make(map[string]map[string]bool, len(members))
	for _, m := range members {
		aliasMap[m] = normalizeAuthor(m)
	}
	if myName != "" {
		if mine, ok := aliasMap[myName]; ok {
			for _, alias := range extraIdentities {
				if alias == "" {
					continue
				}
				for a := range normalizeAuthor(alias) {
					mine[a] = true
				}
			}
		}
	}
	return aliasMap
}

// enrichAliasesFromItems mirrors engine._enrich_aliases_from_items: grow every
// member's alias set with emails observed on activity items; two passes reach
// the name → email → email-local-part closure. Set-iteration order never
// reaches output (pure unioning), so ranging the Go maps is safe.
func enrichAliasesFromItems(aliasMap map[string]map[string]bool, items []*pysem.Obj) {
	emailIndex := map[string]map[string]bool{}
	for _, item := range items {
		email := pysem.Lower(pysem.Strip(strOr(item, "author_email")))
		if email == "" || !strings.Contains(email, "@") {
			continue
		}
		for alias := range normalizeAuthor(strOr(item, "author")) {
			if emailIndex[alias] == nil {
				emailIndex[alias] = map[string]bool{}
			}
			emailIndex[alias][email] = true
		}
	}
	if len(emailIndex) == 0 {
		return
	}
	for range [2]int{} { // second pass closes name → email → local-part chains
		for _, aliases := range aliasMap {
			for _, alias := range keysOf(aliases) { // list(aliases): snapshot before growing
				for email := range emailIndex[alias] {
					for a := range normalizeAuthor(email) {
						aliases[a] = true
					}
				}
			}
		}
	}
}

func keysOf(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	return out
}

// groupActivityByAuthor mirrors engine._group_activity_by_author. The reverse
// index gives the FIRST member (members order) on an alias collision; within
// one member the inner set order cannot matter (all aliases map to the same
// member).
func groupActivityByAuthor(items []*pysem.Obj, members []string, aliasMap map[string]map[string]bool) *Grouped {
	rev := map[string]string{}
	for _, m := range members {
		aliases := aliasMap[m]
		if aliases == nil {
			aliases = normalizeAuthor(m)
		}
		for alias := range aliases {
			if _, taken := rev[alias]; !taken {
				rev[alias] = m
			}
		}
	}
	grouped := newGrouped(members)
	for _, item := range items {
		author := pysem.Strip(strOr(item, "author"))
		for alias := range normalizeAuthor(author) {
			if member, ok := rev[alias]; ok {
				grouped.Items[member] = append(grouped.Items[member], projectedItem(item))
				break
			}
		}
	}
	return grouped
}

// rebuildBundle mirrors engine._rebuild_bundle: a copy holding only `items`,
// per-source counts recomputed over the surviving items, everything else
// carried (dropping partial_sources/reference_tickets here used to silently
// weaken warnings and the practice rules).
func rebuildBundle(bundle *Bundle, items []*pysem.Obj) *Bundle {
	perSource := map[string]int64{}
	for _, item := range items {
		perSource[strOr(item, "source")]++
	}
	counts := make([]SourceCount, 0, len(bundle.Counts))
	for _, c := range bundle.Counts {
		counts = append(counts, SourceCount{Source: c.Source, N: perSource[c.Source]})
	}
	return &Bundle{
		Items:            items,
		Counts:           counts,
		Errors:           append([]SourcePair{}, bundle.Errors...),
		PartialSources:   append([]SourcePair{}, bundle.PartialSources...),
		Skipped:          append([]SourcePair{}, bundle.Skipped...),
		ReferenceTickets: append([]*pysem.Obj{}, bundle.ReferenceTickets...),
	}
}

// filterBundleToMembers mirrors engine._filter_bundle_to_members: only
// activity attributable to the authoritative saved roster survives.
func filterBundleToMembers(bundle *Bundle, aliasMap map[string]map[string]bool) *Bundle {
	known := map[string]bool{}
	for _, aliases := range aliasMap {
		for a := range aliases {
			known[a] = true
		}
	}
	items := []*pysem.Obj{}
	for _, item := range bundle.Items {
		for alias := range normalizeAuthor(strOr(item, "author")) {
			if known[alias] {
				items = append(items, item)
				break
			}
		}
	}
	return rebuildBundle(bundle, items)
}

// dropAutomatedActivity mirrors engine._drop_automated_activity: remove
// service-hook/bot activity posted under members' identities; exclusions
// surface as notice lines, never silently.
func dropAutomatedActivity(bundle *Bundle, config *pysem.Obj) (*Bundle, []string) {
	handling := pysem.Str(config.GetDefault("automation_handling", "exclude"))
	if handling == "off" {
		return bundle, []string{}
	}
	kept, clusters := partitionAutomated(bundle.Items, parseCustomMarkers(strOr(config, "automation_markers")))
	if len(clusters) == 0 {
		return bundle, []string{}
	}
	return rebuildBundle(bundle, kept), noticeLines(clusters)
}

// memberLinks mirrors engine._member_links: distinct (label, url) references,
// deduped by URL preserving order, capped at 6.
func memberLinks(acts []*pysem.Obj) [][2]string {
	seen := map[string]bool{}
	links := [][2]string{}
	for _, a := range acts {
		url := pysem.Strip(strOr(a, "url"))
		if url == "" || seen[url] {
			continue
		}
		seen[url] = true
		label := pysem.Strip(strOr(a, "key"))
		if label == "" {
			label = runeClip(strOr(a, "title"), 40)
		}
		links = append(links, [2]string{label, url})
		if len(links) >= 6 {
			break
		}
	}
	return links
}

// runeClip mirrors Python's s[:n] (character slicing).
func runeClip(s string, n int) string {
	runes := []rune(s)
	if len(runes) <= n {
		return s
	}
	return string(runes[:n])
}

// nestPRCommits mirrors engine._nest_pr_commits: fold commits under the PR
// they belong to (per repository, by PR number from the commit title, falling
// back to the merge subject's source branch); unmatched commits stay put. PR
// dicts are copied (gaining "children") rather than mutated.
func nestPRCommits(acts []*pysem.Obj) []*pysem.Obj {
	out := []*pysem.Obj{}
	prsByNumber := map[[2]string]*pysem.Obj{}
	prsByBranch := map[[2]string]*pysem.Obj{}
	for _, a := range acts {
		if strOr(a, "kind") == "pr" {
			a = a.Clone()
			a.Set("children", []any{})
			repo := strOr(a, "repository")
			if pysem.Truthy(a.Get("pr_id")) {
				prsByNumber[[2]string{repo, pysem.Str(a.Get("pr_id"))}] = a
			}
			if pysem.Truthy(a.Get("branch")) {
				prsByBranch[[2]string{repo, pysem.Str(a.Get("branch"))}] = a
			}
		}
		out = append(out, a)
	}
	if len(prsByNumber) == 0 && len(prsByBranch) == 0 {
		return acts
	}

	kept := []*pysem.Obj{}
	for _, a := range out {
		if strOr(a, "kind") != "commit" {
			kept = append(kept, a)
			continue
		}
		repo := strOr(a, "repository")
		title := strOr(a, "title")
		var parent *pysem.Obj
		for _, pattern := range prNumberRes {
			if match := pattern.FindStringSubmatch(title); match != nil {
				if parent = prsByNumber[[2]string{repo, match[1]}]; parent != nil {
					break
				}
			}
			parent = nil
		}
		if parent == nil {
			if match := mergeBranchRe.FindStringSubmatch(title); match != nil {
				// GitHub merge subjects say "from <owner>/<branch>" while the
				// PR item's branch is the bare head ref — try both spellings.
				ref := match[1]
				parent = prsByBranch[[2]string{repo, ref}]
				if parent == nil && strings.Contains(ref, "/") {
					parent = prsByBranch[[2]string{repo, strings.SplitN(ref, "/", 2)[1]}]
				}
			}
		}
		if parent == nil {
			kept = append(kept, a)
		} else {
			children, _ := parent.Get("children").([]any)
			parent.Set("children", append(children, a))
		}
	}
	return kept
}

// memberEvidence mirrors engine._member_evidence, emitting rows directly in
// the wire shape (aggregate._evidence_to_wire's key order). Rows are ordered
// newest-first (stable, so equal stamps keep collector order; empty-stamp
// carried WIP folds last), deduped in that order — merge commits keyed on
// their PR number so both sides of a merge collapse to one row.
func memberEvidence(acts []*pysem.Obj, cap int, prefixes, workItemIDs map[string]bool) []any {
	ordered := append([]*pysem.Obj{}, acts...)
	sort.SliceStable(ordered, func(i, j int) bool {
		return strOr(ordered[i], "timestamp") > strOr(ordered[j], "timestamp")
	})
	seen := map[string]bool{}
	rows := []any{}
	for _, a := range ordered {
		url := pysem.Strip(strOr(a, "url"))
		title := strOr(a, "title")
		prNumber := ""
		if strOr(a, "kind") == "commit" && isMergeSubject(title) {
			prNumber = prReference(title)
		}
		var dedupe string
		if prNumber != "" {
			dedupe = "pr-merge:" + strOr(a, "repository") + ":" + prNumber
		} else if url != "" {
			// f"review|{url}" if a.get("kind") == "review" else url — a review
			// legitimately shares its URL with the work it points at (an AzDO
			// vote row and the member's own PR row), so reviews dedupe in
			// their own URL namespace; other kinds share the plain-URL one so
			// a ticket's latest event still wins.
			if strOr(a, "kind") == "review" {
				dedupe = "review|" + url
			} else {
				dedupe = url
			}
		} else {
			// f"{a.get('kind','')}:{a.get('key','')}:{a.get('title','')}" —
			// str() of the raw values, so a present-but-null field says "None".
			dedupe = pysem.Str(a.GetDefault("kind", "")) + ":" + pysem.Str(a.GetDefault("key", "")) + ":" +
				pysem.Str(a.GetDefault("title", ""))
		}
		if seen[dedupe] {
			continue
		}
		seen[dedupe] = true

		row := pysem.EmptyObj()
		row.Set("kind", strOr(a, "kind"))
		row.Set("key", pysem.Strip(strOr(a, "key")))
		// Jira update/comment titles are action phrases; the clean ticket
		// summary travels separately and wins.
		summaryOrTitle := pysem.Str(pysem.FirstTruthy(a.Get("summary"), a.Get("title"), ""))
		row.Set("title", pysem.Strip(summaryOrTitle))
		row.Set("url", url)
		row.Set("repository", pysem.Strip(strOr(a, "repository")))
		row.Set("status", pysem.Strip(strOr(a, "status")))
		row.Set("timestamp", pysem.Strip(strOr(a, "timestamp")))
		if children, ok := a.Get("children").([]any); ok && len(children) > 0 {
			childObjs := make([]*pysem.Obj, 0, len(children))
			for _, c := range children {
				if co := pysem.AsObj(c); co != nil {
					childObjs = append(childObjs, co)
				}
			}
			row.Set("children", memberEvidence(childObjs, 6, prefixes, workItemIDs))
		} else {
			row.Set("children", []any{})
		}
		row.Set("issue_type", pysem.Strip(strOr(a, "issue_type")))
		row.Set("parent_key", pysem.Strip(strOr(a, "parent_key")))
		row.Set("subtask", pysem.Truthy(a.Get("subtask")))
		if !isTrackerKind(strOr(a, "kind")) {
			keys := displayTicketKeys(
				strOr(a, "title"),
				strOr(a, "branch"),
				strOr(a, "body"),
				prefixes,
				workItemIDs,
				strList(listOr(a, "work_item_ids")),
			)
			row.Set("ticket_keys", anyList(keys))
		} else {
			row.Set("ticket_keys", []any{})
		}
		rows = append(rows, row)
		if len(rows) >= cap {
			break
		}
	}
	return rows
}

func anyList(values []string) []any {
	out := make([]any, 0, len(values))
	for _, v := range values {
		out = append(out, v)
	}
	return out
}

// referenceGates mirrors engine._reference_gates — the report-wide
// ticket-reference gates, computed once over every member's activity.
func referenceGates(grouped *Grouped) (prefixes, workItemIDs map[string]bool) {
	gateItems := grouped.AllItems()
	return trackerPrefixes(gateItems), trackerWorkItemIDs(gateItems)
}

// memberSource mirrors engine._member_source.
func memberSource(hasSelfReport, hasActivity bool) string {
	if hasSelfReport {
		if hasActivity {
			return "combined"
		}
		return "self-reported"
	}
	return "inferred"
}

// fallbackCodeSummary mirrors engine._fallback_code_summary.
func fallbackCodeSummary(acts []*pysem.Obj, coverage string) string {
	code := []*pysem.Obj{}
	for _, a := range acts {
		if isCodeActivity(a) {
			code = append(code, a)
		}
	}
	if len(code) == 0 {
		return emptySummary(categoryCode, coverage)
	}
	titles := []string{}
	for _, a := range code {
		if pysem.Truthy(a.Get("title")) {
			titles = append(titles, strOr(a, "title"))
		}
	}
	joined := runeClip(strings.Join(titles, "; "), 400)
	if joined == "" {
		return "Code activity detected in the selected repositories."
	}
	return joined
}

// fallbackCategorySummary mirrors engine._fallback_category_summary.
func fallbackCategorySummary(category string, acts []*pysem.Obj, coverage string) string {
	if category == categoryCode {
		return fallbackCodeSummary(acts, coverage)
	}
	if len(acts) == 0 {
		return emptySummary(category, coverage)
	}
	fresh := joinTitles(acts, func(a *pysem.Obj) bool { return strOr(a, "kind") != "wip" })
	if fresh != "" {
		return fresh
	}
	wip := joinTitles(acts, func(a *pysem.Obj) bool { return strOr(a, "kind") == "wip" })
	if wip != "" {
		return runeClip("Continuing work on: "+wip, 400)
	}
	return emptySummary(category, coverage)
}

// joinTitles mirrors the `"; ".join(str(title) for …)[:400]` idiom.
func joinTitles(acts []*pysem.Obj, keep func(*pysem.Obj) bool) string {
	titles := []string{}
	for _, a := range acts {
		if pysem.Truthy(a.Get("title")) && keep(a) {
			titles = append(titles, strOr(a, "title"))
		}
	}
	return runeClip(strings.Join(titles, "; "), 400)
}

// fallbackSummary mirrors engine._fallback_summary: fresh activity first
// (two titles + a count), then WIP, then the canonical empty line.
func fallbackSummary(acts []*pysem.Obj) string {
	fresh := []string{}
	for _, a := range acts {
		if pysem.Truthy(a.Get("title")) && strOr(a, "kind") != "wip" {
			fresh = append(fresh, pysem.Str(a.Get("title")))
		}
	}
	if len(fresh) > 0 {
		head := strings.Join(fresh[:minInt(2, len(fresh))], "; ")
		more := len(fresh) - 2
		if more > 0 {
			return runeClip(head+"; and "+pysem.Str(int64(more))+" more", 400)
		}
		return runeClip(head, 400)
	}
	wipTitles := []string{}
	for _, a := range acts {
		if pysem.Truthy(a.Get("title")) && strOr(a, "kind") == "wip" {
			wipTitles = append(wipTitles, pysem.Str(a.Get("title")))
		}
	}
	wip := runeClip(strings.Join(wipTitles, "; "), 400)
	if wip != "" {
		return runeClip("Continuing work on: "+wip, 400)
	}
	return "No activity detected."
}

// pyListRepr mirrors str(list-of-strings) — "['a', 'b']" — for the one place
// a yesterday entry's "corrected"/"corrections" list is stringified.
func pyListRepr(values []any) string {
	parts := make([]string, 0, len(values))
	for _, v := range values {
		if s, ok := v.(string); ok {
			parts = append(parts, pysem.ReprStr(s))
		} else {
			parts = append(parts, pysem.ReprAny(v))
		}
	}
	return "[" + strings.Join(parts, ", ") + "]"
}

// fallbackProgressNote mirrors engine._fallback_progress_note: yesterday's
// ticket keys ∩ today's — the intersection itself is the gate.
func fallbackProgressNote(yesterdayEntry *pysem.Obj, acts []*pysem.Obj) string {
	parts := []string{}
	if yesterdayEntry != nil {
		for _, k := range yesterdayEntry.Keys() {
			v := yesterdayEntry.Get(k)
			if list, ok := v.([]any); ok {
				parts = append(parts, pyListRepr(list))
			} else {
				parts = append(parts, pysem.Str(v))
			}
		}
	}
	yesterdayText := strings.Join(parts, " ")
	yesterdayKeys := map[string]bool{}
	for _, key := range findTicketKeys(yesterdayText) {
		yesterdayKeys[key] = true
	}
	todayKeys := map[string]bool{}
	for _, a := range acts {
		todayKeys[strOr(a, "key")] = true
	}
	carried := []string{}
	for key := range yesterdayKeys {
		if todayKeys[key] {
			carried = append(carried, key)
		}
	}
	sort.Strings(carried)
	if len(carried) == 0 {
		return ""
	}
	shown := carried[:minInt(3, len(carried))]
	return "Still on " + strings.Join(shown, ", ") + " (carried over from the last standup)."
}

// fallbackOutlook mirrors engine._fallback_outlook.
func fallbackOutlook(acts []*pysem.Obj) string {
	wipTitles := []string{}
	for _, a := range acts {
		if strOr(a, "kind") == "wip" && pysem.Truthy(a.Get("title")) {
			wipTitles = append(wipTitles, strOr(a, "title"))
		}
	}
	if len(wipTitles) == 0 {
		return ""
	}
	shown := wipTitles[:minInt(2, len(wipTitles))]
	return runeClip("Likely continuing: "+strings.Join(shown, "; ")+".", 300)
}

// buildFallbackTeamSummary mirrors engine._build_fallback_team_summary.
func buildFallbackTeamSummary(bundle *Bundle, progress *sprintProgress) string {
	if bundle.Total() == 0 {
		return "No activity detected in the collection window. Sprint status: " + progress.ConfidenceLabel + "."
	}
	return "Sprint status: " + progress.ConfidenceLabel + "."
}

// memberSkeletons mirrors aggregate._member_skeletons — the deterministic
// (non-prose) half of every MemberUpdate, in member order, wire-shaped.
func memberSkeletons(grouped *Grouped, coverage map[string]string, yesterday *pysem.Obj, selfReportedNames map[string]bool) []any {
	prefixes, workItemIDs := referenceGates(grouped)
	skeletons := []any{}
	for _, name := range grouped.Names {
		acts := grouped.Items[name]
		split := splitActivity(acts)

		categoryBlock := func(category string, evidenceActs []*pysem.Obj) *pysem.Obj {
			cov, ok := coverage[category]
			if !ok {
				cov = covered
			}
			block := pysem.EmptyObj()
			block.Set("summary", fallbackCategorySummary(category, split[category], cov))
			block.Set("links", linksToWire(memberLinks(split[category])))
			block.Set("count", int64(len(split[category])))
			// 30 mirrors state.MEMBER_EVIDENCE_CAP, which is engine._member_evidence's
			// cap default — enough for the web timeline to show the real shape of a
			// busy day. Python's gap_taxonomy reads the same constant to tell "at the
			// cap" from "merely busy", so change all three together.
			block.Set("evidence", memberEvidence(evidenceActs, 30, prefixes, workItemIDs))
			return block
		}

		var yesterdayEntry *pysem.Obj
		if yesterday != nil {
			yesterdayEntry = pysem.AsObj(yesterday.Get(name))
		}
		sk := pysem.EmptyObj()
		sk.Set("name", name)
		sk.Set("source", memberSource(selfReportedNames[name], len(acts) > 0))
		sk.Set("links", linksToWire(memberLinks(acts)))
		sk.Set("activity_count", int64(len(acts)))
		sk.Set("fallback_summary", fallbackSummary(acts))
		sk.Set("fallback_progress_note", fallbackProgressNote(yesterdayEntry, acts))
		sk.Set("fallback_outlook", fallbackOutlook(acts))
		sk.Set("ticketing", categoryBlock(categoryTicketing, split[categoryTicketing]))
		// Commits fold under their PR for evidence only — links/counts keep
		// the flat view, exactly as the Python builders do.
		sk.Set("code", categoryBlock(categoryCode, nestPRCommits(split[categoryCode])))
		sk.Set("documentation", categoryBlock(categoryDocumentation, split[categoryDocumentation]))
		skeletons = append(skeletons, sk)
	}
	return skeletons
}

func linksToWire(links [][2]string) []any {
	out := make([]any, 0, len(links))
	for _, pair := range links {
		out = append(out, []any{pair[0], pair[1]})
	}
	return out
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
