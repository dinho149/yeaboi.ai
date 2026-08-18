// yeaboi — the future single-binary CLI. Hidden and unshipped until W19:
// every real command exits 1 pointing at the Python yeaboi, and nothing
// packages this binary. The W8 gate commands (`__dump-foundations`,
// `__dump-args`, `__dump-changelog`) live in dump_gate.go and compile only
// under -tags paritydump (make go-build-cli) — see that file's header for
// why the product build must not carry them.
//
// Python twin: src/yeaboi/cli.py main()'s argparse layer (parser.go holds
// the tree). The version is injected via -ldflags "-X main.version=..." from
// pyproject — the binary reports the *product* version, not binaryVersion.
package main

import (
	"fmt"
	"os"

	ap "github.com/yeaboi-ai/yeaboi/go/internal/argparse"
	"github.com/yeaboi-ai/yeaboi/go/internal/argview"
)

// parserByProg finds the (sub)parser whose prog is the one a help action or
// error carried — the tree is small, so a walk beats bookkeeping.
func parserByProg(p *ap.Parser, prog string) *ap.Parser {
	if p.Prog == prog {
		return p
	}
	for _, a := range p.Actions {
		if a.Kind != ap.KindSubParsers {
			continue
		}
		for _, name := range a.Sub.Names {
			if found := parserByProg(a.Sub.Parsers[name], prog); found != nil {
				return found
			}
		}
	}
	return nil
}

// version is stamped by -ldflags; the fallback marks a local dev build.
var version = "0.0.0+dev"

// dumpGate is the W8 parity hook: nil in a product build, wired by
// dump_gate.go's init under -tags paritydump. Keeping the hook a variable
// (rather than tagging main itself) is what lets the untagged build compile
// with zero hidden argv surface.
var dumpGate func(args []string) (code int, handled bool)

func main() {
	args := os.Args[1:]
	if len(args) > 0 && dumpGate != nil {
		if code, handled := dumpGate(args); handled {
			os.Exit(code)
		}
	}

	parser := buildParser()
	result := parser.ParseArgs(args)
	switch result.Kind {
	case ap.ResultError:
		// argparse prints the erroring parser's usage block above the
		// error line, both on stderr.
		if p := parserByProg(parser, result.Prog); p != nil {
			fmt.Fprint(os.Stderr, argview.FormatUsage(p))
		}
		fmt.Fprintf(os.Stderr, "%s: error: %s\n", result.Prog, result.Message)
		os.Exit(2)
	case ap.ResultVersion:
		fmt.Printf("yeaboi %s\n", version)
		os.Exit(0)
	case ap.ResultHelp:
		// -h fires on the parser it reached (a subcommand's -h prints that
		// subcommand's screen), to stdout, exit 0 — exactly argparse.
		if p := parserByProg(parser, result.Prog); p != nil {
			fmt.Print(argview.FormatHelp(p))
		}
		os.Exit(0)
	}
	_ = fromNamespace(result.Ns)
	fmt.Fprintln(os.Stderr, "not yet implemented in yeaboi (Go) — use the Python yeaboi")
	os.Exit(1)
}

// dumpArgsResult mirrors argdump._outcome: what one parse ended as. It stays
// outside dump_gate.go because the argv golden test replays it untagged.
func dumpArgsResult(result ap.Result) map[string]any {
	switch result.Kind {
	case ap.ResultOk:
		return map[string]any{"status": "ok", "args": result.Ns}
	case ap.ResultError:
		return map[string]any{
			"status":  "error",
			"exit":    2,
			"prog":    result.Prog,
			"message": result.Message,
		}
	case ap.ResultVersion:
		return map[string]any{"status": "exit", "code": 0, "action": "version"}
	default:
		return map[string]any{"status": "exit", "code": 0, "action": "help"}
	}
}
