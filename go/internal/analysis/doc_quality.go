package analysis

// doc_quality.go — port of the pure scoring region of
// src/yeaboi/analysis/doc_quality.py (_count_syllables, _clarity_metrics,
// _usefulness_metrics, _has_ai_disclosure, _analyse_page_asset,
// _aggregate_doc_assets, _doc_findings, _prioritize_doc_actions,
// _fallback_doc_quality_insights) plus the seam's result assembly in
// src/yeaboi/analysis/aggregate.py (scoreable_doc_pages, score_docs,
// doc_signal_to_wire) and _insight_item / _INSIGHT_MAX_ITEMS from
// src/yeaboi/tools/team_learning.py. Keep in lockstep: the Python modules are
// the reference implementation; tests/parity/test_analysis_parity.py diffs
// whole-seam output, and tests/unit/test_doc_scoring.py pins the exact values
// mirrored by doc_quality_test.go.
//
// Privacy: nothing from the pages — titles, bodies, authors — is ever logged
// (no log import anywhere in this package) and never appears in an error (no
// error surface exists here at all).
//
// Regex semantics: the doc heuristics lean on Python's UNICODE \s, \d and \b
// far more than the classifiers do, so most patterns are hand-rolled scanners
// over pysem.IsSpace / unicode.IsDigit / wordBoundaryAt instead of RE2
// approximations. Verified empirically against CPython 3.11: the re-\s set,
// the str.isspace() set (== pysem.IsSpace), and the str.split() whitespace
// set are all IDENTICAL (0x9-0xd, 0x1c-0x1f, 0x20, 0x85, 0xa0, 0x1680,
// 0x2000-0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000), so pysem.IsSpace
// serves every \s, strip and split below. Python \d is Unicode Nd, exactly
// Go's unicode.IsDigit. Case-insensitive ASCII literals go through
// strings.EqualFold like the rest of the package (simple folding — the
// accepted deviation for exotic case-fold code points such as U+017F/U+212A,
// which no page realistically carries in an "Owner:" line).

