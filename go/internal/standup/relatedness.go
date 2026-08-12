// relatedness.go — port of src/yeaboi/standup/relatedness.py. Keep in
// lockstep: the Python module is the reference implementation;
// tests/parity/test_standup_parity.py diffs whole-pipeline output.
//
// Does this change plausibly belong to a ticket it never names?
//
// references.py answers a syntactic question — does this text *say* a ticket
// key, under a gate the tracker produced — and its True is strong enough that
// export.py turns it into a hyperlink. This module answers a softer one, and
// its True is only ever safe to stay quiet with. They are deliberately
// separate modules: one truth value that linkifies and one that merely
// suppresses must not live behind the same import, or someone eventually
// linkifies a guess.
//
// THE GOVERNING INVARIANT: relatedness may only ever SUPPRESS a practice
// signal, never create or strengthen one. Every predicate here is one-sided in
// that direction, and habits.go calls them only to *drop* a change from a
// report. A wrong match costs a missed nudge. A wrong accusation costs trust
// in a message that names a person. That asymmetry decides every threshold
// below, and it is why the matched ticket is never surfaced: if the reader
// never sees which ticket we guessed, guessing the wrong sibling ticket costs
// exactly nothing.
//
// Two tiers, because a match is only as trustworthy as its candidate pool:
//
//   - Tier A — tickets this member touched today, or holds open. Every
//     predicate applies, including the word-overlap ones.
//   - Tier B — every other ticket in the window. Only the strong predicates:
//     the ticket naming the change outright, or a rare compound identifier.
//     Tier B exists because a lead who pushes a fix on someone else's ticket,
//     without ever touching the ticket, would otherwise be reported for
//     untracked work.
//
// Why bare token overlap would be fatal here: against a 300-word description,
// two shared content words is near-certain for *any* pair, and tracker text is
// the most repetitive text there is (definition-of-done boilerplate copied
// onto every ticket). Three defences, in order of how much work they do:
//
//  1. Rarity. A token counts only if it appears in at most a quarter of the
//     window's tickets. Boilerplate has a document frequency of *all of them*
//     by construction, so it self-cancels with no hand-maintained stoplist.
//  2. Coverage is denominated on the CHANGE's tokens, never the ticket's.
//  3. A ceiling on ticket size. Above relHugeTicketTokens the word predicates
//     are inadmissible outright.
package standup

