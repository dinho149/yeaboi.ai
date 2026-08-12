package standup

// boundary.go — Python-re boundary semantics on top of RE2.
//
// Python's \b and \w are UNICODE (a letter like é is a word character); RE2's
// are ASCII. Every ported pattern keeps its \b in the RE2 source — for
// patterns whose \b-adjacent characters are themselves word characters (all
// of ours), the ASCII matches are a superset of Python's — and each match is
// then post-filtered with wordBoundaryAt, which applies the unicode rule.
// Lookbehinds, which RE2 cannot express at all, are emulated by checking
// prevRune at the match start (contracts/v1/rpc.md, standup semantics).

import (
	"unicode/utf8"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// wordBoundaryAt reports whether Python's unicode \b would match between
// byte positions i-1 and i of s (i in 0..len(s)).
func wordBoundaryAt(s string, i int) bool {
	var before, after bool
	if i > 0 {
		r, _ := utf8.DecodeLastRuneInString(s[:i])
		before = pysem.IsWordRune(r)
	}
	if i < len(s) {
		r, _ := utf8.DecodeRuneInString(s[i:])
		after = pysem.IsWordRune(r)
	}
	return before != after
}

// prevRune returns the rune ending at byte position i of s, or utf8.RuneError
// with ok=false at the start of the string. Used to emulate lookbehinds.
func prevRune(s string, i int) (rune, bool) {
	if i <= 0 {
		return utf8.RuneError, false
	}
	r, _ := utf8.DecodeLastRuneInString(s[:i])
	return r, true
}