import (
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// docClearMin / docUnclearMax port _CLEAR_MIN / _UNCLEAR_MAX — the clarity
// score bands (aligned to Flesch reading-ease).
const (
	docClearMin   = 60.0
	docUnclearMax = 40.0
)

// docMinSample ports _MIN_DOC_SAMPLE — below this many pages the averages are
// examples, not a trend (summary's small_sample flag).
const docMinSample = 5

// docInsightMaxItems ports team_learning._INSIGHT_MAX_ITEMS — the [:4] cap on
// the insights "start" list.
const docInsightMaxItems = 4

// codeFenceRe ports (?s)```.*?``` — pure ASCII, and RE2's leftmost non-greedy
// match equals Python's for this pattern.
var codeFenceRe = regexp.MustCompile("(?s)```.*?```")

// docWordRe / docSyllableRe port the ASCII word and vowel-group scans —
// deliberately ASCII in Python too ([A-Za-z']+ / [aeiouy]+), so Turkish İ
// splits "VERİFY" into two word tokens exactly like the reference.
var (
	docWordRe     = regexp.MustCompile(`[A-Za-z']+`)
	docSyllableRe = regexp.MustCompile(`[aeiouy]+`)
)

// docActionableWords / docPurposeWords port the \b-bounded vocabularies of
// _usefulness_metrics. They run against text.lower() (pysem.Lower — full case
// mapping, so İ becomes "i"+U+0307 and "veri̇fy" never matches "verify").
// "next step" carries an interior space and "tl;dr" an interior ';', so the
// interior-ASCII-word skip argument from classify.go does NOT transfer — see
// wordVocabSearch for why per-word occurrence scanning is still exact.
var (
	docActionableWords = []string{
		"run", "execute", "deploy", "rollback", "verify", "check", "decide",
		"decision", "next step", "procedure", "troubleshoot", "resolve",
	}
	docPurposeWords = []string{"purpose", "goal", "overview", "summary", "tl;dr", "why"}
)

// docOwnerKeywords ports the (owner|maintainer|contact|responsible)
// alternation of the owner-line pattern. The alternatives share no first
// letter, so at any position at most one can match — alternation order is
// unobservable.
var docOwnerKeywords = []string{"owner", "maintainer", "contact", "responsible"}

// aiContextWords / aiContextTails port _AI_DISCLOSURE_CONTEXT's first branch
// \b(generated|written|drafted|created|produced|co-authored)\s+(with|by)\b
// (IGNORECASE; the \s+ is unicode — an NBSP counts). The second branch,
// "co-authored-by:", is a plain case-insensitive substring reusing
// classify.go's coauthoredByRe.
var (
	aiContextWords = []string{"generated", "written", "drafted", "created", "produced", "co-authored"}
	aiContextTails = []string{"with", "by"}
	aiContextRes   = func() []*regexp.Regexp {
		res := make([]*regexp.Regexp, 0, len(aiContextWords))
		for _, w := range aiContextWords {
			res = append(res, regexp.MustCompile(`(?i)`+regexp.QuoteMeta(w)))
		}
		return res
	}()
)

// ---------------------------------------------------------------------------
// Python-semantics scanners.
// ---------------------------------------------------------------------------

// floatOf mirrors float(v) for the scalar shapes assets carry (json.Number
// from decoding, the json.Number/bool values this package itself sets, and
// the `float(asset.get(key, 0))` default). nil and unparseable shapes read as
// 0 (out of contract: Python would raise).
func floatOf(v any) float64 {
	switch t := v.(type) {
	case json.Number:
		if f, err := t.Float64(); err == nil {
			return f
		}
	case float64:
		return t
	case int64:
		return float64(t)
	case int:
		return float64(t)
	case bool:
		if t {
			return 1
		}
		return 0
	case string:
		if f, err := strconv.ParseFloat(pysem.Strip(t), 64); err == nil {
			return f
		}
	}
	return 0
}

// pySentenceSplit mirrors re.split(r"[.!?]+(?:\s|$)", prose). Hand-rolled
// because Python's \s is unicode (an NBSP after a period splits a sentence)
// — RE2's \s is ASCII. A delimiter is a run of one-or-more [.!?] followed by
// either one Python-whitespace rune (consumed) or end of string; Python's
// bare $ also matches before a final newline, but there the \s branch always
// consumes the newline first, so end-of-string is the only $ case left.
// Every segment is returned, empties included — the caller strips and
// filters exactly like the reference.
func pySentenceSplit(s string) []string {
	out := []string{}
	segStart := 0
	i := 0
	for i < len(s) {
		c := s[i]
		if c != '.' && c != '!' && c != '?' {
			_, size := utf8.DecodeRuneInString(s[i:])
			i += size
			continue
		}
		j := i
		for j < len(s) && (s[j] == '.' || s[j] == '!' || s[j] == '?') {
			j++
		}
		if j >= len(s) {
			// $ branch: delimiter at end of string, consuming nothing more.
			out = append(out, s[segStart:i])
			segStart = j
			i = j
			continue
		}
		if r, size := utf8.DecodeRuneInString(s[j:]); pysem.IsSpace(r) {
			out = append(out, s[segStart:i])
			segStart = j + size
			i = segStart
			continue
		}
		// Run followed by a non-space non-end ("three.Four"): no start inside
		// the run can match either — a shorter greedy run still ends on a
		// punctuation character — so the scan skips past the whole run.
		i = j
	}
	out = append(out, s[segStart:])
	return out
}

// pySplitFields mirrors str.split() with no arguments: maximal runs of
// non-whitespace, no empties (the same whitespace set as re's \s here — see
// the header's empirical note).
func pySplitFields(s string) []string {
	out := []string{}
	start := -1
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		if pysem.IsSpace(r) {
			if start >= 0 {
				out = append(out, s[start:i])
				start = -1
			}
		} else if start < 0 {
			start = i
		}
		i += size
	}
	if start >= 0 {
		out = append(out, s[start:])
	}
	return out
}

// countSyllables ports _count_syllables — vowel groups on the FULL-cased
// lower form (İ lowers to "i"+U+0307, whose i still opens a group), silent-e
// trim, minimum one.
func countSyllables(word string) int {
	lower := pysem.Lower(word)
	n := len(docSyllableRe.FindAllString(lower, -1))
	if strings.HasSuffix(lower, "e") && n > 1 {
		n--
	}
	return max(1, n)
}

// docHeadingCount mirrors len(re.findall(r"(?m)^\s{0,3}#{1,6}\s", prose)).
// Hand-rolled: every \s is unicode, and \s{0,3} may consume newlines (a match
// at one line start can swallow blank lines before the '#'). A match can only
// start where (?m)^ holds, i.e. at a line start; the greedy quantifiers admit
// exactly one shape — the whole space run (≤ 3 runes, else every backtrack
// lands on another space), the whole '#' run (1–6, else every backtrack lands
// on another '#'), one trailing space rune. findall is non-overlapping, so
// line starts inside a match are skipped via minPos.
func docHeadingCount(s string) int {
	count := 0
	minPos := 0
	for _, p := range lineStarts(s) {
		if p < minPos {
			continue
		}
		i, k := p, 0
		for i < len(s) {
			r, size := utf8.DecodeRuneInString(s[i:])
			if !pysem.IsSpace(r) {
				break
			}
			i += size
			k++
			if k > 3 {
				break
			}
		}
		if k > 3 {
			continue
		}
		n := 0
		for i < len(s) && s[i] == '#' {
			i++
			n++
		}
		if n < 1 || n > 6 {
			continue
		}
		r, size := utf8.DecodeRuneInString(s[i:])
		if size == 0 || !pysem.IsSpace(r) {
			continue
		}
		count++
		minPos = i + size
	}
	return count
}

