package standup

// relatedness_test.go — port of tests/unit/test_standup_relatedness.py, plus
// golden cases pinned against the Python reference (the hand-rolled camel
// split, pysem.Lower("İstanbul-FIX") through tokenization, and a near_misses
// tie broken the way Python breaks it).
//
// One block per predicate, and for each: it matches the real thing, it stays
// quiet on the near-miss, and it stays quiet on the specific false positive
// the gate exists for. The polarity is inverted from most tests in this repo —
// a match SUPPRESSES a report — so a missed match costs a nudge and a wrong
// match costs a person being told their work is unapproved scope.

import (
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// relItem builds an ordered item from key/value pairs.
func relItem(kv ...any) *pysem.Obj {
	item := pysem.EmptyObj()
	for i := 0; i+1 < len(kv); i += 2 {
		item.Set(kv[i].(string), kv[i+1])
	}
	return item
}

// relTicketItem mirrors the Python test helper _ticket.
func relTicketItem(key, title, body string, over ...any) *pysem.Obj {
	item := relItem(
		"kind", "issue",
		"key", key,
		"title", title,
		"status", "In Progress",
		"source", "jira",
		"url", "https://j/browse/"+key,
		"timestamp", "2026-08-01T09:00:00",
		"body", body,
	)
	for i := 0; i+1 < len(over); i += 2 {
		item.Set(over[i].(string), over[i+1])
	}
	return item
}

// relCommitItem mirrors the Python test helper _commit.
func relCommitItem(title string, over ...any) *pysem.Obj {
	item := relItem(
		"kind", "commit",
		"key", "a1b2c3d4",
		"title", title,
		"body", "",
		"branch", "",
		"repository", "acme/web",
		"url", "https://g/acme/web/commit/a1b2c3d4",
		"changed_paths", []any{},
		"pr_id", "",
	)
	for i := 0; i+1 < len(over); i += 2 {
		item.Set(over[i].(string), over[i+1])
	}
	return item
}

// relMatches mirrors the Python test helper _matches.
func relMatches(change *pysem.Obj, tickets []*pysem.Obj, own bool, docsOnly bool) bool {
	corpus := buildCorpus(tickets, nil)
	var ownKeys map[string]bool
	if own {
		ownKeys = ticketKeys(tickets)
	}
	profile := buildChangeProfileOpts(change, docsOnly)
	return relatesToTicket(profile, corpus, ownKeys)
}

func relSorted(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// The case that prompted the feature.
func relRealTicket() *pysem.Obj {
	return relTicketItem(
		"PSOT-77",
		"Rename the approval plugins",
		"We agreed the pipeline approval plugin and the access request plugin should use their new "+
			"names everywhere.\n\nDefinition of done:\n- [ ] Documentation\n- [ ] Proper Testing",
	)
}

func relRealCommit() *pysem.Obj {
	return relCommitItem("Rename the plugins to pipeline-approval and access-request", "key", "bf132e43")
}

func TestReportedCase(t *testing.T) {
	t.Run("a commit matches the ticket that describes it", func(t *testing.T) {
		if !relMatches(relRealCommit(), []*pysem.Obj{relRealTicket()}, true, false) {
			t.Fatal("expected the reported case to match")
		}
	})
	t.Run("an unrelated ticket does not rescue it", func(t *testing.T) {
		unrelated := relTicketItem("PSOT-90", "Migrate the billing schema", "Update the billing tables and backfill.")
		if relMatches(relRealCommit(), []*pysem.Obj{unrelated}, true, false) {
			t.Fatal("unrelated ticket must not match")
		}
	})
	t.Run("one generic word in common is not a match", func(t *testing.T) {
		vague := relTicketItem("PSOT-91", "Quarterly cleanup", "Some of the plugins may need a look at some point.")
		if relMatches(relRealCommit(), []*pysem.Obj{vague}, true, false) {
			t.Fatal("one generic word must not match")
		}
	})
}

func TestBackReference(t *testing.T) {
	t.Run("the ticket pasting the url matches", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Something", "Shipped as https://g/acme/web/pull/91 today.")
		change := relCommitItem("Do a thing", "kind", "pr", "key", "#91", "url", "https://g/acme/web/pull/91")
		if !relMatches(change, []*pysem.Obj{ticket}, false, false) { // strong enough for a teammate's ticket
			t.Fatal("url back-reference must match in tier B")
		}
	})
	t.Run("a trailing slash or query still matches", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Something", "See https://g/acme/web/pull/91/?utm=slack")
		change := relCommitItem("Do a thing", "kind", "pr", "key", "#91", "url", "https://g/acme/web/pull/91")
		if !relMatches(change, []*pysem.Obj{ticket}, false, false) {
			t.Fatal("normalized url must match")
		}
	})
	t.Run("a full sha in the ticket matches", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Something", "Fixed in a1b2c3d4 yesterday.")
		if !relMatches(relCommitItem("Do a thing", "key", "a1b2c3d4"), []*pysem.Obj{ticket}, false, false) {
			t.Fatal("sha back-reference must match")
		}
	})
	t.Run("a short hex string is not a sha", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Something", "Colour is a1b2 in the palette.")
		if relMatches(relCommitItem("Do a thing", "key", "a1b2"), []*pysem.Obj{ticket}, false, false) {
			t.Fatal("short hex must not match")
		}
	})
	t.Run("a bare number needs the repository named too", func(t *testing.T) {
		// "#91" is a PR number on GitHub and a work-item id on Azure Boards —
		// the ambiguity references.py exists for.
		without := relTicketItem("A-1", "Something", "Blocked by #91 for now.")
		withRepo := relTicketItem("A-1", "Something", "Blocked by #91 in web for now.")
		change := relCommitItem("Do a thing", "kind", "pr", "key", "#91", "pr_id", "91", "url", "", "repository", "acme/web")
		if relMatches(change, []*pysem.Obj{without}, false, false) {
			t.Fatal("bare number without the repo named must not match")
		}
		if !relMatches(change, []*pysem.Obj{withRepo}, false, false) {
			t.Fatal("bare number with the repo named must match")
		}
	})
}

