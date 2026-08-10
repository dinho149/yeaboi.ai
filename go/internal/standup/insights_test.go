// insights_test.go — port of tests/unit/test_standup_insights.py. Keep in lockstep: the Python module is the reference implementation; tests/parity/test_standup_parity.py diffs whole-pipeline output.
package standup

import (
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// insightsTestItem builds an activity item from alternating key/value strings.
func insightsTestItem(kvs ...string) *pysem.Obj {
	o := pysem.EmptyObj()
	for i := 0; i+1 < len(kvs); i += 2 {
		o.Set(kvs[i], kvs[i+1])
	}
	return o
}

// insightsTestGrouped builds a Grouped for one member, mirroring the Python
// tests' single-entry dict literals.
func insightsTestGrouped(name string, items ...*pysem.Obj) *Grouped {
	g := newGrouped([]string{name})
	g.Items[name] = append(g.Items[name], items...)
	return g
}

// insightsTestPrevReport mirrors the Python _prev_report helper: a previous
// report with a single member named Alice.
func insightsTestPrevReport(member PrevMember) *PrevReport {
	member.Name = "Alice"
	return &PrevReport{Members: []PrevMember{member}}
}

// insightsTestNameList builds an ordered {name: [strings]} object (the
// transcript-corrections / corrected-fields wire shape).
func insightsTestNameList(name string, items ...string) *pysem.Obj {
	o := pysem.EmptyObj()
	values := make([]any, 0, len(items))
	for _, s := range items {
		values = append(values, s)
	}
	o.Set(name, values)
	return o
}

// insightsTestStrings unwraps a []any-of-string signal/correction list.
func insightsTestStrings(t *testing.T, v any) []string {
	t.Helper()
	arr, ok := v.([]any)
	if !ok {
		t.Fatalf("value %#v is not a []any", v)
	}
	out := make([]string, 0, len(arr))
	for _, e := range arr {
		s, ok := e.(string)
		if !ok {
			t.Fatalf("element %#v is not a string", e)
		}
		out = append(out, s)
	}
	return out
}

// insightsTestEntry fetches a member entry from a yesterdayContext result.
func insightsTestEntry(t *testing.T, ctx *pysem.Obj, name string) *pysem.Obj {
	t.Helper()
	entry := pysem.AsObj(ctx.Get(name))
	if entry == nil {
		t.Fatalf("no entry for %q (keys %v)", name, ctx.Keys())
	}
	return entry
}

func TestInsightsBlockedStatus(t *testing.T) {
	t.Run("blocked issue fires", func(t *testing.T) {
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "issue", "key", "PSOT-9", "title", "Auth flow", "status", "Blocked"))
		signals := detectBlockerSignals(grouped, nil)
		got := insightsTestStrings(t, signals.Get("Alice"))
		want := []string{"PSOT-9 'Auth flow' is in Blocked"}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("signals = %v, want %v", got, want)
		}
	})

	t.Run("wip and work_item and update kinds fire", func(t *testing.T) {
		for _, kind := range []string{"wip", "work_item", "update"} {
			grouped := insightsTestGrouped("Alice",
				insightsTestItem("kind", kind, "key", "AB-1", "title", "t", "status", "On Hold"))
			if !detectBlockerSignals(grouped, nil).Has("Alice") {
				t.Errorf("kind %q did not fire", kind)
			}
		}
	})

	t.Run("waiting for prefix fires", func(t *testing.T) {
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "issue", "key", "AB-1", "title", "t", "status", "Waiting for deploy window"))
		if !detectBlockerSignals(grouped, nil).Has("Alice") {
			t.Fatal("waiting-for prefix did not fire")
		}
	})

	t.Run("normal statuses ignored", func(t *testing.T) {
		for _, status := range []string{"In Progress", "Done", "In Review", "To Do", "Waiting Room Feature", ""} {
			grouped := insightsTestGrouped("Alice",
				insightsTestItem("kind", "issue", "key", "AB-1", "title", "t", "status", status))
			if detectBlockerSignals(grouped, nil).Len() != 0 {
				t.Errorf("status %q fired", status)
			}
		}
	})

	t.Run("non status kinds ignored", func(t *testing.T) {
		// A commit titled "fix blocked pipeline" must never read as a blocker.
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "commit", "key", "", "title", "fix blocked pipeline", "status", "blocked"))
		if detectBlockerSignals(grouped, nil).Len() != 0 {
			t.Fatal("commit kind fired")
		}
	})

	t.Run("same ticket deduped across kinds", func(t *testing.T) {
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "issue", "key", "AB-1", "title", "t", "status", "Blocked"),
			insightsTestItem("kind", "update", "key", "AB-1", "title", "moved AB-1 't' to Blocked", "status", "Blocked"))
		if got := len(insightsTestStrings(t, detectBlockerSignals(grouped, nil).Get("Alice"))); got != 1 {
			t.Fatalf("got %d signals, want 1", got)
		}
	})

	t.Run("signal capped per member", func(t *testing.T) {
		items := []*pysem.Obj{}
		for i := 0; i < 6; i++ {
			items = append(items, insightsTestItem("kind", "issue", "key", fmt.Sprintf("AB-%d", i), "title", "t", "status", "Blocked"))
		}
		grouped := insightsTestGrouped("Alice", items...)
		if got := len(insightsTestStrings(t, detectBlockerSignals(grouped, nil).Get("Alice"))); got != insightsMaxSignalsPerMember {
			t.Fatalf("got %d signals, want %d", got, insightsMaxSignalsPerMember)
		}
	})
}

