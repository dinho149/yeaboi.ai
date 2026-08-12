package analysis

// classify.go — port of the pure classifiers of
// src/yeaboi/analysis/ai_usage.py (the marker tables, _classify_ai_markers,
// _classify_ai_authors, _classify_ai_item, _activity_bucket,
// aggregate_ai_markers, _collect_samples). Keep in lockstep: the Python
// module is the reference implementation; tests/parity/test_analysis_parity.py
// diffs whole-seam output.
//
// Data model: activity items stay generic ordered JSON (*pysem.Obj), exactly
// as Python works on dicts — values are only coerced at use sites, so a
// collector field that arrives as a number is emitted back byte-identical to
// the reference.
//
// Privacy: nothing from the items — titles, bodies, authors — is ever logged
// (no log import anywhere in this package) and never appears in an error.
//
// Regex semantics: Python's \b/\w are unicode, RE2's are ASCII, and Python's
// \s is unicode where RE2's is ASCII. Every pattern below keeps as much of
// the Python source as RE2 can express exactly; where they differ, the match
// is either post-filtered with wordBoundaryAt (each \b stays in the RE2
// source — for these patterns the boundary-adjacent characters are ASCII word
// characters, so the ASCII matches are a superset of Python's, and a rejected
// span can never hide a later Python-valid start: every span's interior is
// ASCII word characters with no second start position) or evaluated manually
// with pysem.IsSpace (the unicode \s cases). \d as ASCII is an accepted
// deviation throughout (a digit run adjoining non-ASCII digits reads
// differently, and no collector produces one). re.IGNORECASE and Go's (?i)
// both apply simple case folding — equivalent for these ASCII patterns.

import (
	"encoding/json"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// ---------------------------------------------------------------------------
// Boundary helpers — Python-re unicode \b / lookaround emulation on top of
// RE2, copied from go/internal/standup/boundary.go (packages cannot share
// unexported helpers; keep the two copies in lockstep).
// ---------------------------------------------------------------------------

// wordBoundaryAt reports whether Python's unicode \b would match between
// byte positions i-1 and i of s (i in 0..len(s)).
func wordBoundaryAt(s string, i int) bool {
	var before, after bool
	if i > 0 {
		r, _ := utf8.DecodeLastRuneInString(s[:i])
		before = pysem.IsWordRune(r)
	}
	if i < len(s) {
		r, _ := utf8.DecodeRuneInString(s[i:])
		after = pysem.IsWordRune(r)
	}
	return before != after
}

// prevRune returns the rune ending at byte position i of s, or utf8.RuneError
// with ok=false at the start of the string. Used to emulate lookbehinds and
// the consumed-prefix alternations.
func prevRune(s string, i int) (rune, bool) {
	if i <= 0 {
		return utf8.RuneError, false
	}
	r, _ := utf8.DecodeLastRuneInString(s[:i])
	return r, true
}

// boundedSearch mirrors pattern.search for a pattern whose only non-RE2-exact
// pieces are edge \b's: every RE2 match (an ASCII superset) is re-checked with
// the unicode boundary rule at the requested edges.
func boundedSearch(text string, re *regexp.Regexp, leading, trailing bool) bool {
	for _, loc := range re.FindAllStringIndex(text, -1) {
		if leading && !wordBoundaryAt(text, loc[0]) {
			continue
		}
		if trailing && !wordBoundaryAt(text, loc[1]) {
			continue
		}
		return true
	}
	return false
}

// lineStarts returns every position Python's (?m)^ matches at: 0 and the
// position after each "\n" (including len(s) after a trailing newline).
func lineStarts(s string) []int {
	out := []int{0}
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			out = append(out, i+1)
		}
	}
	return out
}

// skipSpace advances past Python-\s runes (unicode — pysem.IsSpace — so it
// may cross newlines, exactly as a \s* does).
func skipSpace(s string, i int) int {
	for i < len(s) {
		r, size := utf8.DecodeRuneInString(s[i:])
		if !pysem.IsSpace(r) {
			break
		}
		i += size
	}
	return i
}

