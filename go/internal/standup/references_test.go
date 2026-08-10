package standup

// references_test.go — assertion tables ported from
// tests/unit/test_standup_references.py, plus golden cases for the
// unicode-boundary traps the RE2 post-filters exist for. The gate is the
// point of this module — ticket-*shaped* text is not evidence of a ticket —
// so most of these pin what must NOT be recognised.

import (
	"reflect"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// refsItem builds an activity item with keys set in the given order.
func refsItem(kv ...any) *pysem.Obj {
	o := pysem.EmptyObj()
	for i := 0; i+1 < len(kv); i += 2 {
		o.Set(kv[i].(string), kv[i+1])
	}
	return o
}

func refsAssertStrs(t *testing.T, got, want []string) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func refsAssertSet(t *testing.T, got map[string]bool, want map[string]bool) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

// --- TestTicketKeys -------------------------------------------------------

func TestFindsJiraShapedKeys(t *testing.T) {
	refsAssertStrs(t, findTicketKeys("Fixes PSOT-12 and ACME-3"), []string{"PSOT-12", "ACME-3"})
}

func TestLongerKeyNeverHalfMatches(t *testing.T) {
	refsAssertStrs(t, findTicketKeys("PSOT-123"), []string{"PSOT-123"})
}

func TestPrefixesOfSplitsOnTheFirstDash(t *testing.T) {
	refsAssertSet(t, prefixesOf([]string{"PSOT-12", "PSOT-3", "ACME-1"}), map[string]bool{"PSOT": true, "ACME": true})
}

func TestLookalikesAreGatedOutWithoutThePrefix(t *testing.T) {
	for _, text := range []string{"UTF-8", "SHA-256", "ISO-8601", "HTTP-2"} {
		// They DO match the raw regex — that is exactly why the gate exists.
		if len(findTicketKeys(text)) == 0 {
			t.Fatalf("expected raw match for %q", text)
		}
		refsAssertStrs(t, gatedTicketKeys(text, map[string]bool{"PSOT": true}), []string{})
	}
}

func TestLookalikePassesWhenTheTrackerReallyUsesThatPrefix(t *testing.T) {
	// A project genuinely called UTF is not this module's problem to guess at.
	refsAssertStrs(t, gatedTicketKeys("UTF-8", map[string]bool{"UTF": true}), []string{"UTF-8"})
}

// --- TestTrackerGates -----------------------------------------------------

func TestPrefixesComeOnlyFromTrackerKinds(t *testing.T) {
	items := []*pysem.Obj{
		refsItem("kind", "issue", "key", "PSOT-1"),
		refsItem("kind", "commit", "key", "DEAD-1"), // a sha-ish key must not widen the gate
		refsItem("kind", "pr", "key", "#91"),
	}
	refsAssertSet(t, trackerPrefixes(items), map[string]bool{"PSOT": true})
}

func TestWorkItemIDsComeOnlyFromAzureBoardsKinds(t *testing.T) {
	items := []*pysem.Obj{
		refsItem("kind", "work_item", "key", "#1234"),
		refsItem("kind", "wip", "key", "#77"),
		refsItem("kind", "pr", "key", "#91"), // a GitHub PR number is not a work item
		refsItem("kind", "issue", "key", "PSOT-1"),
	}
	refsAssertSet(t, trackerWorkItemIDs(items), map[string]bool{"1234": true, "77": true})
}

// --- TestHasTrackerReference ----------------------------------------------

func TestGatedJiraKeyCounts(t *testing.T) {
	if !hasTrackerReference([]string{"feature/PSOT-12-retry"}, map[string]bool{"PSOT": true}, nil) {
		t.Fatal("gated key should count")
	}
	if hasTrackerReference([]string{"feature/PSOT-12-retry"}, map[string]bool{"ACME": true}, nil) {
		t.Fatal("wrong prefix must not count")
	}
}

func TestAzdoABSyntaxIsUngated(t *testing.T) {
	// AB#123 spells its own evidence; nothing else uses that syntax.
	if !hasTrackerReference([]string{"Fixes AB#1234"}, nil, nil) {
		t.Fatal("AB#1234 should count ungated")
	}
	if !hasTrackerReference([]string{"fixes ab#1234"}, nil, nil) {
		t.Fatal("ab#1234 should count case-insensitively")
	}
}

func TestABPrefixNeedsABoundary(t *testing.T) {
	if hasTrackerReference([]string{"LAB#1234"}, nil, nil) {
		t.Fatal("LAB#1234 must not count")
	}
}

func TestBareHashNeedsAMatchingWorkItemID(t *testing.T) {
	if hasTrackerReference([]string{"Closes #91"}, nil, nil) {
		t.Fatal("#91 without a work-item id must not count")
	}
	if !hasTrackerReference([]string{"Closes #91"}, nil, map[string]bool{"91": true}) {
		t.Fatal("#91 with a matching work-item id should count")
	}
}

func TestBareHashOnAGitHubOnlySetupNeverCounts(t *testing.T) {
	// The empty id set is what stops a PR number reading as a work item.
	if hasTrackerReference([]string{"Merge (#91)"}, nil, map[string]bool{}) {
		t.Fatal("#91 must not count on a GitHub-only setup")
	}
}

func TestEmptyTextsAreNotEvidence(t *testing.T) {
	if hasTrackerReference([]string{"", ""}, map[string]bool{"PSOT": true}, nil) {
		t.Fatal("empty texts must not count")
	}
}

// --- TestIsTrackerKind ----------------------------------------------------

func TestTrackerKindsAreTickets(t *testing.T) {
	for _, kind := range []string{"issue", "wip", "work_item", "update", "comment", "ticket_context"} {
		if !isTrackerKind(kind) {
			t.Fatalf("%q should be a tracker kind", kind)
		}
	}
}

func TestChangesAreNotTickets(t *testing.T) {
	for _, kind := range []string{"commit", "pr", "review", "page", ""} {
		if isTrackerKind(kind) {
			t.Fatalf("%q must not be a tracker kind", kind)
		}
	}
}

// --- TestDisplayTicketKeys ------------------------------------------------
// The naming twin of hasTrackerReference: same gates, but returns the keys.

func TestDisplayJiraKeysArePrefixGated(t *testing.T) {
	psot := map[string]bool{"PSOT": true}
	refsAssertStrs(t, displayTicketKeys("PSOT-12 fix login", "", "", psot, nil, nil), []string{"PSOT-12"})
	// "UTF-8" is ticket-shaped; the gate is what keeps it from becoming a claim.
	refsAssertStrs(t, displayTicketKeys("Support UTF-8 PSOT-12", "", "", psot, nil, nil), []string{"PSOT-12"})
	refsAssertStrs(t, displayTicketKeys("PSOT-12 fix login", "", "", map[string]bool{"ACME": true}, nil, nil), []string{})
}

func TestDisplayAzdoABSyntaxIsUngatedAndSpelledLikeEvidenceKeys(t *testing.T) {
	refsAssertStrs(t, displayTicketKeys("Fixes ab#123", "", "", nil, nil, nil), []string{"#123"})
}

func TestDisplayBareHashIsIDGated(t *testing.T) {
	refsAssertStrs(t, displayTicketKeys("Closes #91", "", "", nil, nil, nil), []string{})
	refsAssertStrs(t, displayTicketKeys("Closes #91", "", "", nil, map[string]bool{"91": true}, nil), []string{"#91"})
}

func TestDisplayFirstPartyLinkedIDsAreAppended(t *testing.T) {
	refsAssertStrs(t, displayTicketKeys("no refs here", "", "", nil, nil, []string{"1234"}), []string{"#1234"})
}

func TestDisplayOrderedDedupeAcrossTextsAndLinks(t *testing.T) {
	keys := displayTicketKeys(
		"PSOT-12 then AB#77",
		"feature/PSOT-12-retry",
		"",
		map[string]bool{"PSOT": true},
		map[string]bool{"77": true},
		[]string{"77", "88"},
	)
	refsAssertStrs(t, keys, []string{"PSOT-12", "#77", "#88"})
}

// --- TestPullRequestClaims ------------------------------------------------

func TestPRReference(t *testing.T) {
	cases := []struct {
		subject  string
		expected string
	}{
		{"Merge pull request #91 from acme/feature", "91"},
		{"Merge pull request 48806 from acme/x", "48806"},
		{"Merged PR 123: Add retry", "123"},
		{"fix the login redirect (#91)", "91"},
		{"fix the login redirect (PR #91)", "91"},
		{"just a normal commit", ""},
	}
	for _, c := range cases {
		if got := prReference(c.subject); got != c.expected {
			t.Fatalf("prReference(%q) = %q, want %q", c.subject, got, c.expected)
		}
	}
}

func TestMergeSourceBranch(t *testing.T) {
	if got := mergeSourceBranch("Merge pull request #91 from acme/feature"); got != "acme/feature" {
		t.Fatalf("got %q", got)
	}
	if got := mergeSourceBranch("regular commit"); got != "" {
		t.Fatalf("got %q", got)
	}
}

func TestClaimsPullRequestIsTrueWithoutAParentInWindow(t *testing.T) {
	// The habit rules need the weaker fact than _nest_pr_commits does: a
	// merge subject is plumbing whether or not its PR was collected.
	if !claimsPullRequest("Merge pull request #99999 from acme/x") {
		t.Fatal("expected a PR claim")
	}
	if claimsPullRequest("add the SAML config") {
		t.Fatal("plain subject must not claim a PR")
	}
}

// --- TestNormalizeCommitSubject -------------------------------------------

func TestStripsCollectorTails(t *testing.T) {
	cases := []struct {
		raw      string
		expected string
	}{
		{"wip (PR #4)", "wip"},
		{"wip (my-repo)", "wip"},
		{"wip (my-repo) (PR #4)", "wip"},
		{"fix the retry loop", "fix the retry loop"},
		{"", ""},
	}
	for _, c := range cases {
		if got := normalizeCommitSubject(c.raw); got != c.expected {
			t.Fatalf("normalizeCommitSubject(%q) = %q, want %q", c.raw, got, c.expected)
		}
	}
}

// --- TestExportParity -----------------------------------------------------
// The refactor must not have changed what export.py's key map admits.

func TestMatchesTheOldInlineLoop(t *testing.T) {
	prose := "Shipped PSOT-12 using UTF-8 encoding, tracked in ACME-3 and SHA-256 hashed."
	known := map[string]bool{"PSOT": true, "ACME": true}
	refsAssertStrs(t, gatedTicketKeys(prose, known), []string{"PSOT-12", "ACME-3"})
}

// --- TestMergeSubjectIsNarrowerThanPrClaim --------------------------------
// The two gates the habit rules need, and why they are not one gate: a
// squash-merge subject ends "(#91)" and the collector appends " (PR #91)" —
// both belong to a PR, but both are authored subjects, so a rule about
// message quality must still judge them.

func TestRealMergeSubjectsAreBoth(t *testing.T) {
	for _, subject := range []string{"Merge pull request #91 from acme/x", "Merged PR 123: Add retry"} {
		if !claimsPullRequest(subject) {
			t.Fatalf("%q should claim a PR", subject)
		}
		if !isMergeSubject(subject) {
			t.Fatalf("%q should be a merge subject", subject)
		}
	}
}

func TestParenthesisedReferencesClaimAPRButAreNotMerges(t *testing.T) {
	for _, subject := range []string{"fix login (#91)", "wip (PR #91)"} {
		if !claimsPullRequest(subject) {
			t.Fatalf("%q should claim a PR", subject)
		}
		if isMergeSubject(subject) {
			t.Fatalf("%q must not be a merge subject", subject)
		}
	}
}

func TestBranchSyncMergesAreMergesButClaimNoPR(t *testing.T) {
	// git wrote these subjects, not the author: no rule should judge them —
	// but they name no PR, so nesting under a PR parent stays impossible.
	for _, subject := range []string{
		"Merge branch 'main' into feature-x",
		"Merge remote-tracking branch 'origin/master' into psot/jenkins-governance-plugins",
	} {
		if !isMergeSubject(subject) {
			t.Fatalf("%q should be a merge subject", subject)
		}
		if claimsPullRequest(subject) {
			t.Fatalf("%q must not claim a PR", subject)
		}
	}
}

func TestAPlainSubjectIsNeither(t *testing.T) {
	if claimsPullRequest("Add the SAML config") {
		t.Fatal("plain subject must not claim a PR")
	}
	if isMergeSubject("Add the SAML config") {
		t.Fatal("plain subject must not be a merge subject")
	}
	// "branch" mid-sentence is not a merge commit.
	if isMergeSubject("Fix the merge branch selector UI") {
		t.Fatal("mid-sentence 'branch' must not be a merge subject")
	}
}

// --- Golden unicode-boundary traps ----------------------------------------
// Python's \b and \w are unicode; RE2's are ASCII. These pin the post-filters
// that close the gap.

func TestUnicodeWordCharBlocksTicketKeyStart(t *testing.T) {
	// é is a unicode word char: Python sees no \b before P, so no match —
	// even though RE2's ASCII \b matches and must be filtered out.
	refsAssertStrs(t, findTicketKeys("éPROJ-12"), []string{})
}

func TestUnicodeWordCharBlocksTicketKeyEnd(t *testing.T) {
	refsAssertStrs(t, findTicketKeys("PROJ-12é"), []string{})
}

func TestAzdoLookbehindIsASCIIOnly(t *testing.T) {
	// The lookbehind class is exactly ASCII [A-Za-z0-9], NOT unicode \w:
	// underscore and é both pass it in Python.
	refsAssertStrs(t, azdoRefIDs("_AB#12"), []string{"12"})
	refsAssertStrs(t, azdoRefIDs("éAB#12"), []string{"12"})
	if !hasTrackerReference([]string{"_AB#12"}, nil, nil) {
		t.Fatal("_AB#12 should count")
	}
	if !hasTrackerReference([]string{"éAB#12"}, nil, nil) {
		t.Fatal("éAB#12 should count")
	}
}

func TestAzdoTrailingBoundaryIsUnicode(t *testing.T) {
	// The trailing \b IS unicode: a unicode word char after the digits kills
	// the match in Python, so it must here too.
	refsAssertStrs(t, azdoRefIDs("AB#12é"), []string{})
}

func TestBareIDLookbehindIsUnicode(t *testing.T) {
	// BARE_ID_RE's lookbehind class IS unicode \w (plus '#'): é fails it, so
	// "é#12" never yields a reference — even with the id in the gate.
	refsAssertStrs(t, bareRefIDs("é#12"), []string{})
	if hasTrackerReference([]string{"é#12"}, nil, map[string]bool{"12": true}) {
		t.Fatal("é#12 must not count")
	}
}

func TestBareIDLookbehindExcludesHashAndWord(t *testing.T) {
	refsAssertStrs(t, bareRefIDs("##12"), []string{})
	refsAssertStrs(t, bareRefIDs("utf#8"), []string{})
	refsAssertStrs(t, bareRefIDs("v1.2#3"), []string{})
	refsAssertStrs(t, bareRefIDs("Closes #91"), []string{"91"})
}

func TestBranchSyncTrailingBoundaryIsUnicode(t *testing.T) {
	// "branché" — 'h' and 'é' are both unicode word chars, so Python's \b
	// after "branch" fails and this is not a merge subject.
	if isMergeSubject("Merge branché 'main' into x") {
		t.Fatal("Merge branché must not be a merge subject")
	}
	if isMergeSubject("Merge branches cleanly") {
		t.Fatal("'branches' must not satisfy the \\b after 'branch'")
	}
}

// --- Rejected spans must not hide later matches ---------------------------
// The post-filter runs after a superset RE2 scan; a rejected span is skipped,
// which is only safe because no Python-valid match can start inside one (the
// interiors offer no valid start position). These pin that scanning continues
// correctly past rejected spans.

func TestRejectedTicketKeyDoesNotBlockALaterOne(t *testing.T) {
	refsAssertStrs(t, findTicketKeys("éPROJ-12 PSOT-3"), []string{"PSOT-3"})
	refsAssertStrs(t, findTicketKeys("PROJ-12é ACME-4 more"), []string{"ACME-4"})
}

func TestRejectedAzdoRefDoesNotBlockALaterOne(t *testing.T) {
	refsAssertStrs(t, azdoRefIDs("xAB#12 AB#34"), []string{"34"})
}

func TestRejectedBareIDDoesNotBlockALaterOne(t *testing.T) {
	refsAssertStrs(t, bareRefIDs("utf#8 #91"), []string{"91"})
	// "#12#34": the first is valid; the second's lookbehind sees the '2'
	// (a \w) and fails, in Python exactly as here.
	refsAssertStrs(t, bareRefIDs("#12#34"), []string{"12"})
}