// docHasLists mirrors bool(re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s", prose)).
// From every line start the unbounded unicode \s* is skipped (a run crossing
// a newline reaches a token a later line start also reaches, so per-start
// scanning is boolean-equivalent), then one bullet ('•' is multi-byte) or a
// maximal unicode-digit run (Python \d is Nd — Arabic-Indic ٣ counts) with
// '.' or ')' — backtracking a shorter digit run lands on a digit, never on
// [.)], so only the maximal run can match — then one \s rune.
func docHasLists(s string) bool {
	for _, p := range lineStarts(s) {
		i := skipSpace(s, p)
		if i >= len(s) {
			continue
		}
		if s[i] == '-' || s[i] == '*' {
			if isSpaceAt(s, i+1) {
				return true
			}
			continue
		}
		if r, size := utf8.DecodeRuneInString(s[i:]); r == '•' {
			if isSpaceAt(s, i+size) {
				return true
			}
			continue
		}
		j := i
		for j < len(s) {
			r, size := utf8.DecodeRuneInString(s[j:])
			if !unicode.IsDigit(r) {
				break
			}
			j += size
		}
		if j > i && j < len(s) && (s[j] == '.' || s[j] == ')') && isSpaceAt(s, j+1) {
			return true
		}
	}
	return false
}

// docHasOwnerLine mirrors bool(re.search(
// r"(?im)^\s*[*_]{0,2}(owner|maintainer|contact|responsible)[*_]{0,2}\s*[:\-|]",
// text)) on the ORIGINAL (unlowered) text. From every line start: the
// unbounded unicode \s* (may cross blank lines; boolean-equivalent per-start
// scanning as above), then a bold/italic marker run — greedy {0,2} with
// backtracking admits only the whole run and only when it is ≤ 2 ("***Owner"
// never matches, pinned), the keyword case-insensitively, the trailing
// marker run under the same ≤-2 rule, another unicode \s* (this one crosses
// "Owner\n\n: Jane", pinned), and finally one of ':', '-', '|'. A leading
// pipe defeats the anchor ("| Owner | Jane |" is not owned, pinned).
func docHasOwnerLine(text string) bool {
	for _, p := range lineStarts(text) {
		i := skipSpace(text, p)
		m, k := i, 0
		for m < len(text) && (text[m] == '*' || text[m] == '_') {
			m++
			k++
			if k > 2 {
				break
			}
		}
		if k > 2 {
			continue
		}
		for _, kw := range docOwnerKeywords {
			if m+len(kw) > len(text) || !strings.EqualFold(text[m:m+len(kw)], kw) {
				continue
			}
			j, c := m+len(kw), 0
			for j < len(text) && (text[j] == '*' || text[j] == '_') {
				j++
				c++
				if c > 2 {
					break
				}
			}
			if c > 2 {
				continue
			}
			j = skipSpace(text, j)
			if j < len(text) && (text[j] == ':' || text[j] == '-' || text[j] == '|') {
				return true
			}
		}
	}
	return false
}

// wordVocabSearch mirrors re.search(r"\b(w1|w2|…)\b", text) for a literal
// vocabulary. The Python engine backtracks through every alternative at every
// position, so the boolean is exactly "some occurrence of some word carries a
// unicode \b at both pattern edges" — which per-word occurrence scanning
// computes directly, with no rejected-span skip to reason about (unlike
// classify.go's single-regex approach, which "next step"'s interior space and
// "tl;dr"'s ';' would break).
func wordVocabSearch(text string, words []string) bool {
	for _, w := range words {
		for from := 0; ; {
			rel := strings.Index(text[from:], w)
			if rel < 0 {
				break
			}
			start := from + rel
			if wordBoundaryAt(text, start) && wordBoundaryAt(text, start+len(w)) {
				return true
			}
			from = start + 1
		}
	}
	return false
}

// aiDisclosureContext mirrors _AI_DISCLOSURE_CONTEXT.search: either the
// plain substring branch, or word1 (leading unicode \b — "re-co-authored by"
// matches because '-' is a non-word rune, pinned empirically), one-or-more
// unicode spaces (\s+ — a shorter backtracked run lands on a space, never on
// 'w'/'b', so only the maximal skip can match), then "with" or "by" with a
// trailing unicode \b.
func aiDisclosureContext(text string) bool {
	if coauthoredByRe.MatchString(text) {
		return true
	}
	for _, re := range aiContextRes {
		for _, loc := range re.FindAllStringIndex(text, -1) {
			if !wordBoundaryAt(text, loc[0]) {
				continue
			}
			j := skipSpace(text, loc[1])
			if j == loc[1] {
				continue // \s+ needs at least one whitespace rune
			}
			for _, tail := range aiContextTails {
				if j+len(tail) <= len(text) && strings.EqualFold(text[j:j+len(tail)], tail) &&
					wordBoundaryAt(text, j+len(tail)) {
					return true
				}
			}
		}
	}
	return false
}

