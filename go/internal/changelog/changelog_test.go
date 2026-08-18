// Golden-driven parity for the changelog surface: the malformed-entry
// corpus under tests/parity/goldens/changelog/ replays against Parse and
// BuildText (the Python freeze test keeps those two files honest against
// changelog.py), and the embedded data is pinned byte-for-byte to the
// Python bundle — the guard that keeps the auto-version workflow's rewrite
// of changelog_data.json from stranding the go:embed copy.
package changelog

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

const goldensDir = "../../../tests/parity/goldens/changelog"

func TestCorpusGoldenParity(t *testing.T) {
	corpus, err := os.ReadFile(filepath.Join(goldensDir, "corpus.json"))
	if err != nil {
		t.Fatalf("read corpus: %v (run `uv run python -m tests.parity.foundations.regen`)", err)
	}
	golden, err := os.ReadFile(filepath.Join(goldensDir, "parsed.json"))
	if err != nil {
		t.Fatalf("read golden: %v (run `uv run python -m tests.parity.foundations.regen`)", err)
	}
	var want any
	if err := json.Unmarshal(golden, &want); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(DumpPayload(Parse(corpus)))
	if err != nil {
		t.Fatal(err)
	}
	var got any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	gb, _ := json.Marshal(got)
	wb, _ := json.Marshal(want)
	if !bytes.Equal(gb, wb) {
		t.Fatalf("corpus parse disagrees with the golden\n--- got ---\n%s\n--- want ---\n%s", gb, wb)
	}
}

func TestEmbeddedDataMatchesPythonBundle(t *testing.T) {
	python, err := os.ReadFile(filepath.Join("..", "..", "..", "src", "yeaboi", "changelog_data.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(EmbeddedData(), python) {
		t.Fatal(
			"go/internal/changelog/changelog_data.json is not byte-identical to " +
				"src/yeaboi/changelog_data.json — re-copy the Python bundle over the embed " +
				"(cp src/yeaboi/changelog_data.json go/internal/changelog/changelog_data.json)",
		)
	}
}

func TestLoadServesTheEmbeddedBundle(t *testing.T) {
	entries := Load()
	if len(entries) == 0 {
		t.Fatal("the embedded changelog parsed to zero entries")
	}
	for _, e := range entries {
		if e.Version == "" {
			t.Fatal("an entry with an empty version survived the parse")
		}
		for _, h := range e.Highlights {
			if len(h.Areas) == 0 {
				t.Fatalf("highlight %q carries no areas — _coerce_areas never returns empty", h.Text)
			}
			for _, a := range h.Areas {
				if !ValidAreas[a] {
					t.Fatalf("highlight %q carries unknown area %q", h.Text, a)
				}
			}
		}
	}
}

func TestAreaVocabularyMatchesColors(t *testing.T) {
	if len(ValidAreas) != len(AreaColors) {
		t.Fatal("ValidAreas and AreaColors diverged")
	}
	for area := range ValidAreas {
		if _, ok := AreaColors[area]; !ok {
			t.Fatalf("area %q has no color", area)
		}
	}
}
