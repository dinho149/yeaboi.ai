package analysis

// code_health.go — port of src/yeaboi/analysis/code_health.py. Keep in
// lockstep: the Python module is the reference implementation; the parity
// suite diffs the two outputs whole.
//
// Deterministic repository-health analysis and prioritized actions. Inputs
// are decoded ordered JSON (*pysem.Obj / []any / string / json.Number / bool
// / nil); every result object is built in the exact key order the Python
// dicts are, so canonical JSON matches byte for byte.
//
// Two deliberate asymmetries the reference pins (do not "fix" them):
//   - first_by_scope is a dict comprehension, so despite its name the
//     hotspot/large-change exemplar is the LAST change touching a scope —
//     link and confidence come from the last write.
//   - isTestPath does WHOLE-part matching ("src/testing/util.py" is not a
//     test path), while AnalyseRepositoryHealth's test-suite check is a
//     "/test" SUBSTRING check. Two different semantics, kept separate.
//
// Pure: no I/O, no config — and NOTHING here is ever logged; no error ever
// carries input content (no error surface exists at all).

import (
	"fmt"
	"sort"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// manifests ports _MANIFESTS.
var manifests = map[string]bool{
	"package.json":     true,
	"pyproject.toml":   true,
	"setup.py":         true,
	"cargo.toml":       true,
	"go.mod":           true,
	"pom.xml":          true,
	"build.gradle":     true,
	"requirements.txt": true,
}

// binarySuffixes ports _BINARY_SUFFIXES.
var binarySuffixes = map[string]bool{
	".7z": true, ".avi": true, ".bin": true, ".bmp": true, ".class": true,
	".dll": true, ".docx": true, ".exe": true, ".gif": true, ".gz": true,
	".ico": true, ".jar": true, ".jpeg": true, ".jpg": true, ".mov": true,
	".mp3": true, ".mp4": true, ".pdf": true, ".png": true, ".pptx": true,
	".pyc": true, ".so": true, ".tar": true, ".woff": true, ".woff2": true,
	".xlsx": true, ".zip": true,
}

// generatedParts ports _GENERATED_PARTS. A file literally named "build" (a
// bare path equal to a generated-dir name) is excluded too: the filename is
// itself a path part.
var generatedParts = map[string]bool{
	".next": true, "build": true, "coverage": true, "dist": true,
	"generated": true, "node_modules": true, "target": true, "vendor": true,
}

// testParts ports _TEST_PARTS.
var testParts = map[string]bool{
	"test": true, "tests": true, "__tests__": true, "spec": true, "specs": true,
}

// docFileSuffixes ports _DOC_SUFFIXES. Distinct from practices.go's
// docSuffixes on purpose: this module's set carries ".txt" (and no ".adoc"),
// exactly as the two Python modules disagree.
var docFileSuffixes = map[string]bool{".md": true, ".mdx": true, ".rst": true, ".txt": true}

// pathSuffix mirrors PurePosixPath(path).suffix over a full path (posixParts
// / posixName live in practices.go), under the CPython rule verified against
// this repo's interpreter: leading dots of the name never open a suffix, and
// the substring from the last remaining dot — including a bare trailing "."
// — is the suffix when that dot is not at index 0. The trailing-dot cases
// where this differs from older CPythons are never members of any suffix set
// here, so behaviour is version-stable.
func pathSuffix(path string) string {
	name := strings.TrimLeft(posixName(path), ".")
	if i := strings.LastIndexByte(name, '.'); i > 0 {
		return name[i:]
	}
	return ""
}

// newFinding ports _finding — one repository-health finding, key order as the
// Python dict literal.
func newFinding(repo *pysem.Obj, category, title, detail, evidence, priority, effort, confidence string) *pysem.Obj {
	f := pysem.EmptyObj()
	id := fmt.Sprintf("%s:%s:%s:%s",
		pysem.Str(repo.GetDefault("provider", "")), pysem.Str(repo.GetDefault("name", "")), category, title)
	f.Set("id", strings.ReplaceAll(pysem.Lower(id), " ", "-"))
	f.Set("category", category)
	f.Set("title", title)
	f.Set("detail", detail)
	f.Set("priority", priority)
	f.Set("impact", "Improves delivery safety and maintainability")
	f.Set("confidence", confidence)
	f.Set("evidence", evidence)
	f.Set("link", repo.GetDefault("url", ""))
	f.Set("affected_scope", []any{repo.GetDefault("name", "")})
	f.Set("next_steps", []any{detail})
	f.Set("owner_role", "Repository maintainer")
	f.Set("effort", effort)
	f.Set("completion_check", fmt.Sprintf("Confirm %s for %s.",
		pysem.Lower(title), pysem.Str(repo.GetDefault("name", "the repository"))))
	return f
}

// anyPath mirrors any(pred(p) for p in set) over a lowered-path set —
// membership only, so map iteration order never reaches the output.
func anyPath(set map[string]bool, pred func(string) bool) bool {
	for p := range set {
		if pred(p) {
			return true
		}
	}
	return false
}

// AnalyseRepositoryHealth ports analyse_repository_health — analyse every
// recently active repository from its complete file inventory. activeNames
// holds (provider, lowercased name) pairs, mirroring the Python set of
// tuples.
//
// AHEAD OF THE SEAM: no RPC method calls this (or RepositoryHealthSummary)
// yet, so tests/parity/ does NOT guard them — only the Go unit pins do. They
// are ported now because the natural follow-on method (repository health /
// score_docs) needs them; until that method exists, a change to their Python
// twins must be mirrored by hand, because `make parity` will stay green.
func AnalyseRepositoryHealth(inventory []any, activeNames map[[2]string]bool) ([]any, []any) {
	reports := []any{}
	findings := []any{}
	for _, v := range inventory {
		repo := pysem.AsObj(v)
		if repo == nil {
			continue // out of contract: Python would raise on .get
		}
		if pysem.Truthy(repo.Get("discovery_error")) {
			continue
		}
		key := [2]string{
			pysem.Str(repo.GetDefault("provider", "")),
			pysem.Lower(pysem.Str(repo.GetDefault("name", ""))),
		}
		// GitHub names are owner/repo while activity is tagged with the same slug.
		var active bool
		if repo.Get("provider") == "github" {
			active = pysem.Truthy(repo.Get("active"))
		} else {
			active = activeNames[key]
		}
		if !active {
			report := repo.Clone()
			report.Set("status", "unchanged")
			report.Set("findings", []any{})
			reports = append(reports, report)
			continue
		}
		pathsAny, _ := repo.GetDefault("paths", []any{}).([]any)
		paths := make([]string, 0, len(pathsAny))
		for _, p := range pathsAny {
			paths = append(paths, pysem.Str(p))
		}
		lower := make(map[string]bool, len(paths))
		for _, p := range paths {
			lower[pysem.Lower(p)] = true
		}
		basenames := map[string]bool{}
		for p := range lower {
			basenames[p[strings.LastIndexByte(p, '/')+1:]] = true
		}
		repoFindings := []any{}
		if !anyPath(basenames, func(name string) bool { return strings.HasPrefix(name, "readme") }) {
			repoFindings = append(repoFindings, newFinding(repo,
				"documentation",
				"Add a repository README",
				"Document the repository purpose, setup, test, and release workflow.",
				"No README was found on the default branch.",
				"high", "small", "high"))
		}
		if !anyPath(lower, func(path string) bool {
			return strings.Contains(path, "architecture") || strings.Contains("/"+path, "/adr/") ||
				strings.HasPrefix(path, "adr/") || strings.HasPrefix(path, "docs/adr/")
		}) {
			repoFindings = append(repoFindings, newFinding(repo,
				"architecture",
				"Record architecture decisions",
				"Add a short architecture overview and ADRs for consequential design choices.",
				"No architecture overview or ADR directory was found.",
				"medium", "small", "medium"))
		}
		if !anyPath(lower, func(path string) bool {
			return strings.HasPrefix(path, "test/") || strings.HasPrefix(path, "tests/") ||
				strings.HasPrefix(path, "__tests__/") || strings.Contains(path, "/test")
		}) {
			repoFindings = append(repoFindings, newFinding(repo,
				"testing",
				"Establish an automated test suite",
				"Add executable tests for the highest-risk paths and run them in CI.",
				"No conventional test directory was found.",
				"high", "medium", "medium"))
		}
		hasCI := anyPath(lower, func(path string) bool {
			return strings.HasPrefix(path, ".github/workflows/") || strings.HasPrefix(path, ".azure-pipelines/")
		}) || lower["azure-pipelines.yml"] || lower[".gitlab-ci.yml"] || lower["jenkinsfile"]
		if !hasCI {
			repoFindings = append(repoFindings, newFinding(repo,
				"delivery",
				"Add continuous integration",
				"Run build, test, and static checks for every proposed change.",
				"No supported CI workflow was found.",
				"high", "medium", "high"))
		}
		hasManifest := false
		for name := range manifests {
			if basenames[name] {
				hasManifest = true
				break
			}
		}
		if !hasManifest {
			repoFindings = append(repoFindings, newFinding(repo,
				"maintainability",
				"Document dependency management",
				"Add or document the canonical build and dependency manifest.",
				"No recognised dependency/build manifest was found.",
				"medium", "small", "medium"))
		}
		if !anyPath(lower, func(path string) bool { return strings.Contains(path, "codeowners") }) {
			repoFindings = append(repoFindings, newFinding(repo,
				"ownership",
				"Define code ownership",
				"Add CODEOWNERS entries for critical areas and review routing.",
				"No CODEOWNERS file was found.",
				"medium", "small", "high"))
		}
		if !anyPath(lower, func(path string) bool {
			return strings.Contains(path, "dependabot") || strings.Contains(path, "renovate")
		}) {
			repoFindings = append(repoFindings, newFinding(repo,
				"security",
				"Automate dependency updates",
				"Configure a dependency update service and require validation before merge.",
				"No Dependabot or Renovate configuration was found; this is an indicator, not a security audit.",
				"medium", "small", "medium"))
		}
		operational := anyPath(lower, func(path string) bool {
			return strings.HasSuffix(path, ".tf") || strings.HasSuffix(path, "dockerfile") ||
				strings.HasSuffix(path, "docker-compose.yml") || strings.HasSuffix(path, "docker-compose.yaml") ||
				strings.Contains(path, "k8s/") || strings.Contains(path, "kubernetes/")
		})
		hasRunbook := anyPath(lower, func(path string) bool {
			return strings.Contains(path, "runbook") || strings.Contains(path, "playbook")
		})
		if operational && !hasRunbook {
			repoFindings = append(repoFindings, newFinding(repo,
				"operations",
				"Add an operational runbook",
				"Document deployment, rollback, verification, alert response, and ownership.",
				"Deployment/infrastructure files exist but no runbook or playbook was found.",
				"high", "medium", "medium"))
		}
		report := pysem.EmptyObj()
		report.Set("provider", repo.GetDefault("provider", ""))
		report.Set("container", repo.GetDefault("container", ""))
		report.Set("repository", repo.GetDefault("name", ""))
		report.Set("url", repo.GetDefault("url", ""))
		report.Set("default_branch", repo.GetDefault("default_branch", ""))
		report.Set("files_scanned", int64(len(paths)))
		if pysem.Truthy(repo.Get("error")) {
			report.Set("status", "truncated")
		} else {
			report.Set("status", "succeeded")
		}
		report.Set("findings", repoFindings)
		reports = append(reports, report)
		findings = append(findings, repoFindings...)
	}
	return reports, findings
}

// actionKey mirrors the (category, title) tuple key of prioritize_actions'
// grouping dict; `any` fields so raw decoded values compare like Python
// tuple elements.
type actionKey struct {
	category, title any
}

// PrioritizeActions ports prioritize_actions — deduplicate estate findings
// into cross-repository, inspectable actions.
func PrioritizeActions(findings []any) []any {
	order := map[string]int{"critical": 0, "high": 1, "medium": 2, "low": 3}
	keyOrder := []actionKey{}
	grouped := map[actionKey][]*pysem.Obj{}
	for _, v := range findings {
		finding := pysem.AsObj(v)
		if finding == nil {
			continue // out of contract: Python would raise on .get
		}
		key := actionKey{category: finding.GetDefault("category", ""), title: finding.GetDefault("title", "")}
		if _, ok := grouped[key]; !ok {
			keyOrder = append(keyOrder, key)
		}
		grouped[key] = append(grouped[key], finding)
	}
	actions := make([]*pysem.Obj, 0, len(keyOrder))
	for _, key := range keyOrder {
		group := grouped[key]
		action := group[0].Clone()
		// Scopes are strings on every producer path in this module; the
		// truthiness filter (`if s`) drops empties.
		scopeSet := map[string]bool{}
		for _, item := range group {
			arr, _ := item.GetDefault("affected_scope", []any{}).([]any)
			for _, s := range arr {
				if str, ok := s.(string); ok && str != "" {
					scopeSet[str] = true
				}
			}
		}
		sortedScopes := pysem.SortedKeys(scopeSet)
		scopes := make([]any, 0, len(sortedScopes))
		for _, s := range sortedScopes {
			scopes = append(scopes, s)
		}
		action.Set("affected_scope", scopes)
		action.Set("breadth", int64(len(scopes)))
		if len(scopes) > 1 {
			action.Set("evidence", fmt.Sprintf("%s Affects %d repositories.",
				pysem.Str(action.Get("evidence")), len(scopes)))
		}
		actions = append(actions, action)
	}
	rank := func(a *pysem.Obj) int {
		if r, ok := order[pysem.Str(a.GetDefault("priority", "low"))]; ok {
			return r
		}
		return 9 // order.get(..., 9): unknown priorities sort last
	}
	sort.SliceStable(actions, func(i, j int) bool {
		ri, rj := rank(actions[i]), rank(actions[j])
		if ri != rj {
			return ri < rj
		}
		bi, bj := intOf(actions[i].GetDefault("breadth", int64(1))), intOf(actions[j].GetDefault("breadth", int64(1)))
		if bi != bj {
			return bi > bj // -breadth: wider actions first
		}
		// Python string < is by code point; byte-wise UTF-8 compare agrees.
		return pysem.Str(actions[i].GetDefault("title", "")) < pysem.Str(actions[j].GetDefault("title", ""))
	})
	out := make([]any, 0, len(actions))
	for _, a := range actions {
		out = append(out, a)
	}
	return out
}

// categoryCounts mirrors dict(sorted(Counter(f.get("category", "") for f in
// findings).items())): counts keyed by category, keys sorted ascending.
func categoryCounts(findings []any) *pysem.Obj {
	counts := map[string]int64{}
	for _, v := range findings {
		f := pysem.AsObj(v)
		if f == nil {
			continue
		}
		counts[pysem.Str(f.GetDefault("category", ""))]++
	}
	out := pysem.EmptyObj()
	for _, k := range pysem.SortedKeys(counts) {
		out.Set(k, counts[k])
	}
	return out
}

// RepositoryHealthSummary ports repository_health_summary.
func RepositoryHealthSummary(reports []any, findings []any) *pysem.Obj {
	var analysed, unchanged, files int64
	for _, v := range reports {
		r := pysem.AsObj(v)
		if r == nil {
			continue
		}
		switch r.Get("status") {
		case "succeeded", "truncated":
			analysed++
		case "unchanged":
			unchanged++
		}
		files += intOf(r.GetDefault("files_scanned", int64(0)))
	}
	out := pysem.EmptyObj()
	out.Set("repositories_analysed", analysed)
	out.Set("repositories_unchanged", unchanged)
	out.Set("files_inventoried", files)
	out.Set("findings", int64(len(findings)))
	out.Set("by_category", categoryCounts(findings))
	return out
}

// fileScope ports _file_scope.
func fileScope(change *pysem.Obj) string {
	return pysem.Str(change.GetDefault("repository", "")) + ":" + pysem.Str(change.GetDefault("path", ""))
}

// isTestPath ports _is_test_path — WHOLE-part matching plus name conventions.
// "src/testing/util.py" is not a test path; the Go-style "store_test.go"
// suffix is deliberately outside its vocabulary, only the "test_" prefix
// counts.
func isTestPath(path string) bool {
	for _, part := range posixParts(path) {
		if testParts[pysem.Lower(part)] {
			return true
		}
	}
	name := pysem.Lower(posixName(path))
	return strings.HasPrefix(name, "test_") || strings.Contains(name, ".test.") || strings.Contains(name, ".spec.")
}

// eligibility ports _eligibility. Check order is contractual: failed, then
// deleted, then binary, then generated/vendored.
func eligibility(change *pysem.Obj) (bool, string) {
	path := pysem.Str(change.GetDefault("path", ""))
	suffix := pysem.Lower(pathSuffix(path))
	status := pysem.Lower(pysem.Str(change.GetDefault("status", "")))
	if status == "failed" {
		return false, pysem.Str(pysem.FirstTruthy(change.GetDefault("error", ""), "change lookup failed"))
	}
	if status == "delete" || status == "deleted" {
		return false, "deleted file"
	}
	if binarySuffixes[suffix] {
		return false, "binary file"
	}
	for _, part := range posixParts(path) {
		if generatedParts[pysem.Lower(part)] {
			return false, "generated or vendored file"
		}
	}
	return true, ""
}

// changeGroupKey mirrors the (provider, repository, change_id) str-tuple key
// of the per-change grouping dict.
type changeGroupKey struct {
	provider, repository, changeID string
}

// AnalyseChangedFiles ports analyse_changed_files — analyse only files
// attributable to selected-user commits or authored PRs. The Python default
// window_days=120 is spelled by the caller.
func AnalyseChangedFiles(changes []any, windowDays int) ([]any, []any, *pysem.Obj) {
	tracker := NewCoverageTracker("code", windowDays)
	reports := []any{}
	findings := []any{}
	eligibleReports := []*pysem.Obj{}
	for _, v := range changes {
		change := pysem.AsObj(v)
		if change == nil {
			continue // out of contract: Python would raise on .get
		}
		ok, reason := eligibility(change)
		provider := pysem.Str(change.GetDefault("provider", ""))
		container := strings.Trim(
			pysem.Str(change.GetDefault("container", ""))+"/"+pysem.Str(change.GetDefault("repository", "")), "/")
		asset := pysem.Str(change.GetDefault("path", ""))
		if !ok {
			status := "unchanged"
			if pysem.Lower(pysem.Str(change.GetDefault("status", ""))) == "failed" {
				status = "failed"
			}
			tracker.Add(provider, container, asset, status, reason, status == "failed")
			report := change.Clone()
			if status == "failed" {
				report.Set("analysis_status", "failed")
			} else {
				report.Set("analysis_status", "excluded")
			}
			report.Set("reason", reason)
			reports = append(reports, report)
			continue
		}
		coverageStatus := "succeeded"
		if pysem.Truthy(change.Get("truncated")) {
			coverageStatus = "truncated"
		}
		tracker.Add(provider, container, asset, coverageStatus, pysem.Str(change.GetDefault("error", "")), true)
		report := change.Clone()
		report.Set("analysis_status", coverageStatus)
		report.Set("reason", pysem.Str(change.GetDefault("error", "")))
		reports = append(reports, report)
		eligibleReports = append(eligibleReports, report)
	}

	// Aggregate repeat touches into file-level hotspots. Counter iterates in
	// first-seen order; the exemplar map is last-write-wins (see header).
	touchOrder := []string{}
	touches := map[string]int{}
	lastByScope := map[string]*pysem.Obj{}
	for _, change := range eligibleReports {
		scope := fileScope(change)
		if _, seen := touches[scope]; !seen {
			touchOrder = append(touchOrder, scope)
		}
		touches[scope]++
		lastByScope[scope] = change
	}
	for _, scope := range touchOrder {
		count := touches[scope]
		change := lastByScope[scope]
		var churn int64
		for _, item := range eligibleReports {
			if fileScope(item) == scope {
				churn += intOf(item.Get("additions")) + intOf(item.Get("deletions"))
			}
		}
		if count >= 3 {
			priority := "medium"
			if count >= 5 {
				priority = "high"
			}
			f := pysem.EmptyObj()
			f.Set("id", scope+":hotspot")
			f.Set("category", "hotspot")
			f.Set("title", "Stabilise a frequently changed file")
			f.Set("detail",
				"Review why this file changes repeatedly and split responsibilities if it has become a bottleneck.")
			f.Set("priority", priority)
			f.Set("impact", "Reduces regression risk in a concentrated change hotspot.")
			f.Set("confidence", change.GetDefault("confidence", "high"))
			f.Set("evidence", fmt.Sprintf("%s changed in %d selected-user changes during the window.", scope, count))
			f.Set("link", change.GetDefault("url", ""))
			f.Set("affected_scope", []any{scope})
			f.Set("next_steps", []any{
				"Review recent changes together.",
				"Extract unstable responsibilities or add focused tests.",
			})
			f.Set("owner_role", "Selected contributor")
			f.Set("effort", "medium")
			f.Set("completion_check",
				"The file has a clear responsibility and regression coverage for its frequently changed paths.")
			findings = append(findings, f)
		}
		if churn >= 500 {
			f := pysem.EmptyObj()
			f.Set("id", scope+":large-change")
			f.Set("category", "change-size")
			f.Set("title", "Break down a large code change")
			f.Set("detail", "Split large changes into independently reviewable units with targeted validation.")
			f.Set("priority", "high")
			f.Set("impact", "Makes review and rollback safer.")
			f.Set("confidence", change.GetDefault("confidence", "high"))
			f.Set("evidence", fmt.Sprintf("%s accumulated %d added/deleted lines.", scope, churn))
			f.Set("link", change.GetDefault("url", ""))
			f.Set("affected_scope", []any{scope})
			f.Set("next_steps", []any{
				"Separate behavioural and mechanical changes.",
				"Add focused tests for each unit.",
			})
			f.Set("owner_role", "Selected contributor")
			f.Set("effort", "small")
			f.Set("completion_check", "Future changes are split into reviewable units with explicit validation.")
			findings = append(findings, f)
		}
	}

	// A selected user's production change should normally carry a test change
	// in the same commit or PR. Whole-PR attribution remains medium confidence.
	changeKeyOrder := []changeGroupKey{}
	byChange := map[changeGroupKey][]*pysem.Obj{}
	for _, change := range eligibleReports {
		key := changeGroupKey{
			provider:   pysem.Str(change.GetDefault("provider", "")),
			repository: pysem.Str(change.GetDefault("repository", "")),
			changeID:   pysem.Str(change.GetDefault("change_id", "")),
		}
		if _, seen := byChange[key]; !seen {
			changeKeyOrder = append(changeKeyOrder, key)
		}
		byChange[key] = append(byChange[key], change)
	}
	for _, key := range changeKeyOrder {
		group := byChange[key]
		production := []*pysem.Obj{}
		for _, item := range group {
			path := pysem.Str(item.GetDefault("path", ""))
			if !isTestPath(path) && !docFileSuffixes[pysem.Lower(pathSuffix(path))] {
				production = append(production, item)
			}
		}
		hasTestChange := false
		for _, item := range group {
			if isTestPath(pysem.Str(item.GetDefault("path", ""))) {
				hasTestChange = true
				break
			}
		}
		if len(production) == 0 || hasTestChange {
			continue
		}
		scopeSet := map[string]bool{}
		for _, item := range production {
			scopeSet[fileScope(item)] = true
		}
		sortedScopes := pysem.SortedKeys(scopeSet)
		scopes := make([]any, 0, len(sortedScopes))
		for _, s := range sortedScopes {
			scopes = append(scopes, s)
		}
		exemplar := production[0]
		f := pysem.EmptyObj()
		f.Set("id", key.repository+":"+key.changeID+":tests")
		f.Set("category", "testing")
		f.Set("title", "Add tests alongside production changes")
		f.Set("detail", "Add or update focused tests in the same commit or PR as the behavioural change.")
		f.Set("priority", "high")
		f.Set("impact", "Makes selected-user changes safer to review and release.")
		f.Set("confidence", exemplar.GetDefault("confidence", "high"))
		f.Set("evidence", fmt.Sprintf("%d production file(s) changed without a test-file change.", len(scopes)))
		f.Set("link", exemplar.GetDefault("url", ""))
		f.Set("affected_scope", scopes)
		f.Set("next_steps", []any{
			"Identify the changed behaviour.",
			"Add a regression test that fails without the change.",
		})
		f.Set("owner_role", "Selected contributor")
		f.Set("effort", "small")
		f.Set("completion_check", "The change has an automated test covering its intended behaviour.")
		findings = append(findings, f)
	}

	return reports, findings, tracker.AsDict()
}

// ChangedFileSummary ports changed_file_summary.
func ChangedFileSummary(reports []any, findings []any) *pysem.Obj {
	eligible := []*pysem.Obj{}
	var excluded, failed int64
	for _, v := range reports {
		r := pysem.AsObj(v)
		if r == nil {
			continue
		}
		switch r.Get("analysis_status") {
		case "succeeded", "truncated":
			eligible = append(eligible, r)
		case "excluded":
			excluded++
		case "failed":
			failed++
		}
	}
	// Repositories are strings on every producer path; the truthiness filter
	// drops empties before the distinct count.
	repos := map[string]bool{}
	var authoredCommit, authoredPR int64
	for _, r := range eligible {
		if repo, ok := r.Get("repository").(string); ok && repo != "" {
			repos[repo] = true
		}
		switch r.Get("attribution") {
		case "authored_commit":
			authoredCommit++
		case "authored_pr":
			authoredPR++
		}
	}
	out := pysem.EmptyObj()
	out.Set("files_analysed", int64(len(eligible)))
	out.Set("files_excluded", excluded)
	out.Set("files_failed", failed)
	out.Set("repositories_touched", int64(len(repos)))
	out.Set("authored_commit_files", authoredCommit)
	out.Set("authored_pr_files", authoredPR)
	out.Set("findings", int64(len(findings)))
	out.Set("by_category", categoryCounts(findings))
	return out
}
