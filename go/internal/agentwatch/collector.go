package agentwatch

// Local agent-session ingestion — a port of src/yeaboi/agentwatch/collector.py.
//
// The two invariants that shape the design carry over verbatim:
//
//  1. Privacy — nothing from a transcript's *content* is persisted. Findings
//     are (pattern label, file, line); ingest-failure warnings carry only the
//     failure class name.
//  2. Correct token math — usage counts once per requestId and tool_use
//     blocks once per block id; the dedup is whole-file, so any change
//     triggers a full reparse whose rollup replaces the previous one.
//
// Parsing runs on a goroutine worker pool capped at min(GOMAXPROCS, 8);
// results are applied in submission order on the caller's goroutine, which
// owns the single SQLite connection — one transaction per ingestBatchSize
// parsed files, exactly like the Python batching.

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"

	"github.com/yeaboi-ai/yeaboi/go/internal/contract"
)

// ingestBatchSize is the parsed-files-per-write-transaction bound (mirrors
// collector._INGEST_BATCH_SIZE).
const ingestBatchSize = 64

// maxParseWorkers caps the parse pool (mirrors collector._MAX_PARSE_WORKERS).
const maxParseWorkers = 8

// SourceRoot is one (source label, root dir) pair to scan.
type SourceRoot struct {
	Source string
	Root   string
}

// DefaultRoots mirrors collector._source_roots(): the Claude Code projects
// directory under the user's home.
func DefaultRoots() []SourceRoot {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil
	}
	return []SourceRoot{{Source: "claude_code", Root: filepath.Join(home, ".claude", "projects")}}
}

// ── Per-file parse ────────────────────────────────────────────────────────

type rollup struct {
	sessionID   string
	projectPath string
	gitBranch   string
	cliVersion  string
	startedAt   string
	endedAt     string
	turns       int
	modelUsage  map[string]map[string]int64
	toolCounts  map[string]int64
}

type finding struct {
	category  string
	severity  string
	pattern   string
	lineNo    int
	sessionID string
}

type parseOutcome struct {
	rollup    *rollup
	findings  []finding
	malformed int
	errClass  string // non-empty means the file failed to ingest
	errDetail string // goes to stderr logs only, never to warnings
}

var usageKeys = []string{"input", "output", "cache_write_5m", "cache_write_1h", "cache_read", "calls"}

func newBucket() map[string]int64 {
	b := make(map[string]int64, len(usageKeys))
	for _, k := range usageKeys {
		b[k] = 0
	}
	return b
}

// addUsage folds one deduped assistant message's usage into the per-model
// totals (collector._add_usage).
func addUsage(r *rollup, model string, usage map[string]any) error {
	bucket, ok := r.modelUsage[model]
	if !ok {
		bucket = newBucket()
		r.modelUsage[model] = bucket
	}
	cacheDetail := map[string]any{}
	if cc := usage["cache_creation"]; pyTruthy(cc) {
		m, ok := cc.(map[string]any)
		if !ok {
			// Python: (truthy non-dict).get(...) → AttributeError → the whole
			// file becomes a "failed to ingest" warning.
			return &pyError{class: "AttributeError", msg: "cache_creation is not an object"}
		}
		cacheDetail = m
	}
	write1h, err := pyIntOrZero(cacheDetail["ephemeral_1h_input_tokens"])
	if err != nil {
		return err
	}
	write5m, err := pyIntOrZero(cacheDetail["ephemeral_5m_input_tokens"])
	if err != nil {
		return err
	}
	if write1h == 0 && write5m == 0 {
		// Older CLI versions report only the aggregate; treat it as 5m writes.
		if write5m, err = pyIntOrZero(usage["cache_creation_input_tokens"]); err != nil {
			return err
		}
	}
	inTok, err := pyIntOrZero(usage["input_tokens"])
	if err != nil {
		return err
	}
	outTok, err := pyIntOrZero(usage["output_tokens"])
	if err != nil {
		return err
	}
	readTok, err := pyIntOrZero(usage["cache_read_input_tokens"])
	if err != nil {
		return err
	}
	bucket["input"] += inTok
	bucket["output"] += outTok
	bucket["cache_write_5m"] += write5m
	bucket["cache_write_1h"] += write1h
	bucket["cache_read"] += readTok
	bucket["calls"]++
	return nil
}

