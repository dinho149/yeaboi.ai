"""CLI entry point for yeaboi.

Module-level imports here are deliberately limited to the stdlib and yeaboi's
lightweight config/paths modules: `yeaboi --version`/`--help` (and every
subcommand's fixed overhead) pay for everything imported at the top of this
file before argparse even runs. Anything that pulls rich, prompt_toolkit, or
the langchain/anthropic stack is imported at its call site instead — the same
convention as beta.py, enforced by tests/unit/test_cli_startup.py.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from yeaboi import __version__, fs_policy, paths
from yeaboi.beta import AGENTWATCH_BETA_NOTICE, BETA_TAG, PERFORMANCE_BETA_NOTICE
from yeaboi.config import (
    detect_proxy,
    disable_langsmith_tracing,
    is_langsmith_enabled,
    load_user_config,
)

if TYPE_CHECKING:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

# Default filename for exported questionnaire templates
DEFAULT_QUESTIONNAIRE_FILENAME = "scrum-questionnaire.md"

# Default DB path — inside the user config directory alongside history/config.
# Matches the path used by SessionStore in run_repl(). Single-sourced via
# paths.ROOT_DIR so the config-dir location (~/.yeaboi) lives in one place.
_SESSIONS_DB_DIR = paths.ROOT_DIR


def _summarise_scrum_md(console: Console, path: Path) -> None:
    """Print a brief pre-flight summary of the SCRUM.md file.
    Shows line count, URL count, and detected ## sections so users can
    confirm the right file was picked up before the analysis runs.
    """
    try:
        content = path.read_text()
    except OSError:
        console.print("[dim]  SCRUM.md detected — your project context will be included in the analysis.[/dim]")
        return

    lines = content.count("\n") + 1
    url_count = len(re.findall(r"https?://", content))
    sections = [ln.lstrip("#").strip() for ln in content.splitlines() if ln.startswith("## ")]

    stats = f"{lines} lines" + (f", {url_count} URL{'s' if url_count != 1 else ''}" if url_count else "")
    console.print(f"[dim]  SCRUM.md detected ({stats})[/dim]")
    if sections:
        console.print(f"[dim]    Sections: {' · '.join(sections)}[/dim]")


def _build_welcome_panel() -> Panel:
    """Build the branded welcome panel with version and quick-start hint.

    # See docs: "Architecture" — the CLI layer is the outermost layer,
    # responsible for user-facing chrome like the welcome screen.
    """
    from rich.panel import Panel
    from rich.text import Text

    body = Text.from_markup(
        f"[bold cyan]yeaboi.ai[/bold cyan]  [dim]v{__version__}[/dim]\n"
        "[white]Best friend to engineers and agents[/white]\n\n"
        "[dim]Describe your project to get started, or type [cyan]help[/cyan] for commands.[/dim]"
    )
    return Panel(body, border_style="cyan", padding=(1, 2))


# ---------------------------------------------------------------------------
# Session listing / picker helpers
# ---------------------------------------------------------------------------


def _build_sessions_table(sessions: list[dict], display_names: dict[str, str] | None = None) -> Table:
    """Build a Rich Table of saved sessions.

    Used by both --list-sessions and the interactive --resume picker.

    Args:
        sessions: List of session metadata dicts.
        display_names: Optional ``{session_id: unique_name}`` mapping from
            ``make_unique_display_names()``. When provided, the Project column
            shows the collision-free display name instead of the raw project_name.
    """
    from rich.table import Table

    table = Table(title="Saved sessions", show_lines=False, padding=(0, 1))
    table.add_column("#", style="bold", width=3)
    table.add_column("Project", style="cyan")
    table.add_column("Date", style="dim")
    table.add_column("Last Step", style="green")
    table.add_column("Session ID", style="dim")
    for i, meta in enumerate(sessions, 1):
        sid = meta.get("session_id", "")
        if display_names and sid in display_names:
            project = display_names[sid]
        else:
            project = meta.get("project_name") or "(unnamed)"
        date_str = meta.get("created_at", "")[:10]
        last_node = meta.get("last_node_completed") or "-"
        table.add_row(str(i), project, date_str, last_node, sid)
    return table


def _print_sessions_table(console: Console) -> None:
    """Print a table of all saved sessions and exit.

    Used by --list-sessions. Opens its own SessionStore so it works
    independently from the REPL.
    """
    from yeaboi.sessions import SessionStore, make_unique_display_names

    _SESSIONS_DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _SESSIONS_DB_DIR / "sessions.db"
    with SessionStore(db_path) as store:
        sessions = store.list_sessions()
    if not sessions:
        console.print("[hint]No saved sessions found.[/hint]")
        return
    unique_names = make_unique_display_names(sessions)
    console.print(_build_sessions_table(sessions, display_names=unique_names))


def _clear_sessions(console: Console) -> None:
    """Interactively delete saved sessions.

    Shows a numbered list plus an [A] All option. The user picks a session
    number to delete one, or 'a'/'all' to wipe everything.
    """
    from prompt_toolkit import PromptSession

    from yeaboi.sessions import SessionStore, make_unique_display_names

    _SESSIONS_DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _SESSIONS_DB_DIR / "sessions.db"
    with SessionStore(db_path) as store:
        sessions = store.list_sessions()
        if not sessions:
            console.print("[hint]No saved sessions found.[/hint]")
            return
        unique_names = make_unique_display_names(sessions)
        console.print(_build_sessions_table(sessions, display_names=unique_names))
        console.print()
        console.print(
            "[hint]Enter a session number to delete, [command]all[/command] to clear everything, "
            "or [command]q[/command] to cancel.[/hint]"
        )
        pick_session: PromptSession[str] = PromptSession()
        while True:
            try:
                raw = pick_session.prompt("Clear> ")
            except (KeyboardInterrupt, EOFError):
                return
            raw = raw.strip().lower()
            if raw in ("q", "quit", "cancel"):
                return
            if raw in ("a", "all"):
                count = store.delete_all_sessions()
                console.print(f"[success]Deleted all {count} session{'s' if count != 1 else ''}.[/success]")
                return
            try:
                idx = int(raw)
            except ValueError:
                console.print("[warning]Please enter a number, 'all', or 'q' to cancel.[/warning]")
                continue
            if 1 <= idx <= len(sessions):
                picked = sessions[idx - 1]
                sid = picked["session_id"]
                name = unique_names.get(sid, sid)
                store.delete_session(sid)
                console.print(f"[success]Deleted session: {name}[/success]")
                return
            console.print(f"[warning]Please pick a number between 1 and {len(sessions)}.[/warning]")


def _resolve_resume(console: Console, resume_arg: str) -> tuple[dict | None, str | None]:
    """Resolve --resume into a (graph_state, session_id) tuple.

    Args:
        console: Rich Console for output.
        resume_arg: The value of args.resume — "__pick__" for interactive,
            "latest" for most recent, or a specific session ID.

    Returns:
        (graph_state, session_id) on success, (None, None) on failure/cancel.

    # See docs: "Memory & State" — session persistence, --resume
    """
    from prompt_toolkit import PromptSession

    from yeaboi.sessions import SessionStore, make_display_name, make_unique_display_names

    _SESSIONS_DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _SESSIONS_DB_DIR / "sessions.db"
    with SessionStore(db_path) as store:
        if resume_arg == "latest":
            sid = store.get_latest_session_id()
            if sid is None:
                console.print("[warning]No saved sessions found.[/warning]")
                return None, None
            state = store.load_state(sid)
            if state is None:
                console.print(f"[warning]Session {sid} has no saved state or is corrupt.[/warning]")
                return None, None
            meta = store.get_session(sid)
            name = make_display_name(meta) if meta else sid
            console.print(f"[success]Loading session:[/success] {name}")
            return state, sid

        if resume_arg == "__pick__":
            sessions = store.list_sessions()
            if not sessions:
                console.print("[hint]No saved sessions found.[/hint]")
                return None, None
            unique_names = make_unique_display_names(sessions)
            console.print(_build_sessions_table(sessions, display_names=unique_names))
            console.print()
            pick_session: PromptSession[str] = PromptSession()
            while True:
                try:
                    raw = pick_session.prompt("Pick a session number (or 'q' to cancel): ")
                except (KeyboardInterrupt, EOFError):
                    return None, None
                raw = raw.strip().lower()
                if raw in ("q", "quit", "cancel"):
                    return None, None
                # Try numeric index first, then match by display name.
                try:
                    idx = int(raw)
                except ValueError:
                    # Match against display names (case-insensitive, partial match)
                    matches = [(i, s) for i, s in enumerate(sessions) if raw in unique_names[s["session_id"]].lower()]
                    if len(matches) == 1:
                        idx = matches[0][0] + 1  # convert to 1-based
                    elif len(matches) > 1:
                        console.print(f"[warning]'{raw}' matches multiple sessions. Be more specific.[/warning]")
                        continue
                    else:
                        console.print("[warning]No match. Enter a number or session name (or 'q' to cancel).[/warning]")
                        continue
                if 1 <= idx <= len(sessions):
                    picked = sessions[idx - 1]
                    sid = picked["session_id"]
                    state = store.load_state(sid)
                    if state is None:
                        console.print(f"[warning]Session {sid} has no saved state or is corrupt.[/warning]")
                        return None, None
                    name = make_display_name(picked)
                    console.print(f"[success]Loading session:[/success] {name}")
                    return state, sid
                console.print(f"[warning]Please pick a number between 1 and {len(sessions)}.[/warning]")

        # Specific session ID
        state = store.load_state(resume_arg)
        if state is None:
            console.print(f"[warning]Session '{resume_arg}' not found or has no saved state.[/warning]")
            return None, None
        meta = store.get_session(resume_arg)
        name = make_display_name(meta) if meta else resume_arg
        console.print(f"[success]Loading session:[/success] {name}")
        return state, resume_arg


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="yeaboi",
        description=(
            "yeaboi.ai — best friend to engineers and agents. Runs your team's scrum, and watches your AI agents work."
        ),
        epilog=(
            "examples:\n"
            "  yeaboi                        interactive mode (recommended)\n"
            "  yeaboi --quick                quick intake (2 questions only)\n"
            "  yeaboi --questionnaire q.md   import pre-filled questionnaire\n"
            "  yeaboi --export-only --quick  non-interactive, auto-accept all\n"
            "  yeaboi --resume               resume last session (interactive picker)\n"
            "  yeaboi --resume latest         resume most recent session\n"
            "  yeaboi --list-sessions         list all saved sessions\n"
            "  yeaboi --clear-sessions        delete saved sessions\n"
            '  yeaboi --non-interactive --description "Build a todo app"  headless mode\n'
            '  yeaboi --non-interactive --description "..." --output json  JSON to stdout'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        nargs="?",
        const="__pick__",
        default=None,
        help="Resume a previous session. Without an argument, shows an interactive session picker. "
        "Pass 'latest' to resume the most recent session, or a session ID to resume a specific one.",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        default=False,
        help="List all saved sessions and exit.",
    )
    parser.add_argument(
        "--clear-sessions",
        action="store_true",
        default=False,
        help="Interactively delete saved sessions (pick one or clear all) and exit.",
    )
    parser.add_argument(
        "--export-questionnaire",
        metavar="PATH",
        nargs="?",
        const=DEFAULT_QUESTIONNAIRE_FILENAME,
        default=None,
        help=f"Export a blank questionnaire template as Markdown (default: {DEFAULT_QUESTIONNAIRE_FILENAME}).",
    )
    parser.add_argument(
        "--questionnaire",
        metavar="PATH",
        default=None,
        help="Import a filled-in questionnaire Markdown file and jump to confirmation.",
    )

    # Intake mode flags — mutually exclusive.
    # Smart mode is the default when neither flag is given.
    # See docs: "Project Intake Questionnaire" — smart intake
    # The legacy --full-intake (30-question "standard" mode) has been removed —
    # smart intake is the single interactive path. --quick remains for power users.
    intake_group = parser.add_mutually_exclusive_group()
    intake_group.add_argument(
        "--quick",
        action="store_true",
        default=False,
        help="Quick intake — only ask team size and tech stack, auto-fill everything else.",
    )

    parser.add_argument(
        "--export-only",
        action="store_true",
        default=False,
        help="Auto-accept all review checkpoints and exit after the full plan is generated. "
        "Combine with --quick or --questionnaire for fully non-interactive runs.",
    )

    parser.add_argument(
        "--prior-art",
        action="append",
        default=None,
        metavar="REPO_KEY",
        help="Existing repository to build this plan on, as a key like 'github:acme/auth' "
        "(repeatable). Requires --non-interactive; an interactive run asks about prior art "
        "in the intake instead, so the flag is ignored there.",
    )

    parser.add_argument(
        "--ac-format",
        choices=["gwt", "bullets"],
        default=None,
        help="Acceptance-criteria style for generated stories: 'gwt' (Given/When/Then) or "
        "'bullets' (clear testable statements). Default: follow the learned team profile "
        "(or YEABOI_AC_FORMAT).",
    )

    parser.add_argument(
        "--architecture-spike",
        choices=["auto", "include", "skip"],
        default=None,
        help="Whether to add an architecture-validation spike when the analyzer's decision is "
        "open (2+ options): 'include'/'skip' force it, 'auto' adds it unless the analyzer's "
        "confidence is high. Default: ask interactively (auto in non-interactive runs).",
    )

    parser.add_argument(
        "--no-bell",
        action="store_true",
        default=False,
        help="Disable terminal bell after pipeline steps.",
    )

    parser.add_argument(
        "--theme",
        choices=["dark", "light"],
        default="dark",
        help="Terminal colour theme (default: dark). Use 'light' for white/cream backgrounds.",
    )

    parser.add_argument(
        "--mode",
        choices=["project-planning"],  # extend this list as new modes ship
        default=None,
        help="Skip the startup menu and launch directly into a specific mode.",
    )

    parser.add_argument(
        "--setup",
        action="store_true",
        default=False,
        help="Re-run the first-time setup wizard to update credentials.",
    )

    parser.add_argument(
        "--allow-path",
        metavar="PATH",
        action="append",
        default=[],
        help=(
            "Allow filesystem access to PATH for this run only (repeatable). "
            "yeaboi is sandboxed to ~/.yeaboi; persistent allowances live in "
            "YEABOI_ALLOWED_PATHS or Settings → Allowed Paths."
        ),
    )

    parser.add_argument(
        "--list-audio-devices",
        action="store_true",
        default=False,
        help="List the microphones yeaboi can record from, then exit. "
        "Set the one you want with VOICE_DEVICE (or Settings → Voice Input).",
    )

    parser.add_argument(
        "--install-voice",
        action="store_true",
        default=False,
        help="Install the dictation packages and speech model into this environment, then exit. "
        "The same thing double-tapping Space offers inside the app — this is the non-TUI path "
        "for CI, dev containers and terminals the full-screen UI cannot drive.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run the TUI with mock data and fake delays — no LLM calls. For UI development.",
    )

    # ── Non-interactive / headless mode ──────────────────────────────────
    # Runs the full pipeline without user interaction. Requires --description.
    # Combine with --output to control output format (default: markdown).
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        default=False,
        help="Run the full pipeline headlessly (no user interaction). Requires --description.",
    )
    parser.add_argument(
        "--output",
        choices=["markdown", "json", "html", "prd"],
        default=None,
        help="Output format for the generated plan ('prd' writes a Product Requirements "
        "Document; one extra LLM call). Only valid with --non-interactive or --export-only.",
    )
    parser.add_argument(
        "--description",
        metavar="TEXT",
        default=None,
        help="Project description for headless mode. Use @file.txt to read from a file.",
    )
    parser.add_argument(
        "--team-size",
        metavar="N",
        type=int,
        default=None,
        help="Team size (maps to intake Q6). Only used with --non-interactive.",
    )
    parser.add_argument(
        "--sprint-length",
        metavar="WEEKS",
        type=int,
        choices=[1, 2, 3, 4],
        default=None,
        help="Sprint length in weeks (maps to intake Q8). Only used with --non-interactive.",
    )

    # ── Daily Standup flags ───────────────────────────────────────────────
    # --standup-run is what the OS scheduler (launchd/cron) invokes: it runs a
    # standup headlessly and delivers it. See docs: "Daily Standup".
    parser.add_argument(
        "--standup-run",
        action="store_true",
        default=False,
        help="Run a daily standup headlessly and deliver it (used by the OS scheduler).",
    )
    parser.add_argument(
        "--standup-session",
        metavar="SESSION_ID",
        default=None,
        help="Session to run the standup for. Defaults to the most recent session. Only used with --standup-run.",
    )
    parser.add_argument(
        "--standup-output",
        choices=["terminal", "desktop", "slack", "email", "all"],
        default=None,
        help="Override delivery channel(s) for --standup-run (default: the session's saved channels).",
    )
    parser.add_argument(
        "--standup-interactive",
        action="store_true",
        default=False,
        help="With --standup-run: prompt for your update + confirm (timed) before generating. "
        "What the scheduler opens in a terminal; falls back to headless when no TTY.",
    )
    # The second scheduled job: fires AFTER the standup, posts one desktop
    # notification if any standup went unchecked, and exits. No terminal, no LLM.
    parser.add_argument(
        "--standup-remind-transcript",
        action="store_true",
        default=False,
        help="Post a desktop reminder if standups went unchecked against their meetings (used by the OS scheduler).",
    )

    # ── Team learning flags ───────────────────────────────────────────────
    parser.add_argument(
        "--learn",
        action="store_true",
        default=False,
        help="Analyse historical Jira/AzDO sprint data and store a team calibration profile. "
        "Subsequent planning sessions use this profile to calibrate estimates.",
    )
    parser.add_argument(
        "--team-profile",
        action="store_true",
        default=False,
        help="Display the current stored team calibration profile and exit.",
    )
    parser.add_argument(
        "--retro",
        metavar="SESSION_ID",
        nargs="?",
        const="latest",
        default=None,
        help="Compare a past session's plan to actual Jira/AzDO outcomes. "
        "Pass a session ID or omit for the most recent session.",
    )

    # ── Subcommands — headless mode runners ───────────────────────────────
    # Additive: every flat flag above keeps working (the subparsers action is
    # optional, so bare `yeaboi` and `yeaboi --<flag>` parse unchanged).
    # See CLAUDE.md "REQUIRED: Surface Parity" — each mode needs a CLI path;
    # these run the same engines the TUI and the MCP server use.
    subparsers = parser.add_subparsers(
        dest="command", metavar="{report,standup,standup-review,perf,retro,poker,analyze,agents}"
    )

    report_p = subparsers.add_parser("report", help="Generate a stakeholder delivery report (Reporting mode)")
    report_p.add_argument(
        "--period", choices=["last_week", "last_sprint", "last_month", "quarter", "window"], default="last_sprint"
    )
    report_p.add_argument("--session", default="", metavar="ID", help="Session to use (default: most recent)")
    report_p.add_argument(
        "--window-start", default="", metavar="YYYY-MM-DD", help="Explicit window start (quarter/window periods)"
    )
    report_p.add_argument("--window-end", default="", metavar="YYYY-MM-DD", help="Explicit window end")
    report_p.add_argument(
        "--sprint-names", default="", metavar="A,B", help="Comma-separated sprint names framing a quarter report"
    )
    report_p.add_argument("--label", default="", metavar="TEXT", help='Period label override (e.g. "Q3 2026")')
    report_p.add_argument("--jira-project", default="", metavar="KEY", help="Jira project key override")
    report_p.add_argument("--azdo-project", default="", metavar="NAME", help="Azure DevOps project override")
    report_p.add_argument(
        "--source",
        choices=["jira", "azdevops", "both"],
        default="",
        help="Ticketing source(s) for delivered work (default: every configured tracker)",
    )
    report_p.add_argument(
        "--code-sources",
        nargs="+",
        choices=["github", "azdevops"],
        default=None,
        metavar="SOURCE",
        help="Code hosts to pull supporting PR/commit context from (default: all configured)",
    )
    report_p.add_argument(
        "--documentation-sources",
        nargs="+",
        choices=["confluence", "notion"],
        default=None,
        metavar="SOURCE",
        help="Doc platforms to pull supporting doc-update context from (default: all configured)",
    )
    report_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings/empty report)")
    report_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    report_p.add_argument(
        "--theme",
        default="midnight",
        metavar="NAME",
        help="Export palette: midnight/aurora/sunset/mono or a custom name from reporting_themes.json",
    )

    standup_p = subparsers.add_parser("standup", help="Run a Daily Standup (alias of --standup-run, more knobs)")
    standup_p.add_argument("--session", default="", metavar="ID", help="Session to use (default: most recent)")
    standup_p.add_argument("--deliver", action="store_true", help="Send to the configured channels (default: print)")
    standup_p.add_argument(
        "--channels", nargs="+", choices=["terminal", "desktop", "slack", "email"], help="Override delivery channels"
    )
    standup_p.add_argument("--days", type=int, default=0, help="Activity look-back window override")
    standup_p.add_argument(
        "--tracker-sources",
        nargs="+",
        choices=["jira", "azure_devops"],
        help="Override saved standup tracker sources",
    )
    standup_p.add_argument(
        "--team-members",
        nargs="+",
        metavar="NAME",
        help="Override the saved authoritative team roster for this run",
    )
    standup_p.add_argument(
        "--code-sources",
        nargs="+",
        choices=["github", "azure_devops"],
        help="Override saved code providers for this run",
    )
    standup_p.add_argument(
        "--github-owners",
        nargs="+",
        metavar="OWNER",
        help="Override saved GitHub owner/organisation scope (covers every active repo inside each)",
    )
    standup_p.add_argument(
        "--github-repositories",
        nargs="+",
        metavar="OWNER/REPO",
        help="Override saved GitHub repository scope (exact repos, unioned with --github-owners)",
    )
    standup_p.add_argument(
        "--github-excluded-repositories",
        nargs="+",
        metavar="OWNER/REPO",
        help="Override saved GitHub repos to drop from an included owner's expansion",
    )
    standup_p.add_argument(
        "--azdo-projects",
        nargs="+",
        metavar="PROJECT",
        help="Override saved Azure DevOps project scope (all repositories in each project)",
    )
    standup_p.add_argument(
        "--azdo-repositories",
        nargs="+",
        metavar="PROJECT/REPO",
        help="Legacy Azure Repos override",
    )
    standup_p.add_argument(
        "--documentation-sources",
        nargs="+",
        choices=["confluence", "notion"],
        help="Override saved documentation providers for this run",
    )
    standup_p.add_argument(
        "--list-members",
        action="store_true",
        help="List discovered members for the selected/saved tracker sources and exit",
    )
    standup_p.add_argument(
        "--schedule",
        choices=["install", "remove", "status"],
        help="Manage the OS schedule (launchd/cron) that runs the standup daily, instead of running one now",
    )
    standup_p.add_argument(
        "--no-transcript-review",
        dest="review_transcripts",
        action="store_false",
        default=True,
        help="Skip the pre-standup review of any unreviewed meeting transcripts",
    )
    standup_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    standup_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    # ── standup-review ────────────────────────────────────────────────────
    # A sibling subcommand rather than `standup review`: `standup` is a leaf
    # parser and converting it into a parser-of-parsers would break the
    # existing `yeaboi standup --deliver` form.
    review_p = subparsers.add_parser(
        "standup-review",
        help="Review standup meeting transcripts to find what standup missed, and why",
    )
    review_p.add_argument("--session", default="", metavar="ID", help="Session to use (default: most recent)")
    # A bare `yeaboi standup-review meeting.vtt` is the shape people reach for
    # first, and it is what a dragged file produces.
    review_p.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Transcript files to review (same as --transcript; '-' reads the transcript from stdin)",
    )
    review_p.add_argument(
        "--transcript",
        dest="transcript_paths",
        nargs="+",
        metavar="PATH",
        help="Review specific transcript files instead of sweeping the transcript folders",
    )
    review_p.add_argument(
        "--transcript-text",
        default="",
        metavar="TEXT",
        help="Review transcript text directly; it is saved to ~/.yeaboi/transcripts first",
    )
    review_p.add_argument(
        "--transcript-dir",
        default="",
        metavar="DIR",
        help="An extra transcript folder for this run (~/.yeaboi/transcripts is always swept)",
    )
    review_p.add_argument(
        "--date",
        dest="standup_date",
        default="",
        metavar="YYYY-MM-DD",
        help="Attribute transcripts to this standup date when their own date can't be inferred",
    )
    review_p.add_argument(
        "--max-transcripts",
        type=int,
        default=5,
        help="Cap on distinct standup dates reviewed (one AI call each)",
    )
    review_p.add_argument(
        "--include-reviewed",
        action="store_true",
        help="Re-review transcripts that have already been processed",
    )
    review_p.add_argument(
        "--file-issues",
        action="store_true",
        help="File the drafted gaps as GitHub issues (writes to a PUBLIC repo; off by default)",
    )
    review_p.add_argument(
        "--list-gaps",
        action="store_true",
        help="List past reviews and the gap→issue ledger instead of running a review",
    )
    review_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    review_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    perf_p = subparsers.add_parser(
        "perf",
        help=f"Performance mode {BETA_TAG}: 1:1 prep/completion, reviews, notes",
        description=PERFORMANCE_BETA_NOTICE,
    )
    perf_sub = perf_p.add_subparsers(dest="perf_command", metavar="{roster,prep,complete,review,note}", required=True)
    # Every child carries the same description: `yeaboi perf prep --help` is a
    # perfectly normal place to arrive without ever seeing the parent's help.
    perf_sub.add_parser(
        "roster",
        help="List the engineer roster from recent tracker assignees",
        description=PERFORMANCE_BETA_NOTICE,
    )
    prep_p = perf_sub.add_parser("prep", help="Prepare a 1:1 for an engineer", description=PERFORMANCE_BETA_NOTICE)
    prep_p.add_argument("engineer", help="Engineer name (see `yeaboi perf roster`)")
    prep_p.add_argument("--session", default="", metavar="ID", help="Session for team context (default: most recent)")
    prep_p.add_argument("--jira-project", default="", metavar="KEY", help="Jira project key override")
    prep_p.add_argument("--azdo-project", default="", metavar="NAME", help="Azure DevOps project override")
    prep_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    complete_p = perf_sub.add_parser(
        "complete", help="Complete a held 1:1 from its transcript", description=PERFORMANCE_BETA_NOTICE
    )
    complete_p.add_argument("engineer", help="Engineer name")
    complete_p.add_argument(
        "--transcript", required=True, metavar="TEXT", help="Transcript text; @file.txt reads from file"
    )
    complete_p.add_argument("--deliver", action="store_true", help="Email the summary via the configured SMTP")
    complete_p.add_argument("--session", default="", metavar="ID", help="Session for team context")
    complete_p.add_argument(
        "--images", nargs="+", default=[], metavar="PATH", help="Whiteboard/notes photos to attach to the summary"
    )
    complete_p.add_argument(
        "--recipients", nargs="+", default=None, metavar="EMAIL", help="Email recipients override (with --deliver)"
    )
    complete_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    review_p = perf_sub.add_parser(
        "review", help="Draft a periodic performance review", description=PERFORMANCE_BETA_NOTICE
    )
    review_p.add_argument("engineer", help="Engineer name")
    review_p.add_argument("--months", type=int, default=6, help="Review period in months (default 6)")
    review_p.add_argument("--session", default="", metavar="ID", help="Session for team context")
    review_p.add_argument("--jira-project", default="", metavar="KEY", help="Jira project key override")
    review_p.add_argument("--azdo-project", default="", metavar="NAME", help="Azure DevOps project override")
    review_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    note_p = perf_sub.add_parser("note", help="Record a note about an engineer", description=PERFORMANCE_BETA_NOTICE)
    note_p.add_argument("engineer", help="Engineer name")
    note_p.add_argument("--text", required=True, help="The note text")

    retro_p = subparsers.add_parser("retro", help="Read past retrospectives (the live board runs in the TUI)")
    retro_p.add_argument("--session", default="", metavar="ID", help="Session to read (default: most recent)")
    retro_p.add_argument("--limit", type=int, default=10, help="Number of past retros to show (default 10)")
    # dest stays "export"; the flag is spelled out because a bare "--export"
    # abbreviation-collides with the top-level --export-questionnaire/--export-only
    # under argparse's prefix matching (Python <3.14).
    retro_p.add_argument(
        "--export-latest",
        dest="export",
        action="store_true",
        help="Also export the latest retro to Markdown + HTML",
    )
    retro_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    poker_p = subparsers.add_parser("poker", help="Read past poker sessions (the live voting board runs in the TUI)")
    poker_p.add_argument("--session", default="", metavar="ID", help="Only show sessions recorded under this id")
    poker_p.add_argument("--limit", type=int, default=10, help="Number of past sessions to show (default 10)")
    # dest stays "export"; spelled out for the same argparse prefix-collision
    # reason as the retro subcommand above.
    poker_p.add_argument(
        "--export-latest",
        dest="export",
        action="store_true",
        help="Also export the latest poker session to Markdown + HTML",
    )
    poker_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    agents_p = subparsers.add_parser(
        "agents",
        help=f"Agents mode {BETA_TAG}: monitor your AI coding agents (cost, recoverable spend, activity, security)",
        description=AGENTWATCH_BETA_NOTICE,
    )
    agents_sub = agents_p.add_subparsers(
        dest="agents_command", metavar="{cost,advisor,standup,security}", required=True
    )
    # Every child carries the same description — `yeaboi agents cost --help` is
    # a perfectly normal place to arrive without ever seeing the parent's help.
    cost_p = agents_sub.add_parser(
        "cost",
        help="What your agents cost: per-model/project/source breakdowns + daily trend",
        description=AGENTWATCH_BETA_NOTICE,
    )
    cost_p.add_argument("--window-days", type=int, default=30, metavar="N", help="Days to look back (default 30)")
    cost_p.add_argument("--project", default="", metavar="NAME", help="Filter by project directory name (substring)")
    cost_p.add_argument("--source", default="", choices=["", "claude_code"], help="Filter by telemetry source")
    cost_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    cost_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    advisor_p = agents_sub.add_parser(
        "advisor",
        help="How much of your agent spend is recoverable: Read waste, cache health, prompt-prefix churn",
        description=AGENTWATCH_BETA_NOTICE,
    )
    advisor_p.add_argument("--window-days", type=int, default=30, metavar="N", help="Days to look back (default 30)")
    advisor_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    advisor_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    astandup_p = agents_sub.add_parser(
        "standup",
        help="Daily digest of what your agents did (sessions + agent-authored commits/PRs)",
        description=AGENTWATCH_BETA_NOTICE,
    )
    astandup_p.add_argument(
        "--days",
        type=int,
        default=None,
        metavar="N",
        help="Days to look back (default: since the previous working day)",
    )
    astandup_p.add_argument(
        "--tracker-sources",
        nargs="*",
        default=None,
        choices=["github", "azdo"],
        metavar="SRC",
        help="Trackers to scan for agent-authored work (default both; pass none for local-only)",
    )
    astandup_p.add_argument(
        "--github-owners",
        nargs="+",
        default=None,
        metavar="OWNER",
        help="GitHub owners/orgs to scan (default configured)",
    )
    astandup_p.add_argument(
        "--azdo-projects",
        nargs="+",
        default=None,
        metavar="NAME",
        help="Azure DevOps projects to scan (default configured)",
    )
    astandup_p.add_argument(
        "--no-local-sessions",
        dest="include_local_sessions",
        action="store_false",
        help="Skip local session logs for a tracker-only digest (use off this machine)",
    )
    astandup_p.add_argument("--deliver", action="store_true", help="Post the digest to the configured Slack webhook")
    astandup_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    astandup_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    asec_p = agents_sub.add_parser(
        "security",
        help="Audit your agent setup: permissions, MCP servers, secrets exposure, risky commands",
        description=AGENTWATCH_BETA_NOTICE,
    )
    asec_p.add_argument("--deep", action="store_true", help="Re-scan every transcript, not just new/changed ones")
    asec_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    asec_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")

    provenance_desc = (
        "Every deterministic signal yeaboi surfaces (practice nudges, blocker flags, confidence "
        "adjustments, conflict cards, performance preps and reviews) is recorded in a tamper-evident "
        "hash chain with its evidence. This command verifies and reads that chain — deterministic, "
        "local, no LLM involved."
    )
    provenance_p = subparsers.add_parser(
        "provenance",
        help="Audit the tamper-evident decision chain behind yeaboi's signals",
        description=provenance_desc,
    )
    provenance_sub = provenance_p.add_subparsers(dest="provenance_command", metavar="{audit,trace}", required=True)
    # Every child carries the same description — `yeaboi provenance audit --help`
    # is a perfectly normal place to arrive without ever seeing the parent's help.
    paudit_p = provenance_sub.add_parser(
        "audit",
        help="Verify every chain link and summarise the recorded decisions",
        description=provenance_desc,
    )
    paudit_p.add_argument("--window-days", type=int, default=30, metavar="N", help="Days to look back (default 30)")
    paudit_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    paudit_p.add_argument(
        "--strict", action="store_true", help="Exit 3 on a broken chain or an empty one (warnings present)"
    )
    ptrace_p = provenance_sub.add_parser(
        "trace",
        help='The "why" trail behind one recorded decision, evidence included',
        description=provenance_desc,
    )
    ptrace_p.add_argument("entity_id", metavar="ENTITY", help="Entity id, as listed by `yeaboi provenance audit`")
    ptrace_p.add_argument("--depth", type=int, default=2, metavar="N", help="Evidence hops to follow (default 2)")
    ptrace_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    ship_desc = (
        "Hand a story from your saved sprint plan to a supervised coding agent (Claude Code headless): "
        "isolated worktree and branch, deterministic validation, a human approval gate at the terminal, "
        "and a PR only after approval. A user-global launch budget caps runs."
    )
    ship_p = subparsers.add_parser(
        "ship",
        help="Implement a story from your plan via a supervised coding agent",
        description=ship_desc,
    )
    ship_sub = ship_p.add_subparsers(dest="ship_command", metavar="{run,status,history}", required=True)
    srun_p = ship_sub.add_parser("run", help="Run one story through the pipeline", description=ship_desc)
    srun_p.add_argument("story_id", metavar="STORY", help="Story id from the plan (e.g. US-001)")
    srun_p.add_argument("--repo", default=".", metavar="PATH", help="Target git repository (default: current dir)")
    srun_p.add_argument("--session", default="", metavar="ID", help="Planning session id (default: latest)")
    srun_p.add_argument(
        "--check", default="", metavar="CMD", help="Validation command run in the worktree (e.g. 'make test')"
    )
    srun_p.add_argument("--timeout-minutes", type=int, default=30, metavar="N", help="Agent run timeout (default 30)")
    srun_p.add_argument("--dry-run", action="store_true", help="Canned run — no agent, no git, no network")
    srun_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    srun_p.add_argument("--strict", action="store_true", help="Exit 3 when the run did not end approved")
    sstatus_p = ship_sub.add_parser("status", help="The latest run and the launch budget", description=ship_desc)
    sstatus_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    shistory_p = ship_sub.add_parser("history", help="Recent runs, newest first", description=ship_desc)
    shistory_p.add_argument("--limit", type=int, default=10, metavar="N", help="Runs to show (default 10)")
    shistory_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    analyze_p = subparsers.add_parser("analyze", help="Analyse team board history into a calibration profile")
    analyze_p.add_argument(
        "--source",
        choices=["jira", "azdevops", "both"],
        default="",
        help="Tracker: jira, azdevops, or both (default: auto-detect a single tracker)",
    )
    analyze_p.add_argument("--project", default="", metavar="KEY", help="Project key (default: configured)")
    analyze_p.add_argument("--sprints", type=int, default=8, help="Closed sprints to analyse (default 8)")
    analyze_p.add_argument(
        "--depth",
        choices=["quick", "deep"],
        default="deep",
        help="Analysis depth: deep provides exhaustive AI enrichment; quick is metrics-only (default deep)",
    )
    analyze_p.add_argument(
        "--window-days",
        type=int,
        default=120,
        help="Changed-content window shared by Code and Docs (default 120)",
    )
    analyze_p.add_argument(
        "--analysis-model",
        default=None,
        metavar="MODEL",
        help="Per-run model for structured Analysis tasks (final synthesis still uses the primary model)",
    )
    analyze_p.add_argument(
        "--features",
        nargs="+",
        choices=["delivery", "ai_footprint", "code_health", "documentation"],
        default=None,
        metavar="FEATURE",
        help="Analysis areas to run (default: all supported by the selected integrations)",
    )
    analyze_p.add_argument("--github-owner", action="append", default=None, metavar="OWNER")
    analyze_p.add_argument("--azdo-code-project", action="append", default=None, metavar="PROJECT")
    analyze_p.add_argument("--confluence-space", action="append", default=None, metavar="SPACE")
    analyze_p.add_argument("--notion-root", action="append", default=None, metavar="PAGE_ID")
    analyze_p.add_argument(
        "--samples",
        action="store_true",
        help="Also generate sample tickets (requires --depth deep; extra LLM calls)",
    )
    analyze_p.add_argument("--no-insights", action="store_true", help="Skip the coaching-insights LLM call")
    analyze_p.add_argument(
        "--no-ai-usage",
        dest="include_ai_usage",
        action="store_false",
        help="Skip the AI-adoption scan (commit/PR AI-tool markers)",
    )
    analyze_p.add_argument(
        "--no-doc-quality",
        dest="include_doc_quality",
        action="store_false",
        help="Skip the documentation usefulness and clarity scan",
    )
    # Each component runs over its OWN sub-sources (not the tracker): delivery ←
    # jira/azdevops boards, code ← github/azdo repos, docs ← confluence/notion.
    analyze_p.add_argument(
        "--delivery",
        nargs="+",
        choices=["jira", "azdevops"],
        default=None,
        metavar="TRACKER",
        help="Delivery (velocity/calibration) trackers to analyse. e.g. --delivery jira",
    )
    analyze_p.add_argument(
        "--code",
        nargs="+",
        choices=["github", "azdo"],
        default=None,
        metavar="HOST",
        help="Code hosts for the AI-usage scan. e.g. --code github azdo",
    )
    analyze_p.add_argument(
        "--docs",
        nargs="+",
        choices=["confluence", "notion"],
        default=None,
        metavar="PLATFORM",
        help="Doc platforms for the clarity/usefulness read. e.g. --docs confluence",
    )
    analyze_p.add_argument(
        "--members",
        nargs="+",
        default=None,
        metavar="NAME",
        help=(
            "Selected members for delivery and code. Code analysis is empty without "
            "an explicit member scope; it never falls back to whole-team activity."
        ),
    )
    analyze_p.add_argument("--strict", action="store_true", help="Exit 3 on a degraded run (warnings present)")
    analyze_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    return parser


def _run_headless(args: argparse.Namespace) -> None:
    """Run the full pipeline headlessly — no TUI, no interactive input.

    Pre-populates a QuestionnaireState from CLI args (--description,
    --team-size, --sprint-length), then delegates to run_repl() with
    export_only=True and non_interactive=True.

    When --output json, Rich console output goes to stderr so only
    JSON is written to stdout.

    # See docs: "Architecture" — headless mode for CI/CD pipelines
    """
    from rich.console import Console

    from yeaboi.formatters import build_theme
    from yeaboi.questionnaire_io import build_questionnaire_from_answers
    from yeaboi.repl import run_repl

    output_format = args.output or "markdown"

    # When JSON output, redirect console to stderr so stdout is clean JSON
    if output_format == "json":
        console = Console(theme=build_theme(args.theme), file=sys.stderr)
    else:
        console = Console(theme=build_theme(args.theme))

    # Pre-populate questionnaire from CLI args
    answers: dict[int, str] = {}
    # Q1 gets the project description
    answers[1] = args.description
    if args.team_size is not None:
        answers[6] = str(args.team_size)
    if args.sprint_length is not None:
        answers[8] = str(args.sprint_length)

    # Load SCRUM.md from working directory if present — fills gaps the CLI
    # args didn't cover (e.g., tech stack, integrations, constraints).
    # Uses deterministic keyword extraction only (no LLM call) to stay fast.
    # CLI args always take priority over SCRUM.md.
    try:
        from yeaboi.agent.nodes import _keyword_extract_fallback, _load_user_context

        scrum_md_content, _ = _load_user_context()
        if scrum_md_content:
            scrum_extracted: dict[int, str] = {}
            _keyword_extract_fallback(scrum_md_content, scrum_extracted)
            # Merge: CLI args win over SCRUM.md
            for q_num, answer in scrum_extracted.items():
                if q_num not in answers:
                    answers[q_num] = answer
    except Exception:
        pass  # best-effort — never block headless mode

    questionnaire = build_questionnaire_from_answers(answers)

    run_repl(
        console=console,
        questionnaire=questionnaire,
        intake_mode="quick",
        export_only=True,
        bell=False,
        theme=args.theme,
        non_interactive=True,
        output_format=output_format,
        prior_art=args.prior_art,
    )


def _run_standup(args: argparse.Namespace) -> int:
    """Run a Daily Standup headlessly and deliver it. Returns a process exit code.

    This is what the OS scheduler (launchd plist / crontab entry) invokes at the
    configured time — even when the interactive app is closed. It resolves the
    target session (``--standup-session`` or the most recent), runs the engine,
    and delivers to the configured (or ``--standup-output``-overridden) channels.

    Exit codes: 0 = delivered, 2 = no session found, 1 = unexpected error.

    # See docs: "Daily Standup" — scheduling, headless run
    """
    from yeaboi.logging_setup import attach_mode_handler, configure_logging
    from yeaboi.paths import get_db_path
    from yeaboi.sessions import SessionStore

    # Route standup records to logs/standup/standup.log (rotating) alongside the
    # main TUI log, so scheduled runs are auditable. Level comes from LOG_LEVEL
    # (default WARNING) — set LOG_LEVEL=INFO in ~/.yeaboi/.env for run-by-run
    # audit detail. The process exits after the run, so no detach is needed.
    configure_logging()
    attach_mode_handler("standup")

    db_path = get_db_path()
    session_id = args.standup_session
    if not session_id or session_id == "latest":
        with SessionStore(db_path) as store:
            session_id = store.get_latest_session_id()
    if not session_id:
        print("Error: no session found to run a standup for.", file=sys.stderr)
        return 2

    # Resolve channel override: "all" expands to every channel.
    channels = None
    if args.standup_output:
        if args.standup_output == "all":
            from yeaboi.standup.delivery import ALL_CHANNELS

            channels = list(ALL_CHANNELS)
        else:
            channels = [args.standup_output]

    # Interactive scheduled run: prompt for the user's update + confirm (timed),
    # then generate + deliver. Falls back to headless when no TTY is attached.
    if getattr(args, "standup_interactive", False):
        from yeaboi.standup.interactive import run_interactive_standup

        return run_interactive_standup(session_id, channels=channels)

    try:
        from yeaboi.standup.engine import run_standup

        report = run_standup(session_id, channels=channels, deliver=True)
        warn = f" ({len(report.warnings)} notice(s))" if report.warnings else ""
        print(
            f"Standup delivered for session '{session_id}' (day {report.sprint_day}/{report.sprint_total_days}){warn}."
        )
        return 0
    except Exception as e:
        logging.getLogger(__name__).error("Standup run failed: %s", e, exc_info=True)
        print(f"Error: standup run failed: {e}", file=sys.stderr)
        return 1


def _run_transcript_reminder(args: argparse.Namespace) -> int:
    """Post one desktop reminder if standups went unchecked. Returns an exit code.

    The second job the OS scheduler installs, firing shortly AFTER the standup —
    the moment the recording has just landed and the meeting is still in mind.

    Deliberately tiny: no LLM, no collectors, no terminal window. It asks the
    same deterministic question the hub card asks, and if the answer is "nothing
    to say" it exits silently. A scheduled job that speaks only when it has
    something to report is one you leave installed.

    # See docs: "Daily Standup" — scheduling
    """
    from yeaboi.logging_setup import attach_mode_handler, configure_logging
    from yeaboi.paths import get_db_path
    from yeaboi.sessions import SessionStore

    configure_logging()
    attach_mode_handler("standup")

    session_id = args.standup_session
    if not session_id or session_id == "latest":
        with SessionStore(get_db_path()) as store:
            session_id = store.get_latest_session_id()
    if not session_id:
        print("Error: no session found to check transcripts for.", file=sys.stderr)
        return 2

    try:
        from yeaboi.standup.delivery import notify_desktop
        from yeaboi.standup.engine import transcript_nudge

        nudge = transcript_nudge(session_id)
        if not nudge:
            logging.getLogger(__name__).info("transcript reminder: nothing to say for %s", session_id)
            return 0
        notify_desktop("Standup transcript", nudge.message)
        print(nudge.message)
        return 0
    except Exception as e:
        logging.getLogger(__name__).error("Transcript reminder failed: %s", e, exc_info=True)
        print(f"Error: transcript reminder failed: {e}", file=sys.stderr)
        return 1


def _install_voice() -> int:
    """Install dictation from the command line, printing plain progress.

    Deliberately not a Rich ``Live``: this runs where the TUI cannot, so the
    output has to survive a pipe, a CI log and a terminal with no cursor
    control. Returns a process exit code.

    There is no MCP tool for this on purpose — installing arbitrary packages
    into the host environment on an LLM's say-so is not a capability worth
    shipping. Both entry points are human-initiated.
    """
    from yeaboi import voice, voice_install

    log = logging.getLogger(__name__)
    log.info("Installing voice input from the CLI")

    if voice.is_voice_available()[0]:
        print("Dictation is already installed.")
    else:
        # ignore_verdict: typing this command *is* the retry. A stored failure
        # from a month ago (a wheel that had not landed yet, a mirror that had
        # not synced) must not be the reason the explicit escape hatch refuses.
        plan = voice_install.install_plan(ignore_verdict=True)
        if plan.blocked:
            print(f"Cannot install dictation here — {plan.blocked}")
            return 1
        print(f"Installing dictation: {plan.display_command}")
        echoed: list[str] = []
        ok, message = voice_install.install_packages(lambda phrase: _echo_once(echoed, phrase))
        if not ok:
            print(message)
            return 1
        from yeaboi.config import mark_voice_extra_installed

        mark_voice_extra_installed()
        print("Packages installed.")
        if plan.follow_up:
            print(f"To keep dictation across upgrades: {plan.follow_up}")

    from yeaboi.config import get_voice_model

    size = get_voice_model()
    if voice_install.model_is_cached(size):
        print(f"Speech model '{size}' is already downloaded.")
    else:
        print(f"Downloading the '{size}' speech model to {voice_install.model_cache_dir()}…")
        ok, message = voice_install.download_model(size, _echo_progress)
        if not ok:
            # A missing model is a warning, not a failure: it downloads lazily on
            # the first dictation exactly as it always did. Fall through to the
            # probe rather than returning — the exit code still has to report
            # whether dictation can actually run here.
            print(message)
        else:
            print("\nSpeech model downloaded.")

    ready, reason = voice.probe_voice_backend(force=True)
    print("Dictation is ready — double-tap Space in any text field." if ready else f"Not ready — {reason}")
    return 0 if ready else 1


def _echo_once(echoed: list[str], phrase: str) -> None:
    """Print an installer phrase, skipping consecutive repeats.

    narrate() keeps emitting the same phrase for every line of a package's
    download, which on a terminal reads as a stutter rather than progress.
    """
    if phrase and (not echoed or echoed[-1] != phrase):
        echoed.append(phrase)
        print(f"  {phrase}")


def _echo_progress(status: str, fraction: float | None) -> None:
    """One-line download progress that degrades to nothing on a dumb pipe."""
    if fraction is None:
        return
    print(f"\r  {int(fraction * 100):3d}%  {status}", end="", flush=True)


def _list_audio_devices() -> None:
    """Print the available microphones — the diagnostic for "it can\'t hear me".

    Rescans first: PortAudio caches its device list at init, so a mic plugged in
    after this process started would otherwise be missing from the very listing
    the user ran to check whether it was detected.
    """
    from yeaboi import voice

    log = logging.getLogger(__name__)
    log.info("Listing audio input devices")
    available, reason = voice.is_voice_available()
    if not available:
        log.info("Audio device listing skipped: voice unavailable — %s", reason)
        print(f"Voice input unavailable — {reason}")
        return

    voice.refresh_devices()
    devices = voice.list_input_devices()
    if not devices:
        log.warning("Audio device listing found no input devices")
        print("No microphones found.")
        return

    configured = voice.get_voice_device()
    selected = voice.resolve_device(configured)
    print("Input devices:")
    for device in devices:
        tags = []
        if device["is_default"]:
            tags.append("system default")
        if selected is not None and device["index"] == selected:
            tags.append(f"selected via VOICE_DEVICE={configured}")
        suffix = f"  ({', '.join(tags)})" if tags else ""
        print(
            f"  {device['index']:>2}  {device['name']:<34} {device['channels']} ch  {device['samplerate']} Hz{suffix}"
        )
    if selected is None:
        print("\nUsing the system default. Choose another with VOICE_DEVICE=<name or index>.")
    log.info("Listed %d audio input device(s); VOICE_DEVICE=%r resolved to %s", len(devices), configured, selected)


def _json_dump(obj: object) -> str:
    """JSON for CLI --format json output — flattens frozen-dataclass artifacts."""
    import dataclasses
    import json

    def _default(o):
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        return str(o)

    return json.dumps(obj, indent=2, default=_default)


def _resolve_cli_session(session_id: str) -> str | None:
    """Blank/latest → the most recent saved session id (None when none exist).

    An explicit id must exist — a typo'd --session used to pass through
    verbatim and silently produce empty-context output.
    """
    from yeaboi.paths import get_db_path
    from yeaboi.sessions import SessionStore

    with SessionStore(get_db_path()) as store:
        if session_id and session_id != "latest":
            known = [s["session_id"] for s in store.list_sessions()]
            if session_id not in known:
                listing = ", ".join(known) if known else "none saved yet"
                raise ValueError(f"session {session_id!r} not found — available: {listing}")
            return session_id
        return store.get_latest_session_id()


def _run_subcommand(args: argparse.Namespace) -> int:
    """Dispatch `yeaboi <command>` headless runners. Returns a process exit code.

    Thin adapters over the same engines the TUI and MCP server use
    (CLAUDE.md "REQUIRED: Surface Parity").
    """
    from rich.console import Console

    to_json = getattr(args, "format", "text") == "json"
    # JSON mode keeps stdout machine-clean: human chatter goes to stderr.
    console = Console(stderr=to_json)
    handlers = {
        "report": _cmd_report,
        "standup": _cmd_standup,
        "standup-review": _cmd_standup_review,
        "perf": _cmd_perf,
        "retro": _cmd_retro,
        "poker": _cmd_poker,
        "analyze": _cmd_analyze,
        "agents": _cmd_agents,
        "provenance": _cmd_provenance,
        "ship": _cmd_ship,
    }
    try:
        return handlers[args.command](args, console)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _strict_exit(strict: bool, warnings, empty: bool = False) -> int:
    """--strict maps a degraded run (warnings, or an empty result) to exit 3 —
    so CI can tell a real report from a deterministic fallback. Default runs
    keep exit 0 with warnings on stderr."""
    if strict and (warnings or empty):
        detail = f"{len(list(warnings))} warning(s)" + (", empty result" if empty else "")
        print(f"strict: degraded run ({detail}) — exit 3", file=sys.stderr)
        return 3
    return 0


def _print_beta_notice(notice: str) -> None:
    """Print a beta-maturity caveat before a beta subcommand runs.

    stderr, not stdout: the artifact these commands print is routinely piped or
    redirected, and a caveat inside the file would be worse than no caveat. It
    also matches the ``⚠ {warning}`` lines the handlers already emit. Scripted
    callers turn it off with ``BETA_NOTICES_ENABLED=false``.

    Deliberately NOT routed through the engine's warnings tuple: ``--strict``
    maps any warning to exit 3, which would make every strict run fail forever.
    """
    from yeaboi.config import is_beta_notice_enabled

    if is_beta_notice_enabled():
        print(f"⚠ {notice}", file=sys.stderr)


def _cmd_report(args: argparse.Namespace, console: Console) -> int:
    from yeaboi.reporting.engine import run_delivery_report
    from yeaboi.reporting.render import format_report_rich

    console.print(f"[bold cyan]Generating {args.period.replace('_', ' ')} delivery report...[/bold cyan]")
    sprint_names = tuple(s.strip() for s in args.sprint_names.split(",") if s.strip())
    # Assemble the engine's sources dict only when a source flag was given —
    # None means "every configured source" (the engine's auto default).
    sources: dict | None = None
    if args.source or args.code_sources is not None or args.documentation_sources is not None:
        sources = {}
        if args.source:
            sources["delivery"] = ["jira", "azdevops"] if args.source == "both" else [args.source]
        if args.code_sources is not None:
            sources["code"] = list(args.code_sources)
        if args.documentation_sources is not None:
            sources["docs"] = list(args.documentation_sources)
    report = run_delivery_report(
        args.period,
        session_id=_resolve_cli_session(args.session) or "",
        jira_project=args.jira_project,
        azdo_project=args.azdo_project,
        window_start=args.window_start,
        window_end=args.window_end,
        sprint_names=sprint_names,
        period_label_override=args.label,
        theme=args.theme,
        sources=sources,
    )
    for warning in report.warnings:
        print(f"⚠ {warning}", file=sys.stderr)
    if args.format == "json":
        print(_json_dump(report))
    else:
        from yeaboi.paths import REPORTING_EXPORTS_DIR

        console.print(format_report_rich(report))
        console.print(
            f"[dim]Exports (Markdown + HTML + slides, and .pptx when python-pptx is installed): "
            f"{REPORTING_EXPORTS_DIR}[/dim]"
        )
    return _strict_exit(args.strict, report.warnings, empty=not report.delivered_items)


def _cmd_standup(args: argparse.Namespace, console: Console) -> int:
    # Route this run's records to ~/.yeaboi/logs/standup/ like every other
    # standup entry point (CLAUDE.md "Observability" — each mode logs to its own
    # directory). Only the --standup-run scheduler path did this before, so a
    # `yeaboi standup` run left nothing behind in the standup log.
    from yeaboi.logging_setup import mode_log

    with mode_log("standup"):
        return _cmd_standup_inner(args, console)


def _cmd_standup_inner(args: argparse.Namespace, console: Console) -> int:
    from yeaboi.standup.engine import run_standup
    from yeaboi.standup.render import format_standup_rich

    session_id = _resolve_cli_session(args.session)
    if not session_id:
        print("Error: no session found to run a standup for.", file=sys.stderr)
        return 2
    if args.schedule:
        return _cmd_standup_schedule(args, console, session_id)
    if args.list_members:
        from yeaboi.config import get_azure_devops_project, get_jira_project_key
        from yeaboi.paths import get_db_path
        from yeaboi.standup.roster import default_tracker_sources, discover_team_members
        from yeaboi.standup.store import StandupStore

        with StandupStore(get_db_path()) as store:
            config = store.load_config(session_id) or {}
        jira_project = get_jira_project_key() or ""
        azdo_project = get_azure_devops_project() or ""
        sources = (
            args.tracker_sources
            or (config.get("tracker_sources") if config.get("roster_configured") else None)
            or default_tracker_sources(
                jira_project=jira_project,
                azdo_project=azdo_project,
            )
        )
        for member in discover_team_members(
            sources,
            jira_project=jira_project,
            azdo_project=azdo_project,
        ):
            console.print(member)
        return 0
    report = run_standup(
        session_id,
        deliver=args.deliver,
        days=args.days or None,
        channels=args.channels,
        tracker_sources=args.tracker_sources,
        team_members=args.team_members,
        code_sources=args.code_sources,
        github_owners=args.github_owners,
        github_repositories=args.github_repositories,
        github_excluded_repositories=args.github_excluded_repositories,
        azdo_projects=args.azdo_projects,
        azdo_repositories=args.azdo_repositories,
        documentation_sources=args.documentation_sources,
        review_transcripts=args.review_transcripts,
    )
    for warning in report.warnings:
        print(f"⚠ {warning}", file=sys.stderr)
    if args.format == "json":
        print(_json_dump(report))
    else:
        console.print(format_standup_rich(report))
    return _strict_exit(args.strict, report.warnings)


def _cmd_standup_schedule(args: argparse.Namespace, console: Console, session_id: str) -> int:
    """`yeaboi standup --schedule install|remove|status` — manage the OS-native daily job.

    Uses the session's saved standup config (time/weekdays/lead) — set it via the
    TUI Configure screen or the MCP standup_config_set tool.
    """
    import json

    from yeaboi.standup.scheduler import get_schedule_status, install_schedule, remove_schedule

    logging.getLogger("yeaboi.cli").info("standup schedule %s: session=%s", args.schedule, session_id)
    if args.schedule == "status":
        status = get_schedule_status(session_id)
        if args.format == "json":
            print(json.dumps(status, indent=2))
        else:
            state = "installed" if status.get("installed") else "not installed"
            suffix = f" ({status.get('path')})" if status.get("path") else ""
            console.print(f"Schedule for [bold]{session_id}[/bold] [{status.get('platform', '?')}]: {state}{suffix}")
        return 0
    if args.schedule == "remove":
        console.print(remove_schedule(session_id))
        return 0
    # install — from the saved config (defaults mirror standup/store.py)
    from yeaboi.paths import get_db_path
    from yeaboi.standup.store import StandupStore

    with StandupStore(get_db_path()) as store:
        config = store.load_config(session_id) or {}
    standup_time = config.get("time") or "10:00"
    weekdays = config.get("weekdays") or "1-5"
    lead_minutes = int(config.get("lead_minutes", 10))
    console.print(install_schedule(session_id, standup_time, weekdays, lead_minutes))
    return 0


def _format_review_text(review, console: Console) -> None:
    """Print a transcript review as scannable text."""
    console.print(f"[bold]Transcript review — {review.standup_date or 'unknown date'}[/bold]")
    if review.sources:
        console.print("Read: " + ", ".join(s.filename for s in review.sources))
    if review.accuracy_note:
        console.print(review.accuracy_note)
    if review.gaps:
        console.print("\n[bold]Gaps in standup itself[/bold] (drafted as GitHub issues)")
        for gap in review.gaps:
            # Parentheses, not brackets: Rich reads [high/high] as markup and
            # silently swallows it.
            console.print(f"  • {gap.title}  ({gap.priority} priority, {gap.confidence} confidence)  {gap.fingerprint}")
            if gap.root_cause:
                console.print(f"    {gap.root_cause}")
    if review.config_suggestions:
        console.print("\n[bold]Fix in your configuration[/bold] (never filed)")
        for gap in review.config_suggestions:
            console.print(f"  • {gap.title}")
            if gap.remedy:
                console.print(f"    → {gap.remedy}")
    if not review.gaps and not review.config_suggestions:
        console.print("\nNo gaps found — the report matched what the team said.")


def _resolve_review_inputs(args: argparse.Namespace) -> tuple[list[str] | None, str]:
    """Fold the positional paths, ``-`` and ``--transcript-text`` into engine args.

    Returns ``(transcript_paths, transcript_text)``.

    Stdin is resolved HERE rather than in the engine: ``sweep_and_review`` does a
    bare ``Path(p)`` on everything it is handed, so a literal ``"-"`` would reach
    it as a filename. Every path is run through ``normalize_dropped_path`` because
    a file dragged from Finder arrives quoted (Terminal) or backslash-escaped
    (iTerm2), and would otherwise fail as "not found".
    """
    from yeaboi.standup.transcripts import normalize_dropped_path

    raw = [*(args.transcript_paths or []), *(getattr(args, "paths", None) or [])]
    text = args.transcript_text or ""

    paths: list[str] = []
    for entry in raw:
        if entry == "-":
            if not text:
                # A bare `-` on an interactive terminal means the user expected a
                # pipe and did not give one. Reading anyway blocks forever with a
                # blank screen and no hint, which reads as a hung command.
                if sys.stdin.isatty():
                    raise SystemExit(
                        "standup-review -: nothing is piped in. "
                        "Pipe a transcript (cat standup.txt | yeaboi standup-review -), "
                        "or pass the file path instead."
                    )
                text = sys.stdin.read()
            continue
        cleaned = normalize_dropped_path(entry)
        if cleaned:
            paths.append(cleaned)
    return (paths or None), text


def _cmd_standup_review(args: argparse.Namespace, console: Console) -> int:
    """`yeaboi standup-review` — audit standup reports against meeting transcripts."""
    from yeaboi.logging_setup import mode_log

    with mode_log("standup"):
        return _cmd_standup_review_inner(args, console)


def _cmd_standup_review_inner(args: argparse.Namespace, console: Console) -> int:
    from yeaboi.paths import get_db_path
    from yeaboi.standup.engine import file_transcript_issues, run_transcript_review, transcript_nudge
    from yeaboi.standup.store import StandupStore

    logging.getLogger(__name__).info("standup-review (list_gaps=%s file=%s)", args.list_gaps, args.file_issues)
    session_id = _resolve_cli_session(args.session)
    if not session_id:
        print("Error: no session found to review transcripts for.", file=sys.stderr)
        return 2

    if args.list_gaps:
        # The dedup ledger, so it is possible to SEE dedup working rather than
        # having to trust it.
        with StandupStore(get_db_path()) as store:
            reviews = store.get_reviews(session_id, limit=30)
            ledger = store.get_gap_issues(limit=50)
        if args.format == "json":
            print(_json_dump({"reviews": reviews, "gap_issues": ledger}))
            return 0
        console.print(f"[bold]{len(reviews)} review(s)[/bold]")
        for row in reviews:
            console.print(f"  {row['standup_date'] or '?'}  run={row['run_id'] or '-'}  {row['status']}")
        console.print(f"\n[bold]{len(ledger)} tracked gap(s)[/bold]")
        for entry in ledger:
            issue = f"#{entry['issue_number']}" if entry["issue_number"] else entry["state"]
            console.print(f"  {entry['fingerprint']}  {issue}  ×{entry['occurrences']}  {entry['title']}")
        nudge = transcript_nudge(session_id)
        if nudge:
            console.print(f"\n[bold]{len(nudge.missed_dates)} unchecked standup(s)[/bold]")
            console.print("  " + ", ".join(nudge.missed_dates[:14]))
            console.print(f"  {nudge.message}")
        return 0

    paths, transcript_text = _resolve_review_inputs(args)

    review = run_transcript_review(
        session_id,
        transcript_paths=paths,
        transcript_text=transcript_text,
        transcript_dir=args.transcript_dir,
        standup_date=args.standup_date,
        max_transcripts=args.max_transcripts,
        include_reviewed=args.include_reviewed,
    )
    if transcript_text and review.sources:
        # Name what landed: an import the user cannot see is an import they
        # cannot tell went to the right day.
        imported = review.sources[0]
        print(f"Imported {imported.filename} — covers {imported.covered_date} ({imported.attribution})")
    for warning in review.warnings:
        print(f"⚠ {warning}", file=sys.stderr)

    filing = None
    if args.file_issues:
        if not review.gaps:
            print("Nothing to file — no gaps in standup itself were diagnosed.", file=sys.stderr)
        else:
            filing = file_transcript_issues(review.review_id, session_id=session_id)
            for warning in filing.warnings:
                print(f"⚠ {warning}", file=sys.stderr)

    if args.format == "json":
        print(_json_dump({"review": review, "filing": filing} if filing else review))
    else:
        _format_review_text(review, console)
        if filing:
            console.print(f"\nFiled {filing.filed}, commented {filing.commented}, skipped {filing.skipped}.")
            for link in filing.links:
                if link.issue_url:
                    console.print(f"  {link.issue_url}")
        elif review.gaps:
            console.print("\nRun again with --file-issues to file these on GitHub.")

    warnings = list(review.warnings) + list(filing.warnings if filing else [])
    return _strict_exit(args.strict, warnings)


def _cmd_perf(args: argparse.Namespace, console: Console) -> int:
    logging.getLogger(__name__).info("perf %s (beta)", args.perf_command)
    # One call site ahead of the branch covers all five subcommands.
    _print_beta_notice(PERFORMANCE_BETA_NOTICE)

    if args.perf_command == "roster":
        from yeaboi.performance.roster import fetch_roster

        engineers = fetch_roster()
        if not engineers:
            console.print("[yellow]No engineers found — is a tracker (Jira/AzDO) configured?[/yellow]")
            return 2
        for eng in engineers:
            console.print(f"  • {getattr(eng, 'name', eng)}")
        return 0

    if args.perf_command == "prep":
        from yeaboi.performance.engine import run_one_on_one_prep
        from yeaboi.performance.render import format_prep_rich

        prep = run_one_on_one_prep(
            args.engineer,
            session_id=_resolve_cli_session(args.session) or "",
            jira_project=args.jira_project,
            azdo_project=args.azdo_project,
        )
        for warning in prep.warnings:
            print(f"⚠ {warning}", file=sys.stderr)
        console.print(format_prep_rich(prep))
        return _strict_exit(args.strict, prep.warnings)

    if args.perf_command == "complete":
        transcript = args.transcript
        if transcript.startswith("@"):
            try:
                path = fs_policy.resolve_and_check(transcript[1:], mode="read", context="perf complete @transcript")
            except fs_policy.SandboxViolationError as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1
            if not path.exists():
                print(f"Error: transcript file not found: {path}", file=sys.stderr)
                return 1
            transcript = path.read_text().strip()
        from yeaboi.performance.engine import complete_one_on_one
        from yeaboi.performance.render import format_completion_rich

        record = complete_one_on_one(
            args.engineer,
            transcript,
            session_id=_resolve_cli_session(args.session) or "",
            deliver=args.deliver,
            recipients=args.recipients,
            images=tuple(args.images),
        )
        for warning in record.warnings:
            print(f"⚠ {warning}", file=sys.stderr)
        console.print(format_completion_rich(record))
        return _strict_exit(args.strict, record.warnings)

    if args.perf_command == "review":
        from yeaboi.performance.engine import run_six_month_review
        from yeaboi.performance.render import format_review_rich

        review = run_six_month_review(
            args.engineer,
            session_id=_resolve_cli_session(args.session) or "",
            jira_project=args.jira_project,
            azdo_project=args.azdo_project,
            period_months=args.months,
        )
        for warning in review.warnings:
            print(f"⚠ {warning}", file=sys.stderr)
        console.print(format_review_rich(review))
        return _strict_exit(args.strict, review.warnings)

    # note
    from yeaboi.paths import get_db_path
    from yeaboi.performance.store import PerformanceStore

    with PerformanceStore(get_db_path()) as store:
        store.add_note(args.engineer, args.text)
    console.print(f"[green]Note recorded for {args.engineer}[/green]")
    return 0


def _cmd_provenance(args: argparse.Namespace, console: Console) -> int:
    """The provenance audit headless: same engine the MCP tools use
    (CLAUDE.md "REQUIRED: Surface Parity")."""
    import json
    from dataclasses import asdict

    logging.getLogger(__name__).info("provenance %s", args.provenance_command)

    if args.provenance_command == "audit":
        from yeaboi.provenance.engine import run_provenance_audit
        from yeaboi.provenance.render import format_audit_rich

        report = run_provenance_audit(window_days=args.window_days)
        for warning in report.warnings:
            print(f"⚠ {warning}", file=sys.stderr)
        if args.format == "json":
            print(json.dumps(asdict(report), indent=2))
        else:
            console.print(format_audit_rich(report))
        return _strict_exit(args.strict, report.warnings, empty=report.total_records == 0)

    # trace
    from yeaboi.provenance.engine import trace_entity
    from yeaboi.provenance.render import format_trace_rich

    trace = trace_entity(args.entity_id, depth=args.depth)
    for warning in trace.warnings:
        print(f"⚠ {warning}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(asdict(trace), indent=2))
    else:
        console.print(format_trace_rich(trace))
    return 0 if trace.found else 1


def _cmd_ship(args: argparse.Namespace, console: Console) -> int:
    """The ship pipeline headless: same engine the TUI page uses
    (CLAUDE.md "REQUIRED: Surface Parity"). The approval gate prompts on the
    terminal and resolves through the same ShipStore CAS as the TUI screen."""
    import json
    from dataclasses import asdict

    from yeaboi.beta import SHIP_BETA_NOTICE

    logging.getLogger(__name__).info("ship %s (beta)", args.ship_command)
    _print_beta_notice(SHIP_BETA_NOTICE)

    if args.ship_command == "history":
        from yeaboi.ship.render import format_history_rich
        from yeaboi.ship.store import ShipStore

        with ShipStore() as store:
            runs = store.list_runs(limit=args.limit)
        if args.format == "json":
            print(json.dumps([asdict(r) for r in runs], indent=2))
        else:
            console.print(format_history_rich(runs))
        return 0

    if args.ship_command == "status":
        from yeaboi.ship import budget
        from yeaboi.ship.render import format_budget_rich, format_run_rich
        from yeaboi.ship.store import ShipStore

        with ShipStore() as store:
            runs = store.list_runs(limit=1)
        posture = budget.status()
        if args.format == "json":
            print(json.dumps({"latest": asdict(runs[0]) if runs else None, "budget": asdict(posture)}, indent=2))
        else:
            if runs:
                console.print(format_run_rich(runs[0]))
            else:
                console.print("No ship runs yet.")
            console.print(format_budget_rich(posture))
        return 0

    # run
    return _ship_run(args, console)


def _ship_run(args: argparse.Namespace, console: Console) -> int:
    """`yeaboi ship run` — engine on a worker thread, the gate answered here."""
    import json
    import threading
    import time
    from dataclasses import asdict

    from yeaboi import fs_policy
    from yeaboi.ship import engine, worktree
    from yeaboi.ship.render import format_run_rich
    from yeaboi.ship.store import ShipStore

    repo = str(Path(args.repo).expanduser().resolve())
    if not args.dry_run:
        try:
            # Resolve the toplevel BEFORE the consent check: every write lands
            # there (`git worktree add` into <toplevel>/.git, the later push),
            # and fs_policy containment is `is_relative_to` — so granting a
            # subdirectory would let yeaboi write outside the approved root.
            repo = str(worktree.resolve_repo(repo))
        except worktree.WorktreeError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2
        try:
            fs_policy.resolve_and_check(repo, mode="write", context="ship: run a coding agent against this repository")
        except PermissionError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2
        if not sys.stdin.isatty():
            # The gate is a human decision made at a terminal; without one the
            # run would hang forever awaiting an approval nobody can give.
            print("✗ ship run needs an interactive terminal — the approval gate prompts here.", file=sys.stderr)
            return 2

    result_box: list = [None]
    cancel = threading.Event()

    def _work() -> None:
        result_box[0] = engine.run_ship(
            args.story_id,
            repo,
            session_id=args.session,
            check_command=args.check,
            timeout_minutes=args.timeout_minutes,
            dry_run=args.dry_run,
            cancel_event=cancel,
        )

    with ShipStore() as preexisting:
        # Runs that already exist belong to OTHER sessions (a TUI in another
        # terminal, a stale process). Prompting for those would let this user
        # approve a diff they are not looking at — and push it.
        known = {r.run_id for r in preexisting.list_runs(limit=100)}
    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    try:
        with ShipStore() as store:
            while worker.is_alive():
                worker.join(timeout=0.5)
                if not worker.is_alive() or cancel.is_set():
                    continue  # a cancelled run winds down on its own; stop prompting
                # An open gate is one this loop has not answered yet: resolving
                # stamps gate_resolution, and a rework clears it again — so the
                # reopened gate prompts again by construction.
                for run in store.list_runs(limit=3):
                    if run.run_id in known or run.status != "awaiting_approval" or run.gate_resolution:
                        continue
                    console.print(format_run_rich(run))
                    try:
                        answer = input("Approve and open a PR? [y]es / [n]o with feedback / [c]ancel run: ")
                    except EOFError:
                        answer = "c"
                    answer = answer.strip().lower()
                    if answer in ("y", "yes"):
                        store.resolve_gate(run.run_id, "approved")
                    elif answer in ("n", "no"):
                        try:
                            comment = input("Feedback for the agent's rework: ").strip()
                        except EOFError:
                            comment = ""
                        store.resolve_gate(run.run_id, "rejected", comment)
                    else:
                        cancel.set()
                time.sleep(0.5)
    except KeyboardInterrupt:
        cancel.set()
        print("Cancelling the run — the agent is stopped and nothing is pushed…", file=sys.stderr)
        worker.join(timeout=60)
    worker.join(timeout=5)
    run = result_box[0]
    if run is None:
        print("✗ the run did not report a result — see logs", file=sys.stderr)
        return 1
    for warning in run.warnings:
        print(f"⚠ {warning}", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(asdict(run), indent=2))
    else:
        console.print(format_run_rich(run))
    if args.strict and run.status != "approved":
        print(f"strict: run ended {run.status} — exit 3", file=sys.stderr)
        return 3
    return 0


def _cmd_agents(args: argparse.Namespace, console: Console) -> int:
    """The Agents family headless: same engines the TUI cards and MCP tools use
    (CLAUDE.md "REQUIRED: Surface Parity")."""
    logging.getLogger(__name__).info("agents %s (beta)", args.agents_command)
    _print_beta_notice(AGENTWATCH_BETA_NOTICE)

    if args.agents_command == "cost":
        import json
        from dataclasses import asdict

        from yeaboi.agentwatch.engine import run_agent_usage
        from yeaboi.agentwatch.render import format_usage_rich

        report = run_agent_usage(window_days=args.window_days, project=args.project, source=args.source)
        for warning in report.warnings:
            print(f"⚠ {warning}", file=sys.stderr)
        if args.format == "json":
            print(json.dumps(asdict(report), indent=2))
        else:
            console.print(format_usage_rich(report))
        return _strict_exit(args.strict, report.warnings, empty=report.session_count == 0)

    if args.agents_command == "advisor":
        import json
        from dataclasses import asdict

        from yeaboi.agentwatch.advisor import run_agent_advisor
        from yeaboi.agentwatch.render import format_advisor_rich

        report = run_agent_advisor(window_days=args.window_days)
        for warning in report.warnings:
            print(f"⚠ {warning}", file=sys.stderr)
        if args.format == "json":
            print(json.dumps(asdict(report), indent=2))
        else:
            console.print(format_advisor_rich(report))
        return _strict_exit(args.strict, report.warnings, empty=report.session_count == 0)

    if args.agents_command == "standup":
        import json
        from dataclasses import asdict

        from yeaboi.agentwatch.engine import run_agent_standup
        from yeaboi.agentwatch.render import format_standup_rich

        digest = run_agent_standup(
            days=args.days,
            tracker_sources=args.tracker_sources,
            github_owners=args.github_owners,
            azdo_projects=args.azdo_projects,
            include_local_sessions=args.include_local_sessions,
            deliver=args.deliver,
        )
        for warning in digest.warnings:
            print(f"⚠ {warning}", file=sys.stderr)
        if args.format == "json":
            print(json.dumps(asdict(digest), indent=2))
        else:
            console.print(format_standup_rich(digest))
        empty = digest.sessions_worked == 0 and not digest.repo_activity
        return _strict_exit(args.strict, digest.warnings, empty=empty)

    if args.agents_command == "security":
        import json
        from dataclasses import asdict

        from yeaboi.agentwatch.engine import run_agent_security
        from yeaboi.agentwatch.render import format_security_rich

        report = run_agent_security(deep=args.deep)
        for warning in report.warnings:
            print(f"⚠ {warning}", file=sys.stderr)
        if args.format == "json":
            print(json.dumps(asdict(report), indent=2))
        else:
            console.print(format_security_rich(report))
        return _strict_exit(args.strict, report.warnings)

    return 1


def _cmd_retro(args: argparse.Namespace, console: Console) -> int:
    """Read-back of past retro boards. The live collaborative board needs a TTY
    host and stays in the TUI (see the surface-parity registry)."""
    import json

    from yeaboi.paths import get_db_path
    from yeaboi.retro.store import RetroStore

    session_id = _resolve_cli_session(args.session)
    if not session_id:
        print("Error: no session found to read retros for.", file=sys.stderr)
        return 2
    with RetroStore(get_db_path()) as store:
        history = store.get_history(session_id, limit=args.limit)
        # Load the latest report for the carried-items summary (and for --export).
        latest = store.get_latest_report(session_id)
    exported: dict = {}
    if args.export:
        if latest is None:
            print("Error: no retro recorded yet — run a retro board from the TUI first.", file=sys.stderr)
            return 2
        from yeaboi.retro.export import export_retro

        exported = {k: str(v) for k, v in export_retro(latest, history=history).items()}
    # Summarise the latest retro's carried-over action items by status.
    carried = list(latest.carried_action_items) if latest else []
    carried_summary = ""
    if carried:
        from collections import Counter

        from yeaboi.retro.board import CARRIED_STATUS_LABELS

        counts = Counter((c.status or "pending") for c in carried)
        carried_summary = " · ".join(
            f"{n} {CARRIED_STATUS_LABELS.get(st, st).lower()}" for st, n in counts.most_common()
        )
    if args.format == "json":
        print(
            json.dumps(
                {
                    "session_id": session_id,
                    "history": history,
                    "exported": exported,
                    "carried_action_items": [{"text": c.text, "status": c.status or "pending"} for c in carried],
                },
                indent=2,
            )
        )
        return 0
    if not history:
        console.print("[yellow]No retros recorded for this session yet — run one from the TUI Retro page.[/yellow]")
        return 0
    for row in history:
        console.print(
            f"  • {row['retro_date'] or row['run_at'][:10]}  {row['project_name'] or session_id}"
            f"  — {row['card_count']} cards"
        )
    if carried_summary:
        console.print(f"  Last sprint's actions: {carried_summary}")
    for kind, path in exported.items():
        console.print(f"  Exported {kind}: {path}")
    return 0


def _cmd_poker(args: argparse.Namespace, console: Console) -> int:
    """Read-back of past poker sessions. The live voting board needs a TTY host
    and stays in the TUI (see the surface-parity registry)."""
    import json

    from yeaboi.paths import get_db_path
    from yeaboi.poker.store import PokerStore

    with PokerStore(get_db_path()) as store:
        # Poker sessions often run under auto-created quick sessions, so the
        # default listing is cross-session; --session narrows it.
        rows = store.get_all_history(200)
        if args.session:
            rows = [r for r in rows if r.get("session_id") == args.session]
        rows = rows[: args.limit]
        latest = store.get_run_by_id(rows[0]["id"]) if rows else None
    exported: dict = {}
    if args.export:
        if latest is None:
            print("Error: no poker session recorded yet — run one from the TUI Poker page.", file=sys.stderr)
            return 2
        from yeaboi.poker.export import export_poker

        with PokerStore(get_db_path()) as _store:
            run_history = _store.get_history(latest.session_id, limit=30) if latest.session_id else []
        exported = {k: str(v) for k, v in export_poker(latest, history=run_history).items()}
    if args.format == "json":
        print(json.dumps({"history": rows, "exported": exported}, indent=2))
        return 0
    if not rows:
        console.print("[yellow]No poker sessions recorded yet — run one from the TUI Poker page.[/yellow]")
        return 0
    for row in rows:
        scope = " · ".join(p for p in (row.get("source"), row.get("scope_label")) if p)
        console.print(
            f"  • {row['poker_date'] or row['run_at'][:10]}  {scope or row.get('project_name') or '—'}"
            f"  — {row['estimated_count']}/{row['ticket_count']} estimated"
        )
    for kind, path in exported.items():
        console.print(f"  Exported {kind}: {path}")
    return 0


def _cmd_analyze(args: argparse.Namespace, console: Console) -> int:
    from rich.table import Table

    from yeaboi.analysis import run_team_analysis

    # Each component runs over its own sub-sources; --members applies to whichever
    # delivery tracker(s) run (the engine reads the per-tracker entry) and to code authors.
    components: dict[str, list[str]] = {}
    if args.delivery:
        components["delivery"] = args.delivery
    if args.code:
        components["code"] = args.code
    if args.docs:
        components["docs"] = args.docs
    members = {"jira": args.members, "azdevops": args.members} if args.members else None
    analysis_scope = {
        provider: values
        for provider, values in {
            "github": getattr(args, "github_owner", None),
            "azdo": getattr(args, "azdo_code_project", None),
            "confluence": getattr(args, "confluence_space", None),
            "notion": getattr(args, "notion_root", None),
        }.items()
        if values
    }

    # An explicit components map is authoritative for delivery, so --source only takes
    # effect when delivery is left to the default. Warn rather than silently ignore it.
    if components and "delivery" not in components and args.source:
        print(
            f"⚠ --source {args.source} ignored: no --delivery selected (code/docs are global scans)",
            file=sys.stderr,
        )

    console.print("[bold cyan]Analysing team history...[/bold cyan]")
    result = run_team_analysis(
        source=args.source,
        project_key=args.project,
        sprint_count=args.sprints,
        generate_samples=args.samples,
        include_insights=not args.no_insights,
        include_ai_usage=args.include_ai_usage,
        include_doc_quality=args.include_doc_quality,
        analysis_depth=args.depth,
        analysis_window_days=getattr(args, "window_days", 120),
        analysis_scope=analysis_scope or None,
        analysis_model=getattr(args, "analysis_model", None),
        analysis_features=getattr(args, "features", None),
        components=components or None,
        members=members,
    )
    for warning in result["warnings"]:
        print(f"⚠ {warning}", file=sys.stderr)
    if args.format == "json":
        print(_json_dump(result))
        return _strict_exit(args.strict, result["warnings"])
    # Delivery — one section per tracker, under a "From Jira"/"From Azure DevOps"
    # banner so it's clear which velocity numbers came from which tracker.
    _source_names = {"jira": "From Jira", "azdevops": "From Azure DevOps"}
    for tracker, sub in result.get("delivery", {}).items():
        console.rule(f"[bold cyan]{_source_names.get(tracker, tracker)}[/bold cyan]")
        _print_profile_summary(console, sub)
    comparison = result.get("comparison") or []
    if comparison:
        table = Table(title="Delivery — side by side", show_header=True)
        table.add_column("Metric", style="bold")
        table.add_column("Jira")
        table.add_column("Azure DevOps")
        for label, jira_val, azdo_val in comparison:
            table.add_row(label, jira_val, azdo_val)
        console.print(table)
    # Code + Docs — single global scans, printed once.
    if result.get("code"):
        _print_code_summary(console, result["code"])
    if result.get("docs"):
        _print_docs_summary(console, result["docs"])
    return _strict_exit(args.strict, result["warnings"])


def _print_profile_summary(console: Console, sub: dict) -> None:
    """Print one delivery tracker's summary (saved profile + top coaching insights)."""
    profile = sub["profile"]
    console.print(f"[green]Team profile saved for {profile.source}/{profile.project_key}[/green]")
    console.print(
        f"  Analysed [bold]{profile.sample_sprints}[/bold] sprints, [bold]{profile.sample_stories}[/bold] stories"
    )
    console.print(f"  Avg velocity: [bold]{profile.velocity_avg:.0f} ± {profile.velocity_stddev:.0f}[/bold] pts/sprint")
    insights = sub.get("insights") or {}
    for category in ("start", "stop", "keep", "try"):
        for item in insights.get(category, [])[:2]:
            console.print(f"  [bold]{category.upper()}[/bold]: {item.get('title', '')}")


