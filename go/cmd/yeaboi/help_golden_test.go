// Golden-driven parity for the help screens: every parser in the tree must
// render, through internal/argview, the exact bytes Python argparse printed
// into tests/parity/goldens/cli/help/ (captured at COLUMNS=80 — see
// tests/parity/foundations/helpdump.py). The check is two-way: a committed
// screen no parser claims is as much a failure as a parser without a
// screen, so deleting a subcommand cannot leave its golden behind.
package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	ap "github.com/yeaboi-ai/yeaboi/go/internal/argparse"
	"github.com/yeaboi-ai/yeaboi/go/internal/argview"
)

const helpGoldensDir = "../../../tests/parity/goldens/cli/help"

// walkParsers collects every parser with its prog, in add_parser order.
func walkParsers(p *ap.Parser, out *[]*ap.Parser) {
	*out = append(*out, p)
	for _, a := range p.Actions {
		if a.Kind != ap.KindSubParsers {
			continue
		}
		for _, name := range a.Sub.Names {
			walkParsers(a.Sub.Parsers[name], out)
		}
	}
}

func TestHelpGoldenParity(t *testing.T) {
	t.Setenv("COLUMNS", "80")
	var parsers []*ap.Parser
	root := buildParser()
	walkParsers(root, &parsers)
	if len(parsers) < 34 {
		t.Fatalf("parser walk found only %d parsers", len(parsers))
	}
	claimed := map[string]bool{"version.txt": true}
	for _, p := range parsers {
		name := strings.ReplaceAll(p.Prog, " ", "-") + ".txt"
		claimed[name] = true
		p := p
		t.Run(p.Prog, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(helpGoldensDir, name))
			if err != nil {
				t.Fatalf("read golden (run `uv run python -m tests.parity.foundations.regen`): %v", err)
			}
			got := argview.FormatHelp(p)
			if got != string(raw) {
				t.Errorf("help screen diverged from the committed golden\n--- got ---\n%s\n--- want ---\n%s", got, raw)
			}
		})
	}
	entries, err := os.ReadDir(helpGoldensDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if !claimed[e.Name()] {
			t.Errorf("committed golden %s is claimed by no parser — regenerate the goldens", e.Name())
		}
	}
}

func TestVersionTemplateGolden(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(helpGoldensDir, "version.txt"))
	if err != nil {
		t.Fatalf("read golden: %v", err)
	}
	// main.go prints "yeaboi <version>\n"; the golden pins that shape as a
	// template because the product version changes per release.
	if string(raw) != "yeaboi {version}\n" {
		t.Errorf("version template drifted: %q", raw)
	}
}

func TestUsageIsTheHelpPrefix(t *testing.T) {
	// error() prints FormatUsage; it must always be the first block of the
	// help screen, so the goldens pin it transitively.
	t.Setenv("COLUMNS", "80")
	var parsers []*ap.Parser
	walkParsers(buildParser(), &parsers)
	for _, p := range parsers {
		usage := argview.FormatUsage(p)
		help := argview.FormatHelp(p)
		if !strings.HasPrefix(help, strings.TrimRight(usage, "\n")) {
			t.Errorf("%s: usage is not a prefix of help", p.Prog)
		}
	}
}
