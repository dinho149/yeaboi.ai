package agentwatch

// The agentwatch.security pipeline — a port of
// engine._deterministic_security_report and the whole of
// src/yeaboi/agentwatch/security_checks.py. The LLM summary and history stay
// Python-side.
//
// Two invariants carried over verbatim from security_checks.py:
//
//  1. Never store matched content from a TRANSCRIPT. A finding's detail may
//     quote a CONFIG value (the permission mode, the allow rule) because the
//     user's own settings file is not session content — never widen this to a
//     transcript-derived value.
//  2. Never raise on malformed config: unreadable JSON becomes an info
//     finding. (One documented divergence: Python crashes on a truthy
//     non-object "permissions"/"env" value; Go treats it as empty.)
//
// Iteration order over config objects is DOCUMENT order (contract rule 8) —
// that is what jsonObj preserves — and quoted config values use Python !r
// formatting (contract rule 9, pyReprStr).

import (
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"

	"github.com/yeaboi-ai/yeaboi/go/internal/contract"
)

// The security_checks.py rule patterns, translated per the patterns.go rules
// (\w → pyWord, \s → pySpace; everything else was already RE2-safe). The
// blobs the last three scan are Python-json.dumps re-serializations, which
// are pure ASCII, so the unicode widening is only load-bearing for the raw
// env values secretShapedRe also sees.
var (
	bypassModes      = map[string]bool{"bypasspermissions": true, "dangerouslyskippermissions": true}
	wildcardAllowRe  = regexp.MustCompile(`^(?:\*|Bash\(\*?\)|Bash\(\*[^)]*\))$`)
	broadBashAllowRe = regexp.MustCompile(`(?i)^Bash\((?:rm|curl|wget|sudo|sh|bash|eval|chmod)\b[^)]*\)$`)
	networkPipeRe    = regexp.MustCompile(`\b(?:curl|wget)\b[^|;&]*\|[` + pySpace + `]*(?:sudo[` + pySpace + `]+)?(?:ba|z|da)?sh\b`)
	secretShapedRe   = regexp.MustCompile(`sk-ant-[` + pyWord + `-]{10,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[` + pyWord + `-]{10,}|AKIA[0-9A-Z]{16}`)
	// Python's trailing \b is Unicode-aware; Go's is ASCII. They disagree only
	// when "@latest" is immediately followed by a non-ASCII letter — accepted.
	unpinnedNpxRe = regexp.MustCompile(`@latest\b`)
)

// readJSONConfig ports security_checks._read_json: (parsed object or empty,
// note finding or nil). A missing file and a non-object document are both
// silently empty; a read or parse failure becomes an info finding whose
// detail carries the Python exception class name the same failure would have.
func readJSONConfig(path string) (*jsonObj, *contract.SecurityFinding) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return emptyObj(), nil
		}
		return emptyObj(), unreadableFinding(path, "OSError")
	}
	v, err := decodeOrderedJSON(data)
	if err != nil {
		return emptyObj(), unreadableFinding(path, "JSONDecodeError")
	}
	if obj := asObj(v); obj != nil {
		return obj, nil
	}
	return emptyObj(), nil
}

func unreadableFinding(path, class string) *contract.SecurityFinding {
	return &contract.SecurityFinding{
		Severity:    "info",
		Category:    "settings",
		Title:       "Unreadable agent config",
		Location:    path,
		Pattern:     "unreadable-config",
		Detail:      "could not parse: " + class,
		Remediation: "Fix or remove the file so the audit can read it.",
	}
}

// objOrEmpty mirrors `value or {}` when the result is used as a dict.
func objOrEmpty(v any) *jsonObj {
	if o := asObj(v); o != nil {
		return o
	}
	return emptyObj()
}