// ---------------------------------------------------------------------------
// Shared item accessors (the Python dict idioms).
// ---------------------------------------------------------------------------

// strOr mirrors `str(item.get(key, "") or "")`.
func strOr(item *pysem.Obj, key string) string {
	v := item.Get(key)
	if !pysem.Truthy(v) {
		return ""
	}
	return pysem.Str(v)
}

// strippedOr mirrors `(item.get(key) or "").strip()`.
func strippedOr(item *pysem.Obj, key string) string {
	return pysem.Strip(strOr(item, key))
}

// ---------------------------------------------------------------------------
// Marker table — ports _AI_MARKERS, same order. Each Python pattern is an
// alternation; the RE2-exact branches stay one compiled regex and the
// unicode-\b / unicode-\s branches are evaluated by the helpers below.
// ---------------------------------------------------------------------------

var coauthoredByRe = regexp.MustCompile(`(?i)co-authored-by:`)

// coauthorThenLiteral ports the `co-authored-by:\s*<literal>` branch shape
// (claude's trailer). Python's \s* is unicode, so the run after the colon is
// skipped with pysem.IsSpace and the literal compared case-insensitively
// (ASCII literal — EqualFold's simple folding equals both engines' folding).
func coauthorThenLiteral(text, literal string) bool {
	for _, loc := range coauthoredByRe.FindAllStringIndex(text, -1) {
		i := skipSpace(text, loc[1])
		if i+len(literal) <= len(text) && strings.EqualFold(text[i:i+len(literal)], literal) {
			return true
		}
	}
	return false
}

// coauthorLineWord ports the `co-authored-by:.*\b<word>\b` branch shape.
// Python's greedy `.*` backtracks, so the word may sit ANYWHERE after the
// colon on the same line (`.` crosses neither engine's "\n") — a single
// leftmost RE2 match of the whole branch would miss an earlier word when the
// last one fails the unicode boundary filter, so every co-author prefix and
// every word occurrence after it is tried instead.
func coauthorLineWord(text string, wordRe *regexp.Regexp) bool {
	for _, loc := range coauthoredByRe.FindAllStringIndex(text, -1) {
		e := loc[1]
		lineEnd := strings.IndexByte(text[e:], '\n')
		if lineEnd < 0 {
			lineEnd = len(text)
		} else {
			lineEnd += e
		}
		for _, m := range wordRe.FindAllStringIndex(text[e:lineEnd], -1) {
			if wordBoundaryAt(text, e+m[0]) && wordBoundaryAt(text, e+m[1]) {
				return true
			}
		}
	}
	return false
}

// aiderSubjectPrefix ports the `^\s*aider:\s` (IGNORECASE|MULTILINE) branch:
// from every (?m)^ position, unicode whitespace is skipped (a \s* crossing a
// newline reaches text a later line start also reaches, so per-start scanning
// is boolean-equivalent), then the literal and one trailing Python-\s rune.
func aiderSubjectPrefix(text string) bool {
	for _, p := range lineStarts(text) {
		i := skipSpace(text, p)
		if i+6 > len(text) || !strings.EqualFold(text[i:i+6], "aider:") {
			continue
		}
		if r, size := utf8.DecodeRuneInString(text[i+6:]); size > 0 && pysem.IsSpace(r) {
			return true
		}
	}
	return false
}

