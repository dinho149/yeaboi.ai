package standup

// references.go — port of src/yeaboi/standup/references.py. Keep in lockstep:
// the Python module is the reference implementation;
// tests/parity/test_standup_parity.py diffs whole-pipeline output.
//
// Ticket-reference and pull-request parsing shared across the standup
// pipeline. Three consumers used to keep their own copies of these patterns
// (export linkifying, engine's carried-over note + PR folding, habits'
// tracked-work gate); one copy drifting from another is a silent bug.
//
// The gate: ticket-shaped text is not evidence of a ticket. "UTF-8",
// "SHA-256", "ISO-8601" and "HTTP-2" all match a Jira key regex, and on
// GitHub "#91" is a pull-request number, not a work item. Each syntax is
// admitted only on evidence the tracker itself produced in this run:
//
//   - "PROJ-123" — prefix-gated on trackerPrefixes.
//   - "AB#123"   — ungated: AzDO's ARM syntax carries its own evidence.
//   - "#123"     — id-gated on trackerWorkItemIDs (empty on GitHub-only).
//
// Evidence from the tracker unlocks a pattern; a pattern never unlocks itself.
// Pure: no I/O, no config, no LLM — and NOTHING here is ever logged.
//
// Regex semantics: Python's \b/\w are unicode, RE2's are ASCII, and RE2 has
// no lookarounds. Each pattern keeps as much of the Python source as RE2 can
// express, then post-filters matches with wordBoundaryAt / prevRune
// (boundary.go). Post-filtering after a superset scan could in principle
// diverge from Python when a rejected ASCII-matched span overlaps a later
// Python-valid span — but for every pattern below a valid start cannot occur
// inside a rejected span (a Jira key's interior is ASCII word chars a \b can
// never precede; an AZDO/bare reference's interior holds no second '#'-or-'A'
// start position whose lookbehind could pass), so a plain reject-and-continue
// scan is exact. references_test.go pins the sequential cases.
//
// # See docs: "Daily Standup" — exports

