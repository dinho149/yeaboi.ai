// Port of src/yeaboi/poker/export.py (the pure builders: _title, _pts,
// _votes_str, build_poker_markdown, _ticket_payload, poker_export_args) and
// poker/store.report_from_dict — keep in lockstep; the Python modules are
// the reference implementation and tests/parity/test_exports_parity.py
// diffs whole-seam output.
package exports

import (
	"strconv"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// pokerVote mirrors state.PokerVote after the report_from_dict round-trip.
type pokerVote struct {
	voter, avatar, value any
}

// pokerTicket mirrors state.PokerTicketResult after the round-trip.
// initial/final hold float64 or nil (the _float_or_none widening — a wire
// integer 3 becomes 3.0 downstream, which is why the payload renders them
// through FloatRepr rather than echoing the wire literal).
type pokerTicket struct {
	key, url, summary, description, state, assignee any
	initial, final                                  any
	estimated                                       bool
	votes                                           []pokerVote
	aiNote, duelTranscript, duelLow, duelHigh       any
}

// pokerReport mirrors state.PokerReport after the round-trip.
type pokerReport struct {
	date, sessionID, projectName, source, scopeLabel, generatedAt any
	tickets                                                       []*pokerTicket
	participants                                                  []any
}

// pokerTicketFrom ports store.report_from_dict's _ticket (with _vote and
// _float_or_none inside).
func pokerTicketFrom(v any) (*pokerTicket, error) {
	t := pysem.AsObj(v)
	if t == nil {
		return nil, &pysem.Error{Class: "AttributeError", Msg: "ticket rows must be objects"}
	}
	ticket := &pokerTicket{
		key:            t.GetDefault("key", ""),
		url:            t.GetDefault("url", ""),
		summary:        t.GetDefault("summary", ""),
		description:    t.GetDefault("description", ""),
		state:          t.GetDefault("state", ""),
		assignee:       t.GetDefault("assignee", ""),
		initial:        pyFloatOrNil(t.Get("initial_points")),
		final:          pyFloatOrNil(t.Get("final_points")),
		estimated:      pysem.Truthy(t.Get("estimated")),
		aiNote:         t.GetDefault("ai_note", ""),
		duelTranscript: t.GetDefault("duel_transcript", ""),
		duelLow:        t.GetDefault("duel_low", ""),
		duelHigh:       t.GetDefault("duel_high", ""),
	}
	votes, err := iterField(t, "votes")
	if err != nil {
		return nil, err
	}
	for _, raw := range votes {
		vo := pysem.AsObj(raw)
		if vo == nil {
			return nil, &pysem.Error{Class: "AttributeError", Msg: "vote rows must be objects"}
		}
		ticket.votes = append(ticket.votes, pokerVote{
			voter:  vo.GetDefault("voter", ""),
			avatar: vo.GetDefault("avatar", ""),
			value:  vo.GetDefault("value", ""),
		})
	}
	return ticket, nil
}

// pokerReportFrom ports store.report_from_dict.
func pokerReportFrom(d *pysem.Obj) (*pokerReport, error) {
	report := &pokerReport{
		date:         d.GetDefault("date", ""),
		sessionID:    d.GetDefault("session_id", ""),
		projectName:  d.GetDefault("project_name", ""),
		source:       d.GetDefault("source", ""),
		scopeLabel:   d.GetDefault("scope_label", ""),
		generatedAt:  d.GetDefault("generated_at", ""),
		participants: []any{},
	}
	tickets, err := iterField(d, "tickets")
	if err != nil {
		return nil, err
	}
	for _, raw := range tickets {
		ticket, err := pokerTicketFrom(raw)
		if err != nil {
			return nil, err
		}
		report.tickets = append(report.tickets, ticket)
	}
	participants, err := iterField(d, "participants")
	if err != nil {
		return nil, err
	}
	report.participants = participants
	return report, nil
}

// pokerTitle ports export._title.
func pokerTitle(r *pokerReport) string {
	if pysem.Truthy(r.scopeLabel) {
		return "Planning Poker — " + pysem.Str(r.scopeLabel)
	}
	return "Planning Poker"
}

// ptsStr ports export._pts — "—" for none, ints without the trailing .0.
// value is float64 or nil (post-_float_or_none), never a wire literal.
func ptsStr(value any) string {
	f, ok := value.(float64)
	if !ok {
		return "—"
	}
	if f == float64(int64(f)) {
		return strconv.FormatInt(int64(f), 10)
	}
	return pysem.FloatRepr(f)
}

// votesStr ports export._votes_str — `Alex 5 · Sam 8` (empty if none).
func votesStr(t *pokerTicket) string {
	parts := []string{}
	for _, v := range t.votes {
		if pysem.Truthy(v.value) {
			parts = append(parts, pysem.Str(v.voter)+" "+pysem.Str(v.value))
		}
	}
	return strings.Join(parts, " · ")
}

// estimatedCount is the shared `sum(1 for t in tickets if t.estimated)`.
func estimatedCount(r *pokerReport) int {
	count := 0
	for _, t := range r.tickets {
		if t.estimated {
			count++
		}
	}
	return count
}

// buildPokerMarkdown ports export.build_poker_markdown. generatedTS is the
// wire-pinned footer timestamp (see buildRetroMarkdown on the unreachable
// "empty means now" fallback).
func buildPokerMarkdown(r *pokerReport, generatedTS any) string {
	estimated := estimatedCount(r)
	lines := []string{
		"# " + pokerTitle(r),
		"",
		"**Date:** " + pysem.Str(r.date) + "  ",
		"**Source:** " + orDash(r.source) + " · " + orDash(r.scopeLabel) + "  ",
		"**Estimated:** " + strconv.Itoa(estimated) + "/" + strconv.Itoa(len(r.tickets)) + " tickets  ",
		"**Participants:** " + joinOrDash(r.participants),
		"",
		"| Ticket | Summary | Before | Final | Votes |",
		"|--------|---------|--------|-------|-------|",
	}
	for _, t := range r.tickets {
		// SafeURL: tracker URLs are attacker-influenced, and a Markdown link
		// becomes an <a href> downstream (Notion/Confluence/GitHub).
		key := pysem.Str(t.key)
		if safe := SafeURL(t.url); safe != "" {
			key = "[" + pysem.Str(t.key) + "](" + safe + ")"
		}
		final := "_skipped_"
		if t.estimated {
			final = ptsStr(t.final)
		}
		votes := votesStr(t)
		if votes == "" {
			votes = "—"
		}
		lines = append(lines,
			"| "+key+" | "+mdTableCell(t.summary)+" | "+ptsStr(t.initial)+" | "+final+" | "+mdTableCell(votes)+" |")
	}
	lines = append(lines, "")

	aiNotes := [][2]any{}
	for _, t := range r.tickets {
		if pysem.Truthy(t.aiNote) {
			aiNotes = append(aiNotes, [2]any{t.key, t.aiNote})
		}
	}
	if len(aiNotes) > 0 {
		lines = append(lines, "## AI perspectives", "")
		for _, pair := range aiNotes {
			lines = append(lines, "- **"+pysem.Str(pair[0])+"** — "+mdTableCell(pair[1]))
		}
		lines = append(lines, "")
	}

	duels := []*pokerTicket{}
	for _, t := range r.tickets {
		if pysem.Truthy(t.duelTranscript) {
			duels = append(duels, t)
		}
	}
	if len(duels) > 0 {
		lines = append(lines, "## Duels", "")
		for _, t := range duels {
			lines = append(lines, "**"+pysem.Str(t.key)+"** — "+pysem.Str(t.duelLow)+" vs "+pysem.Str(t.duelHigh), "")
			for _, line := range pysem.Splitlines(pysem.Str(t.duelTranscript)) {
				lines = append(lines, "> "+line)
			}
			lines = append(lines, "")
		}
	}

	lines = append(lines, "---", "", "🤙 _Generated by [yeaboi.ai](https://yeaboi.ai) · "+pysem.Str(generatedTS)+"_", "")
	return strings.Join(lines, "\n")
}

// ticketPayload ports export._ticket_payload — one ticket as data. `final`
// is nil whenever the room skipped the ticket, even if a stale final_points
// survived on the record.
func ticketPayload(t *pokerTicket) *pysem.Obj {
	out := pysem.EmptyObj()
	out.Set("key", t.key)
	out.Set("summary", t.summary)
	out.Set("before", numOrNil(t.initial))
	if t.estimated {
		out.Set("final", numOrNil(t.final))
	} else {
		out.Set("final", nil)
	}
	out.Set("estimated", t.estimated)
	votes := []any{}
	for _, v := range t.votes {
		if pysem.Truthy(v.value) {
			vote := pysem.EmptyObj()
			vote.Set("voter", v.voter)
			vote.Set("value", pysem.Str(v.value))
			votes = append(votes, vote)
		}
	}
	out.Set("votes", votes)
	// SafeURL here, not in the bundle's safeUrl, only because the tracker URL
	// is also what the Markdown twin links — one allowlist, both artifacts.
	if safe := SafeURL(t.url); safe != "" {
		out.Set("url", safe)
	}
	if pysem.Truthy(t.aiNote) {
		out.Set("aiNote", t.aiNote)
	}
	if pysem.Truthy(t.duelTranscript) {
		duel := pysem.EmptyObj()
		duel.Set("low", t.duelLow)
		duel.Set("high", t.duelHigh)
		duel.Set("transcript", t.duelTranscript)
		out.Set("duel", duel)
	}
	return out
}

// pokerExportArgs ports export.poker_export_args — the chrome + payload
// keyword arguments for one poker document. Poker has no editable share, so
// there is no editable flag here.
func pokerExportArgs(r *pokerReport, history []any, generatedDate any) (*pysem.Obj, error) {
	estimated := estimatedCount(r)
	tickets := []any{}
	hasAI, hasDuel := false, false
	for _, t := range r.tickets {
		payload := ticketPayload(t)
		if payload.Has("aiNote") {
			hasAI = true
		}
		if payload.Has("duel") {
			hasDuel = true
		}
		tickets = append(tickets, payload)
	}

	// The contents links are built here because the shell renders before the
	// report does; which sections exist is a fact about the payload either way.
	nav := []any{[]any{"overview", "Overview"}, []any{"tickets", "Tickets"}}
	if hasAI {
		nav = append(nav, []any{"ai", "AI perspectives"})
	}
	if hasDuel {
		nav = append(nav, []any{"duels", "Duels"})
	}

	args := pysem.EmptyObj()
	args.Set("mode", "poker")
	args.Set("title", pokerTitle(r))
	args.Set("wordmark", "poker")
	args.Set("facts", []any{
		[]any{"SOURCE", orEmpty(r.source)},
		[]any{"SCOPE", orEmpty(r.scopeLabel)},
		[]any{"DATE", orEmpty(r.date)},
		[]any{"ESTIMATED", strconv.Itoa(estimated) + "/" + strconv.Itoa(len(r.tickets))},
	})
	args.Set("nav", nav)

	report := pysem.EmptyObj()
	report.Set("kind", "poker")
	report.Set("tickets", tickets)
	report.Set("participants", append([]any{}, r.participants...))
	trend, err := trendPayload(
		history, "poker_date", "estimated_count", "Estimation trend", "Tickets estimated",
		r.date, r.date, float64(estimated),
	)
	if err != nil {
		return nil, err
	}
	report.Set("trend", trend)
	args.Set("report", report)
	args.Set("footer", "Generated by yeaboi.ai • "+pysem.Str(generatedDate))
	return args, nil
}