func TestInsightsPrOpenAcrossStandups(t *testing.T) {
	t.Run("pr seen yesterday still open fires", func(t *testing.T) {
		prev := insightsTestPrevReport(PrevMember{CodeLinks: [][2]string{{"export refactor", "https://g.h/acme/app/pull/490"}}})
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "pr", "key", "#490", "title", "export refactor", "status", "open",
				"url", "https://g.h/acme/app/pull/490"))
		signals := detectBlockerSignals(grouped, prev)
		got := insightsTestStrings(t, signals.Get("Alice"))
		want := []string{"PR #490 'export refactor' still open since the last standup"}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("signals = %v, want %v", got, want)
		}
	})

	t.Run("merged pr no signal", func(t *testing.T) {
		prev := insightsTestPrevReport(PrevMember{CodeLinks: [][2]string{{"x", "https://g.h/p/1"}}})
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "pr", "key", "#1", "title", "x", "status", "merged", "url", "https://g.h/p/1"))
		if detectBlockerSignals(grouped, prev).Len() != 0 {
			t.Fatal("merged PR fired")
		}
	})

	t.Run("new pr not in previous report no signal", func(t *testing.T) {
		prev := insightsTestPrevReport(PrevMember{CodeLinks: [][2]string{{"other", "https://g.h/p/2"}}})
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "pr", "key", "#1", "title", "x", "status", "open", "url", "https://g.h/p/1"))
		if detectBlockerSignals(grouped, prev).Len() != 0 {
			t.Fatal("new PR fired")
		}
	})

	t.Run("no previous report rule off", func(t *testing.T) {
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "pr", "key", "#1", "title", "x", "status", "open", "url", "https://g.h/p/1"))
		if detectBlockerSignals(grouped, nil).Len() != 0 {
			t.Fatal("rule fired with no previous report")
		}
	})

	t.Run("legacy links field counts", func(t *testing.T) {
		prev := insightsTestPrevReport(PrevMember{Links: [][2]string{{"x", "https://g.h/p/1"}}})
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "pr", "key", "#1", "title", "x", "status", "open", "url", "https://g.h/p/1"))
		if !detectBlockerSignals(grouped, prev).Has("Alice") {
			t.Fatal("legacy links did not count")
		}
	})
}

