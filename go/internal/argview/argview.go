// Package argview hand-renders argparse help and usage screens for the
// yeaboi CLI parse tree, byte-matching CPython 3.11's HelpFormatter — no
// cobra, no flag package, because nothing else reproduces argparse's bytes.
//
// Python twin: Lib/argparse.py's rendering half (HelpFormatter,
// RawDescriptionHelpFormatter, ArgumentParser.format_help/format_usage), as
// exercised by src/yeaboi/cli.py build_parser(). The committed goldens under
// tests/parity/goldens/cli/help/ (captured at COLUMNS=80) pin every screen;
// go/cmd/yeaboi's golden test replays them against this package.
//
// Two scope notes, both deliberate:
//   - Only the feature set build_parser() uses is rendered: the two default
//     argument groups, subparser choice listings, and mutually exclusive
//     groups of one optional action (which argparse renders identically to
//     a plain optional, so group bracket insertion is not implemented).
//   - Terminal width comes from the COLUMNS environment variable, falling
//     back to 80. Python additionally queries the terminal via ioctl when
//     COLUMNS is unset; the Go binary assumes the fallback — a documented
//     deviation (rpc.md precedent), invisible under the pinned goldens.
package argview

import (
	"fmt"
	"os"
	"regexp"
	"strings"

	ap "github.com/yeaboi-ai/yeaboi/go/internal/argparse"
	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

const maxHelpPosition = 24

// Width mirrors HelpFormatter's default width:
// shutil.get_terminal_size().columns - 2, with COLUMNS the override.
func Width() int {
	if raw, ok := os.LookupEnv("COLUMNS"); ok {
		if cols, err := pysem.ParseInt(raw); err == nil && cols > 0 {
			return int(cols) - 2
		}
	}
	return 80 - 2
}

// FormatHelp mirrors ArgumentParser.format_help().
func FormatHelp(p *ap.Parser) string {
	f := &formatter{p: p, width: Width()}
	parts := []string{f.formatUsageBlock()}
	parts = append(parts, f.formatText(p.Description))
	parts = append(parts, f.formatSection("positional arguments:", f.positionals()))
	parts = append(parts, f.formatSection("options:", f.optionals()))
	parts = append(parts, f.formatText(p.Epilog))
	return finish(strings.Join(parts, ""))
}

// FormatUsage mirrors ArgumentParser.format_usage() — what error() prints
// above "prog: error: ...".
func FormatUsage(p *ap.Parser) string {
	f := &formatter{p: p, width: Width()}
	return finish(f.formatUsageBlock())
}

// finish mirrors HelpFormatter.format_help's closing pass: long breaks
// collapse to one blank line, and the text ends with exactly one newline.
var longBreak = regexp.MustCompile(`\n\n\n+`)

func finish(text string) string {
	text = longBreak.ReplaceAllString(text, "\n\n")
	text = strings.Trim(text, "\n")
	if text == "" {
		return ""
	}
	return text + "\n"
}

type formatter struct {
	p     *ap.Parser
	width int
}

func (f *formatter) positionals() []*ap.Action {
	var out []*ap.Action
	for _, a := range f.p.Actions {
		if len(a.OptionStrings) == 0 {
			out = append(out, a)
		}
	}
	return out
}

func (f *formatter) optionals() []*ap.Action {
	var out []*ap.Action
	for _, a := range f.p.Actions {
		if len(a.OptionStrings) > 0 {
			out = append(out, a)
		}
	}
	return out
}

// ── metavars and args strings ────────────────────────────────────────────

// choiceStr is str(choice) over the value shapes the tree holds.
func choiceStr(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case int64:
		return fmt.Sprintf("%d", t)
	case float64:
		return pysem.FloatRepr(t)
	}
	return fmt.Sprintf("%v", v)
}

// metavarFor mirrors _metavar_formatter(action, default)(1): explicit
// metavar, else {choices}, else the default (dest, upper-cased by the
// optionals path).
func metavarFor(a *ap.Action, dflt string) string {
	if a.Metavar != "" {
		return a.Metavar
	}
	if a.Kind == ap.KindSubParsers {
		return "{" + strings.Join(a.Sub.Names, ",") + "}"
	}
	if a.Choices != nil {
		strs := make([]string, len(a.Choices))
		for i, c := range a.Choices {
			strs[i] = choiceStr(c)
		}
		return "{" + strings.Join(strs, ",") + "}"
	}
	return dflt
}

func defaultMetavarOptional(a *ap.Action) string   { return strings.ToUpper(a.Dest) }
func defaultMetavarPositional(a *ap.Action) string { return a.Dest }