def _print_code_summary(console: Console, code: dict) -> None:
    """Print selected-user Code activity, changed-file, and coverage summary."""
    sig = code.get("signal")
    examples = code.get("examples") or {}
    enabled = set(examples.get("enabled_features") or ("ai_footprint", "code_health"))
    console.rule("[bold cyan]Code — selected-user analysis[/bold cyan]")
    if "ai_footprint" in enabled:
        from yeaboi.analysis.ai_usage import footprint_small_sample

        fp = getattr(sig, "footprint_pct", 0.0)
        scanned = getattr(sig, "scanned_commits", 0) + getattr(sig, "scanned_prs", 0)
        marked = getattr(sig, "ai_commits", 0) + getattr(sig, "ai_prs", 0)
        if sig is not None and footprint_small_sample(sig):
            console.print(
                f"  AI-marked: [bold]{marked} of {scanned}[/bold] commits/PRs "
                "(small sample — % suppressed; lower bound)"
            )
        else:
            console.print(f"  AI footprint: [bold]{fp:.0f}%[/bold] of {scanned} commits/PRs (lower bound)")
    health = examples.get("repository_health") or {}
    if "code_health" in enabled:
        console.print(
            f"  Changed files: [bold]{health.get('files_analysed', 0)}[/bold] analysed · "
            f"{health.get('repositories_touched', 0)} repositories touched · {health.get('findings', 0)} findings"
        )
    selected = examples.get("selected_users") or []
    unmatched = examples.get("unmatched_users") or []
    if selected:
        console.print(f"  Selected users: {', '.join(selected)}")
    if unmatched:
        console.print(f"  Unmatched users: [yellow]{', '.join(unmatched)}[/yellow]")
    coverage = examples.get("coverage_report") or {}
    console.print(
        f"  Coverage: [bold]{str(coverage.get('status', 'complete')).upper()}[/bold] · "
        f"{coverage.get('succeeded', 0)}/{coverage.get('eligible', 0)} eligible assets succeeded"
    )
    for action in (examples.get("action_plan") or [])[:5]:
        console.print(f"  [bold]{str(action.get('priority', '')).upper()}[/bold]: {action.get('title', '')}")


