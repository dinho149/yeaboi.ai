package standup

// habits.go — port of src/yeaboi/standup/habits.py. Keep in lockstep: the
// Python module is the reference implementation;
// tests/parity/test_standup_parity.py diffs whole-pipeline output.
//
// The suppress-only invariant survives the port unchanged: the adjudicator
// seam and the feedback excuser can only ever REMOVE a report, so neither can
// make this module louder than its deterministic rules. The adjudicator here
// is always the aggregate's capture-and-drop stub (the LLM lives in Python,
// reached via the two-pass protocol — see aggregate.go).

import (
	"crypto/sha1" // #nosec G401 — an id, not a secret (mirrors Python change_handle)
	"fmt"
	"sort"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

const (
	ruleUntrackedWork   = "untracked-work"
	ruleUntrackedDocs   = "untracked-docs"
	ruleBoardNotUpdated = "board-not-updated"
	ruleWipSprawl       = "wip-sprawl"
	ruleLargeChange     = "large-change"
	ruleNoPullRequest   = "no-pull-request"
	ruleCommitMessages  = "commit-messages"
)

var allRules = []string{
	ruleUntrackedWork,
	ruleUntrackedDocs,
	ruleBoardNotUpdated,
	ruleWipSprawl,
	ruleLargeChange,
	ruleNoPullRequest,
	ruleCommitMessages,
}

// Short labels for the chip in every surface; the detail sentence carries the nudge.
var ruleTitles = map[string]string{
	ruleUntrackedWork:   "Untracked work",
	ruleUntrackedDocs:   "Untracked docs",
	ruleBoardNotUpdated: "Board out of date",
	ruleWipSprawl:       "Spread thin",
	ruleLargeChange:     "Oversized change",
	ruleNoPullRequest:   "Bypassed review",
	ruleCommitMessages:  "Thin commit messages",
}

const (
	habitsMaxSignalsPerMember = 3
	habitsTitleClip           = 60
	habitsEvidencePerSignal   = 4 // links attached to one signal

	habitsMinSubjectChars = 12 // a normalised subject shorter than this says nothing either

	habitsLargeChangeFiles       = 40
	habitsWipSprawlTickets       = 4
	habitsLooseCommits           = 3
	habitsLowInformationCommits  = 3
	habitsAdjudicationTextClip   = 600
	habitsAdjudicationCandidates = 3
)

// Rule 3 (board-not-updated): EXACT matches only — no prefix families.
var habitsTodoStatuses = map[string]bool{
	"to do": true, "todo": true, "to-do": true, "backlog": true, "new": true,
	"open": true, "ready": true, "ready for development": true,
	"selected for development": true, "not started": true,
}

// Rule 4 (wip-sprawl): "In Review" is deliberately absent.
var habitsInProgressStatuses = map[string]bool{
	"in progress": true, "in-progress": true, "inprogress": true, "doing": true,
	"active": true, "committed": true, "in development": true, "started": true,
}

// Kinds whose status describes where a ticket sits AND which are credited to
// the person holding it ("update" excluded on purpose).
var habitsHeldTicketKinds = map[string]bool{"issue": true, "wip": true, "work_item": true}

// Rule 6 (large-change): generated/vendored/lockfile bulk.
var habitsGeneratedFilenames = map[string]bool{
	"uv.lock": true, "package-lock.json": true, "yarn.lock": true,
	"pnpm-lock.yaml": true, "poetry.lock": true, "cargo.lock": true, "gemfile.lock": true,
}
var habitsGeneratedDirectories = map[string]bool{
	"dist": true, "build": true, "vendor": true, "node_modules": true, "__snapshots__": true, "generated": true,
}
var habitsGeneratedSuffixes = []string{".min.js", ".min.css", ".snap", ".lock", ".map"}

// Rule 7 (commit-messages): subjects that name no outcome, exact after
// normalisation.
var habitsLowInformationSubjects = map[string]bool{
	"fix": true, "fixes": true, "fixed": true, "fixup": true, "wip": true,
	"update": true, "updates": true, "updated": true, "change": true, "changes": true,
	"minor": true, "cleanup": true, "clean up": true, "tweak": true, "tweaks": true,
	"refactor": true, "test": true, "tests": true, "temp": true, "tmp": true,
	"stuff": true, "misc": true, "more": true, "asdf": true, "x": true,
	".": true, "..": true, "...": true,
}

// Excuser mirrors habits.Excuser — suppress-only by shape.
type excuser func(rule, handle string) bool

// adjudicationCase mirrors habits.AdjudicationCase.
type adjudicationCase struct {
	CaseID     string
	Subject    string
	Branch     string
	Paths      []string
	Candidates [][3]string // (key, title, clipped text)
}

// adjudicator mirrors habits.Adjudicator: returns the ids to DROP.
type adjudicator func(cases []adjudicationCase) []string

// habitsEnabled mirrors habits.enabled.
func habitsEnabled(config *pysem.Obj) bool {
	v := pysem.Str(pysem.FirstTruthy(config.GetDefault("habit_detection", "on"), "on"))
	return pysem.Lower(pysem.Strip(v)) != "off"
}

// selectedRules mirrors habits.selected_rules (membership set; empty csv → all).
func selectedRules(config *pysem.Obj) map[string]bool {
	raw := pysem.Strip(pysem.Str(pysem.FirstTruthy(config.GetDefault("habit_rules", ""), "")))
	all := map[string]bool{}
	for _, rule := range allRules {
		all[rule] = true
	}
	if raw == "" {
		return all
	}
	chosen := map[string]bool{}
	for _, part := range strings.Split(raw, ",") {
		part = pysem.Lower(pysem.Strip(part))
		if part != "" && all[part] {
			chosen[part] = true
		}
	}
	if len(chosen) == 0 {
		return all
	}
	return chosen
}

// habitsClip mirrors habits._clip (rune slicing, rstrip before the ellipsis).
func habitsClip(text string, limit int) string {
	text = pysem.Strip(text)
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return strings.TrimRightFunc(string(runes[:limit-1]), pysem.IsSpace) + "…"
}

// habitsNorm mirrors habits._norm: " ".join(str(value or "").strip().lower().split()).
func habitsNorm(value any) string {
	s := pysem.Lower(pysem.Strip(pysem.Str(pysem.FirstTruthy(value, ""))))
	return strings.Join(strings.FieldsFunc(s, pysem.IsSpace), " ")
}

// habitsCount mirrors habits._count: "3 commits" / "1 commit".
func habitsCount(n int, noun string) string {
	if n == 1 {
		return fmt.Sprintf("%d %s", n, noun)
	}
	return fmt.Sprintf("%d %ss", n, noun)
}

// habitsLabel mirrors habits._label.
func habitsLabel(item *pysem.Obj) string {
	key := pysem.Strip(strOr(item, "key"))
	title := habitsClip(pysem.Str(pysem.FirstTruthy(item.Get("summary"), item.Get("title"), "")), habitsTitleClip)
	if key != "" && title != "" {
		return key + " '" + title + "'"
	}
	if key != "" {
		return key
	}
	if title != "" {
		return title
	}
	return "an item"
}

// changeHandle mirrors habits.change_handle — a verdict about a change must
// compute the byte-identical handle tomorrow (contract rule 11).
func changeHandle(item *pysem.Obj) string {
	url := habitsNorm(item.Get("url"))
	if url != "" {
		return "url:" + url
	}
	kind := habitsNorm(item.Get("kind"))
	if kind == "" {
		kind = "change"
	}
	repo := habitsNorm(item.Get("repository"))
	ident := pysem.Strip(pysem.Str(pysem.FirstTruthy(item.Get("pr_id"), "")))
	if ident == "" {
		ident = pysem.Strip(pysem.Str(pysem.FirstTruthy(item.Get("key"), "")))
	}
	if ident != "" {
		return kind + ":" + repo + ":" + pysem.Lower(ident)
	}
	subject := normalizeCommitSubject(pysem.Str(pysem.FirstTruthy(item.Get("summary"), item.Get("title"), "")))
	digest := fmt.Sprintf("%x", sha1.Sum([]byte(subject))) // #nosec G401
	return kind + ":" + repo + ":s" + digest[:16]
}

func habitsIsExcused(rule string, item *pysem.Obj, feedback excuser) bool {
	return feedback != nil && feedback(rule, changeHandle(item))
}

// habitsExcuse mirrors habits._excuse: the changes still worth reporting.
func habitsExcuse(rule string, items []*pysem.Obj, feedback excuser) []*pysem.Obj {
	if feedback == nil {
		return append([]*pysem.Obj{}, items...)
	}
	kept := []*pysem.Obj{}
	for _, item := range items {
		if !habitsIsExcused(rule, item, feedback) {
			kept = append(kept, item)
		}
	}
	return kept
}

// habitsEvidence mirrors habits._evidence: (label, url) pairs deduped by url,
// capped at habitsEvidencePerSignal.
func habitsEvidence(items []*pysem.Obj) [][2]string {
	seen := map[string]bool{}
	pairs := [][2]string{}
	for _, item := range items {
		url := pysem.Strip(strOr(item, "url"))
		key := pysem.Strip(strOr(item, "key"))
		if key == "" {
			key = habitsClip(strOr(item, "title"), 40)
		}
		if key == "" || (url != "" && seen[url]) {
			continue
		}
		if url != "" {
			seen[url] = true
		}
		pairs = append(pairs, [2]string{key, url})
		if len(pairs) >= habitsEvidencePerSignal {
			break
		}
	}
	return pairs
}

func habitsChangedPaths(item *pysem.Obj) []string {
	out := []string{}
	for _, p := range listOr(item, "changed_paths") {
		if pysem.Truthy(p) {
			out = append(out, pysem.Str(p))
		}
	}
	return out
}

func habitsLinksKnown(item *pysem.Obj) bool {
	return pysem.Truthy(item.GetDefault("work_items_known", true))
}

func habitsLinkedWorkItems(item *pysem.Obj) []string {
	out := []string{}
	for _, wid := range listOr(item, "work_item_ids") {
		if pysem.Strip(pysem.Str(wid)) != "" {
			out = append(out, pysem.Str(wid))
		}
	}
	return out
}

func habitsIsRevert(subject string) bool {
	return strings.HasPrefix(pysem.Lower(pysem.Strip(subject)), "revert ")
}

// habitsBelongsToAPullRequest mirrors habits._belongs_to_a_pull_request.
func habitsBelongsToAPullRequest(subject string) bool {
	return claimsPullRequest(subject) || isMergeSubject(subject) || habitsIsRevert(subject)
}

// habitsIsPlumbing mirrors habits._is_plumbing (deliberately narrower).
func habitsIsPlumbing(subject string) bool {
	return isMergeSubject(subject) || habitsIsRevert(subject)
}

// habitsSignal mirrors habits._signal (wire-shaped: rule, title, detail,
// evidence, repeat, handles — aggregate._signal_to_wire key order).
func habitsSignal(rule, detail string, evidence []*pysem.Obj) *pysem.Obj {
	title, ok := ruleTitles[rule]
	if !ok {
		title = habitsCapitalize(strings.ReplaceAll(rule, "-", " "))
	}
	signal := pysem.EmptyObj()
	signal.Set("rule", rule)
	signal.Set("title", title)
	signal.Set("detail", detail)
	pairs := []any{}
	for _, pair := range habitsEvidence(evidence) {
		pairs = append(pairs, []any{pair[0], pair[1]})
	}
	signal.Set("evidence", pairs)
	signal.Set("repeat", false)
	handles := []any{}
	seenHandles := map[string]bool{}
	for _, item := range evidence {
		handle := changeHandle(item)
		if !seenHandles[handle] {
			seenHandles[handle] = true
			handles = append(handles, handle)
		}
	}
	signal.Set("handles", handles)
	return signal
}

// habitsCapitalize mirrors str.capitalize (first rune upper, rest lower).
func habitsCapitalize(s string) string {
	runes := []rune(s)
	if len(runes) == 0 {
		return s
	}
	return strings.ToUpper(string(runes[:1])) + pysem.Lower(string(runes[1:]))
}

// habitsPreviousSignalRules mirrors habits._previous_signal_rules.
func habitsPreviousSignalRules(previousReport *PrevReport) map[string]map[string]bool {
	if previousReport == nil {
		return map[string]map[string]bool{}
	}
	out := map[string]map[string]bool{}
	for _, member := range previousReport.Members {
		rules := map[string]bool{}
		for _, rule := range member.PracticeRules {
			rules[rule] = true
		}
		if len(rules) > 0 {
			out[member.Name] = rules
		}
	}
	return out
}

// habitsMarkRepeats mirrors habits._mark_repeats (signals are wire objects, so
// the flag is set in place — each signal object is freshly built per run).
func habitsMarkRepeats(found []*pysem.Obj, previousRules map[string]bool) []*pysem.Obj {
	for _, signal := range found {
		if rule, _ := signal.Get("rule").(string); previousRules[rule] {
			signal.Set("repeat", true)
		}
	}
	return found
}

// habitsTicketStatusIndex mirrors habits._ticket_status_index.
func habitsTicketStatusIndex(items []*pysem.Obj) map[string]string {
	index := map[string]string{}
	stamps := map[string]string{}
	for _, item := range items {
		if !habitsHeldTicketKinds[strOr(item, "kind")] {
			continue
		}
		key := pysem.Strip(strOr(item, "key"))
		status := pysem.Strip(strOr(item, "status"))
		if key == "" || status == "" {
			continue
		}
		stamp := strOr(item, "timestamp")
		if _, ok := index[key]; !ok || stamp >= stamps[key] {
			index[key] = status
			stamps[key] = stamp
		}
	}
	return index
}

// habitsReferencedKeys mirrors habits._referenced_keys (ordered dedupe).
func habitsReferencedKeys(item *pysem.Obj, prefixes, workItemIDs map[string]bool) []string {
	keys := []string{}
	for _, text := range []string{strOr(item, "title"), strOr(item, "branch"), strOr(item, "body")} {
		keys = append(keys, gatedTicketKeys(text, prefixes)...)
		keys = append(keys, azdoRefIDs(text)...)
		for _, match := range bareRefIDs(text) {
			if workItemIDs[match] {
				keys = append(keys, match)
			}
		}
	}
	keys = append(keys, habitsLinkedWorkItems(item)...)
	seen := map[string]bool{}
	out := []string{}
	for _, key := range keys {
		if !seen[key] {
			seen[key] = true
			out = append(out, key)
		}
	}
	return out
}

func habitsHasReference(item *pysem.Obj, prefixes, workItemIDs map[string]bool) bool {
	if len(habitsLinkedWorkItems(item)) > 0 {
		return true
	}
	return hasTrackerReference(
		[]string{strOr(item, "title"), strOr(item, "branch"), strOr(item, "body")},
		prefixes,
		workItemIDs,
	)
}

// habitsIsDocsOnly mirrors habits._is_docs_only (empty means UNKNOWN → false).
func habitsIsDocsOnly(item *pysem.Obj) bool {
	paths := habitsReviewablePaths(item)
	if len(paths) == 0 {
		return false
	}
	for _, path := range paths {
		if !isDocumentationPath(path) {
			return false
		}
	}
	return true
}

// habitsReviewablePaths mirrors habits._reviewable_paths.
func habitsReviewablePaths(item *pysem.Obj) []string {
	out := []string{}
	for _, path := range habitsChangedPaths(item) {
		normalized := pysem.Lower(strings.Trim(strings.ReplaceAll(path, `\`, "/"), "/"))
		if normalized == "" {
			continue
		}
		parts := strings.Split(normalized, "/")
		if habitsGeneratedFilenames[parts[len(parts)-1]] || habitsHasSuffix(normalized, habitsGeneratedSuffixes) {
			continue
		}
		skip := false
		for _, part := range parts[:len(parts)-1] {
			if habitsGeneratedDirectories[part] {
				skip = true
				break
			}
		}
		if skip {
			continue
		}
		out = append(out, path)
	}
	return out
}

func habitsHasSuffix(s string, suffixes []string) bool {
	for _, suffix := range suffixes {
		if strings.HasSuffix(s, suffix) {
			return true
		}
	}
	return false
}

// habitsLooseUntrackedWork mirrors habits._loose_untracked_work.
func habitsLooseUntrackedWork(items []*pysem.Obj, prefixes, workItemIDs map[string]bool, corpus *ticketCorpus, ownKeys map[string]bool) []*pysem.Obj {
	loose := []*pysem.Obj{}
	for _, item := range items {
		kind := strOr(item, "kind")
		switch kind {
		case "pr":
			// An Azure PR whose links we couldn't read is UNKNOWN, not unlinked.
			if !habitsLinksKnown(item) {
				continue
			}
		case "commit":
			if habitsBelongsToAPullRequest(strOr(item, "title")) {
				continue
			}
			// Local-git commits carry no repository — never matchable to a PR.
			if pysem.Strip(strOr(item, "repository")) == "" {
				continue
			}
		default:
			continue
		}
		if habitsHasReference(item, prefixes, workItemIDs) {
			continue
		}
		profile := buildChangeProfileOpts(item, habitsIsDocsOnly(item))
		if relatesToTicket(profile, corpus, ownKeys) {
			continue
		}
		loose = append(loose, item)
	}
	return loose
}

func habitsUntrackedWorkSignal(loose []*pysem.Obj, checked bool) []*pysem.Obj {
	if len(loose) == 0 {
		return nil
	}
	// Lead with a PR when there is one — it is the reviewable unit.
	var head string
	prs := []*pysem.Obj{}
	for _, i := range loose {
		if strOr(i, "kind") == "pr" {
			prs = append(prs, i)
		}
	}
	if len(prs) > 0 {
		head = habitsLabel(prs[0])
	} else {
		head = habitsLabel(loose[0])
	}
	var subject string
	if len(loose) > 1 {
		subject = fmt.Sprintf("%s and %s carry", head, habitsCount(len(loose)-1, "other change"))
	} else {
		subject = head + " carries"
	}
	var detail string
	if checked {
		detail = subject + " no ticket reference — no key in the branch, title, or description, and no " +
			"wording or file path that matches a ticket the team has open. Link a ticket (or raise " +
			"one) so the work counts toward sprint scope."
	} else {
		detail = subject + " no ticket reference in the branch, title, or description. " +
			"Link a ticket (or raise one) so the work counts toward sprint scope."
	}
	return []*pysem.Obj{habitsSignal(ruleUntrackedWork, detail, loose)}
}

// habitsLooseUntrackedDocs mirrors habits._loose_untracked_docs.
func habitsLooseUntrackedDocs(items []*pysem.Obj, prefixes, workItemIDs map[string]bool, corpus *ticketCorpus, ownKeys map[string]bool) []*pysem.Obj {
	loose := []*pysem.Obj{}
	seenKeys := map[string]bool{}
	createdKeys := map[string]bool{}
	for _, i := range items {
		if strOr(i, "kind") == "page-created" {
			createdKeys[strOr(i, "key")] = true
		}
	}
	for _, item := range items {
		kind := strOr(item, "kind")
		if kind != "page" && kind != "page-created" {
			continue
		}
		key := pysem.Strip(strOr(item, "key"))
		if kind == "page" && createdKeys[key] {
			continue
		}
		if key != "" && seenKeys[key] {
			continue
		}
		seenKeys[key] = true
		if hasTrackerReference(
			[]string{strOr(item, "summary"), strOr(item, "title"), strOr(item, "body")},
			prefixes,
			workItemIDs,
		) {
			continue
		}
		// A page IS documentation, so it always gets the definition-of-done bar.
		profile := buildChangeProfileOpts(item, true)
		if relatesToTicket(profile, corpus, ownKeys) {
			continue
		}
		loose = append(loose, item)
	}
	return loose
}

func habitsUntrackedDocsSignal(loose []*pysem.Obj, checked bool) []*pysem.Obj {
	if len(loose) == 0 {
		return nil
	}
	head := habitsLabel(loose[0])
	var subject string
	if len(loose) > 1 {
		subject = fmt.Sprintf("%s and %s have", head, habitsCount(len(loose)-1, "other page"))
	} else {
		subject = head + " has"
	}
	var detail string
	if checked {
		detail = subject + " no ticket reference, and nothing in them matches a ticket the team has open " +
			"— including tickets whose definition of done covers documentation. Documentation effort " +
			"is real effort — tie it to a ticket so it shows up in the sprint."
	} else {
		detail = subject + " no ticket reference. Documentation effort is real effort — " +
			"tie it to a ticket so it shows up in the sprint."
	}
	return []*pysem.Obj{habitsSignal(ruleUntrackedDocs, detail, loose)}
}

// habitsBoardNotUpdated mirrors habits._board_not_updated.
func habitsBoardNotUpdated(items []*pysem.Obj, prefixes, workItemIDs map[string]bool, statuses map[string]string, feedback excuser) []*pysem.Obj {
	staleKeys := []string{}
	stale := map[string]*pysem.Obj{}
	for _, item := range items {
		if strOr(item, "kind") != "pr" || habitsNorm(item.Get("status")) != "merged" {
			continue
		}
		for _, key := range habitsReferencedKeys(item, prefixes, workItemIDs) {
			for _, candidate := range []string{key, "#" + key} {
				if habitsTodoStatuses[habitsNorm(statuses[candidate])] {
					if _, ok := stale[candidate]; !ok {
						stale[candidate] = item
						staleKeys = append(staleKeys, candidate)
					}
				}
			}
		}
	}
	keptKeys := []string{}
	for _, key := range staleKeys {
		if !habitsIsExcused(ruleBoardNotUpdated, stale[key], feedback) {
			keptKeys = append(keptKeys, key)
		}
	}
	if len(keptKeys) == 0 {
		return nil
	}
	sortedKeys := append([]string{}, keptKeys...)
	sort.Strings(sortedKeys)
	head := strings.Join(sortedKeys[:minInt(3, len(sortedKeys))], ", ")
	evidence := []*pysem.Obj{}
	for _, key := range keptKeys { // insertion order, mirroring stale.values()
		evidence = append(evidence, stale[key])
	}
	return []*pysem.Obj{habitsSignal(
		ruleBoardNotUpdated,
		head+" shipped in a merged pull request but the board still has it not started. "+
			"Move the ticket so the sprint reflects what actually landed.",
		evidence,
	)}
}

// habitsWipSprawl mirrors habits._wip_sprawl.
func habitsWipSprawl(items []*pysem.Obj, feedback excuser) []*pysem.Obj {
	heldKeys := []string{}
	held := map[string]*pysem.Obj{}
	for _, item := range items {
		if !habitsHeldTicketKinds[strOr(item, "kind")] {
			continue
		}
		if !habitsInProgressStatuses[habitsNorm(item.Get("status"))] {
			continue
		}
		key := pysem.Strip(strOr(item, "key"))
		if key != "" {
			if _, ok := held[key]; !ok {
				held[key] = item
				heldKeys = append(heldKeys, key)
			}
		}
	}
	keptKeys := []string{}
	for _, key := range heldKeys {
		if !habitsIsExcused(ruleWipSprawl, held[key], feedback) {
			keptKeys = append(keptKeys, key)
		}
	}
	if len(keptKeys) < habitsWipSprawlTickets {
		return nil
	}
	sortedKeys := append([]string{}, keptKeys...)
	sort.Strings(sortedKeys)
	ellipsis := ""
	if len(sortedKeys) > 4 {
		ellipsis = "…"
	}
	evidence := []*pysem.Obj{}
	for _, key := range keptKeys {
		evidence = append(evidence, held[key])
	}
	return []*pysem.Obj{habitsSignal(
		ruleWipSprawl,
		fmt.Sprintf("%s in progress at once (%s%s). Finishing one beats starting another.",
			habitsCount(len(keptKeys), "ticket"), strings.Join(sortedKeys[:minInt(4, len(sortedKeys))], ", "), ellipsis),
		evidence,
	)}
}

// habitsLargeChange mirrors habits._large_change.
func habitsLargeChange(items []*pysem.Obj, feedback excuser) []*pysem.Obj {
	big := []*pysem.Obj{}
	for _, item := range items {
		if strOr(item, "kind") != "pr" {
			continue
		}
		paths := habitsReviewablePaths(item)
		if len(paths) < habitsLargeChangeFiles {
			continue
		}
		// A big docs-only change is a different animal from a big code change.
		docsOnly := true
		for _, path := range paths {
			if !isDocumentationPath(path) {
				docsOnly = false
				break
			}
		}
		if docsOnly {
			continue
		}
		big = append(big, item)
	}
	big = habitsExcuse(ruleLargeChange, big, feedback)
	if len(big) == 0 {
		return nil
	}
	parts := []string{}
	for _, item := range big[:minInt(2, len(big))] {
		parts = append(parts, fmt.Sprintf("%s (%d files)", habitsLabel(item), len(habitsReviewablePaths(item))))
	}
	return []*pysem.Obj{habitsSignal(
		ruleLargeChange,
		strings.Join(parts, ", ")+" — changes this size are hard to review well. "+
			"Splitting them gets sharper review and lands sooner.",
		big,
	)}
}

// habitsNoPullRequest mirrors habits._no_pull_request.
func habitsNoPullRequest(items []*pysem.Obj, feedback excuser) []*pysem.Obj {
	prRepos := map[string]bool{}
	for _, i := range items {
		if strOr(i, "kind") == "pr" {
			if repo := strOr(i, "repository"); repo != "" {
				prRepos[repo] = true
			}
		}
	}
	if len(prRepos) == 0 {
		return nil
	}
	looseOrder := []string{}
	loose := map[string][]*pysem.Obj{}
	seen := map[string]bool{}
	for _, item := range items {
		if strOr(item, "kind") != "commit" {
			continue
		}
		repo := strOr(item, "repository")
		if !prRepos[repo] {
			continue
		}
		if habitsBelongsToAPullRequest(strOr(item, "title")) {
			continue
		}
		key := strOr(item, "key")
		if key != "" && seen[key] {
			continue
		}
		seen[key] = true
		if _, ok := loose[repo]; !ok {
			looseOrder = append(looseOrder, repo)
		}
		loose[repo] = append(loose[repo], item)
	}

	// Excused before the per-repo threshold; first offender in sorted-repo order.
	offenderRepos := []string{}
	offenders := map[string][]*pysem.Obj{}
	for _, repo := range looseOrder {
		kept := habitsExcuse(ruleNoPullRequest, loose[repo], feedback)
		if len(kept) >= habitsLooseCommits {
			offenderRepos = append(offenderRepos, repo)
			offenders[repo] = kept
		}
	}
	if len(offenderRepos) == 0 {
		return nil
	}
	sort.Strings(offenderRepos)
	repo := offenderRepos[0]
	commits := offenders[repo]
	return []*pysem.Obj{habitsSignal(
		ruleNoPullRequest,
		fmt.Sprintf("%s landed in %s without a pull request, in a repo "+
			"where you opened one today. Even a small PR gets the change a second pair of eyes.",
			habitsCount(len(commits), "commit"), repo),
		commits,
	)}
}

// habitsIsLowInformation mirrors habits._is_low_information.
func habitsIsLowInformation(subject string) (bool, string) {
	text := normalizeCommitSubject(subject)
	// Conventional-commit prefix: judge what comes after the colon.
	if strings.Contains(text, ":") {
		head, tail, _ := strings.Cut(text, ":")
		if head != "" && !strings.Contains(pysem.Strip(head), " ") && pysem.Strip(tail) != "" {
			text = pysem.Strip(tail)
		}
	}
	// A subject that is only a ticket key is tracked work but says nothing.
	for _, key := range findTicketKeys(text) {
		text = strings.ReplaceAll(text, key, " ")
	}
	text = strings.Join(strings.FieldsFunc(text, pysem.IsSpace), " ")
	normalized := strings.Trim(pysem.Lower(text), " .-_")
	if normalized == "" {
		return true, text
	}
	low := habitsLowInformationSubjects[normalized] || len([]rune(normalized)) < habitsMinSubjectChars
	return low, text
}

// habitsCommitMessages mirrors habits._commit_messages.
func habitsCommitMessages(items []*pysem.Obj, feedback excuser) []*pysem.Obj {
	type thinPair struct {
		item       *pysem.Obj
		normalized string
	}
	thin := []thinPair{}
	seen := map[string]bool{}
	for _, item := range items {
		if strOr(item, "kind") != "commit" {
			continue
		}
		subject := strOr(item, "title")
		if pysem.Strip(subject) == "" || habitsIsPlumbing(subject) {
			continue
		}
		key := strOr(item, "key")
		if key != "" && seen[key] {
			continue
		}
		seen[key] = true
		if low, normalized := habitsIsLowInformation(subject); low {
			thin = append(thin, thinPair{item, normalized})
		}
	}
	kept := []thinPair{}
	for _, pair := range thin {
		if !habitsIsExcused(ruleCommitMessages, pair.item, feedback) {
			kept = append(kept, pair)
		}
	}
	if len(kept) < habitsLowInformationCommits {
		return nil
	}
	quotedParts := []string{}
	for _, pair := range kept {
		if pair.normalized != "" {
			quotedParts = append(quotedParts, "'"+habitsClip(pair.normalized, 24)+"'")
			if len(quotedParts) >= 3 {
				break
			}
		}
	}
	evidence := []*pysem.Obj{}
	for _, pair := range kept {
		evidence = append(evidence, pair.item)
	}
	return []*pysem.Obj{habitsSignal(
		ruleCommitMessages,
		fmt.Sprintf("%s have subjects that name no outcome (%s). "+
			"A subject that says what changed saves the next reader — often you — a bisect.",
			habitsCount(len(kept), "commit"), strings.Join(quotedParts, ", ")),
		evidence,
	)}
}

// habitsAdjudicate mirrors habits._adjudicate: offer every surviving change to
// the adjudicator once, as a single batch (the aggregate's capture stub —
// exception handling around the real LLM lives Python-side).
func habitsAdjudicate(
	looseWork, looseDocs map[string][]*pysem.Obj,
	corpus *ticketCorpus,
	ownKeysByMember map[string]map[string]bool,
	adj adjudicator,
) (map[string][]*pysem.Obj, map[string][]*pysem.Obj) {
	if !corpus.truthy() {
		return looseWork, looseDocs
	}
	type caseRef struct {
		bucket   string
		name     string
		position int
	}
	cases := []adjudicationCase{}
	index := map[string]caseRef{}
	for _, bucketPair := range []struct {
		name   string
		bucket map[string][]*pysem.Obj
	}{{"work", looseWork}, {"docs", looseDocs}} {
		names := []string{}
		for name := range bucketPair.bucket {
			names = append(names, name)
		}
		sort.Strings(names) // Python: for name in sorted(bucket)
		for _, name := range names {
			ownKeys := ownKeysByMember[name]
			for position, item := range bucketPair.bucket[name] {
				caseID := fmt.Sprintf("%s-%d", bucketPair.name, len(cases))
				profile := buildChangeProfile(item)
				candidates := [][3]string{}
				for _, key := range nearMisses(profile, corpus, ownKeys, habitsAdjudicationCandidates) {
					ticket := corpus.tickets[key]
					candidates = append(candidates, [3]string{
						key, ticket.title, runeClip(ticket.text, habitsAdjudicationTextClip),
					})
				}
				if len(candidates) == 0 {
					continue // nothing to weigh it against; do not spend a slot
				}
				cases = append(cases, adjudicationCase{
					CaseID:  caseID,
					Subject: pysem.Str(pysem.FirstTruthy(item.Get("summary"), item.Get("title"), "")),
					Branch:  pysem.Str(pysem.FirstTruthy(item.Get("branch"), "")),
					Paths: func() []string {
						paths := habitsReviewablePaths(item)
						return paths[:minInt(10, len(paths))]
					}(),
					Candidates: candidates,
				})
				index[caseID] = caseRef{bucketPair.name, name, position}
			}
		}
	}
	if len(cases) == 0 {
		return looseWork, looseDocs
	}

	dropped := map[string]bool{}
	for _, caseID := range adj(cases) {
		// Ids we did not send are discarded rather than trusted.
		if _, ok := index[pysem.Str(caseID)]; ok {
			dropped[pysem.Str(caseID)] = true
		}
	}
	if len(dropped) == 0 {
		return looseWork, looseDocs
	}

	remove := map[[2]string]map[int]bool{}
	for caseID := range dropped {
		ref := index[caseID]
		key := [2]string{ref.bucket, ref.name}
		if remove[key] == nil {
			remove[key] = map[int]bool{}
		}
		remove[key][ref.position] = true
	}
	out := []map[string][]*pysem.Obj{}
	for _, bucketPair := range []struct {
		name   string
		bucket map[string][]*pysem.Obj
	}{{"work", looseWork}, {"docs", looseDocs}} {
		filtered := map[string][]*pysem.Obj{}
		for name, items := range bucketPair.bucket {
			kept := []*pysem.Obj{}
			for i, item := range items {
				if !remove[[2]string{bucketPair.name, name}][i] {
					kept = append(kept, item)
				}
			}
			filtered[name] = kept
		}
		out = append(out, filtered)
	}
	return out[0], out[1]
}

// detectPractices mirrors habits.detect_practices. The returned object is
// ordered {member: [signal wire objects]} — members with none are absent.
func detectPractices(
	grouped *Grouped,
	config *pysem.Obj,
	categoryCoverage [][2]string,
	previousReport *PrevReport,
	referenceGrouped *Grouped,
	referenceItems []*pysem.Obj,
	adj adjudicator,
	feedback excuser,
) *pysem.Obj {
	signals := pysem.EmptyObj()
	if !habitsEnabled(config) {
		return signals
	}
	rules := selectedRules(config)

	coverage := map[string]string{}
	for _, pair := range categoryCoverage {
		coverage[pair[0]] = pair[1]
	}
	// The kill switch: no usable tracker, no tracker-shaped accusations.
	ticketing, ok := coverage[categoryTicketing]
	if !ok {
		ticketing = covered
	}
	trackerUsable := ticketing != failedState && ticketing != notConfigured

	allItems := grouped.AllItems()
	gateItems := append(append([]*pysem.Obj{}, allItems...), referenceItems...)
	prefixes := trackerPrefixes(gateItems)
	workItemIDs := trackerWorkItemIDs(gateItems)
	ticketStatus := habitsTicketStatusIndex(allItems)
	previous := habitsPreviousSignalRules(previousReport)

	corpus := emptyTicketCorpus()
	if trackerUsable {
		corpus = buildCorpus(allItems, referenceItems)
	}
	looseWork := map[string][]*pysem.Obj{}
	looseDocs := map[string][]*pysem.Obj{}
	ownKeysByMember := map[string]map[string]bool{}
	if trackerUsable {
		for _, name := range grouped.Names {
			own := map[string]bool{}
			for key := range ticketKeys(grouped.Items[name]) {
				own[key] = true
			}
			for key := range ticketKeys(referenceGrouped.Items[name]) {
				own[key] = true
			}
			ownKeysByMember[name] = own
		}
		for _, name := range grouped.Names {
			items := grouped.Items[name]
			ownKeys := ownKeysByMember[name]
			if rules[ruleUntrackedWork] {
				looseWork[name] = habitsExcuse(
					ruleUntrackedWork,
					habitsLooseUntrackedWork(items, prefixes, workItemIDs, corpus, ownKeys),
					feedback,
				)
			}
			if rules[ruleUntrackedDocs] {
				looseDocs[name] = habitsExcuse(
					ruleUntrackedDocs,
					habitsLooseUntrackedDocs(items, prefixes, workItemIDs, corpus, ownKeys),
					feedback,
				)
			}
		}
		if adj != nil {
			looseWork, looseDocs = habitsAdjudicate(looseWork, looseDocs, corpus, ownKeysByMember, adj)
		}
	}

	for _, name := range grouped.Names {
		items := grouped.Items[name]
		found := []*pysem.Obj{}
		if trackerUsable {
			if rules[ruleUntrackedWork] {
				found = append(found, habitsUntrackedWorkSignal(looseWork[name], corpus.truthy())...)
			}
			if rules[ruleUntrackedDocs] {
				found = append(found, habitsUntrackedDocsSignal(looseDocs[name], corpus.truthy())...)
			}
			if rules[ruleBoardNotUpdated] {
				found = append(found, habitsBoardNotUpdated(items, prefixes, workItemIDs, ticketStatus, feedback)...)
			}
			if rules[ruleWipSprawl] {
				found = append(found, habitsWipSprawl(items, feedback)...)
			}
		}
		if rules[ruleLargeChange] {
			found = append(found, habitsLargeChange(items, feedback)...)
		}
		if rules[ruleNoPullRequest] {
			found = append(found, habitsNoPullRequest(items, feedback)...)
		}
		if rules[ruleCommitMessages] {
			found = append(found, habitsCommitMessages(items, feedback)...)
		}
		if len(found) > 0 {
			capped := found[:minInt(habitsMaxSignalsPerMember, len(found))]
			marked := habitsMarkRepeats(capped, previous[name])
			wire := []any{}
			for _, signal := range marked {
				wire = append(wire, signal)
			}
			signals.Set(name, wire)
		}
	}
	return signals
}
