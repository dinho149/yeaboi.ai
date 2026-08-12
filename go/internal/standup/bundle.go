package standup

// bundle.go — mirror of collector.ActivityBundle (the slice the aggregate
// consumes: items + per-source bookkeeping; the collectors themselves stay in
// Python). Pairs keep list-of-pairs form because their ORDER is part of the
// wire shape (counts keep the collector's source order).

import (
	"github.com/yeaboi-ai/yeaboi/go/internal/pysem"
)

// SourceCount is one (source, count) pair.
type SourceCount struct {
	Source string
	N      int64
}

// SourcePair is one (source, message/reason) pair.
type SourcePair struct {
	Source string
	Text   string
}

// Bundle mirrors collector.ActivityBundle.
type Bundle struct {
	Items            []*pysem.Obj
	Counts           []SourceCount
	Errors           []SourcePair
	PartialSources   []SourcePair
	Skipped          []SourcePair
	ReferenceTickets []*pysem.Obj
}

// Total mirrors ActivityBundle.total(exclude_kinds=...).
func (b *Bundle) Total(excludeKinds ...string) int {
	if len(excludeKinds) == 0 {
		return len(b.Items)
	}
	excluded := map[string]bool{}
	for _, kind := range excludeKinds {
		excluded[kind] = true
	}
	n := 0
	for _, item := range b.Items {
		if !excluded[strOr(item, "kind")] {
			n++
		}
	}
	return n
}

// bundleFromWire hydrates the params' bundle object.
func bundleFromWire(v any) *Bundle {
	b := &Bundle{}
	obj := pysem.AsObj(v)
	if obj == nil {
		return b
	}
	b.Items = objList(obj.Get("items"))
	b.ReferenceTickets = objList(obj.Get("reference_tickets"))
	for _, pair := range pairList(obj.Get("counts")) {
		n, _ := pysem.IntOrZero(pair[1])
		b.Counts = append(b.Counts, SourceCount{Source: pysem.Str(pair[0]), N: n})
	}
	b.Errors = sourcePairs(obj.Get("errors"))
	b.PartialSources = sourcePairs(obj.Get("partial_sources"))
	b.Skipped = sourcePairs(obj.Get("skipped"))
	return b
}

// objList hydrates a JSON array of objects, skipping non-objects.
func objList(v any) []*pysem.Obj {
	arr, _ := v.([]any)
	out := make([]*pysem.Obj, 0, len(arr))
	for _, raw := range arr {
		if o := pysem.AsObj(raw); o != nil {
			out = append(out, o)
		}
	}
	return out
}

func pairList(v any) [][2]any {
	arr, _ := v.([]any)
	out := make([][2]any, 0, len(arr))
	for _, raw := range arr {
		pair, _ := raw.([]any)
		if len(pair) == 2 {
			out = append(out, [2]any{pair[0], pair[1]})
		}
	}
	return out
}

func sourcePairs(v any) []SourcePair {
	out := []SourcePair{}
	for _, pair := range pairList(v) {
		out = append(out, SourcePair{Source: pysem.Str(pair[0]), Text: pysem.Str(pair[1])})
	}
	return out
}