// insightsTestChurnGrouped mirrors the Python _churn_grouped helper: Alice
// owns AB-7, the commenters round-robin comment items onto it. Member order
// mirrors dict insertion order: Alice first, then commenters as they appear.
func insightsTestChurnGrouped(nComments int, commenters ...string) *Grouped {
	names := []string{"Alice"}
	seen := map[string]bool{"Alice": true}
	for i := 0; i < nComments; i++ {
		name := commenters[i%len(commenters)]
		if !seen[name] {
			seen[name] = true
			names = append(names, name)
		}
	}
	g := newGrouped(names)
	g.Items["Alice"] = append(g.Items["Alice"],
		insightsTestItem("kind", "issue", "key", "AB-7", "title", "t", "status", "In Progress"))
	for i := 0; i < nComments; i++ {
		name := commenters[i%len(commenters)]
		g.Items[name] = append(g.Items[name],
			insightsTestItem("kind", "comment", "key", "AB-7", "title", fmt.Sprintf("commented on AB-7 (%d)", i), "status", ""))
	}
	return g
}

func TestInsightsCommentChurn(t *testing.T) {
	t.Run("churn attributed to ticket owner", func(t *testing.T) {
		signals := detectBlockerSignals(insightsTestChurnGrouped(4, "Bob", "Carla"), nil)
		got := insightsTestStrings(t, signals.Get("Alice"))
		want := []string{"Heavy discussion on AB-7 (4 comments)"}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("signals = %v, want %v", got, want)
		}
		if signals.Has("Bob") { // commenters are not flagged, the owner is
			t.Fatal("Bob was flagged")
		}
	})

	t.Run("below comment floor no signal", func(t *testing.T) {
		if detectBlockerSignals(insightsTestChurnGrouped(3, "Bob", "Carla"), nil).Len() != 0 {
			t.Fatal("fired below the comment floor")
		}
	})

	t.Run("single commenter no signal", func(t *testing.T) {
		if detectBlockerSignals(insightsTestChurnGrouped(4, "Bob"), nil).Len() != 0 {
			t.Fatal("fired with a single commenter")
		}
	})

	t.Run("orphan key dropped", func(t *testing.T) {
		full := insightsTestChurnGrouped(4, "Bob", "Carla")
		g := newGrouped([]string{"Bob", "Carla"}) // nobody owns AB-7 anymore
		g.Items["Bob"] = full.Items["Bob"]
		g.Items["Carla"] = full.Items["Carla"]
		if detectBlockerSignals(g, nil).Len() != 0 {
			t.Fatal("orphan key fired")
		}
	})
}

func TestInsightsClip(t *testing.T) {
	// Golden additions: exact clipping semantics — at the limit unchanged, one
	// over cuts to limit-1 runes, rstrips, and appends U+2026; lengths and
	// slices count RUNES, not bytes.
	t.Run("at exactly the limit unchanged", func(t *testing.T) {
		text := strings.Repeat("a", 60)
		if got := insightsClip(text, 60); got != text {
			t.Fatalf("got %q", got)
		}
	})
	t.Run("one over the limit clips", func(t *testing.T) {
		text := strings.Repeat("a", 61)
		want := strings.Repeat("a", 59) + "…"
		if got := insightsClip(text, 60); got != want {
			t.Fatalf("got %q, want %q", got, want)
		}
	})
	t.Run("rstrip before the ellipsis", func(t *testing.T) {
		// runes[:9] of "abcdefg   hij" is "abcdefg  " → rstrip → "abcdefg…".
		if got := insightsClip("abcdefg   hij", 10); got != "abcdefg…" {
			t.Fatalf("got %q", got)
		}
	})
	t.Run("multibyte runes count as one", func(t *testing.T) {
		text := strings.Repeat("é", 61)
		want := strings.Repeat("é", 59) + "…"
		if got := insightsClip(text, 60); got != want {
			t.Fatalf("got %q, want %q", got, want)
		}
	})
	t.Run("clipped label in a signal", func(t *testing.T) {
		grouped := insightsTestGrouped("Alice",
			insightsTestItem("kind", "issue", "key", "AB-1", "title", strings.Repeat("x", 61), "status", "Blocked"))
		got := insightsTestStrings(t, detectBlockerSignals(grouped, nil).Get("Alice"))
		want := []string{"AB-1 '" + strings.Repeat("x", 59) + "…' is in Blocked"}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("signals = %v, want %v", got, want)
		}
	})
}

