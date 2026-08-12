// Package analysis holds the Go twins of the deterministic analysis-mode
// modules (src/yeaboi/analysis/). Python is the reference implementation;
// every result is built as ordered JSON (*pysem.Obj) so canonical output
// bytes match the reference exactly.
package analysis

// coverage.go — port of src/yeaboi/analysis/coverage.py. Keep in lockstep:
// the Python module is the reference implementation; the parity suite diffs
// the two outputs whole.
//
// Shared exhaustive-scan coverage accounting. Analysis collectors use this
// instead of silently applying provider caps: every discovered asset receives
// one terminal state and the overall run is only "complete" when no eligible
// asset failed, was inaccessible, or was truncated.
//
// Pure: no I/O, no config — and NOTHING here is ever logged; no error ever
// carries input content (no error surface exists at all).
//
// Regex semantics: Python's \s/\w/\d/\b are unicode, RE2's are ASCII.
// Each pattern spells the unicode classes explicitly (\p{...} and the exact
// Python str-\s character set) and enforces unicode \b by post-filtering
// matches with wordBoundaryAt — the same superset-scan-then-reject scheme as
// go/internal/standup/references.go. A rejected span cannot hide a later
// Python-valid match: every boundary-filtered pattern matches a run of word
// runes, whose interior holds no word boundary for either engine.

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// pySpaceClass is the exact character set Python's str-pattern \s matches
// (str.isspace(): the Unicode White_Space property plus U+001C..U+001F),
// spelled as an RE2 class body. RE2's own \s is ASCII-only.
const pySpaceClass = `\t\n\x0B\x0C\r \x1C-\x1F\x{85}\x{A0}\x{1680}\x{2000}-\x{200A}\x{2028}\x{2029}\x{202F}\x{205F}\x{3000}`

// pyWordClass is Python's str-pattern \w (underscore plus everything
// str.isalnum() counts: L*, Nd, Nl, No) as an RE2 class body — the class
// pysem.IsWordRune implements rune-wise.
const pyWordClass = `\p{L}\p{Nl}\p{Nd}\p{No}_`

// One mid-scan network loss produces dozens of identical exceptions differing
// only by URL/sha/id. Grouping must key on the *shape* of the error, not its
// raw text, or every failure renders as its own coverage note.
//
// Ports _DETAIL_SUBSTITUTIONS, same order. The first two patterns carry no
// boundary assertions and translate to RE2 exactly; the sha and long-number
// patterns keep Python's unicode \b via subWordBounded post-filtering (the
// hex pattern keeps the ASCII \b in its source as a superset pre-filter; the
// digit pattern cannot — RE2's \b misjudges non-ASCII digits — so it relies
// on the post-filter alone).
var (
	detailURLRe     = regexp.MustCompile(`https?://[^` + pySpaceClass + `]+`)
	detailAPIPathRe = regexp.MustCompile(`/[` + pyWordClass + `][` + pyWordClass + `./~%-]*_apis/[^` + pySpaceClass + `]+`)
	detailHexIDRe   = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	detailNumberRe  = regexp.MustCompile(`\p{Nd}{4,}`)
	detailSpacesRe  = regexp.MustCompile(`[` + pySpaceClass + `]+`)
)

const detailMaxLen = 200

// subWordBounded mirrors pattern.sub(repl, text) for a pattern whose Python
// source is wrapped in unicode \b: every RE2 match is kept only when both
// ends sit on a unicode word boundary (wordBoundaryAt, classify.go).
func subWordBounded(re *regexp.Regexp, text, repl string) string {
	locs := re.FindAllStringIndex(text, -1)
	if len(locs) == 0 {
		return text
	}
	var b strings.Builder
	last := 0
	for _, loc := range locs {
		if !wordBoundaryAt(text, loc[0]) || !wordBoundaryAt(text, loc[1]) {
			continue
		}
		b.WriteString(text[last:loc[0]])
		b.WriteString(repl)
		last = loc[1]
	}
	b.WriteString(text[last:])
	return b.String()
}

// normalizeDetail ports _normalize_detail — strip volatile parts (URLs, shas,
// ids) so repeated errors group as one. Truncation counts code points, like
// Python string slicing.
func normalizeDetail(detail string) string {
	text := detail
	text = detailURLRe.ReplaceAllString(text, "<url>")
	text = detailAPIPathRe.ReplaceAllString(text, "<api-path>")
	text = subWordBounded(detailHexIDRe, text, "<id>")
	text = subWordBounded(detailNumberRe, text, "<n>")
	text = pysem.Strip(detailSpacesRe.ReplaceAllString(text, " "))
	if runes := []rune(text); len(runes) > detailMaxLen {
		text = string(runes[:detailMaxLen-1]) + "\u2026"
	}
	return text
}