var (
	claudeRe      = regexp.MustCompile(`(?i)generated with \[?claude code|noreply@anthropic\.com|claude\.com/claude-code`)
	copilotRe     = regexp.MustCompile(`(?i)github-copilot\[bot\]|co-authored-by:.*copilot|copilot@github\.com|gpt-4-copilot`)
	codexWordRe   = regexp.MustCompile(`(?i)\bcodex\b`)
	codexRe       = regexp.MustCompile(`(?i)generated (?:with|by) codex|chatgpt\.com/codex|openai\.com/codex`)
	cursorWordRe  = regexp.MustCompile(`(?i)\bcursor\b`)
	cursorRe      = regexp.MustCompile(`(?i)generated (?:with|by) cursor|agent@cursor\.com|cursor\.com/agents`)
	aiderWordRe   = regexp.MustCompile(`(?i)\baider\b`)
	aiderChatRe   = regexp.MustCompile(`(?i)\baider\.chat\b`)
	aiderParenRe  = regexp.MustCompile(`(?i)\(aider\)`)
	devinWordRe   = regexp.MustCompile(`(?i)\bdevin\b`)
	devinBotRe    = regexp.MustCompile(`(?i)devin-ai-integration\[bot\]`)
	devinAiRe     = regexp.MustCompile(`(?i)\bdevin\.ai\b`)
	codeiumWordRe = regexp.MustCompile(`(?i)\b(codeium|windsurf)\b`)
	codeiumComRe  = regexp.MustCompile(`(?i)\bcodeium\.com\b`)
	windsurfComRe = regexp.MustCompile(`(?i)\bwindsurf\.com\b`)
	codeiumGenRe  = regexp.MustCompile(`(?i)generated (?:with|by) (?:codeium|windsurf)`)
)

// aiMarkers ports _AI_MARKERS: (tool_id, does-the-pattern-match), same order.
// Order matters only in that “other_ai“ is handled after the table (see
// classifyAiMarkers).
var aiMarkers = []struct {
	id    string
	match func(text string) bool
}{
	{"claude", func(t string) bool { return coauthorThenLiteral(t, "claude") || claudeRe.MatchString(t) }},
	{"copilot", func(t string) bool { return copilotRe.MatchString(t) }},
	{"codex", func(t string) bool { return coauthorLineWord(t, codexWordRe) || codexRe.MatchString(t) }},
	{"cursor", func(t string) bool { return coauthorLineWord(t, cursorWordRe) || cursorRe.MatchString(t) }},
	{"aider", func(t string) bool {
		return coauthorLineWord(t, aiderWordRe) || boundedSearch(t, aiderChatRe, true, true) ||
			aiderParenRe.MatchString(t) || aiderSubjectPrefix(t)
	}},
	{"devin", func(t string) bool {
		return coauthorLineWord(t, devinWordRe) || devinBotRe.MatchString(t) ||
			boundedSearch(t, devinAiRe, true, true)
	}},
	{"codeium", func(t string) bool {
		return coauthorLineWord(t, codeiumWordRe) || boundedSearch(t, codeiumComRe, true, true) ||
			boundedSearch(t, windsurfComRe, true, true) || codeiumGenRe.MatchString(t)
	}},
}

// aiAuthorMarkers ports _AI_AUTHOR_MARKERS — anchored account shapes, pure
// RE2. Python's un-flagged `$` also matches before a FINAL newline where Go's
// matches only end-of-text; both inputs are str.strip()ped before these run,
// so no trailing newline can reach the anchor and the two agree. `\s?` in the
// cursor pattern is ASCII where Python's is unicode — accepted deviation (a
// non-ASCII space inside an account name does not occur).
var aiAuthorMarkers = []struct {
	id string
	re *regexp.Regexp
}{
	{"claude", regexp.MustCompile(`(?i)^claude(\[bot\])?$|noreply@anthropic\.com$`)},
	{"codex", regexp.MustCompile(`(?i)^(openai-)?codex(\[bot\])?$|^chatgpt-codex-connector(\[bot\])?$`)},
	{"copilot", regexp.MustCompile(
		`(?i)^(github-)?copilot(-swe-agent)?(\[bot\])?$|^copilot@github\.com$|copilot@users\.noreply\.github\.com$`)},
	{"cursor", regexp.MustCompile(`(?i)^cursor\s?agent$|@cursor\.com$`)},
	{"aider", regexp.MustCompile(`(?i)\(aider\)$`)}, // aider suffixes the human author name
	{"devin", regexp.MustCompile(`(?i)^devin-ai-integration(\[bot\])?$|@devin\.ai$`)},
	{"gemini", regexp.MustCompile(`(?i)^google-labs-jules(\[bot\])?$`)},
}

