// Port of src/yeaboi/artifacts/render.py (annotations_payload,
// annotations_markdown, with_annotations, edit_map, row_anchor) plus
// artifacts/paths.escape_value and agent/state.annotations_from — keep in
// lockstep; the Python modules are the reference implementation.
//
// The rule the Python module exists to enforce holds here too: an annotation
// that is stored but not rendered is worse than one that was never accepted,
// so anything that grows an annotations field grows a call to BOTH the
// payload and the markdown helper at the same time.
package exports

import (
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// notesHeading ports render.NOTES_HEADING — says who put it there without
// claiming it was verified.
const notesHeading = "Added by the team"

// annotation is the deserialized wire form of agent/state.Annotation: every
// field already str()-ed, exactly as annotations_from rebuilds them.
type annotation struct {
	kind, anchor, label, text, author, avatar, at string
}

// annotationsFrom ports state.annotations_from: tolerant — anything that is
// not a sequence of objects deserializes to nothing rather than erroring,
// and every field is str()-ed with the dataclass default.
func annotationsFrom(value any) []annotation {
	rows, ok := value.([]any)
	if !ok {
		return nil
	}
	out := []annotation{}
	for _, raw := range rows {
		a := pysem.AsObj(raw)
		if a == nil {
			continue
		}
		out = append(out, annotation{
			kind:   pysem.Str(a.GetDefault("kind", "note")),
			anchor: pysem.Str(a.GetDefault("anchor", "")),
			label:  pysem.Str(a.GetDefault("label", "")),
			text:   pysem.Str(a.GetDefault("text", "")),
			author: pysem.Str(a.GetDefault("author", "")),
			avatar: pysem.Str(a.GetDefault("avatar", "")),
			at:     pysem.Str(a.GetDefault("at", "")),
		})
	}
	return out
}

// annotationsPayload ports render.annotations_payload: the wire form of a
// document's annotations (rows with empty text dropped), or an empty list.
func annotationsPayload(rows []annotation) []any {
	out := []any{}
	for _, a := range rows {
		if a.text == "" {
			continue
		}
		row := pysem.EmptyObj()
		row.Set("kind", a.kind)
		row.Set("anchor", a.anchor)
		row.Set("label", a.label)
		row.Set("text", a.text)
		row.Set("author", a.author)
		row.Set("avatar", a.avatar)
		row.Set("at", a.at)
		out = append(out, row)
	}
	return out
}

// annotationsMarkdown ports render.annotations_markdown: Markdown lines for
// a document's annotations, or nothing.
func annotationsMarkdown(rows []annotation) []string {
	kept := []annotation{}
	for _, a := range rows {
		if a.text != "" {
			kept = append(kept, a)
		}
	}
	if len(kept) == 0 {
		return nil
	}
	lines := []string{"## " + notesHeading, ""}
	for _, a := range kept {
		who := ""
		if a.author != "" {
			who = " — _" + a.author + "_"
		}
		where := ""
		if a.anchor != "" {
			where = " (on `" + a.anchor + "`)"
		}
		if a.kind == "field" && a.label != "" {
			lines = append(lines, "- **"+a.label+":** "+a.text+where+who)
		} else {
			lines = append(lines, "- "+a.text+where+who)
		}
	}
	lines = append(lines, "")
	return lines
}

// withAnnotations ports render.with_annotations: attach the annotations to
// args["report"] when any survive the empty-text filter — omitted when
// empty, so a document nobody has annotated stays byte-identical.
func withAnnotations(args *pysem.Obj, rows []annotation) {
	payload := annotationsPayload(rows)
	if len(payload) == 0 {
		return
	}
	if report := pysem.AsObj(args.Get("report")); report != nil {
		report.Set("annotations", payload)
	}
}

// editMap ports render.edit_map for the one field tuple this seam uses,
// ("text",): the {field: {path, value}} map that makes a region correctable.
// Non-string values are skipped rather than coerced, exactly like the
// reference — a silently stringified value would offer an affordance the
// server would then refuse.
func editMap(anchor string, text any) *pysem.Obj {
	out := pysem.EmptyObj()
	value, ok := text.(string)
	if !ok {
		return out
	}
	path := "text"
	if anchor != "" {
		path = anchor + ".text"
	}
	entry := pysem.EmptyObj()
	entry.Set("path", path)
	entry.Set("value", value)
	out.Set("text", entry)
	return out
}

// escapeValue ports artifacts/paths.escape_value: urllib.parse.quote with
// safe="" plus the explicit "." → "%2E" pass (the dot is in urllib's
// always-safe set and is also our path separator).
func escapeValue(value string) string {
	return strings.ReplaceAll(pysem.QuoteAll(value), ".", "%2E")
}

// rowAnchor ports render.row_anchor — `cards[id=abc123]`, a row addressed by
// its natural key. The reference passes the raw id into escape_value (a
// non-string would raise TypeError there); ids are server-assigned hex
// strings, so the Str here is unobservable in practice.
func rowAnchor(listField, key string, value any) string {
	return listField + "[" + key + "=" + escapeValue(pysem.Str(value)) + "]"
}
