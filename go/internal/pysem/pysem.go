// Package pysem holds Python-semantics helpers shared by every line-for-line
// port in the sidecar (agentwatch, standup). Every place the Python source
// leans on a language builtin — truthiness, str(), int(), strip(), lower(),
// round(), json.dumps, repr() — goes through one of these so the observable
// behavior, and any persisted or wire bytes, stay identical to CPython.
//
// Promoted from go/internal/agentwatch/{pysem,pyjson}.go in Wave 4; the
// agentwatch package keeps thin aliases so its proven parity surface did not
// have to be re-verified.
package pysem

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf16"
	"unicode/utf8"
)

// Error carries the Python exception class name a failure would have had.
type Error struct {
	Class string
	Msg   string
}

func (e *Error) Error() string { return e.Msg }

// IsSpace matches Python str.isspace() — unicode.IsSpace plus the
// file/group/record/unit separators U+001C..U+001F, which Go's table lacks.
func IsSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// Strip mirrors str.strip() with no arguments.
func Strip(s string) string {
	return strings.TrimFunc(s, IsSpace)
}

// Lower mirrors str.lower(). Go's strings.ToLower applies the simple case
// mapping, which sends U+0130 (İ) to a bare "i"; Python applies the full
// mapping from SpecialCasing.txt, which is "i" + combining dot above. İ is
// the only code point whose FULL lowercase differs from the simple one, so it
// is special-cased rather than pulling in x/text.
func Lower(s string) string {
	if strings.ContainsRune(s, 'İ') {
		s = strings.ReplaceAll(s, "İ", "i̇")
	}
	return strings.ToLower(s)
}

// IsWordRune mirrors Python re's unicode \w: underscore, plus anything
// str.isalnum() counts — letters (L*) and all numeric categories (Nd/Nl/No).
func IsWordRune(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsDigit(r) || unicode.Is(unicode.No, r) || unicode.Is(unicode.Nl, r)
}

// Truthy mirrors Python truthiness over JSON-decoded values
// (nil / bool / string / json.Number / []any / *Obj).
func Truthy(v any) bool {
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
	case *Obj:
		return t != nil && len(t.keys) > 0
	}
	return true
}

// FirstTruthy mirrors an `a or b or c` chain.
func FirstTruthy(values ...any) any {
	for _, v := range values {
		if Truthy(v) {
			return v
		}
	}
	if len(values) == 0 {
		return nil
	}
	return values[len(values)-1]
}

// Str mirrors str() over the scalar shapes JSON decoding produces.
func Str(v any) string {
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
		return FloatRepr(f)
	}
	return fmt.Sprintf("%v", v)
}

// FloatRepr approximates Python repr(float) for the rare non-integer-literal
// numbers that reach str().
func FloatRepr(f float64) string {
	if f == math.Trunc(f) && math.Abs(f) < 1e16 {
		return strconv.FormatFloat(f, 'f', 1, 64)
	}
	return strconv.FormatFloat(f, 'g', -1, 64)
}

// IntOrZero mirrors `int(value or 0)`: falsy → 0, floats truncate toward
// zero, numeric strings parse, anything else raises the Python error class.
func IntOrZero(v any) (int64, error) {
	if !Truthy(v) {
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
			return 0, &Error{Class: "ValueError", Msg: "invalid number"}
		}
		return int64(f), nil
	case string:
		s := strings.ReplaceAll(Strip(t), "_", "")
		i, err := strconv.ParseInt(s, 10, 64)
		if err != nil {
			return 0, &Error{Class: "ValueError", Msg: "invalid literal for int()"}
		}
		return i, nil
	}
	return 0, &Error{Class: "TypeError", Msg: "int() argument has wrong type"}
}

// ParseInt mirrors int(str): unicode-whitespace strip, optional sign, ASCII
// digits with single underscores allowed only between digits; anything else
// raises ValueError. Two bounded deviations, both outside every parity
// corpus: non-ASCII decimal digits (int("١٢") works in CPython) are
// rejected, and a value past int64 saturates instead of growing arbitrarily
// — every config.py caller clamps or defaults immediately after parsing, so
// the observable result is identical.
func ParseInt(s string) (int64, error) {
	valueError := &Error{Class: "ValueError", Msg: fmt.Sprintf("invalid literal for int() with base 10: %s", ReprStr(s))}
	t := Strip(s)
	if t == "" {
		return 0, valueError
	}
	digits := t
	if digits[0] == '+' || digits[0] == '-' {
		digits = digits[1:]
	}
	if digits == "" {
		return 0, valueError
	}
	prevDigit := false
	for i := 0; i < len(digits); i++ {
		c := digits[i]
		if c == '_' {
			if !prevDigit {
				return 0, valueError // leading or doubled underscore
			}
			prevDigit = false
			continue
		}
		if c < '0' || c > '9' {
			return 0, valueError
		}
		prevDigit = true
	}
	if !prevDigit {
		return 0, valueError // trailing underscore
	}
	v, err := strconv.ParseInt(strings.ReplaceAll(t, "_", ""), 10, 64)
	if err != nil {
		var numErr *strconv.NumError
		if errors.As(err, &numErr) && errors.Is(numErr.Err, strconv.ErrRange) {
			return v, nil // saturated — see the deviation note above
		}
		return 0, valueError
	}
	return v, nil
}