func TestInsightsYesterdayContext(t *testing.T) {
	t.Run("none returns empty", func(t *testing.T) {
		if yesterdayContext(nil, nil, nil).Len() != 0 {
			t.Fatal("expected empty context")
		}
	})

	t.Run("maps summary blockers outlook", func(t *testing.T) {
		prev := insightsTestPrevReport(PrevMember{Summary: "Did X", Blockers: "waiting on review", Outlook: "Likely to finish X"})
		entry := insightsTestEntry(t, yesterdayContext(prev, nil, nil), "Alice")
		if !reflect.DeepEqual(entry.Keys(), []string{"summary", "blockers", "outlook"}) {
			t.Fatalf("keys = %v", entry.Keys())
		}
		if entry.Get("summary") != "Did X" || entry.Get("blockers") != "waiting on review" || entry.Get("outlook") != "Likely to finish X" {
			t.Fatalf("entry = %v", entry)
		}
	})

	t.Run("truncates long values", func(t *testing.T) {
		prev := insightsTestPrevReport(PrevMember{Summary: strings.Repeat("x", 500)})
		entry := insightsTestEntry(t, yesterdayContext(prev, nil, nil), "Alice")
		summary := entry.Get("summary").(string)
		if n := len([]rune(summary)); n > insightsYesterdayClip {
			t.Fatalf("summary is %d runes", n)
		}
		if want := strings.Repeat("x", 299) + "…"; summary != want {
			t.Fatalf("summary = %q", summary)
		}
	})

	t.Run("fully empty member omitted", func(t *testing.T) {
		prev := insightsTestPrevReport(PrevMember{Summary: "", Blockers: "", Outlook: ""})
		if yesterdayContext(prev, nil, nil).Len() != 0 {
			t.Fatal("expected empty context")
		}
	})
}

func TestInsightsCorrectedFields(t *testing.T) {
	// The Go seam receives the previous run's edit log already parsed
	// (correctedFields); the path-parsing tests stay Python-side with
	// insights.corrected_members.
	t.Run("a corrected member is flagged", func(t *testing.T) {
		prev := &PrevReport{Members: []PrevMember{{Name: "Ada", Summary: "Landed login."}}}
		ctx := yesterdayContext(prev, nil, insightsTestNameList("Ada", "summary"))
		entry := insightsTestEntry(t, ctx, "Ada")
		if got := insightsTestStrings(t, entry.Get("corrected")); !reflect.DeepEqual(got, []string{"summary"}) {
			t.Fatalf("corrected = %v", got)
		}
		if !reflect.DeepEqual(entry.Keys(), []string{"summary", "blockers", "outlook", "corrected"}) {
			t.Fatalf("keys = %v", entry.Keys())
		}
	})

	t.Run("an uncorrected member carries no flag", func(t *testing.T) {
		prev := &PrevReport{Members: []PrevMember{{Name: "Ada", Summary: "a"}, {Name: "Grace", Summary: "b"}}}
		ctx := yesterdayContext(prev, nil, insightsTestNameList("Ada", "summary"))
		if insightsTestEntry(t, ctx, "Grace").Has("corrected") {
			t.Fatal("Grace carries a corrected flag")
		}
	})

	// Golden addition: sorted(set(...)) — dedupe first, then sort.
	t.Run("corrected fields are deduped and sorted", func(t *testing.T) {
		prev := &PrevReport{Members: []PrevMember{{Name: "Ada", Summary: "a"}}}
		ctx := yesterdayContext(prev, nil, insightsTestNameList("Ada", "summary", "blockers", "summary"))
		got := insightsTestStrings(t, insightsTestEntry(t, ctx, "Ada").Get("corrected"))
		if !reflect.DeepEqual(got, []string{"blockers", "summary"}) {
			t.Fatalf("corrected = %v", got)
		}
	})

	t.Run("no corrections is the shape it always was", func(t *testing.T) {
		prev := &PrevReport{Members: []PrevMember{{Name: "Ada", Summary: "a"}}}
		with := pysem.JSONDumps(yesterdayContext(prev, nil, pysem.EmptyObj()))
		without := pysem.JSONDumps(yesterdayContext(prev, nil, nil))
		if with != without {
			t.Fatalf("%s != %s", with, without)
		}
	})
}

