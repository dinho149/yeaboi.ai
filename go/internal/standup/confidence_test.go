// confidence_test.go — port of tests/unit/test_standup_confidence.py. Keep in lockstep: the Python module is the reference implementation; tests/parity/test_standup_parity.py diffs whole-pipeline output.
package standup

import (
	"encoding/json"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// confidenceTestDate builds a civil date carried in a UTC-midnight time.Time,
// mirroring datetime.date in the Python tests.
func confidenceTestDate(y int, m time.Month, d int) time.Time {
	return time.Date(y, m, d, 0, 0, 0, 0, time.UTC)
}

// confidenceTestRow is one (standup_date, confidence_pct, status) triple from
// the Python _hist helper. pct is any so error-path tests can feed strings,
// lists, and nulls.
type confidenceTestRow struct {
	day    string
	pct    any
	status string
}

// confidenceTestHist mirrors the Python _hist helper: history rows
// newest-first, mirroring StandupStore.get_history output.
func confidenceTestHist(rows ...confidenceTestRow) []*pysem.Obj {
	out := []*pysem.Obj{}
	for i, r := range rows {
		o := pysem.EmptyObj()
		o.Set("standup_date", r.day)
		o.Set("confidence_pct", r.pct)
		o.Set("status", r.status)
		o.Set("sprint_day", json.Number(strconv.Itoa(i)))
		o.Set("run_at", r.day+"T09:00:00")
		o.Set("id", json.Number(strconv.Itoa(i)))
		out = append(out, o)
	}
	return out
}

// confidenceTestPct wraps an int the way the wire delivers it.
func confidenceTestPct(n int) json.Number {
	return json.Number(strconv.Itoa(n))
}

// confidenceTestCompute mirrors the Python _compute helper: start 2026-07-06 →
// day 5 of 10 on 07-10; 10/10 ideal → base pct 100.
func confidenceTestCompute(history []*pysem.Obj, completed float64, activity int, today time.Time) *sprintProgress {
	return computeConfidence("", "2026-07-06", 2, 20, completed, activity, today, history)
}

func TestWorkingDaysBetween(t *testing.T) {
	t.Run("full week", func(t *testing.T) {
		// Mon 2026-07-06 .. Fri 2026-07-10 = 5 working days
		if got := workingDaysBetween(confidenceTestDate(2026, 7, 6), confidenceTestDate(2026, 7, 10), nil); got != 5 {
			t.Fatalf("got %d, want 5", got)
		}
	})
	t.Run("excludes weekend", func(t *testing.T) {
		// Mon .. Sun spans a weekend → still 5
		if got := workingDaysBetween(confidenceTestDate(2026, 7, 6), confidenceTestDate(2026, 7, 12), nil); got != 5 {
			t.Fatalf("got %d, want 5", got)
		}
	})
	t.Run("excludes holidays", func(t *testing.T) {
		holidays := map[time.Time]bool{confidenceTestDate(2026, 7, 8): true} // Wednesday off
		if got := workingDaysBetween(confidenceTestDate(2026, 7, 6), confidenceTestDate(2026, 7, 10), holidays); got != 4 {
			t.Fatalf("got %d, want 4", got)
		}
	})
	t.Run("end before start", func(t *testing.T) {
		if got := workingDaysBetween(confidenceTestDate(2026, 7, 10), confidenceTestDate(2026, 7, 6), nil); got != 0 {
			t.Fatalf("got %d, want 0", got)
		}
	})
	// Golden addition: a Sat..Sun range counts nothing — pins the Go weekday
	// conversion ((Weekday+6)%7 >= 5 is Sat/Sun, matching Python weekday() >= 5).
	t.Run("weekend only is zero", func(t *testing.T) {
		if got := workingDaysBetween(confidenceTestDate(2026, 7, 11), confidenceTestDate(2026, 7, 12), nil); got != 0 {
			t.Fatalf("got %d, want 0", got)
		}
	})
}

func TestConfidenceParseDate(t *testing.T) {
	cases := []struct {
		value string
		want  string // "" = None
	}{
		{"2026-07-08", "2026-07-08"},
		{"2026-07-08T09:00:00", "2026-07-08"}, // ISO datetime slices to its date
		{"", ""},
		{"garbage", ""},
		{"2026-13-01", ""}, // out-of-range month
		{"2026-7-8", ""},   // fromisoformat requires zero-padded fields
	}
	for _, c := range cases {
		got, ok := confidenceParseDate(c.value)
		if c.want == "" {
			if ok {
				t.Errorf("confidenceParseDate(%q) = %v, want None", c.value, got)
			}
			continue
		}
		if !ok || got.Format("2006-01-02") != c.want {
			t.Errorf("confidenceParseDate(%q) = %v ok=%v, want %s", c.value, got, ok, c.want)
		}
	}
}

func TestConfidenceDeclineStreak(t *testing.T) {
	cases := []struct {
		pcts []int
		want int
	}{
		{[]int{90, 85, 80, 75}, 3},
		{[]int{90, 78, 80, 75}, 1},
		{[]int{75}, 0},
		{nil, 0},
	}
	for _, c := range cases {
		if got := confidenceDeclineStreak(c.pcts); got != c.want {
			t.Errorf("confidenceDeclineStreak(%v) = %d, want %d", c.pcts, got, c.want)
		}
	}
}

func TestComputeConfidence(t *testing.T) {
	t.Run("no start date is insufficient", func(t *testing.T) {
		r := computeConfidence("", "", 2, 20, 0, 0, confidenceTestDate(2026, 7, 10), nil)
		if r.ConfidenceLabel != labelInsufficient {
			t.Fatalf("label = %q", r.ConfidenceLabel)
		}
		if r.SprintDay != 0 {
			t.Fatalf("sprint day = %d, want 0", r.SprintDay)
		}
		if r.ConfidenceRationale != "No active sprint start date available — cannot estimate progress." {
			t.Fatalf("rationale = %q", r.ConfidenceRationale)
		}
	})

	// Golden addition: sprint_length_weeks=0 → total working days 0.
	t.Run("zero length sprint is insufficient", func(t *testing.T) {
		r := computeConfidence("", "2026-07-06", 0, 20, 0, 0, confidenceTestDate(2026, 7, 10), nil)
		if r.ConfidenceLabel != labelInsufficient {
			t.Fatalf("label = %q", r.ConfidenceLabel)
		}
		if r.ConfidenceRationale != "Sprint length is zero — cannot estimate progress." {
			t.Fatalf("rationale = %q", r.ConfidenceRationale)
		}
	})

	t.Run("no capacity reports day but not confidence", func(t *testing.T) {
		// Sprint started Mon; today is Wed of the same week → day 3 of 10.
		r := computeConfidence("", "2026-07-06", 2, 0, 0, 0, confidenceTestDate(2026, 7, 8), nil)
		if r.SprintDay != 3 || r.SprintTotalDays != 10 {
			t.Fatalf("day %d of %d, want 3 of 10", r.SprintDay, r.SprintTotalDays)
		}
		if r.ConfidenceLabel != labelInsufficient {
			t.Fatalf("label = %q", r.ConfidenceLabel)
		}
		want := "Day 3 of 10. No committed sprint capacity on record, so burn-down confidence can't be computed."
		if r.ConfidenceRationale != want {
			t.Fatalf("rationale = %q, want %q", r.ConfidenceRationale, want)
		}
	})

	t.Run("on track", func(t *testing.T) {
		// Day 5 of 10, capacity 20 → ideal = 10; completed 10 → 100% On track.
		r := computeConfidence("", "2026-07-06", 2, 20, 10, 5, confidenceTestDate(2026, 7, 10), nil)
		if r.SprintDay != 5 || r.ConfidencePct != 100 || r.ConfidenceLabel != labelOnTrack {
			t.Fatalf("day=%d pct=%d label=%q", r.SprintDay, r.ConfidencePct, r.ConfidenceLabel)
		}
		want := "Day 5 of 10: 10 of ~10 ideal points burned (100%)."
		if r.ConfidenceRationale != want {
			t.Fatalf("rationale = %q, want %q", r.ConfidenceRationale, want)
		}
	})

	t.Run("at risk", func(t *testing.T) {
		// Day 5 of 10, ideal 10, completed 8 → 80% At risk.
		r := computeConfidence("", "2026-07-06", 2, 20, 8, 3, confidenceTestDate(2026, 7, 10), nil)
		if r.ConfidencePct != 80 || r.ConfidenceLabel != labelAtRisk {
			t.Fatalf("pct=%d label=%q", r.ConfidencePct, r.ConfidenceLabel)
		}
	})

	t.Run("behind", func(t *testing.T) {
		// Day 5 of 10, ideal 10, completed 4 → 40% Behind.
		r := computeConfidence("", "2026-07-06", 2, 20, 4, 2, confidenceTestDate(2026, 7, 10), nil)
		if r.ConfidencePct != 40 || r.ConfidenceLabel != labelBehind {
			t.Fatalf("pct=%d label=%q", r.ConfidencePct, r.ConfidenceLabel)
		}
	})

	t.Run("ahead is capped at 100", func(t *testing.T) {
		r := computeConfidence("", "2026-07-06", 2, 20, 18, 5, confidenceTestDate(2026, 7, 8), nil)
		if r.ConfidencePct != 100 || r.ConfidenceLabel != labelOnTrack {
			t.Fatalf("pct=%d label=%q", r.ConfidencePct, r.ConfidenceLabel)
		}
	})

	t.Run("silence penalty past day one", func(t *testing.T) {
		// Day 5, would be 100% on track, but zero activity → *0.7 = 70.
		r := computeConfidence("", "2026-07-06", 2, 20, 10, 0, confidenceTestDate(2026, 7, 10), nil)
		if r.ConfidencePct != 70 {
			t.Fatalf("pct = %d, want 70", r.ConfidencePct)
		}
		want := "Day 5 of 10: 10 of ~10 ideal points burned (70%). No recent activity detected — work may be stalled."
		if r.ConfidenceRationale != want {
			t.Fatalf("rationale = %q, want %q", r.ConfidenceRationale, want)
		}
	})

	// Golden addition: the silence penalty crossing a .5 boundary uses banker's
	// rounding — 75 * 0.7 = 52.5 → 52 (int(round()) is half-even).
	t.Run("silence penalty rounds half even", func(t *testing.T) {
		r := computeConfidence("", "2026-07-06", 2, 20, 7.5, 0, confidenceTestDate(2026, 7, 10), nil)
		if r.ConfidencePct != 52 {
			t.Fatalf("pct = %d, want 52", r.ConfidencePct)
		}
		want := "Day 5 of 10: 8 of ~10 ideal points burned (52%). No recent activity detected — work may be stalled."
		if r.ConfidenceRationale != want {
			t.Fatalf("rationale = %q, want %q", r.ConfidenceRationale, want)
		}
	})

	// Golden additions: f"{x:.0f}" is a correctly-rounded half-even conversion —
	// completed 2.5 renders "2" and 3.5 renders "4"; ideal 12.5 renders "12".
	t.Run("rationale formats completed half even down", func(t *testing.T) {
		r := computeConfidence("", "2026-07-06", 2, 20, 2.5, 5, confidenceTestDate(2026, 7, 10), nil)
		want := "Day 5 of 10: 2 of ~10 ideal points burned (25%)."
		if r.ConfidenceRationale != want {
			t.Fatalf("rationale = %q, want %q", r.ConfidenceRationale, want)
		}
	})
	t.Run("rationale formats completed half even up", func(t *testing.T) {
		r := computeConfidence("", "2026-07-06", 2, 20, 3.5, 5, confidenceTestDate(2026, 7, 10), nil)
		want := "Day 5 of 10: 4 of ~10 ideal points burned (35%)."
		if r.ConfidenceRationale != want {
			t.Fatalf("rationale = %q, want %q", r.ConfidenceRationale, want)
		}
	})
	t.Run("rationale formats ideal half even", func(t *testing.T) {
		// Capacity 25, day 5 of 10 → ideal 12.5 → "12"; 10/12.5 → 80%.
		r := computeConfidence("", "2026-07-06", 2, 25, 10, 5, confidenceTestDate(2026, 7, 10), nil)
		want := "Day 5 of 10: 10 of ~12 ideal points burned (80%)."
		if r.ConfidenceRationale != want {
			t.Fatalf("rationale = %q, want %q", r.ConfidenceRationale, want)
		}
		if r.ConfidenceLabel != labelAtRisk {
			t.Fatalf("label = %q", r.ConfidenceLabel)
		}
	})

	// Golden additions: a weekend `today` — elapsed working days stay at
	// Friday's count, pinning the Python↔Go weekday conversion inside compute.
	t.Run("saturday today counts through friday", func(t *testing.T) {
		r := computeConfidence("", "2026-07-06", 2, 20, 10, 5, confidenceTestDate(2026, 7, 11), nil)
		if r.SprintDay != 5 || r.ConfidencePct != 100 {
			t.Fatalf("day=%d pct=%d, want day=5 pct=100", r.SprintDay, r.ConfidencePct)
		}
	})
	t.Run("sunday today counts through friday", func(t *testing.T) {
		r := computeConfidence("", "2026-07-06", 2, 20, 10, 5, confidenceTestDate(2026, 7, 12), nil)
		if r.SprintDay != 5 || r.ConfidencePct != 100 {
			t.Fatalf("day=%d pct=%d, want day=5 pct=100", r.SprintDay, r.ConfidencePct)
		}
	})
}

func TestConfidenceTrend(t *testing.T) {
	today := confidenceTestDate(2026, 7, 10)

	t.Run("no history unchanged", func(t *testing.T) {
		r := confidenceTestCompute(nil, 10, 5, today)
		if r.ConfidenceTrend != "" || r.ConfidenceDelta != 0 {
			t.Fatalf("trend=%q delta=%d", r.ConfidenceTrend, r.ConfidenceDelta)
		}
		if strings.Contains(r.ConfidenceRationale, "since the last standup") {
			t.Fatalf("rationale = %q", r.ConfidenceRationale)
		}
	})

	t.Run("steady band", func(t *testing.T) {
		r := confidenceTestCompute(confidenceTestHist(
			confidenceTestRow{"2026-07-09", confidenceTestPct(99), "success"},
		), 10, 5, today)
		if r.ConfidenceTrend != trendSteady || r.ConfidenceDelta != 1 {
			t.Fatalf("trend=%q delta=%d", r.ConfidenceTrend, r.ConfidenceDelta)
		}
		if strings.Contains(r.ConfidenceRationale, "since the last standup") {
			t.Fatalf("rationale = %q", r.ConfidenceRationale)
		}
	})

	t.Run("improving adds rationale", func(t *testing.T) {
		r := confidenceTestCompute(confidenceTestHist(
			confidenceTestRow{"2026-07-09", confidenceTestPct(80), "success"},
		), 10, 5, today)
		if r.ConfidenceTrend != trendImproving || r.ConfidenceDelta != 20 {
			t.Fatalf("trend=%q delta=%d", r.ConfidenceTrend, r.ConfidenceDelta)
		}
		if !strings.Contains(r.ConfidenceRationale, "Up 20 pts since the last standup.") {
			t.Fatalf("rationale = %q", r.ConfidenceRationale)
		}
	})

	t.Run("single decline no dampen", func(t *testing.T) {
		// Base pct 75 (7.5/10 ideal → 75).
		r := confidenceTestCompute(confidenceTestHist(
			confidenceTestRow{"2026-07-09", confidenceTestPct(90), "success"},
		), 7.5, 5, today)
		if r.ConfidencePct != 75 { // no damping on a single drop
			t.Fatalf("pct = %d, want 75", r.ConfidencePct)
		}
		if r.ConfidenceTrend != trendDeclining || r.ConfidenceDelta != -15 {
			t.Fatalf("trend=%q delta=%d", r.ConfidenceTrend, r.ConfidenceDelta)
		}
		if !strings.Contains(r.ConfidenceRationale, "Down 15 pts since the last standup.") {
			t.Fatalf("rationale = %q", r.ConfidenceRationale)
		}
	})

	t.Run("three drop streak dampens", func(t *testing.T) {
		history := confidenceTestHist(
			confidenceTestRow{"2026-07-09", confidenceTestPct(80), "success"},
			confidenceTestRow{"2026-07-08", confidenceTestPct(85), "success"},
			confidenceTestRow{"2026-07-07", confidenceTestPct(90), "success"},
		)
		r := confidenceTestCompute(history, 7.5, 5, today) // base 75 < 80 → third straight drop
		if r.ConfidencePct != 68 {                         // 75 * 0.9 = 67.5 → 68
			t.Fatalf("pct = %d, want 68", r.ConfidencePct)
		}
		// Golden addition: the exact whole rationale, streak sentence included.
		want := "Day 5 of 10: 8 of ~10 ideal points burned (68%). Confidence has declined 3 standups in a row."
		if r.ConfidenceRationale != want {
			t.Fatalf("rationale = %q, want %q", r.ConfidenceRationale, want)
		}
		// Delta reflects the displayed (post-dampen) number, one trend sentence only.
		if r.ConfidenceDelta != 68-80 || r.ConfidenceTrend != trendDeclining {
			t.Fatalf("delta=%d trend=%q", r.ConfidenceDelta, r.ConfidenceTrend)
		}
		if strings.Contains(r.ConfidenceRationale, "Down ") {
			t.Fatalf("rationale = %q", r.ConfidenceRationale)
		}
	})

	t.Run("streak broken by rise no dampen", func(t *testing.T) {
		history := confidenceTestHist(
			confidenceTestRow{"2026-07-09", confidenceTestPct(80), "success"},
			confidenceTestRow{"2026-07-08", confidenceTestPct(78), "success"}, // rose 78→80: streak resets
			confidenceTestRow{"2026-07-07", confidenceTestPct(90), "success"},
		)
		r := confidenceTestCompute(history, 7.5, 5, today)
		if r.ConfidencePct != 75 {
			t.Fatalf("pct = %d, want 75", r.ConfidencePct)
		}
	})

	t.Run("trend points filters", func(t *testing.T) {
		history := confidenceTestHist(
			confidenceTestRow{"2026-07-10", confidenceTestPct(40), "success"}, // today — excluded (same-day rerun)
			confidenceTestRow{"2026-07-09", confidenceTestPct(95), "failed"},  // failed run — excluded
			confidenceTestRow{"2026-07-09", confidenceTestPct(80), "success"}, // kept for 07-09
			confidenceTestRow{"2026-07-08", confidenceTestPct(0), "success"},  // pct 0 (insufficient data) — excluded
			confidenceTestRow{"2026-07-07", confidenceTestPct(90), "success"},
		)
		points := confidenceTrendPoints(history, today)
		if len(points) != 2 || points[0] != 90 || points[1] != 80 {
			t.Fatalf("points = %v, want [90 80]", points)
		}
	})

	t.Run("same date rerun dedupes newest wins", func(t *testing.T) {
		history := confidenceTestHist(
			confidenceTestRow{"2026-07-09", confidenceTestPct(85), "success"}, // newest rerun for 07-09
			confidenceTestRow{"2026-07-09", confidenceTestPct(60), "success"},
		)
		points := confidenceTrendPoints(history, today)
		if len(points) != 1 || points[0] != 85 {
			t.Fatalf("points = %v, want [85]", points)
		}
	})

	// Golden additions: the int(row.get("confidence_pct") or 0) coercion —
	// numeric strings parse, unparseable values skip the row (try/except
	// TypeError/ValueError → continue), falsy values coerce to 0 and drop
	// under the pct > 0 filter.
	t.Run("pct coercion mirrors int-or-zero", func(t *testing.T) {
		history := confidenceTestHist(
			confidenceTestRow{"2026-07-09", "85", "success"},                    // int("85") → kept
			confidenceTestRow{"2026-07-08", "n/a", "success"},                   // ValueError → skipped
			confidenceTestRow{"2026-07-07", []any{json.Number("7")}, "success"}, // TypeError → skipped
			confidenceTestRow{"2026-07-06", nil, "success"},                     // None or 0 → 0 → excluded
			confidenceTestRow{"2026-07-03", confidenceTestPct(70), "success"},
		)
		points := confidenceTrendPoints(history, today)
		if len(points) != 2 || points[0] != 70 || points[1] != 85 {
			t.Fatalf("points = %v, want [70 85]", points)
		}
	})

	t.Run("insufficient data paths untouched", func(t *testing.T) {
		r := computeConfidence("", "", 2, 0, 0, 0, today, confidenceTestHist(
			confidenceTestRow{"2026-07-09", confidenceTestPct(80), "success"},
		))
		if r.ConfidenceLabel != labelInsufficient || r.ConfidenceTrend != "" || r.ConfidenceDelta != 0 {
			t.Fatalf("label=%q trend=%q delta=%d", r.ConfidenceLabel, r.ConfidenceTrend, r.ConfidenceDelta)
		}
	})
}
