// logging_setup.py twin: one formatter, one fallback level, rotation
// everywhere. See redact.go for the package comment and the lockstep rule.
//
// Deviations, all documented here because they are structural rather than
// behavioural:
//   - Python's module-level “_handlers“ registry hangs off the process;
//     Go's is a *Registry value so golden tests can run isolated replays.
//     The production wiring (W17+) will hold one process-wide Registry.
//   - Python records can carry exceptions whose tracebacks the formatter
//     appends and redacts; Go has no tracebacks, so a Record is only ever
//     a formatted message. Nothing observable depends on this before W19.
//   - logging_setup logs its own attach/apply lines through the logging
//     module (debug/info). The parity scenario disables that logger on the
//     Python side because those lines carry wall-clock timestamps; the Go
//     twin follows the exports-package precedent and logs nothing at all.
package logfile

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// LogFormat mirrors logging_setup.LOG_FORMAT; formatLine hand-renders it.
const LogFormat = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

// DateFormat mirrors logging_setup.DATE_FORMAT ("%Y-%m-%d %H:%M:%S").
const DateFormat = "2006-01-02 15:04:05"

// MaxBytes and BackupCount mirror logging_setup's rotation policy.
const (
	MaxBytes    = 2 * 1024 * 1024
	BackupCount = 3
)

// Python logging's numeric levels (logging.DEBUG etc.).
const (
	LevelNotSet   = 0
	LevelDebug    = 10
	LevelInfo     = 20
	LevelWarning  = 30
	LevelError    = 40
	LevelCritical = 50
)

// levelNames maps a numeric level to logging.getLevelName's answer for the
// standard levels (the only ones a Record ever carries here).
var levelNames = map[int]string{
	LevelDebug:    "DEBUG",
	LevelInfo:     "INFO",
	LevelWarning:  "WARNING",
	LevelError:    "ERROR",
	LevelCritical: "CRITICAL",
}

// levelByAttr mirrors “getattr(logging, name, ...)“ for every UPPER-CASE
// integer attribute the logging module exports — including the WARN/FATAL
// aliases and NOTSET, which apply_level resolves even though get_log_level
// never emits them.
var levelByAttr = map[string]int{
	"NOTSET":   LevelNotSet,
	"DEBUG":    LevelDebug,
	"INFO":     LevelInfo,
	"WARNING":  LevelWarning,
	"WARN":     LevelWarning,
	"ERROR":    LevelError,
	"CRITICAL": LevelCritical,
	"FATAL":    LevelCritical,
}

// ResolveLevel mirrors apply_level's “getattr(logging, level.upper(),
// WARNING)“ (also the shape of logging_setup._level, whose input is
// already validated by config.GetLogLevel).
func ResolveLevel(level string) int {
	if v, ok := levelByAttr[strings.ToUpper(level)]; ok {
		return v
	}
	return LevelWarning
}

// Record is the slice of logging.LogRecord this surface formats: a name, a
// standard level, an already-interpolated message, and the created time in
// whole seconds (the parity corpus pins integral timestamps; DateFormat
// renders nothing finer).
type Record struct {
	Name    string
	Level   int
	Message string
	Created int64
}

// Formatter is the RedactingFormatter twin: assemble the final line, then
// scrub it. Loc stands in for time.localtime — production passes
// time.Local, golden replays pass the fixture's zone.
type Formatter struct {
	Loc    *time.Location
	Lookup EnvLookup
}

// formatLine renders LogFormat for one record ("%-7s" pads but never
// truncates, so CRITICAL overflows the column exactly as in Python).
func (f *Formatter) formatLine(rec Record) string {
	asctime := time.Unix(rec.Created, 0).In(f.Loc).Format(DateFormat)
	return fmt.Sprintf("%s %-7s %s: %s", asctime, levelNames[rec.Level], rec.Name, rec.Message)
}

// Format mirrors RedactingFormatter.format.
func (f *Formatter) Format(rec Record) string {
	return Redact(f.formatLine(rec), f.Lookup)
}

// Handler is the _SecureRotatingFileHandler twin: an append-mode file,
// rolled over at maxBytes with backupCount backups, chmod 0o600 on every
// open (initial and post-rollover, exactly the _open override).
type Handler struct {
	path        string
	maxBytes    int
	backupCount int
	level       int
	formatter   *Formatter
	file        *os.File
	pos         int64 // stream.tell(): bytes written so far
}

// NewHandler opens the file immediately (Python's delay=False default).
func NewHandler(path string, maxBytes, backupCount, level int, formatter *Formatter) (*Handler, error) {
	h := &Handler{path: path, maxBytes: maxBytes, backupCount: backupCount, level: level, formatter: formatter}
	if err := h.open(); err != nil {
		return nil, err
	}
	return h, nil
}

// open mirrors _SecureRotatingFileHandler._open: append mode, then the
// 0o600 hardening (best-effort, like config.restrict_permissions).
func (h *Handler) open() error {
	f, err := os.OpenFile(h.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o666)
	if err != nil {
		return err
	}
	_ = os.Chmod(h.path, 0o600)
	info, err := f.Stat()
	if err != nil {
		f.Close()
		return err
	}
	h.file = f
	h.pos = info.Size()
	return nil
}

// SetLevel mirrors Handler.setLevel.
func (h *Handler) SetLevel(level int) { h.level = level }

// Level returns the handler's threshold (callHandlers' check lives in
// Registry.Emit).
func (h *Handler) Level() int { return h.level }

