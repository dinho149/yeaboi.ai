package standup

// automation.go — port of src/yeaboi/standup/automation.py. Keep in lockstep:
// the Python module is the reference implementation;
// tests/parity/test_standup_parity.py diffs whole-pipeline output.
//
// Detect automation/service-hook activity posted under a human's identity.
//
// Motivating incident: a Wiz security-scanner service hook posted PR review
// comments using a team member's PAT, and the standup credited "review
// comments across 18 pull requests" to the human. Author-based bot detection
// cannot catch this — the author IS the human — so this module inspects item
// *content* (scanner signatures, service-hook boilerplate), provider metadata
// (author_type == "bot"), and *volume patterns* (bursts of near-identical
// comments across many repositories).
//
// Pure module, no I/O. Precision over recall, mirroring analysis/ai_usage.py's
// convention: a false "excluded your real work" is worse than a missed bot, so
// default markers are attribution-shaped phrases (never bare product names)
// and the burst heuristic needs several corroborating items before it fires.
// Detection applies ONLY to review/comment kinds — a human commit titled
// "fix Wiz finding" is never touched. Exclusions are always surfaced as
// Notices (see noticeLines), never silent.
//
// Regex parity: Python's \b/\w/\s are unicode, RE2's are ASCII. Patterns
// whose \b sits next to ASCII word characters keep the \b in the RE2 source
// (an exact superset) and post-filter every candidate match with
// wordBoundaryAt (boundary.go). Each _SCANNER_MARKERS alternation is compiled
// per branch, because the branches disagree on where \b appears.

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// Config values for standup_config.automation_handling
// (VALID_AUTOMATION_HANDLING).
var validAutomationHandling = []string{"exclude", "off"}

// Only conversational kinds can be service-hook noise; commits/PRs/work items
// are attributed from provider history and stay untouched (_DETECTABLE_KINDS).
var automationDetectableKinds = map[string]bool{"review": true, "comment": true}

// automationBranch is one alternation branch of a ported Python pattern, with
// the unicode word boundaries the branch requires at its match edges.
type automationBranch struct {
	re     *regexp.Regexp
	leadB  bool
	trailB bool
}

func automationBr(expr string, leadB, trailB bool) automationBranch {
	return automationBranch{re: regexp.MustCompile(expr), leadB: leadB, trailB: trailB}
}

// automationScanner is one _SCANNER_MARKERS entry: (marker id, pattern
// branches).
type automationScanner struct {
	id       string
	branches []automationBranch
}

// --- Layer a: content markers ------------------------------------------------
// Attribution-shaped signatures of security/quality scanners that post PR
// comments via service hooks. Deliberately NOT bare product names ("wiz" alone
// would match "wizard") — org-specific hooks go in the user's
// automation_markers config instead.
var automationScannerMarkers = []automationScanner{
	{"wiz", []automationBranch{
		automationBr(`(?i)\bwiz\.io\b`, true, true),
		automationBr(`(?i)\bwiz(?:cli|-bot)\b`, true, true),
		automationBr(`(?i)\bwiz (?:scan(?:ner)?|security|iac|guardrail)s?\b`, true, true),
		automationBr(`(?i)reported by wiz\b`, false, true),
		automationBr(`(?i)wiz found\b`, false, true),
		automationBr(`(?i)powered by wiz\b`, false, true),
	}},
	{"sonar", []automationBranch{
		automationBr(`(?i)\bsonar(?:qube|cloud|lint|source)\b`, true, true),
		automationBr(`(?i)quality gate (?:passed|failed|is red)`, false, false),
	}},
	{"snyk", []automationBranch{automationBr(`(?i)\bsnyk\b`, true, true)}},
	{"checkmarx", []automationBranch{
		automationBr(`(?i)\bcheckmarx\b`, true, true),
		automationBr(`(?i)\bcx(?:one|sast|flow)\b`, true, true),
	}},
	{"veracode", []automationBranch{automationBr(`(?i)\bveracode\b`, true, true)}},
	{"codeql", []automationBranch{
		automationBr(`(?i)\bcodeql\b`, true, true),
		automationBr(`(?i)github advanced security`, false, false),
	}},
	{"semgrep", []automationBranch{automationBr(`(?i)\bsemgrep\b`, true, true)}},
	{"trivy", []automationBranch{
		automationBr(`(?i)\btrivy\b`, true, true),
		automationBr(`(?i)aquasec(?:urity)?/trivy`, false, false),
	}},
	{"fortify", []automationBranch{
		automationBr(`(?i)\bfortify (?:sast|sca|scan|on demand)\b`, true, true),
		automationBr(`(?i)micro ?focus fortify`, false, false),
	}},
	{"blackduck", []automationBranch{
		automationBr(`(?i)\bblack ?duck\b`, true, true),
		automationBr(`(?i)\bcoverity\b`, true, true),
	}},
	{"prisma", []automationBranch{
		automationBr(`(?i)\bprisma cloud\b`, true, true),
		automationBr(`(?i)\btwistlock\b`, true, true),
	}},
	{"dependency", []automationBranch{
		automationBr(`(?i)\bdependabot\b`, true, true),
		automationBr(`(?i)\brenovate\b`, true, true),
		automationBr(`(?i)\bwhitesource\b`, true, true),
		automationBr(`(?i)\bmend\.io\b`, true, true),
	}},
}