// hasAiDisclosure ports _has_ai_disclosure — an explicit AI-authorship
// disclosure: a marker (classify.go's classifyAiMarkers, same package) AND an
// authorship context around it.
func hasAiDisclosure(text string) bool {
	if text == "" {
		return false
	}
	return len(classifyAiMarkers(text)) > 0 && aiDisclosureContext(text)
}

// ---------------------------------------------------------------------------
// Per-page heuristics.
// ---------------------------------------------------------------------------

// clarityMetricsResult carries _clarity_metrics' dict in declaration order;
// avgSentenceWords, longSentencePct and clarity hold the already-rounded
// values the reference reports (the UNROUNDED average feeds Flesch first).
type clarityMetricsResult struct {
	wordCount        int
	sentenceCount    int
	avgSentenceWords float64
	longSentencePct  float64
	headingCount     int
	hasLists         bool
	hasCodeBlocks    bool
	clarity          float64
}

// clarityMetrics ports _clarity_metrics — deterministic readability for one
// page, scored on PROSE only (code fences replaced by a space first).
func clarityMetrics(text string) clarityMetricsResult {
	prose := codeFenceRe.ReplaceAllString(text, " ")
	hasCode := prose != text
	sentences := []string{}
	for _, seg := range pySentenceSplit(prose) {
		if pysem.Strip(seg) != "" {
			sentences = append(sentences, seg)
		}
	}
	words := docWordRe.FindAllString(prose, -1)
	nSentences := len(sentences)
	nWords := len(words)
	if nWords == 0 || nSentences == 0 {
		return clarityMetricsResult{wordCount: nWords, sentenceCount: nSentences, hasCodeBlocks: hasCode}
	}

	avgSentenceWords := float64(nWords) / float64(nSentences)
	longSentences := 0
	for _, s := range sentences {
		if len(pySplitFields(s)) > 25 {
			longSentences++
		}
	}
	longSentencePct := pysem.RoundN(float64(longSentences)/float64(nSentences)*100, 1)
	syllables := 0
	for _, w := range words {
		syllables += countSyllables(w)
	}

	headings := docHeadingCount(prose)
	lists := docHasLists(prose)

	// Flesch Reading Ease — exact operation order, unrounded average in.
	flesch := 206.835 - 1.015*avgSentenceWords - 84.6*(float64(syllables)/float64(nWords))
	clarity := flesch
	if headings != 0 {
		clarity += 4
	}
	if lists {
		clarity += 3
	}
	clarity = math.Max(0.0, math.Min(100.0, clarity))

	return clarityMetricsResult{
		wordCount:        nWords,
		sentenceCount:    nSentences,
		avgSentenceWords: pysem.RoundN(avgSentenceWords, 1),
		longSentencePct:  longSentencePct,
		headingCount:     headings,
		hasLists:         lists,
		hasCodeBlocks:    hasCode,
		clarity:          pysem.RoundN(clarity, 1),
	}
}

// usefulnessMetricsResult carries _usefulness_metrics' dict in declaration
// order.
type usefulnessMetricsResult struct {
	usefulness float64
	owned      bool
	actionable bool
	structured bool
	hasPurpose bool
}

// usefulnessMetrics ports _usefulness_metrics — is the page structured,
// owned, and usable for action. Owner detection runs on the ORIGINAL text;
// the actionable/purpose vocabularies run on the full-cased lower form.
func usefulnessMetrics(text string) usefulnessMetricsResult {
	lower := pysem.Lower(text)
	clarity := clarityMetrics(text)
	owned := docHasOwnerLine(text)
	actionable := wordVocabSearch(lower, docActionableWords)
	hasPurpose := wordVocabSearch(lower, docPurposeWords)
	structured := clarity.headingCount != 0 || clarity.hasLists
	score := 20.0
	if hasPurpose {
		score += 20
	}
	if structured {
		score += 20
	}
	if actionable {
		score += 20
	}
	if owned {
		score += 20
	}
	return usefulnessMetricsResult{
		usefulness: score,
		owned:      owned,
		actionable: actionable,
		structured: structured,
		hasPurpose: hasPurpose,
	}
}

