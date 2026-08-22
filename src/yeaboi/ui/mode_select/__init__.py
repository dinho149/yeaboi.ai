"""Full-screen mode selection screen using Rich Live + raw terminal input.

# See docs: "Architecture" — this is a UI component in the CLI layer.
# Shown after the setup wizard completes (or on subsequent launches).
# The user picks which agent mode to run: Project Planning, Code Review, etc.
# After selecting Planning, the title slides up and the project list fades in.

Mode names are rendered as two-line ASCII art, stacked vertically.
When a mode is selected, its description typewriter-scrolls in underneath.
Arrow keys navigate, Enter selects. "Coming soon" modes are visible but
not selectable.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import time
from datetime import date
from pathlib import Path

from rich.console import Console

from yeaboi.logging_setup import attach_mode_handler, mode_log
from yeaboi.logging_setup import detach as detach_mode_handler
from yeaboi.paths import get_db_path as _get_db_path
from yeaboi.ui.mode_select.screens._project_cards import (  # noqa: F401
    ProfileSummary,
    ProjectSummary,
    _build_action_button,
    _build_empty_state_card,
    _build_new_analysis_card,
    _build_new_project_card,
    _build_peek_above,
    _build_peek_below,
    _build_profile_card,
    _build_project_card,
    _compute_viewport,
)
from yeaboi.ui.mode_select.screens._project_list_screen import (  # noqa: F401
    _build_project_list_screen,
    _build_project_row,
)

# Re-exports for backwards compatibility and test imports.
from yeaboi.ui.mode_select.screens._screens import (  # noqa: F401
    _AGENT_CARDS,
    _INTAKE_CARDS,
    _MIN_HEIGHT,
    _MIN_WIDTH,
    _MODE_CARDS,
    _OFFLINE_CARDS,
    _SWEEP_ROW_WEIGHT,
    _build_mode_screen,
    _build_slide_frame,
    _build_too_small_screen,
    _build_update_screen,
    duck_hit,
    mode_at_row,
    mode_title_widths,
    selected_title_offset,
    welcome_shows_companion,
)
from yeaboi.ui.mode_select.screens._screens_secondary import (  # noqa: F401
    _build_export_success_screen,
    _build_import_screen,
    _build_intake_screen,
    _build_offline_screen,
    _build_project_export_success_screen,
    _build_team_analysis_screen,
)
from yeaboi.ui.shared._animations import (
    COLOR_RGB,
    FADE_OUT_LEVELS,
    FRAME_TIME_60FPS,
    ease_out_cubic,
)
from yeaboi.ui.shared._beta_notice import show_beta_notice
from yeaboi.ui.shared._click import button_click, parse_click
from yeaboi.ui.shared._input import esc_came_from_back_tab, paste_payload, set_text_entry
from yeaboi.ui.shared._input import read_key as _read_key
from yeaboi.ui.shared._music_bar import duck_working_thread, make_live, take_settings_jump
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll, coalesce_steps
from yeaboi.ui.shared._voice_input import DoubleTapSpace

logger = logging.getLogger(__name__)


def _duck_react(quip_key: str, text: str | None = None) -> None:
    """Quack + speak a completion quip through the shared duck voice.

    The one helper every mode's completion moment calls — strictly reactive
    (something just finished), never ambient. ``text`` overrides the DUCK_QUIPS
    entry for dynamic lines ("3 projects recommended."). Logs once per trigger.
    """
    from yeaboi.ui.shared._duck_voice import DUCK_QUIPS, duck_voice
    from yeaboi.ui.shared._music_bar import quack_duck

    line = text or DUCK_QUIPS.get(quip_key, "")
    if not line:
        return
    logger.info("duck react: %s (%s)", quip_key, line)
    quack_duck()
    duck_voice().say(line)


def _run_on_worker(target, render_frame, frame_time: float, *, drain=None):
    """Run ``target`` on a worker thread while the page keeps animating.

    For calls that used to block the render thread solid (retro action items,
    performance 1:1s, publish): ``render_frame(elapsed)`` runs every frame so
    the page — and the working duck — stays live. Returns target()'s result;
    re-raises whatever it raised, on this thread.

    ``drain``: pass the page's ``read_key`` (only when the terminal supports
    timeouts — a blocking read_key would hang here) and any keys typed during
    the wait are swallowed afterwards — otherwise they'd replay against
    whatever view the result switched to (an impatient double-Enter must not
    press a button on a screen the user never saw).
    """
    from yeaboi.ui.shared._music_bar import duck_working_thread

    result_box: list = [None, None]

    def _work() -> None:
        try:
            result_box[0] = target()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the UI thread below
            result_box[1] = exc

    thread = duck_working_thread(_work, name="mode-worker")
    thread.start()
    start = time.monotonic()
    while thread.is_alive():
        render_frame(time.monotonic() - start)
        time.sleep(frame_time)
    thread.join()
    if drain is not None:
        # Bounded: a real tty buffer holds a handful of keys; an unbounded
        # loop would spin forever against a test fake that never runs dry.
        for _ in range(64):
            if not drain(timeout=0):
                break
    if result_box[1] is not None:
        raise result_box[1]
    return result_box[0]


# ---------------------------------------------------------------------------
# Constants used only by the orchestrator
# ---------------------------------------------------------------------------

_DESC_SCROLL_SPEED = 200  # characters per second for typewriter reveal
_HEADER_SUB_SPEED = 45  # characters per second for the page subtitle typewriter reveal
_FRAME_TIME = FRAME_TIME_60FPS
# Menu intro sweep speed, in diagonal-front units per second (see _SWEEP_ROW_WEIGHT
# and _build_mode_row). Higher = faster wipe-in of the whole menu.
_MENU_SWEEP_SPEED = 150.0
# Companion entrance: after the menu wipes in, the duck glides in from the right
# over this many seconds, then the tip bubble + update box fade in above him.
_COMPANION_INTRO_SECONDS = 0.55
# Returning from a sub-page, the entrance starts this far along so the duck's first
# column lines up with his sub-page corner and he slides back from there into the
# menu spot (rather than re-entering from off-screen).
_COMPANION_RETURN_START = 0.28


def _run_output_share_flow(
    console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    document,
    theme,
    title_fn,
    editable=None,
    on_edit=None,
) -> int:
    """Open the shared temporary-output publishing view.

    Returns how many corrections teammates recorded, so the caller can decide
    whether to keep them. Zero for a read-only share.
    """
    from yeaboi.ui.shared._output_share import run_output_share

    _maybe_offer_share_tier(console, live, read_key, frame_time, supports_timeout)
    return run_output_share(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        document=document,
        theme=theme,
        title_fn=title_fn,
        editable=editable,
        on_edit=on_edit,
    )


def _standup_editable_session(report, run_id: int, history):
    """A correctable standup share, or None when there is nothing to anchor edits to.

    Shared by the live standup page and the saved-runs hub so both offer the same
    thing. Edits are appended as a new row that supersedes its parent, so the
    session needs the run it came from — without ``run_id`` there is no base to
    replay onto and the share stays read-only.
    """
    if not run_id:
        logger.info("standup share: no run_id on this report — sharing read-only, edits are not possible")
        return None
    from yeaboi.artifacts.session import EditableSession

    return EditableSession(
        report,
        kind="standup",
        db_path=_ana_dbp,
        run_id=run_id,
        history=tuple(history or ()),
    )


def _next_log_level(current: str) -> str:
    """Return the next level in the Settings cycle: DEBUG → INFO → WARNING → ERROR → DEBUG.

    Unknown values (including CRITICAL, which is .env-only) are treated as
    WARNING, so the first press lands on ERROR.
    """
    from yeaboi.config import VALID_LOG_LEVELS

    current = current.upper()
    if current not in VALID_LOG_LEVELS:
        current = "WARNING"
    return VALID_LOG_LEVELS[(VALID_LOG_LEVELS.index(current) + 1) % len(VALID_LOG_LEVELS)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_ana_sid = ""  # module-level analysis session ID
_ana_dbp = _get_db_path()  # module-level DB path


def _load_ana_session(project_key: str) -> dict | None:
    """Load the most recent analysis session for a project, or None."""
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(_ana_dbp) as store:
            sessions = store.list_analysis_sessions()
            for sess in sessions:
                if project_key in sess.get("project_name", ""):
                    state = store.load_state(sess["session_id"])
                    if state and state.get("last_page") and state["last_page"] not in ("complete", "done", ""):
                        global _ana_sid  # noqa: PLW0603
                        _ana_sid = sess["session_id"]
                        logger.info(
                            "Resuming analysis session %s at page '%s'",
                            sess["session_id"],
                            state["last_page"],
                        )
                        return state
        logger.debug("No resumable analysis session for %s", project_key)
    except Exception:
        logger.debug("Analysis session load failed", exc_info=True)
    return None


def _save_ana(state: dict, node: str) -> None:
    """Save analysis session state (extracted to reduce nesting depth)."""
    if not _ana_sid:
        return
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(_ana_dbp) as store:
            store.save_state(_ana_sid, state)
            store.update_last_node(_ana_sid, node)
        logger.info("Analysis session saved: page='%s', session=%s", node, _ana_sid)
    except Exception:
        logger.debug("Analysis session save failed", exc_info=True)


def _confirm_ticket_generation(
    live,
    console,
    read_key,
    frame_time,
    supports_timeout,
    *,
    subtitle: str = "",
) -> bool:
    """Ask the user whether to generate sample tickets from the team analysis.

    Renders a dedicated confirmation screen (separating "analyse the team/board"
    from "create sample tickets") and drives a thin frame loop. Returns True if
    the user chooses to generate, False if they decline (Not now / Esc).
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_generate_confirm_screen

    logger.info("Analysis: showing ticket-generation confirmation")
    sel = 0  # 0 = Generate tickets, 1 = Not now
    _labels = ["Generate tickets", "Not now"]
    while True:
        w, h = console.size
        _panel = _build_generate_confirm_screen(
            width=w,
            height=h,
            action_sel=sel,
            subtitle=subtitle,
        )
        live.update(_panel)
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(k)
        if _clicked is not None:
            _idx = button_click(console, _panel, *_clicked, _labels)
            if _idx is not None:
                sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            else:
                continue  # click missed the buttons — ignore it
        if k == "left":
            sel = max(0, sel - 1)
        elif k == "right":
            sel = min(1, sel + 1)
        elif k in ("enter", " "):
            proceed = sel == 0
            logger.info("Analysis: ticket generation %s", "confirmed" if proceed else "declined")
            return proceed
        elif k in ("esc", "q"):
            logger.info("Analysis: ticket generation declined (esc)")
            return False


def _run_preview_flow(
    live,
    console,
    read_key,
    frame_time,
    supports_timeout,
    instr_text,
    ta_profile,
    ta_examples,
    *,
    resume_state: dict | None = None,
):
    """Run the analysis preview flow (Instructions → Epic → Stories → Tasks → Sprint).

    If resume_state is provided, jumps to the appropriate page.
    """
    from yeaboi.tools.team_learning import (
        generate_sample_epic,
        generate_sample_stories,
        generate_sample_tasks,
    )
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_analysis_progress_screen,
        _build_instructions_review_screen,
        _build_sample_epic_screen,
        _build_sample_stories_screen,
        _build_sample_tasks_screen,
    )

    # Accepts an optional timeout so coalesce_scroll() can poll non-blocking
    # (timeout=0.0); a bare _rk() keeps the original per-frame/blocking behaviour.
    _rk = lambda timeout=(frame_time if supports_timeout else None): read_key(timeout=timeout)  # noqa: E731

    # ── Inline editor helpers for dict-based artifacts ────────────
    def _dict_editable_start(line: str) -> int | None:
        """Return column where editable value starts, or None if non-editable."""
        stripped = line.strip()
        if not stripped:
            return None
        if stripped.startswith("\u2500\u2500") and stripped.endswith("\u2500\u2500"):
            return None
        import re as _re

        m = _re.match(r"^[A-Za-z][A-Za-z /]+:\s*", line)
        if m:
            return m.end()
        return None  # non-label lines are not editable

    def _edit_dict_artifact(artifact: dict, fields: list[str], label: str) -> dict | None:
        """Open inline editor for a dict artifact. Returns edited dict or None on cancel."""
        from yeaboi.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel
        from yeaboi.ui.shared._components import analysis_title as _a_title

        # Serialize to text
        w = max(len(f) for f in fields) + 2
        buf_lines: list[str] = []
        for f in fields:
            display_label = f.replace("_", " ").title()
            val = artifact.get(f, "")
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            buf_lines.append(f"{display_label + ':':<{w}}{val}")
            buf_lines.append("")

        buffer = buf_lines
        cr, cc = 0, len(buffer[0].split(":")[0]) + 2 if buffer else 0
        # Find first editable position
        for i, ln in enumerate(buffer):
            col = _dict_editable_start(ln)
            if col is not None:
                cr, cc = i, col
                break
        _atitle = _a_title()

        def _render(buf, _cr, _cc, so, rw, rh):
            return render_editor_panel(
                buf,
                _cr,
                _cc,
                so,
                width=rw,
                height=rh,
                editor_label=label,
                title_override=_atitle,
            )

        result = edit_buffer_loop(
            live,
            console,
            buffer,
            cr,
            cc,
            _rk,
            editable_start_fn=_dict_editable_start,
            render_fn=_render,
        )
        if result is None:
            return None

        # Parse back: extract "Label: value" pairs
        import re as _re

        edited = dict(artifact)  # shallow copy
        for line in result:
            m = _re.match(r"^([A-Za-z][A-Za-z /]+):\s*(.*)", line)
            if m:
                key_display = m.group(1).strip()
                value = m.group(2).strip()
                # Map display label back to dict key
                key = key_display.lower().replace(" ", "_")
                if key in artifact:
                    orig = artifact[key]
                    if isinstance(orig, int):
                        try:
                            value = int(value)
                        except ValueError:
                            continue
                    elif isinstance(orig, list):
                        value = [v.strip() for v in value.split(",") if v.strip()]
                    edited[key] = value
        return edited

    def _edit_story_dict(story: dict) -> dict | None:
        """Edit a single story dict using the planning mode story editor."""
        from yeaboi.agent.state import (
            AcceptanceCriterion,
            Discipline,
            Priority,
            StoryPointValue,
            UserStory,
        )
        from yeaboi.ui.session.editor._editor import edit_story

        # Convert dict → UserStory
        acs = tuple(
            AcceptanceCriterion(given=ac.get("given", ""), when=ac.get("when", ""), then=ac.get("then", ""))
            for ac in story.get("acceptance_criteria", [])
            if isinstance(ac, dict)
        )
        pts_raw = story.get("story_points", 3)
        pts_val = pts_raw if pts_raw in (1, 2, 3, 5, 8) else 3
        pri_str = story.get("priority", "medium").lower()
        pri = Priority(pri_str) if pri_str in ("critical", "high", "medium", "low") else Priority.MEDIUM
        disc_str = story.get("discipline", "fullstack").lower()
        try:
            disc = Discipline(disc_str)
        except ValueError:
            disc = Discipline.FULLSTACK

        user_story = UserStory(
            id=story.get("id", "S1"),
            feature_id="F1",
            persona=story.get("persona", "user"),
            goal=story.get("goal", ""),
            benefit=story.get("benefit", ""),
            acceptance_criteria=acs,
            story_points=StoryPointValue(pts_val),
            priority=pri,
            title=story.get("title", ""),
            discipline=disc,
            points_rationale=story.get("rationale", ""),
        )

        w, h = console.size
        edited = edit_story(live, console, user_story, _rk, width=w, height=h)
        if edited is None:
            return None

        # Convert UserStory → dict (preserving extra keys from original)
        result = dict(story)
        result["title"] = edited.title
        result["persona"] = edited.persona
        result["goal"] = edited.goal
        result["benefit"] = edited.benefit
        result["story_points"] = int(edited.story_points)
        result["priority"] = edited.priority.value
        result["discipline"] = edited.discipline.value
        result["acceptance_criteria"] = [
            {"given": ac.given, "when": ac.when, "then": ac.then} for ac in edited.acceptance_criteria
        ]
        result["rationale"] = edited.points_rationale
        return result

    def _edit_task_dict(tasks_for_story: list[dict], story_id: str) -> list[dict] | None:
        """Edit tasks for a story using the planning mode task editor (with ANALYSIS title)."""
        from yeaboi.agent.state import Task
        from yeaboi.ui.session.editor._editor_artifacts import _find_first_editable, _task_editable_start
        from yeaboi.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel
        from yeaboi.ui.shared._components import analysis_title as _a_title

        task_objs = [
            Task(
                id=t.get("id", f"T-{story_id}-{i:02d}"),
                story_id=t.get("story_id", story_id),
                title=t.get("title", ""),
                description=t.get("description", ""),
                label=t.get("label", "Code"),
                test_plan=t.get("test_plan", ""),
            )
            for i, t in enumerate(tasks_for_story, 1)
        ]
        from yeaboi.ui.session.editor._editor_artifacts import _parse_edited_tasks, _tasks_to_text

        text = _tasks_to_text(task_objs)
        buffer = text.split("\n")
        cr, cc = _find_first_editable(buffer, _task_editable_start)
        _atitle = _a_title()

        def _render(buf, _cr, _cc, so, rw, rh):
            return render_editor_panel(
                buf,
                _cr,
                _cc,
                so,
                width=rw,
                height=rh,
                editor_label=f"tasks for {story_id}",
                title_override=_atitle,
            )

        result = edit_buffer_loop(
            live,
            console,
            buffer,
            cr,
            cc,
            _rk,
            editable_start_fn=_task_editable_start,
            render_fn=_render,
        )
        if result is None:
            return None
        edited_objs = _parse_edited_tasks("\n".join(result), task_objs)
        return [
            {**orig, "title": et.title, "description": et.description} for orig, et in zip(tasks_for_story, edited_objs)
        ]

    def _edit_epic_dict(epic: dict) -> dict | None:
        """Edit an epic dict using the planning mode feature editor (with ANALYSIS title)."""
        from yeaboi.agent.state import Feature, Priority
        from yeaboi.ui.session.editor._editor_artifacts import (
            _feature_editable_start,
            _features_to_text,
            _find_first_editable,
            _parse_edited_features,
        )
        from yeaboi.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel
        from yeaboi.ui.shared._components import analysis_title as _a_title

        pri_str = epic.get("priority", "high").lower()
        pri = Priority(pri_str) if pri_str in ("critical", "high", "medium", "low") else Priority.HIGH
        feature = Feature(
            id="F1",
            title=epic.get("title", ""),
            description=epic.get("description", ""),
            priority=pri,
        )
        text = _features_to_text([feature])
        buffer = text.split("\n")
        cr, cc = _find_first_editable(buffer, _feature_editable_start)
        _atitle = _a_title()

        def _render(buf, _cr, _cc, so, rw, rh):
            return render_editor_panel(
                buf,
                _cr,
                _cc,
                so,
                width=rw,
                height=rh,
                editor_label="epic",
                title_override=_atitle,
            )

        result = edit_buffer_loop(
            live,
            console,
            buffer,
            cr,
            cc,
            _rk,
            editable_start_fn=_feature_editable_start,
            render_fn=_render,
        )
        if result is None:
            return None
        edited_list = _parse_edited_features("\n".join(result), [feature])
        edited = edited_list[0]
        result_dict = dict(epic)
        result_dict["title"] = edited.title
        result_dict["description"] = edited.description
        result_dict["priority"] = edited.priority.value
        return result_dict

    def _regenerate(fn, label: str):
        """Run an LLM generation function in a background thread with animation."""

        logger.info("Regenerating %s via LLM", label)

        result_box: list = [None, None]

        def _worker():
            try:
                result_box[0] = fn()
            except Exception as exc:
                result_box[1] = exc

        thread = duck_working_thread(_worker, name="planning-regenerate")
        thread.start()
        start = time.monotonic()
        while thread.is_alive():
            elapsed = time.monotonic() - start
            w, h = console.size
            live.update(
                _build_analysis_progress_screen(
                    [f"Regenerating {label}\u2026"],
                    width=w,
                    height=h,
                    elapsed=elapsed,
                    anim_tick=elapsed,
                    source="",
                    mode="analysis",
                )
            )
            time.sleep(1 / 30)
        thread.join()
        if result_box[1] is not None:
            logger.warning("Regeneration failed: %s", result_box[1])
            return None
        logger.info("Regeneration complete: %s", label)
        return result_box[0]

    _flow_start = time.monotonic()
    last_page = (resume_state or {}).get("last_page", "")
    logger.info(
        "Preview flow started: resume=%s, last_page='%s'",
        resume_state is not None,
        last_page,
    )

    def _do_export():
        """Cumulative export — includes analysis profile + all accepted samples."""
        logger.info("Preview: exporting analysis")
        from yeaboi.agent.ceremony_history import gather_ceremony_context

        # Project-first here — the analysed project_key is known, so its retros sort ahead.
        ceremony = gather_ceremony_context(ta_profile.project_key)
        _team_profile_export_flow(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            profile=ta_profile,
            examples=ta_examples,
            ceremony=ceremony,
        )

    # Ensure we have a session ID for saving progress
    global _ana_sid  # noqa: PLW0603
    if not _ana_sid:
        try:
            from yeaboi.sessions import SessionStore, make_session_id

            _ana_sid = make_session_id()
            with SessionStore(_ana_dbp) as _s:
                _s.create_session(
                    _ana_sid,
                    project_name=getattr(ta_profile, "project_key", "") if ta_profile else "",
                    mode="analysis",
                )
            logger.info("Created analysis session for preview: %s", _ana_sid)
        except Exception:
            logger.debug("Failed to create analysis session", exc_info=True)

    # Determine starting point and load saved artifacts
    last_page = (resume_state or {}).get("last_page", "")
    _instr = (resume_state or {}).get("instructions", "") or instr_text
    _epic = (resume_state or {}).get("sample_epic")
    _stories = (resume_state or {}).get("sample_stories")
    _tasks = (resume_state or {}).get("sample_tasks")
    _sprint = (resume_state or {}).get("sample_sprint")

    # Scroll geometry published by each page's screen builder; reused across the
    # sequential pages (repopulated on every render before any key is handled).
    _scroll_meta: dict = {}

    # ── Page 1: Instructions ──────────────────────────────────────
    logger.info("Preview: entering Instructions page")
    if last_page not in ("epic", "stories", "tasks", "sprint"):
        scroll, sel = 0, 0
        _panel = None  # most recently rendered page panel, for click hit-testing
        _labels = ["Accept", "Edit", "Export"]
        while True:
            k = _rk()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _panel is None:
                    continue
                _idx = button_click(console, _panel, *_clicked, _labels)
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            if k in SCROLL_KEYS:
                _ns = coalesce_scroll(scroll, k, _scroll_meta, _rk)
                if _ns == scroll:
                    continue
                scroll = _ns
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(2, sel + 1)
            elif k in ("enter", " "):
                if sel == 0:
                    _save_ana({"instructions": _instr, "last_page": "instructions"}, "instructions")
                    break  # → epic
                elif sel == 1:
                    # Edit — inline buffer editor (matches planning mode)
                    logger.info("Preview: user editing instructions")
                    from yeaboi.ui.session.editor._editor_core import edit_buffer_loop, render_editor_panel
                    from yeaboi.ui.shared._components import analysis_title as _a_title

                    _buf = _instr.split("\n")
                    _cr, _cc = 0, 0
                    _atitle = _a_title()

                    def _instr_render(buf, cr, cc, so, rw, rh):
                        return render_editor_panel(
                            buf,
                            cr,
                            cc,
                            so,
                            width=rw,
                            height=rh,
                            editor_label="instructions",
                            title_override=_atitle,
                        )

                    _edited = edit_buffer_loop(
                        live,
                        console,
                        _buf,
                        _cr,
                        _cc,
                        _rk,
                        editable_start_fn=lambda line: 0,
                        render_fn=_instr_render,
                    )
                    if _edited is not None:
                        _instr = "\n".join(_edited)
                elif sel == 2:
                    _do_export()
            elif k in ("esc", "q"):
                _save_ana({"instructions": _instr, "last_page": "instructions"}, "instructions")
                return
            w, h = console.size
            _panel = _build_instructions_review_screen(
                _instr,
                scroll_offset=scroll,
                scroll_meta=_scroll_meta,
                width=w,
                height=h,
                action_sel=sel,
            )
            live.update(_panel)

    # ── Page 2: Epic ──────────────────────────────────────────────
    logger.info("Preview: entering Epic page")
    if not _epic:
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                ["Generating sample epic\u2026"],
                width=w,
                height=h,
                elapsed=0,
                anim_tick=0,
                source="",
                mode="analysis",
            )
        )
        logger.info("Preview: generating sample epic via LLM")
        result = _regenerate(lambda: generate_sample_epic(_instr, ta_examples), "epic")
        if result is not None:
            _epic = result
        logger.info("Preview: sample epic generated: %s", _epic.get("title", "?"))
        # Persist the moment generation completes — not only on the Accept/next
        # keypress — so quitting here still leaves a resumable session (matches the
        # Accept-handler save dict below).
        _save_ana({"instructions": _instr, "sample_epic": _epic, "last_page": "epic"}, "epic")

    if last_page not in ("stories", "tasks", "sprint"):
        scroll, sel = 0, 0
        _panel = None  # most recently rendered page panel, for click hit-testing
        _labels = ["Accept", "Edit", "Regenerate", "Export"]
        while True:
            k = _rk()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _panel is None:
                    continue
                _idx = button_click(console, _panel, *_clicked, _labels)
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            if k in SCROLL_KEYS:
                _ns = coalesce_scroll(scroll, k, _scroll_meta, _rk)
                if _ns == scroll:
                    continue
                scroll = _ns
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(3, sel + 1)
            elif k in ("enter", " "):
                if sel == 0:
                    _save_ana({"instructions": _instr, "sample_epic": _epic, "last_page": "epic"}, "epic")
                    break  # → stories
                elif sel == 1:
                    logger.info("Preview: user editing epic")
                    edited = _edit_epic_dict(_epic)
                    if edited is not None:
                        _epic = edited
                elif sel == 2:
                    # Ask what should change first (Esc cancels, empty Enter = plain regenerate).
                    fb = _ask_regen_feedback(console, live, read_key, frame_time, supports_timeout, "epic")
                    if fb is not None:
                        result = _regenerate(
                            lambda: generate_sample_epic(_instr, ta_examples, feedback=fb or None, previous=_epic),
                            "epic",
                        )
                        if result is not None:
                            _epic = result
                elif sel == 3:
                    _do_export()
            elif k in ("esc", "q"):
                _save_ana({"instructions": _instr, "sample_epic": _epic, "last_page": "epic"}, "epic")
                return
            w, h = console.size
            _panel = _build_sample_epic_screen(
                _epic,
                scroll_offset=scroll,
                scroll_meta=_scroll_meta,
                width=w,
                height=h,
                action_sel=sel,
                examples=ta_examples,
            )
            live.update(_panel)

    # ── Page 3: Stories ───────────────────────────────────────────
    logger.info("Preview: entering Stories page")
    if not _stories:
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                ["Generating sample stories\u2026"],
                width=w,
                height=h,
                elapsed=0,
                anim_tick=0,
                source="",
                mode="analysis",
            )
        )
        logger.info("Preview: generating sample stories via LLM")
        result = _regenerate(lambda: generate_sample_stories(_instr, _epic, ta_examples), "stories")
        if result is not None:
            _stories = result
        logger.info("Preview: %d sample stories generated", len(_stories))
        # Persist on generation (see Epic page) so a mid-flow quit stays resumable.
        _save_ana(
            {
                "instructions": _instr,
                "sample_epic": _epic,
                "sample_stories": _stories,
                "last_page": "stories",
            },
            "stories",
        )

    if last_page not in ("tasks", "sprint"):
        scroll, sel = 0, 0
        _panel = None  # most recently rendered page panel, for click hit-testing
        _labels = ["Accept", "Edit", "Regenerate", "Export"]
        while True:
            k = _rk()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _panel is None:
                    continue
                _idx = button_click(console, _panel, *_clicked, _labels)
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            if k in SCROLL_KEYS:
                _ns = coalesce_scroll(scroll, k, _scroll_meta, _rk)
                if _ns == scroll:
                    continue
                scroll = _ns
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(3, sel + 1)
            elif k in ("enter", " "):
                if sel == 0:
                    _st = {
                        "instructions": _instr,
                        "sample_epic": _epic,
                        "sample_stories": _stories,
                        "last_page": "stories",
                    }  # noqa: E501
                    _save_ana(_st, "stories")
                    break  # → tasks
                elif sel == 1:
                    logger.info("Preview: user editing stories")
                    for si, _s in enumerate(_stories):
                        edited = _edit_story_dict(_s)
                        if edited is not None:
                            _stories[si] = edited
                        else:
                            break  # Esc cancels remaining edits
                elif sel == 2:
                    fb = _ask_regen_feedback(console, live, read_key, frame_time, supports_timeout, "stories")
                    if fb is not None:
                        result = _regenerate(
                            lambda: generate_sample_stories(
                                _instr, _epic, ta_examples, feedback=fb or None, previous=_stories
                            ),
                            "stories",
                        )
                        if result is not None:
                            _stories = result
                elif sel == 3:
                    _do_export()
            elif k in ("esc", "q"):
                _save_ana(
                    {"instructions": _instr, "sample_epic": _epic, "sample_stories": _stories, "last_page": "stories"},
                    "stories",
                )
                return
            w, h = console.size
            _panel = _build_sample_stories_screen(
                _stories,
                scroll_offset=scroll,
                scroll_meta=_scroll_meta,
                width=w,
                height=h,
                action_sel=sel,
                epic_title=_epic.get("title", ""),
                examples=ta_examples,
            )
            live.update(_panel)

    # ── Page 4: Tasks ─────────────────────────────────────────────
    logger.info("Preview: entering Tasks page")
    if not _tasks:
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                ["Generating sample tasks\u2026"],
                width=w,
                height=h,
                elapsed=0,
                anim_tick=0,
                source="",
                mode="analysis",
            )
        )
        logger.info("Preview: generating sample tasks via LLM")
        result = _regenerate(lambda: generate_sample_tasks(_instr, _stories, ta_examples), "tasks")
        if result is not None:
            _tasks = result
        logger.info("Preview: %d sample tasks generated", len(_tasks))
        # Persist on generation (see Epic page) so a mid-flow quit stays resumable.
        _save_ana(
            {
                "instructions": _instr,
                "sample_epic": _epic,
                "sample_stories": _stories,
                "sample_tasks": _tasks,
                "last_page": "tasks",
            },
            "tasks",
        )

    if last_page != "sprint":
        scroll, sel = 0, 0
        _panel = None  # most recently rendered page panel, for click hit-testing
        _labels = ["Accept", "Edit", "Regenerate", "Export"]
        while True:
            k = _rk()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _panel is None:
                    continue
                _idx = button_click(console, _panel, *_clicked, _labels)
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            if k in SCROLL_KEYS:
                _ns = coalesce_scroll(scroll, k, _scroll_meta, _rk)
                if _ns == scroll:
                    continue
                scroll = _ns
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(3, sel + 1)
            elif k in ("enter", " "):
                if sel == 0:
                    _st = {
                        "instructions": _instr,
                        "sample_epic": _epic,
                        "sample_stories": _stories,
                        "sample_tasks": _tasks,
                        "last_page": "tasks",
                    }  # noqa: E501
                    _save_ana(_st, "tasks")
                    break  # → sprint
                elif sel == 1:
                    logger.info("Preview: user editing tasks")
                    # Group tasks by story and edit each group
                    _by_story: dict[str, list[tuple[int, dict]]] = {}
                    for ti, _t in enumerate(_tasks):
                        sid = _t.get("story_id", "?")
                        _by_story.setdefault(sid, []).append((ti, _t))
                    _cancelled = False
                    for sid, group in _by_story.items():
                        group_tasks = [t for _, t in group]
                        edited_group = _edit_task_dict(group_tasks, sid)
                        if edited_group is None:
                            _cancelled = True
                            break
                        for (ti, _), et in zip(group, edited_group):
                            _tasks[ti] = et
                elif sel == 2:
                    fb = _ask_regen_feedback(console, live, read_key, frame_time, supports_timeout, "tasks")
                    if fb is not None:
                        result = _regenerate(
                            lambda: generate_sample_tasks(
                                _instr, _stories, ta_examples, feedback=fb or None, previous=_tasks
                            ),
                            "tasks",
                        )
                        if result is not None:
                            _tasks = result
                elif sel == 3:
                    _do_export()
            elif k in ("esc", "q"):
                _save_ana(
                    {
                        "instructions": _instr,
                        "sample_epic": _epic,
                        "sample_stories": _stories,
                        "sample_tasks": _tasks,
                        "last_page": "tasks",
                    },
                    "tasks",
                )
                return
            w, h = console.size
            _panel = _build_sample_tasks_screen(
                _tasks,
                scroll_offset=scroll,
                scroll_meta=_scroll_meta,
                width=w,
                height=h,
                action_sel=sel,
                stories=_stories,
            )
            live.update(_panel)

    # ── Page 5: Sprint ────────────────────────────────────────────
    logger.info(
        "Preview: entering Sprint page (%.1fs elapsed)",
        time.monotonic() - _flow_start,
    )
    _finished = _run_sprint_review(
        live,
        console,
        read_key,
        frame_time,
        supports_timeout,
        _instr,
        _epic,
        _stories,
        _tasks,
        ta_examples,
        resume_sprint=_sprint,
    )
    # Only mark the session complete (non-resumable) when the user actually
    # finished the Sprint page. On a quit, _run_sprint_review has already saved
    # the sprint with last_page="sprint", so the analysis resumes straight here.
    if _finished:
        _save_ana({"last_page": "complete"}, "complete")
    logger.info(
        "Preview flow completed in %.1fs",
        time.monotonic() - _flow_start,
    )


def _run_sprint_review(
    live,
    console,
    read_key,
    frame_time,
    supports_timeout,
    instr_text,
    sample_epic,
    sample_stories,
    sample_tasks,
    ta_examples,
    resume_sprint=None,
):
    """Run the sample sprint review loop (extracted to reduce nesting depth).

    Returns True if the user finished the page (chose "Done"), False if they quit
    (Esc). The caller uses this to decide whether to mark the session complete.
    """
    logger.info("Sprint review: generating sample sprint via LLM")

    from yeaboi.tools.team_learning import generate_sample_sprint
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_analysis_progress_screen,
        _build_sample_sprint_screen,
    )

    def _save_sprint(sprint_obj: dict) -> None:
        # Persist the sprint the moment it is generated (see the Epic page), with
        # last_page="sprint" so a quit here resumes to this page without a re-run.
        _save_ana(
            {
                "instructions": instr_text,
                "sample_epic": sample_epic,
                "sample_stories": sample_stories,
                "sample_tasks": sample_tasks,
                "sample_sprint": sprint_obj,
                "last_page": "sprint",
            },
            "sprint",
        )

    def _regen_sprint(feedback=None, previous=None):
        result_box: list = [None, None]

        def _worker():
            try:
                result_box[0] = generate_sample_sprint(
                    instr_text, sample_stories, sample_tasks, ta_examples, feedback=feedback, previous=previous
                )
            except Exception as exc:
                result_box[1] = exc

        thread = duck_working_thread(_worker, name="sprint-sample-regenerate")
        thread.start()
        start = time.monotonic()
        while thread.is_alive():
            elapsed = time.monotonic() - start
            w, h = console.size
            live.update(
                _build_analysis_progress_screen(
                    ["Regenerating sprint\u2026"],
                    width=w,
                    height=h,
                    elapsed=elapsed,
                    anim_tick=elapsed,
                    source="",
                    mode="analysis",
                )
            )
            time.sleep(1 / 30)
        thread.join()
        if result_box[1] is not None:
            logger.warning("Sprint regeneration failed: %s", result_box[1])
            return None
        return result_box[0]

    if resume_sprint:
        # Resumed session — reuse the saved sprint, skip the (expensive) LLM call.
        sprint = resume_sprint
    else:
        sprint = _regen_sprint() or {
            "sprint_name": "Sprint 1",
            "velocity_target": 20,
            "stories_included": [s.get("id", "") for s in sample_stories],
            "total_points": sum(s.get("story_points", 0) for s in sample_stories),
            "capacity_notes": "Fallback — generation failed.",
            "risks": [],
            "rationale": "Fallback sprint plan.",
        }
        _save_sprint(sprint)
    scroll = 0
    sel = 0
    _scroll_meta: dict = {}
    _panel = None  # most recently rendered sprint panel, for click hit-testing
    _labels = ["Done", "Regenerate", "Export"]
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(k)
        if _clicked is not None:
            if _panel is None:
                continue
            _idx = button_click(console, _panel, *_clicked, _labels)
            if _idx is None:
                continue  # click missed the buttons — ignore it
            sel = _idx
            k = "enter"  # fall through to the existing Enter handling
        if k in SCROLL_KEYS:
            _ns = coalesce_scroll(scroll, k, _scroll_meta, read_key)
            if _ns == scroll:
                continue
            scroll = _ns
        elif k == "left":
            sel = max(0, sel - 1)
        elif k == "right":
            sel = min(2, sel + 1)
        elif k in ("enter", " "):
            if sel == 0:
                return True  # Done — caller marks the session complete
            elif sel == 1:
                # Ask what should change first (Esc cancels, empty Enter = plain regenerate).
                fb = _ask_regen_feedback(console, live, read_key, frame_time, supports_timeout, "sprint")
                if fb is not None:
                    result = _regen_sprint(feedback=fb or None, previous=sprint)
                    if result is not None:
                        sprint = result
                        _save_sprint(sprint)
            elif sel == 2:
                pass  # Export (handled at report level)
        elif k in ("esc", "q"):
            return False  # Quit — keep last_page="sprint" so it stays resumable
        w, h = console.size
        _panel = _build_sample_sprint_screen(
            sprint,
            sample_stories,
            scroll_offset=scroll,
            scroll_meta=_scroll_meta,
            width=w,
            height=h,
            action_sel=sel,
        )
        live.update(_panel)


def _collect_usage_data() -> dict:
    """Gather usage statistics for the Usage dashboard page."""
    import os
    import sys

    data: dict = {}

    # Provider info
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    model = os.environ.get("LLM_MODEL", "")
    if not model:
        # Single source of truth — was a drifting local copy before ollama landed.
        from yeaboi.agent.llm import _PROVIDER_DEFAULTS

        model = _PROVIDER_DEFAULTS.get(provider, "unknown")
    data["provider"] = provider
    data["model"] = model

    # API key status — is_llm_configured knows each provider's real requirement
    # (ollama needs none, bedrock accepts a profile without AWS_REGION, ...).
    from yeaboi.config import is_llm_configured

    _configured, _ = is_llm_configured()
    data["api_key_status"] = "configured" if _configured else "not configured"

    # Session history
    try:
        from yeaboi.sessions import SessionStore

        db_path = _ana_dbp
        with SessionStore(db_path) as store:
            all_sessions = store.list_sessions()
            analysis_sessions = store.list_analysis_sessions()
            planning_count = len(all_sessions) - len(analysis_sessions)
            last_used = all_sessions[0].get("last_modified", "") if all_sessions else ""
            data["sessions"] = {
                "total": len(all_sessions),
                "planning": planning_count,
                "analysis": len(analysis_sessions),
                "last_used": last_used[:19].replace("T", " ") if last_used else "",
            }
    except Exception:
        data["sessions"] = {"total": 0, "planning": 0, "analysis": 0}

    # Environment
    from yeaboi import __version__

    data["version"] = __version__
    data["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    from yeaboi.config import is_langsmith_enabled

    data["langsmith"] = "enabled" if is_langsmith_enabled() else "disabled"
    data["db_path"] = str(_ana_dbp)

    # Team profiles
    try:
        from yeaboi.team_profile import TeamProfileStore

        with TeamProfileStore(_ana_dbp) as ps:
            profiles = ps.list_profiles()
            data["profiles"] = [
                {
                    "name": getattr(p, "team_id", "?"),
                    "source": getattr(p, "source", "?"),
                    "sprints": getattr(p, "sample_sprints", 0),
                }
                for p in profiles
            ]
    except Exception:
        data["profiles"] = []

    # Token usage — session (in-memory) + lifetime (from DB). All rates come
    # from the shared pricing table; an unknown model prices at the Sonnet
    # tier, which matches the old hardcoded $3/$15 estimate.
    from yeaboi.pricing import estimate_cost

    def _cloud_cost(inp: int, out: int, model_id: str = "") -> float:
        return estimate_cost(model_id, inp, out).usd

    def _calc_cost(inp: int, out: int) -> float:
        # Ollama runs on the user's own hardware — there is no per-token bill.
        if provider == "ollama":
            return 0.0
        return round(_cloud_cost(inp, out, model), 4)

    try:
        from yeaboi.agent.llm import get_usage_stats

        stats = get_usage_stats()
        logger.info("Usage stats: %s", stats)
        if stats.get("call_count", 0) > 0:
            inp = stats.get("input_tokens", 0)
            out = stats.get("output_tokens", 0)
            data["tokens"] = {
                "input": inp,
                "output": out,
                "total": inp + out,
                "calls": stats.get("call_count", 0),
                "estimated_cost": _calc_cost(inp, out),
            }
        else:
            data["tokens"] = {}
    except Exception:
        data["tokens"] = {}

    # Lifetime usage from DB (persisted across all sessions)
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(_ana_dbp) as store:
            # Grouped by provider so mixed histories price correctly: ollama
            # rows are free, everything else (incl. legacy rows without a
            # provider stamp, which predate local mode) at the cloud estimate.
            by_provider = store.get_lifetime_usage_by_provider()
            lt_inp = sum(u["input_tokens"] for u in by_provider.values())
            lt_out = sum(u["output_tokens"] for u in by_provider.values())
            lt_calls = sum(u["call_count"] for u in by_provider.values())
            lt_cost = round(
                sum(
                    0.0 if prov == "ollama" else _cloud_cost(u["input_tokens"], u["output_tokens"])
                    for prov, u in by_provider.items()
                ),
                4,
            )
            if lt_calls > 0:
                data["lifetime_tokens"] = {
                    "input": lt_inp,
                    "output": lt_out,
                    "total": lt_inp + lt_out,
                    "calls": lt_calls,
                    "estimated_cost": lt_cost,
                }
            else:
                data["lifetime_tokens"] = {}
            # Local-model throughput/latency (empty for cloud-only histories).
            data["local_performance"] = store.get_local_perf_summary()
    except Exception:
        data["lifetime_tokens"] = {}
        data["local_performance"] = {}

    return data


def _collect_settings_data() -> dict:
    """Gather current configuration values for the Settings page."""
    import os

    from yeaboi.config import get_config_file

    data: dict[str, str] = {}
    # Read all known env vars
    _keys = [
        "LLM_PROVIDER",
        "LLM_MODEL",
        # How the Anthropic credential is obtained, and the subscription token the
        # "subscription" mode mints (the API key covers the other mode).
        "ANTHROPIC_AUTH_MODE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        # Ollama (local provider — server URL + requested context window)
        "OLLAMA_BASE_URL",
        "OLLAMA_NUM_CTX",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "JIRA_PROJECT_KEY",
        "CONFLUENCE_SPACE_KEY",
        # Notion (rendered by the settings screen; was missing from this list)
        "NOTION_TOKEN",
        "NOTION_ROOT_PAGE_ID",
        # Storage (Settings → Data Dir / Paths)
        "YEABOI_HOME",
        "YEABOI_ALLOWED_PATHS",
        "AZURE_DEVOPS_ORG_URL",
        "AZURE_DEVOPS_PROJECT",
        "AZURE_DEVOPS_TOKEN",
        "AZURE_DEVOPS_TEAM",
        "GITHUB_TOKEN",
        # Analysis' GitHub repository estate (comma-separated owners/orgs) — the
        # default that lets CLI/MCP/headless runs scan GitHub without --github-owner.
        "TEAM_ANALYSIS_GITHUB_OWNERS",
        "VOICE_MODEL",
        "VOICE_DEVICE",
        "AWS_REGION",
        "AWS_PROFILE",
        "LOG_LEVEL",
        "SESSION_PRUNE_DAYS",
        "TUNNEL_TIMEOUT_MINUTES",
        # The share tier and its Cloudflare Access configuration. None of these
        # is a secret — the tunnel's actual credential stays in a file on disk
        # that yeaboi only ever references by path.
        "YEABOI_SHARE_MODE",
        "CLOUDFLARE_TUNNEL_ID",
        "CLOUDFLARE_TUNNEL_CREDENTIALS",
        "CLOUDFLARE_ACCESS_HOSTNAME",
        "CLOUDFLARE_ACCESS_TEAM",
        "CLOUDFLARE_ACCESS_AUD",
        "CLOUDFLARE_ACCESS_ADMIN_EMAILS",
        "LANGSMITH_TRACING",
        "TIPS_ENABLED",
        "DUCK_ENABLED",
        # Slack — a provider now rather than a delivery detail. The webhook
        # posts; the bot token is what lets a reaction or a reply be read back.
        "SLACK_WEBHOOK_URL",
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
        "SLACK_ALLOWED_MEMBER_IDS",
        # Daily Standup delivery config (secrets masked by the settings screen)
        "STANDUP_GITHUB_REPO",
        "STANDUP_SMTP_HOST",
        "STANDUP_SMTP_USER",
        "STANDUP_SMTP_PASSWORD",
        "STANDUP_EMAIL_RECIPIENTS",
    ]
    for k in _keys:
        data[k] = os.environ.get(k, "")
    data["_config_path"] = str(get_config_file())
    return data


def _launch_setup_wizard(console: Console, live) -> None:
    """Suspend the Live display, run the setup wizard, reload config, resume.

    Shared by Settings → Configure and the export picker's Open Setup hook.
    """
    logger.info("Launching setup wizard")
    live.stop()
    try:
        from yeaboi.setup_wizard import run_setup_wizard

        run_setup_wizard(console)
        from yeaboi.config import load_user_config

        load_user_config()
        logger.info("Config reloaded after setup wizard")
    finally:
        live.start()


# How long the sign-in result ignores input before a key may dismiss it. Long
# enough to swallow the tail of a paste or a double-tapped Enter, short enough
# that nobody waiting to press a key notices it.
_ACK_SETTLE_SECONDS = 0.35


def _run_subscription_sign_in(
    console: Console, live, read_key, frame_time, supports_timeout, render_page
) -> tuple[str, str]:
    """Drive `claude setup-token` to a token in the duck's speech bubble.

    The CLI runs on a pty this process owns, so its browser flow is drawn as a
    bubble over the page rather than at a bare shell prompt — and ``render_page``
    keeps drawing the settings underneath, so the screen the user was on stays
    exactly where it was. Returns ``(token, message)``; the token is empty on every
    cancel and failure path.

    The code field reuses ``_settings_edit_keypress`` — the same buffer/cursor
    editor every settings row uses — so paste, arrows and backspace behave here
    exactly as they do on the page behind it.
    """
    from functools import partial

    from yeaboi.claude_auth import SubscriptionSignIn
    from yeaboi.ui.mode_select.screens._screens import _draw_signin_bubble
    from yeaboi.ui.shared._input import drain_pending_input, set_text_entry
    from yeaboi.ui.shared._screensaver import suppress_screensaver

    spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    session = SubscriptionSignIn()
    edit = {"buf": "", "cur": 0}
    copied = False
    frame = 0

    def _render() -> None:
        panel = render_page()
        panel._overlay = partial(
            _draw_signin_bubble,
            state={
                "url": session.url,
                "spinner": spin[frame % len(spin)],
                "awaiting_code": session.awaiting_code,
                "code": edit["buf"],
                "cursor": edit["cur"],
                "copied": copied,
                "done": session.done,
                "ok": bool(session.token),
                "detail": session.error,
            },
        )
        live.update(panel)

    if not session.start():
        return "", session.message

    # The code is typed, so bare-key bindings must not eat the keystrokes; cleared
    # in `finally` alongside the child.
    set_text_entry(True)
    # A browser round-trip is long, and an idle screensaver over a half-finished
    # sign-in would lose the pasted code.
    with suppress_screensaver():
        try:
            while True:
                session.poll()
                if session.done:
                    break
                _render()
                frame += 1
                key = read_key(timeout=frame_time) if supports_timeout else read_key()
                if key == "esc":
                    logger.info("Subscription sign-in: cancelled")
                    return "", "Sign-in cancelled"
                if key == "enter":
                    if session.awaiting_code and edit["buf"].strip():
                        session.send_code(edit["buf"])
                        edit["buf"], edit["cur"] = "", 0
                    continue
                if key == "tab" and session.url:
                    # Tab, not `c`: the code field is live for almost the whole of
                    # this screen's life, and there `c` is a character of the code.
                    from yeaboi.clipboard import copy_text

                    copied = bool(copy_text(session.url))
                    continue
                if key:
                    _settings_edit_keypress(key, edit)
            # Result frame, then acknowledge — so a success is seen rather than
            # flashing past. Nothing typed BEFORE the result appeared may dismiss
            # it: a pasted code can overrun its field, and a leftover keystroke
            # both skipped the confirmation and fell through to the settings row
            # underneath — where Enter means "sign in again", so saving a token
            # immediately started minting another one.
            _render()
            drain_pending_input()
            _settled_at = time.monotonic() + _ACK_SETTLE_SECONDS
            while True:
                _k = read_key(timeout=frame_time) if supports_timeout else read_key()
                if _k and time.monotonic() >= _settled_at:
                    break
        finally:
            set_text_entry(False)
            session.cancel()
            # Whatever was typed at the bubble stays with the bubble.
            drain_pending_input()

    logger.info("Subscription sign-in: %s", "token received" if session.token else "no token")
    return session.token, session.message


def _settings_edit_keypress(sk: str, edit: dict) -> None:
    """Apply one keystroke to an in-place settings edit buffer (mutates ``edit``).

    ``edit`` carries ``buf`` (the text) and ``cur`` (cursor index). Handles
    printable insert, backspace, cursor movement (left/right/home/end) and paste;
    Enter/Esc are handled by the caller. Unknown keys are ignored.
    """
    buf, cur = edit["buf"], edit["cur"]
    if sk == "backspace":
        if cur > 0:
            edit["buf"], edit["cur"] = buf[: cur - 1] + buf[cur:], cur - 1
    elif sk == "left":
        edit["cur"] = max(0, cur - 1)
    elif sk == "right":
        edit["cur"] = min(len(buf), cur + 1)
    elif sk == "home":
        edit["cur"] = 0
    elif sk == "end":
        edit["cur"] = len(buf)
    elif isinstance(sk, str) and sk.startswith("paste:"):
        txt = paste_payload(sk)
        edit["buf"], edit["cur"] = buf[:cur] + txt + buf[cur:], cur + len(txt)
    elif isinstance(sk, str) and len(sk) == 1 and sk.isprintable():
        edit["buf"], edit["cur"] = buf[:cur] + sk + buf[cur:], cur + 1


def _settings_save_data_dir(console: Console, live, read_key, frame_time, supports_timeout, value: str) -> str:
    """Persist an edited data directory (YEABOI_HOME), offering to move the tree.

    The path itself is typed on the Settings page like every other value — this is
    only the save half: a Move/Leave popup (the one thing that can't be an in-place
    edit, since relocating sessions/exports/logs needs a decision), then the write.
    ``value`` is the already-typed override, '' meaning back to ~/.yeaboi. Returns
    the status message for the duck.

    The write goes through ``set_data_dir``, NOT the generic ``apply_config_value``:
    YEABOI_HOME lives in the pinned bootstrap ~/.yeaboi/.env, because a config file
    that can relocate the tree can't live inside the tree it relocates.
    """
    from yeaboi.config import set_data_dir
    from yeaboi.paths import move_data_tree

    message = "Data directory saved — restart yeaboi to fully apply"
    new_root = Path(value).expanduser() if value else Path.home() / ".yeaboi"
    if _confirm_move_data(console, live, read_key, frame_time, supports_timeout, new_root):
        ok, move_msg = move_data_tree(new_root)
        logger.info("Settings: data move to %s → ok=%s (%s)", new_root, ok, move_msg)
        message = f"{move_msg}. Restart yeaboi to fully apply"
    set_data_dir(value)
    logger.info("Settings: data directory set to %r", value)
    return message


# How long the duck holds the result in his bubble before it closes itself.
_COMPOSE_RESULT_SECONDS = 2.6
# Eased presence (0→1) for the bubble's entrance and exit. Out is faster than in,
# so dismissing feels immediate while arriving still reads as the duck speaking up.
_COMPOSE_EASE_IN = 0.30
_COMPOSE_EASE_OUT = 0.42


_COMPOSE_FIELDS = 3  # 0 type, 1 area, 2 message


def _feedback_compose_key(
    key: str, compose: dict, *, console=None, live=None, read_key=None, render=None
) -> dict | None:
    """Apply one keystroke to the duck's feedback bubble.

    Three fields: Type, Area and the message. Up/Down move between them, Left/
    Right change a selector (or move the cursor in the message), Enter sends from
    anywhere and Esc cancels. Returns the (mutated) state, or None once the bubble
    should close. Sending runs on a daemon thread so the welcome screen keeps
    animating behind it; the caller polls it each frame (_feedback_compose_tick).

    ``console``/``live``/``read_key``/``render`` are only needed for the two rich
    text affordances the full form has and the bubble keeps: Ctrl+V screenshot
    paste and double-tap-Space dictation, both of which take over the screen while
    they run. Omit them and those keys are simply ignored.
    """
    import threading

    from yeaboi.feedback import FEEDBACK_AREAS, FEEDBACK_TYPES, submit_feedback
    from yeaboi.ui.shared._attachments import referenced_images

    if compose.get("closing"):
        return compose  # already on its way out
    if compose.get("thread") is not None or compose.get("done_at"):
        return compose  # in flight or showing its result — keys do nothing
    compose["notice"] = ""  # a one-off notice lasts until the next keypress
    if key == "esc":
        # The bubble eats the key, so the app-wide back tab must not fold away:
        # the Esc chokepoint armed its retract before we got a say.
        from yeaboi.ui.shared._music_bar import cancel_back_retract

        cancel_back_retract()
        set_text_entry(False)
        logger.info("feedback bubble: cancelled (%d chars)", len(compose["buf"]))
        compose["closing"] = True
        return compose
    if key == "enter":
        text = compose["buf"].strip()
        if not text:
            compose["closing"] = True  # nothing typed — Enter just closes it
            return compose
        kind = FEEDBACK_TYPES[compose["kind"] % len(FEEDBACK_TYPES)]
        area = FEEDBACK_AREAS[compose["area"] % len(FEEDBACK_AREAS)]
        title = text.splitlines()[0][:80]  # opening line, so the issue is scannable
        out: list = []
        compose["status"] = "sending…"
        images = referenced_images(text, compose.get("attachments") or [])
        compose["thread"] = threading.Thread(
            target=lambda: out.append(submit_feedback(kind, area, title, text, images)),
            daemon=True,
        )
        compose["out"] = out
        compose["thread"].start()
        set_text_entry(False)
        logger.info("feedback bubble: submitting %s/%s (%d chars, %d image(s))", kind, area, len(text), len(images))
        return compose
    if key in ("up", "down"):
        step = 1 if key == "down" else -1
        compose["field"] = (compose.get("field", 2) + step) % _COMPOSE_FIELDS
        return compose
    field = compose.get("field", 2)
    if field < 2:  # a selector: Left/Right cycle it, everything else is ignored
        if key in ("left", "right"):
            values = FEEDBACK_TYPES if field == 0 else FEEDBACK_AREAS
            key_name = "kind" if field == 0 else "area"
            compose[key_name] = (compose[key_name] + (1 if key == "right" else -1)) % len(values)
        return compose
    if key == "ctrl+v" and render is not None:
        # Screenshot paste, exactly as the full form does it: the image is saved
        # under ~/.yeaboi/attachments/ and an [image #N] chip goes in the text.
        from yeaboi.ui.shared._attachments import handle_ctrl_v

        compose["status"] = "pasting image…"
        render()
        compose["status"] = ""
        chip = handle_ctrl_v(
            compose["attachments"], scope_id="feedback", set_notice=lambda m: compose.__setitem__("notice", m)
        )
        if chip:
            buf, cur = compose["buf"], compose["cur"]
            compose["buf"], compose["cur"] = buf[:cur] + chip + buf[cur:], cur + len(chip)
            compose["notice"] = f"screenshot attached as {chip}"
        return compose
    _before_cursor = compose["buf"][: compose["cur"]]
    if key == " " and read_key is not None and compose["dts"].is_double(_before_cursor.endswith(" "), time.monotonic()):
        # Double-tap Space → dictate. The first space is already in the buffer and
        # stays as the separator; the transcript is appended after it.
        from yeaboi.ui.shared._voice_input import record_voice_input

        spoken = record_voice_input(
            live, console, read_key, lambda border, line: _compose_voice_frame(compose, line, render)
        )
        compose["status"] = ""
        if spoken:
            buf, cur = compose["buf"], compose["cur"]
            text = spoken.replace("\n", " ")
            compose["buf"], compose["cur"] = buf[:cur] + text + buf[cur:], cur + len(text)
        return compose
    _settings_edit_keypress(key, compose)  # shares the settings buffer/cursor editor
    return compose


def _compose_voice_frame(compose: dict, line: str, render):
    """Renderable for the recording/transcribing indicator, shown IN the bubble.

    record_voice_input owns the screen while it runs, so it paints through this —
    keeping the duck and his bubble on screen instead of a centred popup. The
    bubble has no border of its own to tint, so only the status line is used.
    """
    compose["status"] = line.strip()
    return render(update=False)


def _feedback_compose_tick(compose: dict) -> dict | None:
    """Advance the bubble one frame: its entrance/exit, and any send in flight.

    Returns None once it has finished fading out. Closing is a two-step — the key
    handler asks for it, the animation finishes it — so Esc doesn't make the
    bubble vanish between frames.
    """
    closing = compose.get("closing", False)
    target = 0.0 if closing else 1.0
    ease = _COMPOSE_EASE_OUT if closing else _COMPOSE_EASE_IN
    compose["presence"] += (target - compose["presence"]) * ease
    if closing and compose["presence"] < 0.03:
        return None  # faded out — drop it
    thread = compose.get("thread")
    if thread is not None and not thread.is_alive():
        result = (compose.get("out") or [None])[0]
        compose["thread"] = None
        compose["done_at"] = time.monotonic()
        set_text_entry(False)
        if result is None:
            compose["status"] = "couldn't send — try f again"
        elif result.ok:
            compose["status"] = "sent — thank you!"
        else:
            compose["status"] = result.message or "opened in your browser"
        logger.info("feedback bubble: %s", compose["status"])
    done_at = compose.get("done_at")
    if done_at and time.monotonic() - done_at > _COMPOSE_RESULT_SECONDS:
        compose["closing"] = True  # start the fade; the next tick finishes it
    return compose


def _settings_save_allowed_paths(value: str) -> str:
    """Persist an edited sandbox whitelist (YEABOI_ALLOWED_PATHS).

    The path list is typed on the Settings page like every other value; this is
    the save half. It goes through ``set_allowed_paths`` rather than the generic
    ``apply_config_value`` because that setter dedups and writes the pinned
    bootstrap ~/.yeaboi/.env — the whitelist has to survive relocating the data
    tree it guards. ``value`` is the raw comma-separated text; returns the status
    message for the duck.
    """
    from yeaboi.config import set_allowed_paths

    paths = [p.strip() for p in value.split(",") if p.strip()]
    set_allowed_paths(paths)
    logger.info("Settings: allowed paths whitelist edited → %r", paths)
    if not paths:
        return "Allowed paths cleared — yeaboi is sandboxed to its data directory"
    return f"Allowed paths saved — {len(paths)} path(s) whitelisted"


def _confirm_move_data(console: Console, live, read_key, frame_time, supports_timeout, new_root: Path) -> bool:
    """Move/Leave popup shown after the data directory changes; True = move."""
    from rich.align import Align
    from rich.console import Group
    from rich.text import Text

    from yeaboi.ui.shared._components import (
        PAD,
        SETTINGS_THEME,
        build_action_buttons,
        build_page_panel,
        build_popup,
        settings_title,
    )

    sel = 0
    while True:
        w, h = console.size
        lines: list = [Text(""), settings_title(width=w), Text("")]
        lines.append(Text(PAD + "Move existing data?", style="bold white", justify="left"))
        hint = "Move copies your sessions, exports and logs" if sel == 0 else "Start fresh — old data stays put"
        lines.append(Text(PAD + hint, style=SETTINGS_THEME.muted, justify="left"))
        lines.append(Text(""))
        lines.append(
            Align.center(
                build_popup(
                    f"Move existing data (sessions, exports, logs) to\n{new_root}?",
                    width=min(w - 8, 60),
                    border_style=SETTINGS_THEME.warn,
                )
            )
        )
        lines.append(Text(""))
        _labels = ["Move", "Leave"]
        btn_top, btn_mid, btn_bot = build_action_buttons(_labels, sel)
        lines += [btn_top, btn_mid, btn_bot]
        _panel = build_page_panel(Group(*lines), theme=SETTINGS_THEME, border_style=SETTINGS_THEME.sep, height=h)
        live.update(_panel)
        try:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
        except TypeError:
            k = read_key()
        if not k:  # idle tick / consumed mouse event
            continue
        _clicked = parse_click(k)
        if _clicked is not None:
            _idx = button_click(console, _panel, *_clicked, _labels)
            if _idx is not None:
                return _idx == 0
            continue
        if k == "left":
            sel = 0
        elif k == "right":
            sel = 1
        elif k in ("enter", " "):
            return sel == 0
        elif k in ("esc", "q"):
            return False


def _test_microphone(console: Console, live, read_key, frame_time, supports_timeout, device: dict, render) -> str:
    """Open the highlighted mic and animate its level until a key is pressed.

    This is the answer to "is this the input that actually hears me?" — the one
    question a device *name* cannot settle, since a laptop lid, a muted USB
    interface and a disconnected Bluetooth headset all still enumerate.
    Returns a notice for the picker ("" when the test ran fine).
    """
    from yeaboi import music, voice

    music.pause_for_voice()
    try:
        # monitor=True: this runs for as long as the user leaves the page open,
        # and a retained take grows ~11 MB a minute at 48 kHz stereo for a WAV
        # that is discarded the moment the test ends. Only level() is wanted.
        recorder = voice.Recorder(device=device["index"], monitor=True)
    except Exception as exc:  # noqa: BLE001 - shown to the user, not raised
        logger.warning("Settings: mic test failed for %s", device["name"], exc_info=True)
        music.resume_after_voice()
        return f"{device['name']} would not open — {exc}"
    logger.info("Settings: testing microphone %s (index %s)", device["name"], device["index"])
    try:
        while True:
            live.update(render(testing=True, level=recorder.level()))
            try:
                k = read_key(timeout=frame_time) if supports_timeout else read_key()
            except TypeError:
                k = read_key()
            if not k:
                continue
            if parse_click(k) is not None:
                continue  # a stray mouse event must not end the test
            if k == "esc":
                # Same reason as the picker's own cancel below: the Esc
                # chokepoint armed the app-wide back tab's retract before we saw
                # the key, and this Esc only ends the test.
                from yeaboi.ui.shared._music_bar import cancel_back_retract

                cancel_back_retract()
            logger.info("Settings: mic test finished for %s", device["name"])
            return ""
    finally:
        recorder.stop()
        music.resume_after_voice()


_ACCESS_STEPS = ["Sign in", "Hostname", "Access app", "Verify"]


def _run_access_setup(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> str | None:
    """Guided setup for the Cloudflare Access tier. Returns a status line, or None if cancelled.

    Structure is the analysis wizard's (`_WIZARD_STEPS`): a step table walked by
    an integer index, one entry per screen so "back" is only ever ``index -= 1``,
    with an applicability predicate that is transparent in both directions —
    backing over a step that was skipped forwards skips it backwards too.

    It departs from the standup wizard on one point, deliberately: **each fact is
    saved the moment it is known, not at the end.** These steps have side effects
    on a real Cloudflare account. Creating a tunnel and then losing its id to an
    Esc would leave an orphan this wizard cannot see it made.

    The screens are the existing option-list step screen and the themed line
    editor. Nothing here draws a new widget — a settings flow that grew its own
    screen family would also grow its own scrolling and back-navigation bugs.
    """
    import threading as _threading

    from yeaboi.config import SHARE_MODE_ACCESS
    from yeaboi.sharing import access_setup
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_schedule_step_screen
    from yeaboi.ui.shared._components import SETTINGS_THEME, settings_title

    # read_state() may download ~38 MB and re-hashes the cached binary, so it
    # runs on a worker like every other slow step, and its answer is cached:
    # _applicable() is consulted on every loop iteration.
    state = _run_on_worker(
        access_setup.read_state,
        lambda elapsed: live.update(
            _build_standup_schedule_step_screen(
                [("checking your setup…", "")],
                0,
                step_index=0,
                heading="Cloudflare Access",
                width=console.size[0],
                height=max(10, console.size[1] - 1),
                step_names=_ACCESS_STEPS,
                theme=SETTINGS_THEME,
                title_fn=settings_title,
            )
        ),
        frame_time,
    )
    if not state.binary:
        return "Could not obtain cloudflared — check the network and try again."

    picked: dict[str, str] = {}
    logger.info("access setup wizard: opened (logged_in=%s, jwt=%s)", state.logged_in, state.jwt_installed)

    def _work(heading: str, step_index: int, label: str, fn, *, detail=None):
        """Run one blocking cloudflared step on a worker while the page animates.

        The house rule, uniform with the voice install and the Ollama pull: the
        loop that draws is never the loop that waits, worker→UI state is a plain
        dict (single-key writes are atomic under the GIL), Esc sets an Event the
        child polls, and the frame time is floored here rather than trusted to
        the key reader.
        """
        prog = {"phrase": label}
        out: dict = {}
        cancel = _threading.Event()

        def _target() -> None:
            try:
                out["value"] = fn(lambda phrase: prog.__setitem__("phrase", phrase), cancel)
            except Exception as exc:  # noqa: BLE001 — a traceback here prints through the Live
                logger.error("access setup wizard: %s failed: %s", label, exc, exc_info=True)
                out["value"] = access_setup.Outcome(False, "That step failed — see the log.", "CRASH")

        thread = duck_working_thread(_target, name="access-setup")
        thread.start()
        while thread.is_alive():
            frame_started = time.monotonic()
            w, h = console.size
            live.update(
                _build_standup_schedule_step_screen(
                    [(prog["phrase"], (detail and detail()) or "Esc cancels")],
                    0,
                    step_index=step_index,
                    heading=heading,
                    width=w,
                    height=max(10, h - 1),
                    step_names=_ACCESS_STEPS,
                    theme=SETTINGS_THEME,
                    title_fn=settings_title,
                )
            )
            key = read_key(timeout=frame_time) if supports_timeout else None
            if key in ("esc", "q"):
                cancel.set()
            spent = time.monotonic() - frame_started
            if spent < frame_time:
                time.sleep(frame_time - spent)
        thread.join()
        return out.get("value")

    def _ask(prompt: str, step_index: int, *, default: str = "", validate=None, message: str = "") -> str | None:
        """One text step, re-prompting until ``validate`` accepts. None on Esc.

        Every prompt carries the step strip and a context paragraph: when an
        earlier step is skipped (already signed in), a text field can be the
        first thing the wizard shows, and a bare input box explains nothing.
        """
        notice = ""
        while True:
            value = _standup_read_line(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                prompt=f"{prompt} — {notice}" if notice else prompt,
                step=_ACCESS_STEPS[step_index],
                default=default,
                theme=SETTINGS_THEME,
                title=settings_title(),
                message=message,
                step_names=_ACCESS_STEPS,
                step_index=step_index,
            )
            if value is None:
                return None
            if validate is None or validate(value):
                return value.strip()
            notice = "That does not look right — try again."

    def _choice(options, step_index: int, heading: str, *, message: str = "") -> int | str:
        return _run_schedule_choice_step(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            options=options,
            initial=0,
            step_index=step_index,
            heading=heading,
            step_names=_ACCESS_STEPS,
            message=message,
            theme=SETTINGS_THEME,
            title_fn=settings_title,
        )

    # ── the steps ────────────────────────────────────────────────────────────────────

    def _step_login(_direction: int) -> str:
        # Say what the browser will ask for before opening it. cloudflared's
        # page is a bare zone picker with no explanation, and it needs a domain
        # already on Cloudflare — a prerequisite worth stating as a requirement
        # line here, with a way to go add one, rather than discovering it in a
        # tab with no obvious next action.
        while True:
            chosen = _choice(
                [
                    ("Open Cloudflare and sign in", "you will pick which of your domains to use"),
                    ("I don't have a domain on Cloudflare yet", "see how to add one — the free plan is enough"),
                    ("Back", "leave setup"),
                ],
                0,
                "Sign in to Cloudflare",
                message=(
                    "Requires: a Cloudflare account, and a domain added to it. "
                    "A browser tab will open — sign in, pick the domain you want boards "
                    "served under from the zone list, and press Authorize, then come back here."
                ),
            )
            if chosen == 1:
                try:
                    import webbrowser  # noqa: PLC0415 - matches the repo's other URL call sites

                    webbrowser.open(access_setup.ADD_SITE_URL)
                except Exception as e:  # noqa: BLE001 - a browser that will not open must not fail the screen
                    logger.warning("access setup wizard: could not open the add-site page: %s", e)
                _choice(
                    [("Back", "return to the sign-in step")],
                    0,
                    "Add a domain to Cloudflare",
                    message=(
                        f"At {access_setup.ADD_SITE_URL} (opened in your browser): press Add a site, "
                        "enter your domain — the free plan is enough — then update the nameservers "
                        "at your registrar as the dashboard instructs. Once the domain shows "
                        "Active, come back here and sign in."
                    ),
                )
                continue
            if chosen != 0:
                return "back"
            break
        # The URL is shown as a persistent detail line so it survives every
        # narrated status change — a browser that did not open leaves this as
        # the only way through.
        seen: dict[str, str] = {"url": ""}
        outcome = _work(
            "Sign in to Cloudflare",
            0,
            "waiting for you to authorize in the browser…",
            lambda on_line, cancel: access_setup.login(
                on_line=on_line, on_url=lambda u: seen.__setitem__("url", u), cancel=cancel
            ),
            detail=lambda: f"open this if your browser did not: {seen['url']}" if seen["url"] else "Esc cancels",
        )
        if outcome is None or not outcome.ok:
            if outcome is not None and outcome.code == "CANCELLED":
                return "back"
            if outcome is not None:
                # NO_CERT and friends carry their remedy — show it rather than
                # silently ending setup on a screen the user never saw.
                _choice([("Back", "")], 0, "Sign-in did not finish", message=outcome.message)
                return "back"
            return "stop"
        return "next"

    def _route(host: str) -> str:
        """Point DNS at the picked tunnel and store the hostname. "next" or "back"."""
        outcome = _work(
            "Hostname",
            1,
            "pointing DNS at the tunnel…",
            lambda on_line, cancel: access_setup.route_dns(picked["id"], host, on_line=on_line, cancel=cancel),
        )
        if outcome is not None and not outcome.ok:
            if outcome.code != "DNS_EXISTS":
                _choice([("Back", outcome.message)], 1, "Hostname")
                return "back"
            # The record may already point at this very tunnel (a re-run), or at
            # something else entirely. We refuse to overwrite either way, so the
            # only honest move is to say so and let the host decide.
            if (
                _choice(
                    [
                        ("Use it anyway", "the record already points at this tunnel"),
                        ("Pick another hostname", "it points somewhere else"),
                    ],
                    1,
                    "That hostname already has a DNS record",
                    message=outcome.message,
                )
                != 0
            ):
                return "back"
        picked["hostname"] = host.strip().lower()
        access_setup.save(CLOUDFLARE_ACCESS_HOSTNAME=picked["hostname"])
        return "next"

    def _step_hostname(_direction: int) -> str:
        """The one real question — everything after it is decided, not asked.

        The tunnel is `resolve_tunnel`'s pick (the only one, or the one named
        "yeaboi"), created when none exists; DNS is routed at it. The two
        screens that can still interrupt are safety rules, not questions: the
        picker when several tunnels exist and none is ours, and the
        DNS-collision choice, because a record is never overwritten silently.
        """
        from yeaboi.config import access_hostname

        saved = access_hostname()
        domain = _ask(
            "Your domain on Cloudflare (e.g. acme.com)",
            1,
            default=saved if saved.startswith(f"{access_setup.BOARDS_SUBDOMAIN}.") else "",
            validate=lambda v: access_setup.valid_hostname(access_setup.boards_hostname(v)),
            message=(
                "Boards will be served at boards.<your domain> — you own acme.com, they "
                "appear at https://boards.acme.com. Type just the domain; the boards. part "
                "is added for you, and the full hostname can be changed any time in "
                "Settings ▸ Sharing. yeaboi points it at a tunnel in your Cloudflare "
                "account (yours, or one it creates named 'yeaboi') and puts a verified "
                "sign-in in front."
            ),
        )
        if domain is None:
            return "back"
        host = access_setup.boards_hostname(domain)
        from yeaboi.config import access_credentials_file, access_tunnel_id

        if access_tunnel_id() and access_credentials_file():
            # The tunnel facts are already stored — this step is only running for
            # the hostname. Re-resolving could pick a *different* tunnel and
            # silently overwrite the stored id, so the stored one is kept;
            # changing tunnels is the Settings ▸ Sharing rows' job.
            picked["id"] = access_tunnel_id()
            return _route(host)
        found = _work(
            "Hostname", 1, "finding your tunnel…", lambda on_line, cancel: access_setup.list_tunnels(cancel=cancel)
        )
        tunnels, listed = found if isinstance(found, tuple) else ((), access_setup.Outcome(False, "Cancelled."))
        if not listed.ok:
            _choice([("Back", listed.message)], 1, "Hostname")
            return "back"
        info = access_setup.resolve_tunnel(tunnels)
        if info is None and tunnels:
            chosen = _choice([(t.name, t.id) for t in tunnels], 1, "Which tunnel should serve your boards?")
            if chosen == "back":
                return "back"
            info = tunnels[chosen]
        if info is None:
            created = _work(
                "Hostname",
                1,
                f"creating tunnel '{access_setup.DEFAULT_TUNNEL_NAME}'…",
                lambda on_line, cancel: access_setup.create_tunnel(
                    access_setup.DEFAULT_TUNNEL_NAME, on_line=on_line, cancel=cancel
                ),
            )
            # _work returns an Outcome (not a pair) if the step raised, so this
            # cannot unpack blindly — doing so would throw through the Live,
            # which is the failure _work's handler exists to prevent.
            if isinstance(created, tuple):
                info, outcome = created
            else:
                info, outcome = None, created
            if info is None:
                _choice([("Back", outcome.message if outcome else "Could not create the tunnel")], 1, "Hostname")
                return "back"
        credentials = info.credentials or access_setup.default_credentials(info.id)
        picked["id"] = info.id
        access_setup.save(CLOUDFLARE_TUNNEL_ID=info.id, CLOUDFLARE_TUNNEL_CREDENTIALS=credentials)
        return _route(host)

    def _step_app(_direction: int) -> str:
        """The one step yeaboi does not do for you, and why.

        Creating the Access application needs a zone-scoped Cloudflare API token
        that can also create tunnels and DNS records — a credential far more
        dangerous than anything this tier mints, and one yeaboi would then have
        to store. So this screen explains, waits, and takes the two values only
        the dashboard can mint. Admin emails are not asked — blank means no
        remote visitor gets host powers, and Settings ▸ Sharing grants them
        any time later.
        """
        from yeaboi.config import access_hostname

        host = picked.get("hostname") or access_hostname() or "your hostname"
        while True:
            chosen = _choice(
                [
                    ("Open the create-application form", "lands straight on Add self-hosted application"),
                    ("I've created it — continue", "paste its team name and AUD tag next"),
                    ("Back", "return to the previous step"),
                ],
                2,
                "Create the Access application in the Cloudflare dashboard",
                message=(
                    f"On the form that opens (Access controls → Applications → self-hosted):\n"
                    f"1. Name it anything, e.g. yeaboi boards; set the application domain to {host}.\n"
                    "2. In the policy: Action = Allow; under Include pick Emails and list your\n"
                    "   teammates' addresses — or pick 'Emails ending in' with @your-company.com\n"
                    "   to allow everyone at the company. Name the policy anything; save it.\n"
                    "3. Save the application, open its Overview tab, copy the Audience (AUD)\n"
                    "   tag, and come back here."
                ),
            )
            if chosen == 0:
                try:
                    import webbrowser  # noqa: PLC0415 - matches the repo's other URL call sites

                    webbrowser.open(access_setup.ACCESS_APP_ADD_URL)
                except Exception as e:  # noqa: BLE001 - a browser that will not open must not fail the screen
                    logger.warning("access setup wizard: could not open the Zero Trust dashboard: %s", e)
                continue
            if chosen != 1:
                return "back"
            break
        # The team name and AUD are not asked when they can be read: the
        # application just created makes the hostname's sign-in redirect carry both.
        found = _work(
            "Access app",
            2,
            f"reading your team name from {host}'s sign-in page…",
            lambda on_line, cancel: access_setup.discover_app(host),
        )
        team, aud_hint = found if isinstance(found, tuple) else ("", "")
        if not team:
            asked = _ask(
                "Your Zero Trust team name",
                2,
                message=(
                    "It could not be read from the hostname yet. It is the <team> part of "
                    "https://<team>.cloudflareaccess.com — shown in the Zero Trust dashboard "
                    "under Settings, and in the URL while you are there."
                ),
            )
            if asked is None:
                return "back"
            team = asked
        aud = _ask(
            "The application's AUD tag",
            2,
            default=aud_hint,
            message=(
                (
                    f"Team '{team}' and the tag below were detected from {host}'s sign-in "
                    "page. Check the tag matches the Audience (AUD) tag on the application's "
                    "Overview tab, then press Enter to accept it."
                )
                if aud_hint
                else (
                    (f"Team '{team}' detected from the sign-in redirect. " if team else "")
                    + "On the application you just created: its Overview page shows an "
                    "Application Audience (AUD) tag — a long string of letters and numbers. "
                    "Copy it and paste it here."
                )
            ),
        )
        if aud is None:
            return "back"
        access_setup.save(CLOUDFLARE_ACCESS_TEAM=team, CLOUDFLARE_ACCESS_AUD=aud)
        return "next"

    def _step_verify(_direction: int) -> str:
        # PyJWT never needs input, so it is a phase of Verify, not a step dot.
        if not access_setup.jwt_installed():
            outcome = _work(
                "Verify",
                3,
                "installing the verifier (PyJWT)…",
                lambda on_line, cancel: access_setup.install_jwt(on_line=on_line, cancel=cancel),
            )
            if outcome is None or not outcome.ok:
                _choice([("Back", outcome.message if outcome else "Install cancelled")], 3, "Verify")
                return "back"
        # Every other fact is stored the moment it is known. This one is the
        # switch: writing it before the tier is proven leaves every share
        # surface refusing to publish until the host finds Settings again.
        verdict = _work(
            "Verify", 3, "checking Cloudflare…", lambda on_line, cancel: access_setup.verify(assume_mode=True)
        )
        if verdict is None:
            return "back"
        if verdict.ok:
            access_setup.save(YEABOI_SHARE_MODE=SHARE_MODE_ACCESS)
        closing = (
            f"{verdict.message} Change any of this later in Settings ▸ Sharing." if verdict.ok else verdict.message
        )
        _choice(
            [("Done", closing)],
            3,
            "Verified" if verdict.ok else "Not ready yet",
            message=closing,
        )
        picked["result"] = closing
        return "done" if verdict.ok else "back"

    runners = (_step_login, _step_hostname, _step_app, _step_verify)

    def _applicable(index: int) -> bool:
        """Skipped steps are transparent in both directions — see the analysis wizard.

        A step whose facts were already stored (this or any earlier run) is
        skipped too, so re-entering the wizard resumes at the first missing
        thing — and a fully configured host goes straight to Verify. Judged
        against the state read at entry, so a step completed *during* this run
        stays reachable with Esc. Changing a stored value is the Settings ▸
        Sharing rows' job, which every skip-path screen names.
        """
        missing = set(state.missing_keys)
        if index == 0:
            return not (state.logged_in or access_setup.find_cert())
        if index == 1:
            return bool(
                missing & {"CLOUDFLARE_TUNNEL_ID", "CLOUDFLARE_TUNNEL_CREDENTIALS", "CLOUDFLARE_ACCESS_HOSTNAME"}
            )
        if index == 2:
            return bool(missing & {"CLOUDFLARE_ACCESS_TEAM", "CLOUDFLARE_ACCESS_AUD"})
        return True

    index, direction = 0, 1
    while 0 <= index < len(runners):
        if not _applicable(index):
            index += direction
            continue
        outcome = runners[index](direction)
        logger.info("access setup wizard: step=%s outcome=%s", _ACCESS_STEPS[index], outcome)
        if outcome == "stop":
            return "Cloudflare Access setup stopped."
        if outcome == "done":
            return picked.get("result", "Cloudflare Access is set up.")
        direction = 1 if outcome == "next" else -1
        index += direction
    return None


def _maybe_offer_share_tier(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """One-time, right before the first share: which tier should links use?

    The moment a host first thinks about who can open a link is the moment the
    verified-users tier is worth one screen — buried in Settings it goes
    unfound. Any resolution (either choice, or Esc) persists the prompted flag,
    so the zero-setup default path pays this screen exactly once, ever.
    """
    from yeaboi.config import share_tier_prompted, tunnels_disabled

    if tunnels_disabled() or share_tier_prompted():
        return
    from yeaboi.sharing import access_setup
    from yeaboi.ui.shared._components import SETTINGS_THEME, settings_title

    logger.info("share tier prompt: first share, asking")
    chosen = _run_schedule_choice_step(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        options=[
            ("Anyone with the link + a join code", "zero setup — the default"),
            ("Only verified users", "needs a domain on Cloudflare (~3 min setup)"),
        ],
        initial=0,
        step_index=0,
        heading="Who should be able to open your shared boards?",
        step_names=["Sharing"],
        message="Asked once, before your first share. Change it any time in Settings ▸ Sharing.",
        theme=SETTINGS_THEME,
        title_fn=settings_title,
    )
    access_setup.save(YEABOI_SHARE_TIER_PROMPTED="1")
    logger.info("share tier prompt: answered %s", chosen)
    if chosen == 1:
        result = _run_access_setup(console, live, read_key, frame_time, supports_timeout)
        logger.info("share tier prompt: setup result=%s", result)


def _pick_voice_device(console: Console, live, read_key, frame_time, supports_timeout) -> str | None:
    """Microphone picker for Settings → Voice Input.

    Returns the chosen device name, ``""`` for "use the system default", or None
    when the user backs out. A modal sub-loop (like _confirm_move_data) rather
    than another state in the settings loop: the settings loop already routes
    arrows, scroll, Tab and Esc, and a picker layered into it would have to be
    intercepted ahead of every one of them.
    """
    from yeaboi import voice
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_voice_device_screen, voice_picker_keypress

    # Rescan first: PortAudio caches its device list at startup, so the mic the
    # user just plugged in is exactly the one that would otherwise be missing.
    voice.refresh_devices()
    devices = voice.list_input_devices()
    pref = voice.get_voice_device()
    current = voice.device_name(voice.resolve_device(pref)) if pref else ""
    state = {"devices": devices, "sel": 0}
    for _i, _d in enumerate(devices):
        if _d["name"] == current:
            state["sel"] = _i
    notice = ""
    logger.info("Settings: microphone picker opened — %d input(s)", len(devices))

    def _render(testing: bool = False, level: float = 0.0):
        w, h = console.size
        return _build_voice_device_screen(
            devices,
            state["sel"],
            current=current,
            width=w,
            height=h,
            testing=testing,
            level=level,
            notice=notice,
        )

    while True:
        live.update(_render())
        try:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
        except TypeError:
            k = read_key()
        if not k:  # idle tick
            continue
        if parse_click(k) is not None:
            continue  # the picker is keyboard-driven; clicks would need regions
        notice = ""
        action = voice_picker_keypress(k, state)
        if action == "cancel":
            # The Esc chokepoint armed the app-wide back tab's retract before we
            # saw the key; this Esc only closes the picker, so put it back.
            from yeaboi.ui.shared._music_bar import cancel_back_retract

            cancel_back_retract()
            return None
        if action == "system":
            return ""
        if action == "select":
            if not devices:
                # Nothing to choose. Returning "" here would quietly save
                # "system default" from a page that just said there is no
                # microphone at all — a confirmation for a choice never made.
                notice = "No microphone to select. Esc goes back."
                continue
            return devices[state["sel"]]["name"]
        if action == "test" and devices:
            notice = _test_microphone(
                console, live, read_key, frame_time, supports_timeout, devices[state["sel"]], _render
            )


def _confirm_stop_ollama(console: Console, live, read_key, frame_time, supports_timeout) -> bool:
    """Stop/Leave popup shown when quitting with a local Ollama server up.

    True = stop the server. yeaboi didn't start the server, so this is an
    offer, never automatic — Esc/q leaves it running.
    """
    from rich.align import Align
    from rich.console import Group
    from rich.text import Text

    from yeaboi.ui.shared._components import SETTINGS_THEME, build_action_buttons, build_page_panel, build_popup

    sel = 0
    while True:
        w, h = console.size
        lines: list = [Text("")] * max(1, (h - 12) // 2)
        lines.append(
            Align.center(
                build_popup(
                    "The local Ollama server is still running.\nStop it before quitting? (frees ~5 GB RAM)",
                    width=min(w - 8, 56),
                    border_style=SETTINGS_THEME.warn,
                )
            )
        )
        lines.append(Text(""))
        _labels = ["Stop", "Leave"]
        btn_top, btn_mid, btn_bot = build_action_buttons(_labels, sel)
        lines += [btn_top, btn_mid, btn_bot]
        _panel = build_page_panel(Group(*lines), theme=SETTINGS_THEME, border_style=SETTINGS_THEME.sep, height=h)
        live.update(_panel)
        try:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
        except TypeError:
            k = read_key()
        if not k:  # idle tick / consumed mouse event
            continue
        _clicked = parse_click(k)
        if _clicked is not None:
            _idx = button_click(console, _panel, *_clicked, _labels)
            if _idx is not None:
                return _idx == 0
            continue
        if k == "left":
            sel = 0
        elif k == "right":
            sel = 1
        elif k in ("enter", " "):
            return sel == 0
        elif k in ("esc", "q"):
            return False


def _collect_standup_data(message: str = "") -> dict:
    """Gather Daily Standup dashboard data for the most recent session.

    The standup page targets the most recently modified session. Returns the
    session name, saved standup config, OS-schedule status, and the latest
    generated StandupReport (if any).
    """
    from yeaboi.config import get_standup_user_name

    data: dict = {
        "message": message,
        "session_id": "",
        "session_name": "",
        "my_name": get_standup_user_name(),
        "config": None,
        "report": None,
        "schedule": {},
        "review": None,
        "gap_issues": [],
    }
    try:
        from yeaboi.sessions import SessionStore, make_display_name

        with SessionStore(_ana_dbp) as store:
            session_id = store.get_latest_session_id()
            if not session_id:
                return data
            data["session_id"] = session_id
            meta = store.get_session(session_id) or {}
            data["session_name"] = make_display_name(meta) if meta else session_id
    except Exception:
        logger.warning("standup: failed to resolve latest session", exc_info=True)
        return data

    session_id = data["session_id"]
    try:
        from yeaboi.standup.store import StandupStore

        with StandupStore(_ana_dbp) as store:
            data["config"] = store.load_config(session_id)
            data["report"] = store.get_latest_report(session_id)
            # The most recent transcript review + the gap→issue ledger, so the
            # Transcript Review card can show which gaps are already filed.
            data["review"] = store.get_latest_review(session_id)
            data["gap_issues"] = store.get_gap_issues(limit=50)
        # Two indexed SELECTs, once per hub refresh (never per frame): which
        # standups ran without ever being checked against their meeting.
        from yeaboi.standup import transcripts as _transcripts

        data["nudge"] = _transcripts.transcript_nudge(session_id, config=data.get("config"), db_path=_ana_dbp)
        # The engine resolves "Me" to the user's real tracker identity (e.g. their
        # Jira displayName) — the report's my_name drives the "My Update" row.
        if data["report"] is not None and data["report"].my_name:
            data["my_name"] = data["report"].my_name
    except Exception:
        logger.warning("standup: failed to load standup store data", exc_info=True)
    try:
        from yeaboi.standup.scheduler import get_schedule_status

        data["schedule"] = get_schedule_status(session_id)
    except Exception:
        logger.warning("standup: failed to read schedule status", exc_info=True)
    return data


def _standup_generate(session_id: str, on_progress=None) -> str:
    """Run a standup for preview (no delivery) and return a status message."""
    try:
        from yeaboi.standup.engine import run_standup

        report = run_standup(session_id, deliver=False, dry_run=True, on_progress=on_progress)
        warn = f" · {len(report.warnings)} notice(s)" if report.warnings else ""
        logger.info(
            "standup: generated report — day %s/%s, %d notice(s) (session=%s)",
            report.sprint_day,
            report.sprint_total_days,
            len(report.warnings),
            session_id,
        )
        return f"Generated — day {report.sprint_day}/{report.sprint_total_days}, {report.confidence_label}{warn}."
    except Exception as e:
        logger.error("standup: generate failed: %s", e, exc_info=True)
        return f"Generate failed: {e}"


def _pick_dest(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    mode: str,
    extra_options: list[str] | None = None,
) -> str | None:
    """Open the shared export-destination picker; returns the key or None.

    Passes an ``open_setup`` hook so the blocked-destination warning can jump
    straight into the setup wizard and resume the export.
    """
    from yeaboi.ui.shared._export_picker import pick_export_destination

    return pick_export_destination(
        live,
        console,
        read_key,
        frame_time,
        supports_timeout,
        mode=mode,
        extra_options=extra_options,
        open_setup=lambda: _launch_setup_wizard(console, live),
    )


def _export_via_picker(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    mode: str,
    files_export,
    get_document,
    extra_options: list[str] | None = None,
    extra_handlers: dict | None = None,
) -> str | None:
    """Run the shared destination picker and dispatch the chosen export.

    files_export() -> str runs the existing on-disk Markdown+HTML export;
    get_document() -> (title, markdown) | str supplies the content for
    Notion/Confluence publishing (a plain string is an error message shown
    as-is, e.g. "Nothing to export yet"). Returns the status message to show,
    or None when the user backed out of the picker (caller leaves the page
    message unchanged).
    """
    dest = _pick_dest(console, live, read_key, frame_time, supports_timeout, mode=mode, extra_options=extra_options)
    if dest is None:
        return None
    if dest == "files":
        msg = files_export()
        # Some exporters report failure/no-op as a message rather than raising
        # ("Nothing to export yet…", "Export failed: …") — only a real export
        # ("Exported to …") earns the quack.
        if msg and "Exported" in msg:
            _duck_react("export_done")
        return msg
    if extra_handlers and dest in extra_handlers:
        return extra_handlers[dest]()

    doc = get_document()
    if isinstance(doc, str):
        return doc  # error / nothing-to-export message — surface as-is (also the copy case)
    title, markdown = doc
    if dest == "copy":
        from yeaboi.clipboard import copy_markdown_status

        return copy_markdown_status(markdown)
    from yeaboi.export_targets import publish_markdown
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen

    _dest_label = {"notion": "Notion", "confluence": "Confluence"}.get(dest, dest)

    def _publish_frame(elapsed: float) -> None:
        w, h = console.size
        live.update(
            _build_standup_progress_screen(
                [f"Publishing to {_dest_label}"],
                width=w,
                height=max(10, h - 1),
                elapsed=elapsed,
                anim_tick=elapsed,
                label=f"Publishing to {_dest_label}",
            )
        )

    # On a worker: publishing is a network call that used to freeze the frame
    # loop (and the duck) until the page landed.
    published = _run_on_worker(
        lambda: publish_markdown(dest, title=title, markdown=markdown),
        _publish_frame,
        frame_time,
        drain=read_key if supports_timeout else None,
    )
    if published.ok:
        _duck_react("export_done")
    return published.message


_ANON_PICKER_MODES = {"planning", "analysis", "standup", "retro", "performance", "reporting"}


def _anonymize_files_export(result, *, title: str, project_name: str) -> str:
    """Write the masked copy to disk (Markdown + HTML); return a status message."""
    from yeaboi.anonymize.export import export_anonymized

    paths = export_anonymized(result, title=title, project_name=project_name)
    return f"Exported anonymized copy to {paths['markdown'].parent}  (Markdown + HTML)"


def _anon_note(anon) -> str:
    """The slim subtitle shown under a mode's banner while its data is anonymized.

    Empty string when ``anon`` is None (real data) so the screen builder renders exactly
    as before; a count-carrying line otherwise.
    """
    if anon is None:
        return ""
    return f"Anonymized · {len(anon.replacements)} masked — review before sharing"


def _run_anonymize_pass(
    console: Console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    markdown: str,
    instruction: str,
    project_name: str,
    source_mode: str,
    theme,
    title,
):
    """Run ``run_anonymize`` on a worker thread behind the consistent progress screen.

    This is the loading-screen half of the old ``_anonymize_flow``: the *review* is now
    the mode's own screen re-rendered from masked data (``anonymize.apply.mask_artifact`` /
    ``mask_lines``), not a separate raw-Markdown view. Returns the ``AnonymizedOutput``
    (the caller applies its ``.replacements`` to the native data) or None on failure —
    never raises, never crashes the TUI.

    # See docs: "Guardrails" — output masking for public sharing
    """

    from yeaboi.anonymize.engine import run_anonymize
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen

    logger.info("anonymize: running for mode=%s (%d chars)", source_mode, len(markdown or ""))
    progress: list[str] = ["Starting"]
    result_box: list = [None]

    def _worker() -> None:
        try:
            result_box[0] = run_anonymize(
                markdown,
                instruction=instruction,
                project_name=project_name,
                source_mode=source_mode,
                db_path=_ana_dbp,
                on_progress=progress.append,
            )
        except Exception as e:  # noqa: BLE001 — never crash the TUI; surface as a warning
            logger.error("anonymize worker failed: %s", e, exc_info=True)
            result_box[0] = e

    thread = duck_working_thread(_worker, name="anonymize")
    thread.start()
    start = time.monotonic()
    while thread.is_alive():
        elapsed = time.monotonic() - start
        w, h = console.size
        live.update(
            _build_standup_progress_screen(
                list(progress),
                width=w,
                height=max(10, h - 1),
                elapsed=elapsed,
                anim_tick=elapsed,
                theme=theme,
                title=title,
                label="Anonymizing output",
            )
        )
        time.sleep(1 / 30)
    thread.join()
    res = result_box[0]
    if res is not None and not isinstance(res, Exception):
        _duck_react("anonymize_done")
        return res
    return None


def _anon_export(
    console: Console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    anon,
    doc_title: str,
    markdown: str,
    project_name: str,
    source_mode: str,
) -> str | None:
    """Export / copy the *masked* document through the normal destination picker.

    Applies the anonymize replacements to the mode's export Markdown — so the written
    file, published page, and clipboard match exactly what's masked on screen — then
    routes it through the same Files / Notion / Confluence / Copy picker every mode uses.
    Returns the status message, or None if the user backed out of the picker.
    """
    from dataclasses import replace as _dc_replace

    from yeaboi.anonymize.apply import apply_replacements

    masked_md = apply_replacements(markdown, anon.replacements)
    masked_result = _dc_replace(anon, anonymized_text=masked_md)
    picker_mode = source_mode if source_mode in _ANON_PICKER_MODES else "planning"
    return _export_via_picker(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        mode=picker_mode,
        files_export=lambda: _anonymize_files_export(
            masked_result, title=doc_title, project_name=project_name or source_mode
        ),
        get_document=lambda: (f"{doc_title} (anonymized)", masked_md),
    )


def _team_profile_export_flow(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    profile,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    ceremony=None,
) -> None:
    """Shared team-profile export: picker → files or Notion/Confluence → success screen.

    Blocks on the success screen (min 1.5 s + a key press) like the previous
    inline export blocks did; returns immediately when the picker is cancelled.
    """
    dest = _pick_dest(console, live, read_key, frame_time, supports_timeout, mode="analysis")
    if dest is None:
        return
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_project_export_success_screen

    if dest == "files":
        from yeaboi.team_profile_exporter import export_team_profile_html, export_team_profile_md

        # Markdown first, so the HTML page can name it: the page is drawn in the
        # browser, and the Markdown is what someone with scripting off gets.
        md_path = export_team_profile_md(profile, examples=examples, sprint_names=sprint_names, ceremony=ceremony)
        html_path = export_team_profile_html(
            profile,
            examples=examples,
            sprint_names=sprint_names,
            ceremony=ceremony,
            markdown_name=md_path.name,
        )
        body = f"HTML  {html_path}\nMD    {md_path}"
        subtitle = "Team profile exported (HTML + MD)"
    elif dest == "copy":
        from yeaboi.clipboard import copy_markdown_status
        from yeaboi.team_profile_exporter import build_team_profile_markdown

        md = build_team_profile_markdown(profile, examples=examples, sprint_names=sprint_names, ceremony=ceremony)
        subtitle = copy_markdown_status(md)
        body = "Team profile Markdown copied — paste it anywhere."
    else:
        from yeaboi.export_targets import publish_markdown
        from yeaboi.paths import get_analysis_export_dir
        from yeaboi.team_profile_exporter import build_team_profile_markdown

        # charts_dir gives the velocity chart a real on-disk home so the
        # publish layer can upload it alongside the page.
        md = build_team_profile_markdown(
            profile,
            examples=examples,
            sprint_names=sprint_names,
            ceremony=ceremony,
            charts_dir=get_analysis_export_dir(profile.project_key),
        )
        result = publish_markdown(dest, title=f"Team Profile — {profile.source}/{profile.project_key}", markdown=md)
        body = result.url or result.message
        subtitle = result.message if result.ok else f"Export failed — {result.message}"

    w, h = console.size
    live.update(_build_project_export_success_screen(body, width=w, height=h, subtitle=subtitle, mode="analysis"))
    t0 = time.monotonic()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        if time.monotonic() - t0 > 1.5 and k:
            break


def _export_roadmap_via_picker(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    roadmap_id: int,
) -> str | None:
    """Export a saved roadmap via the shared destination picker.

    Files keeps the on-disk Markdown+HTML export (roadmap/export.py);
    Notion/Confluence publish the Markdown via publish_markdown. Returns the
    status message for the success screen, or None when the picker was
    cancelled. No Jira/AzDO extras — a roadmap is a document, not tickets.
    """
    from yeaboi.roadmap.store import RoadmapStore

    try:
        with RoadmapStore(_ana_dbp) as store:
            row = store.get_roadmap(roadmap_id)
    except Exception:
        logger.warning("roadmap: failed to load for export id=%s", roadmap_id, exc_info=True)
        row = None
    if row is None:
        return "Roadmap not found."
    analysis = row["analysis"]
    label = row.get("label") or row.get("source_label") or "(unnamed roadmap)"
    if analysis is None:
        return "Analyze this roadmap before exporting."

    def _files() -> str:
        try:
            from yeaboi.roadmap.export import export_roadmap

            out = export_roadmap(analysis, name=label)
            logger.info("roadmap: exported id=%s to files", roadmap_id)
            return f"HTML  {out['html']}\nMD    {out['markdown']}"
        except Exception:
            logger.error("roadmap: export failed for id=%s", roadmap_id, exc_info=True)
            return "Export failed — see the log."

    def _doc() -> tuple[str, str]:
        from yeaboi.roadmap.export import build_roadmap_markdown

        return (f"Roadmap — {label}", build_roadmap_markdown(analysis))

    def _share() -> str:
        from yeaboi.sharing.documents import roadmap_document
        from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

        _run_output_share_flow(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            document=roadmap_document(analysis),
            theme=PLANNING_THEME,
            title_fn=planning_title,
        )
        return "Online share closed."

    return _export_via_picker(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        mode="planning",
        files_export=_files,
        get_document=_doc,
        extra_options=["Share Online"],
        extra_handlers={"shareonline": _share},
    )


def _project_tracker_sync(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    project_id: str,
    action: str,
) -> str:
    """Full plan sync to Jira/Azure DevOps with a live progress screen.

    Extracted verbatim from the old project-card export submenu; returns the
    status message to show on the export success screen.
    """
    import threading

    from yeaboi.persistence import load_graph_state, save_graph_state, save_project_snapshot

    tracker_label = "Jira" if action == "jira" else "Azure DevOps"
    if action == "jira":
        from yeaboi.jira_sync import sync_all_to_jira as _sync_all_fn
    else:
        from yeaboi.azdevops_sync import sync_all_to_azdevops as _sync_all_fn

    gs = load_graph_state(project_id)
    if not gs:
        return "No saved state for this project"

    # Run sync in background thread with live progress
    _sync_result_box: list = [None, None]  # [result, error]
    _sync_state_box: list = [None]
    _sync_done = threading.Event()
    # Shared progress state: log of completed items + current active item
    _sync_log: list[str] = []
    _sync_current: list[str] = ["Starting..."]
    _sync_counter: list[int] = [0, 0]  # [current, total]

    def _on_sync_progress(current, total, desc):
        _sync_counter[0] = current
        _sync_counter[1] = total
        if _sync_current[0] and _sync_current[0] != "Starting...":
            _sync_log.append(f"  ✓ {_sync_current[0]}")
        _sync_current[0] = desc

    def _run_sync():
        try:
            r, s = _sync_all_fn(gs, on_progress=_on_sync_progress)
            _sync_result_box[0] = r
            _sync_state_box[0] = s
        except Exception as exc:
            _sync_result_box[1] = exc
        finally:
            _sync_done.set()

    _sync_thread = duck_working_thread(_run_sync, name="tracker-sync")
    _sync_thread.start()

    # Show live scrolling log while the thread runs
    while not _sync_done.is_set():
        w, h = console.size
        viewport_h = max(3, h - 12)
        visible_log = _sync_log[-viewport_h:] if _sync_log else []
        cur = _sync_counter[0]
        tot = _sync_counter[1]
        counter = f"[{cur}/{tot}]" if tot else ""
        active = f"  ▸ {counter} {_sync_current[0]}"
        display_lines = "\n".join([*visible_log, active])
        live.update(
            _build_project_export_success_screen(
                display_lines,
                width=w,
                height=h,
                subtitle=f"{tracker_label} sync",
                hint="",
            )
        )
        time.sleep(frame_time)
    _sync_thread.join()

    if _sync_result_box[1] is not None:
        from yeaboi.ui.session._utils import _classify_api_error

        _sync_err = _classify_api_error(_sync_result_box[1])
        return f"{tracker_label} sync failed: {_sync_err}"
    if _sync_result_box[0] is None:
        return f"{tracker_label} sync failed"

    sr = _sync_result_box[0]
    new_gs = _sync_state_box[0]
    if new_gs:
        save_graph_state(project_id, new_gs)
        save_project_snapshot(project_id, new_gs)
    _iters = getattr(sr, "sprints_created", None) or getattr(sr, "iterations_created", {})
    created = len(sr.stories_created) + len(sr.tasks_created) + len(_iters)
    skipped = sr.skipped
    errors = len(sr.errors)
    parts = []
    if created:
        parts.append(f"{created} created")
    if skipped:
        parts.append(f"{skipped} skipped")
    if errors:
        parts.append(f"{errors} errors")
    epic = getattr(sr, "epic_key", None) or getattr(sr, "epic_id", None) or ""
    prefix = f"Epic: {epic} — " if epic else ""
    summary = ", ".join(parts) or "Nothing to sync"
    if created and not sr.errors:
        _duck_react("sync_done")
    # Show first error for diagnosis
    if sr.errors:
        first_err = sr.errors[0][:80]
        summary += f"\n{first_err}"
        # Write all errors to log file for debugging
        _err_path = Path.home() / ".scrum-agent" / "jira-sync-errors.log"
        _err_path.write_text("\n".join(sr.errors), encoding="utf-8")
    return prefix + summary


def _standup_document(session_id: str, data: dict) -> tuple[str, str] | str:
    """Return (title, markdown) for the latest standup report, or an error message."""
    from yeaboi.standup.export import build_standup_markdown
    from yeaboi.standup.store import StandupStore

    with StandupStore(_ana_dbp) as store:
        report = store.get_latest_report(session_id)
    if report is None:
        return "Nothing to export yet — press Generate first."
    name = data.get("session_name", "") or session_id
    return f"Daily Standup — {report.date} — {name}", build_standup_markdown(report)


def _standup_export(session_id: str, data: dict) -> str:
    """Export the latest standup report as Markdown + HTML. Returns a status message."""
    from yeaboi.standup.export import export_standup
    from yeaboi.standup.store import StandupStore

    with StandupStore(_ana_dbp) as store:
        report = store.get_latest_report(session_id)
        history = store.get_history(session_id, limit=30) if report is not None else []
    if report is None:
        logger.info("standup export: nothing to export yet (session=%s)", session_id)
        return "Nothing to export yet — press Generate first."
    try:
        paths = export_standup(report, project_name=data.get("session_name", "") or session_id, history=history)
        logger.info("standup export: wrote Markdown + HTML to %s (session=%s)", paths["markdown"].parent, session_id)
        return f"Exported to {paths['markdown'].parent}  (Markdown + HTML)"
    except Exception as e:
        logger.error("standup export failed: %s", e, exc_info=True)
        return f"Export failed: {e}"


def _standup_generate_flow(
    console: Console, live, read_key, frame_time, supports_timeout, session_id: str
) -> str | None:
    """Offer the saved setup, confirm sources/team, ask for the user's update, then generate.

    Returns a status message on success, or None if the user cancelled — at the
    saved-setup gate or at the update prompt (no run either way). Source/member
    cancellation returns its explanatory message and also prevents a run.
    Pressing Enter with an empty update skips the self-report and generates with
    inference.
    """
    from datetime import date

    from yeaboi.config import get_standup_user_name
    from yeaboi.standup.store import StandupStore
    from yeaboi.ui.shared._attachments import referenced_images

    # Nothing here changes what the engine reads — it already resolves saved
    # config on its own. This only skips re-asking: when every applicable step
    # was confirmed on an earlier run, offer those answers instead of walking
    # the five pickers again.
    saved = _standup_saved_setup(session_id)
    reuse = False
    if saved is not None:
        source_id, saved_rows = saved
        choice = _run_standup_saved_setup_confirm(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            saved_rows,
        )
        logger.info("standup generate: saved setup -> %s (session=%s)", choice, session_id)
        if choice == "cancel":
            return None  # Esc/Back → cancel the whole Generate
        reuse = choice == "use"
        if reuse and source_id != session_id:
            # The answers came from an older standup session; make them this
            # session's own, so the engine and the page both see them.
            _standup_adopt_setup(source_id, session_id)

    if not reuse:
        # Mirror Analysis mode: confirm tracker sources, discover their roster,
        # then confirm the authoritative member subset. Confirmed choices are
        # persisted immediately, so cancelling the later My Update prompt still
        # leaves the new defaults ready for scheduled runs.
        team_ok, team_message = _standup_team_configure(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            session_id,
        )
        if not team_ok:
            logger.info("standup generate: stopped during team selection (session=%s)", session_id)
            return team_message

        code_ok, code_message = _standup_code_configure(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            session_id,
        )
        if not code_ok:
            logger.info("standup generate: stopped during repository selection (session=%s)", session_id)
            return code_message

        documentation_ok, documentation_message = _standup_documentation_configure(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            session_id,
        )
        if not documentation_ok:
            logger.info("standup generate: stopped during documentation selection (session=%s)", session_id)
            return documentation_message

    attachments: list[str] = []
    update = _standup_read_line(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        prompt="Your update for today (Enter to skip)",
        step="Generate standup  —  add your update",
        default="",
        box_rows=6,
        attachments=attachments,
        scope_id=session_id or "standup",
    )
    if update is None:
        logger.info("standup generate: cancelled at update prompt (session=%s)", session_id)
        return None  # Esc → cancel the whole Generate
    if update.strip():
        member = get_standup_user_name()
        with StandupStore(_ana_dbp) as store:
            store.save_my_update(
                session_id,
                date.today().isoformat(),
                member,
                update.strip(),
                images=referenced_images(update, attachments),
            )
        logger.info("standup generate: self-update saved (session=%s)", session_id)

    # Run the pipeline on a worker thread while the frame loop shows live
    # progress — collection + the LLM call can take many seconds, and without
    # this the input box just sat frozen (same pattern as the analysis pages).

    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen

    progress: list[str] = ["Starting"]
    result_box: list = [None]

    def _worker() -> None:
        result_box[0] = _standup_generate(session_id, on_progress=progress.append)

    thread = duck_working_thread(_worker, name="standup-generate")
    thread.start()
    start = time.monotonic()
    while thread.is_alive():
        elapsed = time.monotonic() - start
        w, h = console.size
        live.update(
            _build_standup_progress_screen(
                list(progress),
                width=w,
                height=max(10, h - 1),
                elapsed=elapsed,
                anim_tick=elapsed,
            )
        )
        time.sleep(1 / 30)
    thread.join()
    msg = result_box[0]
    if msg and not str(msg).startswith("Generate failed"):
        _duck_react("standup_done")
    return msg


_REVIEW_STEP_NAMES = ["Source", "Review", "File"]


def _standup_transcript_counts(session_id: str) -> tuple[int, str]:
    """Return ``(unreviewed transcript count, clipboard hint)`` for the source step.

    Both are read ONCE, when the picker opens — never per frame. ``discover``
    hashes files and ``read_clipboard_text`` shells out with a 10 s timeout, so
    either inside the render loop would stall the TUI.
    """
    from yeaboi.clipboard import read_clipboard_text
    from yeaboi.standup import transcripts as _transcripts
    from yeaboi.standup.store import StandupStore

    count = 0
    try:
        from yeaboi.paths import get_db_path

        with StandupStore(get_db_path()) as store:
            config = store.load_config(session_id) or {}
        found, _warnings = _transcripts.discover(session_id, config=config)
        count = len(found)
    except Exception:  # a count is a nicety; never block the picker on it
        logger.debug("standup review: could not count transcripts", exc_info=True)

    try:
        clip = read_clipboard_text() or ""
    except Exception:
        logger.debug("standup review: could not read the clipboard", exc_info=True)
        clip = ""
    if not clip.strip():
        hint = "nothing on the clipboard"
    else:
        hint = f"{len(clip):,} characters ready"
    return count, hint


def _standup_transcript_dir_label(session_id: str) -> str:
    """Describe the saved transcript folder for the "change my folders" row."""
    from yeaboi.standup.store import StandupStore

    try:
        from yeaboi.paths import get_db_path

        with StandupStore(get_db_path()) as store:
            config = store.load_config(session_id) or {}
    except Exception:
        logger.debug("standup review: could not read the transcript folder", exc_info=True)
        return "saved for every run"
    current = str(config.get("transcript_dir") or "")
    if not current:
        return "currently: none — I only look in ~/.yeaboi/transcripts"
    return f"currently: {current.replace(str(Path.home()), '~')}"


def _standup_review_source_step(
    console: Console, live, read_key, frame_time, supports_timeout, session_id: str, message: str = ""
) -> tuple[str, str] | None:
    """Ask where this review's transcript comes from. Returns ``(kind, value)``.

    ``kind`` is one of ``sweep`` / ``paste`` / ``open`` / ``folder``; ``None``
    means the user backed out. The counts in the descriptions are the point of
    the screen — "3 unreviewed file(s)" answers "is there anything to review?"
    without making the user go and look.
    """
    from yeaboi.clipboard import read_clipboard_text
    from yeaboi.paths import get_transcripts_dir

    count, clip_hint = _standup_transcript_counts(session_id)
    folder = str(get_transcripts_dir()).replace(str(Path.home()), "~")
    options = [
        ("Sweep my transcript folders", f"{count} unreviewed file(s)" if count else "nothing unreviewed right now"),
        ("Paste the transcript from my clipboard", clip_hint),
        (f"Open {folder}", "drop a file in, then come back"),
        ("Use another folder, just this once", "a Zoom or Granola recordings folder"),
        ("Change my transcript folders…", _standup_transcript_dir_label(session_id)),
    ]
    choice = _run_schedule_choice_step(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        options=options,
        initial=1 if (count == 0 and "characters" in clip_hint) else 0,
        step_index=0,
        heading="Transcript review  —  where should I look?",
        step_names=_REVIEW_STEP_NAMES,
        message=message,
    )
    if choice == "back":
        return None
    if choice == 0:
        return ("sweep", "")
    if choice == 1:
        text = read_clipboard_text() or ""
        if not text.strip():
            return ("open", "")  # nothing to paste; fall through to the folder
        return ("paste", text)
    if choice == 2:
        return ("open", "")
    if choice == 4:
        return ("configure", "")
    typed = _standup_read_line(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        prompt="Transcript folder (drag it in, or type a path)",
        step="Transcript review  —  where to look",
        default="",
        box_rows=5,
    )
    if typed is None:
        return None
    from yeaboi.standup.transcripts import normalize_dropped_path

    return ("folder", normalize_dropped_path(typed))


def _standup_review_flow(console: Console, live, read_key, frame_time, supports_timeout, session_id: str) -> str | None:
    """Review standup meeting transcripts, then offer to file the gaps found.

    Two deliberate steps. The review always runs and only ever DRAFTS; filing to
    a public GitHub repo needs a separate confirmation that shows what will be
    published. Returns a status message, or None when the user backs out.
    """

    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen

    extra_dir = ""
    transcript_text = ""
    note = ""
    while True:
        picked = _standup_review_source_step(
            console, live, read_key, frame_time, supports_timeout, session_id, message=note
        )
        if picked is None:
            logger.info("standup review: cancelled at the source step (session=%s)", session_id)
            return None
        kind, value = picked
        if kind == "open":
            # Reveal the folder and come straight back, so "where do I put it?"
            # and "now review it" are one continuous move.
            from yeaboi.os_open import open_path
            from yeaboi.paths import get_transcripts_dir

            note = open_path(get_transcripts_dir())
            continue
        if kind == "configure":
            _saved, note = _standup_transcripts_configure(
                console, live, read_key, frame_time, supports_timeout, session_id
            )
            continue
        if kind == "paste":
            transcript_text = value
            break
        if kind == "folder" and value:
            # Ask BEFORE the read so a path outside the sandbox gets an Allow/Deny
            # popup rather than an exception (same as the performance transcript flow).
            from yeaboi.ui.shared._consent import _preflight_path_consent

            if not _preflight_path_consent(console, live, read_key, frame_time, supports_timeout, value, mode="read"):
                return "Transcript folder not allowed — review cancelled."
            extra_dir = value
        break

    progress: list[str] = ["Starting"]
    result_box: list = [None]

    def _worker() -> None:
        try:
            from yeaboi.standup.engine import run_transcript_review

            result_box[0] = run_transcript_review(
                session_id,
                transcript_dir=extra_dir,
                transcript_text=transcript_text,
                on_progress=progress.append,
            )
        except Exception as e:  # a review must never crash the TUI
            logger.error("standup review failed: %s", e, exc_info=True)
            result_box[0] = e

    thread = duck_working_thread(_worker, name="standup-review")
    thread.start()
    start = time.monotonic()
    while thread.is_alive():
        elapsed = time.monotonic() - start
        w, h = console.size
        live.update(
            _build_standup_progress_screen(
                list(progress), width=w, height=max(10, h - 1), elapsed=elapsed, anim_tick=elapsed
            )
        )
        time.sleep(1 / 30)
    thread.join()

    review = result_box[0]
    if isinstance(review, Exception):
        return f"Transcript review failed: {review}"
    if review is None:
        return "Transcript review produced no result — see logs."

    summary = f"Reviewed {len(review.sources)} transcript(s) · {len(review.gaps)} gap(s)"
    if transcript_text and review.sources:
        # Name the day it landed on: a paste attributed to the wrong standup is
        # the one failure the user cannot see from a success message.
        first = review.sources[0]
        summary = f"Saved {first.filename} ({first.attribution}) · {summary}"
    if review.config_suggestions:
        summary += f" · {len(review.config_suggestions)} to fix in config"
    if not review.gaps:
        return summary
    filed = _standup_file_issues_confirm(console, live, read_key, frame_time, supports_timeout, session_id, review)
    return f"{summary}. {filed}" if filed else summary


def _standup_file_issues_confirm(
    console: Console, live, read_key, frame_time, supports_timeout, session_id: str, review
) -> str:
    """Show what would be published, then file only on an explicit yes.

    The confirmation renders the FINAL redacted body — names masked, home paths
    stripped, secrets removed — because that is what actually lands on a public
    repository, and a preview of the raw text would be misleading.
    """
    from yeaboi.standup import gap_issues

    lines = [f"{len(review.gaps)} gap(s) would be filed to {gap_issues.FEEDBACK_REPO} (a PUBLIC repo):", ""]
    blocked = 0
    for gap in review.gaps:
        body = gap_issues.build_gap_issue_body(gap, review)
        leak = gap_issues.leak_check(body)
        if leak:
            blocked += 1
            lines.append(f"  ✗ {gap.title} — blocked, still looks like it contains {leak}")
            continue
        lines.append(f"  • [{gap.feedback_kind}] {gap.title}")
        for claim in gap.claims[:1]:
            if claim.quote:
                masked = gap_issues.scrub(claim.quote, gap_issues.name_mask(review))
                lines.append(f'      evidence: "{masked[:120]}"')
    lines += ["", "Member names are masked and secrets removed. File them now?"]

    answer = _standup_read_line(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        prompt="Type 'yes' to file on GitHub (Enter to skip)",
        step="\n".join(lines),
        default="",
        box_rows=5,
    )
    if answer is None or answer.strip().lower() not in ("y", "yes"):
        logger.info("standup review: filing declined (session=%s)", session_id)
        return "Nothing filed — the drafts are saved locally."

    from yeaboi.standup.engine import file_transcript_issues

    result = file_transcript_issues(review.review_id, session_id=session_id)
    message = f"Filed {result.filed}, commented {result.commented}"
    if result.skipped:
        message += f", skipped {result.skipped}"
    logger.info("standup review: %s (session=%s)", message, session_id)
    return message + "."


def _ask_regen_feedback(console: Console, live, read_key, frame_time, supports_timeout, label: str) -> str | None:
    """Prompt for feedback before regenerating a sample artifact.

    Returns the feedback text, "" when the user just pressed Enter (regenerate
    as-is, same prompt as today), or None when they pressed Esc (cancel the
    regenerate entirely — no LLM call).
    """
    from yeaboi.ui.shared._components import ANALYSIS_THEME, analysis_title

    fb = _standup_read_line(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        prompt="What should change? (Enter to regenerate as-is)",
        step=f"Regenerate {label} — feedback",
        default="",
        theme=ANALYSIS_THEME,
        title=analysis_title(),
        box_rows=6,
    )
    if fb is None:
        logger.info("Regenerate %s: cancelled at feedback prompt", label)
    elif fb:
        logger.info("Regenerate %s: feedback given (%d chars)", label, len(fb))
    else:
        logger.info("Regenerate %s: no feedback, regenerating as-is", label)
    return fb


def _standup_read_line(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    prompt: str,
    step: str,
    default: str = "",
    theme=None,
    title=None,
    box_rows: int = 1,
    attachments: list[str] | None = None,
    scope_id: str = "",
    initial: str = "",
    message: str = "",
    step_names: list[str] | None = None,
    step_index: int = 0,
) -> str | None:
    """Collect a single line of input inside the Live display (themed, read_key-driven).

    Returns the typed value (or the default on empty Enter), or None if the user
    pressed Esc to cancel. Because it uses read_key — which consumes mouse events
    and returns printable chars — there's no raw terminal prompt and no mouse-escape
    leakage.

    Voice dictation (double-tap Space) works here just like the artifact editors:
    the transcript is inserted at the cursor and the recording indicator renders
    inline on this same screen.

    ``theme``/``title`` re-brand the screen for non-standup pages (e.g. the
    analysis regenerate-feedback prompt); defaults keep the standup look.

    attachments: caller-owned list that Ctrl+V screenshot paths are appended to
        (each marked by an [image #N] chip in the text; resolve survivors with
        referenced_images() after submit). None disables image paste — Ctrl+V
        shows the standard "not supported" notice instead.
    initial: pre-seeds the buffer so a field can be re-opened for editing with
        its existing text (e.g. the feedback form's Title/Description rows).
    """
    import time as _time

    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen
    from yeaboi.ui.shared._attachments import handle_ctrl_v, unsupported_notice
    from yeaboi.ui.shared._voice_input import DoubleTapSpace, record_voice_input

    value = initial
    notice = ""
    _dts = DoubleTapSpace()

    def _set_notice(msg: str) -> None:
        nonlocal notice
        notice = msg

    def _render(*, border_style: str = "", status: str = "") -> None:
        w, h = console.size
        live.update(
            _build_standup_input_screen(
                prompt,
                value,
                step=step,
                default=default,
                width=w,
                height=max(10, h - 1),
                border_style=border_style,
                status=status,
                theme=theme,
                title=title,
                box_rows=box_rows,
                show_image_hint=attachments is not None,
                message=message,
                step_names=step_names,
                step_index=step_index,
            )
        )

    # Voice overlay re-renders THIS screen (not a popup) with the pulsing
    # indicator. record_voice_input() calls this and does the live.update itself,
    # so we only return the renderable.
    def _render_status(border: str, line: str):
        w, h = console.size
        return _build_standup_input_screen(
            prompt,
            value,
            step=step,
            default=default,
            width=w,
            height=max(10, h - 1),
            border_style=border,
            status=line,
            message=message,
            step_names=step_names,
            step_index=step_index,
            theme=theme,
            title=title,
            box_rows=box_rows,
        )

    _render()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        if k and k != "":
            notice = ""
        if k == "enter":
            return value.strip() or default
        if k == "esc":
            return None
        if k == "alt+enter":
            # Alt+Enter / Ctrl+N inserts a newline — only meaningful in the large
            # multi-row box; the single-row field keeps ignoring it.
            if box_rows > 1:
                value += "\n"
        elif k == "backspace":
            value = value[:-1]
        elif k == "clear":  # Ctrl+U
            value = ""
        elif k == "word_backspace":  # Ctrl+W
            value = value.rstrip().rsplit(" ", 1)[0] if " " in value.strip() else ""
        elif isinstance(k, str) and k.startswith("paste:"):
            value += paste_payload(k, multiline=box_rows > 1)
        elif k == "ctrl+v":
            if attachments is None:
                unsupported_notice(_set_notice)
            else:
                _render(status="Pasting image…")
                chip = handle_ctrl_v(attachments, scope_id=scope_id or "standup", set_notice=_set_notice)
                if chip:
                    value += chip
                    notice = f"Screenshot attached as {chip}"
        elif k == " " and _dts.is_double(value.endswith(" "), _time.monotonic()):
            # Double-tap Space → dictate. The first space (already in `value`)
            # stays as a separator; the transcript is appended after it.
            spoken = record_voice_input(live, console, read_key, _render_status)
            if spoken:
                value += spoken.replace("\n", " ")
        elif isinstance(k, str) and len(k) == 1 and k.isprintable():
            value += k
        _render(status=notice)


def _run_standup_source_select(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    sources: list[tuple[str, str]],
    initial: list[str],
    *,
    heading: str = "Choose update sources",
    allow_empty: bool = False,
    select_first_if_empty: bool = True,
):
    """Analysis-style tracker multi-select for the Standup Team flow."""
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_team_source_screen

    checked = {idx for idx, (key, _label) in enumerate(sources) if key in initial}
    if not checked and sources and select_first_if_empty:
        checked = {0}
    cursor = 0
    message = ""
    while True:
        w, h = console.size
        live.update(
            _build_standup_team_source_screen(
                sources,
                checked,
                cursor,
                width=w,
                height=max(10, h - 1),
                message=message,
                heading=heading,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "scroll_up") and sources:
            cursor = (cursor - 1) % len(sources)
        elif key in ("down", "scroll_down") and sources:
            cursor = (cursor + 1) % len(sources)
        elif key == " " and sources:
            checked.symmetric_difference_update({cursor})
            message = ""
        elif key == "enter":
            if not checked and not allow_empty:
                message = "Select at least one update source."
                continue
            return [sources[idx][0] for idx in sorted(checked)]
        elif key in ("esc", "q"):
            return "cancel"


def _run_standup_member_select(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    roster: list[str],
    initial: list[str] | None,
    *,
    heading: str = "Choose team members",
    empty_message: str = "No members found in the selected tracker(s).",
):
    """Analysis-style member multi-select, preselected from saved Standup config."""
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_team_member_screen

    checked = (
        {idx for idx, name in enumerate(roster) if name in initial} if initial is not None else set(range(len(roster)))
    )
    cursor = 0
    message = ""
    while True:
        w, h = console.size
        live.update(
            _build_standup_team_member_screen(
                roster,
                checked,
                cursor,
                width=w,
                height=max(10, h - 1),
                message=message,
                heading=heading,
                empty_message=empty_message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "scroll_up") and roster:
            cursor = (cursor - 1) % len(roster)
        elif key in ("down", "scroll_down") and roster:
            cursor = (cursor + 1) % len(roster)
        elif key == " " and roster:
            checked.symmetric_difference_update({cursor})
            message = ""
        elif key in ("a", "A") and roster:
            checked = set() if len(checked) == len(roster) else set(range(len(roster)))
        elif key == "enter":
            if roster and not checked:
                message = "Select at least one team member."
                continue
            return [roster[idx] for idx in sorted(checked)]
        elif key in ("esc", "q"):
            return "cancel"


def _standup_source_labels(keys) -> str:
    """Render stored source keys as the names the pickers showed."""
    names = {
        "jira": "Jira",
        "azure_devops": "Azure DevOps",
        "github": "GitHub",
        "confluence": "Confluence",
        "notion": "Notion",
    }
    return ", ".join(names.get(key, key) for key in keys)


def _standup_name_summary(names: list[str], *, shown: int = 3) -> str:
    """Name what is being reused, so "use saved" is an informed choice.

    Used for both the roster and the code scope — a bare count ("2 Azure
    project(s)") tells you nothing about whether the saved answer is the one
    you want back.
    """
    if len(names) <= shown:
        return ", ".join(names)
    return f"{', '.join(names[:shown])} +{len(names) - shown} more"


def _standup_last_run_label(row: dict, today: date) -> str | None:
    """Describe a standup_history row as "2 days ago · 84% confidence".

    Pure so it can be tested without a store. Returns None when the row carries
    no parseable date — this line is context on the gate, never a gate itself,
    so an unreadable history row must degrade to "no line" and not to an error.
    """
    raw = str(row.get("standup_date") or "").strip() or str(row.get("run_at") or "")[:10]
    try:
        when = date.fromisoformat(raw)
    except ValueError:
        return None
    days = (today - when).days
    if days < 0:  # a run dated in the future — say when, not "-3 days ago"
        parts = [when.isoformat()]
    elif days == 0:
        parts = ["today"]
    elif days == 1:
        parts = ["yesterday"]
    else:
        parts = [f"{days} days ago"]
    confidence = row.get("confidence_pct")
    if confidence is not None:
        parts.append(f"{confidence}% confidence")
    status = str(row.get("status") or "")
    if status and status != "success":
        parts.append(status)
    return " · ".join(parts)


def _standup_saved_setup(session_id: str) -> tuple[str, list[tuple[str, str]]] | None:
    """Summarise a reusable saved setup, or None when Generate must ask.

    Returns ``(source_session_id, rows)`` — ``(label, value)`` rows only once
    every *applicable* step has been confirmed at least once. A step whose
    integration isn't configured at all (no Confluence/Notion, say) is skipped
    by the flow itself and so is not required here — its ``*_configured`` flag
    would never be set.

    ``source_session_id`` is usually ``session_id``, but the standup page
    targets the most recently modified session of *any* mode, so opening a
    project or a retro is enough to leave the setup stranded on an older
    session. When the current one has no config of its own, fall back to the
    newest that does; the caller copies it forward so the run still happens
    under ``session_id`` (the session the page will reload the report from).
    """
    from yeaboi.config import (
        get_azure_devops_org_url,
        get_azure_devops_project,
        get_confluence_space_key,
        get_github_token,
        get_jira_project_key,
        get_notion_root_page_id,
        get_notion_token,
        get_standup_github_repo,
    )
    from yeaboi.standup.store import StandupStore

    if not session_id:
        return None
    history: list[dict] = []
    source_id = session_id
    try:
        with StandupStore(_ana_dbp) as store:
            config = store.load_config(session_id) or {}
            if not config:
                # The latest session belongs to some other mode; the setup is on
                # an older standup session. Offer that one rather than re-asking.
                source_id = store.get_latest_configured_session() or session_id
                config = store.load_config(source_id) or {} if source_id != session_id else {}
            # Context only — "when did this setup last produce a standup". Read in
            # the same connection, but never allowed to decide the gate: a failure
            # here drops the line, it does not send the user back through the
            # pickers. Hence the inner try rather than one shared except.
            try:
                history = store.get_history(source_id, limit=1)
            except Exception:
                logger.warning("standup: could not read the run history", exc_info=True)
    except Exception:
        logger.warning("standup: could not load the saved setup", exc_info=True)
        return None
    if not config:
        return None

    # What each picker would offer today. Walking the pickers used to self-heal a
    # removed integration (they only ever list what is currently configured), so
    # reusing a saved answer is only safe while it is still a subset of these.
    trackers_available = {
        *(("jira",) if get_jira_project_key() else ()),
        *(("azure_devops",) if get_azure_devops_project() else ()),
    }
    code_available = {
        *(("github",) if get_github_token() or get_standup_github_repo() else ()),
        *(("azure_devops",) if get_azure_devops_org_url() else ()),
    }
    docs_available = {
        *(("confluence",) if get_confluence_space_key() else ()),
        *(("notion",) if get_notion_root_page_id() or get_notion_token() else ()),
    }

    # Team. Mirrors the guard in _standup_team_configure: with no tracker at all
    # there is nothing to reuse, and that function's "open Settings first"
    # message is the more useful thing for the user to see.
    if not trackers_available:
        return None
    trackers = list(config.get("tracker_sources", ()))
    members = list(config.get("team_members", ()))
    if not config.get("roster_configured") or not members:
        return None
    if not trackers or not set(trackers) <= trackers_available:
        return None
    rows = [
        ("Trackers", _standup_source_labels(trackers)),
        ("Members", _standup_name_summary(members)),
    ]

    # Code scope. Applicable only when an integration exists — otherwise
    # _standup_code_configure returns early and never sets the flag.
    if code_available:
        if not config.get("code_scope_configured"):
            return None
        if not set(config.get("code_sources", ())) <= code_available:
            return None
        owners = list(config.get("github_owners", ()))
        github = list(config.get("github_repositories", ()))
        excluded = list(config.get("github_excluded_repositories", ()))
        projects = list(config.get("azdo_projects", ()))
        parts = []
        if owners:
            parts.append(f"{len(owners)} GitHub org(s)")
        if github:
            parts.append(f"{len(github)} GitHub repo(s)")
        if excluded:
            parts.append(f"{len(excluded)} repo(s) excluded")
        if projects:
            parts.append(f"{len(projects)} Azure project(s)")
        counts = " · ".join(parts)
        # Second line names them. A count alone can't tell you whether the saved
        # scope is the one you want back. The newline is the builder's cue to
        # give this a second row — the value is still plain text.
        names = _standup_name_summary(owners + github + projects, shown=4)
        rows.append(("Code", f"{counts}\n{names}" if counts else "none"))

    # Documentation. An empty selection is a real answer here (the picker allows
    # it), so the flag is the signal, not the list.
    if docs_available:
        if not config.get("documentation_scope_configured"):
            return None
        docs = list(config.get("documentation_sources", ()))
        if not set(docs) <= docs_available:
            return None
        rows.append(("Docs", _standup_source_labels(docs) or "none"))

    # Last, and never a gate: when this setup last produced a standup.
    if history:
        last_run = _standup_last_run_label(history[0], date.today())
        if last_run:
            rows.append(("Last run", last_run))
    return source_id, rows


# The setup fields "use saved" carries forward. Schedule fields (enabled, time,
# weekdays, delivery_channels…) are deliberately absent: they belong to the
# session whose launchd job is installed, and copying them would make a second
# session look scheduled when nothing is registered for it.
_STANDUP_SETUP_FIELDS = (
    "repo_path",
    "my_aliases",
    "tracker_sources",
    "team_members",
    "roster_configured",
    "code_sources",
    "github_owners",
    "github_repositories",
    "github_excluded_repositories",
    "azdo_projects",
    "azdo_repositories",
    "code_scope_configured",
    "documentation_sources",
    "documentation_scope_configured",
    "automation_markers",
    "automation_handling",
)


def _standup_adopt_setup(source_id: str, session_id: str) -> None:
    """Copy a saved setup onto ``session_id`` — what the pickers would have written.

    Reusing means running under the session the page is showing, not under the
    one the answers came from: ``run_standup`` resolves config by session id and
    the page reloads the report for the latest session, so generating elsewhere
    would produce a report nothing displays. Only called when ``session_id`` has
    no config of its own, so the upsert cannot clear a schedule.
    """
    from yeaboi.standup.store import StandupStore

    try:
        with StandupStore(_ana_dbp) as store:
            config = store.load_config(source_id)
            if not config:
                return
            store.save_config(
                session_id,
                enabled=False,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                **{key: config[key] for key in _STANDUP_SETUP_FIELDS if key in config},
            )
        logger.info("standup: adopted the saved setup from %s onto %s", source_id, session_id)
    except Exception:
        logger.warning("standup: could not adopt the saved setup", exc_info=True)


def _run_standup_saved_setup_confirm(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    rows: list[tuple[str, str]],
) -> str:
    """Offer the saved setup before Generate re-walks every picker.

    Returns "use" (skip straight to the update prompt), "change" (run the
    pickers as before) or "cancel" (no run at all).
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _SAVED_SETUP_ACTIONS,
        _build_standup_saved_setup_screen,
    )

    outcomes = ("use", "change", "cancel")
    sel = 0
    while True:
        w, h = console.size
        panel = _build_standup_saved_setup_screen(rows, action_sel=sel, width=w, height=max(10, h - 1))
        live.update(panel)
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if not key:  # idle tick / consumed mouse event
            continue
        clicked = parse_click(key)
        if clicked is not None:
            idx = button_click(console, panel, *clicked, _SAVED_SETUP_ACTIONS)
            if idx is not None:
                return outcomes[idx]
            continue
        if key in ("left", "up"):
            sel = (sel - 1) % len(outcomes)
        elif key in ("right", "down"):
            sel = (sel + 1) % len(outcomes)
        elif key in ("enter", " "):
            return outcomes[sel]
        elif key in ("esc", "q"):
            return "cancel"


def _run_standup_practice_review(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    session_id: str,
    member: str,
    signals: list,
) -> str:
    """Record the reader's verdict on each of one member's practice signals.

    A verdict per row rather than a checkbox list: the two answers do different
    things (a thumbs-down suppresses and teaches, a thumbs-up only teaches), and
    "unanswered" has to stay distinct from "answered no" or every signal nobody
    looked at would count as confirmed.

    Notes are asked for only on a thumbs-down, once per signal, after Enter — a
    prompt per row while voting would make saying "that one's wrong" cost a
    paragraph, and the whole point is that it costs a keystroke.

    Returns a status line for the page below it.
    """
    from yeaboi.standup import practice_feedback
    from yeaboi.standup.store import StandupStore
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_practice_review_screen

    verdicts: dict[int, str] = {}
    cursor = 0
    message = ""
    while True:
        w, h = console.size
        live.update(
            _build_standup_practice_review_screen(
                member, signals, verdicts, cursor, width=w, height=max(10, h - 1), message=message
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "scroll_up") and signals:
            cursor = (cursor - 1) % len(signals)
        elif key in ("down", "scroll_down") and signals:
            cursor = (cursor + 1) % len(signals)
        elif key in ("y", "Y") and signals:
            verdicts[cursor] = practice_feedback.VERDICT_UP
            message = ""
        elif key in ("n", "N") and signals:
            verdicts[cursor] = practice_feedback.VERDICT_DOWN
            message = ""
        elif key in ("c", "C") and signals:
            verdicts.pop(cursor, None)
            message = ""
        elif key == "enter":
            answered = {i: v for i, v in verdicts.items() if v}
            if not answered:
                message = "Answer at least one signal, or press Esc to leave them all alone."
                continue
            break
        elif key in ("esc", "q"):
            logger.info("standup: practice review cancelled for %s", member)
            return ""

    notes: dict[int, str] = {}
    for idx in sorted(answered):
        if answered[idx] != practice_feedback.VERDICT_DOWN:
            continue
        typed = _standup_read_line(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            prompt="Why was this wrong? (Enter to skip)",
            step=f"Practices — {signals[idx].title}",
            box_rows=3,
        )
        # Esc on the note cancels the note, not the verdict: the reader has
        # already said the signal was wrong, and making them re-navigate to say
        # it again would be the surest way to stop them ever saying it.
        notes[idx] = typed or ""

    applied = 0
    try:
        with StandupStore(_get_db_path()) as store:
            for idx, verdict in sorted(answered.items()):
                if practice_feedback.apply_verdict(
                    store,
                    session_id=session_id,
                    member=member,
                    rule=signals[idx].rule,
                    verdict=verdict,
                    note=notes.get(idx, ""),
                ):
                    applied += 1
    except Exception as e:  # a feedback nicety must never take the page down
        logger.error("standup: recording practice feedback failed: %s", e, exc_info=True)
        return f"Could not save feedback: {e}"

    hidden = sum(1 for i, v in answered.items() if v == practice_feedback.VERDICT_DOWN)
    logger.info("standup: %d practice verdict(s) recorded for %s (%d hidden)", applied, member, hidden)
    if not applied:
        return "Those signals are no longer in the report — nothing recorded."
    if hidden:
        return f"Thanks — {_count_word(hidden, 'signal')} hidden and won't come back."
    return f"Thanks — {_count_word(applied, 'signal')} confirmed."


def _count_word(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _standup_team_configure(
    console: Console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    session_id: str,
) -> tuple[bool, str]:
    """Choose tracker sources and persist an authoritative Standup roster.

    Returns ``(True, message)`` only after both picker steps were confirmed and
    saved. Cancellation/setup errors return ``(False, message)`` so Generate
    can stop before prompting for the user's update or running the engine.
    """
    import threading

    from yeaboi.config import get_azure_devops_project, get_jira_project_key
    from yeaboi.standup.roster import default_tracker_sources, discover_team_members
    from yeaboi.standup.store import StandupStore
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen

    jira_project = get_jira_project_key() or ""
    azdo_project = get_azure_devops_project() or ""
    sources: list[tuple[str, str]] = []
    if jira_project:
        sources.append(("jira", "Jira"))
    if azdo_project:
        sources.append(("azure_devops", "Azure DevOps"))
    if not sources:
        return False, "No Jira or Azure DevOps tracker configured — open Settings first."

    with StandupStore(_ana_dbp) as store:
        existing = store.load_config(session_id) or {}
    initial_sources = (
        existing.get("tracker_sources", [])
        if existing.get("roster_configured")
        else default_tracker_sources(jira_project=jira_project, azdo_project=azdo_project)
    )
    selected_sources = _run_standup_source_select(
        live,
        console,
        read_key,
        frame_time,
        supports_timeout,
        sources,
        initial_sources,
    )
    if selected_sources == "cancel":
        logger.info("standup team: source selection cancelled (session=%s)", session_id)
        return False, "Team selection cancelled."

    result_box: list = [None]
    done = threading.Event()

    def _discover() -> None:
        try:
            result_box[0] = discover_team_members(
                selected_sources,
                jira_project=jira_project,
                azdo_project=azdo_project,
            )
        except Exception as exc:
            logger.warning("standup team: roster discovery failed: %s", exc)
            result_box[0] = []
        finally:
            done.set()

    started = time.monotonic()
    duck_working_thread(_discover, name="standup-roster").start()
    tick = 0.0
    while not done.is_set():
        tick += frame_time
        w, h = console.size
        live.update(
            _build_standup_progress_screen(
                ["Discovering team members"],
                width=w,
                height=max(10, h - 1),
                elapsed=time.monotonic() - started,
                anim_tick=tick,
                label="Loading selected trackers",
            )
        )
        time.sleep(frame_time)

    discovered = result_box[0] or []
    saved_members = list(existing.get("team_members", ())) if existing.get("roster_configured") else []
    plan_members: list[str] = []
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(_ana_dbp) as sessions:
            state = sessions.load_state(session_id) or {}
        plan_members = [str(name).strip() for name in state.get("selected_team_members", ()) if str(name).strip()]
    except Exception:
        logger.warning("standup team: could not load planning members", exc_info=True)
    roster = sorted(dict.fromkeys([*discovered, *saved_members, *plan_members]), key=str.lower)
    initial_members = (
        saved_members if existing.get("roster_configured") else (discovered if discovered else plan_members)
    )
    selected_members = _run_standup_member_select(
        live,
        console,
        read_key,
        frame_time,
        supports_timeout,
        roster,
        initial_members,
    )
    if selected_members == "cancel":
        logger.info("standup team: member selection cancelled (session=%s)", session_id)
        return False, "Team selection cancelled."

    defaults = {
        "enabled": False,
        "time": "10:00",
        "weekdays": "1-5",
        "delivery_channels": ["terminal"],
        "lead_minutes": 10,
        "timezone": "",
        "repo_path": "",
        "my_aliases": "",
    }
    merged = {**defaults, **existing}
    with StandupStore(_ana_dbp) as store:
        store.save_config(
            session_id,
            enabled=merged["enabled"],
            time=merged["time"],
            weekdays=merged["weekdays"],
            delivery_channels=merged["delivery_channels"],
            lead_minutes=merged["lead_minutes"],
            timezone=merged["timezone"],
            repo_path=merged["repo_path"],
            my_aliases=merged["my_aliases"],
            tracker_sources=selected_sources,
            team_members=selected_members,
            roster_configured=True,
            code_sources=merged.get("code_sources", []),
            github_owners=merged.get("github_owners", []),
            github_repositories=merged.get("github_repositories", []),
            github_excluded_repositories=merged.get("github_excluded_repositories", []),
            azdo_projects=merged.get("azdo_projects", []),
            azdo_repositories=merged.get("azdo_repositories", []),
            code_scope_configured=merged.get("code_scope_configured", False),
            documentation_sources=merged.get("documentation_sources", []),
            documentation_scope_configured=merged.get("documentation_scope_configured", False),
            automation_markers=merged.get("automation_markers", ""),
            automation_handling=merged.get("automation_handling", "exclude"),
            transcript_dir=merged.get("transcript_dir", ""),
            transcript_review_enabled=merged.get("transcript_review_enabled", True),
            habit_detection=merged.get("habit_detection", "on"),
            habit_rules=merged.get("habit_rules", ""),
            habit_ai_match=merged.get("habit_ai_match", "on"),
        )
    logger.info(
        "standup team: saved session=%s sources=%s members=%d",
        session_id,
        selected_sources,
        len(selected_members),
    )
    return True, f"Team saved — {len(selected_members)} member(s) from {len(selected_sources)} tracker(s)."


def _standup_code_configure(
    console: Console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    session_id: str,
) -> tuple[bool, str]:
    """Choose GitHub organisations + repositories, and Azure DevOps projects; persist the code scope.

    GitHub and Azure DevOps get their own screens now — mixing "GitHub · acme"
    and "Azure DevOps · Platform" rows in one list read as one system when they
    are two. Picking an owner/project is still a *container* pick — everything
    inside it is in scope, so the estate never goes stale — but GitHub adds a
    second step: which repos inside the chosen owners to actually scan, all
    pre-checked, so excluding a noisy one is the only tick anybody has to make.
    """
    import threading

    from yeaboi.config import (
        get_azure_devops_org_url,
        get_azure_devops_project,
        get_github_token,
        get_standup_github_repo,
    )
    from yeaboi.standup.code_scope import (
        default_code_scope,
        discover_code_repositories,
        list_owner_repositories,
    )
    from yeaboi.standup.store import StandupStore
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen

    with StandupStore(_ana_dbp) as store:
        existing = store.load_config(session_id) or {}
    available: list[tuple[str, str]] = []
    if get_github_token() or get_standup_github_repo():
        available.append(("github", "GitHub"))
    if get_azure_devops_org_url():
        available.append(("azure_devops", "Azure Repos"))
    if not available:
        return True, "No GitHub or Azure Repos integration configured — continuing without code coverage."

    default_sources, default_github, _default_azdo = default_code_scope()
    initial_sources = existing.get("code_sources", []) if existing.get("code_scope_configured") else default_sources
    selected_sources = _run_standup_source_select(
        live,
        console,
        read_key,
        frame_time,
        supports_timeout,
        available,
        initial_sources,
        heading="Choose code sources",
    )
    if selected_sources == "cancel":
        return False, "Repository selection cancelled."

    def _run_discovery(fetch, label: str, thread_name: str):
        """Run ``fetch`` on a worker thread behind a progress spinner."""
        result_box: list = [None]
        done = threading.Event()

        def _work() -> None:
            try:
                result_box[0] = fetch()
            except Exception as exc:
                logger.warning("standup code: %s failed: %s", label, exc)
                result_box[0] = None
            finally:
                done.set()

        started = time.monotonic()
        duck_working_thread(_work, name=thread_name).start()
        tick = 0.0
        while not done.is_set():
            tick += frame_time
            w, h = console.size
            live.update(
                _build_standup_progress_screen(
                    [label],
                    width=w,
                    height=max(10, h - 1),
                    elapsed=time.monotonic() - started,
                    anim_tick=tick,
                    label=label,
                )
            )
            time.sleep(frame_time)
        return result_box[0]

    discovered = (
        _run_discovery(
            lambda: discover_code_repositories(selected_sources),
            "Discovering repositories",
            "standup-code-sources",
        )
        or {}
    )
    github_choices = list(discovered.get("github", ()))
    azdo_project_choices = list(discovered.get("azure_devops", ()))

    selected_github_owners: list[str] = []
    # Survives untouched unless the repo-exclude step below actually runs — same
    # "nothing lost unless explicitly superseded" rule as preserved_repositories
    # applies below to explicit repos.
    excluded_repositories = list(existing.get("github_excluded_repositories", ()))
    # A narrow repo scope already on file — explicit pins from a previous save,
    # or a legacy STANDUP_GITHUB_REPO pin before anything was ever configured.
    # Computed once, up front, so the pretick decision below and the survival
    # check further down (preserved_repositories) can't drift from each other.
    prior_repositories = list(existing.get("github_repositories", ())) or list(default_github)

    if "github" in selected_sources:
        github_labels = {f"GitHub · {owner}": owner for owner in github_choices}
        saved_owners = {owner.lower() for owner in existing.get("github_owners", ())}
        if saved_owners:
            # GitHub has been scoped before — never widen it silently; the
            # picker offers that upgrade explicitly, exactly as it always has.
            initial_owner_labels = [label for label, owner in github_labels.items() if owner.lower() in saved_owners]
        elif prior_repositories:
            # A narrow repo scope already exists (see above) and no owner has
            # ever been chosen; widening it to those repos' whole owner(s)
            # would be a surprise, so nothing pre-ticks here.
            initial_owner_labels = []
        else:
            # GitHub has never been scoped before — whether this is a first-ever
            # walk, or the standup was already configured (say, Azure-only) and
            # GitHub is only now being added. Either way it starts fully
            # selected, matching what an unconfigured run already covers: every
            # owner the token can see. Nobody should have to re-discover that
            # by hand just because Azure got configured first.
            initial_owner_labels = list(github_labels)
        selected_owner_labels = _run_standup_member_select(
            live,
            console,
            read_key,
            frame_time,
            supports_timeout,
            list(github_labels),
            initial_owner_labels,
            heading="Choose GitHub organisations",
            empty_message="No accessible organisations found for GitHub.",
        )
        if selected_owner_labels == "cancel":
            return False, "Code scope selection cancelled."
        selected_set = set(selected_owner_labels)
        selected_github_owners = [owner for label, owner in github_labels.items() if label in selected_set]

        if selected_github_owners:
            owner_repos, repo_warnings = _run_discovery(
                lambda: list_owner_repositories(selected_github_owners),
                "Discovering GitHub repositories",
                "standup-code-repositories",
            ) or ({}, [])
            for warning in repo_warnings:
                logger.warning("standup code: GitHub repository listing: %s", warning)
            repo_choices = sorted({repo for repos in owner_repos.values() for repo in repos}, key=str.lower)
            if repo_choices:
                saved_excluded = {repo.lower() for repo in existing.get("github_excluded_repositories", ())}
                # Everything selected by default: excluding a repo is the only
                # tick anyone should have to make. A repo already excluded stays
                # unticked so re-entering this screen doesn't silently undo it.
                initial_repo_labels = [repo for repo in repo_choices if repo.lower() not in saved_excluded]
                selected_repo_labels = _run_standup_member_select(
                    live,
                    console,
                    read_key,
                    frame_time,
                    supports_timeout,
                    repo_choices,
                    initial_repo_labels,
                    heading="Choose repositories to scan (everything selected by default)",
                    empty_message="No repositories found for the selected organisation(s).",
                )
                if selected_repo_labels == "cancel":
                    return False, "Code scope selection cancelled."
                kept = set(selected_repo_labels)
                # A partial listing failure (one owner's discovery_error, another's
                # repos returned fine) must not read as "nothing excluded" for the
                # owner the screen never showed — that would silently un-exclude
                # repos this run just didn't manage to list. Only repos actually
                # shown on screen can move in or out of the exclusion list; an
                # exclusion for a repo outside `repo_choices` survives untouched.
                shown = {repo.lower() for repo in repo_choices}
                retained = [
                    repo for repo in existing.get("github_excluded_repositories", ()) if repo.lower() not in shown
                ]
                newly_excluded = [repo for repo in repo_choices if repo not in kept]
                excluded_repositories = retained + newly_excluded

    selected_azdo_projects: list[str] = []
    if "azure_devops" in selected_sources:
        azdo_labels = {f"Azure DevOps · {project}": project for project in azdo_project_choices}
        saved_azdo_projects = list(existing.get("azdo_projects", ()))
        if existing.get("code_scope_configured"):
            initial_azdo_labels = [label for label, project in azdo_labels.items() if project in saved_azdo_projects]
        else:
            legacy_project = (get_azure_devops_project() or "").lower()
            initial_azdo_labels = [label for label, project in azdo_labels.items() if project.lower() == legacy_project]
        selected_azdo_labels = _run_standup_member_select(
            live,
            console,
            read_key,
            frame_time,
            supports_timeout,
            list(azdo_labels),
            initial_azdo_labels,
            heading="Choose Azure DevOps projects",
            empty_message="No accessible Azure DevOps projects found.",
        )
        if selected_azdo_labels == "cancel":
            return False, "Code scope selection cancelled."
        selected_set = set(selected_azdo_labels)
        selected_azdo_projects = [project for label, project in azdo_labels.items() if label in selected_set]

    if not selected_github_owners and not selected_azdo_projects:
        return False, "No accessible organisations or projects found — check integration permissions."

    # Explicit repositories survive unless a chosen owner already covers them.
    # Clearing the list outright lost three things: a pinned STANDUP_GITHUB_REPO
    # on the first walk, a deliberately narrow saved scope, and — worst, because
    # it is invisible — any repo whose owner never surfaced in discovery
    # (github_list_owners caps at 100 and either of its lookups can fail and be
    # skipped).
    covered_owners = {owner.lower() for owner in selected_github_owners}
    preserved_repositories = [
        repository
        for repository in prior_repositories
        for owner, separator, _name in [str(repository).partition("/")]
        if not (separator and owner.lower() in covered_owners)
    ]
    defaults = {
        "enabled": False,
        "time": "10:00",
        "weekdays": "1-5",
        "delivery_channels": ["terminal"],
        "lead_minutes": 10,
        "timezone": "",
        "repo_path": "",
        "my_aliases": "",
        "tracker_sources": ["jira"],
        "team_members": [],
        "roster_configured": False,
    }
    merged = {**defaults, **existing}
    with StandupStore(_ana_dbp) as store:
        store.save_config(
            session_id,
            enabled=merged["enabled"],
            time=merged["time"],
            weekdays=merged["weekdays"],
            delivery_channels=merged["delivery_channels"],
            lead_minutes=merged["lead_minutes"],
            timezone=merged["timezone"],
            repo_path=merged["repo_path"],
            my_aliases=merged["my_aliases"],
            tracker_sources=merged["tracker_sources"],
            team_members=merged["team_members"],
            roster_configured=merged["roster_configured"],
            code_sources=selected_sources,
            github_owners=selected_github_owners,
            github_repositories=preserved_repositories,
            github_excluded_repositories=excluded_repositories,
            azdo_projects=selected_azdo_projects,
            azdo_repositories=[],
            code_scope_configured=True,
            documentation_sources=merged.get("documentation_sources", []),
            documentation_scope_configured=merged.get("documentation_scope_configured", False),
            automation_markers=merged.get("automation_markers", ""),
            automation_handling=merged.get("automation_handling", "exclude"),
            transcript_dir=merged.get("transcript_dir", ""),
            transcript_review_enabled=merged.get("transcript_review_enabled", True),
            habit_detection=merged.get("habit_detection", "on"),
            habit_rules=merged.get("habit_rules", ""),
            habit_ai_match=merged.get("habit_ai_match", "on"),
        )
    logger.info(
        "standup code: saved session=%s sources=%s github_owners=%d github_repos=%d excluded=%d azdo_projects=%d",
        session_id,
        selected_sources,
        len(selected_github_owners),
        len(preserved_repositories),
        len(excluded_repositories),
        len(selected_azdo_projects),
    )
    kept = f" + {len(preserved_repositories)} pinned repo(s)" if preserved_repositories else ""
    excluded_note = f", {len(excluded_repositories)} repo(s) excluded" if excluded_repositories else ""
    return True, (
        f"Code scope saved — {len(selected_github_owners)} GitHub org(s){kept}{excluded_note}, "
        f"{len(selected_azdo_projects)} Azure project(s)."
    )


def _standup_documentation_configure(
    console: Console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    session_id: str,
) -> tuple[bool, str]:
    """Choose Confluence/Notion providers; repository documentation is automatic."""
    from yeaboi.config import get_confluence_space_key, get_notion_root_page_id, get_notion_token
    from yeaboi.standup.documentation_scope import default_documentation_sources
    from yeaboi.standup.store import StandupStore

    confluence_space = get_confluence_space_key() or ""
    notion_root = get_notion_root_page_id() or ("workspace" if get_notion_token() else "")
    available: list[tuple[str, str]] = []
    if confluence_space:
        available.append(("confluence", "Confluence"))
    if notion_root:
        available.append(("notion", "Notion"))
    if not available:
        return True, "No Confluence or Notion integration configured — repository documentation is still included."
    with StandupStore(_ana_dbp) as store:
        existing = store.load_config(session_id) or {}

    initial = (
        existing.get("documentation_sources", [])
        if existing.get("documentation_scope_configured")
        else default_documentation_sources(
            confluence_space=confluence_space,
            notion_root=notion_root,
        )
    )
    selected = _run_standup_source_select(
        live,
        console,
        read_key,
        frame_time,
        supports_timeout,
        available,
        initial,
        heading="Choose documentation sources",
        allow_empty=True,
        select_first_if_empty=False,
    )
    if selected == "cancel":
        return False, "Documentation selection cancelled."

    defaults = {
        "enabled": False,
        "time": "10:00",
        "weekdays": "1-5",
        "delivery_channels": ["terminal"],
        "lead_minutes": 10,
        "timezone": "",
        "repo_path": "",
        "my_aliases": "",
        "tracker_sources": ["jira"],
        "team_members": [],
        "roster_configured": False,
        "code_sources": [],
        "github_owners": [],
        "github_repositories": [],
        "github_excluded_repositories": [],
        "azdo_projects": [],
        "azdo_repositories": [],
        "code_scope_configured": False,
    }
    merged = {**defaults, **existing}
    with StandupStore(_ana_dbp) as store:
        store.save_config(
            session_id,
            enabled=merged["enabled"],
            time=merged["time"],
            weekdays=merged["weekdays"],
            delivery_channels=merged["delivery_channels"],
            lead_minutes=merged["lead_minutes"],
            timezone=merged["timezone"],
            repo_path=merged["repo_path"],
            my_aliases=merged["my_aliases"],
            tracker_sources=merged["tracker_sources"],
            team_members=merged["team_members"],
            roster_configured=merged["roster_configured"],
            code_sources=merged["code_sources"],
            github_owners=merged["github_owners"],
            github_repositories=merged["github_repositories"],
            github_excluded_repositories=merged["github_excluded_repositories"],
            azdo_projects=merged["azdo_projects"],
            azdo_repositories=merged["azdo_repositories"],
            code_scope_configured=merged["code_scope_configured"],
            documentation_sources=selected,
            documentation_scope_configured=True,
            automation_markers=merged.get("automation_markers", ""),
            automation_handling=merged.get("automation_handling", "exclude"),
            transcript_dir=merged.get("transcript_dir", ""),
            transcript_review_enabled=merged.get("transcript_review_enabled", True),
            habit_detection=merged.get("habit_detection", "on"),
            habit_rules=merged.get("habit_rules", ""),
            habit_ai_match=merged.get("habit_ai_match", "on"),
        )
    return True, f"Documentation scope saved — {len(selected)} provider(s); repository docs included."


_TRANSCRIPT_STEP_NAMES = ["Folder", "Auto-review"]
# Sentinel for the "Type a folder…" row: a value no real path can collide with.
_TYPE_A_FOLDER = "\x00type"


def _standup_transcripts_configure(
    console: Console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    session_id: str,
) -> tuple[bool, str]:
    """Choose where recordings come from, and whether the sweep runs by itself.

    Both settings were MCP-only before this: ``transcript_dir`` could be typed
    for a single run but never saved, and ``transcript_review_enabled`` had no
    interface at all. Folders are DETECTED and offered by name rather than
    typed, because "is this the right one?" is a question people can answer and
    "where does Zoom put your recordings?" is not.
    """
    from yeaboi.standup import transcript_sources
    from yeaboi.standup.store import StandupStore
    from yeaboi.standup.transcripts import normalize_dropped_path
    from yeaboi.ui.shared._consent import _preflight_path_choice

    with StandupStore(_ana_dbp) as store:
        existing = store.load_config(session_id) or {}
    current = str(existing.get("transcript_dir") or "")

    options: list[tuple[str, str]] = []
    values: list[str] = []
    for candidate in transcript_sources.detect():
        options.append((candidate.label, transcript_sources.describe(candidate)))
        values.append(candidate.path)
    if current and current not in values:
        options.append((current.replace(str(Path.home()), "~"), "your current folder"))
        values.append(current)
    options.append(("Type a folder…", "drag it in, or type a path"))
    values.append(_TYPE_A_FOLDER)
    options.append(("Just ~/.yeaboi/transcripts", "no extra folder"))
    values.append("")

    chosen_dir = current
    auto = bool(existing.get("transcript_review_enabled", True))
    index = 0
    while index < 2:
        if index == 0:
            initial = values.index(current) if current in values else 0
            pick = _run_schedule_choice_step(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                options=options,
                initial=initial,
                step_index=0,
                heading="Where do your standup recordings land?",
                step_names=_TRANSCRIPT_STEP_NAMES,
            )
            if pick == "back":
                return False, "Transcript setup cancelled."
            value = values[pick]
            if value == _TYPE_A_FOLDER:
                typed = _standup_read_line(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    prompt="Transcript folder (drag it in, or type a path)",
                    step="Transcripts  —  where your recordings land",
                    default="",
                    box_rows=5,
                )
                if typed is None:
                    continue  # Esc from the text box returns to the list
                value = normalize_dropped_path(typed)
            if value:
                choice = _preflight_path_choice(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    value,
                    mode="read",
                    context="Standup — transcript folder",
                )
                if choice == "deny":
                    return False, "Transcript folder not allowed — nothing saved."
                if choice == "allow_once":
                    # An allow-once grant dies with this process. Saving a folder
                    # backed by one produces a config that works right now and
                    # then silently reviews nothing on every scheduled run —
                    # the worst kind of wrong, because it looks configured.
                    return False, (
                        "That folder is allowed for this run only, so it wasn't saved. "
                        "Choose 'Always allow' to keep it."
                    )
            chosen_dir = value
            index = 1
        else:
            pick = _run_schedule_choice_step(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                options=[
                    ("Review transcripts automatically", "before each standup, so misses surface on their own"),
                    ("Only when I ask", "the Review button still works"),
                ],
                initial=0 if auto else 1,
                step_index=1,
                heading="Check each standup against its meeting?",
                step_names=_TRANSCRIPT_STEP_NAMES,
            )
            if pick == "back":
                index = 0
                continue
            auto = pick == 0
            index = 2

    defaults = {
        "enabled": False,
        "time": "10:00",
        "weekdays": "1-5",
        "delivery_channels": ["terminal"],
        "lead_minutes": 10,
        "timezone": "",
        "repo_path": "",
        "my_aliases": "",
        "tracker_sources": ["jira"],
        "team_members": [],
        "roster_configured": False,
        "code_sources": [],
        "github_owners": [],
        "github_repositories": [],
        "github_excluded_repositories": [],
        "azdo_projects": [],
        "azdo_repositories": [],
        "code_scope_configured": False,
        "documentation_sources": [],
        "documentation_scope_configured": False,
    }
    merged = {**defaults, **existing}
    with StandupStore(_ana_dbp) as store:
        store.save_config(
            session_id,
            enabled=merged["enabled"],
            time=merged["time"],
            weekdays=merged["weekdays"],
            delivery_channels=merged["delivery_channels"],
            lead_minutes=merged["lead_minutes"],
            timezone=merged["timezone"],
            repo_path=merged["repo_path"],
            my_aliases=merged["my_aliases"],
            tracker_sources=merged["tracker_sources"],
            team_members=merged["team_members"],
            roster_configured=merged["roster_configured"],
            code_sources=merged["code_sources"],
            github_owners=merged["github_owners"],
            github_repositories=merged["github_repositories"],
            github_excluded_repositories=merged["github_excluded_repositories"],
            azdo_projects=merged["azdo_projects"],
            azdo_repositories=merged["azdo_repositories"],
            code_scope_configured=merged["code_scope_configured"],
            documentation_sources=merged["documentation_sources"],
            documentation_scope_configured=merged["documentation_scope_configured"],
            automation_markers=merged.get("automation_markers", ""),
            automation_handling=merged.get("automation_handling", "exclude"),
            transcript_dir=chosen_dir,
            transcript_review_enabled=auto,
            habit_detection=merged.get("habit_detection", "on"),
            habit_rules=merged.get("habit_rules", ""),
            habit_ai_match=merged.get("habit_ai_match", "on"),
        )
    logger.info(
        "standup transcripts configured: session=%s dir=%s auto=%s",
        session_id,
        chosen_dir or "-",
        auto,
    )
    where = chosen_dir.replace(str(Path.home()), "~") if chosen_dir else "~/.yeaboi/transcripts only"
    return True, f"Transcripts: {where} · {'automatic review on' if auto else 'review on demand'}."


def _run_schedule_choice_step(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    options: list[tuple[str, str]],
    initial: int,
    step_index: int,
    heading: str,
    step_names: list[str] | None = None,
    message: str = "",
    theme=None,
    title_fn=None,
) -> int | str:
    """One single-select (radio) step of an option-list wizard.

    The cursor row IS the selection: Enter returns its index, Esc returns
    ``"back"`` so the wizard can step backwards (analysis-wizard convention).
    ``step_names``/``message``/``theme``/``title_fn`` let a non-standup wizard
    reuse this loop without wearing standup's masthead.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_schedule_step_screen

    cursor = initial if 0 <= initial < len(options) else 0
    while True:
        w, h = console.size
        live.update(
            _build_standup_schedule_step_screen(
                options,
                cursor,
                step_index=step_index,
                heading=heading,
                width=w,
                height=max(10, h - 1),
                message=message,
                step_names=step_names,
                theme=theme,
                title_fn=title_fn,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "scroll_up", "left"):
            cursor = (cursor - 1) % len(options)
        elif key in ("down", "scroll_down", "right"):
            cursor = (cursor + 1) % len(options)
        elif key == "enter":
            return cursor
        elif key in ("esc", "q"):
            return "back"


def _run_schedule_multi_step(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    options: list[tuple[str, str]],
    initial: set[int],
    step_index: int,
    heading: str,
    empty_message: str,
) -> set[int] | str:
    """One multi-select (checkbox) step of the schedule wizard.

    Space toggles the cursor row, Enter confirms (requires at least one
    selection), Esc returns ``"back"``.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_schedule_step_screen

    checked = {i for i in initial if 0 <= i < len(options)}
    cursor = 0
    message = ""
    while True:
        w, h = console.size
        live.update(
            _build_standup_schedule_step_screen(
                options,
                cursor,
                checked=checked,
                step_index=step_index,
                heading=heading,
                width=w,
                height=max(10, h - 1),
                message=message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "scroll_up"):
            cursor = (cursor - 1) % len(options)
        elif key in ("down", "scroll_down"):
            cursor = (cursor + 1) % len(options)
        elif key == " ":
            checked.symmetric_difference_update({cursor})
            message = ""
        elif key == "enter":
            if not checked:
                message = empty_message
                continue
            return checked
        elif key in ("esc", "q"):
            return "back"


_SCHEDULE_TIME_PRESETS = ["09:00", "09:30", "10:00", "10:30", "11:00"]
_SCHEDULE_LEAD_PRESETS = [5, 10, 15, 30]
_SCHEDULE_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_SCHEDULE_CHANNEL_DESCS = {
    "terminal": "print in the terminal the run opens",
    "desktop": "macOS/Linux system notification",
    "slack": "post to Slack (needs SLACK_WEBHOOK_URL)",
    "email": "send via SMTP (needs STANDUP_SMTP_* settings)",
}
# Minutes AFTER the standup for the transcript reminder; 0 = no reminder. The
# offset is not a config column: the existence of the OS job IS the setting, so
# there is nothing to migrate or keep in sync.
_REMINDER_PRESETS = [0, 30, 60, 120]


def _run_standup_schedule_wizard(
    console: Console, live, read_key, frame_time, supports_timeout, session_id: str
) -> str:
    """Option-list wizard: Time → Lead → Days → Channels → Enable → Remind.

    Arrow-key driven replacement for the old free-text Configure flow. Esc steps
    BACK one step (Esc on the first step cancels); Time/Lead offer presets plus a
    "Custom…" escape hatch into the themed line editor. On completion the config
    is merge-saved (identity + scope fields untouched) and the OS schedule is
    installed or removed; the returned message surfaces on the hub.
    """
    from yeaboi.standup.delivery import ALL_CHANNELS
    from yeaboi.standup.scheduler import (
        JOB_TRANSCRIPT_REMINDER,
        install_schedule,
        install_transcript_reminder,
        parse_time,
        remove_schedule,
        transcript_reminder_offset,
        weekday_list,
        weekday_spec,
    )
    from yeaboi.standup.store import StandupStore

    with StandupStore(_ana_dbp) as store:
        existing = store.load_config(session_id) or {}
    time_val = existing.get("time", "10:00")
    lead_val = int(existing.get("lead_minutes", 10))
    days = set(weekday_list(existing.get("weekdays", "1-5")))
    channels = [c for c in existing.get("delivery_channels", ["terminal"]) if c in ALL_CHANNELS] or ["terminal"]
    enabled = bool(existing.get("enabled"))
    # The installed job is the source of truth for the reminder — nothing in the
    # database records it, so we ask the OS. Read the OFFSET back too, not just
    # whether one exists: a user who picked "2 hours after" and later reopens
    # this wizard to change a delivery channel would otherwise Enter past a step
    # showing the default and silently reinstall the job at 30 minutes.
    remind_after = transcript_reminder_offset(session_id, time_val)
    if remind_after and remind_after not in _REMINDER_PRESETS:
        # The standup time can move after the reminder was installed, so the
        # recovered offset need not land on a preset. Snap to the nearest one
        # rather than falling through to "No reminder", which would tear the job
        # down for a user who came here to change something else entirely.
        remind_after = min((p for p in _REMINDER_PRESETS if p), key=lambda p: abs(p - remind_after))
    logger.info("standup schedule wizard: opened (session=%s)", session_id)

    def _custom_text(prompt: str, step: str, default: str, parse) -> str | None:
        """Line-input loop for the Custom… options: re-prompts until ``parse``
        accepts the value; Esc returns None (back to the option list)."""
        current_prompt = prompt
        while True:
            raw = _standup_read_line(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                prompt=current_prompt,
                step=step,
                default=default,
            )
            if raw is None:
                return None
            try:
                return parse(raw)
            except (ValueError, TypeError):
                current_prompt = f"Invalid value — {prompt}"

    index = 0
    while index < 6:
        if index == 0:
            options = [(t, "") for t in _SCHEDULE_TIME_PRESETS]
            if time_val in _SCHEDULE_TIME_PRESETS:
                initial = _SCHEDULE_TIME_PRESETS.index(time_val)
                options.append(("Custom…", "type any HH:MM"))
            else:
                initial = len(options)
                options.append(("Custom…", f"currently {time_val}"))
            got = _run_schedule_choice_step(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                options=options,
                initial=initial,
                step_index=0,
                heading="Standup time — when the meeting happens",
            )
            if got == "back":
                logger.info("standup schedule wizard: cancelled at step 1 (session=%s)", session_id)
                return "Schedule setup cancelled."
            if isinstance(got, int) and got < len(_SCHEDULE_TIME_PRESETS):
                time_val = _SCHEDULE_TIME_PRESETS[got]
                index += 1
            else:

                def _parse_hhmm(raw: str) -> str:
                    parse_time(raw)  # raises ValueError on bad input
                    return raw.strip()

                custom = _custom_text("Standup time (HH:MM)", "Set up schedule  (1/6)", time_val, _parse_hhmm)
                if custom is not None:
                    time_val = custom
                    index += 1
        elif index == 1:
            labels = [(f"{m} minutes before", "") for m in _SCHEDULE_LEAD_PRESETS]
            if lead_val in _SCHEDULE_LEAD_PRESETS:
                initial = _SCHEDULE_LEAD_PRESETS.index(lead_val)
                labels.append(("Custom…", "type any number of minutes"))
            else:
                initial = len(labels)
                labels.append(("Custom…", f"currently {lead_val} min"))
            got = _run_schedule_choice_step(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                options=labels,
                initial=initial,
                step_index=1,
                heading="Deliver how long before the standup?",
            )
            if got == "back":
                index -= 1
            elif isinstance(got, int) and got < len(_SCHEDULE_LEAD_PRESETS):
                lead_val = _SCHEDULE_LEAD_PRESETS[got]
                index += 1
            else:
                custom = _custom_text(
                    "Minutes before the standup",
                    "Set up schedule  (2/6)",
                    str(lead_val),
                    lambda raw: max(0, int(raw)),
                )
                if custom is not None:
                    lead_val = custom
                    index += 1
        elif index == 2:
            got = _run_schedule_multi_step(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                options=[(name, "") for name in _SCHEDULE_DAY_NAMES],
                initial={d - 1 for d in days},
                step_index=2,
                heading="Which days should it run?",
                empty_message="Select at least one day.",
            )
            if got == "back":
                index -= 1
            else:
                days = {i + 1 for i in got}
                index += 1
        elif index == 3:
            got = _run_schedule_multi_step(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                options=[(ch, _SCHEDULE_CHANNEL_DESCS.get(ch, "")) for ch in ALL_CHANNELS],
                initial={i for i, ch in enumerate(ALL_CHANNELS) if ch in channels},
                step_index=3,
                heading="Where should the summary go?",
                empty_message="Select at least one delivery channel.",
            )
            if got == "back":
                index -= 1
            else:
                channels = [ALL_CHANNELS[i] for i in sorted(got)]
                index += 1
        elif index == 4:
            got = _run_schedule_choice_step(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                options=[
                    ("On — install an OS schedule", "launchd/cron runs it automatically"),
                    ("Off — run on demand only", "no background job"),
                ],
                initial=0 if enabled else 1,
                step_index=4,
                heading="Enable scheduled runs?",
            )
            if got == "back":
                index -= 1
            else:
                enabled = got == 0
                index += 1
        else:
            initial = _REMINDER_PRESETS.index(remind_after) if remind_after in _REMINDER_PRESETS else 0
            got = _run_schedule_choice_step(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                options=[
                    ("No reminder", "the standup itself will mention what went unchecked"),
                    ("30 minutes after", "while the recording is still fresh"),
                    ("1 hour after", ""),
                    ("2 hours after", ""),
                ],
                initial=initial,
                step_index=5,
                heading="Remind you to drop the meeting transcript?",
            )
            if got == "back":
                index -= 1
            else:
                remind_after = _REMINDER_PRESETS[got]
                index += 1
        logger.info("standup schedule wizard: moved to step %d (session=%s)", index, session_id)

    weekdays = weekday_spec(days)
    with StandupStore(_ana_dbp) as store:
        store.save_config(
            session_id,
            enabled=enabled,
            time=time_val,
            lead_minutes=lead_val,
            weekdays=weekdays,
            delivery_channels=channels,
            timezone=existing.get("timezone", ""),
            repo_path=existing.get("repo_path", ""),
            my_aliases=existing.get("my_aliases", ""),
            tracker_sources=existing.get("tracker_sources", ["jira"]),
            team_members=existing.get("team_members", []),
            roster_configured=existing.get("roster_configured", False),
            code_sources=existing.get("code_sources", []),
            github_owners=existing.get("github_owners", []),
            github_repositories=existing.get("github_repositories", []),
            github_excluded_repositories=existing.get("github_excluded_repositories", []),
            azdo_projects=existing.get("azdo_projects", []),
            azdo_repositories=existing.get("azdo_repositories", []),
            code_scope_configured=existing.get("code_scope_configured", False),
            documentation_sources=existing.get("documentation_sources", []),
            documentation_scope_configured=existing.get("documentation_scope_configured", False),
            automation_markers=existing.get("automation_markers", ""),
            automation_handling=existing.get("automation_handling", "exclude"),
            transcript_dir=existing.get("transcript_dir", ""),
            transcript_review_enabled=existing.get("transcript_review_enabled", True),
            habit_detection=existing.get("habit_detection", "on"),
            habit_rules=existing.get("habit_rules", ""),
            habit_ai_match=existing.get("habit_ai_match", "on"),
        )
    if enabled:
        msg = install_schedule(session_id, time_val, weekdays, lead_val)
        # A reminder is only meaningful alongside a scheduled standup; when the
        # schedule is off, remove_schedule below tears BOTH kinds down, so a
        # disabled standup can never leave notifications firing.
        if remind_after:
            msg += "  " + install_transcript_reminder(session_id, time_val, weekdays, remind_after)
        else:
            remove_schedule(session_id, kind=JOB_TRANSCRIPT_REMINDER)
    else:
        msg = remove_schedule(session_id)  # every kind
    logger.info(
        "standup schedule wizard: saved session=%s enabled=%s remind_after=%s -> %s",
        session_id,
        enabled,
        remind_after,
        msg,
    )
    return msg


def _standup_identity_configure(console: Console, live, read_key, frame_time, supports_timeout, session_id: str) -> str:
    """Collect identity settings (repo path + aliases) in-TUI and merge-save them.

    Schedule settings moved to the hub wizard (``_run_standup_schedule_wizard``);
    this slim flow keeps the two fields that attribute YOUR activity. No scheduler
    calls — the saved schedule/scope fields pass through untouched. Esc at either
    field cancels. Returns a status message for the dashboard.
    """
    from yeaboi.standup.store import StandupStore

    with StandupStore(_ana_dbp) as store:
        existing = store.load_config(session_id) or {}

    repo_in = _standup_read_line(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        prompt="Local git repo path (optional)",
        step="Identity  (1/2)",
        default=existing.get("repo_path", ""),
    )
    if repo_in is None:
        logger.info("standup identity: cancelled at step 1 (session=%s)", session_id)
        return "Identity cancelled."
    if repo_in.strip() and repo_in.strip() != existing.get("repo_path", ""):
        # Sandbox pre-flight (main thread): the scheduled standup runs headless
        # later, where a denial cannot ask — so consent is collected here, at
        # the moment the user types the path.
        from yeaboi.ui.shared._consent import _preflight_path_consent

        if not _preflight_path_consent(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            repo_in.strip(),
            mode="read",
            context="Daily Standup — local repo scan",
        ):
            return "Repo path denied — identity cancelled. Allow it via Settings → Paths."
    # Aliases let your activity (GitHub handle, Jira display name, commit email)
    # attach to YOUR standup card even when the names don't match exactly.
    aliases_in = _standup_read_line(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        prompt="Your aliases across tools (comma-separated, e.g. GitHub handle, Jira name)",
        step="Identity  (2/2)",
        default=existing.get("my_aliases", ""),
    )
    if aliases_in is None:
        logger.info("standup identity: cancelled at step 2 (session=%s)", session_id)
        return "Identity cancelled."

    with StandupStore(_ana_dbp) as store:
        store.save_config(
            session_id,
            enabled=bool(existing.get("enabled")),
            time=existing.get("time", "10:00"),
            lead_minutes=int(existing.get("lead_minutes", 10)),
            weekdays=existing.get("weekdays", "1-5"),
            delivery_channels=existing.get("delivery_channels", ["terminal"]),
            timezone=existing.get("timezone", ""),
            repo_path=repo_in,
            my_aliases=aliases_in.strip(),
            tracker_sources=existing.get("tracker_sources", ["jira"]),
            team_members=existing.get("team_members", []),
            roster_configured=existing.get("roster_configured", False),
            code_sources=existing.get("code_sources", []),
            github_owners=existing.get("github_owners", []),
            github_repositories=existing.get("github_repositories", []),
            github_excluded_repositories=existing.get("github_excluded_repositories", []),
            azdo_projects=existing.get("azdo_projects", []),
            azdo_repositories=existing.get("azdo_repositories", []),
            code_scope_configured=existing.get("code_scope_configured", False),
            documentation_sources=existing.get("documentation_sources", []),
            documentation_scope_configured=existing.get("documentation_scope_configured", False),
            automation_markers=existing.get("automation_markers", ""),
            automation_handling=existing.get("automation_handling", "exclude"),
            transcript_dir=existing.get("transcript_dir", ""),
            transcript_review_enabled=existing.get("transcript_review_enabled", True),
            habit_detection=existing.get("habit_detection", "on"),
            habit_rules=existing.get("habit_rules", ""),
            habit_ai_match=existing.get("habit_ai_match", "on"),
        )
    logger.info("standup identity: saved (session=%s)", session_id)
    return "Identity saved."


def _run_changelog_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Event loop for the Changelog page (opened with `c` from mode select).

    Read-only: Up/Down scrolls the release notes, Enter/Esc/q returns to mode
    select. Data is the bundled ``changelog_data.json`` (no network); the upgrade
    banner reflects whatever the background PyPI check has found so far.
    """
    from yeaboi.changelog import load_changelog
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_changelog_screen
    from yeaboi.update_check import get_update_status

    entries = load_changelog()
    update_status = get_update_status()
    logger.info(
        "changelog: page opened (%d entries, update_available=%s)", len(entries), update_status["update_available"]
    )
    scroll = 0
    _scroll_meta: dict = {}
    message = ""
    anim_start = time.monotonic()  # typewriter subtitle clock

    def _render() -> None:
        w, h = console.size
        elapsed = time.monotonic() - anim_start
        # One-row safety margin — same as the other pages (see _run_standup_page).
        live.update(
            _build_changelog_screen(
                entries,
                update_status=update_status,
                scroll_offset=scroll,
                scroll_meta=_scroll_meta,
                width=w,
                height=max(10, h - 1),
                # No shimmer clock: the title's travelling highlight reads as a
                # loader on a page that has nothing to load, so it stays solid.
                shimmer_tick=None,
                sub_reveal=elapsed * _HEADER_SUB_SPEED,
                message=message,
            )
        )

    _render()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        # Read-only page with no buttons of its own — a click has nothing to hit
        # here (the app-wide chrome tabs are handled by the shared input layer).
        if parse_click(k) is not None:
            continue
        if k in SCROLL_KEYS:
            _ns = coalesce_scroll(scroll, k, _scroll_meta, read_key)
            if _ns == scroll:
                continue
            scroll = _ns
        elif k in ("esc", "q"):
            break
        _render()
    logger.info("changelog: page closed")


def _run_all_tips_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Event loop for the All Tips page (opened with `a` from mode select).

    Read-only gallery of every tip: Up/Down scrolls, Enter/Esc/q returns to mode
    select. Mirrors ``_run_changelog_page`` — including having no actions of its
    own; content comes live from ``get_tips()``.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_all_tips_screen

    logger.info("all tips: page opened")
    scroll = 0
    _scroll_meta: dict = {}
    anim_start = time.monotonic()  # typewriter subtitle clock

    def _render() -> None:
        w, h = console.size
        elapsed = time.monotonic() - anim_start
        live.update(
            _build_all_tips_screen(
                scroll_offset=scroll,
                scroll_meta=_scroll_meta,
                width=w,
                height=max(10, h - 1),
                # No shimmer clock — the travelling title highlight reads as a
                # loader on a page that opens instantly (see the changelog).
                shimmer_tick=None,
                sub_reveal=elapsed * _HEADER_SUB_SPEED,
            )
        )

    _render()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        # Read-only page with no buttons of its own — a click has nothing to hit.
        if parse_click(k) is not None:
            continue
        if k in SCROLL_KEYS:
            _ns = coalesce_scroll(scroll, k, _scroll_meta, read_key)
            if _ns == scroll:
                continue
            scroll = _ns
        elif k in ("esc", "q"):
            break
        _render()
    logger.info("all tips: page closed")


def _run_feedback_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Event loop for the Feedback page (opened with `f` from mode select).

    A small two-zone form (Type / Area / Title / Description rows + Submit /
    AI Polish / Back buttons) that files a GitHub issue on the yeaboi repo:
    via the API when GITHUB_TOKEN is set, else by opening a pre-filled
    ``issues/new`` URL in the browser. Title/Description entry reuses
    ``_standup_read_line`` so voice dictation (double-tap Space) and Ctrl+V
    screenshot paste work for free; the optional AI Polish step previews an
    LLM rewrite the user can accept or discard.
    """
    import webbrowser

    from yeaboi.feedback import FEEDBACK_AREAS, FEEDBACK_TYPES, polish_feedback, submit_feedback
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_feedback_screen
    from yeaboi.ui.shared._attachments import referenced_images
    from yeaboi.ui.shared._components import FEEDBACK_THEME, build_page_panel, build_popup, feedback_title

    with mode_log("feedback"):
        logger.info("feedback: page opened")
        kind_idx, area_idx = 0, 0
        title_text, description = "", ""
        attachments: list[str] = []
        field_sel, focus, action_sel = 0, "fields", 0
        view, status = "form", ""
        polished: tuple[str, str] | None = None
        result = None  # FeedbackResult after a submit attempt
        scroll = 0
        _scroll_meta: dict = {}
        anim_start = time.monotonic()
        _last_panel = None  # most recently rendered feedback panel, for click hit-testing

        def _render(*, border_style: str = "") -> None:
            nonlocal _last_panel
            w, h = console.size
            elapsed = time.monotonic() - anim_start
            _last_panel = _build_feedback_screen(
                view,
                kind_idx=kind_idx,
                area_idx=area_idx,
                title_text=title_text,
                description=description,
                attachments_count=len(referenced_images(description, attachments)),
                field_sel=field_sel,
                focus=focus,
                action_sel=action_sel,
                polished=polished,
                result_url=result.url if result else "",
                show_open_browser=bool(result and not result.ok and result.url),
                status=status,
                scroll_offset=scroll,
                scroll_meta=_scroll_meta,
                width=w,
                height=max(10, h - 1),
                # No shimmer clock — the travelling title highlight reads as a
                # loader, and this page is a form, not a wait (see the changelog).
                shimmer_tick=None,
                sub_reveal=elapsed * _HEADER_SUB_SPEED,
                border_style=border_style,
            )
            live.update(_last_panel)

        def _run_busy(target, busy_label: str) -> list:
            """Run ``target`` on a daemon thread with a pulsing border; keys are swallowed."""
            nonlocal view, status
            prev_view, prev_status = view, status
            view, status = "busy", busy_label
            out: list = []
            thread = duck_working_thread(lambda: out.append(target()), name="busy")
            thread.start()
            pulse_start = time.monotonic()
            while thread.is_alive():
                elapsed = time.monotonic() - pulse_start
                intensity = (math.sin(elapsed * 6) + 1) / 2
                v = int(60 + 140 * intensity)
                _render(border_style=f"rgb({v},{v},{v})")
                time.sleep(frame_time)
            thread.join()
            view, status = prev_view, prev_status
            return out

        def _confirm_discard() -> bool:
            """Popup guard so Esc can't silently destroy a long draft."""
            from rich.align import Align
            from rich.console import Group
            from rich.text import Text

            while True:
                w, h = console.size
                lines: list = [Text(""), feedback_title(width=w), Text("")]
                lines.append(
                    Align.center(
                        build_popup(
                            "Discard this feedback draft?\nEnter discard  ·  Esc keep editing",
                            width=min(w - 8, 50),
                            border_style=FEEDBACK_THEME.warn,
                        )
                    )
                )
                live.update(build_page_panel(Group(*lines), theme=FEEDBACK_THEME, height=max(10, h - 1)))
                k = read_key(timeout=frame_time) if supports_timeout else read_key()
                if not k:
                    continue
                if k == "enter":
                    return True
                if k in ("esc", "q"):
                    return False

        def _edit_field(which: str) -> None:
            nonlocal title_text, description
            if which == "title":
                new = _standup_read_line(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    prompt="Title",
                    step="Feedback · a one-line summary",
                    theme=FEEDBACK_THEME,
                    title=feedback_title(),
                    initial=title_text,
                )
                if new is not None:
                    title_text = new
            else:
                new = _standup_read_line(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    prompt="Description",
                    step="Feedback · what happened / what you'd like",
                    theme=FEEDBACK_THEME,
                    title=feedback_title(),
                    box_rows=8,
                    attachments=attachments,
                    scope_id="feedback",
                    initial=description,
                )
                if new is not None:
                    description = new
            logger.info("feedback: %s edited (%d chars)", which, len(title_text if which == "title" else description))

        def _do_submit() -> None:
            nonlocal view, status, result, focus, field_sel, action_sel, scroll
            if not title_text.strip():
                status = "Title is required"
                focus, field_sel = "fields", 2
                logger.info("feedback: submit blocked — empty title")
                return
            kind = FEEDBACK_TYPES[kind_idx]
            area = FEEDBACK_AREAS[area_idx]
            images = referenced_images(description, attachments)
            out = _run_busy(lambda: submit_feedback(kind, area, title_text, description, images), "Submitting…")
            result = out[0] if out else None
            view, status, action_sel, scroll = "result", result.message if result else "Submission failed", 0, 0

        def _do_polish() -> None:
            nonlocal view, status, polished, action_sel, scroll
            if not title_text.strip() and not description.strip():
                status = "Write a title or description first"
                return
            kind = FEEDBACK_TYPES[kind_idx]
            area = FEEDBACK_AREAS[area_idx]
            images = referenced_images(description, attachments)
            out = _run_busy(lambda: polish_feedback(kind, area, title_text, description, images), "AI polishing…")
            new_polished, msg = out[0] if out else (None, "AI polish failed")
            if new_polished is not None:
                polished, status, action_sel, scroll = new_polished, "", 0, 0
                view = "polish_preview"
            else:
                status = msg

        _render()
        while True:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            if not k:
                _render()
                continue

            # ── Mouse: click an action button (works across every view) ──
            _clicked = parse_click(k)
            if _clicked is not None:
                if view == "form":
                    _labels = ["Submit", "AI Polish", "Back"]
                elif view == "polish_preview":
                    _labels = ["Accept", "Keep Original"]
                elif view == "result":
                    _labels = ["Done", "Open Browser"] if bool(result and not result.ok and result.url) else ["Done"]
                else:
                    _labels = []
                _idx = button_click(console, _last_panel, *_clicked, _labels) if (_labels and _last_panel) else None
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                action_sel = _idx
                if view == "form":
                    focus = "buttons"  # route the synthesized Enter to the button action
                k = "enter"  # fall through to the existing Enter handling

            if view == "form":
                status = "" if k in ("enter", " ") else status
                if focus == "fields":
                    if k in ("up", "scroll_up"):
                        field_sel = max(0, field_sel - 1)
                    elif k in ("down", "scroll_down"):
                        if field_sel >= 3:
                            focus, action_sel = "buttons", 0
                        else:
                            field_sel += 1
                    elif k in ("left", "right") and field_sel == 0:
                        kind_idx = (kind_idx + (1 if k == "right" else -1)) % len(FEEDBACK_TYPES)
                    elif k in ("left", "right") and field_sel == 1:
                        area_idx = (area_idx + (1 if k == "right" else -1)) % len(FEEDBACK_AREAS)
                    elif k in ("left", "right"):
                        focus, action_sel = "buttons", 0
                    elif k == "enter":
                        if field_sel == 0:
                            kind_idx = (kind_idx + 1) % len(FEEDBACK_TYPES)
                        elif field_sel == 1:
                            area_idx = (area_idx + 1) % len(FEEDBACK_AREAS)
                        elif field_sel == 2:
                            _edit_field("title")
                        else:
                            _edit_field("description")
                    elif k in ("esc", "q"):
                        if not title_text.strip() and not description.strip():
                            break
                        if _confirm_discard():
                            logger.info("feedback: draft discarded")
                            break
                else:  # buttons: Submit / AI Polish / Back
                    if k == "left":
                        action_sel = max(0, action_sel - 1)
                    elif k == "right":
                        action_sel = min(2, action_sel + 1)
                    elif k in ("up", "scroll_up"):
                        focus, field_sel = "fields", 3
                    elif k == "enter":
                        if action_sel == 0:
                            _do_submit()
                        elif action_sel == 1:
                            _do_polish()
                        else:
                            if not title_text.strip() and not description.strip():
                                break
                            if _confirm_discard():
                                logger.info("feedback: draft discarded")
                                break
                    elif k in ("esc", "q"):
                        if not title_text.strip() and not description.strip():
                            break
                        if _confirm_discard():
                            logger.info("feedback: draft discarded")
                            break

            elif view == "polish_preview":
                if k in SCROLL_KEYS:
                    _ns = coalesce_scroll(scroll, k, _scroll_meta, read_key)
                    if _ns != scroll:
                        scroll = _ns
                elif k == "left":
                    action_sel = max(0, action_sel - 1)
                elif k == "right":
                    action_sel = min(1, action_sel + 1)
                elif k == "enter":
                    if action_sel == 0 and polished is not None:  # Accept
                        title_text, description = polished
                        logger.info("feedback: AI polish accepted")
                    else:
                        logger.info("feedback: AI polish discarded — keeping original")
                    polished, view, status, scroll = None, "form", "", 0
                    focus, action_sel = "buttons", 0
                elif k in ("esc", "q"):  # Esc = Keep Original
                    polished, view, status, scroll = None, "form", "", 0
                    focus, action_sel = "buttons", 0

            elif view == "result":
                has_browser_btn = bool(result and not result.ok and result.url)
                if k in SCROLL_KEYS:
                    _ns = coalesce_scroll(scroll, k, _scroll_meta, read_key)
                    if _ns != scroll:
                        scroll = _ns
                elif k == "left":
                    action_sel = max(0, action_sel - 1)
                elif k == "right" and has_browser_btn:
                    action_sel = min(1, action_sel + 1)
                elif k == "enter":
                    if action_sel == 1 and has_browser_btn and result:
                        try:
                            webbrowser.open(result.url)
                            logger.info("feedback: opened fallback browser URL")
                        except Exception as exc:
                            logger.warning("feedback: browser open failed: %s", exc)
                    else:
                        break  # Done
                elif k in ("esc", "q"):
                    break
            _render()
        logger.info("feedback: page closed")


def _run_mode_hub(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    mode: str,
    title_fn,
    subtitle: str,
    empty_title: str,
    empty_subtitle: str,
    new_label: str,
    load_runs,
    files_export,
    get_document,
    delete_run,
    run_new,
    make_detail=None,
    open_snapshot=None,
    get_share_document=None,
    # Returns an artifacts.session.EditableSession, or None for a mode whose
    # shared document is read-only. Absent means read-only everywhere.
    get_editable_session=None,
    share_theme=None,
    new_breaks_out: bool = False,
    extra_label=None,
    extra_action=None,
) -> None:
    """Generic saved-runs hub loop shared by standup / retro / reporting.

    Landing screen for a mode: a browsable list of past runs (``load_runs`` →
    ``RunSummary``s). On a selected run row, Left/Right move focus across
    [card, Delete, Export]; Enter on the card opens the read-only snapshot, on
    Delete raises a confirm popup, on Export opens the shared destination picker.
    The "+ New run" card runs the mode's live page (``run_new``) then reloads. This
    is the TUI half of the "Saved-Sessions Hub" — the store already kept every run;
    this makes them openable / deletable / exportable instead of latest-only.

    All the per-mode behaviour is injected as callables so retro/standup/reporting
    share one loop. Performance uses ``_run_performance_hub`` (per-engineer, mixed
    artifact kinds) but reuses the same screen builders.

    Opening a saved run renders it through the mode's OWN rich screen builder (the
    same one its live page uses) so a snapshot looks identical to the live view —
    themed, with meters / section cards / grids — not flat grey text. ``make_detail``
    (run) loads the report once and returns a per-frame ``render(scroll, action_sel,
    actions, scroll_meta, width, height, message, shimmer_tick) -> Panel`` (or None if
    the run vanished). Standup needs section drill-in beyond plain scroll, so it passes
    an ``open_snapshot`` override instead; the other three use the shared scroll loop.

    ``extra_label``/``extra_action`` (both optional callables) add one fixed card
    below "+ New run" — standup uses it for "Set up a schedule". ``extra_label()``
    returns the card text (recomputed on every reload so status stays fresh; empty
    string hides the card); Enter on it calls ``extra_action()`` and shows the
    returned message. Modes that pass neither are byte-identical to before.
    """
    from yeaboi.ui.mode_select.screens._run_hub_screen import _build_run_hub_screen
    from yeaboi.ui.shared._click import parse_click
    from yeaboi.ui.shared._duck_voice import duck_voice

    voice = duck_voice()  # toasts + the delete confirmation speak through the duck
    runs = load_runs()
    extra_text = extra_label() if extra_label is not None else ""
    selected = 0
    focus = 0  # 0 = card, 1 = Delete, 2 = Export (only on a run row)
    message = ""
    confirm = False  # delete-confirmation popup showing
    anim_start = time.monotonic()
    _last_panel = None  # most recently rendered hub panel, for click hit-testing
    logger.info("%s hub: opened (%d saved run(s))", mode, len(runs))

    def _n_items() -> int:
        return len(runs) + 1 + (1 if extra_text else 0)

    def _reload(msg: str = "") -> None:
        nonlocal runs, selected, focus, message, confirm, extra_text
        runs = load_runs()
        extra_text = extra_label() if extra_label is not None else ""
        selected = min(selected, _n_items() - 1)  # keep within the item range
        focus = 0
        confirm = False
        voice.clear_sticky()  # any pending delete confirmation is moot now
        message = msg
        if msg:
            logger.info("%s hub: %s", mode, msg)
            voice.say(msg)

    def _render_list() -> None:
        nonlocal _last_panel
        w, h = console.size
        tick = time.monotonic() - anim_start
        on_run = selected < len(runs)
        _last_panel = _build_run_hub_screen(
            runs,
            selected,
            title_fn=title_fn,
            theme=share_theme,
            subtitle=subtitle,
            message=message,
            width=w,
            height=max(10, h - 1),
            focus=focus if on_run else 0,
            del_fade=1.0 if (on_run and focus == 1) else 0.0,
            exp_fade=1.0 if (on_run and focus == 2) else 0.0,
            card_fade=1.0,
            action_btns_visible=2.0 if on_run else 0.0,
            delete_popup_name=(runs[selected].title if (confirm and on_run) else ""),
            delete_popup_t=1.0 if confirm else 0.0,
            new_label=new_label,
            empty_title=empty_title,
            empty_subtitle=empty_subtitle,
            shimmer_tick=tick,
            extra_label=extra_text,
        )
        live.update(_last_panel)

    def _run_action(run, act: str) -> tuple[bool, str | None]:
        """Perform a snapshot action button. Returns (leave_snapshot, message).

        message is None when nothing should change on screen (e.g. Export cancelled).
        Shared by the generic scroll loop and the standup section-drill override so the
        Export / Delete / Run again / Back behaviour stays identical across modes.
        """
        if act == "Back":
            return True, None
        if act == "Export":
            return False, _export_via_picker(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                mode=mode,
                files_export=lambda r=run: files_export(r),
                get_document=lambda r=run: get_document(r),
            )
        if act == "Share Online" and get_share_document is not None:
            document = get_share_document(run)
            if document is None:
                return False, "That artifact cannot be shared."
            # A correctable share when the mode offers one; otherwise exactly
            # the read-only publish this has always been.
            editing = get_editable_session(run) if get_editable_session is not None else None
            # The session holds a lease while it is open, so a writer that
            # cannot see this screen — a practice verdict arriving from Slack —
            # defers its rewrite instead of being resurrected by the commit
            # below. `finally`, because this flow also ends on Esc, on Back and
            # on an exception, and only `commit` releases on its own.
            try:
                recorded = _run_output_share_flow(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    document=document,
                    theme=share_theme,
                    title_fn=title_fn,
                    editable=editing.share if editing is not None else None,
                    on_edit=editing.persist if editing is not None else None,
                )
                if recorded and editing is not None:
                    # Appended, so the generated original is still there and every
                    # trend chart picks the corrected row up on its own.
                    editing.commit()
                    _reload(f"Saved {recorded} " + ("correction" if recorded == 1 else "corrections") + ".")
                    return False, None
                return False, None
            finally:
                if editing is not None:
                    editing.close()
        if act == "Delete":
            delete_run(run)
            _reload("Run deleted.")
            return True, None
        if act == "Run again":
            run_new()
            _reload("New run recorded.")
            return True, None
        return False, None

    def _open_snapshot(run) -> None:
        """Read-only view of one saved run rendered through the mode's rich builder.

        Standup overrides this (``open_snapshot``) for section drill-in; the other three
        modes use this shared loop: Up/Down scroll the report, Left/Right move across the
        [Export, Delete, Run again, Back] buttons, Enter presses one. Scroll is clamped to
        the geometry the builder publishes into ``scroll_meta`` (the live-page pattern).
        """
        if open_snapshot is not None:
            # The override drives the same actions via a run-bound callable: run_action(act).
            open_snapshot(run, lambda act, r=run: _run_action(r, act))
            return
        render = make_detail(run)
        if render is None:
            _reload("That run is no longer available.")
            return
        scroll = 0
        sel = 0
        actions = ["Export"]
        if get_share_document is not None and get_share_document(run) is not None:
            actions.append("Share Online")
        actions.extend(["Delete", "Run again", "Back"])
        msg = ""
        s_anim = time.monotonic()
        logger.info("%s hub: opened run id=%s", mode, run.run_id)

        _snap_panel = None  # most recently rendered snapshot panel, for click hit-testing

        def _render_snap() -> None:
            nonlocal scroll, _snap_panel
            w, h = console.size
            scroll_meta: dict = {}
            panel = render(
                scroll=scroll,
                action_sel=sel,
                actions=actions,
                scroll_meta=scroll_meta,
                width=w,
                height=max(10, h - 1),
                message=msg,
                shimmer_tick=time.monotonic() - s_anim,
            )
            # The builder reports its max scroll only after laying the body out — clamp
            # here so held Down keys don't run the offset past the end.
            scroll = max(0, min(scroll, scroll_meta.get("max_scroll", scroll)))
            _snap_panel = panel
            live.update(_snap_panel)

        _render_snap()
        while True:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _snap_panel is not None:
                    _idx = button_click(console, _snap_panel, *_clicked, actions)
                    if _idx is not None:
                        sel = _idx
                        k = "enter"  # fall through to the existing Enter handling
                    else:
                        continue  # click missed the buttons — ignore it
                else:
                    continue
            if k in SCROLL_KEYS:
                step = 1 if k in ("down", "scroll_down", "pagedown") else -1
                scroll = max(0, scroll + step)
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(len(actions) - 1, sel + 1)
            elif k in ("enter", " "):
                leave, m = _run_action(run, actions[sel])
                if leave:
                    return
                if m is not None:
                    msg = m
            elif k in ("esc", "q"):
                return
            _render_snap()

    _render_list()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        n_items = _n_items()
        on_run = selected < len(runs)
        on_extra = extra_text and selected == len(runs) + 1

        # ── Mouse: click a card to open it, or the "+ New" card to start a run ──
        _pos = parse_click(k)
        if _pos is not None and not confirm:
            _cx, _cy = _pos
            # Delete/Export buttons sit to the RIGHT of the selected run card and
            # share its rows, so they must be tested (with x) BEFORE the y-only card
            # regions — otherwise a button click would land on the card. The buttons
            # belong to the currently-selected card, so a hit acts on ``selected``:
            # set the matching focus and reuse the existing Enter code paths below.
            _btn = next(
                (
                    label
                    for x0, y0, x1, y1, label in getattr(_last_panel, "_btn_regions", []) or []
                    if x0 <= _cx <= x1 and y0 <= _cy <= y1
                ),
                None,
            )
            if _btn is not None:
                focus = 1 if _btn == "delete" else 2
                k = "enter"  # fall through to the existing focus==1/2 Enter handling
            else:
                _hit = next(
                    (idx for y0, y1, idx in getattr(_last_panel, "_card_regions", []) or [] if y0 <= _cy <= y1),
                    None,
                )
                if _hit is not None:
                    selected, focus = _hit, 0
                    if _hit >= len(runs):  # the "+ New" card
                        if new_breaks_out:
                            break  # Performance: hand control back to the roster
                        run_new()
                        _reload("New run recorded.")
                    else:
                        _open_snapshot(runs[_hit])
                _render_list()
                continue
        if confirm:
            # Delete-confirmation popup is modal: Enter confirms, Esc cancels.
            if k in ("enter", " "):
                run = runs[selected]
                delete_run(run)
                _reload("Run deleted.")
            elif k in ("esc", "q"):
                confirm = False
                voice.clear_sticky()
            _render_list()
            continue
        if k in SCROLL_KEYS or k in ("up", "down"):
            step = 1 if k in ("down", "scroll_down", "pagedown") else -1
            selected = (selected + step) % n_items
            focus = 0
        elif k == "left":
            focus = max(0, focus - 1)
        elif k == "right":
            if on_run:
                focus = min(2, focus + 1)
        elif k in ("enter", " "):
            if on_extra:
                # The fixed extra card (e.g. standup's schedule setup) runs its
                # action in place, then reloads so its status label refreshes.
                msg = extra_action() if extra_action is not None else None
                _reload(msg or "")
                _render_list()
                continue
            if not on_run:
                if new_breaks_out:
                    # Performance: "+ New" hands control back to the roster (where the
                    # create actions live) instead of running a live page in place.
                    break
                # "+ New run" card → run the live page, then reload the list.
                run_new()
                _reload("New run recorded.")
                _render_list()
                continue
            run = runs[selected]
            if focus == 0:
                _open_snapshot(run)
            elif focus == 1:
                confirm = True
                # Cap the title so the prompt (with its load-bearing "Enter to
                # confirm") fits the bubble even on an 84-col terminal — sticky
                # lines are never truncated by the chrome.
                _t = run.title if len(run.title) <= 18 else run.title[:17].rstrip() + "…"
                voice.say_sticky(f'Delete "{_t}"?  Enter to confirm')
            elif focus == 2:
                msg = _export_via_picker(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    mode=mode,
                    files_export=lambda r=run: files_export(r),
                    get_document=lambda r=run: get_document(r),
                )
                if msg is not None:
                    message = msg
                    logger.info("%s hub: %s", mode, msg)
                    voice.say(msg)
        elif k in ("esc", "q"):
            break
        _render_list()
    voice.clear_sticky()  # never carry a modal prompt onto the next page
    logger.info("%s hub: closed", mode)


def _run_standup_hub(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Standup saved-runs hub → landing for the Standup card (was: straight into latest)."""
    from yeaboi.persistence import _relative_time
    from yeaboi.standup.export import build_standup_markdown, export_standup
    from yeaboi.standup.store import StandupStore
    from yeaboi.ui.mode_select.screens._project_cards import RunSummary
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_screen
    from yeaboi.ui.mode_select.screens._standup_sections import standup_card_order
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    def _report(run_id: int):
        with StandupStore(_ana_dbp) as store:
            return store.get_run_by_id(run_id)

    def load_runs():
        with StandupStore(_ana_dbp) as store:
            rows = store.get_all_history(100)
        out = []
        for r in rows:
            date = r.get("standup_date") or ""
            conf = r.get("confidence_pct", 0)
            sub = f"Day {r.get('sprint_day', 0)} · {conf}% confident" if conf else "standup"
            out.append(
                RunSummary(
                    "standup",
                    r["id"],
                    f"Standup — {date or _relative_time(r['run_at'])}",
                    sub,
                    _relative_time(r["run_at"]),
                    session_id=r.get("session_id", ""),
                )
            )
        return out

    def open_standup_snapshot(run, run_action) -> None:
        """Read-only standup snapshot with section drill-in (the live overview, replayed).

        The overview is just a section list, so a flat scroll would show less than the
        markdown did. Instead this replays the live standup screen: ↑/↓ move the section
        selection, Enter opens that section's detail (or toggles the Team row's inline
        member rows, matching the live board), Esc returns to the overview. Left/Right +
        Enter drive the shared [Export, Delete, Run again, Back] actions via ``run_action``.
        """
        report = _report(run.run_id)
        if report is None:
            return
        # StandupReport has no project name; the header just reads "Daily standup" for a
        # saved run (the run row already names the date). my_name drives the "My Update" row.
        data = {"report": report, "session_name": "", "my_name": report.my_name, "team_expanded": False, "message": ""}
        order = standup_card_order(data)
        actions = ["Export", "Share Online", "Delete", "Run again", "Back"]
        view = "overview"  # "overview" | a section key
        focus = "sections"  # overview focus zone: "sections" | "buttons"
        card_idx, scroll, sel = 0, 0, 0
        s_anim = time.monotonic()
        _last_panel = None  # most recently rendered snapshot panel, for click hit-testing
        logger.info("standup hub: opened run id=%s", run.run_id)

        def _render() -> None:
            nonlocal scroll, _last_panel
            w, h = console.size
            scroll_meta: dict = {}
            panel = _build_standup_screen(
                data,
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                width=w,
                height=max(10, h - 1),
                action_sel=sel,
                shimmer_tick=time.monotonic() - s_anim,
                view=view,
                selected_card=card_idx,
                actions=(actions if view == "overview" else ["← Overview"]),
            )
            scroll = max(0, min(scroll, scroll_meta.get("max_scroll", scroll)))
            _last_panel = panel
            live.update(_last_panel)

        _render()
        while True:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _last_panel is None:
                    continue
                # Hit-test against whichever button row is actually on screen this frame.
                _labels = actions if view == "overview" else ["← Overview"]
                _idx = button_click(console, _last_panel, *_clicked, _labels)
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                if view == "overview":
                    focus, sel = "buttons", _idx
                k = "enter"  # fall through to the existing Enter handling
            if view != "overview":
                # Drilled into a section: Up/Down scroll it; any exit key returns to overview.
                if k in SCROLL_KEYS:
                    scroll = max(0, scroll + (1 if k in ("down", "scroll_down", "pagedown") else -1))
                elif k in ("enter", " ", "esc", "q", "left", "right"):
                    view, scroll = "overview", 0
                _render()
                continue
            if k in ("up", "down") or k in SCROLL_KEYS:
                focus = "sections"
                card_idx = (card_idx + (1 if k in ("down", "scroll_down", "pagedown") else -1)) % len(order)
            elif k == "left":
                focus = "buttons"
                sel = max(0, sel - 1)
            elif k == "right":
                focus = "buttons"
                sel = min(len(actions) - 1, sel + 1)
            elif k in ("enter", " "):
                if focus == "buttons":
                    leave, m = run_action(actions[sel])
                    if leave:
                        return
                    if m is not None:
                        data["message"] = m
                elif order[card_idx] == "team":
                    # Team row expands inline into member sub-rows (live behaviour), not a detail view.
                    data["team_expanded"] = not data["team_expanded"]
                    order = standup_card_order(data)
                else:
                    view, scroll = order[card_idx], 0
            elif k in ("esc", "q"):
                return
            _render()

    def _history_for(report):
        # Trend for the report's own session, clipped to its date inside the exporter.
        with StandupStore(_ana_dbp) as store:
            return store.get_history(report.session_id, limit=30) if report.session_id else []

    def files_export(run):
        report = _report(run.run_id)
        if report is None:
            return "That run is no longer available."
        paths = export_standup(report, history=_history_for(report))
        return f"Exported to {paths['markdown'].parent}  (Markdown + HTML)"

    def get_document(run):
        report = _report(run.run_id)
        return (
            "That run is no longer available."
            if report is None
            else (f"Standup — {report.date}", build_standup_markdown(report))
        )

    def get_share_document(run):
        """A past run, shared read-only.

        Deliberately NOT correctable. This path already offers
        ``get_editable_session`` below, and a document cannot be both: the
        editable share re-renders from its own edit log, so a practice vote
        written straight to the run underneath it would be overwritten by the
        next edit. One writer per shared document.

        Since the live page's Share Online became editable too, no surface builds
        a practice-correctable document any more — signals are answered from the
        TUI's "Practices" action. The share path stays because carrying a verdict
        THROUGH the edit log (a third op beside OP_NOTE/OP_FIELD) is what would
        let both exist on one document; see YEA-80.
        """
        report = _report(run.run_id)
        if report is None:
            return None
        from yeaboi.sharing.documents import standup_document

        return standup_document(report, history=_history_for(report))

    def get_editable_session(run):
        """A correctable standup, when the run is still readable."""
        report = _report(run.run_id)
        if report is None:
            return None
        return _standup_editable_session(report, run.run_id, _history_for(report))

    def delete_run(run):
        with StandupStore(_ana_dbp) as store:
            store.delete_run(run.run_id)

    def _schedule_session_id() -> str:
        """The session the hub schedule targets.

        Prefer a session that already has an ENABLED schedule (most recently
        updated first) — otherwise a schedule installed for an older session
        would be invisible here and the wizard would create a duplicate for the
        latest one. Fall back to the latest session (the live page's targeting).
        """
        from yeaboi.sessions import SessionStore

        with StandupStore(_ana_dbp) as store:
            enabled = store.get_enabled_schedule_sessions()
        if enabled:
            return enabled[0]
        with SessionStore(_ana_dbp) as store:
            return store.get_latest_session_id() or ""

    def _schedule_label() -> str:
        """Text for the hub's fixed schedule card, recomputed on every reload."""
        from yeaboi.standup.scheduler import get_schedule_status, run_time_str, weekday_spec_label

        try:
            session_id = _schedule_session_id()
            if not session_id:
                return "⏰ Set up a schedule"
            with StandupStore(_ana_dbp) as store:
                cfg = store.load_config(session_id) or {}
            if not cfg.get("enabled"):
                return "⏰ Set up a schedule"
            installed = bool(get_schedule_status(session_id).get("installed"))
            state = "On" if installed else "Off (not installed)"
            fire = run_time_str(cfg.get("time", "10:00"), int(cfg.get("lead_minutes", 10)))
            days = weekday_spec_label(cfg.get("weekdays", "1-5"))
            channels = ", ".join(cfg.get("delivery_channels", ["terminal"]))
            label = f"⏰ Schedule · {state} · {fire} · {days} · {channels}"
            return label if len(label) <= 52 else label[:51] + "…"
        except Exception:
            logger.warning("standup hub: failed to build schedule label", exc_info=True)
            return "⏰ Set up a schedule"

    def _open_schedule() -> str:
        """Enter on the schedule card → run the wizard (never crash the hub)."""
        session_id = _schedule_session_id()
        if not session_id:
            return "No session yet — run a standup or create a plan first."
        try:
            return _run_standup_schedule_wizard(console, live, read_key, frame_time, supports_timeout, session_id)
        except Exception as e:
            logger.error("standup hub: schedule wizard failed", exc_info=True)
            return f"Schedule setup failed: {e}"

    _run_mode_hub(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        mode="standup",
        title_fn=standup_title,
        subtitle="Saved standups",
        empty_title="No standups yet",
        empty_subtitle="Press Enter to run your first standup",
        new_label="+ New standup",
        load_runs=load_runs,
        open_snapshot=open_standup_snapshot,
        files_export=files_export,
        get_document=get_document,
        get_share_document=get_share_document,
        get_editable_session=get_editable_session,
        share_theme=STANDUP_THEME,
        delete_run=delete_run,
        run_new=lambda: _run_standup_page(console, live, read_key, frame_time, supports_timeout),
        extra_label=_schedule_label,
        extra_action=_open_schedule,
    )


def _run_retro_hub(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Retro saved-runs hub → landing for the Retro card.

    Opening a saved retro renders the recorded board as a read-only text snapshot
    (from ``build_retro_markdown``) rather than resurrecting the live LAN board —
    "+ New retro" starts a fresh live board via the existing page.
    """
    from yeaboi.persistence import _relative_time
    from yeaboi.retro.export import _title, build_retro_markdown, export_retro
    from yeaboi.retro.store import RetroStore
    from yeaboi.ui.mode_select.screens._project_cards import RunSummary
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_retro_screen
    from yeaboi.ui.shared._components import RETRO_THEME, retro_title

    def _report(run_id: int):
        with RetroStore(_ana_dbp) as store:
            return store.get_run_by_id(run_id)

    def load_runs():
        with RetroStore(_ana_dbp) as store:
            rows = store.get_all_history(100)
        out = []
        for r in rows:
            date = r.get("retro_date") or ""
            proj = r.get("project_name") or ""
            n = r.get("card_count", 0)
            sub = " · ".join(p for p in (proj, f"{n} card{'s' if n != 1 else ''}") if p)
            out.append(
                RunSummary(
                    "retro",
                    r["id"],
                    f"Retro — {date or _relative_time(r['run_at'])}",
                    sub,
                    _relative_time(r["run_at"]),
                    session_id=r.get("session_id", ""),
                )
            )
        return out

    def make_detail(run):
        # Replay the saved board through the live Retro screen (structured grids +
        # card badges + carried-actions review), suppressing the live-only join block.
        report = _report(run.run_id)
        if report is None:
            return None
        grids = report.by_grid()
        carried = list(report.carried_action_items)
        session_name = report.project_name

        def render(*, scroll, action_sel, actions, scroll_meta, width, height, message, shimmer_tick):
            return _build_retro_screen(
                {
                    "grids": grids,
                    "carried": carried,
                    "session_name": session_name,
                    "snapshot": True,
                    "actions": actions,
                    "message": message,
                },
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                action_sel=action_sel,
                width=width,
                height=height,
                shimmer_tick=shimmer_tick,
            )

        return render

    def _history_for(report):
        # Trend chart data: this session's past retros (newest-first rows).
        if not report.session_id:
            return []
        with RetroStore(_ana_dbp) as store:
            return store.get_history(report.session_id, limit=30)

    def files_export(run):
        report = _report(run.run_id)
        if report is None:
            return "That run is no longer available."
        paths = export_retro(report, history=_history_for(report))
        return f"Exported to {paths['markdown'].parent}  (Markdown + HTML)"

    def get_document(run):
        report = _report(run.run_id)
        return "That run is no longer available." if report is None else (_title(report), build_retro_markdown(report))

    def get_share_document(run):
        report = _report(run.run_id)
        if report is None:
            return None
        from yeaboi.sharing.documents import retro_document

        return retro_document(report, history=_history_for(report))

    def get_editable_session(run):
        """A correctable retro, when the run is still readable."""
        report = _report(run.run_id)
        if report is None:
            return None
        from yeaboi.artifacts.session import EditableSession

        return EditableSession(
            report,
            kind="retro",
            db_path=_ana_dbp,
            run_id=run.run_id,
            history=tuple(_history_for(report)),
        )

    def delete_run(run):
        with RetroStore(_ana_dbp) as store:
            store.delete_run(run.run_id)

    _run_mode_hub(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        mode="retro",
        title_fn=retro_title,
        subtitle="Saved retros",
        empty_title="No retros yet",
        empty_subtitle="Press Enter to start your first retro board",
        new_label="+ New retro",
        load_runs=load_runs,
        make_detail=make_detail,
        files_export=files_export,
        get_document=get_document,
        get_share_document=get_share_document,
        get_editable_session=get_editable_session,
        share_theme=RETRO_THEME,
        delete_run=delete_run,
        run_new=lambda: _run_retro_page(console, live, read_key, frame_time, supports_timeout),
    )


def _run_reporting_hub(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Reporting saved-runs hub → landing for the Reporting card."""
    from yeaboi.persistence import _relative_time
    from yeaboi.reporting.export import _title, build_report_markdown, export_report
    from yeaboi.reporting.store import ReportingStore
    from yeaboi.ui.mode_select.screens._project_cards import RunSummary
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_reporting_screen
    from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

    def _report(run_id: int):
        with ReportingStore(_ana_dbp) as store:
            return store.get_run_by_id(run_id)

    def load_runs():
        with ReportingStore(_ana_dbp) as store:
            rows = store.get_all_history(100)
        out = []
        for r in rows:
            period = r.get("period") or ""
            proj = r.get("project_name") or ""
            n = r.get("item_count", 0)
            sub = " · ".join(p for p in (proj, f"{n} item{'s' if n != 1 else ''} delivered") if p)
            out.append(
                RunSummary(
                    "reporting",
                    r["id"],
                    f"Report — {period or _relative_time(r['run_at'])}",
                    sub,
                    _relative_time(r["run_at"]),
                    session_id=r.get("session_id", ""),
                )
            )
        return out

    def make_detail(run):
        # Render the saved report through the live Reporting detail screen (indigo
        # theme + the rich artifact renderer) instead of flat grey lines.
        report = _report(run.run_id)
        if report is None:
            return None
        detail_title = f"Delivery Report — {report.period_label}"

        def render(*, scroll, action_sel, actions, scroll_meta, width, height, message, shimmer_tick):
            return _build_reporting_screen(
                {
                    "view": "detail",
                    "report": report,
                    "detail_title": detail_title,
                    "actions": actions,
                    "message": message,
                },
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                action_sel=action_sel,
                width=width,
                height=height,
                shimmer_tick=shimmer_tick,
            )

        return render

    def _history_for(run):
        # Trend chart data: past reports for the run's session (newest-first rows).
        with ReportingStore(_ana_dbp) as store:
            return store.get_history(run.session_id, limit=30)

    def files_export(run):
        from yeaboi.reporting.style import load_deck_style

        report = _report(run.run_id)
        if report is None:
            return "That run is no longer available."
        # Saved deck-style prefs apply to every export, including hub re-exports.
        paths = export_report(report, history=_history_for(run), style=load_deck_style())
        kinds = "Markdown + HTML + slides" + (" + PowerPoint" if "pptx" in paths else "")
        return f"Exported to {paths['markdown'].parent}  ({kinds})"

    def get_document(run):
        report = _report(run.run_id)
        return "That run is no longer available." if report is None else (_title(report), build_report_markdown(report))

    def get_share_document(run):
        report = _report(run.run_id)
        if report is None:
            return None
        from yeaboi.sharing.documents import reporting_document

        return reporting_document(report, history=_history_for(run))

    def get_editable_session(run):
        """A correctable reporting, when the run is still readable."""
        report = _report(run.run_id)
        if report is None:
            return None
        from yeaboi.artifacts.session import EditableSession

        return EditableSession(
            report,
            kind="reporting",
            db_path=_ana_dbp,
            run_id=run.run_id,
            history=tuple(_history_for(run)),
        )

    def delete_run(run):
        with ReportingStore(_ana_dbp) as store:
            store.delete_run(run.run_id)

    _run_mode_hub(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        mode="reporting",
        title_fn=reporting_title,
        subtitle="Saved reports",
        empty_title="No reports yet",
        empty_subtitle="Press Enter to generate your first delivery report",
        new_label="+ New report",
        load_runs=load_runs,
        make_detail=make_detail,
        files_export=files_export,
        get_document=get_document,
        get_share_document=get_share_document,
        get_editable_session=get_editable_session,
        share_theme=REPORTING_THEME,
        delete_run=delete_run,
        run_new=lambda: _run_reporting_page(console, live, read_key, frame_time, supports_timeout),
    )


def _run_performance_hub(
    console: Console, live, read_key, frame_time: float, supports_timeout: bool, engineer: str
) -> None:
    """Per-engineer saved-artifacts hub (opened from the Performance roster's "History").

    Performance is keyed by engineer, not by a single run, so the hub lists every saved
    artifact for one engineer — 1:1 preps, completions, 6-month reviews, and notes — with
    the same Open / Delete / Export experience. "+ New artifact" hands control back to the
    roster, where the create actions (Prep / Complete / Review / Notes) live.
    """
    from yeaboi.performance.export import (
        build_completion_markdown,
        build_prep_markdown,
        build_review_markdown,
        export_artifact,
    )
    from yeaboi.performance.render import format_completion_lines, format_prep_lines, format_review_lines
    from yeaboi.performance.store import PerformanceStore
    from yeaboi.persistence import _relative_time
    from yeaboi.ui.mode_select.screens._project_cards import RunSummary
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_performance_screen
    from yeaboi.ui.shared._components import PERFORMANCE_THEME, performance_title

    def load_runs():
        with PerformanceStore(_ana_dbp) as store:
            rows = store.get_engineer_history(engineer, 100)
        return [
            RunSummary(
                "performance",
                r["id"],
                r["title"],
                r["kind"].capitalize(),
                _relative_time(r["created_at"]),
                kind=r["kind"],
            )
            for r in rows
        ]

    def _artifact(run):
        """Return (artifact_or_text, kind, lines) for a saved row, or None if gone."""
        with PerformanceStore(_ana_dbp) as store:
            if run.kind == "review":
                art = store.get_review_by_id(run.run_id)
                return None if art is None else (art, "review", format_review_lines(art))
            if run.kind == "note":
                for n in store.get_notes(engineer, 200):
                    if n["id"] == run.run_id:
                        return (n["note_text"], "note", (n["note_text"] or "").splitlines() or ["(empty note)"])
                return None
            pair = store.get_one_on_one_by_id(run.run_id)
        if pair is None:
            return None
        kind, art = pair
        lines = format_completion_lines(art) if kind == "completion" else format_prep_lines(art)
        return (art, kind, lines)

    def make_detail(run):
        # Render the saved artifact through the live Performance detail screen (coral
        # theme + semantic _styled colouring) instead of flat grey lines.
        got = _artifact(run)
        if got is None:
            return None
        _art, _kind, lines = got
        title = run.title

        def render(*, scroll, action_sel, actions, scroll_meta, width, height, message, shimmer_tick):
            return _build_performance_screen(
                {
                    "view": "detail",
                    "detail_lines": lines,
                    "detail_title": title,
                    "actions": actions,
                    "message": message,
                },
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                action_sel=action_sel,
                width=width,
                height=height,
                shimmer_tick=shimmer_tick,
            )

        return render

    def delete_run(run):
        with PerformanceStore(_ana_dbp) as store:
            if run.kind == "review":
                store.delete_review(run.run_id)
            elif run.kind == "note":
                store.delete_note(run.run_id)
            else:
                store.delete_one_on_one(run.run_id)

    def files_export(run):
        got = _artifact(run)
        if got is None:
            return "That artifact is no longer available."
        art, kind, _lines = got
        if kind == "note":
            return "Notes aren't exported to files individually — use Copy/Publish."
        paths = export_artifact(art, engineer=engineer, kind=kind)
        return f"Exported to {paths['markdown'].parent}  (Markdown + HTML)"

    def get_document(run):
        got = _artifact(run)
        if got is None:
            return "That artifact is no longer available."
        art, kind, _lines = got
        if kind == "note":
            return (f"Note — {engineer}", art if isinstance(art, str) else "")
        if kind == "completion":
            return (run.title, build_completion_markdown(art))
        if kind == "review":
            return (run.title, build_review_markdown(art))
        return (run.title, build_prep_markdown(art))

    def get_share_document(run):
        got = _artifact(run)
        if got is None:
            return None
        art, kind, _lines = got
        if kind == "note":
            return None
        from yeaboi.sharing.documents import performance_document

        return performance_document(art, kind=kind)

    _run_mode_hub(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        mode="performance",
        title_fn=performance_title,
        subtitle=f"Saved artifacts — {engineer}",
        empty_title=f"No saved artifacts for {engineer}",
        empty_subtitle="Press Enter to create one from the roster",
        new_label="+ New artifact",
        load_runs=load_runs,
        make_detail=make_detail,
        files_export=files_export,
        get_document=get_document,
        get_share_document=get_share_document,
        share_theme=PERFORMANCE_THEME,
        delete_run=delete_run,
        run_new=lambda: None,
        new_breaks_out=True,
    )


def _run_standup_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Event loop for the Daily Standup page (overview + expandable sections).

    Follows the team-analysis pattern: a pinned status strip carries the
    sprint/day/confidence meters, and the overview lists selectable section
    cards (Team Summary, My Update, Team, Activity,
    Schedule, Notices) with a two-zone focus model: Up/Down focuses the list
    and moves the selection, Enter opens the selected section directly —
    except the Team row, where Enter toggles the inline member sub-rows;
    Left/Right moves focus to the button row (Generate / Team / Configure / Back),
    where Enter presses the highlighted button. A detail view free-scrolls
    with Up/Down; Back/Esc returns to the overview. Generate/Configure open
    themed in-TUI input screens (driven by read_key, so no raw prompt and no
    mouse-escape leakage), then refresh. Generate collects the user's own
    update first, so there is no separate My Update button.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_screen
    from yeaboi.ui.mode_select.screens._standup_sections import standup_card_order

    team_expanded = False  # inline Team-row expansion; survives data refreshes
    data = _collect_standup_data()
    data["team_expanded"] = team_expanded
    view = "overview"
    # Generate is the primary landing action; Up/Down still moves into sections.
    focus = "buttons"  # overview focus zone: "sections" | "buttons"
    card_idx, scroll, sel = 0, 0, 0
    _scroll_meta: dict = {}
    anim_start = time.monotonic()  # shimmer title + typewriter subtitle clock
    # Anonymize state: None = real data; an AnonymizedOutput = mask the report in place.
    anon = None
    anon_instruction = ""

    def _detail_member_update():
        """The MemberUpdate this detail view is showing, or None elsewhere."""
        report = data.get("report")
        if report is None:
            return None
        if view == "my_update":
            name = data.get("my_name", "")
        elif view.startswith("member:"):
            name = view.split(":", 1)[1]
        else:
            return None
        return next((m for m in report.member_updates if m.name == name), None)

    def _votable_practices() -> list:
        """This member's practice signals a verdict can be recorded against.

        Empty while anonymized: the names on screen are masked, so a verdict
        cast here would be about a person the store cannot match.
        """
        from yeaboi.standup import practice_feedback

        if anon is not None:
            return []
        member = _detail_member_update()
        return list(practice_feedback.votable(getattr(member, "practices", ()) or ()))

    def _actions() -> list[str]:
        if view == "overview":
            base = ["Generate", "Review", "Team", "Sources", "Anonymize", "Identity", "Back"]
        else:
            base = ["Back", "Export", "Anonymize"]
            if _votable_practices():
                base.insert(1, "Practices")
        if data.get("report") is not None:
            base.insert(-1, "Share Online")
        if anon is not None:  # swap Anonymize → Adjust + Revert while masked
            i = base.index("Anonymize")
            base[i : i + 1] = ["Adjust", "Revert"]
        return base

    def _open_section() -> None:
        nonlocal view, scroll, sel, team_expanded
        order = standup_card_order(data)
        if not order:
            return
        key = order[card_idx % len(order)]
        if key == "team":  # Team row toggles its inline sub-rows, no detail view
            team_expanded = not team_expanded
            data["team_expanded"] = team_expanded
            logger.info("standup: team row %s", "expanded" if team_expanded else "collapsed")
            return
        view = key
        scroll = 0
        sel = 0
        logger.info("standup: opened section %s", view)

    def _reset_to_overview() -> None:
        nonlocal view, scroll, sel, card_idx, focus
        view = "overview"
        focus = "sections"
        scroll = 0
        sel = 0
        # Refreshes rebuild the data dict, so re-apply the expansion flag here
        # (every refresh is followed by this reset).
        data["team_expanded"] = team_expanded
        card_idx = min(card_idx, max(0, len(standup_card_order(data)) - 1))

    _last_panel = None  # most recently rendered standup panel, for click hit-testing

    def _render() -> None:
        nonlocal _last_panel
        w, h = console.size
        elapsed = time.monotonic() - anim_start
        # When anonymized, mask the report in place so the SAME cards re-render with
        # only the sensitive words swapped (never a separate raw-text view).
        render_data = data
        if anon is not None and data.get("report") is not None:
            from yeaboi.anonymize.apply import mask_artifact

            render_data = {**data, "report": mask_artifact(data["report"], anon.replacements)}
        # Leave a one-row safety margin: a Live renderable exactly equal to the
        # terminal height loses its last row (the action buttons) to the cursor.
        _last_panel = _build_standup_screen(
            render_data,
            scroll_offset=scroll,
            scroll_meta=_scroll_meta,
            width=w,
            height=max(10, h - 1),
            # No button is highlighted while the section list has focus.
            action_sel=-1 if (view == "overview" and focus == "sections") else sel,
            shimmer_tick=elapsed,
            sub_reveal=elapsed * _HEADER_SUB_SPEED,
            view=view,
            selected_card=card_idx,
            actions=_actions(),
            anon_note=_anon_note(anon),
        )
        live.update(_last_panel)

    _render()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(k)
        if _clicked is not None:
            if _last_panel is None:
                continue
            _idx = button_click(console, _last_panel, *_clicked, _actions())
            if _idx is None:
                continue  # click missed the buttons — ignore it
            sel = _idx
            focus = "buttons"  # route the synthesized Enter to the button action
            k = "enter"  # fall through to the existing Enter handling
        if view == "overview" and k in SCROLL_KEYS:
            # On the overview, Up/Down focuses the section list and moves the
            # selection (the screen auto-scrolls the selected row into view).
            focus = "sections"
            order = standup_card_order(data)
            if order:
                card_idx += 1 if k in ("down", "scroll_down", "pagedown") else -1
                card_idx %= len(order)
        elif k in SCROLL_KEYS:
            _ns = coalesce_scroll(scroll, k, _scroll_meta, read_key)
            if _ns == scroll:
                continue
            scroll = _ns
        elif k == "left":
            if view == "overview" and focus != "buttons":
                focus = "buttons"  # first Left/Right only moves focus to the row
            else:
                sel = max(0, sel - 1)
        elif k == "right":
            if view == "overview" and focus != "buttons":
                focus = "buttons"
            else:
                sel = min(len(_actions()) - 1, sel + 1)
        elif k in ("enter", " "):
            session_id = data.get("session_id", "")
            if not session_id:
                logger.info("standup: no session available — returning to mode select")
                break
            if view == "overview" and focus == "sections":
                _open_section()
                _render()
                continue
            act = _actions()[sel]
            if act == "Back":
                if view == "overview":
                    break
                _reset_to_overview()
            elif act == "Generate":  # ask for the user's own update first, then run
                logger.info("standup: Generate pressed (session=%s)", session_id)
                try:
                    proceed = _standup_generate_flow(console, live, read_key, frame_time, supports_timeout, session_id)
                except Exception as e:  # never let a prompt crash the TUI
                    logger.error("standup generate failed: %s", e, exc_info=True)
                    proceed = f"Generate failed: {e}"
                data = _collect_standup_data(message=proceed if proceed is not None else "")
                anon, anon_instruction = None, ""  # new report → drop any stale mask
                _reset_to_overview()
            elif act == "Review":  # audit past standups against their meeting transcripts
                logger.info("standup: Review pressed (session=%s)", session_id)
                try:
                    msg = _standup_review_flow(console, live, read_key, frame_time, supports_timeout, session_id)
                except Exception as e:  # never let a review crash the TUI
                    logger.error("standup review flow failed: %s", e, exc_info=True)
                    msg = f"Transcript review failed: {e}"
                data = _collect_standup_data(message=msg or "")
                _reset_to_overview()
            elif act == "Practices":  # was the coaching note right about this person?
                logger.info("standup: Practices pressed (session=%s, view=%s)", session_id, view)
                member = _detail_member_update()
                msg = _run_standup_practice_review(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    session_id,
                    getattr(member, "name", ""),
                    _votable_practices(),
                )
                # A thumbs-down rewrote the stored report, so re-read it rather
                # than filtering the copy on screen — the two must not disagree.
                # Unlike every other refresh here we stay in the detail view, so
                # the expanded-team flag has to be carried over by hand or the
                # member rows this view was opened from vanish underneath it.
                data = _collect_standup_data(message=msg)
                data["team_expanded"] = team_expanded
                # "Practices" disappears once the last signal is answered, which
                # would otherwise leave the cursor past the end of the row.
                sel = min(sel, len(_actions()) - 1)
            elif act == "Export":  # pick a destination (files / Notion / Confluence)
                logger.info("standup: Export pressed (session=%s)", session_id)
                if anon is not None:  # export the masked copy, matching the screen
                    doc = _standup_document(session_id, data)
                    msg = (
                        doc
                        if isinstance(doc, str)
                        else _anon_export(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            anon=anon,
                            doc_title=doc[0],
                            markdown=doc[1],
                            project_name=data.get("session_name", "") or session_id,
                            source_mode="standup",
                        )
                    )
                else:
                    msg = _export_via_picker(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        mode="standup",
                        files_export=lambda: _standup_export(session_id, data),
                        get_document=lambda: _standup_document(session_id, data),
                    )
                if msg is not None:  # None = user backed out of the picker
                    data = _collect_standup_data(message=msg)
                _reset_to_overview()
            elif act == "Share Online":
                report = data.get("report")
                if report is not None:
                    from yeaboi.sharing.documents import standup_document
                    from yeaboi.standup.store import StandupStore as _SStore
                    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

                    share_history = []
                    share_run_id = None
                    if anon is None:  # masked shares carry no charts — skip the query
                        with _SStore(_ana_dbp) as _sstore:
                            share_history = _sstore.get_history(session_id, limit=30)
                            share_run_id = _sstore.get_latest_run_id(session_id)
                    # The same correctable share the saved-runs hub offers: a
                    # reader can fix a wrong line and add a note, attributed and
                    # versioned. Never while anonymized — an edit made against a
                    # mask cannot be matched back to a member.
                    #
                    # This share is deliberately NOT practice-votable. One writer
                    # per document: the editable share re-renders from its own
                    # edit log, so a verdict written straight to the run beneath
                    # it would be overwritten by the next edit. Practice signals
                    # are answered from the "Practices" action instead.
                    editing = (
                        _standup_editable_session(report, share_run_id or 0, share_history) if anon is None else None
                    )
                    # The lease this session holds is what extends the "one
                    # writer" rule above to a writer this screen cannot see: a
                    # verdict arriving from Slack while the share is open
                    # defers its rewrite rather than losing it to the commit.
                    try:
                        recorded = _run_output_share_flow(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            document=standup_document(report, anon=anon, history=share_history),
                            theme=STANDUP_THEME,
                            title_fn=standup_title,
                            editable=editing.share if editing is not None else None,
                            on_edit=editing.persist if editing is not None else None,
                        )
                        if recorded and editing is not None:
                            # Appended, so the generated original is still there and
                            # every trend chart picks the corrected row up on its own.
                            editing.commit()
                            noun = "correction" if recorded == 1 else "corrections"
                            data = _collect_standup_data(message=f"Saved {recorded} {noun}.")
                    finally:
                        if editing is not None:
                            editing.close()
                _reset_to_overview()
            elif act == "Anonymize":  # mask the report in place for public sharing
                logger.info("standup: Anonymize pressed (session=%s)", session_id)
                from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

                doc = _standup_document(session_id, data)
                if isinstance(doc, str):
                    data = _collect_standup_data(message=doc)
                else:
                    res = _run_anonymize_pass(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        markdown=doc[1],
                        instruction="",
                        project_name=data.get("session_name", "") or session_id,
                        source_mode="standup",
                        theme=STANDUP_THEME,
                        title=standup_title(),
                    )
                    if res is not None:
                        anon, anon_instruction = res, ""
                    else:
                        data = _collect_standup_data(message="Anonymize failed (see logs).")
                _reset_to_overview()
            elif act == "Adjust":  # refine the mask with a free-text instruction
                from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

                adj = _standup_read_line(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    prompt="Also mask …  ·  don't mask … (it's public/safe)",
                    step="Anonymize — adjust what's masked",
                    default="",
                    theme=STANDUP_THEME,
                    title=standup_title(),
                    box_rows=6,
                )
                if adj is not None and adj.strip():
                    anon_instruction = f"{anon_instruction}\n{adj.strip()}".strip()
                    doc = _standup_document(session_id, data)
                    if not isinstance(doc, str):
                        res = _run_anonymize_pass(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            markdown=doc[1],
                            instruction=anon_instruction,
                            project_name=data.get("session_name", "") or session_id,
                            source_mode="standup",
                            theme=STANDUP_THEME,
                            title=standup_title(),
                        )
                        if res is not None:
                            anon = res
            elif act == "Revert":  # restore the real names (no LLM call)
                anon, anon_instruction = None, ""
            elif act == "Team":
                try:
                    logger.info("standup: Team pressed (session=%s)", session_id)
                    _saved, msg = _standup_team_configure(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        session_id,
                    )
                except Exception as e:
                    logger.error("standup team selection failed: %s", e, exc_info=True)
                    msg = f"Team selection failed: {e}"
                data = _collect_standup_data(message=msg)
                _reset_to_overview()
            elif act == "Sources":
                # The only way back into the code-source picker after first setup.
                # Without it, a standup that has never been told about GitHub can
                # only be corrected by declining the saved setup mid-Generate.
                try:
                    logger.info("standup: Sources pressed (session=%s)", session_id)
                    _saved, msg = _standup_code_configure(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        session_id,
                    )
                except Exception as e:
                    logger.error("standup code-source selection failed: %s", e, exc_info=True)
                    msg = f"Source selection failed: {e}"
                data = _collect_standup_data(message=msg)
                _reset_to_overview()
            elif act == "Identity":  # in-TUI themed input (stays inside Live)
                try:
                    logger.info("standup: Identity pressed (session=%s)", session_id)
                    msg = _standup_identity_configure(console, live, read_key, frame_time, supports_timeout, session_id)
                except Exception as e:  # never let a prompt crash the TUI
                    logger.error("standup action failed: %s", e, exc_info=True)
                    msg = f"Action failed: {e}"
                data = _collect_standup_data(message=msg)
                _reset_to_overview()
        elif k in ("esc", "q"):
            if view == "overview":
                break
            _reset_to_overview()
        _render()
    logger.info("standup: page closed (session=%s)", data.get("session_id", ""))


# ---------------------------------------------------------------------------
# Performance mode page
# ---------------------------------------------------------------------------


def _collect_performance_data(message: str = "") -> dict:
    """Gather Performance page data: latest session + the Jira/AzDO engineer roster.

    The roster is the real people who did work on the board (assignees) — see
    performance/roster.py. Session context (sprint length/project) is best-effort;
    the page still works with no session.
    """
    data: dict = {"message": message, "session_id": "", "session_name": "", "roster": [], "roster_hints": []}
    try:
        from yeaboi.sessions import SessionStore, make_display_name

        with SessionStore(_ana_dbp) as store:
            session_id = store.get_latest_session_id() or ""
            data["session_id"] = session_id
            if session_id:
                meta = store.get_session(session_id) or {}
                data["session_name"] = make_display_name(meta) if meta else session_id
    except Exception:
        logger.warning("performance: failed to resolve latest session", exc_info=True)
    try:
        from yeaboi.performance.roster import fetch_roster

        data["roster"] = [r.name for r in fetch_roster()]
    except Exception:
        logger.warning("performance: failed to fetch roster", exc_info=True)
    # Fallback: no live Jira/AzDO roster → use the planning session's own team
    # members (also board-derived) so Performance is usable without a live tracker.
    if not data["roster"] and data["session_id"]:
        data["roster"] = _performance_session_team(data["session_id"])
        if data["roster"]:
            logger.info("performance: roster fell back to session team members")
    data["roster_hints"] = _performance_roster_hints(data["roster"])
    logger.info("performance: %d engineer(s) in roster", len(data["roster"]))
    return data


def _performance_session_team(session_id: str) -> list[str]:
    """Return the session's team-member names (fallback roster when no tracker).

    Reads ``selected_team_members`` from the saved plan state — the same
    board-derived roster the standup uses. Best-effort: any error → []. Names are
    de-duplicated preserving order and sorted for a stable page.
    """
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(_ana_dbp) as store:
            state = store.load_state(session_id) or {}
    except Exception:
        logger.warning("performance: failed to load session team members", exc_info=True)
        return []
    names = [str(n).strip() for n in (state.get("selected_team_members") or ()) if str(n).strip()]
    return sorted(dict.fromkeys(names), key=str.lower)


def _performance_roster_hints(roster: list[str]) -> list[str]:
    """Build a one-line status hint per engineer (open 1:1 actions + review on file).

    Shown as the description under the selected engineer's big ASCII name. Best-effort
    — a store error just yields the generic hint so the page always renders.
    """
    generic = "1:1 prep · completion · 6-month review"
    if not roster:
        return []
    try:
        from yeaboi.performance.store import PerformanceStore

        with PerformanceStore(_ana_dbp) as store:
            open_actions = store.get_all_open_action_items()
            hints: list[str] = []
            for name in roster:
                n = len(open_actions.get(name, ()))
                has_review = store.get_latest_review(name) is not None
                if n:
                    hint = f"{n} open 1:1 action{'s' if n != 1 else ''}"
                else:
                    hint = "no open 1:1 actions"
                if has_review:
                    hint += " · review on file"
                hints.append(hint)
            return hints
    except Exception:
        logger.warning("performance: failed to build roster hints", exc_info=True)
        return [generic for _ in roster]


def _performance_get_transcript(console, live, read_key, frame_time, supports_timeout) -> tuple[str, list[str]] | None:
    """Collect a 1:1 transcript — via a file path, or pasted/typed inline.

    Returns (transcript_text, image_paths), or None if the user cancelled (Esc).
    Supports both input methods per the design: a file path is read from disk; an
    empty path drops to an inline paste field. In the inline field, Ctrl+V attaches
    screenshots (e.g. a photo of whiteboard notes) that the summarising LLM call
    receives as multimodal image blocks.
    """
    path = _standup_read_line(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        prompt="Transcript file path (Enter to paste instead)",
        step="1:1 Complete  —  transcript source",
        default="",
    )
    if path is None:
        return None
    path = path.strip()
    if path:
        # Sandbox pre-flight (main thread): consent BEFORE the read so the
        # user gets the Allow/Deny popup instead of a SandboxViolationError.
        from yeaboi.ui.shared._consent import _preflight_path_consent

        if not _preflight_path_consent(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            path,
            mode="read",
            context="Performance — 1:1 transcript import",
        ):
            logger.info("performance: transcript path denied by sandbox consent: %s", path)
            return None
        try:
            from pathlib import Path

            text = Path(path).expanduser().read_text(encoding="utf-8")
            logger.info("performance: read transcript from %s (%d chars)", path, len(text))
            return text, []
        except Exception as e:  # noqa: BLE001 — fall through to paste on a bad path
            logger.warning("performance: could not read transcript file %s: %s", path, e)

    from yeaboi.ui.shared._attachments import referenced_images

    attachments: list[str] = []
    text = _standup_read_line(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        prompt="Paste the meeting notes / transcript",
        step="1:1 Complete  —  paste transcript",
        default="",
        attachments=attachments,
        scope_id="performance",
    )
    if text is None:
        return None
    return text, referenced_images(text, attachments)


def _performance_latest_artifact(engineer: str) -> tuple[object, str] | None:
    """Return the engineer's most recent artifact as (artifact, kind), or None.

    Priority mirrors usefulness: review > completion > prep.
    """
    from yeaboi.performance.store import PerformanceStore

    with PerformanceStore(_ana_dbp) as store:
        review = store.get_latest_review(engineer)
        completions = store.get_recent_completions(engineer, limit=1)
        prep = store.get_latest_prep(engineer)
    if review is not None:
        return review, "review"
    if completions:
        return completions[0], "completion"
    if prep is not None:
        return prep, "prep"
    return None


def _performance_export(engineer: str) -> str:
    """Re-export the engineer's most recent artifact (review > completion > prep)."""
    from yeaboi.performance import export

    found = _performance_latest_artifact(engineer)
    if found is None:
        logger.info("performance export: nothing to export yet for engineer=%s", engineer)
        return "Nothing to export yet — generate a 1:1 prep or review first."
    artifact, kind = found
    try:
        paths = export.export_artifact(artifact, engineer=engineer, kind=kind)
        logger.info("performance export: wrote %s for engineer=%s to %s", kind, engineer, paths["markdown"].parent)
        return f"Exported {kind} to {paths['markdown'].parent}  (Markdown + HTML)"
    except Exception as e:  # noqa: BLE001
        logger.error("performance export failed: %s", e, exc_info=True)
        return f"Export failed: {e}"


def _move_analysis_list_cursor(cursor: int, key: str, count: int) -> int:
    """Single-column list navigation for every Analysis setup picker.

    The setup screens always render one toggle row per line (there is no wide
    two-column layout), so movement is a plain ±1 with wraparound — the same
    convention as the standup and reporting pickers."""
    if count <= 1:
        return 0
    if key in ("up", "left", "scroll_up"):
        return (cursor - 1) % count
    if key in ("down", "right", "scroll_down"):
        return (cursor + 1) % count
    return cursor


def _run_analysis_feature_select(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    available: dict[str, bool],
    initial_features: list[str] | None = None,
):
    """Choose analysis result areas; every runnable area starts selected.

    ``initial_features`` restores a previous selection when the setup wizard
    re-enters this step (Esc back-navigation)."""
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _ANALYSIS_FEATURE_KEYS,
        _build_analysis_feature_screen,
    )

    runnable = {feature for feature in _ANALYSIS_FEATURE_KEYS if available.get(feature)}
    if not runnable:
        return "cancel"
    checked = set(initial_features) & runnable if initial_features is not None else set(runnable)
    cursor = 0
    rows = ("all",) + _ANALYSIS_FEATURE_KEYS
    message = ""
    while True:
        w, h = console.size
        live.update(
            _build_analysis_feature_screen(
                available,
                checked,
                cursor,
                width=w,
                height=h,
                message=message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "down", "left", "right", "scroll_up", "scroll_down"):
            cursor = _move_analysis_list_cursor(cursor, key, len(rows))
        elif key in (" ", "a", "A"):
            feature = rows[cursor]
            if feature == "all" or key in ("a", "A"):
                checked = set() if runnable <= checked else set(runnable)
            elif feature in runnable:
                checked.symmetric_difference_update({feature})
            message = ""
        elif key == "enter":
            if not checked:
                message = "Select at least one analysis area."
                continue
            return [feature for feature in _ANALYSIS_FEATURE_KEYS if feature in checked]
        elif key in ("esc", "q"):
            return "cancel"


def _run_component_select(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    grid: dict,
    descriptions: dict[str, str] | None = None,
    initial: dict[str, list[str]] | None = None,
    *,
    theme=None,
    brand: str = "ANALYSIS SETUP",
    title_builder=None,
    footer_verb: str = "analyse",
    required: dict[str, str] | None = None,
):
    """Blocking ragged component × sub-source picker.

    ``grid`` maps each component ('delivery'/'code'/'docs') to its CONFIGURED
    sub-sources. Returns a ``{component: [selected sub-sources]}`` dict (only
    components with a selection; ready to pass straight to ``run_team_analysis`` as
    ``components=``) or the string ``"cancel"`` on Esc. Everything is checked by
    default; at least one source overall must stay selected. ``initial`` restores a
    previous selection on wizard re-entry; a component absent from it (newly enabled
    by a feature change) defaults to all-checked, matching the first visit.

    ``theme``/``brand``/``title_builder``/``footer_verb`` re-brand the screen for
    other modes (Reporting). ``required`` maps a component to the message shown
    when Enter is pressed with that component empty (only enforced when the grid
    actually offers the component)."""
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _COMPONENT_KEYS,
        _build_component_select_screen,
    )

    rows = [c for c in _COMPONENT_KEYS if grid.get(c)]
    if not rows:  # nothing configured at all
        return "cancel"
    if initial is None:
        checked: dict[str, set[int]] = {c: set(range(len(grid[c]))) for c in rows}
    else:
        checked = {c: {i for i, s in enumerate(grid[c]) if s in set(initial.get(c, grid[c]))} for c in rows}
    row_idx = 0
    col_idx = 0
    message = ""

    def _ncols(r: int) -> int:
        return len(grid[rows[r]])

    while True:
        col_idx = min(col_idx, _ncols(row_idx) - 1)
        w, h = console.size
        live.update(
            _build_component_select_screen(
                grid,
                rows,
                checked,
                row_idx,
                col_idx,
                width=w,
                height=h,
                message=message,
                descriptions=descriptions,
                theme=theme,
                brand=brand,
                title_builder=title_builder,
                footer_verb=footer_verb,
            )
        )
        kk = read_key(timeout=frame_time) if supports_timeout else read_key()
        if kk in ("up", "scroll_up"):
            row_idx = (row_idx - 1) % len(rows)
        elif kk in ("down", "scroll_down"):
            row_idx = (row_idx + 1) % len(rows)
        elif kk == "left":
            col_idx = (col_idx - 1) % _ncols(row_idx)
        elif kk == "right":
            col_idx = (col_idx + 1) % _ncols(row_idx)
        elif kk == " ":
            checked[rows[row_idx]].symmetric_difference_update({col_idx})
            message = ""
        elif kk == "enter":
            result = {c: [grid[c][i] for i in sorted(checked[c])] for c in rows if checked[c]}
            if not result:
                message = f"Select at least one source to {footer_verb}."
                continue
            missing = next((m for c, m in (required or {}).items() if c in rows and c not in result), None)
            if missing:
                message = missing
                continue
            return result
        elif kk in ("esc", "q"):
            return "cancel"


def _run_analysis_depth_select(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    initial_depth: str = "deep",
):
    """Choose Quick/Deep analysis. Deep is preselected; Esc returns ``cancel``."""
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_analysis_depth_screen

    selected = 0 if initial_depth == "quick" else 1
    while True:
        w, h = console.size
        live.update(_build_analysis_depth_screen(selected, width=w, height=h))
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "left", "scroll_up", "down", "right", "scroll_down"):
            selected = _move_analysis_list_cursor(selected, key, 2)
        elif key == "enter":
            return ("quick", "deep")[selected]
        elif key in ("esc", "q"):
            return "cancel"


def _run_analysis_model_offer(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    preflight,
    initial_model: str | None = None,
):
    """Let an Ollama user explicitly accept a faster installed Analysis model."""
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_analysis_model_offer_screen

    if not preflight.get("offer"):
        return None
    selected = 1 if initial_model and initial_model == preflight.get("model") else 0
    options = (preflight["recommended_model"], preflight["model"])
    while True:
        w, h = console.size
        live.update(
            _build_analysis_model_offer_screen(
                preflight["model"],
                preflight["recommended_model"],
                int(preflight["predicted_seconds"]),
                selected,
                target_seconds=int(preflight.get("target_seconds", 600)),
                width=w,
                height=h,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "left", "scroll_up", "down", "right", "scroll_down"):
            selected = _move_analysis_list_cursor(selected, key, 2)
        elif key == "enter":
            return options[selected]
        elif key in ("esc", "q"):
            return "cancel"


def _run_analysis_window_select(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    initial_days: int = 120,
):
    """Choose the changed-content window. 120 days is preselected."""
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_analysis_window_screen

    options = (30, 90, 120, 365)
    selected = options.index(initial_days) if initial_days in options else 2
    while True:
        w, h = console.size
        live.update(_build_analysis_window_screen(selected, width=w, height=h))
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "left", "scroll_up", "down", "right", "scroll_down"):
            selected = _move_analysis_list_cursor(selected, key, len(options))
        elif key == "enter":
            return options[selected]
        elif key in ("esc", "q"):
            return "cancel"


def _run_member_select(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    roster: list,
    initial_members: list[str] | None = None,
):
    """Blocking roster picker.

    Every member starts selected. Returns at least one selected name, ``None`` only
    when the roster itself is empty, or the string ``"cancel"`` on Esc.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_member_select_screen

    initial = set(initial_members) if initial_members is not None else set(roster)
    checked: set[int] = {index for index, name in enumerate(roster) if name in initial}
    cursor = 0
    message = ""
    while True:
        w, h = console.size
        live.update(_build_member_select_screen(roster, checked, cursor, width=w, height=h, message=message))
        kk = read_key(timeout=frame_time) if supports_timeout else read_key()
        if kk in ("up", "down", "left", "right", "scroll_up", "scroll_down"):
            cursor = _move_analysis_list_cursor(cursor, kk, len(roster)) if roster else 0
        elif kk == " " and roster:
            checked.symmetric_difference_update({cursor})
            message = ""
        elif kk in ("a", "A") and roster:
            checked = set() if len(checked) == len(roster) else set(range(len(roster)))
            message = ""
        elif kk == "enter":
            if roster and not checked:
                message = "Select at least one member to run the analysis."
                continue
            return sorted(roster[i] for i in checked) or None
        elif kk in ("esc", "q"):
            return "cancel"


# Everything that differs between the two code hosts' scope pickers. The screen,
# the discovery thread and the key loop are identical, so they live once in
# _run_code_scope_select and read their wording from here; a second copy of that
# 70-line body would drift the moment either host gains a state.
_CODE_SCOPE_PROVIDERS: dict[str, dict] = {
    "github": {
        "heading": "GitHub owners",
        "unit": "owners",
        "spinner": "Discovering GitHub owners and organisations…",
        "thread": "analysis-github-owners",
        # Owner granularity is what the engine takes (github_analysis_inventory
        # walks an owner's repos), so the cost of one checkbox needs stating.
        "hint": "Every non-archived repo with activity in the window is scanned.",
        "empty": "No GitHub owners were visible to the configured token",
        "require": "Select at least one GitHub owner.",
        # Nothing pre-checked when config names no owners: discovery here is
        # UNBOUNDED (personal login + every org, each fanning out to a whole repo
        # estate), so an all-checked default would scan everything visible after
        # three Enters. The user says which owners are theirs.
        "select_all_default": False,
    },
    "azdo": {
        "heading": "Azure projects",
        "unit": "projects",
        "spinner": "Discovering accessible Azure projects…",
        "thread": "analysis-azdo-projects",
        "hint": "",
        "empty": "No Azure projects were accessible with the configured PAT",
        "require": "Select at least one Azure project.",
        # Azure is only ever offered when a project is already configured, so the
        # config default always matches; all-checked is its pre-existing fallback.
        "select_all_default": True,
    },
}


def _code_scope_discovery(provider: str):
    """(discover, configured_default) callables for one code host.

    Imported lazily and separately from the driver so tests can monkeypatch the
    underlying tool functions, and so a missing provider SDK never costs the
    other host its picker."""
    if provider == "github":
        from yeaboi.config import get_team_analysis_github_owners
        from yeaboi.tools.github import github_list_owners

        return github_list_owners, get_team_analysis_github_owners
    if provider == "azdo":
        from yeaboi.config import get_team_analysis_azdo_projects
        from yeaboi.tools.azure_devops import azdevops_list_projects

        return azdevops_list_projects, get_team_analysis_azdo_projects
    # Fail loudly: silently falling through to Azure would discover the wrong
    # host's scope under a third host's heading.
    raise ValueError(f"unknown code-scope provider: {provider}")


def _run_code_scope_select(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    provider: str,
    initial: list[str] | None = None,
) -> list[str] | str:
    """Discover a code host's scope (GitHub owners, Azure projects) and pick it.

    ``initial`` restores a previous selection on wizard re-entry (discovery itself
    re-runs; only the checked state carries over). Discovery failure is a warning
    on the screen, not an exit — the configured default still lets the run go
    ahead."""
    import threading

    cfg = _CODE_SCOPE_PROVIDERS[provider]
    discover, configured_default = _code_scope_discovery(provider)
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_analysis_progress_screen,
        _build_code_scope_select_screen,
    )

    result: list = [None]
    error: list[str] = [""]
    done = threading.Event()

    def _discover() -> None:
        try:
            result[0] = discover()
        except Exception as exc:
            # The screen shows this, but a run that quietly fell back to config
            # would otherwise leave no trace of WHY its scope was narrow.
            logger.warning("Analysis %s scope discovery failed: %s", provider, exc)
            error[0] = str(exc)
            result[0] = list(configured_default())
        finally:
            done.set()

    logger.info("Analysis %s scope: discovering", provider)
    started = time.monotonic()
    duck_working_thread(_discover, name=cfg["thread"]).start()
    tick = 0.0
    while not done.is_set():
        tick += frame_time
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                [cfg["spinner"]],
                width=w,
                height=h,
                elapsed=time.monotonic() - started,
                anim_tick=tick,
                source=provider,
                mode="analysis",
            )
        )
        time.sleep(frame_time)

    items = sorted(dict.fromkeys(result[0] or ()), key=str.lower)
    if initial is not None:
        wanted = {name.lower() for name in initial}
        checked = {idx for idx, name in enumerate(items) if name.lower() in wanted}
    else:
        checked = set()
    if not checked:
        defaults = {name.lower() for name in configured_default()}
        checked = {idx for idx, name in enumerate(items) if name.lower() in defaults}
    if not checked and cfg["select_all_default"]:
        checked = set(range(len(items)))
    cursor = 0
    # An empty estate is a real outcome, not a crash: stay on the screen and say
    # why, so Esc back to the sources step is an informed choice.
    if error[0]:
        message = f"Discovery warning: {error[0]}"
    elif not items:
        message = cfg["empty"] + " — press Esc to go back."
    else:
        message = ""
    while True:
        w, h = console.size
        live.update(
            _build_code_scope_select_screen(
                items,
                checked,
                cursor,
                heading=cfg["heading"],
                unit=cfg["unit"],
                empty_label=cfg["empty"],
                hint=cfg["hint"],
                width=w,
                height=h,
                message=message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("up", "down", "left", "right", "scroll_up", "scroll_down"):
            cursor = _move_analysis_list_cursor(cursor, key, len(items))
        elif key == " " and items:
            checked.symmetric_difference_update({cursor})
            message = ""
        elif key in ("a", "A") and items:
            checked = set() if len(checked) == len(items) else set(range(len(items)))
            message = ""
        elif key == "enter":
            if not checked:
                # "Select at least one…" is impossible advice with nothing to
                # select, so an empty estate names the way out instead.
                message = cfg["require"] if items else cfg["empty"] + " — press Esc to go back."
                continue
            selected = [items[idx] for idx in sorted(checked)]
            logger.info(
                "Analysis %s scope: %d discovered, %d selected",
                provider,
                len(items),
                len(selected),
            )
            return selected
        elif key in ("esc", "q"):
            return "cancel"


def _run_analysis_setup_review(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    features: list[str],
    components: dict[str, list[str]],
    members: list[str] | None,
    analysis_scope: dict[str, list[str]],
    depth: str,
    window_days: int,
    model: str | None,
) -> str:
    """Review the exact setup payload before starting any analysis work."""
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_analysis_setup_review_screen,
    )

    selected = 0
    _labels = ["Run Analysis", "Back"]
    while True:
        w, h = console.size
        _panel = _build_analysis_setup_review_screen(
            features=features,
            components=components,
            members=members,
            analysis_scope=analysis_scope,
            depth=depth,
            window_days=window_days,
            model=model,
            action_sel=selected,
            width=w,
            height=h,
        )
        live.update(_panel)
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(key)
        if _clicked is not None:
            _idx = button_click(console, _panel, *_clicked, _labels)
            if _idx is None:
                continue  # click missed the buttons — ignore it
            selected = _idx
            key = "enter"  # fall through to the existing Enter handling
        if key in ("left", "up", "scroll_up"):
            selected = 0
        elif key in ("right", "down", "scroll_down"):
            selected = 1
        elif key == "enter":
            return ("run", "back")[selected]
        elif key in ("esc", "q"):
            return "back"


def _prefetch_roster(live, console: Console, sources: list, project_key: str, db_path) -> list:
    """Discover the union of assignee names across ``sources`` (network only, no LLM),
    showing a progress screen while the lookup runs. Returns a sorted name list ([] on
    any failure — the caller can then skip member selection)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from yeaboi.analysis import get_team_roster
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_analysis_progress_screen

    names_box: list = [None]

    def _work():
        found: set[str] = set()
        if sources:
            with ThreadPoolExecutor(
                max_workers=min(2, len(sources)),
                thread_name_prefix="analysis-roster",
            ) as executor:
                futures = {
                    executor.submit(
                        get_team_roster,
                        source,
                        project_key if len(sources) == 1 else "",
                        db_path=db_path,
                    ): source
                    for source in sources
                }
                for future in as_completed(futures):
                    source = futures[future]
                    try:
                        found.update(future.result())
                    except Exception as exc:  # best-effort — a failed roster just means "no subset offered"
                        logger.warning("Roster prefetch failed for %s: %s", source, exc)
        names_box[0] = sorted(found)

    done = threading.Event()

    def _runner():
        try:
            _work()
        finally:
            done.set()

    started = time.monotonic()
    duck_working_thread(_runner, name="analysis-roster-prefetch").start()
    tick = 0.0
    while not done.is_set():
        tick += _FRAME_TIME
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                ["Discovering team members…"],
                width=w,
                height=h,
                elapsed=time.monotonic() - started,
                anim_tick=tick,
                source=sources[0] if sources else "",
                mode="analysis",
            )
        )
        time.sleep(_FRAME_TIME)
    return names_box[0] or []


def _prefetch_roster_result(live, console: Console, sources: list, project_key: str, db_path):
    """Status-aware roster lookup used by Analysis setup."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from yeaboi.analysis import get_team_roster_result
    from yeaboi.team_roster import RosterResult
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_analysis_progress_screen

    result_box: list = [None]

    def _work():
        source_results = []
        if sources:
            with ThreadPoolExecutor(
                max_workers=min(2, len(sources)),
                thread_name_prefix="analysis-roster",
            ) as executor:
                futures = {
                    executor.submit(
                        get_team_roster_result,
                        source,
                        project_key if len(sources) == 1 else "",
                        db_path=db_path,
                    ): source
                    for source in sources
                }
                for future in as_completed(futures):
                    source = futures[future]
                    try:
                        source_results.append(future.result())
                    except Exception as exc:
                        logger.warning("Roster prefetch failed for %s: %s", source, exc)
        provider_results = tuple(provider for result in source_results for provider in result.sources)
        members_by_name = {}
        for result in source_results:
            for member in result.members:
                members_by_name.setdefault(member.name.casefold(), member)
        members = tuple(sorted(members_by_name.values(), key=lambda item: item.name.casefold()))
        warnings = tuple(warning for result in source_results for warning in result.warnings)
        if sources and not source_results:
            status = "failed"
        elif any(result.status in {"failed", "partial"} for result in source_results):
            status = "partial" if members else "failed"
        elif members:
            status = "complete"
        else:
            status = "empty"
        result_box[0] = RosterResult(members, status, provider_results, warnings)

    done = threading.Event()

    def _runner():
        try:
            _work()
        finally:
            done.set()

    started = time.monotonic()
    duck_working_thread(_runner, name="analysis-roster-prefetch").start()
    tick = 0.0
    while not done.is_set():
        tick += _FRAME_TIME
        w, h = console.size
        labels = ", ".join("Azure DevOps" if source == "azdevops" else source.title() for source in sources)
        live.update(
            _build_analysis_progress_screen(
                [f"Fetching team members · {labels or 'tracker'}"],
                width=w,
                height=h,
                elapsed=time.monotonic() - started,
                anim_tick=tick,
                source=sources[0] if sources else "",
                mode="analysis",
            )
        )
        time.sleep(_FRAME_TIME)
    return result_box[0] or RosterResult((), "failed", (), ("Roster lookup did not complete",))


def _run_analysis_roster_lookup(
    live,
    console: Console,
    read_key,
    sources: list,
    project_key: str,
    db_path,
):
    """Retry a failed lookup explicitly; never convert failure into unscoped analysis."""
    from rich.align import Align
    from rich.panel import Panel
    from rich.text import Text

    from yeaboi.ui.shared._components import ANALYSIS_THEME, build_page_panel

    while True:
        result = _prefetch_roster_result(live, console, sources, project_key, db_path)
        if result.status != "failed":
            return result
        body = Text()
        body.append("Team members could not be loaded.\n\n", style="bold")
        body.append("\n".join(result.warnings) or "The selected tracker did not return a roster.")
        body.append("\n\nEnter / R  Retry     Esc  Back", style="dim")
        w, h = console.size
        # Wrap the dialog in a full-screen page panel: with an Align root,
        # MusicLive's unstyled-Panel safety net never fires, so the terminal's
        # own background would bleed through around the popup.
        live.update(
            build_page_panel(
                Align.center(
                    Panel(body, title="Analysis · Team members", width=min(76, max(40, w - 4))),
                    vertical="middle",
                ),
                theme=ANALYSIS_THEME,
                height=max(10, h - 1),
            )
        )
        key = read_key()
        if key in ("enter", "r", "R"):
            continue
        return None


# The Analysis setup flow is a WIZARD: an ordered tuple of steps, each with an
# applicability predicate over the live selections. This replaces two duplicated
# ~200-line linear call sequences whose Esc handling fell out of the restart
# loops (and exited the app). Esc/"cancel" moves the index backward; steps whose
# predicate no longer holds are transparent in BOTH directions, so backing over
# e.g. the model offer after switching to Quick depth skips it cleanly.
# One entry per SCREEN — the walker below expresses "back" only as index -= 1, so
# a step that ran two pickers could not offer Esc between them. Hence one scope
# step per code host, ordered github-then-azdo to match the Code row's order.
_WIZARD_STEPS = (
    "features",
    "sources",
    "github_owners",
    "azdo_projects",
    "depth",
    "model",
    "window",
    "members",
    "review",
)


def _run_analysis_setup_wizard(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    grid: dict[str, list[str]],
    roster_fallback: list[str],
    project_key: str = "",
    db_path=None,
) -> dict | None:
    """Walk the Analysis setup steps with Esc-back navigation and state carry-over.

    ``grid`` is the FULL configured component → sub-source map (pre feature
    filtering); ``roster_fallback`` names the delivery trackers to fetch the
    roster from when no delivery component is selected. Returns the run config
    dict, or ``None`` when the user backs out of the first step (the caller
    returns to the analysis landing screen).

    Choices for steps that later become inapplicable stay in ``state`` (so
    re-enabling a feature restores them) but ``_config()`` coerces them out of
    the run payload — a stale Deep depth can never leak into a docs-only run."""
    state: dict = {
        "features": None,
        "components": None,
        "github_owners": None,
        "azdo_projects": None,
        "depth": "deep",
        "model": None,
        "window_days": 120,
        "members": None,
        "roster": None,
        "roster_key": None,
    }
    preflight_box: list = [None]  # ollama preflight, probed at most once per wizard run

    def _feature_set() -> set[str]:
        return set(state["features"] or [])

    def _filtered_grid() -> dict[str, list[str]]:
        fs = _feature_set()
        return {
            "delivery": grid["delivery"] if "delivery" in fs else [],
            "code": grid["code"] if fs & {"ai_footprint", "code_health"} else [],
            "docs": grid["docs"] if "documentation" in fs else [],
        }

    def _preflight() -> dict:
        if preflight_box[0] is None:
            from yeaboi.analysis.llm_runtime import get_ollama_analysis_preflight

            preflight_box[0] = get_ollama_analysis_preflight(db_path)
        return preflight_box[0]

    def _depth_applicable() -> bool:
        return bool(_feature_set() & {"delivery", "ai_footprint"})

    def _effective_depth() -> str:
        return state["depth"] if _depth_applicable() else "quick"

    def _applicable(step: str) -> bool:
        fs = _feature_set()
        comps = state["components"] or {}
        if step in ("features", "sources", "review"):
            return True
        if step in ("github_owners", "azdo_projects"):
            host = "github" if step == "github_owners" else "azdo"
            return bool(fs & {"ai_footprint", "code_health"}) and host in (comps.get("code") or [])
        if step == "depth":
            return _depth_applicable()
        if step == "model":
            return _effective_depth() == "deep" and bool(_preflight().get("offer"))
        if step == "window":
            return bool(fs & {"ai_footprint", "code_health", "documentation"})
        if step == "members":
            return bool(fs & {"delivery", "ai_footprint", "code_health"})
        return False

    def _config() -> dict:
        comps = state["components"] or {}
        members = state["members"] if _applicable("members") else None
        trackers = comps.get("delivery") or roster_fallback
        # Each host's scope is gated on its OWN applicability, so de-selecting a
        # code host at the sources step coerces its stale picks out of the payload
        # (the same discipline that keeps a stale Deep depth out of a docs-only run).
        scope: dict[str, list[str]] = {}
        for _step, _host in (("github_owners", "github"), ("azdo_projects", "azdo")):
            if _applicable(_step) and state[_step]:
                scope[_host] = state[_step]
        return {
            "features": state["features"],
            "components": comps,
            "analysis_scope": scope,
            "depth": _effective_depth(),
            "model": state["model"] if _applicable("model") else None,
            "window_days": state["window_days"] if _applicable("window") else 120,
            "members": members,
            "members_map": {tracker: members for tracker in trackers} if members else None,
        }

    def _members_step(direction: int) -> str:
        sources = (state["components"] or {}).get("delivery") or roster_fallback
        key = tuple(sources)
        if state["roster_key"] != key:  # cache — don't re-fetch on back/forward
            result = _run_analysis_roster_lookup(live, console, read_key, sources, project_key, db_path)
            if result is None:  # lookup failed and the user declined the retry
                return "back"
            state["roster"] = [member.name for member in result.members]
            state["roster_key"] = key
            state["members"] = None  # roster changed — the old subset is invalid
        if not state["roster"]:
            # Nothing to pick: stay transparent in the direction of travel so Esc
            # from review doesn't ping-pong against an auto-advancing step.
            return "next" if direction >= 0 else "back"
        selected = _run_member_select(
            live,
            console,
            read_key,
            frame_time,
            supports_timeout,
            state["roster"],
            initial_members=state["members"],
        )
        if selected == "cancel":
            return "back"
        state["members"] = selected
        return "next"

    def _run_step(step: str, direction: int) -> str:
        if step == "features":
            chosen = _run_analysis_feature_select(
                live,
                console,
                read_key,
                frame_time,
                supports_timeout,
                {
                    "delivery": bool(grid["delivery"]),
                    "ai_footprint": bool(grid["code"]),
                    "code_health": bool(grid["code"]),
                    "documentation": bool(grid["docs"]),
                },
                initial_features=state["features"],
            )
            if chosen == "cancel":
                return "back"
            state["features"] = chosen
            return "next"
        if step == "sources":
            fs = _feature_set()
            chosen = _run_component_select(
                live,
                console,
                read_key,
                frame_time,
                supports_timeout,
                _filtered_grid(),
                descriptions={
                    "code": (
                        "AI footprint + selected-user code-change health"
                        if fs >= {"ai_footprint", "code_health"}
                        else "detectable AI markers in selected-user activity"
                        if "ai_footprint" in fs
                        else "selected-user code-change health"
                    )
                },
                initial=state["components"],
            )
            if chosen == "cancel":
                return "back"
            state["components"] = chosen
            return "next"
        if step in ("github_owners", "azdo_projects"):
            chosen = _run_code_scope_select(
                live,
                console,
                read_key,
                frame_time,
                supports_timeout,
                provider="github" if step == "github_owners" else "azdo",
                initial=state[step],
            )
            if chosen == "cancel":
                return "back"
            state[step] = chosen
            return "next"
        if step == "depth":
            chosen = _run_analysis_depth_select(
                live, console, read_key, frame_time, supports_timeout, initial_depth=state["depth"]
            )
            if chosen == "cancel":
                return "back"
            state["depth"] = chosen
            return "next"
        if step == "model":
            chosen = _run_analysis_model_offer(
                live, console, read_key, frame_time, supports_timeout, _preflight(), initial_model=state["model"]
            )
            if chosen == "cancel":
                return "back"
            state["model"] = chosen
            return "next"
        if step == "window":
            chosen = _run_analysis_window_select(
                live, console, read_key, frame_time, supports_timeout, initial_days=state["window_days"]
            )
            if chosen == "cancel":
                return "back"
            state["window_days"] = chosen
            return "next"
        if step == "members":
            return _members_step(direction)
        config = _config()
        verdict = _run_analysis_setup_review(
            live,
            console,
            read_key,
            frame_time,
            supports_timeout,
            features=config["features"],
            components=config["components"],
            members=config["members"],
            analysis_scope=config["analysis_scope"],
            depth=config["depth"],
            window_days=config["window_days"],
            model=config["model"],
        )
        return "run" if verdict == "run" else "back"

    index = 0
    direction = 1
    while True:
        step = _WIZARD_STEPS[index]
        if not _applicable(step):
            index += direction  # inapplicable steps are transparent in both directions
            continue
        outcome = _run_step(step, direction)
        logger.info("Analysis setup wizard: step=%s outcome=%s", step, outcome)
        if outcome == "run":
            return _config()
        if outcome == "back":
            if index == 0:
                return None  # Esc on the first step → analysis landing screen
            direction = -1
            index -= 1
        else:
            direction = 1
            index += 1


def _run_team_analysis_results(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    profile,
    examples: dict | None,
    *,
    sprint_names: list[str] | None = None,
    team_name: str = "",
    delivery: dict | None = None,
    code: dict | None = None,
    docs: dict | None = None,
    comparison: list | None = None,
    analysis_features: list[str] | None = None,
    active_box: list | None = None,
    source: str = "",
    project_key: str = "",
    retry_config: dict | None = None,
) -> str:
    """Event loop for the team-analysis results screen (overview + section cards).

    Starts on the overview (headline stats, AI executive summary, section list):
    Up/Down choose a section card, Enter on "Open" shows that card's detail view
    (metrics + AI "What this means" + glossary), Back/Esc returns to the
    overview. Export writes HTML + MD from any view. Returns ``"continue"``
    when the user chose Continue (ticket generation) and ``"back"`` on Esc from
    the overview — the callers own what happens next.

    Decoupled components: ``delivery`` (tracker → per-tracker sub-dict) drives a
    ``Tab``-cycled delivery toggle — each tracker keeps its own velocity/contributor
    cards. ``code``/``docs`` are the GLOBAL scans (``{signal, examples}``) shown as
    standalone cards that don't move when the delivery toggle switches. The active
    delivery tracker's (profile, examples, sprint_names, team_name) is mirrored into
    ``active_box`` for the caller's downstream ticket-gen step.
    """
    from yeaboi.ui.mode_select.screens._analysis_sections import visible_card_order

    delivery_order = list(delivery.keys()) if delivery else []
    code_signal = code.get("signal") if code else None
    code_examples = code.get("examples") if code else None
    doc_signal = docs.get("signal") if docs else None
    doc_examples = docs.get("examples") if docs else {}
    src_idx = 0
    base_team_name = team_name  # caller-passed fallback; never let one tracker's team bleed into another
    # Fallback source/project for a delivery-off single-source run (profile is None).
    base_source = source or getattr(profile, "source", "")
    base_project = project_key or getattr(profile, "project_key", "")

    view = "overview"
    card_idx = 0
    scroll = 0
    scroll_meta: dict = {}
    sel = 0
    anim0 = time.monotonic()  # shimmer title clock
    # Anonymize state: None = real profile; an AnonymizedOutput = mask it in place.
    anon = None
    anon_instruction = ""
    logger.info(
        "Analysis results: showing overview for %s/%s",
        getattr(profile, "source", "") or base_source,
        getattr(profile, "project_key", "") or base_project,
    )

    def _failed_features() -> list[str]:
        failed: list[str] = []
        if code_examples:
            enabled = set(code_examples.get("enabled_features") or ())
            if "ai_footprint" in enabled and code_examples.get("activity_coverage", {}).get("status") in {
                "partial",
                "failed",
            }:
                failed.append("ai_footprint")
            if "code_health" in enabled and code_examples.get("coverage_report", {}).get("status") in {
                "partial",
                "failed",
            }:
                failed.append("code_health")
        if doc_examples.get("coverage_report", {}).get("status") in {"partial", "failed"}:
            failed.append("documentation")
        return failed

    def _anon_doc() -> tuple[str, str]:
        from yeaboi.team_profile_exporter import build_team_profile_markdown

        md = build_team_profile_markdown(profile, examples=examples, sprint_names=sprint_names)
        return f"Team Analysis — {profile.project_key}", md

    while True:
        # 'Both' mode: rebind the active tracker each frame from the toggle
        # selection, and mirror it back so callers act on the shown source.
        cur_source = base_source
        cur_project = base_project
        if delivery_order:
            active_source = delivery_order[src_idx]
            _cur = delivery[active_source]
            profile = _cur["profile"]
            examples = _cur["examples"]
            sprint_names = _cur["sprint_names"]
            cur_source = _cur.get("source", "") or active_source
            cur_project = _cur.get("project_key", "")
            # Fall back to the caller's value, NOT the mutated local, so the
            # previous tracker's team name can't leak onto this one's screen.
            team_name = getattr(profile, "team_name", "") or base_team_name
            if active_box is not None:
                active_box[0] = (profile, examples, sprint_names, team_name)

        # A code/docs-only view (no delivery profile) has no velocity profile to
        # export, anonymize or drive ticket generation from — offer only navigation.
        if profile is None:
            actions = ["Open"] if view == "overview" else ["Back"]
        else:
            actions = (
                ["Open", "Export", "Share Online", "Anonymize", "Continue"]
                if view == "overview"
                else ["Back", "Export", "Share Online", "Anonymize", "Continue"]
            )
        retry_features = _failed_features()
        if retry_config and retry_features:
            actions.insert(1, "Retry failed")
        if anon is not None and "Anonymize" in actions:  # swap Anonymize → Adjust + Revert while masked
            i = actions.index("Anonymize")
            actions[i : i + 1] = ["Adjust", "Revert"]

        _pa = getattr(profile, "ai_adoption", None)
        _pd = getattr(profile, "doc_quality", None)
        _has_code = code_signal is not None or bool(_pa and (_pa.scanned_commits + _pa.scanned_prs) > 0)
        _has_code_health = bool(
            (code_examples or {}).get("repository_health")
            or (examples or {}).get("ai_adoption", {}).get("repository_health")
            or (code is not None and "code_health" in set(analysis_features or ()))
        )
        _has_docs = doc_signal is not None or bool(_pd and _pd.pages_scanned > 0)
        order = visible_card_order(
            profile,
            _has_code,
            _has_docs,
            has_code_health=_has_code_health,
            analysis_features=analysis_features,
        )

        # When anonymized, render from a masked copy of the profile (and its sample
        # ``examples``) so the SAME cards/tables re-render with only the words swapped.
        render_profile = profile
        render_examples = examples
        if anon is not None:
            from yeaboi.anonymize.apply import mask_artifact, mask_obj

            render_profile = mask_artifact(profile, anon.replacements)
            render_examples = mask_obj(examples, anon.replacements)

        w, h = console.size
        _panel = _build_team_analysis_screen(
            render_profile,
            scroll_offset=scroll,
            scroll_meta=scroll_meta,
            width=w,
            height=h,
            export_sel=sel,
            examples=render_examples,
            sprint_names=sprint_names,
            team_name=team_name,
            view=view,
            selected_card=card_idx,
            actions=actions,
            shimmer_tick=time.monotonic() - anim0,
            anon_note=_anon_note(anon),
            source_toggle=delivery_order or None,
            active_source=(delivery_order[src_idx] if delivery_order else ""),
            comparison=comparison if view == "overview" else None,
            source=cur_source,
            project_key=cur_project,
            code_signal=code_signal,
            code_examples=code_examples,
            doc_signal=doc_signal,
            doc_examples=doc_examples,
            analysis_features=analysis_features,
        )
        live.update(_panel)

        kk = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(kk)
        if _clicked is not None:
            _idx = button_click(console, _panel, *_clicked, actions)
            if _idx is None:
                continue  # click missed the buttons — ignore it
            sel = _idx
            kk = "enter"  # fall through to the existing Enter handling
        if delivery_order and len(delivery_order) > 1 and kk == "tab":
            # Switch delivery tracker: reset the view/scroll and drop any mask (the
            # replacements were computed for the other profile).
            src_idx = (src_idx + 1) % len(delivery_order)
            view = "overview"
            scroll = 0
            sel = 0
            card_idx = 0
            anon = None
            continue
        if view == "overview" and kk in SCROLL_KEYS:
            # On the overview, Up/Down moves the card selection (the screen
            # auto-scrolls the selected row into view).
            card_idx += 1 if kk in ("down", "scroll_down", "pagedown") else -1
            card_idx %= len(order)
        elif kk in SCROLL_KEYS:
            scroll = coalesce_scroll(scroll, kk, scroll_meta, read_key)
        elif kk == "left":
            sel = max(0, sel - 1)
        elif kk == "right":
            sel = min(len(actions) - 1, sel + 1)
        elif kk in ("enter", " "):
            act = actions[sel]
            if act == "Open":
                view = order[card_idx % len(order)]
                scroll = 0
                sel = 0
                logger.info("Analysis results: opened section %s", view)
            elif act == "Back":
                view = "overview"
                scroll = 0
                sel = 0
            elif act == "Retry failed":
                import threading

                retry_progress: list = []
                retry_result: list = [None]
                retry_error: list[str] = [""]
                retry_done = threading.Event()

                def _retry():
                    try:
                        from yeaboi.analysis import run_team_analysis

                        configured_components = retry_config.get("components", {})
                        retry_components = {
                            "delivery": [],
                            "code": (
                                configured_components.get("code", [])
                                if set(retry_features) & {"ai_footprint", "code_health"}
                                else []
                            ),
                            "docs": (
                                configured_components.get("docs", []) if "documentation" in retry_features else []
                            ),
                        }
                        retry_result[0] = run_team_analysis(
                            source=retry_config.get("source", ""),
                            project_key=retry_config.get("project_key", ""),
                            team_name=retry_config.get("team_name", ""),
                            analysis_depth=retry_config.get("analysis_depth", "deep"),
                            analysis_window_days=retry_config.get("analysis_window_days", 120),
                            analysis_scope=retry_config.get("analysis_scope"),
                            analysis_model=retry_config.get("analysis_model"),
                            analysis_features=retry_features,
                            components=retry_components,
                            members=retry_config.get("members"),
                            progress=retry_progress,
                            db_path=retry_config.get("db_path"),
                        )
                    except Exception as exc:
                        retry_error[0] = str(exc)
                    finally:
                        retry_done.set()

                duck_working_thread(_retry, name="analysis-retry").start()
                retry_started = time.monotonic()
                while not retry_done.is_set():
                    from yeaboi.ui.mode_select.screens._screens_secondary import (
                        _build_analysis_progress_screen,
                    )

                    w, h = console.size
                    live.update(
                        _build_analysis_progress_screen(
                            retry_progress,
                            width=w,
                            height=h,
                            elapsed=time.monotonic() - retry_started,
                            anim_tick=time.monotonic() - retry_started,
                            source=source,
                            mode="analysis",
                        )
                    )
                    time.sleep(frame_time)
                if retry_error[0]:
                    logger.warning("Retry failed analyses: %s", retry_error[0])
                    continue
                retried = retry_result[0] or {}
                code = retried.get("code") or code
                docs = retried.get("docs") or docs
                code_signal = code.get("signal") if code else None
                code_examples = code.get("examples") if code else None
                doc_signal = docs.get("signal") if docs else None
                doc_examples = docs.get("examples") if docs else {}
                if delivery:
                    from yeaboi.analysis.engine import _persist_delivery

                    _persist_delivery(delivery, code, docs, retry_config.get("db_path"))
                view = "overview"
                scroll = 0
                sel = 0
                card_idx = 0
            elif act == "Export":
                logger.info("Analysis results: Export pressed (view=%s)", view)
                if anon is not None:  # export the masked copy, matching the screen
                    doc_title, doc_md = _anon_doc()
                    _anon_export(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        anon=anon,
                        doc_title=doc_title,
                        markdown=doc_md,
                        project_name=profile.project_key or "",
                        source_mode="analysis",
                    )
                else:
                    _team_profile_export_flow(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        profile=profile,
                        examples=examples,
                        sprint_names=sprint_names,
                    )
            elif act == "Share Online":
                from yeaboi.sharing.documents import analysis_document
                from yeaboi.ui.shared._components import ANALYSIS_THEME, analysis_title

                _run_output_share_flow(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    document=analysis_document(
                        profile,
                        examples=examples,
                        sprint_names=sprint_names,
                        anon=anon,
                    ),
                    theme=ANALYSIS_THEME,
                    title_fn=analysis_title,
                )
            elif act == "Anonymize":
                logger.info("Analysis results: Anonymize pressed (view=%s)", view)
                from yeaboi.ui.shared._components import ANALYSIS_THEME, analysis_title

                res = _run_anonymize_pass(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    markdown=_anon_doc()[1],
                    instruction="",
                    project_name=profile.project_key or "",
                    source_mode="analysis",
                    theme=ANALYSIS_THEME,
                    title=analysis_title(),
                )
                if res is not None:
                    anon, anon_instruction = res, ""
            elif act == "Adjust":  # refine the mask with a free-text instruction
                from yeaboi.ui.shared._components import ANALYSIS_THEME, analysis_title

                adj = _standup_read_line(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    prompt="Also mask …  ·  don't mask … (it's public/safe)",
                    step="Anonymize — adjust what's masked",
                    default="",
                    theme=ANALYSIS_THEME,
                    title=analysis_title(),
                    box_rows=6,
                )
                if adj is not None and adj.strip():
                    anon_instruction = f"{anon_instruction}\n{adj.strip()}".strip()
                    res = _run_anonymize_pass(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        markdown=_anon_doc()[1],
                        instruction=anon_instruction,
                        project_name=profile.project_key or "",
                        source_mode="analysis",
                        theme=ANALYSIS_THEME,
                        title=analysis_title(),
                    )
                    if res is not None:
                        anon = res
            elif act == "Revert":  # restore the real names (no LLM call)
                anon, anon_instruction = None, ""
            elif act == "Continue":
                logger.info("Analysis results: continue to ticket generation")
                return "continue"
        elif kk in ("esc", "q"):
            if view == "overview":
                logger.info("Analysis results: closed")
                return "back"
            view = "overview"
            scroll = 0
            sel = 0


def _performance_document(engineer: str) -> tuple[str, str] | str:
    """Return (title, markdown) for the engineer's latest artifact, or an error message."""
    from yeaboi.performance import export

    found = _performance_latest_artifact(engineer)
    if found is None:
        return "Nothing to export yet — generate a 1:1 prep or review first."
    artifact, kind = found
    builders = {
        "prep": (export.build_prep_markdown, "1:1 Prep"),
        "completion": (export.build_completion_markdown, "1:1 Summary"),
        "review": (export.build_review_markdown, "6-Month Review"),
    }
    build, label = builders[kind]
    return f"{label} — {engineer}", build(artifact)


def _run_team_insights(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    profile,
    examples: dict | None,
    *,
    sprint_names: list[str] | None = None,
) -> str:
    """Event loop for the coaching-insights screen (results → insights → confirm).

    Shows the AI's start/stop/keep/try advice before the app suggests
    generating sample tickets. Up/Down scroll, Left/Right pick an action,
    Enter runs it. Returns ``"continue"`` to proceed to ticket generation
    and ``"back"`` (Back/Esc) to return to the results overview.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_insights_screen

    scroll = 0
    scroll_meta: dict = {}
    sel = 0
    actions = ["Continue", "Export", "Back"]
    subtitle = f"{profile.source}/{profile.project_key}  ·  Team Insights" if profile else "Team Insights"
    logger.info("Team insights: showing for %s/%s", profile.source, profile.project_key)

    while True:
        w, h = console.size
        _panel = _build_team_insights_screen(
            profile,
            examples=examples,
            scroll_offset=scroll,
            scroll_meta=scroll_meta,
            width=w,
            height=h,
            action_sel=sel,
            subtitle=subtitle,
        )
        live.update(_panel)

        kk = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(kk)
        if _clicked is not None:
            _idx = button_click(console, _panel, *_clicked, actions)
            if _idx is None:
                continue  # click missed the buttons — ignore it
            sel = _idx
            kk = "enter"  # fall through to the existing Enter handling
        if kk in SCROLL_KEYS:
            scroll = coalesce_scroll(scroll, kk, scroll_meta, read_key)
        elif kk == "left":
            sel = max(0, sel - 1)
        elif kk == "right":
            sel = min(len(actions) - 1, sel + 1)
        elif kk in ("enter", " "):
            act = actions[sel]
            if act == "Continue":
                logger.info("Team insights: continue to ticket generation")
                return "continue"
            if act == "Back":
                logger.info("Team insights: back to results")
                return "back"
            if act == "Export":
                logger.info("Team insights: Export pressed")
                _team_profile_export_flow(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    profile=profile,
                    examples=examples,
                    sprint_names=sprint_names,
                )
        elif kk in ("esc", "q"):
            logger.info("Team insights: back to results")
            return "back"


def _ensure_insights(
    live,
    console: Console,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    profile,
    examples: dict | None,
) -> dict:
    """Backfill coaching insights for profiles saved before insights existed.

    Fresh analyses attach ``examples["insights"]`` at analysis time; old saved
    profiles lack it. Generate on demand (worker thread + progress screen so
    the UI keeps animating) and persist back to the store so it's a one-time
    cost per profile. The generator falls back deterministically, so this
    never fails — worst case the screen shows fallback insights.
    """
    ex = dict(examples or {})
    if isinstance(ex.get("insights"), dict):
        return ex

    import threading

    from yeaboi.tools.team_learning import _generate_team_insights
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_analysis_progress_screen

    logger.info("Team insights: backfilling for %s", profile.team_id)
    result_box: list = [None]
    done = threading.Event()

    def _work() -> None:
        try:
            result_box[0] = _generate_team_insights(profile, ex)
        finally:
            done.set()

    t0 = time.monotonic()
    duck_working_thread(_work, name="performance-insights").start()
    anim = 0.0
    while not done.is_set():
        anim += frame_time
        w, h = console.size
        live.update(
            _build_analysis_progress_screen(
                ["Generating coaching insights…"],
                width=w,
                height=h,
                elapsed=time.monotonic() - t0,
                anim_tick=anim,
                source=profile.source,
                mode="analysis",
            )
        )
        time.sleep(frame_time)

    if isinstance(result_box[0], dict):
        ex["insights"] = result_box[0]
        try:
            from yeaboi.team_profile import TeamProfileStore

            with TeamProfileStore(_ana_dbp) as _s:
                _s.save(profile, examples=ex)
            logger.info("Team insights: backfilled and saved for %s", profile.team_id)
        except Exception as exc:
            logger.warning("Team insights: backfill save failed: %s", exc)
    return ex


def _run_performance_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Event loop for the Performance page.

    Two views. In "roster": Up/Down choose an engineer, Left/Right pick an action
    (1:1 Prep / 1:1 Complete / 6mo Review / Notes / Export / Back), Enter runs it —
    an AI action switches to "detail" showing the artifact. In "detail": Up/Down
    scroll, Export re-writes the artifact, Back returns to the roster.

    # See docs: "Performance Mode" — TUI page
    """
    from yeaboi.performance.render import (
        format_completion_lines,
        format_prep_lines,
        format_review_lines,
    )
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_performance_screen

    base = _collect_performance_data()
    session_id = base["session_id"]
    session_name = base["session_name"]
    roster: list[str] = base["roster"]
    roster_hints: list[str] = base.get("roster_hints", [])

    state = {
        "view": "roster",
        "selected": 0,
        "scroll": 0,
        "scroll_meta": {},
        "sel": 0,
        "message": "",
        "detail_lines": [],
        "detail_title": "",
    }
    roster_actions = ["1:1 Prep", "1:1 Complete", "6mo Review", "Notes", "History", "Export"]  # back tab covers Back
    detail_actions = ["Export", "Share Online", "Anonymize"]  # back tab covers Back
    # Anonymize state: None = real artifact; an AnonymizedOutput = mask the detail lines.
    anon = None
    anon_instruction = ""

    def _detail_actions() -> list[str]:
        acts = list(detail_actions)
        if anon is not None:  # swap Anonymize → Adjust + Revert while masked
            i = acts.index("Anonymize")
            acts[i : i + 1] = ["Adjust", "Revert"]
        return acts

    def _data() -> dict:
        lines = state["detail_lines"]
        title = state["detail_title"]
        # In-place mask: the detail view re-renders the SAME lines with words swapped.
        if anon is not None and state["view"] == "detail":
            from yeaboi.anonymize.apply import apply_replacements, mask_lines

            lines = mask_lines(lines, anon.replacements)
            title = apply_replacements(title, anon.replacements)
        return {
            "session_name": session_name,
            "view": state["view"],
            "roster": roster,
            "roster_hints": roster_hints,
            "selected_idx": state["selected"],
            "detail_lines": lines,
            "detail_title": title,
            "actions": roster_actions if state["view"] == "roster" else _detail_actions(),
            "message": state["message"],
        }

    # Animation clocks — mirror the intake mode picker: a shimmer sweeps the
    # selected engineer's ASCII name (shimmer_tick) and its description reveals
    # typewriter-style (desc_reveal), reset whenever the selection changes.
    anim_start = time.monotonic()
    state["select_time"] = anim_start

    _last_panel = None  # most recently rendered performance panel, for click hit-testing

    def _render() -> None:
        nonlocal _last_panel
        w, h = console.size
        now = time.monotonic()
        tick = now - anim_start  # title shimmer (+ roster-word shimmer) — runs in both views
        sub_reveal = tick * _HEADER_SUB_SPEED
        # The per-engineer description only reveals in the roster view, and restarts
        # whenever the selection changes (select_time), like the intake picker.
        reveal = (now - state["select_time"]) * _DESC_SCROLL_SPEED if state["view"] == "roster" else 0.0
        _last_panel = _build_performance_screen(
            _data(),
            scroll_offset=state["scroll"],
            scroll_meta=state["scroll_meta"],
            width=w,
            height=max(10, h - 1),
            action_sel=state["sel"],
            shimmer_tick=tick,
            desc_reveal=reveal,
            sub_reveal=sub_reveal,
            anon_note=_anon_note(anon),
        )
        live.update(_last_panel)

    def _show_detail(lines: list[str], title: str, message: str) -> None:
        state["view"] = "detail"
        state["detail_lines"] = lines
        state["detail_title"] = title
        state["message"] = message
        state["sel"] = 0
        state["scroll"] = 0

    def _run_action(label: str, engineer: str) -> None:
        """Run one AI/notes action for the selected engineer (blocks briefly)."""
        try:
            if label == "1:1 Prep":
                from yeaboi.performance.engine import run_one_on_one_prep

                state["message"] = f"Generating 1:1 prep for {engineer}…"
                prep = _run_on_worker(
                    lambda: run_one_on_one_prep(engineer, session_id=session_id, db_path=_ana_dbp),
                    lambda _e: _render(),
                    frame_time,
                    drain=read_key if supports_timeout else None,
                )
                logger.info("performance: 1:1 prep generated for engineer=%s", engineer)
                _duck_react("artifact_done")
                _show_detail(format_prep_lines(prep), f"1:1 Prep — {engineer}", "Prep generated.")
            elif label == "1:1 Complete":
                transcript_result = _performance_get_transcript(console, live, read_key, frame_time, supports_timeout)
                if transcript_result is None or not transcript_result[0].strip():
                    logger.info("performance: 1:1 completion cancelled — no transcript (engineer=%s)", engineer)
                    state["message"] = "1:1 completion cancelled — no transcript."
                    return
                transcript, transcript_images = transcript_result
                from yeaboi.performance.engine import complete_one_on_one

                state["message"] = f"Completing the 1:1 for {engineer}…"
                record = _run_on_worker(
                    lambda: complete_one_on_one(
                        engineer, transcript, session_id=session_id, db_path=_ana_dbp, images=transcript_images
                    ),
                    lambda _e: _render(),
                    frame_time,
                    drain=read_key if supports_timeout else None,
                )
                sent = "email sent" if not record.warnings else "see notices"
                logger.info("performance: 1:1 completed for engineer=%s (%s)", engineer, sent)
                _duck_react("artifact_done")
                _show_detail(format_completion_lines(record), f"1:1 Summary — {engineer}", f"Completed — {sent}.")
            elif label == "6mo Review":
                from yeaboi.performance.engine import run_six_month_review

                state["message"] = f"Generating the 6-month review for {engineer}…"
                review = _run_on_worker(
                    lambda: run_six_month_review(engineer, session_id=session_id, db_path=_ana_dbp),
                    lambda _e: _render(),
                    frame_time,
                    drain=read_key if supports_timeout else None,
                )
                logger.info("performance: 6-month review generated for engineer=%s", engineer)
                _duck_react("artifact_done")
                _show_detail(format_review_lines(review), f"6-Month Review — {engineer}", "Review generated.")
            elif label == "Notes":
                note = _standup_read_line(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    prompt=f"Note about {engineer}",
                    step="Performance  —  add note",
                    default="",
                )
                if note and note.strip():
                    from yeaboi.performance.store import PerformanceStore

                    with PerformanceStore(_ana_dbp) as store:
                        store.add_note(engineer, note.strip())
                    logger.info("performance: note saved for engineer=%s", engineer)
                    state["message"] = f"Note saved for {engineer}."
                else:
                    logger.info("performance: note cancelled — nothing entered (engineer=%s)", engineer)
                    state["message"] = "No note entered."
        except Exception as e:  # never let an action crash the TUI
            logger.error("performance action %s failed: %s", label, e, exc_info=True)
            state["message"] = f"{label} failed: {e}"

    _render()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(k)
        if _clicked is not None:
            if _last_panel is None:
                continue
            _labels = roster_actions if state["view"] == "roster" else _detail_actions()
            _idx = button_click(console, _last_panel, *_clicked, _labels)
            if _idx is None:
                continue  # click missed the buttons — ignore it
            state["sel"] = _idx
            k = "enter"  # fall through to the existing Enter handling
        if state["view"] == "roster":
            if k in ("up", "scroll_up"):
                if roster:
                    state["selected"] = (state["selected"] - 1) % len(roster)
                    state["select_time"] = time.monotonic()  # restart the description reveal
            elif k in ("down", "scroll_down"):
                if roster:
                    state["selected"] = (state["selected"] + 1) % len(roster)
                    state["select_time"] = time.monotonic()
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(roster_actions) - 1, state["sel"] + 1)
            elif k in ("enter", " "):
                label = roster_actions[state["sel"]]
                if label == "Back":
                    break
                if not roster:
                    logger.info("performance: %s pressed with empty roster", label)
                    state["message"] = "No engineers — connect Jira or Azure DevOps first."
                else:
                    engineer = roster[state["selected"]]
                    logger.info("performance: %s pressed for engineer=%s", label, engineer)
                    if label == "History":
                        # Browse this engineer's saved artifacts (open / delete / export).
                        _run_performance_hub(console, live, read_key, frame_time, supports_timeout, engineer)
                        roster_hints[:] = _performance_roster_hints(roster)
                    elif label == "Export":
                        msg = _export_via_picker(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            mode="performance",
                            files_export=lambda: _performance_export(engineer),
                            get_document=lambda: _performance_document(engineer),
                        )
                        if msg is not None:
                            state["message"] = msg
                    else:
                        _run_action(label, engineer)
                        # An action may have changed open-action counts / added a
                        # review — refresh the per-engineer hints shown in the roster.
                        roster_hints[:] = _performance_roster_hints(roster)
            elif k in ("esc", "q"):
                break
        else:  # detail view
            if k in SCROLL_KEYS:
                _ns = coalesce_scroll(state["scroll"], k, state["scroll_meta"], read_key)
                if _ns == state["scroll"]:
                    continue  # at a boundary — don't repaint (avoids title-shimmer flicker)
                state["scroll"] = _ns
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(_detail_actions()) - 1, state["sel"] + 1)
            elif k in ("enter", " "):
                label = _detail_actions()[state["sel"]]
                if label == "Back":
                    state["view"] = "roster"
                    state["sel"], state["scroll"], state["message"] = 0, 0, ""
                    anon, anon_instruction = None, ""  # leaving the artifact drops the mask
                    state["select_time"] = time.monotonic()  # replay the reveal on return
                elif label == "Export" and roster:
                    engineer = roster[state["selected"]]
                    logger.info("performance: Export pressed in detail view for engineer=%s", engineer)
                    if anon is not None:  # export the masked copy, matching the screen
                        doc = _performance_document(engineer)
                        msg = (
                            doc
                            if isinstance(doc, str)
                            else _anon_export(
                                console,
                                live,
                                read_key,
                                frame_time,
                                supports_timeout,
                                anon=anon,
                                doc_title=doc[0],
                                markdown=doc[1],
                                project_name=engineer,
                                source_mode="performance",
                            )
                        )
                    else:
                        msg = _export_via_picker(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            mode="performance",
                            files_export=lambda: _performance_export(engineer),
                            get_document=lambda: _performance_document(engineer),
                        )
                    if msg is not None:
                        state["message"] = msg
                elif label == "Share Online" and roster:
                    engineer = roster[state["selected"]]
                    found = _performance_latest_artifact(engineer)
                    if found is None:
                        state["message"] = "Nothing to share yet."
                    else:
                        artifact, kind = found
                        from yeaboi.sharing.documents import performance_document
                        from yeaboi.ui.shared._components import PERFORMANCE_THEME, performance_title

                        _run_output_share_flow(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            document=performance_document(artifact, kind=kind, anon=anon),
                            theme=PERFORMANCE_THEME,
                            title_fn=performance_title,
                        )
                elif label == "Anonymize" and roster:
                    engineer = roster[state["selected"]]
                    logger.info("performance: Anonymize pressed in detail view for engineer=%s", engineer)
                    from yeaboi.ui.shared._components import PERFORMANCE_THEME, performance_title

                    doc = _performance_document(engineer)
                    if isinstance(doc, str):
                        state["message"] = doc
                    else:
                        res = _run_anonymize_pass(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            markdown=doc[1],
                            instruction="",
                            project_name=engineer,
                            source_mode="performance",
                            theme=PERFORMANCE_THEME,
                            title=performance_title(),
                        )
                        if res is not None:
                            anon, anon_instruction = res, ""
                        else:
                            state["message"] = "Anonymize failed (see logs)."
                elif label == "Adjust" and roster:  # refine the mask with a free-text instruction
                    engineer = roster[state["selected"]]
                    from yeaboi.ui.shared._components import PERFORMANCE_THEME, performance_title

                    adj = _standup_read_line(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        prompt="Also mask …  ·  don't mask … (it's public/safe)",
                        step="Anonymize — adjust what's masked",
                        default="",
                        theme=PERFORMANCE_THEME,
                        title=performance_title(),
                        box_rows=6,
                    )
                    if adj is not None and adj.strip():
                        anon_instruction = f"{anon_instruction}\n{adj.strip()}".strip()
                        doc = _performance_document(engineer)
                        if not isinstance(doc, str):
                            res = _run_anonymize_pass(
                                console,
                                live,
                                read_key,
                                frame_time,
                                supports_timeout,
                                markdown=doc[1],
                                instruction=anon_instruction,
                                project_name=engineer,
                                source_mode="performance",
                                theme=PERFORMANCE_THEME,
                                title=performance_title(),
                            )
                            if res is not None:
                                anon = res
                elif label == "Revert":  # restore the real names (no LLM call)
                    anon, anon_instruction = None, ""
            elif k in ("esc", "q"):
                state["view"] = "roster"
                state["sel"], state["scroll"], state["message"] = 0, 0, ""
                anon, anon_instruction = None, ""
                state["select_time"] = time.monotonic()
        _render()
    logger.info("performance: page closed (session=%s)", session_id)


def _collect_reporting_data(message: str = "") -> dict:
    """Gather Reporting page data: the latest session id + display name.

    The report itself is generated on demand (Generate button); this just resolves
    which session's sprint length / project name the report should use. Best-effort —
    the page still works with no session (it reports from the live tracker config).
    """
    data: dict = {"message": message, "session_id": "", "session_name": ""}
    try:
        from yeaboi.sessions import SessionStore, make_display_name

        with SessionStore(_ana_dbp) as store:
            session_id = store.get_latest_session_id() or ""
            data["session_id"] = session_id
            if session_id:
                meta = store.get_session(session_id) or {}
                data["session_name"] = make_display_name(meta) if meta else session_id
    except Exception:
        logger.warning("reporting: failed to resolve latest session", exc_info=True)
    logger.info("reporting: session=%s", data["session_id"])
    return data


def _run_reporting_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Event loop for the Reporting page.

    Four views. In "picker": Up/Down choose a period (Last week / Last sprint / Last
    month / Whole quarter / Custom date range), Left/Right pick an action (Generate
    Report / Sources / Theme / Back). The first Generate confirms the data sources
    (ticketing / code / docs — analysis-style grid, skipped when only one source is
    configured); the choice is sticky for the page and reopenable via Sources.
    For a quarter, Generate opens "sprint_select": Up/Down
    move, Space toggles which sprints make up the quarter (the current quarter's
    sprints pre-checked), Enter generates. A custom range prompts for start/end dates
    first. "theme_select" previews every palette (built-ins + custom) as color
    swatches. "style_select" edits the persisted deck style (colors, font, layout,
    section toggles — Space changes the focused option; Save persists to
    reporting_prefs.json, Reset restores defaults, Back/Esc discards unsaved
    edits). "detail" shows the report: Up/Down scroll, Export
    re-writes files, Back returns to the picker. Generation runs on a worker thread
    behind the shared progress screen — Esc cancels cooperatively.

    # See docs: "Reporting Mode" — TUI page
    """
    from datetime import date as _date
    from datetime import timedelta as _timedelta

    from yeaboi.reporting.activity import (
        PERIOD_LABELS,
        PERIOD_LAST_MONTH,
        PERIOD_LAST_SPRINT,
        PERIOD_LAST_WEEK,
        PERIOD_QUARTER,
        PERIOD_WINDOW,
        available_report_sources,
    )
    from yeaboi.reporting.sprints import list_sprints, mark_in_quarter, quarter_bounds
    from yeaboi.reporting.style import (
        COLOR_ROLES,
        CONTENT_FITS,
        DEFAULT_STYLE,
        FONT_PRESETS,
        FONT_SCALES,
        LAYOUTS,
        MAX_BULLET_CHOICES,
        STYLE_FIELDS,
        load_deck_style,
        save_deck_style,
        style_summary,
    )
    from yeaboi.reporting.themes import all_palettes
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_reporting_screen

    base = _collect_reporting_data()
    session_id = base["session_id"]
    session_name = base["session_name"]

    q_label, q_start, q_end = quarter_bounds()
    periods = [
        (PERIOD_LAST_WEEK, PERIOD_LABELS[PERIOD_LAST_WEEK], "The last 7 days of completed work"),
        (PERIOD_LAST_SPRINT, PERIOD_LABELS[PERIOD_LAST_SPRINT], "The most recent sprint's completed work"),
        (PERIOD_LAST_MONTH, PERIOD_LABELS[PERIOD_LAST_MONTH], "The last ~4 weeks across ~2 sprints"),
        (PERIOD_QUARTER, f"Whole quarter ({q_label})", "Pick the sprints that make up the quarter"),
        (PERIOD_WINDOW, PERIOD_LABELS[PERIOD_WINDOW], "Pick explicit start and end dates"),
    ]
    # Loaded once per page entry — custom palettes come from reporting_themes.json;
    # the source grid probes which trackers / code hosts / doc platforms have creds.
    palettes = all_palettes()
    theme_names = list(palettes)
    source_grid = available_report_sources()

    state = {
        "view": "picker",
        "selected": 0,  # period index
        "scroll": 0,
        "scroll_meta": {},
        "sel": 0,  # action button index
        "message": "",
        "theme": "midnight",
        "detail_title": "",
        "report": None,
        # sources selection — None until confirmed; sticky for the page session
        "sources": None,
        # sprint_select view state
        "sprints": [],  # list[SprintRef]
        "sprint_cursor": 0,
        "sprint_checked": set(),
        # theme_select view state
        "theme_cursor": 0,
        "theme_return_view": "picker",
        # style_select view state — the persisted deck style, edited in place
        "style": load_deck_style(),
        "style_cursor": 0,
        "style_return_view": "picker",
    }
    picker_actions = ["Generate Report", "Sources", "Theme", "Style", "Back"]
    detail_actions = ["Export", "Share Online", "Anonymize", "Theme", "Style", "Back"]
    sprint_actions = ["Generate Report", "Back"]
    theme_actions = ["Select", "Back"]
    style_actions = ["Save", "Reset", "Back"]
    # Anonymize state: None = real report; an AnonymizedOutput = mask the shown report.
    anon = None
    anon_instruction = ""

    def _actions() -> list[str]:
        if state["view"] == "detail":
            acts = list(detail_actions)
            if anon is not None:  # swap Anonymize → Adjust + Revert while masked
                i = acts.index("Anonymize")
                acts[i : i + 1] = ["Adjust", "Revert"]
            return acts
        if state["view"] == "sprint_select":
            return sprint_actions
        if state["view"] == "theme_select":
            return theme_actions
        if state["view"] == "style_select":
            return style_actions
        return picker_actions

    def _data() -> dict:
        report = state["report"]
        title = state["detail_title"]
        # In-place mask: the detail view re-renders the SAME report with words swapped.
        if anon is not None and state["view"] == "detail" and report is not None:
            from yeaboi.anonymize.apply import apply_replacements, mask_artifact

            report = mask_artifact(report, anon.replacements)
            title = apply_replacements(title, anon.replacements)
        return {
            "session_name": session_name,
            "view": state["view"],
            "periods": periods,
            "selected_idx": state["selected"],
            "theme": state["theme"],
            "report": report,
            "detail_title": title,
            "actions": _actions(),
            "message": state["message"],
            "sources_summary": _sources_summary(),
            # sprint_select rendering
            "quarter_label": q_label,
            "sprints": state["sprints"],
            "sprint_cursor": state["sprint_cursor"],
            "sprint_checked": state["sprint_checked"],
            # theme_select rendering
            "theme_names": theme_names,
            "palettes": palettes,
            "theme_cursor": state["theme_cursor"],
            # style_select rendering
            "style": state["style"],
            "style_cursor": state["style_cursor"],
            "style_summary": style_summary(state["style"]),
        }

    anim_start = time.monotonic()

    _last_panel = None  # most recently rendered reporting panel, for click hit-testing

    def _render() -> None:
        nonlocal _last_panel
        w, h = console.size
        tick = time.monotonic() - anim_start
        _last_panel = _build_reporting_screen(
            _data(),
            scroll_offset=state["scroll"],
            scroll_meta=state["scroll_meta"],
            width=w,
            height=max(10, h - 1),
            action_sel=state["sel"],
            shimmer_tick=tick,
            sub_reveal=tick * _HEADER_SUB_SPEED,
            anon_note=_anon_note(anon),
        )
        live.update(_last_panel)

    def _show_report(report, msg: str) -> None:
        state["report"] = report
        state["detail_title"] = f"Delivery Report — {report.period_label}"
        state["view"] = "detail"
        state["sel"], state["scroll"] = 0, 0
        state["message"] = msg

    def _delivered_msg(report) -> str:
        n = len(report.delivered_items)
        plural = "s" if n != 1 else ""
        return f"Report generated — {n} item{plural} delivered. Auto-saved (md/html/slides)."

    source_titles = {
        "jira": "Jira",
        "azuredevops": "Azure DevOps",
        "github": "GitHub",
        "confluence": "Confluence",
        "notion": "Notion",
    }

    def _sources_summary() -> str:
        """One status line for the picker: what the next Generate will consult."""
        sel = state["sources"] if state["sources"] is not None else source_grid

        def _fmt(component: str) -> str:
            chosen = [source_titles.get(s, s) for s in sel.get(component, [])]
            return " + ".join(chosen) if chosen else "—"

        return f"Sources: {_fmt('delivery')}  ·  Code: {_fmt('code')}  ·  Docs: {_fmt('docs')}"

    def _confirm_sources(*, force: bool = False) -> dict | None:
        """Confirm which data sources feed the report (analysis-style grid).

        Returns the ``{component: [sources]}`` selection (sticky in
        ``state["sources"]`` for the page session), or None when the user Esc-backs
        out. Shown only when a real choice exists (≥2 configured sources overall);
        otherwise the single/empty configuration is confirmed silently. The Sources
        button passes ``force=True`` to open it regardless.
        """
        from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

        grid = {c: list(v) for c, v in source_grid.items() if v}
        total = sum(len(v) for v in grid.values())
        if not grid:
            # Nothing configured — the engine surfaces the "no board" warning.
            if force:
                state["message"] = "No data sources configured — connect a tracker in Settings."
            state["sources"] = {}
            return {}
        if not force and total <= 1:
            logger.info("reporting: single configured source — skipping sources step")
            state["sources"] = grid
            return grid
        logger.info("reporting: sources select opened (%d source(s))", total)
        result = _run_component_select(
            live,
            console,
            read_key,
            frame_time,
            supports_timeout,
            grid,
            descriptions={
                "delivery": "where completed tickets come from",
                "code": "merged PRs/commits as supporting context",
                "docs": "doc updates as supporting context",
            },
            initial=state["sources"],
            theme=REPORTING_THEME,
            brand="REPORTING SETUP",
            title_builder=lambda w, h: reporting_title(width=w),
            footer_verb="report on",
            required={"delivery": "Select at least one ticketing source."} if grid.get("delivery") else None,
        )
        if result == "cancel":
            logger.info("reporting: sources selection cancelled")
            return None
        # A fully-unchecked component comes back absent from the picker result;
        # store it as an explicit empty list — a missing key means "auto" to
        # normalize_sources, which would silently re-enable what the user just
        # deselected.
        result = {c: result.get(c, []) for c in grid}
        state["sources"] = result
        logger.info("reporting: sources confirmed — %s", result)
        return result

    def _ensure_sources() -> bool:
        """Generate-path gate: confirm sources once, then stay sticky."""
        if state["sources"] is not None:
            return True
        return _confirm_sources() is not None

    def _run_report_generate(make_report) -> None:
        """Run ``make_report`` on a worker thread behind the shared progress screen.

        ``make_report(on_progress=..., cancel_event=...)`` runs the engine; the frame
        loop repaints live progress at ~30fps (the same worker-thread pattern the
        standup generate uses — without it the whole TUI froze for the tracker fetch
        + LLM call). Esc/q sets the cancel event; the engine raises ReportCancelledError
        at the next stage boundary. Success lands on the detail view.
        """
        import threading

        from yeaboi.reporting.engine import ReportCancelledError
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen
        from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

        progress: list[str] = ["Starting"]
        result_box: list = [None, None]  # [report, exception]
        cancel_event = threading.Event()

        def _worker() -> None:
            try:
                result_box[0] = make_report(on_progress=progress.append, cancel_event=cancel_event)
            except BaseException as e:  # noqa: BLE001 — re-surfaced on the UI thread below
                result_box[1] = e

        thread = duck_working_thread(_worker, name="reporting-generate")
        thread.start()
        start = time.monotonic()
        cancelling = False
        while thread.is_alive():
            elapsed = time.monotonic() - start
            w, h = console.size
            live.update(
                _build_standup_progress_screen(
                    list(progress),
                    width=w,
                    height=max(10, h - 1),
                    elapsed=elapsed,
                    anim_tick=elapsed,
                    theme=REPORTING_THEME,
                    # width picks the tall ANSI wordmark (the pixel-block art is the
                    # narrow-terminal fallback); shimmer animates like the page header.
                    title=reporting_title(elapsed, width=w),
                    label="Cancelling…" if cancelling else "Generating delivery report",
                )
            )
            # With timeout support the key read doubles as the frame pacer; without
            # it a blocking read would stall the spinner, so just sleep (no cancel).
            if supports_timeout:
                k = read_key(timeout=frame_time)
                if k in ("esc", "q") and not cancelling:
                    cancelling = True
                    cancel_event.set()
                    logger.info("reporting: generate cancel requested")
            else:
                time.sleep(1 / 30)
        thread.join()
        err = result_box[1]
        if err is None and result_box[0] is not None:
            report = result_box[0]
            logger.info("reporting: report generated — %d item(s)", len(report.delivered_items))
            _duck_react("report_done")
            _show_report(report, _delivered_msg(report))
        elif isinstance(err, ReportCancelledError):
            logger.info("reporting: generate cancelled")
            state["message"] = "Generation cancelled."
        else:
            logger.error("reporting generate failed: %s", err, exc_info=err)
            state["message"] = f"Generate failed: {err}"

    def _generate() -> None:
        """Generate the delivery report for the selected simple period (threaded)."""
        period_key = periods[state["selected"]][0]
        logger.info("reporting: generating report (period=%s, session=%s)", period_key, session_id)
        from yeaboi.reporting.engine import run_delivery_report

        def _make(on_progress=None, cancel_event=None):
            return run_delivery_report(
                period_key,
                session_id=session_id,
                db_path=_ana_dbp,
                theme=state["theme"],
                sources=state["sources"],
                on_progress=on_progress,
                cancel_event=cancel_event,
            )

        _run_report_generate(_make)

    def _run_quarter(window_start: str, window_end: str, names: tuple, label: str) -> None:
        """Generate a quarter report over an explicit sprint-derived window (threaded)."""
        logger.info(
            "reporting: generating quarter report %s → %s over %d sprint(s) (session=%s)",
            window_start,
            window_end,
            len(names),
            session_id,
        )
        from yeaboi.reporting.engine import run_delivery_report

        def _make(on_progress=None, cancel_event=None):
            return run_delivery_report(
                PERIOD_QUARTER,
                session_id=session_id,
                db_path=_ana_dbp,
                window_start=window_start,
                window_end=window_end,
                sprint_names=names,
                period_label_override=label,
                theme=state["theme"],
                sources=state["sources"],
                on_progress=on_progress,
                cancel_event=cancel_event,
            )

        _run_report_generate(_make)

    def _ask_date(step: str, default: str, *, not_before: str = "") -> str | None:
        """Prompt for one ISO date with a retry loop; None when the user Esc-cancels."""
        from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

        prompt = "YYYY-MM-DD"
        while True:
            raw = _standup_read_line(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                prompt=prompt,
                step=step,
                default=default,
                theme=REPORTING_THEME,
                title=reporting_title(width=console.size[0]),
            )
            if raw is None:
                return None
            raw = (raw or "").strip() or default
            try:
                value = _date.fromisoformat(raw).isoformat()
            except ValueError:
                prompt = f"{raw!r} isn't a date — enter YYYY-MM-DD"
                continue
            if not_before and value < not_before:
                prompt = f"End date must not be before {not_before} — enter YYYY-MM-DD"
                continue
            return value

    def _generate_window() -> None:
        """Prompt for explicit start/end dates, then generate over that window (threaded)."""
        today = _date.today()
        start_iso = _ask_date("Custom range — start date", (today - _timedelta(days=28)).isoformat())
        if start_iso is None:
            state["message"] = ""
            return
        end_iso = _ask_date("Custom range — end date", today.isoformat(), not_before=start_iso)
        if end_iso is None:
            state["message"] = ""
            return
        logger.info("reporting: generating custom-range report %s → %s (session=%s)", start_iso, end_iso, session_id)
        from yeaboi.reporting.engine import run_delivery_report

        def _make(on_progress=None, cancel_event=None):
            return run_delivery_report(
                PERIOD_WINDOW,
                session_id=session_id,
                db_path=_ana_dbp,
                window_start=start_iso,
                window_end=end_iso,
                theme=state["theme"],
                sources=state["sources"],
                on_progress=on_progress,
                cancel_event=cancel_event,
            )

        _run_report_generate(_make)

    def _open_sprint_select() -> None:
        """Load the sprint list for the quarter and switch to the multi-select view.

        When no sprint list is available (no tracker, no plan sprints), skip the
        picker and report straight over the calendar-quarter dates.
        """
        plan_state = {}
        try:
            from yeaboi.sessions import SessionStore

            with SessionStore(_ana_dbp) as store:
                plan_state = store.load_state(session_id) or {}
        except Exception:  # noqa: BLE001 — plan state is only the fallback source
            logger.warning("reporting: could not load plan state for sprint list", exc_info=True)
        refs = mark_in_quarter(list_sprints(plan_state), q_start, q_end)
        if not refs:
            logger.info("reporting: no sprint list available — reporting over the calendar quarter")
            today_iso = _date.today().isoformat()
            _run_quarter(q_start, min(q_end, today_iso), (), q_label)
            state["message"] = "No sprint list available — reported over the calendar quarter. " + state["message"]
            return
        logger.info("reporting: sprint multi-select opened (%d sprint(s))", len(refs))
        state["sprints"] = refs
        state["sprint_checked"] = {i for i, s in enumerate(refs) if s.in_quarter}
        inq = [i for i, s in enumerate(refs) if s.in_quarter]
        state["sprint_cursor"] = inq[0] if inq else 0
        state["view"] = "sprint_select"
        state["sel"], state["scroll"], state["message"] = 0, 0, ""

    def _generate_from_selection() -> None:
        """Compute the window from the checked sprints and generate the quarter report."""
        refs = state["sprints"]
        checked = sorted(i for i in state["sprint_checked"] if 0 <= i < len(refs))
        if not checked:
            logger.info("reporting: sprint selection confirmed with no sprints checked")
            state["message"] = "Select at least one sprint (Space to toggle)."
            return
        logger.info("reporting: sprint selection confirmed (%d of %d sprint(s))", len(checked), len(refs))
        sel = [refs[i] for i in checked]
        starts = [s.start_date for s in sel if s.start_date]
        ends = [s.end_date for s in sel if s.end_date]
        today_iso = _date.today().isoformat()
        window_start = min(starts) if starts else q_start
        window_end = min(max(ends) if ends else q_end, today_iso)
        names = tuple(s.name for s in sel)
        detected = {i for i, s in enumerate(refs) if s.in_quarter}
        label = q_label if set(checked) == detected else f"{q_label} (custom)"
        _run_quarter(window_start, window_end, names, label)

    def _tilde(p) -> str:
        """Abbreviate $HOME to ~ so export paths survive the banner's ellipsis."""
        home = str(Path.home())
        s = str(p)
        return "~" + s[len(home) :] if s.startswith(home) else s

    def _resolve_fit_for_export():
        """Resolve content_fit="ask" by offering the extra slides ("expand") or the
        fixed grid ("tight") — the deck builders themselves can never prompt.

        Returns the DeckStyle to export with, or None when the user Esc-cancels.
        Only asks when expanding actually costs slides; the answer applies to this
        export only (the saved preference stays "ask").
        """
        style = state["style"]
        report = state.get("report")
        if style.content_fit != "ask" or report is None:
            return style
        from yeaboi.reporting.layout import count_fit_slides
        from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

        tight_n, expand_n = count_fit_slides(report, style)
        if expand_n <= tight_n:  # everything fits without extra slides — nothing to ask
            return dataclasses.replace(style, content_fit="expand")
        extra = expand_n - tight_n
        raw = _standup_read_line(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            prompt="y = add them, everything fits · n = keep it tight (may trim with '… and N more')",
            step=f"Fit all content with {extra} extra slide{'s' if extra != 1 else ''}?",
            default="y",
            theme=REPORTING_THEME,
            title=reporting_title(width=console.size[0]),
        )
        if raw is None:
            return None
        chosen = "tight" if raw.strip().lower().startswith("n") else "expand"
        logger.info("reporting: content-fit offer (+%d slide(s)) answered %s", extra, chosen)
        return dataclasses.replace(state["style"], content_fit=chosen)

    def _export_files() -> str:
        report = state.get("report")
        style = _resolve_fit_for_export()
        if style is None:
            return "Export cancelled."
        try:
            from yeaboi.reporting.export import export_report
            from yeaboi.reporting.store import ReportingStore

            with ReportingStore(_ana_dbp) as _store:
                run_history = _store.get_history(session_id, limit=30)
            paths = export_report(report, theme=state["theme"], history=run_history, style=style)
            kinds = "Markdown + HTML + slides" + (" + PowerPoint" if "pptx" in paths else "")
            return f"Exported to {_tilde(paths['markdown'].parent)}  ({kinds})"
        except Exception as e:  # noqa: BLE001
            logger.error("reporting export failed: %s", e, exc_info=True)
            return f"Export failed: {e}"

    def _export_pptx() -> str:
        report = state.get("report")
        style = _resolve_fit_for_export()
        if style is None:
            return "Export cancelled."
        try:
            from yeaboi.reporting.export import export_pptx_only

            path = export_pptx_only(report, theme=state["theme"], style=style)
            if path is None:
                return "PowerPoint export needs python-pptx — install with: uv sync --extra docs"
            return f"Exported PowerPoint to {_tilde(path)}"
        except Exception as e:  # noqa: BLE001
            logger.error("reporting pptx export failed: %s", e, exc_info=True)
            return f"PowerPoint export failed: {e}"

    def _export_document() -> tuple[str, str] | str:
        from yeaboi.paths import get_reporting_export_dir
        from yeaboi.reporting.export import _slug, _title, build_report_markdown

        report = state.get("report")
        # charts_dir gives the delivered-work chart an on-disk home so the
        # publish layer can upload it alongside the page.
        charts_dir = get_reporting_export_dir(_slug(report.project_name or "report"))
        return _title(report), build_report_markdown(report, charts_dir=charts_dir)

    def _export() -> None:
        report = state.get("report")
        if report is None:
            logger.info("reporting: Export pressed with nothing to export")
            state["message"] = "Nothing to export yet — generate a report first."
            return
        logger.info("reporting: Export pressed (period=%s)", report.period_label)
        if anon is not None:  # export the masked copy, matching the screen
            doc = _export_document()
            if isinstance(doc, str):
                state["message"] = doc
                return
            msg = _anon_export(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                anon=anon,
                doc_title=doc[0],
                markdown=doc[1],
                project_name=report.project_name or "",
                source_mode="reporting",
            )
        else:
            msg = _export_via_picker(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                mode="reporting",
                files_export=_export_files,
                get_document=_export_document,
                extra_options=["PowerPoint"],
                extra_handlers={"powerpoint": _export_pptx},
            )
        if msg is not None:
            state["message"] = msg

    def _open_theme_select() -> None:
        """Switch to the palette preview list, remembering where to return."""
        state["theme_return_view"] = state["view"]
        state["theme_cursor"] = theme_names.index(state["theme"]) if state["theme"] in theme_names else 0
        state["view"] = "theme_select"
        state["sel"], state["message"] = 0, ""
        logger.info("reporting: theme select opened (%d palette(s))", len(theme_names))

    def _close_theme_select(*, chosen: bool) -> None:
        if chosen:
            state["theme"] = theme_names[state["theme_cursor"]]
            state["message"] = f"Presentation theme: {state['theme']}"
            logger.info("reporting: presentation theme set to %s", state["theme"])
        state["view"] = state["theme_return_view"]
        state["sel"] = 0

    # Baseline for the style editor: what's on disk. Space edits a working copy
    # (live previews + this session's exports); Save persists, Back/Esc discards.
    style_saved = state["style"]

    def _open_style_select() -> None:
        """Switch to the deck-style options list, remembering where to return."""
        nonlocal style_saved
        style_saved = state["style"]
        state["style_return_view"] = state["view"]
        state["view"] = "style_select"
        state["sel"], state["message"] = 0, ""
        logger.info("reporting: style select opened (%s)", style_summary(state["style"]))

    def _save_style_select() -> None:
        nonlocal style_saved
        save_deck_style(state["style"])
        style_saved = state["style"]
        state["view"] = state["style_return_view"]
        state["sel"] = 0
        state["message"] = "Style saved — applies to every future export."
        logger.info("reporting: style saved (%s)", style_summary(state["style"]))

    def _close_style_select() -> None:
        discarded = state["style"] != style_saved
        state["style"] = style_saved  # Back/Esc drops unsaved edits
        state["view"] = state["style_return_view"]
        state["sel"] = 0
        if discarded:
            state["message"] = "Style changes discarded."
            logger.info("reporting: style changes discarded")

    def _set_style(field: str, value) -> None:
        """Apply one style change to the working copy (persisted on Save)."""
        state["style"] = dataclasses.replace(state["style"], **{field: value})
        logger.info("reporting: style %s → %r", field, value)

    def _read_custom_hex(label: str, current: str) -> str | None:
        """Prompt for a #RRGGBB color; retries on bad input, Esc keeps the old value."""
        import re as _re

        from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

        prompt = "#RRGGBB, a palette role (accent/accent2/fg/muted), or blank for theme default"
        while True:
            raw = _standup_read_line(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                prompt=prompt,
                step=f"{label} — custom color",
                default=current,
                theme=REPORTING_THEME,
                title=reporting_title(width=console.size[0]),
            )
            if raw is None:
                return None
            raw = raw.strip().lower()
            if raw == "" or raw in COLOR_ROLES or _re.match(r"^#[0-9a-f]{6}$", raw):
                return raw
            prompt = f"{raw!r} isn't valid — use #RRGGBB, accent/accent2/fg/muted, or blank"

    def _cycle_style() -> None:
        """Space on a style row: flip/cycle the focused option (colors end in a custom prompt)."""
        field, label, kind = STYLE_FIELDS[state["style_cursor"]]
        current = getattr(state["style"], field)
        if kind == "bool":
            _set_style(field, not current)
        elif kind == "choice":
            values = {
                "font_family": tuple(FONT_PRESETS),
                "font_scale": tuple(FONT_SCALES),
                "layout": LAYOUTS,
                "content_fit": CONTENT_FITS,
            }[field]
            _set_style(field, values[(values.index(current) + 1) % len(values)] if current in values else values[0])
        elif kind == "int":
            nxt = next((v for v in MAX_BULLET_CHOICES if v > current), MAX_BULLET_CHOICES[0])
            _set_style(field, nxt)
        elif kind == "color":
            # "" → roles in order → custom hex prompt → back to "".
            cycle = ("", *COLOR_ROLES)
            if current in cycle and current != cycle[-1]:
                _set_style(field, cycle[cycle.index(current) + 1])
            elif current == cycle[-1]:
                chosen = _read_custom_hex(label, "")
                _set_style(field, chosen if chosen is not None else "")
            else:  # currently a custom hex — wrap to theme default
                _set_style(field, "")
        else:  # text (footer)
            from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

            raw = _standup_read_line(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                prompt="Shown on every slide — blank clears it",
                step="Footer text",
                default=current,
                theme=REPORTING_THEME,
                title=reporting_title(width=console.size[0]),
            )
            if raw is not None:
                _set_style(field, raw.strip()[:120])

    _render()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(k)
        if _clicked is not None:
            if _last_panel is None:
                continue
            _idx = button_click(console, _last_panel, *_clicked, _actions())
            if _idx is None:
                continue  # click missed the buttons — ignore it
            state["sel"] = _idx
            k = "enter"  # fall through to the existing Enter handling
        if state["view"] == "picker":
            if k in ("up", "scroll_up"):
                state["selected"] = (state["selected"] - 1) % len(periods)
            elif k in ("down", "scroll_down"):
                state["selected"] = (state["selected"] + 1) % len(periods)
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(picker_actions) - 1, state["sel"] + 1)
            elif k in ("enter", " "):
                label = picker_actions[state["sel"]]
                if label == "Back":
                    break
                elif label == "Generate Report":
                    if not _ensure_sources():
                        state["message"] = ""
                        _render()
                        continue
                    period_key = periods[state["selected"]][0]
                    if period_key == PERIOD_QUARTER:
                        _open_sprint_select()
                    elif period_key == PERIOD_WINDOW:
                        _generate_window()
                    else:
                        _generate()
                elif label == "Sources":
                    _confirm_sources(force=True)
                elif label == "Theme":
                    _open_theme_select()
                elif label == "Style":
                    _open_style_select()
            elif k in ("esc", "q"):
                break
        elif state["view"] == "theme_select":
            n_themes = len(theme_names)
            if k in ("up", "scroll_up"):
                state["theme_cursor"] = (state["theme_cursor"] - 1) % n_themes
            elif k in ("down", "scroll_down"):
                state["theme_cursor"] = (state["theme_cursor"] + 1) % n_themes
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(theme_actions) - 1, state["sel"] + 1)
            elif k in ("enter", " "):
                _close_theme_select(chosen=theme_actions[state["sel"]] == "Select")
            elif k in ("esc", "q"):
                _close_theme_select(chosen=False)
        elif state["view"] == "style_select":
            n_fields = len(STYLE_FIELDS)
            if k in ("up", "scroll_up"):
                state["style_cursor"] = (state["style_cursor"] - 1) % n_fields
            elif k in ("down", "scroll_down"):
                state["style_cursor"] = (state["style_cursor"] + 1) % n_fields
            elif k == " ":  # change the option under the cursor
                _cycle_style()
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(style_actions) - 1, state["sel"] + 1)
            elif k == "enter":
                label = style_actions[state["sel"]]
                if label == "Save":
                    _save_style_select()
                elif label == "Reset":
                    state["style"] = DEFAULT_STYLE  # working copy only — Save commits
                    logger.info("reporting: style reset to defaults (unsaved)")
                else:  # Back
                    _close_style_select()
            elif k in ("esc", "q"):
                _close_style_select()
        elif state["view"] == "sprint_select":
            n_sprints = len(state["sprints"])
            if k in ("up", "scroll_up"):
                if n_sprints:
                    state["sprint_cursor"] = (state["sprint_cursor"] - 1) % n_sprints
            elif k in ("down", "scroll_down"):
                if n_sprints:
                    state["sprint_cursor"] = (state["sprint_cursor"] + 1) % n_sprints
            elif k == " ":  # toggle the sprint under the cursor
                cur = state["sprint_cursor"]
                if cur in state["sprint_checked"]:
                    state["sprint_checked"].discard(cur)
                else:
                    state["sprint_checked"].add(cur)
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(sprint_actions) - 1, state["sel"] + 1)
            elif k == "enter":
                label = sprint_actions[state["sel"]]
                if label == "Back":
                    state["view"] = "picker"
                    state["sel"], state["scroll"], state["message"] = 0, 0, ""
                else:  # Generate Report
                    _generate_from_selection()
            elif k in ("esc", "q"):
                state["view"] = "picker"
                state["sel"], state["scroll"], state["message"] = 0, 0, ""
        else:  # detail view
            if k in SCROLL_KEYS:
                _ns = coalesce_scroll(state["scroll"], k, state["scroll_meta"], read_key)
                if _ns == state["scroll"]:
                    continue  # at a boundary — don't repaint (avoids title-shimmer flicker)
                state["scroll"] = _ns
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(_actions()) - 1, state["sel"] + 1)
            elif k in ("enter", " "):
                label = _actions()[state["sel"]]
                if label == "Back":
                    state["view"] = "picker"
                    state["sel"], state["scroll"], state["message"] = 0, 0, ""
                    anon, anon_instruction = None, ""  # leaving the report drops the mask
                elif label == "Export":
                    _export()
                elif label == "Share Online":
                    report = state.get("report")
                    if report is not None:
                        from yeaboi.sharing.documents import reporting_document
                        from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

                        _run_output_share_flow(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            document=reporting_document(report, anon=anon),
                            theme=REPORTING_THEME,
                            title_fn=reporting_title,
                        )
                elif label == "Anonymize":
                    report = state.get("report")
                    if report is None:
                        state["message"] = "Nothing to anonymize yet — generate a report first."
                    else:
                        from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

                        doc = _export_document()
                        if isinstance(doc, str):
                            state["message"] = doc
                        else:
                            res = _run_anonymize_pass(
                                console,
                                live,
                                read_key,
                                frame_time,
                                supports_timeout,
                                markdown=doc[1],
                                instruction="",
                                project_name=report.project_name or "",
                                source_mode="reporting",
                                theme=REPORTING_THEME,
                                title=reporting_title(width=console.size[0]),
                            )
                            if res is not None:
                                anon, anon_instruction = res, ""
                            else:
                                state["message"] = "Anonymize failed (see logs)."
                elif label == "Adjust":  # refine the mask with a free-text instruction
                    from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

                    adj = _standup_read_line(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        prompt="Also mask …  ·  don't mask … (it's public/safe)",
                        step="Anonymize — adjust what's masked",
                        default="",
                        theme=REPORTING_THEME,
                        title=reporting_title(width=console.size[0]),
                        box_rows=6,
                    )
                    if adj is not None and adj.strip():
                        anon_instruction = f"{anon_instruction}\n{adj.strip()}".strip()
                        doc = _export_document()
                        if not isinstance(doc, str):
                            res = _run_anonymize_pass(
                                console,
                                live,
                                read_key,
                                frame_time,
                                supports_timeout,
                                markdown=doc[1],
                                instruction=anon_instruction,
                                project_name=(state.get("report").project_name if state.get("report") else "") or "",
                                source_mode="reporting",
                                theme=REPORTING_THEME,
                                title=reporting_title(width=console.size[0]),
                            )
                            if res is not None:
                                anon = res
                elif label == "Revert":  # restore the real names (no LLM call)
                    anon, anon_instruction = None, ""
                elif label == "Theme":
                    _open_theme_select()
                elif label == "Style":
                    _open_style_select()
            elif k in ("esc", "q"):
                state["view"] = "picker"
                state["sel"], state["scroll"], state["message"] = 0, 0, ""
                anon, anon_instruction = None, ""
        _render()
    logger.info("reporting: page closed (session=%s)", session_id)


def _pick_analysis_profile(
    console: Console, live, read_key, frame_time: float, supports_timeout: bool, *, board_configured: bool
) -> str:
    """Show the analysis-profile picker and return the chosen team_id ("" = skip).

    Extracted from the Phase-4 intake branch so the Roadmap card can reuse it —
    pure extraction, no behavior change: returns "" when no board is configured,
    the DB is missing, there are no profiles, or the user skips/cancels. Never
    raises (a picker failure just means no profile).
    """
    if not board_configured:
        return ""
    selected_profile_id = ""
    try:
        from yeaboi.team_profile import TeamProfileStore

        if not _ana_dbp.exists():
            return ""
        with TeamProfileStore(_ana_dbp) as _pp_store:
            _pp_profiles = _pp_store.list_profiles()
        if not _pp_profiles:
            return ""
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_profile_picker_screen

        _pp_sel = 0
        _pp_n = len(_pp_profiles) + 1  # profiles + Skip
        w, h = console.size
        _pp_panel = _build_profile_picker_screen(_pp_profiles, _pp_sel, width=w, height=h)
        live.update(_pp_panel)
        while True:
            pk = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(pk)
            if _clicked is not None:
                # The only button is "Select" — a hit confirms the highlighted list row.
                if button_click(console, _pp_panel, *_clicked, ["Select"]) is None:
                    continue  # click missed the button — ignore it
                pk = "enter"  # fall through to the existing Enter handling
            if pk in ("up", "scroll_up"):
                _pp_sel = (_pp_sel - 1) % _pp_n
            elif pk in ("down", "scroll_down"):
                _pp_sel = (_pp_sel + 1) % _pp_n
            elif pk == "enter":
                if _pp_sel < len(_pp_profiles):
                    selected_profile_id = _pp_profiles[_pp_sel].team_id
                    logger.info("Profile selected: %s", selected_profile_id)
                else:
                    logger.info("Profile picker: Skip selected")
                break
            elif pk in ("esc", "q"):
                break
            w, h = console.size
            _pp_panel = _build_profile_picker_screen(_pp_profiles, _pp_sel, width=w, height=h)
            live.update(_pp_panel)
    except Exception:
        logger.debug("Profile picker failed", exc_info=True)
    return selected_profile_id


def _load_planning_rows() -> list[ProjectSummary]:
    """Merged "Your projects" rows: planning projects + saved roadmaps, newest first.

    Saved roadmaps ride the project-list pipeline as ProjectSummary rows with
    kind="roadmap" (amber-tagged card, meta in `created`) — the merge is purely
    presentational; RoadmapStore stays the backing store. Roadmap-load failure
    degrades to projects-only.
    """
    from yeaboi.persistence import load_projects

    rows = load_projects()
    try:
        from yeaboi.roadmap.store import RoadmapStore

        with RoadmapStore(_ana_dbp) as store:
            roadmaps = store.list_roadmaps()
    except Exception:
        logger.warning("could not load saved roadmaps for the project list", exc_info=True)
        roadmaps = []
    for rm in roadmaps:
        n = int(rm.get("project_count") or 0)
        if rm.get("analyzed"):
            detail = f"{n} candidate project{'s' if n != 1 else ''} · analyzed {(rm.get('updated_at') or '')[:10]}"
        else:
            detail = "not analyzed yet"
        rows.append(
            ProjectSummary(
                name=rm.get("label") or rm.get("source_label") or "(unnamed roadmap)",
                kind="roadmap",
                roadmap_id=rm["id"],
                created=" · ".join(x for x in (rm.get("source_type", ""), detail) if x),
                updated_at=rm.get("updated_at") or rm.get("created_at") or "",
            )
        )
    rows.sort(key=lambda r: r.updated_at or "", reverse=True)
    return rows


def _run_roadmap_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    dry_run: bool = False,
    open_roadmap_id: int | None = None,
) -> tuple[str, str] | str | None:
    """Event loop for the Roadmap intake page (a Planning sub-page).

    Two views. "source" (home when creating a new roadmap): Up/Down choose where
    the roadmap lives (Confluence / Notion / local file), Select opens a
    line-input for the page URL / file path, then the analysis runs. While an
    analysis runs, a `busy` flag renders a spinner-only screen (the source
    options stay hidden). "results" shows the recommended projects: Up/Down move
    the project cursor, Plan This hands the selection back to the caller,
    Re-analyze re-runs on the saved source (updating the same roadmap row),
    Change Source returns to "source".

    Saved roadmaps live as amber-tagged cards inside the Planning "Your projects"
    list (see _load_planning_rows); pass open_roadmap_id to open one of them
    directly — the page loads the row and enters "results" (analyzing first if
    the row was never analyzed). Saved roadmaps live in the RoadmapStore
    `roadmaps` table (opened in short-lived blocks only).

    Returns:
      ("small_project"|"smart", description) — the user picked a project to plan.
      "done" — Back/Esc after a roadmap row exists; the caller should return to
               the project list (where the roadmap card now lives).
      None   — backed out of the source view before anything was saved (or the
               open_roadmap_id row is gone); the caller stays where it was.

    # See docs: "Roadmap Intake" — TUI page
    """
    from yeaboi.roadmap.engine import intake_mode_for, run_roadmap_analysis
    from yeaboi.roadmap.ingest import RoadmapSource, parse_confluence_locator, parse_notion_locator
    from yeaboi.roadmap.store import RoadmapStore
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_roadmap_screen

    logger.info("roadmap page opened (dry_run=%s)", dry_run)

    def _sources() -> list[tuple[str, str, str]]:
        """Source options with configured-status hints (still selectable when unset)."""
        from yeaboi.config import get_confluence_base_url, get_notion_token

        conf_hint = "Read a page by URL, ID, or title"
        if not get_confluence_base_url():
            conf_hint = "Not configured — set CONFLUENCE_* (or JIRA_*) in .env"
        notion_hint = "Read a page by URL or ID"
        if not get_notion_token():
            notion_hint = "Not configured — set NOTION_TOKEN in .env"
        return [
            ("confluence", "Confluence page", conf_hint),
            ("notion", "Notion page", notion_hint),
            ("local", "Local file (.md .txt .rst .pdf .docx .pptx)", "Read a roadmap document from disk"),
        ]

    state = {
        "view": "source",
        "current_roadmap_id": None,  # row being viewed/re-analyzed; None = creating new
        "selected": 0,  # source index
        "cursor": 0,  # project cursor (results view)
        "scroll_meta": {},
        "sel": 0,  # action button index (source/results views)
        "message": "",
        "analysis": None,
        "source": None,  # RoadmapSource once configured
        "busy": False,  # True while the analysis worker runs (spinner-only screen)
    }
    source_actions = ["Select", "Back"]
    results_actions = ["Plan This", "Re-analyze", "Change Source", "Share Online", "Anonymize", "Back"]
    # Anonymize state: None = real analysis; an AnonymizedOutput = mask it in place.
    # Roadmap has no Export button normally, so anonymizing adds one (to share the masked copy).
    anon = None
    anon_instruction = ""

    def _actions() -> list[str]:
        if state["view"] != "results":
            return source_actions
        acts = list(results_actions)
        if anon is not None:  # swap Anonymize → Adjust + Revert + Export while masked
            i = acts.index("Anonymize")
            acts[i : i + 1] = ["Adjust", "Revert", "Export"]
        return acts

    def _data() -> dict:
        analysis = state["analysis"]
        # When anonymized, render from a masked copy so the SAME project cards/summary
        # re-render with only the sensitive words swapped.
        if anon is not None and analysis is not None:
            from yeaboi.anonymize.apply import mask_artifact

            analysis = mask_artifact(analysis, anon.replacements)
        return {
            "view": state["view"],
            "sources": _sources(),
            "selected_idx": state["selected"],
            "analysis": analysis,
            "project_cursor": state["cursor"],
            "actions": _actions(),
            "message": state["message"],
            "busy": state["busy"],
            "source_label": getattr(analysis, "source_label", ""),
            "analyzed_at": (getattr(analysis, "generated_at", "") or "")[:10],
        }

    anim_start = time.monotonic()
    _last_panel = None  # most recently rendered roadmap panel, for click hit-testing

    def _render() -> None:
        nonlocal _last_panel
        w, h = console.size
        tick = time.monotonic() - anim_start
        _last_panel = _build_roadmap_screen(
            _data(),
            scroll_meta=state["scroll_meta"],
            width=w,
            height=max(10, h - 1),
            action_sel=state["sel"],
            shimmer_tick=tick,
            sub_reveal=tick * _HEADER_SUB_SPEED,
            anon_note=_anon_note(anon),
        )
        live.update(_last_panel)

    def _roadmap_document() -> tuple[str, str] | str:
        analysis = state["analysis"]
        if analysis is None:
            return "Analyze this roadmap first."
        from yeaboi.roadmap.export import build_roadmap_markdown

        return "Roadmap", build_roadmap_markdown(analysis)

    def _analyze(source) -> None:
        """Run the analysis on a worker thread while the frame loop animates progress.

        The ingest + LLM call can take ~30s; running it inline would freeze the
        Live display on one frame and make the TUI look hung (same reasoning as
        the standup-generate and retro-tunnel workers). The worker only writes
        into result_box/progress; all state/render updates stay on this thread.
        """

        progress: list[str] = ["Starting…"]
        result_box: list = [None]

        def _worker() -> None:
            try:
                result_box[0] = run_roadmap_analysis(
                    source, db_path=_ana_dbp, dry_run=dry_run, on_progress=progress.append
                )
            except Exception as e:  # never let an action crash the TUI
                result_box[0] = e

        thread = duck_working_thread(_worker, name="roadmap-analyze")
        thread.start()
        started = time.monotonic()
        state["busy"] = True  # loading screen replaces the source options underneath
        # The consistent loading screen (spinner + activity rows + elapsed) the
        # other modes use, in the roadmap accent — not a hand-rolled status line.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen
        from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

        while thread.is_alive():
            elapsed = time.monotonic() - started
            w, h = console.size
            live.update(
                _build_standup_progress_screen(
                    list(progress),
                    width=w,
                    height=max(10, h - 1),
                    elapsed=elapsed,
                    anim_tick=elapsed,
                    theme=PLANNING_THEME,  # roadmap is a Planning sub-page (same accent)
                    title=planning_title(elapsed),
                    label="Analyzing roadmap",
                )
            )
            time.sleep(1 / 30)
        thread.join()
        state["busy"] = False
        state["message"] = ""

        outcome = result_box[0]
        if isinstance(outcome, Exception) or outcome is None:
            logger.error("roadmap analyze failed: %s", outcome, exc_info=isinstance(outcome, Exception))
            state["message"] = f"Analyze failed: {outcome}" if outcome else "Analyze failed."
            return
        analysis = outcome
        try:
            with RoadmapStore(_ana_dbp) as store:
                # New roadmap (id None) inserts a row; Re-analyze/Change Source
                # update the row being viewed in place.
                state["current_roadmap_id"] = store.save_roadmap(
                    source, analysis, roadmap_id=state["current_roadmap_id"]
                )
        except Exception:  # remembering the roadmap is best-effort — still show results
            logger.error("roadmap analyze: failed to save roadmap", exc_info=True)
        state["analysis"] = analysis
        state["source"] = source
        state["view"] = "results"
        state["cursor"], state["sel"] = 0, 0
        n = len(analysis.projects)
        plural = "s" if n != 1 else ""
        state["message"] = f"{n} project{plural} recommended." if n else ""
        logger.info("roadmap analyze: %d project(s)", n)
        if n:
            _duck_react("roadmap_done", state["message"])

    def _enter_locator() -> None:
        """Ask for the selected source's locator, then analyze."""
        key = _sources()[state["selected"]][0]
        prompts = {
            "confluence": "Confluence page URL, ID, or title",
            "notion": "Notion page URL or ID",
            "local": "Roadmap file path (.md .txt .rst .pdf .docx .pptx)",
        }
        from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

        raw = _standup_read_line(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            prompt=prompts[key],
            step="Roadmap source",
            theme=PLANNING_THEME,
            title=planning_title(),
        )
        if raw is None or not raw.strip():
            state["message"] = ""
            return  # Esc / empty — stay on the source view
        raw = raw.strip()
        logger.info("roadmap source entered: type=%s", key)
        if key == "local":
            path = Path(raw).expanduser()
            if not path.exists() or not path.is_file():
                state["message"] = f"File not found: {path}"
                return
            # Sandbox pre-flight (main thread): consent before the engine reads
            # the file, so a denial is a status line, not an exception mid-run.
            from yeaboi.ui.shared._consent import _preflight_path_consent

            if not _preflight_path_consent(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                path,
                mode="read",
                context="Roadmap intake — local file",
            ):
                state["message"] = f"Access to {path} denied — allow it via Settings → Paths."
                return
            source = RoadmapSource(source_type="local", locator=str(path), label=path.name)
        elif key == "confluence":
            source = RoadmapSource(source_type="confluence", locator=parse_confluence_locator(raw), label=raw)
        else:
            source = RoadmapSource(source_type="notion", locator=parse_notion_locator(raw), label=raw)
        _analyze(source)

    def _plan_selected() -> tuple[str, str] | None:
        analysis = state["analysis"]
        projects = tuple(getattr(analysis, "projects", ()) or ())
        if not projects:
            state["message"] = "No projects to plan — Re-analyze or Change Source."
            return None
        project = projects[max(0, min(state["cursor"], len(projects) - 1))]
        description = project.description or project.name
        logger.info("roadmap: Plan This → %r (size=%s)", project.name, project.size)
        return (intake_mode_for(project), description)

    _stay = object()  # sentinel: _source_back handled the key, keep the page running

    def _source_back() -> object:
        """Back/Esc from the source view.

        Returns "done"/None to bubble out of the page, or _stay when the page
        should keep running (returned to the results view).
        """
        if state["analysis"] is not None:
            # The return leg of "Change Source" — the roadmap still has results.
            state["view"] = "results"
            state["sel"], state["message"] = 0, ""
            return _stay
        if state["current_roadmap_id"] is not None:
            # A saved (but unanalyzed / failed-analysis) roadmap — its card
            # lives in the project list, so hand control back there.
            logger.info("roadmap page closed (saved roadmap, no results)")
            return "done"
        logger.info("roadmap page closed before saving")
        return None

    # ── Entry: open a saved roadmap directly, or start at the source picker ──
    if open_roadmap_id is not None:
        try:
            with RoadmapStore(_ana_dbp) as store:
                row = store.get_roadmap(open_roadmap_id)
        except Exception:
            logger.warning("roadmap: failed to open id=%s", open_roadmap_id, exc_info=True)
            row = None
        if row is None:
            return None
        logger.info("roadmap: opened id=%s (analyzed=%s)", row["id"], row["analysis"] is not None)
        state["current_roadmap_id"] = row["id"]
        state["source"] = row["source"]
        if row["analysis"] is None:
            # Saved but never analyzed (e.g. seeded from a config-only v10 DB).
            # Analysis failure lands on the source view with the error message.
            _analyze(row["source"])
        else:
            state["analysis"] = row["analysis"]
            state["view"] = "results"

    _render()
    while True:
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        _clicked = parse_click(k)
        if _clicked is not None:
            if _last_panel is None:
                continue
            _idx = button_click(console, _last_panel, *_clicked, _actions())
            if _idx is None:
                continue  # click missed the buttons — ignore it
            state["sel"] = _idx
            k = "enter"  # fall through to the existing Enter handling
        if state["view"] == "source":
            n_sources = len(_sources())
            if k in ("up", "scroll_up"):
                state["selected"] = (state["selected"] - 1) % n_sources
            elif k in ("down", "scroll_down"):
                state["selected"] = (state["selected"] + 1) % n_sources
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(source_actions) - 1, state["sel"] + 1)
            elif k in ("enter", " "):
                label = source_actions[state["sel"]]
                if label == "Back":
                    result = _source_back()
                    if result is not _stay:
                        return result
                else:  # Select
                    _enter_locator()
            elif k in ("esc", "q"):
                result = _source_back()
                if result is not _stay:
                    return result
        else:  # results view
            projects = tuple(getattr(state["analysis"], "projects", ()) or ())
            if k in ("up", "scroll_up"):
                if projects:
                    state["cursor"] = (state["cursor"] - 1) % len(projects)
            elif k in ("down", "scroll_down"):
                if projects:
                    state["cursor"] = (state["cursor"] + 1) % len(projects)
            elif k == "left":
                state["sel"] = max(0, state["sel"] - 1)
            elif k == "right":
                state["sel"] = min(len(_actions()) - 1, state["sel"] + 1)
            elif k in ("enter", " "):
                label = _actions()[state["sel"]]
                if label == "Back":
                    logger.info("roadmap page closed from results view")
                    return "done"
                elif label == "Plan This":
                    result = _plan_selected()
                    if result is not None:
                        return result
                elif label == "Re-analyze":
                    anon, anon_instruction = None, ""  # new analysis → drop any stale mask
                    if state["source"] is not None:
                        _analyze(state["source"])
                    else:
                        state["view"] = "source"
                        state["sel"], state["message"] = 0, ""
                elif label == "Change Source":
                    anon, anon_instruction = None, ""
                    state["view"] = "source"
                    state["sel"], state["message"] = 0, ""
                elif label == "Share Online":
                    analysis = state.get("analysis")
                    if analysis is not None:
                        from yeaboi.sharing.documents import roadmap_document
                        from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

                        _run_output_share_flow(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            document=roadmap_document(analysis, anon=anon),
                            theme=PLANNING_THEME,
                            title_fn=planning_title,
                        )
                elif label == "Anonymize":
                    if state["analysis"] is None:
                        state["message"] = "Analyze this roadmap before anonymizing."
                    else:
                        logger.info("roadmap: Anonymize pressed")
                        from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

                        doc = _roadmap_document()
                        if isinstance(doc, str):
                            state["message"] = doc
                        else:
                            res = _run_anonymize_pass(
                                console,
                                live,
                                read_key,
                                frame_time,
                                supports_timeout,
                                markdown=doc[1],
                                instruction="",
                                project_name="roadmap",
                                source_mode="roadmap",
                                theme=PLANNING_THEME,
                                title=planning_title(),
                            )
                            if res is not None:
                                anon, anon_instruction = res, ""
                            else:
                                state["message"] = "Anonymize failed (see logs)."
                elif label == "Adjust":  # refine the mask with a free-text instruction
                    from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

                    adj = _standup_read_line(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        prompt="Also mask …  ·  don't mask … (it's public/safe)",
                        step="Anonymize — adjust what's masked",
                        default="",
                        theme=PLANNING_THEME,
                        title=planning_title(),
                        box_rows=6,
                    )
                    if adj is not None and adj.strip():
                        anon_instruction = f"{anon_instruction}\n{adj.strip()}".strip()
                        doc = _roadmap_document()
                        if not isinstance(doc, str):
                            res = _run_anonymize_pass(
                                console,
                                live,
                                read_key,
                                frame_time,
                                supports_timeout,
                                markdown=doc[1],
                                instruction=anon_instruction,
                                project_name="roadmap",
                                source_mode="roadmap",
                                theme=PLANNING_THEME,
                                title=planning_title(),
                            )
                            if res is not None:
                                anon = res
                elif label == "Revert":  # restore the real names (no LLM call)
                    anon, anon_instruction = None, ""
                elif label == "Export":  # only present while masked → export the masked copy
                    doc = _roadmap_document()
                    if isinstance(doc, str):
                        state["message"] = doc
                    elif anon is not None:
                        msg = _anon_export(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            anon=anon,
                            doc_title=doc[0],
                            markdown=doc[1],
                            project_name="roadmap",
                            source_mode="roadmap",
                        )
                        if msg is not None:
                            state["message"] = msg
            elif k in ("esc", "q"):
                logger.info("roadmap page closed from results view")
                return "done"
        _render()


def _resolve_retro_session() -> tuple[str, str, str, str]:
    """Resolve the retro's target session → (session_id, session_name, project_name, sprint_name).

    Like the standup page, the retro targets the most recently modified session.
    Returns empty strings when there is no session yet.
    """
    try:
        from yeaboi.sessions import SessionStore, make_display_name

        with SessionStore(_ana_dbp) as store:
            session_id = store.get_latest_session_id()
            if not session_id:
                return "", "", "", ""
            meta = store.get_session(session_id) or {}
            state = store.load_state(session_id) or {}
        session_name = make_display_name(meta) if meta else session_id
        project_name = state.get("project_name", "") or session_name
        # Sprint name is best-effort: the export/report titles degrade gracefully if blank.
        sprint_name = str(state.get("sprint_name", "") or "")
        return session_id, session_name, project_name, sprint_name
    except Exception:
        logger.warning("retro: failed to resolve latest session", exc_info=True)
        return "", "", "", ""


def _run_retro_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Event loop for the collaborative Retro board page.

    Starts a small loopback web server plus the Cloudflare tunnel that is the only
    way teammates reach it, so they can add cards from a browser; the board
    refreshes every frame as cards arrive — the existing frame-timed read_key loop
    IS the live-update mechanism, so no extra TUI-side thread is needed (the
    background threads are the HTTP server and the one-shot tunnel setup).
    Buttons: [Copy Invite, Copy Host Link, Generate Action Items, Export,
    Anonymize, Close], plus Retry Link if the tunnel failed. Up/Down scroll,
    Left/Right select, Enter activates. On exit the board is flushed to RetroStore
    and the server + tunnel torn down (in a finally, so Ctrl-C/exception still
    persists + stops them).

    # See docs: "Retro" — TUI page, browser collaboration
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_retro_screen

    anim_start = time.monotonic()  # shimmer title + typewriter subtitle clock
    _scroll_meta: dict = {}  # scroll geometry published by _build_retro_screen
    _last_panel = None  # most recently rendered retro panel, for click hit-testing

    def _render(data: dict, scroll: int, sel: int) -> None:
        nonlocal _last_panel
        w, h = console.size
        elapsed = time.monotonic() - anim_start
        # Leave a one-row safety margin (same reason as the standup page).
        _last_panel = _build_retro_screen(
            data,
            scroll_offset=scroll,
            scroll_meta=_scroll_meta,
            width=w,
            height=max(10, h - 1),
            action_sel=sel,
            shimmer_tick=elapsed,
            sub_reveal=elapsed * _HEADER_SUB_SPEED,
            anon_note=data.get("anon_note", ""),
        )
        live.update(_last_panel)

    session_id, session_name, project_name, sprint_name = _resolve_retro_session()
    if not session_id:
        logger.info("retro: no session available — showing notice")
        data = {
            "session_name": "",
            "display_code": "—",
            # No server, so no join block: `snapshot` suppresses it and leaves the
            # message alone on the page. Without it the screen would offer to
            # share a board that was never started.
            "snapshot": True,
            "message": "No project session yet — create one in Planning first, then start a retro.",
            "grids": {},
        }
        _render(data, 0, 2)
        while True:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                # Notice screen: the default button row is the only action — any hit exits.
                if (
                    _last_panel is not None
                    and button_click(console, _last_panel, *_clicked, ["Generate Action Items", "Export", "Close"])
                    is not None
                ):
                    break
                continue
            if k in ("enter", " ", "esc", "q"):
                break
            _render(data, 0, 2)
        return

    from yeaboi.config import get_retro_server_port
    from yeaboi.retro.board import RetroBoard, board_to_report
    from yeaboi.retro.engine import carried_action_items_for_session, history_providers
    from yeaboi.retro.server import RetroServer
    from yeaboi.retro.store import RetroStore

    board = RetroBoard(session_id, project_name=project_name, sprint_name=sprint_name)
    # Seed last sprint's action items for review before the server starts, so the
    # first browser poll already shows the "Last sprint's actions" column. Best-effort:
    # carried_action_items_for_session returns () when there's no prior retro.
    carried = carried_action_items_for_session(session_id, project_name=project_name, db_path=_ana_dbp)
    if carried:
        board.seed_carried(list(carried))
        logger.info("retro: seeded %d carried-over action item(s) (session=%s)", len(carried), session_id)
    server = RetroServer(board, port=get_retro_server_port())
    # Previous retros, for the board's back arrow. Read lazily, so a store that
    # cannot be opened costs a board with no history rather than a board.
    server.history_list, server.history_report = history_providers(project_name=project_name, db_path=_ana_dbp)
    try:
        server.start()
        logger.info("retro: server started on port %s (session=%s)", server.port, session_id)
    except OSError as e:
        logger.error("retro: failed to start server: %s", e, exc_info=True)
        data = {
            "session_name": session_name,
            "display_code": "—",
            "snapshot": True,  # no server → no join block (see above)
            "message": f"Could not start the retro server: {e}",
            "grids": {},
        }
        _render(data, 0, 2)
        while True:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                # Notice screen: the default button row is the only action — any hit exits.
                if (
                    _last_panel is not None
                    and button_click(console, _last_panel, *_clicked, ["Generate Action Items", "Export", "Close"])
                    is not None
                ):
                    break
                continue
            if k in ("enter", " ", "esc", "q"):
                break
            _render(data, 0, 2)
        return

    logger.info("retro: page opened for session=%s on %s", session_id, server.url.split("?")[0])
    scroll, sel = 0, 0
    # Empty on purpose: the status slot renders `_link_status_text() or message or
    # remote["status"]`, so an empty message lets the tunnel narrate its own
    # progress, and the first thing the host does takes the slot back — except for
    # a time-critical tunnel event (an expiry warning, or the expiry itself), which
    # always wins over whatever `message` last held (see `_link_status_text`).
    message = ""

    # Tunnel state. The server binds loopback, so the Cloudflare tunnel is the
    # ONLY way a teammate reaches this board — there is no link to show until it
    # is up, which is why setup starts by itself below rather than on a button.
    # Setup (binary download + tunnel handshake + DNS gate) is slow, so it runs on
    # a worker thread; the frame-timed loop shows its progress and fills in the
    # participant link the moment it lands.

    remote: dict = {"tunnel": None, "url": "", "status": "", "starting": False, "failed": False, "expired": False}

    def _start_remote() -> None:
        def _worker() -> None:
            try:
                from yeaboi.sharing.tunnel import ensure_cloudflared, open_tunnel

                remote["status"] = "Setting up the secure link — fetching cloudflared (first use, ~40MB)…"
                binary = ensure_cloudflared()
                if binary is None:
                    logger.warning("retro: secure link failed — could not obtain cloudflared binary")
                    remote["status"] = "Secure link failed — could not obtain cloudflared (see logs)."
                    remote["failed"] = True
                    return
                remote["status"] = "Starting secure Cloudflare tunnel (verifying it's reachable)…"

                def _on_expired() -> None:
                    # Runs on the tunnel's timer thread once TUNNEL_TIMEOUT_MINUTES
                    # elapses. Reuse the existing "Retry Link" affordance rather than
                    # inventing a new one — un-publish the URL server-side too, so
                    # /api/invite and the QR stop handing out a dead link.
                    remote["tunnel"] = None
                    remote["url"] = ""
                    server.set_public_url("")
                    # So the board's own invite panel stops saying "coming".
                    server.set_share_state("failed")
                    remote["status"] = (
                        "Secure link expired after the configured timeout — click Retry Link to reconnect."
                    )
                    remote["failed"] = True
                    remote["expired"] = True
                    logger.info("retro: secure link expired (session=%s)", session_id)

                # One call decides the tier. In the quick tier this is the same
                # CloudflareTunnel as before; in the Access tier it is a named
                # tunnel plus the identity gate the server verifies against, and
                # a refusal here NEVER falls back to a public quick tunnel — a
                # host who configured Access and silently got a trycloudflare.com
                # URL is worse off than one who got no share at all.
                transport = open_tunnel(server.port, surface="retro", binary=binary, on_expire=_on_expired)
                if transport.tunnel is None:
                    logger.warning("retro: secure link unavailable — %s", transport.error)
                    remote["status"] = f"Secure link unavailable — {transport.error}"
                    remote["failed"] = True
                    return
                # Armed before start(), so verification is on before the door is.
                server.set_access_gate(transport.gate)
                tunnel = transport.tunnel
                # Published BEFORE start(), which blocks for up to the 45 s
                # handshake budget (URL + edge registration + a possible
                # --region retry) plus the ~30 s DNS-propagation gate. The
                # page's finally stops whatever is in this slot — stop() also
                # cancels an in-flight start(); assigning after start() (as
                # this did) meant closing a board during setup — now a routine
                # path, since setup is no longer a deliberate button press —
                # orphaned a cloudflared child still forwarding to that port.
                remote["tunnel"] = tunnel
                public = tunnel.start(timeout=45)
                if not public:
                    tunnel.stop()
                    remote["tunnel"] = None
                    # The Access tier's failures are host *setup* errors — a
                    # credentials file that is not there, a tunnel id Cloudflare
                    # does not know — so name the one that happened rather than
                    # sending the host to their router.
                    detail = getattr(tunnel, "last_error", "")
                    logger.warning("retro: secure link failed — %s", detail or "tunnel did not start within timeout")
                    server.set_share_state("failed")
                    remote["status"] = (
                        f"Secure link failed — {detail}."
                        if detail
                        else "Secure link failed — tunnel did not start (see logs)."
                    )
                    remote["failed"] = True
                    return
                logger.info("retro: secure link ready (port=%s)", server.port)
                # Token-free public URL: teammates must still enter the join code
                # (the token is never handed out in a shareable link).
                remote["url"] = f"{public}/"
                # Tell the server its own public address, so /api/invite and the QR
                # hand out the tunnel URL rather than the loopback one the host's
                # own browser arrived on.
                server.set_public_url(remote["url"])
                # Clears a previous attempt's failure, so Retry Link puts the
                # board's own panel back to "ready" too.
                server.set_share_state("pending")
                remote["status"] = "Link ready — send it and the code to your team."
                _duck_react("link_ready")
            except Exception as e:  # never let the worker crash anything
                logger.error("retro: secure link setup failed: %s", e, exc_info=True)
                server.set_share_state("failed")
                remote["status"] = f"Secure link failed — {e}"
                remote["failed"] = True
            finally:
                remote["starting"] = False

        from yeaboi.config import tunnels_disabled

        if tunnels_disabled():
            # Opt-out for dry runs and offline/locked-down networks. The board
            # still works for the host on loopback; it just has nothing to share.
            logger.info("retro: tunnel disabled by YEABOI_NO_TUNNEL — board is host-only")
            server.set_share_state("off")
            remote["status"] = "Sharing is off (YEABOI_NO_TUNNEL) — this board is yours only."
            remote["starting"] = False
            remote["failed"] = False
            return

        logger.info("retro: starting secure link setup (session=%s)", session_id)
        remote["starting"] = True
        remote["failed"] = False
        remote["expired"] = False
        remote["status"] = "Setting up the secure link…"
        duck_working_thread(_worker, name="retro-tunnel-setup").start()

    # Start it now. The board is open and the join code is already valid; the link
    # is the one piece that takes a few seconds to exist.
    _maybe_offer_share_tier(console, live, read_key, frame_time, supports_timeout)
    _start_remote()

    # Anonymize state: None = live board; an AnonymizedOutput = mask card text/authors.
    anon = None
    anon_instruction = ""

    def _actions() -> list[str]:
        # Copy Invite leads, matching the Share Online screen. Copy Host Link is
        # the one extra button the live boards carry, because they are the only
        # screens that have a host link at all.
        base = [
            "Copy Invite",
            "Copy Host Link",
            "Generate Action Items",
            "Export",
            "Anonymize",
            "Close",
        ]
        if anon is not None:  # swap Anonymize → Adjust + Revert while masked
            i = base.index("Anonymize")
            base[i : i + 1] = ["Adjust", "Revert"]
        # Only offered when there is something to retry, and appended rather than
        # inserted: a worker thread flips `failed` between a frame being drawn and
        # a keypress being handled, so a button that appeared mid-row would slide
        # every later label one place under the user's cursor.
        if remote["failed"]:
            base.append("Retry Link")
        return base

    def _link_status_text() -> str:
        """Time-critical tunnel-health text, or "" when there is none.

        A retro can easily run 60-90 minutes, and a quick tunnel gets a fresh
        random hostname on every launch — once this one auto-expires, Retry
        Link hands out a *different* URL, so the invite already sent to
        everyone is permanently dead. Warn the host with time left to wrap
        up or re-share, rather than the link just vanishing mid-ceremony.

        Returned text must win over `message` in `_data()`, not just
        `remote["status"]` — `message` is a sticky action-result string (set
        by Copy Invite etc. and never cleared per frame) that would otherwise
        swallow this for the rest of the session the moment the host presses
        the one button they need to press to share the link at all.
        """
        if remote["expired"]:
            return remote["status"]
        tunnel = remote.get("tunnel")
        remaining = tunnel.time_until_expiry() if tunnel is not None else None
        if remaining is not None and remaining <= 300:
            mins = max(1, -(-int(remaining) // 60))  # ceil to whole minutes, min 1
            return f"Secure link expires in ~{mins} min — reconnecting will need a fresh invite."
        return ""

    def _data() -> dict:
        grids = board.cards_by_grid()
        # In-place mask: re-render the SAME cards with only text/author words swapped.
        if anon is not None:
            from dataclasses import replace as _replace

            from yeaboi.anonymize.apply import apply_replacements

            reps = anon.replacements
            grids = {
                g: [
                    _replace(
                        c,
                        text=apply_replacements(c.text, reps),
                        author=apply_replacements(c.author, reps),
                    )
                    for c in cards
                ]
                for g, cards in grids.items()
            }
        carried = board.carried_snapshot()
        if anon is not None:
            from dataclasses import replace as _replace

            from yeaboi.anonymize.apply import apply_replacements

            reps = anon.replacements
            carried = [_replace(c, text=apply_replacements(c.text, reps)) for c in carried]
        return {
            "session_name": session_name,
            "display_code": server.display_code,
            "host_url": server.url,
            "public_url": server.share_url,
            "link_failed": remote["failed"],
            # `_link_status_text()` wins first: it is only non-"" for a time-critical
            # tunnel event (the expiry warning, or the expiry itself), and both must
            # reach the host even mid-frame after a sticky `message`. Otherwise
            # `message` wins over the ambient `remote["status"]` — `message` is only
            # ever set by something the host just did, and reading status first (as
            # this did) silently swallowed every action result and error on the page.
            "message": _link_status_text() or message or remote["status"],
            "grids": grids,
            "carried": carried,
            "actions": _actions(),
            "anon_note": _anon_note(anon),
        }

    def _retro_document() -> tuple[str, str]:
        from yeaboi.retro.export import build_retro_markdown

        report = board_to_report(board, sprint_name=sprint_name)
        name = project_name or session_name
        return f"Retro — {name}" if name else "Retro", build_retro_markdown(report)

    try:
        _render(_data(), scroll, sel)
        while True:
            # The Retry Link button appears and disappears on a worker thread, so
            # the row can shrink under a cursor parked on its last entry. Clamp
            # every frame: without it an Enter aimed at a button that just left
            # would fall through to the out-of-range branch and close the board.
            sel = min(sel, len(_actions()) - 1)
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _last_panel is None:
                    continue
                _idx = button_click(console, _last_panel, *_clicked, _actions())
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            if k in SCROLL_KEYS:
                _ns = coalesce_scroll(scroll, k, _scroll_meta, read_key)
                if _ns == scroll:
                    continue
                scroll = _ns
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(len(_actions()) - 1, sel + 1)
            elif k in ("enter", " "):
                acts = _actions()
                label = acts[sel] if sel < len(acts) else "Close"
                if label == "Close":  # Close
                    break
                if label == "Generate Action Items":  # (one LLM call, never raises)
                    logger.info("retro: Generate Action Items pressed (session=%s)", session_id)
                    try:
                        from yeaboi.retro.engine import generate_action_items

                        # On a worker: the LLM call used to freeze the board
                        # (and the duck) solid — now the frame loop keeps going.
                        message = "Drafting action items…"
                        message = _run_on_worker(
                            lambda: generate_action_items(board),
                            lambda _e: _render(_data(), scroll, sel),
                            frame_time,
                            drain=read_key if supports_timeout else None,
                        )
                        logger.info("retro: generate action items result: %s", message)
                        # "never raises" means an empty board comes back as a
                        # message — no cards, no drafts, no celebratory quack.
                        if not message.startswith("Add some cards first"):
                            _duck_react("actions_done")
                    except Exception as e:  # defensive — never let it crash the TUI
                        logger.error("retro: generate action items failed: %s", e, exc_info=True)
                        message = f"Generate failed: {e}"
                    scroll = 0
                elif label == "Copy Invite":
                    from yeaboi.clipboard import copy_markdown_status
                    from yeaboi.sharing.access import invite_url

                    logger.info("retro: Copy Invite pressed (session=%s)", session_id)
                    # One link, code in the fragment — never the host link, which
                    # carries the admin secret and would make every reader a host.
                    invite = invite_url(server.share_url, server.display_code)
                    # Never put a half-invite on the clipboard. Before the tunnel
                    # lands there is no address that works for the reader, and a
                    # "Copied" that pasted the code alone would send the host into
                    # a chat window with nothing to click.
                    message = (
                        copy_markdown_status(invite)
                        if invite
                        else "The secure link is still starting — try again in a moment."
                    )
                    scroll = 0
                elif label == "Copy Host Link":
                    from yeaboi.clipboard import copy_markdown_status

                    logger.info("retro: Copy Host Link pressed (session=%s)", session_id)
                    message = copy_markdown_status(server.url)
                    scroll = 0
                elif label == "Retry Link":  # only present after a failed setup
                    if not remote["starting"]:
                        logger.info("retro: Retry Link pressed (session=%s)", session_id)
                        _start_remote()
                    # Hand the status slot back to the tunnel so its progress is
                    # what the host sees, and step off a button that is about to
                    # disappear from the end of the row.
                    message = ""
                    sel = 0
                    scroll = 0
                elif label == "Export":  # pick a destination (files / Notion / Confluence)
                    logger.info("retro: Export pressed (session=%s)", session_id)

                    def _retro_files() -> str:
                        try:
                            from yeaboi.retro.export import export_retro

                            report = board_to_report(board, sprint_name=sprint_name)
                            with RetroStore(_ana_dbp) as _store:
                                run_history = _store.get_history(session_id, limit=30)
                            paths = export_retro(report, project_name=project_name or session_name, history=run_history)
                            logger.info("retro: exported to %s", paths["markdown"].parent)
                            return f"Exported to {paths['markdown'].parent}  (Markdown + HTML)"
                        except Exception as e:
                            logger.error("retro: export failed: %s", e, exc_info=True)
                            return f"Export failed: {e}"

                    if anon is not None:  # export the masked copy, matching the screen
                        doc = _retro_document()
                        msg = _anon_export(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            anon=anon,
                            doc_title=doc[0],
                            markdown=doc[1],
                            project_name=project_name or session_name,
                            source_mode="retro",
                        )
                    else:
                        msg = _export_via_picker(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            mode="retro",
                            files_export=_retro_files,
                            get_document=_retro_document,
                        )
                    if msg is not None:
                        message = msg
                        scroll = 0
                elif label == "Anonymize":  # mask the board in place for public sharing
                    logger.info("retro: Anonymize pressed (session=%s)", session_id)
                    from yeaboi.ui.shared._components import RETRO_THEME, retro_title

                    res = _run_anonymize_pass(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        markdown=_retro_document()[1],
                        instruction="",
                        project_name=project_name or session_name,
                        source_mode="retro",
                        theme=RETRO_THEME,
                        title=retro_title(),
                    )
                    if res is not None:
                        anon, anon_instruction = res, ""
                    else:
                        message = "Anonymize failed (see logs)."
                    scroll = 0
                elif label == "Adjust":  # refine the mask with a free-text instruction
                    from yeaboi.ui.shared._components import RETRO_THEME, retro_title

                    adj = _standup_read_line(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        prompt="Also mask …  ·  don't mask … (it's public/safe)",
                        step="Anonymize — adjust what's masked",
                        default="",
                        theme=RETRO_THEME,
                        title=retro_title(),
                        box_rows=6,
                    )
                    if adj is not None and adj.strip():
                        anon_instruction = f"{anon_instruction}\n{adj.strip()}".strip()
                        res = _run_anonymize_pass(
                            console,
                            live,
                            read_key,
                            frame_time,
                            supports_timeout,
                            markdown=_retro_document()[1],
                            instruction=anon_instruction,
                            project_name=project_name or session_name,
                            source_mode="retro",
                            theme=RETRO_THEME,
                            title=retro_title(),
                        )
                        if res is not None:
                            anon = res
                elif label == "Revert":  # restore the real names (no LLM call)
                    anon, anon_instruction = None, ""
                sel = min(sel, len(_actions()) - 1)  # actions may have shrunk (Revert)
            elif k in ("esc", "q"):
                break
            _render(_data(), scroll, sel)
    finally:
        # Always flush the board, stop the tunnel, and tear the server down — even
        # on exception or Ctrl-C — so the retro persists and no process leaks.
        try:
            report = board_to_report(board, sprint_name=sprint_name)
            with RetroStore(_ana_dbp) as store:
                store.record_run(report)
        except Exception as e:
            logger.warning("retro: flush to store failed: %s", e)
        if remote.get("tunnel") is not None:
            remote["tunnel"].stop()
        server.stop()
        logger.info("retro: page closed for session=%s", session_id)


def _play_duck_shades(console, live, selected, *, tip_offset, start_time, select_time) -> None:
    """Play the click-the-duck gag: his sunglasses lift to reveal a second pair
    underneath, then drop back. Re-renders the whole welcome screen per lift stage
    (the rest of it keeps its current animation state) so only the duck changes."""
    from yeaboi.ui.shared._mascot import SHADES_LIFT_SEQUENCE

    for lift in SHADES_LIFT_SEQUENCE:
        w, h = console.size
        tick = time.monotonic() - start_time
        reveal = (time.monotonic() - select_time) * _DESC_SCROLL_SPEED
        live.update(
            _build_mode_screen(
                selected,
                width=w,
                height=h,
                shimmer_tick=tick,
                desc_reveal=reveal,
                tip_offset=tip_offset,
                duck_lift=lift,
            )
        )
        time.sleep(_FRAME_TIME * 3)  # ~20fps — a readable lift/drop over ~0.5s


# How long the success screen counts down before relaunching. Long enough to read
# "updated to vX" and hit esc, short enough that the restart still feels automatic.
_UPDATE_RESTART_SECONDS = 3.0

# Upper bound on the keystrokes drained after the upgrade finishes. Bounded so a
# held-down key (auto-repeat keeps refilling the buffer) can't spin here forever;
# 64 is far more than an impatient user types during a 30s upgrade.
_UPDATE_DRAIN_LIMIT = 64


def _run_update_flow(console, live, read_key, frame_time, supports_timeout) -> bool:
    """Run the in-app upgrade (the ctrl+U shortcut): show a spinner while the
    detected ``uv/pipx upgrade`` command runs on a worker thread, then the result.

    Only invoked when an update is available (the caller gates on it). The upgrade
    runs in a subprocess, so the freshly-installed code only takes effect in a NEW
    process: on success this counts down :data:`_UPDATE_RESTART_SECONDS` and then
    returns True, meaning "unwind the TUI so cli.main can relaunch us". Esc (or q)
    during the countdown declines, and a failure — or an install we can't work out
    how to relaunch — falls back to the old dismiss-with-any-key result.

    Returns True when the caller should unwind for a restart, False to stay put.
    """

    from yeaboi import update_check
    from yeaboi.ui.shared._screensaver import suppress_screensaver

    status = update_check.get_update_status()
    latest = status.get("latest", "")
    command = status.get("upgrade_command", "")
    logger.info("update: ctrl+U upgrade to v%s via '%s'", latest, command)

    result: dict = {}

    def _worker() -> None:
        result["ok"], result["detail"] = update_check.run_upgrade()

    spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    thread = duck_working_thread(_worker, name="app-update")
    # Exclude the (potentially slow) network upgrade from idle tracking so the
    # screensaver doesn't take over mid-update.
    with suppress_screensaver():
        thread.start()
        i = 0
        while thread.is_alive():
            w, h = console.size
            live.update(_build_update_screen(w, h, latest=latest, command=command, spinner=spin[i % len(spin)]))
            i += 1
            time.sleep(max(0.05, frame_time * 4))
        thread.join(timeout=0.1)

    ok = bool(result.get("ok"))
    detail = result.get("detail", "") or ""
    logger.info("update: upgrade %s", "succeeded" if ok else "failed")
    # The spinner loop above never reads keys, so anything typed during the (often
    # 5-30s) upgrade is still queued on the tty. Left there, the first read below
    # would return a stale keystroke and fire the restart instantly — the user would
    # never see which version landed, nor get the esc window this screen is for.
    if supports_timeout:
        for _ in range(_UPDATE_DRAIN_LIMIT):
            if not read_key(timeout=0):
                break
    # Only offer the restart when we can actually perform one — an install whose
    # console script we can't resolve gets the honest "restart it yourself" screen.
    can_restart = ok and update_check.resolve_relaunch_command() is not None
    deadline = time.monotonic() + _UPDATE_RESTART_SECONDS
    while True:
        w, h = console.size
        # Clamp to 1: the loop exits the moment the deadline passes, so a rendered
        # "restarting in 0…" would only ever be a stray final frame.
        remaining = max(1, math.ceil(deadline - time.monotonic())) if can_restart else None
        live.update(
            _build_update_screen(
                w,
                h,
                latest=latest,
                command=command,
                done=True,
                ok=ok,
                detail=detail,
                restart_in=remaining,
                can_restart=can_restart,
            )
        )
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        if not can_restart:
            if k:
                return False
            continue
        if k in ("esc", "q"):
            logger.info("update: restart declined, staying on the running version")
            return False
        # Mouse traffic isn't an answer to this screen — a stray wheel nudge must
        # not cut the window the user has to read the version and hit esc.
        if k in ("scroll_up", "scroll_down") or (isinstance(k, str) and k.startswith("click:")):
            k = ""
        # Any other key restarts immediately — no need to sit through the countdown.
        if k or time.monotonic() >= deadline:
            update_check.request_restart(latest)
            return True


def _run_poker_setup(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> dict | None:
    """Poker setup wizard: source → scope → sprint → ticket types → fetch.

    Returns ``{"source", "scope_label", "tickets"}`` ready for the live page, or
    None when the user backs out. Each step is a view of ``_build_poker_screen``
    (the dict-driven picker convention); the ticket fetch runs on a worker
    thread behind the shared progress screen because it makes tracker network
    calls that can take seconds.
    """
    from yeaboi.poker import tickets as poker_tickets
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_poker_screen

    anim_start = time.monotonic()

    def _pick(title: str, hint: str, options: list[tuple[str, str]], preselect: int = 0) -> int | None:
        """Arrow-key option picker. Returns the chosen index, or None on back/esc."""
        sel = max(0, min(preselect, len(options) - 1))
        action_sel = 0
        actions = ["Select", "Back"]
        _last_panel = None  # most recently rendered picker panel, for click hit-testing

        def _render() -> None:
            nonlocal _last_panel
            w, h = console.size
            elapsed = time.monotonic() - anim_start
            _last_panel = _build_poker_screen(
                {
                    "subtitle": "Set up a poker session",
                    "pick": {"title": title, "hint": hint, "options": options, "sel": sel},
                    "actions": actions,
                },
                width=w,
                height=max(10, h - 1),
                action_sel=action_sel,
                shimmer_tick=elapsed,
                sub_reveal=elapsed * _HEADER_SUB_SPEED,
            )
            live.update(_last_panel)

        _render()
        while True:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _last_panel is None:
                    continue
                _idx = button_click(console, _last_panel, *_clicked, actions)
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                action_sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            if k == "up":
                sel = max(0, sel - 1)
            elif k == "down":
                sel = min(len(options) - 1, sel + 1)
            elif k == "left":
                action_sel = max(0, action_sel - 1)
            elif k == "right":
                action_sel = min(len(actions) - 1, action_sel + 1)
            elif k in ("enter", " "):
                if actions[action_sel] == "Back":
                    return None
                return sel
            elif k in ("esc", "q"):
                return None
            _render()

    def _toggle(title: str, hint: str, options: list[tuple[str, str]], checked: set[int]) -> set[int] | None:
        """Multi-select toggle on the same pick view (space flips, enter continues).

        The pick view has no checkbox concept — the [✓]/[ ] state is baked
        into the option labels on every render, the same trick the standup
        source multi-select uses. Returns the checked set, or None on back.
        """
        sel = 0
        action_sel = 0
        actions = ["Continue", "Back"]
        warn = ""
        _last_panel = None  # most recently rendered toggle panel, for click hit-testing

        def _render() -> None:
            nonlocal _last_panel
            w, h = console.size
            elapsed = time.monotonic() - anim_start
            opts = [(("[✓] " if i in checked else "[ ] ") + label, sub) for i, (label, sub) in enumerate(options)]
            _last_panel = _build_poker_screen(
                {
                    "subtitle": "Set up a poker session",
                    "pick": {"title": title, "hint": warn or hint, "options": opts, "sel": sel},
                    "actions": actions,
                },
                width=w,
                height=max(10, h - 1),
                action_sel=action_sel,
                shimmer_tick=elapsed,
                sub_reveal=elapsed * _HEADER_SUB_SPEED,
            )
            live.update(_last_panel)

        _render()
        while True:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _last_panel is None:
                    continue
                _idx = button_click(console, _last_panel, *_clicked, actions)
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                action_sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            if k == "up":
                sel = max(0, sel - 1)
            elif k == "down":
                sel = min(len(options) - 1, sel + 1)
            elif k == "left":
                action_sel = max(0, action_sel - 1)
            elif k == "right":
                action_sel = min(len(actions) - 1, action_sel + 1)
            elif k == " ":
                checked ^= {sel}
                warn = ""
            elif k == "enter":
                if actions[action_sel] == "Back":
                    return None
                if not checked:
                    warn = "Select at least one type."
                else:
                    return checked
            elif k in ("esc", "q"):
                return None
            _render()

    # ── Step 1: source (Jira / Azure DevOps / Demo) ───────────────────────
    sources = poker_tickets.available_sources()
    options = [(poker_tickets.source_label(s), "") for s in sources]
    # Demo is always offered last — a no-tracker playground (write-back is a no-op).
    options.append(("Demo tickets", "no tracker needed — try the flow with sample tickets"))
    hint = (
        "Both boards are configured — pick which one this session estimates against."
        if len(sources) > 1
        else ("No tracker configured — add Jira or Azure DevOps credentials in Settings." if not sources else "")
    )
    idx = _pick("Where do the tickets come from?", hint, options)
    if idx is None:
        return None
    source = sources[idx] if idx < len(sources) else poker_tickets.SOURCE_DEMO
    logger.info("poker setup: source=%s", source)

    sprint = None
    scope_label = "Demo"
    if source != poker_tickets.SOURCE_DEMO:
        # ── Step 2: scope (a sprint or the backlog) ───────────────────────
        idx = _pick(
            "Which tickets should the team estimate?",
            "",
            [("A sprint", "pick one from the board's sprint list"), ("The backlog", "open items not in any sprint")],
        )
        if idx is None:
            return None
        if idx == 0:
            # ── Step 3: sprint list ───────────────────────────────────────
            sprints = poker_tickets.list_sprints(source)
            if not sprints:
                logger.warning("poker setup: no sprints found for %s", source)
                _pick(
                    "No sprints found",
                    "Check the board's credentials/logs, or estimate the backlog instead.",
                    [("Back", "")],
                )
                return None
            sprint_opts = [
                (
                    s.get("name", "?"),
                    " · ".join(p for p in (s.get("start_date"), s.get("end_date"), s.get("state")) if p),
                )
                for s in sprints
            ]
            active = next((i for i, s in enumerate(sprints) if s.get("state") == "active"), len(sprints) - 1)
            idx = _pick("Which sprint?", "", sprint_opts, preselect=active)
            if idx is None:
                return None
            sprint = sprints[idx]
            scope_label = sprint.get("name", "Sprint")
        else:
            scope_label = "Backlog"
    logger.info("poker setup: scope=%s", scope_label)

    # ── Step 4: ticket types (multi-toggle; demo skips) ───────────────────
    include_types: tuple[str, ...] | None = None
    if source != poker_tickets.SOURCE_DEMO:
        if source == poker_tickets.SOURCE_JIRA:
            sublabels = {"story": "issuetype Story", "bug": "issuetype Bug", "task": "issuetype Task"}
            type_hint = "Space toggles · Sub-tasks are never included."
        else:
            sublabels = {
                "story": "User Story / Product Backlog Item",
                "bug": "Bug",
                "task": "child tasks — usually not estimated",
            }
            type_hint = "Space toggles · pick the work-item types to estimate."
        type_defaults = poker_tickets.default_include_types(source)
        checked = _toggle(
            "Which ticket types should be estimated?",
            type_hint,
            [(poker_tickets.TICKET_TYPE_LABELS[t], sublabels[t]) for t in poker_tickets.TICKET_TYPES],
            {i for i, t in enumerate(poker_tickets.TICKET_TYPES) if t in type_defaults},
        )
        if checked is None:
            return None
        include_types = tuple(t for i, t in enumerate(poker_tickets.TICKET_TYPES) if i in checked)
        logger.info("poker setup: include_types=%s", ",".join(include_types))

    # ── Step 5: fetch tickets (worker thread + progress screen) ───────────

    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen
    from yeaboi.ui.shared._components import POKER_THEME, poker_title

    result: dict = {}
    progress = [f"Fetching tickets from {poker_tickets.source_label(source)} ({scope_label})"]

    def _fetch() -> None:
        result["tickets"] = poker_tickets.fetch_tickets(source, sprint=sprint, include_types=include_types)

    worker = duck_working_thread(_fetch, name="poker-fetch")
    started = time.monotonic()
    worker.start()
    while worker.is_alive():
        w, h = console.size
        elapsed = time.monotonic() - started
        live.update(
            _build_standup_progress_screen(
                progress,
                width=w,
                height=max(10, h - 1),
                elapsed=elapsed,
                anim_tick=elapsed,
                theme=POKER_THEME,
                title=poker_title(),
                label="Fetching tickets",
            )
        )
        read_key(timeout=frame_time) if supports_timeout else time.sleep(frame_time)
    tickets = result.get("tickets") or []
    if not tickets:
        logger.warning("poker setup: no tickets fetched (source=%s scope=%s)", source, scope_label)
        _pick(
            "No tickets found",
            f"{poker_tickets.source_label(source)} returned nothing for {scope_label} — check credentials (see logs).",
            [("Back", "")],
        )
        return None
    logger.info("poker setup: fetched %d ticket(s)", len(tickets))
    return {"source": source, "scope_label": scope_label, "tickets": tickets}


def _run_poker_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Event loop for the collaborative Scrum Poker page.

    Runs the setup wizard (source → sprint/backlog → fetch), then starts the
    loopback web server and the Cloudflare tunnel that is the only way teammates
    reach it. Like retro, the TUI is a monitoring view refreshed every frame; the
    host DRIVES the session (reveal / finalize / edit / AI) from their own browser
    via the private admin link — duplicating those controls in the terminal would
    double the admin surface. Buttons: [Copy Invite, Copy Host Link, Export,
    Close], plus Retry Link if the tunnel failed. On exit the session is flushed
    to PokerStore and the server + tunnel torn down (in a finally, so Ctrl-C still
    persists).

    # See docs: "Poker" — TUI page, browser collaboration
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_poker_screen

    setup = _run_poker_setup(console, live, read_key, frame_time, supports_timeout)
    if setup is None:
        return

    anim_start = time.monotonic()
    _scroll_meta: dict = {}
    _last_panel = None  # most recently rendered poker panel, for click hit-testing

    def _render(data: dict, scroll: int, sel: int) -> None:
        nonlocal _last_panel
        w, h = console.size
        elapsed = time.monotonic() - anim_start
        _last_panel = _build_poker_screen(
            data,
            scroll_offset=scroll,
            scroll_meta=_scroll_meta,
            width=w,
            height=max(10, h - 1),
            action_sel=sel,
            shimmer_tick=elapsed,
            sub_reveal=elapsed * _HEADER_SUB_SPEED,
        )
        live.update(_last_panel)

    # A poker session doesn't need a planning session to exist — fall back to a
    # stable quick-session id so history still records and groups sensibly.
    session_id, session_name, project_name, _sprint = _resolve_retro_session()
    if not session_id:
        session_id, session_name, project_name = "quick-poker", "", ""

    from yeaboi.config import get_poker_server_port
    from yeaboi.poker.board import PokerBoard, board_to_report
    from yeaboi.poker.server import PokerServer
    from yeaboi.poker.store import PokerStore

    board = PokerBoard(
        session_id,
        project_name=project_name,
        source=setup["source"],
        scope_label=setup["scope_label"],
        tickets=setup["tickets"],
    )
    server = PokerServer(board, port=get_poker_server_port())
    try:
        server.start()
        logger.info("poker: server started on port %s (session=%s)", server.port, session_id)
    except OSError as e:
        logger.error("poker: failed to start server: %s", e, exc_info=True)
        data = {
            "session_name": session_name,
            "message": f"Could not start the poker server: {e}",
            "state": board.state_snapshot(),
            # No server, so no join block — offering to share a board that never
            # started would print a code that resolves to nothing.
            "snapshot": True,
            "actions": ["Close"],
        }
        _render(data, 0, 0)
        while True:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                # Notice screen: the only button is "Close" — any hit exits.
                if _last_panel is not None and button_click(console, _last_panel, *_clicked, ["Close"]) is not None:
                    return
                continue
            if k in ("enter", " ", "esc", "q"):
                return
            _render(data, 0, 0)

    logger.info("poker: page opened for session=%s on %s", session_id, server.url.split("?")[0])
    scroll, sel = 0, 0
    # Empty on purpose — see the retro loop: the status slot renders
    # `_link_status_text() or message or remote["status"]`, so the tunnel narrates
    # until the host acts, except a time-critical tunnel event which always wins.
    message = ""

    # Tunnel state — see the retro loop for the full note. The short version: the
    # server binds loopback, so this tunnel is the only way a teammate reaches the
    # board, and it therefore starts by itself rather than on a button.

    remote: dict = {"tunnel": None, "url": "", "status": "", "starting": False, "failed": False, "expired": False}

    def _start_remote() -> None:
        def _worker() -> None:
            try:
                from yeaboi.sharing.tunnel import ensure_cloudflared, open_tunnel

                remote["status"] = "Setting up the secure link — fetching cloudflared (first use, ~40MB)…"
                binary = ensure_cloudflared()
                if binary is None:
                    logger.warning("poker: secure link failed — could not obtain cloudflared binary")
                    remote["status"] = "Secure link failed — could not obtain cloudflared (see logs)."
                    remote["failed"] = True
                    return
                remote["status"] = "Starting secure Cloudflare tunnel (verifying it's reachable)…"

                def _on_expired() -> None:
                    # See the retro loop's _on_expired for the full note: reuses the
                    # existing "Retry Link" affordance and un-publishes server-side.
                    remote["tunnel"] = None
                    remote["url"] = ""
                    server.set_public_url("")
                    remote["status"] = (
                        "Secure link expired after the configured timeout — click Retry Link to reconnect."
                    )
                    remote["failed"] = True
                    remote["expired"] = True
                    logger.info("poker: secure link expired (session=%s)", session_id)

                # One call decides the tier. In the quick tier this is the same
                # CloudflareTunnel as before; in the Access tier it is a named
                # tunnel plus the identity gate the server verifies against, and
                # a refusal here NEVER falls back to a public quick tunnel — a
                # host who configured Access and silently got a trycloudflare.com
                # URL is worse off than one who got no share at all.
                transport = open_tunnel(server.port, surface="poker", binary=binary, on_expire=_on_expired)
                if transport.tunnel is None:
                    logger.warning("poker: secure link unavailable — %s", transport.error)
                    remote["status"] = f"Secure link unavailable — {transport.error}"
                    remote["failed"] = True
                    return
                # Armed before start(), so verification is on before the door is.
                server.set_access_gate(transport.gate)
                tunnel = transport.tunnel
                # Published BEFORE start() so the page's finally can stop it —
                # see the retro loop for the orphaned-cloudflared note.
                remote["tunnel"] = tunnel
                public = tunnel.start(timeout=45)
                if not public:
                    tunnel.stop()
                    remote["tunnel"] = None
                    # The Access tier's failures are host *setup* errors — a
                    # credentials file that is not there, a tunnel id Cloudflare
                    # does not know — so it names the one that happened rather
                    # than sending the host to their router.
                    detail = getattr(tunnel, "last_error", "")
                    logger.warning("poker: secure link failed — %s", detail or "tunnel did not start within timeout")
                    remote["status"] = (
                        f"Secure link failed — {detail}."
                        if detail
                        else "Secure link failed — tunnel did not start (see logs)."
                    )
                    remote["failed"] = True
                    return
                logger.info("poker: secure link ready (port=%s)", server.port)
                remote["url"] = f"{public}/"
                # So /api/invite and the QR hand out the tunnel URL rather than the
                # loopback address the host's own browser arrived on.
                server.set_public_url(remote["url"])
                remote["status"] = "Link ready — send it and the code to your team."
                _duck_react("link_ready")
            except Exception as e:
                logger.error("poker: secure link setup failed: %s", e, exc_info=True)
                remote["status"] = f"Secure link failed — {e}"
                remote["failed"] = True
            finally:
                remote["starting"] = False

        from yeaboi.config import tunnels_disabled

        if tunnels_disabled():
            # Opt-out for dry runs and offline/locked-down networks. The board
            # still works for the host on loopback; it just has nothing to share.
            logger.info("poker: tunnel disabled by YEABOI_NO_TUNNEL — board is host-only")
            remote["status"] = "Sharing is off (YEABOI_NO_TUNNEL) — this board is yours only."
            remote["starting"] = False
            remote["failed"] = False
            return

        logger.info("poker: starting secure link setup (session=%s)", session_id)
        remote["starting"] = True
        remote["failed"] = False
        remote["expired"] = False
        remote["status"] = "Setting up the secure link…"
        duck_working_thread(_worker, name="poker-tunnel-setup").start()

    # Start it now — the board is open and the join code is already valid.
    _maybe_offer_share_tier(console, live, read_key, frame_time, supports_timeout)
    _start_remote()

    def _actions() -> list[str]:
        # Same leading pair as the retro board and the Share Online screen.
        base = ["Copy Invite", "Copy Host Link", "Export", "Close"]
        # The duel records on *this* machine. Opening the floor is an admin
        # route, but the admin secret rides in the host link's query string —
        # which reaches Cloudflare's edge log — and it is static for the life of
        # this screen. So the mic answers to a local control as well: nothing a
        # remote request can send turns it on by itself. Label carries the state
        # because a microphone's status must never be something you have to go
        # looking for.
        base.insert(2, f"Duel Mic: {'ON' if server.duel_mic_armed else 'off'}")
        # Appended, not inserted — a worker thread flips `failed` between a frame
        # being drawn and a keypress being handled (see the retro loop).
        if remote["failed"]:
            base.append("Retry Link")
        return base

    def _link_status_text() -> str:
        # See the retro loop's _link_status_text for the full note: a quick
        # tunnel gets a fresh random hostname on every launch, so once this
        # one auto-expires the invite already sent to the table is
        # permanently dead. Returns "" when there's nothing urgent — must win
        # over the sticky `message` in `_data()`, not just `remote["status"]`.
        if remote["expired"]:
            return remote["status"]
        tunnel = remote.get("tunnel")
        remaining = tunnel.time_until_expiry() if tunnel is not None else None
        if remaining is not None and remaining <= 300:
            mins = max(1, -(-int(remaining) // 60))  # ceil to whole minutes, min 1
            return f"Secure link expires in ~{mins} min — reconnecting will need a fresh invite."
        return ""

    def _data() -> dict:
        return {
            "session_name": session_name or setup["scope_label"],
            "display_code": server.display_code,
            "host_url": server.url,
            "public_url": server.share_url,
            "link_failed": remote["failed"],
            # `_link_status_text()` wins first — see the retro loop for why.
            "message": _link_status_text() or message or remote["status"],
            "state": board.state_snapshot(),
            "actions": _actions(),
        }

    def _poker_document() -> tuple[str, str]:
        from yeaboi.poker.export import build_poker_markdown

        report = board_to_report(board)
        name = project_name or session_name or setup["scope_label"]
        return (f"Poker — {name}" if name else "Poker", build_poker_markdown(report))

    try:
        _render(_data(), scroll, sel)
        while True:
            # The Retry Link button appears and disappears on a worker thread, so
            # the row can shrink under a cursor parked on its last entry. Clamp
            # every frame: without it an Enter aimed at a button that just left
            # would fall through to the out-of-range branch and close the board.
            sel = min(sel, len(_actions()) - 1)
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
            _clicked = parse_click(k)
            if _clicked is not None:
                if _last_panel is None:
                    continue
                _idx = button_click(console, _last_panel, *_clicked, _actions())
                if _idx is None:
                    continue  # click missed the buttons — ignore it
                sel = _idx
                k = "enter"  # fall through to the existing Enter handling
            if k in SCROLL_KEYS:
                _ns = coalesce_scroll(scroll, k, _scroll_meta, read_key)
                if _ns == scroll:
                    continue
                scroll = _ns
            elif k == "left":
                sel = max(0, sel - 1)
            elif k == "right":
                sel = min(len(_actions()) - 1, sel + 1)
            elif k in ("enter", " "):
                acts = _actions()
                label = acts[sel] if sel < len(acts) else "Close"
                if label == "Close":
                    break
                if label.startswith("Duel Mic:"):
                    armed = not server.duel_mic_armed
                    server.set_duel_mic_armed(armed)
                    logger.info(
                        "poker: duel mic %s by the host (session=%s)", "armed" if armed else "disarmed", session_id
                    )
                    message = (
                        "Duel mic armed — a duel may now record on this machine."
                        if armed
                        else "Duel mic disarmed — a duel cannot open this machine's microphone."
                    )
                    scroll = 0
                elif label == "Copy Invite":
                    from yeaboi.clipboard import copy_markdown_status
                    from yeaboi.sharing.access import invite_url

                    logger.info("poker: Copy Invite pressed (session=%s)", session_id)
                    # One link, code in the fragment — never the host link, which
                    # holds the admin secret (reveal, save, edit, AI).
                    invite = invite_url(server.share_url, server.display_code)
                    # Never put a half-invite on the clipboard — see the retro loop.
                    message = (
                        copy_markdown_status(invite)
                        if invite
                        else "The secure link is still starting — try again in a moment."
                    )
                    scroll = 0
                elif label == "Copy Host Link":
                    from yeaboi.clipboard import copy_markdown_status

                    logger.info("poker: Copy Host Link pressed (session=%s)", session_id)
                    message = copy_markdown_status(server.url)
                    scroll = 0
                elif label == "Retry Link":  # only present after a failed setup
                    if not remote["starting"]:
                        logger.info("poker: Retry Link pressed (session=%s)", session_id)
                        _start_remote()
                    # Hand the status slot back to the tunnel, and step off a
                    # button that is about to leave the end of the row.
                    message = ""
                    sel = 0
                    scroll = 0
                elif label == "Export":
                    logger.info("poker: Export pressed (session=%s)", session_id)

                    def _poker_files() -> str:
                        try:
                            from yeaboi.poker.export import export_poker

                            report = board_to_report(board)
                            with PokerStore(_ana_dbp) as _store:
                                run_history = _store.get_history(session_id, limit=30)
                            paths = export_poker(report, project_name=project_name or session_name, history=run_history)
                            logger.info("poker: exported to %s", paths["markdown"].parent)
                            return f"Exported to {paths['markdown'].parent}  (Markdown + HTML)"
                        except Exception as e:
                            logger.error("poker: export failed: %s", e, exc_info=True)
                            return f"Export failed: {e}"

                    msg = _export_via_picker(
                        console,
                        live,
                        read_key,
                        frame_time,
                        supports_timeout,
                        mode="poker",
                        files_export=_poker_files,
                        get_document=_poker_document,
                    )
                    if msg is not None:
                        message = msg
                        scroll = 0
            elif k in ("esc", "q"):
                break
            _render(_data(), scroll, sel)
    finally:
        # Always flush the session and tear the server down — even on exception
        # or Ctrl-C — so the estimates' record persists and no process leaks.
        try:
            report = board_to_report(board)
            with PokerStore(_ana_dbp) as store:
                store.record_run(report)
            if any(t.estimated for t in report.tickets):
                _duck_react("poker_done")  # lands on the hub the page returns to
        except Exception as e:
            logger.warning("poker: flush to store failed: %s", e)
        if remote.get("tunnel") is not None:
            remote["tunnel"].stop()
        server.stop()
        logger.info("poker: page closed for session=%s", session_id)


def _run_poker_hub(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Poker saved-runs hub → landing for the Poker card.

    Opening a saved session renders the recorded report as a read-only snapshot;
    "+ New session" runs the setup wizard + live page.
    """
    from yeaboi.persistence import _relative_time
    from yeaboi.poker.export import _title, build_poker_markdown, export_poker
    from yeaboi.poker.store import PokerStore
    from yeaboi.ui.mode_select.screens._project_cards import RunSummary
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_poker_screen
    from yeaboi.ui.shared._components import POKER_THEME, poker_title

    def _report(run_id: int):
        with PokerStore(_ana_dbp) as store:
            return store.get_run_by_id(run_id)

    def load_runs():
        with PokerStore(_ana_dbp) as store:
            rows = store.get_all_history(100)
        out = []
        for r in rows:
            date = r.get("poker_date") or ""
            scope = r.get("scope_label") or ""
            n, done = r.get("ticket_count", 0), r.get("estimated_count", 0)
            sub = " · ".join(p for p in (scope, f"{done}/{n} estimated") if p)
            out.append(
                RunSummary(
                    "poker",
                    r["id"],
                    f"Poker — {date or _relative_time(r['run_at'])}",
                    sub,
                    _relative_time(r["run_at"]),
                    session_id=r.get("session_id", ""),
                )
            )
        return out

    def make_detail(run):
        report = _report(run.run_id)
        if report is None:
            return None

        def render(*, scroll, action_sel, actions, scroll_meta, width, height, message, shimmer_tick):
            return _build_poker_screen(
                {
                    "report": report,
                    "session_name": report.project_name or report.scope_label,
                    "snapshot": True,
                    "actions": actions,
                    "message": message,
                },
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                action_sel=action_sel,
                width=width,
                height=height,
                shimmer_tick=shimmer_tick,
            )

        return render

    def _history_for(report):
        # Trend chart data: this session's past poker runs (newest-first rows).
        if not report.session_id:
            return []
        with PokerStore(_ana_dbp) as store:
            return store.get_history(report.session_id, limit=30)

    def files_export(run):
        report = _report(run.run_id)
        if report is None:
            return "That run is no longer available."
        paths = export_poker(report, history=_history_for(report))
        return f"Exported to {paths['markdown'].parent}  (Markdown + HTML)"

    def get_document(run):
        report = _report(run.run_id)
        return "That run is no longer available." if report is None else (_title(report), build_poker_markdown(report))

    def delete_run(run):
        with PokerStore(_ana_dbp) as store:
            store.delete_run(run.run_id)

    _run_mode_hub(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        mode="poker",
        title_fn=poker_title,
        subtitle="Saved poker sessions",
        empty_title="No poker sessions yet",
        empty_subtitle="Press Enter to start estimating tickets with your team",
        new_label="+ New session",
        load_runs=load_runs,
        make_detail=make_detail,
        files_export=files_export,
        get_document=get_document,
        share_theme=POKER_THEME,
        delete_run=delete_run,
        run_new=lambda: _run_poker_page(console, live, read_key, frame_time, supports_timeout),
    )


def _sweep_menu_in(
    console: Console,
    live,
    selected: int,
    n: int,
    *,
    sweep_skip: int | None = None,
    companion_from: float | None = None,
    cards: list[dict] | None = None,
    mascot: str = "duck",
) -> None:
    """Play the diagonal intro wipe that reveals the mode titles top-left →
    bottom-right, then land on the fully-revealed frame.

    Shared by the fresh-load intro and the return transitions. ``sweep_skip``
    leaves that one title fully shown throughout (used after the return slide, when
    the mode you came from is already home and only the rest scroll in). A no-op
    wipe (straight to the final frame) when the terminal is too small.
    ``cards``/``mascot`` pick the menu (Humans default, Agents when passed).
    """
    _iw, _ih = console.size
    if _iw >= _MIN_WIDTH and _ih >= _MIN_HEIGHT:
        _widths = mode_title_widths(cards)
        # Front value at which the last-revealed cell of each title is covered;
        # the sweep runs until the largest of these.
        _front_max = 0.0
        _rb = 0
        for _i in range(n):
            _front_max = max(_front_max, (_rb + 1) * _SWEEP_ROW_WEIGHT + _widths[_i])
            _rb += (2 + (3 if _i == selected else 0)) + (1 if _i < n - 1 else 0)
        _front_max += 2
        _intro_start = time.monotonic()
        while True:
            _front = (time.monotonic() - _intro_start) * _MENU_SWEEP_SPEED
            w, h = console.size
            # On a return (companion_from set) the duck slides back IN as the wipe
            # runs — from where it sat in the sub-page corner to its menu spot — so
            # it never clears; on a fresh load it waits off-screen until the wipe ends.
            if companion_from is not None:
                _ci = companion_from + (1.0 - companion_from) * min(1.0, _front / _front_max)
            else:
                _ci = 0.0
            live.update(
                _build_mode_screen(
                    selected,
                    width=w,
                    height=h,
                    shimmer_tick=0.0,
                    desc_reveal=0,
                    sweep_front=_front,
                    sweep_skip=sweep_skip,
                    companion_intro=_ci,
                    cards=cards,
                    mascot=mascot,
                )
            )
            if _front >= _front_max:
                break
            time.sleep(_FRAME_TIME)
    # Final frame with normal styling (fully revealed). The duck is home on a
    # return (companion slid in during the wipe), still off-screen on a fresh load.
    w, h = console.size
    _ci_final = 1.0 if companion_from is not None else 0.0
    live.update(
        _build_mode_screen(
            selected,
            width=w,
            height=h,
            shimmer_tick=0.0,
            desc_reveal=0,
            companion_intro=_ci_final,
            cards=cards,
            mascot=mascot,
        )
    )


def _slide_menu_in(
    console: Console, live, selected: int, n: int, *, cards: list[dict] | None = None, mascot: str = "duck"
) -> None:
    """Return-to-menu transition: the mode you came from slides back FIRST, then the
    rest scroll in around it exactly like the fresh-load intro.

    Phase 1 is the inverse of the select→page lift — the selected title drops from
    the top row (where that lift left it) down to its resting position, alone.
    Phase 2 hands off to the diagonal wipe (``_sweep_menu_in`` with ``sweep_skip``)
    so every OTHER title reveals top-left → bottom-right while the one you picked
    stays put. A no-op (straight to the final frame) when the terminal is too small.
    ``cards``/``mascot`` pick the menu (Humans default, Agents when passed).
    """
    _card_list = _MODE_CARDS if cards is None else cards
    w, h = console.size
    if w >= _MIN_WIDTH and h >= _MIN_HEIGHT:
        chosen = _card_list[selected]
        base_r, base_g, base_b = COLOR_RGB.get(chosen["color"], (180, 180, 180))
        base_style = f"bold rgb({base_r},{base_g},{base_b})"
        start_offset = 1  # the top row the select→page lift left the title on
        target_offset = selected_title_offset(selected, width=w, height=h, cards=cards)
        # Phase 1: the selected title slides home, on its own.
        slide_frames = 14
        for frame in range(slide_frames + 1):
            t = frame / slide_frames
            eased = ease_out_cubic(t)
            current_offset = int(start_offset + (target_offset - start_offset) * eased)
            w, h = console.size
            live.update(_build_slide_frame(chosen, top_offset=current_offset, width=w, height=h, style=base_style))
            time.sleep(_FRAME_TIME)
    # Phase 2: the rest scroll in with the same diagonal wipe as a fresh load, while
    # the selected title (already home) is held fully shown. The companion slides
    # back in during the wipe (from its sub-page corner) so it never clears.
    _sweep_menu_in(
        console,
        live,
        selected,
        n,
        sweep_skip=selected,
        companion_from=_COMPANION_RETURN_START,
        cards=cards,
        mascot=mascot,
    )


def _run_category_screen(
    console: Console,
    live,
    read_key,
    supports_timeout: bool,
    *,
    preselected: str = "humans",
) -> str | None:
    """Phase 0 — the Humans/Agents landing split. Returns a category key or
    None to quit.

    Always shown on a fresh load (the last-used category is *preselected*,
    never auto-skipped — auto-skip would make the other family invisible).
    Esc and q both quit here: there is nothing further back to go to.
    """
    from yeaboi.ui.mode_select.screens._screens_category import (
        _CATEGORY_CARDS,
        _build_category_screen,
        category_at_pos,
        category_index,
    )

    selected = category_index(preselected)
    start = time.monotonic()
    logger.info("category screen shown (preselected: %s)", preselected)
    while True:
        w, h = console.size
        if w < _MIN_WIDTH or h < _MIN_HEIGHT:
            live.update(_build_too_small_screen(w, h))
            k = read_key(timeout=_FRAME_TIME) if supports_timeout else read_key()
            if k in ("q", "esc"):
                return None
            continue
        elapsed = time.monotonic() - start
        live.update(
            _build_category_screen(
                selected,
                width=w,
                height=h,
                shimmer_tick=elapsed,
                intro=min(1.0, elapsed / 0.4),
            )
        )
        key = read_key(timeout=_FRAME_TIME) if supports_timeout else read_key()
        if key in ("left", "right", "up", "down", "tab"):
            selected = 1 - selected
        elif key == "enter":
            chosen = _CATEGORY_CARDS[selected]["key"]
            logger.info("category chosen: %s", chosen)
            return chosen
        elif key in ("q", "esc"):
            logger.info("quit from category screen")
            return None
        elif isinstance(key, str) and key.startswith("click:"):
            try:
                cx, cy = (int(p) for p in key.split(":")[1:3])
            except ValueError:
                continue
            hit = category_at_pos(w, h, row=cy, col=cx)
            if hit is None:
                continue
            if hit == selected:
                chosen = _CATEGORY_CARDS[selected]["key"]
                logger.info("category click-chosen: %s", chosen)
                return chosen
            selected = hit


def _landing_first_frame(category: str, *, width: int, height: int):
    """The frame ``select_mode``'s Live is seeded with.

    Rich paints the seed on entry, before any loop body runs, so it has to be the
    opening frame of whatever the loop shows first — Phase 0, the landing split,
    at intro 0. Seed the *menu* instead and its hint row and music pocket flash
    over the tail of the splash for a frame.
    """
    from yeaboi.ui.mode_select.screens._screens_category import _build_category_screen, category_index

    return _build_category_screen(
        category_index(category),
        width=width,
        height=height,
        shimmer_tick=0.0,
        intro=0.0,
    )


def select_mode(
    console: Console | None = None, *, dry_run: bool = False, _read_key_fn=None
) -> tuple[str, str | None, str | None] | None:
    """Show full-screen mode selection, then project list → intake mode for Planning.

    Returns (mode_key, intake_mode, questionnaire_path) tuple or None if cancelled.
    - Small:  ("project-planning", "small_project", None)
    - Epic:   ("project-planning", "smart", None)
    - Import: ("project-planning", None, "/path/to/questionnaire.md")
    - Export/Cancel: None
    Only available modes can be selected.
    """
    console = console or Console()
    read_key = _read_key_fn or _read_key
    selected = 0
    n = len(_MODE_CARDS)

    # The landing split (Phase 0). `category` picks which card list Phase 1
    # shows; the last choice is persisted and *preselected* on the next launch
    # (never auto-skipped). Esc from a menu returns here; q quits.
    from yeaboi.config import get_last_category, set_last_category

    category = get_last_category()
    cards: list[dict] = _MODE_CARDS if category == "humans" else _AGENT_CARDS
    mascot = "duck" if category == "humans" else "robo"
    _category_pending = True  # show the split on the first pass through the loop
    _back_to_category = False

    # The TUI is interactive — flip the filesystem sandbox (fs_policy) into
    # consent mode: denials still raise, but ALSO queue a ConsentRequest that
    # the session loop pops after each graph turn to show the Allow once /
    # Always allow / Deny popup. Headless paths (cli flags, MCP, scheduled
    # standups) never set this, so they stay hard-deny with an actionable error.
    from yeaboi import fs_policy

    fs_policy.set_interactive(True)

    # Kick off the one-shot PyPI update check on a daemon thread. Idempotent and
    # fire-and-forget — the bottom-left version row picks the result up whenever
    # a frame renders after the fetch lands.
    from yeaboi.update_check import start_background_check

    start_background_check()

    w, h = console.size
    start_time = time.monotonic()
    select_time = start_time
    # Manual tip browsing ([ / ] keys). A rotation shift added to the auto index:
    # browsing moves through the list while auto-rotation keeps running, so tips
    # never get stuck on one card (see resolve_index in ui/shared/_tips.py).
    tip_offset = 0

    import inspect

    _supports_timeout = "timeout" in inspect.signature(read_key).parameters

    # Render into the alternate-screen buffer (screen=True) so Rich double-buffers
    # each frame. The welcome screen animates continuously (the selected title's
    # shimmer, the cross-fading tip, the idle duck, the music equalizer), so in
    # inline mode Rich rewrites scattered lines across the full height every frame
    # — the visible "reprint"/flicker. Alt-screen swaps composite frames cleanly.
    # 60fps keeps the shimmer/duck/tip motion smooth (the input loop already polls
    # at _FRAME_TIME = 1/60, so the Live refresh cap was the bottleneck);
    # alt-screen double-buffering means the higher rate costs redraw work but
    # never flickers.
    #
    with make_live(
        _landing_first_frame(category, width=w, height=h),
        console=console,
        refresh_per_second=60,
        screen=True,
    ) as live:
        # Outer loop: returns here when user presses Esc from project list
        # to go back to mode selection (instead of recursive select_mode call).
        _restart_mode_select = True
        _skip_fade_in = False
        # A reverse transition (the project-list Esc) already slides the menu back
        # in on its own; every other return snaps in cold. This flag marks the
        # former so the sweep below runs on a fresh load AND on a cold return —
        # animating the menu items back in — but doesn't double up on the slide.
        _reverse_animated = False
        while _restart_mode_select:
            _restart_mode_select = False

            # _skip_fade_in signals a return from a sub-page (drives the companion's
            # slide-back-from-the-corner entrance); it no longer suppresses the sweep.
            _returning = _skip_fade_in
            _skip_fade_in = False

            # ── Phase 0: the Humans/Agents landing split ─────────────────────
            # Shown on a fresh load and whenever Esc backs out of a menu; a
            # return from a sub-page keeps its category and skips straight to
            # the menu transition below.
            if _category_pending:
                _category_pending = False
                _pick = _run_category_screen(console, live, read_key, _supports_timeout, preselected=category)
                if _pick is None:
                    return None
                if _pick != category:
                    set_last_category(_pick)
                category = _pick
                cards = _MODE_CARDS if category == "humans" else _AGENT_CARDS
                mascot = "duck" if category == "humans" else "robo"
                n = len(cards)
                selected = 0
                # A category pick always sweeps its menu in fresh.
                _returning = False
                _reverse_animated = False

            if _reverse_animated:
                # The reverse transition already revealed every item — don't re-run.
                _reverse_animated = False
            elif _returning:
                # Cold return from a sub-page: the mode you came from slides home,
                # then the rest load in around it (the inverse of the select lift).
                _slide_menu_in(console, live, selected, n, cards=cards, mascot=mascot)
            else:
                # Fresh load: one diagonal wipe reveals every title top-left →
                # bottom-right (the inverse of the splash crumble).
                _sweep_menu_in(console, live, selected, n, cards=cards, mascot=mascot)
            select_time = time.monotonic()
            # Companion entrance. Fresh load: full slide-in from off-screen right,
            # starting once the wipe has landed. On a RETURN the duck already slid
            # back from its sub-page corner during the wipe (see _sweep_menu_in's
            # companion_from), so start the entrance already finished — otherwise it
            # would clear and re-slide, the "duck disappears then comes back" glitch.
            _companion_intro_start = time.monotonic() - (_COMPANION_INTRO_SECONDS if _returning else 0.0)

            # ── Phase 1: Mode selection ───────────────────────────────────────
            _compose: dict | None = None  # the duck's feedback bubble, when open
            while True:
                # Terminal-size guard: below the minimum the welcome screen can't
                # show every mode + description + hints without clipping, so the
                # duck asks the user to size up. Poll for a resize (or quit)
                # instead of rendering a broken menu.
                _w, _h = console.size
                if _w < _MIN_WIDTH or _h < _MIN_HEIGHT:
                    live.update(_build_too_small_screen(_w, _h))
                    _k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                    if _k in ("q", "esc"):
                        return None
                    continue

                key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                # A pending Ctrl+R (stale subscription token) selects Settings and
                # enters it, from wherever the key was pressed: inside a mode it is
                # claimed on the way back out here, since the hub is the only thing
                # that routes. Selecting rather than jumping keeps the normal
                # transition, so the destination still arrives the usual way.
                if take_settings_jump():
                    _s_idx = next((i for i, c in enumerate(cards) if c.get("key") == "settings"), None)
                    if _s_idx is not None:
                        logger.info("Opening Settings for a stale subscription token")
                        selected = _s_idx
                        key = "enter"

                if _compose is not None:
                    # The duck's feedback bubble owns every key while it's open —
                    # including 'q', which is a character you may want to type.
                    if key and not _compose.get("closing"):

                        def _compose_render(update: bool = True):
                            _w, _h = console.size
                            _panel = _build_mode_screen(
                                selected,
                                width=_w,
                                height=_h,
                                shimmer_tick=time.monotonic() - start_time,
                                desc_reveal=999,
                                tip_offset=tip_offset,
                                compose=_compose,
                                cards=cards,
                                mascot=mascot,
                            )
                            if update:
                                live.update(_panel)
                            return _panel

                        _compose = _feedback_compose_key(
                            key,
                            _compose,
                            console=console,
                            live=live,
                            read_key=read_key,
                            render=_compose_render,
                        )
                    if _compose is not None:
                        _compose = _feedback_compose_tick(_compose)
                    if _compose is None:
                        select_time = time.monotonic()  # restart the description reveal
                elif key in ("up", "left", "scroll_up", "down", "right", "scroll_down"):
                    # Coalesce a fast wheel/held-key burst into one net move + one
                    # repaint, so the animated mode carousel doesn't stutter.
                    _delta = coalesce_steps(
                        key,
                        read_key,
                        down=("down", "right", "scroll_down"),
                        up=("up", "left", "scroll_up"),
                    )
                    if _delta:
                        selected = (selected + _delta) % n
                        select_time = time.monotonic()
                    else:
                        continue  # net-zero burst — nothing moved, skip the repaint
                elif key == "enter":
                    mode = cards[selected]
                    if mode["available"]:
                        break
                    continue
                elif key == "esc":
                    # Esc backs out to the landing split (the screen this menu
                    # came from). Quitting stays on q, mirroring every sub-page's
                    # esc-goes-back convention.
                    logger.info("esc from %s menu — back to category screen", category)
                    _back_to_category = True
                    break
                elif key == "q":
                    # Courtesy on quit: offer to stop a running local Ollama
                    # server (gated on provider/localhost/reachable — cloud
                    # exits stay instant). Never let this block quitting.
                    try:
                        from yeaboi.ollama_control import should_offer_ollama_stop, stop_ollama_server

                        if should_offer_ollama_stop() and _confirm_stop_ollama(
                            console, live, read_key, _FRAME_TIME, _supports_timeout
                        ):
                            _stopped, _msg = stop_ollama_server()
                            logger.info("ollama stop on quit: %s", _msg)
                    except Exception:
                        logger.debug("ollama exit prompt failed", exc_info=True)
                    return None
                elif key == "t":
                    # Toggle the rotating tips on/off and persist the choice. The
                    # live.update() at the bottom of the loop re-renders with the
                    # new state, so the tip banner hides/shows instantly.
                    from yeaboi.config import is_tips_enabled, set_tips_enabled

                    set_tips_enabled(not is_tips_enabled())
                elif key in ("[", "]"):
                    # Browse tips manually by nudging the rotation shift. Auto-
                    # rotation keeps running from the new position (never stuck).
                    tip_offset += 1 if key == "]" else -1
                elif key == "g":
                    # Jump into the feature the current tip describes (if it maps
                    # to a selectable card). Reuses the enter/activate path. Tips
                    # rotate on both menus, so a tip may point at the OTHER
                    # category's card — then the jump switches category too.
                    from yeaboi.ui.shared._tips import resolve_index, tip_at

                    _tip = tip_at(resolve_index(time.monotonic() - start_time, tip_offset))
                    if _tip.mode_key is not None:
                        _j = next((i for i, m in enumerate(cards) if m["key"] == _tip.mode_key), None)
                        if _j is not None and cards[_j]["available"]:
                            logger.info("tip jump to mode: %s", _tip.mode_key)
                            selected = _j
                            break
                        _other = _AGENT_CARDS if cards is _MODE_CARDS else _MODE_CARDS
                        _j = next((i for i, m in enumerate(_other) if m["key"] == _tip.mode_key), None)
                        if _j is not None and _other[_j]["available"]:
                            category = "agents" if _other is _AGENT_CARDS else "humans"
                            logger.info("tip jump across categories to %s (%s)", _tip.mode_key, category)
                            set_last_category(category)
                            cards = _other
                            mascot = "duck" if category == "humans" else "robo"
                            n = len(cards)
                            selected = _j
                            break
                elif key == "c":
                    # Open the Changelog page (bottom-left hint). Handled inline
                    # like `t` — no break, so returning falls straight back into
                    # this loop and the frame update below repaints mode select.
                    # No wordmark intro: its shine sweep reads as a loader, and these
                    # two open instantly (bundled JSON / an empty form). The All Tips
                    # gallery below keeps its entrance.
                    logger.info("changelog opened from mode select")
                    _run_changelog_page(console, live, read_key, _FRAME_TIME, _supports_timeout)
                    _slide_menu_in(console, live, selected, n, cards=cards, mascot=mascot)  # animate the menu back in
                    select_time = time.monotonic()  # restart the description typewriter
                elif key == "s":
                    # Ceremonies (bottom-left hint). Handled inline like `c`: the
                    # page is a reader, so returning falls straight back here.
                    # A keycap rather than a mode card because the menu draws
                    # every card and an eleventh pushes the version row off screen
                    # at the enforced minimum size.
                    logger.info("ceremonies opened from mode select")
                    from yeaboi.ui.mode_select._ceremonies import run_ceremonies_page

                    run_ceremonies_page(console, live, read_key, _FRAME_TIME, _supports_timeout, dry_run=dry_run)
                    _slide_menu_in(console, live, selected, n, cards=cards, mascot=mascot)
                    select_time = time.monotonic()
                elif key == "f":
                    # Quick feedback comes out of the duck: his tip bubble becomes a
                    # composer in place, so the welcome screen never leaves. The full
                    # form (type/area/AI polish/attachments) is Tab from inside it.
                    # The bubble is drawn over the duck's lane, so without one there
                    # is nothing to draw on — fall back to the full form rather than
                    # swallowing keys into an invisible composer.
                    _fw, _fh = console.size
                    if not welcome_shows_companion(_fw, _fh):
                        logger.info("feedback: terminal too small for the bubble, opening the form")
                        _run_feedback_page(console, live, read_key, _FRAME_TIME, _supports_timeout)
                        _slide_menu_in(console, live, selected, n, cards=cards, mascot=mascot)
                        select_time = time.monotonic()
                        continue
                    logger.info("feedback bubble opened from mode select")
                    # set_text_entry so 'c' types a 'c' instead of opening the
                    # controls drawer while the message is being written.
                    set_text_entry(True)
                    _compose = {
                        "field": 2,  # land in the message; the selectors are up from there
                        "kind": 0,
                        "area": 0,
                        "buf": "",
                        "cur": 0,
                        "status": "",
                        "notice": "",
                        "presence": 0.0,  # eased in on the first frames
                        "closing": False,
                        "attachments": [],  # Ctrl+V screenshots, as [image #N] chips
                        "dts": DoubleTapSpace(),  # double-tap Space → dictation
                        "thread": None,
                        "done_at": 0.0,
                    }
                elif key == "F":
                    # The full Feedback form, for anything the bubble is too small for.
                    logger.info("feedback opened from mode select")
                    _run_feedback_page(console, live, read_key, _FRAME_TIME, _supports_timeout)
                    _slide_menu_in(console, live, selected, n, cards=cards, mascot=mascot)  # animate the menu back in
                    select_time = time.monotonic()  # restart the description typewriter
                elif key == "a":
                    # Open the All Tips gallery (bottom-left hint) — same inline
                    # pattern as the Changelog/Feedback pages above.
                    # No wordmark intro here either (see the changelog above).
                    logger.info("all tips opened from mode select")
                    _run_all_tips_page(console, live, read_key, _FRAME_TIME, _supports_timeout)
                    _slide_menu_in(console, live, selected, n, cards=cards, mascot=mascot)  # animate the menu back in
                    select_time = time.monotonic()  # restart the description typewriter
                elif key == "clear":
                    # Ctrl+U — the update shortcut advertised by the bottom-right
                    # update box. Only acts when a newer release exists; Ctrl+U is
                    # otherwise the text "kill line" key, unused on this menu.
                    from yeaboi.update_check import get_update_status

                    if get_update_status()["update_available"]:
                        if _run_update_flow(console, live, read_key, _FRAME_TIME, _supports_timeout):
                            # Unwind the whole TUI: cli.main relaunches us onto the
                            # new version once its finally has restored the terminal
                            # (os.execv skips atexit, so it can't be done from here).
                            # Deliberately not the q/esc quit path — this isn't a
                            # quit, so a local Ollama server should stay up for the
                            # process that's about to take over.
                            return None
                        select_time = time.monotonic()
                elif isinstance(key, str) and key.startswith("click:"):
                    # Click-to-select: a click on a mode's block highlights it
                    # (revealing its description); a click on the already-selected
                    # mode activates it, exactly like Enter. Clicks off the list
                    # (tips, version row, the duck lane) resolve to None → ignored.
                    try:
                        _cx, _cy = (int(p) for p in key.split(":")[1:3])
                    except ValueError:
                        _cx = _cy = -1
                    _w, _h = console.size
                    if mascot == "duck" and duck_hit(_w, _h, row=_cy, col=_cx):
                        # Click the duck → his shades lift to reveal a second
                        # pair. Duck-only: the robo companion wears a visor.
                        logger.info("duck clicked — double-shades gag")
                        _play_duck_shades(
                            console,
                            live,
                            selected,
                            tip_offset=tip_offset,
                            start_time=start_time,
                            select_time=select_time,
                        )
                        continue
                    _hit = mode_at_row(selected, width=_w, height=_h, row=_cy, col=_cx, cards=cards)
                    if _hit is not None:
                        if _hit == selected:
                            if cards[selected]["available"]:
                                logger.info("mode click-activate: %s", cards[selected]["key"])
                                break
                        else:
                            logger.info("mode click-select: %s", cards[_hit]["key"])
                            selected = _hit
                            select_time = time.monotonic()

                elapsed = time.monotonic() - select_time
                reveal = elapsed * _DESC_SCROLL_SPEED  # float for sub-char fade

                w, h = console.size
                tick = time.monotonic() - start_time
                companion_intro = min(1.0, (time.monotonic() - _companion_intro_start) / _COMPANION_INTRO_SECONDS)
                live.update(
                    _build_mode_screen(
                        selected,
                        width=w,
                        height=h,
                        shimmer_tick=tick,
                        desc_reveal=reveal,
                        tip_offset=tip_offset,
                        companion_intro=companion_intro,
                        compose=_compose,
                        cards=cards,
                        mascot=mascot,
                    )
                )

            # Esc backed out of the menu — return to the landing split rather
            # than running the select transition below.
            if _back_to_category:
                _back_to_category = False
                _category_pending = True
                _restart_mode_select = True
                continue

            # ── Phase 2: Transition ───────────────────────────────────────────
            chosen = cards[selected]
            all_indices = list(range(n))
            others = [i for i in all_indices if i != selected]
            base_r, base_g, base_b = COLOR_RGB.get(chosen["color"], (180, 180, 180))
            base_style = f"bold rgb({base_r},{base_g},{base_b})"

            # 2a: Pulse the selected mode
            for frame in range(12):
                t = frame / 11
                intensity = math.sin(t * math.pi)
                r = int(base_r + (255 - base_r) * intensity)
                g = int(base_g + (255 - base_g) * intensity)
                b = int(base_b + (255 - base_b) * intensity)
                pulse_style = f"bold rgb({r},{g},{b})"
                w, h = console.size
                live.update(
                    _build_mode_screen(
                        selected,
                        width=w,
                        height=h,
                        visible=all_indices,
                        fade_style=pulse_style,
                        fade_indices=[selected],
                        cards=cards,
                        mascot=mascot,
                    )
                )
                time.sleep(_FRAME_TIME)

            # 2b: Fade out unselected modes — and, in step, fade the tip bubble +
            # update box out (the duck stays put so the sub-page overlay can continue
            # him into his corner). The mirror of the fade-in on arrival.
            _nfade = max(1, len(FADE_OUT_LEVELS) - 1)
            for _i, grey in enumerate(FADE_OUT_LEVELS):
                w, h = console.size
                live.update(
                    _build_mode_screen(
                        selected,
                        width=w,
                        height=h,
                        visible=all_indices,
                        fade_style=grey,
                        fade_indices=others,
                        selected_style=base_style,
                        extras_reveal=1.0 - (_i / _nfade),
                        cards=cards,
                        mascot=mascot,
                    )
                )
                time.sleep(_FRAME_TIME)

            # 2c: Slide the chosen title up to the top. It starts from the item's
            # ACTUAL resting row (so a mid-list pick lifts from where it sits, not
            # from a fixed centre) and rises to one line below the top border.
            w, h = console.size
            start_offset = selected_title_offset(selected, width=w, height=h)
            end_offset = 1  # one blank line above title to match project list layout

            slide_frames = 15
            for frame in range(slide_frames + 1):
                t = frame / slide_frames
                eased = ease_out_cubic(t)
                current_offset = int(start_offset + (end_offset - start_offset) * eased)
                w, h = console.size
                live.update(
                    _build_slide_frame(
                        chosen,
                        top_offset=current_offset,
                        width=w,
                        height=h,
                        style=base_style,
                    )
                )
                time.sleep(_FRAME_TIME)

            # The duck walks into the corner of whichever page the card opens —
            # the chat-greeting entrance, replayed per card entry. Any keypress
            # skips it (read_key calls skip_duck_entrance app-wide).
            from yeaboi.ui.shared._music_bar import start_duck_entrance

            start_duck_entrance(replay=True)

            # ── Route: the Agents family → one prefix dispatch, not three more
            # chain branches. route_agent_mode wraps each mode in mode_log() and
            # its beta gate; returning lands back on the Agents menu.
            if chosen["key"].startswith("agent-"):
                from yeaboi.ui.mode_select._agents import route_agent_mode

                route_agent_mode(
                    chosen["key"],
                    console=console,
                    live=live,
                    read_key=read_key,
                    frame_time=_FRAME_TIME,
                    supports_timeout=_supports_timeout,
                )
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Team Analysis mode → dedicated analysis flow ──────
            if chosen["key"] == "team-analysis":
                logger.info("Analysis mode selected")
                # Route all records to logs/analysis/analysis.log while the
                # analysis flow runs. The branch is too large for a `with`
                # block, so it detaches explicitly at both `continue` exits.
                attach_mode_handler("analysis")
                from yeaboi.azdevops_sync import is_azdevops_board_configured as _azdevops_check
                from yeaboi.jira_sync import is_jira_configured as _jira_check

                _jira_ok = _jira_check()
                _azdevops_ok = _azdevops_check()
                _board_configured = _jira_ok or _azdevops_ok

                if not _board_configured:
                    # No board configured — show message and return to mode select.
                    # Re-render each frame so the ANALYSIS title keeps shimmering.
                    _br_anim0 = time.monotonic()  # shimmer title clock
                    while True:
                        w, h = console.size
                        live.update(
                            _build_project_export_success_screen(
                                "No board configured.\n\n"
                                "Set JIRA_BASE_URL + JIRA_API_TOKEN\n"
                                "or AZURE_DEVOPS_ORG_URL + AZURE_DEVOPS_TOKEN\n"
                                "in your .env file.",
                                width=w,
                                height=h,
                                subtitle="Board required",
                                hint="",  # the back tab now shows the go-back affordance
                                mode="analysis",
                                shimmer_tick=time.monotonic() - _br_anim0,
                            )
                        )
                        k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                        if k:
                            break
                    _restart_mode_select = True
                    _skip_fade_in = True
                    detach_mode_handler("analysis")
                    continue

                # Load existing team profiles
                _profiles_for_analysis: list = []
                try:
                    from datetime import UTC, datetime

                    from yeaboi.team_profile import TeamProfileStore

                    _tp_db = _ana_dbp
                    if _tp_db.exists():
                        with TeamProfileStore(_tp_db) as _tp_store:
                            _raw_profiles = _tp_store.list_profiles()
                        for _rp in _raw_profiles:
                            days = 0
                            if _rp.updated_at:
                                try:
                                    _up = datetime.fromisoformat(_rp.updated_at)
                                    days = (datetime.now(UTC) - _up).days
                                except Exception:
                                    pass
                            # Check if preview flow was completed for this profile
                            _is_complete = False
                            try:
                                _a_sessions = _tp_store._conn.execute(
                                    "SELECT last_node_completed FROM sessions_meta "
                                    "WHERE session_mode = 'analysis' AND project_name LIKE ? "
                                    "ORDER BY last_modified DESC LIMIT 1",
                                    (f"%{_rp.project_key}%",),
                                ).fetchone()
                                if _a_sessions and _a_sessions[0] in ("complete", "done"):
                                    _is_complete = True
                            except Exception:
                                pass
                            _profiles_for_analysis.append(
                                ProfileSummary(
                                    team_id=_rp.team_id,
                                    source=_rp.source,
                                    project_key=_rp.project_key,
                                    sample_sprints=_rp.sample_sprints,
                                    velocity_avg=_rp.velocity_avg,
                                    sample_stories=_rp.sample_stories,
                                    updated="today" if days == 0 else (f"{days} day{'s' if days != 1 else ''} ago"),
                                    staleness_days=days,
                                    preview_complete=_is_complete,
                                )
                            )
                except Exception:
                    pass

                # Load resumable analysis sessions
                _ana_sessions: list[dict] = []
                try:
                    from yeaboi.sessions import SessionStore as _SessStore

                    _sess_db = _ana_dbp
                    if _sess_db.exists():
                        with _SessStore(_sess_db) as _ss:
                            _ana_sessions = _ss.list_analysis_sessions()
                except Exception:
                    pass

                logger.info(
                    "Analysis mode: %d profiles, %d sessions, jira=%s, azdevops=%s",
                    len(_profiles_for_analysis),
                    len(_ana_sessions),
                    _jira_ok,
                    _azdevops_ok,
                )

                # Always one button; board picker popup shown if both configured
                _ana_labels = ["+ New Analysis"]

                # Show profile list or go straight to analysis
                _ana_items = _profiles_for_analysis + _ana_labels  # type: ignore[operator]
                _ana_selected = 0
                _ana_n = len(_profiles_for_analysis) + len(_ana_labels)

                # Stagger reveal
                _reveal_target = float(_ana_n)
                _cards_visible = 0.0
                _reveal_speed = 15.0
                _reveal_start = time.monotonic()
                while _cards_visible < _reveal_target:
                    dt_r = time.monotonic() - _reveal_start
                    _cards_visible = min(_reveal_target, dt_r * _reveal_speed)
                    w, h = console.size
                    live.update(
                        _build_project_list_screen(
                            [],
                            _ana_selected,
                            width=w,
                            height=h,
                            cards_visible=_cards_visible,
                            card_fade=1.0,
                            jira_enabled=_jira_ok,
                            azdevops_enabled=_azdevops_ok,
                            profiles=_profiles_for_analysis,
                            new_analysis_labels=_ana_labels,
                            mode="analysis",
                            shimmer_tick=dt_r,
                        )
                    )
                    time.sleep(_FRAME_TIME)

                # Analysis mode interaction loop
                _team_popup_result = ""
                _ana_focus = 0
                _ana_card_fade = 1.0
                _ana_restart = True
                while _ana_restart:
                    _ana_restart = False
                    _ana_focus = 0
                    _ana_action_btns = 0.0
                    _has_prof = _profiles_for_analysis and _ana_selected < len(_profiles_for_analysis)
                    _ana_action_btns_target = 2.0 if _has_prof else 0.0
                    _ana_del_fade = 0.0
                    _ana_exp_fade = 0.0
                    _ana_export_submenu = False
                    _ana_sub_sel = 0
                    _ana_sub_html_fade = 0.0
                    _ana_sub_md_fade = 0.0
                    _ana_sub_visible = 0.0
                    _ana_sub_visible_target = 0.0
                    _ana_del_popup_open = False
                    _ana_del_popup_t = 0.0
                    _ana_del_popup_target = 0.0
                    _ana_del_popup_name = ""
                    _ana_del_popup_pulse = 0.0
                    _ana_del_popup_flash = 0.0
                    _ana_del_pending = False
                    _ana_prev = time.monotonic()
                    _ana_anim0 = _ana_prev  # shimmer title clock
                    _ana_last_panel = None  # most recent list panel, for click hit-testing

                    while True:
                        key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                        _is_profile = _ana_selected < len(_profiles_for_analysis)
                        _is_analysis_btn = _ana_selected >= len(_profiles_for_analysis)

                        # ── Mouse: click a card to select + activate it ──
                        # Mirrors _build_run_hub_screen's _card_regions hit-test: map the
                        # click-y onto a card's flat index, then synthesise the same Enter
                        # activation. Ignored while the delete popup is modal.
                        _ana_click = parse_click(key)
                        if _ana_click is not None:
                            if _ana_del_popup_open:
                                continue
                            _ana_hit = next(
                                (
                                    idx
                                    for y0, y1, idx in getattr(_ana_last_panel, "_card_regions", []) or []
                                    if y0 <= _ana_click[1] <= y1
                                ),
                                None,
                            )
                            if _ana_hit is None:
                                continue
                            _ana_selected = _ana_hit
                            _ana_focus = 0
                            _is_profile = _ana_selected < len(_profiles_for_analysis)
                            _is_analysis_btn = _ana_selected >= len(_profiles_for_analysis)
                            key = "enter"  # fall through to Enter-on-card handling

                        # ── Delete confirmation popup ─────────────────
                        if _ana_del_popup_open and key:
                            if key == "enter":
                                _ana_del_popup_flash = 1.0
                                _ana_del_pending = True
                            elif key in ("esc", "q"):
                                _ana_del_popup_target = 0.0
                            continue

                        # Perform delete after popup slides out
                        if _ana_del_popup_open and _ana_del_popup_target == 0.0 and _ana_del_popup_t <= 0:
                            if _ana_del_pending:
                                try:
                                    from yeaboi.team_profile import TeamProfileStore

                                    _tp_db = _ana_dbp
                                    if _tp_db.exists():
                                        _del_p = _profiles_for_analysis[_ana_selected]
                                        with TeamProfileStore(_tp_db) as _s:
                                            _s.delete(_del_p.team_id)
                                    _profiles_for_analysis.pop(_ana_selected)
                                    _ana_n = len(_profiles_for_analysis) + len(_ana_labels)
                                    _ana_selected = min(_ana_selected, _ana_n - 1)
                                    _ana_focus = 0
                                    _ana_action_btns = 0.0
                                    _ana_del_fade = 0.0
                                    _ana_exp_fade = 0.0
                                    _has_prof = _profiles_for_analysis and _ana_selected < len(_profiles_for_analysis)
                                    _ana_action_btns_target = 2.0 if _has_prof else 0.0
                                except Exception:
                                    pass
                            _ana_del_popup_open = False
                            _ana_del_popup_name = ""
                            _ana_del_pending = False

                        if key in ("up", "scroll_up", "down", "scroll_down"):
                            _delta = coalesce_steps(key, read_key, down=("down", "scroll_down"), up=("up", "scroll_up"))
                            if not _delta:
                                continue
                            _ana_selected = (_ana_selected + _delta) % _ana_n
                            _ana_focus = 0
                            _ana_action_btns = 0.0
                            _is_profile = _ana_selected < len(_profiles_for_analysis)
                            _ana_action_btns_target = 2.0 if _is_profile else 0.0
                            _ana_del_fade = 0.0
                            _ana_exp_fade = 0.0
                            _ana_export_submenu = False
                            _ana_sub_visible_target = 0.0
                        elif key == "left":
                            if _ana_focus > 0:
                                _ana_focus -= 1
                            _ana_del_fade = 0.0 if _ana_focus != 1 else 1.0
                            _ana_exp_fade = 0.0 if _ana_focus != 2 else 1.0
                        elif key == "right":
                            if _is_profile and _ana_focus < 2:
                                _ana_focus += 1
                            _ana_del_fade = 0.0 if _ana_focus != 1 else 1.0
                            _ana_exp_fade = 0.0 if _ana_focus != 2 else 1.0
                        elif key == "enter":
                            if _is_profile and _ana_focus == 0:
                                # View profile results
                                _sel_p = _profiles_for_analysis[_ana_selected]
                                from yeaboi.team_profile import TeamProfileStore

                                _tp_db = _ana_dbp
                                _full = None
                                _stored_ex: dict | None = None
                                if _tp_db.exists():
                                    with TeamProfileStore(_tp_db) as _s:
                                        _full, _stored_ex = _s.load_with_examples(
                                            _sel_p.team_id,
                                        )
                                if _full:
                                    while True:
                                        _res = _run_team_analysis_results(
                                            live,
                                            console,
                                            read_key,
                                            _FRAME_TIME,
                                            _supports_timeout,
                                            _full,
                                            _stored_ex,
                                        )
                                        if _res != "continue":
                                            break

                                        # Backfill insights for profiles saved before
                                        # they existed, then show them; Back returns
                                        # to the results overview.
                                        _stored_ex = _ensure_insights(
                                            live,
                                            console,
                                            read_key,
                                            _FRAME_TIME,
                                            _supports_timeout,
                                            _full,
                                            _stored_ex,
                                        )
                                        if (
                                            _run_team_insights(
                                                live,
                                                console,
                                                read_key,
                                                _FRAME_TIME,
                                                _supports_timeout,
                                                _full,
                                                _stored_ex,
                                            )
                                            == "back"
                                        ):
                                            continue

                                        from yeaboi.agent.nodes import _format_team_calibration

                                        _si_text = _format_team_calibration(
                                            _full,
                                            examples=_stored_ex,
                                        )
                                        if _si_text.strip():
                                            _si_resume = _load_ana_session(
                                                _full.project_key if _full else "",
                                            )
                                            # Skip the confirmation when resuming a
                                            # ticket session already mid-generation —
                                            # the user confirmed on the first pass.
                                            _resuming = bool(_si_resume) and _si_resume.get("last_page") in (
                                                "epic",
                                                "stories",
                                                "tasks",
                                                "sprint",
                                            )
                                            if _resuming or _confirm_ticket_generation(
                                                live,
                                                console,
                                                read_key,
                                                _FRAME_TIME,
                                                _supports_timeout,
                                                subtitle=f"{_full.source}/{_full.project_key}" if _full else "",
                                            ):
                                                _run_preview_flow(
                                                    live,
                                                    console,
                                                    read_key,
                                                    _FRAME_TIME,
                                                    _supports_timeout,
                                                    _si_text,
                                                    _full,
                                                    _stored_ex,
                                                    resume_state=_si_resume,
                                                )
                                        break
                                continue
                            elif _is_profile and _ana_focus == 1:
                                # Delete profile — open confirmation popup
                                _sel_p = _profiles_for_analysis[_ana_selected]
                                _ana_del_popup_open = True
                                _ana_del_popup_target = 1.0
                                _ana_del_popup_name = f"{_sel_p.source}/{_sel_p.project_key}"
                                _ana_del_popup_pulse = 0.0
                                _ana_del_popup_flash = 0.0
                                _ana_del_pending = False
                                continue
                            elif _is_profile and _ana_focus == 2:
                                # Export → shared destination picker (files / Notion / Confluence)
                                _sel_p = _profiles_for_analysis[_ana_selected]
                                _tp_db = _ana_dbp
                                _full_p = None
                                _st_ex: dict | None = None
                                if _tp_db.exists():
                                    from yeaboi.team_profile import TeamProfileStore

                                    with TeamProfileStore(_tp_db) as _s:
                                        _full_p, _st_ex = _s.load_with_examples(_sel_p.team_id)
                                if _full_p:
                                    from yeaboi.agent.ceremony_history import gather_ceremony_context

                                    _team_profile_export_flow(
                                        console,
                                        live,
                                        read_key,
                                        _FRAME_TIME,
                                        _supports_timeout,
                                        profile=_full_p,
                                        examples=_st_ex,
                                        ceremony=gather_ceremony_context(_full_p.project_key),
                                    )
                                _ana_exp_fade = 1.0
                                continue
                            elif _is_analysis_btn:
                                # New analysis — the source popup is gone; the unified
                                # component grid (shown in the analysis-run block below)
                                # picks delivery trackers itself, so go straight to it.
                                _team_popup_result = "analyse"
                                break
                        elif key in ("esc", "q"):
                            _restart_mode_select = True
                            _skip_fade_in = True
                            break

                        # Animate
                        _now = time.monotonic()
                        _dt = _now - _ana_prev
                        _ana_prev = _now
                        _astep = _dt * 12.0
                        if _ana_action_btns < _ana_action_btns_target:
                            _ana_action_btns = min(_ana_action_btns + _astep, _ana_action_btns_target)
                        elif _ana_action_btns > _ana_action_btns_target:
                            _ana_action_btns = max(_ana_action_btns - _astep, _ana_action_btns_target)
                        if _ana_sub_visible < _ana_sub_visible_target:
                            _ana_sub_visible = min(_ana_sub_visible + _astep, _ana_sub_visible_target)
                        elif _ana_sub_visible > _ana_sub_visible_target:
                            _ana_sub_visible = max(_ana_sub_visible - _astep, _ana_sub_visible_target)
                        # Delete popup animation
                        if _ana_del_popup_t < _ana_del_popup_target:
                            _ana_del_popup_t = min(_ana_del_popup_t + _astep * 0.5, _ana_del_popup_target)
                        elif _ana_del_popup_t > _ana_del_popup_target:
                            _ana_del_popup_t = max(_ana_del_popup_t - _astep * 0.5, _ana_del_popup_target)
                        if _ana_del_popup_open:
                            _ana_del_popup_pulse += _dt * 4.0
                        if _ana_del_popup_flash > 0:
                            _ana_del_popup_flash = max(0.0, _ana_del_popup_flash - _dt * 3.0)
                            if _ana_del_popup_flash <= 0.1 and _ana_del_pending:
                                _ana_del_popup_target = 0.0

                        w, h = console.size
                        _ana_last_panel = _build_project_list_screen(
                            [],
                            _ana_selected,
                            width=w,
                            height=h,
                            jira_enabled=_jira_ok,
                            azdevops_enabled=_azdevops_ok,
                            profiles=_profiles_for_analysis,
                            new_analysis_labels=_ana_labels,
                            profile_focus=_ana_focus,
                            profile_del_fade=_ana_del_fade,
                            profile_card_fade=1.0,
                            profile_action_btns_visible=_ana_action_btns,
                            profile_exp_fade=_ana_exp_fade,
                            profile_export_submenu=_ana_export_submenu,
                            profile_submenu_sel=_ana_sub_sel,
                            profile_submenu_html_fade=_ana_sub_html_fade,
                            profile_submenu_md_fade=_ana_sub_md_fade,
                            profile_submenu_visible=_ana_sub_visible,
                            delete_popup_name=_ana_del_popup_name,
                            delete_popup_t=_ana_del_popup_t,
                            delete_popup_pulse=_ana_del_popup_pulse,
                            delete_popup_flash=_ana_del_popup_flash,
                            mode="analysis",
                            shimmer_tick=_now - _ana_anim0,
                        )
                        live.update(_ana_last_panel)

                    if _restart_mode_select:
                        break  # break out of _ana_restart loop → back to mode select

                    # Run team analysis (reuse Phase 3a logic)
                    if _team_popup_result.startswith("analyse"):
                        import threading

                        from yeaboi.analysis import run_team_analysis
                        from yeaboi.analysis.engine import (
                            AnalysisCancelledError,
                            _available_doc_sources,
                            _available_sources,
                            _offerable_code_sources,
                        )

                        # Unified component grid: each component picks its OWN configured
                        # sub-sources (delivery \u2190 jira/azdevops, code \u2190 github/azdo, docs
                        # \u2190 confluence/notion). The Code row lists hosts the wizard can
                        # SCOPE, not only ones already scoped in config \u2014 GitHub owners are
                        # discovered in-wizard, so a bare token is enough to offer it. The
                        # wizard owns Esc-back navigation; backing out of its first step
                        # returns to the analysis screen.
                        _ta_setup = _run_analysis_setup_wizard(
                            live,
                            console,
                            read_key,
                            _FRAME_TIME,
                            _supports_timeout,
                            grid={
                                "delivery": _available_sources(),
                                "code": _offerable_code_sources(),
                                "docs": _available_doc_sources(),
                            },
                            roster_fallback=_available_sources(),
                            project_key="",
                            db_path=_ana_dbp,
                        )
                        if _ta_setup is None:
                            _ana_restart = True
                            _team_popup_result = ""
                            continue
                        _ta_features = _ta_setup["features"]
                        _ta_components = _ta_setup["components"]
                        _ta_analysis_scope = _ta_setup["analysis_scope"]
                        _ta_depth = _ta_setup["depth"]
                        _ta_analysis_model = _ta_setup["model"]
                        _ta_window_days = _ta_setup["window_days"]
                        _ta_members_map = _ta_setup["members_map"]
                        _ta_dlv = _ta_components.get("delivery") or []
                        _ta_disp_source = _ta_dlv[0] if _ta_dlv else "analysis"

                        _ta_progress: list = []
                        _ta_profile_box: list = [None]
                        _ta_examples_box: list = [None]
                        _ta_sprint_names_box: list = [[]]
                        _ta_result_box: list = [None]  # full engine dict (carries 'both' results)
                        _ta_error_box: list[str] = [""]
                        _ta_done = threading.Event()
                        _ta_cancel_event = threading.Event()

                        def _run_team_analysis_mode():
                            try:
                                # One code path with CLI/MCP: the engine fetches,
                                # analyses, saves the profile, and writes the log.
                                _res = run_team_analysis(
                                    analysis_depth=_ta_depth,
                                    analysis_window_days=_ta_window_days,
                                    components=_ta_components,
                                    members=_ta_members_map,
                                    analysis_scope=_ta_analysis_scope or None,
                                    analysis_model=_ta_analysis_model,
                                    analysis_features=_ta_features,
                                    progress=_ta_progress,
                                    db_path=_ana_dbp,
                                    cancel_event=_ta_cancel_event,
                                )
                                _ta_result_box[0] = _res
                                # Seed the boxes with the first delivery tracker (the
                                # initially-shown source); code/docs-only runs have no
                                # delivery profile, so seed None.
                                _dlv = _res.get("delivery") or {}
                                _first = next(iter(_dlv.values())) if _dlv else {}
                                _ta_profile_box[0] = _first.get("profile")
                                _ta_examples_box[0] = _first.get("examples") or {}
                                _ta_sprint_names_box[0] = _first.get("sprint_names") or []
                            except AnalysisCancelledError:
                                pass  # cancelled — boxes stay empty; the poll loop owns the notice
                            except ValueError as exc:
                                _ta_error_box[0] = str(exc)
                            except Exception as exc:
                                from yeaboi.ui.session._utils import _classify_api_error

                                _ta_error_box[0] = _classify_api_error(exc)
                            finally:
                                _ta_done.set()

                        _ta_thread_start = time.monotonic()
                        _ta_thread = duck_working_thread(_run_team_analysis_mode, name="team-analysis")
                        logger.info("Analysis: starting analysis (components=%s)", _ta_components)
                        _ta_thread.start()

                        from yeaboi.ui.mode_select.screens._screens_secondary import (
                            _build_analysis_progress_screen,
                        )

                        _ta_anim_tick = 0.0
                        _ta_cancelled = False
                        try:
                            while not _ta_done.is_set():
                                _ta_anim_tick += _FRAME_TIME
                                w, h = console.size
                                live.update(
                                    _build_analysis_progress_screen(
                                        _ta_progress,
                                        width=w,
                                        height=h,
                                        elapsed=time.monotonic() - _ta_thread_start,
                                        anim_tick=_ta_anim_tick,
                                        source=_ta_disp_source,
                                        mode="analysis",
                                    )
                                )
                                time.sleep(_FRAME_TIME)
                        except KeyboardInterrupt:
                            # First Ctrl-C: cooperative cancel. The bounded wait below is
                            # NOT wrapped, so a second Ctrl-C re-raises out of select_mode
                            # and quits the app via cli.py's existing handler.
                            logger.info("Analysis: Ctrl-C received — cancelling run")
                            _ta_cancel_event.set()
                            _ta_progress.append("Cancelling — waiting for running work to stop…")
                            _ta_deadline = time.monotonic() + 10.0
                            while not _ta_done.is_set() and time.monotonic() < _ta_deadline:
                                _ta_anim_tick += _FRAME_TIME
                                w, h = console.size
                                live.update(
                                    _build_analysis_progress_screen(
                                        _ta_progress,
                                        width=w,
                                        height=h,
                                        elapsed=time.monotonic() - _ta_thread_start,
                                        anim_tick=_ta_anim_tick,
                                        source=_ta_disp_source,
                                        mode="analysis",
                                    )
                                )
                                time.sleep(_FRAME_TIME)
                            _ta_cancelled = True
                        # Daemon thread — abandoned after the bounded wait if a job is
                        # still busy; the engine's pre-persist gate still guarantees
                        # nothing is saved once the cancel event is set.
                        _ta_thread.join(timeout=0.1 if _ta_cancelled else None)

                        if _ta_cancelled:
                            w, h = console.size
                            live.update(
                                _build_project_export_success_screen(
                                    "Analysis cancelled — no results were saved.",
                                    width=w,
                                    height=h,
                                    subtitle="Analysis cancelled",
                                    hint="Press any key to continue.",
                                    mode="analysis",
                                )
                            )
                            while True:
                                _k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                if _k:
                                    break
                            _ana_restart = True
                            _team_popup_result = ""
                            continue

                        _ta_profile = _ta_profile_box[0]
                        _ta_duration = time.monotonic() - _ta_thread_start
                        if _ta_profile:
                            logger.info(
                                "Analysis completed in %.1fs: %d sprints, %d stories, vel=%.1f",
                                _ta_duration,
                                _ta_profile.sample_sprints,
                                _ta_profile.sample_stories,
                                _ta_profile.velocity_avg,
                            )
                        elif _ta_error_box[0]:
                            logger.error("Analysis failed: %s", _ta_error_box[0])
                        # Show results whenever the engine returned anything (a delivery-off
                        # run has no top-level profile but still has code/docs cards).
                        if _ta_result_box[0] and not _ta_error_box[0]:
                            # Persist + analysis log already handled inside
                            # run_team_analysis (one code path with CLI/MCP).
                            _duck_react("analysis_done")

                            # Show results (overview + section cards). In 'both'
                            # mode the loop toggles between the two trackers and
                            # reports the selected one back via _ta_active_box.
                            _ta_examples = _ta_examples_box[0] or {}
                            _ta_sprint_names = _ta_sprint_names_box[0]
                            _ta_team_name = ""
                            _ta_src = getattr(_ta_profile, "source", "") or _ta_disp_source
                            _ta_sub = f"{_ta_src}/{getattr(_ta_profile, 'project_key', '')}"
                            _ta_full = _ta_result_box[0] or {}
                            while True:
                                _ta_active_box: list = [None]
                                _res = _run_team_analysis_results(
                                    live,
                                    console,
                                    read_key,
                                    _FRAME_TIME,
                                    _supports_timeout,
                                    _ta_profile,
                                    _ta_examples,
                                    sprint_names=_ta_sprint_names,
                                    team_name=_ta_team_name,
                                    delivery=_ta_full.get("delivery"),
                                    code=_ta_full.get("code"),
                                    docs=_ta_full.get("docs"),
                                    comparison=_ta_full.get("comparison"),
                                    analysis_features=_ta_full.get("analysis_features"),
                                    active_box=_ta_active_box,
                                    source=_ta_disp_source,
                                    retry_config={
                                        "source": "",
                                        "project_key": "",
                                        "team_name": "",
                                        "analysis_depth": _ta_depth,
                                        "analysis_window_days": _ta_window_days,
                                        "analysis_scope": _ta_analysis_scope or None,
                                        "analysis_model": _ta_analysis_model,
                                        "components": _ta_components,
                                        "members": _ta_members_map,
                                        "db_path": _ana_dbp,
                                    },
                                )
                                # Downstream insights/ticket steps operate on the
                                # delivery tracker the user last viewed.
                                if _ta_active_box[0] is not None:
                                    _ta_profile, _ta_examples, _ta_sprint_names, _ta_team_name = _ta_active_box[0]
                                    _ta_src = getattr(_ta_profile, "source", "") or _ta_disp_source
                                    _ta_sub = f"{_ta_src}/{getattr(_ta_profile, 'project_key', '')}"
                                if _res != "continue":
                                    break

                                # Coaching insights before suggesting sample
                                # tickets; Back returns to the results overview.
                                if (
                                    _run_team_insights(
                                        live,
                                        console,
                                        read_key,
                                        _FRAME_TIME,
                                        _supports_timeout,
                                        _ta_profile,
                                        _ta_examples,
                                        sprint_names=_ta_sprint_names,
                                    )
                                    == "back"
                                ):
                                    continue

                                global _ana_sid  # noqa: PLW0603

                                # Ask before generating tickets — separate the
                                # team/board analysis from ticket creation.
                                if _confirm_ticket_generation(
                                    live,
                                    console,
                                    read_key,
                                    _FRAME_TIME,
                                    _supports_timeout,
                                    subtitle=_ta_sub,
                                ):
                                    from yeaboi.agent.nodes import _format_team_calibration
                                    from yeaboi.sessions import SessionStore as _AStore
                                    from yeaboi.sessions import make_session_id

                                    _ana_sid = make_session_id()
                                    try:
                                        with _AStore(_ana_dbp) as _as:
                                            _as.create_session(
                                                _ana_sid,
                                                _ta_profile.project_key if _ta_profile else "",
                                                mode="analysis",
                                            )
                                    except Exception:
                                        pass

                                    _instr_text = _format_team_calibration(
                                        _ta_profile,
                                        examples=_ta_examples,
                                    )
                                    if _instr_text.strip():
                                        _run_preview_flow(
                                            live,
                                            console,
                                            read_key,
                                            _FRAME_TIME,
                                            _supports_timeout,
                                            _instr_text,
                                            _ta_profile,
                                            _ta_examples,
                                            resume_state=None,
                                        )
                                break
                        elif _ta_error_box[0]:
                            w, h = console.size
                            live.update(
                                _build_project_export_success_screen(
                                    _ta_error_box[0],
                                    width=w,
                                    height=h,
                                    subtitle="Analysis failed",
                                    hint="Press any key to continue.",
                                    mode="analysis",
                                )
                            )
                            while True:
                                k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                if k:
                                    break

                        # Reload profiles and restart analysis list
                        try:
                            from datetime import UTC, datetime

                            from yeaboi.team_profile import TeamProfileStore

                            _tp_db = _ana_dbp
                            if _tp_db.exists():
                                with TeamProfileStore(_tp_db) as _tp_s:
                                    _raw2 = _tp_s.list_profiles()
                                _profiles_for_analysis = []
                                for _rp in _raw2:
                                    days = 0
                                    if _rp.updated_at:
                                        try:
                                            _up = datetime.fromisoformat(_rp.updated_at)
                                            days = (datetime.now(UTC) - _up).days
                                        except Exception:
                                            pass
                                    _profiles_for_analysis.append(
                                        ProfileSummary(
                                            team_id=_rp.team_id,
                                            source=_rp.source,
                                            project_key=_rp.project_key,
                                            sample_sprints=_rp.sample_sprints,
                                            velocity_avg=_rp.velocity_avg,
                                            sample_stories=_rp.sample_stories,
                                            updated="today"
                                            if days == 0
                                            else (f"{days} day{'s' if days != 1 else ''} ago"),
                                            staleness_days=days,
                                        )
                                    )
                        except Exception:
                            pass
                        _ana_n = len(_profiles_for_analysis) + len(_ana_labels)
                        _ana_selected = 0
                        _ana_restart = True
                        _team_popup_result = ""
                        continue

                    # Esc from analysis list → back to mode select
                    _restart_mode_select = True
                    _skip_fade_in = True

                # Always return to mode select after analysis mode exits
                detach_mode_handler("analysis")
                continue

            # 2d: Smooth fade-in — all cards appear together, opacity 0→1
            # See docs: "Memory & State" — load persisted project history
            # (planning projects + saved roadmaps in one merged list)
            projects = _load_planning_rows()
            proj_selected = 0
            if projects:
                proj_n = len(projects) + 1
            else:
                proj_n = 2

            # Check which trackers are configured — used to show/dim submenu buttons.
            from yeaboi.azdevops_sync import is_azdevops_board_configured as _azdevops_check
            from yeaboi.jira_sync import is_jira_configured as _jira_check

            _jira_ok = _jira_check()
            _azdevops_ok = _azdevops_check()
            # Submenu has HTML(0), Markdown(1), then tracker buttons dynamically
            _submenu_max = 1 + (1 if _jira_ok else 0) + (1 if _azdevops_ok else 0)

            # Check team profile staleness for the popup on "+ New Project"
            _board_configured = _jira_ok or _azdevops_ok
            _staleness_days: int | None = None
            if _board_configured:
                try:
                    from yeaboi.team_profile import TeamProfileStore

                    _tp_db = _ana_dbp
                    if _tp_db.exists():
                        with TeamProfileStore(_tp_db) as _tp_store:
                            _tp_profiles = _tp_store.list_profiles()
                        # Filter to profiles matching the configured board(s)
                        _matching_profiles = []
                        for _tpp in _tp_profiles:
                            if _jira_ok and _tpp.source == "jira":
                                _matching_profiles.append(_tpp)
                            elif _azdevops_ok and _tpp.source == "azdevops":
                                _matching_profiles.append(_tpp)
                        if _matching_profiles:
                            from datetime import UTC
                            from datetime import datetime as _dt

                            _latest = _matching_profiles[0]
                            if _latest.updated_at:
                                try:
                                    _up = _dt.fromisoformat(_latest.updated_at)
                                    _staleness_days = (_dt.now(UTC) - _up).days
                                except Exception:
                                    pass
                except Exception:
                    pass
            logger.info(
                "Board config: jira=%s, azdevops=%s, staleness_days=%s",
                _jira_ok,
                _azdevops_ok,
                _staleness_days,
            )

            # ── Route: Daily Standup mode → dashboard + actions ──────────
            if chosen["key"] == "daily-standup":
                logger.info("Daily Standup mode selected")
                # Route all records to logs/standup/standup.log while the page runs.
                with mode_log("standup"):
                    _run_standup_hub(console, live, read_key, _FRAME_TIME, _supports_timeout)
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Retro mode → collaborative board page ─────────────
            if chosen["key"] == "retro":
                logger.info("Retro mode selected")
                with mode_log("retro"):
                    _run_retro_hub(console, live, read_key, _FRAME_TIME, _supports_timeout)
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Poker mode → collaborative estimation page ────────
            if chosen["key"] == "poker":
                logger.info("Poker mode selected")
                with mode_log("poker"):
                    _run_poker_hub(console, live, read_key, _FRAME_TIME, _supports_timeout)
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Performance mode → per-engineer dashboard ─────────
            if chosen["key"] == "performance":
                logger.info("Performance mode selected")
                with mode_log("performance"):
                    # Beta gate first: the mode drafts material about named
                    # people, so the caveat comes before the roster, not after.
                    # Shown once ever; a decline returns to the menu unrecorded.
                    if show_beta_notice(
                        live, console, read_key, _FRAME_TIME, _supports_timeout, mode_key="performance"
                    ):
                        _run_performance_page(console, live, read_key, _FRAME_TIME, _supports_timeout)
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Reporting mode → delivery-report page ─────────────
            if chosen["key"] == "reporting":
                logger.info("Reporting mode selected")
                with mode_log("reporting"):
                    _run_reporting_hub(console, live, read_key, _FRAME_TIME, _supports_timeout)
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Ship mode → supervised story → PR pipeline ────────
            if chosen["key"] == "ship":
                logger.info("Ship mode selected")
                with mode_log("ship"):
                    # Beta gate first: the mode launches a coding agent against
                    # the user's own repository, so the caveat comes before the
                    # story picker. Shown once ever; a decline returns to the
                    # menu unrecorded.
                    if show_beta_notice(live, console, read_key, _FRAME_TIME, _supports_timeout, mode_key="ship"):
                        from yeaboi.ui.mode_select._ship import run_ship_page

                        run_ship_page(console, live, read_key, _FRAME_TIME, _supports_timeout)
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Usage mode → single-page dashboard ────────────────
            if chosen["key"] == "usage":
                logger.info("Usage mode selected")
                from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

                _usage_data = _collect_usage_data()
                _u_scroll, _u_sel = 0, 0
                _u_scroll_meta: dict = {}
                _u_actions = ["Copy", "Back"]
                _u_message = ""
                _u_anim_start = time.monotonic()  # shimmer title + typewriter subtitle
                w, h = console.size
                live.update(
                    _build_usage_screen(
                        _usage_data,
                        scroll_offset=_u_scroll,
                        scroll_meta=_u_scroll_meta,
                        width=w,
                        height=h,
                        action_sel=_u_sel,
                        shimmer_tick=0.0,
                        sub_reveal=0.0,
                        actions=_u_actions,
                        message=_u_message,
                    )
                )
                logger.info("Usage page opened")
                while True:
                    k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                    if k in SCROLL_KEYS:
                        _ns = coalesce_scroll(_u_scroll, k, _u_scroll_meta, read_key)
                        if _ns == _u_scroll:
                            continue
                        _u_scroll = _ns
                    elif k in ("c", "C"):  # copy the usage report to the clipboard
                        from yeaboi.clipboard import copy_markdown_status
                        from yeaboi.usage_export import build_usage_text

                        logger.info("Usage: Copy pressed")
                        _u_message = copy_markdown_status(build_usage_text(_usage_data))
                        from yeaboi.ui.shared._duck_voice import duck_voice

                        duck_voice().say(_u_message)  # the duck speaks the copy status
                    elif k in ("esc", "q"):
                        break
                    w, h = console.size
                    _u_elapsed = time.monotonic() - _u_anim_start
                    live.update(
                        _build_usage_screen(
                            _usage_data,
                            scroll_offset=_u_scroll,
                            scroll_meta=_u_scroll_meta,
                            width=w,
                            height=h,
                            action_sel=_u_sel,
                            shimmer_tick=_u_elapsed,
                            sub_reveal=_u_elapsed * _HEADER_SUB_SPEED,
                            actions=_u_actions,
                            message=_u_message,
                        )
                    )
                logger.info("Usage page closed")
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Settings mode → config viewer + setup wizard ────────
            if chosen["key"] == "settings":
                logger.info("Settings mode selected")
                from yeaboi.ui.mode_select.screens._screens_secondary import (
                    _SETTINGS_TABS,
                    _build_settings_screen,
                    settings_focus_move,
                    settings_tab_action,
                )
                from yeaboi.ui.shared._duck_voice import duck_voice as _settings_voice

                _settings_data = _collect_settings_data()
                _s_scroll, _s_tab = 0, 0
                _n_tabs = len(_SETTINGS_TABS)
                _s_scroll_meta: dict = {}
                _s_edit: dict | None = None  # in-place row editor: {env, label, masked, buf, cur}
                _s_anim_start = time.monotonic()  # shimmer title + typewriter subtitle
                _tab_pos = float(_s_tab)  # eased fractional tab index → the sliding underline
                # Keyboard focus level: (-1, -1) = the tab bar, (b, -1) = section box b,
                # (b, f) = value f inside it. See _build_settings_screen's docstring.
                _s_box, _s_field = -1, -1

                def _render_settings(tick: float) -> object:
                    nonlocal _tab_pos, _s_scroll
                    w, h = console.size
                    _tab_pos += (_s_tab - _tab_pos) * 0.28  # ease the underline toward the active tab
                    _editing = (_s_edit["env"], _s_edit["buf"], _s_edit["cur"]) if _s_edit else None
                    panel = _build_settings_screen(
                        _settings_data,
                        scroll_offset=_s_scroll,
                        scroll_meta=_s_scroll_meta,
                        width=w,
                        height=h,
                        active_tab=_s_tab,
                        tab_pos=_tab_pos,
                        shimmer_tick=tick,
                        sub_reveal=tick * _HEADER_SUB_SPEED,
                        editing=_editing,
                        sel_box=_s_box,
                        sel_field=_s_field,
                    )
                    # The builder scrolls the focused box/value into view; adopt the
                    # offset it settled on so the next manual scroll starts from there.
                    _s_scroll = _s_scroll_meta.get("scroll", _s_scroll)
                    live.update(panel)
                    return panel

                def _s_fields_of(b: int) -> list:
                    """The editable (env, label, masked) rows of section ``b``, per the
                    last render — empty when the index is stale (the data can change)."""
                    _bf = getattr(_s_panel, "_box_fields", []) or []
                    return _bf[b] if 0 <= b < len(_bf) else []

                def _s_begin_edit(env: str, label: str, masked: bool) -> None:
                    """Open the in-place editor for a field.

                    Every value is typed on the page itself, the data directory
                    included — its move-or-leave decision happens on save (see
                    _settings_save_data_dir), not on a screen of its own.
                    """
                    nonlocal _s_edit, _settings_data
                    if env == "YEABOI_SHARE_MODE":
                        # Turning this on is five steps against Cloudflare, not a
                        # word to type — so Enter opens the wizard, the way
                        # VOICE_DEVICE opens a picker. Turning it *off* stays a
                        # one-key answer: a host who wants their boards back on
                        # the zero-setup tier should not walk a setup flow to
                        # say so.
                        from yeaboi.config import SHARE_MODE_ACCESS, SHARE_MODE_QUICK, apply_config_value
                        from yeaboi.ui.shared._components import SETTINGS_THEME, settings_title

                        _pick = _run_schedule_choice_step(
                            console,
                            live,
                            read_key,
                            _FRAME_TIME,
                            _supports_timeout,
                            options=[
                                ("Set up verified users…", "Cloudflare Access — only people you name get in"),
                                ("Quick tunnels", "the default: a random URL and a join code"),
                            ],
                            initial=0 if _settings_data.get(env, "") == SHARE_MODE_ACCESS else 1,
                            step_index=0,
                            heading="How should shared boards be protected?",
                            step_names=["Choose"],
                            theme=SETTINGS_THEME,
                            title_fn=settings_title,
                        )
                        if _pick == "back":
                            return
                        if _pick == 1:
                            apply_config_value(env, SHARE_MODE_QUICK)
                            _settings_data = _collect_settings_data()
                            _settings_data["_message"] = "Shared boards use quick tunnels."
                            logger.info("Settings: YEABOI_SHARE_MODE set to quick")
                            return
                        _result = _run_access_setup(console, live, read_key, _FRAME_TIME, _supports_timeout)
                        _settings_data = _collect_settings_data()
                        if _result:
                            _settings_data["_message"] = _result
                            _settings_voice().say(_result)
                        return
                    if env == "VOICE_DEVICE":
                        # A device list is a choice, not free text — you cannot type a
                        # name you have not seen. Returns before set_text_entry(True),
                        # so the picker keeps the app-wide bare-key bindings.
                        _picked = _pick_voice_device(console, live, read_key, _FRAME_TIME, _supports_timeout)
                        if _picked is None:
                            return
                        from yeaboi.config import apply_config_value

                        apply_config_value(env, _picked)
                        _settings_data = _collect_settings_data()
                        _msg = f"Microphone set to {_picked}" if _picked else "Microphone: using the system default"
                        _settings_data["_message"] = _msg
                        _settings_voice().say(_msg)
                        logger.info("Settings: VOICE_DEVICE set to %r", _picked)
                        return
                    from yeaboi.ui.mode_select.screens._screens_secondary import SETTINGS_ACTION_ENVS

                    if env in SETTINGS_ACTION_ENVS:
                        # `claude setup-token` is interactive, but it runs on a pty
                        # this process owns rather than on the real terminal — so the
                        # TUI never goes away. The renderer goes with it, so these
                        # settings keep drawing underneath while the duck's bubble
                        # carries the flow (see yeaboi.claude_auth).
                        logger.info("Settings: running subscription sign-in for %s", env)
                        _tok, _msg = _run_subscription_sign_in(
                            console,
                            live,
                            read_key,
                            _FRAME_TIME,
                            _supports_timeout,
                            lambda: _render_settings(time.monotonic() - _s_anim_start),
                        )
                        if _tok:
                            from yeaboi.auth_state import clear_subscription_stale
                            from yeaboi.config import apply_config_value

                            apply_config_value(env, _tok)
                            clear_subscription_stale()  # the warning is answered
                        _settings_data = _collect_settings_data()
                        _settings_data["_message"] = _msg
                        _settings_voice().say(_msg)
                        return
                    # A fixed-choice row has no editor either: Enter steps to the next
                    # option and saves it outright. Routed through the ordinary commit
                    # path so the write, the environment update, the status line and
                    # the logging are all the same code the typed fields use.
                    from yeaboi.ui.mode_select.screens._screens_secondary import (
                        SETTINGS_CHOICES,
                        settings_choice_value,
                    )

                    _choices = SETTINGS_CHOICES.get(env)
                    if _choices:
                        # Step on from the option the ROW is lighting, resolved by the
                        # same helper the builder uses. Reading the raw stored value
                        # here instead is how "unset" lit WARNING while Enter jumped
                        # to INFO — an unset var is not the same as the first option.
                        _at = _choices.index(settings_choice_value(env, _settings_data.get(env, ""))) + 1
                        _s_edit = {
                            "env": env,
                            "label": label,
                            "masked": False,
                            "buf": _choices[_at % len(_choices)],
                            "cur": 0,
                        }
                        _s_commit_edit()
                        return
                    # Hidden fields start blank (type a new value); others start at the
                    # current value so you edit in place.
                    _start = "" if masked else (_settings_data.get(env, "") or "")
                    _s_edit = {"env": env, "label": label, "masked": masked, "buf": _start, "cur": len(_start)}
                    set_text_entry(True)  # 'c' types a 'c' now, not the controls drawer
                    logger.info("Settings: editing %s", env)

                def _s_commit_edit() -> None:
                    """Save the open in-place edit — the Enter path, also used when a
                    click moves straight to another row (clicking away commits, the
                    way a form field blurs). No-op when nothing is being edited."""
                    nonlocal _s_edit, _settings_data
                    if _s_edit is None:
                        return
                    set_text_entry(False)
                    _env, _label, _masked = _s_edit["env"], _s_edit["label"], _s_edit["masked"]
                    _val = _s_edit["buf"].strip()
                    _cur_val = _settings_data.get(_env, "")
                    _s_edit = None
                    _save = True
                    if _val == "-":
                        _val = ""  # explicit clear
                    elif _masked and _val == "":
                        _save = False  # empty on a hidden field = keep the value
                    if _save and not _masked and _val == (_cur_val or ""):
                        _save = False  # unchanged
                    if _save and _env == "YEABOI_ALLOWED_PATHS":
                        # The whitelist has its own setter (dedup + pinned .env).
                        _ap_msg = _settings_save_allowed_paths(_val)
                        _settings_data = _collect_settings_data()
                        _settings_data["_message"] = _ap_msg
                        _settings_voice().say(_ap_msg)  # the duck speaks the save status
                    elif _save and _env == "YEABOI_HOME":
                        # Relocating the tree needs a move-or-leave answer and a write
                        # to the pinned bootstrap .env, so it saves through its own
                        # helper rather than the generic path.
                        _dd_msg = _settings_save_data_dir(console, live, read_key, _FRAME_TIME, _supports_timeout, _val)
                        _settings_data = _collect_settings_data()
                        _settings_data["_message"] = _dd_msg
                        _settings_voice().say(_dd_msg)
                    elif _save:
                        from yeaboi.config import apply_config_value

                        # apply_ (not set_) so the edit lands in os.environ too — the
                        # page re-reads the environment, so a file-only write wouldn't
                        # show until a restart.
                        apply_config_value(_env, _val)
                        if _env == "DUCK_ENABLED":
                            # Apply the mute now — duck_muted() caches the flag,
                            # so a file-only write wouldn't show until restart.
                            from yeaboi.ui.shared._duck_voice import set_duck_muted

                            set_duck_muted(_val.strip().lower() == "false")
                        if _env == "LOG_LEVEL" and _val:
                            from yeaboi.logging_setup import apply_level

                            try:
                                apply_level(_val)
                            except Exception:  # noqa: BLE001 - a bad level shouldn't crash settings
                                logger.debug("apply_level failed for %r", _val, exc_info=True)
                        _settings_data = _collect_settings_data()
                        _settings_data["_message"] = f"{_label} {'cleared' if not _val else 'updated'}"
                        _settings_voice().say(_settings_data["_message"])
                        logger.info("Settings: %s %s", _env, "cleared" if not _val else "updated")

                _s_panel = _render_settings(0.0)
                while True:
                    sk = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                    # ── In-place edit mode: keystrokes go to the field being edited ──
                    if _s_edit is not None:
                        _edit_click = parse_click(sk)
                        if _edit_click is not None:
                            # Clicking straight onto another row (or a tab) commits what
                            # was typed and routes normally below — no Esc round trip.
                            # A click on empty space leaves the edit alone rather than
                            # committing a half-typed value by accident.
                            _ecx, _ecy = _edit_click
                            _lands = any(
                                _ecy == _rr and _rx0 <= _ecx <= _rx1
                                for _rr, _rx0, _rx1, *_rest in getattr(_s_panel, "_row_regions", [])
                            ) or any(
                                _ecy in (_lr, _ur) and _sc <= _ecx <= _ec
                                for _lr, _ur, _sc, _ec in getattr(_s_panel, "_tab_regions", [])
                            )
                            if not _lands:
                                continue
                            _s_commit_edit()
                        elif sk == "enter":
                            _s_commit_edit()
                            _s_panel = _render_settings(time.monotonic() - _s_anim_start)
                            continue
                        elif sk == "esc" and esc_came_from_back_tab():
                            # The back BUTTON means leave, not "unwind one level at a
                            # time" — clicking it three times to get out is not a
                            # back button. Commit like any other click away, then go.
                            _s_commit_edit()
                            _s_box, _s_field = -1, -1
                            logger.info("Settings: back tab clicked")
                            break
                        elif sk == "esc":
                            # Esc alone cancels: 'q' is a character you have to be able
                            # to type (an Ollama model name starts with one). The edit
                            # eats the key, so the back tab must not fold away — the
                            # Esc chokepoint armed its retract before we got a say.
                            from yeaboi.ui.shared._music_bar import cancel_back_retract

                            cancel_back_retract()
                            set_text_entry(False)
                            _s_edit = None  # cancel — discard the buffer
                            _s_panel = _render_settings(time.monotonic() - _s_anim_start)
                            continue
                        else:
                            _settings_edit_keypress(sk, _s_edit)  # mutate buffer/cursor
                            _s_panel = _render_settings(time.monotonic() - _s_anim_start)
                            continue

                    _s_click = parse_click(sk)
                    if _s_click is not None:
                        _cx, _cy = _s_click
                        # Click a tab (its label or the underline) → switch to it.
                        _hit_tab = False
                        for _i, (_lr, _ur, _sc, _ec) in enumerate(getattr(_s_panel, "_tab_regions", [])):
                            if _cy in (_lr, _ur) and _sc <= _cx <= _ec:
                                if _i != _s_tab:
                                    _s_tab, _s_scroll = _i, 0
                                    _s_box, _s_field = -1, -1
                                _hit_tab = True
                                break
                        # Otherwise, click an editable config row → edit it in place.
                        if not _hit_tab:
                            # Sections are boxed side by side now, so two editable rows
                            # can share a terminal row — the column range disambiguates.
                            for _rr, _rx0, _rx1, _env, _label, _masked in getattr(_s_panel, "_row_regions", []):
                                if _cy != _rr or not (_rx0 <= _cx <= _rx1):
                                    continue
                                # Move keyboard focus onto whatever was clicked, so Esc
                                # lands back on that section rather than the tab bar.
                                for _bi, _fields in enumerate(getattr(_s_panel, "_box_fields", [])):
                                    _hit = [_fi for _fi, _f in enumerate(_fields) if _f[0] == _env]
                                    if _hit:
                                        _s_box, _s_field = _bi, _hit[0]
                                        break
                                _s_begin_edit(_env, _label, _masked)
                                break
                    elif sk in ("up", "down", "left", "right") and (_s_box >= 0 or sk == "down"):
                        # Arrows drive focus: between values inside an opened section,
                        # otherwise between the section boxes themselves. Left/Right at
                        # the tab-bar level fall through to the tab switch below.
                        _s_box, _s_field = settings_focus_move(
                            sk,
                            getattr(_s_panel, "_box_cols", []) or [],
                            getattr(_s_panel, "_box_tail", []) or [],
                            getattr(_s_panel, "_box_fields", []) or [],
                            _s_box,
                            _s_field,
                        )
                    elif sk in SCROLL_KEYS:
                        _ns = coalesce_scroll(_s_scroll, sk, _s_scroll_meta, read_key)
                        if _ns == _s_scroll:
                            continue
                        _s_scroll = _ns
                    elif sk == "left":
                        _s_tab, _s_scroll = (_s_tab - 1) % _n_tabs, 0
                    elif sk in ("right", "tab"):
                        _s_tab, _s_scroll, _s_box, _s_field = (_s_tab + 1) % _n_tabs, 0, -1, -1
                    elif sk in ("enter", " ") and _s_box >= 0:
                        _fields = _s_fields_of(_s_box)
                        if 0 <= _s_field < len(_fields):
                            _env, _label, _masked = _fields[_s_field]
                            _s_begin_edit(_env, _label, _masked)
                        elif _fields:
                            _s_field = 0  # open the section — arrows now walk its values
                    elif sk == "esc" and _s_box >= 0 and not esc_came_from_back_tab():
                        # Pops one focus level instead of leaving, so the app-wide back
                        # tab (already armed by the Esc chokepoint) must stay put.
                        from yeaboi.ui.shared._music_bar import cancel_back_retract

                        cancel_back_retract()
                        if _s_field >= 0:
                            _s_field = -1
                        else:
                            _s_box = -1
                    elif sk in ("enter", " "):
                        _act = settings_tab_action(_s_tab)
                        # 'datadir' is gone as a tab action: Storage folded into System,
                        # and YEABOI_HOME is opened as a row like every other value
                        # (see _s_begin_edit, which still routes it to the move flow).
                        if _act == "loglevel":
                            # Advanced tab → cycle log level, persist to .env, apply live.
                            from yeaboi.config import get_log_level, set_log_level
                            from yeaboi.logging_setup import apply_level

                            _new_level = _next_log_level(get_log_level())
                            set_log_level(_new_level)
                            apply_level(_new_level)
                            _settings_data = _collect_settings_data()
                            logger.info("Settings: log level cycled to %s", _new_level)
                        elif _act == "sharing":
                            logger.info("Settings: launching Cloudflare Access setup from the Sharing tab")
                            _result = _run_access_setup(console, live, read_key, _FRAME_TIME, _supports_timeout)
                            _settings_data = _collect_settings_data()
                            if _result:
                                _settings_data["_message"] = _result
                        else:
                            # Every other tab → the setup wizard configures it.
                            logger.info("Settings: launching setup wizard (%s)", _SETTINGS_TABS[_s_tab])
                            _launch_setup_wizard(console, live)
                            _settings_data = _collect_settings_data()
                        _s_box, _s_field = -1, -1  # the flow may have changed the data
                    elif sk in ("esc", "q"):
                        logger.info("Settings: user pressed Esc")
                        set_text_entry(False)  # belt and braces — never leave it latched
                        break
                    _s_panel = _render_settings(time.monotonic() - _s_anim_start)
                _restart_mode_select = True
                _skip_fade_in = True
                continue

            # ── Route: Planning mode → project list + session ────────────
            # Reached only when none of the mode branches above matched, i.e.
            # chosen["key"] == "project-planning". Runs once, before the project
            # list loop, so the intro plays a single time per Planning entry.

            # Staggered vertical reveal — cards pop in one by one, fast.
            _reveal_target = float(proj_n)
            _cards_visible = 0.0
            _reveal_speed = 15.0  # cards per second (~1 card every 4 frames)
            _reveal_start = time.monotonic()
            while _cards_visible < _reveal_target:
                dt_r = time.monotonic() - _reveal_start
                _cards_visible = min(_reveal_target, dt_r * _reveal_speed)
                w, h = console.size
                live.update(
                    _build_project_list_screen(
                        projects,
                        proj_selected,
                        width=w,
                        height=h,
                        cards_visible=_cards_visible,
                        card_fade=1.0,
                        jira_enabled=_jira_ok,
                        azdevops_enabled=_azdevops_ok,
                        shimmer_tick=dt_r,
                    )
                )
                time.sleep(_FRAME_TIME)

            # ── Phase 3: Project list interaction ─────────────────────────────
            # focus: 0 = project card, 1 = Delete button, 2 = Export button.
            # Up/Down navigates between projects (resets focus to card).
            # Left/Right navigates between card ↔ Delete ↔ Export within a row.
            # Enter activates the focused element (open project, delete, export).
            #
            # When Export is activated, a split submenu [HTML | Markdown] slides
            # out from the Export button. Left/Right switches between the two
            # halves; Enter exports; Esc closes the submenu.
            #
            # Button colour animation: buttons start grey and smoothly fade
            # to their accent colour when focused, then fade back to grey
            # when focus leaves.  del_fade_target / exp_fade_target track the
            # desired end state; del_fade / exp_fade are the animated values.
            #
            # _restart_project_list: set to True when a session ends (Esc or
            # completed) so we loop back to this point from Phase 4.
            _restart_project_list = True
            while _restart_project_list:
                _restart_project_list = False
                focus = 0
                del_fade = 0.0  # current animated value 0.0 (grey) → 1.0 (colour)
                exp_fade = 0.0
                card_fade = 1.0  # start fully visible for initially selected card
                pulse = 0.0  # one-shot white flash on Enter (decays from 1.0 → 0.0)
                del_fade_target = 0.0
                exp_fade_target = 0.0
                card_fade_target = 1.0
                fade_speed = 6.0  # units per second — full transition ≈ 0.17s

                _is_project_row = lambda: projects and proj_selected < len(projects)  # noqa: E731

                # Action buttons (Delete/Export) stagger-reveal on the selected row
                action_btns_visible = 0.0
                action_btns_visible_target = 2.0 if _is_project_row() else 0.0

                # Export submenu state — the split [HTML | Markdown | Jira] panel
                export_submenu_open = False
                submenu_sel = 0  # 0 = HTML, 1 = Markdown, 2 = Jira
                submenu_html_fade = 0.0
                submenu_md_fade = 0.0
                submenu_jira_fade = 0.0
                submenu_azdevops_fade = 0.0
                submenu_html_fade_target = 0.0
                submenu_md_fade_target = 0.0
                submenu_jira_fade_target = 0.0
                submenu_azdevops_fade_target = 0.0
                submenu_visible = 0.0
                submenu_visible_target = 0.0

                # Delete popup state — non-blocking overlay instead of full-screen modal.
                # The popup slides up from the bottom of the project list screen.
                delete_popup_open = False
                delete_popup_t = 0.0  # animated 0→1 (slide-up progress)
                delete_popup_target = 0.0  # 0.0 = hidden, 1.0 = visible
                delete_popup_name = ""
                delete_popup_pulse = 0.0  # sine-wave phase for red pulsing
                delete_popup_flash = 0.0  # white flash on confirm (1→0 decay)
                _delete_pending = False  # True after Enter confirm, delete after slide-out

                # Team analysis popup state — staleness prompt when profile >30d old
                team_popup_open = False
                team_popup_t = 0.0
                team_popup_target = 0.0
                team_popup_sel = 0  # 0 = Yes Analyse, 1 = Skip
                team_popup_pulse = 0.0
                _team_popup_result = ""  # "analyse" or "skip"
                _team_popup_msg = ""  # dynamic staleness message

                prev_tick = time.monotonic()
                _list_anim0 = prev_tick  # shimmer title clock
                _list_last_panel = None  # most recent list panel, for click hit-testing

                while True:
                    key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                    # ── Mouse: click a card to select + open it ──────────────
                    # Mirrors _build_run_hub_screen's _card_regions hit-test: map the
                    # click-y onto a card's flat index, then synthesise Enter-on-card.
                    # Only the card body activates (focus 0); edge Delete/Export buttons
                    # are not click-targets here. Ignored while a popup is modal.
                    _list_click = parse_click(key)
                    if _list_click is not None:
                        if team_popup_open or delete_popup_open:
                            continue
                        _list_hit = next(
                            (
                                idx
                                for y0, y1, idx in getattr(_list_last_panel, "_card_regions", []) or []
                                if y0 <= _list_click[1] <= y1
                            ),
                            None,
                        )
                        if _list_hit is None:
                            continue
                        proj_selected = _list_hit
                        focus = 0
                        del_fade_target = 0.0
                        exp_fade_target = 0.0
                        action_btns_visible = 0.0
                        action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                        key = "enter"  # fall through to Enter-on-card handling

                    # ── Team analysis popup mode ──────────────────────────────
                    # Button selector: Left/Right navigates, Enter confirms.
                    # When both boards configured: [Jira] [AzDO] [Skip] (3 buttons)
                    # When one board configured:   [Yes, Analyse] [Skip] (2 buttons)
                    if team_popup_open:
                        _both_boards = _jira_ok and _azdevops_ok
                        _popup_btn_count = 4 if _both_boards else 2
                        if key == "left":
                            team_popup_sel = max(0, team_popup_sel - 1)
                        elif key == "right":
                            team_popup_sel = min(_popup_btn_count - 1, team_popup_sel + 1)
                        elif key == "enter":
                            if _both_boards:
                                # 0=Jira, 1=AzDO, 2=Both, 3=Skip
                                _team_popup_result = [
                                    "analyse_jira",
                                    "analyse_azdevops",
                                    "analyse_both",
                                    "skip",
                                ][team_popup_sel]
                            else:
                                # 0=Yes, 1=Skip
                                _team_popup_result = "analyse" if team_popup_sel == 0 else "skip"
                            team_popup_target = 0.0  # slide out
                        elif key in ("esc", "q"):
                            _team_popup_result = "skip"
                            team_popup_target = 0.0

                    # ── Delete popup mode ─────────────────────────────────────
                    # When the popup is open, Enter confirms delete, Esc dismisses.
                    # All other keys are ignored so the user can't navigate away.
                    elif delete_popup_open:
                        if key == "enter":
                            # Confirm delete — white flash, THEN slide down.
                            # Setting flash to 1.0 triggers the flash phase.
                            # The slide-down only begins once the flash decays
                            # below a threshold (see animation section below).
                            delete_popup_flash = 1.0
                            _delete_pending = True
                        elif key in ("esc", "q"):
                            # Dismiss popup without deleting
                            delete_popup_target = 0.0

                    # ── Normal project list mode ───────────────────────────────
                    elif key in ("up", "scroll_up", "down", "scroll_down"):
                        # Coalesce a fast wheel/held-key burst into one net move.
                        _delta = coalesce_steps(key, read_key, down=("down", "scroll_down"), up=("up", "scroll_up"))
                        if not _delta:
                            continue
                        proj_selected = (proj_selected + _delta) % proj_n
                        focus = 0
                        del_fade_target = 0.0
                        exp_fade_target = 0.0
                        card_fade = 0.0
                        card_fade_target = 1.0
                        action_btns_visible = 0.0
                        action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                    elif key == "left":
                        if focus > 0:
                            focus -= 1
                        else:
                            proj_selected = (proj_selected - 1) % proj_n
                            focus = 0
                            card_fade = 0.0
                            card_fade_target = 1.0
                            action_btns_visible = 0.0
                            action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                        del_fade_target = 1.0 if focus == 1 else 0.0
                        exp_fade_target = 1.0 if focus == 2 else 0.0
                    elif key == "right":
                        if _is_project_row() and focus < 2:
                            focus += 1
                        else:
                            proj_selected = (proj_selected + 1) % proj_n
                            focus = 0
                            card_fade = 0.0
                            card_fade_target = 1.0
                            action_btns_visible = 0.0
                            action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                        del_fade_target = 1.0 if focus == 1 else 0.0
                        exp_fade_target = 1.0 if focus == 2 else 0.0

                    elif key == "enter":
                        # ── Focus 1: Delete → open popup overlay ───────────
                        if focus == 1 and _is_project_row():
                            delete_popup_open = True
                            delete_popup_target = 1.0
                            delete_popup_name = projects[proj_selected].name

                        # ── Focus 2: Export → shared destination picker ───
                        elif focus == 2 and _is_project_row() and projects[proj_selected].kind == "roadmap":
                            # Roadmap rows: Files / Notion / Confluence only —
                            # a roadmap is a document, not tickets to sync.
                            path = _export_roadmap_via_picker(
                                console,
                                live,
                                read_key,
                                _FRAME_TIME,
                                _supports_timeout,
                                roadmap_id=projects[proj_selected].roadmap_id,
                            )
                            if path:
                                w, h = console.size
                                live.update(
                                    _build_project_export_success_screen(
                                        str(path),
                                        width=w,
                                        height=h,
                                    )
                                )
                                # Show for at least 1.5s, then wait for a real keypress
                                _export_t0 = time.monotonic()
                                while True:
                                    k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                    elapsed = time.monotonic() - _export_t0
                                    if elapsed < 1.5:
                                        continue  # enforce minimum display time
                                    if k and k not in ("scroll_up", "scroll_down", ""):
                                        break
                            exp_fade_target = 1.0  # restore Export highlight

                        elif focus == 2 and _is_project_row():
                            _extra = ["Share Online"]
                            _extra += (["Jira"] if _jira_ok else []) + (["Azure DevOps"] if _azdevops_ok else [])
                            _dest = _pick_dest(
                                console,
                                live,
                                read_key,
                                _FRAME_TIME,
                                _supports_timeout,
                                mode="planning",
                                extra_options=_extra,
                            )
                            path = None
                            if _dest is not None:
                                project = projects[proj_selected]
                                if _dest == "files":
                                    from yeaboi.persistence import export_project_html, export_project_md

                                    _hp = export_project_html(project.id)
                                    _mp = export_project_md(project.id)
                                    if _hp or _mp:
                                        path = f"HTML  {_hp}\nMD    {_mp}"
                                    else:
                                        path = "No saved state for this project"
                                elif _dest in ("jira", "azdevops"):
                                    path = _project_tracker_sync(
                                        console,
                                        live,
                                        read_key,
                                        _FRAME_TIME,
                                        _supports_timeout,
                                        project.id,
                                        _dest,
                                    )
                                elif _dest == "shareonline":
                                    from yeaboi.persistence import load_graph_state
                                    from yeaboi.sharing.documents import planning_document
                                    from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

                                    _gs = load_graph_state(project.id)
                                    if not _gs:
                                        path = "No saved state for this project"
                                    else:
                                        _run_output_share_flow(
                                            console,
                                            live,
                                            read_key,
                                            _FRAME_TIME,
                                            _supports_timeout,
                                            document=planning_document(_gs),
                                            theme=PLANNING_THEME,
                                            title_fn=planning_title,
                                        )
                                        path = "Online share closed."
                                elif _dest == "copy":
                                    from yeaboi.clipboard import copy_markdown_status
                                    from yeaboi.persistence import load_graph_state
                                    from yeaboi.repl._io import build_plan_markdown

                                    _gs = load_graph_state(project.id)
                                    path = (
                                        copy_markdown_status(build_plan_markdown(_gs))
                                        if _gs
                                        else "No saved state for this project"
                                    )
                                else:  # notion / confluence
                                    from yeaboi.persistence import load_graph_state

                                    _gs = load_graph_state(project.id)
                                    if not _gs:
                                        path = "No saved state for this project"
                                    else:
                                        from yeaboi.export_targets import publish_markdown
                                        from yeaboi.repl._io import build_plan_markdown

                                        _pr = publish_markdown(
                                            _dest,
                                            title=f"Sprint Plan — {project.name}",
                                            markdown=build_plan_markdown(_gs),
                                        )
                                        path = _pr.url or _pr.message
                            if path:
                                w, h = console.size
                                live.update(
                                    _build_project_export_success_screen(
                                        str(path),
                                        width=w,
                                        height=h,
                                    )
                                )
                                # Show for at least 1.5s, then wait for a real keypress
                                _export_t0 = time.monotonic()
                                while True:
                                    k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                                    elapsed = time.monotonic() - _export_t0
                                    if elapsed < 1.5:
                                        continue  # enforce minimum display time
                                    if k and k not in ("scroll_up", "scroll_down", ""):
                                        break
                            exp_fade_target = 1.0  # restore Export highlight

                        # ── Focus 0: Card (empty state / new project) ────
                        elif not projects or proj_selected == len(projects):
                            # Check freshness — show popup only if stale (>30d) or missing
                            _profile_fresh = _staleness_days is not None and _staleness_days <= 30
                            if _board_configured and not team_popup_open and not _profile_fresh:
                                # Build dynamic staleness message
                                if _staleness_days is not None:
                                    _team_popup_msg = (
                                        f"Your team analysis is {_staleness_days} days old. Re-analyse before planning?"
                                    )
                                else:
                                    _team_popup_msg = "No team analysis found. Analyse your board before planning?"
                                team_popup_open = True
                                team_popup_target = 1.0
                                team_popup_sel = 0
                                team_popup_pulse = 0.0
                                _team_popup_result = ""
                            else:
                                pulse = 1.0
                                break  # → intake mode selection
                        else:
                            # White pulse flash on selected card before opening
                            pulse = 1.0
                            _pulse_frames = 8
                            for _pf in range(_pulse_frames):
                                pulse = max(0.0, 1.0 - (_pf + 1) / _pulse_frames)
                                w, h = console.size
                                live.update(
                                    _build_project_list_screen(
                                        projects,
                                        proj_selected,
                                        width=w,
                                        height=h,
                                        focus=focus,
                                        del_fade=del_fade,
                                        exp_fade=exp_fade,
                                        card_fade=card_fade,
                                        pulse=pulse,
                                        jira_enabled=_jira_ok,
                                        azdevops_enabled=_azdevops_ok,
                                    )
                                )
                                time.sleep(_FRAME_TIME)

                            project = projects[proj_selected]
                            if project.kind == "roadmap":
                                # A saved roadmap card — open the roadmap page
                                # straight into its results (analyzing first if
                                # the row was never analyzed).
                                _rm = _run_roadmap_page(
                                    console,
                                    live,
                                    read_key,
                                    _FRAME_TIME,
                                    _supports_timeout,
                                    dry_run=dry_run,
                                    open_roadmap_id=project.roadmap_id,
                                )
                                if isinstance(_rm, tuple):
                                    # "Plan This" — start a session pre-seeded
                                    # with the chosen candidate project.
                                    _selected_profile_id = _pick_analysis_profile(
                                        console,
                                        live,
                                        read_key,
                                        _FRAME_TIME,
                                        _supports_timeout,
                                        board_configured=_board_configured,
                                    )
                                    from yeaboi.ui.session import run_session

                                    run_session(
                                        live,
                                        console,
                                        intake_mode=_rm[0],
                                        dry_run=dry_run,
                                        _read_key_fn=_read_key_fn,
                                        analysis_profile_id=_selected_profile_id,
                                        initial_description=_rm[1],
                                    )
                                # None / "done" → back to the project list.
                                projects = _load_planning_rows()
                                proj_n = (len(projects) + 1) if projects else 2
                                proj_selected = min(proj_selected, proj_n - 1)
                                pulse = 0.0
                                continue

                            # Resume an existing project — load its saved graph state
                            # so the session can skip already-completed phases.
                            # See docs: "Memory & State" — session persistence.
                            from langchain_core.messages import HumanMessage

                            from yeaboi.persistence import load_graph_state
                            from yeaboi.ui.session import run_session

                            saved_state = load_graph_state(project.id)

                            # Fallback: if no state file exists (project created before
                            # state persistence was added), build a minimal graph state
                            # from project metadata so the session skips Phase A.
                            if saved_state is None:
                                saved_state = {
                                    "messages": [HumanMessage(content=project.name)],
                                }

                            run_session(
                                live,
                                console,
                                intake_mode=saved_state.get("_intake_mode", "smart"),
                                resume_project_id=project.id,
                                resume_graph_state=saved_state,
                                dry_run=dry_run,
                                _read_key_fn=_read_key_fn,
                            )
                            # Session ended (Esc or completed) — return to project list
                            projects = _load_planning_rows()
                            proj_n = (len(projects) + 1) if projects else 2
                            proj_selected = min(proj_selected, proj_n - 1)
                            pulse = 0.0
                            continue

                    elif key == "esc":
                        # ── Reverse transition: fade out cards → slide title down ──
                        # 1) cards fade out, 2) Planning slides from top to its
                        # position in the 3-item layout, 3) other titles fade in
                        # as Planning reaches its resting position.

                        # Step 1: Reverse stagger — cards disappear bottom-to-top
                        _dismiss_target = 0.0
                        _dismiss_visible = float(proj_n)
                        _dismiss_speed = 15.0  # cards per second (matches reveal)
                        _dismiss_start = time.monotonic()
                        while _dismiss_visible > _dismiss_target:
                            dt_d = time.monotonic() - _dismiss_start
                            _dismiss_visible = max(_dismiss_target, float(proj_n) - dt_d * _dismiss_speed)
                            w, h = console.size
                            live.update(
                                _build_project_list_screen(
                                    projects,
                                    proj_selected,
                                    width=w,
                                    height=h,
                                    cards_visible=_dismiss_visible,
                                    jira_enabled=_jira_ok,
                                    azdevops_enabled=_azdevops_ok,
                                )
                            )
                            time.sleep(_FRAME_TIME)

                        # Step 2: Slide Planning title from top down to its 3-item
                        # layout position. In the last ~40% of the slide, fade in
                        # the other two mode titles so they appear as Planning lands.
                        chosen = _MODE_CARDS[selected]
                        base_r, base_g, base_b = COLOR_RGB.get(chosen["color"], (180, 180, 180))
                        base_style = f"bold rgb({base_r},{base_g},{base_b})"
                        others = [i for i in range(n) if i != selected]

                        w, h = console.size
                        inner_h = h - 4
                        # Target: where Planning sits in the full 3-item mode screen.
                        # body_h for 3 items with Planning selected (no desc during slide):
                        # Planning(2) + blank(1) + CodeReview(2) + blank(1) + Sprint(2) = 8
                        body_h_no_desc = 2 * n + (n - 1)
                        target_offset = max(0, (inner_h - body_h_no_desc) // 2)
                        start_offset = 1  # current position (top of project list)

                        slide_frames = 18
                        for frame in range(slide_frames + 1):
                            t = frame / slide_frames
                            eased = ease_out_cubic(t)
                            current_offset = int(start_offset + (target_offset - start_offset) * eased)

                            # Fade others in during the last 40% of the slide
                            fade_t = max(0.0, (t - 0.6) / 0.4)

                            w, h = console.size
                            if fade_t <= 0:
                                # Only Planning visible — use slide frame
                                live.update(
                                    _build_slide_frame(
                                        chosen,
                                        top_offset=current_offset,
                                        width=w,
                                        height=h,
                                        style=base_style,
                                    )
                                )
                            else:
                                # Cross-fade: show all items, fade others from dark
                                # to their resting dim colour (100,100,100).
                                from yeaboi.ui.shared._animations import BLACK_RGB, lerp_color

                                dim_rgb = (100, 100, 100)
                                fade_rgb = lerp_color(fade_t, BLACK_RGB, dim_rgb)
                                live.update(
                                    _build_mode_screen(
                                        selected,
                                        width=w,
                                        height=h,
                                        shimmer_tick=0.0,
                                        desc_reveal=0,
                                        fade_style=fade_rgb,
                                        fade_indices=others,
                                    )
                                )
                            time.sleep(_FRAME_TIME)

                        # Step 3: Restart mode selection. This branch already slid the
                        # menu back in, so mark it so the outer loop doesn't re-sweep;
                        # _skip_fade_in still gives the companion its return entrance.
                        _restart_mode_select = True
                        _skip_fade_in = True
                        _reverse_animated = True
                        break  # break Phase 3 loop → restart Phase 1

                    # Animate button fade — smoothly move current values toward targets
                    now = time.monotonic()
                    dt = now - prev_tick
                    prev_tick = now
                    step = fade_speed * dt

                    if del_fade < del_fade_target:
                        del_fade = min(del_fade + step, del_fade_target)
                    elif del_fade > del_fade_target:
                        del_fade = max(del_fade - step, del_fade_target)
                    if exp_fade < exp_fade_target:
                        exp_fade = min(exp_fade + step, exp_fade_target)
                    elif exp_fade > exp_fade_target:
                        exp_fade = max(exp_fade - step, exp_fade_target)
                    if card_fade < card_fade_target:
                        card_fade = min(card_fade + step, card_fade_target)
                    elif card_fade > card_fade_target:
                        card_fade = max(card_fade - step, card_fade_target)
                    # Pulse decays toward 0
                    if pulse > 0:
                        pulse = max(0.0, pulse - step)

                    # Action buttons stagger animation (same speed as export submenu)
                    action_stagger_step = dt * 12.0
                    if action_btns_visible < action_btns_visible_target:
                        action_btns_visible = min(action_btns_visible + action_stagger_step, action_btns_visible_target)
                    elif action_btns_visible > action_btns_visible_target:
                        action_btns_visible = max(action_btns_visible - action_stagger_step, action_btns_visible_target)

                    # Export submenu stagger animation — faster rate so the
                    # three buttons pop in/out quickly one after another.
                    stagger_step = dt * 12.0  # ~0.25s to reveal all 3 buttons
                    if submenu_visible < submenu_visible_target:
                        submenu_visible = min(submenu_visible + stagger_step, submenu_visible_target)
                    elif submenu_visible > submenu_visible_target:
                        submenu_visible = max(submenu_visible - stagger_step, submenu_visible_target)
                    if submenu_html_fade < submenu_html_fade_target:
                        submenu_html_fade = min(submenu_html_fade + step, submenu_html_fade_target)
                    elif submenu_html_fade > submenu_html_fade_target:
                        submenu_html_fade = max(submenu_html_fade - step, submenu_html_fade_target)
                    if submenu_md_fade < submenu_md_fade_target:
                        submenu_md_fade = min(submenu_md_fade + step, submenu_md_fade_target)
                    elif submenu_md_fade > submenu_md_fade_target:
                        submenu_md_fade = max(submenu_md_fade - step, submenu_md_fade_target)
                    if submenu_jira_fade < submenu_jira_fade_target:
                        submenu_jira_fade = min(submenu_jira_fade + step, submenu_jira_fade_target)
                    elif submenu_jira_fade > submenu_jira_fade_target:
                        submenu_jira_fade = max(submenu_jira_fade - step, submenu_jira_fade_target)
                    if submenu_azdevops_fade < submenu_azdevops_fade_target:
                        submenu_azdevops_fade = min(submenu_azdevops_fade + step, submenu_azdevops_fade_target)
                    elif submenu_azdevops_fade > submenu_azdevops_fade_target:
                        submenu_azdevops_fade = max(submenu_azdevops_fade - step, submenu_azdevops_fade_target)

                    # Team analysis popup slide animation
                    if team_popup_t < team_popup_target:
                        team_popup_t = min(team_popup_t + step, team_popup_target)
                    elif team_popup_t > team_popup_target:
                        team_popup_t = max(team_popup_t - step, team_popup_target)

                    if team_popup_open and team_popup_t > 0:
                        team_popup_pulse += dt
                    elif team_popup_t <= 0:
                        team_popup_pulse = 0.0

                    # When team popup finishes sliding out, resolve the result.
                    if team_popup_open and team_popup_target == 0.0 and team_popup_t <= 0:
                        team_popup_open = False
                        if _team_popup_result.startswith("analyse"):
                            break
                        # "skip" falls through to normal intake
                        pulse = 1.0
                        break  # → intake mode selection

                    # Delete popup slide animation
                    if delete_popup_t < delete_popup_target:
                        delete_popup_t = min(delete_popup_t + step, delete_popup_target)
                    elif delete_popup_t > delete_popup_target:
                        delete_popup_t = max(delete_popup_t - step, delete_popup_target)

                    # Pulse clock: ticks whenever the popup is visible so the
                    # border oscillates between dark/bright red (like a loader).
                    if delete_popup_open and delete_popup_t > 0:
                        delete_popup_pulse += dt
                    elif delete_popup_t <= 0:
                        delete_popup_pulse = 0.0

                    # White flash decays toward 0 (slower rate so it's visible)
                    if delete_popup_flash > 0:
                        delete_popup_flash = max(0.0, delete_popup_flash - dt * 3.0)
                        # Once flash finishes, start the slide-down
                        if delete_popup_flash <= 0 and _delete_pending:
                            delete_popup_target = 0.0

                    # When popup finishes sliding out, clear the open state.
                    # If a delete was confirmed (_delete_pending), perform it now.
                    if delete_popup_open and delete_popup_target == 0.0 and delete_popup_t <= 0:
                        if _delete_pending:
                            project = projects[proj_selected]
                            if project.kind == "roadmap":
                                try:
                                    from yeaboi.roadmap.store import RoadmapStore

                                    with RoadmapStore(_ana_dbp) as _rm_store:
                                        _rm_store.delete_roadmap(project.roadmap_id)
                                    logger.info("roadmap: deleted id=%s", project.roadmap_id)
                                except Exception:
                                    logger.error("roadmap: delete failed for id=%s", project.roadmap_id, exc_info=True)
                            else:
                                from yeaboi.persistence import delete_project

                                delete_project(project.id)
                            projects = _load_planning_rows()
                            if projects:
                                proj_n = len(projects) + 1
                                proj_selected = min(proj_selected, proj_n - 1)
                            else:
                                proj_n = 2
                                proj_selected = 0
                            _delete_pending = False
                            focus = 0
                            # Reset button animations so focus returns to card
                            del_fade = 0.0
                            del_fade_target = 0.0
                            exp_fade = 0.0
                            exp_fade_target = 0.0
                            action_btns_visible = 0.0
                            action_btns_visible_target = 2.0 if _is_project_row() else 0.0
                        else:
                            # Esc dismiss — keep Delete button focused
                            focus = 1
                            del_fade = 1.0
                            del_fade_target = 1.0
                            exp_fade = 0.0
                            exp_fade_target = 0.0
                        delete_popup_open = False
                        delete_popup_name = ""
                        delete_popup_flash = 0.0
                        card_fade = 1.0
                        card_fade_target = 1.0

                    w, h = console.size
                    _list_last_panel = _build_project_list_screen(
                        projects,
                        proj_selected,
                        width=w,
                        height=h,
                        focus=focus,
                        del_fade=del_fade,
                        exp_fade=exp_fade,
                        card_fade=card_fade,
                        pulse=pulse,
                        action_btns_visible=action_btns_visible,
                        show_export_submenu=export_submenu_open or submenu_visible > 0,
                        submenu_sel=submenu_sel,
                        submenu_html_fade=submenu_html_fade,
                        submenu_md_fade=submenu_md_fade,
                        submenu_jira_fade=submenu_jira_fade,
                        submenu_azdevops_fade=submenu_azdevops_fade,
                        submenu_visible=submenu_visible,
                        delete_popup_name=delete_popup_name,
                        delete_popup_t=delete_popup_t,
                        delete_popup_pulse=delete_popup_pulse,
                        delete_popup_flash=delete_popup_flash,
                        team_popup_t=team_popup_t,
                        team_popup_sel=team_popup_sel,
                        team_popup_pulse=team_popup_pulse,
                        team_popup_message=_team_popup_msg,
                        jira_enabled=_jira_ok,
                        azdevops_enabled=_azdevops_ok,
                        shimmer_tick=now - _list_anim0,
                    )
                    live.update(_list_last_panel)

                # Guard: Esc from project list sets _restart_mode_select → skip to outer loop
                if _restart_mode_select:
                    break

                # ── Phase 3a: Team analysis (if user selected "Analyse") ──────────
                if _team_popup_result.startswith("analyse"):
                    import threading

                    from yeaboi.analysis import run_team_analysis

                    # Determine source from popup result
                    if _team_popup_result == "analyse_jira":
                        _ta_source = "jira"
                    elif _team_popup_result == "analyse_azdevops":
                        _ta_source = "azdevops"
                    elif _team_popup_result == "analyse_both":
                        _ta_source = "both"
                    else:
                        _ta_source = "jira" if _jira_ok else "azdevops"
                    _ta_project_key = ""
                    _ta_team_name = ""
                    try:
                        if _ta_source == "both":
                            pass  # project/team auto-resolved per source in the engine
                        elif _ta_source == "jira":
                            from yeaboi.config import get_jira_project_key

                            _ta_project_key = get_jira_project_key() or ""
                        else:
                            from yeaboi.config import (
                                get_azure_devops_project,
                                get_azure_devops_team,
                            )

                            _ta_project_key = get_azure_devops_project() or ""
                            _ta_team_name = get_azure_devops_team() or ""
                    except Exception:
                        pass

                    from yeaboi.analysis.engine import (
                        AnalysisCancelledError,
                        _available_doc_sources,
                        _offerable_code_sources,
                    )

                    # The wizard owns the whole setup sequence (Esc steps back one
                    # screen; backing out of the first step returns to this list).
                    _delivery_grid = ["jira", "azdevops"] if _ta_source == "both" else [_ta_source]
                    _ta_setup = _run_analysis_setup_wizard(
                        live,
                        console,
                        read_key,
                        _FRAME_TIME,
                        _supports_timeout,
                        grid={
                            "delivery": _delivery_grid,
                            # Offerable, not merely configured — see the sibling call site.
                            "code": _offerable_code_sources(),
                            "docs": _available_doc_sources(),
                        },
                        roster_fallback=_delivery_grid,
                        project_key=_ta_project_key,
                        db_path=_ana_dbp,
                    )
                    if _ta_setup is None:
                        _restart_project_list = True
                        _team_popup_result = ""
                        continue
                    _ta_features = _ta_setup["features"]
                    _ta_components = _ta_setup["components"]
                    _ta_analysis_scope = _ta_setup["analysis_scope"]
                    _ta_depth = _ta_setup["depth"]
                    _ta_analysis_model = _ta_setup["model"]
                    _ta_window_days = _ta_setup["window_days"]
                    _ta_members_map = _ta_setup["members_map"]

                    _ta_progress: list = []
                    _ta_profile_box: list = [None]
                    _ta_examples_box: list = [None]
                    _ta_sprint_names_box: list = [[]]
                    _ta_result_box: list = [None]  # full engine dict (carries 'both' results)
                    _ta_error_box: list[str] = [""]
                    _ta_done = threading.Event()
                    _ta_cancel_event = threading.Event()

                    def _run_team_analysis():
                        try:
                            # One code path with CLI/MCP: the engine fetches,
                            # analyses, saves the profile, and writes the log.
                            _res = run_team_analysis(
                                source=_ta_source,
                                project_key=_ta_project_key,
                                team_name=_ta_team_name,
                                analysis_depth=_ta_depth,
                                analysis_window_days=_ta_window_days,
                                analysis_scope=_ta_analysis_scope or None,
                                analysis_model=_ta_analysis_model,
                                analysis_features=_ta_features,
                                components=_ta_components,
                                members=_ta_members_map,
                                progress=_ta_progress,
                                db_path=_ana_dbp,
                                cancel_event=_ta_cancel_event,
                            )
                            _ta_result_box[0] = _res
                            _dlv = _res.get("delivery") or {}
                            _first = next(iter(_dlv.values())) if _dlv else {}
                            _ta_profile_box[0] = _first.get("profile")
                            _ta_examples_box[0] = _first.get("examples") or {}
                            _ta_sprint_names_box[0] = _first.get("sprint_names") or []
                        except AnalysisCancelledError:
                            pass  # cancelled — boxes stay empty; the poll loop owns the notice
                        except ValueError as exc:
                            _ta_error_box[0] = str(exc)
                        except Exception as exc:
                            from yeaboi.ui.session._utils import _classify_api_error

                            _ta_error_box[0] = _classify_api_error(exc)
                        finally:
                            _ta_done.set()

                    logger.info(
                        "Starting team analysis: source=%s, project=%s",
                        _ta_source,
                        _ta_project_key,
                    )
                    _ta_thread_start = time.monotonic()
                    _ta_thread = duck_working_thread(_run_team_analysis, name="team-analysis")
                    _ta_thread.start()

                    # Processing animation while waiting
                    from yeaboi.ui.mode_select.screens._screens_secondary import (
                        _build_analysis_progress_screen,
                    )

                    _ta_anim_tick = 0.0
                    _ta_cancelled = False
                    try:
                        while not _ta_done.is_set():
                            _ta_anim_tick += _FRAME_TIME
                            w, h = console.size
                            live.update(
                                _build_analysis_progress_screen(
                                    _ta_progress,
                                    width=w,
                                    height=h,
                                    elapsed=time.monotonic() - _ta_thread_start,
                                    anim_tick=_ta_anim_tick,
                                    source=_ta_source,
                                    mode="analysis",
                                )
                            )
                            time.sleep(_FRAME_TIME)
                    except KeyboardInterrupt:
                        # First Ctrl-C: cooperative cancel. The bounded wait below is
                        # NOT wrapped, so a second Ctrl-C re-raises out of select_mode
                        # and quits the app via cli.py's existing handler.
                        logger.info("Analysis: Ctrl-C received — cancelling run")
                        _ta_cancel_event.set()
                        _ta_progress.append("Cancelling — waiting for running work to stop…")
                        _ta_deadline = time.monotonic() + 10.0
                        while not _ta_done.is_set() and time.monotonic() < _ta_deadline:
                            _ta_anim_tick += _FRAME_TIME
                            w, h = console.size
                            live.update(
                                _build_analysis_progress_screen(
                                    _ta_progress,
                                    width=w,
                                    height=h,
                                    elapsed=time.monotonic() - _ta_thread_start,
                                    anim_tick=_ta_anim_tick,
                                    source=_ta_source,
                                    mode="analysis",
                                )
                            )
                            time.sleep(_FRAME_TIME)
                        _ta_cancelled = True
                    # Daemon thread — abandoned after the bounded wait if a job is
                    # still busy; the engine's pre-persist gate still guarantees
                    # nothing is saved once the cancel event is set.
                    _ta_thread.join(timeout=0.1 if _ta_cancelled else None)

                    if _ta_cancelled:
                        w, h = console.size
                        live.update(
                            _build_project_export_success_screen(
                                "Analysis cancelled — no results were saved.",
                                width=w,
                                height=h,
                                subtitle="Analysis cancelled",
                                hint="Press any key to continue.",
                                mode="analysis",
                            )
                        )
                        while True:
                            _k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                            if _k:
                                break
                        _restart_project_list = True
                        _team_popup_result = ""
                        continue

                    _ta_profile = _ta_profile_box[0]
                    _ta_duration = time.monotonic() - _ta_thread_start
                    if _ta_profile:
                        logger.info(
                            "Analysis complete: %s — %d sprints, %d stories (%.1fs)",
                            _ta_profile.team_id,
                            _ta_profile.sample_sprints,
                            _ta_profile.sample_stories,
                            _ta_duration,
                        )

                        # Persist + analysis log already handled inside
                        # run_team_analysis (one code path with CLI/MCP).

                        # Show results screen (overview + section cards).
                        # Continue shows the coaching insights first (Back
                        # returns to the results overview); Continue on the
                        # insights and Esc both fall through to intake below.
                        _duck_react("analysis_done")
                        _ta_examples = _ta_examples_box[0] or {}
                        _ta_sprint_names = _ta_sprint_names_box[0]
                        _ta_full = _ta_result_box[0] or {}
                        while True:
                            _ta_active_box: list = [None]
                            _ta_res = _run_team_analysis_results(
                                live,
                                console,
                                read_key,
                                _FRAME_TIME,
                                _supports_timeout,
                                _ta_profile,
                                _ta_examples,
                                sprint_names=_ta_sprint_names,
                                team_name=_ta_team_name,
                                delivery=_ta_full.get("delivery"),
                                code=_ta_full.get("code"),
                                docs=_ta_full.get("docs"),
                                comparison=_ta_full.get("comparison"),
                                analysis_features=_ta_full.get("analysis_features"),
                                active_box=_ta_active_box,
                                source=_ta_source,
                                project_key=_ta_project_key,
                                retry_config={
                                    "source": _ta_source,
                                    "project_key": _ta_project_key,
                                    "team_name": _ta_team_name,
                                    "analysis_depth": _ta_depth,
                                    "analysis_window_days": _ta_window_days,
                                    "analysis_scope": _ta_analysis_scope or None,
                                    "analysis_model": _ta_analysis_model,
                                    "components": _ta_components,
                                    "members": _ta_members_map,
                                    "db_path": _ana_dbp,
                                },
                            )
                            if _ta_active_box[0] is not None:
                                _ta_profile, _ta_examples, _ta_sprint_names, _ta_team_name = _ta_active_box[0]
                            if _ta_res != "continue":
                                break
                            if (
                                _run_team_insights(
                                    live,
                                    console,
                                    read_key,
                                    _FRAME_TIME,
                                    _supports_timeout,
                                    _ta_profile,
                                    _ta_examples,
                                    sprint_names=_ta_sprint_names,
                                )
                                == "back"
                            ):
                                continue
                            break
                    elif _ta_error_box[0]:
                        w, h = console.size
                        live.update(
                            _build_project_export_success_screen(
                                _ta_error_box[0],
                                width=w,
                                height=h,
                                subtitle="Analysis failed",
                                hint="Press any key to continue.",
                            )
                        )
                        while True:
                            k = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                            if k:
                                break

                # ── Phase 3b: Transition to intake mode selection ─────────────────
                # Show title + new subtitle, then stagger-reveal intake options.
                intake_selected = 0
                intake_n = len(_INTAKE_CARDS)
                intake_start = time.monotonic()

                # Blank frame — title + subtitle, no intake items yet
                w, h = console.size
                live.update(
                    _build_intake_screen(
                        intake_selected,
                        width=w,
                        height=h,
                        visible_items=0,
                    )
                )
                time.sleep(_FRAME_TIME * 2)

                # Stagger-reveal intake options one at a time
                for item_i in range(1, intake_n + 1):
                    w, h = console.size
                    live.update(
                        _build_intake_screen(
                            intake_selected,
                            width=w,
                            height=h,
                            visible_items=item_i,
                        )
                    )
                    time.sleep(_FRAME_TIME * 2)

                # ── Phase 4: Intake mode selection ────────────────────────────────
                chosen_intake = None
                while True:
                    key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                    if key in ("up", "left", "scroll_up", "down", "right", "scroll_down"):
                        _delta = coalesce_steps(
                            key,
                            read_key,
                            down=("down", "right", "scroll_down"),
                            up=("up", "left", "scroll_up"),
                        )
                        if not _delta:
                            continue
                        intake_selected = (intake_selected + _delta) % intake_n
                        intake_start = time.monotonic()
                    elif key == "enter":
                        chosen_intake = _INTAKE_CARDS[intake_selected]["key"]
                        if chosen_intake == "roadmap":
                            # ── Roadmap card: goes straight to the source picker;
                            # analyze the quarterly roadmap and pick a recommended
                            # project. The page returns the suggested intake mode +
                            # a pre-seeded description, "done" when a roadmap was
                            # saved (its card now lives in the project list), or
                            # None when the user backed out before saving.
                            _rm = _run_roadmap_page(
                                console, live, read_key, _FRAME_TIME, _supports_timeout, dry_run=dry_run
                            )
                            if _rm is None:
                                # Esc / Back before saving — stay on the intake cards
                                intake_start = time.monotonic()
                                continue
                            if _rm == "done":
                                # Roadmap saved — show it in the merged project list
                                projects = _load_planning_rows()
                                proj_n = (len(projects) + 1) if projects else 2
                                proj_selected = min(proj_selected, proj_n - 1)
                                _restart_project_list = True
                                break  # break Phase 4 loop → restart Phase 3
                            _rm_mode, _rm_desc = _rm
                            _selected_profile_id = _pick_analysis_profile(
                                console,
                                live,
                                read_key,
                                _FRAME_TIME,
                                _supports_timeout,
                                board_configured=_board_configured,
                            )
                            from yeaboi.ui.session import run_session

                            run_session(
                                live,
                                console,
                                intake_mode=_rm_mode,
                                dry_run=dry_run,
                                _read_key_fn=_read_key_fn,
                                analysis_profile_id=_selected_profile_id,
                                initial_description=_rm_desc,
                            )
                            # Session ended (Esc or completed) — return to project list
                            projects = _load_planning_rows()
                            proj_n = (len(projects) + 1) if projects else 2
                            proj_selected = min(proj_selected, proj_n - 1)
                            _restart_project_list = True
                            break  # break Phase 4 loop → restart Phase 3
                        if chosen_intake != "offline":
                            # ── Profile picker: let user select analysis profile ──
                            _selected_profile_id = _pick_analysis_profile(
                                console,
                                live,
                                read_key,
                                _FRAME_TIME,
                                _supports_timeout,
                                board_configured=_board_configured,
                            )
                            from yeaboi.ui.session import run_session

                            run_session(
                                live,
                                console,
                                intake_mode=chosen_intake,
                                dry_run=dry_run,
                                _read_key_fn=_read_key_fn,
                                analysis_profile_id=_selected_profile_id,
                            )
                            # Session ended (Esc or completed) — return to project list
                            projects = _load_planning_rows()
                            proj_n = (len(projects) + 1) if projects else 2
                            proj_selected = min(proj_selected, proj_n - 1)
                            _restart_project_list = True
                            break  # break Phase 4 loop → restart Phase 3
                        break  # → offline sub-menu (Phase 5)
                    elif key == "esc":
                        # Back to project list
                        _restart_project_list = True
                        break

                    elapsed = time.monotonic() - intake_start
                    reveal = elapsed * _DESC_SCROLL_SPEED  # float for sub-char fade

                    w, h = console.size
                    tick = time.monotonic() - start_time
                    live.update(
                        _build_intake_screen(
                            intake_selected,
                            width=w,
                            height=h,
                            shimmer_tick=tick,
                            desc_reveal=reveal,
                        )
                    )

                # Guard: Phase 4 Esc or session-end sets restart → skip Phase 5
                if _restart_project_list:
                    continue
                if _restart_mode_select:
                    break

                # ── Phase 5: Offline sub-menu (Export / Import) ───────────────
                offline_selected = 0
                offline_n = len(_OFFLINE_CARDS)
                offline_start = time.monotonic()

                # Blank frame — title + subtitle, no items yet
                w, h = console.size
                live.update(
                    _build_offline_screen(
                        offline_selected,
                        width=w,
                        height=h,
                        visible_items=0,
                    )
                )
                time.sleep(_FRAME_TIME * 2)

                # Stagger-reveal offline options one at a time
                for item_i in range(1, offline_n + 1):
                    w, h = console.size
                    live.update(
                        _build_offline_screen(
                            offline_selected,
                            width=w,
                            height=h,
                            visible_items=item_i,
                        )
                    )
                    time.sleep(_FRAME_TIME * 2)

                # Phase 5 interaction loop
                while True:
                    key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                    if key in ("up", "left", "scroll_up", "down", "right", "scroll_down"):
                        _delta = coalesce_steps(
                            key,
                            read_key,
                            down=("down", "right", "scroll_down"),
                            up=("up", "left", "scroll_up"),
                        )
                        if not _delta:
                            continue
                        offline_selected = (offline_selected + _delta) % offline_n
                        offline_start = time.monotonic()
                    elif key == "enter":
                        break  # → Phase 5b (export or import)
                    elif key == "esc":
                        # Go back to project list
                        _restart_project_list = True
                        break

                    elapsed = time.monotonic() - offline_start
                    reveal = elapsed * _DESC_SCROLL_SPEED  # float for sub-char fade

                    w, h = console.size
                    tick = time.monotonic() - start_time
                    live.update(
                        _build_offline_screen(
                            offline_selected,
                            width=w,
                            height=h,
                            shimmer_tick=tick,
                            desc_reveal=reveal,
                        )
                    )

                # Guard: if Phase 5 Esc or Import Esc set restart, skip 5b
                if _restart_project_list:
                    continue

                # ── Phase 5b: Export or Import ────────────────────────────────
                offline_choice = _OFFLINE_CARDS[offline_selected]["key"]

                if offline_choice == "export":
                    # Export a blank questionnaire template directly
                    from yeaboi.questionnaire_io import export_questionnaire_md

                    out_path = export_questionnaire_md(None, Path("scrum-questionnaire.md"))
                    w, h = console.size
                    live.update(_build_export_success_screen(str(out_path), width=w, height=h))
                    # Wait for any keypress to exit
                    while True:
                        key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()
                        if key:
                            break
                    return None  # cli.py exits

                else:
                    # Import — show text input for file path
                    import_value = ""
                    import_error = ""
                    _default_path = "scrum-questionnaire.md"

                    w, h = console.size
                    live.update(_build_import_screen(import_value, width=w, height=h, placeholder=_default_path))

                    while True:
                        key = read_key(timeout=_FRAME_TIME) if _supports_timeout else read_key()

                        if key == "enter":
                            # Use default if empty
                            file_path = import_value.strip() if import_value.strip() else _default_path
                            p = Path(file_path)
                            if not p.exists():
                                import_error = f"File not found: {file_path}"
                            elif not p.suffix == ".md":
                                import_error = f"Expected a .md file, got: {p.suffix or 'no extension'}"
                            else:
                                # Sandbox pre-flight (main thread): cli.py will
                                # parse this path headlessly — consent now, so
                                # the import never dies on a sandbox error.
                                from yeaboi.ui.shared._consent import _preflight_path_consent

                                if _preflight_path_consent(
                                    console,
                                    live,
                                    read_key,
                                    _FRAME_TIME,
                                    _supports_timeout,
                                    str(p),
                                    mode="read",
                                    context="Questionnaire import",
                                ):
                                    return ("project-planning", None, str(p))
                                import_error = f"Access to {p} denied — allow it via Settings → Paths"

                            w, h = console.size
                            live.update(
                                _build_import_screen(
                                    import_value,
                                    width=w,
                                    height=h,
                                    error=import_error,
                                    placeholder=_default_path,
                                )
                            )
                            continue

                        elif key == "esc":
                            _restart_project_list = True
                            break
                        elif key == "backspace":
                            import_value = import_value[:-1]
                            import_error = ""
                        elif key == "clear":
                            import_value = ""
                            import_error = ""
                        elif key.startswith("paste:") if isinstance(key, str) else False:
                            import_value += paste_payload(key)
                            import_error = ""
                        elif key == "ctrl+v":
                            # A file-path field never reaches an LLM — reject image paste.
                            from yeaboi.ui.shared._attachments import UNSUPPORTED_MESSAGE

                            import_error = UNSUPPORTED_MESSAGE
                        elif len(key) == 1 and key.isprintable():
                            import_value += key
                            import_error = ""
                        elif key == "":
                            pass  # timeout, no input
                        else:
                            continue

                        w, h = console.size
                        live.update(
                            _build_import_screen(
                                import_value,
                                width=w,
                                height=h,
                                error=import_error,
                                placeholder=_default_path,
                            )
                        )

    return None