// aiBranchMarkers ports _AI_BRANCH_MARKERS — prefix-anchored agent branch
// names. `^` without (?m) anchors to start-of-text in both engines.
var aiBranchMarkers = []struct {
	id string
	re *regexp.Regexp
}{
	{"codex", regexp.MustCompile(`(?i)^codex/`)},
	{"copilot", regexp.MustCompile(`(?i)^copilot/`)},
	{"cursor", regexp.MustCompile(`(?i)^cursor/`)},
	{"devin", regexp.MustCompile(`(?i)^devin/`)},
}

// otherAiNameRe / automationBotNameRe port _OTHER_AI_NAME /
// _AUTOMATION_BOT_NAME — word-bounded name vocabularies, edges post-filtered.
var (
	otherAiNameRe       = regexp.MustCompile(`(?i)\b(ai|assistant|llm|gpt|chatgpt|openai|gemini|agent)\b`)
	automationBotNameRe = regexp.MustCompile(
		`(?i)\b(dependabot|renovate|greenkeeper|snyk|github-actions|imgbot|allcontributors|whitesource|mend` +
			`|pre-commit-ci|codecov|semantic-release|release-please|pyup)\b`)
)

// coauthorNames ports _COAUTHOR_LINE.finditer — the captured name of every
// `^\s*co-authored-by:\s*([^<\n]+)` match, in order. Both \s*'s are unicode
// in Python (they may cross newlines; a crossing run is also reached from the
// later line start, so per-start scanning stays boolean-equivalent for the
// other_ai gate). When everything between the colon and the '<' is
// whitespace, Python's backtracking captures a whitespace-only name — which
// can never carry a \b-bounded word, so it is equivalently skipped here.
func coauthorNames(text string) []string {
	const prefix = "co-authored-by:"
	names := []string{}
	for _, p := range lineStarts(text) {
		q := skipSpace(text, p)
		if q+len(prefix) > len(text) || !strings.EqualFold(text[q:q+len(prefix)], prefix) {
			continue
		}
		s := skipSpace(text, q+len(prefix))
		if s >= len(text) || text[s] == '<' {
			continue
		}
		end := s
		for end < len(text) && text[end] != '<' && text[end] != '\n' {
			end++
		}
		names = append(names, text[s:end])
	}
	return names
}

// docsTitleAlts ports _DOCS_TITLE, one alternative per entry (boolean OR, so
// alternation order is immaterial): \breadme\b | \bdocs?/ | \.md\b |
// \bdocumentation\b | \bchangelog\b, each edge \b post-filtered.
var docsTitleAlts = []struct {
	re                *regexp.Regexp
	leading, trailing bool
}{
	{regexp.MustCompile(`(?i)\breadme\b`), true, true},
	{regexp.MustCompile(`(?i)\bdocs?/`), true, false},
	{regexp.MustCompile(`(?i)\.md\b`), false, true},
	{regexp.MustCompile(`(?i)\bdocumentation\b`), true, true},
	{regexp.MustCompile(`(?i)\bchangelog\b`), true, true},
}

