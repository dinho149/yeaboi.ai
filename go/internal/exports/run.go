// The RPC entrypoints behind retro.build_export and poker.build_export
// (contracts/v1), plus the shared Python-conversion helpers the two report
// deserializers lean on. Params arrive as ordered JSON, results go back as
// *pysem.Obj in the reference implementation's dict-literal key order
// (contractual — args is json.dumps-ed into the page boot payload). The
// reference implementations are src/yeaboi/retro/export.py::build_retro_export
// and src/yeaboi/poker/export.py::build_poker_export. Pure compute: no DB, no
// clock, no logging, and never any input content in an error.
package exports

import (
	"encoding/json"
	"strconv"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// RunRetroBuildExport mirrors export.build_retro_export: rebuild the report
// through the store-deserializer round-trip, then render markdown + args
// with the wire-pinned timestamps.
func RunRetroBuildExport(params *pysem.Obj) (*pysem.Obj, error) {
	reportObj, err := objParam(params, "report")
	if err != nil {
		return nil, err
	}
	history, err := listParam(params, "history")
	if err != nil {
		return nil, err
	}
	editable, err := requiredParam(params, "editable")
	if err != nil {
		return nil, err
	}
	generatedTS, err := requiredParam(params, "generated_ts")
	if err != nil {
		return nil, err
	}
	generatedDate, err := requiredParam(params, "generated_date")
	if err != nil {
		return nil, err
	}
	report, err := retroReportFrom(reportObj)
	if err != nil {
		return nil, err
	}
	args, err := retroExportArgs(report, history, pysem.Truthy(editable), generatedDate)
	if err != nil {
		return nil, err
	}
	result := pysem.EmptyObj()
	result.Set("contract_version", int64(1))
	result.Set("markdown", buildRetroMarkdown(report, generatedTS))
	result.Set("args", args)
	return result, nil
}

// RunPokerBuildExport mirrors export.build_poker_export. No editable param —
// poker has no editable share.
func RunPokerBuildExport(params *pysem.Obj) (*pysem.Obj, error) {
	reportObj, err := objParam(params, "report")
	if err != nil {
		return nil, err
	}
	history, err := listParam(params, "history")
	if err != nil {
		return nil, err
	}
	generatedTS, err := requiredParam(params, "generated_ts")
	if err != nil {
		return nil, err
	}
	generatedDate, err := requiredParam(params, "generated_date")
	if err != nil {
		return nil, err
	}
	report, err := pokerReportFrom(reportObj)
	if err != nil {
		return nil, err
	}
	args, err := pokerExportArgs(report, history, generatedDate)
	if err != nil {
		return nil, err
	}
	result := pysem.EmptyObj()
	result.Set("contract_version", int64(1))
	result.Set("markdown", buildPokerMarkdown(report, generatedTS))
	result.Set("args", args)
	return result, nil
}

// requiredParam mirrors the reference's inputs["key"] — a missing key is a
// KeyError, never a silent default.
func requiredParam(params *pysem.Obj, key string) (any, error) {
	if !params.Has(key) {
		return nil, &pysem.Error{Class: "KeyError", Msg: key + " is required"}
	}
	return params.Get(key), nil
}

func objParam(params *pysem.Obj, key string) (*pysem.Obj, error) {
	value, err := requiredParam(params, key)
	if err != nil {
		return nil, err
	}
	obj := pysem.AsObj(value)
	if obj == nil {
		return nil, &pysem.Error{Class: "TypeError", Msg: key + " must be an object"}
	}
	return obj, nil
}

func listParam(params *pysem.Obj, key string) ([]any, error) {
	value, err := requiredParam(params, key)
	if err != nil {
		return nil, err
	}
	list, ok := value.([]any)
	if !ok {
		return nil, &pysem.Error{Class: "TypeError", Msg: key + " must be a list"}
	}
	return list, nil
}

// iterField mirrors tuple(d.get(key, ())): a missing key is empty, a list
// iterates as itself, a string iterates per code point, an object iterates
// its keys (dict iteration), and anything else is the TypeError iter(x)
// would raise.
func iterField(d *pysem.Obj, key string) ([]any, error) {
	if !d.Has(key) {
		return []any{}, nil
	}
	switch t := d.Get(key).(type) {
	case []any:
		return t, nil
	case string:
		out := []any{}
		for _, r := range t {
			out = append(out, string(r))
		}
		return out, nil
	case *pysem.Obj:
		out := []any{}
		for _, k := range t.Keys() {
			out = append(out, k)
		}
		return out, nil
	}
	return nil, &pysem.Error{Class: "TypeError", Msg: key + " is not iterable"}
}

// mdTableCell ports markdown_convert.md_table_cell — pipes would split the
// cell and newlines would break the row, so `|` becomes `\` and whitespace
// runs collapse (`" ".join(str(text).replace("|", "\\").split())`).
func mdTableCell(text any) string {
	return strings.Join(pysem.SplitWS(strings.ReplaceAll(pysem.Str(text), "|", `\`)), " ")
}

// joinOrDash mirrors `", ".join(participants) if participants else "—"`.
// The reference's join raises TypeError on a non-string participant; the
// Str here is unobservable in practice (participants are display names).
func joinOrDash(values []any) string {
	if len(values) == 0 {
		return "—"
	}
	parts := make([]string, 0, len(values))
	for _, v := range values {
		parts = append(parts, pysem.Str(v))
	}
	return strings.Join(parts, ", ")
}

// intCast mirrors int(value) where the reference lets the error propagate
// (reaction counts). Messages are fixed — input content never appears in an
// error.
func intCast(v any) (int64, error) {
	switch t := v.(type) {
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	case json.Number:
		s := string(t)
		if !strings.ContainsAny(s, ".eE") {
			n, err := strconv.ParseInt(s, 10, 64)
			if err != nil {
				return 0, &pysem.Error{Class: "ValueError", Msg: "invalid integer literal"}
			}
			return n, nil
		}
		f, err := t.Float64()
		if err != nil {
			return 0, &pysem.Error{Class: "ValueError", Msg: "invalid number literal"}
		}
		return int64(f), nil // int(float) truncates toward zero
	case string:
		n, err := strconv.ParseInt(strings.TrimFunc(t, pysem.IsSpace), 10, 64)
		if err != nil {
			return 0, &pysem.Error{Class: "ValueError", Msg: "invalid literal for int()"}
		}
		return n, nil
	}
	return 0, &pysem.Error{Class: "TypeError", Msg: "count must be a number or a numeric string"}
}

// strconvParsePyFloat parses a string the way float(s) does for the shapes
// that reach this seam: unicode-whitespace trim, then a decimal literal.
func strconvParsePyFloat(s string) (float64, error) {
	return strconv.ParseFloat(strings.TrimFunc(s, pysem.IsSpace), 64)
}

// pyFloatOrNil ports poker/store._float_or_none: float(v) with TypeError and
// ValueError swallowed to None — nil in, nil out, nil on anything
// unconvertible.
func pyFloatOrNil(v any) any {
	if v == nil {
		return nil
	}
	f, err := pyFloat(v)
	if err != nil {
		return nil
	}
	return f
}

// numOrNil renders a post-widening float for the wire: nil stays null, a
// float64 goes out through Python repr(float) (3 arrived, 3.0 leaves —
// json.dumps of the reference's widened float).
func numOrNil(v any) any {
	f, ok := v.(float64)
	if !ok {
		return nil
	}
	return json.Number(pysem.FloatRepr(f))
}
