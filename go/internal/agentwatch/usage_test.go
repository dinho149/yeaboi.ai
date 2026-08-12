package agentwatch

import (
	"path/filepath"
	"testing"

	"github.com/yeaboi-ai/yeaboi/go/internal/contract"
)

func usageParams(dir string, roots []SourceRoot) *contract.UsageParams {
	specs := make([]contract.RootSpec, 0, len(roots))
	for _, r := range roots {
		specs = append(specs, contract.RootSpec{Source: r.Source, Root: r.Root})
	}
	return &contract.UsageParams{
		DBPath:     filepath.Join(dir, "sessions.db"),
		WindowDays: 30,
		Today:      "2026-08-09",
		Roots:      &specs,
	}
}

func TestRunAgentUsageOverFixture(t *testing.T) {
	dir := t.TempDir()
	roots, _ := fixtureRoots(t, dir)
	var events []*contract.Event
	result, err := RunAgentUsage(usageParams(dir, roots), func(ev *contract.Event) { events = append(events, ev) })
	if err != nil {
		t.Fatal(err)
	}
	if result.ContractVersion != 1 {
		t.Errorf("contract_version = %d", result.ContractVersion)
	}
	if result.Stats.FilesParsed != 1 {
		t.Errorf("stats = %+v", result.Stats)
	}
	a := result.Artifact
	if a.PeriodStart != "2026-07-11" || a.PeriodEnd != "2026-08-09" {
		t.Errorf("window = %s..%s", a.PeriodStart, a.PeriodEnd)
	}
	if a.SessionCount != 1 {
		t.Errorf("session_count = %d", a.SessionCount)
	}
	if a.TotalCostUSD != 0.0043 { // round4(0.0042725), banker's rounding
		t.Errorf("total_cost_usd = %v, want 0.0043", a.TotalCostUSD)
	}
	if a.TotalInputTokens != 12 || a.TotalOutputTokens != 150 ||
		a.TotalCacheWriteTokens != 40 || a.TotalCacheReadTokens != 200 {
		t.Errorf("token totals = %d/%d/%d/%d", a.TotalInputTokens, a.TotalOutputTokens,
			a.TotalCacheWriteTokens, a.TotalCacheReadTokens)
	}
	if a.UnknownModelCostShare != 0.0 {
		t.Errorf("unknown_model_cost_share = %v", a.UnknownModelCostShare)
	}
	if a.PricingAsOf != "2026-06-24" {
		t.Errorf("pricing_as_of = %q", a.PricingAsOf)
	}
	if len(a.ByModel) != 1 {
		t.Fatalf("by_model = %+v", a.ByModel)
	}
	m := a.ByModel[0]
	if m.Model != "claude-opus-5" || m.InputTokens != 12 || m.OutputTokens != 150 ||
		m.CacheWriteTokens != 40 || m.CacheReadTokens != 200 || m.Calls != 2 ||
		m.CostUSD != 0.0043 || !m.KnownPricing {
		t.Errorf("by_model[0] = %+v", m)
	}
	if len(a.ByProject) != 1 || a.ByProject[0].Key != "proj" || a.ByProject[0].Sessions != 1 ||
		a.ByProject[0].InputTokens != 12 || a.ByProject[0].CostUSD != 0.0043 {
		t.Errorf("by_project = %+v", a.ByProject)
	}
	if len(a.BySource) != 1 || a.BySource[0].Key != "claude_code" {
		t.Errorf("by_source = %+v", a.BySource)
	}
	if len(a.DailyTrend) != 1 || a.DailyTrend[0].Date != "2026-08-07" ||
		a.DailyTrend[0].CostUSD != 0.0043 || a.DailyTrend[0].Sessions != 1 {
		t.Errorf("daily_trend = %+v", a.DailyTrend)
	}
	if len(a.Insights) != 0 || len(a.Recommendations) != 0 || a.GeneratedAt != "" {
		t.Errorf("prose fields must stay empty from Go: %+v", a)
	}
	if len(a.Warnings) != 0 {
		t.Errorf("warnings = %v", a.Warnings)
	}

	// Engine-level phase events: scan running … scan completed with the
	// verbatim detail, then price running/completed.
	var phases []string
	for _, ev := range events {
		if ev.ComponentID == "scan" && ev.Current == nil {
			phases = append(phases, "scan:"+ev.Status+":"+ev.Detail)
		}
		if ev.ComponentID == "price" {
			phases = append(phases, "price:"+ev.Status+":"+ev.Detail)
		}
	}
	want := []string{
		"scan:running:",
		"scan:completed:1 parsed · 0 cached",
		"price:running:1 transcript(s)",
		"price:completed:1 transcript(s)",
	}
	if len(phases) != len(want) {
		t.Fatalf("phase events = %v, want %v", phases, want)
	}
	for i := range want {
		if phases[i] != want[i] {
			t.Errorf("phase[%d] = %q, want %q", i, phases[i], want[i])
		}
	}
}