// auditOneSettings ports security_checks._audit_one_settings.
func auditOneSettings(path string) []contract.SecurityFinding {
	settings, note := readJSONConfig(path)
	findings := []contract.SecurityFinding{}
	if note != nil {
		findings = append(findings, *note)
	}
	if len(settings.Keys()) == 0 {
		return findings
	}

	permissions := objOrEmpty(settings.GetDefault("permissions", nil))
	defaultModeRaw := permissions.GetDefault("defaultMode", "")
	if bypassModes[strings.ToLower(pyStr(defaultModeRaw))] {
		findings = append(findings, contract.SecurityFinding{
			Severity:    "critical",
			Category:    "settings",
			Title:       "Permission prompts bypassed by default",
			Location:    path,
			Pattern:     "permission-bypass-default",
			Detail:      "permissions.defaultMode is " + pyReprAny(permissions.GetDefault("defaultMode", nil)),
			Remediation: "Remove the bypass default; approve tools per session instead.",
		})
	}
	for _, rule := range listOrEmpty(permissions.GetDefault("allow", nil)) {
		ruleS := pyStr(rule)
		if wildcardAllowRe.MatchString(ruleS) {
			findings = append(findings, contract.SecurityFinding{
				Severity:    "high",
				Category:    "settings",
				Title:       "Wildcard tool allow rule",
				Location:    path,
				Pattern:     "wildcard-allow",
				Detail:      "allow rule " + pyReprStr(ruleS) + " auto-approves everything it matches",
				Remediation: "Replace the wildcard with the specific commands you trust.",
			})
		} else if broadBashAllowRe.MatchString(ruleS) {
			findings = append(findings, contract.SecurityFinding{
				Severity:    "medium",
				Category:    "settings",
				Title:       "Broad shell allow rule",
				Location:    path,
				Pattern:     "broad-bash-allow",
				Detail:      "allow rule " + pyReprStr(ruleS) + " pre-approves a destructive/network command family",
				Remediation: "Narrow the rule to exact commands and arguments.",
			})
		}
	}

	// Hooks run arbitrary shell on the agent's lifecycle. The blob is the
	// Python-json.dumps re-serialization (settings.get("hooks", {})).
	hooksBlob := "{}"
	if settings.Has("hooks") {
		hooksBlob = pyJSONDumps(settings.GetDefault("hooks", nil))
	}
	if networkPipeRe.MatchString(hooksBlob) {
		findings = append(findings, contract.SecurityFinding{
			Severity:    "high",
			Category:    "settings",
			Title:       "Hook pipes a network download into a shell",
			Location:    path,
			Pattern:     "hook-curl-pipe-shell",
			Remediation: "Vendor the script locally instead of piping curl/wget into sh.",
		})
	}

	env := objOrEmpty(settings.GetDefault("env", nil))
	for _, key := range env.Keys() {
		if value, ok := env.Get(key).(string); ok && secretShapedRe.MatchString(value) {
			findings = append(findings, contract.SecurityFinding{
				Severity:    "high",
				Category:    "settings",
				Title:       "Secret-shaped value in settings env",
				Location:    path,
				Pattern:     "secret-in-settings-env",
				Detail:      "env key " + pyReprStr(key) + " holds a credential-shaped value",
				Remediation: "Move the credential to a secret manager or shell profile.",
			})
		}
	}
	return findings
}

// listOrEmpty mirrors `value or []` followed by iteration. Python would also
// iterate a dict's keys there; mirror that shape too.
func listOrEmpty(v any) []any {
	if list, ok := v.([]any); ok {
		return list
	}
	if o := asObj(v); o != nil {
		out := make([]any, 0, len(o.Keys()))
		for _, k := range o.Keys() {
			out = append(out, k)
		}
		return out
	}
	return nil
}

// auditSettings ports security_checks.audit_settings: the global + local +
// per-project settings files, in that order.
func auditSettings(claudeDir, claudeJSON string) []contract.SecurityFinding {
	paths := []string{
		pyPathStr(claudeDir, "settings.json"),
		pyPathStr(claudeDir, "settings.local.json"),
	}
	top, _ := readJSONConfig(claudeJSON) // the note surfaces via inventoryMCP, like Python
	if projects := asObj(top.GetDefault("projects", nil)); projects != nil {
		for _, projectPath := range projects.Keys() {
			paths = append(paths,
				pyPathStr(projectPath, ".claude", "settings.json"),
				pyPathStr(projectPath, ".claude", "settings.local.json"))
		}
	}
	findings := []contract.SecurityFinding{}
	for _, p := range paths {
		findings = append(findings, auditOneSettings(p)...)
	}
	return findings
}