// truthyVal mirrors Python truthiness including the Go-native number shapes
// this package stores in result objects (pysem.Truthy covers only the
// JSON-decoded shapes and would read int64(0) as truthy).
func truthyVal(v any) bool {
	switch t := v.(type) {
	case int:
		return t != 0
	case int64:
		return t != 0
	case float64:
		return t != 0
	}
	return pysem.Truthy(v)
}

// intOf mirrors int(v or 0) for the value shapes that reach the counters:
// JSON-decoded scalars plus the Go-native ints this package itself stores.
// int() of a float truncates toward zero in both languages.
func intOf(v any) int64 {
	switch t := v.(type) {
	case nil:
		return 0
	case int:
		return int64(t)
	case int64:
		return t
	case float64:
		return int64(t)
	}
	n, _ := pysem.IntOrZero(v)
	return n
}

// commaInt mirrors Python f"{n:,}" for integers: thousands separated by
// commas, sign preserved.
func commaInt(n int64) string {
	s := strconv.FormatInt(n, 10)
	sign := ""
	if strings.HasPrefix(s, "-") {
		sign, s = "-", s[1:]
	}
	var b strings.Builder
	lead := len(s) % 3
	if lead == 0 {
		lead = 3
	}
	b.WriteString(s[:lead])
	for i := lead; i < len(s); i += 3 {
		b.WriteByte(',')
		b.WriteString(s[i : i+3])
	}
	return sign + b.String()
}

// CoverageTracker ports the CoverageTracker dataclass. Assets holds one
// ordered object per discovered asset (raw values when reconstructed from a
// decoded coverage dict).
type CoverageTracker struct {
	Component  string
	WindowDays int
	Assets     []any
}

// NewCoverageTracker mirrors CoverageTracker(component, window_days).
func NewCoverageTracker(component string, windowDays int) *CoverageTracker {
	return &CoverageTracker{Component: component, WindowDays: windowDays, Assets: []any{}}
}

// Add ports CoverageTracker.add (Python defaults detail="" and eligible=True;
// Go spells both).
func (t *CoverageTracker) Add(provider, container, asset, status, detail string, eligible bool) {
	entry := pysem.EmptyObj()
	entry.Set("provider", provider)
	entry.Set("container", container)
	entry.Set("asset", asset)
	entry.Set("status", status)
	entry.Set("detail", detail)
	entry.Set("eligible", eligible)
	t.Assets = append(t.Assets, entry)
}

// covGroupKey mirrors the (provider, status, detail) tuple key of the
// grouped-errors dict. The fields stay `any` so raw decoded values compare
// the way Python tuple keys do.
type covGroupKey struct {
	provider, status, detail any
}

type covGroup struct {
	provider, status, detail any
	count                    int64
	containers               map[string]bool
	examples                 []any
}

