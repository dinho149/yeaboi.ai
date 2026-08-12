package analysis

// practices.go — port of src/yeaboi/analysis/practices.py. Keep in lockstep:
// the Python module is the reference implementation;
// tests/parity/test_analysis_parity.py diffs whole-seam output.
//
// Per-member engineering-practice hygiene over attributed commits/PRs: do
// their changes ship with tests, touch or mention docs, reference a ticket,
// and carry meaningful PR descriptions. Pure over its inputs; items without
// ``changed_file_paths`` stay out of the file-based denominators so partial
// coverage never silently deflates a rate.
//
// Depends on isTestPath (the port of code_health._is_test_path) from
// code_health.go in this package.
//
// Regex semantics follow the same rules as classify.go: \b's stay in the RE2
// source (ASCII superset — every boundary-adjacent pattern char is an ASCII
// word char) and every match is re-checked with the unicode wordBoundaryAt;
// unicode-\s pieces are evaluated manually with pysem.IsSpace; \d as ASCII is
// an accepted deviation.

import (
	"encoding/json"
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// minPracticeSample ports MIN_PRACTICE_SAMPLE — below this many items in a
// cell's denominator, renderers show the raw fraction instead of a
// percentage.
const minPracticeSample = 5

// ---------------------------------------------------------------------------
// Ticket references.
// ---------------------------------------------------------------------------

// jiraRe ports _JIRA_REF = \b([A-Z][A-Z0-9]{1,9})-\d+\b — Jira-style ABC-123
// keys; both \b's are post-filtered with the unicode rule.
var jiraRe = regexp.MustCompile(`\b([A-Z][A-Z0-9]{1,9})-\d+\b`)

// jiraFalsePrefixes ports _JIRA_FALSE_PREFIXES — technical tokens that share
// the key shape (UTF-8, SHA-256, CVE-2024-1234, GPT-4, ...) but are not
// tickets.
var jiraFalsePrefixes = map[string]bool{
	"UTF":   true,
	"SHA":   true,
	"MD":    true,
	"ISO":   true,
	"RFC":   true,
	"CVE":   true,
	"GPT":   true,
	"AES":   true,
	"RSA":   true,
	"TLS":   true,
	"IPV":   true,
	"HTTP":  true,
	"HTML":  true,
	"CSS":   true,
	"ES":    true,
	"OAUTH": true,
	"X":     true,
	"PY":    true,
	"V":     true,
}

// azdoRe ports _AZDO_REF = \bAB#\d+\b (IGNORECASE); both \b's post-filtered.
var azdoRe = regexp.MustCompile(`(?i)\bAB#\d+\b`)

// issueRe scans for _ISSUE_REF = (?:^|[\s(\[])#\d+\b candidates. The Python
// alternation deliberately CONSUMES the leading char (not a lookbehind); for
// a boolean search that consumption is unobservable, so the RE2 source drops
// the prefix class and issueRefSearch re-applies it on the previous rune —
// with Python's unicode \s — plus the unicode trailing \b. A rejected span
// ('#'+digits) holds no second '#' to restart on, so reject-and-continue is
// exact.
var issueRe = regexp.MustCompile(`#\d+`)

// squashParenRe is the tail of _SQUASH_SUFFIX = \s*\(#\d+\)\s*$ — see
// stripSquashSuffix for the \s*/$ handling.
var squashParenRe = regexp.MustCompile(`\(#\d+\)$`)

// branchRe ports _BRANCH_REF = (?:^|[/_])([A-Za-z][A-Za-z0-9]{1,9})-\d+
// (IGNORECASE) — every class is ASCII and ^ anchors to start-of-text in both
// engines, so no post-filter is needed.
var branchRe = regexp.MustCompile(`(?i)(?:^|[/_])([A-Za-z][A-Za-z0-9]{1,9})-\d+`)

// branchFalsePrefixes ports _BRANCH_FALSE_PREFIXES — a COMPUTED UNION of the
// branch vocabulary with the lowercased jira denylist, mirrored as the same
// union rather than flattened, so a new jira prefix keeps flowing into both.
var branchFalsePrefixes = func() map[string]bool {
	out := map[string]bool{
		"bugfix":  true,
		"fix":     true,
		"feature": true,
		"feat":    true,
		"release": true,
		"hotfix":  true,
		"version": true,
		"v":       true,
		"part":    true,
		"step":    true,
		"phase":   true,
		"wip":     true,
		"dev":     true,
		"test":    true,
		"issue":   true,
		"pr":      true,
		"sprint":  true,
	}
	for prefix := range jiraFalsePrefixes {
		out[pysem.Lower(prefix)] = true
	}
	return out
}()

// jiraHit ports _jira_hit — any Jira-shaped key whose prefix is not on the
// denylist.
func jiraHit(text string) bool {
	for _, m := range jiraRe.FindAllStringSubmatchIndex(text, -1) {
		if !wordBoundaryAt(text, m[0]) || !wordBoundaryAt(text, m[1]) {
			continue // Python's \b is unicode at both ends
		}
		if !jiraFalsePrefixes[text[m[2]:m[3]]] {
			return true
		}
	}
	return false
}

// azdoSearch mirrors _AZDO_REF.search with the unicode \b post-filters. A
// rejected span ("AB#"+digits) holds no interior "AB#" start, so
// reject-and-continue is exact.
func azdoSearch(text string) bool {
	return boundedSearch(text, azdoRe, true, true)
}

// issueRefSearch mirrors _ISSUE_REF.search — see issueRe.
func issueRefSearch(text string) bool {
	for _, loc := range issueRe.FindAllStringIndex(text, -1) {
		if r, ok := prevRune(text, loc[0]); ok && r != '(' && r != '[' && !pysem.IsSpace(r) {
			continue // (?:^|[\s(\[]) — start of text, unicode whitespace, ( or [
		}
		if !wordBoundaryAt(text, loc[1]) {
			continue // trailing \b is unicode in Python
		}
		return true
	}
	return false
}

// stripSquashSuffix ports _SQUASH_SUFFIX.sub("", title) — \s*\(#\d+\)\s*$.
// Python's \s is unicode and its bare $ also matches before a final newline;
// both are reproduced manually: trailing unicode whitespace (which subsumes
// any final newline) is trimmed, the "(#N)" tail cut, and the whitespace run
// before it goes with it (the leftmost match start is the start of that
// run). Only one match can end at $, so a single application equals sub.
func stripSquashSuffix(title string) string {
	t := strings.TrimRightFunc(title, pysem.IsSpace)
	loc := squashParenRe.FindStringIndex(t)
	if loc == nil {
		return title // no match — sub returns the input untouched
	}
	return strings.TrimRightFunc(t[:loc[0]], pysem.IsSpace)
}

// hasTicketReference ports has_ticket_reference — whether the item's title,
// body, or branch references a work ticket. GitHub's squash-merge "(#123)"
// tail is tooling, not an author practice, so commit titles drop it first.
func hasTicketReference(item *pysem.Obj) bool {
	title := strOr(item, "title")
	if kind, ok := item.Get("kind").(string); ok && kind == "commit" {
		title = stripSquashSuffix(title)
	}
	body := strOr(item, "body")
	branch := strOr(item, "branch")
	text := title + "\n" + body
	if jiraHit(text) || azdoSearch(text) || issueRefSearch(text) {
		return true
	}
	if azdoSearch(branch) {
		return true
	}
	for _, m := range branchRe.FindAllStringSubmatchIndex(branch, -1) {
		if !branchFalsePrefixes[pysem.Lower(branch[m[2]:m[3]])] {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// Docs & context.
// ---------------------------------------------------------------------------

var docSuffixes = map[string]bool{".md": true, ".mdx": true, ".rst": true, ".adoc": true}
var docParts = map[string]bool{
	"docs": true, "doc": true, "documentation": true, "wiki": true, "adr": true, "adrs": true, "rfcs": true,
}
var docNames = map[string]bool{
	"readme": true, "contributing": true, "changelog": true, "architecture": true, "runbook": true,
}

// docMentionRe ports _DOC_MENTION — word-bounded so "docstring" or
// "adrenaline" never count; both \b's post-filtered.
var docMentionRe = regexp.MustCompile(`(?i)\b(documentation|docs|readme|changelog|runbook|adr)\b`)

// posixParts mirrors PurePosixPath(path).parts: a leading root ("/", or "//"
// for exactly two leading slashes — POSIX reserves that spelling), then the
// non-empty segments with "." dropped ("..") kept.
func posixParts(path string) []string {
	parts := []string{}
	rest := path
	if strings.HasPrefix(path, "/") {
		root := "/"
		if strings.HasPrefix(path, "//") && !strings.HasPrefix(path, "///") {
			root = "//"
		}
		parts = append(parts, root)
		rest = strings.TrimLeft(path, "/")
	}
	for _, seg := range strings.Split(rest, "/") {
		if seg == "" || seg == "." {
			continue
		}
		parts = append(parts, seg)
	}
	return parts
}

// posixName mirrors PurePosixPath(path).name.
func posixName(path string) string {
	parts := posixParts(path)
	if len(parts) == 0 || (len(parts) == 1 && strings.HasPrefix(parts[0], "/")) {
		return ""
	}
	return parts[len(parts)-1]
}

// posixSuffix mirrors PurePosixPath(path).suffix: the final "."-extension of
// the name, empty for dotfiles and trailing dots. ('.' is ASCII, so byte
// indices are rune-safe here.)
func posixSuffix(name string) string {
	i := strings.LastIndexByte(name, '.')
	if i > 0 && i < len(name)-1 {
		return name[i:]
	}
	return ""
}

// isDocsPath ports _is_docs_path.
func isDocsPath(path string) bool {
	name := posixName(path)
	if docSuffixes[pysem.Lower(posixSuffix(name))] {
		return true
	}
	for _, part := range posixParts(path) {
		if docParts[pysem.Lower(part)] {
			return true
		}
	}
	stem := strings.SplitN(pysem.Lower(name), ".", 2)[0]
	return docNames[stem]
}

// touchesDocs ports _touches_docs.
func touchesDocs(item *pysem.Obj) bool {
	if paths, ok := item.Get("changed_file_paths").([]any); ok {
		for _, p := range paths {
			if isDocsPath(pysem.Str(p)) {
				return true
			}
		}
	}
	text := strOr(item, "title") + "\n" + strOr(item, "body")
	return boundedSearch(text, docMentionRe, true, true)
}

// ---------------------------------------------------------------------------
// Description quality (PRs only).
// ---------------------------------------------------------------------------

// Thresholds ported from _DESC_LONG / _DESC_MULTILINE / _DESC_STRUCTURED,
// pinned by tests. Lengths count CODE POINTS (Python len()).
const (
	descLong       = 120
	descMultiline  = 60
	descStructured = 40
)

// pyLineBreaks holds every terminator str.splitlines() recognises — a wider
// set than "\n" ("\r\n" counts once).
var pyLineBreaks = map[rune]bool{
	'\n': true, '\r': true, '\v': true, '\f': true,
	0x1c: true, 0x1d: true, 0x1e: true, 0x85: true, 0x2028: true, 0x2029: true,
}

// pySplitLines mirrors str.splitlines() (no trailing empty line for a final
// terminator).
func pySplitLines(s string) []string {
	out := []string{}
	start, i := 0, 0
	for i < len(s) {
		r, size := utf8.DecodeRuneInString(s[i:])
		if !pyLineBreaks[r] {
			i += size
			continue
		}
		out = append(out, s[start:i])
		i += size
		if r == '\r' && i < len(s) && s[i] == '\n' {
			i++
		}
		start = i
	}
	if start < len(s) {
		out = append(out, s[start:])
	}
	return out
}

// hasStructure ports _STRUCTURE.search — (?m)^\s*(#{1,6}\s|[-*]\s|\d+\.\s|
// \[[ xX]\]). Evaluated manually so \s and \d keep Python's unicode
// semantics: from every (?m)^ position ("\n" starts only — narrower than
// splitlines, exactly like Python), skip unicode whitespace (a run crossing a
// newline reaches a token a later line start also reaches, so per-start
// scanning is boolean-equivalent), then match one alternative.
func hasStructure(text string) bool {
	for _, p := range lineStarts(text) {
		i := skipSpace(text, p)
		if i >= len(text) {
			continue
		}
		switch text[i] {
		case '#':
			// #{1,6}\s — greedy with backtracking: only a run of 1..6 '#'
			// immediately followed by whitespace can satisfy it.
			n := 0
			for i+n < len(text) && text[i+n] == '#' {
				n++
			}
			if n <= 6 && isSpaceAt(text, i+n) {
				return true
			}
		case '-', '*':
			if isSpaceAt(text, i+1) {
				return true
			}
		case '[':
			if i+2 < len(text) && (text[i+1] == ' ' || text[i+1] == 'x' || text[i+1] == 'X') && text[i+2] == ']' {
				return true
			}
		}
		// \d+\.\s — Python \d is unicode Nd; maximal digit run then '.'.
		j := i
		for j < len(text) {
			r, size := utf8.DecodeRuneInString(text[j:])
			if !unicode.IsDigit(r) {
				break
			}
			j += size
		}
		if j > i && j < len(text) && text[j] == '.' && isSpaceAt(text, j+1) {
			return true
		}
	}
	return false
}

// isSpaceAt reports whether the rune starting at byte i is Python \s.
func isSpaceAt(s string, i int) bool {
	if i >= len(s) {
		return false
	}
	r, _ := utf8.DecodeRuneInString(s[i:])
	return pysem.IsSpace(r)
}

// hasMeaningfulDescription ports has_meaningful_description — substance, not
// blank/one-liner filler: one solid paragraph, multiple lines, or markdown
// structure with at least minimal length.
func hasMeaningfulDescription(body string) bool {
	text := pysem.Strip(body)
	if text == "" {
		return false
	}
	length := utf8.RuneCountInString(text)
	lines := 0
	for _, line := range pySplitLines(text) {
		if pysem.Strip(line) != "" {
			lines++
		}
	}
	return length >= descLong ||
		(lines >= 2 && length >= descMultiline) ||
		(hasStructure(text) && length >= descStructured)
}

// ---------------------------------------------------------------------------
// Aggregation.
// ---------------------------------------------------------------------------

const agentRowName = "AI agent accounts"

// practRow mirrors _new_row's counters; the wire shape (and its key order)
// is emitted by wire().
type practRow struct {
	member       string
	commits      int64
	prs          int64
	withFileData int64
	testsNum     int64
	testsDen     int64
	docsNum      int64
	docsDen      int64
	ticketNum    int64
	ticketDen    int64
	descNum      int64
	descDen      int64
}

// scoreItem ports _score_item.
func scoreItem(row *practRow, item *pysem.Obj) {
	kind, _ := item.Get("kind").(string)
	if kind == "commit" {
		row.commits++
	} else {
		row.prs++
	}

	row.ticketDen++
	if hasTicketReference(item) {
		row.ticketNum++
	}

	if kind == "pr" {
		row.descDen++
		if hasMeaningfulDescription(strOr(item, "body")) {
			row.descNum++
		}
	}

	v := item.Get("changed_file_paths")
	if v == nil {
		return // no change metadata fetched — stays out of file-based denominators
	}
	row.withFileData++
	row.docsDen++
	if touchesDocs(item) {
		row.docsNum++
	}
	paths, _ := v.([]any)
	production := false
	anyTest := false
	for _, p := range paths {
		path := pysem.Str(p)
		if isTestPath(path) {
			anyTest = true
		} else if !isDocsPath(path) {
			production = true
		}
	}
	if production {
		// Tests-only / docs-only changes have nothing to pair a test with, so
		// they never enter the tests denominator.
		row.testsDen++
		if anyTest {
			row.testsNum++
		}
	}
}

// wire emits the row in _new_row's exact key order, with _finalize's rates.
func (r *practRow) wire() *pysem.Obj {
	o := pysem.EmptyObj()
	o.Set("member", r.member)
	o.Set("commits", r.commits)
	o.Set("prs", r.prs)
	o.Set("with_file_data", r.withFileData)
	o.Set("tests_num", r.testsNum)
	o.Set("tests_den", r.testsDen)
	o.Set("tests_rate", practiceRate(r.testsNum, r.testsDen))
	o.Set("docs_num", r.docsNum)
	o.Set("docs_den", r.docsDen)
	o.Set("docs_rate", practiceRate(r.docsNum, r.docsDen))
	o.Set("ticket_num", r.ticketNum)
	o.Set("ticket_den", r.ticketDen)
	o.Set("ticket_rate", practiceRate(r.ticketNum, r.ticketDen))
	o.Set("desc_num", r.descNum)
	o.Set("desc_den", r.descDen)
	o.Set("desc_rate", practiceRate(r.descNum, r.descDen))
	return o
}

// practiceRate ports _finalize's round(num / den * 100, 1) if den else None.
// A json.Number keeps Python's repr(float) on the wire ("50.0", never "50").
func practiceRate(num, den int64) any {
	if den == 0 {
		return nil
	}
	return json.Number(pysem.FloatRepr(pysem.RoundN(float64(num)/float64(den)*100, 1)))
}

// MemberPractices ports member_practices — practice hygiene per selected
// member over commit/PR items. Human items land on their matched_members
// rows; bot-authored items retained by the member filter land on a trailing
// "AI agent accounts" row. The team row is recomputed over the union of
// items, never averaged from member rates. Result key order (members, team,
// min_sample, file_data) is contractual.
func MemberPractices(items []any, selectedUsers []string) *pysem.Obj {
	order := []string{}
	memberRows := map[string]*practRow{}
	for _, member := range selectedUsers {
		if _, seen := memberRows[member]; !seen {
			order = append(order, member)
		}
		// A duplicate re-creates the row at its first position, exactly like
		// the Python dict comprehension.
		memberRows[member] = &practRow{member: member}
	}
	agentRow := &practRow{member: agentRowName}
	teamRow := &practRow{member: "Team"}
	var withFileData, total int64
	for _, raw := range items {
		item := pysem.AsObj(raw)
		if item == nil {
			continue
		}
		kind, _ := item.Get("kind").(string)
		if kind != "commit" && kind != "pr" {
			continue
		}
		total++
		if item.Get("changed_file_paths") != nil {
			withFileData++
		}
		targets := []*practRow{}
		if matched, ok := item.Get("matched_members").([]any); ok {
			for _, m := range matched {
				if name, isStr := m.(string); isStr && memberRows[name] != nil {
					targets = append(targets, memberRows[name])
				}
			}
		}
		if len(targets) == 0 && pysem.Truthy(item.Get("agent_authored")) {
			targets = []*practRow{agentRow}
		}
		for _, row := range targets {
			scoreItem(row, item)
		}
		scoreItem(teamRow, item)
	}

	members := make([]*practRow, 0, len(order))
	for _, member := range order {
		members = append(members, memberRows[member])
	}
	sort.SliceStable(members, func(i, j int) bool {
		vi, vj := members[i].commits+members[i].prs, members[j].commits+members[j].prs
		if vi != vj {
			return vi > vj
		}
		return members[i].member < members[j].member
	})
	if agentRow.commits != 0 || agentRow.prs != 0 {
		members = append(members, agentRow)
	}

	membersWire := []any{}
	for _, row := range members {
		membersWire = append(membersWire, row.wire())
	}
	fileData := pysem.EmptyObj()
	fileData.Set("with_file_data", withFileData)
	fileData.Set("total", total)
	out := pysem.EmptyObj()
	out.Set("members", membersWire)
	out.Set("team", teamRow.wire())
	out.Set("min_sample", int64(minPracticeSample))
	out.Set("file_data", fileData)
	return out
}