// formatArgs mirrors _format_args for the nargs values the tree uses.
func formatArgs(a *ap.Action, dflt string) string {
	metavar := metavarFor(a, dflt)
	if a.Kind == ap.KindSubParsers {
		return metavar + " ..."
	}
	switch a.Nargs {
	case "?":
		return "[" + metavar + "]"
	case "*":
		return "[" + metavar + " ...]"
	case "+":
		return metavar + " [" + metavar + " ...]"
	default:
		return metavar
	}
}

// ── usage ────────────────────────────────────────────────────────────────

// usagePart is one action's usage fragment (_format_actions_usage, minus
// the group-insert machinery — see the package comment).
func usagePart(a *ap.Action) string {
	if len(a.OptionStrings) == 0 {
		return formatArgs(a, defaultMetavarPositional(a))
	}
	var part string
	if a.NargsZero() {
		part = a.OptionStrings[0]
	} else {
		part = a.OptionStrings[0] + " " + formatArgs(a, defaultMetavarOptional(a))
	}
	if !a.Required {
		part = "[" + part + "]"
	}
	return part
}

func joinParts(actions []*ap.Action) string {
	parts := make([]string, len(actions))
	for i, a := range actions {
		parts[i] = usagePart(a)
	}
	return strings.Join(parts, " ")
}

// splitUsageParts mirrors the part_regexp findall:
// `\(.*?\)+(?=\s|$)|\[.*?\]+(?=\s|$)|\S+` — a bracketed group runs to the
// first ]-run followed by space/end; anything else is a plain token.
func splitUsageParts(text string) []string {
	runes := []rune(text)
	var parts []string
	i := 0
	for i < len(runes) {
		if runes[i] == ' ' {
			i++
			continue
		}
		start := i
		if runes[i] == '[' || runes[i] == '(' {
			closer := ']'
			if runes[i] == '(' {
				closer = ')'
			}
			end := -1
			for j := i + 1; j < len(runes); j++ {
				if runes[j] != closer {
					continue
				}
				k := j
				for k+1 < len(runes) && runes[k+1] == closer {
					k++
				}
				if k+1 == len(runes) || runes[k+1] == ' ' {
					end = k + 1
					break
				}
				j = k
			}
			if end > 0 {
				parts = append(parts, string(runes[start:end]))
				i = end
				continue
			}
		}
		for i < len(runes) && runes[i] != ' ' {
			i++
		}
		parts = append(parts, string(runes[start:i]))
	}
	return parts
}

// formatUsageBlock mirrors _format_usage(...) + the trailing "\n\n" the
// root section sees.
func (f *formatter) formatUsageBlock() string {
	const prefix = "usage: "
	prog := f.p.Prog
	optionals := f.optionals()
	positionals := f.positionals()

	usage := strings.TrimSpace(strings.Join(nonEmpty([]string{prog, joinParts(optionals), joinParts(positionals)}), " "))
	textWidth := f.width
	if len([]rune(prefix))+len([]rune(usage)) > textWidth {
		optParts := splitUsageParts(joinParts(optionals))
		posParts := splitUsageParts(joinParts(positionals))
		var lines []string
		if float64(len(prefix)+len([]rune(prog))) <= 0.75*float64(textWidth) {
			indent := strings.Repeat(" ", len(prefix)+len([]rune(prog))+1)
			if len(optParts) > 0 {
				lines = getLines(append([]string{prog}, optParts...), indent, prefix, textWidth)
				lines = append(lines, getLines(posParts, indent, "", textWidth)...)
			} else if len(posParts) > 0 {
				lines = getLines(append([]string{prog}, posParts...), indent, prefix, textWidth)
			} else {
				lines = []string{prog}
			}
		} else {
			indent := strings.Repeat(" ", len(prefix))
			merged := append(append([]string{}, optParts...), posParts...)
			lines = getLines(merged, indent, "", textWidth)
			if len(lines) > 1 {
				lines = getLines(optParts, indent, "", textWidth)
				lines = append(lines, getLines(posParts, indent, "", textWidth)...)
			}
			lines = append([]string{prog}, lines...)
		}
		usage = strings.Join(lines, "\n")
	}
	return prefix + usage + "\n\n"
}

func nonEmpty(items []string) []string {
	var out []string
	for _, s := range items {
		if s != "" {
			out = append(out, s)
		}
	}
	return out
}