// AsDict ports CoverageTracker.as_dict — the wire-contractual coverage shape,
// key order exactly as the Python dict is built.
func (t *CoverageTracker) AsDict() *pysem.Obj {
	var discovered, eligible, attempted, succeeded, cached, failed, unchanged, inaccessible, truncated int64
	for _, v := range t.Assets {
		a := pysem.AsObj(v)
		if a == nil {
			continue // out of contract: Python would raise on indexing
		}
		discovered++
		if truthyVal(a.Get("eligible")) {
			eligible++
		}
		switch a.Get("status") {
		case "succeeded":
			attempted++
			succeeded++
		case "failed":
			attempted++
			failed++
		case "truncated":
			attempted++
			truncated++
		case "cached":
			cached++
		case "unchanged":
			unchanged++
		case "inaccessible":
			inaccessible++
		}
	}
	completed := succeeded + cached
	gapCount := failed + inaccessible + truncated
	var status string
	switch {
	case gapCount > 0 && completed == 0:
		status = "failed"
	case gapCount > 0:
		status = "partial"
	case completed == 0:
		status = "no_data"
	default:
		status = "complete"
	}

	perContainer := pysem.EmptyObj()
	for _, v := range t.Assets {
		a := pysem.AsObj(v)
		if a == nil {
			continue
		}
		key := pysem.Str(a.Get("provider")) + ":" + pysem.Str(a.Get("container"))
		bucket := pysem.AsObj(perContainer.Get(key))
		if bucket == nil {
			bucket = pysem.EmptyObj()
			bucket.Set("discovered", int64(0))
			bucket.Set("succeeded", int64(0))
			bucket.Set("cached", int64(0))
			bucket.Set("failed", int64(0))
			bucket.Set("unchanged", int64(0))
			perContainer.Set(key, bucket)
		}
		bump := func(field string) { bucket.Set(field, bucket.Get(field).(int64)+1) }
		bump("discovered")
		switch a.Get("status") {
		case "succeeded":
			bump("succeeded")
		case "cached":
			bump("cached")
		case "unchanged":
			bump("unchanged")
		case "failed", "inaccessible", "truncated":
			bump("failed")
		}
	}

	groupOrder := []covGroupKey{}
	grouped := map[covGroupKey]*covGroup{}
	for _, v := range t.Assets {
		a := pysem.AsObj(v)
		if a == nil {
			continue
		}
		st := a.Get("status")
		if st != "failed" && st != "inaccessible" && st != "truncated" {
			continue
		}
		// Python: _normalize_detail(asset["detail"]) — a None detail reads as
		// "" (the `detail or ""` inside), matched here by the string assert.
		rawDetail, _ := a.Get("detail").(string)
		var detail any = normalizeDetail(rawDetail)
		if !truthyVal(detail) {
			detail = st
		}
		key := covGroupKey{provider: a.Get("provider"), status: st, detail: detail}
		group, ok := grouped[key]
		if !ok {
			group = &covGroup{provider: a.Get("provider"), status: st, detail: detail, containers: map[string]bool{}}
			grouped[key] = group
			groupOrder = append(groupOrder, key)
		}
		group.count++
		// Container values are strings on every write path (Add's signature);
		// Str keeps a raw decoded value sortable rather than dropping it.
		group.containers[pysem.Str(a.Get("container"))] = true
		if len(group.examples) < 3 {
			group.examples = append(group.examples, a.Get("asset"))
		}
	}
	groupedErrors := []any{}
	for _, key := range groupOrder {
		group := grouped[key]
		errObj := pysem.EmptyObj()
		errObj.Set("provider", group.provider)
		errObj.Set("status", group.status)
		errObj.Set("detail", group.detail)
		errObj.Set("count", group.count)
		containers := []any{}
		for _, c := range pysem.SortedKeys(group.containers) {
			containers = append(containers, c)
		}
		errObj.Set("containers", containers)
		examples := group.examples
		if examples == nil {
			examples = []any{}
		}
		errObj.Set("examples", examples)
		groupedErrors = append(groupedErrors, errObj)
	}

	out := pysem.EmptyObj()
	out.Set("component", t.Component)
	out.Set("status", status)
	out.Set("has_data", completed > 0)
	pct := 100.0
	if eligible != 0 {
		pct = pysem.RoundN(float64(completed)/float64(eligible)*100, 1)
	}
	// json.Number keeps Python float rendering ("100.0", not "100") on both
	// encoding/json and pysem.JSONDumps paths.
	out.Set("completion_pct", json.Number(pysem.FloatRepr(pct)))
	out.Set("window_days", int64(t.WindowDays))
	out.Set("discovered", discovered)
	out.Set("eligible", eligible)
	out.Set("attempted", attempted)
	out.Set("succeeded", succeeded)
	out.Set("cached", cached)
	out.Set("failed", failed)
	out.Set("unchanged", unchanged)
	out.Set("inaccessible", inaccessible)
	out.Set("truncated", truncated)
	out.Set("completed", completed)
	out.Set("per_container", perContainer)
	out.Set("grouped_errors", groupedErrors)
	out.Set("assets", append([]any{}, t.Assets...))
	return out
}

// CoverageNotes ports coverage_notes — human-readable gaps for legacy
// renderers.
func CoverageNotes(coverage *pysem.Obj) []string {
	notes := []string{}
	grouped, ok := coverage.Get("grouped_errors").([]any)
	if !ok {
		assets, _ := coverage.GetDefault("assets", []any{}).([]any)
		if assets == nil {
			assets = []any{}
		}
		tracker := &CoverageTracker{
			Component:  pysem.Str(coverage.GetDefault("component", "")),
			WindowDays: int(intOf(coverage.Get("window_days"))),
			Assets:     append([]any{}, assets...),
		}
		grouped, _ = tracker.AsDict().Get("grouped_errors").([]any)
	}
	for _, v := range grouped {
		errObj := pysem.AsObj(v)
		if errObj == nil {
			continue // out of contract: Python would raise on .get
		}
		status := errObj.Get("status")
		label := "truncated"
		if status == "failed" || status == "inaccessible" {
			label = "error"
		}
		countVal := errObj.GetDefault("count", int64(1))
		if !truthyVal(countVal) {
			countVal = int64(1)
		}
		count := intOf(countVal)
		containersVal := errObj.Get("containers")
		scope := ""
		if truthyVal(containersVal) {
			n := 0
			switch t := containersVal.(type) {
			case []any:
				n = len(t)
			case string:
				n = utf8.RuneCountInString(t) // Python len() of a str
			}
			scope = " across " + strconv.Itoa(n) + " container(s)"
		}
		detail := pysem.FirstTruthy(errObj.Get("detail"), status)
		notes = append(notes,
			pysem.Str(errObj.GetDefault("provider", ""))+": "+label+
				" ("+commaInt(count)+" item(s)"+scope+": "+pysem.Str(detail)+")")
	}
	return notes
}