func TestIdentifiers(t *testing.T) {
	t.Run("a rare compound matches the ticket prose that spells it out", func(t *testing.T) {
		// The asymmetry that makes this work: the change wrote it as one
		// token, the ticket wrote it as two words.
		ticket := relTicketItem("A-1", "Plugin rename", "The pipeline approval plugin needs its new name.")
		if !relMatches(relCommitItem("Rename pipeline-approval"), []*pysem.Obj{ticket}, false, false) {
			t.Fatal("compound identifier must match ticket prose")
		}
	})
	t.Run("separator and case variants are the same identifier", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Plugin rename", "The pipeline approval plugin needs its new name.")
		for _, spelling := range []string{"pipeline_approval", "PipelineApproval", "pipeline.approval", "pipeline/approval"} {
			if !relMatches(relCommitItem("Rename "+spelling), []*pysem.Obj{ticket}, false, false) {
				t.Fatalf("spelling %q must match", spelling)
			}
		}
	})
	t.Run("short compounds are not identifiers", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Locale work", "Set en US as the default and bump to v 2.")
		if relMatches(relCommitItem("Set en-US and v-2"), []*pysem.Obj{ticket}, false, false) {
			t.Fatal("short compounds must not match")
		}
	})
	t.Run("a common identifier across the corpus is not rare", func(t *testing.T) {
		tickets := []*pysem.Obj{}
		for i := 0; i < 8; i++ {
			tickets = append(tickets, relTicketItem(
				fmt.Sprintf("A-%d", i), fmt.Sprintf("Piece %d", i), "Touches the pipeline approval plugin."))
		}
		if relMatches(relCommitItem("Rename pipeline-approval"), tickets, false, false) {
			t.Fatal("a corpus-wide identifier must not match")
		}
	})
}