// Emit mirrors RotatingFileHandler.emit: rollover decision, then write.
// CPython's shouldRollover compares stream.tell() — a BYTE offset — against
// len(formatted + "\n") counted in CHARACTERS, so a multi-byte line
// advances the two at different rates; the corpus pins that drift.
func (h *Handler) Emit(rec Record) error {
	line := h.formatter.Format(rec) + "\n"
	if h.maxBytes > 0 && h.pos+int64(len([]rune(line))) >= int64(h.maxBytes) {
		if err := h.rollover(); err != nil {
			return err
		}
	}
	n, err := h.file.WriteString(line)
	h.pos += int64(n)
	return err
}

// rollover mirrors RotatingFileHandler.doRollover: shift .1→.2→…, move the
// base to .1 (dropping what falls off the end), reopen fresh.
func (h *Handler) rollover() error {
	if h.file != nil {
		h.file.Close()
		h.file = nil
	}
	if h.backupCount > 0 {
		for i := h.backupCount - 1; i > 0; i-- {
			sfn := fmt.Sprintf("%s.%d", h.path, i)
			dfn := fmt.Sprintf("%s.%d", h.path, i+1)
			if _, err := os.Stat(sfn); err == nil {
				_ = os.Remove(dfn)
				if err := os.Rename(sfn, dfn); err != nil {
					return err
				}
			}
		}
		dfn := h.path + ".1"
		_ = os.Remove(dfn)
		if _, err := os.Stat(h.path); err == nil {
			if err := os.Rename(h.path, dfn); err != nil {
				return err
			}
		}
	}
	return h.open()
}

// Close mirrors detach's flush+close half.
func (h *Handler) Close() error {
	if h.file == nil {
		return nil
	}
	err := h.file.Close()
	h.file = nil
	return err
}

// LevelSource yields the configured level name — config.GetLogLevel in
// production, so the two packages stay decoupled.
type LevelSource func() string

// Registry mirrors logging_setup's module state: the tracked handlers, the
// "yeaboi" namespace logger's level, and the attach-order the logger would
// iterate. Not safe for concurrent use — neither is the Python module
// (CPython's handler list mutations ride the GIL).
type Registry struct {
	levelSource LevelSource
	formatter   *Formatter
	handlers    map[string]*Handler
	order       []string
	loggerLevel int // 0 = NOTSET: effective level falls to the root's WARNING
}

// NewRegistry builds an empty registry over the given level source and
// formatter.
func NewRegistry(levelSource LevelSource, formatter *Formatter) *Registry {
	return &Registry{levelSource: levelSource, formatter: formatter, handlers: map[string]*Handler{}}
}

// resolvedLevel mirrors logging_setup._level().
func (r *Registry) resolvedLevel() int { return ResolveLevel(r.levelSource()) }

// Attach mirrors logging_setup._attach: idempotent per key; parent dir
// created and hardened to 0o700; handler and namespace logger tuned to the
// configured level (re-tuning the logger on EVERY attach — the quirk the
// parity scenario pins after apply_level).
func (r *Registry) Attach(key, path string) error {
	if _, ok := r.handlers[key]; ok {
		return nil
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o777); err != nil {
		return err
	}
	_ = os.Chmod(dir, 0o700)
	h, err := NewHandler(path, MaxBytes, BackupCount, r.resolvedLevel(), r.formatter)
	if err != nil {
		return err
	}
	r.loggerLevel = r.resolvedLevel()
	r.handlers[key] = h
	r.order = append(r.order, key)
	return nil
}

// Detach mirrors logging_setup.detach (no-op when absent).
func (r *Registry) Detach(key string) {
	h, ok := r.handlers[key]
	if !ok {
		return
	}
	_ = h.Close()
	delete(r.handlers, key)
	for i, k := range r.order {
		if k == key {
			r.order = append(r.order[:i], r.order[i+1:]...)
			break
		}
	}
}

// AttachSession mirrors attach_session_log: a new session replaces the
// previous handler. The caller renders the per-session path (the registry
// deliberately doesn't know the paths surface).
func (r *Registry) AttachSession(path string) error {
	r.Detach("session")
	return r.Attach("session", path)
}

// ApplyLevel mirrors logging_setup.apply_level: retune the namespace
// logger AND every tracked handler live.
func (r *Registry) ApplyLevel(level string) {
	resolved := ResolveLevel(level)
	r.loggerLevel = resolved
	for _, h := range r.handlers {
		h.SetLevel(resolved)
	}
}

// effectiveLevel mirrors Logger.getEffectiveLevel: NOTSET delegates to the
// root logger, whose default is WARNING.
func (r *Registry) effectiveLevel() int {
	if r.loggerLevel != LevelNotSet {
		return r.loggerLevel
	}
	return LevelWarning
}

// Emit mirrors “logger.log“ through the namespace logger: the
// isEnabledFor gate, then callHandlers' per-handler level check, in attach
// order.
func (r *Registry) Emit(rec Record) error {
	if rec.Level < r.effectiveLevel() {
		return nil
	}
	for _, key := range r.order {
		h := r.handlers[key]
		if rec.Level >= h.Level() {
			if err := h.Emit(rec); err != nil {
				return err
			}
		}
	}
	return nil
}

// Keys returns the registered handler keys, sorted (the parity dump's
// “registry“ field).
func (r *Registry) Keys() []string {
	out := append([]string(nil), r.order...)
	sort.Strings(out)
	return out
}

// Close detaches everything (the dumper's cleanup).
func (r *Registry) Close() {
	for _, key := range append([]string(nil), r.order...) {
		r.Detach(key)
	}
}
