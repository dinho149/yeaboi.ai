// Package argparse is a behavioural port of CPython 3.11's argparse for the
// exact feature set src/yeaboi/cli.py uses: store/store_true/store_false/
// append/help/version actions, nargs None/"?"/"*"/"+", const, type=int/float,
// choices, required, prefix abbreviation (allow_abbrev), mutually exclusive
// groups, "--opt=value", "--" separators, and (nested) subparsers.
//
// Python twin: Lib/argparse.py's parsing half (_parse_known_args and the
// helpers it calls), as exercised by src/yeaboi/cli.py's build_parser().
// Help/usage RENDERING is deliberately absent — that is W8 phase 4's
// internal/argview. What this package pins now is every observable parse
// outcome: the resulting namespace, or the erroring parser's prog and its
// error message (the line argparse prints after "prog: error: ").
// tests/parity/goldens/cli/args.json replays the committed Python outcomes
// against this engine, so a semantic drift here fails `make go-test`.
package argparse

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// Kind is the action class — the subset of argparse's action registry that
// cli.py uses.
type Kind int

const (
	// KindStore is argparse's default "store" action.
	KindStore Kind = iota
	// KindStoreTrue / KindStoreFalse are the flag actions (nargs=0).
	KindStoreTrue
	KindStoreFalse
	// KindAppend is action="append".
	KindAppend
	// KindHelp / KindVersion exit instead of storing (dest SUPPRESS-ed).
	KindHelp
	KindVersion
	// KindSubParsers is the PARSER-nargs positional add_subparsers() adds.
	KindSubParsers
)

// ValType is the argparse type= converter. Python passes callables; the tree
// only ever passes str (the identity), int and float.
type ValType int

const (
	TypeStr ValType = iota
	TypeInt
	TypeFloat
)

// Action mirrors argparse.Action for the fields that shape parse behaviour.
type Action struct {
	OptionStrings []string // empty ⇒ positional
	Dest          string   // "" ⇒ SUPPRESS (help/version)
	Kind          Kind
	// Nargs: "" = None (one argument), "?", "*", "+". Flag/const actions
	// (store_true/false, help, version) consume zero regardless.
	Nargs    string
	Const    any
	Default  any
	Type     ValType
	Choices  []any // nil ⇒ unrestricted
	Required bool
	Metavar  string
	Version  string      // KindVersion only ("%(prog)s" substituted)
	Sub      *SubParsers // KindSubParsers only
}

// name mirrors argparse._get_action_name.
func (a *Action) name() string {
	if len(a.OptionStrings) > 0 {
		return strings.Join(a.OptionStrings, "/")
	}
	if a.Metavar != "" {
		return a.Metavar
	}
	return a.Dest
}

// nargsZero reports whether the action consumes no arguments.
func (a *Action) nargsZero() bool {
	switch a.Kind {
	case KindStoreTrue, KindStoreFalse, KindHelp, KindVersion:
		return true
	}
	return false
}

// SubParsers is the name → parser map behind a KindSubParsers action, in
// add_parser order (the order "invalid choice" lists them in).
type SubParsers struct {
	Names   []string
	Parsers map[string]*Parser
}

// AddParser mirrors subparsers.add_parser(name): child prog is
// "<parent prog> <name>" and every parser grows -h/--help first.
func (s *SubParsers) AddParser(parent *Parser, name string) *Parser {
	p := NewParser(parent.Prog + " " + name)
	s.Names = append(s.Names, name)
	s.Parsers[name] = p
	return p
}

// Parser mirrors argparse.ArgumentParser (parsing only).
type Parser struct {
	Prog    string
	Actions []*Action

	optionStrings []string           // registration order — abbreviation listing order
	optionActions map[string]*Action // option string → action
	// mutually exclusive conflicts: action → the actions it excludes.
	conflicts map[*Action][]*Action
}

// NewParser mirrors ArgumentParser(prog=...) with add_help=True.
func NewParser(prog string) *Parser {
	p := &Parser{
		Prog:          prog,
		optionActions: map[string]*Action{},
		conflicts:     map[*Action][]*Action{},
	}
	p.Add(&Action{OptionStrings: []string{"-h", "--help"}, Kind: KindHelp})
	return p
}

