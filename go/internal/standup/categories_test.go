package standup

// categories_test.go — port of tests/unit/test_standup_categories.py (the
// categories-module assertions; the engine/documentation_scope tests in that
// file belong to other modules' ports).

import (
	"reflect"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// catItem builds an activity item from key/value pairs, mirroring the Python
// dict literals.
func catItem(kv ...any) *pysem.Obj {
	item := pysem.EmptyObj()
	for i := 0; i+1 < len(kv); i += 2 {
		item.Set(kv[i].(string), kv[i+1])
	}
	return item
}

func TestCategoriesDocumentationPathConventions(t *testing.T) {
	for _, path := range []string{"README.md", "docs/setup.txt", "architecture/ADR-004.md"} {
		if !isDocumentationPath(path) {
			t.Errorf("isDocumentationPath(%q) = false, want true", path)
		}
	}
	for _, path := range []string{"src/readme.py", "pyproject.toml"} {
		if isDocumentationPath(path) {
			t.Errorf("isDocumentationPath(%q) = true, want false", path)
		}
	}
}

func TestCategoriesDocumentationPathUnicodeFilenames(t *testing.T) {
	// Golden: unicode filenames follow the same conventions (verified against
	// the Python reference).
	for _, path := range []string{"docs/übersicht.txt", "notizen.md"} {
		if !isDocumentationPath(path) {
			t.Errorf("isDocumentationPath(%q) = false, want true", path)
		}
	}
	if isDocumentationPath("src/读我.py") {
		t.Errorf("isDocumentationPath(%q) = true, want false", "src/读我.py")
	}
}

func TestCategoriesSplitActivityPartitionsAndDuplicatesMixedRepositoryWork(t *testing.T) {
	ticket := catItem("source", sourceJira, "kind", "update", "title", "Moved PSOT-1")
	docsOnly := catItem(
		"source", sourceGithub,
		"kind", "commit",
		"title", "Update guide",
		"changed_files", []any{"docs/guide.md"},
	)
	mixed := catItem(
		"source", sourceAzdoRepos,
		"kind", "pr",
		"title", "Add API and guide",
		"changed_files", []any{"src/api.py", "README.md"},
	)
	confluence := catItem("source", sourceConfluence, "kind", "page", "title", "Runbook")

	split := splitActivity([]*pysem.Obj{ticket, docsOnly, mixed, confluence})

	if !reflect.DeepEqual(split["ticketing"], []*pysem.Obj{ticket}) {
		t.Errorf("ticketing = %v, want [ticket]", split["ticketing"])
	}
	if !reflect.DeepEqual(split["code"], []*pysem.Obj{mixed}) {
		t.Errorf("code = %v, want [mixed]", split["code"])
	}
	if !reflect.DeepEqual(split["documentation"], []*pysem.Obj{docsOnly, mixed, confluence}) {
		t.Errorf("documentation = %v, want [docsOnly, mixed, confluence]", split["documentation"])
	}
}

func TestCategoriesUnknownRepositoryPathsStayCodeOnly(t *testing.T) {
	event := catItem("source", sourceGithub, "kind", "commit", "title", "Work")
	split := splitActivity([]*pysem.Obj{event})
	if !reflect.DeepEqual(split["code"], []*pysem.Obj{event}) {
		t.Errorf("code = %v, want [event]", split["code"])
	}
	if len(split["documentation"]) != 0 {
		t.Errorf("documentation = %v, want empty", split["documentation"])
	}
}

func catStates(states [][2]string) map[string]string {
	out := map[string]string{}
	for _, pair := range states {
		out[pair[0]] = pair[1]
	}
	return out
}

func TestCategoriesCoverageDistinguishesConfiguredPartialAndMissing(t *testing.T) {
	bundle := &Bundle{
		Counts: []SourceCount{{Source: sourceJira, N: 0}, {Source: sourceGithub, N: 1}},
		Errors: []SourcePair{{Source: sourceConfluence, Text: "authentication failed"}},
	}
	enabled := map[string]bool{sourceJira: true, sourceGithub: true, sourceConfluence: true}
	states := catStates(coverageStates(enabled, bundle))
	want := map[string]string{
		"ticketing":     "covered",
		"code":          "covered",
		"documentation": "partial",
	}
	if !reflect.DeepEqual(states, want) {
		t.Errorf("states = %v, want %v", states, want)
	}
}

func TestCategoriesPartialEnrichmentMarksDocumentationPartialWithoutSourceFailure(t *testing.T) {
	bundle := &Bundle{
		Counts:         []SourceCount{{Source: sourceConfluence, N: 1}},
		PartialSources: []SourcePair{{Source: sourceConfluence, Text: "earlier editors incomplete"}},
	}

	states := catStates(coverageStates(map[string]bool{sourceConfluence: true}, bundle))

	if states["documentation"] != "partial" {
		t.Errorf("documentation = %q, want partial", states["documentation"])
	}
}

func TestCategoriesCoverageStatesKeepCategoriesOrder(t *testing.T) {
	states := coverageStates(map[string]bool{}, &Bundle{})
	want := [][2]string{
		{"ticketing", "not_configured"},
		{"code", "not_configured"},
		{"documentation", "not_configured"},
	}
	if !reflect.DeepEqual(states, want) {
		t.Errorf("states = %v, want %v", states, want)
	}
}

func TestCategoriesExplicitEmptyMessages(t *testing.T) {
	if got := emptySummary("ticketing", "not_configured"); got != "Ticketing sources not configured." {
		t.Errorf("emptySummary(ticketing, not_configured) = %q", got)
	}
	got := emptySummary("documentation", "failed")
	want := "Documentation activity unavailable because the selected sources failed."
	if got != want {
		t.Errorf("emptySummary(documentation, failed) = %q, want %q", got, want)
	}
}

func TestCategoriesIsEmptyStateRecognisesDroppableSentencesAndNothingElse(t *testing.T) {
	for _, category := range categoriesList {
		for _, coverage := range []string{"covered", "partial", "not_configured"} {
			if !isEmptyState(emptySummary(category, coverage)) {
				t.Errorf("isEmptyState(emptySummary(%q, %q)) = false, want true", category, coverage)
			}
		}
	}
	// FAILED is not droppable: "we could not look" is per-member news, and a
	// member folded into a "No activity detected" strip on a 401 day would be
	// a positive claim nobody verified.
	for _, category := range categoriesList {
		if isEmptyState(emptySummary(category, "failed")) {
			t.Errorf("isEmptyState(emptySummary(%q, failed)) = true, want false", category)
		}
	}
	// Whitespace tolerated; bespoke prose and near-misses are not droppable.
	if !isEmptyState("  No code activity detected in the selected repositories. ") {
		t.Error("whitespace-padded droppable sentence not recognised")
	}
	for _, text := range []string{
		"No code activity detected today.",
		"Nothing merged, two reviews pending.",
		"",
	} {
		if isEmptyState(text) {
			t.Errorf("isEmptyState(%q) = true, want false", text)
		}
	}
}