import (
	"regexp"
	"strings"
	"unicode"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// ticketKeyRe ports TICKET_KEY_RE = \b[A-Z][A-Z0-9]+-\d+\b — Jira-style
// ticket keys ("PSOT-12"). AzDO work items ("#1234" / "AB#1234") deliberately
// don't match — they have their own patterns with their own gates, because a
// bare number is far more ambiguous than a prefixed key. The \b stays in the
// RE2 source (ASCII matches are a superset here: the boundary-adjacent
// pattern chars are ASCII word chars), and every match is re-checked with the
// unicode wordBoundaryAt at both ends.
var ticketKeyRe = regexp.MustCompile(`\b[A-Z][A-Z0-9]+-\d+\b`)

// azdoRefRe ports AZDO_REF_RE = (?<![A-Za-z0-9])AB#(\d+)\b (IGNORECASE) —
// Azure DevOps' "artifact reference" syntax. Case-insensitive because commit
// subjects spell it "ab#123" as often as "AB#123"; the leading boundary stops
// "LAB#12" from matching. RE2 has no lookbehind and its trailing \b would be
// ASCII, so both are dropped from the source and enforced in azdoRefIDs:
// the previous rune must not be ASCII [A-Za-z0-9] (exactly that class — NOT
// unicode \w: "_AB#12" and "éAB#12" both match in Python), and the match end
// must sit on a unicode word boundary.
var azdoRefRe = regexp.MustCompile(`(?i)AB#(\d+)`)

// bareIDRe ports BARE_ID_RE = (?<![\w#])#(\d+)\b — a bare "#123". The
// lookbehind excludes "AB#123" (already matched above) and anything
// word-adjacent, so "utf#8" and "v1.2#3" don't produce a reference. Here the
// lookbehind class IS unicode \w plus '#': enforced in bareRefIDs via
// pysem.IsWordRune, with wordBoundaryAt at the match end.
var bareIDRe = regexp.MustCompile(`#(\d+)`)

// prNumberRes ports PR_NUMBER_RES, same order. Commit → PR association lives
// in title text only: collectors emit pr_id and branch on PR items, but a
// commit names its PR solely via its subject. The real-world formats, one
// pattern each: GitHub/AzDO merge commits ("Merge pull request #91 …" /
// "Merge pull request 48806 …"), AzDO squash merges ("Merged PR 123: Title"),
// and parenthesised references — GitHub squash merges end in "(#91)" and the
// collector's own PR-branch scan appends "(PR #91)". No \b in any of them;
// \d as ASCII is an accepted deviation (tracker numbers are ASCII).
var prNumberRes = []*regexp.Regexp{
	regexp.MustCompile(`Merge pull request #?(\d+)`),
	regexp.MustCompile(`Merged PR (\d+):`),
	regexp.MustCompile(`\((?:PR )?#(\d+)\)`),
}

// mergeBranchRe ports MERGE_BRANCH_RE. \S as ASCII is an accepted deviation
// (a branch name delimited by a unicode space would capture wider than
// Python; branch names are ASCII in practice).
var mergeBranchRe = regexp.MustCompile(`Merge pull request .*? from (\S+)`)

// branchSyncRe ports BRANCH_SYNC_RE = ^\s*Merge (?:remote-tracking )?branch\b
// — a merge that names no PR at all: "Merge branch 'main' into feature-x".
// Pure plumbing — git wrote the subject, not the author. The trailing \b is
// enforced by wordBoundaryAt after the match (isMergeSubject); \s as ASCII is
// an accepted deviation.
var branchSyncRe = regexp.MustCompile(`^\s*Merge (?:remote-tracking )?branch`)

// subjectTailRe ports _SUBJECT_TAIL_RE = \s*\((?:PR #\d+|[^()]{1,60})\)\s*$ —
// the tail the collectors append to a commit subject for provenance:
// " (PR #91)" from github's PR-branch scan, " (my-repo)" from every AzDO
// commit and PR. RE2-safe as written ({1,60} counts runes in both engines;
// Go alternation is leftmost-first like Python); \s/\d as ASCII and $ as
// strict end-of-text are accepted deviations — normalizeCommitSubject strips
// the text first, so a trailing newline never reaches the anchor.
var subjectTailRe = regexp.MustCompile(`\s*\((?:PR #\d+|[^()]{1,60})\)\s*$`)

// trackerKinds ports _TRACKER_KINDS — kinds whose key is a tracker handle
// rather than a sha or PR number. The prefix/id gates are built from these
// and only these. "ticket_context" belongs here for the same reason the rest
// do: these items came back from Jira/Azure themselves, key and all. Leaving
// it out made the gate a function of *board activity* rather than of what the
// tracker contains — on a quiet Monday the prefixes went empty and a commit
// titled "PROJ-12 fix login" read as untracked work.
var trackerKinds = map[string]bool{
	"issue":          true,
	"wip":            true,
	"work_item":      true,
	"update":         true,
	"comment":        true,
	"ticket_context": true,
}

// workItemKinds ports _WORK_ITEM_KINDS — the subset whose keys are Azure
// Boards ids, for the bare "#1234" gate. A Jira ticket_context key
// ("PROJ-12") is filtered out by the isdigit check below rather than by kind,
// so one membership list serves both trackers.
var workItemKinds = map[string]bool{
	"work_item":      true,
	"wip":            true,
	"ticket_context": true,
}

// isTrackerKind ports is_tracker_kind — whether an item of this kind IS a
// ticket (vs a change that may name one).
func isTrackerKind(kind string) bool {
	return trackerKinds[kind]
}

// findTicketKeys ports find_ticket_keys — every Jira-shaped key in text,
// ungated, in order of appearance.
func findTicketKeys(text string) []string {
	out := []string{}
	for _, loc := range ticketKeyRe.FindAllStringIndex(text, -1) {
		// Python's \b is unicode: "éPROJ-12" / "PROJ-12é" must not match.
		if wordBoundaryAt(text, loc[0]) && wordBoundaryAt(text, loc[1]) {
			out = append(out, text[loc[0]:loc[1]])
		}
	}
	return out
}

// prefixesOf ports prefixes_of — project prefixes of a set of ticket keys
// ("PSOT-12" → "PSOT"). Python returns a frozenset; the Go shape is an
// unordered membership map.
func prefixesOf(keys []string) map[string]bool {
	out := map[string]bool{}
	for _, key := range keys {
		if key == "" {
			continue
		}
		out[strings.SplitN(key, "-", 2)[0]] = true
	}
	return out
}

// trackerPrefixes ports tracker_prefixes — project prefixes the trackers
// actually produced in this window. Built from tracker-sourced item *keys*,
// never from prose: a key invented by an LLM (or a "UTF-8" in a commit
// subject) must not be able to widen the gate that is meant to exclude it.
func trackerPrefixes(items []*pysem.Obj) map[string]bool {
	keys := []string{}
	for _, item := range items {
		// Python: item.get("kind") in _TRACKER_KINDS — a non-string kind is
		// never a member.
		if kind, ok := item.Get("kind").(string); ok && trackerKinds[kind] {
			keys = append(keys, strippedOr(item, "key"))
		}
	}
	return prefixesOf(keys)
}

// trackerWorkItemIDs ports tracker_work_item_ids — Azure Boards work-item ids
// seen this window ("#1234" → "1234"). The gate for bare "#123" references:
// empty on a Jira-only or GitHub-only setup, which is what keeps a GitHub PR
// number from reading as a work item.
func trackerWorkItemIDs(items []*pysem.Obj) map[string]bool {
	ids := map[string]bool{}
	for _, item := range items {
		kind, ok := item.Get("kind").(string)
		if !ok || !workItemKinds[kind] {
			continue
		}
		key := strings.TrimLeft(strippedOr(item, "key"), "#")
		if refsIsDigit(key) {
			ids[key] = true
		}
	}
	return ids
}

// gatedTicketKeys ports gated_ticket_keys — Jira-shaped keys in text whose
// project prefix the tracker produced.
func gatedTicketKeys(text string, prefixes map[string]bool) []string {
	out := []string{}
	for _, key := range findTicketKeys(text) {
		if prefixes[strings.SplitN(key, "-", 2)[0]] {
			out = append(out, key)
		}
	}
	return out
}

// hasTrackerReference ports has_tracker_reference — whether any of texts
// references a ticket, under all three gates. (Python spells the texts as a
// *texts vararg; Go takes the slice.) Used by the untracked-work rule, so it
// answers the question one way only: true means we found positive evidence of
// a link; false means we found none — never "there is none".
func hasTrackerReference(texts []string, prefixes, workItemIDs map[string]bool) bool {
	for _, text := range texts {
		if text == "" {
			continue
		}
		if len(azdoRefIDs(text)) > 0 {
			return true
		}
		if len(prefixes) > 0 && len(gatedTicketKeys(text, prefixes)) > 0 {
			return true
		}
		if len(workItemIDs) > 0 {
			for _, match := range bareRefIDs(text) {
				if workItemIDs[match] {
					return true
				}
			}
		}
	}
	return false
}

// displayTicketKeys ports display_ticket_keys — exact tracker references in
// evidence-key form ("PROJ-12", "#123"). The naming twin of
// hasTrackerReference: same three gates plus linkedIDs, the first-party work
// items a tracker attached to the change itself (AzDO PR links). These keys
// become *visible claims*, so only a reference the change's own text or the
// tracker itself names may appear; the fuzzy relatedness matcher must never
// feed this. Ordered, deduped, spelled the way evidence rows spell their keys.
// (Python spells the texts as a *texts vararg; the Go seam fixes them as the
// three every caller passes: title, branch, body — in that order.)
func displayTicketKeys(title, branch, body string, prefixes, workItemIDs map[string]bool, linkedIDs []string) []string {
	// Mirrors the keys: dict[str, None] ordered-set idiom: first insertion
	// keeps its position, later duplicates are dropped.
	keys := []string{}
	seen := map[string]bool{}
	add := func(key string) {
		if !seen[key] {
			seen[key] = true
			keys = append(keys, key)
		}
	}
	for _, text := range []string{title, branch, body} {
		if text == "" {
			continue
		}
		for _, key := range gatedTicketKeys(text, prefixes) {
			add(key)
		}
		for _, wid := range azdoRefIDs(text) {
			add("#" + wid)
		}
		for _, wid := range bareRefIDs(text) {
			if workItemIDs[wid] {
				add("#" + wid)
			}
		}
	}
	for _, wid := range linkedIDs {
		wid = strings.TrimLeft(pysem.Strip(wid), "#")
		if wid != "" {
			add("#" + wid)
		}
	}
	return keys
}

// prReference ports pr_reference — the PR number a commit subject claims, or
// "" — text evidence only.
func prReference(title string) string {
	for _, pattern := range prNumberRes {
		if m := pattern.FindStringSubmatch(title); m != nil {
			return m[1]
		}
	}
	return ""
}

// mergeSourceBranch ports merge_source_branch — the source branch named by a
// GitHub merge subject, or "".
func mergeSourceBranch(subject string) string {
	if m := mergeBranchRe.FindStringSubmatch(subject); m != nil {
		return m[1]
	}
	return ""
}

// claimsPullRequest ports claims_pull_request — whether a commit subject
// textually claims a PR, parent found or not. engine._nest_pr_commits needs a
// *real* parent before folding a commit under it; the habit rules need the
// weaker fact: a subject that says "Merge pull request #91" belongs to a PR
// whether or not that PR is inside the collection window, and judging it as
// loose untracked work would be wrong.
func claimsPullRequest(subject string) bool {
	return prReference(subject) != "" || mergeSourceBranch(subject) != ""
}

// isMergeSubject ports is_merge_subject — whether a subject is an actual
// merge commit, not merely PR-referencing. Narrower than claimsPullRequest on
// purpose: the parenthesised form ("fix login (#91)", or the " (PR #91)" the
// collector appends itself) is *provenance on an authored commit*, so a rule
// that judges what the author wrote must still see it. Wider in one
// direction: a branch-sync merge ("Merge branch 'main' into …") names no PR,
// but git wrote its subject, so no rule should judge it as the author's work
// either.
func isMergeSubject(subject string) bool {
	text := subject
	if prNumberRes[0].MatchString(text) || prNumberRes[1].MatchString(text) || mergeBranchRe.MatchString(text) {
		return true
	}
	// BRANCH_SYNC_RE's trailing \b (after "branch") is unicode in Python:
	// enforce it on the match end. ^-anchored, so no rescan is ever needed.
	if loc := branchSyncRe.FindStringIndex(text); loc != nil && wordBoundaryAt(text, loc[1]) {
		return true
	}
	return false
}

// normalizeCommitSubject ports normalize_commit_subject — strip
// collector-added provenance tails so a subject reads as authored.
// " (PR #91)" (github.py's PR-branch scan) and " (my-repo)" (every AzDO
// commit) make a one-word subject look substantial; judging message quality
// without stripping them would let "wip" pass as "wip (my-repo)".
func normalizeCommitSubject(subject string) string {
	text := pysem.Strip(subject)
	for {
		loc := subjectTailRe.FindStringIndex(text)
		if loc == nil {
			break
		}
		// Python: text[: match.start()].rstrip() — rstrip() strips unicode
		// whitespace, hence TrimRightFunc over pysem.IsSpace.
		text = strings.TrimRightFunc(text[:loc[0]], pysem.IsSpace)
	}
	return text
}

// azdoRefIDs mirrors AZDO_REF_RE.findall / .search: the captured work-item
// ids of every Python-valid match, in order. Applies the two post-filters the
// RE2 source cannot carry (see azdoRefRe). A rejected span cannot hide a
// later Python-valid match: the span is "AB#"+digits, and no interior
// position both starts the pattern and passes a lookbehind whose previous
// rune would then be a span character (all ASCII alnum or '#').
func azdoRefIDs(text string) []string {
	out := []string{}
	for _, m := range azdoRefRe.FindAllStringSubmatchIndex(text, -1) {
		if r, ok := prevRune(text, m[0]); ok && refsIsASCIIAlnum(r) {
			continue // (?<![A-Za-z0-9]) — exactly ASCII, not unicode \w
		}
		if !wordBoundaryAt(text, m[1]) {
			continue // trailing \b is unicode in Python
		}
		out = append(out, text[m[2]:m[3]])
	}
	return out
}

// bareRefIDs mirrors BARE_ID_RE.findall: the captured ids of every
// Python-valid bare "#123", in order. Same rescan argument as azdoRefIDs: a
// rejected span is '#'+digits, with no interior '#' to restart on.
func bareRefIDs(text string) []string {
	out := []string{}
	for _, m := range bareIDRe.FindAllStringSubmatchIndex(text, -1) {
		if r, ok := prevRune(text, m[0]); ok && (pysem.IsWordRune(r) || r == '#') {
			continue // (?<![\w#]) — unicode \w plus '#'
		}
		if !wordBoundaryAt(text, m[1]) {
			continue // trailing \b is unicode in Python
		}
		out = append(out, text[m[2]:m[3]])
	}
	return out
}

// refsIsASCIIAlnum reports whether r is in ASCII [A-Za-z0-9] — the exact
// lookbehind class of AZDO_REF_RE (underscore and unicode letters excluded).
func refsIsASCIIAlnum(r rune) bool {
	return (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9')
}

// refsIsDigit approximates Python str.isdigit(): non-empty and every rune a
// decimal digit (unicode Nd). Accepted deviation: Python also admits
// Numeric_Type=Digit runes outside Nd (superscripts like "²"); those read as
// non-digit here, which only narrows the work-item gate for inputs Azure
// Boards cannot produce.
func refsIsDigit(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !unicode.IsDigit(r) {
			return false
		}
	}
	return true
}
