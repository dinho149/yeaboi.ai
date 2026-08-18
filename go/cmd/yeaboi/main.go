// yeaboi — the future single-binary CLI. Hidden and unshipped until W19:
// every real command exits 1 pointing at the Python yeaboi, and nothing
// packages this binary. What is live today are the W8 gate commands:
//
//	yeaboi __dump-foundations   the Go twin of tests/parity/foundations/dump.py
//	yeaboi __dump-args ARGS...  parse ARGS with the cli.py-twin tree, dump JSON
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
	"github.com/yeaboi-ai/yeaboi/go/internal/foundations"
	"github.com/yeaboi-ai/yeaboi/go/internal/home"
)

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
		}
	}

	result := buildParser().ParseArgs(args)
	switch result.Kind {
	case ap.ResultError:
		// Usage rendering arrives with internal/argview (W8 phase 4); the
		// error line itself is already argparse's.
		fmt.Fprintf(os.Stderr, "%s: error: %s\n", result.Prog, result.Message)
		os.Exit(2)
	case ap.ResultVersion:
		fmt.Printf("yeaboi %s\n", version)
		os.Exit(0)
	case ap.ResultHelp:
		fmt.Fprintf(os.Stderr, "%s: help text arrives with internal/argview (W8 phase 4)\n", result.Prog)
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
