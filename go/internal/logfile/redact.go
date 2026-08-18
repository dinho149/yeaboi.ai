// Package logfile ports src/yeaboi/logging_setup.py and
// src/yeaboi/redaction.py — keep in lockstep; the Python modules are the
// reference implementation and tests/parity/foundations/ diffs the whole
// surface (vectors, formatter lines, and the registry/rotation scenario's
// on-disk outcome) per fixture.
//
// This file is the redaction.py twin. The one structural deviation from the
// Python module is documented where it lives: Python compiles every secret
// value and token shape into a single alternation regex, which RE2 cannot
// express (the URL-credential pattern needs lookarounds, and alternation
// order must decide ties by position). Redact therefore scans by hand —
// at each rune position it tries the same alternatives in the same order —
// which reproduces re.sub's leftmost-scan, first-alternative-wins
// semantics exactly. There is no compiled-regex cache to invalidate, so
// redaction.py's env-snapshot cache has no twin here.
package logfile

import (
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

// Redacted mirrors redaction.REDACTED.
const Redacted = "[REDACTED]"

// LogValueLimit mirrors redaction.LOG_VALUE_LIMIT.
const LogValueLimit = 200

// minSecretLen mirrors redaction._MIN_SECRET_LEN.
const minSecretLen = 8

// EnvLookup is the env accessor redaction reads secrets through — a func so
// golden tests can replay a fixture environment without mutating the
// process env (same shape as home.Env).
type EnvLookup func(key string) (string, bool)

// SecretEnvKeys mirrors redaction.SECRET_ENV_KEYS.
var SecretEnvKeys = []string{
	"ANTHROPIC_API_KEY",
	"OPENAI_API_KEY",
	"GOOGLE_API_KEY",
	"LANGSMITH_API_KEY",
	"GITHUB_TOKEN",
	"JIRA_API_TOKEN",
	"AZURE_DEVOPS_TOKEN",
	"CONFLUENCE_API_TOKEN",
	"NOTION_TOKEN",
	"STANDUP_SMTP_PASSWORD",
	"SLACK_WEBHOOK_URL",
	"AWS_ACCESS_KEY_ID",
	"AWS_SECRET_ACCESS_KEY",
	"AWS_SESSION_TOKEN",
}

// pySpaceCls is Python's str-mode \s as an RE2 character-class body:
// unicode.IsSpace's set (White_Space) plus the \x1c-\x1f file separators —
// exactly pysem.IsSpace, spelled for a regex.
const pySpaceCls = `\t\n\x0B\f\r\x1C-\x1F \x{85}\x{A0}\x{1680}\x{2000}-\x{200A}\x{2028}\x{2029}\x{202F}\x{205F}\x{3000}`

// pyWordCls is Python's str-mode \w as an RE2 character-class body:
// underscore plus everything str.isalnum() counts (letters and all three
// numeric categories) — exactly pysem.IsWordRune.
const pyWordCls = `\p{L}\p{N}_`

// tokenPatterns mirrors redaction._TOKEN_PATTERNS, each anchored so the
// scanner can try it at a single position. The final Python pattern (URL
// credentials) is not in this list — its lookarounds are handled by the
// scanner itself (urlCredRe below).
var tokenPatterns = []*regexp.Regexp{
	regexp.MustCompile(`^sk-ant-[` + pyWordCls + `-]{10,}`),
	regexp.MustCompile(`^sk-[A-Za-z0-9_-]{20,}`),
	regexp.MustCompile(`^gh[pousr]_[A-Za-z0-9]{20,}`),
	regexp.MustCompile(`^github_pat_[A-Za-z0-9_]{20,}`),
	regexp.MustCompile(`^xox[abprs]-[` + pyWordCls + `-]{10,}`),
	regexp.MustCompile(`^AIza[` + pyWordCls + `-]{35}`),
	regexp.MustCompile(`^AKIA[0-9A-Z]{16}`),
	regexp.MustCompile(`^ATATT[` + pyWordCls + `=+/-]{20,}`),
	regexp.MustCompile(`^ntn_[A-Za-z0-9]{20,}`),
	regexp.MustCompile(`^secret_[A-Za-z0-9]{30,}`),
	regexp.MustCompile(`^hooks\.slack\.com/services/[` + pyWordCls + `/]+`),
	regexp.MustCompile(`^(?i:bearer|basic)[` + pySpaceCls + `]+[A-Za-z0-9._~+/=-]{16,}`),
}

// urlCredRe is the URL-credential pattern with its lookarounds unrolled:
// the scanner checks the "(?<=://)" prefix itself, and the trailing "@"
// consumed here stands in for "(?=@)" — the scanner resumes scanning AT
// the "@", exactly where Python's zero-width lookahead left the cursor.
var urlCredRe = regexp.MustCompile(`^[^/:@` + pySpaceCls + `]+:[^/@` + pySpaceCls + `]{4,}@`)

// currentSecretValues mirrors redaction._current_secret_values: the env
// values worth matching, deduplicated, longest (in characters) first.
// Python breaks length ties by set-iteration order, which cannot affect
// the output (two distinct equal-length literals can never match at the
// same position); Go breaks them lexicographically for determinism.
func currentSecretValues(lookup EnvLookup) []string {
	seen := map[string]bool{}
	for _, key := range SecretEnvKeys {
		if v, ok := lookup(key); ok && v != "" && utf8.RuneCountInString(v) >= minSecretLen {
			seen[v] = true
		}
	}
	values := make([]string, 0, len(seen))
	for v := range seen {
		values = append(values, v)
	}
	sort.Slice(values, func(i, j int) bool {
		li, lj := utf8.RuneCountInString(values[i]), utf8.RuneCountInString(values[j])
		if li != lj {
			return li > lj
		}
		return values[i] < values[j]
	})
	return values
}

// matchAt tries every alternative at byte position i, in Python's
// alternation order: env values (longest first), then the token shapes,
// then URL credentials. It returns the end of the span to redact and the
// position scanning resumes from (they differ only for URL credentials,
// where the "@" is checked but not consumed).
func matchAt(values []string, text string, i int) (redactEnd, resume int, ok bool) {
	rest := text[i:]
	for _, v := range values {
		if strings.HasPrefix(rest, v) {
			return i + len(v), i + len(v), true
		}
	}
	for _, re := range tokenPatterns {
		if loc := re.FindStringIndex(rest); loc != nil {
			return i + loc[1], i + loc[1], true
		}
	}
	if i >= 3 && text[i-3:i] == "://" {
		if loc := urlCredRe.FindStringIndex(rest); loc != nil {
			return i + loc[1] - 1, i + loc[1] - 1, true // resume at the "@"
		}
	}
	return 0, 0, false
}

// Redact mirrors redaction.redact: replace every known secret value and
// token shape in text with [REDACTED]. Pure and idempotent.
func Redact(text string, lookup EnvLookup) string {
	values := currentSecretValues(lookup)
	var b strings.Builder
	i := 0
	for i < len(text) {
		if end, resume, ok := matchAt(values, text, i); ok {
			b.WriteString(Redacted)
			_ = end
			i = resume
			continue
		}
		_, size := utf8.DecodeRuneInString(text[i:])
		b.WriteString(text[i : i+size])
		i += size
	}
	return b.String()
}

// controlChar mirrors redaction.CONTROL_CHARS_RE: C0 controls minus
// \t\n\r, plus DEL.
func controlChar(r rune) bool {
	return (r <= 0x08) || r == 0x0b || r == 0x0c || (r >= 0x0e && r <= 0x1f) || r == 0x7f
}

// LogSafe mirrors redaction.log_safe with the default limit: collapse
// CR/LF/tab to spaces, strip the remaining control characters, and cap at
// LogValueLimit code points (never bytes) with a trailing ellipsis.
func LogSafe(value string) string {
	text := strings.NewReplacer("\r", " ", "\n", " ", "\t", " ").Replace(value)
	var b strings.Builder
	for _, r := range text {
		if !controlChar(r) {
			b.WriteRune(r)
		}
	}
	runes := []rune(b.String())
	if len(runes) > LogValueLimit {
		return string(runes[:LogValueLimit-1]) + "…"
	}
	return b.String()
}
