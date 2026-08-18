// The logfile section of the __dump-foundations document (W8 phase 5).
//
// Python twin: tests/parity/foundations/dump.py _logfile_dump() — the same
// vectors, the same scripted registry + rotation scenario, the same walk of
// the sandbox's logs dir. The vector tables are duplicated from dump.py on
// purpose (see the package comment in foundations.go): dump.py is the
// reference, and the subprocess gate keeps this copy honest.
package foundations

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/yeaboi-ai/yeaboi/go/internal/config"
	"github.com/yeaboi-ai/yeaboi/go/internal/home"
	"github.com/yeaboi-ai/yeaboi/go/internal/logfile"
)

// logLevelVectors mirrors dump.LOG_LEVEL_VECTORS.
var logLevelVectors = []string{
	"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL",
	"debug", "Error", "warn", "fatal", "notset",
	"VERBOSE", "", " warning ",
}

// logRedactVectors mirrors dump.LOG_REDACT_VECTORS.
var logRedactVectors = []string{
	"plain text with no secrets at all, port 8080 true",
	"anthropic sk-ant-api03-AbCdEf123456 trailing",
	"unicode tail sk-ant-abcé2345678é9 done",
	"openai sk-abcdefghijklmnopqrst123 x",
	"github ghp_ABCDEFGHIJKLMNOPQRST clean",
	"fine ghp_short7 stays",
	"pat github_pat_11ABCDEFGHIJKLMNOPQRSTUV done",
	"slack xoxb-1234567890-abc token",
	"google AIzaSyA-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa end",
	"aws AKIAIOSFODNN7EXAMPLE key",
	"atlassian ATATT3xFfGF0T-abc+def/ghi=jkl end",
	"notion ntn_abcdefghijklmnopqrst123 page",
	"notion secret_abcdefghijklmnopqrstuvwxyz1234 page",
	"webhook hooks.slack.com/services/T0AA/B0BB/curly done",
	"webhook at https://hooks.slack.com/services/T0AA/B0BB/curly end",
	"auth Bearer abcdef1234567890XYZ sent",
	"auth bEaReR\u00a0abcdef1234567890XYZ sent", // NBSP separator
	"auth Basic dGVzdDp0ZXN0cGFzcw== ok",
	"auth Bearer short1 ok",
	"url https://svc:AKCp8fffffff@nexus.corp/simple pinned",
	"url https://svc:pw@nexus.corp/simple short-password",
	"url ftp://ghp_ABCDEFGHIJKLMNOPQRST:pw12@x positional-preference",
	"no scheme svc:longpassword@host untouched",
	"value hush-hush-value-123 and hush-hush-value-123-extended overlap",
	"value fallback-key from the project env",
	"mail env-wins@acme.dev interpolated into GOOGLE_API_KEY",
	"tiny short-secret value stays: tiny",
}

// logSafeVectors mirrors dump.LOG_SAFE_VECTORS.
var logSafeVectors = []string{
	"clean value",
	"crlf\r\ninjected\tline",
	"controls \x00\x01 kept? \x7f end",
	"vertical\x0btab\x0cformfeed",
	"nbsp\u00a0stays", // NBSP survives log_safe
	strings.Repeat("x", 200),
	strings.Repeat("y", 201),
	strings.Repeat("é", 230) + "tail",
	"secret ghp_ABCDEFGHIJKLMNOPQRST inside",
}

// logFormatVectors mirrors dump.LOG_FORMAT_VECTORS.
var logFormatVectors = []logfile.Record{
	{Name: "yeaboi.cli", Level: logfile.LevelInfo, Message: "startup complete", Created: 1755500000},
	{Name: "yeaboi.agent.llm", Level: logfile.LevelDebug, Message: "prompt cached", Created: 1755500001},
	{Name: "yeaboi.tools.github", Level: logfile.LevelWarning, Message: "retrying", Created: 1755503661},
	{Name: "yeaboi.retro", Level: logfile.LevelError, Message: "boom with ghp_ABCDEFGHIJKLMNOPQRST token", Created: 1755589199},
	{Name: "yeaboi", Level: logfile.LevelCritical, Message: "unicode café \U0001F680 %s literal", Created: 1755589200},
}

// logRotationMsgs mirrors dump.LOG_ROTATION_MSGS.
var logRotationMsgs = []string{
	"first line of the rotation corpus",
	"second line, a bit longer than the first one is",
	"unicode " + strings.Repeat("é", 29) + " run",
	"fourth line arrives after the unicode run",
	"fifth line pushes the byte count over",
	"token ghp_ABCDEFGHIJKLMNOPQRST rides along",
	strings.Repeat("x", 300),
	"small after the oversize one",
	"closing line of the rotation corpus",
}

const (
	logRotationMaxBytes = 192
	logTS               = 1755500000 // dump._LOG_TS
)

