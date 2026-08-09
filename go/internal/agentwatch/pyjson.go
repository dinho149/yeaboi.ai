package agentwatch

// Ordered JSON + Python repr, for the security audit port (security.go).
//
// Python's json.loads keeps object keys in DOCUMENT order (dicts preserve
// insertion), and security_checks iterates those dicts — so the order of
// findings, MCP records and duplicate-name notes all depend on it (contract
// rule 8). A plain Go map loses that order, hence jsonObj. The audit also
// re-serializes config subtrees before pattern-matching (hooks, MCP env) and
// embeds config values in finding details via Python's !r — pyJSONDumps and
// pyReprStr reproduce those byte-for-byte for the shapes configs contain.

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"unicode"
)

// jsonObj is a JSON object with document key order preserved. A duplicate key
// keeps its first position with the last value, exactly like a Python dict.
type jsonObj struct {
	keys []string
	vals map[string]any
}

func emptyObj() *jsonObj {
	return &jsonObj{vals: map[string]any{}}
}

func (o *jsonObj) set(key string, v any) {
	if _, exists := o.vals[key]; !exists {
		o.keys = append(o.keys, key)
	}
	o.vals[key] = v
}

// getDefault mirrors dict.get(key, default).
func (o *jsonObj) getDefault(key string, def any) any {
	if v, ok := o.vals[key]; ok {
		return v
	}
	return def
}

func (o *jsonObj) has(key string) bool {
	_, ok := o.vals[key]
	return ok
}

// asObj returns v as an ordered object, or nil when it is not one (the
// isinstance(v, dict) checks).
func asObj(v any) *jsonObj {
	o, _ := v.(*jsonObj)
	return o
}

// decodeOrderedJSON parses one JSON document into nil / bool / json.Number /
// string / []any / *jsonObj, rejecting trailing data like Python json.loads.
func decodeOrderedJSON(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	v, err := decodeOrderedValue(dec)
	if err != nil {
		return nil, err
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, errors.New("trailing data after JSON document")
	}
	return v, nil
}

func decodeOrderedValue(dec *json.Decoder) (any, error) {
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	delim, ok := tok.(json.Delim)
	if !ok {
		return tok, nil // string, json.Number, bool, nil
	}
	switch delim {
	case '{':
		obj := emptyObj()
		for dec.More() {
			keyTok, err := dec.Token()
			if err != nil {
				return nil, err
			}
			key, _ := keyTok.(string)
			val, err := decodeOrderedValue(dec)
			if err != nil {
				return nil, err
			}
			obj.set(key, val)
		}
		if _, err := dec.Token(); err != nil { // consume '}'
			return nil, err
		}
		return obj, nil
	case '[':
		arr := []any{}
		for dec.More() {
			val, err := decodeOrderedValue(dec)
			if err != nil {
				return nil, err
			}
			arr = append(arr, val)
		}
		if _, err := dec.Token(); err != nil { // consume ']'
			return nil, err
		}
		return arr, nil
	}
	return nil, fmt.Errorf("unexpected delimiter %v", delim)
}

// pyJSONDumps renders a decoded value exactly as Python's json.dumps defaults
// would: ", "/": " separators, ensure_ascii escaping, document key order.
func pyJSONDumps(v any) string {
	var b strings.Builder
	writePyJSONValue(&b, v)
	return b.String()
}

func writePyJSONValue(b *strings.Builder, v any) {
	switch t := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if t {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case string:
		writePyJSONString(b, t)
	case json.Number:
		b.WriteString(pyJSONNumber(t))
	case []any:
		b.WriteByte('[')
		for i, e := range t {
			if i > 0 {
				b.WriteString(", ")
			}
			writePyJSONValue(b, e)
		}
		b.WriteByte(']')
	case *jsonObj:
		b.WriteByte('{')
		for i, k := range t.keys {
			if i > 0 {
				b.WriteString(", ")
			}
			writePyJSONString(b, k)
			b.WriteString(": ")
			writePyJSONValue(b, t.vals[k])
		}
		b.WriteByte('}')
	default:
		b.WriteString(pyStr(v))
	}
}

// pyJSONNumber renders a decoded number like Python json.dumps: an integer
// literal as itself, a float through repr(float).
func pyJSONNumber(n json.Number) string {
	s := string(n)
	if !strings.ContainsAny(s, ".eE") {
		return s
	}
	f, err := n.Float64()
	if err != nil {
		return s
	}
	return pyFloatRepr(f)
}

// pyReprStr mirrors Python repr() for str for the printable shapes config
// values contain: single quotes, unless the string holds a single quote and
// no double quote; backslash/quote/\n\r\t escapes; non-printable characters
// as \xXX / \uXXXX / \UXXXXXXXX.
func pyReprStr(s string) string {
	quote := '\''
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var b strings.Builder
	b.WriteRune(quote)
	for _, r := range s {
		switch {
		case r == quote || r == '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case !pyPrintable(r):
			switch {
			case r < 0x100:
				fmt.Fprintf(&b, `\x%02x`, r)
			case r <= 0xffff:
				fmt.Fprintf(&b, `\u%04x`, r)
			default:
				fmt.Fprintf(&b, `\U%08x`, r)
			}
		default:
			b.WriteRune(r)
		}
	}
	b.WriteRune(quote)
	return b.String()
}

// pyPrintable approximates str.isprintable(): everything except the control,
// format, surrogate, private-use and non-space separator categories (the
// unassigned check is skipped — unassigned code points do not appear in real
// config files).
func pyPrintable(r rune) bool {
	if r == ' ' {
		return true
	}
	return !unicode.In(r, unicode.Cc, unicode.Cf, unicode.Cs, unicode.Co, unicode.Zl, unicode.Zp, unicode.Zs)
}

// pyReprAny mirrors !r for the scalar shapes findings quote.
func pyReprAny(v any) string {
	switch t := v.(type) {
	case string:
		return pyReprStr(t)
	case bool:
		if t {
			return "True"
		}
		return "False"
	case nil:
		return "None"
	case json.Number:
		return pyJSONNumber(t)
	}
	return pyStr(v)
}

// pyPathStr mirrors str(pathlib.Path(base) / parts...) for POSIX-style path
// strings: "." and empty segments collapse, trailing/duplicate slashes drop,
// ".." is KEPT (pathlib does not resolve it).
func pyPathStr(base string, parts ...string) string {
	rooted := strings.HasPrefix(base, "/")
	segs := []string{}
	for _, piece := range append([]string{base}, parts...) {
		for _, s := range strings.Split(piece, "/") {
			if s == "" || s == "." {
				continue
			}
			segs = append(segs, s)
		}
	}
	out := strings.Join(segs, "/")
	switch {
	case rooted:
		return "/" + out
	case out == "":
		return "."
	default:
		return out
	}
}