// scanSecurity emits findings for one raw line — pattern + location only
// (collector._scan_security). record is nil for malformed lines.
func scanSecurity(line string, lineNo int, record map[string]any, sessionID string, emit func(finding)) error {
	for _, sp := range secretPatterns {
		if sp.guard != nil && !sp.guard(line) {
			continue
		}
		if sp.re.MatchString(line) {
			emit(finding{category: "secret", severity: "critical", pattern: sp.label, lineNo: lineNo, sessionID: sessionID})
		}
	}
	if record == nil {
		return nil
	}
	// Python: `message = record.get("message") or {}` — a truthy non-dict
	// raises AttributeError on .get, failing the whole file.
	message := map[string]any{}
	if m := record["message"]; pyTruthy(m) {
		mm, ok := m.(map[string]any)
		if !ok {
			return &pyError{class: "AttributeError", msg: "message is not an object"}
		}
		message = mm
	}
	content, ok := message["content"].([]any)
	if !ok {
		return nil
	}
	for _, raw := range content {
		block, ok := raw.(map[string]any)
		if !ok || block["type"] != "tool_use" {
			continue
		}
		input := map[string]any{}
		if in := block["input"]; pyTruthy(in) {
			im, ok := in.(map[string]any)
			if !ok {
				return &pyError{class: "AttributeError", msg: "tool_use input is not an object"}
			}
			input = im
		}
		command, ok := input["command"].(string)
		if !ok {
			continue
		}
		for _, rp := range riskyBashPatterns {
			if rp.re.MatchString(command) {
				emit(finding{category: "risky_tool", severity: rp.severity, pattern: rp.label, lineNo: lineNo, sessionID: sessionID})
			}
		}
	}
	return nil
}

// readUniversalLine reads one line honoring Python text-mode universal
// newlines (\n, \r, \r\n all terminate a line). The terminator is dropped.
func readUniversalLine(r *bufio.Reader) (string, error) {
	var b strings.Builder
	for {
		c, err := r.ReadByte()
		if err != nil {
			if errors.Is(err, io.EOF) && b.Len() > 0 {
				return b.String(), nil
			}
			return "", err
		}
		switch c {
		case '\n':
			return b.String(), nil
		case '\r':
			if next, err := r.Peek(1); err == nil && next[0] == '\n' {
				_, _ = r.ReadByte()
			}
			return b.String(), nil
		default:
			b.WriteByte(c)
		}
	}
}

// pyJSONLoads mirrors json.loads strictness for one line: a single value,
// optionally surrounded by whitespace, decoded with numbers kept exact.
func pyJSONLoads(line string) (any, error) {
	dec := json.NewDecoder(strings.NewReader(line))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	var trailing any
	if err := dec.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, &pyError{class: "ValueError", msg: "extra data"}
	}
	return v, nil
}

// pathStem mirrors pathlib.Path.stem: the final component minus its last
// suffix.
func pathStem(path string) string {
	base := filepath.Base(path)
	if i := strings.LastIndexByte(base, '.'); i > 0 {
		return base[:i]
	}
	return base
}