// mcpRecords ports security_checks._mcp_records.
func mcpRecords(servers *jsonObj, scope string) ([]contract.McpServer, []contract.SecurityFinding) {
	records := []contract.McpServer{}
	findings := []contract.SecurityFinding{}
	for _, name := range servers.Keys() {
		spec := asObj(servers.Get(name))
		if spec == nil {
			continue
		}
		url := truthyStr(spec.GetDefault("url", ""))
		command := truthyStr(spec.GetDefault("command", ""))
		args := []string{}
		if list, ok := spec.GetDefault("args", nil).([]any); ok {
			for _, a := range list {
				args = append(args, pyStr(a))
			}
		}
		transport := ""
		if typeVal := spec.GetDefault("type", ""); pyTruthy(typeVal) {
			transport = pyStr(typeVal)
		} else if url != "" {
			transport = "http"
		} else {
			transport = "stdio"
		}
		target := url
		if target == "" {
			target = pyStrip(strings.Join(append([]string{command}, args...), " "))
		}
		location := fmt.Sprintf("%s mcpServers[%s]", scope, name)
		flags := []string{}
		if strings.HasPrefix(url, "http://") {
			flags = append(flags, "plain-http")
			findings = append(findings, contract.SecurityFinding{
				Severity:    "medium",
				Category:    "mcp",
				Title:       "MCP server over plain HTTP",
				Location:    location,
				Pattern:     "plain-http-transport",
				Detail:      "tool traffic (and any tokens in it) travels unencrypted",
				Remediation: "Use https:// (or a local stdio server).",
			})
		}
		commandBlob := strings.Join(append([]string{command}, args...), " ")
		if unpinnedNpxRe.MatchString(commandBlob) {
			flags = append(flags, "unpinned-package")
			findings = append(findings, contract.SecurityFinding{
				Severity:    "medium",
				Category:    "mcp",
				Title:       "MCP server runs an unpinned package",
				Location:    location,
				Pattern:     "unpinned-package",
				Detail:      "@latest re-resolves on every start — a supply-chain change runs unreviewed",
				Remediation: "Pin the package to an exact version.",
			})
		}
		envBlob := "{}"
		if envVal := spec.GetDefault("env", nil); pyTruthy(envVal) {
			envBlob = pyJSONDumps(envVal)
		}
		if secretShapedRe.MatchString(envBlob) {
			flags = append(flags, "inline-credential")
			findings = append(findings, contract.SecurityFinding{
				Severity:    "medium",
				Category:    "mcp",
				Title:       "Credential inlined in MCP config",
				Location:    location,
				Pattern:     "inline-mcp-credential",
				Remediation: "Reference the credential from the environment instead of the config file.",
			})
		}
		records = append(records, contract.McpServer{
			Name: name, Scope: scope, Transport: transport, Target: target, Flags: flags,
		})
	}
	return records, findings
}

// inventoryMCP ports security_checks.inventory_mcp: global + per-project MCP
// servers with risk flags, plus the duplicate-name info notes.
func inventoryMCP(claudeJSON string) ([]contract.McpServer, []contract.SecurityFinding) {
	top, note := readJSONConfig(claudeJSON)
	records := []contract.McpServer{}
	findings := []contract.SecurityFinding{}
	if note != nil {
		findings = append(findings, *note)
	}
	if servers := asObj(top.GetDefault("mcpServers", nil)); servers != nil {
		recs, finds := mcpRecords(servers, "global")
		records = append(records, recs...)
		findings = append(findings, finds...)
	}
	if projects := asObj(top.GetDefault("projects", nil)); projects != nil {
		for _, projectPath := range projects.Keys() {
			cfg := asObj(projects.Get(projectPath))
			if cfg == nil {
				continue
			}
			servers := asObj(cfg.GetDefault("mcpServers", nil))
			if servers == nil {
				continue
			}
			recs, finds := mcpRecords(servers, "project:"+projectPath)
			records = append(records, recs...)
			findings = append(findings, finds...)
		}
	}
	// Duplicate names across scopes: informational, first-seen order.
	var nameOrder []string
	counts := map[string]int{}
	for _, r := range records {
		if counts[r.Name] == 0 {
			nameOrder = append(nameOrder, r.Name)
		}
		counts[r.Name]++
	}
	for _, name := range nameOrder {
		if count := counts[name]; count > 1 {
			findings = append(findings, contract.SecurityFinding{
				Severity: "info",
				Category: "mcp",
				Title:    "MCP server name defined in multiple scopes",
				Location: fmt.Sprintf("mcpServers[%s]", name),
				Pattern:  "duplicate-mcp-name",
				Detail:   fmt.Sprintf("defined %d times; the effective one depends on the working directory", count),
			})
		}
	}
	return records, findings
}

// truthyStr mirrors `str(value or "")`.
func truthyStr(v any) string {
	if !pyTruthy(v) {
		return ""
	}
	return pyStr(v)
}

var severityRank = map[string]int{"critical": 0, "high": 1, "medium": 2, "info": 3}

func sevRank(s string) int {
	if r, ok := severityRank[s]; ok {
		return r
	}
	return 9
}

// rankFindings ports security_checks.rank_findings: a stable sort by
// (severity rank, category, location, pattern) — Go's byte-wise string
// comparison equals Python's code-point comparison over UTF-8.
func rankFindings(findings []contract.SecurityFinding) []contract.SecurityFinding {
	out := append([]contract.SecurityFinding{}, findings...)
	sort.SliceStable(out, func(i, j int) bool {
		a, b := out[i], out[j]
		if ar, br := sevRank(a.Severity), sevRank(b.Severity); ar != br {
			return ar < br
		}
		if a.Category != b.Category {
			return a.Category < b.Category
		}
		if a.Location != b.Location {
			return a.Location < b.Location
		}
		return a.Pattern < b.Pattern
	})
	return out
}