// RoundN mirrors Python round(x, n) for n >= 0: round to the closest multiple
// of 10^-n with ties to even, computed over the float's exact decimal
// expansion. strconv's fixed-precision formatting is a correctly-rounded
// half-even conversion — the same algorithm CPython's _Py_dg_dtoa applies —
// so format-then-parse reproduces round() bit-for-bit.
func RoundN(x float64, n int) float64 {
	if math.IsNaN(x) || math.IsInf(x, 0) {
		return x
	}
	v, err := strconv.ParseFloat(strconv.FormatFloat(x, 'f', n, 64), 64)
	if err != nil {
		return x
	}
	return v
}

// RoundInt mirrors Python int(round(x)): banker's rounding to zero decimals.
func RoundInt(x float64) int {
	return int(RoundN(x, 0))
}

// Format0f mirrors Python f"{x:.0f}": a correctly-rounded (half-even)
// fixed-point conversion, which Go's strconv also implements.
func Format0f(x float64) string {
	return strconv.FormatFloat(x, 'f', 0, 64)
}

// ---------------------------------------------------------------------------
// Ordered JSON — Python dicts preserve insertion order, json.loads preserves
// document order, and both the security audit and the standup aggregate lean
// on that order. Obj mirrors a Python dict wherever order can matter.
// ---------------------------------------------------------------------------

// Obj is a JSON object with document key order preserved. A duplicate key
// keeps its first position with the last value, exactly like a Python dict.
type Obj struct {
	keys []string
	vals map[string]any
}

// EmptyObj mirrors {}.
func EmptyObj() *Obj {
	return &Obj{vals: map[string]any{}}
}

// Set mirrors d[key] = v.
func (o *Obj) Set(key string, v any) {
	if _, exists := o.vals[key]; !exists {
		o.keys = append(o.keys, key)
	}
	o.vals[key] = v
}

// GetDefault mirrors dict.get(key, default).
func (o *Obj) GetDefault(key string, def any) any {
	if v, ok := o.vals[key]; ok {
		return v
	}
	return def
}

// Get mirrors dict.get(key).
func (o *Obj) Get(key string) any { return o.GetDefault(key, nil) }

// Has mirrors `key in d`.
func (o *Obj) Has(key string) bool {
	_, ok := o.vals[key]
	return ok
}

// Delete mirrors dict.pop(key, None).
func (o *Obj) Delete(key string) {
	if _, ok := o.vals[key]; !ok {
		return
	}
	delete(o.vals, key)
	for i, k := range o.keys {
		if k == key {
			o.keys = append(o.keys[:i], o.keys[i+1:]...)
			break
		}
	}
}

// Keys returns the keys in document/insertion order (do not mutate).
func (o *Obj) Keys() []string { return o.keys }

// Len mirrors len(d).
func (o *Obj) Len() int { return len(o.keys) }

// Clone is a shallow copy, mirroring dict(d).
func (o *Obj) Clone() *Obj {
	out := EmptyObj()
	for _, k := range o.keys {
		out.Set(k, o.vals[k])
	}
	return out
}

// AsObj returns v as an ordered object, or nil when it is not one (the
// isinstance(v, dict) checks).
func AsObj(v any) *Obj {
	o, _ := v.(*Obj)
	return o
}

// MarshalJSON emits the object with its keys in document/insertion order, so
// an Obj can ride encoding/json all the way out through the RPC writer and a
// Python json.loads sees the same dict order the reference implementation
// produced.
func (o *Obj) MarshalJSON() ([]byte, error) {
	var b bytes.Buffer
	b.WriteByte('{')
	for i, k := range o.keys {
		if i > 0 {
			b.WriteByte(',')
		}
		kb, err := json.Marshal(k)
		if err != nil {
			return nil, err
		}
		b.Write(kb)
		b.WriteByte(':')
		vb, err := json.Marshal(o.vals[k])
		if err != nil {
			return nil, err
		}
		b.Write(vb)
	}
	b.WriteByte('}')
	return b.Bytes(), nil
}