// analysePageAsset ports _analyse_page_asset — one page scored into the
// complete derived record, key order as the Python dict literal. Titles are
// sliced to 80 CODE POINTS after the get-default, so a present-but-empty
// title stays "" (only a missing key becomes "Untitled").
func analysePageAsset(page *pysem.Obj) *pysem.Obj {
	text := pysem.Str(page.GetDefault("text", ""))
	clarity := clarityMetrics(text)
	useful := usefulnessMetrics(text)
	asset := pysem.EmptyObj()
	asset.Set("title", truncRunes(pysem.Str(page.GetDefault("title", "Untitled")), 80))
	asset.Set("platform", page.GetDefault("platform", ""))
	// json.Number so both serializers render Python repr(float) ("0.0", never
	// "0") — the same convention as classify.go's footprint_pct.
	asset.Set("clarity", json.Number(pysem.FloatRepr(clarity.clarity)))
	asset.Set("usefulness", json.Number(pysem.FloatRepr(useful.usefulness)))
	asset.Set("owned", useful.owned)
	asset.Set("actionable", useful.actionable)
	asset.Set("structured", useful.structured)
	asset.Set("has_code_blocks", clarity.hasCodeBlocks)
	asset.Set("marked", hasAiDisclosure(text))
	asset.Set("url", page.GetDefault("url", ""))
	asset.Set("key", page.GetDefault("key", ""))
	asset.Set("container", page.GetDefault("container", ""))
	// page.get("version") or page.get("timestamp", "") — a falsy version
	// falls through to the timestamp (FirstTruthy semantics).
	asset.Set("version", pysem.FirstTruthy(page.Get("version"), page.GetDefault("timestamp", "")))
	return asset
}

// ---------------------------------------------------------------------------
// Aggregation.
// ---------------------------------------------------------------------------

// docSignal carries _aggregate_doc_assets' DocQualitySignal fields. The two
// legacy fields (avg_ai_likelihood / likely_ai_pages) stay at their dataclass
// defaults on this path and are spelled only by docSignalWire.
type docSignal struct {
	pagesScanned    int64
	platforms       []string
	avgClarity      float64
	avgUsefulness   float64
	clearPages      int64
	mixedPages      int64
	unclearPages    int64
	ownedPages      int64
	actionablePages int64
	structuredPages int64
	aiMarkedPages   int64
	perPlatform     []any
	flagged         [][2]string
}

// aggregateDocAssets ports _aggregate_doc_assets — fresh and version-matched
// cached assets aggregated without needing bodies. Empty input returns the
// default (all-zero) signal, mirroring DocQualitySignal().
func aggregateDocAssets(assets []any) docSignal {
	sig := docSignal{perPlatform: []any{}}
	if len(assets) == 0 {
		return sig
	}
	type scoredEntry struct {
		clarity    float64
		usefulness float64
		title      string
	}
	perPlatform := map[string]int64{}
	scored := []scoredEntry{}
	var claritySum, usefulnessSum float64
	for _, raw := range assets {
		asset := pysem.AsObj(raw)
		if asset == nil {
			continue // out of contract: Python would raise on .get
		}
		platform := pysem.Str(asset.GetDefault("platform", ""))
		if platform != "" {
			seen := false
			for _, p := range sig.platforms {
				if p == platform {
					seen = true
					break
				}
			}
			if !seen {
				sig.platforms = append(sig.platforms, platform)
			}
		}
		perPlatform[platform]++ // EVERY platform counts here, "" included
		clarity := floatOf(asset.Get("clarity"))
		usefulness := floatOf(asset.Get("usefulness"))
		if clarity >= docClearMin {
			sig.clearPages++
		}
		if clarity < docUnclearMax {
			sig.unclearPages++
		}
		if clarity >= docUnclearMax && clarity < docClearMin {
			sig.mixedPages++
		}
		if pysem.Truthy(asset.Get("owned")) {
			sig.ownedPages++
		}
		if pysem.Truthy(asset.Get("actionable")) {
			sig.actionablePages++
		}
		if pysem.Truthy(asset.Get("structured")) {
			sig.structuredPages++
		}
		if pysem.Truthy(asset.Get("marked")) {
			sig.aiMarkedPages++
		}
		scored = append(scored, scoredEntry{clarity, usefulness, pysem.Str(asset.GetDefault("title", "Untitled"))})
		claritySum += clarity
		usefulnessSum += usefulness
	}

	// Flagged pages: clarity ascending first (stable — ties keep input
	// order), then usefulness ascending for titles not already flagged. No
	// cap, deduped by title across both passes.
	seenTitles := map[string]bool{}
	byClarity := append([]scoredEntry{}, scored...)
	sort.SliceStable(byClarity, func(i, j int) bool { return byClarity[i].clarity < byClarity[j].clarity })
	for _, e := range byClarity {
		if e.clarity < docClearMin && !seenTitles[e.title] {
			sig.flagged = append(sig.flagged,
				[2]string{e.title, fmt.Sprintf("clarity %s/100 — dense or long-winded", pysem.Format0f(e.clarity))})
			seenTitles[e.title] = true
		}
	}
	byUsefulness := append([]scoredEntry{}, scored...)
	sort.SliceStable(byUsefulness, func(i, j int) bool { return byUsefulness[i].usefulness < byUsefulness[j].usefulness })
	for _, e := range byUsefulness {
		if e.usefulness < 60 && !seenTitles[e.title] {
			sig.flagged = append(sig.flagged, [2]string{e.title,
				fmt.Sprintf("usefulness %s/100 — missing purpose, ownership, or actions", pysem.Format0f(e.usefulness))})
			seenTitles[e.title] = true
		}
	}

	sig.pagesScanned = int64(len(assets))
	sig.avgClarity = pysem.RoundN(claritySum/float64(len(assets)), 1)
	sig.avgUsefulness = pysem.RoundN(usefulnessSum/float64(len(assets)), 1)
	sig.perPlatform = sortedPairs(perPlatform)
	return sig
}

