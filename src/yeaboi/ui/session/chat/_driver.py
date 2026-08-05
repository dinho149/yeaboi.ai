"""The chat driver — one state-driven loop replacing the phased planning flow.

# See docs: "Architecture" — TUI system; "Session Management" — resume
# See docs: "Guardrails" — input guardrails + human-in-the-loop

Each loop iteration inspects graph_state and decides what the conversation
needs next (the same predicates the old phases used), so resume, the PTO
sub-loop, and mid-chat size switches fall out of state inspection rather
than control flow:

  greeting/size (TUI-side, kept in _chat_preamble — NEVER in messages,
  because project_intake reads messages[0] as the description)
  → intake Q&A (one question per graph invoke, choices drawn inline)
  → intake review (summary card + accept/edit replies)
  → pipeline stages (auto-advanced; artifact cards + accept/edit replies;
    capacity overflow stays a popup — its semantics must not drift)
  → free refinement chat (streamed via the agent ReAct node).

Threading: every graph call runs on a worker thread through
stream_chat_turn(); the main thread paints 30fps frames, joining the token
buffer each frame. on_token appends to a plain list (GIL-atomic) and never
touches Rich/Live.
"""

from __future__ import annotations

import logging
import threading
import time

from langchain_core.messages import AIMessage, HumanMessage
from rich.console import Console
from rich.live import Live

from yeaboi.agent.chat_intake import GREETING_TEXT, SIZE_QUESTION_TEXT, parse_size_reply, resolve_intake_mode
from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState, ReviewDecision
from yeaboi.agent.streaming import ChatStreamCancelledError, predict_next_node, stream_chat_turn
from yeaboi.input_guardrails import MAX_CHAT_INPUT_CHARS, validate_chat_input
from yeaboi.persistence import save_project_snapshot
from yeaboi.prompts.intake import decorate_question_for_chat
from yeaboi.ui.shared._animations import FRAME_TIME_30FPS
from yeaboi.ui.shared._attachments import handle_ctrl_v, referenced_images
from yeaboi.ui.shared._input import set_text_entry
from yeaboi.ui.shared._music_bar import (
    poke_duck,
    quack_duck,
    set_duck_working,
    skip_duck_entrance,
    start_duck_entrance,
)
from yeaboi.ui.shared._scroll import SCROLL_BOTTOM, coalesce_scroll

from ._commands import ChatContext, dispatch, matching_commands
from ._composer import ChatComposer, PasteImage, Submit, Truncated, Voice
from ._duck import ChatDuck
from ._question_view import derive_question_view
from ._screen import ChoiceRows, PipelineProgress, build_chat_screen
from ._transcript import ChatTranscript

logger = logging.getLogger(__name__)

_PIPELINE_NODES = (
    "project_analyzer",
    "feature_skip",
    "feature_generator",
    "story_writer",
    "task_decomposer",
    "sprint_planner",
)

_ACCEPT_WORDS = frozenset({"accept", "a", "ok", "yes", "looks good", "lgtm", "continue"})

# What the duck quacks as each pipeline stage completes.
_STAGE_QUIPS = {
    "project_analyzer": "Analysis done!",
    "feature_skip": "Epics drawn up!",
    "feature_generator": "Epics drawn up!",
    "story_writer": "Stories done!",
    "task_decomposer": "Tasks sliced!",
    "sprint_planner": "Sprints packed!",
}

# Which state key proves a pipeline step has produced its artifact.
_PROGRESS_DONE_KEYS = {
    "project_analyzer": "project_analysis",
    "epic_review": "_epic_reviewed",
    "feature_generator": "features",
    "story_writer": "stories",
    "task_decomposer": "tasks",
    "sprint_planner": "sprints",
}
_FORM_CHOICE_LABEL = "Fill it out as a form instead"
_ESC_WINDOW_SECONDS = 2.0
_DRY_STAGE_SECONDS = 1.5  # fake per-stage delay in --dry-run (patched to 0 in tests)
_IDLE_HINT_SECONDS = 8.0  # stuck on a question this long → the duck offers a hint
_IDLE_TIP_AFTER_SECONDS = 3.0  # quiet this long (greeting / post-plan) → rotating tips


def run_chat_session(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    *,
    project_id: str,
    bell: bool = True,
    dry_run: bool = False,
    initial_description: str = "",
) -> dict | None:
    """Run the whole planning conversation. Returns the final state (or None on quit pre-description)."""
    driver = _ChatDriver(
        live,
        console,
        graph,
        graph_state,
        _key,
        project_id=project_id,
        bell=bell,
        dry_run=dry_run,
        initial_description=initial_description,
    )
    # The chat is one big typing surface — suppress bare-letter chrome
    # shortcuts ("c" controls etc.) for the whole session.
    set_text_entry(True)
    try:
        return driver.run()
    finally:
        set_text_entry(False)