func TestBranchSlug(t *testing.T) {
	t.Run("a workflow namespace is stripped", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Webhook retry backoff", "Add retry and backoff to the webhook sender.")
		if !relMatches(relCommitItem("Assorted", "branch", "feature/retry-backoff-webhook"), []*pysem.Obj{ticket}, true, false) {
			t.Fatal("workflow namespace must be stripped")
		}
	})
	t.Run("an author segment is stripped too", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Webhook retry backoff", "Add retry and backoff to the webhook sender.")
		if !relMatches(relCommitItem("Assorted", "branch", "users/alice/retry-backoff-webhook"), []*pysem.Obj{ticket}, true, false) {
			t.Fatal("author segment must be stripped")
		}
	})
	t.Run("a placeholder branch names nothing", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Webhook retry backoff", "Add retry and backoff to the webhook sender.")
		for _, branch := range []string{"patch-1", "dev", "alice/wip"} {
			if relMatches(relCommitItem("Assorted", "branch", branch), []*pysem.Obj{ticket}, true, false) {
				t.Fatalf("placeholder branch %q must not match", branch)
			}
		}
	})
}

func TestSubjectWords(t *testing.T) {
	t.Run("a matching title is enough", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Add retry and backoff to the webhook sender", "")
		if !relMatches(relCommitItem("Add retry and backoff to the webhook sender"), []*pysem.Obj{ticket}, true, false) {
			t.Fatal("a matching title must match")
		}
	})
	t.Run("words alone never reach a teammates ticket", func(t *testing.T) {
		// Tier B admits only the strong predicates: a lead pushing on someone
		// else's ticket is covered by identifiers, not by vocabulary.
		ticket := relTicketItem("A-1", "Add retry and backoff to the webhook sender", "")
		if relMatches(relCommitItem("Add retry and backoff to the webhook sender"), []*pysem.Obj{ticket}, false, false) {
			t.Fatal("word overlap must not reach tier B")
		}
	})
	t.Run("words alone cannot match a huge ticket", func(t *testing.T) {
		filler := make([]string, 0, 320)
		for i := 0; i < 320; i++ {
			filler = append(filler, fmt.Sprintf("topic%d", i))
		}
		ticket := relTicketItem("A-1", "Platform", strings.Join(filler, " ")+" retry backoff webhook sender")
		if relMatches(relCommitItem("Retry backoff webhook sender rewrite"), []*pysem.Obj{ticket}, true, false) {
			t.Fatal("word overlap must be inadmissible against a huge ticket")
		}
	})
	t.Run("definition of done boilerplate matches nothing", func(t *testing.T) {
		// The failure mode this module was designed against: a DoD block is
		// copied onto every ticket, so it must self-cancel through rarity.
		tickets := []*pysem.Obj{}
		for i := 0; i < 8; i++ {
			tickets = append(tickets, relTicketItem(
				fmt.Sprintf("A-%d", i), fmt.Sprintf("Piece %d", i),
				"Definition of done:\n- [ ] Documentation\n- [ ] Proper Testing\n- [ ] Code Merged to Main"))
		}
		if relMatches(relCommitItem("Update documentation and testing"), tickets, true, false) {
			t.Fatal("DoD boilerplate must not match")
		}
	})
}

func TestChangedPaths(t *testing.T) {
	t.Run("two rare path tokens match", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Standup habits", "Rework how standup habits are detected.")
		change := relCommitItem("Assorted", "changed_paths", []any{"src/yeaboi/standup/habits.py"})
		if !relMatches(change, []*pysem.Obj{ticket}, true, false) {
			t.Fatal("two rare path tokens must match")
		}
	})
	t.Run("one shared path token is not evidence", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Session expiry", "Sessions should expire after an hour.")
		change := relCommitItem("Assorted", "changed_paths", []any{"src/auth/session.py"})
		if relMatches(change, []*pysem.Obj{ticket}, true, false) {
			t.Fatal("one shared path token must not match")
		}
	})
	t.Run("unknown paths never match and never crash", func(t *testing.T) {
		// Empty means UNKNOWN — the collectors cap detail lookups — so it must
		// contribute nothing in either direction.
		ticket := relTicketItem("A-1", "Standup habits", "Rework how standup habits are detected.")
		if relMatches(relCommitItem("Assorted", "changed_paths", []any{}), []*pysem.Obj{ticket}, true, false) {
			t.Fatal("empty paths must not match")
		}
	})
	t.Run("generic basenames never match alone", func(t *testing.T) {
		ticket := relTicketItem("A-1", "Index rewrite", "Rewrite the index and the utils module.")
		change := relCommitItem("Assorted", "changed_paths", []any{"src/index.ts", "src/utils.ts"})
		if relMatches(change, []*pysem.Obj{ticket}, true, false) {
			t.Fatal("generic basenames must not match")
		}
	})
}