// docSignalWire mirrors aggregate.doc_signal_to_wire — every field explicit
// in declaration order, including the two legacy fields new analysis leaves
// at zero.
func docSignalWire(sig docSignal) *pysem.Obj {
	o := pysem.EmptyObj()
	o.Set("pages_scanned", sig.pagesScanned)
	o.Set("platforms_scanned", stringsAsAny(sig.platforms))
	o.Set("avg_clarity", json.Number(pysem.FloatRepr(sig.avgClarity)))
	o.Set("avg_usefulness", json.Number(pysem.FloatRepr(sig.avgUsefulness)))
	o.Set("clear_pages", sig.clearPages)
	o.Set("mixed_pages", sig.mixedPages)
	o.Set("unclear_pages", sig.unclearPages)
	o.Set("owned_pages", sig.ownedPages)
	o.Set("actionable_pages", sig.actionablePages)
	o.Set("structured_pages", sig.structuredPages)
	o.Set("avg_ai_likelihood", json.Number("0.0"))
	o.Set("likely_ai_pages", int64(0))
	o.Set("ai_marked_pages", sig.aiMarkedPages)
	o.Set("per_platform", sig.perPlatform)
	o.Set("flagged_pages", flaggedAsAny(sig.flagged))
	o.Set("is_ai_estimate", false)
	return o
}

// docSummaryWire mirrors score_docs' summary dict — the signal without the
// legacy AI fields, with small_sample appended last.
func docSummaryWire(sig docSignal) *pysem.Obj {
	o := pysem.EmptyObj()
	o.Set("pages_scanned", sig.pagesScanned)
	o.Set("platforms_scanned", stringsAsAny(sig.platforms))
	o.Set("avg_clarity", json.Number(pysem.FloatRepr(sig.avgClarity)))
	o.Set("avg_usefulness", json.Number(pysem.FloatRepr(sig.avgUsefulness)))
	o.Set("clear_pages", sig.clearPages)
	o.Set("mixed_pages", sig.mixedPages)
	o.Set("unclear_pages", sig.unclearPages)
	o.Set("owned_pages", sig.ownedPages)
	o.Set("actionable_pages", sig.actionablePages)
	o.Set("structured_pages", sig.structuredPages)
	o.Set("ai_marked_pages", sig.aiMarkedPages)
	o.Set("per_platform", sig.perPlatform)
	o.Set("flagged_pages", flaggedAsAny(sig.flagged))
	o.Set("is_ai_estimate", false)
	o.Set("small_sample", sig.pagesScanned < docMinSample)
	return o
}

func stringsAsAny(values []string) []any {
	out := []any{}
	for _, v := range values {
		out = append(out, v)
	}
	return out
}

func flaggedAsAny(flagged [][2]string) []any {
	out := []any{}
	for _, pair := range flagged {
		out = append(out, []any{pair[0], pair[1]})
	}
	return out
}

// ---------------------------------------------------------------------------
// Findings, actions, insights.
// ---------------------------------------------------------------------------

// docFindingBase mirrors the shared `base` dict of _doc_findings — spread
// first, so these four keys lead every finding.
func docFindingBase(asset *pysem.Obj, scope string) *pysem.Obj {
	f := pysem.EmptyObj()
	f.Set("link", asset.GetDefault("url", ""))
	f.Set("affected_scope", []any{scope})
	f.Set("owner_role", "Documentation owner")
	f.Set("confidence", "high")
	return f
}

