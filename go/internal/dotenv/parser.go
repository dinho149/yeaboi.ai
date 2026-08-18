// Port of python-dotenv's parser (site-packages/dotenv/parser.py, v1.1) —
// keep in lockstep; the library is pinned by uv.lock and yeaboi/config.py
// leans on its exact parse of ~/.yeaboi/.env and the project .env, so the
// Go reader must accept and reject byte-for-byte the same statements.
// tests/parity/foundations/ replays a nasty-.env corpus against both sides.
//
// The Python parser is a handful of anchored regexes over one string with
// backtracking; this port is a hand-written scanner producing the same
// bindings, because Go's RE2 has no backtracking and Python's \s is
// unicode-aware where Go's is ASCII (pysem.IsSpace carries the difference).
package dotenv

import (
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// Binding mirrors parser.Binding: one parsed statement. Key is nil for
// blank/comment/error statements; Value is nil for a bare `KEY` line.
// Original is the exact source slice the statement covers — set_key writes
// it back verbatim for every line it does not replace.
type Binding struct {
	Key      *string
	Value    *string
	Original string
	Err      bool
}

type reader struct {
	s    string
	pos  int
	mark int
}

func (r *reader) hasNext() bool  { return r.pos < len(r.s) }
func (r *reader) setMark()       { r.mark = r.pos }
func (r *reader) marked() string { return r.s[r.mark:r.pos] }
func (r *reader) peekByte() (byte, bool) {
	if r.pos >= len(r.s) {
		return 0, false
	}
	return r.s[r.pos], true
}

// isLineSpace mirrors the `[^\S\r\n]` class: unicode whitespace minus newlines.
func isLineSpace(r rune) bool { return pysem.IsSpace(r) && r != '\r' && r != '\n' }

// readMultilineWhitespace mirrors `\s*` (unicode, newlines included).
func (r *reader) readMultilineWhitespace() {
	for _, c := range r.s[r.pos:] {
		if !pysem.IsSpace(c) {
			break
		}
		r.pos += len(string(c))
	}
}

// readLineWhitespace mirrors `[^\S\r\n]*`.
func (r *reader) readLineWhitespace() {
	for _, c := range r.s[r.pos:] {
		if !isLineSpace(c) {
			break
		}
		r.pos += len(string(c))
	}
}

// readExport mirrors `(?:export[^\S\r\n]+)?`: consumed only when the literal
// is followed by at least one same-line whitespace rune, else matches empty
// (so a key named `exportFOO` parses as itself).
func (r *reader) readExport() {
	rest := r.s[r.pos:]
	if !strings.HasPrefix(rest, "export") {
		return
	}
	after := rest[len("export"):]
	first, size := firstRune(after)
	if size == 0 || !isLineSpace(first) {
		return
	}
	r.pos += len("export") + size
	r.readLineWhitespace()
}

func firstRune(s string) (rune, int) {
	for _, c := range s {
		return c, len(string(c))
	}
	return 0, 0
}

// readSingleQuotedKey mirrors `'([^']+)'`.
func (r *reader) readSingleQuotedKey() (string, bool) {
	rest := r.s[r.pos:]
	if len(rest) == 0 || rest[0] != '\'' {
		return "", false
	}
	end := strings.IndexByte(rest[1:], '\'')
	if end <= 0 { // -1: unterminated; 0: empty key — `+` needs one char
		return "", false
	}
	r.pos += 1 + end + 1
	return rest[1 : 1+end], true
}

// readUnquotedKey mirrors `([^=\#\s]+)` (unicode \s).
func (r *reader) readUnquotedKey() (string, bool) {
	start := r.pos
	for _, c := range r.s[r.pos:] {
		if c == '=' || c == '#' || pysem.IsSpace(c) {
			break
		}
		r.pos += len(string(c))
	}
	if r.pos == start {
		return "", false
	}
	return r.s[start:r.pos], true
}

// parseKey mirrors parse_key: nil for a comment line, error when neither
// key form matches.
func (r *reader) parseKey() (*string, error) {
	c, ok := r.peekByte()
	if ok && c == '#' {
		return nil, nil
	}
	if ok && c == '\'' {
		key, ok := r.readSingleQuotedKey()
		if !ok {
			return nil, parseErr()
		}
		return &key, nil
	}
	key, ok := r.readUnquotedKey()
	if !ok {
		return nil, parseErr()
	}
	return &key, nil
}

func parseErr() error { return errParse }

var errParse = &pysem.Error{Class: "dotenv.parser.Error", Msg: "read_regex: Pattern not found"}

// readQuotedValue mirrors `q((?:\\q|[^q])*)q` with Python's backtracking:
// the closer is the first unescaped q, else the last (escaped) q — whose
// backslash then stays in the content — else the statement fails.
func (r *reader) readQuotedValue(q byte) (string, bool) {
	rest := r.s[r.pos:]
	if len(rest) == 0 || rest[0] != q {
		return "", false
	}
	body := rest[1:]
	closer := -1
	for i := 0; i < len(body); i++ {
		if body[i] != q {
			continue
		}
		if i == 0 || body[i-1] != '\\' {
			closer = i // first unescaped quote wins outright
			break
		}
		closer = i // remember the last escaped quote as the fallback closer
	}
	if closer < 0 {
		return "", false
	}
	r.pos += 1 + closer + 1
	return body[:closer], true
}

// decodeSingleEscapes mirrors decode_escapes(_single_quote_escapes, ...):
// only `\\` and `\'` decode; every other backslash stays literal.
func decodeSingleEscapes(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) && (s[i+1] == '\\' || s[i+1] == '\'') {
			b.WriteByte(s[i+1])
			i++
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

// decodeDoubleEscapes mirrors decode_escapes(_double_quote_escapes, ...):
// the C-style set `\\ \' \" \a \b \f \n \r \t \v`.
func decodeDoubleEscapes(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			if decoded, ok := doubleEscape(s[i+1]); ok {
				b.WriteByte(decoded)
				i++
				continue
			}
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

func doubleEscape(c byte) (byte, bool) {
	switch c {
	case '\\', '\'', '"':
		return c, true
	case 'a':
		return '\a', true
	case 'b':
		return '\b', true
	case 'f':
		return '\f', true
	case 'n':
		return '\n', true
	case 'r':
		return '\r', true
	case 't':
		return '\t', true
	case 'v':
		return '\v', true
	}
	return 0, false
}

// readUnquotedValue mirrors parse_unquoted_value: the rest of the line, cut
// at the first whitespace run that precedes a '#', then rstripped.
func (r *reader) readUnquotedValue() string {
	start := r.pos
	for r.pos < len(r.s) && r.s[r.pos] != '\r' && r.s[r.pos] != '\n' {
		r.pos++
	}
	part := r.s[start:r.pos]
	if cut := commentCut(part); cut >= 0 {
		part = part[:cut]
	}
	return strings.TrimRightFunc(part, pysem.IsSpace)
}

// commentCut finds where re.sub(r"\s+#.*", "", part) cuts: the start of the
// whitespace run immediately before the first '#' that follows whitespace.
func commentCut(part string) int {
	runes := []rune(part)
	byteAt := 0
	byteOffsets := make([]int, len(runes)+1)
	for i, c := range runes {
		byteOffsets[i] = byteAt
		byteAt += len(string(c))
	}
	byteOffsets[len(runes)] = byteAt
	for i := 1; i < len(runes); i++ {
		if runes[i] == '#' && pysem.IsSpace(runes[i-1]) {
			j := i - 1
			for j > 0 && pysem.IsSpace(runes[j-1]) {
				j--
			}
			return byteOffsets[j]
		}
	}
	return -1
}

// parseValue mirrors parse_value.
func (r *reader) parseValue() (string, error) {
	c, ok := r.peekByte()
	if !ok || c == '\n' || c == '\r' {
		return "", nil
	}
	if c == '\'' {
		v, ok := r.readQuotedValue('\'')
		if !ok {
			return "", parseErr()
		}
		return decodeSingleEscapes(v), nil
	}
	if c == '"' {
		v, ok := r.readQuotedValue('"')
		if !ok {
			return "", parseErr()
		}
		return decodeDoubleEscapes(v), nil
	}
	return r.readUnquotedValue(), nil
}

// readComment mirrors `(?:[^\S\r\n]*#[^\r\n]*)?` — all-or-nothing: the
// leading whitespace is consumed only when a '#' follows it.
func (r *reader) readComment() {
	save := r.pos
	r.readLineWhitespace()
	if c, ok := r.peekByte(); ok && c == '#' {
		for r.pos < len(r.s) && r.s[r.pos] != '\r' && r.s[r.pos] != '\n' {
			r.pos++
		}
		return
	}
	r.pos = save
}

// readEndOfLine mirrors `[^\S\r\n]*(?:\r\n|\n|\r|$)`.
func (r *reader) readEndOfLine() error {
	r.readLineWhitespace()
	if !r.hasNext() {
		return nil
	}
	if strings.HasPrefix(r.s[r.pos:], "\r\n") {
		r.pos += 2
		return nil
	}
	if c := r.s[r.pos]; c == '\n' || c == '\r' {
		r.pos++
		return nil
	}
	return parseErr()
}

// readRestOfLine mirrors `[^\r\n]*(?:\r|\n|\r\n)?` — including the quirk
// that the `\r` alternative wins over `\r\n`, so a CRLF after a bad
// statement leaves the `\n` to the next (blank) binding.
func (r *reader) readRestOfLine() {
	for r.pos < len(r.s) && r.s[r.pos] != '\r' && r.s[r.pos] != '\n' {
		r.pos++
	}
	if r.pos < len(r.s) { // \r or \n — one byte, mirroring the alternation order
		r.pos++
	}
}

func (r *reader) parseBinding() Binding {
	r.setMark()
	b, err := r.tryParseBinding()
	if err != nil {
		r.readRestOfLine()
		return Binding{Original: r.marked(), Err: true}
	}
	b.Original = r.marked()
	return b
}

func (r *reader) tryParseBinding() (Binding, error) {
	r.readMultilineWhitespace()
	if !r.hasNext() {
		return Binding{}, nil
	}
	r.readExport()
	key, err := r.parseKey()
	if err != nil {
		return Binding{}, err
	}
	r.readLineWhitespace()
	var value *string
	if c, ok := r.peekByte(); ok && c == '=' {
		r.pos++
		r.readLineWhitespace()
		v, err := r.parseValue()
		if err != nil {
			return Binding{}, err
		}
		value = &v
	}
	r.readComment()
	if err := r.readEndOfLine(); err != nil {
		return Binding{}, err
	}
	return Binding{Key: key, Value: value}, nil
}

// Parse mirrors parse_stream over a whole source string.
func Parse(src string) []Binding {
	r := &reader{s: src}
	var out []Binding
	for r.hasNext() {
		out = append(out, r.parseBinding())
	}
	return out
}
