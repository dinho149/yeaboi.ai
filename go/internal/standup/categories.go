package standup

// categories.go — port of src/yeaboi/standup/categories.py. Keep in lockstep:
// the Python module is the reference implementation;
// tests/parity/test_standup_parity.py diffs whole-pipeline output.
//
// Activity classification and coverage helpers for structured standup updates.

import (
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// Canonical source identifiers (collector.SOURCE_* — collector.py stays in
// Python, so the seven strings are pinned here; the parity suite would catch
// any drift).
const (
	sourceJira       = "jira"
	sourceAzdo       = "azure_devops"
	sourceAzdoRepos  = "azdo_repos" // AzDO git commits/PRs — separate key so a repo-API failure never hides work items
	sourceGithub     = "github"
	sourceLocalGit   = "local_git"
	sourceConfluence = "confluence"
	sourceNotion     = "notion"
)

// CATEGORY_TICKETING / CATEGORY_CODE / CATEGORY_DOCUMENTATION / CATEGORIES.
const (
	categoryTicketing     = "ticketing"
	categoryCode          = "code"
	categoryDocumentation = "documentation"
)

var categoriesList = []string{categoryTicketing, categoryCode, categoryDocumentation}

// COVERED / PARTIAL / FAILED / NOT_CONFIGURED coverage states.
const (
	covered       = "covered"
	partialState  = "partial"
	failedState   = "failed"
	notConfigured = "not_configured"
)

var (
	categoriesDocExtensions  = map[string]bool{".md": true, ".mdx": true, ".rst": true, ".adoc": true, ".asciidoc": true}
	categoriesDocDirectories = map[string]bool{"docs": true, "documentation": true, "wiki": true}
	categoriesDocFilenames   = map[string]bool{
		"readme": true, "changelog": true, "contributing": true, "authors": true, "license": true,
	}
)

// isDocumentationPath mirrors is_documentation_path: whether a repository path
// conventionally represents documentation.
func isDocumentationPath(path string) bool {
	// Python: str(path or "").replace("\\", "/").strip("/") — strip removes
	// only slashes here.
	normalized := strings.Trim(strings.ReplaceAll(path, "\\", "/"), "/")
	if normalized == "" {
		return false
	}
	// PurePosixPath(normalized).parts — empty and "." segments collapse,
	// ".." is kept (pathlib does not resolve it).
	parts := []string{}
	for _, seg := range strings.Split(normalized, "/") {
		if seg == "" || seg == "." {
			continue
		}
		parts = append(parts, seg)
	}
	// any(part in _DOC_DIRECTORIES for part in lowered_parts[:-1])
	for i := 0; i < len(parts)-1; i++ {
		if categoriesDocDirectories[pysem.Lower(parts[i])] {
			return true
		}
	}
	// PurePosixPath.stem/.suffix (Python 3.11 rule): the suffix starts at the
	// last "." only when it is neither the first nor the last character of the
	// name. (3.14 treats a lone trailing dot as a suffix, but every name the
	// two rules disagree on fails both the extension and the filename test in
	// both versions, so the decision is identical.)
	name := ""
	if len(parts) > 0 {
		name = parts[len(parts)-1]
	}
	suffix, stem := "", name
	if i := strings.LastIndex(name, "."); i > 0 && i < len(name)-1 {
		suffix, stem = name[i:], name[:i]
	}
	stem = pysem.Lower(stem)
	return categoriesDocExtensions[pysem.Lower(suffix)] || (suffix == "" && categoriesDocFilenames[stem])
}

// documentationPaths mirrors documentation_paths: the documentation paths
// attached to a repository activity event.
func documentationPaths(item *pysem.Obj) []string {
	out := []string{}
	for _, path := range listOr(item, "changed_files") {
		s := pysem.Str(path)
		if isDocumentationPath(s) {
			out = append(out, s)
		}
	}
	return out
}

func isRepositoryActivity(item *pysem.Obj) bool {
	switch strOr(item, "source") {
	case sourceGithub, sourceAzdoRepos, sourceLocalGit:
		return true
	}
	switch strOr(item, "kind") {
	case "commit", "pr", "review":
		return true
	}
	return false
}

func isDocumentationActivity(item *pysem.Obj) bool {
	switch strOr(item, "source") {
	case sourceConfluence, sourceNotion:
		return true
	}
	return len(documentationPaths(item)) > 0
}

func isTicketingActivity(item *pysem.Obj) bool {
	switch strOr(item, "source") {
	case sourceJira, sourceAzdo:
		return true
	}
	return false
}

// isCodeActivity mirrors is_code_activity: code includes repository events
// unless all known changed files are documentation.
func isCodeActivity(item *pysem.Obj) bool {
	if !isRepositoryActivity(item) {
		return false
	}
	// Python filters truthy paths BEFORE str().
	paths := []string{}
	for _, path := range listOr(item, "changed_files") {
		if pysem.Truthy(path) {
			paths = append(paths, pysem.Str(path))
		}
	}
	if len(paths) == 0 {
		return true
	}
	for _, path := range paths {
		if !isDocumentationPath(path) {
			return true
		}
	}
	return false
}

// splitActivity mirrors split_activity: split items into category-specific
// evidence lists. Mixed repository changes intentionally appear in both code
// and documentation. Every category key is always present.
func splitActivity(items []*pysem.Obj) map[string][]*pysem.Obj {
	split := map[string][]*pysem.Obj{
		categoryTicketing:     {},
		categoryCode:          {},
		categoryDocumentation: {},
	}
	for _, item := range items {
		if isTicketingActivity(item) {
			split[categoryTicketing] = append(split[categoryTicketing], item)
		}
	}
	for _, item := range items {
		if isCodeActivity(item) {
			split[categoryCode] = append(split[categoryCode], item)
		}
	}
	for _, item := range items {
		if isDocumentationActivity(item) {
			split[categoryDocumentation] = append(split[categoryDocumentation], item)
		}
	}
	return split
}

// categorySources mirrors category_sources: enabled collectors that can
// supply one output category.
func categorySources(category string, enabledSources map[string]bool) map[string]bool {
	var supplying []string
	switch category {
	case categoryTicketing:
		supplying = []string{sourceJira, sourceAzdo}
	case categoryCode:
		supplying = []string{sourceGithub, sourceAzdoRepos, sourceLocalGit}
	case categoryDocumentation:
		// Repository collectors are documentation sources too when changed
		// paths identify docs files.
		supplying = []string{sourceConfluence, sourceNotion, sourceGithub, sourceAzdoRepos}
	default:
		panic(&pysem.Error{Class: "ValueError", Msg: "unknown standup category: " + category})
	}
	out := map[string]bool{}
	for _, source := range supplying {
		if enabledSources[source] {
			out[source] = true
		}
	}
	return out
}

// categoriesIntersects mirrors `bool(a & b)`.
func categoriesIntersects(a, b map[string]bool) bool {
	for k := range a {
		if b[k] {
			return true
		}
	}
	return false
}

// coverageStates mirrors coverage_states: report-wide coverage for ticketing,
// code, and documentation, in CATEGORIES order.
func coverageStates(enabledSources map[string]bool, bundle *Bundle) [][2]string {
	completed := map[string]bool{}
	for _, sc := range bundle.Counts {
		completed[sc.Source] = true
	}
	failedSources := map[string]bool{}
	for _, pair := range bundle.Errors {
		failedSources[pair.Source] = true
	}
	partialSources := map[string]bool{}
	for _, pair := range bundle.PartialSources {
		partialSources[pair.Source] = true
	}
	if failedSources[sourceAzdo] {
		failedSources[sourceAzdoRepos] = true
	}
	states := [][2]string{}
	for _, category := range categoriesList {
		expected := categorySources(category, enabledSources)
		var state string
		switch {
		case len(expected) == 0:
			state = notConfigured
		case categoriesIntersects(expected, completed):
			if categoriesIntersects(expected, failedSources) || categoriesIntersects(expected, partialSources) {
				state = partialState
			} else {
				state = covered
			}
		default:
			if categoriesIntersects(expected, failedSources) {
				state = failedState
			} else {
				state = partialState
			}
		}
		states = append(states, [2]string{category, state})
	}
	return states
}

// emptySummary mirrors empty_summary: the explicit empty-state sentence for a
// category.
func emptySummary(category, coverage string) string {
	labels := map[string]string{"ticketing": "Ticketing", "code": "Code", "documentation": "Documentation"}
	label, ok := labels[category]
	if !ok {
		panic(&pysem.Error{Class: "KeyError", Msg: category})
	}
	if coverage == notConfigured {
		return label + " sources not configured."
	}
	if coverage == failedState {
		return label + " activity unavailable because the selected sources failed."
	}
	if coverage == partialState {
		return "No " + category + " activity detected from the sources that were successfully scanned; coverage was partial."
	}
	if category == categoryCode {
		return "No code activity detected in the selected repositories."
	}
	return "No " + category + " activity detected in the selected sources."
}

// isEmptyState mirrors is_empty_state: whether text is one of the canonical
// *droppable* empty-state sentences.
//
// Exporters use this to drop per-member "No X activity detected…" footnotes:
// coverage is a report-wide fact the Details section already states once, and
// repeating it on every card buried the members who did something. Exact
// string match on purpose — bespoke prose ("Nothing merged, two reviews
// pending") must never be classified as sayable-by-a-machine and dropped.
//
// The FAILED sentence is deliberately NOT droppable: "activity unavailable
// because the selected sources failed" means *we could not look*, and a
// member folded into a "No activity detected" strip on a day Jira 401'd
// would be a positive claim about a named person that nobody verified.
func isEmptyState(text string) bool {
	stripped := pysem.Strip(text)
	for _, category := range categoriesList {
		for _, coverage := range []string{covered, partialState, notConfigured} {
			if stripped == emptySummary(category, coverage) {
				return true
			}
		}
	}
	return false
}