def _print_docs_summary(console: Console, docs: dict) -> None:
    """Print the global Docs (clarity) scan summary."""
    sig = docs.get("signal")
    examples = docs.get("examples") or {}
    console.rule("[bold cyan]Docs — clarity[/bold cyan]")
    from yeaboi.analysis.doc_quality import doc_small_sample

    window = examples.get("window_days")
    small = " (small sample)" if sig is not None and doc_small_sample(sig) else ""
    console.print(
        f"  Clarity: [bold]{getattr(sig, 'avg_clarity', 0):.0f}/100[/bold] · "
        f"Usefulness: [bold]{getattr(sig, 'avg_usefulness', 0):.0f}/100[/bold] · "
        f"{getattr(sig, 'pages_scanned', 0)} pages" + (f" · last {window} days" if window else "") + small
    )
    coverage = examples.get("coverage_report") or {}
    console.print(
        f"  Coverage: [bold]{str(coverage.get('status', 'complete')).upper()}[/bold] · "
        f"{coverage.get('succeeded', 0)}/{coverage.get('eligible', 0)} eligible assets succeeded"
    )
    for action in (examples.get("action_plan") or [])[:5]:
        console.print(f"  [bold]{str(action.get('priority', '')).upper()}[/bold]: {action.get('title', '')}")


