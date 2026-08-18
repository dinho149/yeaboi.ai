// Golden-driven parity for the CLI parse tree: replays every committed argv
// vector under tests/parity/goldens/cli/args.json (written by the Python
// dumper — see tests/parity/foundations/argdump.py) against buildParser().
// The Python freeze test keeps that file honest against cli.py, so passing
// here is Python ↔ Go parity without a binary in the loop — the
// subprocess-vs-binary arm is test_go_binary_matches_python_argdump.
package main

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

const argsGolden = "../../../tests/parity/goldens/cli/args.json"

type argsGoldenDoc struct {
	Vectors []struct {
		Name   string          `json:"name"`
		Argv   []string        `json:"argv"`
		Result json.RawMessage `json:"result"`
	} `json:"vectors"`
}

func TestArgsGoldenParity(t *testing.T) {
	raw, err := os.ReadFile(argsGolden)
	if err != nil {
		t.Fatalf("read golden (run `uv run python -m tests.parity.foundations.regen`): %v", err)
	}
	var doc argsGoldenDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	if len(doc.Vectors) == 0 {
		t.Fatal("golden holds no vectors")
	}
	for _, v := range doc.Vectors {
		t.Run(v.Name, func(t *testing.T) {
			var want any
			if err := json.Unmarshal(v.Result, &want); err != nil {
				t.Fatal(err)
			}
			result := dumpArgsResult(buildParser().ParseArgs(v.Argv))
			// canonicalise through JSON so int64s and the golden's float64s
			// compare cleanly.
			blob, err := json.Marshal(result)
			if err != nil {
				t.Fatal(err)
			}
			var got any
			if err := json.Unmarshal(blob, &got); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(got, want) {
				t.Errorf("argv %v:\n got %#v\nwant %#v", v.Argv, got, want)
			}
		})
	}
}

// TestTypedArgsSmoke pins the namespace → typed-Args seam over a
// representative parse of every subcommand family.
func TestTypedArgsSmoke(t *testing.T) {
	result := buildParser().ParseArgs([]string{"--quick", "report", "--period", "quarter", "--strict"})
	a := fromNamespace(result.Ns)
	if !a.Quick || a.Command != "report" || a.Report == nil {
		t.Fatalf("top-level view wrong: %+v", a)
	}
	if a.Report.Period != "quarter" || !a.Report.Strict || a.Report.Theme != "midnight" {
		t.Fatalf("report view wrong: %+v", a.Report)
	}

	result = buildParser().ParseArgs([]string{"perf", "complete", "Bob", "--transcript", "t", "--images", "a.png"})
	a = fromNamespace(result.Ns)
	if a.Perf == nil || a.Perf.Command != "complete" || a.Perf.Engineer != "Bob" || a.Perf.Transcript != "t" {
		t.Fatalf("perf view wrong: %+v", a.Perf)
	}
	if len(a.Perf.Images) != 1 || a.Perf.Images[0] != "a.png" || a.Perf.Recipients != nil {
		t.Fatalf("perf list fields wrong: %+v", a.Perf)
	}

	result = buildParser().ParseArgs([]string{"--resume", "--team-size", "4"})
	a = fromNamespace(result.Ns)
	if a.Resume == nil || *a.Resume != "__pick__" || a.TeamSize == nil || *a.TeamSize != 4 {
		t.Fatalf("nullable fields wrong: %+v", a)
	}
	if a.SprintLength != nil || a.PriorArt != nil || len(a.AllowPath) != 0 || a.AllowPath == nil {
		t.Fatalf("None/empty defaults wrong: %+v", a)
	}
}