// Add registers an action (argparse add_argument, already resolved: the
// caller supplies dest explicitly for optionals; positionals use Dest).
func (p *Parser) Add(a *Action) *Action {
	p.Actions = append(p.Actions, a)
	for _, s := range a.OptionStrings {
		p.optionStrings = append(p.optionStrings, s)
		p.optionActions[s] = a
	}
	return a
}

// AddSubparsers mirrors parser.add_subparsers(dest=..., metavar=...,
// required=...).
func (p *Parser) AddSubparsers(dest, metavar string, required bool) *Action {
	a := &Action{
		Dest:     dest,
		Kind:     KindSubParsers,
		Metavar:  metavar,
		Required: required,
		Sub:      &SubParsers{Parsers: map[string]*Parser{}},
	}
	return p.Add(a)
}

// MutuallyExclusive records a mutually exclusive group over actions already
// added (argparse checks membership pairwise at parse time).
func (p *Parser) MutuallyExclusive(actions ...*Action) {
	for _, a := range actions {
		for _, b := range actions {
			if a != b {
				p.conflicts[a] = append(p.conflicts[a], b)
			}
		}
	}
}

// ResultKind is what a parse ended as.
type ResultKind int

const (
	// ResultOk: a namespace (parse_args returned).
	ResultOk ResultKind = iota
	// ResultHelp / ResultVersion: the action fired; argparse exits 0.
	ResultHelp
	ResultVersion
	// ResultError: some parser called error(); argparse exits 2 printing
	// "<prog>: error: <message>".
	ResultError
)

// Result is the outcome of ParseArgs.
type Result struct {
	Kind    ResultKind
	Ns      map[string]any
	Prog    string // the parser that ended the parse (help/version/error)
	Message string // ResultError only — the text after "error: "
}

// parseError carries a parser.error() call: the erroring parser's prog and
// message. argumentError builds the "argument %s: %s" form ArgumentError
// stringifies to.
type parseError struct {
	prog string
	msg  string
}

func argumentError(prog string, a *Action, format string, args ...any) *parseError {
	msg := fmt.Sprintf(format, args...)
	if name := a.name(); name != "" {
		msg = fmt.Sprintf("argument %s: %s", name, msg)
	}
	return &parseError{prog: prog, msg: msg}
}

// special is a help/version action firing mid-parse.
type special struct {
	kind ResultKind
	prog string
}

// ParseArgs mirrors parser.parse_args(argv).
func (p *Parser) ParseArgs(argv []string) Result {
	ns := map[string]any{}
	extras, sp, err := p.parseKnownArgs(argv, ns)
	if err != nil {
		return Result{Kind: ResultError, Prog: err.prog, Message: err.msg}
	}
	if sp != nil {
		return Result{Kind: sp.kind, Prog: sp.prog}
	}
	if len(extras) > 0 {
		return Result{
			Kind:    ResultError,
			Prog:    p.Prog,
			Message: "unrecognized arguments: " + strings.Join(extras, " "),
		}
	}
	return Result{Kind: ResultOk, Ns: ns}
}

// optionTuple mirrors the (action, option_string, explicit_arg) triple;
// action nil means "looks like an option but matches nothing" (extras).
// ambiguous carries the abbreviation-collision message — argparse errors on
// it eagerly, during classification, before consuming anything.
type optionTuple struct {
	action      *Action
	optionStr   string
	explicitArg *string
	ambiguous   *string
}

var negativeNumber = regexp.MustCompile(`^-\d+$|^-\d*\.\d+$`)

// parseOptional mirrors _parse_optional.
func (p *Parser) parseOptional(arg string) *optionTuple {
	if arg == "" || !strings.HasPrefix(arg, "-") {
		return nil
	}
	if a, ok := p.optionActions[arg]; ok {
		return &optionTuple{action: a, optionStr: arg}
	}
	if len(arg) == 1 {
		return nil
	}
	if i := strings.IndexByte(arg, '='); i >= 0 {
		opt, expl := arg[:i], arg[i+1:]
		if a, ok := p.optionActions[opt]; ok {
			return &optionTuple{action: a, optionStr: opt, explicitArg: &expl}
		}
	}
	tuples, ambiguous := p.optionTuples(arg)
	if ambiguous != nil {
		return &optionTuple{action: nil, optionStr: arg, ambiguous: ambiguous}
	}
	if len(tuples) == 1 {
		return &tuples[0]
	}
	// No options look like negative numbers anywhere in the tree, so a
	// negative number is always a positional.
	if negativeNumber.MatchString(arg) {
		return nil
	}
	if strings.Contains(arg, " ") {
		return nil
	}
	return &optionTuple{action: nil, optionStr: arg}
}