def _run_learn(console: Console) -> None:
    """Run the full team analysis via the analysis engine and print a summary.

    Delegates to analysis/engine.py:run_team_analysis — the same pipeline the
    TUI Analysis mode and the MCP team_analyze tool use — so the CLI stores the
    identical rich profile (with examples) in the real sessions DB.
    """
    from rich.table import Table

    console.print("[bold cyan]Analysing team history...[/bold cyan]")
    try:
        from yeaboi.analysis import run_team_analysis

        result = run_team_analysis(include_insights=False)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return

    for warning in result["warnings"]:
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    # --learn auto-detects a single tracker → one delivery profile.
    delivery = result.get("delivery") or {}
    if not delivery:
        console.print("[yellow]No delivery profile produced (no tracker configured).[/yellow]")
        return
    profile = next(iter(delivery.values()))["profile"]

    console.print(f"[green]Team profile saved for {profile.source}/{profile.project_key}[/green]")
    console.print(
        f"  Analysed [bold]{profile.sample_sprints}[/bold] sprints, [bold]{profile.sample_stories}[/bold] stories"
    )
    console.print(f"  Avg velocity: [bold]{profile.velocity_avg:.0f} ± {profile.velocity_stddev:.0f}[/bold] pts/sprint")
    console.print(f"  Estimation accuracy: [bold]{profile.estimation_accuracy_pct:.0f}%[/bold]")
    console.print(f"  Sprint completion rate: [bold]{profile.sprint_completion_rate:.0f}%[/bold]")

    if profile.point_calibrations:
        table = Table(title="Story Point Calibration", show_header=True)
        table.add_column("Points", style="bold")
        table.add_column("Avg Cycle Time")
        table.add_column("Samples")
        table.add_column("Overshoot %")
        for cal in profile.point_calibrations:
            if cal.sample_count > 0:
                table.add_row(
                    str(cal.point_value),
                    f"{cal.avg_cycle_time_days:.1f} days",
                    str(cal.sample_count),
                    f"{cal.overshoot_pct:.0f}%",
                )
        console.print(table)


