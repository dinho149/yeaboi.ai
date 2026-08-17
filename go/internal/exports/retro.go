// Port of src/yeaboi/retro/export.py (the pure builders: _title, _stem's
// callers stay Python, _reactions_str, build_retro_markdown, _card_payload,
// retro_export_args), retro/board.py's grid/status constants,
// agent/state.RetroReport.by_grid and retro/store.report_from_dict — keep in
// lockstep; the Python modules are the reference implementation and
// tests/parity/test_exports_parity.py diffs whole-seam output.
package exports

import (
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// retroGrids ports board.RETRO_GRIDS — the four authoring grids, in board
// order (also the contractual column order of the export payload).
var retroGrids = []string{"went_well", "didnt_go_well", "action_items", "demos"}

// retroGridLabels ports board.RETRO_GRID_LABELS.
var retroGridLabels = map[string]string{
	"went_well":     "What went well",
	"didnt_go_well": "What didn't go well",
	"action_items":  "Action items",
	"demos":         "Demos",
}

// carriedStatusLabels ports board.CARRIED_STATUS_LABELS.
var carriedStatusLabels = map[string]string{
	"pending":      "Pending",
	"done":         "Done",
	"in_progress":  "In Progress",
	"carried_over": "Carried Over",
	"not_relevant": "Not Relevant",
}

// reaction is one (emoji, count) pair after store deserialization —
// str()/int() already applied, exactly as report_from_dict rebuilds them.
type reaction struct {
	emoji string
	count int64
}

// retroCard mirrors state.RetroCard after the report_from_dict round-trip.
// Scalar fields stay `any`: the reference stores whatever .get returned and
// str()s only at the f-string render sites.
type retroCard struct {
	id, grid, text, author, createdAt, origin, status any
	reactions                                         []reaction
}

// retroReport mirrors state.RetroReport after the round-trip.
type retroReport struct {
	date, sessionID, projectName, sprintName, generatedAt any
	cards, carried                                        []*retroCard
	participants                                          []any
	annotations                                           []annotation
}

// retroCardFrom ports store.report_from_dict's _card: .get() defaults for
// every field; reactions rebuilt as (str(emoji), int(count)) pairs, rows
// that are not two elements long dropped.
func retroCardFrom(v any) (*retroCard, error) {
	c := pysem.AsObj(v)
	if c == nil {
		return nil, &pysem.Error{Class: "AttributeError", Msg: "card rows must be objects"}
	}
	card := &retroCard{
		id:        c.GetDefault("id", ""),
		grid:      c.GetDefault("grid", ""),
		text:      c.GetDefault("text", ""),
		author:    c.GetDefault("author", ""),
		createdAt: c.GetDefault("created_at", ""),
		origin:    c.GetDefault("origin", "web"),
		status:    c.GetDefault("status", ""),
	}
	rows, err := iterField(c, "reactions")
	if err != nil {
		return nil, err
	}
	for _, raw := range rows {
		pair, ok := raw.([]any)
		if !ok {
			return nil, &pysem.Error{Class: "TypeError", Msg: "reaction rows must be [emoji, count] pairs"}
		}
		if len(pair) != 2 {
			continue
		}
		count, err := intCast(pair[1])
		if err != nil {
			return nil, err
		}
		card.reactions = append(card.reactions, reaction{pysem.Str(pair[0]), count})
	}
	return card, nil
}

// retroReportFrom ports store.report_from_dict.
func retroReportFrom(d *pysem.Obj) (*retroReport, error) {
	report := &retroReport{
		date:         d.GetDefault("date", ""),
		sessionID:    d.GetDefault("session_id", ""),
		projectName:  d.GetDefault("project_name", ""),
		sprintName:   d.GetDefault("sprint_name", ""),
		generatedAt:  d.GetDefault("generated_at", ""),
		annotations:  annotationsFrom(d.Get("annotations")),
		participants: []any{},
	}
	cards, err := iterField(d, "cards")
	if err != nil {
		return nil, err
	}
	for _, raw := range cards {
		card, err := retroCardFrom(raw)
		if err != nil {
			return nil, err
		}
		report.cards = append(report.cards, card)
	}
	carried, err := iterField(d, "carried_action_items")
	if err != nil {
		return nil, err
	}
	for _, raw := range carried {
		card, err := retroCardFrom(raw)
		if err != nil {
			return nil, err
		}
		report.carried = append(report.carried, card)
	}
	participants, err := iterField(d, "participants")
	if err != nil {
		return nil, err
	}
	report.participants = participants
	return report, nil
}

// retroTitle ports export._title.
func retroTitle(r *retroReport) string {
	if pysem.Truthy(r.sprintName) {
		return "Sprint Retro — " + pysem.Str(r.sprintName)
	}
	return "Sprint Retro"
}

// byGrid ports RetroReport.by_grid, restricted to the four known grids (the
// reference also buckets unknown grid keys, but nothing ever reads them —
// unknown-grid cards are dropped from columns and counted only in the CARDS
// fact and the trend's current point).
func byGrid(r *retroReport) map[string][]*retroCard {
	out := map[string][]*retroCard{}
	for _, grid := range retroGrids {
		out[grid] = []*retroCard{}
	}
	for _, c := range r.cards {
		if grid, ok := c.grid.(string); ok {
			if _, known := out[grid]; known {
				out[grid] = append(out[grid], c)
			}
		}
	}
	return out
}

// reactionsStr ports export._reactions_str — `👍 3 ❤️ 1` (empty if none).
func reactionsStr(c *retroCard) string {
	parts := []string{}
	for _, r := range c.reactions {
		if r.count != 0 {
			parts = append(parts, r.emoji+" "+pysem.Str(r.count))
		}
	}
	return strings.Join(parts, "  ")
}

// carriedStatusLabel ports CARRIED_STATUS_LABELS.get(status or "pending",
// status or "Pending").
func carriedStatusLabel(status any) string {
	if !pysem.Truthy(status) {
		return carriedStatusLabels["pending"]
	}
	if s, ok := status.(string); ok {
		if label, known := carriedStatusLabels[s]; known {
			return label
		}
	}
	return pysem.Str(status)
}

// buildRetroMarkdown ports export.build_retro_markdown. generatedTS is the
// wire-pinned footer timestamp — always non-empty on this path
// (build_retro_export_inputs stamps it), so the reference's "empty means
// now" fallback is unreachable here and stays unported (this seam has no
// clock).
func buildRetroMarkdown(r *retroReport, generatedTS any) string {
	grids := byGrid(r)
	lines := []string{
		"# " + retroTitle(r),
		"",
		"**Date:** " + pysem.Str(r.date) + "  ",
		"**Participants:** " + joinOrDash(r.participants),
		"",
	}
	for _, grid := range retroGrids {
		cards := grids[grid]
		lines = append(lines, "## "+retroGridLabels[grid], "")
		if len(cards) == 0 {
			lines = append(lines, "_No cards._", "")
			continue
		}
		if grid == "action_items" {
			// Action items are to-dos — checkboxes become native task lists
			// on Notion (to_do) and Confluence (<ac:task-list>).
			for _, c := range cards {
				tag := ""
				if eqStr(c.origin, "ai") {
					tag = " _(AI)_"
				} else if pysem.Truthy(c.author) {
					tag = " — _" + pysem.Str(c.author) + "_"
				}
				lines = append(lines, "- [ ] "+pysem.Str(c.text)+tag)
			}
		} else {
			// Card grids read best as a table (author + reactions alongside).
			lines = append(lines, "| Card | By | Reactions |", "|------|----|-----------|")
			for _, c := range cards {
				by := "—"
				if eqStr(c.origin, "ai") {
					by = "_(AI)_"
				} else if pysem.Truthy(c.author) {
					by = mdTableCell(c.author)
				}
				rx := reactionsStr(c)
				if rx == "" {
					rx = "—"
				}
				lines = append(lines, "| "+mdTableCell(c.text)+" | "+by+" | "+rx+" |")
			}
		}
		lines = append(lines, "")
	}

	// Last sprint's action items + the progress the team recorded this retro.
	if len(r.carried) > 0 {
		lines = append(lines, "## Last sprint's action items — progress", "")
		for _, c := range r.carried {
			lines = append(lines, "- **["+carriedStatusLabel(c.status)+"]** "+mdTableCell(c.text))
		}
		lines = append(lines, "")
	}

	lines = append(lines, annotationsMarkdown(r.annotations)...)
	lines = append(lines, "---", "", "🤙 _Generated by [yeaboi.ai](https://yeaboi.ai) · "+pysem.Str(generatedTS)+"_", "")
	return strings.Join(lines, "\n")
}

// cardPayload ports export._card_payload — one card as data: its text, who
// wrote it, and what the room did to it.
func cardPayload(c *retroCard, editable bool, listField string) *pysem.Obj {
	out := pysem.EmptyObj()
	out.Set("text", c.text)
	rx := []any{}
	for _, r := range c.reactions {
		if r.count != 0 {
			rx = append(rx, []any{r.emoji, r.count})
		}
	}
	out.Set("reactions", rx)
	if editable && pysem.Truthy(c.id) {
		// Addressed by id, not by text — the text is the editable field, and
		// a key that moves when you edit it is not a key.
		anchor := rowAnchor(listField, "id", c.id)
		out.Set("anchor", anchor)
		out.Set("edit", editMap(anchor, c.text))
	}
	// A card the AI facilitator wrote is attributed as such, never to a
	// person — origin is the fact, and author on those rows is literal "AI".
	if eqStr(c.origin, "ai") {
		out.Set("ai", true)
	} else if pysem.Truthy(c.author) {
		out.Set("author", c.author)
	}
	return out
}

// retroExportArgs ports export.retro_export_args — the chrome + payload
// keyword arguments for one retro document. generatedDate is the
// wire-pinned footer date (see buildRetroMarkdown on the unreachable
// "empty means now" fallback).
func retroExportArgs(r *retroReport, history []any, editable bool, generatedDate any) (*pysem.Obj, error) {
	grids := byGrid(r)
	// Every column, including the empty ones, in board order — whether an
	// empty column gets a card or a footnote is the bundle's layout question.
	columns := []any{}
	for _, grid := range retroGrids {
		col := pysem.EmptyObj()
		col.Set("grid", grid)
		cards := []any{}
		for _, c := range grids[grid] {
			cards = append(cards, cardPayload(c, editable, "cards"))
		}
		col.Set("cards", cards)
		columns = append(columns, col)
	}

	args := pysem.EmptyObj()
	args.Set("mode", "retro")
	args.Set("title", retroTitle(r))
	args.Set("wordmark", "retro")
	args.Set("facts", []any{
		[]any{"DATE", orEmpty(r.date)},
		[]any{"CARDS", pysem.Str(int64(len(r.cards)))},
		[]any{"PARTICIPANTS", pysem.Str(int64(len(r.participants)))},
	})

	report := pysem.EmptyObj()
	report.Set("kind", "retro")
	report.Set("columns", columns)
	report.Set("participants", append([]any{}, r.participants...))
	carried := []any{}
	for _, c := range r.carried {
		row := pysem.EmptyObj()
		if pysem.Truthy(c.status) {
			row.Set("status", c.status)
		} else {
			row.Set("status", "pending")
		}
		row.Set("text", c.text)
		if editable {
			// The payload's "text" merges onto the existing key in place —
			// same value, original position — exactly like the dict literal.
			payload := cardPayload(c, true, "carried_action_items")
			for _, key := range payload.Keys() {
				row.Set(key, payload.Get(key))
			}
		}
		carried = append(carried, row)
	}
	report.Set("carried", carried)
	trend, err := trendPayload(
		history, "retro_date", "card_count", "Card volume trend", "Card volume",
		r.date, r.date, float64(len(r.cards)),
	)
	if err != nil {
		return nil, err
	}
	report.Set("trend", trend)
	args.Set("report", report)
	args.Set("footer", "Generated by yeaboi.ai • "+pysem.Str(generatedDate))
	withAnnotations(args, r.annotations)
	return args, nil
}