// docFindings ports _doc_findings — per asset, a clarity finding then a
// usefulness finding (assets in input order), each only when the score is
// strictly below 60.
func docFindings(assets []any) []any {
	findings := []any{}
	for _, raw := range assets {
		asset := pysem.AsObj(raw)
		if asset == nil {
			continue // out of contract: Python would raise on .get
		}
		title := pysem.Str(asset.GetDefault("title", "Untitled"))
		scope := pysem.Str(asset.GetDefault("platform", "")) + ":" + title
		if floatOf(asset.Get("clarity")) < docClearMin {
			f := docFindingBase(asset, scope)
			f.Set("id", scope+":clarity")
			f.Set("category", "clarity")
			f.Set("title", "Rewrite dense documentation")
			f.Set("detail",
				"Lead with the outcome, shorten sentences, and split the page with descriptive headings and lists.")
			f.Set("priority", "high")
			f.Set("impact", "Makes operational knowledge faster to understand and use.")
			f.Set("evidence", fmt.Sprintf("%s scored %s/100 for clarity.",
				title, pysem.Format0f(floatOf(asset.Get("clarity")))))
			f.Set("next_steps", []any{
				"Rewrite the summary and longest sections.",
				"Have a target reader validate the instructions.",
			})
			f.Set("effort", "small")
			f.Set("completion_check",
				"A target reader can identify the purpose and required action without author help.")
			findings = append(findings, f)
		}
		if floatOf(asset.Get("usefulness")) < 60 {
			f := docFindingBase(asset, scope)
			f.Set("id", scope+":usefulness")
			f.Set("category", "usefulness")
			f.Set("title", "Add purpose, ownership, and actions")
			f.Set("detail",
				"State why the page exists, who maintains it, and the concrete procedure or decision it supports.")
			f.Set("priority", "high")
			f.Set("impact", "Turns descriptive prose into maintainable, actionable team knowledge.")
			f.Set("evidence", fmt.Sprintf("%s scored %s/100 for usefulness.",
				title, pysem.Format0f(floatOf(asset.Get("usefulness")))))
			f.Set("next_steps", []any{
				"Add purpose and owner fields.",
				"Add verified steps, decisions, or next actions.",
			})
			f.Set("effort", "small")
			f.Set("completion_check", "The page names an owner and provides a verifiable action or decision.")
			findings = append(findings, f)
		}
	}
	return findings
}

// prioritizeDocActions ports _prioritize_doc_actions — group by
// (str(category), str(title)) in first-seen insertion order, exemplar =
// shallow copy of the group's FIRST finding, scopes sorted with FALSY scopes
// KEPT (unlike code_health's prioritize_actions — the divergence is
// deliberate, pinned), the " Affects N pages." suffix only when N > 1, and a
// STABLE sort by (priority rank, -breadth) with NO title tiebreak.
func prioritizeDocActions(findings []any) []any {
	order := map[string]int{"high": 0, "medium": 1, "low": 2}
	type docActionKey struct{ category, title string }
	keyOrder := []docActionKey{}
	grouped := map[docActionKey][]*pysem.Obj{}
	for _, v := range findings {
		finding := pysem.AsObj(v)
		if finding == nil {
			continue // out of contract: Python would raise on .get
		}
		key := docActionKey{category: pysem.Str(finding.Get("category")), title: pysem.Str(finding.Get("title"))}
		if _, ok := grouped[key]; !ok {
			keyOrder = append(keyOrder, key)
		}
		grouped[key] = append(grouped[key], finding)
	}
	actions := make([]*pysem.Obj, 0, len(keyOrder))
	for _, key := range keyOrder {
		group := grouped[key]
		action := group[0].Clone()
		scopeSet := map[string]bool{}
		for _, item := range group {
			arr, _ := item.GetDefault("affected_scope", []any{}).([]any)
			for _, s := range arr {
				if str, ok := s.(string); ok {
					scopeSet[str] = true // no truthiness filter: "" survives
				}
			}
		}
		sortedScopes := pysem.SortedKeys(scopeSet)
		scopes := make([]any, 0, len(sortedScopes))
		for _, s := range sortedScopes {
			scopes = append(scopes, s)
		}
		action.Set("affected_scope", scopes)
		action.Set("breadth", int64(len(scopes)))
		if len(scopes) > 1 {
			action.Set("evidence", fmt.Sprintf("%s Affects %d pages.",
				pysem.Str(action.Get("evidence")), len(scopes)))
		}
		actions = append(actions, action)
	}
	rank := func(a *pysem.Obj) int {
		if r, ok := order[pysem.Str(a.Get("priority"))]; ok {
			return r
		}
		return 9 // order.get(str(...), 9): unknown priorities sort last
	}
	sort.SliceStable(actions, func(i, j int) bool {
		ri, rj := rank(actions[i]), rank(actions[j])
		if ri != rj {
			return ri < rj
		}
		return intOf(actions[i].GetDefault("breadth", int64(1))) > intOf(actions[j].GetDefault("breadth", int64(1)))
	})
	out := make([]any, 0, len(actions))
	for _, a := range actions {
		out = append(out, a)
	}
	return out
}

// insightItem ports team_learning._insight_item — {title, detail, evidence}.
func insightItem(title, detail, evidence string) *pysem.Obj {
	o := pysem.EmptyObj()
	o.Set("title", title)
	o.Set("detail", detail)
	o.Set("evidence", evidence)
	return o
}