func TestDocumentationCarveOut(t *testing.T) {
	dod := func() *pysem.Obj {
		return relTicketItem("A-1", "Checkout resilience",
			"Add retry and backoff on the checkout call.\nDefinition of done:\n- [ ] Documentation")
	}
	noDoD := func() *pysem.Obj {
		return relTicketItem("A-1", "Checkout resilience", "Add retry and backoff on the checkout call.")
	}
	docs := func() *pysem.Obj {
		return relCommitItem("Document retry and backoff behaviour", "changed_paths", []any{"docs/guide.md"})
	}

	t.Run("docs match a ticket whose definition of done covers them", func(t *testing.T) {
		if !relMatches(docs(), []*pysem.Obj{dod()}, true, true) {
			t.Fatal("docs must match a ticket that asked for docs")
		}
	})
	t.Run("the same docs do not match a ticket that never asked for them", func(t *testing.T) {
		// The relaxation is gated on positive evidence: it is not "docs get a
		// pass", it is "docs get a pass against a ticket that said docs".
		if relMatches(docs(), []*pysem.Obj{noDoD()}, true, true) {
			t.Fatal("docs must not get a discount from a ticket without a docs DoD")
		}
	})
	t.Run("a code change gets no discount from the same ticket", func(t *testing.T) {
		if relMatches(docs(), []*pysem.Obj{dod()}, true, false) {
			t.Fatal("a non-docs change must not get the docs discount")
		}
	})
	t.Run("prose mentioning documentation is not a definition of done", func(t *testing.T) {
		prose := relTicketItem("A-1", "Checkout resilience", "Add retry and backoff; this is documented elsewhere.")
		if relMatches(docs(), []*pysem.Obj{prose}, true, true) {
			t.Fatal("prose documentation mention must not open the carve-out")
		}
	})
}

func TestCorpus(t *testing.T) {
	t.Run("text is merged across items sharing a key", func(t *testing.T) {
		// kind does not predict which item carries the body: changelog and
		// comment items name the same ticket and carry no description at all.
		corpus := buildCorpus([]*pysem.Obj{
			relItem("kind", "update", "key", "A-1", "summary", "Rename the plugins", "title", "moved A-1", "body", ""),
			relTicketItem("A-1", "Rename the plugins", "The pipeline approval plugin needs a new name."),
		}, nil)
		if !corpus.tickets["A-1"].idents["pipeline-approval"] {
			t.Fatal("merged ticket must carry the body's identifier")
		}
	})
	t.Run("a ticket with no text is dropped", func(t *testing.T) {
		corpus := buildCorpus([]*pysem.Obj{relTicketItem("A-1", "", "")}, nil)
		if len(corpus.tickets) != 0 || len(corpus.order) != 0 {
			t.Fatal("a textless ticket must be dropped")
		}
	})
	t.Run("an empty corpus matches nothing", func(t *testing.T) {
		corpus := buildCorpus(nil, nil)
		if corpus.truthy() {
			t.Fatal("empty corpus must be falsy")
		}
		if relatesToTicket(buildChangeProfile(relRealCommit()), corpus, nil) {
			t.Fatal("empty corpus must match nothing")
		}
		if got := nearMisses(buildChangeProfile(relRealCommit()), corpus, nil, 3); len(got) != 0 {
			t.Fatalf("empty corpus must offer no near misses, got %v", got)
		}
	})
	t.Run("candidate keys come from every tracker kind", func(t *testing.T) {
		// Jira's WIP query skips issues the main search already returned, so
		// an actively working member has ZERO kind=="wip" items. Selecting on
		// that kind would empty the pool for exactly the busiest people.
		items := []*pysem.Obj{
			relTicketItem("A-1", "One", ""),
			relItem("kind", "wip", "key", "A-2"),
			relItem("kind", "comment", "key", "A-3"),
		}
		got := relSorted(ticketKeys(items))
		if !reflect.DeepEqual(got, []string{"A-1", "A-2", "A-3"}) {
			t.Fatalf("ticketKeys = %v", got)
		}
	})
	t.Run("reference tickets join the same corpus", func(t *testing.T) {
		// An open ticket nobody touched today still has to be able to claim a
		// commit, which is the largest source of false untracked-work reports.
		corpus := buildCorpus(nil, []*pysem.Obj{relTicketItem("A-1", "Rename plugins", "The pipeline approval plugin.")})
		if _, ok := corpus.tickets["A-1"]; !ok {
			t.Fatal("reference tickets must join the corpus")
		}
	})
	t.Run("the shortlist fields habits reads are populated", func(t *testing.T) {
		corpus := buildCorpus([]*pysem.Obj{relTicketItem("A-1", "Rename plugins", "The pipeline approval plugin.")}, nil)
		ticket := corpus.tickets["A-1"]
		if ticket.title != "Rename plugins" || ticket.text != "The pipeline approval plugin." {
			t.Fatalf("title/text = %q / %q", ticket.title, ticket.text)
		}
		if !reflect.DeepEqual(corpus.order, []string{"A-1"}) {
			t.Fatalf("order = %v", corpus.order)
		}
	})
}