def _run_team_profile(console: Console) -> None:
    """Display the current stored team calibration profile."""

    from yeaboi.paths import get_db_path
    from yeaboi.team_profile import TeamProfileStore

    db_path = get_db_path()
    if not db_path.exists():
        console.print("[yellow]No team profiles found. Run --learn first.[/yellow]")
        return

    with TeamProfileStore(db_path) as store:
        profiles = store.list_profiles()

    if not profiles:
        console.print("[yellow]No team profiles found. Run --learn first.[/yellow]")
        return

    for profile in profiles:
        console.print(
            f"\n[bold cyan]{profile.team_id}[/bold cyan] "
            f"({profile.sample_sprints} sprints, {profile.sample_stories} stories)"
        )
        console.print(f"  Velocity: {profile.velocity_avg:.0f} ± {profile.velocity_stddev:.0f} pts/sprint")
        console.print(f"  Estimation accuracy: {profile.estimation_accuracy_pct:.0f}%")
        console.print(f"  Sprint completion rate: {profile.sprint_completion_rate:.0f}%")
        if profile.point_calibrations:
            console.print("  [dim]Point calibrations:[/dim]")
            for cal in profile.point_calibrations:
                if cal.sample_count > 0:
                    console.print(
                        f"    {cal.point_value} pt → {cal.avg_cycle_time_days:.1f} day avg "
                        f"({cal.sample_count} samples, {cal.overshoot_pct:.0f}% overshoot)"
                    )