import (
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// --- tokenizer -------------------------------------------------------------

// relPySpace spells Python's unicode \s (the str.isspace set) as an RE2 class
// body, so the ported patterns keep Python's whitespace semantics instead of
// RE2's ASCII \s.
const relPySpace = `\t-\r\x{1C}-\x{1F} \x{85}\x{A0}\p{Zs}\p{Zl}\p{Zp}`

const relSpaceCls = `[` + relPySpace + `]`

var (
	// Content words: four characters or more, so "fix"/"the"/"api" never carry
	// a match on their own. (_WORD_RE — runs on already-lowered text.)
	relWordRe = regexp.MustCompile(`[a-z][a-z0-9]{3,}`)
	// A compound a human deliberately typed as ONE token: pipeline-approval,
	// access_request, standup/habits, foo.bar. This is the high-signal class.
	// (_COMPOUND_RE — runs on raw text.)
	relCompoundRe = regexp.MustCompile(`[A-Za-z][A-Za-z0-9]*(?:[-_./][A-Za-z0-9]+)+`)
	// camelCase / PascalCase, canonicalised to the same hyphenated form. The
	// leading character may be either case. (_CAMEL_RE — raw text.)
	relCamelRe = regexp.MustCompile(`[A-Za-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+`)
	relSplitRe = regexp.MustCompile(`[-_./]+`)
	// _CAMEL_SPLIT_RE ((?<=[a-z0-9])(?=[A-Z])) is a lookaround RE2 cannot
	// express; relCamelSplit hand-rolls it.
	// Word occurrences with their offsets, for the ticket-side bigram pass.
	relRunRe = regexp.MustCompile(`[A-Za-z][A-Za-z0-9]*`)
)

const (
	relMinIdentPartChars = 3 // _MIN_IDENT_PART_CHARS
	relMinIdentChars     = 9 // _MIN_IDENT_CHARS
)

// Structural English plus the words every engineering tracker repeats. The
// rarity gate handles project-specific boilerplate on its own; this list only
// has to cover the small-corpus case (two or three tickets in a window) where
// document frequency carries almost no information — weekends, holidays.
var relStopwords = map[string]bool{
	"this": true, "that": true, "with": true, "from": true, "into": true,
	"when": true, "then": true, "than": true, "them": true, "they": true,
	"their": true, "there": true, "should": true, "would": true, "could": true,
	"have": true, "will": true, "been": true, "being": true, "does": true,
	"done": true, "make": true, "made": true, "also": true, "only": true,
	"some": true, "such": true, "each": true, "both": true, "must": true,
	"need": true, "needs": true, "want": true, "wants": true, "the": true,
	"and": true, "for": true, "are": true, "was": true, "not": true,
	"use": true, "its": true, "via": true, "per": true, "new": true,
	"any": true, "all": true, "our": true, "page": true, "ticket": true,
	"story": true, "task": true, "issue": true, "item": true, "items": true,
	"work": true, "sprint": true, "board": true, "code": true, "file": true,
	"files": true, "change": true, "changes": true, "update": true,
	"updates": true, "user": true, "users": true, "test": true, "tests": true,
	"testing": true, "documentation": true, "docs": true, "review": true,
	"reviewed": true, "merged": true, "merge": true, "acceptance": true,
	"criteria": true, "definition": true, "released": true, "release": true,
	"sign": true, "stakeholder": true, "knowledge": true, "sharing": true,
	"given": true, "able": true,
}

// --- thresholds ------------------------------------------------------------

const (
	// A token is "rare" when at most this share of the window's tickets
	// mention it. Self-tuning: with four tickets the bar is one, with forty it
	// is ten. (_RARE_DF_RATIO)
	relRareDFRatio = 0.25
	// Above this many body tokens, word predicates are inadmissible entirely.
	relHugeTicketTokens = 300 // _HUGE_TICKET_TOKENS

	relTitleCoverage   = 0.50 // _TITLE_COVERAGE
	relSubjectCoverage = 0.60 // _SUBJECT_COVERAGE
	relBranchCoverage  = 0.60 // _BRANCH_COVERAGE
	relDocsCoverage    = 0.34 // _DOCS_COVERAGE

	relMinChangeTokens    = 3 // _MIN_CHANGE_TOKENS
	relMinRareWords       = 3 // _MIN_RARE_WORDS
	relMinRareWordsTitle  = 2 // _MIN_RARE_WORDS_TITLE
	relMinRareWordsBranch = 2 // _MIN_RARE_WORDS_BRANCH
	relMinRareWordsDocs   = 1 // _MIN_RARE_WORDS_DOCS

	relMinPathTokenChars = 5 // _MIN_PATH_TOKEN_CHARS
	relPathRareHits      = 2 // _PATH_RARE_HITS
	// A forty-file pull request offers forty basenames, so accidental
	// suppression gets steadily likelier on exactly the change you most want
	// reported. Past this many files the bar goes up rather than staying flat.
	relPathShotgunMax      = 25 // _PATH_SHOTGUN_MAX
	relPathRareHitsShotgun = 3  // _PATH_RARE_HITS_SHOTGUN
	relMinShaChars         = 7  // _MIN_SHA_CHARS
	// Bounds the per-change work. Hitting the cap can only LOSE a match, i.e.
	// only produce a report — the safe direction.
	relMaxCandidatesPerChange = 8    // _MAX_CANDIDATES_PER_CHANGE
	relMaxTicketBodyChars     = 4000 // _MAX_TICKET_BODY_CHARS
)

// Branch-name segments that name a workflow, not the work: strip before
// reading the slug. The segment following a "users"-family namespace is an
// author name and goes too.
var relBranchNamespaces = map[string]bool{
	"feature": true, "feat": true, "fix": true, "bugfix": true, "hotfix": true,
	"chore": true, "release": true, "refactor": true, "docs": true,
	"doc": true, "test": true, "dev": true,
}

var relBranchActorNamespaces = map[string]bool{"users": true, "user": true, "personal": true}

// Basenames so common they identify nothing.
var relGenericBasenames = map[string]bool{
	"index": true, "main": true, "utils": true, "util": true, "types": true,
	"init": true, "test": true, "tests": true, "conftest": true, "setup": true,
	"readme": true, "config": true, "const": true, "constants": true,
	"helpers": true, "common": true, "base": true,
}

// A checklist-shaped documentation line in a definition of done. Matches
// "- [ ] Documentation", "* Docs updated", "Definition of done: user guide" —
// and deliberately not the word "documented" inside a prose sentence, because
// this gate is what keeps the relaxed documentation bar honest.
// (_DOD_DOC_RE — the `docs\b` keeps its \b in the RE2 source; ASCII \b is a
// superset of Python's unicode \b there, and relDoDDocSearch post-filters.)
var relDoDDocRe = regexp.MustCompile(
	`(?im)^` + relSpaceCls + `*(?:[-*+]` + relSpaceCls + `*)?(?:\[[ xX]?\]` + relSpaceCls + `*)?` +
		`(?:definition of done` + relSpaceCls + `*:?` + relSpaceCls + `*)?` +
		`(documentation|docs\b|user guide|runbook|release notes|update (?:the )?docs)`,
)

// References a ticket might use to name a change. \S and \d are spelled as
// Python's unicode classes (relPySpace's complement; \p{Nd}), so no ASCII
// narrowing sneaks in. _SHA_RE keeps its \b in the RE2 source and each match
// is post-filtered with wordBoundaryAt at both ends; (?i)[0-9a-f] simple-folds
// to exactly [0-9a-fA-F], which is written out literally.
var (
	relURLRe     = regexp.MustCompile(`https?://[^` + relPySpace + `]+`)
	relNumRe     = regexp.MustCompile(`[#!](\p{Nd}+)`)
	relShaRe     = regexp.MustCompile(`\b([0-9a-fA-F]{7,40})\b`)
	relShaFullRe = regexp.MustCompile(`^[0-9a-fA-F]{7,40}$`)
)

// relTicketKeyRe is references.TICKET_KEY_RE (\b[A-Z][A-Z0-9]+-\d+\b), ported
// here because relBranchTokens needs references.find_ticket_keys and the
// references module has no Go port yet. Fold into references.go when that
// module lands. \d → \p{Nd} (Python's unicode \d); \b post-filtered.
var relTicketKeyRe = regexp.MustCompile(`\b[A-Z][A-Z0-9]+-\p{Nd}+\b`)

// Kinds that carry a ticket's own text. "ticket_context" is the open-ticket
// matching context the collector fetches separately; it is never activity.
var relTicketKinds = map[string]bool{
	"issue": true, "wip": true, "work_item": true, "update": true,
	"comment": true, "ticket_context": true,
}

// relFindTicketKeys mirrors references.find_ticket_keys: every ticket-key
// shaped token, in document order, under Python's unicode word boundaries.
func relFindTicketKeys(text string) []string {
	out := []string{}
	for _, m := range relTicketKeyRe.FindAllStringIndex(text, -1) {
		if wordBoundaryAt(text, m[0]) && wordBoundaryAt(text, m[1]) {
			out = append(out, text[m[0]:m[1]])
		}
	}
	return out
}

// relCamelSplit hand-rolls _CAMEL_SPLIT_RE ((?<=[a-z0-9])(?=[A-Z])): split
// between a [a-z0-9] rune and a following [A-Z] rune. Like re.split with a
// zero-width pattern, the pieces always concatenate back to the input, and an
// empty input yields [""].
func relCamelSplit(s string) []string {
	runes := []rune(s)
	out := []string{}
	start := 0
	for i := 1; i < len(runes); i++ {
		p, c := runes[i-1], runes[i]
		if ((p >= 'a' && p <= 'z') || (p >= '0' && p <= '9')) && c >= 'A' && c <= 'Z' {
			out = append(out, string(runes[start:i]))
			start = i
		}
	}
	return append(out, string(runes[start:]))
}

// relIsDigits mirrors str.isdigit(): non-empty and every rune a digit.
// Python's isdigit also counts Numeric_Type=Digit characters beyond Nd; the
// common ones (super/subscripts) are covered here, the exotic remainder
// (circled digits and friends) is an accepted deviation — every caller feeds
// either ASCII regex matches or branch/path segments.
func relIsDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if unicode.IsDigit(r) {
			continue
		}
		switch {
		case r == '¹' || r == '²' || r == '³': // ¹²³
		case r >= '⁰' && r <= '⁹': // superscripts
		case r >= '₀' && r <= '₉': // subscripts
		default:
			return false
		}
	}
	return true
}

