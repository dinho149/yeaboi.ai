"""Argv behavioral vectors for the W8 CLI parse gate.

Each vector is one argv for ``yeaboi`` (never including the program name).
``argdump.py`` runs every vector through ``cli.build_parser()`` and records
the outcome — the parsed namespace, or the erroring parser's prog and error
message; the committed golden at ``tests/parity/goldens/cli/args.json``
freezes those outcomes, and both the Go tree test
(``go/cmd/yeaboi/args_golden_test.go``) and the ``yeaboi __dump-args``
subprocess arm replay them.

The traps, per the W8 spec: prefix abbreviation including the ``--export``
collision cli.py documents, ``nargs="?"`` consts (``--resume`` →
``"__pick__"``), choices (string and int), Python ``int()``'s whitespace
tolerance and its ``"5.0"`` rejection, required options and subcommands,
``store_false`` dest inversion, append accumulation, ``--`` separation, and
option-vs-subcommand token classification. Help and ``--version`` vectors
are deliberately absent: their OUTPUT is byte-pinned by the phase-4 help
goldens, and ``--version`` embeds the product version, which would rot the
golden every release.
"""

from __future__ import annotations

VECTORS: list[tuple[str, list[str]]] = [
    # -- defaults and the flat flags ------------------------------------
    ("empty", []),
    ("flags-quick-export-only", ["--quick", "--export-only"]),
    ("list-sessions-no-bell", ["--list-sessions", "--no-bell"]),
    ("learn-team-profile", ["--learn", "--team-profile"]),
    ("install-voice-dry-run", ["--install-voice", "--dry-run"]),
    ("mode-project-planning", ["--mode", "project-planning"]),
    ("ac-format-and-spike", ["--ac-format", "bullets", "--architecture-spike", "skip"]),
    ("standup-run-flags", ["--standup-run", "--standup-output", "slack", "--standup-interactive"]),
    ("headless", ["--non-interactive", "--description", "@spec.txt", "--output", "json"]),
    # -- nargs="?" consts -----------------------------------------------
    ("resume-bare", ["--resume"]),
    ("resume-latest", ["--resume", "latest"]),
    ("resume-then-flag", ["--resume", "--quick"]),
    ("retro-flag-bare", ["--retro"]),
    ("retro-flag-session", ["--retro", "abc123"]),
    ("export-questionnaire-bare", ["--export-questionnaire"]),
    ("export-questionnaire-path", ["--export-questionnaire", "out.md"]),
    # -- abbreviation ---------------------------------------------------
    ("abbrev-theme", ["--th", "light"]),
    ("abbrev-export-ambiguous", ["--export"]),
    ("abbrev-export-questionnaire", ["--export-q", "q.md"]),
    ("abbrev-in-subparser", ["retro", "--export-latest", "--form", "json"]),
    ("abbrev-export-after-subcommand", ["retro", "--export"]),
    # -- explicit =value ------------------------------------------------
    ("equals-value", ["--theme=light"]),
    ("equals-on-flag", ["--quick=1"]),
    # -- type conversion and choices ------------------------------------
    ("int-whitespace", ["--team-size", " 5 "]),
    ("int-underscore", ["--team-size", "1_0"]),
    ("int-rejects-float", ["--team-size", "5.0"]),
    ("int-choices-bad-value", ["--sprint-length", "5"]),
    ("int-choices-bad-type", ["--sprint-length", "x"]),
    ("choice-invalid", ["--theme", "blue"]),
    ("float-ok", ["ceremonies", "add", "daily", "--mode", "standup", "--monthly-cap", "2.5"]),
    ("float-invalid", ["ceremonies", "add", "daily", "--mode", "standup", "--monthly-cap", "abc"]),
    # -- append ---------------------------------------------------------
    ("append-prior-art", ["--prior-art", "github:acme/auth", "--prior-art", "github:acme/pay"]),
    ("append-allow-path", ["--allow-path", "/x"]),
    ("append-analyze-owners", ["analyze", "--github-owner", "acme", "--github-owner", "octo"]),
    # -- unknown options and unrecognized arguments ---------------------
    ("unknown-option", ["--bogus"]),
    ("unknown-option-in-subparser", ["retro", "--bogus"]),
    ("stray-positional-in-subparser", ["retro", "extra", "--format", "json"]),
    ("unknown-before-subcommand", ["--format", "json", "report"]),
    # -- subcommands ----------------------------------------------------
    ("bad-subcommand", ["badcmd"]),
    ("report-defaults", ["report"]),
    ("report-knobs", ["report", "--period", "quarter", "--strict", "--format", "json"]),
    ("report-bad-period", ["report", "--period=weekly"]),
    ("report-sub-theme", ["--quick", "report", "--theme", "aurora"]),
    ("report-multi-choice", ["report", "--code-sources", "github", "azdevops", "--documentation-sources", "notion"]),
    ("standup-channels", ["standup", "--channels", "slack", "email", "--days", "3"]),
    ("standup-channels-empty", ["standup", "--channels"]),
    ("standup-schedule", ["standup", "--schedule", "install"]),
    ("standup-no-transcript-review", ["standup", "--no-transcript-review"]),
    # -- positionals ----------------------------------------------------
    ("review-paths", ["standup-review", "a.vtt", "b.vtt", "--max-transcripts", "2"]),
    ("review-stdin-dash", ["standup-review", "-"]),
    ("review-double-dash", ["standup-review", "--", "--weird"]),
    ("review-transcript-option", ["standup-review", "--transcript", "x.vtt", "y.vtt"]),
    # -- required subcommands and options -------------------------------
    ("perf-missing-subcommand", ["perf"]),
    ("perf-roster", ["perf", "roster"]),
    ("perf-prep", ["perf", "prep", "Alice", "--jira-project", "K"]),
    ("perf-prep-missing-engineer", ["perf", "prep"]),
    ("perf-complete-missing-transcript", ["perf", "complete", "Bob"]),
    ("perf-note", ["perf", "note", "Carol", "--text", "hi"]),
    ("perf-review-months", ["perf", "review", "Dave", "--months", "12"]),
    ("ceremonies-missing-subcommand", ["ceremonies"]),
    (
        "ceremonies-add",
        [
            "ceremonies",
            "add",
            "daily",
            "--mode",
            "standup",
            "--arg",
            "k=v",
            "--arg",
            "x=y",
            "--stale-after",
            "0",
        ],
    ),
    ("ceremonies-add-missing-mode", ["ceremonies", "add", "daily"]),
    ("ceremonies-history-default-name", ["ceremonies", "history"]),
    ("ceremonies-run", ["ceremonies", "run", "daily", "--scheduled", "--dry-run"]),
    ("ceremonies-pause", ["ceremonies", "pause", "daily"]),
    # -- agents ---------------------------------------------------------
    ("agents-cost", ["agents", "cost", "--window-days", "7", "--source", "claude_code"]),
    ("agents-cost-bad-source", ["agents", "cost", "--source", "github"]),
    ("agents-standup-empty-trackers", ["agents", "standup", "--tracker-sources"]),
    ("agents-standup-local-off", ["agents", "standup", "--no-local-sessions"]),
    ("agents-bad-subcommand", ["agents", "bogus"]),
    # -- provenance / ship ----------------------------------------------
    ("provenance-trace", ["provenance", "trace", "ENT-1", "--depth", "3"]),
    ("ship-run", ["ship", "run", "US-001", "--dry-run", "--timeout-minutes", "5"]),
    ("ship-run-missing-story", ["ship", "run"]),
    # -- analyze --------------------------------------------------------
    (
        "analyze-features-members",
        [
            "analyze",
            "--features",
            "delivery",
            "code_health",
            "--members",
            "ada",
            "grace",
        ],
    ),
    ("analyze-store-false", ["analyze", "--no-ai-usage", "--no-doc-quality", "--samples"]),
]
