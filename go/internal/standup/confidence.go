// confidence.go — port of src/yeaboi/standup/confidence.py. Keep in lockstep: the Python module is the reference implementation; tests/parity/test_standup_parity.py diffs whole-pipeline output.
//
// Deterministic sprint-day and confidence scoring for Daily Standup mode.
//
// No LLM is involved here — confidence is pure arithmetic over the sprint's
// ideal burn-down, so it's cheap, fast, and unit-testable. The engine calls
// compute() and drops the result straight onto the StandupReport.
//
// Model:
//   - Sprint day = working days elapsed since the sprint start (Mon-Fri, minus
//     bank holidays), 1-indexed, capped at the sprint's total working days.
//   - Confidence = actual completed points vs the *ideal linear burn* for the
//     day. On day D of a T-day sprint with capacity C, you'd ideally have
//     burned C * D / T points. completed / ideal → a ratio, bucketed into
//     On track / At risk / Behind. A dead-quiet sprint (no recent activity past
//     day 1) is nudged down because silence usually means stalled work.

package standup

import (
	"fmt"
	"math"
	"time"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// Confidence buckets (percent of ideal burn achieved).
const (
	confidenceOnTrackMin = 90
	confidenceAtRiskMin  = 70
)

// Trend thresholds over previous standups' recorded pcts.
const (
	confidenceTrendSteadyBand  = 2   // |delta| ≤ this vs the last standup reads as "steady"
	confidenceDeclineStreakMin = 3   // consecutive strict declines (ending today) before we dampen
	confidenceDeclineDampen    = 0.9 // sustained slide → today's pct is knocked down 10%
)

const (
	trendImproving = "improving"
	trendSteady    = "steady"
	trendDeclining = "declining"
)

const (
	labelOnTrack      = "On track"
	labelAtRisk       = "At risk"
	labelBehind       = "Behind"
	labelInsufficient = "Insufficient data"
)

// sprintProgress mirrors confidence.SprintProgress — the result of a
// confidence computation, mirroring the StandupReport fields. The Python
// dataclass defaults confidence_label to "Insufficient data"; Go's zero value
// is "", so every construction site sets ConfidenceLabel explicitly.
type sprintProgress struct {
	SprintDay           int
	SprintTotalDays     int
	ConfidencePct       int
	ConfidenceDelta     int // final pct minus the previous standup's pct (0 without usable history)
	ConfidenceLabel     string
	ConfidenceRationale string
	ConfidenceTrend     string // trend* constant, or "" when there is no usable history
}

// workingDaysBetween mirrors confidence.working_days_between: count Mon-Fri
// days in [start, end] inclusive, excluding holidays. Returns 0 when
// end < start. Dates are civil dates carried in UTC-midnight time.Time values
// (holiday keys must be the same shape).
func workingDaysBetween(start, end time.Time, holidays map[time.Time]bool) int {
	if end.Before(start) {
		return 0
	}
	count := 0
	d := start
	for !d.After(end) {
		// Python date.weekday() is Mon=0..Sun=6; Go time.Weekday is Sun=0.
		// (wd+6)%7 converts Go's numbering to Python's, so "< 5" keeps Mon-Fri.
		wd := int((d.Weekday() + 6) % 7)
		if wd < 5 && !holidays[d] { // Mon=0 .. Fri=4
			count++
		}
		d = d.AddDate(0, 0, 1)
	}
	return count
}

// confidenceParseDate mirrors confidence._parse_date: parse a YYYY-MM-DD (or
// ISO datetime) string to a date, or ok=false. Python slices value[:10] by
// codepoint before date.fromisoformat; here the first 10 runes go through
// time.Parse and any error means None.
func confidenceParseDate(value string) (time.Time, bool) {
	if value == "" {
		return time.Time{}, false
	}
	r := []rune(value)
	if len(r) > 10 {
		r = r[:10]
	}
	t, err := time.Parse("2006-01-02", string(r))
	if err != nil {
		return time.Time{}, false
	}
	return t, true
}

// confidenceTrendPoints mirrors confidence._trend_points: usable
// previous-standup pcts, oldest→newest, from StandupStore.get_history rows.
//
// Filters: status success/partial only, standup_date strictly before today (a
// same-day earlier rerun is not "the previous standup"), and pct > 0 —
// "Insufficient data" runs record 0, and letting them into the trend would
// fabricate a collapse/recovery around a capacity-less day. Rows arrive
// newest-first; same-date reruns dedupe keeping the newest.
func confidenceTrendPoints(history []*pysem.Obj, today time.Time) []int {
	seenDates := map[string]bool{}
	type dayPct struct {
		day string
		pct int
	}
	newestFirst := []dayPct{}
	for _, row := range history {
		if s := strOr(row, "status"); s != "success" && s != "partial" {
			continue
		}
		day := strOr(row, "standup_date")
		parsed, ok := confidenceParseDate(day)
		if !ok || !parsed.Before(today) {
			continue
		}
		// Python: int(row.get("confidence_pct") or 0) inside try/except
		// (TypeError, ValueError) → skip the row on either error class.
		pct64, err := pysem.IntOrZero(row.Get("confidence_pct"))
		if err != nil {
			continue
		}
		pct := int(pct64)
		if pct <= 0 || seenDates[day] {
			continue
		}
		seenDates[day] = true
		newestFirst = append(newestFirst, dayPct{day: day, pct: pct})
	}
	out := make([]int, 0, len(newestFirst))
	for i := len(newestFirst) - 1; i >= 0; i-- {
		out = append(out, newestFirst[i].pct)
	}
	return out
}

// confidenceDeclineStreak mirrors confidence._decline_streak: number of
// consecutive strict day-over-day drops ending at the last element.
func confidenceDeclineStreak(pcts []int) int {
	streak := 0
	for i := len(pcts) - 1; i > 0; i-- {
		if pcts[i] < pcts[i-1] {
			streak++
		} else {
			break
		}
	}
	return streak
}

// computeConfidence mirrors confidence.compute: sprint day + confidence from
// sprint dates, burn-down, and prior standups.
//
//   - startDate: sprint start (ISO). Empty → "insufficient data".
//   - sprintLengthWeeks: sprint length; total working days = weeks * 5.
//   - capacityPoints: total points committed for the sprint.
//   - completedPoints: points marked Done so far.
//   - activityCount: number of recent-activity items detected (drives the
//     silence penalty).
//   - today: the civil date, carried in a UTC-midnight time.Time. The Python
//     default (date.today()) never applies — the aggregate seam always passes
//     it.
//   - history: previous runs' metadata rows (StandupStore.get_history shape,
//     newest-first) as ordered JSON objects. Feeds the trend: today's number is
//     still burn-down arithmetic, but a sustained slide across standups dampens
//     it and the rationale explains the day-over-day movement.
//
// The Python `holidays` parameter is omitted: the engine never passes it, so
// the reference always runs with an empty holiday set here.
func computeConfidence(
	sprintName string,
	startDate string,
	sprintLengthWeeks int,
	capacityPoints float64,
	completedPoints float64,
	activityCount int,
	today time.Time,
	history []*pysem.Obj,
) *sprintProgress {
	_ = sprintName // Python only feeds sprint_name to logger.info; the port does not log.

	start, ok := confidenceParseDate(startDate)
	if !ok {
		return &sprintProgress{
			ConfidenceLabel:     labelInsufficient,
			ConfidenceRationale: "No active sprint start date available — cannot estimate progress.",
		}
	}

	// Total working days across the whole sprint (weeks * 5).
	sprintEnd := start.AddDate(0, 0, sprintLengthWeeks*7-1)
	totalDays := workingDaysBetween(start, sprintEnd, nil)
	if totalDays <= 0 {
		return &sprintProgress{
			ConfidenceLabel:     labelInsufficient,
			ConfidenceRationale: "Sprint length is zero — cannot estimate progress.",
		}
	}

	// Working days elapsed through today, clamped into [1, total_days].
	end := today
	if sprintEnd.Before(today) { // min(today, sprint_end)
		end = sprintEnd
	}
	elapsed := workingDaysBetween(start, end, nil)
	sprintDay := elapsed
	if sprintDay > totalDays {
		sprintDay = totalDays
	}
	if sprintDay < 1 { // max(1, min(elapsed, total_days))
		sprintDay = 1
	}

	// Without a committed capacity we can still report the sprint day, but not
	// a burn-based confidence — say so rather than inventing a number.
	if capacityPoints <= 0 {
		return &sprintProgress{
			SprintDay:       sprintDay,
			SprintTotalDays: totalDays,
			ConfidenceLabel: labelInsufficient,
			ConfidenceRationale: fmt.Sprintf(
				"Day %d of %d. No committed sprint capacity on record, "+
					"so burn-down confidence can't be computed.",
				sprintDay, totalDays,
			),
		}
	}

	idealPoints := capacityPoints * float64(sprintDay) / float64(totalDays)
	// Ratio of achieved to ideal; being ahead is capped at 1.0 (100%).
	ratio := 1.0
	if idealPoints > 0 {
		ratio = completedPoints / idealPoints
	}
	pct := pysem.RoundInt(math.Min(ratio, 1.0) * 100)

	// Silence penalty: past the first day, zero recent activity usually means
	// stalled work — knock confidence down and note it.
	silenceNote := ""
	if sprintDay > 1 && activityCount == 0 {
		pct = pysem.RoundInt(float64(pct) * 0.7)
		silenceNote = " No recent activity detected — work may be stalled."
	}

	// Trend vs previous standups: today's pct stays burn-down arithmetic, but a
	// sustained slide (3+ strict drops in a row, counting today) dampens it —
	// momentum is signal the single-day snapshot can't see. Never boosts.
	trend := ""
	delta := 0
	trendNote := ""
	points := confidenceTrendPoints(history, today)
	if len(points) > 0 {
		withToday := append(append([]int{}, points...), pct)
		streak := confidenceDeclineStreak(withToday)
		if streak >= confidenceDeclineStreakMin {
			pct = pysem.RoundInt(float64(pct) * confidenceDeclineDampen)
			if pct < 0 { // max(0, ...)
				pct = 0
			}
			trendNote = fmt.Sprintf(" Confidence has declined %d standups in a row.", streak)
		}
		// Delta uses the final (post-dampen) pct so the displayed movement
		// always matches the displayed number.
		delta = pct - points[len(points)-1]
		absDelta := delta
		if absDelta < 0 {
			absDelta = -absDelta
		}
		switch {
		case absDelta <= confidenceTrendSteadyBand:
			trend = trendSteady
		case delta > 0:
			trend = trendImproving
			trendNote = fmt.Sprintf(" Up %d pts since the last standup.", delta)
		default:
			trend = trendDeclining
			if trendNote == "" { // the streak sentence already explains the slide
				trendNote = fmt.Sprintf(" Down %d pts since the last standup.", absDelta)
			}
		}
	}

	var label string
	switch {
	case pct >= confidenceOnTrackMin:
		label = labelOnTrack
	case pct >= confidenceAtRiskMin:
		label = labelAtRisk
	default:
		label = labelBehind
	}

	rationale := fmt.Sprintf(
		"Day %d of %d: %s of ~%s ideal points burned (%d%%).%s%s",
		sprintDay, totalDays,
		pysem.Format0f(completedPoints), pysem.Format0f(idealPoints),
		pct, silenceNote, trendNote,
	)
	return &sprintProgress{
		SprintDay:           sprintDay,
		SprintTotalDays:     totalDays,
		ConfidencePct:       pct,
		ConfidenceLabel:     label,
		ConfidenceRationale: rationale,
		ConfidenceDelta:     delta,
		ConfidenceTrend:     trend,
	}
}