// optionTuples mirrors _get_option_tuples (prefix abbreviation). The second
// return is non-nil when the prefix is ambiguous: it carries the joined
// match list for the error message.
func (p *Parser) optionTuples(arg string) ([]optionTuple, *string) {
	var result []optionTuple
	if strings.HasPrefix(arg, "--") {
		prefix := arg
		var expl *string
		if i := strings.IndexByte(arg, '='); i >= 0 {
			pre, e := arg[:i], arg[i+1:]
			prefix, expl = pre, &e
		}
		for _, opt := range p.optionStrings {
			if strings.HasPrefix(opt, prefix) {
				result = append(result, optionTuple{action: p.optionActions[opt], optionStr: opt, explicitArg: expl})
			}
		}
	} else {
		// single-dash: "-xy" can be "-x y" (explicit arg) or a prefix.
		prefix := arg
		short := arg[:2]
		shortExpl := arg[2:]
		for _, opt := range p.optionStrings {
			if opt == short {
				var e *string
				if shortExpl != "" {
					e = &shortExpl
				}
				result = append(result, optionTuple{action: p.optionActions[opt], optionStr: opt, explicitArg: e})
			} else if strings.HasPrefix(opt, prefix) {
				result = append(result, optionTuple{action: p.optionActions[opt], optionStr: opt})
			}
		}
	}
	if len(result) > 1 {
		names := make([]string, len(result))
		for i, t := range result {
			names[i] = t.optionStr
		}
		msg := fmt.Sprintf("ambiguous option: %s could match %s", arg, strings.Join(names, ", "))
		return nil, &msg
	}
	return result, nil
}

// nargsPattern mirrors _get_nargs_pattern + the optional-action '-' strip.
// Pattern strings here only ever hold 'A' and 'O', so the '-*' fragments
// argparse includes for positionals are dropped throughout.
func nargsPattern(a *Action) string {
	if a.nargsZero() {
		return "()"
	}
	if a.Kind == KindSubParsers {
		return "(A[AO]*)"
	}
	switch a.Nargs {
	case "?":
		return "(A?)"
	case "*":
		return "(A*)"
	case "+":
		return "(AA*)"
	default:
		return "(A)"
	}
}

// matchArgument mirrors _match_argument for one action against the pattern
// remainder.
func (p *Parser) matchArgument(a *Action, pattern string) (int, *parseError) {
	re := regexp.MustCompile(`^` + nargsPattern(a))
	m := re.FindStringSubmatch(pattern)
	if m == nil {
		var msg string
		switch a.Nargs {
		case "+":
			msg = "expected at least one argument"
		default:
			msg = "expected one argument"
		}
		return 0, argumentError(p.Prog, a, "%s", msg)
	}
	return len(m[1]), nil
}

// matchArgumentsPartial mirrors _match_arguments_partial: the longest prefix
// of the positionals list whose concatenated patterns match the start of the
// remaining pattern.
func matchArgumentsPartial(actions []*Action, pattern string) []int {
	for i := len(actions); i > 0; i-- {
		joined := "^"
		for _, a := range actions[:i] {
			joined += nargsPattern(a)
		}
		re := regexp.MustCompile(joined)
		if m := re.FindStringSubmatch(pattern); m != nil {
			counts := make([]int, i)
			for j := 1; j <= i; j++ {
				counts[j-1] = len(m[j])
			}
			return counts
		}
	}
	return nil
}