// _SERVICE_HOOK_BOILERPLATE — no \b anywhere, ports directly.
var automationBoilerplateRe = regexp.MustCompile(
	`(?i)this (?:comment|message|review) (?:was|is) (?:auto-?generated|automatically (?:generated|posted))` +
		`|automated (?:security|code|vulnerability|compliance) (?:scan|review|analysis|finding)` +
		`|do not (?:reply|respond) to this (?:automated )?(?:comment|message)` +
		`|posted (?:automatically|by (?:an )?automation|via (?:a )?service hook)` +
		`|this is an automated (?:comment|message|review)`,
)

// --- Layer b: provider metadata ----------------------------------------------
// _BOT_AUTHOR_RE = \[bot\]$ (re.I). Python's $ also matches just before a
// final newline; Go's does not, so the optional \n is spelled out.
var automationBotAuthorRe = regexp.MustCompile(`(?i)\[bot\]\n?$`)

// --- Layer c: burst heuristic --------------------------------------------------
// A masquerading hook posts many near-identical comments in one sweep. Humans
// rarely paste the same ≥40-char text across 3+ repositories in one window.
const (
	automationBurstMinCluster     = 5  // K near-identical items…
	automationBurstMinRepos       = 3  // …across at least M distinct repositories
	automationBurstSingleRepoMin  = 10 // template spam confined to one repo needs more evidence
	automationMinFingerprintChars = 40 // "lgtm" / "nit: typo" repeats never cluster
)

var (
	// _URL_RE: RE2's \S is the ASCII complement [^\t\n\f\r ] — an accepted
	// deviation (a URL followed by unicode whitespace absorbs it here where
	// Python stops).
	automationURLRe = regexp.MustCompile(`https?://\S+`)
	// _HEX_RE: ASCII-\b superset in the source, unicode-filtered at the edges
	// by automationHexSub.
	automationHexRe = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	// _PATH_RE: Python's unicode \w spelled out as letters+numbers+underscore.
	automationPathRe = regexp.MustCompile(`[\p{L}\p{N}_.-]+(?:/[\p{L}\p{N}_.-]+)+`)
	// re.sub(r"\d+", "#"): RE2 \d is ASCII 0-9 where Python's is unicode Nd —
	// an accepted deviation.
	automationDigitsRe = regexp.MustCompile(`\d+`)
)

// automationCluster mirrors the AutomationCluster dataclass: one detected
// group of automated items, for Notices (never logged).
type automationCluster struct {
	// "marker:<id>" | "boilerplate" | "bot-author" | "burst-cross-repo" | "burst-template" | "burst-same-second"
	reason       string
	label        string // human-readable, e.g. "matched 'wiz'" / "near-identical bodies"
	author       string // the identity the items were posted under
	count        int
	kind         string
	repositories []string
	keys         []string // item keys, for debug only
}

// automationMarker is one compiled custom-marker (token, pattern) pair.
type automationMarker struct {
	token string
	re    *regexp.Regexp
}