func TestDeterminism(t *testing.T) {
	tickets := []*pysem.Obj{}
	for i := 0; i < 6; i++ {
		tickets = append(tickets, relTicketItem(
			fmt.Sprintf("A-%d", i), fmt.Sprintf("Piece %d", i),
			fmt.Sprintf("Work on the widget number %d handler.", i)))
	}
	reversed := make([]*pysem.Obj, len(tickets))
	for i, ticket := range tickets {
		reversed[len(tickets)-1-i] = ticket
	}
	forwards := relMatches(relCommitItem("Rewrite the widget number 3 handler"), tickets, true, false)
	backwards := relMatches(relCommitItem("Rewrite the widget number 3 handler"), reversed, true, false)
	if forwards != backwards {
		t.Fatalf("shuffled input changed the answer: %v vs %v", forwards, backwards)
	}
}

// --- golden cases pinned against the Python reference ----------------------

func TestCamelSplitGolden(t *testing.T) {
	// Golden values from _CAMEL_SPLIT_RE.split on the reference build.
	cases := []struct {
		in   string
		want []string
	}{
		{"HTTPServer", []string{"HTTPServer"}},
		{"parseURLFast", []string{"parse", "URLFast"}},
		{"already_split", []string{"already_split"}},
		{"ABCDef", []string{"ABCDef"}},
		{"a1B", []string{"a1", "B"}},
		{"", []string{""}},
	}
	for _, c := range cases {
		if got := relCamelSplit(c.in); !reflect.DeepEqual(got, c.want) {
			t.Fatalf("relCamelSplit(%q) = %v, want %v", c.in, got, c.want)
		}
	}
}