// parseKnownArgs mirrors _parse_known_args + the parse_known_args wrapper's
// ArgumentError → error() translation, returning (extras, help/version,
// error).
func (p *Parser) parseKnownArgs(args []string, ns map[string]any) ([]string, *special, *parseError) {
	// defaults first, exactly like parse_known_args does.
	for _, a := range p.Actions {
		if a.Dest != "" {
			if _, ok := ns[a.Dest]; !ok {
				ns[a.Dest] = a.Default
			}
		}
	}

	// classify tokens: 'O' for option-ish, 'A' for everything else;
	// everything after a bare "--" is 'A'.
	pattern := make([]byte, len(args))
	tuples := map[int]*optionTuple{}
	sawDoubleDash := false
	for i, arg := range args {
		if sawDoubleDash {
			pattern[i] = 'A'
			continue
		}
		if arg == "--" {
			sawDoubleDash = true
			pattern[i] = 'A'
			continue
		}
		if t := p.parseOptional(arg); t != nil {
			if t.ambiguous != nil {
				// argparse calls error() from _parse_optional, i.e. while
				// classifying — before consuming a single token.
				return nil, nil, &parseError{prog: p.Prog, msg: *t.ambiguous}
			}
			tuples[i] = t
			pattern[i] = 'O'
		} else {
			pattern[i] = 'A'
		}
	}
	patternStr := string(pattern)

	var extras []string
	seen := map[*Action]bool{}
	seenNonDefault := map[*Action]bool{}
	var positionals []*Action
	for _, a := range p.Actions {
		if len(a.OptionStrings) == 0 {
			positionals = append(positionals, a)
		}
	}

	var sp *special

	takeAction := func(a *Action, argStrings []string, optionString string) *parseError {
		seen[a] = true
		values, isDefault, err := p.getValues(a, argStrings)
		if err != nil {
			return err
		}
		if !isDefault {
			seenNonDefault[a] = true
			for _, conflict := range p.conflicts[a] {
				if seenNonDefault[conflict] {
					return argumentError(p.Prog, a, "not allowed with argument %s", conflict.name())
				}
			}
		}
		return p.callAction(a, values, ns, &extras, &sp)
	}

	consumePositionals := func(startIndex int) (int, *parseError) {
		counts := matchArgumentsPartial(positionals, patternStr[startIndex:])
		for i, count := range counts {
			a := positionals[i]
			strs := append([]string{}, args[startIndex:startIndex+count]...)
			startIndex += count
			if err := takeAction(a, strs, ""); err != nil {
				return startIndex, err
			}
		}
		positionals = positionals[len(counts):]
		return startIndex, nil
	}

	consumeOptional := func(startIndex int) (int, *parseError) {
		t := tuples[startIndex]
		action, optionString, explicitArg := t.action, t.optionStr, t.explicitArg
		type actionTuple struct {
			action *Action
			args   []string
			opt    string
		}
		var actionTuples []actionTuple
		stop := startIndex + 1
		for {
			if action == nil {
				extras = append(extras, optionString)
				return startIndex + 1, nil
			}
			if explicitArg != nil {
				count, err := p.matchArgument(action, "A")
				if err != nil {
					return 0, err
				}
				switch {
				case count == 1:
					stop = startIndex + 1
					actionTuples = append(actionTuples, actionTuple{action, []string{*explicitArg}, optionString})
				case count == 0 && !strings.HasPrefix(optionString, "--") && *explicitArg != "":
					// combined short flags: -hx → -h + -x…
					actionTuples = append(actionTuples, actionTuple{action, nil, optionString})
					next := optionString[:1] + (*explicitArg)[:1]
					rest := (*explicitArg)[1:]
					if a, ok := p.optionActions[next]; ok {
						action, optionString = a, next
						if rest == "" {
							explicitArg = nil
						} else {
							explicitArg = &rest
						}
						continue
					}
					return 0, argumentError(p.Prog, action, "ignored explicit argument %s", pysem.ReprStr(*explicitArg))
				default:
					return 0, argumentError(p.Prog, action, "ignored explicit argument %s", pysem.ReprStr(*explicitArg))
				}
			} else {
				start := startIndex + 1
				count, err := p.matchArgument(action, patternStr[start:])
				if err != nil {
					return 0, err
				}
				stop = start + count
				actionTuples = append(actionTuples, actionTuple{action, append([]string{}, args[start:stop]...), optionString})
			}
			break
		}
		for _, at := range actionTuples {
			if err := takeAction(at.action, at.args, at.opt); err != nil {
				return 0, err
			}
		}
		return stop, nil
	}

	// the main consumption loop, straight from _parse_known_args.
	maxOptionIndex := -1
	for i := range tuples {
		if i > maxOptionIndex {
			maxOptionIndex = i
		}
	}
	startIndex := 0
	for startIndex <= maxOptionIndex {
		nextOptionIndex := -1
		for i := startIndex; i <= maxOptionIndex; i++ {
			if _, ok := tuples[i]; ok {
				nextOptionIndex = i
				break
			}
		}
		if startIndex != nextOptionIndex {
			end, err := consumePositionals(startIndex)
			if err != nil {
				return nil, sp, err
			}
			if sp != nil {
				return nil, sp, nil
			}
			if end > startIndex {
				startIndex = end
				continue
			}
			startIndex = end
		}
		if startIndex != nextOptionIndex {
			extras = append(extras, args[startIndex:nextOptionIndex]...)
			startIndex = nextOptionIndex
		}
		var err *parseError
		startIndex, err = consumeOptional(startIndex)
		if err != nil {
			return nil, sp, err
		}
		if sp != nil {
			return nil, sp, nil
		}
	}
	stopIndex, err := consumePositionals(startIndex)
	if err != nil {
		return nil, sp, err
	}
	if sp != nil {
		return nil, sp, nil
	}
	extras = append(extras, args[stopIndex:]...)

	// required checks.
	var requiredNames []string
	for _, a := range p.Actions {
		if !seen[a] && a.Required {
			requiredNames = append(requiredNames, a.name())
		}
	}
	if len(requiredNames) > 0 {
		return nil, sp, &parseError{
			prog: p.Prog,
			msg:  "the following arguments are required: " + strings.Join(requiredNames, ", "),
		}
	}
	return extras, sp, nil
}