func TestRunAgentUsageEmptyWindow(t *testing.T) {
	dir := t.TempDir()
	roots, _ := fixtureRoots(t, dir)
	p := usageParams(dir, roots)
	p.WindowDays = 1 // 2026-08-09 only; the fixture session ended 2026-08-07
	var events []*contract.Event
	result, err := RunAgentUsage(p, func(ev *contract.Event) { events = append(events, ev) })
	if err != nil {
		t.Fatal(err)
	}
	a := result.Artifact
	if a.SessionCount != 0 || a.TotalCostUSD != 0.0 {
		t.Errorf("expected empty window, got %+v", a)
	}
	if len(a.Warnings) != 1 || a.Warnings[0] != emptyWindowWarning {
		t.Errorf("warnings = %v", a.Warnings)
	}
	if len(a.ByModel) != 0 || len(a.DailyTrend) != 0 {
		t.Errorf("rows must be empty: %+v", a)
	}
	sawNoData := false
	for _, ev := range events {
		if ev.ComponentID == "price" && ev.Status == "no_data" {
			sawNoData = true
		}
	}
	if !sawNoData {
		t.Error("expected the price phase to close as no_data on an empty window")
	}
}

func TestRunAgentUsageFilters(t *testing.T) {
	dir := t.TempDir()
	roots, _ := fixtureRoots(t, dir)
	p := usageParams(dir, roots)
	p.Project = "PROJ" // case-insensitive substring on the project dir name
	result, err := RunAgentUsage(p, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result.Artifact.SessionCount != 1 {
		t.Errorf("project filter should match, got %+v", result.Artifact)
	}
	p2 := usageParams(dir, roots)
	p2.Source = "codex"
	result2, err := RunAgentUsage(p2, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result2.Artifact.SessionCount != 0 {
		t.Errorf("source filter should exclude, got %+v", result2.Artifact)
	}
}

func TestRunAgentRefreshMethod(t *testing.T) {
	dir := t.TempDir()
	roots, _ := fixtureRoots(t, dir)
	specs := []contract.RootSpec{{Source: roots[0].Source, Root: roots[0].Root}}
	p := &contract.RefreshParams{DBPath: filepath.Join(dir, "sessions.db"), Roots: &specs}
	result, err := RunAgentRefresh(p, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result.ContractVersion != 1 || result.Stats.FilesParsed != 1 {
		t.Errorf("refresh result = %+v", result)
	}
	// reset_cursors forces a reparse through the RPC surface too.
	p.ResetCursors = true
	result, err = RunAgentRefresh(p, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result.Stats.FilesParsed != 1 || result.Stats.FilesSkipped != 0 {
		t.Errorf("reset_cursors refresh = %+v", result.Stats)
	}
}

func TestProjectLabel(t *testing.T) {
	cases := map[string]string{
		"/home/dev/proj":  "proj",
		"/home/dev/proj/": "proj",
		"/":               "/",
		"":                "(unknown)",
		"proj":            "proj",
	}
	for in, want := range cases {
		if got := projectLabel(in); got != want {
			t.Errorf("projectLabel(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestPyJSONDumpsMatchesPython(t *testing.T) {
	// json.dumps(value, sort_keys=True) byte parity — these bytes persist in
	// sessions.db and both implementations read each other's rows.
	usage := map[string]map[string]int64{
		"claude-opus-5": {"input": 12, "output": 150, "cache_write_5m": 10, "cache_write_1h": 30, "cache_read": 200, "calls": 2},
	}
	want := `{"claude-opus-5": {"cache_read": 200, "cache_write_1h": 30, "cache_write_5m": 10, "calls": 2, "input": 12, "output": 150}}`
	if got := pyJSONDumpsUsage(usage); got != want {
		t.Errorf("usage dump\n got %s\nwant %s", got, want)
	}
	counts := map[string]int64{"Bash": 1, "Edit": 1}
	if got := pyJSONDumpsCounts(counts); got != `{"Bash": 1, "Edit": 1}` {
		t.Errorf("counts dump = %s", got)
	}
	if got := pyJSONDumpsCounts(map[string]int64{}); got != "{}" {
		t.Errorf("empty dump = %s", got)
	}
	// ensure_ascii=True escapes non-ASCII, exactly as Python writes it.
	wantEscaped := "{\"\\u00e9moji\\u2713\": 1}"
	if got := pyJSONDumpsCounts(map[string]int64{"émoji✓": 1}); got != wantEscaped {
		t.Errorf("ensure_ascii dump = %s, want %s", got, wantEscaped)
	}
}
