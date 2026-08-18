// The yeaboi CLI parse tree.
//
// Python twin: src/yeaboi/cli.py build_parser() — every parser, option,
// dest, default, const, choice list, nargs, required flag, help string,
// description and epilog, transcribed in the same order. Parse behaviour is
// pinned by tests/parity/goldens/cli/args.json; the help text is pinned
// byte-for-byte by the help goldens under tests/parity/goldens/cli/help/
// (rendered through internal/argview), so a divergence on either half
// fails `make go-test`.
package main

import (
	ap "github.com/yeaboi-ai/yeaboi/go/internal/argparse"
)

// defaultQuestionnaireFilename mirrors cli.DEFAULT_QUESTIONNAIRE_FILENAME.
const defaultQuestionnaireFilename = "scrum-questionnaire.md"

// The beta vocabulary, mirroring src/yeaboi/beta.py.
const (
	betaTag = "(beta)"

	performanceBetaNotice = "Performance mode is in beta — its output is not yet verified against real " +
		"delivery data. Treat every 1:1 prep, summary and review as a draft to edit, " +
		"not a verdict."

	agentwatchBetaNotice = "The Agents modes are in beta — costs and activity are estimates from local " +
		"session logs and public rate tables, not your provider's bill. Treat every " +
		"number as an estimate to verify, not an invoice."
)

// The two long subcommand descriptions defined inline in build_parser().
const (
	provenanceDesc = "Every deterministic signal yeaboi surfaces (practice nudges, blocker flags, confidence " +
		"adjustments, conflict cards, performance preps and reviews) is recorded in a tamper-evident " +
		"hash chain with its evidence. This command verifies and reads that chain — deterministic, " +
		"local, no LLM involved."

	shipDesc = "Hand a story from your saved sprint plan to a supervised coding agent (Claude Code headless): " +
		"isolated worktree and branch, deterministic validation, a human approval gate at the terminal, " +
		"and a PR only after approval. A user-global launch budget caps runs."
)

func choices(vals ...string) []any {
	out := make([]any, len(vals))
	for i, v := range vals {
		out[i] = v
	}
	return out
}

// flag is action="store_true", default False (explicit or argparse's own).
func flag(dest, help string, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Kind: ap.KindStoreTrue, Default: false, Help: help}
}

// offFlag is action="store_false" with default True (argparse supplies it).
func offFlag(dest, help string, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Kind: ap.KindStoreFalse, Default: true, Help: help}
}

// str is a plain store action for a string option.
func str(dest, metavar string, dflt any, help string, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Metavar: metavar, Default: dflt, Help: help}
}

// intOpt is type=int with a default (nil for None).
func intOpt(dest, metavar string, dflt any, help string, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Metavar: metavar, Default: dflt, Type: ap.TypeInt, Help: help}
}

// strChoice is a string store with choices.
func strChoice(dest string, dflt any, opts []any, help string, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Default: dflt, Choices: opts, Help: help}
}

// multi is nargs="+" (or "*") over strings.
func multi(dest, nargs, metavar string, dflt any, opts []any, help string, names ...string) *ap.Action {
	return &ap.Action{OptionStrings: names, Dest: dest, Nargs: nargs, Metavar: metavar, Default: dflt, Choices: opts, Help: help}
}

// formatOpt is the ubiquitous --format text|json, default text.
func formatOpt() *ap.Action {
	return strChoice("format", "text", choices("text", "json"), "Output format", "--format")
}

// strictOpt is the ubiquitous --strict flag (the help line varies per mode).
func strictOpt(help string) *ap.Action { return flag("strict", help, "--strict") }

const strictWarnings = "Exit 3 on a degraded run (warnings present)"

// sessionOpt is the ubiquitous --session ID, default "".
func sessionOpt(help string) *ap.Action { return str("session", "ID", "", help, "--session") }

