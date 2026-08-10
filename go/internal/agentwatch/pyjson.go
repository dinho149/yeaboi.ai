package agentwatch

// Ordered JSON + Python repr — thin aliases over internal/pysem (promoted in
// Wave 4). The security audit's ordering/repr rationale lives with the shared
// implementations now; this file only preserves the local names its call
// sites were written against.

import (
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// jsonObj is a JSON object with document key order preserved (pysem.Obj).
type jsonObj = pysem.Obj

func emptyObj() *jsonObj { return pysem.EmptyObj() }

func asObj(v any) *jsonObj { return pysem.AsObj(v) }

func decodeOrderedJSON(data []byte) (any, error) { return pysem.DecodeOrdered(data) }

func pyJSONDumps(v any) string { return pysem.JSONDumps(v) }

func pyReprStr(s string) string { return pysem.ReprStr(s) }

func pyReprAny(v any) string { return pysem.ReprAny(v) }

func pyPathStr(base string, parts ...string) string { return pysem.PathStr(base, parts...) }

func writePyJSONString(b *strings.Builder, s string) { pysem.WriteJSONString(b, s) }
