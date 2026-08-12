package agentwatch

// Python-semantics helpers — thin aliases over internal/pysem, where the
// shared implementations moved in Wave 4 so the standup port could use them.
// The local names (and the local pyError type, whose class names reach
// persisted warnings) are kept so the proven agentwatch parity surface did
// not have to be edited call-site by call-site.

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// pyError carries the Python exception class name a failure would have had.
// Only the class name ever reaches a persisted warning (privacy rule 1).
type pyError struct {
	class string
	msg   string
}

func (e *pyError) Error() string { return e.msg }

func pyIsSpace(r rune) bool { return pysem.IsSpace(r) }

func pyStrip(s string) string { return pysem.Strip(s) }

func pyTruthy(v any) bool { return pysem.Truthy(v) }

func firstTruthy(values ...any) any { return pysem.FirstTruthy(values...) }

func pyStr(v any) string { return pysem.Str(v) }

func pyFloatRepr(f float64) string { return pysem.FloatRepr(f) }

// pyIntOrZero mirrors `int(value or 0)`, translating the shared error type
// back into the local pyError so pyErrClass keeps seeing the class name.
func pyIntOrZero(v any) (int64, error) {
	i, err := pysem.IntOrZero(v)
	var pe *pysem.Error
	if errors.As(err, &pe) {
		return i, &pyError{class: pe.Class, msg: pe.Msg}
	}
	return i, err
}

// round4 mirrors Python round(x, 4) — see pysem.RoundN for the algorithm note
// (verified against CPython in TestRound4MatchesPython).
func round4(x float64) float64 { return pysem.RoundN(x, 4) }

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
		pysem.WriteJSONString(&b, k)
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
		pysem.WriteJSONString(b, k)
		b.WriteString(": ")
		b.WriteString(strconv.FormatInt(m[k], 10))
	}
	b.WriteByte('}')
}

func sortedKeys[V any](m map[string]V) []string { return pysem.SortedKeys(m) }

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
