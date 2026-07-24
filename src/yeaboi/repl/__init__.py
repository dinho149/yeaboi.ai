"""Interactive REPL loop for yeaboi."""

import logging
import re
import time
from pathlib import Path

import anthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console

from yeaboi.agent.graph import create_graph
from yeaboi.agent.nodes import _build_intake_summary, _parse_review_intent
from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState, ReviewDecision
from yeaboi.config import get_session_prune_days
from yeaboi.input_guardrails import validate_input
from yeaboi.output_guardrails import validate_output
from yeaboi.prompts.intake import PHASE_LABELS, QUESTION_METADATA, is_choice_question
from yeaboi.questionnaire_io import export_questionnaire_md
from yeaboi.repl._intake_menu import (
    _render_intake_mode_menu,
    _render_offline_submenu,
    _resolve_intake_mode,
    _resolve_offline_choice,
)
from yeaboi.repl._io import (
    _export_checkpoint,
    _export_plan_markdown,
    _get_active_suggestion,
    _import_questionnaire_file,
    _is_intake_phase,
    _is_md_file_path,
    _render_artifacts,
    _render_resume_summary,
)
from yeaboi.repl._questionnaire import (
    _SUGGEST_CONFIRM,
    AI_LABEL,
    AI_QUESTION_LABEL,
    EDIT_HINT,
    USER_LABEL,
    _render_choice_options,
    _render_questionnaire_ui,
    _render_resume_context,
    _resolve_choice_input,
    _resolve_dynamic_choice,
    _split_intake_preamble,
    _warm_confirm,
)
from yeaboi.repl._questionnaire import (
    _render_dynamic_choices as _render_dynamic_choices,
)
from yeaboi.repl._review import (
    REVIEW_HINT,
    _clear_downstream_artifacts,
    _is_unrecognized_review_input,
    _resolve_review_choice,
    _serialize_artifacts_for_review,
)
from yeaboi.repl._ui import (
    _PIPELINE_STEPS,
    _build_spinner_message,
    _build_toolbar,
    _predict_next_node,
    _simulate_stream,
    print_phase_header,
    stream_response,
)
from yeaboi.sessions import SessionStore, make_display_name, make_session_id, make_unique_display_names

logger = logging.getLogger(__name__)

PROMPT = "scrum> "
EXIT_COMMANDS = {"exit", "quit"}
HELP_COMMANDS = {"help", "?"}
HISTORY_DIR = Path.home() / ".scrum-agent"

# Default filename for in-REPL export
DEFAULT_EXPORT_FILENAME = "scrum-questionnaire.md"

HELP_TEXT = """\
[bold]Available commands:[/bold]
  [command]help[/command], [command]?[/command]       Show this help message
  [command]skip[/command]          Skip the current intake question (uses a sensible default)
  [command]defaults[/command]      Apply defaults for all remaining questions in the current phase
  [command]export[/command]        Export current artifacts as HTML report + Markdown
  [command]/compact[/command]      Switch to compact output (hide secondary columns)
  [command]/verbose[/command]      Switch to verbose output (full detail, default)
  [command]/resume[/command]       Load a previously saved session
  [command]/clear[/command]        Delete saved sessions (pick one or all)
  [command]Q6: answer[/command]  Edit Q6 inline (from the summary)
  [command]edit Q6[/command]      Re-answer Q6 interactively (from the summary)
  [command]exit[/command], [command]quit[/command]   Exit the agent
  [command]Ctrl+C[/command]       Exit the agent
  [command]Ctrl+D[/command]       Exit the agent

Type a project description or question to get started.\
"""

# No autocomplete — commands are discoverable via `help`. A WordCompleter
# would pop up on every keystroke during free-text project descriptions,
# which is distracting. Power users who know the commands can just type them.

# ---------------------------------------------------------------------------
# Rate-limit retry with exponential backoff
# ---------------------------------------------------------------------------
# See docs: "Guardrails" — graceful degradation on API errors
#
# When the Anthropic API returns a 429 (rate limit), we retry up to 3 times
# with exponential backoff (5s → 10s → 20s) and show a live countdown so
# the terminal doesn't look frozen.

_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 5  # seconds


def _handle_rate_limit(console: Console, graph, invoke_state: dict) -> dict | None:
    """Retry graph.invoke() with exponential backoff after rate-limit.

    Args:
        console: Rich Console for countdown output.
        graph: Compiled LangGraph to invoke.
        invoke_state: The state dict to pass to graph.invoke().

    Returns:
        The graph result on success, or None if all retries exhausted.
    """
    for attempt in range(1, _RATE_LIMIT_MAX_RETRIES + 1):
        delay = _RATE_LIMIT_BASE_DELAY * (2 ** (attempt - 1))
        logger.warning("repl: rate limited — retry %d/%d in %ds", attempt, _RATE_LIMIT_MAX_RETRIES, delay)
        console.print(f"[warning]Rate limited — retry {attempt}/{_RATE_LIMIT_MAX_RETRIES} in {delay}s...[/warning]")
        for remaining in range(delay, 0, -1):
            console.print(f"[hint]  {remaining}s...[/hint]")
            time.sleep(1)
        try:
            result = graph.invoke(invoke_state)
            logger.info("repl: rate-limit retry succeeded (attempt %d)", attempt)
            console.print("[success]Retry succeeded.[/success]")
            return result
        except anthropic.RateLimitError:
            continue
    logger.error("repl: rate-limit retries exhausted after %d attempts", _RATE_LIMIT_MAX_RETRIES)
    console.print("[error]Rate-limit retries exhausted. Please wait and try again.[/error]")
    return None


def _display_tool_activity(console: Console, old_messages: list, new_messages: list) -> None:
    """Show a dim summary of tool calls that ran during a graph invocation.

    # See docs: "Guardrails" — tool layer transparency
    # Scans for ToolMessages that appeared in new_messages but not in old_messages.
    # Displays tool name + a brief status so the user can see what the agent did.
    """
    old_count = len(old_messages)
    new_tool_msgs = [m for m in new_messages[old_count:] if isinstance(m, ToolMessage)]
    if not new_tool_msgs:
        return

    for tm in new_tool_msgs:
        name = tm.name or "unknown_tool"
        content = str(tm.content) if tm.content else ""
        if content.startswith("Error"):
            status = "[warning]failed[/warning]"
        else:
            # Show a brief snippet of the result (first 80 chars)
            snippet = content[:80].replace("\n", " ").strip()
            if len(content) > 80:
                snippet += "…"
            status = f"[dim]{snippet}[/dim]"
        console.print(f"  [dim]↳ tool:[/dim] [hint]{name}[/hint] — {status}")