def _run_retro(console: Console, session_id: str) -> None:
    """Run compare_plan_to_actuals and display the result."""
    import json

    from yeaboi.tools.team_learning import compare_plan_to_actuals

    console.print(f"[bold cyan]Comparing plan to actuals for session: {session_id}[/bold cyan]")
    try:
        result = compare_plan_to_actuals.invoke({"session_id": session_id})
        data = json.loads(result)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return

    if "error" in data:
        console.print(f"[red]{data['error']}[/red]")
        return

    console.print(f"  Session: [bold]{data.get('session_id', session_id)}[/bold]")
    console.print(f"  Planned stories: {data.get('planned_story_count', 0)}")
    console.print(f"  Planned sprints: {data.get('planned_sprint_count', 0)}")
    console.print(f"  Planned points: {data.get('planned_total_points', 0)}")
    console.print(f"  Tracker: {data.get('tracker', 'none')}")
    console.print(f"  Matched stories: {data.get('matched_stories', 0)}")
    if "note" in data:
        console.print(f"  [yellow]{data['note']}[/yellow]")


def _seed_allowed_paths_from_standup() -> None:
    """One-time sandbox grandfathering for pre-sandbox standup configs.

    Standup repo paths configured before the filesystem sandbox existed would
    silently stop being scanned. Exactly once — while YEABOI_ALLOWED_PATHS has
    never been set (unset, not empty) — copy any configured repo_path values
    into the whitelist and log it. Users who later edit or clear the whitelist
    are never re-seeded.
    """
    if os.getenv("YEABOI_ALLOWED_PATHS") is not None:
        return
    db = paths.DB_PATH
    if not db.exists():
        return
    try:
        import sqlite3

        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute("SELECT DISTINCT repo_path FROM standup_config WHERE repo_path != ''").fetchall()
    except Exception:  # noqa: BLE001 — missing table/old schema: nothing to seed
        return
    repo_paths = [r[0] for r in rows if r and r[0]]
    if not repo_paths:
        return
    from yeaboi.config import set_allowed_paths

    set_allowed_paths(repo_paths)
    logging.getLogger(__name__).info(
        "sandbox: seeded YEABOI_ALLOWED_PATHS from existing standup repo paths: %s", repo_paths
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the yeaboi CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Migrate the config tree from the pre-rebrand ~/.scrum-agent dir BEFORE any
    # read/mkdir of ~/.yeaboi. Must run ahead of load_user_config() (which mkdirs
    # the config dir) and ahead of the headless/standup flows that return early,
    # otherwise those paths would create an empty ~/.yeaboi and skip migration.
    paths.migrate_root_dir()

    # Load ~/.yeaboi/.env before any credential reads.
    # override=False means shell env vars and project .env always take precedence.
    load_user_config()

    # --ac-format rides the YEABOI_AC_FORMAT env override that resolve_ac_style
    # already reads — one seam serves the TUI, REPL and headless paths alike.
    if getattr(args, "ac_format", None):
        os.environ["YEABOI_AC_FORMAT"] = args.ac_format
    # Same seam for the architecture spike ("auto" = the built-in behaviour,
    # so only a forced include/skip needs the override).
    if getattr(args, "architecture_spike", None) in ("include", "skip"):
        os.environ["YEABOI_ARCHITECTURE_SPIKE"] = args.architecture_spike

    # ── --list-audio-devices: print the mic table and exit ───────────────────
    # After load_user_config() so the currently-configured VOICE_DEVICE can be
    # marked, and before anything interactive so it never triggers the wizard.
    if args.list_audio_devices:
        _list_audio_devices()
        return

    # ── --install-voice: set dictation up headlessly and exit ────────────────
    # Logging is configured first, unlike the exits above it: this is the CI and
    # dev-container surface, where the log file is the only diagnostic and the
    # installer's raw child output (logged at DEBUG) is the whole point of it.
    # Without this the records go to a handler-less root logger and vanish.
    if args.install_voice:
        from yeaboi.logging_setup import configure_logging

        configure_logging()
        raise SystemExit(_install_voice())

    # ── Filesystem sandbox: session-scoped --allow-path grants ───────────────
    # Applied before any flow that might touch user-supplied paths, so every
    # denial message's "--allow-path" remedy actually works for the same
    # command re-run. Session-only: nothing is persisted (see fs_policy.py).
    for allowed in args.allow_path:
        fs_policy.grant_session(allowed)
    _seed_allowed_paths_from_standup()

    # ── Validation for --non-interactive mode ────────────────────────────────
    if args.non_interactive and not args.description:
        print("Error: --non-interactive requires --description", file=sys.stderr)
        sys.exit(1)

    if args.output and not args.non_interactive and not args.export_only:
        print("Error: --output is only valid with --non-interactive or --export-only", file=sys.stderr)
        sys.exit(1)

    # Resolve --description @file.txt → read file contents
    if args.description and args.description.startswith("@"):
        try:
            desc_path = fs_policy.resolve_and_check(args.description[1:], mode="read", context="--description @file")
        except fs_policy.SandboxViolationError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        if not desc_path.exists():
            print(f"Error: description file not found: {desc_path}", file=sys.stderr)
            sys.exit(1)
        args.description = desc_path.read_text().strip()

    # ── Subcommand dispatch (yeaboi report/standup/perf/analyze) ─────────────
    # Headless mode runners over the shared engines — no TUI, no splash.
    if getattr(args, "command", None):
        from yeaboi.logging_setup import configure_logging

        configure_logging()
        sys.exit(_run_subcommand(args))

    # ── Daily Standup headless flow ──────────────────────────────────────────
    # What the OS scheduler (launchd/cron) invokes: run a standup and deliver it,
    # with no TUI/splash. Runs before the interactive setup below.
    if args.standup_run:
        sys.exit(_run_standup(args))

    if args.standup_remind_transcript:
        sys.exit(_run_transcript_reminder(args))

    # ── Non-interactive headless flow ────────────────────────────────────────
    # Runs the full pipeline without any TUI, splash, or interactive input.
    if args.non_interactive:
        from yeaboi.logging_setup import configure_logging

        configure_logging()
        _run_headless(args)
        return

    # ── Migrate legacy file structure ────────────────────────────────────────
    from yeaboi.paths import migrate_legacy_paths

    migrate_legacy_paths()

    # ── File-based logging ────────────────────────────────────────────────────
    # Writes to ~/.yeaboi/logs/tui/yeaboi.log so developers can diagnose issues
    # without interfering with the TUI display. Rotates at 2 MB. Log level is
    # controlled by LOG_LEVEL in .env (default: WARNING; DEBUG for diagnostics)
    # and can be changed live from the Settings page.
    from yeaboi.logging_setup import configure_logging

    configure_logging()

    # Interactive-path imports — deferred so the headless flows above (and
    # --version/--help) never pay for rich or the setup/splash machinery.
    from rich.console import Console

    from yeaboi.formatters import build_theme
    from yeaboi.persistence import migrate_history_file
    from yeaboi.setup_wizard import is_first_run, run_setup_wizard
    from yeaboi.ui.splash import show_splash

    # Create the console with the requested theme so semantic style names
    # ([command], [hint], [success], etc.) resolve correctly throughout the
    # REPL. Must be created after arg parsing so args.theme is available.
    console = Console(theme=build_theme(args.theme))

    # Rename legacy history file (~/.scrum-agent/history → repl-history).
    # See docs: "Memory & State" — clearer naming for the REPL history file.
    migrate_history_file()

    # See docs: "Architecture" — splash replaces the static welcome panel.
    # The animated intro runs before any interactive UI (wizard / mode select).
    # Skipped when this process was relaunched by the ctrl+U in-app update: the
    # user just watched the upgrade land and doesn't need the ~2s intro again.
    # select_mode's Live(screen=True) enters the alternate screen on its own, so
    # the only thing lost is the animation.
    from yeaboi import update_check

    if not update_check.is_fresh_restart():
        show_splash(console)

    # ── First-run setup wizard ────────────────────────────────────────────────
    # Triggers when ~/.scrum-agent/.env is absent (first run) or --setup is passed.
    # If the user cancels the wizard, exit early — the agent can't run without
    # a configured provider (a cloud API key, or a local Ollama server).
    # Runs immediately after splash — both use fullscreen Live, so no console
    # prints happen in between (avoids visible flicker on alt-screen exit).
    if args.setup or is_first_run():
        completed = run_setup_wizard(console)
        if not completed:
            return

    # Determine early whether we'll use the old REPL or the fullscreen TUI.
    # The TUI path keeps alt-screen active from splash → select_mode seamlessly.
    # The old REPL path needs to exit alt-screen and print info to the terminal.
    use_old_repl = args.mode is not None or args.quick or args.questionnaire is not None

    team_learning_flag = args.learn or args.team_profile or args.retro is not None
    if use_old_repl or args.resume is not None or args.list_sessions or args.clear_sessions or team_learning_flag:
        # Leave alt-screen before printing to the normal terminal
        if console.is_alt_screen:
            console.set_alt_screen(False)

        # Informational prints — only shown for non-TUI paths
        scrum_md = Path(os.getcwd()) / "SCRUM.md"
        if scrum_md.is_file():
            _summarise_scrum_md(console, scrum_md)
        else:
            console.print(
                "[dim]  Tip: create a [cyan]SCRUM.md[/cyan] in this directory to add project notes, "
                "URLs, and design decisions that the agent will read automatically. "
                "See [cyan]SCRUM.md.example[/cyan] for a template.[/dim]"
            )

        if is_langsmith_enabled():
            proxy = detect_proxy()
            if proxy:
                disable_langsmith_tracing()
                console.print(f"[yellow]Warning: proxy detected ({proxy}) — LangSmith tracing auto-disabled[/yellow]")
            else:
                console.print("[dim]LangSmith tracing enabled[/dim]")

    # ── --list-sessions: print all sessions and exit ──────────────────────────
    if args.list_sessions:
        _print_sessions_table(console)
        return

    # ── --clear-sessions: interactive delete and exit ─────────────────────────
    if args.clear_sessions:
        _clear_sessions(console)
        return

    # ── --learn: analyse team history and store calibration profile ───────────
    if args.learn:
        _run_learn(console)
        return

    # ── --team-profile: display stored calibration profile ───────────────────
    if args.team_profile:
        _run_team_profile(console)
        return

    # ── --retro: compare plan to actuals ─────────────────────────────────────
    if args.retro is not None:
        _run_retro(console, args.retro)
        return

    # --export-questionnaire: write a blank template and exit (no REPL)
    if args.export_questionnaire is not None:
        from yeaboi.questionnaire_io import export_questionnaire_md

        path = export_questionnaire_md(None, Path(args.export_questionnaire))
        console.print(f"[green]Questionnaire template exported to {path}[/green]")
        return

    # ── --resume: load saved session and skip mode menu ───────────────────────
    # Phase 8B: when --resume is passed, load the saved state and go directly
    # to run_repl() with the resume_state — no mode selection needed since
    # resumed sessions are always project-planning.
    # See docs: "Memory & State" — session persistence, --resume
    if args.resume is not None:
        resume_state, resume_session_id = _resolve_resume(console, args.resume)
        if resume_state is None:
            return  # user cancelled or no sessions
        from yeaboi.repl import run_repl

        run_repl(
            console=console,
            bell=not args.no_bell,
            theme=args.theme,
            resume_state=resume_state,
            resume_session_id=resume_session_id,
        )
        return  # skip mode menu — resume goes straight to REPL

    # --questionnaire: import a filled file, build state, pass to REPL
    questionnaire = None
    if args.questionnaire is not None:
        qpath = Path(args.questionnaire)
        if not qpath.exists():
            console.print(f"[red]Error: file not found: {qpath}[/red]")
            sys.exit(1)
        try:
            from yeaboi.questionnaire_io import build_questionnaire_from_answers, parse_questionnaire_md

            parsed = parse_questionnaire_md(qpath)
            questionnaire = build_questionnaire_from_answers(parsed)
            console.print(f"[green]Loaded {len(parsed)} answers from {qpath}[/green]")
        except (ValueError, fs_policy.SandboxViolationError) as e:
            # A sandbox denial (path outside the whitelist) prints the same
            # clean message as --description @file / perf @transcript, not a traceback.
            console.print(f"[red]Error: {e}[/red]")
            sys.exit(1)

    # Determine intake mode from flags.
    # Default is None — triggers the interactive mode selection menu in the REPL.
    # CLI flags bypass the menu for power users who know what they want.
    # See docs: "Project Intake Questionnaire" — smart intake
    if args.quick:
        intake_mode = "quick"
    else:
        intake_mode = None

    # --export-only: validate that a non-interactive intake source is provided.
    # Without --quick or --questionnaire the intake requires interactive input,
    # which defeats the purpose of --export-only.
    if args.export_only and not args.quick and questionnaire is None:
        console.print("[red]Error: --export-only requires --quick or --questionnaire to supply intake answers.[/red]")
        sys.exit(1)

    # ── Top-level mode selection ──────────────────────────────────────────────
    # Full-screen mode selector with ASCII art titles and typewriter descriptions.
    #
    # Three paths:
    #   1. --mode flag → bypass UI, use old REPL (backwards compat for scripted runs)
    #   2. --quick/--questionnaire → bypass UI, use old REPL
    #   3. Interactive → full-screen TUI mode selector, which launches the TUI
    #      session (run_session) for smart intake inside its Live context
    #
    # See docs: "Architecture" — mode selection is a CLI-layer concern.
    if use_old_repl:
        from yeaboi.repl import run_repl

        startup_mode = args.mode or "project-planning"
        if startup_mode == "project-planning":
            run_repl(
                console=console,
                questionnaire=questionnaire,
                intake_mode=intake_mode,
                export_only=args.export_only,
                bell=not args.no_bell,
                theme=args.theme,
            )
        else:
            console.print(f"\n[warning]Unknown mode '{startup_mode}'.[/warning]")
    else:
        # Interactive TUI flow — select_mode() launches the full session when
        # the user picks Smart or Full intake. It returns None when done.
        # Alt-screen stays active from splash through select_mode to avoid
        # flicker; clean it up when the TUI exits.
        # Mouse tracking captures scroll-wheel events so they scroll within
        # the app instead of the terminal's own scrollback buffer.
        import atexit

        from yeaboi.ui.mode_select import select_mode
        from yeaboi.ui.shared._input import (
            disable_mouse_tracking,
            enable_mouse_tracking,
            enter_raw_mode,
            exit_raw_mode,
        )

        def _terminal_cleanup() -> None:
            """Safety net — ensure terminal is restored even on unhandled crash."""
            try:
                disable_mouse_tracking()
            except Exception:
                pass
            try:
                exit_raw_mode()
            except Exception:
                pass

        atexit.register(_terminal_cleanup)
        # Hold cbreak+no-echo for the whole TUI so mouse-report bytes arriving
        # between key reads can't echo as garbage during a fast wheel scroll.
        enter_raw_mode()
        enable_mouse_tracking()
        _tui_error: Exception | None = None
        _kb_interrupt = False
        try:
            mode_result = select_mode(console, dry_run=args.dry_run)
        except KeyboardInterrupt:
            mode_result = None
            _kb_interrupt = True
        except Exception as _exc:
            logging.getLogger(__name__).exception("Unhandled exception in TUI")
            _tui_error = _exc
            mode_result = None
        finally:
            disable_mouse_tracking()
            exit_raw_mode()
            # Stop any background music daemon so it doesn't outlive the app.
            from yeaboi import music

            music.shutdown()
            if console.is_alt_screen:
                console.set_alt_screen(False)
        # Surface a friendly, one-line message (never a raw traceback) now that
        # the terminal is restored. See _classify_api_error for the mapping.
        if _tui_error is not None:
            from yeaboi.ui.session._utils import _classify_api_error

            console.print(f"[red]{_classify_api_error(_tui_error)}[/red]")
            console.print("[dim]See ~/.scrum-agent/logs/tui/yeaboi.log for details.[/dim]")
        # Ctrl-C unwinds past the menu's own quit popup, so offer the same
        # stop-Ollama courtesy here — but only now that the finally above has
        # restored the terminal (raw mode off, alt-screen closed), so a plain
        # console.input() echoes normally. The esc/q path handles its own
        # prompt in-TUI and never sets _kb_interrupt, so this can't double-fire.
        if _kb_interrupt:
            try:
                from yeaboi.ollama_control import should_offer_ollama_stop, stop_ollama_server

                if sys.stdin.isatty() and should_offer_ollama_stop():
                    if console.input("Stop the local Ollama server? [y/N] ").strip().lower() == "y":
                        _stopped, _msg = stop_ollama_server()
                        console.print(f"[dim]{_msg}[/dim]")
            except Exception:
                pass
        # The ctrl+U update flow asks for a relaunch onto the version it just
        # installed. It has to happen HERE: os.execv replaces the process image
        # without running atexit handlers, so it's only safe now that the finally
        # above has taken the terminal out of raw mode, stopped mouse tracking and
        # left the alternate screen. restart_in_place only returns on failure.
        if update_check.restart_requested():
            if not update_check.restart_in_place():
                console.print("[dim]Update installed — restart yeaboi to use the new version.[/dim]")
            return
        if mode_result is None:
            return
        # mode_result is non-None only for offline import (questionnaire path)
        startup_mode, ui_intake, questionnaire_path = mode_result
        if ui_intake is not None and intake_mode is None:
            intake_mode = ui_intake
        if questionnaire_path and questionnaire is None:
            qpath = Path(questionnaire_path)
            try:
                from yeaboi.questionnaire_io import build_questionnaire_from_answers, parse_questionnaire_md

                parsed = parse_questionnaire_md(qpath)
                questionnaire = build_questionnaire_from_answers(parsed)
                console.print(f"[green]Loaded {len(parsed)} answers from {qpath}[/green]")
            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                sys.exit(1)
        # Import flow falls through to REPL for review
        if startup_mode == "project-planning":
            from yeaboi.repl import run_repl

            run_repl(
                console=console,
                questionnaire=questionnaire,
                intake_mode=intake_mode,
                export_only=args.export_only,
                bell=not args.no_bell,
                theme=args.theme,
            )


if __name__ == "__main__":
    main()