// getValues mirrors _get_values; the bool reports "values is action.default"
// (argparse compares identity — here: the OPTIONAL-empty branch returning
// const/default is the only way a default comes back out).
func (p *Parser) getValues(a *Action, argStrings []string) (any, bool, *parseError) {
	if a.Kind != KindSubParsers {
		for i, s := range argStrings {
			if s == "--" {
				argStrings = append(append([]string{}, argStrings[:i]...), argStrings[i+1:]...)
				break
			}
		}
	}
	switch {
	case a.Nargs == "?" && len(argStrings) == 0:
		var value any
		isDefault := true
		if len(a.OptionStrings) > 0 {
			value = a.Const
			isDefault = a.Const == nil && a.Default == nil
		} else {
			value = a.Default
		}
		// argparse runs a *string* const/default through the type converter
		// and the choices check even when no argument was supplied.
		if s, ok := value.(string); ok {
			v, err := p.getValue(a, s)
			if err != nil {
				return nil, false, err
			}
			if err := p.checkValue(a, v); err != nil {
				return nil, false, err
			}
			value = v
		}
		return value, isDefault, nil
	case a.Nargs == "*" && len(argStrings) == 0 && len(a.OptionStrings) == 0:
		if a.Default != nil {
			return a.Default, true, nil
		}
		return []any{}, false, nil
	case len(argStrings) == 1 && (a.Nargs == "" || a.Nargs == "?") && !a.nargsZero() && a.Kind != KindSubParsers:
		v, err := p.getValue(a, argStrings[0])
		if err != nil {
			return nil, false, err
		}
		if err := p.checkValue(a, v); err != nil {
			return nil, false, err
		}
		return v, false, nil
	case a.Kind == KindSubParsers:
		values := make([]any, len(argStrings))
		for i, s := range argStrings {
			values[i] = s
		}
		if len(values) > 0 {
			if err := p.checkValue(a, values[0]); err != nil {
				return nil, false, err
			}
		}
		return values, false, nil
	default:
		values := make([]any, len(argStrings))
		for i, s := range argStrings {
			v, err := p.getValue(a, s)
			if err != nil {
				return nil, false, err
			}
			values[i] = v
		}
		for _, v := range values {
			if err := p.checkValue(a, v); err != nil {
				return nil, false, err
			}
		}
		return values, false, nil
	}
}