def run_repl(
    console: Console | None = None,
    questionnaire: QuestionnaireState | None = None,
    intake_mode: str | None = None,
    export_only: bool = False,
    bell: bool = True,
    theme: str = "dark",
    resume_state: dict | None = None,
    resume_session_id: str | None = None,
    non_interactive: bool = False,
    output_format: str | None = None,
) -> None:
    """Run the interactive REPL loop.

    Args:
        console: Rich Console instance for output. Creates one if not provided.
        questionnaire: Pre-populated questionnaire state (from --questionnaire flag).
            When provided, the intake summary is shown immediately and the user
            can confirm or edit before proceeding.
        intake_mode: Intake questionnaire mode — "smart", "quick",
            "small_project", or None. When None (default), an interactive mode
            selection menu is shown before the first prompt. The --quick CLI flag
            bypasses the menu by passing the mode directly.
        export_only: When True, auto-accept all review checkpoints and exit
            after the full plan is generated. Writes scrum-plan.md on completion.
        bell: When True (default), ring the terminal bell after pipeline steps
            complete (analyzer → sprint planner). Disable with --no-bell.
        theme: Terminal colour theme — "dark" (default) or "light". Applied to
            the Console so semantic style names resolve to theme-appropriate colors.
        resume_state: Pre-loaded graph state from a saved session (--resume).
            When provided, skips the intake mode menu and conversational opener,
            and picks up from where the previous session left off.
            # See docs: "Memory & State" — session persistence, --resume
        resume_session_id: Session ID of the session being resumed. Used so
            subsequent save_state() calls update the same row.
        non_interactive: When True, skip all interactive prompts. The
            --description value is injected as the first HumanMessage.
        output_format: Output format for non-interactive mode — "json",
            "html", or "markdown". When set, the corresponding export is
            written at pipeline completion.
    """
    logger.info("run_repl started: mode=%s export_only=%s", intake_mode, export_only)

    if console is None:
        from yeaboi.formatters import build_theme

        console = Console(theme=build_theme(theme))

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_file = HISTORY_DIR / "repl-history"

    # Phase 8A/8B: session persistence — db sits alongside the history file so
    # the existing HISTORY_DIR monkeypatch in tests automatically redirects
    # the db to tmp_path, keeping tests isolated from ~/.scrum-agent/.
    # See docs: "Memory & State" — session persistence
    from yeaboi.paths import get_db_path

    _session_id = resume_session_id or make_session_id()
    _store = SessionStore(get_db_path())

    # Phase 8C: warn if the DB was written by a newer version of the code.
    # This can happen when a user downgrades — schema changes may be
    # incompatible, so we surface a dim warning rather than silently corrupting.
    if _store.schema_mismatch:
        console.print(
            "[dim]Warning: sessions.db was created by a newer version of yeaboi. "
            "Session data may not load correctly.[/dim]"
        )

    # Phase 8C: auto-prune old sessions at startup to prevent unbounded growth.
    # Configurable via SESSION_PRUNE_DAYS env var (default 30, 0=disabled).
    # See docs: "Memory & State" — session persistence
    _prune_days = get_session_prune_days()
    if _prune_days > 0:
        try:
            _pruned = _store.prune_old_sessions(_prune_days)
            if _pruned:
                _s = "s" if _pruned != 1 else ""
                console.print(f"[dim]Pruned {_pruned} session{_s} older than {_prune_days} days.[/dim]")
        except Exception:
            pass  # best-effort — never block startup

    # Phase 8B: when resuming, the session row already exists — mark it as
    # created so save_state() works immediately. For new sessions, create the
    # row eagerly so partial sessions (mid-questionnaire) can be resumed too.
    _session_created: bool = resume_state is not None
    _session_has_data: bool = resume_state is not None
    _project_name_recorded: bool = False

    # See docs: "Architecture" — REPL-side UI layer
    # bottom_toolbar uses a callable closure so the toolbar re-reads the
    # mutable graph_state dict on each prompt repaint. Python closures
    # capture variables by reference, so reassignment at line ~1460
    # (graph_state = result) is visible to the lambda on the next repaint.
    session = PromptSession(
        PROMPT,
        history=FileHistory(str(history_file)),
        bottom_toolbar=lambda: _build_toolbar(graph_state),
    )

    # See docs: "Agentic Blueprint Reference" — Core Graph Setup
    # Compile the graph once outside the loop for efficiency. create_graph()
    # validates the topology and returns a CompiledStateGraph; doing this once
    # avoids re-compiling on every user message.
    # create_graph() auto-loads tools via get_tools() when no tools are passed.
    graph = create_graph()
    logger.info("Graph compiled for REPL session")

    # See docs: "Memory & State" — stateless invocation requires manual history
    # Without a checkpointer (Phase 7), we must manually pass all prior messages
    # AND questionnaire state on each invocation. We track the full graph state
    # dict (not just messages) so that questionnaire progress persists across
    # turns. After each graph.invoke(), we save the returned state and merge
    # the next user message into it before the next invocation.
    graph_state: dict = {"messages": []}

    # Output detail level — toggled by /compact and /verbose commands.
    # Compact mode hides secondary columns (descriptions, ACs, disciplines).
    compact_mode: bool = False

    # Track when the user picked [2] Edit from the intake confirm menu — we
    # just showed "Which question would you like to change?" and are waiting
    # for a bare question number on the next prompt. When True, the review
    # intercept normalises bare numbers (e.g. "6") to "Q6" before resolving,
    # so they don't accidentally match reject keywords.
    _awaiting_edit_q_num: bool = False

    # Set True when the questionnaire just completed so the next loop iteration
    # can show [1] Analyse / [2] Chat and treat Enter or "1" as "continue".
    _post_questionnaire_ready: bool = False

    # ── Phase 8B: resume from saved session ─────────────────────────────
    # When resume_state is provided, skip the intake mode menu and
    # conversational opener entirely. The existing route_entry conditional
    # router handles the routing — if state has epics but no stories, the
    # next graph.invoke() routes to story_writer automatically.
    # See docs: "Memory & State" — session persistence, --resume
    if resume_state is not None:
        logger.info("Resuming session: id=%s", resume_session_id)
        graph_state = resume_state
        # Ensure messages list exists — on resume we inject a synthetic
        # message so the graph has at least one message for add_messages.
        if "messages" not in graph_state:
            graph_state["messages"] = []
        if not graph_state["messages"]:
            graph_state["messages"] = [AIMessage(content="Session resumed.")]

        # Show resume status — project name and where we left off.
        # Pre-analysis sessions use the Q1 answer as a fallback label so the
        # user sees something recognisable instead of "(unnamed project)".
        _pa = graph_state.get("project_analysis")
        _project = getattr(_pa, "project_name", "") if _pa else ""
        if not _project:
            _qs_label = graph_state.get("questionnaire")
            if isinstance(_qs_label, QuestionnaireState):
                _project = _qs_label.answers.get(1, "")[:50]
        _next = _predict_next_node(graph_state)
        _label = _project or "(unnamed project)"
        console.print(f"\n[success]Resumed session:[/success] {_label}")
        console.print(f"[hint]Next step: {_next}[/hint]")

        # If pending_review is set, show the artifacts and review menu
        if graph_state.get("pending_review"):
            _render_artifacts(console, graph_state, compact=compact_mode)
            console.print(f"\n{REVIEW_HINT}")
        # If questionnaire is mid-progress, show context and the questionnaire UI
        elif isinstance(graph_state.get("questionnaire"), QuestionnaireState):
            _qs_resume = graph_state["questionnaire"]
            if not _qs_resume.completed:
                _render_resume_context(console, _qs_resume)
                _render_questionnaire_ui(console, _qs_resume)
            elif _predict_next_node(graph_state) != "agent":
                _render_resume_summary(console, graph_state)
                console.print("[hint]Type 'continue' or press Enter to proceed.[/hint]")
        elif _next != "agent":
            _render_resume_summary(console, graph_state)
            console.print("[hint]Type 'continue' or press Enter to proceed.[/hint]")

        # Check if project name is already known for session tracking
        if _pa and getattr(_pa, "project_name", ""):
            _project_name_recorded = True

    # If a pre-populated questionnaire was provided (via --questionnaire flag),
    # show the intake summary immediately so the user can confirm or edit.
    # See docs: "Project Intake Questionnaire" — offline workflow
    elif questionnaire is not None:
        questionnaire.intake_mode = intake_mode or "smart"
        summary = _build_intake_summary(questionnaire)
        ai_msg = AIMessage(content=summary)
        graph_state["questionnaire"] = questionnaire
        graph_state["messages"] = [ai_msg]
        # Set pending_review so the review intercept handles [Accept/Edit/Reject/Export].
        graph_state["pending_review"] = "project_intake"
        # Render the Rich table summary instead of streaming raw markdown.
        # The markdown version stays in messages for the LLM.
        console.print(f"\n{AI_LABEL}")
        from yeaboi.formatters import render_intake_summary

        console.print(render_intake_summary(questionnaire, compact=compact_mode))
        console.print(f"\n{REVIEW_HINT}")
    else:
        # Interactive mode selection — when no CLI flag was given, show a
        # numbered menu so the user understands the intake modes and
        # picks one before the first question. The --quick CLI flag
        # bypasses this by passing the mode directly.
        if intake_mode is None:
            _render_intake_mode_menu(console)
            while True:
                try:
                    choice = session.prompt()
                except (KeyboardInterrupt, EOFError):
                    console.print("[hint]Goodbye![/hint]")
                    return
                intake_mode = _resolve_intake_mode(choice.strip())
                if intake_mode is not None:
                    break
                console.print("[warning]Please pick 1 or 2.[/warning]")

        # ── Offline questionnaire flow ────────────────────────────────
        # When the user picks [2] Offline, show a sub-menu to export a
        # blank template or import a filled one. Export writes the file
        # and exits; import loads the file and enters the confirm flow.
        if intake_mode == "offline":
            _render_offline_submenu(console)
            while True:
                try:
                    sub_choice = session.prompt()
                except (KeyboardInterrupt, EOFError):
                    console.print("[hint]Goodbye![/hint]")
                    return
                offline_action = _resolve_offline_choice(sub_choice.strip())
                if offline_action is not None:
                    break
                console.print("[warning]Please pick 1 or 2.[/warning]")

            if offline_action == "export":
                path = export_questionnaire_md(None, Path(DEFAULT_EXPORT_FILENAME))
                console.print(f"[success]Questionnaire exported to {path}[/success]")
                console.print("[hint]Fill it in, then re-run with:[/hint]  yeaboi --questionnaire " + str(path))
                return  # Nothing more to do — user fills it offline

            # offline_action == "import"
            # Default to the same filename that export uses — pressing Enter
            # without typing a path will look for scrum-questionnaire.md in
            # the current directory.
            default_import = Path(DEFAULT_EXPORT_FILENAME)
            console.print(
                f"[hint]Enter the path to your filled questionnaire (.md),[/hint] "
                f"[hint]or press Enter for[/hint] [command]{default_import}[/command][hint]:[/hint]"
            )
            while True:
                try:
                    file_input = session.prompt()
                except (KeyboardInterrupt, EOFError):
                    console.print("[hint]Goodbye![/hint]")
                    return
                raw = file_input.strip()
                file_path = Path(raw).expanduser() if raw else default_import
                if file_path.exists() and file_path.suffix == ".md":
                    break
                console.print("[warning]File not found or not a .md file. Try again.[/warning]")

            try:
                graph_state = _import_questionnaire_file(console, file_path, graph_state)
            except (ValueError, FileNotFoundError) as e:
                console.print(f"[error]Error importing file: {e}[/error]")
                return

            # Skip the conversational opener — go straight to the main loop.
            # Import already showed the summary + confirm hint. The questionnaire
            # is in awaiting_confirmation state, so the main while-loop picks up
            # at the confirm/edit flow.
        else:
            # Conversational opener — greet the user and set expectations before
            # the first prompt. Same for all modes — the mode menu already
            # explained what to expect.
            console.print()
            console.print("[bold]Tell me about your project[/bold] — what are you building and why?")
            console.print("[hint]A few sentences is enough to get started. I'll ask follow-up questions.[/hint]")
            console.print()
            console.print(
                "[hint]Example: \"We're building a mobile app for restaurant reservations. "
                "The team is 4 developers, we use React Native and Node.js, and we need "
                'to launch an MVP in 3 months."[/hint]'
            )
            console.print()

    # Set the intake mode on graph state — by this point intake_mode is always
    # a concrete string (either from CLI flags, menu selection, or defaulted to
    # "smart" for the pre-loaded questionnaire path). When resuming, preserve
    # the saved intake mode from the previous session.
    if resume_state is None:
        graph_state["_intake_mode"] = intake_mode or "smart"

    while True:
        # ── Auto-drive for --export-only mode ─────────────────────────
        # When export_only is True, inject synthetic input instead of
        # prompting the user. Auto-accepts reviews, auto-confirms intake,
        # and auto-continues through the pipeline. Breaks when all sprint
        # planning is done (pipeline complete).
        auto_driven = False
        # Tracks whether the review/confirm intercept already printed the
        # user label this iteration — prevents double-printing when those
        # paths fall through to graph.invoke() (e.g. pipeline "accept").
        _user_label_printed = False
        if export_only:
            qs = graph_state.get("questionnaire")
            # Auto-accept capacity override warnings — pick "1" (recommended sprints).
            # Print the warning so it's visible in stderr (e.g. OpenClaw's exec logs).
            _cap_sel = graph_state.get("capacity_override_target", 0)
            if _cap_sel < -1:
                _recommended = abs(_cap_sel)
                console.print(
                    f"[warning]Capacity warning: stories exceed target"
                    f" — auto-accepting {_recommended} sprints[/warning]"
                )
                stripped = "1"
                auto_driven = True
            elif isinstance(qs, QuestionnaireState) and qs.completed:
                if graph_state.get("pending_review"):
                    stripped = "accept"
                    auto_driven = True
                elif _predict_next_node(graph_state) == "agent":
                    # Pipeline complete — all artifacts generated
                    break
                else:
                    stripped = "continue"
                    auto_driven = True
            elif isinstance(qs, QuestionnaireState) and qs.awaiting_confirmation:
                stripped = "confirm"
                auto_driven = True

        if not auto_driven:
            try:
                user_input = session.prompt()
            except KeyboardInterrupt:
                break
            except EOFError:
                break

            stripped = user_input.strip()

        # ── Post-questionnaire ready gate ────────────────────────────
        # After the questionnaire is accepted, we show a "Start analysis" hint.
        # The graph routes questionnaire-complete state → project_analyzer on any
        # message, so we gate here: only explicit trigger words actually start the
        # pipeline. Anything else re-shows the hint without invoking the graph.
        if _post_questionnaire_ready:
            _start_words = {"1", "start", "analyse", "analyze", "continue", "yes", "y", "go"}
            _lower = stripped.lower()
            if not stripped or _lower in _start_words:
                _post_questionnaire_ready = False
                stripped = "continue"
                # fall through to graph.invoke()
            elif _lower in EXIT_COMMANDS or _lower in HELP_COMMANDS or stripped.startswith("/"):
                # Exit / help / slash commands bypass the gate so they reach
                # their normal handlers below (break, print help, etc.).
                _post_questionnaire_ready = False
                # fall through
            else:
                # Any other input (including "2", free text, etc.) — keep the
                # gate open so the user doesn't accidentally trigger analysis.
                console.print("[hint](Press Enter or type `start` when ready to begin analysis.)[/hint]")
                continue

        # ── Suggestion confirmation ───────────────────────────────────
        # When a question has a suggested answer (from the initial
        # description), pressing Enter or typing Y/yes confirms it.
        # The REPL resolves the input to the suggestion text before
        # sending it to the graph — the node doesn't need special logic.
        # _suggest_confirm is deferred until after the You: label so the
        # conversation reads: You → AI confirms.
        _suggest_confirm: str | None = None
        if not stripped or stripped.lower() in _SUGGEST_CONFIRM:
            suggestion = _get_active_suggestion(graph_state)
            if suggestion:
                stripped = suggestion
                _suggest_confirm = _warm_confirm(suggestion)
            elif not stripped:
                continue

        if not stripped:
            continue

        # ── Input guardrails ──────────────────────────────────────────
        # See docs: "Guardrails" — three lines of defence (Input layer)
        # Skip validation for short commands (exit, help, slash commands)
        # which can't trigger guardrails anyway.
        # Also skip when the user is answering a follow-up probe with
        # dynamic choices — inputs like "all" or "1,3" are valid selections
        # but the off-topic classifier may reject them.
        _qs_for_guard = graph_state.get("questionnaire")
        _in_followup_probe = (
            isinstance(_qs_for_guard, QuestionnaireState)
            and not _qs_for_guard.completed
            and _qs_for_guard.current_question in _qs_for_guard.probed_questions
        )
        if (
            not stripped.startswith("/")
            and stripped.lower() not in EXIT_COMMANDS | HELP_COMMANDS
            and not _in_followup_probe
        ):
            _guardrail_msg = validate_input(stripped)
            if _guardrail_msg:
                console.print(f"[warning]{_guardrail_msg}[/warning]")
                continue

        if stripped.lower() in EXIT_COMMANDS:
            break

        if stripped.lower() in HELP_COMMANDS:
            console.print(HELP_TEXT)
            continue

        # Handle /compact and /verbose toggle commands.
        if stripped.lower() == "/compact":
            compact_mode = True
            console.print("[hint]Switched to compact output.[/hint]")
            continue
        if stripped.lower() == "/verbose":
            compact_mode = False
            console.print("[hint]Switched to verbose output.[/hint]")
            continue

        # ── /resume: load a previously saved session inline ──────────
        # Reuses the same _store that's already open for this REPL session.
        # Lazy-imports _build_sessions_table from cli to avoid a circular
        # import (cli imports run_repl from this module).
        # See docs: "Memory & State" — session persistence
        if stripped.lower() == "/resume":
            from yeaboi.cli import _build_sessions_table

            _resume_sessions = _store.list_sessions()
            if not _resume_sessions:
                console.print("[hint]No saved sessions found.[/hint]")
                continue
            _resume_unique_names = make_unique_display_names(_resume_sessions)

            # Confirm if the current session has data that would be discarded
            if _session_has_data:
                console.print("[warning]You have an active session. Switch to the saved session? (y/n)[/warning]")
                try:
                    _confirm_raw = session.prompt("scrum> ")
                except (KeyboardInterrupt, EOFError):
                    continue
                if _confirm_raw.strip().lower() not in ("y", "yes"):
                    console.print("[hint]Cancelled — staying in current session.[/hint]")
                    continue

            # Show the sessions table and prompt for a pick.
            # Use a dedicated PromptSession so input stays isolated from the
            # main REPL — prevents history/prompt bleed between the two.
            console.print(_build_sessions_table(_resume_sessions, display_names=_resume_unique_names))
            console.print()
            _resume_prompt: PromptSession[str] = PromptSession()
            while True:
                try:
                    _pick_raw = _resume_prompt.prompt("Pick a session number (or 'q' to cancel): ")
                except (KeyboardInterrupt, EOFError):
                    _pick_raw = "q"
                _pick_raw = _pick_raw.strip().lower()
                if _pick_raw in ("q", "quit", "cancel"):
                    break
                # Try numeric index first, then match by display name.
                try:
                    _pick_idx = int(_pick_raw)
                except ValueError:
                    # Match against display names (case-insensitive, partial match)
                    _name_matches = [
                        (i, s)
                        for i, s in enumerate(_resume_sessions)
                        if _pick_raw in _resume_unique_names[s["session_id"]].lower()
                    ]
                    if len(_name_matches) == 1:
                        _pick_idx = _name_matches[0][0] + 1  # convert to 1-based
                    elif len(_name_matches) > 1:
                        console.print(f"[warning]'{_pick_raw}' matches multiple sessions. Be more specific.[/warning]")
                        continue
                    else:
                        console.print("[warning]No match. Enter a number or session name (or 'q' to cancel).[/warning]")
                        continue
                if 1 <= _pick_idx <= len(_resume_sessions):
                    _picked = _resume_sessions[_pick_idx - 1]
                    _picked_sid = _picked["session_id"]
                    _loaded = _store.load_state(_picked_sid)
                    if _loaded is None:
                        console.print(f"[warning]Session {_picked_sid} has no saved state or is corrupt.[/warning]")
                        break
                    # ── Replace graph state and reset tracking variables ──
                    graph_state = _loaded
                    if "messages" not in graph_state:
                        graph_state["messages"] = []
                    if not graph_state["messages"]:
                        graph_state["messages"] = [AIMessage(content="Session resumed.")]

                    _session_id = _picked_sid
                    _session_created = True
                    _session_has_data = True

                    _pa = graph_state.get("project_analysis")
                    _project_name_recorded = bool(_pa and getattr(_pa, "project_name", ""))

                    _post_questionnaire_ready = False
                    _awaiting_edit_q_num = False

                    # Show resume status — same pattern as the startup resume path
                    _project = getattr(_pa, "project_name", "") if _pa else ""
                    if not _project:
                        _qs_label2 = graph_state.get("questionnaire")
                        if isinstance(_qs_label2, QuestionnaireState):
                            _project = _qs_label2.answers.get(1, "")[:50]
                    _next = _predict_next_node(graph_state)
                    _label = _project or "(unnamed project)"
                    console.print(f"\n[success]Resumed session:[/success] {_label}")
                    console.print(f"[hint]Next step: {_next}[/hint]")

                    if graph_state.get("pending_review"):
                        _render_artifacts(console, graph_state, compact=compact_mode)
                        console.print(f"\n{REVIEW_HINT}")
                    elif isinstance(graph_state.get("questionnaire"), QuestionnaireState):
                        _qs_resume = graph_state["questionnaire"]
                        if not _qs_resume.completed:
                            _render_resume_context(console, _qs_resume)
                            _render_questionnaire_ui(console, _qs_resume)
                        elif _next != "agent":
                            _render_resume_summary(console, graph_state)
                            console.print("[hint]Type 'continue' or press Enter to proceed.[/hint]")
                    elif _next != "agent":
                        _render_resume_summary(console, graph_state)
                        console.print("[hint]Type 'continue' or press Enter to proceed.[/hint]")
                    break
                console.print(f"[warning]Please pick a number between 1 and {len(_resume_sessions)}.[/warning]")
            continue

        # ── /clear: delete saved sessions inline ─────────────────────────
        # Shows the session list with an option to delete one or all.
        if stripped.lower() in ("/clear", "/clear sessions"):
            from yeaboi.cli import _build_sessions_table

            _clear_sessions = _store.list_sessions()
            if not _clear_sessions:
                console.print("[hint]No saved sessions found.[/hint]")
                continue
            _clear_unique = make_unique_display_names(_clear_sessions)
            console.print(_build_sessions_table(_clear_sessions, display_names=_clear_unique))
            console.print()
            console.print(
                "[hint]Enter a session number to delete, [command]all[/command] to clear everything, "
                "or [command]q[/command] to cancel.[/hint]"
            )
            # Use a dedicated PromptSession so input stays isolated from the
            # main REPL — prevents history/prompt bleed between the two.
            _clear_prompt: PromptSession[str] = PromptSession()
            while True:
                try:
                    _clear_raw = _clear_prompt.prompt("Clear> ")
                except (KeyboardInterrupt, EOFError):
                    _clear_raw = "q"
                _clear_raw = _clear_raw.strip().lower()
                if _clear_raw in ("q", "quit", "cancel"):
                    console.print("[hint]Cancelled.[/hint]")
                    break
                if _clear_raw in ("a", "all"):
                    _del_count = _store.delete_all_sessions()
                    console.print(
                        f"[success]Deleted all {_del_count} session{'s' if _del_count != 1 else ''}.[/success]"
                    )
                    break
                try:
                    _clear_idx = int(_clear_raw)
                except ValueError:
                    console.print("[warning]Please enter a number, 'all', or 'q' to cancel.[/warning]")
                    continue
                if 1 <= _clear_idx <= len(_clear_sessions):
                    _del_picked = _clear_sessions[_clear_idx - 1]
                    _del_sid = _del_picked["session_id"]
                    _del_name = _clear_unique.get(_del_sid, _del_sid)
                    _store.delete_session(_del_sid)
                    console.print(f"[success]Deleted session: {_del_name}[/success]")
                    break
                console.print(f"[warning]Please pick a number between 1 and {len(_clear_sessions)}.[/warning]")
            continue

        # Handle `export` command — write HTML + Markdown of current artifacts.
        # Priority: review checkpoint stage > full plan > questionnaire only.
        # The review/confirm intercepts below handle [4] Export from numbered menus;
        # this handles the bare "export" keyword typed at any point in the session.
        if stripped.lower() == "export":
            _pending = graph_state.get("pending_review")
            _qs_state = graph_state.get("questionnaire")
            _artifact_keys = ("project_analysis", "epics", "stories", "tasks", "sprints")
            has_artifacts = any(graph_state.get(k) for k in _artifact_keys)
            if _pending:
                # At a review checkpoint — use the stage label and re-show the menu.
                _export_checkpoint(console, graph_state, stage=_pending)
                console.print(f"\n{REVIEW_HINT}")
            elif has_artifacts:
                # Pipeline has generated at least one artifact — export all available.
                _export_checkpoint(console, graph_state, stage="complete")
            else:
                # Intake phase — export questionnaire template/answers as Markdown.
                qs_for_export = _qs_state if isinstance(_qs_state, QuestionnaireState) else None
                path = export_questionnaire_md(qs_for_export, Path(DEFAULT_EXPORT_FILENAME))
                console.print(f"[success]Questionnaire exported to {path}[/success]")
                if isinstance(_qs_state, QuestionnaireState) and _qs_state.awaiting_confirmation:
                    console.print(f"\n{REVIEW_HINT}")
            continue

        # File-path auto-detect: if input looks like a .md file path and the
        # file exists on disk, and we're still in the intake phase, treat it
        # as an import. This avoids false positives — normal conversation rarely
        # ends in .md, and requiring the file to exist filters coincidences.
        if _is_md_file_path(stripped) and _is_intake_phase(graph_state):
            candidate = Path(stripped).expanduser()
            if candidate.exists():
                try:
                    graph_state = _import_questionnaire_file(console, candidate, graph_state)
                except (ValueError, FileNotFoundError) as e:
                    console.print(f"[error]Error importing file: {e}[/error]")
                continue

        # ── Review / intake confirmation checkpoint intercept ──────────
        # See docs: "Guardrails" — human-in-the-loop pattern
        #
        # When pending_review is set, the REPL intercepts the user's input
        # for the [Accept / Edit / Reject / Export] flow before invoking the
        # graph. This operates between graph.invoke() calls — outside the
        # graph — so operator.add reducers on artifact lists don't apply.
        #
        # "project_intake" is routed here too (pending_review set by the
        # intake node alongside awaiting_confirmation=True). The intake gate
        # has its own special handling: bare "edit" → prompt for Q number,
        # "reject" → reset questionnaire, everything else → graph.invoke().
        if graph_state.get("pending_review"):
            if not auto_driven:
                console.print(f"\n{USER_LABEL} {stripped}")
                _user_label_printed = True
            pending_node = graph_state["pending_review"]

            if pending_node == "project_intake":
                # ── Intake confirmation gate ────────────────────────────
                # See docs: "Project Intake Questionnaire" — confirmation gate
                #
                # _awaiting_edit_q_num: user picked [2] Edit last turn and we
                # showed "Which question?". Normalize the bare number to Q<N>
                # before the keyword resolver so "3" isn't treated as "reject".
                if _awaiting_edit_q_num:
                    _awaiting_edit_q_num = False
                    num_match = re.match(r"^[qQ]?(\d{1,2})$", stripped.strip())
                    if num_match:
                        q_num = int(num_match.group(1))
                        if 1 <= q_num <= TOTAL_QUESTIONS:
                            stripped = f"Q{q_num}"
                        else:
                            console.print(
                                f"[warning]Q{q_num} is out of range (questions are 1–{TOTAL_QUESTIONS}).[/warning]"
                            )
                            console.print(f"\n{REVIEW_HINT}")
                            continue
                    # Non-number input → fall through with original stripped.

                resolved_intake = _resolve_review_choice(stripped)
                lowered_intake = resolved_intake.strip().lower()

                if lowered_intake == "export":
                    _export_checkpoint(console, graph_state, stage="questionnaire")
                    console.print(f"\n{REVIEW_HINT}")
                    continue

                if lowered_intake == "edit":
                    # Bare "edit" — prompt for which question to change.
                    console.print(
                        "[hint]Which question would you like to change? "
                        "(e.g.[/hint] [command]Q6[/command] [hint]or[/hint] "
                        "[command]Q6: 5 engineers[/command][hint])[/hint]"
                    )
                    _awaiting_edit_q_num = True
                    continue

                if lowered_intake in {"reject", "restart", "start over", "redo"}:
                    # Reject — reset questionnaire and restart from the opener.
                    graph_state["questionnaire"] = QuestionnaireState()
                    graph_state["messages"] = []
                    graph_state.pop("pending_review", None)
                    console.print("[warning]Starting over — tell me about your project again.[/warning]")
                    console.print()
                    continue

                # "accept", "confirm", "yes", Q<N>, Q<N>: answer, etc.
                # _is_confirm_intent() in the graph handles all accept keywords.
                # Pop pending_review so the graph processes the message cleanly.
                graph_state.pop("pending_review", None)
                # Use resolved_intake (e.g. "accept") not the raw stripped input
                # (e.g. "1") — otherwise the graph receives "1" which
                # _parse_edit_intent() matches as a bare question number and
                # re-asks Q1 instead of confirming.
                stripped = resolved_intake
                # No continue — fall through to graph.invoke().

            else:
                # ── Pipeline review checkpoint ──────────────────────────
                # Resolve numeric menu selections ("1"→"accept", "2"→"edit",
                # "3"→"reject", "4"→"export") before passing to the keyword parser.
                resolved = _resolve_review_choice(stripped)

                # [4] Export — write HTML + Markdown snapshot, stay on same menu.
                if resolved.lower() == "export":
                    _export_checkpoint(console, graph_state, stage=pending_node)
                    console.print(f"\n{REVIEW_HINT}")
                    continue

                decision, feedback = _parse_review_intent(resolved)

                # Catch typos — unrecognized text would silently reject otherwise.
                if _is_unrecognized_review_input(resolved, decision, feedback):
                    console.print(
                        "[warning]I didn't recognise that — please pick 1, 2, or 3 (or type a keyword).[/warning]"
                    )
                    continue

                if decision == ReviewDecision.ACCEPT:
                    # Clear review state — the pipeline continues on next invoke.
                    # route_entry will see the artifacts and route to the next node.
                    graph_state.pop("pending_review", None)
                    graph_state.pop("last_review_decision", None)
                    graph_state.pop("last_review_feedback", None)
                    graph_state.pop("review_feedback_images", None)

                    # Check if the pipeline is now complete (all artifacts generated).
                    # If so, show a completion message instead of invoking the graph
                    # again — the agent node would just dump a redundant markdown
                    # summary of everything the user already saw as Rich tables.
                    if _predict_next_node(graph_state) == "agent":
                        console.print("[success]Accepted — plan complete![/success]")
                        console.print()
                        console.print(
                            "[hint]Your full plan is ready. "
                            "Type [/hint][command]export[/command][hint] to save as HTML + Markdown, "
                            "or keep chatting to ask questions about the plan.[/hint]"
                        )
                        continue

                    console.print("[success]Accepted — continuing to the next step.[/success]")
                    # Don't continue — fall through to graph.invoke() so the next
                    # pipeline node runs immediately without an extra prompt.

                elif decision in (ReviewDecision.EDIT, ReviewDecision.REJECT):
                    # Reject is removed from the menu — both edit and the legacy
                    # "reject" keyword go through the same EDIT path. This gives
                    # the LLM the previous output as context (what to keep/change)
                    # which is more useful than a blind regeneration.
                    if not feedback:
                        console.print("[warning]What changes would you like? (describe your edits)[/warning]")
                        try:
                            feedback = session.prompt().strip()
                        except (KeyboardInterrupt, EOFError):
                            break
                        if not feedback:
                            continue

                    # Serialize current artifacts before clearing — the generation
                    # node uses this as reference for what to keep/modify.
                    serialized = _serialize_artifacts_for_review(graph_state, pending_node)

                    # Clear this node's artifacts + all downstream artifacts.
                    _clear_downstream_artifacts(graph_state, pending_node)
                    graph_state["last_review_decision"] = ReviewDecision.EDIT
                    # Pack feedback + previous output into a single string.
                    # The node splits on "---PREVIOUS OUTPUT---" to extract both.
                    if serialized:
                        graph_state["last_review_feedback"] = f"{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}"
                    else:
                        graph_state["last_review_feedback"] = feedback
                    graph_state.pop("pending_review", None)
                    console.print(f'[hint]Applying: "{feedback}"[/hint]')
                    console.print("[warning]Regenerating with your changes...[/warning]")
                    # Fall through to graph.invoke() to re-run the node.

        # ── Capacity warning intercept ─────────────────────────────────
        # When capacity_override_target is negative (< -1), the sprint_planner
        # detected that total story points exceed the user's sprint target.
        # The user picks: [1] Accept recommended target, [2] Keep original.
        # See docs: "Guardrails" — human-in-the-loop pattern
        _cap_sel = graph_state.get("capacity_override_target", 0)
        if _cap_sel < -1:
            _recommended = abs(_cap_sel)

            # Resume gate — re-display warning + options if user typed "continue"
            if stripped.lower() in ("continue", ""):
                _last_ai = next(
                    (m for m in reversed(graph_state.get("messages", [])) if hasattr(m, "content") and m.content),
                    None,
                )
                if _last_ai:
                    _cap_lines = [ln.strip() for ln in _last_ai.content.split("\n") if ln.strip()]
                    console.print("\n[warning]Capacity warning:[/warning]")
                    for _cl in _cap_lines:
                        console.print(f"  [warning]⚠ {_cl.replace('**', '')}[/warning]")
                console.print(
                    f"\n  [command]\\[1][/command] Accept {_recommended} sprints [hint](recommended)[/hint]"
                    f"\n  [command]\\[2][/command] Keep original target [hint](stories may be packed tighter)[/hint]"
                )
                continue

            if not auto_driven and not _user_label_printed:
                console.print(f"\n{USER_LABEL} {stripped}")
                _user_label_printed = True

            if stripped.strip() == "1":
                graph_state["capacity_override_target"] = _recommended
                console.print(f"[success]Accepted — planning for {_recommended} sprints.[/success]")
                # Fall through to graph.invoke() — sprint_planner re-runs with new target
            elif stripped.strip() == "2":
                graph_state["capacity_override_target"] = -1  # rejected
                console.print("[success]Keeping original target — sprint planner will do its best to fit.[/success]")
                # Fall through to graph.invoke()
            else:
                console.print("[warning]Please pick 1 or 2.[/warning]")
                continue

        # ── Resolve numeric choice input ──────────────────────────────
        # During the active questionnaire, if the current question is a
        # choice question, resolve "1" → "Greenfield" etc. This also
        # applies when editing a choice question via the re-ask flow.
        # Dynamic follow-up choices are resolved here too — when the user
        # is answering a follow-up probe, numeric input maps to the LLM-
        # generated options stored in _follow_up_choices.
        # ── Resolve numeric choice input ──────────────────────────────
        # Resolve before printing so we can show the original input as the
        # user's message and the resolved text as an AI confirmation.
        # _probe_confirm is set when a follow-up probe answer is resolved
        # — it's printed AFTER the You: label, attributed to the AI.
        _probe_confirm: str | None = None
        qs_for_resolve = graph_state.get("questionnaire")
        if isinstance(qs_for_resolve, QuestionnaireState) and not qs_for_resolve.completed:
            if qs_for_resolve.editing_question is not None:
                # Editing flow — resolve against the question being edited
                stripped = _resolve_choice_input(stripped, qs_for_resolve.editing_question)
            elif not qs_for_resolve.awaiting_confirmation:
                cur_q = qs_for_resolve.current_question
                # Check for dynamic choices first (follow-up probes or node-generated, e.g. Q27)
                dynamic_choices = qs_for_resolve._follow_up_choices.get(cur_q)
                if dynamic_choices:
                    # Reject out-of-range numbers — same guard as static choice menus.
                    try:
                        idx = int(stripped)
                        if not (1 <= idx <= len(dynamic_choices)):
                            n = len(dynamic_choices)
                            console.print(f"[warning]Please pick 1–{n}, or type your own answer.[/warning]")
                            continue
                    except ValueError:
                        pass  # Free text or "all" — let _resolve_dynamic_choice handle it
                    stripped = _resolve_dynamic_choice(stripped, dynamic_choices)
                    _probe_confirm = _warm_confirm(stripped)
                elif cur_q in qs_for_resolve.probed_questions:
                    # Follow-up probe with no dynamic choices
                    _probe_confirm = _warm_confirm(stripped)
                else:
                    # Normal questionnaire flow — resolve against current question.
                    # Reject out-of-range numbers on choice questions so "5" on a
                    # 3-option menu doesn't silently pass through as a literal answer.
                    if is_choice_question(cur_q):
                        meta = QUESTION_METADATA.get(cur_q)
                        try:
                            idx = int(stripped)
                            if meta and not (1 <= idx <= len(meta.options)):
                                console.print(f"[warning]Please pick 1–{len(meta.options)}.[/warning]")
                                continue
                        except ValueError:
                            pass  # Free text is allowed
                    stripped = _resolve_choice_input(stripped, cur_q)

        # Show "You:" label for review decisions, re-ask answers, and main
        # agent conversation. Skip during active intake questionnaire (the
        # user just typed at scrum>, so the echo is redundant). Also skip
        # if the review intercept already printed the label this iteration.
        _qs_for_label = graph_state.get("questionnaire")
        _in_active_intake = (
            isinstance(_qs_for_label, QuestionnaireState)
            and not _qs_for_label.completed
            and not _qs_for_label.awaiting_confirmation
            and _qs_for_label.editing_question is None
        )
        if not auto_driven and not _user_label_printed and not _in_active_intake:
            console.print(f"\n{USER_LABEL} {stripped}")

        # Show the warm confirmation after the user's message, attributed
        # to the AI so the conversation reads naturally: You → AI confirms.
        # Show warm confirmation after the user's message, attributed to
        # the AI so the conversation reads: You → AI confirms.
        _confirm_msg = _probe_confirm or _suggest_confirm
        if _confirm_msg:
            console.print(f"\n{AI_LABEL} {_confirm_msg}")

        # See docs: "The ReAct Loop" — Thought → Action → Observation
        # Wrap the user's input as a HumanMessage (LangChain's representation
        # of a user turn) and pass the full state (messages + questionnaire)
        # to the graph. The graph returns updated state with the AI's response.
        user_msg = HumanMessage(content=stripped)

        # Capture pre-invoke state before invoking so we can detect transitions.
        # IMPORTANT: QuestionnaireState is a mutable dataclass — the node mutates
        # it in place during graph.invoke(). Snapshot boolean flags NOW, before
        # the invoke, otherwise prev_qs and new_qs point to the same object and
        # transition detection (phase changes) silently fails.
        prev_qs = graph_state.get("questionnaire")
        prev_phase = (
            prev_qs.current_phase if isinstance(prev_qs, QuestionnaireState) and not prev_qs.completed else None
        )
        # Snapshot completed flag before invoke — QuestionnaireState is mutable
        # and will be updated in-place, so we capture it now for transition detection.
        prev_completed = isinstance(prev_qs, QuestionnaireState) and prev_qs.completed

        # Append the new user message to existing conversation history.
        # Spread existing messages and add the new one — this preserves
        # the full conversation context for both the intake questionnaire
        # and the main agent.
        invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}

        # Show a spinner during graph.invoke() so the terminal doesn't
        # look frozen. The message adapts to the current pipeline stage.
        node_name = _predict_next_node(invoke_state)
        spinner_msg = _build_spinner_message(node_name)

        # ── Inner try: graph.invoke() with specific API error handling ──
        # AuthenticationError and RateLimitError are subclasses of APIStatusError,
        # so they must be caught first. Each handler prints an actionable message
        # (e.g. "check your ANTHROPIC_API_KEY") instead of a raw traceback.
        try:
            logger.info("Graph invoke: node=%s msgs=%d", node_name, len(invoke_state.get("messages", [])))
            start_time = time.time()
            with console.status(f"[ai.label]{spinner_msg}...[/ai.label]", spinner="dots"):
                result = graph.invoke(invoke_state)
            elapsed = time.time() - start_time
            logger.info("Graph invoke complete: %.1fs", elapsed)
        except anthropic.AuthenticationError as e:
            console.print(
                "[error]Authentication failed.[/error] "
                "[warning]Check your ANTHROPIC_API_KEY in .env — it may be missing, expired, or invalid.[/warning]"
            )
            if export_only:
                # Headless auto-drive would retry the same doomed call forever.
                raise SystemExit(1) from e
            continue
        except anthropic.RateLimitError:
            logger.warning("Rate limited — starting retry backoff")
            result = _handle_rate_limit(console, graph, invoke_state)
            if result is None:
                continue
            elapsed = 0.0
        except anthropic.APIConnectionError:
            console.print(
                "[error]Network error.[/error] [warning]Check your internet connection and try again.[/warning]"
            )
            continue
        except anthropic.APIStatusError as e:
            console.print(f"[error]API error (status {e.status_code}):[/error] [warning]{e.message}[/warning]")
            continue
        except Exception as e:
            # Local Ollama failures — actionable hint (server down / model not
            # pulled / timeout), not a raw error. The node guards re-raise these
            # only for the ollama provider (see nodes._should_reraise_llm_error).
            from yeaboi.agent.nodes import _local_llm_hint

            local_hint = _local_llm_hint(e)
            if local_hint:
                logger.error("Local Ollama failure: %s", e)
                console.print(f"[error]{local_hint}[/error]")
                if export_only:
                    # Headless auto-drive would re-inject "continue" and hit the
                    # same error forever — a broken local setup must exit instead.
                    raise SystemExit(1) from e
                continue
            logger.error("Unexpected graph invoke error: %s", e, exc_info=True)
            console.print(f"[error]Unexpected error: {e}[/error]")
            if export_only:
                raise SystemExit(1) from e
            continue

        # ── Outer try: post-processing (rendering, hints, state update) ──
        try:
            ai_msg: AIMessage = result["messages"][-1]

            # Show completion line with elapsed time for pipeline steps.
            # Intake questions are fast and frequent — skip the timing noise.
            if node_name in _PIPELINE_STEPS:
                console.print(f"[success]✓[/success] [hint]{spinner_msg} (took {elapsed:.1f}s)[/hint]")
                if bell:
                    console.bell()

            # Show tool activity — dim summary of any tools the agent called.
            # See docs: "Guardrails" — tool layer transparency
            _display_tool_activity(console, invoke_state.get("messages", []), result.get("messages", []))

            # Detect phase transitions — show a styled divider when the
            # questionnaire moves to a new phase, making the break visible.
            # Also show the header on the very first question (prev_phase is
            # None because no questionnaire existed in state yet).
            new_qs = result.get("questionnaire")
            new_phase = (
                new_qs.current_phase if isinstance(new_qs, QuestionnaireState) and not new_qs.completed else None
            )
            if new_phase and new_phase != prev_phase:
                print_phase_header(console, PHASE_LABELS[new_phase])

            # Display the AI response. When structured artifacts are available
            # (epics, stories, tasks, sprints, analysis), render them as Rich
            # Tables/Panels for scannable output. Otherwise stream as markdown.
            # Pick the question-style label when the intake is actively asking Qs.
            is_intake_question = (
                isinstance(new_qs, QuestionnaireState)
                and not new_qs.completed
                and not new_qs.awaiting_confirmation
                and new_qs.editing_question is None
            )
            # Capacity overflow warning — rendered as output warnings (⚠ style)
            # instead of a streamed AI message, for visual consistency with
            # post-plan output guardrail warnings.
            _cap_result = result.get("capacity_override_target", 0)
            if _cap_result < -1:
                _recommended = abs(_cap_result)
                # Parse the structured warning from the AIMessage content.
                # Format: "Your stories total **X**. At **Y** velocity... Recommendation: ..."
                _cap_lines = [ln.strip() for ln in ai_msg.content.split("\n") if ln.strip()]
                console.print("\n[warning]Capacity warning:[/warning]")
                for _cl in _cap_lines:
                    # Strip markdown bold for cleaner warning output
                    _cl_plain = _cl.replace("**", "")
                    console.print(f"  [warning]⚠ {_cl_plain}[/warning]")
                console.print(
                    f"\n  [command]\\[1][/command] Accept {_recommended} sprints [hint](recommended)[/hint]"
                    f"\n  [command]\\[2][/command] Keep original target [hint](stories may be packed tighter)[/hint]"
                )
            else:
                console.print(f"\n{AI_QUESTION_LABEL if is_intake_question else AI_LABEL}")

                # Display structured artifacts (project_intake summary, epics, stories,
                # tasks, sprints, analysis) via Rich formatters when pending_review is set.
                # Falls back to streaming markdown for regular intake questions and agent chat.
                if result.get("pending_review"):
                    _render_artifacts(console, result, compact=compact_mode)

                    # Run output guardrails on generated artifacts.
                    # See docs: "Guardrails" — output layer (programmatic checks)
                    _out_warnings = validate_output(
                        stories=result.get("stories"),
                        sprints=result.get("sprints"),
                        velocity=result.get("velocity") or 0,
                    )
                    if _out_warnings:
                        console.print("\n[warning]Output warnings:[/warning]")
                        for _w in _out_warnings:
                            console.print(f"  [warning]⚠ {_w}[/warning]")

                elif is_intake_question:
                    # Split preamble (extraction summary, remaining count) from
                    # the actual question.  Preamble is rendered dim so the
                    # question stands out visually.
                    preamble, question = _split_intake_preamble(ai_msg.content)
                    for line in preamble:
                        console.print(f"[hint]{line}[/hint]")
                    if preamble:
                        console.print()  # visual separator
                    stream_response(console, _simulate_stream(question))
                else:
                    stream_response(console, _simulate_stream(ai_msg.content))

            # Show review hint when the graph just set pending_review.
            # This tells the user they can [Accept / Edit / Reject] the output.
            if result.get("pending_review"):
                console.print(f"\n{REVIEW_HINT}")

            # Detect questionnaire-just-completed transition — show a "ready to
            # analyse" menu so the user knows what to do next. Without this the
            # REPL drops silently to scrum> with no guidance after acceptance.
            elif (
                not prev_completed
                and isinstance(new_qs, QuestionnaireState)
                and new_qs.completed
                and not result.get("pending_review")
            ):
                _post_questionnaire_ready = True
                console.print("\n  [command]\\[1][/command] Start analysis\n[hint](press 1 or Enter to begin)[/hint]")
                console.print("[dim]  Your session is saved automatically — you can resume anytime.[/dim]")

            # Show contextual hints during the active questionnaire.
            # Not shown after completion or when in main agent mode (no questionnaire).
            # REVIEW_HINT already covers awaiting_confirmation (pending_review branch above).
            elif isinstance(new_qs, QuestionnaireState) and not new_qs.completed:
                if new_qs.editing_question is not None:
                    # User is re-answering a question — show options if choice Q
                    if is_choice_question(new_qs.editing_question):
                        _render_choice_options(console, new_qs.editing_question)
                    console.print(f"\n{EDIT_HINT}")
                elif not new_qs.awaiting_confirmation:
                    # awaiting_confirmation is handled by pending_review → REVIEW_HINT above
                    _render_questionnaire_ui(console, new_qs)

            # Save the full returned state (messages + questionnaire + any
            # other fields) so it persists to the next invocation.
            graph_state = result

            # Phase 8B: persist session state after every successful invoke
            # so --resume can pick up from where the user left off. The session
            # row is created eagerly (on first invoke) so partial questionnaire
            # sessions are also resumable. Project name is set once known.
            # See docs: "Memory & State" — session persistence
            _session_has_data = True
            try:
                if not _session_created:
                    _store.create_session(_session_id)
                    _session_created = True
                _store.save_state(_session_id, graph_state)
                _pa = result.get("project_analysis")
                if _pa and getattr(_pa, "project_name", "") and not _project_name_recorded:
                    _store.update_project_name(_session_id, _pa.project_name)
                    _project_name_recorded = True
                # Early project name from Q1 — gives a readable display name
                # before the analyzer runs (e.g. mid-questionnaire sessions).
                elif not _project_name_recorded:
                    _qs_for_name = result.get("questionnaire")
                    if isinstance(_qs_for_name, QuestionnaireState):
                        _q1 = _qs_for_name.answers.get(1, "")
                        if _q1:
                            _store.update_project_name(_session_id, _q1[:50])
                            _project_name_recorded = True
                _store.update_last_node(_session_id, node_name)
            except Exception:
                pass  # session tracking is best-effort — never block the REPL

        except Exception as e:
            console.print(f"[error]Error: {e}[/error]")
            # Still save the result so the conversation doesn't get stuck
            graph_state = result

    # Export plan when --export-only or --non-interactive was used.
    if export_only:
        if output_format == "json":
            import sys

            from yeaboi.json_exporter import export_plan_json

            print(export_plan_json(graph_state), file=sys.stdout)
        elif output_format == "html":
            from yeaboi.html_exporter import export_plan_html

            export_path = export_plan_html(graph_state)
            console.print(f"[success]Plan exported to {export_path}[/success]")
        else:
            export_path = _export_plan_markdown(graph_state)
            console.print(f"[success]Plan exported to {export_path}[/success]")

    # Show a "Session saved" confirmation so the user knows where work went.
    # Only shown when at least one successful invoke occurred and the project
    # name is known (gives a meaningful display name like "lendflow-2026-03-06").
    if _session_has_data:
        try:
            _meta = _store.get_session(_session_id)
            if _meta and _meta["project_name"]:
                console.print(f"[dim]Session saved: {make_display_name(_meta)}[/dim]")
        except Exception:
            pass  # best-effort — never block shutdown

    logger.info("REPL session ended")
    console.print("[hint]Goodbye![/hint]")
