// The yeaboi CLI parse tree.
//
// Python twin: src/yeaboi/cli.py build_parser() — every parser, option,
// dest, default, const, choice list, nargs and required flag, transcribed
// in the same order. Help/description strings are deliberately absent until
// W8 phase 4 (internal/argview), which pins them byte-for-byte against the
// committed help goldens; parse behaviour does not read them.
// tests/parity/goldens/cli/args.json replays the committed argparse
// outcomes against this tree, so a divergence fails `make go-test`.
package main

import (
	ap "github.com/yeaboi-ai/yeaboi/go/internal/argparse"
)

// defaultQuestionnaireFilename mirrors cli.DEFAULT_QUESTIONNAIRE_FILENAME.
const defaultQuestionnaireFilename = "scrum-questionnaire.md"

func choices(vals ...string) []any {
	out := make([]any, len(vals))
	for i, v := range vals {
		out[i] = v
	}
	return out
}

// flag is action="store_true", default False (explicit or argparse's own).
func flag(dest string, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Kind: ap.KindStoreTrue, Default: false}
}

// offFlag is action="store_false" with default True (argparse supplies it).
func offFlag(dest string, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Kind: ap.KindStoreFalse, Default: true}
}

// str is a plain store action for a string option.
func str(dest, metavar string, dflt any, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Metavar: metavar, Default: dflt}
}

// intOpt is type=int with a default (nil for None).
func intOpt(dest, metavar string, dflt any, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Metavar: metavar, Default: dflt, Type: ap.TypeInt}
}

// strChoice is a string store with choices.
func strChoice(dest string, dflt any, opts []any, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Default: dflt, Choices: opts}
}

// multi is nargs="+" (or "*") over strings.
func multi(dest, nargs, metavar string, dflt any, opts []any, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Nargs: nargs, Metavar: metavar, Default: dflt, Choices: opts}
}

// formatOpt is the ubiquitous --format text|json, default text.
func formatOpt() *ap.Action {
	return strChoice("format", "text", choices("text", "json"), "--format")
}

// strictOpt is the ubiquitous --strict flag.
func strictOpt() *ap.Action { return flag("strict", "--strict") }

// sessionOpt is the ubiquitous --session ID, default "".
func sessionOpt() *ap.Action { return str("session", "ID", "", "--session") }

