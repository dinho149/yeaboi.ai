//go:build paritydump

// The W8 gate commands — the subprocess arms of tests/parity/foundations/:
//
//	yeaboi __dump-foundations   the Go twin of tests/parity/foundations/dump.py
//	yeaboi __dump-args ARGS...  parse ARGS with the cli.py-twin tree, dump JSON
//	yeaboi __dump-changelog     the embedded changelog, parsed + rendered
//
// Compiled only under -tags paritydump (make go-build-cli), so the gate
// binary and the product binary are different artifacts. That split is
// load-bearing, not cosmetic: __dump-foundations dumps every config getter
// verbatim — API keys, tracker tokens, the SMTP password — straight past the
// redaction twin, which is exactly what the parity gate needs and exactly
// what a binary on a user's machine must never answer. A product build
// (plain `go build`, no tag) does not carry the argv, so the leak cannot
// ship by omission at W19; nothing has to remember to remove it.
package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/yeaboi-ai/yeaboi/go/internal/changelog"
	"github.com/yeaboi-ai/yeaboi/go/internal/foundations"
	"github.com/yeaboi-ai/yeaboi/go/internal/home"
)

func init() {
	dumpGate = func(args []string) (int, bool) {
		switch args[0] {
		case "__dump-foundations":
			return runDumpFoundations(), true
		case "__dump-args":
			return runDumpArgs(args[1:]), true
		case "__dump-changelog":
			return runDumpChangelog(), true
		}
		return 0, false
	}
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