class _ChatDriver:
    def __init__(
        self,
        live: Live,
        console: Console,
        graph,
        graph_state: dict,
        _key,
        *,
        project_id: str,
        bell: bool,
        dry_run: bool,
        initial_description: str,
    ) -> None:
        self.live = live
        self.console = console
        self.graph = graph
        self.state = graph_state
        self._key = _key
        self.project_id = project_id
        self.bell = bell
        self.dry_run = dry_run
        self.initial_description = initial_description

        self.transcript = ChatTranscript()
        self.composer = ChatComposer()
        self.scroll_offset = 0
        self.scroll_meta: dict = {}
        self.follow = True
        self.notice = ""
        self.subtitle = ""
        self.choices: ChoiceRows | None = None
        self.choice_labels: list[str] = []
        self.quit = False
        self.edit_armed = False
        self._esc_at = 0.0
        self._prompted: set[str] = set()
        self._prefilled_q = -1
        self._form_requested = False  # /form (or the greeting pick) before the questionnaire exists
        self._finish_requested = False  # /finish before the questionnaire exists
        self._anim0 = time.monotonic()
        self._dry_full_state: dict | None = None
        self.duck = ChatDuck()  # sole owner of the corner duck's speech bubble
        self.progress: PipelineProgress | None = None  # stage checklist while building
        self._built_this_session = False  # a pipeline stage ran here (gates the celebration)
        self._last_phase = ""  # intake phase last seen (quack on boundary)
        self._hinted_q = -1  # question already idle-hinted (one per question)
        self._idle_since = time.monotonic()  # last keypress — feeds hints + idle tips

    # ------------------------------------------------------------------ utils

    def _qs(self) -> QuestionnaireState | None:
        qs = self.state.get("questionnaire")
        return qs if isinstance(qs, QuestionnaireState) else None

    def _bottom(self) -> int:
        return self.scroll_meta.get("max_offset", 0)

    def _render(self, *, processing: bool = False, tick: float = 0.0, stream_text: str | None = None) -> None:
        w, h = self.console.size
        menu = None
        first_line = self.composer.lines[0] if self.composer.lines else ""
        if not processing:
            if first_line.startswith("/") and self.composer.row == 0:
                menu = matching_commands(self._ctx(), first_line.split(" ")[0])
            else:
                # Mid-draft "/" keeps the menu reachable: key off the token at
                # the cursor, not the whole buffer (which is the user's text).
                word, _start = self.composer.cursor_word()
                if word.startswith("/"):
                    menu = matching_commands(self._ctx(), word) or None
        panel = build_chat_screen(
            self.transcript,
            self.composer,
            self.state,
            width=w,
            height=h,
            scroll_offset=self.scroll_offset,
            scroll_meta=self.scroll_meta,
            processing=processing,
            tick=tick,
            shimmer_tick=time.monotonic() - self._anim0,
            notice=self.notice,
            choices=self.choices if not processing else None,
            command_menu=menu,
            subtitle=self.subtitle,
            stage=self._stage(),
            stream_text=stream_text,
            console=self.console,
            progress=self.progress,
        )
        line = self.duck.tick()
        if line is not None:
            # The chrome duck reads these off the panel (see MusicLive); the
            # arbiter is the only writer, so features never fight over the bubble.
            panel._duck_say, panel._duck_say_hold, panel._duck_say_seq = line
        self.live.update(panel)
        if self.follow or self.scroll_offset == SCROLL_BOTTOM:
            self.scroll_offset = self._bottom()

    def _say(self, text: str) -> None:
        self.transcript.add_assistant(text)
        self._pin_bottom()

    def _note(self, text: str) -> None:
        self.transcript.add_system(text)
        self._pin_bottom()

    def _bubble(self, text: str, hold: float | None = None) -> None:
        """Give the corner duck an ephemeral line (an ack, a stage quip).

        Additive only: anything the user might scroll back for stays a
        transcript whisper — the bubble fades and leaves no record.
        """
        if self.duck.say(text, hold=hold):
            logger.info("Duck bubble: %s", text)

    def _pin_bottom(self) -> None:
        self.scroll_offset = SCROLL_BOTTOM
        self.follow = True

    def _save(self) -> None:
        if self.project_id:
            save_project_snapshot(self.project_id, self.state)

    def _preamble_add(self, role: str, text: str) -> None:
        preamble = list(self.state.get("_chat_preamble") or [])
        preamble.append({"role": role, "text": text})
        self.state["_chat_preamble"] = preamble

    # ------------------------------------------------------------ command ctx

    def _ctx(self) -> ChatContext:
        def intake_active() -> bool:
            qs = self._qs()
            return qs is not None and not qs.completed

        return ChatContext(
            state=lambda: self.state,
            run_turn=lambda text: self._run_turn(text, echo_user=True),
            add_system=self._note,
            add_artifact=lambda kind: (self.transcript.add_artifact(kind), self._pin_bottom()),
            insert_text=self.composer.insert_text,
            trigger_voice=self._voice,
            trigger_image=self._paste_image,
            export=self._export,
            switch_size=self._switch_size,
            edit_question=self._edit_question,
            request_quit=self._request_quit,
            intake_active=intake_active,
            questionnaire_exists=lambda: self._qs() is not None,
            enter_form=self._form_mode,
            fast_forward=self._fast_forward,
            plan_complete=lambda: bool(self.state.get("sprints")),
        )

    def _request_quit(self) -> None:
        self.quit = True

    def _export(self, scope: str) -> None:
        from yeaboi.ui.session.phases._phases import _plan_export_flow

        stage = self.state.get("pending_review") or ("complete" if self.state.get("sprints") else "project_intake")
        logger.info("Chat: export requested (scope=%s stage=%s)", scope or "ask", stage)
        _plan_export_flow(self.live, self.console, self._key, self.state, stage, scope=scope or "ask")
        self._note("Export finished.")
        self._bubble("Export finished!")

    def _form_mode(self) -> None:
        """Full-screen questionnaire takeover — the legacy card loop, then back to chat.

        Same modal pattern as _export/_capacity_popup: the legacy phase owns
        the screen, drives the same graph, and hands the state back. Reviews
        after intake stay in chat.
        """
        from yeaboi.ui.session.phases._phases_intake import _phase_intake_questions

        qs = self._qs()
        if qs is None:
            # Pre-graph (greeting): the questionnaire only exists after the
            # first invoke, which needs the description as messages[0].
            self._form_requested = True
            self._note("I'll open the form right after you describe the project.")
            return
        if self.dry_run:
            self._note("Form mode is not available in dry-run.")
            return
        if qs.awaiting_confirmation and not qs._awaiting_leave_input and qs.editing_question is None:
            self._note("The questions are done — reply **accept**, or /edit N to change an answer.")
            return
        before_answers = len(qs.answers)
        before_msgs = len(self.state.get("messages", []))
        logger.info("Chat: form takeover start (Q%d, %d answered)", qs.current_question, before_answers)
        self.state = _phase_intake_questions(
            self.live, self.console, self.graph, self.state, self._key, False, return_state_on_esc=True
        )
        self.transcript.invalidate_artifacts()
        qs = self._qs()
        filled = len(qs.answers) - before_answers if qs else 0
        self._note(f"Form closed — filled {filled} answer(s). Back to chat.")
        if len(self.state.get("messages", [])) != before_msgs:
            # The node's last reply is the summary (→ card + accept bubble) or
            # the current question; re-showing it anchors the chat. On an
            # immediate Esc nothing ran, and re-adding the question already in
            # the transcript would duplicate it.
            self._append_reply(streamed="")
        self._save()
        self._drain_consents()
        logger.info("Chat: form takeover end (filled=%d)", filled)

    def _fast_forward(self) -> None:
        """/finish — defaults for everything, then build the plan with no more stops."""
        if self.state.get("sprints"):
            self._note("The plan is already complete — /export saves it.")
            return
        if self._qs() is None:
            # Pre-graph (greeting): the intake needs a description before
            # there is anything to fast-forward — defer, like /form does.
            self._finish_requested = True
            self._note("I'll fast-forward right after you describe the project.")
            return
        self.state["_chat_fast_forward"] = True
        logger.info("Chat: fast-forward enabled (stage=%s)", self._stage())
        qs = self._qs()
        if qs is not None and not qs.completed and not qs.awaiting_confirmation:
            # The intake node handles the literal — one deterministic turn
            # that defaults every remaining question and shows the summary.
            self._run_turn("defaults all", echo_user=True)
            self._note("One **accept** on the summary builds the whole plan — no more stops.")
        elif qs is not None and qs.awaiting_confirmation:
            self._note("Fast mode on — accept the summary and the whole plan builds with no more stops.")
        else:
            self._note("Fast mode on — remaining reviews will be auto-accepted.")
        self._save()

    def _switch_size(self, target_mode: str) -> None:
        from yeaboi.agent.nodes import apply_size_switch

        current = self.state.get("_intake_mode", "")
        label = "Small" if target_mode == "small_project" else "Large"
        if current == target_mode:
            self._note(f"Already planning {label}.")
            return
        if self._qs() is None:
            # Pre-intake: just record the preference; the size exchange honors it.
            self.state["_intake_mode"] = target_mode
            self._note(f"Got it — {label} plan.")
            self._bubble(f"{label} it is!")
            return
        if self.dry_run:
            self._note("Size switching is not available in dry-run.")
            return
        apply_size_switch(self.state, target_mode)
        self._note(f"Switched to {label} — I kept all your answers.")
        # One no-LLM invoke so project_intake produces the first gap question
        # (mirrors the old _switch_to_epic_pending re-entry).
        self._run_turn("", echo_user=False, synthetic=True)

    def _edit_question(self, q_num: int | None) -> None:
        if q_num is not None:
            qs = self._qs()
            if qs is None:
                self._note("Nothing to edit yet.")
                return
            if not (1 <= q_num <= TOTAL_QUESTIONS):
                self._note(f"There is no question {q_num}.")
                return
            # The intake node's review path consumes "edit N" itself.
            self._run_turn(f"edit {q_num}", echo_user=True)
            return
        if self.state.get("pending_review") in _PIPELINE_NODES:
            self.edit_armed = True
            self._note("Edit mode — describe what you'd like changed and press Enter.")
        else:
            self._note("Use /edit N to re-answer question N (see /summary for numbers).")

    # ------------------------------------------------------------- attachments

    def _paste_image(self) -> None:
        self.notice = "Pasting image…"
        self._render()

        def set_notice(msg: str) -> None:
            self.notice = msg

        chip = handle_ctrl_v(
            self.composer.attachments,
            scope_id=self.state.get("_attachment_scope", "") or "planning",
            set_notice=set_notice,
        )
        if chip:
            self.composer.insert_text(chip)
            self.notice = f"Screenshot attached as {chip}"

    def _voice(self) -> None:
        from yeaboi.ui.shared._voice_input import record_voice_input, voice_indicator

        def render_status(status: str, tick: float):
            border, line = voice_indicator(status, tick)
            w, h = self.console.size
            return build_chat_screen(
                self.transcript,
                self.composer,
                self.state,
                width=w,
                height=h,
                scroll_offset=self.scroll_offset,
                scroll_meta=self.scroll_meta,
                notice=line,
                border_override=border,
                subtitle=self.subtitle,
                stage=self._stage(),
                console=self.console,
            )

        spoken = record_voice_input(self.live, self.console, self._key, render_status=render_status)
        if spoken:
            self.composer.insert_text(spoken)

    # ------------------------------------------------------------- graph turns

    def _run_turn(
        self, text: str, *, echo_user: bool, images: list[str] | None = None, synthetic: bool = False
    ) -> bool:
        """One graph turn. Returns False when nothing ran (guardrail block, no graph)."""
        intake_turn = predict_next_node(self.state) == "project_intake"
        if echo_user and not synthetic:
            block = validate_chat_input(text, intake=intake_turn)
            if block is not None:
                logger.info("Chat input blocked: layer=%s len=%d", block.layer, len(text))
                self._note(block.message)
                return False

        if echo_user:
            self.transcript.add_user(text)
            self._pin_bottom()
        logger.info("Chat turn start: len=%d images=%d synthetic=%s", len(text), len(images or []), synthetic)

        if self.graph is None:
            return self._dry_run_turn(text)

        messages = list(self.state.get("messages", []))
        if text:
            messages.append(HumanMessage(content=text))
        invoke_state = {**self.state, "messages": messages}
        if images:
            if intake_turn:
                invoke_state["pasted_images"] = list(self.state.get("pasted_images") or []) + images
            else:
                invoke_state["chat_images"] = images

        buffer: list[str] = []
        cancel = threading.Event()
        result_box: list = [None, None]

        def worker() -> None:
            try:
                result_box[0] = stream_chat_turn(self.graph, invoke_state, buffer.append, cancel=cancel)
            except Exception as exc:  # noqa: BLE001 — classified below on the main thread
                result_box[1] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        start = time.monotonic()
        first_token_logged = False
        set_duck_working(True)  # the corner duck bobs through every wait
        try:
            while thread.is_alive():
                tick = time.monotonic() - start
                stream_text = "".join(buffer)
                if stream_text and not first_token_logged:
                    logger.info("Chat stream first token: %.2fs", tick)
                    first_token_logged = True
                key = self._key(FRAME_TIME_30FPS)
                self._processing_key(key, cancel)
                self._render(processing=True, tick=tick, stream_text=stream_text or None)
        finally:
            set_duck_working(False)
        thread.join()

        if result_box[1] is not None:
            error = result_box[1]
            if isinstance(error, ChatStreamCancelledError):
                self._note("Cancelled.")
                self._bubble("Cancelled.")
                return False
            from yeaboi.ui.session._utils import _classify_api_error

            message = _classify_api_error(error)
            logger.error("Chat turn failed: %s", message)
            self._note(message)
            return False

        if result_box[0] is None:
            # Worker died without a result or an Exception (BaseException) —
            # state must never become None.
            logger.error("Chat turn produced no result — worker died without raising")
            self._note("Something went wrong — nothing was changed. Try again.")
            return False

        self.state = result_box[0]
        self.transcript.invalidate_artifacts()
        self._append_reply(streamed="".join(buffer))
        logger.info("Chat turn done: %.1fs", time.monotonic() - start)
        self._save()
        self._drain_consents()
        self._pin_bottom()
        return True

    def _processing_key(self, key: str, cancel: threading.Event) -> None:
        """Keys during a running turn: scroll works, typing buffers, Esc cancels."""
        if not key:
            return
        if key == "esc":
            cancel.set()
        elif key in ("scroll_up", "pageup", "home", "scroll_down", "pagedown", "end"):
            new = coalesce_scroll(self.scroll_offset, key, self.scroll_meta, self._key)
            if key in ("scroll_up", "pageup", "home"):
                self.follow = False
            self.scroll_offset = new
            if self.scroll_offset >= self._bottom():
                self.follow = True
        elif key == "enter":
            self.notice = "Still working — your message will send when this finishes."
        else:
            self.composer.handle_key(key)

    def _append_reply(self, *, streamed: str) -> None:
        """Append the assistant's reply bubble (or a review card + prompt)."""
        messages = self.state.get("messages", [])
        reply = messages[-1].content if messages and isinstance(messages[-1], AIMessage) else ""
        if not reply:
            return

        qs = self._qs()
        # Intake confirmation summary → card + short bubble instead of the
        # node's markdown wall (the card is the same data, rendered properly).
        if (
            qs is not None
            and qs.awaiting_confirmation
            and not qs._awaiting_leave_input
            and not qs._awaiting_velocity_input
            and qs.current_question > TOTAL_QUESTIONS
        ):
            self.transcript.add_artifact("intake_summary")
            self._say(
                "Here's everything I've got. Reply **accept** to build the plan, "
                "**edit N** to change an answer, or just tell me what's off."
            )
            return

        if qs is not None and not qs.completed:
            reply = decorate_question_for_chat(qs.current_question, reply)
        self._say(reply)

    def _drain_consents(self) -> None:
        from yeaboi.ui.session.phases._phases import _drain_sandbox_consents

        _drain_sandbox_consents(
            self.console,
            self.live,
            self._key,
            lambda t: self._run_turn(t, echo_user=False, synthetic=True),
            self._note,
        )

    # ---------------------------------------------------------------- stages

    def _stage(self) -> str:
        if self.state.get("capacity_override_target", 0) < -1 and not self.dry_run:
            return "capacity"
        pending = self.state.get("pending_review")
        if pending == "project_intake":
            return "intake"  # confirmation gate — the node consumes the reply
        if pending in _PIPELINE_NODES:
            return "review"
        next_node = predict_next_node(self.state) if not self.dry_run else self._dry_next_node()
        if next_node == "project_intake":
            return "intake"
        if next_node in _PIPELINE_NODES:
            if (
                next_node in ("feature_generator", "feature_skip")
                and self.state.get("project_analysis")
                and not self.state.get("_epic_reviewed")
            ):
                return "epic"
            return "pipeline"
        return "chat"

    def _stage_meta(self, node: str) -> tuple[str, str]:
        from yeaboi.repl._ui import _PIPELINE_STEPS, _SPINNER_MESSAGES

        step_node = "feature_generator" if node == "feature_skip" else node
        step = _PIPELINE_STEPS.index(step_node) + 1 if step_node in _PIPELINE_STEPS else 0
        return _SPINNER_MESSAGES.get(node, "Working"), f"[{step}/{len(_PIPELINE_STEPS)}]"

    def _refresh_progress(self, active_node: str | None) -> None:
        """Rebuild the stage checklist from graph state (per stage entry/exit —
        the per-frame animation lives in the screen's _progress_rows)."""
        from yeaboi.repl._ui import _PIPELINE_STEPS, _SPINNER_MESSAGES

        active = "feature_generator" if active_node == "feature_skip" else active_node
        now = time.monotonic()
        prog = self.progress or PipelineProgress(run_started=now)
        stages: list[tuple[str, str]] = []
        done = 0
        for step_node in _PIPELINE_STEPS:
            label = "Formatting epic" if step_node == "epic_review" else _SPINNER_MESSAGES.get(step_node, step_node)
            if step_node == active:
                status = "active"
            elif self.state.get(_PROGRESS_DONE_KEYS[step_node]):
                status = "done"
                done += 1
            else:
                status = "pending"
            stages.append((label, status))
        prog.stages = stages
        prog.total = len(_PIPELINE_STEPS)
        prog.step = min(prog.total, done + (1 if active else 0))
        if active and prog.active_node != active:
            prog.active_node, prog.active_started = active, now
        self.progress = prog

    def _run_pipeline_stage(self) -> bool:
        """Run one pipeline stage. Returns False when the turn failed/was cancelled."""
        node = predict_next_node(self.state) if not self.dry_run else self._dry_next_node()
        label, progress = self._stage_meta(node)
        self.subtitle = f"{label}… {progress}"
        self._refresh_progress(node)
        self._built_this_session = True
        logger.info("Pipeline stage entry (chat): %s", node)
        if self.dry_run:
            self._dry_run_stage(node)
        elif not self._run_turn("continue", echo_user=False, synthetic=True):
            # Failed or cancelled — state is unchanged, so the caller must NOT
            # loop straight back here (that would retry forever with no way in).
            self.subtitle = ""
            self.progress = None
            logger.warning("Pipeline stage failed (chat): %s", node)
            return False
        self.subtitle = ""
        self._refresh_progress(None)  # the artifact landed → its row flips to ✓
        quack_duck()
        self._bubble(_STAGE_QUIPS.get(node, "Done!"))
        if self.bell:
            self.console.bell()
        pending = self.state.get("pending_review")
        if pending in _PIPELINE_NODES and not self.state.get("_chat_fast_forward"):
            # Fast mode: the run loop's auto-accept shows the card itself —
            # showing it here too would duplicate it and prompt for a reply
            # nobody is going to give.
            self._show_review_card(pending)
            self.progress = None  # a review gate pauses the build — card takes over
        return True

    def _show_review_card(self, pending: str, *, prompt: bool = True) -> None:
        kind = {
            "project_analyzer": "analysis",
            "feature_generator": "features",
            "feature_skip": "features",
            "story_writer": "stories",
            "task_decomposer": "tasks",
            "sprint_planner": "sprints",
        }.get(pending, "analysis")
        self.transcript.add_artifact(kind)
        if prompt:
            prompts = [
                "Reply **accept** to continue",
                "**edit** + your changes to refine",
                "/export to save",
                "/finish auto-accepts the rest",
            ]
            if pending == "project_analyzer" and self.state.get("_small_project_oversized"):
                prompts.insert(1, "**switch to large** for a fuller plan (this looks bigger than a small project)")
            self._say(" · ".join(prompts) + ".")
        self._prompted.add(pending)

    def _auto_accept_review(self) -> None:
        """Fast mode: show the artifact, accept it, move on — no input loop."""
        pending = self.state.get("pending_review", "")
        if pending not in self._prompted:
            # /finish typed at an already-shown review gate must not re-add the card.
            self._show_review_card(pending, prompt=False)
        self._note("Auto-accepted (fast mode).")
        self._bubble("Auto-accepted!")
        logger.info("Review decision (chat): auto-accept %s (fast mode)", pending)
        for key in (
            "pending_review",
            "last_review_decision",
            "last_review_feedback",
            "review_feedback_images",
            "_small_project_oversized",
        ):
            self.state.pop(key, None)
        self._prompted.discard(pending)
        self._save()
        self._pin_bottom()

    def _epic_step(self) -> None:
        """Team-style epic reformat + review card before the feature stage."""
        from ._epic import reformat_epic_to_team_style

        self.subtitle = "Formatting epic… [2/6]"
        self._refresh_progress("epic_review")  # _epic_reviewed isn't set yet → row shows active
        self.state["_epic_reviewed"] = True
        self._built_this_session = True
        result_box: list = [None, None]

        def worker() -> None:
            try:
                result_box[0] = reformat_epic_to_team_style(self.state, dry_run=self.dry_run)
            except Exception as exc:  # noqa: BLE001
                result_box[1] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        start = time.monotonic()
        cancel = threading.Event()  # reformat isn't cancellable; keys still buffer
        set_duck_working(True)
        try:
            while thread.is_alive():
                self._processing_key(self._key(FRAME_TIME_30FPS), cancel)
                self._render(processing=True, tick=time.monotonic() - start)
        finally:
            set_duck_working(False)
        thread.join()
        if result_box[1] is not None:
            # reformat_epic_to_team_style catches its own failures, so this is
            # an unexpected error — the original epic stands, but say so.
            logger.error("Epic reformat step failed unexpectedly: %s", result_box[1])
        self.subtitle = ""
        self._refresh_progress(None)
        quack_duck()
        self._bubble("Epic polished!")
        self.transcript.add_artifact("epic")
        if self.state.get("_chat_fast_forward"):
            self._note("Epic auto-accepted (fast mode).")
            logger.info("Epic review (chat): auto-accept (fast mode)")
            self._save()
            return
        self.progress = None  # the epic gate waits for a verdict — card takes over
        self._say(
            "Here's the project epic. Reply **accept** to break it into epics and stories, or **edit** + your changes."
        )
        self._save()
        # Wait for the user's verdict before generating features.
        while not self.quit:
            submit = self._input_loop()
            if submit is None:
                continue
            text = submit.strip()
            if text.startswith("/"):
                dispatch(self._ctx(), text)
                if self.state.get("_chat_fast_forward"):
                    return  # /finish at the epic gate — proceed without a verdict
                continue
            if text.lower() in _ACCEPT_WORDS:
                self.transcript.add_user(text)
                self._pin_bottom()
                return
            # Anything else is edit feedback on the analysis/epic.
            self._apply_edit_feedback("project_analyzer", text.removeprefix("edit").strip() or text)
            return

    def _capacity_popup(self) -> None:
        """Capacity overflow — same three options and assignments as the card flow."""
        from yeaboi.ui.session.phases._phases import _pipeline_choice_screen

        cap = self.state.get("capacity_override_target", 0)
        recommended = abs(cap)
        if self.state.get("_chat_fast_forward"):
            # Fast mode takes the recommended extension — same pick headless
            # auto-drive makes (agent/headless.py).
            self.state["capacity_override_target"] = recommended
            self._note(f"Capacity: extended to {recommended} sprints (fast mode).")
            logger.info("Capacity overflow (chat): auto-extend to %d (fast mode)", recommended)
            return
        original_target = self.state.get("_original_target_sprints", recommended)
        recommended_team = self.state.get("_recommended_team_size", 0)
        current_team = self.state.get("team_size", 1)
        messages = self.state.get("messages", [])
        warning = messages[-1].content.replace("**", "") if messages and isinstance(messages[-1], AIMessage) else ""

        options = [f"Extend to {recommended} sprints"]
        team_can_grow = recommended_team > current_team
        if team_can_grow:
            options.append(f"Keep {original_target} sprints — increase team to {recommended_team} engineers")
        elif recommended_team > 0:
            warning += (
                f"\n\nIncrease team is unavailable — your Jira board has "
                f"{current_team} team member(s), which is already the maximum."
            )
        options.append(f"Keep {original_target} sprints, {current_team} engineer(s) — overload (not recommended)")

        label, progress = self._stage_meta("sprint_planner")
        choice = _pipeline_choice_screen(
            self.live,
            self.console,
            self._key,
            title="Capacity Overflow",
            subtitle=warning,
            options=options,
            step=5,
            total=5,
            stage_label=label,
            progress=progress,
        )
        overload_index = len(options) - 1
        team_index = 1 if team_can_grow else -1
        if choice == team_index:
            self.state["capacity_override_target"] = -1
            self.state["_capacity_team_override"] = recommended_team
        elif choice == overload_index:
            self.state["capacity_override_target"] = -1
        else:
            self.state["capacity_override_target"] = recommended
        self.state["_capacity_warning"] = {"text": warning, "recommended": recommended}
        self._note(f"Capacity: {options[choice if choice is not None else 0]}")

    # ----------------------------------------------------------- review logic

    def _review_reply(self, text: str) -> None:
        pending = self.state.get("pending_review", "")
        lowered = text.lower().strip()
        block = validate_chat_input(text)
        if block is not None:
            logger.info("Chat input blocked: layer=%s len=%d", block.layer, len(text))
            self._note(block.message)
            return
        if self.edit_armed:
            self.edit_armed = False
            self._apply_edit_feedback(pending, text)
            return
        if lowered in _ACCEPT_WORDS:
            self.transcript.add_user(text)
            logger.info("Review decision (chat): accept %s", pending)
            for key in (
                "pending_review",
                "last_review_decision",
                "last_review_feedback",
                "review_feedback_images",
                "_small_project_oversized",
            ):
                self.state.pop(key, None)
            self._save()
            self._pin_bottom()
            return
        if lowered in ("switch to large", "switch") and pending == "project_analyzer":
            self.transcript.add_user(text)
            self._switch_size("smart")
            return
        if lowered in ("sync jira", "sync azure", "sync azure devops", "sync"):
            self._tracker_sync(lowered, pending)
            return
        # Everything else is edit feedback — "refine by chatting".
        feedback = text.removeprefix("edit").removeprefix("regenerate").strip() or text
        self._apply_edit_feedback(pending, feedback)

    def _apply_edit_feedback(self, pending: str, feedback: str) -> None:
        from yeaboi.repl._review import _clear_downstream_artifacts, _serialize_artifacts_for_review

        if self.dry_run:
            self._note("Edits are not available in dry-run — reply accept to continue.")
            return
        self.transcript.add_user(feedback)
        logger.info("Review decision (chat): edit %s (len=%d)", pending, len(feedback))
        serialized = _serialize_artifacts_for_review(self.state, pending)
        _clear_downstream_artifacts(self.state, pending)
        self.state["last_review_decision"] = ReviewDecision.EDIT
        images = referenced_images(feedback, self.composer.attachments)
        self.composer.attachments = []
        if images:
            self.state["review_feedback_images"] = images
        self.state["last_review_feedback"] = (
            f"{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}" if serialized else feedback
        )
        self.state.pop("pending_review", None)
        self._pin_bottom()
        # The next loop pass re-runs the stage with the feedback.

    def _tracker_sync(self, lowered: str, pending: str) -> None:
        from yeaboi.ui.session.phases._phases import _get_active_trackers, _handle_tracker_sync

        trackers = _get_active_trackers()
        if not trackers:
            self._note("No tracker configured — connect Jira or Azure DevOps first.")
            return
        tracker = "azdevops" if "azure" in lowered else ("jira" if "jira" in lowered else trackers[0])
        if tracker not in trackers:
            self._note(f"{tracker} is not configured.")
            return
        label, progress = self._stage_meta(pending or "sprint_planner")
        result = _handle_tracker_sync(
            self.live,
            self.console,
            self._key,
            self.state,
            pending or "sprint_planner",
            label,
            progress,
            5,
            5,
            tracker=tracker,
        )
        if result is not None:
            self.state = result
            self._save()
            self._note("Tracker sync finished.")
            self._bubble("Synced!")
        else:
            self._note("Tracker sync cancelled.")

    # ------------------------------------------------------------ input loop

    def _input_loop(self) -> str | None:
        """Wait for one submission. Returns the text, or None if quit/Esc-quit."""
        while not self.quit:
            self._render()
            key = self._key(FRAME_TIME_30FPS)
            if not key:
                self._idle_tick()
                continue
            self._idle_since = time.monotonic()
            skip_duck_entrance()  # typing beats choreography (no-op once settled)
            if self.notice and key != "":
                self.notice = ""

            if key == "esc":
                now = time.monotonic()
                if now - self._esc_at <= _ESC_WINDOW_SECONDS:
                    self.quit = True
                    return None
                self._esc_at = now
                self.notice = "Press Esc again to leave — progress is saved."
                continue

            # Choice navigation only while the composer is empty (one rule).
            if self.choices is not None and self.choices.options and self.composer.is_empty():
                if key in ("up", "scroll_up"):
                    self.choices.highlight = (self.choices.highlight - 1) % len(self.choices.options)
                    continue
                if key in ("down", "scroll_down"):
                    self.choices.highlight = (self.choices.highlight + 1) % len(self.choices.options)
                    continue
                if key == " " and self.choices.multi:
                    i = self.choices.highlight
                    label, checked = self.choices.options[i]
                    self.choices.options[i] = (label, not checked)
                    continue
                if key == "enter":
                    return self._choice_answer()

            if key in ("pageup", "pagedown", "home", "end") or key in ("scroll_up", "scroll_down"):
                new = coalesce_scroll(self.scroll_offset, key, self.scroll_meta, self._key)
                if key in ("scroll_up", "pageup", "home"):
                    self.follow = False
                self.scroll_offset = new
                if self.scroll_offset >= self._bottom():
                    self.follow = True
                continue

            if key == "tab":
                # Complete the /token at the cursor in place — works mid-draft
                # and never clobbers the rest of the buffer.
                word, start = self.composer.cursor_word()
                if word.startswith("/"):
                    matches = matching_commands(self._ctx(), word)
                    if matches:
                        completed = f"/{matches[0].name} "
                        line = self.composer.lines[self.composer.row]
                        self.composer.lines[self.composer.row] = line[:start] + completed + line[start + len(word) :]
                        self.composer.col = start + len(completed)
                continue

            # Transcript scroll on bare arrows when there's nothing to edit.
            if key in ("up", "down") and self.composer.is_empty() and len(self.composer.lines) == 1:
                new = coalesce_scroll(self.scroll_offset, key, self.scroll_meta, self._key)
                if key == "up":
                    self.follow = False
                self.scroll_offset = new
                if self.scroll_offset >= self._bottom():
                    self.follow = True
                continue

            if key == "enter":
                inline = self._pop_inline_command()
                if inline is not None:
                    return inline

            event = self.composer.handle_key(key)
            if isinstance(event, Submit):
                text = event.text
                self.composer.reset()
                return text
            if isinstance(event, Voice):
                self._voice()
            elif isinstance(event, PasteImage):
                self._paste_image()
            elif isinstance(event, Truncated):
                self.notice = f"Paste truncated at {MAX_CHAT_INPUT_CHARS:,} characters."
        return None

    def _pop_inline_command(self) -> str | None:
        """Pop an exact /command token typed mid-draft, preserving the draft.

        Enter on e.g. "building an app /small" runs /small and keeps
        "building an app" in the composer. The token is *returned* as a
        normal /-submission so the outer loop dispatches it exactly like a
        whole-message command — no nested graph turns from inside the input
        loop. Only an exact, available command name qualifies: prose tokens
        like "/usr/bin" (and whole-message commands, which the normal submit
        path already dispatches) fall through untouched.
        """
        from yeaboi.ui.session.chat._commands import exact_command

        word, start = self.composer.cursor_word()
        if not word.startswith("/"):
            return None
        text = self.composer.text().strip()
        if text == word or text.startswith("/"):
            return None
        if exact_command(self._ctx(), word) is None:
            return None
        line = self.composer.lines[self.composer.row]
        before, after = line[:start], line[start + len(word) :]
        if before.endswith(" ") and (not after or after.startswith(" ")):
            before = before[:-1]
        self.composer.lines[self.composer.row] = before + after
        self.composer.col = len(before)
        return word

    def _choice_answer(self) -> str:
        assert self.choices is not None
        checked = [label for label, is_checked in self.choices.options if is_checked]
        if self.choices.multi and checked:
            return ", ".join(checked)
        return self.choices.options[self.choices.highlight][0]

    # -------------------------------------------------------------- greeting

    def _greeting_flow(self) -> None:
        """Greeting + size + description — all pre-graph, all in _chat_preamble."""
        # The one-time entrance: he waddles into his corner while the greeting
        # is read (chrome-only, non-blocking; the resume path never gets here).
        start_duck_entrance()
        self._bubble("Quack — let's plan!", hold=4.0)
        logger.info("Duck entrance started (greeting)")
        self._say(GREETING_TEXT)
        self._preamble_add("ai", GREETING_TEXT)
        preset_mode = self.state.get("_intake_mode", "")
        if preset_mode:
            label = "Small" if preset_mode == "small_project" else "Large"
            note = f"(Planning {label} — switch any time with /small · /large.)"
            self._note(note)
        else:
            # Offer the 1/2 size pick up front — answering it is optional; a
            # description works just as well (the classifier sizes it). The
            # third row is the form preference (feature-parity with /form).
            self.choices = self._size_choices(include_form=True)
        prefill = self.initial_description
        if not prefill and self.dry_run:
            # Same demo description the old dry-run description editor used —
            # the developer just hits Enter to move on quickly.
            prefill = (
                "We're building a mobile app for restaurant reservations. "
                "The team is 4 developers, we use React Native and Node.js, "
                "and we need to launch an MVP in 3 months."
            )
        if prefill:
            self.composer.set_text(prefill)
            self.composer.row = len(self.composer.lines) - 1
            self.composer.col = len(self.composer.lines[-1])

        description = ""
        mode = preset_mode
        while not self.quit and not (description and mode):
            submit = self._input_loop()
            if submit is None:
                return
            if submit.startswith("/"):
                dispatch(self._ctx(), submit)
                mode = self.state.get("_intake_mode", mode)
                if mode:
                    self.choices = None  # size settled — the pick is moot
                continue
            picked = self.choices is not None and any(submit == label for label, _sel in self.choices.options)
            form_offered = self.choices is not None and any(
                label == _FORM_CHOICE_LABEL for label, _sel in self.choices.options
            )
            self.transcript.add_user(submit)
            self.choices = None

            # Typed "1"/"2" work through parse_size_reply; "3" (and "form")
            # need the same parity while the third row is on offer.
            if (picked and submit == _FORM_CHOICE_LABEL) or (form_offered and submit.strip().lower() in ("3", "form")):
                # A form preference, not a size answer — the questionnaire
                # needs a description (messages[0]) first, so keep collecting
                # in chat and open the form right after the first invoke.
                self._form_requested = True
                self._preamble_add("user", submit)
                ack = "You got it — I'll open the form once I know the basics. First, what are you building, and why?"
                self._say(ack)
                self._preamble_add("ai", ack)
                continue

            if not description and not mode:
                if picked:
                    # A picked choice row is a size answer by construction —
                    # no classifier call, and it stays in the preamble.
                    mode = self._choice_mode(submit) or ""
                    self._preamble_add("user", submit)
                else:
                    resolved_mode, resolved_desc = self._resolve_first_message(submit)
                    mode = resolved_mode or mode
                    description = resolved_desc
                    if not resolved_desc:
                        # A bare size answer belongs to the preamble; a description
                        # becomes messages[0] and must NOT be duplicated there.
                        self._preamble_add("user", submit)
            elif not mode:
                mode = parse_size_reply(submit) or (self._choice_mode(submit))
                self._preamble_add("user", submit)
            else:
                description = submit

            if not description and mode:
                label = "Small" if mode == "small_project" else "Large"
                prompt = f"{label} it is. Now tell me about the project — what are you building, and why?"
                self._say(prompt)
                self._preamble_add("ai", prompt)
            elif description and not mode:
                self._ask_size()
            elif description and mode:
                announce = (
                    f"Sounds like a {'Small' if mode == 'small_project' else 'Large'} plan — "
                    "switch any time with /small · /large."
                )
                self._say(announce)
                self._preamble_add("ai", announce)

        if self.quit:
            return
        self.state["_intake_mode"] = mode
        self.state["_chat_greeting_done"] = True
        self.choices = None
        images = referenced_images(description, self.composer.attachments)
        self.composer.attachments = []
        if images:
            self.state["pasted_images"] = images
        # First graph invoke: the description is messages[0] — byte-identical
        # entry conditions to the old card flow (see agent/chat_intake.py).
        self._save()
        if self.dry_run:
            self._dry_run_bootstrap()
        else:
            self._run_turn(description, echo_user=False, synthetic=True)

    def _resolve_first_message(self, text: str) -> tuple[str | None, str]:
        if self.dry_run:
            # No LLM calls in dry-run — a bare size answer still parses, and a
            # description falls through to the deterministic size question.
            direct = parse_size_reply(text)
            return direct, "" if direct else text
        try:
            return resolve_intake_mode(text)
        except Exception as exc:
            from yeaboi.ui.session._utils import _classify_api_error

            self._note(_classify_api_error(exc))
            return None, text

    def _ask_size(self) -> None:
        self._say(SIZE_QUESTION_TEXT)
        self._preamble_add("ai", SIZE_QUESTION_TEXT)
        self.choices = self._size_choices()

    def _size_choices(self, *, include_form: bool = False) -> ChoiceRows:
        options = [
            ("Small — a ticket or two, one quick sprint", False),
            ("Large — epics and multiple sprints", False),
        ]
        if include_form:
            options.append((_FORM_CHOICE_LABEL, False))
        return ChoiceRows(options=options, highlight=0, multi=False)

    def _choice_mode(self, submit: str) -> str | None:
        lowered = submit.lower()
        if lowered.startswith("small"):
            return "small_project"
        if lowered.startswith("large"):
            return "smart"
        return None

    # ---------------------------------------------------------------- resume

    def _rebuild_transcript(self) -> None:
        for entry in self.state.get("_chat_preamble") or []:
            if entry.get("role") == "user":
                self.transcript.add_user(entry.get("text", ""))
            else:
                self.transcript.add_assistant(entry.get("text", ""))
        for message in self.state.get("messages", []):
            if isinstance(message, HumanMessage) and isinstance(message.content, str):
                self.transcript.add_user(message.content)
            elif isinstance(message, AIMessage) and isinstance(message.content, str) and message.content:
                self.transcript.add_assistant(message.content)
        for kind, key in (
            ("analysis", "project_analysis"),
            ("features", "features"),
            ("stories", "stories"),
            ("tasks", "tasks"),
            ("sprints", "sprints"),
        ):
            if self.state.get(key):
                self.transcript.add_artifact(kind)
        if self.state.get("sprints"):
            # A finished plan resumes with its recap card — silently: the
            # celebration (quack + shades) fired when the build completed and
            # must not replay on every resume (_built_this_session gates it).
            self.transcript.add_artifact("recap")
        self._pin_bottom()
        logger.info("Chat transcript rebuilt: %d messages", len(self.transcript.messages))

    # --------------------------------------------------------------- dry run

    def _dry_next_node(self) -> str:
        for key, node in (
            ("project_analysis", "project_analyzer"),
            ("features", "feature_generator"),
            ("stories", "story_writer"),
            ("tasks", "task_decomposer"),
            ("sprints", "sprint_planner"),
        ):
            if not self.state.get(key):
                return node
        return "agent"

    def _dry_run_bootstrap(self) -> None:
        from yeaboi.ui.session._dry_run import load_dry_run_state

        full = load_dry_run_state()
        if full is None:
            self._note("Dry-run state unavailable.")
            self.quit = True
            return
        self._dry_full_state = full
        state = dict(full)
        for key in ("project_analysis", "epics", "features", "stories", "tasks", "sprints"):
            state.pop(key, None)
        qs = state.get("questionnaire")
        if isinstance(qs, QuestionnaireState):
            qs.completed = False
            qs.awaiting_confirmation = True
        state["pending_review"] = "project_intake"
        state["_intake_mode"] = self.state.get("_intake_mode", "smart")
        state["_chat_greeting_done"] = True
        state["_chat_preamble"] = self.state.get("_chat_preamble", [])
        state["_attachment_scope"] = self.state.get("_attachment_scope", "")
        self.state = state
        self.transcript.add_artifact("intake_summary")
        self._say("Here's everything I've got (dry run). Reply **accept** to build the plan.")

    def _dry_run_turn(self, text: str) -> bool:
        qs = self._qs()
        if qs is not None and qs.awaiting_confirmation:
            qs.completed = True
            qs.awaiting_confirmation = False
            self.state.pop("pending_review", None)
        return True

    def _dry_run_stage(self, node: str) -> None:
        from yeaboi.ui.session._dry_run import build_stage_snapshot

        start = time.monotonic()
        cancel = threading.Event()  # nothing to cancel; keys buffer as type-ahead
        set_duck_working(True)
        try:
            while time.monotonic() - start < _DRY_STAGE_SECONDS:
                self._processing_key(self._key(FRAME_TIME_30FPS), cancel)
                self._render(processing=True, tick=time.monotonic() - start)
        finally:
            set_duck_working(False)
        if self._dry_full_state is None:
            from yeaboi.ui.session._dry_run import load_dry_run_state

            self._dry_full_state = load_dry_run_state() or {}
        snapshot = build_stage_snapshot(self._dry_full_state, node)
        snapshot["_intake_mode"] = self.state.get("_intake_mode", "smart")
        snapshot["_chat_greeting_done"] = True
        snapshot["_chat_preamble"] = self.state.get("_chat_preamble", [])
        snapshot["_attachment_scope"] = self.state.get("_attachment_scope", "")
        if self.state.get("_chat_fast_forward"):
            # A real graph echoes this state field back; the snapshot swap must
            # not silently drop fast mode mid-pipeline.
            snapshot["_chat_fast_forward"] = True
        self.state = snapshot
        self.transcript.invalidate_artifacts()

    # ------------------------------------------------------------------- run

    def run(self) -> dict | None:
        if self.state.get("messages") or self.state.get("_chat_greeting_done"):
            self._rebuild_transcript()
            self._drain_consents()
        else:
            self._greeting_flow()
            if self.quit or not self.state.get("_chat_greeting_done"):
                return None

        if self._form_requested and self._qs() is not None:
            # Deferred /form (or the greeting form pick): the first invoke has
            # created the questionnaire, so the takeover can run now.
            self._form_requested = False
            self._form_mode()
        if self._finish_requested and self._qs() is not None:
            # Deferred /finish: same rule — the questionnaire exists now.
            self._finish_requested = False
            self._fast_forward()

        while not self.quit:
            stage = self._stage()
            if stage == "capacity":
                self._capacity_popup()
                continue
            if stage == "epic":
                self._epic_step()
                continue
            if stage == "pipeline":
                if not self._run_pipeline_stage():
                    # Failed or cancelled: wait for the user instead of
                    # retrying in a hot loop. Enter retries; commands work.
                    self._note("Stage stopped — send any message to retry, /export to save what's done, or /quit.")
                    submit = self._input_loop()
                    if submit is None:
                        break
                    if submit.startswith("/"):
                        dispatch(self._ctx(), submit)
                continue

            if stage == "review" and self.state.get("_chat_fast_forward"):
                self._auto_accept_review()
                continue

            if stage == "review" and self.state.get("pending_review") not in self._prompted:
                self._show_review_card(self.state.get("pending_review", ""))

            if stage == "intake":
                view = derive_question_view(self.state)
                self.subtitle = " · ".join(s for s in (view.progress, view.phase_label) if s)
                self._coach_phase()
                if view.choices:
                    highlight = next((i for i, (_o, sel) in enumerate(view.choices) if sel), 0)
                    self.choices = ChoiceRows(options=list(view.choices), highlight=highlight, multi=view.multi_select)
                else:
                    self.choices = None
                    if view.suggestion and self.composer.is_empty() and self._prefilled_q != view.current_question:
                        self.composer.set_text(view.suggestion)
                        # set_text resets the cursor to 0,0 — typing must land
                        # after the suggestion, not before it.
                        self.composer.row = len(self.composer.lines) - 1
                        self.composer.col = len(self.composer.lines[-1])
                        self._prefilled_q = view.current_question
            else:
                self.choices = None
                self.subtitle = "" if stage != "chat" else "Plan complete — keep refining, or /export"
                if stage == "chat":
                    self.progress = None  # the build is over — drop the checklist
                    self._maybe_celebrate_completion()
                if stage == "chat" and self.state.pop("_chat_fast_forward", None):
                    self._note("Fast mode done — the plan is complete. /export saves it.")
                    self._save()

            submit = self._input_loop()
            if submit is None:
                break
            if submit.startswith("/"):
                dispatch(self._ctx(), submit)
                continue

            if stage == "review":
                self._prompted.discard(self.state.get("pending_review", ""))
                self._review_reply(submit)
                continue

            # Intake / confirmation / free chat: resolve choices, then one turn.
            answer = submit
            qs = self._qs()
            if stage == "intake" and qs is not None and not qs.completed:
                if qs.editing_question is not None:
                    answer = self._resolve_choice(answer, qs.editing_question)
                elif not qs.awaiting_confirmation:
                    dynamic = qs._follow_up_choices.get(qs.current_question)
                    if dynamic:
                        from yeaboi.repl._questionnaire import _resolve_dynamic_choice

                        answer = _resolve_dynamic_choice(answer, dynamic)
                    else:
                        answer = self._resolve_choice(answer, qs.current_question)
            images = referenced_images(answer, self.composer.attachments)
            self.composer.attachments = []
            self.choices = None
            self._run_turn(answer, echo_user=True, images=images)

        self._save()
        logger.info("Chat session ended: quit=%s messages=%d", self.quit, len(self.transcript.messages))
        return self.state

    def _coach_phase(self) -> None:
        """Quack + a short lead-in when the intake crosses a phase boundary."""
        from ._duck import COACH_HOLD, PHASE_QUIPS, PRIORITY_COACH

        qs = self._qs()
        phase = str(qs.current_phase) if qs is not None else ""
        if not phase or phase == self._last_phase:
            return
        self._last_phase = phase
        quip = PHASE_QUIPS.get(phase)
        if quip and self.duck.say(quip, priority=PRIORITY_COACH, hold=COACH_HOLD):
            quack_duck()
            logger.info("Duck coaching (phase): %s", quip)

    def _intake_hint(self, q: int) -> str | None:
        """The one idle hint a question may earn, keyed off what it accepts."""
        from yeaboi.prompts.intake import QUESTION_DEFAULTS, is_choice_question

        if q > 20:
            return "/finish builds the rest with defaults"
        if is_choice_question(q):
            return "Type the number — or ↑/↓ then Enter"
        if q in QUESTION_DEFAULTS:
            return "/defaults fills this phase for you"
        return None

    def _idle_tick(self) -> None:
        """No key this frame — hints and idle tips ride the quiet moments.

        Cheap by construction (a couple of clock compares per frame); the say()
        calls are no-ops while the same line is already showing.
        """
        now = time.monotonic()
        if not self.composer.is_empty():
            return
        stage = self._stage()
        if stage == "intake" and self.state.get("messages"):
            qs = self._qs()
            q = qs.current_question if qs is not None and not qs.completed else 0
            if q > 0 and q != self._hinted_q and now - self._idle_since > _IDLE_HINT_SECONDS:
                hint = self._intake_hint(q)
                self._hinted_q = q  # one shot per question, even when it has no hint
                if hint:
                    from ._duck import COACH_HOLD, PRIORITY_COACH

                    if self.duck.say(hint, priority=PRIORITY_COACH, hold=COACH_HOLD, now=now):
                        logger.info("Duck coaching (idle hint): %s", hint)
            return
        # Rotating tips: only where nothing is in flight and nothing is asked —
        # the greeting (pre-description) and the post-plan free chat.
        if stage == "chat" or (stage == "intake" and not self.state.get("messages")):
            if now - self._idle_since < _IDLE_TIP_AFTER_SECONDS:
                return
            from yeaboi.ui.shared._tips import TIP_ROTATE_SECONDS, current_tip

            from ._duck import PRIORITY_TIP

            _idx, tip = current_tip(now - self._anim0)
            text = tip.text.split("Tip:", 1)[-1].strip()
            # No logging here: this runs per frame and rotates every 6s — the
            # arbiter's no-op-on-same-text rule keeps it cheap.
            self.duck.say(text, priority=PRIORITY_TIP, hold=TIP_ROTATE_SECONDS - 1.0, now=now)

    def _maybe_celebrate_completion(self) -> None:
        """One-time completion beat: recap card + duck celebration.

        Fires only on the transition — a pipeline stage ran in THIS session
        (resume shows the recap silently via _rebuild_transcript) and the
        recap isn't already in the transcript.
        """
        if not self.state.get("sprints") or not self._built_this_session:
            return
        if any(m.artifact_kind == "recap" for m in self.transcript.messages):
            return
        self.transcript.add_artifact("recap")
        self._say(
            "That's the whole plan — every stage is done. Keep refining anything you like, or /export to save it."
        )
        quack_duck()
        poke_duck()  # the double-shades gag — he's earned it
        self._bubble("Quack! Plan's done.")
        logger.info("Plan complete (chat): recap card + celebration")
        self._pin_bottom()

    def _resolve_choice(self, answer: str, q_num: int) -> str:
        from yeaboi.repl._questionnaire import _resolve_choice_input

        return _resolve_choice_input(answer, q_num)
