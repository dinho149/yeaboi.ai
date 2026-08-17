// Port of src/yeaboi/html_theme.py (safe_url, history_series, trend) — keep
// in lockstep; the Python module is the reference implementation and
// tests/parity/test_exports_parity.py diffs whole-seam output.
//
// Package exports serves retro.build_export and poker.build_export
// (contracts/v1): the pure builders behind the retro/poker document seam.
// Pure compute: no DB, no clock, no logging — this package imports no
// logging facility at all (card text, ticket summaries and voter names cross
// the wire as params, and the privacy invariant says input content never
// reaches a log line or an error message; see rpc.md rule 13).
//
// Documented deviation: Python's safe_url logs a warning when it drops a
// protocol-relative URL or an unsafe scheme; the Go side drops silently.
package exports

import (
	"encoding/json"
	"regexp"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// safeURLSchemes ports _SAFE_URL_SCHEMES — schemes allowed to reach an href.
var safeURLSchemes = map[string]bool{"http": true, "https": true, "mailto": true}

// schemeRe ports _SCHEME_RE — a scheme per RFC 3986 (all-ASCII, RE2-safe).
var schemeRe = regexp.MustCompile(`^([A-Za-z][A-Za-z0-9+.\-]*):`)

// urlEdgeStrip is str.strip(" \t\n\r\v\f\x00\x7f")'s character set.
func urlEdgeStrip(r rune) bool {
	switch r {
	case ' ', '\t', '\n', '\r', '\v', '\f', '\x00', '\x7f':
		return true
	}
	return false
}

// interiorStrip ports _URL_STRIP_RE — browsers remove TAB/LF/CR from
// anywhere in a URL before parsing, so the allowlist must too.
var interiorStrip = strings.NewReplacer("\t", "", "\n", "", "\r", "")

// SafeURL ports html_theme.safe_url: the url if it is safe to place in an
// href, else "". Relative references (no scheme) pass through unchanged;
// protocol-relative "//host" and non-allowlisted schemes are dropped.
func SafeURL(url any) string {
	if !pysem.Truthy(url) {
		return ""
	}
	cleaned := interiorStrip.Replace(strings.TrimFunc(pysem.Str(url), urlEdgeStrip))
	if cleaned == "" {
		return ""
	}
	if strings.HasPrefix(cleaned, "//") {
		return "" // protocol-relative — Python also warns here; we only drop
	}
	m := schemeRe.FindStringSubmatch(cleaned)
	if m == nil {
		return cleaned // relative reference — inert
	}
	if safeURLSchemes[strings.ToLower(m[1])] {
		return cleaned
	}
	return "" // unsafe scheme — Python also warns here; we only drop
}

// seriesPoint is one (day, value) pair from history_series. day stays `any`
// because the reference appends the CURRENT point's day un-stringified.
type seriesPoint struct {
	day   any
	value float64
}

// historySeries ports html_theme.history_series for the callers this seam
// has: newest-first store rows → (date, value) oldest → newest, cutoff
// applied, same-date dedupe keeping the newest, current appended when its
// date is not already present, capped to the trailing max_points. The
// status_key filtering stays unported until a wave needs it (retro and poker
// never pass it).
func historySeries(
	rows []any, dateKey, valueKey string, cutoff any, currentDay any, currentValue float64, maxPoints int,
) ([]seriesPoint, error) {
	points := []seriesPoint{}
	seen := map[string]bool{}
	cutoffStr, cutoffIsStr := cutoff.(string)
	for _, raw := range rows {
		row := pysem.AsObj(raw)
		if row == nil {
			return nil, &pysem.Error{Class: "AttributeError", Msg: "history rows must be objects"}
		}
		day := pysem.Str(orEmpty(row.GetDefault(dateKey, "")))
		value := row.Get(valueKey)
		if day == "" || value == nil {
			continue
		}
		if pysem.Truthy(cutoff) {
			if !cutoffIsStr {
				return nil, &pysem.Error{Class: "TypeError", Msg: "cutoff_date must be a string"}
			}
			// Go byte order == Python code-point order for UTF-8 strings.
			if day > cutoffStr {
				continue
			}
		}
		if seen[day] {
			continue
		}
		seen[day] = true
		f, err := pyFloat(value)
		if err != nil {
			return nil, err
		}
		points = append(points, seriesPoint{day, f})
	}
	// Input is newest-first; the chart wants oldest → newest.
	for i, j := 0, len(points)-1; i < j; i, j = i+1, j-1 {
		points[i], points[j] = points[j], points[i]
	}
	if pysem.Truthy(currentDay) {
		key, isStr := currentDay.(string)
		if !isStr || !seen[key] {
			points = append(points, seriesPoint{currentDay, currentValue})
		}
	}
	if len(points) > maxPoints {
		points = points[len(points)-maxPoints:]
	}
	return points, nil
}

// trendPayload ports html_theme.trend for the two callers this seam has: the
// export bundle's trend-card payload, or nil for no chart (fewer than two
// points — one run is not a trend; nil rather than an omitted key, so the
// bundle can tell "no chart" from "field missing"). floor/ceiling stay
// unported until a wave needs them (retro and poker never pass them).
func trendPayload(
	rows []any, dateKey, valueKey, title, label string, cutoff any, currentDay any, currentValue float64,
) (any, error) {
	points, err := historySeries(rows, dateKey, valueKey, cutoff, currentDay, currentValue, 14)
	if err != nil {
		return nil, err
	}
	if len(points) < 2 {
		return nil, nil
	}
	out := pysem.EmptyObj()
	out.Set("title", title)
	out.Set("label", label+" — last "+pysem.Str(int64(len(points)))+" runs")
	wire := []any{}
	for _, p := range points {
		wire = append(wire, []any{p.day, json.Number(pysem.FloatRepr(p.value))})
	}
	out.Set("points", wire)
	return out, nil
}

// orEmpty mirrors `value or ""`.
func orEmpty(v any) any {
	if pysem.Truthy(v) {
		return v
	}
	return ""
}

// orDash mirrors `str(value or "—")` at the f-string sites that print it.
func orDash(v any) string {
	if pysem.Truthy(v) {
		return pysem.Str(v)
	}
	return "—"
}

// eqStr reports whether v is the exact string s — Python `v == "s"`, which
// is False (never an error) for non-string values.
func eqStr(v any, s string) bool {
	got, ok := v.(string)
	return ok && got == s
}

// pyFloat mirrors float(value) where the reference lets the error propagate.
// The message is fixed — input content never appears in an error.
func pyFloat(v any) (float64, error) {
	switch t := v.(type) {
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	case json.Number:
		f, err := t.Float64()
		if err != nil {
			return 0, &pysem.Error{Class: "ValueError", Msg: "could not convert value to float"}
		}
		return f, nil
	case string:
		f, err := strconvParsePyFloat(t)
		if err != nil {
			return 0, &pysem.Error{Class: "ValueError", Msg: "could not convert string to float"}
		}
		return f, nil
	}
	return 0, &pysem.Error{Class: "TypeError", Msg: "value must be a string or a number"}
}