func TestInsightsYesterdayCorrections(t *testing.T) {
	// Corrections are fed FORWARD, never written back into yesterday's report.
	t.Run("correction attaches to an existing entry", func(t *testing.T) {
		prev := insightsTestPrevReport(PrevMember{Summary: "Did X"})
		ctx := yesterdayContext(prev, insightsTestNameList("Alice", "also commented on the design doc"), nil)
		entry := insightsTestEntry(t, ctx, "Alice")
		if entry.Get("summary") != "Did X" {
			t.Fatalf("summary = %v", entry.Get("summary"))
		}
		got := insightsTestStrings(t, entry.Get("corrections"))
		if !reflect.DeepEqual(got, []string{"also commented on the design doc"}) {
			t.Fatalf("corrections = %v", got)
		}
		if !reflect.DeepEqual(entry.Keys(), []string{"summary", "blockers", "outlook", "corrections"}) {
			t.Fatalf("keys = %v", entry.Keys())
		}
	})

	t.Run("member with only a correction still gets an entry", func(t *testing.T) {
		// The correction is the only thing we know about their yesterday.
		ctx := yesterdayContext(nil, insightsTestNameList("Alice", "shipped the alerting PR"), nil)
		entry := insightsTestEntry(t, ctx, "Alice")
		got := insightsTestStrings(t, entry.Get("corrections"))
		if !reflect.DeepEqual(got, []string{"shipped the alerting PR"}) {
			t.Fatalf("corrections = %v", got)
		}
		if entry.Get("summary") != "" {
			t.Fatalf("summary = %v", entry.Get("summary"))
		}
	})

	t.Run("no corrections leaves the key absent", func(t *testing.T) {
		ctx := yesterdayContext(insightsTestPrevReport(PrevMember{Summary: "Did X"}), nil, nil)
		if insightsTestEntry(t, ctx, "Alice").Has("corrections") {
			t.Fatal("corrections key present")
		}
	})

	t.Run("corrections are capped", func(t *testing.T) {
		items := []string{}
		for i := 0; i < 10; i++ {
			items = append(items, fmt.Sprintf("thing %d", i))
		}
		ctx := yesterdayContext(nil, insightsTestNameList("Alice", items...), nil)
		if got := len(insightsTestStrings(t, insightsTestEntry(t, ctx, "Alice").Get("corrections"))); got != 3 {
			t.Fatalf("got %d corrections, want 3", got)
		}
	})

	t.Run("corrections are clipped", func(t *testing.T) {
		ctx := yesterdayContext(nil, insightsTestNameList("Alice", strings.Repeat("x", 900)), nil)
		first := insightsTestStrings(t, insightsTestEntry(t, ctx, "Alice").Get("corrections"))[0]
		if n := len([]rune(first)); n > 305 {
			t.Fatalf("correction is %d runes", n)
		}
	})

	t.Run("empty corrections are ignored", func(t *testing.T) {
		if yesterdayContext(nil, insightsTestNameList("Alice", "", "   "), nil).Len() != 0 {
			t.Fatal("expected empty context")
		}
	})
}