// getValue mirrors _get_value (type conversion).
func (p *Parser) getValue(a *Action, s string) (any, *parseError) {
	switch a.Type {
	case TypeInt:
		v, err := pysem.ParseInt(s)
		if err != nil {
			return nil, argumentError(p.Prog, a, "invalid int value: %s", pysem.ReprStr(s))
		}
		return v, nil
	case TypeFloat:
		v, err := parsePyFloat(s)
		if err != nil {
			return nil, argumentError(p.Prog, a, "invalid float value: %s", pysem.ReprStr(s))
		}
		return v, nil
	default:
		return s, nil
	}
}

// checkValue mirrors _check_value.
func (p *Parser) checkValue(a *Action, v any) *parseError {
	if a.Kind == KindSubParsers {
		s, _ := v.(string)
		for _, name := range a.Sub.Names {
			if name == s {
				return nil
			}
		}
		reprs := make([]string, len(a.Sub.Names))
		for i, name := range a.Sub.Names {
			reprs[i] = pysem.ReprStr(name)
		}
		return argumentError(p.Prog, a, "invalid choice: %s (choose from %s)", reprAny(v), strings.Join(reprs, ", "))
	}
	if a.Choices == nil {
		return nil
	}
	for _, c := range a.Choices {
		if c == v {
			return nil
		}
	}
	reprs := make([]string, len(a.Choices))
	for i, c := range a.Choices {
		reprs[i] = reprAny(c)
	}
	return argumentError(p.Prog, a, "invalid choice: %s (choose from %s)", reprAny(v), strings.Join(reprs, ", "))
}

// reprAny is Python repr() over the value shapes a namespace holds.
func reprAny(v any) string {
	switch t := v.(type) {
	case string:
		return pysem.ReprStr(t)
	case int64:
		return strconv.FormatInt(t, 10)
	case float64:
		return pysem.FloatRepr(t)
	}
	return pysem.ReprAny(v)
}

// callAction mirrors the action __call__s.
func (p *Parser) callAction(a *Action, values any, ns map[string]any, extras *[]string, sp **special) *parseError {
	switch a.Kind {
	case KindStore:
		ns[a.Dest] = values
	case KindStoreTrue:
		ns[a.Dest] = true
	case KindStoreFalse:
		ns[a.Dest] = false
	case KindAppend:
		var items []any
		if prev, ok := ns[a.Dest].([]any); ok {
			items = append(items, prev...) // _copy_items
		}
		items = append(items, values)
		ns[a.Dest] = items
	case KindHelp:
		*sp = &special{kind: ResultHelp, prog: p.Prog}
	case KindVersion:
		*sp = &special{kind: ResultVersion, prog: p.Prog}
	case KindSubParsers:
		list := values.([]any)
		name := list[0].(string)
		rest := make([]string, len(list)-1)
		for i, v := range list[1:] {
			rest[i] = v.(string)
		}
		if a.Dest != "" {
			ns[a.Dest] = name
		}
		child := a.Sub.Parsers[name]
		childNs := map[string]any{}
		childExtras, childSp, err := child.parseKnownArgs(rest, childNs)
		if err != nil {
			return err
		}
		for k, v := range childNs {
			ns[k] = v
		}
		if childSp != nil {
			*sp = childSp
			return nil
		}
		*extras = append(*extras, childExtras...)
	}
	return nil
}

// parsePyFloat mirrors float(str): unicode-whitespace strip, underscores
// between digits, inf/infinity/nan (any case, signed), decimal/scientific
// notation only (Python float() takes no hex literals).
func parsePyFloat(s string) (float64, error) {
	t := pysem.Strip(s)
	if t == "" {
		return 0, fmt.Errorf("empty")
	}
	if strings.ContainsAny(t, "xX") {
		return 0, fmt.Errorf("hex literal")
	}
	if strings.Contains(t, "_") {
		// Python permits single underscores between digits only.
		ok := true
		for i := 0; i < len(t); i++ {
			if t[i] != '_' {
				continue
			}
			if i == 0 || i == len(t)-1 || !isASCIIDigit(t[i-1]) || !isASCIIDigit(t[i+1]) {
				ok = false
				break
			}
		}
		if !ok {
			return 0, fmt.Errorf("bad underscore")
		}
		t = strings.ReplaceAll(t, "_", "")
	}
	v, err := strconv.ParseFloat(t, 64)
	if err != nil {
		return 0, err
	}
	return v, nil
}

func isASCIIDigit(b byte) bool { return b >= '0' && b <= '9' }