// parseTranscript streams one transcript into a rollup — the port of
// collector._parse_file / _parse_worker. Any failure returns only an
// exception-class-equivalent name; the detail stays out of warnings.
func parseTranscript(path string) parseOutcome {
	out := parseOutcome{}
	r := &rollup{
		sessionID:  pathStem(path),
		modelUsage: map[string]map[string]int64{},
		toolCounts: map[string]int64{},
	}
	emit := func(f finding) { out.findings = append(out.findings, f) }

	fail := func(err error) parseOutcome {
		return parseOutcome{errClass: pyErrClass(err), errDetail: err.Error()}
	}

	handle, err := os.Open(path)
	if err != nil {
		return fail(err)
	}
	defer handle.Close()
	if fi, err := handle.Stat(); err == nil && fi.IsDir() {
		// Python's open() raises IsADirectoryError up front.
		return fail(&pyError{class: "IsADirectoryError", msg: "is a directory"})
	}

	reader := bufio.NewReaderSize(handle, 256*1024)
	countedRequests := map[string]bool{}
	countedToolBlocks := map[string]bool{}
	lineNo := 0
	for {
		raw, err := readUniversalLine(reader)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return fail(err)
		}
		lineNo++
		// Python decodes with errors="replace"; invalid UTF-8 becomes U+FFFD.
		line := pyStrip(strings.ToValidUTF8(raw, "�"))
		if line == "" {
			continue
		}
		value, jsonErr := pyJSONLoads(line)
		if jsonErr != nil {
			out.malformed++
			if err := scanSecurity(line, lineNo, nil, r.sessionID, emit); err != nil {
				return fail(err)
			}
			continue
		}
		record, ok := value.(map[string]any)
		if !ok {
			out.malformed++
			continue
		}
		if sid := record["sessionId"]; pyTruthy(sid) {
			r.sessionID = pyStr(sid)
		}
		if err := scanSecurity(line, lineNo, record, r.sessionID, emit); err != nil {
			return fail(err)
		}
		if cwd := record["cwd"]; pyTruthy(cwd) {
			r.projectPath = pyStr(cwd)
		}
		if branch := record["gitBranch"]; pyTruthy(branch) {
			r.gitBranch = pyStr(branch)
		}
		if version := record["version"]; pyTruthy(version) {
			r.cliVersion = pyStr(version)
		}
		if timestamp := record["timestamp"]; pyTruthy(timestamp) {
			if r.startedAt == "" {
				r.startedAt = pyStr(timestamp)
			}
			r.endedAt = pyStr(timestamp)
		}
		kind := record["type"]
		message := map[string]any{}
		if m, ok := record["message"].(map[string]any); ok {
			message = m
		}
		if kind == "user" {
			originIsNone := true
			var origin any
			if originObj, ok := record["origin"].(map[string]any); ok {
				if v, present := originObj["kind"]; present && v != nil {
					origin = v
					originIsNone = false
				}
			}
			_, contentIsStr := message["content"].(string)
			if origin == "human" || (originIsNone && contentIsStr) {
				r.turns++
			}
		} else if kind == "assistant" {
			// One API response spans several lines sharing a requestId, each
			// repeating identical usage — count it exactly once.
			requestID := pyStr(firstTruthy(record["requestId"], record["uuid"], int64(lineNo)))
			if usage, ok := message["usage"].(map[string]any); ok && !countedRequests[requestID] {
				countedRequests[requestID] = true
				model := pyStr(firstTruthy(message["model"], "unknown"))
				if err := addUsage(r, model, usage); err != nil {
					return fail(err)
				}
			}
			if content, ok := message["content"].([]any); ok {
				for _, rawBlock := range content {
					block, ok := rawBlock.(map[string]any)
					if !ok || block["type"] != "tool_use" {
						continue
					}
					blockID := pyStr(firstTruthy(block["id"], fmt.Sprintf("%s:%d", requestID, lineNo)))
					if countedToolBlocks[blockID] {
						continue
					}
					countedToolBlocks[blockID] = true
					name := pyStr(firstTruthy(block["name"], "unknown"))
					r.toolCounts[name]++
				}
			}
		}
	}
	out.rollup = r
	return out
}

// pyErrClass maps a Go error onto the Python exception class name the same
// failure would have carried — the only part of a failure that may reach a
// persisted warning.
func pyErrClass(err error) string {
	var pe *pyError
	if errors.As(err, &pe) {
		return pe.class
	}
	if errors.Is(err, syscall.EISDIR) {
		return "IsADirectoryError"
	}
	var pathErr *fs.PathError
	if errors.As(err, &pathErr) {
		return "OSError"
	}
	return "Exception"
}

// firstLineSha hashes the first line (terminator included) so a
// replaced/rotated same-size file is detectable. "" on any read failure.
func firstLineSha(path string) string {
	handle, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer handle.Close()
	line, err := bufio.NewReader(handle).ReadBytes('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return ""
	}
	sum := sha256.Sum256(line)
	return hex.EncodeToString(sum[:])
}