// DecodeOrdered parses one JSON document into nil / bool / json.Number /
// string / []any / *Obj, rejecting trailing data like Python json.loads.
func DecodeOrdered(data []byte) (any, error) {
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
		obj := EmptyObj()
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
			obj.Set(key, val)
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

// JSONDumps renders a decoded value exactly as Python's json.dumps defaults
// would: ", "/": " separators, ensure_ascii escaping, document key order.
func JSONDumps(v any) string {
	var b strings.Builder
	writeJSONValue(&b, v)
	return b.String()
}

func writeJSONValue(b *strings.Builder, v any) {
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
		WriteJSONString(b, t)
	case json.Number:
		b.WriteString(JSONNumber(t))
	case []any:
		b.WriteByte('[')
		for i, e := range t {
			if i > 0 {
				b.WriteString(", ")
			}
			writeJSONValue(b, e)
		}
		b.WriteByte(']')
	case *Obj:
		b.WriteByte('{')
		for i, k := range t.keys {
			if i > 0 {
				b.WriteString(", ")
			}
			WriteJSONString(b, k)
			b.WriteString(": ")
			writeJSONValue(b, t.vals[k])
		}
		b.WriteByte('}')
	default:
		b.WriteString(Str(v))
	}
}

// WriteJSONString escapes like json.dumps with ensure_ascii=True.
func WriteJSONString(b *strings.Builder, s string) {
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

// JSONNumber renders a decoded number like Python json.dumps: an integer
// literal as itself, a float through repr(float).
func JSONNumber(n json.Number) string {
	s := string(n)
	if !strings.ContainsAny(s, ".eE") {
		return s
	}
	f, err := n.Float64()
	if err != nil {
		return s
	}
	return FloatRepr(f)
}

// ReprStr mirrors Python repr() for str for printable shapes: single quotes,
// unless the string holds a single quote and no double quote; backslash/
// quote/\n\r\t escapes; non-printable characters as \xXX / \uXXXX /
// \UXXXXXXXX.
func ReprStr(s string) string {
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
		case !Printable(r):
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

// Printable approximates str.isprintable(): everything except the control,
// format, surrogate, private-use and non-space separator categories.
func Printable(r rune) bool {
	if r == ' ' {
		return true
	}
	return !unicode.In(r, unicode.Cc, unicode.Cf, unicode.Cs, unicode.Co, unicode.Zl, unicode.Zp, unicode.Zs)
}

// ReprAny mirrors !r for scalar shapes.
func ReprAny(v any) string {
	switch t := v.(type) {
	case string:
		return ReprStr(t)
	case bool:
		if t {
			return "True"
		}
		return "False"
	case nil:
		return "None"
	case json.Number:
		return JSONNumber(t)
	}
	return Str(v)
}

// PathStr mirrors str(pathlib.Path(base) / parts...) for POSIX-style path
// strings: "." and empty segments collapse, trailing/duplicate slashes drop,
// ".." is KEPT (pathlib does not resolve it).
func PathStr(base string, parts ...string) string {
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

// SortedKeys returns a map's keys sorted, mirroring sorted(d).
func SortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// splitlineBreaks holds every terminator str.splitlines() recognises — a
// wider set than "\n" ("\r\n" counts once). Promoted from
// go/internal/analysis/practices.go in Wave 7.
var splitlineBreaks = map[rune]bool{
	'\n': true, '\r': true, '\v': true, '\f': true,
	0x1c: true, 0x1d: true, 0x1e: true, 0x85: true, 0x2028: true, 0x2029: true,
}

// Splitlines mirrors str.splitlines() (no trailing empty line for a final
// terminator; "\r\n" is one break).
func Splitlines(s string) []string {
	out := []string{}
	start, i := 0, 0
	for i < len(s) {
		r, size := utf8.DecodeRuneInString(s[i:])
		if !splitlineBreaks[r] {
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

// SplitWS mirrors no-argument str.split(): runs of unicode whitespace
// (Python's isspace set) separate fields, and empty fields never appear.
// The same shape go/internal/standup spells inline as
// strings.FieldsFunc(s, pysem.IsSpace).
func SplitWS(s string) []string {
	return strings.FieldsFunc(s, IsSpace)
}

// quoteAlwaysSafe is urllib.parse.quote's always-safe set (RFC 3986
// unreserved): ALPHA / DIGIT / "_" / "." / "-" / "~".
func quoteSafeByte(b byte) bool {
	switch {
	case b >= 'A' && b <= 'Z', b >= 'a' && b <= 'z', b >= '0' && b <= '9':
		return true
	case b == '_' || b == '.' || b == '-' || b == '~':
		return true
	}
	return false
}

// QuoteAll mirrors urllib.parse.quote(s, safe=""): UTF-8 encode, keep only
// the always-safe unreserved bytes, percent-encode everything else with
// uppercase hex. (The dot stays — it is in urllib's always-safe set no
// matter what safe says; callers that need it encoded replace it after.)
func QuoteAll(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		if quoteSafeByte(c) {
			b.WriteByte(c)
		} else {
			b.WriteString(fmt.Sprintf("%%%02X", c))
		}
	}
	return b.String()
}
