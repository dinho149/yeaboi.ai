// Package changelog ports src/yeaboi/changelog.py — the bundled
// release-notes loader — keep in lockstep; the Python module is the
// reference implementation and tests/parity/goldens/changelog/ diffs the
// parse and the rendered text over a malformed-entry corpus, while the
// `yeaboi __dump-changelog` arm diffs the real bundled data end to end.
//
// The data rides go:embed where Python bundles it with hatchling; the
// embedded copy is a byte-for-byte duplicate of src/yeaboi/
// changelog_data.json, enforced two ways (changelog_test.go here and
// TestChangelogEmbedLockstep in tests/unit/test_gocore_packaging.py), so
// the auto-version workflow's rewrite of the Python file cannot silently
// strand this one.
package changelog

import (
	_ "embed"
	"encoding/json"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

//go:embed changelog_data.json
var rawData []byte

// ValidAreas mirrors changelog.VALID_AREAS.
var ValidAreas = map[string]bool{
	"analysis": true, "planning": true, "standup": true, "retro": true,
	"performance": true, "reporting": true, "usage": true, "settings": true,
	"agents": true, "general": true,
}

// AreaColors mirrors changelog.AREA_COLORS.
var AreaColors = map[string]string{
	"analysis":    "rgb(100,180,100)",
	"planning":    "rgb(110,140,220)",
	"standup":     "rgb(200,100,180)",
	"retro":       "rgb(80,190,190)",
	"performance": "rgb(220,110,90)",
	"reporting":   "rgb(140,120,230)",
	"usage":       "rgb(220,160,60)",
	"settings":    "rgb(160,160,180)",
	"agents":      "rgb(90,160,210)",
	"general":     "rgb(160,160,180)",
}

// Highlight mirrors changelog.ChangelogHighlight.
type Highlight struct {
	Text  string
	Areas []string
}

// Entry mirrors changelog.ChangelogEntry.
type Entry struct {
	Version    string
	Date       string
	Summary    string
	Highlights []Highlight
}

// coerceAreas mirrors changelog._coerce_areas: non-list input collapses to
// ("general",); unknown or non-string tags become "general"; the result is
// deduplicated preserving order and never empty.
func coerceAreas(raw any) []string {
	list, ok := raw.([]any)
	if !ok {
		return []string{"general"}
	}
	var areas []string
	seen := map[string]bool{}
	for _, item := range list {
		area, isStr := item.(string)
		if !isStr || !ValidAreas[area] {
			area = "general"
		}
		if !seen[area] {
			seen[area] = true
			areas = append(areas, area)
		}
	}
	if len(areas) == 0 {
		return []string{"general"}
	}
	return areas
}

// parseEntry mirrors changelog._parse_entry: nil (skipped) when malformed.
// Python iterates “raw.get("highlights") or []“ — a string iterates as
// characters and a dict as keys, none of which are dicts, so both shapes
// legally yield zero highlights and are mirrored by the type switch.
func parseEntry(raw any) *Entry {
	obj, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	version, ok := obj["version"].(string)
	if !ok || version == "" {
		return nil
	}
	var highlights []Highlight
	if items, ok := obj["highlights"].([]any); ok {
		for _, item := range items {
			h, ok := item.(map[string]any)
			if !ok {
				continue
			}
			text, ok := h["text"].(string)
			if !ok || text == "" {
				continue
			}
			highlights = append(highlights, Highlight{Text: text, Areas: coerceAreas(h["areas"])})
		}
	}
	entry := &Entry{Version: version, Highlights: highlights}
	if date, ok := obj["date"].(string); ok {
		entry.Date = date
	}
	if summary, ok := obj["summary"].(string); ok {
		entry.Summary = summary
	}
	return entry
}

// Parse mirrors load_changelog's body over arbitrary bytes: [] on any read
// or JSON problem, entries parsed newest-first as stored.
func Parse(raw []byte) []Entry {
	var data any
	if err := json.Unmarshal(raw, &data); err != nil {
		return []Entry{}
	}
	obj, ok := data.(map[string]any)
	if !ok {
		return []Entry{}
	}
	rawEntries, _ := obj["entries"].([]any)
	entries := []Entry{}
	for _, raw := range rawEntries {
		if entry := parseEntry(raw); entry != nil {
			entries = append(entries, *entry)
		}
	}
	return entries
}

// Load mirrors changelog.load_changelog over the embedded data.
func Load() []Entry {
	return Parse(rawData)
}

// EmbeddedData exposes the raw embedded bytes for the sync guard.
func EmbeddedData() []byte {
	return rawData
}

// DumpPayload renders entries in the __dump-changelog JSON shape —
// tests/parity/foundations/changelogdump.py entries_payload is the twin.
func DumpPayload(entries []Entry) map[string]any {
	out := make([]any, len(entries))
	for i, e := range entries {
		hs := make([]any, len(e.Highlights))
		for j, h := range e.Highlights {
			hs[j] = map[string]any{"text": h.Text, "areas": h.Areas}
		}
		out[i] = map[string]any{"version": e.Version, "date": e.Date, "summary": e.Summary, "highlights": hs}
	}
	return map[string]any{"entries": out, "text": BuildText(entries)}
}

// BuildText mirrors changelog.build_changelog_text over the given entries.
func BuildText(entries []Entry) string {
	if len(entries) == 0 {
		return "# yeaboi — Changelog\n\n(no changelog available)\n"
	}
	lines := []string{"# yeaboi — Changelog", ""}
	for _, e := range entries {
		header := "## " + e.Version
		if e.Date != "" {
			header += " — " + e.Date
		}
		lines = append(lines, header)
		if e.Summary != "" {
			lines = append(lines, "", e.Summary)
		}
		for _, h := range e.Highlights {
			lines = append(lines, "- "+h.Text)
		}
		lines = append(lines, "")
	}
	// Python's no-arg rstrip() strips unicode whitespace — pysem.IsSpace.
	return strings.TrimRightFunc(strings.Join(lines, "\n"), pysem.IsSpace) + "\n"
}