// parseCustomMarkers mirrors parse_custom_markers: compile the user's
// comma-separated automation_markers config. Each token becomes a
// word-bounded case-insensitive pattern. A bare word ("wiz") is acceptable
// HERE because the user explicitly opted in for their own org's hook
// signature.
//
// The \b pair is NOT put in the RE2 source: a token may start or end with a
// non-word character (e.g. "[bot]"), where RE2's ASCII \b can miss matches
// Python finds. The literal is matched everywhere and wordBoundaryAt applies
// Python's unicode \b at both edges instead.
func parseCustomMarkers(raw string) []automationMarker {
	markers := []automationMarker{}
	for _, token := range strings.Split(raw, ",") {
		token = pysem.Strip(token)
		if token != "" {
			markers = append(markers, automationMarker{
				token: token,
				re:    regexp.MustCompile(`(?i)` + regexp.QuoteMeta(token)),
			})
		}
	}
	return markers
}

// automationSearchBounded mirrors pattern.search(text) for one branch: every
// candidate start position is tried (overlapping included, like Python's
// scan), and a candidate counts only when the branch's required unicode word
// boundaries hold at its edges.
func automationSearchBounded(text string, br automationBranch) bool {
	if !br.leadB && !br.trailB {
		return br.re.MatchString(text)
	}
	off := 0
	for off <= len(text) {
		loc := br.re.FindStringIndex(text[off:])
		if loc == nil {
			return false
		}
		s, e := off+loc[0], off+loc[1]
		if (!br.leadB || wordBoundaryAt(text, s)) && (!br.trailB || wordBoundaryAt(text, e)) {
			return true
		}
		_, size := utf8.DecodeRuneInString(text[s:])
		if size < 1 {
			size = 1
		}
		off = s + size
	}
	return false
}

// automationMarkerHit mirrors _marker_hit: (reason, label) when the item's
// BODY carries an automation signature.
//
// Body only, never the title: review/comment titles are synthesized by our
// fetchers from the PR title ("reviewed PR #12: fix snyk findings"), so a
// human's genuine review of a PR *about* scanner work would otherwise match a
// scanner marker and lose credit. A hook's signature lives in the text it
// posted — the body.
func automationMarkerHit(item *pysem.Obj, customMarkers []automationMarker) (string, string, bool) {
	text := strOr(item, "body")
	for _, m := range customMarkers {
		if automationSearchBounded(text, automationBranch{re: m.re, leadB: true, trailB: true}) {
			return "marker:" + m.token, "matched '" + m.token + "'", true
		}
	}
	for _, scanner := range automationScannerMarkers {
		for _, br := range scanner.branches {
			if automationSearchBounded(text, br) {
				return "marker:" + scanner.id, "matched '" + scanner.id + "'", true
			}
		}
	}
	if automationBoilerplateRe.MatchString(text) {
		return "boilerplate", "service-hook boilerplate", true
	}
	return "", "", false
}

// automationBotAuthorHit mirrors _bot_author_hit.
func automationBotAuthorHit(item *pysem.Obj) (string, string, bool) {
	if strOr(item, "author_type") == "bot" || automationBotAuthorRe.MatchString(strOr(item, "author")) {
		return "bot-author", "bot account", true
	}
	return "", "", false
}

// automationHexSub mirrors _HEX_RE.sub("<hex>", text): left-to-right
// non-overlapping replacement, accepting only matches whose edges are unicode
// word boundaries; a rejected candidate advances the scan one rune, exactly
// like Python trying the next start position.
func automationHexSub(text string) string {
	var b strings.Builder
	last, pos := 0, 0
	for pos <= len(text) {
		loc := automationHexRe.FindStringIndex(text[pos:])
		if loc == nil {
			break
		}
		s, e := pos+loc[0], pos+loc[1]
		if wordBoundaryAt(text, s) && wordBoundaryAt(text, e) {
			b.WriteString(text[last:s])
			b.WriteString("<hex>")
			last, pos = e, e
			continue
		}
		_, size := utf8.DecodeRuneInString(text[s:])
		if size < 1 {
			size = 1
		}
		pos = s + size
	}
	b.WriteString(text[last:])
	return b.String()
}