func docsTitleSearch(text string) bool {
	for _, alt := range docsTitleAlts {
		if boundedSearch(text, alt.re, alt.leading, alt.trailing) {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// Classifiers.
// ---------------------------------------------------------------------------

// classifyAiMarkers ports _classify_ai_markers: the set of AI-tool ids whose
// markers appear in text. “other_ai“ fires only when NO specific tool
// matched, and only for a co-author line whose name looks like an AI and is
// not dependency automation. Empty text returns the empty set.
func classifyAiMarkers(text string) map[string]bool {
	hits := map[string]bool{}
	if text == "" {
		return hits
	}
	for _, marker := range aiMarkers {
		if marker.match(text) {
			hits[marker.id] = true
		}
	}
	if len(hits) == 0 {
		for _, name := range coauthorNames(text) {
			if boundedSearch(name, otherAiNameRe, true, true) && !boundedSearch(name, automationBotNameRe, true, true) {
				hits["other_ai"] = true
				break
			}
		}
	}
	return hits
}

// classifyAiAuthors ports _classify_ai_authors: tool ids whose agent/bot
// account shape matches the git author identity.
func classifyAiAuthors(author, authorEmail string) map[string]bool {
	name := pysem.Strip(author)
	email := pysem.Strip(authorEmail)
	hits := map[string]bool{}
	if name == "" && email == "" {
		return hits
	}
	for _, marker := range aiAuthorMarkers {
		if marker.re.MatchString(name) || marker.re.MatchString(email) {
			hits[marker.id] = true
		}
	}
	if len(hits) == 0 && strings.HasSuffix(pysem.Lower(name), "[bot]") {
		if boundedSearch(name, otherAiNameRe, true, true) && !boundedSearch(name, automationBotNameRe, true, true) {
			hits["other_ai"] = true
		}
	}
	return hits
}

// ClassifyAiItem ports _classify_ai_item: the union of text, author-account
// and PR source-branch markers, with a specific tool hit suppressing
// “other_ai“. Exported for the analysis.score_code seam.
func ClassifyAiItem(item *pysem.Obj) map[string]bool {
	// Python: f"{item.get('title', '')}\n{item.get('body', '')}" — an explicit
	// null renders as "None", exactly like str(None).
	text := pysem.Str(item.GetDefault("title", "")) + "\n" + pysem.Str(item.GetDefault("body", ""))
	hits := classifyAiMarkers(text)
	for id := range classifyAiAuthors(pysem.Str(item.GetDefault("author", "")), pysem.Str(item.GetDefault("author_email", ""))) {
		hits[id] = true
	}
	branch := strOr(item, "branch")
	if branch != "" {
		for _, marker := range aiBranchMarkers {
			if marker.re.MatchString(branch) {
				hits[marker.id] = true
			}
		}
	}
	if len(hits) > 1 {
		delete(hits, "other_ai")
	}
	return hits
}

// activityBucket ports _activity_bucket: pr / docs / code.
func activityBucket(item *pysem.Obj) string {
	if kind, ok := item.Get("kind").(string); ok && kind == "pr" {
		return "pr"
	}
	// Python: str(item.get("title", "")) — no `or`, so null renders "None".
	if docsTitleSearch(pysem.Str(item.GetDefault("title", ""))) {
		return "docs"
	}
	return "code"
}

// ---------------------------------------------------------------------------
// Aggregation.
// ---------------------------------------------------------------------------

// AggregateAiMarkers ports aggregate_ai_markers, emitting the signal directly
// in its wire shape (aggregate.signal_to_wire's key order — contractual):
// scanned_commits, scanned_prs, ai_commits, ai_prs, footprint_pct, per_tool,
// per_author, per_activity, per_source, sources_scanned, is_lower_bound.
func AggregateAiMarkers(items []any) *pysem.Obj {
	var scannedCommits, scannedPRs, aiCommits, aiPRs int64
	perTool := map[string]int64{}
	perAuthor := map[string]int64{}
	perActivity := map[string]int64{}
	perSource := map[string]int64{}
	sources := []string{}
	seenSource := map[string]bool{}

	for _, raw := range items {
		item := pysem.AsObj(raw)
		if item == nil {
			continue
		}
		kind, _ := item.Get("kind").(string)
		isPR := kind == "pr"
		switch {
		case isPR:
			scannedPRs++
		case kind == "commit":
			scannedCommits++
		default:
			continue // only commits/PRs carry an AI footprint
		}

		// Python: str(item.get("source", "")).strip() — an explicit null
		// becomes "None" and IS counted as a source, like the reference.
		src := pysem.Strip(pysem.Str(item.GetDefault("source", "")))
		if src != "" && !seenSource[src] {
			seenSource[src] = true
			sources = append(sources, src)
		}

		tools := ClassifyAiItem(item)
		if len(tools) == 0 {
			continue
		}

		if isPR {
			aiPRs++
		} else {
			aiCommits++
		}
		for t := range tools {
			perTool[t]++
		}
		author := strippedOr(item, "author")
		if author == "" {
			author = "unknown"
		}
		perAuthor[author]++
		perActivity[activityBucket(item)]++
		if src != "" {
			perSource[src]++
		}
	}

	scanned := scannedCommits + scannedPRs
	footprint := 0.0
	if scanned != 0 {
		footprint = pysem.RoundN(float64(aiCommits+aiPRs)/float64(scanned)*100, 1)
	}

	sourcesWire := []any{}
	for _, src := range sources {
		sourcesWire = append(sourcesWire, src)
	}

	out := pysem.EmptyObj()
	out.Set("scanned_commits", scannedCommits)
	out.Set("scanned_prs", scannedPRs)
	out.Set("ai_commits", aiCommits)
	out.Set("ai_prs", aiPRs)
	// A json.Number so both serializers render Python repr(float) ("0.0",
	// never "0") — a float64 through encoding/json would drop the ".0".
	out.Set("footprint_pct", json.Number(pysem.FloatRepr(footprint)))
	out.Set("per_tool", sortedPairs(perTool))
	out.Set("per_author", sortedPairs(perAuthor))
	out.Set("per_activity", sortedPairs(perActivity))
	out.Set("per_source", sortedPairs(perSource))
	out.Set("sources_scanned", sourcesWire)
	out.Set("is_lower_bound", true)
	return out
}

// sortedPairs ports _sorted_pairs: [[name, count], ...] sorted by
// (-count, name). Go's bytewise string order equals Python's code-point
// order over UTF-8, and the name makes the key total, so map iteration
// order cannot leak through.
func sortedPairs(counts map[string]int64) []any {
	names := make([]string, 0, len(counts))
	for name := range counts {
		names = append(names, name)
	}
	sort.Slice(names, func(i, j int) bool {
		if counts[names[i]] != counts[names[j]] {
			return counts[names[i]] > counts[names[j]]
		}
		return names[i] < names[j]
	})
	pairs := []any{}
	for _, name := range names {
		pairs = append(pairs, []any{name, counts[name]})
	}
	return pairs
}

// ---------------------------------------------------------------------------
// Evidence samples.
// ---------------------------------------------------------------------------

// CollectSamples ports _collect_samples: AI-marked evidence items for the
// report (never bodies), item key order author, tool, activity, title,
// source, key, url. A negative limit mirrors Python's “limit=None“ (no
// cap); like the reference, the cap is checked after appending, so limit=0
// still returns the first sample.
func CollectSamples(items []any, limit int) []any {
	out := []any{}
	for _, raw := range items {
		item := pysem.AsObj(raw)
		if item == nil {
			continue
		}
		tools := ClassifyAiItem(item)
		if len(tools) == 0 {
			continue
		}
		names := make([]string, 0, len(tools))
		for t := range tools {
			names = append(names, t)
		}
		sort.Strings(names) // sorted(tools)[0]

		author := strippedOr(item, "author")
		if author == "" {
			author = "unknown"
		}
		sample := pysem.EmptyObj()
		sample.Set("author", author)
		sample.Set("tool", names[0])
		sample.Set("activity", activityBucket(item))
		// Python slices by CODE POINT: str(...)[:80].
		sample.Set("title", truncRunes(pysem.Str(item.GetDefault("title", "")), 80))
		sample.Set("source", item.GetDefault("source", ""))
		sample.Set("key", pysem.Str(item.GetDefault("key", "")))
		sample.Set("url", item.GetDefault("url", ""))
		out = append(out, sample)
		if limit >= 0 && len(out) >= limit {
			break
		}
	}
	return out
}

// truncRunes mirrors s[:n] — Python slices strings by code point, not byte.
func truncRunes(s string, n int) string {
	runes := []rune(s)
	if len(runes) <= n {
		return s
	}
	return string(runes[:n])
}