// fallbackDocQualityInsights ports _fallback_doc_quality_insights —
// deterministic, evidence-linked start/stop/keep/try coaching. The action-led
// branch caps start at docInsightMaxItems and appends "link" AFTER evidence
// only when the action carries a truthy one.
func fallbackDocQualityInsights(sig docSignal, samples []any) *pysem.Obj {
	actions := prioritizeDocActions(docFindings(samples))
	out := pysem.EmptyObj()
	if len(actions) > 0 {
		items := []any{}
		for i, raw := range actions {
			if i >= docInsightMaxItems {
				break
			}
			action := pysem.AsObj(raw)
			item := insightItem(
				pysem.Str(action.GetDefault("title", "")),
				pysem.Str(action.GetDefault("detail", "")),
				pysem.Str(action.GetDefault("evidence", "")),
			)
			if pysem.Truthy(action.Get("link")) {
				item.Set("link", action.Get("link"))
			}
			items = append(items, item)
		}
		out.Set("start", items)
		out.Set("stop", []any{insightItem(
			"Stop publishing ownerless guidance",
			"Every operational page should name a maintainer and a concrete validation step.",
			fmt.Sprintf("%d page(s) lack an owner signal", max(int64(0), sig.pagesScanned-sig.ownedPages)),
		)})
		out.Set("keep", []any{insightItem(
			"Keep actionable pages current",
			"Preserve the pages that already combine clear structure with executable guidance.",
			fmt.Sprintf("%d actionable page(s) found", sig.actionablePages),
		)})
		out.Set("try", []any{insightItem(
			"Use a shared documentation template",
			"Start pages with purpose, owner, last-reviewed date, procedure, and verification.",
			fmt.Sprintf("Average usefulness %s/100", pysem.Format0f(sig.avgUsefulness)),
		)})
		return out
	}
	out.Set("start", []any{insightItem(
		"Set a documentation quality baseline",
		"Use purpose, owner, procedure, and verification fields for every shared page.",
		fmt.Sprintf("%d page(s) scanned", sig.pagesScanned),
	)})
	out.Set("stop", []any{insightItem(
		"Stop relying on implicit ownership",
		"Name a maintainer so readers know who can verify and update the page.",
		"Ownership is assessed explicitly in the new documentation score.",
	)})
	out.Set("keep", []any{insightItem(
		"Keep clear documentation patterns",
		"Continue using concise sections and concrete procedures.",
		fmt.Sprintf("Average clarity %s/100", pysem.Format0f(sig.avgClarity)),
	)})
	out.Set("try", []any{insightItem(
		"Review documentation with a target reader",
		"Ask someone other than the author to execute or explain the documented process.",
		fmt.Sprintf("Average usefulness %s/100", pysem.Format0f(sig.avgUsefulness)),
	)})
	return out
}

// ---------------------------------------------------------------------------
// The seam.
// ---------------------------------------------------------------------------

// scoreableDocPages mirrors aggregate.scoreable_doc_pages — the pages that
// produce an asset: a cached asset dict, or a body whose str() strips
// non-empty (a JSON null "text" strs to "None" and IS scoreable, exactly like
// the reference).
func scoreableDocPages(pages []any) []*pysem.Obj {
	out := []*pysem.Obj{}
	for _, raw := range pages {
		page := pysem.AsObj(raw)
		if page == nil {
			continue // out of contract: Python would raise on .get
		}
		if pysem.AsObj(page.Get("asset")) != nil || pysem.Strip(pysem.Str(page.GetDefault("text", ""))) != "" {
			out = append(out, page)
		}
	}
	return out
}

// ScoreDocs mirrors aggregate.py::score_docs: pages in (each either carrying
// a cached "asset" object — passed through verbatim, decoded key order kept —
// or a "text" body to score), the full ordered result object out. The
// contract_version envelope is prepended by the dispatch layer (RunScoreDocs),
// never here.
func ScoreDocs(pages []any) *pysem.Obj {
	assets := []any{}
	for _, page := range scoreableDocPages(pages) {
		if cached := pysem.AsObj(page.Get("asset")); cached != nil {
			assets = append(assets, cached)
		} else {
			assets = append(assets, analysePageAsset(page))
		}
	}
	sig := aggregateDocAssets(assets)
	findings := docFindings(assets)
	result := pysem.EmptyObj()
	result.Set("assets", assets)
	result.Set("signal", docSignalWire(sig))
	result.Set("summary", docSummaryWire(sig))
	result.Set("findings", findings)
	result.Set("action_plan", prioritizeDocActions(findings))
	// The insights recompute findings/actions from the assets internally,
	// exactly like _fallback_doc_quality_insights does from its samples.
	result.Set("insights", fallbackDocQualityInsights(sig, assets))
	return result
}