// dumpLogfile mirrors dump._logfile_dump. TZ (the fixtures pin UTC) and
// the redaction secrets both resolve through cfg.Env() — the layered
// environment that mirrors os.environ after Python's dotenv loads, which
// is exactly what redaction.py and time.tzset read at scenario time.
func dumpLogfile(paths *home.Paths, cfg *config.Config) (map[string]any, error) {
	env := cfg.Env()
	loc := time.Local
	if tz, ok := env("TZ"); ok {
		if l, err := time.LoadLocation(tz); err == nil {
			loc = l
		}
	}
	lookup := logfile.EnvLookup(env)
	formatter := &logfile.Formatter{Loc: loc, Lookup: lookup}

	applyLevel := map[string]any{}
	for _, v := range logLevelVectors {
		applyLevel[v] = logfile.ResolveLevel(v)
	}
	redact := make([][]string, len(logRedactVectors))
	for i, v := range logRedactVectors {
		redact[i] = []string{v, logfile.Redact(v, lookup)}
	}
	logSafe := make([][]string, len(logSafeVectors))
	for i, v := range logSafeVectors {
		logSafe[i] = []string{v, logfile.LogSafe(v)}
	}
	format := make([]string, len(logFormatVectors))
	for i, rec := range logFormatVectors {
		format[i] = formatter.Format(rec)
	}

	out := map[string]any{
		"configured_level": logfile.ResolveLevel(cfg.GetLogLevel()),
		"apply_level":      applyLevel,
		"redact":           redact,
		"log_safe":         logSafe,
		"format":           format,
	}

	// --- rotation corpus -------------------------------------------------
	rotDir := filepath.Join(paths.LogsDir, "rotation")
	if err := os.MkdirAll(rotDir, 0o777); err != nil {
		return nil, err
	}
	_ = os.Chmod(rotDir, 0o700)
	rot, err := logfile.NewHandler(filepath.Join(rotDir, "rot.log"), logRotationMaxBytes, 3, logfile.LevelNotSet, formatter)
	if err != nil {
		return nil, err
	}
	for i, msg := range logRotationMsgs {
		if err := rot.Emit(logfile.Record{Name: "yeaboi.rot", Level: logfile.LevelInfo, Message: msg, Created: logTS + 100 + int64(i)}); err != nil {
			return nil, err
		}
	}
	if err := rot.Close(); err != nil {
		return nil, err
	}

	// --- registry scenario ----------------------------------------------
	reg := logfile.NewRegistry(cfg.GetLogLevel, formatter)
	emit := func(name string, level int, msg string, offset int64) error {
		return reg.Emit(logfile.Record{Name: name, Level: level, Message: msg, Created: logTS + offset})
	}
	tuiLog, err := paths.GetTUILogPath()
	if err != nil {
		return nil, err
	}
	notion, _ := env("NOTION_TOKEN")
	if notion == "" {
		notion = "<unset>"
	}
	steps := []func() error{
		func() error { return reg.Attach("tui", tuiLog) }, // configure_logging
		func() error { return emit("yeaboi.cli", logfile.LevelInfo, "startup complete", 0) },
		func() error { return emit("yeaboi.agent.llm", logfile.LevelDebug, "prompt tokens: 512", 1) },
		func() error {
			return emit("yeaboi.tools.github", logfile.LevelError, "auth failed: token ghp_abcdefghij0123456789 rejected", 2)
		},
		func() error { return reg.Attach("retro", filepath.Join(paths.LogsDir, "retro", "retro.log")) },
		func() error { return reg.Attach("retro", filepath.Join(paths.LogsDir, "retro", "retro.log")) },
		func() error { return emit("yeaboi.retro.engine", logfile.LevelWarning, "card parse fallback used", 3) },
		func() error { return reg.AttachSession(filepath.Join(paths.PlanningLogsDir, "sess-alpha.log")) },
		func() error { return emit("yeaboi.agent.nodes", logfile.LevelError, "plan node failed", 4) },
		func() error { return reg.AttachSession(filepath.Join(paths.PlanningLogsDir, "sess-beta.log")) },
		func() error { return emit("yeaboi.agent.nodes", logfile.LevelCritical, "graph aborted", 5) },
		func() error { reg.ApplyLevel("debug"); return nil },
		func() error { return emit("yeaboi.agent.llm", logfile.LevelDebug, "cache hit", 6) },
		func() error { return reg.Attach("poker", filepath.Join(paths.LogsDir, "poker", "poker.log")) },
		func() error { return emit("yeaboi.poker.engine", logfile.LevelDebug, "vote recorded", 7) },
		func() error { return emit("yeaboi.poker.engine", logfile.LevelError, "sync failed", 8) },
		func() error { reg.Detach("retro"); return nil },
		func() error { return emit("yeaboi.cli", logfile.LevelWarning, "shutting down", 9) },
		func() error {
			return emit("yeaboi.tools.notion", logfile.LevelError, "post failed for token "+notion, 10)
		},
	}
	for _, step := range steps {
		if err := step(); err != nil {
			return nil, err
		}
	}
	out["registry"] = reg.Keys()
	reg.Close()

	files := map[string]any{}
	modes := map[string]any{}
	err = filepath.WalkDir(paths.LogsDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, err := filepath.Rel(paths.LogsDir, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		info, err := os.Stat(path)
		if err != nil {
			return err
		}
		files[rel] = string(raw)
		modes[rel] = fmt.Sprintf("0o%o", info.Mode().Perm()&fs.ModePerm)
		return nil
	})
	if err != nil {
		return nil, err
	}
	for _, rel := range []string{"tui", "retro", "poker", "planning", "rotation"} {
		info, err := os.Stat(filepath.Join(paths.LogsDir, rel))
		if err != nil {
			return nil, err
		}
		modes[rel+"/"] = fmt.Sprintf("0o%o", info.Mode().Perm()&fs.ModePerm)
	}
	out["files"] = files
	out["modes"] = modes
	return out, nil
}