// automationCollapseSpace mirrors re.sub(r"\s+", " ", text) with Python's
// unicode \s, rune by rune via pysem.IsSpace (Python's re \s and
// str.isspace() agree on the whole set, U+001C..U+001F included).
func automationCollapseSpace(text string) string {
	var b strings.Builder
	inSpace := false
	for _, r := range text {
		if pysem.IsSpace(r) {
			if !inSpace {
				b.WriteByte(' ')
				inSpace = true
			}
			continue
		}
		b.WriteRune(r)
		inSpace = false
	}
	return b.String()
}

// automationFingerprint mirrors _fingerprint: normalize a comment body into a
// template fingerprint.
//
// Scanner comments differ only in paths/line numbers/URLs/hashes — replacing
// those with placeholders makes a whole sweep collapse to one key. Short
// texts return "" so common human repeats ("lgtm") never cluster.
func automationFingerprint(item *pysem.Obj) string {
	// (item.get("body") or item.get("title") or "").lower()
	text := pysem.Lower(pysem.Str(pysem.FirstTruthy(item.Get("body"), item.Get("title"), "")))
	text = automationURLRe.ReplaceAllLiteralString(text, "<url>")
	text = automationHexSub(text)
	text = automationPathRe.ReplaceAllLiteralString(text, "<path>")
	text = automationDigitsRe.ReplaceAllLiteralString(text, "#")
	text = pysem.Strip(automationCollapseSpace(text))
	// Python slices by character, not byte.
	runes := []rune(text)
	if len(runes) < automationMinFingerprintChars {
		return ""
	}
	if len(runes) > 160 {
		return string(runes[:160])
	}
	return text
}

// automationIndexed is one (original index, item) pair.
type automationIndexed struct {
	idx  int
	item *pysem.Obj
}

// automationBurstClusters mirrors _burst_clusters: {item index: (reason,
// label)} for indices caught by volume patterns. Each index appears at most
// once, so map order cannot reach the output; group formation follows
// Python's first-seen dict-insertion order explicitly.
func automationBurstClusters(items []automationIndexed) map[int][2]string {
	hits := map[int][2]string{}

	// Template bursts: same author + kind + normalized body.
	type automationGroupKey [3]string
	templateOrder := []automationGroupKey{}
	byTemplate := map[automationGroupKey][]automationIndexed{}
	for _, entry := range items {
		fp := automationFingerprint(entry.item)
		if fp != "" {
			key := automationGroupKey{pysem.Lower(strOr(entry.item, "author")), strOr(entry.item, "kind"), fp}
			if _, seen := byTemplate[key]; !seen {
				templateOrder = append(templateOrder, key)
			}
			byTemplate[key] = append(byTemplate[key], entry)
		}
	}
	for _, key := range templateOrder {
		group := byTemplate[key]
		repos := map[string]bool{}
		for _, entry := range group {
			repos[strOr(entry.item, "repository")] = true
		}
		var reason, label string
		switch {
		case len(group) >= automationBurstMinCluster && len(repos) >= automationBurstMinRepos:
			reason, label = "burst-cross-repo", fmt.Sprintf("near-identical across %d repositories", len(repos))
		case len(group) >= automationBurstSingleRepoMin:
			reason, label = "burst-template", "near-identical template comments"
		default:
			continue
		}
		for _, entry := range group {
			if _, taken := hits[entry.idx]; !taken {
				hits[entry.idx] = [2]string{reason, label}
			}
		}
	}

	// Same-second sweeps: one identity cannot humanly comment in 3+ repos in
	// one second.
	secondOrder := []automationGroupKey{}
	bySecond := map[automationGroupKey][]automationIndexed{}
	for _, entry := range items {
		ts := strOr(entry.item, "timestamp")
		if ts != "" {
			key := automationGroupKey{pysem.Lower(strOr(entry.item, "author")), strOr(entry.item, "kind"), ts}
			if _, seen := bySecond[key]; !seen {
				secondOrder = append(secondOrder, key)
			}
			bySecond[key] = append(bySecond[key], entry)
		}
	}
	for _, key := range secondOrder {
		group := bySecond[key]
		repos := map[string]bool{}
		for _, entry := range group {
			repos[strOr(entry.item, "repository")] = true
		}
		if len(repos) >= automationBurstMinRepos {
			for _, entry := range group {
				if _, taken := hits[entry.idx]; !taken {
					hits[entry.idx] = [2]string{"burst-same-second", fmt.Sprintf("same-second posts in %d repositories", len(repos))}
				}
			}
		}
	}
	return hits
}

