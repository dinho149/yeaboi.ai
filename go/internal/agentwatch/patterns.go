package agentwatch

// Security signal patterns, ported from src/yeaboi/agentwatch/collector.py
// and src/yeaboi/redaction.py (_TOKEN_PATTERNS). Labels only — matched text
// is never stored.
//
// Two translation rules keep line-level found/not-found decisions identical
// to Python:
//
//  1. Python's `\w` and `\s` are Unicode-aware; Go's are ASCII. Every `\w`
//     becomes [\p{L}\p{N}_] and every `\s` becomes the pySpace class below,
//     which spells out exactly the characters str.isspace() accepts.
//  2. RE2 has no lookarounds, so the `(?<=://)…(?=@)` URL-credential pattern
//     is rewritten as a consuming match `://user:pass@`. That preserves the
//     boolean per line: the lookarounds only trim what the match *covers*,
//     and the finding stores no matched text — only (label, file, line).
//     TestURLCredentialEquivalence pins the tricky cases.

import (
	"regexp"
	"strings"
)

// pySpace is the exact character set Python's `\s` matches for str patterns:
// [ \t\n\r\f\v], U+001C..U+001F, NEL, NBSP, and the Unicode space separators.
const pySpace = ` \t\n\x0b\x0c\r\x1c-\x1f\x{85}\x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}`

// pyWord is the class content matching Python's Unicode `\w`.
const pyWord = `\p{L}\p{N}_`

// labelHeadRe mirrors re.match(r"[\w./-]+", …) in _pattern_label.
var labelHeadRe = regexp.MustCompile(`^[` + pyWord + `./-]+`)

// PatternLabel derives a stable label from a token regex's literal head —
// a byte-for-byte port of collector._pattern_label. Labels are persisted in
// agent_security_findings and compared by the parity suite, so this must
// produce IDENTICAL strings to Python for every pattern.
func PatternLabel(pattern string) string {
	literal := labelHeadRe.FindString(strings.ReplaceAll(pattern, `\.`, "."))
	if literal == "" {
		runes := []rune(pattern)
		if len(runes) > 12 {
			runes = runes[:12]
		}
		literal = string(runes)
	}
	if literal == "" {
		return "secret-token"
	}
	return "secret-" + strings.ToLower(strings.TrimRight(literal, "-_"))
}

type secretPattern struct {
	label string
	re    *regexp.Regexp
	guard func(string) bool
}

type riskyPattern struct {
	label    string
	severity string
	re       *regexp.Regexp
}

// tokenPatterns pairs each Python pattern (the label source — redaction
// _TOKEN_PATTERNS verbatim) with its RE2 translation.
var tokenPatterns = []struct {
	py string
	re string
}{
	{`sk-ant-[\w-]{10,}`, `sk-ant-[` + pyWord + `-]{10,}`},
	{`sk-[A-Za-z0-9_-]{20,}`, `sk-[A-Za-z0-9_-]{20,}`},
	{`gh[pousr]_[A-Za-z0-9]{20,}`, `gh[pousr]_[A-Za-z0-9]{20,}`},
	{`github_pat_[A-Za-z0-9_]{20,}`, `github_pat_[A-Za-z0-9_]{20,}`},
	{`xox[abprs]-[\w-]{10,}`, `xox[abprs]-[` + pyWord + `-]{10,}`},
	{`AIza[\w-]{35}`, `AIza[` + pyWord + `-]{35}`},
	{`AKIA[0-9A-Z]{16}`, `AKIA[0-9A-Z]{16}`},
	{`ATATT[\w=+/-]{20,}`, `ATATT[` + pyWord + `=+/-]{20,}`},
	{`ntn_[A-Za-z0-9]{20,}`, `ntn_[A-Za-z0-9]{20,}`},
	{`secret_[A-Za-z0-9]{30,}`, `secret_[A-Za-z0-9]{30,}`},
	{`hooks\.slack\.com/services/[\w/]+`, `hooks\.slack\.com/services/[` + pyWord + `/]+`},
	{`(?i:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}`, `(?i:bearer|basic)[` + pySpace + `]+[A-Za-z0-9._~+/=-]{16,}`},
	// user:password inside a URL — the one lookaround pattern, rewritten as a
	// consuming match (see the package comment).
	{`(?<=://)[^/\s:@]+:[^/\s@]{4,}(?=@)`, `://[^/` + pySpace + `:@]+:[^/` + pySpace + `@]{4,}@`},
}

// Cheap substring pre-checks for the two patterns with no literal prefix,
// keyed on the EXACT Python pattern text (collector._PATTERN_GUARDS). Each
// guard is logically implied by its regex, so it can only cost speed.
var patternGuards = map[string]func(string) bool{
	`(?i:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}`: guardBearer,
	`(?<=://)[^/\s:@]+:[^/\s@]{4,}(?=@)`:          guardURLCredentials,
}

func guardBearer(line string) bool {
	lowered := strings.ToLower(line)
	return strings.Contains(lowered, "bearer") || strings.Contains(lowered, "basic")
}

func guardURLCredentials(line string) bool {
	return strings.Contains(line, "://") && strings.Contains(line, "@")
}

var secretPatterns = func() []secretPattern {
	out := make([]secretPattern, 0, len(tokenPatterns))
	for _, tp := range tokenPatterns {
		out = append(out, secretPattern{
			label: PatternLabel(tp.py),
			re:    regexp.MustCompile(tp.re),
			guard: patternGuards[tp.py],
		})
	}
	return out
}()

// riskyBashPatterns mirrors collector._RISKY_BASH_PATTERNS. All were already
// RE2-safe; only `\s` is widened to the Python whitespace class. The Python
// `(?:\s|$)` tail of rm-rf-root is equivalent here: `$` without re.M also
// matches just before a trailing newline in Python, but that position is
// already covered by `\s` matching the newline itself.
var riskyBashPatterns = []riskyPattern{
	{"curl-pipe-shell", "high", regexp.MustCompile(`\b(?:curl|wget)\b[^|;&]*\|[` + pySpace + `]*(?:sudo[` + pySpace + `]+)?(?:ba|z|da)?sh\b`)},
	{"base64-decode-pipe-shell", "high", regexp.MustCompile(`base64[` + pySpace + `]+(?:-d|--decode)[^|;&]*\|[` + pySpace + `]*(?:ba|z|da)?sh\b`)},
	{"rm-rf-root", "high", regexp.MustCompile(`\brm[` + pySpace + `]+-[a-z]*rf?[a-z]*[` + pySpace + `]+/(?:[` + pySpace + `]|$)`)},
	{"permission-bypass-flag", "high", regexp.MustCompile(`--dangerously-skip-permissions\b`)},
	{"sudo", "medium", regexp.MustCompile(`(?:^|[;&|][` + pySpace + `]*)sudo[` + pySpace + `]`)},
}