// statMtime reproduces CPython's st_mtime double: seconds + 1e-9 * nanoseconds.
func statMtime(fi os.FileInfo) float64 {
	t := fi.ModTime()
	return float64(t.Unix()) + 1e-9*float64(t.Nanosecond())
}

// ── Public entry point ────────────────────────────────────────────────────

// Refresh ingests new/changed session transcripts into the store — the port
// of collector.refresh. File-level failures become warnings; only store or
// I/O-loop failures return an error.
func Refresh(store *Store, roots []SourceRoot, emit func(*contract.Event)) (*contract.Stats, error) {
	stats := contract.NewStats()
	scanFailed := false

	// Materialise across ALL roots before scanning so the progress meter has
	// a global denominator.
	type pendingFile struct {
		source string
		path   string
	}
	var pending []pendingFile
	for _, sr := range roots {
		fi, err := os.Stat(sr.Root)
		if err != nil {
			if !errors.Is(err, fs.ErrNotExist) {
				stats.Warnings = append(stats.Warnings, fmt.Sprintf("cannot scan %s: %v", sr.Root, err))
				scanFailed = true
			}
			continue
		}
		if !fi.IsDir() {
			continue
		}
		var found []string
		walkErr := filepath.WalkDir(sr.Root, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if path != sr.Root && strings.HasSuffix(d.Name(), ".jsonl") {
				found = append(found, path)
			}
			return nil
		})
		if walkErr != nil {
			stats.Warnings = append(stats.Warnings, fmt.Sprintf("cannot scan %s: %v", sr.Root, walkErr))
			scanFailed = true
			continue
		}
		sort.Strings(found) // rglob yields sorted full paths
		for _, p := range found {
			pending = append(pending, pendingFile{source: sr.Source, path: p})
		}
	}

	total := len(pending)
	lastPct := -1
	emitScan := func(current int) {
		denom := total
		if denom < 1 {
			denom = 1
		}
		lastPct = current * 100 / denom
		if emit == nil {
			return
		}
		cur, tot, parsed := current, total, stats.FilesParsed
		emit(&contract.Event{
			Kind:           "analysis_component",
			ComponentID:    "scan",
			Label:          "Scan agent sessions",
			Status:         "running",
			Detail:         "",
			Current:        &cur,
			Total:          &tot,
			Unit:           "files",
			SecondaryCount: &parsed,
			SecondaryUnit:  "parsed",
		})
	}
	if emit != nil && total > 0 {
		emitScan(0)
	}

	// ── Pass 1: disposition every file via the cursor (cheap stat + head
	// hash). Changed files queue for parsing WITH their pre-parse stat.
	type parseJob struct {
		source string
		path   string
		size   int64
		mtime  float64
	}
	var toParse []parseJob
	handled := 0
	for _, pf := range pending {
		stats.FilesSeen++
		fi, err := os.Stat(pf.path)
		if err != nil {
			handled++
			continue
		}
		size, mtime := fi.Size(), statMtime(fi)
		cursor, err := store.GetCursor(pf.path)
		if err != nil {
			return stats, err
		}
		skip := false
		if cursor != nil && cursor.Size == size && cursor.Mtime == mtime {
			// Same size AND same mtime — only a same-size replacement
			// remains, which the head hash catches. An empty stored hash
			// predates the check; treat it as a match.
			skip = cursor.FirstLineSha == "" || cursor.FirstLineSha == firstLineSha(pf.path)
		}
		if !skip {
			toParse = append(toParse, parseJob{source: pf.source, path: pf.path, size: size, mtime: mtime})
			continue
		}
		stats.FilesSkipped++
		handled++
		if emit != nil && (handled == total || handled*100/total != lastPct) {
			emitScan(handled)
		}
	}

	// ── Pass 2: parse on a worker pool; apply results in submission order on
	// this goroutine, which is the only writer.
	n := len(toParse)
	results := make([]chan parseOutcome, n)
	for i := range results {
		results[i] = make(chan parseOutcome, 1)
	}
	if n > 0 {
		workers := runtime.GOMAXPROCS(0)
		if workers > maxParseWorkers {
			workers = maxParseWorkers
		}
		if workers > n {
			workers = n
		}
		jobs := make(chan int)
		for w := 0; w < workers; w++ {
			go func() {
				for i := range jobs {
					results[i] <- parseTranscript(toParse[i].path)
				}
			}()
		}
		go func() {
			for i := 0; i < n; i++ {
				jobs <- i
			}
			close(jobs)
		}()
	}

	inBatch := 0
	var applyErr error
	for i := 0; i < n; i++ {
		res := <-results[i]
		job := toParse[i]
		handled++
		if inBatch == 0 {
			if err := store.Begin(); err != nil {
				applyErr = err
				break
			}
		}
		inBatch++
		if res.errClass != "" {
			// Only the class name reaches the warning: a parse exception can
			// quote transcript text, and warnings are persisted, exported and
			// rendered. The detail goes to the local (stderr) log.
			log.Printf("agentwatch ingest failed for %s: %s", job.path, res.errDetail)
			stats.Warnings = append(stats.Warnings,
				fmt.Sprintf("failed to ingest %s (%s — see logs)", filepath.Base(job.path), res.errClass))
			// No cursor either — the file reparses next run.
		} else {
			stats.MalformedLines += res.malformed
			if err := storeParsed(store, job.source, job.path, res, stats); err != nil {
				applyErr = err
				break
			}
			if err := store.SetCursor(job.path, job.source, job.size, job.mtime, firstLineSha(job.path)); err != nil {
				applyErr = err
				break
			}
		}
		if inBatch >= ingestBatchSize {
			if err := store.Commit(); err != nil {
				applyErr = err
				inBatch = 0
				break
			}
			inBatch = 0
		}
		if emit != nil {
			// Every parsed file emits — this is the live meter on a cold run.
			emitScan(handled)
		}
	}
	if inBatch > 0 {
		// Commit completed files even on an unwinding failure (the Python
		// finally does the same): a file is cursored in the same batch as its
		// rollup, so committed work is consistent.
		if err := store.Commit(); err != nil && applyErr == nil {
			applyErr = err
		}
	}
	if applyErr != nil {
		return stats, applyErr
	}
	// Guarantee the meter closes at N/N even when the last file's stat()
	// failed and its per-file emit was skipped.
	if emit != nil && total > 0 && lastPct != 100 {
		emitScan(total)
	}

	// Drop state for transcripts gone from disk — but never when a root
	// failed to scan (an unmounted root would make everything look deleted).
	if !scanFailed {
		known, err := store.KnownSourcePaths()
		if err != nil {
			return stats, err
		}
		for _, p := range known {
			if _, err := os.Stat(p); err == nil {
				continue
			}
			if err := store.ForgetSourcePath(p); err != nil {
				return stats, err
			}
			stats.FilesPruned++
		}
	}

	log.Printf("agentwatch refresh: %d seen, %d parsed, %d skipped, %d pruned, %d sessions, %d findings, %d malformed",
		stats.FilesSeen, stats.FilesParsed, stats.FilesSkipped, stats.FilesPruned,
		stats.SessionsUpserted, stats.FindingsAdded, stats.MalformedLines)
	return stats, nil
}

// storeParsed writes one parsed transcript's rollup + findings, replacing
// priors (collector._store_parsed).
func storeParsed(store *Store, source, path string, res parseOutcome, stats *contract.Stats) error {
	stats.FilesParsed++
	r := res.rollup
	if len(r.modelUsage) == 0 && r.turns == 0 && len(r.toolCounts) == 0 {
		return nil // not a session transcript (some other tool's JSONL)
	}
	if r.endedAt == "" {
		if r.startedAt != "" {
			r.endedAt = r.startedAt
		} else {
			r.endedAt = nowISO()
		}
	}
	if err := store.UpsertSession(
		r.sessionID, source, path, r.projectPath, r.gitBranch, r.cliVersion,
		r.startedAt, r.endedAt, r.turns, r.modelUsage, r.toolCounts,
	); err != nil {
		return err
	}
	stats.SessionsUpserted++
	if err := store.DeleteFindingsForPath(path); err != nil {
		return err
	}
	for _, f := range res.findings {
		if err := store.AddFinding(f.category, f.severity, f.pattern, path, f.lineNo, f.sessionID); err != nil {
			return err
		}
		stats.FindingsAdded++
	}
	return nil
}