// computePosture ports security_checks.compute_posture.
func computePosture(findings []contract.SecurityFinding) string {
	has := map[string]bool{}
	for _, f := range findings {
		has[f.Severity] = true
	}
	if has["critical"] {
		return "at-risk"
	}
	if has["high"] || has["medium"] {
		return "needs-attention"
	}
	return "good"
}

// Stored-signal presentation constants (engine._STORED_FINDING_*).
var storedFindingTitles = map[string]string{
	"secret":     "Credential-shaped text in a session transcript",
	"risky_tool": "Risky shell command run by an agent",
}

var storedFindingRemediation = map[string]string{
	"secret":     "Rotate the credential; avoid pasting secrets into agent sessions.",
	"risky_tool": "Review the session; consider denying the pattern in your agent's permission rules.",
}

// storedFindings ports engine._stored_findings: collector-persisted signals →
// finding rows. (pattern, file, line) references only — never content.
func storedFindings(store *Store) ([]contract.SecurityFinding, error) {
	rows, err := store.ListFindings()
	if err != nil {
		return nil, err
	}
	out := make([]contract.SecurityFinding, 0, len(rows))
	for _, f := range rows {
		title, ok := storedFindingTitles[f.Category]
		if !ok {
			title = "Session security signal"
		}
		detail := ""
		if f.SessionID != "" {
			detail = "session " + f.SessionID
		}
		out = append(out, contract.SecurityFinding{
			Severity:    f.Severity,
			Category:    f.Category,
			Title:       title,
			Location:    f.SourcePath,
			LineNo:      int(f.LineNo),
			Pattern:     f.Pattern,
			Detail:      detail,
			Remediation: storedFindingRemediation[f.Category],
		})
	}
	return out, nil
}

// RunAgentSecurity services the agentwatch.security method.
func RunAgentSecurity(p *contract.SecurityParams, emit func(*contract.Event)) (*contract.SecurityResult, error) {
	scanLabel := "Scan transcripts"
	if p.ResetCursors {
		scanLabel = "Re-scan every transcript"
	}
	emitPhase(emit, "scan", "running", scanLabel, "")
	store, err := OpenStore(p.DBPath)
	if err != nil {
		return nil, err
	}
	if p.ResetCursors {
		if err := store.ResetCursors(); err != nil {
			store.Close()
			return nil, err
		}
	}
	stats, err := Refresh(store, resolveRoots(p.Roots), emit)
	if err != nil {
		store.Close()
		return nil, err
	}
	emitPhase(emit, "scan", "completed", scanLabel,
		fmt.Sprintf("%d parsed · %d cached", stats.FilesParsed, stats.FilesSkipped))
	allSessions, err := store.ListSessions("")
	if err != nil {
		store.Close()
		return nil, err
	}
	findings, err := storedFindings(store)
	store.Close()
	if err != nil {
		return nil, err
	}

	emitPhase(emit, "settings", "running", "Audit settings", "")
	settingsFindings := auditSettings(p.ClaudeDir, p.ClaudeJSON)
	findings = append(findings, settingsFindings...)
	emitPhase(emit, "settings", "completed", "Audit settings", fmt.Sprintf("%d finding(s)", len(settingsFindings)))
	emitPhase(emit, "mcp", "running", "Inventory MCP servers", "")
	servers, mcpFindings := inventoryMCP(p.ClaudeJSON)
	findings = append(findings, mcpFindings...)
	emitPhase(emit, "mcp", "completed", "Inventory MCP servers", fmt.Sprintf("%d server(s)", len(servers)))

	ranked := rankFindings(findings)
	secretsFound := 0
	flagSet := map[string]bool{}
	for _, f := range ranked {
		if f.Category == "secret" {
			secretsFound++
		}
		if f.Category == "settings" && f.Severity != "info" {
			flagSet[f.Pattern] = true
		}
	}

	artifact := &contract.SecurityArtifact{
		ScanDate:        p.ScanDate,
		Posture:         computePosture(ranked),
		SessionsScanned: distinctSessionCount(allSessions),
		FilesScanned:    stats.FilesSeen,
		SecretsFound:    secretsFound,
		Findings:        ranked,
		McpServers:      servers,
		SettingsFlags:   sortedKeys(flagSet),
		Summary:         "",
		Recommendations: []string{},
		Warnings:        append([]string{}, stats.Warnings...),
		GeneratedAt:     "",
	}
	return &contract.SecurityResult{ContractVersion: contract.Version, Stats: stats, Artifact: artifact}, nil
}
