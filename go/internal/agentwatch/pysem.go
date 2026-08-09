package agentwatch

// Python-semantics helpers. The collector and store are line-for-line ports of
// src/yeaboi/agentwatch/{collector,store}.py, so every place the Python code
// leans on a language builtin (truthiness, str(), int(), strip(), round(),
// json.dumps) goes through one of these to keep the observable behavior — and
// the persisted bytes — identical.

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf16"
)

// pyError carries the Python exception class name a failure would have had.
// Only the class name ever reaches a persisted warning (privacy rule 1).
type pyError struct {
	class string
	msg   string
}

func (e *pyError) Error() string { return e.msg }

// pyIsSpace matches Python str.isspace() — unicode.IsSpace plus the
// file/group/record/unit separators U+001C..U+001F, which Go's table lacks.
func pyIsSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// pyStrip mirrors str.strip() with no arguments.
func pyStrip(s string) string {
	return strings.TrimFunc(s, pyIsSpace)
}

// pyTruthy mirrors Python truthiness over JSON-decoded values
// (nil / bool / string / json.Number / []any / map[string]any).
func pyTruthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case json.Number:
		f, err := strconv.ParseFloat(string(t), 64)
		if err != nil {
			return true
		}
		return f != 0
	case []any:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	}
	return true
}

// firstTruthy mirrors an `a or b or c` chain.
func firstTruthy(values ...any) any {
	for _, v := range values {
		if pyTruthy(v) {
			return v
		}
	}
	if len(values) == 0 {
		return nil
	}
	return values[len(values)-1]
}

// pyStr mirrors str() over the scalar shapes the collector feeds it. Real
// transcripts only ever hit the string branch.
func pyStr(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case bool:
		if t {
			return "True"
		}
		return "False"
	case nil:
		return "None"
	case int:
		return strconv.Itoa(t)
	case int64:
		return strconv.FormatInt(t, 10)
	case json.Number:
		s := string(t)
		if !strings.ContainsAny(s, ".eE") {
			return s // integer literal renders as itself, like Python int
		}
		f, err := strconv.ParseFloat(s, 64)
		if err != nil {
			return s
		}
		return pyFloatRepr(f)
	}
	return fmt.Sprintf("%v", v)
}

// pyFloatRepr approximates Python repr(float) for the rare non-integer-literal
// numbers that reach str().
func pyFloatRepr(f float64) string {
	if f == math.Trunc(f) && math.Abs(f) < 1e16 {
		return strconv.FormatFloat(f, 'f', 1, 64)
	}
	return strconv.FormatFloat(f, 'g', -1, 64)
}

// pyIntOrZero mirrors `int(value or 0)`: falsy → 0, floats truncate toward
// zero, numeric strings parse, anything else raises the Python error class.
func pyIntOrZero(v any) (int64, error) {
	if !pyTruthy(v) {
		return 0, nil
	}
	switch t := v.(type) {
	case bool:
		return 1, nil // false is falsy, handled above
	case json.Number:
		s := string(t)
		if i, err := strconv.ParseInt(s, 10, 64); err == nil {
			return i, nil
		}
		f, err := strconv.ParseFloat(s, 64)
		if err != nil {
			return 0, &pyError{class: "ValueError", msg: "invalid number"}
		}
		return int64(f), nil
	case string:
		s := strings.ReplaceAll(pyStrip(t), "_", "")
		i, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return 0, &pyError{class: "ValueError", msg: "invalid literal for int()"}
		}
		return i, nil
	}
	return 0, &pyError{class: "TypeError", msg: "int() argument has wrong type"}
}

// round4 mirrors Python round(x, 4): round to the closest multiple of 1e-4
// with ties going to the even choice (banker's rounding), computed over the
// float's exact decimal expansion. strconv's fixed-precision formatting is a
// correctly-rounded half-even conversion — the same algorithm CPython's
// _Py_dg_dtoa applies — so format-then-parse reproduces round() bit-for-bit
// (verified against CPython in TestRound4MatchesPython).
func round4(x float64) float64 {
	if math.IsNaN(x) || math.IsInf(x, 0) {
		return x
	}
	v, err := strconv.ParseFloat(strconv.FormatFloat(x, 'f', 4, 64), 64)
	if err != nil {
		return x
	}
	return v
}

// pyJSONDumpsUsage renders {model: {key: int}} exactly as Python's
// json.dumps(value, sort_keys=True): sorted keys, ", "/": " separators,
// ensure_ascii escaping. The bytes are persisted, so they must match.
func pyJSONDumpsUsage(m map[string]map[string]int64) string {
	var b strings.Builder
	b.WriteByte('{')
	for i, k := range sortedKeys(m) {
		if i > 0 {
			b.WriteString(", ")
		}
		writePyJSONString(&b, k)
		b.WriteString(": ")
		writePyIntMap(&b, m[k])
	}
	b.WriteByte('}')
	return b.String()
}

// pyJSONDumpsCounts renders {name: int} the same way.
func pyJSONDumpsCounts(m map[string]int64) string {
	var b strings.Builder
	writePyIntMap(&b, m)
	return b.String()
}

func writePyIntMap(b *strings.Builder, m map[string]int64) {
	b.WriteByte('{')
	for i, k := range sortedKeys(m) {
		if i > 0 {
			b.WriteString(", ")
		}
		writePyJSONString(b, k)
		b.WriteString(": ")
		b.WriteString(strconv.FormatInt(m[k], 10))
	}
	b.WriteByte('}')
}

// writePyJSONString escapes like json.dumps with ensure_ascii=True.
func writePyJSONString(b *strings.Builder, s string) {
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			switch {
			case r < 0x20 || (r >= 0x7f && r <= 0xffff):
				fmt.Fprintf(b, `\u%04x`, r)
			case r > 0xffff:
				h, l := utf16.EncodeRune(r)
				fmt.Fprintf(b, `\u%04x\u%04x`, h, l)
			default:
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// nowISO mirrors datetime.now(UTC).isoformat(): microsecond precision with a
// "+00:00" offset, the fractional part omitted when it is exactly zero.
func nowISO() string {
	t := time.Now().UTC()
	micro := t.Nanosecond() / 1000
	base := t.Format("2006-01-02T15:04:05")
	if micro == 0 {
		return base + "+00:00"
	}
	return fmt.Sprintf("%s.%06d+00:00", base, micro)
}