// partitionAutomated mirrors partition_automated: split activity items into
// (kept, automated-clusters).
//
// Kept items preserve input order. Clusters aggregate excluded items by
// (reason, author, kind) so Notices stay short even for big sweeps.
func partitionAutomated(items []*pysem.Obj, customMarkers []automationMarker) ([]*pysem.Obj, []automationCluster) {
	allItems := items
	detectable := []automationIndexed{}
	for idx, item := range allItems {
		if automationDetectableKinds[strOr(item, "kind")] {
			detectable = append(detectable, automationIndexed{idx: idx, item: item})
		}
	}

	flagged := map[int][2]string{}
	for _, entry := range detectable {
		reason, label, ok := automationMarkerHit(entry.item, customMarkers)
		if !ok {
			reason, label, ok = automationBotAuthorHit(entry.item)
		}
		if ok {
			flagged[entry.idx] = [2]string{reason, label}
		}
	}
	for idx, hit := range automationBurstClusters(detectable) {
		if _, taken := flagged[idx]; !taken {
			flagged[idx] = hit
		}
	}

	if len(flagged) == 0 {
		return allItems, []automationCluster{}
	}

	kept := []*pysem.Obj{}
	for idx, item := range allItems {
		if _, isFlagged := flagged[idx]; !isFlagged {
			kept = append(kept, item)
		}
	}

	// grouped: insertion order over sorted(flagged.items()) — first-seen key
	// order, exactly like the Python dict.
	type automationClusterKey [4]string
	flaggedIdxs := make([]int, 0, len(flagged))
	for idx := range flagged {
		flaggedIdxs = append(flaggedIdxs, idx)
	}
	sort.Ints(flaggedIdxs)
	groupOrder := []automationClusterKey{}
	grouped := map[automationClusterKey][]*pysem.Obj{}
	for _, idx := range flaggedIdxs {
		hit := flagged[idx]
		item := allItems[idx]
		key := automationClusterKey{hit[0], hit[1], strOr(item, "author"), strOr(item, "kind")}
		if _, seen := grouped[key]; !seen {
			groupOrder = append(groupOrder, key)
		}
		grouped[key] = append(grouped[key], item)
	}
	clusters := []automationCluster{}
	for _, key := range groupOrder {
		group := grouped[key]
		repoSet := map[string]bool{}
		for _, item := range group {
			if repo := strOr(item, "repository"); repo != "" {
				repoSet[repo] = true
			}
		}
		repositories := pysem.SortedKeys(repoSet)
		keys := make([]string, 0, len(group))
		for _, item := range group {
			keys = append(keys, pysem.Str(item.GetDefault("key", "")))
		}
		clusters = append(clusters, automationCluster{
			reason:       key[0],
			label:        key[1],
			author:       key[2],
			count:        len(group),
			kind:         key[3],
			repositories: repositories,
			keys:         keys,
		})
	}
	return kept, clusters
}

// noticeLines mirrors notice_lines: human-readable Notices explaining exactly
// what was excluded and how to tune it.
//
// One short line per cluster, plus a single shared how-to-tune tail: the
// notice recurs every run, so the config instructions must not — repeating
// them per cluster made a 16-item exclusion a paragraph.
func noticeLines(clusters []automationCluster) []string {
	lines := []string{}
	for _, c := range clusters {
		if strings.HasPrefix(c.reason, "burst") {
			scope := ""
			if len(c.repositories) > 1 {
				scope = fmt.Sprintf(" across %d repositories", len(c.repositories))
			}
			lines = append(lines, fmt.Sprintf(
				"Excluded %d near-identical %s item(s) posted under '%s'%s that look like service-hook automation.",
				c.count, c.kind, c.author, scope,
			))
		} else {
			lines = append(lines, fmt.Sprintf(
				"Excluded %d %s item(s) posted under '%s' that look automated (%s) — not credited as personal work.",
				c.count, c.kind, c.author, c.label,
			))
		}
	}
	if len(lines) > 0 {
		lines = append(lines,
			"Tune 'automation_markers' or set 'automation_handling' to 'off' "+
				"(standup_config_set via the yeaboi MCP server).",
		)
	}
	return lines
}
