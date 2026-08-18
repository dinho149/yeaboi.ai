// yeaboi — the future single-binary CLI. Hidden and unshipped until W19:
// every real command exits 1 pointing at the Python yeaboi, and nothing
// packages this binary. What is live today are the W8 gate commands:
//
//	yeaboi __dump-foundations   the Go twin of tests/parity/foundations/dump.py
//	yeaboi __dump-args ARGS...  parse ARGS with the cli.py-twin tree, dump JSON
//	yeaboi __dump-changelog     the embedded changelog, parsed + rendered
//
// Python twin: src/yeaboi/cli.py main()'s argparse layer (parser.go holds
// the tree). The version is injected via -ldflags "-X main.version=..." from
// pyproject — the binary reports the *product* version, not binaryVersion.
package main

import (
	"encoding/json"
	"fmt"
	"os"

	ap "github.com/yeaboi-ai/yeaboi/go/internal/argparse"
	"github.com/yeaboi-ai/yeaboi/go/internal/argview"
	"github.com/yeaboi-ai/yeaboi/go/internal/changelog"
	"github.com/yeaboi-ai/yeaboi/go/internal/foundations"
	"github.com/yeaboi-ai/yeaboi/go/internal/home"
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

func main() {
	args := os.Args[1:]
	if len(args) > 0 {
		switch args[0] {
		case "__dump-foundations":
			os.Exit(runDumpFoundations())
		case "__dump-args":
			os.Exit(runDumpArgs(args[1:]))
		case "__dump-changelog":
			os.Exit(runDumpChangelog())
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

// runDumpFoundations prints the foundations dump for the current process
// environment — the subprocess arm of the W8 foundations gate.
func runDumpFoundations() int {
	cwd, err := os.Getwd()
	if err != nil {
		fmt.Fprintf(os.Stderr, "__dump-foundations: %v\n", err)
		return 1
	}
	dump, err := foundations.Dump(home.OSEnv, cwd)
	if err != nil {
		fmt.Fprintf(os.Stderr, "__dump-foundations: %v\n", err)
		return 1
	}
	return printJSON(dump)
}

// runDumpChangelog prints the embedded changelog — parsed entries plus the
// rendered Markdown — as JSON. The subprocess arm of the W8 changelog gate:
// tests/parity/foundations/changelogdump.py build_live_dump() is the twin,
// so the embedded copy and yeaboi.changelog's parse must agree end to end.
func runDumpChangelog() int {
	entries := changelog.Load()
	return printJSON(changelog.DumpPayload(entries))
}

// runDumpArgs parses the given argv against the cli.py-twin tree and prints
// the outcome as JSON — the subprocess arm of the W8 argv gate. The result
// shape matches tests/parity/foundations/argdump.py exactly.
func runDumpArgs(argv []string) int {
	return printJSON(dumpArgsResult(buildParser().ParseArgs(argv)))
}

// dumpArgsResult mirrors argdump._outcome: what one parse ended as.
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

func printJSON(v any) int {
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		fmt.Fprintf(os.Stderr, "encode: %v\n", err)
		return 1
	}
	return 0
}
