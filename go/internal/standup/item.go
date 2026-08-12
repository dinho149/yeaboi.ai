// Package standup is a line-for-line port of the deterministic middle of the
// standup pipeline — the standup.aggregate RPC (contracts/v1/rpc.md). Each
// file names its Python twin; the Python module remains the REFERENCE
// implementation and tests/parity/test_standup_parity.py diffs the two whole.
//
// Data model: activity items stay generic ordered JSON (*pysem.Obj), exactly
// as Python works on dicts — values are only coerced at use sites (strOr,
// pysem.Truthy…), so a collector field that arrives as a number is emitted
// back as a number, byte-identical to the reference. Anything member-keyed
// that reaches the wire is emitted in members order via *pysem.Obj.
//
// Privacy: nothing from the bundle — titles, bodies, authors — is ever
// logged. Counts and rule labels only.
package standup

// item.go — the shared item vocabulary: accessors mirroring the Python
// idioms, and the projection every consumer sees
// (engine._projected_item / engine._group_activity_by_author).

import (
	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// strOr mirrors `str(item.get(key) or "")` — the single most common idiom in
// the pipeline. Also covers `item.get(key, "")` at sites that then compare
// against non-empty strings.
func strOr(item *pysem.Obj, key string) string {
	v := item.Get(key)
	if !pysem.Truthy(v) {
		return ""
	}
	return pysem.Str(v)
}

// strippedOr mirrors `(item.get(key) or "").strip()`.
func strippedOr(item *pysem.Obj, key string) string {
	return pysem.Strip(strOr(item, key))
}

// listOr mirrors `item.get(key) or ()`.
func listOr(item *pysem.Obj, key string) []any {
	if arr, ok := item.Get(key).([]any); ok {
		return arr
	}
	return nil
}

// strList mirrors `[str(x) for x in seq]`.
func strList(seq []any) []string {
	out := make([]string, 0, len(seq))
	for _, v := range seq {
		out = append(out, pysem.Str(v))
	}
	return out
}

// projectedItem mirrors engine._projected_item — the per-item shape the rest
// of the pipeline sees, one place, with the exact key order the Python dict
// literal produces (the order reaches the wire in `grouped` and from there
// the LLM prompt's json.dumps, so it is part of the contract).
func projectedItem(item *pysem.Obj) *pysem.Obj {
	out := pysem.EmptyObj()
	out.Set("kind", item.GetDefault("kind", ""))
	out.Set("title", item.GetDefault("title", ""))
	out.Set("summary", item.GetDefault("summary", ""))
	out.Set("status", item.GetDefault("status", ""))
	out.Set("source", item.GetDefault("source", ""))
	out.Set("key", item.GetDefault("key", ""))
	out.Set("url", item.GetDefault("url", ""))
	out.Set("repository", item.GetDefault("repository", ""))
	out.Set("timestamp", item.GetDefault("timestamp", ""))
	out.Set("pr_id", item.GetDefault("pr_id", ""))
	out.Set("branch", item.GetDefault("branch", ""))
	out.Set("body", item.GetDefault("body", ""))
	// Python: tuple(item.get("changed_files") or ()) — values pass through
	// verbatim; the wire spells a tuple as a list either way.
	out.Set("changed_paths", append([]any{}, listOr(item, "changed_files")...))
	out.Set("work_item_ids", append([]any{}, listOr(item, "work_item_ids")...))
	out.Set("work_items_known", item.GetDefault("work_items_known", true))
	out.Set("issue_type", item.GetDefault("issue_type", ""))
	out.Set("parent_key", item.GetDefault("parent_key", ""))
	out.Set("subtask", pysem.Truthy(item.GetDefault("subtask", false)))
	return out
}

// Grouped mirrors the ordered {member: [projected item]} dict — every member
// present (possibly empty), in members order.
type Grouped struct {
	Names []string
	Items map[string][]*pysem.Obj
}

func newGrouped(members []string) *Grouped {
	g := &Grouped{Items: make(map[string][]*pysem.Obj, len(members))}
	for _, m := range members {
		g.Names = append(g.Names, m)
		g.Items[m] = []*pysem.Obj{}
	}
	return g
}

// AllItems mirrors `[item for items in grouped.values() for item in items]`.
func (g *Grouped) AllItems() []*pysem.Obj {
	out := []*pysem.Obj{}
	for _, name := range g.Names {
		out = append(out, g.Items[name]...)
	}
	return out
}

// PrevMember / PrevReport mirror the previous-report wire projection
// (aggregate._previous_report_to_wire) — the narrow read-only slice of
// yesterday's report the deterministic layer consumes.
type PrevMember struct {
	Name          string
	Summary       string
	Blockers      string
	Outlook       string
	Links         [][2]string
	CodeLinks     [][2]string
	PracticeRules []string
}

type PrevReport struct {
	Members []PrevMember
}

// prevReportFromWire hydrates the projection; nil for a null/absent payload.
func prevReportFromWire(v any) *PrevReport {
	obj := pysem.AsObj(v)
	if obj == nil {
		return nil
	}
	report := &PrevReport{}
	updates, _ := obj.Get("member_updates").([]any)
	for _, raw := range updates {
		m := pysem.AsObj(raw)
		if m == nil {
			continue
		}
		member := PrevMember{
			Name:     pysem.Str(m.GetDefault("name", "")),
			Summary:  pysem.Str(m.GetDefault("summary", "")),
			Blockers: pysem.Str(m.GetDefault("blockers", "")),
			Outlook:  pysem.Str(m.GetDefault("outlook", "")),
		}
		member.Links = linkPairs(m.Get("links"))
		member.CodeLinks = linkPairs(m.Get("code_links"))
		practices, _ := m.Get("practices").([]any)
		for _, p := range practices {
			if po := pysem.AsObj(p); po != nil {
				if rule := pysem.Str(po.GetDefault("rule", "")); rule != "" {
					member.PracticeRules = append(member.PracticeRules, rule)
				}
			}
		}
		report.Members = append(report.Members, member)
	}
	return report
}

// linkPairs hydrates [[label, url], …].
func linkPairs(v any) [][2]string {
	arr, _ := v.([]any)
	out := make([][2]string, 0, len(arr))
	for _, raw := range arr {
		pair, _ := raw.([]any)
		if len(pair) == 2 {
			out = append(out, [2]string{pysem.Str(pair[0]), pysem.Str(pair[1])})
		}
	}
	return out
}