// buildParser mirrors cli.build_parser().
func buildParser() *ap.Parser {
	p := ap.NewParser("yeaboi")
	p.Description = "yeaboi.ai — best friend to engineers and agents. Runs your team's scrum, and watches your AI agents work."
	p.Epilog = "examples:\n" +
		"  yeaboi                        interactive mode (recommended)\n" +
		"  yeaboi --quick                quick intake (2 questions only)\n" +
		"  yeaboi --questionnaire q.md   import pre-filled questionnaire\n" +
		"  yeaboi --export-only --quick  non-interactive, auto-accept all\n" +
		"  yeaboi --resume               resume last session (interactive picker)\n" +
		"  yeaboi --resume latest         resume most recent session\n" +
		"  yeaboi --list-sessions         list all saved sessions\n" +
		"  yeaboi --clear-sessions        delete saved sessions\n" +
		"  yeaboi --non-interactive --description \"Build a todo app\"  headless mode\n" +
		"  yeaboi --non-interactive --description \"...\" --output json  JSON to stdout"
	p.Raw = true
	p.Add(&ap.Action{
		OptionStrings: []string{"--version"}, Kind: ap.KindVersion,
		Help: "show program's version number and exit",
	})
	p.Add(&ap.Action{
		OptionStrings: []string{"--resume"}, Dest: "resume", Metavar: "SESSION_ID",
		Nargs: "?", Const: "__pick__", Default: nil,
		Help: "Resume a previous session. Without an argument, shows an interactive session picker. " +
			"Pass 'latest' to resume the most recent session, or a session ID to resume a specific one.",
	})
	p.Add(flag("list_sessions", "List all saved sessions and exit.", "--list-sessions"))
	p.Add(flag("clear_sessions", "Interactively delete saved sessions (pick one or clear all) and exit.", "--clear-sessions"))
	p.Add(&ap.Action{
		OptionStrings: []string{"--export-questionnaire"}, Dest: "export_questionnaire", Metavar: "PATH",
		Nargs: "?", Const: defaultQuestionnaireFilename, Default: nil,
		Help: "Export a blank questionnaire template as Markdown (default: " + defaultQuestionnaireFilename + ").",
	})
	p.Add(str("questionnaire", "PATH", nil,
		"Import a filled-in questionnaire Markdown file and jump to confirmation.", "--questionnaire"))
	quick := p.Add(flag("quick",
		"Quick intake — only ask team size and tech stack, auto-fill everything else.", "--quick"))
	p.MutuallyExclusive(quick)
	p.Add(flag("export_only",
		"Auto-accept all review checkpoints and exit after the full plan is generated. "+
			"Combine with --quick or --questionnaire for fully non-interactive runs.", "--export-only"))
	p.Add(&ap.Action{
		OptionStrings: []string{"--prior-art"}, Dest: "prior_art", Kind: ap.KindAppend, Metavar: "REPO_KEY", Default: nil,
		Help: "Existing repository to build this plan on, as a key like 'github:acme/auth' " +
			"(repeatable). Requires --non-interactive; an interactive run asks about prior art " +
			"in the intake instead, so the flag is ignored there.",
	})
	p.Add(strChoice("ac_format", nil, choices("gwt", "bullets"),
		"Acceptance-criteria style for generated stories: 'gwt' (Given/When/Then) or "+
			"'bullets' (clear testable statements). Default: follow the learned team profile "+
			"(or YEABOI_AC_FORMAT).", "--ac-format"))
	p.Add(strChoice("architecture_spike", nil, choices("auto", "include", "skip"),
		"Whether to add an architecture-validation spike when the analyzer's decision is "+
			"open (2+ options): 'include'/'skip' force it, 'auto' adds it unless the analyzer's "+
			"confidence is high. Default: ask interactively (auto in non-interactive runs).", "--architecture-spike"))
	p.Add(flag("no_bell", "Disable terminal bell after pipeline steps.", "--no-bell"))
	p.Add(strChoice("theme", "dark", choices("dark", "light"),
		"Terminal colour theme (default: dark). Use 'light' for white/cream backgrounds.", "--theme"))
	p.Add(strChoice("mode", nil, choices("project-planning"),
		"Skip the startup menu and launch directly into a specific mode.", "--mode"))
	p.Add(flag("setup", "Re-run the first-time setup wizard to update credentials.", "--setup"))
	p.Add(&ap.Action{
		OptionStrings: []string{"--allow-path"}, Dest: "allow_path", Kind: ap.KindAppend, Metavar: "PATH", Default: []any{},
		Help: "Allow filesystem access to PATH for this run only (repeatable). " +
			"yeaboi is sandboxed to ~/.yeaboi; persistent allowances live in " +
			"YEABOI_ALLOWED_PATHS or Settings → Allowed Paths.",
	})
	p.Add(flag("list_audio_devices",
		"List the microphones yeaboi can record from, then exit. "+
			"Set the one you want with VOICE_DEVICE (or Settings → Voice Input).", "--list-audio-devices"))
	p.Add(flag("install_voice",
		"Install the dictation packages and speech model into this environment, then exit. "+
			"The same thing double-tapping Space offers inside the app — this is the non-TUI path "+
			"for CI, dev containers and terminals the full-screen UI cannot drive.", "--install-voice"))
	p.Add(flag("dry_run",
		"Run the TUI with mock data and fake delays — no LLM calls. For UI development.", "--dry-run"))
	p.Add(flag("non_interactive",
		"Run the full pipeline headlessly (no user interaction). Requires --description.", "--non-interactive"))
	p.Add(strChoice("output", nil, choices("markdown", "json", "html", "prd"),
		"Output format for the generated plan ('prd' writes a Product Requirements "+
			"Document; one extra LLM call). Only valid with --non-interactive or --export-only.", "--output"))
	p.Add(str("description", "TEXT", nil,
		"Project description for headless mode. Use @file.txt to read from a file.", "--description"))
	p.Add(intOpt("team_size", "N", nil,
		"Team size (maps to intake Q6). Only used with --non-interactive.", "--team-size"))
	p.Add(&ap.Action{
		OptionStrings: []string{"--sprint-length"}, Dest: "sprint_length", Metavar: "WEEKS",
		Type: ap.TypeInt, Choices: []any{int64(1), int64(2), int64(3), int64(4)}, Default: nil,
		Help: "Sprint length in weeks (maps to intake Q8). Only used with --non-interactive.",
	})
	p.Add(flag("standup_run",
		"Run a daily standup headlessly and deliver it (used by the OS scheduler).", "--standup-run"))
	p.Add(str("standup_session", "SESSION_ID", nil,
		"Session to run the standup for. Defaults to the most recent session. Only used with --standup-run.",
		"--standup-session"))
	p.Add(strChoice("standup_output", nil, choices("terminal", "desktop", "slack", "email", "all"),
		"Override delivery channel(s) for --standup-run (default: the session's saved channels).", "--standup-output"))
	p.Add(flag("standup_interactive",
		"With --standup-run: prompt for your update + confirm (timed) before generating. "+
			"What the scheduler opens in a terminal; falls back to headless when no TTY.", "--standup-interactive"))
	p.Add(flag("standup_remind_transcript",
		"Post a desktop reminder if standups went unchecked against their meetings (used by the OS scheduler).",
		"--standup-remind-transcript"))
	p.Add(flag("learn",
		"Analyse historical Jira/AzDO sprint data and store a team calibration profile. "+
			"Subsequent planning sessions use this profile to calibrate estimates.", "--learn"))
	p.Add(flag("team_profile",
		"Display the current stored team calibration profile and exit.", "--team-profile"))
	p.Add(&ap.Action{
		OptionStrings: []string{"--retro"}, Dest: "retro", Metavar: "SESSION_ID",
		Nargs: "?", Const: "latest", Default: nil,
		Help: "Compare a past session's plan to actual Jira/AzDO outcomes. " +
			"Pass a session ID or omit for the most recent session.",
	})

	sub := p.AddSubparsers("command", "{report,standup,standup-review,perf,retro,poker,analyze,agents}", false)

	reportP := sub.Sub.AddParser(p, "report", "Generate a stakeholder delivery report (Reporting mode)")
	reportP.Add(strChoice("period", "last_sprint",
		choices("last_week", "last_sprint", "last_month", "quarter", "window"), "", "--period"))
	reportP.Add(sessionOpt("Session to use (default: most recent)"))
	reportP.Add(str("window_start", "YYYY-MM-DD", "",
		"Explicit window start (quarter/window periods)", "--window-start"))
	reportP.Add(str("window_end", "YYYY-MM-DD", "", "Explicit window end", "--window-end"))
	reportP.Add(str("sprint_names", "A,B", "",
		"Comma-separated sprint names framing a quarter report", "--sprint-names"))
	reportP.Add(str("label", "TEXT", "", `Period label override (e.g. "Q3 2026")`, "--label"))
	reportP.Add(str("jira_project", "KEY", "", "Jira project key override", "--jira-project"))
	reportP.Add(str("azdo_project", "NAME", "", "Azure DevOps project override", "--azdo-project"))
	reportP.Add(strChoice("source", "", choices("jira", "azdevops", "both"),
		"Ticketing source(s) for delivered work (default: every configured tracker)", "--source"))
	reportP.Add(multi("code_sources", "+", "SOURCE", nil, choices("github", "azdevops"),
		"Code hosts to pull supporting PR/commit context from (default: all configured)", "--code-sources"))
	reportP.Add(multi("documentation_sources", "+", "SOURCE", nil, choices("confluence", "notion"),
		"Doc platforms to pull supporting doc-update context from (default: all configured)",
		"--documentation-sources"))
	reportP.Add(strictOpt("Exit 3 on a degraded run (warnings/empty report)"))
	reportP.Add(formatOpt())
	reportP.Add(str("theme", "NAME", "midnight",
		"Export palette: midnight/aurora/sunset/mono or a custom name from reporting_themes.json", "--theme"))

	standupP := sub.Sub.AddParser(p, "standup", "Run a Daily Standup (alias of --standup-run, more knobs)")
	standupP.Add(sessionOpt("Session to use (default: most recent)"))
	standupP.Add(flag("deliver", "Send to the configured channels (default: print)", "--deliver"))
	standupP.Add(multi("channels", "+", "", nil, choices("terminal", "desktop", "slack", "email"),
		"Override delivery channels", "--channels"))
	standupP.Add(intOpt("days", "", int64(0), "Activity look-back window override", "--days"))
	standupP.Add(multi("tracker_sources", "+", "", nil, choices("jira", "azure_devops"),
		"Override saved standup tracker sources", "--tracker-sources"))
	standupP.Add(multi("team_members", "+", "NAME", nil, nil,
		"Override the saved authoritative team roster for this run", "--team-members"))
	standupP.Add(multi("code_sources", "+", "", nil, choices("github", "azure_devops"),
		"Override saved code providers for this run", "--code-sources"))
	standupP.Add(multi("github_owners", "+", "OWNER", nil, nil,
		"Override saved GitHub owner/organisation scope (covers every active repo inside each)",
		"--github-owners"))
	standupP.Add(multi("github_repositories", "+", "OWNER/REPO", nil, nil,
		"Override saved GitHub repository scope (exact repos, unioned with --github-owners)",
		"--github-repositories"))
	standupP.Add(multi("github_excluded_repositories", "+", "OWNER/REPO", nil, nil,
		"Override saved GitHub repos to drop from an included owner's expansion",
		"--github-excluded-repositories"))
	standupP.Add(multi("azdo_projects", "+", "PROJECT", nil, nil,
		"Override saved Azure DevOps project scope (all repositories in each project)", "--azdo-projects"))
	standupP.Add(multi("azdo_repositories", "+", "PROJECT/REPO", nil, nil,
		"Legacy Azure Repos override", "--azdo-repositories"))
	standupP.Add(multi("documentation_sources", "+", "", nil, choices("confluence", "notion"),
		"Override saved documentation providers for this run", "--documentation-sources"))
	standupP.Add(flag("list_members",
		"List discovered members for the selected/saved tracker sources and exit", "--list-members"))
	standupP.Add(strChoice("schedule", nil, choices("install", "remove", "status"),
		"Manage the OS schedule (launchd/cron) that runs the standup daily, instead of running one now",
		"--schedule"))
	standupP.Add(offFlag("review_transcripts",
		"Skip the pre-standup review of any unreviewed meeting transcripts", "--no-transcript-review"))
	standupP.Add(strictOpt(strictWarnings))
	standupP.Add(formatOpt())

	reviewP := sub.Sub.AddParser(p, "standup-review",
		"Review standup meeting transcripts to find what standup missed, and why")
	reviewP.Add(sessionOpt("Session to use (default: most recent)"))
	reviewP.Add(&ap.Action{Dest: "paths", Nargs: "*", Metavar: "PATH",
		Help: "Transcript files to review (same as --transcript; '-' reads the transcript from stdin)"})
	reviewP.Add(multi("transcript_paths", "+", "PATH", nil, nil,
		"Review specific transcript files instead of sweeping the transcript folders", "--transcript"))
	reviewP.Add(str("transcript_text", "TEXT", "",
		"Review transcript text directly; it is saved to ~/.yeaboi/transcripts first", "--transcript-text"))
	reviewP.Add(str("transcript_dir", "DIR", "",
		"An extra transcript folder for this run (~/.yeaboi/transcripts is always swept)", "--transcript-dir"))
	reviewP.Add(str("standup_date", "YYYY-MM-DD", "",
		"Attribute transcripts to this standup date when their own date can't be inferred", "--date"))
	reviewP.Add(intOpt("max_transcripts", "", int64(5),
		"Cap on distinct standup dates reviewed (one AI call each)", "--max-transcripts"))
	reviewP.Add(flag("include_reviewed",
		"Re-review transcripts that have already been processed", "--include-reviewed"))
	reviewP.Add(flag("file_issues",
		"File the drafted gaps as GitHub issues (writes to a PUBLIC repo; off by default)", "--file-issues"))
	reviewP.Add(flag("list_gaps",
		"List past reviews and the gap→issue ledger instead of running a review", "--list-gaps"))
	reviewP.Add(strictOpt(strictWarnings))
	reviewP.Add(formatOpt())

	perfP := sub.Sub.AddParser(p, "perf", "Performance mode "+betaTag+": 1:1 prep/completion, reviews, notes")
	perfP.Description = performanceBetaNotice
	perfSub := perfP.AddSubparsers("perf_command", "{roster,prep,complete,review,note}", true)
	rosterP := perfSub.Sub.AddParser(perfP, "roster", "List the engineer roster from recent tracker assignees")
	rosterP.Description = performanceBetaNotice
	prepP := perfSub.Sub.AddParser(perfP, "prep", "Prepare a 1:1 for an engineer")
	prepP.Description = performanceBetaNotice
	prepP.Add(&ap.Action{Dest: "engineer", Required: true, Help: "Engineer name (see `yeaboi perf roster`)"})
	prepP.Add(sessionOpt("Session for team context (default: most recent)"))
	prepP.Add(str("jira_project", "KEY", "", "Jira project key override", "--jira-project"))
	prepP.Add(str("azdo_project", "NAME", "", "Azure DevOps project override", "--azdo-project"))
	prepP.Add(strictOpt(strictWarnings))
	completeP := perfSub.Sub.AddParser(perfP, "complete", "Complete a held 1:1 from its transcript")
	completeP.Description = performanceBetaNotice
	completeP.Add(&ap.Action{Dest: "engineer", Required: true, Help: "Engineer name"})
	completeP.Add(&ap.Action{OptionStrings: []string{"--transcript"}, Dest: "transcript", Metavar: "TEXT",
		Required: true, Help: "Transcript text; @file.txt reads from file"})
	completeP.Add(flag("deliver", "Email the summary via the configured SMTP", "--deliver"))
	completeP.Add(sessionOpt("Session for team context"))
	completeP.Add(multi("images", "+", "PATH", []any{}, nil,
		"Whiteboard/notes photos to attach to the summary", "--images"))
	completeP.Add(multi("recipients", "+", "EMAIL", nil, nil,
		"Email recipients override (with --deliver)", "--recipients"))
	completeP.Add(strictOpt(strictWarnings))
	perfReviewP := perfSub.Sub.AddParser(perfP, "review", "Draft a periodic performance review")
	perfReviewP.Description = performanceBetaNotice
	perfReviewP.Add(&ap.Action{Dest: "engineer", Required: true, Help: "Engineer name"})
	perfReviewP.Add(intOpt("months", "", int64(6), "Review period in months (default 6)", "--months"))
	perfReviewP.Add(sessionOpt("Session for team context"))
	perfReviewP.Add(str("jira_project", "KEY", "", "Jira project key override", "--jira-project"))
	perfReviewP.Add(str("azdo_project", "NAME", "", "Azure DevOps project override", "--azdo-project"))
	perfReviewP.Add(strictOpt(strictWarnings))
	noteP := perfSub.Sub.AddParser(perfP, "note", "Record a note about an engineer")
	noteP.Description = performanceBetaNotice
	noteP.Add(&ap.Action{Dest: "engineer", Required: true, Help: "Engineer name"})
	noteP.Add(&ap.Action{OptionStrings: []string{"--text"}, Dest: "text", Required: true, Help: "The note text"})

	retroP := sub.Sub.AddParser(p, "retro", "Read past retrospectives (the live board runs in the TUI)")
	retroP.Add(sessionOpt("Session to read (default: most recent)"))
	retroP.Add(intOpt("limit", "", int64(10), "Number of past retros to show (default 10)", "--limit"))
	retroP.Add(flag("export", "Also export the latest retro to Markdown + HTML", "--export-latest"))
	retroP.Add(formatOpt())

	pokerP := sub.Sub.AddParser(p, "poker", "Read past poker sessions (the live voting board runs in the TUI)")
	pokerP.Add(sessionOpt("Only show sessions recorded under this id"))
	pokerP.Add(intOpt("limit", "", int64(10), "Number of past sessions to show (default 10)", "--limit"))
	pokerP.Add(flag("export", "Also export the latest poker session to Markdown + HTML", "--export-latest"))
	pokerP.Add(formatOpt())

	ceremoniesP := sub.Sub.AddParser(p, "ceremonies", "Run a mode on a cadence — declare it once, the OS fires it")
	cerSub := ceremoniesP.AddSubparsers("ceremonies_command", "", true)
	cerList := cerSub.Sub.AddParser(ceremoniesP, "list", "Show this session's ceremonies and what they last did")
	cerList.Add(sessionOpt("Session to read (default: most recent)"))
	cerList.Add(formatOpt())
	cerAdd := cerSub.Sub.AddParser(ceremoniesP, "add", "Declare a ceremony and install its job")
	cerAdd.Add(&ap.Action{Dest: "name", Required: true,
		Help: "A short name — lowercase letters, digits, dot, dash, underscore"})
	cerAdd.Add(&ap.Action{OptionStrings: []string{"--mode"}, Dest: "mode", Required: true,
		Help: "Which mode runs (see `ceremonies modes`)"})
	cerAdd.Add(str("at", "HH:MM", "09:00", "Local time it should happen (default 09:00)", "--at"))
	cerAdd.Add(str("weekdays", "SPEC", "", "e.g. 1-5, 1,3,5 (default: the mode's own)", "--weekdays"))
	cerAdd.Add(str("channels", "LIST", "terminal",
		"Comma-separated: terminal, desktop, slack, email (default terminal)", "--channels"))
	cerAdd.Add(&ap.Action{OptionStrings: []string{"--arg"}, Dest: "arg", Kind: ap.KindAppend,
		Metavar: "KEY=VALUE", Default: []any{}, Help: "A mode argument; repeatable (see `ceremonies modes`)"})
	cerAdd.Add(sessionOpt("Session to attach it to"))
	cerAdd.Add(intOpt("stale_after", "MIN", int64(120),
		"Skip a scheduled run this many minutes late (0 disables; default 120)", "--stale-after"))
	cerAdd.Add(&ap.Action{OptionStrings: []string{"--monthly-cap"}, Dest: "monthly_cap", Metavar: "USD",
		Type: ap.TypeFloat, Default: float64(0.0), Help: "Skip scheduled runs past this month's spend"})
	cerAdd.Add(formatOpt())
	cerRm := cerSub.Sub.AddParser(ceremoniesP, "remove", "Forget a ceremony and tear its job down")
	cerRm.Add(&ap.Action{Dest: "name", Required: true})
	cerRm.Add(sessionOpt(""))
	cerRm.Add(formatOpt())
	for _, verb := range []struct{ name, blurb string }{
		{"pause", "Stop a ceremony firing, keeping it declared"},
		{"resume", "Start it again"},
	} {
		s := cerSub.Sub.AddParser(ceremoniesP, verb.name, verb.blurb)
		s.Add(&ap.Action{Dest: "name", Required: true})
		s.Add(sessionOpt(""))
		s.Add(formatOpt())
	}
	cerRun := cerSub.Sub.AddParser(ceremoniesP, "run", "Run one now (this is what the scheduled job invokes)")
	cerRun.Add(&ap.Action{Dest: "name", Required: true})
	cerRun.Add(sessionOpt(""))
	cerRun.Add(flag("scheduled",
		"Arm the guards a fired run needs: staleness, the monthly cap, and pause", "--scheduled"))
	cerRun.Add(flag("dry_run", "Run the engine without LLM calls or delivery", "--dry-run"))
	cerRun.Add(formatOpt())
	cerHist := cerSub.Sub.AddParser(ceremoniesP, "history", "What the ceremonies actually did")
	cerHist.Add(&ap.Action{Dest: "name", Nargs: "?", Default: "", Help: "Only this ceremony (default: all)"})
	cerHist.Add(sessionOpt(""))
	cerHist.Add(intOpt("limit", "", int64(20), "Rows to show (default 20)", "--limit"))
	cerHist.Add(formatOpt())
	cerModes := cerSub.Sub.AddParser(ceremoniesP, "modes", "Which modes can run on a cadence, and which cannot")
	cerModes.Add(formatOpt())

	agentsP := sub.Sub.AddParser(p, "agents",
		"Agents mode "+betaTag+": monitor your AI coding agents (cost, recoverable spend, activity, security)")
	agentsP.Description = agentwatchBetaNotice
	agentsSub := agentsP.AddSubparsers("agents_command", "{cost,advisor,standup,security}", true)
	costP := agentsSub.Sub.AddParser(agentsP, "cost",
		"What your agents cost: per-model/project/source breakdowns + daily trend")
	costP.Description = agentwatchBetaNotice
	costP.Add(intOpt("window_days", "N", int64(30), "Days to look back (default 30)", "--window-days"))
	costP.Add(str("project", "NAME", "", "Filter by project directory name (substring)", "--project"))
	costP.Add(strChoice("source", "", choices("", "claude_code"), "Filter by telemetry source", "--source"))
	costP.Add(formatOpt())
	costP.Add(strictOpt(strictWarnings))
	advisorP := agentsSub.Sub.AddParser(agentsP, "advisor",
		"How much of your agent spend is recoverable: Read waste, cache health, prompt-prefix churn")
	advisorP.Description = agentwatchBetaNotice
	advisorP.Add(intOpt("window_days", "N", int64(30), "Days to look back (default 30)", "--window-days"))
	advisorP.Add(formatOpt())
	advisorP.Add(strictOpt(strictWarnings))
	astandupP := agentsSub.Sub.AddParser(agentsP, "standup",
		"Daily digest of what your agents did (sessions + agent-authored commits/PRs)")
	astandupP.Description = agentwatchBetaNotice
	astandupP.Add(intOpt("days", "N", nil,
		"Days to look back (default: since the previous working day)", "--days"))
	astandupP.Add(multi("tracker_sources", "*", "SRC", nil, choices("github", "azdo"),
		"Trackers to scan for agent-authored work (default both; pass none for local-only)",
		"--tracker-sources"))
	astandupP.Add(multi("github_owners", "+", "OWNER", nil, nil,
		"GitHub owners/orgs to scan (default configured)", "--github-owners"))
	astandupP.Add(multi("azdo_projects", "+", "NAME", nil, nil,
		"Azure DevOps projects to scan (default configured)", "--azdo-projects"))
	astandupP.Add(offFlag("include_local_sessions",
		"Skip local session logs for a tracker-only digest (use off this machine)", "--no-local-sessions"))
	astandupP.Add(flag("deliver", "Post the digest to the configured Slack webhook", "--deliver"))
	astandupP.Add(formatOpt())
	astandupP.Add(strictOpt(strictWarnings))
	asecP := agentsSub.Sub.AddParser(agentsP, "security",
		"Audit your agent setup: permissions, MCP servers, secrets exposure, risky commands")
	asecP.Description = agentwatchBetaNotice
	asecP.Add(flag("deep", "Re-scan every transcript, not just new/changed ones", "--deep"))
	asecP.Add(formatOpt())
	asecP.Add(strictOpt(strictWarnings))

	provenanceP := sub.Sub.AddParser(p, "provenance",
		"Audit the tamper-evident decision chain behind yeaboi's signals")
	provenanceP.Description = provenanceDesc
	provSub := provenanceP.AddSubparsers("provenance_command", "{audit,trace}", true)
	pauditP := provSub.Sub.AddParser(provenanceP, "audit",
		"Verify every chain link and summarise the recorded decisions")
	pauditP.Description = provenanceDesc
	pauditP.Add(intOpt("window_days", "N", int64(30), "Days to look back (default 30)", "--window-days"))
	pauditP.Add(formatOpt())
	pauditP.Add(strictOpt("Exit 3 on a broken chain or an empty one (warnings present)"))
	ptraceP := provSub.Sub.AddParser(provenanceP, "trace",
		`The "why" trail behind one recorded decision, evidence included`)
	ptraceP.Description = provenanceDesc
	ptraceP.Add(&ap.Action{Dest: "entity_id", Metavar: "ENTITY", Required: true,
		Help: "Entity id, as listed by `yeaboi provenance audit`"})
	ptraceP.Add(intOpt("depth", "N", int64(2), "Evidence hops to follow (default 2)", "--depth"))
	ptraceP.Add(formatOpt())

	shipP := sub.Sub.AddParser(p, "ship", "Implement a story from your plan via a supervised coding agent")
	shipP.Description = shipDesc
	shipSub := shipP.AddSubparsers("ship_command", "{run,status,history}", true)
	srunP := shipSub.Sub.AddParser(shipP, "run", "Run one story through the pipeline")
	srunP.Description = shipDesc
	srunP.Add(&ap.Action{Dest: "story_id", Metavar: "STORY", Required: true,
		Help: "Story id from the plan (e.g. US-001)"})
	srunP.Add(str("repo", "PATH", ".", "Target git repository (default: current dir)", "--repo"))
	srunP.Add(sessionOpt("Planning session id (default: latest)"))
	srunP.Add(str("check", "CMD", "",
		"Validation command run in the worktree (e.g. 'make test')", "--check"))
	srunP.Add(intOpt("timeout_minutes", "N", int64(30), "Agent run timeout (default 30)", "--timeout-minutes"))
	srunP.Add(flag("dry_run", "Canned run — no agent, no git, no network", "--dry-run"))
	srunP.Add(formatOpt())
	srunP.Add(strictOpt("Exit 3 when the run did not end approved"))
	sstatusP := shipSub.Sub.AddParser(shipP, "status", "The latest run and the launch budget")
	sstatusP.Description = shipDesc
	sstatusP.Add(formatOpt())
	shistoryP := shipSub.Sub.AddParser(shipP, "history", "Recent runs, newest first")
	shistoryP.Description = shipDesc
	shistoryP.Add(intOpt("limit", "N", int64(10), "Runs to show (default 10)", "--limit"))
	shistoryP.Add(formatOpt())

	analyzeP := sub.Sub.AddParser(p, "analyze", "Analyse team board history into a calibration profile")
	analyzeP.Add(strChoice("source", "", choices("jira", "azdevops", "both"),
		"Tracker: jira, azdevops, or both (default: auto-detect a single tracker)", "--source"))
	analyzeP.Add(str("project", "KEY", "", "Project key (default: configured)", "--project"))
	analyzeP.Add(intOpt("sprints", "", int64(8), "Closed sprints to analyse (default 8)", "--sprints"))
	analyzeP.Add(strChoice("depth", "deep", choices("quick", "deep"),
		"Analysis depth: deep provides exhaustive AI enrichment; quick is metrics-only (default deep)",
		"--depth"))
	analyzeP.Add(intOpt("window_days", "", int64(120),
		"Changed-content window shared by Code and Docs (default 120)", "--window-days"))
	analyzeP.Add(str("analysis_model", "MODEL", nil,
		"Per-run model for structured Analysis tasks (final synthesis still uses the primary model)",
		"--analysis-model"))
	analyzeP.Add(multi("features", "+", "FEATURE", nil,
		choices("delivery", "ai_footprint", "code_health", "documentation"),
		"Analysis areas to run (default: all supported by the selected integrations)", "--features"))
	analyzeP.Add(&ap.Action{OptionStrings: []string{"--github-owner"}, Dest: "github_owner",
		Kind: ap.KindAppend, Metavar: "OWNER", Default: nil})
	analyzeP.Add(&ap.Action{OptionStrings: []string{"--azdo-code-project"}, Dest: "azdo_code_project",
		Kind: ap.KindAppend, Metavar: "PROJECT", Default: nil})
	analyzeP.Add(&ap.Action{OptionStrings: []string{"--confluence-space"}, Dest: "confluence_space",
		Kind: ap.KindAppend, Metavar: "SPACE", Default: nil})
	analyzeP.Add(&ap.Action{OptionStrings: []string{"--notion-root"}, Dest: "notion_root",
		Kind: ap.KindAppend, Metavar: "PAGE_ID", Default: nil})
	analyzeP.Add(flag("samples",
		"Also generate sample tickets (requires --depth deep; extra LLM calls)", "--samples"))
	analyzeP.Add(flag("no_insights", "Skip the coaching-insights LLM call", "--no-insights"))
	analyzeP.Add(offFlag("include_ai_usage",
		"Skip the AI-adoption scan (commit/PR AI-tool markers)", "--no-ai-usage"))
	analyzeP.Add(offFlag("include_doc_quality",
		"Skip the documentation usefulness and clarity scan", "--no-doc-quality"))
	analyzeP.Add(multi("delivery", "+", "TRACKER", nil, choices("jira", "azdevops"),
		"Delivery (velocity/calibration) trackers to analyse. e.g. --delivery jira", "--delivery"))
	analyzeP.Add(multi("code", "+", "HOST", nil, choices("github", "azdo"),
		"Code hosts for the AI-usage scan. e.g. --code github azdo", "--code"))
	analyzeP.Add(multi("docs", "+", "PLATFORM", nil, choices("confluence", "notion"),
		"Doc platforms for the clarity/usefulness read. e.g. --docs confluence", "--docs"))
	analyzeP.Add(multi("members", "+", "NAME", nil, nil,
		"Selected members for delivery and code. Code analysis is empty without "+
			"an explicit member scope; it never falls back to whole-team activity.", "--members"))
	analyzeP.Add(strictOpt(strictWarnings))
	analyzeP.Add(formatOpt())

	return p
}