func TestCanonicalIdentGolden(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"PipelineApproval", "pipeline-approval"},
		{"src-index", "src-index"},
		{"en-US", ""},
	}
	for _, c := range cases {
		if got := relCanonicalIdent(c.in); got != c.want {
			t.Fatalf("relCanonicalIdent(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestLowerIstanbulThroughTokenization(t *testing.T) {
	// pysem.Lower sends U+0130 İ to "i" + combining dot above, exactly like
	// CPython — the combining mark then splits the ASCII word scan.
	if got := relSorted(relWords("İstanbul-FIX")); !reflect.DeepEqual(got, []string{"stanbul"}) {
		t.Fatalf("relWords(İstanbul-FIX) = %v", got)
	}
	if got := relSorted(relIdents("İstanbul-FIX")); !reflect.DeepEqual(got, []string{"stanbul-fix"}) {
		t.Fatalf("relIdents(İstanbul-FIX) = %v", got)
	}
}

func TestDoDDocGolden(t *testing.T) {
	cases := []struct {
		body string
		want bool
	}{
		{"docsé", false}, // unicode \b: é is a word rune, Python finds no match
		{"- [ ] Documentation", true},
		{"* Docs updated", true},
		{"Definition of done: user guide", true},
		{"this is documented elsewhere", false},
		{"update the docs", true},
		{"docs", true},
		{"intro text\n- [x] Runbook", true},
	}
	for _, c := range cases {
		if got := relDoDDocSearch(c.body); got != c.want {
			t.Fatalf("relDoDDocSearch(%q) = %v, want %v", c.body, got, c.want)
		}
	}
}

func TestFindTicketKeysGolden(t *testing.T) {
	got := relFindTicketKeys("see ABC-123 and éABC-124 ok")
	if !reflect.DeepEqual(got, []string{"ABC-123"}) { // é kills Python's \b before A
		t.Fatalf("relFindTicketKeys = %v", got)
	}
}

func TestFindShasGolden(t *testing.T) {
	got := relFindShas("fixed in a1b2c3d4 and édeadbeef99 done")
	if !reflect.DeepEqual(got, []string{"a1b2c3d4"}) { // é kills Python's \b before d
		t.Fatalf("relFindShas = %v", got)
	}
}

func TestBranchTokensGolden(t *testing.T) {
	words, idents := relBranchTokens("users/alice/retry-backoff-webhook")
	if got := relSorted(words); !reflect.DeepEqual(got, []string{"backoff", "retry", "webhook"}) {
		t.Fatalf("branch words = %v", got)
	}
	if got := relSorted(idents); !reflect.DeepEqual(got, []string{"retry-backoff-webhook"}) {
		t.Fatalf("branch idents = %v", got)
	}
	words, idents = relBranchTokens("feature/ABC-123-fix-widget-cache")
	if got := relSorted(words); !reflect.DeepEqual(got, []string{"cache", "widget"}) {
		t.Fatalf("key-stripped branch words = %v", got)
	}
	if got := relSorted(idents); !reflect.DeepEqual(got, []string{"fix-widget-cache"}) {
		t.Fatalf("key-stripped branch idents = %v", got)
	}
}

func TestPathTokensGolden(t *testing.T) {
	if got := relSorted(relPathTokens([]string{"src/yeaboi/standup/habits.py"})); !reflect.DeepEqual(got, []string{"habits", "standup", "yeaboi"}) {
		t.Fatalf("path tokens = %v", got)
	}
	if got := relSorted(relPathIdents([]string{"src/yeaboi/standup/habits.py", "src/index.ts"})); !reflect.DeepEqual(got, []string{"src-index", "standup-habits"}) {
		t.Fatalf("path idents = %v", got)
	}
}

func TestNormalizeURLGolden(t *testing.T) {
	if got := relNormalizeURL("  HTTPS://G/x/Pull/91/?utm=1#frag  "); got != "https://g/x/pull/91" {
		t.Fatalf("relNormalizeURL = %q", got)
	}
}

func TestBigramsGolden(t *testing.T) {
	// Adjacent means exactly one space: the double-spaced pair is dropped, as
	// are pairs touching a stopword or a short word.
	got := relSorted(relBigrams("the pipeline approval  plugin needs work"))
	if !reflect.DeepEqual(got, []string{"pipeline-approval"}) {
		t.Fatalf("relBigrams = %v", got)
	}
}

func TestNearMissTieBreakGolden(t *testing.T) {
	// Two tickets with equal scores must come back in Python's order: own
	// tickets first, then score descending, then key ascending. Golden output
	// from near_misses on the reference build.
	tickets := []*pysem.Obj{
		relTicketItem("Z-1", "Grobble flumox overhaul", ""),
		relTicketItem("B-2", "Wizzle support", ""),
		relTicketItem("A-9", "Plonk support", ""),
	}
	corpus := buildCorpus(tickets, nil)
	change := relItem(
		"kind", "commit", "key", "", "title", "Rework grobble flumox wizzle plonk",
		"body", "", "branch", "", "changed_paths", []any{}, "url", "", "pr_id", "", "repository", "",
	)
	profile := buildChangeProfile(change)
	if got := nearMisses(profile, corpus, nil, 3); !reflect.DeepEqual(got, []string{"Z-1", "A-9", "B-2"}) {
		t.Fatalf("near misses (no own keys) = %v, want [Z-1 A-9 B-2]", got)
	}
	own := map[string]bool{"B-2": true}
	if got := nearMisses(profile, corpus, own, 3); !reflect.DeepEqual(got, []string{"B-2", "Z-1", "A-9"}) {
		t.Fatalf("near misses (own B-2) = %v, want [B-2 Z-1 A-9]", got)
	}
}