// getLines mirrors _format_usage's inner get_lines: a greedy fill of parts
// at text_width, the first line optionally hung off the prefix.
func getLines(parts []string, indent, prefix string, textWidth int) []string {
	var lines []string
	var line []string
	lineLen := len([]rune(indent)) - 1
	if prefix != "" {
		lineLen = len([]rune(prefix)) - 1
	}
	for _, part := range parts {
		if lineLen+1+len([]rune(part)) > textWidth && len(line) > 0 {
			lines = append(lines, indent+strings.Join(line, " "))
			line = nil
			lineLen = len([]rune(indent)) - 1
		}
		line = append(line, part)
		lineLen += 1 + len([]rune(part))
	}
	if len(line) > 0 {
		lines = append(lines, indent+strings.Join(line, " "))
	}
	if prefix != "" && len(lines) > 0 {
		lines[0] = lines[0][len(indent):]
	}
	return lines
}

// ── text blocks (description / epilog) ───────────────────────────────────

// whitespaceMatcher mirrors argparse._whitespace_matcher: ASCII \s only.
var whitespaceMatcher = regexp.MustCompile(`[ \t\n\r\f\v]+`)

func collapse(text string) string {
	return strings.TrimSpace(whitespaceMatcher.ReplaceAllString(text, " "))
}

// formatText mirrors _format_text + the trailing "\n\n": raw formatters
// pass the text through, the default fills it to the width.
func (f *formatter) formatText(text string) string {
	if text == "" {
		return ""
	}
	textWidth := f.width
	if textWidth < 11 {
		textWidth = 11
	}
	if f.p.Raw {
		return text + "\n\n"
	}
	return strings.Join(wrapCollapsed(collapse(text), textWidth), "\n") + "\n\n"
}

// ── the argument sections ────────────────────────────────────────────────

// invocation mirrors _format_action_invocation.
func invocation(a *ap.Action) string {
	if len(a.OptionStrings) == 0 {
		return metavarFor(a, defaultMetavarPositional(a))
	}
	if a.NargsZero() {
		return strings.Join(a.OptionStrings, ", ")
	}
	args := formatArgs(a, defaultMetavarOptional(a))
	parts := make([]string, len(a.OptionStrings))
	for i, s := range a.OptionStrings {
		parts[i] = s + " " + args
	}
	return strings.Join(parts, ", ")
}

// actionMaxLength mirrors add_argument's running maximum: every action's
// invocation (and its subactions') measured at the section indent.
func (f *formatter) actionMaxLength() int {
	maxLen := 0
	for _, a := range f.p.Actions {
		lengths := []string{invocation(a)}
		if a.Kind == ap.KindSubParsers {
			for _, name := range a.Sub.Names {
				if _, ok := a.Sub.Helps[name]; ok {
					lengths = append(lengths, name)
				}
			}
		}
		for _, inv := range lengths {
			if l := len([]rune(inv)) + 2; l > maxLen {
				maxLen = l
			}
		}
	}
	return maxLen
}

// formatSection mirrors _Section.format_help for one default group:
// "\n" + heading + the formatted actions + "\n" (empty when no actions).
func (f *formatter) formatSection(heading string, actions []*ap.Action) string {
	if len(actions) == 0 {
		return ""
	}
	helpPosition := f.actionMaxLength() + 2
	if helpPosition > maxHelpPosition {
		helpPosition = maxHelpPosition
	}
	var b strings.Builder
	for _, a := range actions {
		b.WriteString(f.formatAction(a, 2, helpPosition))
	}
	return "\n" + heading + "\n" + b.String() + "\n"
}

// formatAction mirrors _format_action at the given indent.
func (f *formatter) formatAction(a *ap.Action, indent, helpPosition int) string {
	helpWidth := f.width - helpPosition
	if helpWidth < 11 {
		helpWidth = 11
	}
	actionWidth := helpPosition - indent - 2
	inv := invocation(a)

	var b strings.Builder
	indentFirst := 0
	switch {
	case a.Help == "":
		b.WriteString(strings.Repeat(" ", indent) + inv + "\n")
	case len([]rune(inv)) <= actionWidth:
		b.WriteString(strings.Repeat(" ", indent) + inv + strings.Repeat(" ", actionWidth-len([]rune(inv))) + "  ")
	default:
		b.WriteString(strings.Repeat(" ", indent) + inv + "\n")
		indentFirst = helpPosition
	}
	if strings.TrimSpace(a.Help) != "" {
		lines := wrapCollapsed(collapse(a.Help), helpWidth)
		if len(lines) > 0 {
			b.WriteString(strings.Repeat(" ", indentFirst) + lines[0] + "\n")
			for _, line := range lines[1:] {
				b.WriteString(strings.Repeat(" ", helpPosition) + line + "\n")
			}
		}
	}
	if a.Kind == ap.KindSubParsers {
		for _, name := range a.Sub.Names {
			help, ok := a.Sub.Helps[name]
			if !ok {
				continue
			}
			b.WriteString(f.formatAction(&ap.Action{Dest: name, Help: help}, indent+2, helpPosition))
		}
	}
	return b.String()
}