// relClip mirrors Python s[:n] — slicing by RUNE, not byte.
func relClip(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

// relCanonicalIdent mirrors _canonical_ident: PipelineApproval /
// pipeline_approval / pipeline.approval → pipeline-approval.
//
// Returns "" for anything that is not identifier-shaped enough to carry a
// match on its own: fewer than two meaningful parts, or too short overall.
func relCanonicalIdent(raw string) string {
	pieces := []string{}
	for _, chunk := range relSplitRe.Split(raw, -1) {
		pieces = append(pieces, relCamelSplit(chunk)...)
	}
	parts := []string{}
	for _, p := range pieces {
		if utf8.RuneCountInString(p) >= relMinIdentPartChars && !relIsDigits(p) {
			parts = append(parts, pysem.Lower(p))
		}
	}
	if len(parts) < 2 {
		return ""
	}
	ident := strings.Join(parts, "-")
	if utf8.RuneCountInString(ident) < relMinIdentChars {
		return ""
	}
	return ident
}

// relWords mirrors _words: lower first (pysem.Lower, so İ decomposes exactly
// as CPython does), then the ASCII content-word scan, minus stopwords.
func relWords(text string) map[string]bool {
	out := map[string]bool{}
	for _, w := range relWordRe.FindAllString(pysem.Lower(text), -1) {
		if !relStopwords[w] {
			out[w] = true
		}
	}
	return out
}

// relIdents mirrors _idents: compound identifiers a human typed as one token.
func relIdents(text string) map[string]bool {
	out := map[string]bool{}
	for _, re := range []*regexp.Regexp{relCompoundRe, relCamelRe} {
		for _, match := range re.FindAllString(text, -1) {
			if ident := relCanonicalIdent(match); ident != "" {
				out[ident] = true
			}
		}
	}
	return out
}

// relBigrams mirrors _bigrams: adjacent word pairs — TICKET SIDE ONLY.
//
// This asymmetry is the mechanism behind the whole module. The change side
// must be identifier-precise: a human deliberately wrote pipeline-approval as
// one token, or a path made it one. The ticket side is prose written by
// whoever raised it, who will type "the pipeline approval plugin" in words.
// Reading bigrams from the ticket lets those meet, while a *symmetric* bigram
// pass would let two unrelated prose documents collide on ordinary phrasing.
func relBigrams(text string) map[string]bool {
	out := map[string]bool{}
	matches := relRunRe.FindAllStringIndex(text, -1)
	for i := 1; i < len(matches); i++ {
		prev, cur := matches[i-1], matches[i]
		if text[prev[1]:cur[0]] != " " {
			continue
		}
		// _RUN_RE matches are pure ASCII, so byte length equals rune length.
		first := pysem.Lower(text[prev[0]:prev[1]])
		second := pysem.Lower(text[cur[0]:cur[1]])
		if len(first) >= relMinIdentPartChars && len(second) >= relMinIdentPartChars &&
			!relStopwords[first] && !relStopwords[second] {
			ident := first + "-" + second
			if len(ident) >= relMinIdentChars {
				out[ident] = true
			}
		}
	}
	return out
}

// relBranchTokens mirrors _branch_tokens: (words, idents) from a branch name,
// with workflow namespaces stripped.
//
// Python calls remainder.upper() (full case mapping) where Go's ToUpper is the
// simple mapping. The difference (ß→SS, ligatures) is provably inert here: a
// key found only through a full-case expansion changes length, so the
// case-insensitive substitution back into the remainder can never fire in
// Python either — the remainder comes out identical both ways.
func relBranchTokens(branch string) (map[string]bool, map[string]bool) {
	slug := strings.Trim(pysem.Strip(branch), "/")
	if slug == "" {
		return map[string]bool{}, map[string]bool{}
	}
	segments := strings.Split(slug, "/")
	for len(segments) > 0 {
		head := pysem.Lower(segments[0])
		if relBranchNamespaces[head] && len(segments) > 1 {
			segments = segments[1:]
		} else if relBranchActorNamespaces[head] && len(segments) > 2 {
			segments = segments[2:] // the namespace AND the author name after it
		} else {
			break
		}
	}
	remainder := strings.Join(segments, "/")
	// A ticket key in the branch is handled by references, not by wording.
	// Keys are ASCII ticket keys, so (?i) simple folding is exact for the sub.
	for _, key := range relFindTicketKeys(strings.ToUpper(remainder)) {
		remainder = regexp.MustCompile("(?i)"+regexp.QuoteMeta(key)).ReplaceAllLiteralString(remainder, " ")
	}
	idents := relIdents(remainder)
	if ident := relCanonicalIdent(strings.ReplaceAll(remainder, "/", "-")); ident != "" {
		idents[ident] = true
	}
	return relWords(remainder), idents
}

// relStemOf mirrors name.rsplit(".", 1)[0].
func relStemOf(name string) string {
	if i := strings.LastIndex(name, "."); i >= 0 {
		return name[:i]
	}
	return name
}

// relPathTokens mirrors _path_tokens: distinctive tokens from changed paths —
// basename stems, directories, modules.
func relPathTokens(paths []string) map[string]bool {
	out := map[string]bool{}
	for _, path := range paths {
		normalized := strings.Trim(strings.ReplaceAll(path, "\\", "/"), "/")
		if normalized == "" {
			continue
		}
		parts := strings.Split(normalized, "/")
		stem := pysem.Lower(relStemOf(parts[len(parts)-1]))
		if utf8.RuneCountInString(stem) >= relMinPathTokenChars && !relGenericBasenames[stem] && !relStopwords[stem] {
			out[stem] = true
		}
		lo := len(parts) - 3 // parts[-3:-1]
		if lo < 0 {
			lo = 0
		}
		for i := lo; i < len(parts)-1; i++ {
			token := pysem.Lower(parts[i])
			if utf8.RuneCountInString(token) >= relMinPathTokenChars && !relGenericBasenames[token] && !relStopwords[token] {
				out[token] = true
			}
		}
	}
	return out
}

// relPathIdents mirrors _path_idents: compound identifiers implied by a path —
// standup/habits.py → standup-habits.
func relPathIdents(paths []string) map[string]bool {
	out := map[string]bool{}
	for _, path := range paths {
		parts := strings.Split(strings.Trim(strings.ReplaceAll(path, "\\", "/"), "/"), "/")
		if len(parts) >= 2 {
			stem := relStemOf(parts[len(parts)-1])
			if ident := relCanonicalIdent(parts[len(parts)-2] + "-" + stem); ident != "" {
				out[ident] = true
			}
		}
		if ident := relCanonicalIdent(parts[len(parts)-1]); ident != "" {
			out[ident] = true
		}
	}
	return out
}

// relNormalizeURL mirrors _normalize_url.
func relNormalizeURL(url string) string {
	cleaned := pysem.Lower(pysem.Strip(url))
	if i := strings.Index(cleaned, "?"); i >= 0 {
		cleaned = cleaned[:i]
	}
	if i := strings.Index(cleaned, "#"); i >= 0 {
		cleaned = cleaned[:i]
	}
	return strings.TrimRight(cleaned, "/")
}

// relFindShas mirrors _SHA_RE.findall: candidate matches from the RE2 pattern
// (whose \b is the ASCII superset), kept only where Python's unicode \b holds
// at both ends. A rejected candidate cannot hide a real one: the interior of a
// hex run never contains a unicode word boundary.
func relFindShas(text string) []string {
	out := []string{}
	for _, m := range relShaRe.FindAllStringSubmatchIndex(text, -1) {
		s, e := m[2], m[3]
		if wordBoundaryAt(text, s) && wordBoundaryAt(text, e) {
			out = append(out, text[s:e])
		}
	}
	return out
}

// relDoDDocSearch mirrors bool(_DOD_DOC_RE.search(body)). The only \b in the
// pattern sits after the bare "docs" alternative, so a candidate match is
// rejected exactly when that alternative matched and Python's unicode \b fails
// at its end. Every match is anchored at a line start, so a rejected candidate
// cannot mask a genuine match elsewhere on the same line.
func relDoDDocSearch(body string) bool {
	for _, m := range relDoDDocRe.FindAllStringSubmatchIndex(body, -1) {
		alt := body[m[2]:m[3]]
		if strings.EqualFold(alt, "docs") && !wordBoundaryAt(body, m[3]) {
			continue
		}
		return true
	}
	return false
}

// --- profiles --------------------------------------------------------------

// corpusTicket mirrors TicketProfile: one ticket, reduced to what a matcher
// can ask about.
type corpusTicket struct {
	key string
	// The readable text, kept alongside the token sets purely so an
	// adjudicator can be shown what it is ruling on. No predicate here reads
	// them. habits.go reads them for the adjudication shortlist.
	title      string
	text       string
	titleWords map[string]bool
	words      map[string]bool
	idents     map[string]bool
	// Every token in the ticket's text, unfiltered — used ONLY to confirm a
	// repository name beside an ambiguous "#91". words is no good for that: it
	// drops anything under four characters, so a repo called "web" or "api"
	// could never satisfy the guard.
	mentions  map[string]bool
	urls      map[string]bool
	numbers   map[string]bool
	shas      map[string]bool
	size      int
	docsInDoD bool
}

// ticketCorpus mirrors TicketCorpus: every ticket in the window, plus the
// index that makes lookups cheap.
type ticketCorpus struct {
	tickets map[string]*corpusTicket
	// order preserves the Python dict's insertion order over tickets (sorted
	// keys, from build_corpus). No predicate depends on it, but anything that
	// walks the corpus into ordered output must range this, never the map.
	order    []string
	postings map[string]map[string]bool
	// Urls, numbers and shas a ticket names, indexed separately and consulted
	// WITHOUT the rarity gate. A back-reference shares no vocabulary with the
	// change it points at, so routing it through the word index would make the
	// strongest predicate unreachable — and a url cited by several tickets is
	// still a pointer, not noise.
	refPostings map[string]map[string]bool
	rareMax     int
}

// truthy mirrors TicketCorpus.__bool__: bool(self.tickets). habits.py leans on
// it twice — `if not corpus` and `checked=bool(corpus)`.
func (c *ticketCorpus) truthy() bool {
	return c != nil && len(c.tickets) > 0
}

// isRare mirrors TicketCorpus.is_rare.
func (c *ticketCorpus) isRare(token string) bool {
	return len(c.postings[token]) <= c.rareMax
}

// emptyTicketCorpus mirrors TicketCorpus() — the dataclass defaults.
func emptyTicketCorpus() *ticketCorpus {
	return &ticketCorpus{
		tickets:     map[string]*corpusTicket{},
		postings:    map[string]map[string]bool{},
		refPostings: map[string]map[string]bool{},
		rareMax:     1,
	}
}

// changeProfile mirrors ChangeProfile: one commit or pull request, reduced the
// same way.
type changeProfile struct {
	subjectWords map[string]bool
	branchWords  map[string]bool
	pathWords    map[string]bool
	idents       map[string]bool
	url          string
	prID         string
	repoToken    string
	shas         map[string]bool
	pathCount    int
	docsOnly     bool
}

// relUnion mirrors frozenset a | b.
func relUnion(a, b map[string]bool) map[string]bool {
	out := make(map[string]bool, len(a)+len(b))
	for k := range a {
		out[k] = true
	}
	for k := range b {
		out[k] = true
	}
	return out
}

func relContains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// buildCorpus mirrors build_corpus: index every ticket across the given item
// sequences, merged by key.
//
// Text is merged rather than picked from one item because kind does not
// predict which item carries the body: Jira changelog and comment items name
// the same ticket and deliberately carry no description, and the WIP query is
// a different search from the updated-in-window one.
func buildCorpus(allItems, referenceItems []*pysem.Obj) *ticketCorpus {
	titles := map[string][]string{}
	bodies := map[string]string{}
	for _, items := range [][]*pysem.Obj{allItems, referenceItems} {
		for _, item := range items {
			kind, _ := item.Get("kind").(string)
			if !relTicketKinds[kind] {
				continue
			}
			key := strippedOr(item, "key")
			if key == "" {
				continue
			}
			for _, field := range []string{"summary", "title"} {
				text := strippedOr(item, field)
				if text != "" && !relContains(titles[key], text) {
					titles[key] = append(titles[key], text)
				}
			}
			body := strippedOr(item, "body")
			// Longest wins: order-independent, unlike newest-wins, so the
			// corpus does not depend on how the collector happened to
			// interleave sources. Lengths are Python len() — rune counts.
			if utf8.RuneCountInString(body) > utf8.RuneCountInString(bodies[key]) {
				bodies[key] = relClip(body, relMaxTicketBodyChars)
			}
		}
	}

	keySet := map[string]bool{}
	for k := range titles {
		keySet[k] = true
	}
	for k := range bodies {
		keySet[k] = true
	}
	keys := make([]string, 0, len(keySet))
	for k := range keySet {
		keys = append(keys, k)
	}
	sort.Strings(keys) // sorted(set(titles) | set(bodies))

	tickets := map[string]*corpusTicket{}
	order := []string{}
	for _, key := range keys {
		title := strings.Join(titles[key], " ")
		body := bodies[key]
		combined := title + "\n" + body
		titleWords := relWords(title)
		bodyWords := relWords(body)
		if len(titleWords) == 0 && len(bodyWords) == 0 {
			continue // nothing to match on; carrying it would only cost time
		}
		mentions := map[string]bool{}
		for _, m := range relRunRe.FindAllString(pysem.Lower(combined), -1) {
			mentions[m] = true
		}
		urls := map[string]bool{}
		for _, u := range relURLRe.FindAllString(body, -1) {
			urls[relNormalizeURL(u)] = true
		}
		numbers := map[string]bool{}
		for _, m := range relNumRe.FindAllStringSubmatch(body, -1) {
			numbers[m[1]] = true
		}
		shas := map[string]bool{}
		for _, s := range relFindShas(body) {
			if utf8.RuneCountInString(s) >= relMinShaChars { // implied by {7,40}; kept for fidelity
				shas[pysem.Lower(s)] = true
			}
		}
		tickets[key] = &corpusTicket{
			key:        key,
			title:      title,
			text:       body,
			titleWords: titleWords,
			words:      relUnion(titleWords, bodyWords),
			idents:     relUnion(relIdents(combined), relBigrams(combined)),
			mentions:   mentions,
			urls:       urls,
			numbers:    numbers,
			shas:       shas,
			size:       len(bodyWords),
			docsInDoD:  relDoDDocSearch(body),
		}
		order = append(order, key)
	}

	postings := map[string]map[string]bool{}
	refPostings := map[string]map[string]bool{}
	for _, key := range order {
		profile := tickets[key]
		for token := range relUnion(profile.words, profile.idents) {
			if postings[token] == nil {
				postings[token] = map[string]bool{}
			}
			postings[token][key] = true
		}
		for ref := range relProfileRefs(profile) {
			if refPostings[ref] == nil {
				refPostings[ref] = map[string]bool{}
			}
			refPostings[ref][key] = true
		}
	}
	rareMax := int(relRareDFRatio * float64(len(tickets))) // int(_RARE_DF_RATIO * len(tickets))
	if rareMax < 1 {
		rareMax = 1
	}
	return &ticketCorpus{tickets: tickets, order: order, postings: postings, refPostings: refPostings, rareMax: rareMax}
}

// relProfileRefs mirrors _profile_refs: namespaced reference tokens, so a url
// can never collide with a word.
func relProfileRefs(profile *corpusTicket) map[string]bool {
	out := map[string]bool{}
	for u := range profile.urls {
		out["url:"+u] = true
	}
	for s := range profile.shas {
		out["sha:"+s] = true
	}
	for n := range profile.numbers {
		out["num:"+n] = true
	}
	return out
}

// relChangeRefs mirrors _change_refs.
func relChangeRefs(change *changeProfile) map[string]bool {
	refs := map[string]bool{}
	for s := range change.shas {
		refs["sha:"+s] = true
	}
	if change.url != "" {
		refs["url:"+change.url] = true
	}
	if change.prID != "" {
		refs["num:"+change.prID] = true
	}
	return refs
}

// ticketKeys mirrors ticket_keys: the ticket keys these items name — the
// member's Tier-A pool.
//
// Built from every tracker kind, not from "wip": Jira's WIP query skips any
// issue the updated-in-window search already returned, so an actively working
// member has zero wip items. Selecting on that kind would hand an empty
// candidate pool to exactly the people doing the most work.
func ticketKeys(items []*pysem.Obj) map[string]bool {
	out := map[string]bool{}
	for _, item := range items {
		kind, _ := item.Get("kind").(string)
		if !relTicketKinds[kind] {
			continue
		}
		if key := strippedOr(item, "key"); key != "" { // the Python `- {""}`
			out[key] = true
		}
	}
	return out
}

// buildChangeProfile mirrors build_change_profile with docs_only defaulted,
// matching the Python keyword default. Callers that judge documentation pass
// the flag through buildChangeProfileOpts.
func buildChangeProfile(item *pysem.Obj) *changeProfile {
	return buildChangeProfileOpts(item, false)
}

// buildChangeProfileOpts mirrors build_change_profile(item, docs_only=…):
// reduce one commit or pull request to its matchable tokens.
func buildChangeProfileOpts(item *pysem.Obj, docsOnly bool) *changeProfile {
	// `summary` as well as `title`: a Confluence page carries its real name
	// there, and the documentation rule judges pages. Commits and pull
	// requests never set it, so this is free for them.
	subjectParts := []string{}
	for _, field := range []string{"summary", "title"} {
		if v := item.Get(field); pysem.Truthy(v) {
			subjectParts = append(subjectParts, pysem.Str(v))
		}
	}
	subject := strings.Join(subjectParts, " ")
	body := strOr(item, "body")
	branch := strOr(item, "branch")
	paths := []string{}
	for _, p := range listOr(item, "changed_paths") {
		if pysem.Truthy(p) {
			paths = append(paths, pysem.Str(p))
		}
	}
	branchWords, branchIdents := relBranchTokens(branch)
	repo := strippedOr(item, "repository")
	repoToken := repo
	if i := strings.LastIndex(repo, "/"); i >= 0 { // repo.rsplit("/", 1)[-1]
		repoToken = repo[i+1:]
	}
	key := strings.TrimLeft(pysem.Strip(strOr(item, "key")), "#!")
	shas := map[string]bool{}
	if utf8.RuneCountInString(key) >= relMinShaChars && relShaFullRe.MatchString(key) {
		shas[pysem.Lower(key)] = true
	}
	return &changeProfile{
		subjectWords: relWords(subject),
		branchWords:  branchWords,
		pathWords:    relPathTokens(paths),
		idents:       relUnion(relUnion(relIdents(subject), relIdents(body)), relUnion(branchIdents, relPathIdents(paths))),
		url:          relNormalizeURL(strOr(item, "url")),
		prID:         strippedOr(item, "pr_id"),
		repoToken:    pysem.Lower(repoToken),
		shas:         shas,
		pathCount:    len(paths),
		docsOnly:     docsOnly,
	}
}

// --- predicates ------------------------------------------------------------

func relRareHits(tokens, profileTokens map[string]bool, corpus *ticketCorpus) int {
	n := 0
	for token := range tokens {
		if profileTokens[token] && corpus.isRare(token) {
			n++
		}
	}
	return n
}

// relCovered mirrors _covered: share of the CHANGE's tokens the ticket
// accounts for — never the reverse.
func relCovered(tokens, profileTokens map[string]bool) float64 {
	if len(tokens) == 0 {
		return 0.0
	}
	n := 0
	for token := range tokens {
		if profileTokens[token] {
			n++
		}
	}
	return float64(n) / float64(len(tokens))
}

// relBackreference mirrors _backreference: the ticket names the change
// outright. Exact, and admissible in both tiers.
func relBackreference(change *changeProfile, profile *corpusTicket) bool {
	if change.url != "" && profile.urls[change.url] {
		return true
	}
	for s := range change.shas {
		if profile.shas[s] {
			return true
		}
	}
	// A bare "#91" is ambiguous by construction — on GitHub it is a PR number,
	// on Azure Boards a work-item id — so it only counts when the ticket also
	// mentions the repository the change lives in.
	return change.prID != "" && profile.numbers[change.prID] &&
		change.repoToken != "" && profile.mentions[change.repoToken]
}

// relSharedIdentifier mirrors _shared_identifier: a rare compound identifier
// on both sides. The strongest lexical evidence.
func relSharedIdentifier(change *changeProfile, profile *corpusTicket, corpus *ticketCorpus) bool {
	for ident := range change.idents {
		if profile.idents[ident] && corpus.isRare(ident) {
			return true
		}
	}
	return false
}

// relWordMatch mirrors _word_match: ordinary-word overlap. Tier A only, and
// never against a huge ticket.
func relWordMatch(change *changeProfile, profile *corpusTicket, corpus *ticketCorpus) bool {
	if profile.size > relHugeTicketTokens {
		return false
	}
	coverageFloor, rareFloor := float64(relSubjectCoverage), relMinRareWords
	titleRareFloor := relMinRareWordsTitle
	branchRareFloor := relMinRareWordsBranch
	titleFloor, branchFloor := float64(relTitleCoverage), float64(relBranchCoverage)
	if change.docsOnly && profile.docsInDoD {
		// Documentation is a definition-of-done item on every story this
		// product's planner generates, so docs accompanying a ticket is the
		// expected shape rather than new scope — and being wrong here is the
		// worst variant of the message, telling someone the runbook that WAS
		// the ticket's definition of done "doesn't count toward the sprint".
		// The relaxation is gated on the ticket actually saying so: no docs in
		// its definition of done, no discount.
		coverageFloor, titleFloor, branchFloor = relDocsCoverage, relDocsCoverage, relDocsCoverage
		rareFloor, titleRareFloor, branchRareFloor = relMinRareWordsDocs, relMinRareWordsDocs, relMinRareWordsDocs
	}

	if len(change.branchWords) >= 2 &&
		relCovered(change.branchWords, profile.words) >= branchFloor &&
		relRareHits(change.branchWords, profile.words, corpus) >= branchRareFloor {
		return true
	}
	if len(change.subjectWords) > 0 &&
		relCovered(change.subjectWords, profile.titleWords) >= titleFloor &&
		relRareHits(change.subjectWords, profile.titleWords, corpus) >= titleRareFloor {
		return true
	}
	if len(change.subjectWords) >= relMinChangeTokens &&
		relCovered(change.subjectWords, profile.words) >= coverageFloor &&
		relRareHits(change.subjectWords, profile.words, corpus) >= rareFloor {
		return true
	}
	// Paths. Empty means UNKNOWN — the collectors cap detail lookups — so an
	// absent list contributes nothing in either direction. Two distinct rare
	// matches, not one: a path is contextual rather than declarative, and
	// src/auth/session.py against any ticket mentioning sessions is not
	// evidence of anything. A path that really does name the work reaches the
	// identifier predicate instead, which is admissible in both tiers.
	needed := relPathRareHits
	if change.pathCount > relPathShotgunMax {
		needed = relPathRareHitsShotgun
	}
	return len(change.pathWords) > 0 && relRareHits(change.pathWords, profile.words, corpus) >= needed
}

// relCandidates mirrors _candidates: tickets worth scoring — the member's
// own, plus anything sharing a rare token.
//
// Postings for common tokens are skipped rather than walked, which *is* the
// rarity gate — implemented for free, and the reason this stays linear in the
// number of tickets a change actually resembles rather than in the corpus.
func relCandidates(change *changeProfile, corpus *ticketCorpus, ownKeys map[string]bool) []string {
	scored := map[string]int{}
	tokens := relUnion(relUnion(change.idents, change.subjectWords), relUnion(change.branchWords, change.pathWords))
	for token := range tokens {
		keys := corpus.postings[token]
		if len(keys) > corpus.rareMax {
			continue
		}
		for key := range keys {
			scored[key]++
		}
	}
	// Ungated, and scored highest: a ticket that names this change outright is
	// the best candidate there can be, even with no words in common.
	for ref := range relChangeRefs(change) {
		for key := range corpus.refPostings[ref] {
			scored[key] += 100
		}
	}
	for key := range ownKeys {
		if _, inCorpus := corpus.tickets[key]; inCorpus {
			if _, seen := scored[key]; !seen {
				scored[key] = 0
			}
		}
	}
	// Own tickets first, then by shared-rare-token count, then by key — fully
	// deterministic, so a shuffled input cannot change the answer. The key
	// tuple is total (the key string breaks every tie), so the map-range
	// starting order cannot leak through.
	ordered := make([]string, 0, len(scored))
	for key := range scored {
		ordered = append(ordered, key)
	}
	sort.SliceStable(ordered, func(i, j int) bool {
		a, b := ordered[i], ordered[j]
		rankA, rankB := 1, 1
		if ownKeys[a] {
			rankA = 0
		}
		if ownKeys[b] {
			rankB = 0
		}
		if rankA != rankB {
			return rankA < rankB
		}
		if scored[a] != scored[b] {
			return scored[a] > scored[b]
		}
		return a < b
	})
	if len(ordered) > relMaxCandidatesPerChange {
		ordered = ordered[:relMaxCandidatesPerChange]
	}
	return ordered
}

// relatesToTicket mirrors relates_to_ticket: whether this change plausibly
// belongs to some ticket. SUPPRESSION ONLY.
//
// The matched key is deliberately not returned. Nothing downstream may name
// it, so matching the wrong sibling ticket in an epic costs nothing at all.
// Tier gating lives here: the word-overlap predicate is consulted only for
// the member's own tickets (Tier A); a teammate's ticket (Tier B) must name
// the change outright or share a rare compound identifier.
func relatesToTicket(change *changeProfile, corpus *ticketCorpus, ownKeys map[string]bool) bool {
	if !corpus.truthy() {
		return false
	}
	for _, key := range relCandidates(change, corpus, ownKeys) {
		profile := corpus.tickets[key]
		if relBackreference(change, profile) || relSharedIdentifier(change, profile, corpus) {
			return true
		}
		if ownKeys[key] && relWordMatch(change, profile, corpus) {
			return true
		}
	}
	return false
}

// nearMisses mirrors near_misses: the tickets a change most resembles without
// matching — the adjudicator's shortlist.
//
// Only ever used to give a language model something concrete to rule on. It
// carries no verdict of its own.
func nearMisses(profile *changeProfile, corpus *ticketCorpus, ownKeys map[string]bool, limit int) []string {
	if !corpus.truthy() {
		return nil
	}
	cands := relCandidates(profile, corpus, ownKeys)
	// Python [:limit] slice semantics, including a negative limit.
	if limit < 0 {
		limit += len(cands)
		if limit < 0 {
			limit = 0
		}
	}
	if limit > len(cands) {
		limit = len(cands)
	}
	return cands[:limit]
}