// buildParser mirrors cli.build_parser().
func buildParser() *ap.Parser {
	p := ap.NewParser("yeaboi")
	p.Add(&ap.Action{OptionStrings: []string{"--version"}, Kind: ap.KindVersion})
	p.Add(&ap.Action{
		OptionStrings: []string{"--resume"}, Dest: "resume", Metavar: "SESSION_ID",
		Nargs: "?", Const: "__pick__", Default: nil,
	})
	p.Add(flag("list_sessions", "--list-sessions"))
	p.Add(flag("clear_sessions", "--clear-sessions"))
	p.Add(&ap.Action{
		OptionStrings: []string{"--export-questionnaire"}, Dest: "export_questionnaire", Metavar: "PATH",
		Nargs: "?", Const: defaultQuestionnaireFilename, Default: nil,
	})
	p.Add(str("questionnaire", "PATH", nil, "--questionnaire"))
	quick := p.Add(flag("quick", "--quick"))
	p.MutuallyExclusive(quick)
	p.Add(flag("export_only", "--export-only"))
	p.Add(&ap.Action{OptionStrings: []string{"--prior-art"}, Dest: "prior_art", Kind: ap.KindAppend, Metavar: "REPO_KEY", Default: nil})
	p.Add(strChoice("ac_format", nil, choices("gwt", "bullets"), "--ac-format"))
	p.Add(strChoice("architecture_spike", nil, choices("auto", "include", "skip"), "--architecture-spike"))
	p.Add(flag("no_bell", "--no-bell"))
	p.Add(strChoice("theme", "dark", choices("dark", "light"), "--theme"))
	p.Add(strChoice("mode", nil, choices("project-planning"), "--mode"))
	p.Add(flag("setup", "--setup"))
	p.Add(&ap.Action{OptionStrings: []string{"--allow-path"}, Dest: "allow_path", Kind: ap.KindAppend, Metavar: "PATH", Default: []any{}})
	p.Add(flag("list_audio_devices", "--list-audio-devices"))
	p.Add(flag("install_voice", "--install-voice"))
	p.Add(flag("dry_run", "--dry-run"))
	p.Add(flag("non_interactive", "--non-interactive"))
	p.Add(strChoice("output", nil, choices("markdown", "json", "html", "prd"), "--output"))
	p.Add(str("description", "TEXT", nil, "--description"))
	p.Add(intOpt("team_size", "N", nil, "--team-size"))
	p.Add(&ap.Action{
		OptionStrings: []string{"--sprint-length"}, Dest: "sprint_length", Metavar: "WEEKS",
		Type: ap.TypeInt, Choices: []any{int64(1), int64(2), int64(3), int64(4)}, Default: nil,
	})
	p.Add(flag("standup_run", "--standup-run"))
	p.Add(str("standup_session", "SESSION_ID", nil, "--standup-session"))
	p.Add(strChoice("standup_output", nil, choices("terminal", "desktop", "slack", "email", "all"), "--standup-output"))
	p.Add(flag("standup_interactive", "--standup-interactive"))
	p.Add(flag("standup_remind_transcript", "--standup-remind-transcript"))
	p.Add(flag("learn", "--learn"))
	p.Add(flag("team_profile", "--team-profile"))
	p.Add(&ap.Action{
		OptionStrings: []string{"--retro"}, Dest: "retro", Metavar: "SESSION_ID",
		Nargs: "?", Const: "latest", Default: nil,
	})

	sub := p.AddSubparsers("command", "{report,standup,standup-review,perf,retro,poker,analyze,agents}", false)

	reportP := sub.Sub.AddParser(p, "report")
	reportP.Add(strChoice("period", "last_sprint", choices("last_week", "last_sprint", "last_month", "quarter", "window"), "--period"))
	reportP.Add(sessionOpt())
	reportP.Add(str("window_start", "YYYY-MM-DD", "", "--window-start"))
	reportP.Add(str("window_end", "YYYY-MM-DD", "", "--window-end"))
	reportP.Add(str("sprint_names", "A,B", "", "--sprint-names"))
	reportP.Add(str("label", "TEXT", "", "--label"))
	reportP.Add(str("jira_project", "KEY", "", "--jira-project"))
	reportP.Add(str("azdo_project", "NAME", "", "--azdo-project"))
	reportP.Add(strChoice("source", "", choices("jira", "azdevops", "both"), "--source"))
	reportP.Add(multi("code_sources", "+", "SOURCE", nil, choices("github", "azdevops"), "--code-sources"))
	reportP.Add(multi("documentation_sources", "+", "SOURCE", nil, choices("confluence", "notion"), "--documentation-sources"))
	reportP.Add(strictOpt())
	reportP.Add(formatOpt())
	reportP.Add(str("theme", "NAME", "midnight", "--theme"))

	standupP := sub.Sub.AddParser(p, "standup")
	standupP.Add(sessionOpt())
	standupP.Add(flag("deliver", "--deliver"))
	standupP.Add(multi("channels", "+", "", nil, choices("terminal", "desktop", "slack", "email"), "--channels"))
	standupP.Add(intOpt("days", "", int64(0), "--days"))
	standupP.Add(multi("tracker_sources", "+", "", nil, choices("jira", "azure_devops"), "--tracker-sources"))
	standupP.Add(multi("team_members", "+", "NAME", nil, nil, "--team-members"))
	standupP.Add(multi("code_sources", "+", "", nil, choices("github", "azure_devops"), "--code-sources"))
	standupP.Add(multi("github_owners", "+", "OWNER", nil, nil, "--github-owners"))
	standupP.Add(multi("github_repositories", "+", "OWNER/REPO", nil, nil, "--github-repositories"))
	standupP.Add(multi("github_excluded_repositories", "+", "OWNER/REPO", nil, nil, "--github-excluded-repositories"))
	standupP.Add(multi("azdo_projects", "+", "PROJECT", nil, nil, "--azdo-projects"))
	standupP.Add(multi("azdo_repositories", "+", "PROJECT/REPO", nil, nil, "--azdo-repositories"))
	standupP.Add(multi("documentation_sources", "+", "", nil, choices("confluence", "notion"), "--documentation-sources"))
	standupP.Add(flag("list_members", "--list-members"))
	standupP.Add(strChoice("schedule", nil, choices("install", "remove", "status"), "--schedule"))
	standupP.Add(offFlag("review_transcripts", "--no-transcript-review"))
	standupP.Add(strictOpt())
	standupP.Add(formatOpt())

	reviewP := sub.Sub.AddParser(p, "standup-review")
	reviewP.Add(sessionOpt())
	reviewP.Add(&ap.Action{Dest: "paths", Nargs: "*", Metavar: "PATH"})
	reviewP.Add(multi("transcript_paths", "+", "PATH", nil, nil, "--transcript"))
	reviewP.Add(str("transcript_text", "TEXT", "", "--transcript-text"))
	reviewP.Add(str("transcript_dir", "DIR", "", "--transcript-dir"))
	reviewP.Add(str("standup_date", "YYYY-MM-DD", "", "--date"))
	reviewP.Add(intOpt("max_transcripts", "", int64(5), "--max-transcripts"))
	reviewP.Add(flag("include_reviewed", "--include-reviewed"))
	reviewP.Add(flag("file_issues", "--file-issues"))
	reviewP.Add(flag("list_gaps", "--list-gaps"))
	reviewP.Add(strictOpt())
	reviewP.Add(formatOpt())

	perfP := sub.Sub.AddParser(p, "perf")
	perfSub := perfP.AddSubparsers("perf_command", "{roster,prep,complete,review,note}", true)
	perfSub.Sub.AddParser(perfP, "roster")
	prepP := perfSub.Sub.AddParser(perfP, "prep")
	prepP.Add(&ap.Action{Dest: "engineer", Required: true})
	prepP.Add(sessionOpt())
	prepP.Add(str("jira_project", "KEY", "", "--jira-project"))
	prepP.Add(str("azdo_project", "NAME", "", "--azdo-project"))
	prepP.Add(strictOpt())
	completeP := perfSub.Sub.AddParser(perfP, "complete")
	completeP.Add(&ap.Action{Dest: "engineer", Required: true})
	completeP.Add(&ap.Action{OptionStrings: []string{"--transcript"}, Dest: "transcript", Metavar: "TEXT", Required: true})
	completeP.Add(flag("deliver", "--deliver"))
	completeP.Add(sessionOpt())
	completeP.Add(multi("images", "+", "PATH", []any{}, nil, "--images"))
	completeP.Add(multi("recipients", "+", "EMAIL", nil, nil, "--recipients"))
	completeP.Add(strictOpt())
	perfReviewP := perfSub.Sub.AddParser(perfP, "review")
	perfReviewP.Add(&ap.Action{Dest: "engineer", Required: true})
	perfReviewP.Add(intOpt("months", "", int64(6), "--months"))
	perfReviewP.Add(sessionOpt())
	perfReviewP.Add(str("jira_project", "KEY", "", "--jira-project"))
	perfReviewP.Add(str("azdo_project", "NAME", "", "--azdo-project"))
	perfReviewP.Add(strictOpt())
	noteP := perfSub.Sub.AddParser(perfP, "note")
	noteP.Add(&ap.Action{Dest: "engineer", Required: true})
	noteP.Add(&ap.Action{OptionStrings: []string{"--text"}, Dest: "text", Required: true})

	retroP := sub.Sub.AddParser(p, "retro")
	retroP.Add(sessionOpt())
	retroP.Add(intOpt("limit", "", int64(10), "--limit"))
	retroP.Add(flag("export", "--export-latest"))
	retroP.Add(formatOpt())

	pokerP := sub.Sub.AddParser(p, "poker")
	pokerP.Add(sessionOpt())
	pokerP.Add(intOpt("limit", "", int64(10), "--limit"))
	pokerP.Add(flag("export", "--export-latest"))
	pokerP.Add(formatOpt())

	ceremoniesP := sub.Sub.AddParser(p, "ceremonies")
	cerSub := ceremoniesP.AddSubparsers("ceremonies_command", "", true)
	cerList := cerSub.Sub.AddParser(ceremoniesP, "list")
	cerList.Add(sessionOpt())
	cerList.Add(formatOpt())
	cerAdd := cerSub.Sub.AddParser(ceremoniesP, "add")
	cerAdd.Add(&ap.Action{Dest: "name", Required: true})
	cerAdd.Add(&ap.Action{OptionStrings: []string{"--mode"}, Dest: "mode", Required: true})
	cerAdd.Add(str("at", "HH:MM", "09:00", "--at"))
	cerAdd.Add(str("weekdays", "SPEC", "", "--weekdays"))
	cerAdd.Add(str("channels", "LIST", "terminal", "--channels"))
	cerAdd.Add(&ap.Action{OptionStrings: []string{"--arg"}, Dest: "arg", Kind: ap.KindAppend, Metavar: "KEY=VALUE", Default: []any{}})
	cerAdd.Add(sessionOpt())
	cerAdd.Add(intOpt("stale_after", "MIN", int64(120), "--stale-after"))
	cerAdd.Add(&ap.Action{OptionStrings: []string{"--monthly-cap"}, Dest: "monthly_cap", Metavar: "USD", Type: ap.TypeFloat, Default: float64(0.0)})
	cerAdd.Add(formatOpt())
	cerRm := cerSub.Sub.AddParser(ceremoniesP, "remove")
	cerRm.Add(&ap.Action{Dest: "name", Required: true})
	cerRm.Add(sessionOpt())
	cerRm.Add(formatOpt())
	for _, verb := range []string{"pause", "resume"} {
		s := cerSub.Sub.AddParser(ceremoniesP, verb)
		s.Add(&ap.Action{Dest: "name", Required: true})
		s.Add(sessionOpt())
		s.Add(formatOpt())
	}
	cerRun := cerSub.Sub.AddParser(ceremoniesP, "run")
	cerRun.Add(&ap.Action{Dest: "name", Required: true})
	cerRun.Add(sessionOpt())
	cerRun.Add(flag("scheduled", "--scheduled"))
	cerRun.Add(flag("dry_run", "--dry-run"))
	cerRun.Add(formatOpt())
	cerHist := cerSub.Sub.AddParser(ceremoniesP, "history")
	cerHist.Add(&ap.Action{Dest: "name", Nargs: "?", Default: ""})
	cerHist.Add(sessionOpt())
	cerHist.Add(intOpt("limit", "", int64(20), "--limit"))
	cerHist.Add(formatOpt())
	cerModes := cerSub.Sub.AddParser(ceremoniesP, "modes")
	cerModes.Add(formatOpt())

	agentsP := sub.Sub.AddParser(p, "agents")
	agentsSub := agentsP.AddSubparsers("agents_command", "{cost,advisor,standup,security}", true)
	costP := agentsSub.Sub.AddParser(agentsP, "cost")
	costP.Add(intOpt("window_days", "N", int64(30), "--window-days"))
	costP.Add(str("project", "NAME", "", "--project"))
	costP.Add(strChoice("source", "", choices("", "claude_code"), "--source"))
	costP.Add(formatOpt())
	costP.Add(strictOpt())
	advisorP := agentsSub.Sub.AddParser(agentsP, "advisor")
	advisorP.Add(intOpt("window_days", "N", int64(30), "--window-days"))
	advisorP.Add(formatOpt())
	advisorP.Add(strictOpt())
	astandupP := agentsSub.Sub.AddParser(agentsP, "standup")
	astandupP.Add(intOpt("days", "N", nil, "--days"))
	astandupP.Add(multi("tracker_sources", "*", "SRC", nil, choices("github", "azdo"), "--tracker-sources"))
	astandupP.Add(multi("github_owners", "+", "OWNER", nil, nil, "--github-owners"))
	astandupP.Add(multi("azdo_projects", "+", "NAME", nil, nil, "--azdo-projects"))
	astandupP.Add(offFlag("include_local_sessions", "--no-local-sessions"))
	astandupP.Add(flag("deliver", "--deliver"))
	astandupP.Add(formatOpt())
	astandupP.Add(strictOpt())
	asecP := agentsSub.Sub.AddParser(agentsP, "security")
	asecP.Add(flag("deep", "--deep"))
	asecP.Add(formatOpt())
	asecP.Add(strictOpt())

	provenanceP := sub.Sub.AddParser(p, "provenance")
	provSub := provenanceP.AddSubparsers("provenance_command", "{audit,trace}", true)
	pauditP := provSub.Sub.AddParser(provenanceP, "audit")
	pauditP.Add(intOpt("window_days", "N", int64(30), "--window-days"))
	pauditP.Add(formatOpt())
	pauditP.Add(strictOpt())
	ptraceP := provSub.Sub.AddParser(provenanceP, "trace")
	ptraceP.Add(&ap.Action{Dest: "entity_id", Metavar: "ENTITY", Required: true})
	ptraceP.Add(intOpt("depth", "N", int64(2), "--depth"))
	ptraceP.Add(formatOpt())

	shipP := sub.Sub.AddParser(p, "ship")
	shipSub := shipP.AddSubparsers("ship_command", "{run,status,history}", true)
	srunP := shipSub.Sub.AddParser(shipP, "run")
	srunP.Add(&ap.Action{Dest: "story_id", Metavar: "STORY", Required: true})
	srunP.Add(str("repo", "PATH", ".", "--repo"))
	srunP.Add(sessionOpt())
	srunP.Add(str("check", "CMD", "", "--check"))
	srunP.Add(intOpt("timeout_minutes", "N", int64(30), "--timeout-minutes"))
	srunP.Add(flag("dry_run", "--dry-run"))
	srunP.Add(formatOpt())
	srunP.Add(strictOpt())
	sstatusP := shipSub.Sub.AddParser(shipP, "status")
	sstatusP.Add(formatOpt())
	shistoryP := shipSub.Sub.AddParser(shipP, "history")
	shistoryP.Add(intOpt("limit", "N", int64(10), "--limit"))
	shistoryP.Add(formatOpt())

	analyzeP := sub.Sub.AddParser(p, "analyze")
	analyzeP.Add(strChoice("source", "", choices("jira", "azdevops", "both"), "--source"))
	analyzeP.Add(str("project", "KEY", "", "--project"))
	analyzeP.Add(intOpt("sprints", "", int64(8), "--sprints"))
	analyzeP.Add(strChoice("depth", "deep", choices("quick", "deep"), "--depth"))
	analyzeP.Add(intOpt("window_days", "", int64(120), "--window-days"))
	analyzeP.Add(str("analysis_model", "MODEL", nil, "--analysis-model"))
	analyzeP.Add(multi("features", "+", "FEATURE", nil, choices("delivery", "ai_footprint", "code_health", "documentation"), "--features"))
	analyzeP.Add(&ap.Action{OptionStrings: []string{"--github-owner"}, Dest: "github_owner", Kind: ap.KindAppend, Metavar: "OWNER", Default: nil})
	analyzeP.Add(&ap.Action{OptionStrings: []string{"--azdo-code-project"}, Dest: "azdo_code_project", Kind: ap.KindAppend, Metavar: "PROJECT", Default: nil})
	analyzeP.Add(&ap.Action{OptionStrings: []string{"--confluence-space"}, Dest: "confluence_space", Kind: ap.KindAppend, Metavar: "SPACE", Default: nil})
	analyzeP.Add(&ap.Action{OptionStrings: []string{"--notion-root"}, Dest: "notion_root", Kind: ap.KindAppend, Metavar: "PAGE_ID", Default: nil})
	analyzeP.Add(flag("samples", "--samples"))
	analyzeP.Add(flag("no_insights", "--no-insights"))
	analyzeP.Add(offFlag("include_ai_usage", "--no-ai-usage"))
	analyzeP.Add(offFlag("include_doc_quality", "--no-doc-quality"))
	analyzeP.Add(multi("delivery", "+", "TRACKER", nil, choices("jira", "azdevops"), "--delivery"))
	analyzeP.Add(multi("code", "+", "HOST", nil, choices("github", "azdo"), "--code"))
	analyzeP.Add(multi("docs", "+", "PLATFORM", nil, choices("confluence", "notion"), "--docs"))
	analyzeP.Add(multi("members", "+", "NAME", nil, nil, "--members"))
	analyzeP.Add(strictOpt())
	analyzeP.Add(formatOpt())

	return p
}
