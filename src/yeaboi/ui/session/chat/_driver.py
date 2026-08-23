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
from yeaboi.agent.chat_session import (
    ACCEPT_WORDS,
    PIPELINE_NODES,
    PROGRESS_DONE_KEYS,
    Accept,
    AwaitConfirm,
    ChatSession,
    Done,
    SwitchSize,
    Token,
    TrackerSync,
    at_intake_summary,
    at_prior_art,
    clear_review_state,
    next_node,
    replay_plan,
    reply_event,
    review_gate,
    review_verdict,
)

# The decision layer: which stage the conversation is in, what a reply becomes,
# how a review verdict reads. This module renders those answers; it does not
# make them, so every other surface driving the same graph can reuse them.
from yeaboi.agent.chat_session import CONFIRM_VERDICT_PROMPT as _CONFIRM_VERDICT_PROMPT
from yeaboi.agent.chat_session import PRIOR_ART_VERDICT_PROMPT as _PRIOR_ART_VERDICT_PROMPT
from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState, ReviewDecision
from yeaboi.agent.streaming import ChatStreamCancelledError
from yeaboi.input_guardrails import validate_chat_input
from yeaboi.persistence import save_project_snapshot
from yeaboi.ui.shared._animations import FRAME_TIME_30FPS
from yeaboi.ui.shared._attachments import handle_ctrl_v, referenced_images
from yeaboi.ui.shared._input import set_text_entry, take_paste_dropped
from yeaboi.ui.shared._music_bar import (
    poke_duck,
    quack_duck,
    set_duck_working,
    skip_duck_entrance,
    start_duck_entrance,
)
from yeaboi.ui.shared._scroll import SCROLL_BOTTOM, coalesce_scroll

from ._commands import ChatContext, dispatch, matching_commands
from ._composer import (
    ChatComposer,
    Cleared,
    PasteImage,
    Restored,
    Submit,
    Truncated,
    Voice,
    clear_notice,
    paste_notice,
)
from ._duck import ChatDuck
from ._question_view import derive_question_view
from ._screen import ChoiceRows, PipelineProgress, build_chat_screen
from ._transcript import ChatTranscript

logger = logging.getLogger(__name__)

# What the duck quacks as each pipeline stage completes.
_STAGE_QUIPS = {
    "project_analyzer": "Analysis done!",
    "feature_skip": "Epics drawn up!",
    "feature_generator": "Epics drawn up!",
    "story_writer": "Stories done!",
    "task_decomposer": "Tasks sliced!",
    "sprint_planner": "Sprints packed!",
}

_FORM_CHOICE_LABEL = "Fill it out as a form instead"
_ESC_WINDOW_SECONDS = 2.0
_DRY_STAGE_SECONDS = 1.5  # fake per-stage delay in --dry-run (patched to 0 in tests)
_IDLE_HINT_SECONDS = 8.0  # stuck on a question this long → the duck offers a hint
_WORK_QUIP_SECONDS = 5.0  # a working wait this long → the duck starts entertaining
_BUBBLE_MIN_COLS = 12  # narrower than this and a bubble is skipped, not squeezed


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
    stop_after_intake: bool = True,
) -> dict | None:
    """Run the intake conversation. Returns the final state (or None on quit pre-description).

    stop_after_intake (the production default): the chat owns the greeting, the
    size pick, the questions and the summary confirmation, then hands the state
    back so the card pipeline can run the reviews. False keeps the original
    end-to-end chat (the pipeline/review/epic/capacity path below) — used by the
    tests that cover it.
    """
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
        stop_after_intake=stop_after_intake,
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
        stop_after_intake: bool = True,
    ) -> None:
        self.live = live
        self.console = console
        # The session owns the graph and the state; this class owns the screen.
        self.session = ChatSession(graph, graph_state, dry_run=dry_run)
        self._key = _key
        self.project_id = project_id
        self.bell = bell
        self.dry_run = dry_run
        self.initial_description = initial_description
        self.stop_after_intake = stop_after_intake

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
        from yeaboi.config import is_duck_enabled

        self.duck.mute(not is_duck_enabled())  # honour a persisted /duck or Settings mute
        self.progress: PipelineProgress | None = None  # stage checklist while building
        self._built_this_session = False  # a pipeline stage ran here (gates the celebration)
        self._last_phase = ""  # intake phase last seen (quack on boundary)
        self._hinted_q = -1  # question already idle-hinted (one per question)
        self._work_quip_idx = -1  # last working-quip slot shown (reset per wait)
        self._confirm_free_text = False  # Tell-me pick: next gate pass is composer-only
        self._idle_since = time.monotonic()  # last keypress — feeds hints + idle tips

    # ------------------------------------------------------------------ utils

    @property
    def state(self) -> dict:
        return self.session.state

    @state.setter
    def state(self, value: dict) -> None:
        # The modal takeovers (form mode, the answer browser, dry-run stage
        # snapshots) hand back a whole new state — one owner, one assignment.
        self.session.state = value

    @property
    def graph(self):
        return self.session.graph

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
            # The bubble may only use the empty margin RIGHT of the reading
            # column — never the composer/transcript (user feedback: a wide
            # bubble over the Message box reads as interference). Too narrow →
            # skipped entirely; the transcript already carries anything durable.
            text, hold, seq = line
            room = self._bubble_room(w)
            if room >= _BUBBLE_MIN_COLS:
                if len(text) > room:
                    text = text[: max(1, room - 1)].rstrip() + "…"
                panel._duck_say, panel._duck_say_hold, panel._duck_say_seq = text, hold, seq
        self.live.update(panel)
        if self.follow or self.scroll_offset == SCROLL_BOTTOM:
            self.scroll_offset = self._bottom()

    def _bubble_room(self, width: int) -> int:
        """Text columns a bubble may use: the gap between the reading column's
        right edge and the duck, minus the bubble borders/tail/gap (7 cols)."""
        from ._screen import _DUCK_LANE, _column_metrics

        col_w, margin = _column_metrics(width)
        return (width - _DUCK_LANE) - (margin + col_w + 4) - 7

    def _say(self, text: str) -> None:
        self.transcript.add_assistant(text)
        self._pin_bottom()

    def _note(self, text: str) -> None:
        self.transcript.add_system(text)
        self._pin_bottom()

    def _composer_cleared(self, event: Cleared | Restored) -> None:
        """Report a Ctrl+U clear or its undo.

        Logged as well as shown: it is the one destructive thing the box does,
        so "my draft vanished" needs to be answerable from the log.
        """
        logger.info(
            "Chat composer %s: chars=%d images=%d",
            "cleared" if isinstance(event, Cleared) else "restored",
            event.chars,
            event.images,
        )
        self.notice = clear_notice(event)

    def _paste_truncated(self, event: Truncated) -> None:
        """Report a paste that did not fit — loudly, and in two places.

        The hint line is gone on the very next keypress and clips below ~120
        columns, so the same sentence also goes into the transcript, which wraps
        and stays put. Someone who pastes a document and keeps typing must still
        be able to find out that half of it never arrived.
        """
        message = paste_notice(event)
        logger.info("Chat paste truncated: offered=%d kept=%d dropped=%d", event.offered, event.kept, event.dropped)
        self.notice = message
        self._note(message)

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
            toggle_duck=self._toggle_duck,
            show_questions=self._show_questions,
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

    def _edit_answers(self) -> None:
        """Full-screen answer browser at the review gate — the pre-chat accordion.

        Same modal pattern as _form_mode: the legacy phase owns the screen and
        hands the state back. It is the accordion rather than a chat affordance
        because the question that matters here — "what can I actually change?" —
        is answered by seeing every question next to its answer, which a
        transcript note listing this run's planned questions cannot do.
        """
        from yeaboi.ui.session.phases._phases_review import _edit_accordion_browse

        qs = self._qs()
        if qs is None:
            self._note("Nothing to edit yet.")
            return
        # The dry-run branch of the accordion edits qs.answers in place without
        # touching messages; the graph branch appends messages. Snapshot both,
        # since which one moved decides how the chat re-anchors below.
        before_answers = dict(qs.answers)
        before_msgs = len(self.state.get("messages", []))
        logger.info("Chat: answer browser start (%d answered)", len(before_answers))
        result = _edit_accordion_browse(
            self.live,
            self.console,
            self.graph,
            self.state,
            self._key,
            False,
            return_state_on_esc=True,
            edit_hint="Enter to edit · ↑/↓ browse · Esc back to chat",
        )
        if result is not None:
            self.state = result
        # The "Your answers" card renders live from graph_state and caches its
        # wrapped lines — without this it would redraw the pre-edit answers.
        self.transcript.invalidate_artifacts()
        qs = self._qs()
        after_answers = dict(qs.answers) if qs else {}
        changed = sorted(
            q for q in set(before_answers) | set(after_answers) if before_answers.get(q) != after_answers.get(q)
        )
        replied = len(self.state.get("messages", [])) != before_msgs
        if changed:
            self._note("Updated " + ", ".join(f"Q{q}" for q in changed) + ".")
        elif not replied:
            self._note("No changes — back to the review.")
        if replied:
            # The node ran: its last reply is the summary (→ card + verdict) or
            # whatever it asked next. _append_reply routes on the same gate
            # predicate, so this is one call for both.
            self._append_reply(streamed="")
        elif changed and self._at_intake_summary():
            # Dry-run edits move answers without messages — re-post the card.
            self.transcript.add_artifact("intake_summary")
            self._say(_CONFIRM_VERDICT_PROMPT)
        self._save()
        self._drain_consents()
        logger.info("Chat: answer browser end (changed=%s)", changed)

    def _fast_forward(self) -> None:
        """/finish — default every remaining answer so the questions are done in one go.

        It ends at the summary: the review gates after it belong to the card
        pipeline, which stops at each one. _chat_fast_forward is still set
        because the end-to-end chat path (stop_after_intake=False) reads it;
        the handoff in run() pops it.
        """
        if self.state.get("sprints"):
            self._note("The plan is already complete — /export saves it.")
            return
        if self.state.get("_chat_fast_forward"):
            # /finish is a toggle: the second call is the graceful exit.
            self._stop_fast_mode("/finish toggle")
            self._note("Fast mode off — I'll stop at each review again.")
            self._save()
            return
        if self._qs() is None:
            # Pre-graph (greeting): the intake needs a description before
            # there is anything to fast-forward — defer, like /form does.
            if self._finish_requested:
                self._finish_requested = False
                self._note("Okay, no fast-forward — I'll ask the questions one by one.")
                return
            self._finish_requested = True
            self._note("I'll fast-forward right after you describe the project. /finish again cancels.")
            return
        self.state["_chat_fast_forward"] = True
        logger.info("Chat: fast-forward enabled (stage=%s)", self._stage())
        qs = self._qs()
        if qs is not None and not qs.completed and not qs.awaiting_confirmation:
            # The intake node handles the literal — one deterministic turn
            # that defaults every remaining question and shows the summary.
            self._run_turn("defaults all", echo_user=True)
            self._note("That's every question answered — **accept** the summary and I'll start building.")
        elif qs is not None and qs.awaiting_confirmation:
            self._note("The questions are already done — **accept** the summary and I'll start building.")
        else:
            self._note("Nothing left to fast-forward.")
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
        # The switch resets the prior-art sub-loop, so its card has no data to
        # render from any more and would show as "(… unavailable)". The step
        # re-runs under the new mode and posts a fresh one.
        self.transcript.drop_artifact("prior_art")
        self.state.pop("_prior_art_preview", None)
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
        if self.state.get("pending_review") in PIPELINE_NODES:
            self.edit_armed = True
            self._note("Edit mode — describe what you'd like changed and press Enter.")
        else:
            # Bare /edit during intake means "which one?" — the browser answers
            # that better than a note telling the user to go find a number.
            self._edit_answers()

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
        from yeaboi.ui.shared._voice_input import record_voice_input

        def render_status(border: str, line: str):
            # record_voice_input owns the indicator state (level, elapsed time,
            # which mic opened) and hands us the finished pair to render.
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
            result = self.composer.insert_text(spoken)
            if not result.ok:
                # A long dictation can hit the cap; an image chip (~11 chars)
                # cannot, which is why _paste_image ignores its result.
                self._paste_truncated(Truncated(offered=result.offered, kept=result.kept, dropped=result.dropped))

    # ------------------------------------------------------------- graph turns

    def _run_turn(
        self, text: str, *, echo_user: bool, images: list[str] | None = None, synthetic: bool = False
    ) -> bool:
        """One graph turn. Returns False when nothing ran (guardrail block, no graph)."""
        if echo_user and not synthetic:
            # Regex layers only, on every turn. No topical classification here:
            # every message that reaches _run_turn answers something the agent
            # asked — an intake question, the review gate, a refinement request
            # — and check_off_topic sees the reply without the question, so it
            # scored "one", "any", "yeah" and "change q6" as off-topic and threw
            # them away. The description is classified in _greeting_flow instead.
            block = validate_chat_input(text)
            if block is not None:
                logger.info("Chat input blocked: layer=%s len=%d", block.layer, len(text))
                self._note(block.message)
                return False

        if echo_user:
            self.transcript.add_user(text)
            self._pin_bottom()
        # Any turn ends the Tell-me composer-only window — the node re-shows
        # the summary and the gate re-derives its menu fresh.
        self._confirm_free_text = False
        logger.info("Chat turn start: len=%d images=%d synthetic=%s", len(text), len(images or []), synthetic)

        if self.graph is None:
            return self._dry_run_turn(text)

        # The turn runs on a worker thread so this one keeps painting 30fps
        # frames. on_event only appends to plain lists (GIL-atomic) and never
        # touches Rich — the reply events are rendered below, after the join.
        buffer: list[str] = []
        replies: list = []
        cancel = threading.Event()
        result_box: list = [None, None]

        def on_event(event) -> None:
            if isinstance(event, Token):
                buffer.append(event.text)
            elif not isinstance(event, Done):
                replies.append(event)

        def worker() -> None:
            try:
                result_box[0] = self.session.send(text, on_event, images=images, cancel=cancel)
            except Exception as exc:  # noqa: BLE001 — classified below on the main thread
                result_box[1] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        start = time.monotonic()
        first_token_logged = False
        self._work_quip_idx = -1
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
                self._entertain_duck(tick)
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

        if not result_box[0]:
            # Worker died without a result or an Exception (BaseException) —
            # state must never become None.
            logger.error("Chat turn produced no result — worker died without raising")
            self._note("Something went wrong — nothing was changed. Try again.")
            return False

        self.transcript.invalidate_artifacts()
        for event in replies:
            self._render_reply(event)
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
            if self._finish_requested:
                # A deferred /finish is fast mode that hasn't armed yet — Esc
                # must cancel it too, or the description turn it interrupts
                # would still fast-forward on completion.
                self._finish_requested = False
                self.notice = "Fast-forward cancelled."
            if self.state.get("_chat_fast_forward"):
                # Esc mid-turn also leaves fast mode: cancelling the stage but
                # letting the next one auto-accept would look like Esc did
                # nothing.
                self._stop_fast_mode("esc during turn")
                self.notice = "Fast mode stopped."
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
            # Typing continues during a turn, so clearing and truncation have to
            # report here too — this branch used to discard the event entirely,
            # which made both silent for the whole time the graph was working.
            # Voice and image capture stay suppressed mid-turn, as before.
            event = self.composer.handle_key(key, dropped=take_paste_dropped())
            if isinstance(event, Cleared | Restored):
                self._composer_cleared(event)
            elif isinstance(event, Truncated):
                self._paste_truncated(event)

    def _at_intake_summary(self) -> bool:
        return at_intake_summary(self.state)

    def _at_prior_art(self) -> bool:
        return at_prior_art(self.state)

    def _append_reply(self, *, streamed: str) -> None:
        """Render the newest reply after a modal takeover drove the graph."""
        event = reply_event(self.state)
        if event is not None:
            self._render_reply(event)

    def _render_reply(self, event) -> None:
        """Draw one reply event: a card and its prompt, or a bubble."""
        if isinstance(event, AwaitConfirm):
            self.transcript.add_artifact(event.kind)
            self._say(event.prompt)
            return
        self._say(event.text)

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
        return self.session.awaiting

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
            elif self.state.get(PROGRESS_DONE_KEYS.get(step_node, "")):
                # .get twice: a pipeline step added to repl/_ui without a row
                # here must render as pending, not KeyError a build mid-run.
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
        node = next_node(self.state, dry_run=self.dry_run)
        label, progress = self._stage_meta(node)
        self.subtitle = self._fast_prefix() + f"{label}… {progress}"
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
        if pending in PIPELINE_NODES and not self.state.get("_chat_fast_forward"):
            # Fast mode: the run loop's auto-accept shows the card itself —
            # showing it here too would duplicate it and prompt for a reply
            # nobody is going to give.
            self._show_review_card(pending)
            self.progress = None  # a review gate pauses the build — card takes over
        return True

    def _show_review_card(self, pending: str, *, prompt: bool = True) -> None:
        gate = review_gate(self.state, pending)
        self.transcript.add_artifact(gate.kind)
        if prompt:
            self._say(gate.prompt)
        self._prompted.add(pending)

    def _fast_prefix(self) -> str:
        """Subtitle marker while fast mode is on — the exit must be visible."""
        return "Fast mode (Esc stops) · " if self.state.get("_chat_fast_forward") else ""

    def _stop_fast_mode(self, where: str) -> None:
        self.state.pop("_chat_fast_forward", None)
        logger.info("Chat: fast mode stopped (%s)", where)

    def _auto_accept_review(self) -> None:
        """Fast mode: show the artifact, accept it, move on — checking for an
        Esc between accepts so the run stays stoppable."""
        # Drain any keys pressed since the last frame: Esc here means "stop
        # auto-accepting", and the review card then takes over normally.
        # Other type-ahead is deliberately dropped — fast mode is not going
        # to answer it, and replaying stale keys into the next input loop
        # would act on a screen the user wasn't looking at.
        while key := self._key(0):
            if key == "esc":
                self._stop_fast_mode("esc at review gate")
                self._note("Fast mode stopped — here's the review.")
                return
        pending = self.state.get("pending_review", "")
        if pending not in self._prompted:
            # /finish typed at an already-shown review gate must not re-add the card.
            self._show_review_card(pending, prompt=False)
        self._note("Auto-accepted (fast mode).")
        self._bubble("Auto-accepted!")
        logger.info("Review decision (chat): auto-accept %s (fast mode)", pending)
        clear_review_state(self.state)
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
        self._work_quip_idx = -1
        set_duck_working(True)
        try:
            while thread.is_alive():
                tick = time.monotonic() - start
                self._processing_key(self._key(FRAME_TIME_30FPS), cancel)
                self._entertain_duck(tick)
                self._render(processing=True, tick=tick)
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
            if text.lower() in ACCEPT_WORDS:
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

    def _spike_popup(self) -> None:
        """Architecture-spike opt-in/out — the confidence rule sets the default.

        Same choice-screen pattern as _capacity_popup; the recommended option
        always lists first, and fast mode takes it without asking (matching
        the headless auto-rule).
        """
        from yeaboi.ui.session.phases._phases import _pipeline_choice_screen

        prompt = self.state.get("_spike_prompt") or {}
        recommended = prompt.get("recommended", "include")
        chosen = prompt.get("chosen", "the recommended architecture")
        confidence = prompt.get("confidence", "medium")
        if self.state.get("_chat_fast_forward"):
            self.state["spike_choice"] = recommended
            self.state["_spike_prompt"] = {}
            self._note(f"Architecture spike: {recommended} (fast mode, confidence {confidence}).")
            logger.info("Spike question (chat): auto-%s (fast mode)", recommended)
            return

        messages = self.state.get("messages", [])
        subtitle = messages[-1].content.replace("**", "") if messages and isinstance(messages[-1], AIMessage) else ""
        add_label = "Add a validation spike (1-3 days)"
        skip_label = f"Skip — commit to {chosen}"
        if recommended == "include":
            options = [f"{add_label} (recommended — confidence {confidence})", skip_label]
        else:
            options = [f"{skip_label} (recommended — confidence high)", add_label]

        small_mode = self.state.get("_intake_mode") == "small_project"
        label, progress = self._stage_meta("task_decomposer" if small_mode else "story_writer")
        choice = _pipeline_choice_screen(
            self.live,
            self.console,
            self._key,
            title="Architecture Spike",
            subtitle=subtitle,
            options=options,
            step=3,
            total=5,
            stage_label=label,
            progress=progress,
        )
        follows_recommendation = choice in (None, 0)
        picked_include = (recommended == "include") == follows_recommendation
        self.state["spike_choice"] = "include" if picked_include else "skip"
        self.state["_spike_prompt"] = {}
        self._note(f"Architecture spike: {self.state['spike_choice']}.")
        logger.info("Spike question (chat): %s (recommended=%s)", self.state["spike_choice"], recommended)

    # ----------------------------------------------------------- review logic

    def _review_reply(self, text: str) -> None:
        pending = self.state.get("pending_review", "")
        block = validate_chat_input(text)
        if block is not None:
            logger.info("Chat input blocked: layer=%s len=%d", block.layer, len(text))
            self._note(block.message)
            return
        if self.edit_armed:
            self.edit_armed = False
            self._apply_edit_feedback(pending, text)
            return
        verdict = review_verdict(text, pending)
        if isinstance(verdict, Accept):
            self.transcript.add_user(text)
            logger.info("Review decision (chat): accept %s", pending)
            clear_review_state(self.state)
            self._save()
            self._pin_bottom()
        elif isinstance(verdict, SwitchSize):
            self.transcript.add_user(text)
            self._switch_size(verdict.target)
        elif isinstance(verdict, TrackerSync):
            self._tracker_sync(verdict.tracker, pending)
        else:
            self._apply_edit_feedback(pending, verdict.text)

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

    def _tracker_sync(self, requested: str, pending: str) -> None:
        from yeaboi.ui.session.phases._phases import _get_active_trackers, _handle_tracker_sync

        trackers = _get_active_trackers()
        if not trackers:
            self._note("No tracker configured — connect Jira or Azure DevOps first.")
            return
        tracker = requested or trackers[0]
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
                if self.choices.auto_submit and not self.choices.multi and key.isdigit():
                    # Command menus (size, review verdict): a bare digit picks
                    # and submits in one stroke. Out-of-range digits fall
                    # through to the composer like any other character.
                    idx = int(key) - 1
                    if 0 <= idx < len(self.choices.options):
                        self.choices.highlight = idx
                        logger.info("Chat choice auto-submit: %d", idx + 1)
                        return self._choice_answer()
                # Arrows move the highlight; the wheel deliberately does NOT.
                # A menu sits under the thing it asks about — the review sits
                # under a 30-row summary — so a wheel that only cycled three
                # rows would trap the transcript off-screen. Wheel/PageUp fall
                # through to the scroll handler below.
                if key == "up":
                    self.choices.highlight = (self.choices.highlight - 1) % len(self.choices.options)
                    self._carousel_moved()
                    continue
                if key == "down":
                    self.choices.highlight = (self.choices.highlight + 1) % len(self.choices.options)
                    self._carousel_moved()
                    continue
                if key in ("left", "right") and self.choices.carousel:
                    # A carousel browses sideways too. Safe to claim here: the
                    # composer is empty (outer gate), so there is no cursor for
                    # ←/→ to move, and ordinary menus fall through untouched.
                    step = -1 if key == "left" else 1
                    self.choices.highlight = (self.choices.highlight + step) % len(self.choices.options)
                    self._carousel_moved()
                    continue
                if key == " " and self.choices.multi:
                    i = self.choices.highlight
                    label, checked = self.choices.options[i]
                    if i in self.choices.banned:
                        # Space on a banned row un-bans and picks it — the two
                        # marks are mutually exclusive, and reaching for Space
                        # says "actually I want this one".
                        self.choices.banned.discard(i)
                        self.choices.options[i] = (label, True)
                        logger.info("Chat prior-art ban toggle: row %d -> unbanned (picked)", i + 1)
                        continue
                    self.choices.options[i] = (label, not checked)
                    continue
                if key in ("x", "X") and self.choices.carousel:
                    # Permanent "never suggest again" — kept off Space so a
                    # quick multi-pick can't blacklist a repo by accident.
                    # Claiming a letter is safe only because the composer is
                    # empty; prose starting with "x" needs another char first.
                    i = self.choices.highlight
                    label, _checked = self.choices.options[i]
                    if i in self.choices.banned:
                        self.choices.banned.discard(i)
                    else:
                        self.choices.banned.add(i)
                        self.choices.options[i] = (label, False)
                    logger.info(
                        "Chat prior-art ban toggle: row %d -> %s",
                        i + 1,
                        "banned" if i in self.choices.banned else "unbanned",
                    )
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
                    self.composer.forget_stash()
                    return inline

            event = self.composer.handle_key(key, dropped=take_paste_dropped())
            if isinstance(event, Submit):
                text = event.text
                self.composer.reset()
                return text
            if isinstance(event, Voice):
                self._voice()
            elif isinstance(event, PasteImage):
                self._paste_image()
            elif isinstance(event, Cleared | Restored):
                self._composer_cleared(event)
            elif isinstance(event, Truncated):
                self._paste_truncated(event)
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

    def _carousel_moved(self) -> None:
        """Sync the prior-art preview card to the highlighted row.

        Presentation-only: the preview index lives on graph_state under a
        driver-owned key (precedent: _intake_mode) and never reaches the node.
        Fires in key branches only — never per frame — and invalidates just
        the one card rather than the whole transcript cache.
        """
        if self.choices is None or not self.choices.carousel:
            return
        self.state["_prior_art_preview"] = self.choices.highlight
        self.transcript.invalidate_artifact("prior_art")

    def _choice_answer(self) -> str:
        assert self.choices is not None
        # This return leaves the input loop without touching handle_key, so the
        # stash has to be burned here as it is on a typed submit.
        self.composer.forget_stash()
        if self.choices.carousel:
            # The batch verdict goes to the node as its index grammar, never as
            # joined labels — a repo named "a, b" would shatter on the comma
            # join below and then re-parse as nonsense.
            picked = [
                str(i + 1)
                for i, (_label, is_checked) in enumerate(self.choices.options)
                if is_checked and i not in self.choices.banned
            ]
            banned = [f"!{i + 1}" for i in sorted(self.choices.banned)]
            answer = " ".join(picked + banned) or "none"
            logger.info("Chat prior-art batch submit: %s", answer)
            return answer
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
            # Typed "1"/"2" work through parse_size_reply; "3" (and "form")
            # need the same parity while the third row is on offer.
            wants_form = (picked and submit == _FORM_CHOICE_LABEL) or (
                form_offered and submit.strip().lower() in ("3", "form")
            )
            # The description is the one message in the whole session that
            # answers no question, so it is the one input check_off_topic can
            # judge — and until now the only one it never saw, because the
            # greeting hands it to _run_turn as synthetic (unvalidated) text.
            # Everything else on this turn answers the size question: a picked
            # row, the form preference, or a bare size reply (parse_size_reply
            # is deterministic and total, so this costs nothing). Guarding here
            # rather than after the branch below also keeps a rejected input
            # from burning the resolve_intake_mode call.
            if not picked and not wants_form and not parse_size_reply(submit):
                block = validate_chat_input(submit, classify_topic=True)
                if block is not None:
                    logger.info("Chat input blocked: layer=%s len=%d", block.layer, len(submit))
                    self._note(block.message)
                    continue
            self.transcript.add_user(submit)
            self.choices = None

            if wants_form:
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
        # auto_submit makes the placeholder's "Press 1 or 2 to size it"
        # literally true — no Enter needed.
        return ChoiceRows(options=options, highlight=0, multi=False, auto_submit=True)

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
        messages = self.state.get("messages", [])
        # Which stored replies come back as cards rather than as the markdown
        # wall the cards exist to replace — the same routing the live turn used.
        plan = replay_plan(self.state)
        for i, message in enumerate(messages):
            if isinstance(message, HumanMessage) and isinstance(message.content, str):
                self.transcript.add_user(message.content)
            elif isinstance(message, AIMessage) and isinstance(message.content, str) and message.content:
                if i == plan.summary_at:
                    self.transcript.add_artifact("intake_summary")
                    self.transcript.add_assistant(_CONFIRM_VERDICT_PROMPT)
                elif i == plan.prior_art_at:
                    self.transcript.add_artifact("prior_art")
                    self.transcript.add_assistant(_PRIOR_ART_VERDICT_PROMPT)
                else:
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
        return next_node(self.state, dry_run=True)

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
        self._work_quip_idx = -1
        set_duck_working(True)
        try:
            while time.monotonic() - start < _DRY_STAGE_SECONDS:
                tick = time.monotonic() - start
                self._processing_key(self._key(FRAME_TIME_30FPS), cancel)
                self._entertain_duck(tick)
                self._render(processing=True, tick=tick)
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
            if self.stop_after_intake and stage != "intake":
                # The questionnaire is accepted — the card pipeline owns
                # everything from here (reviews, exports, tracker sync,
                # capacity), so hand the state back before any of the branches
                # below can fire.
                self.state.pop("_chat_fast_forward", None)
                logger.info("Chat handing off after intake (next stage=%s)", stage)
                break
            if stage == "capacity":
                self._capacity_popup()
                continue
            if stage == "spike":
                self._spike_popup()
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
                self.subtitle = self._fast_prefix() + " · ".join(s for s in (view.progress, view.phase_label) if s)
                self._coach_phase()
                if view.choices and self._confirm_free_text:
                    # The Tell-me pick promised the composer the floor — no
                    # menu (and no digit auto-submit) until that reply runs.
                    view.choices = None
                if view.choices:
                    highlight = next((i for i, (_o, sel) in enumerate(view.choices) if sel), 0)
                    banned: set[int] = set()
                    if view.prior_art:
                        # Pre-bans mirror the pre-checks: populated only when a
                        # legacy mid-loop session resumed with per-repo verdicts
                        # already recorded, so they stay visible and reversible.
                        qs = self._qs()
                        rejected_keys = {r.get("key", "") for r in getattr(qs, "_prior_art_rejected", [])}
                        banned = {
                            i
                            for i, c in enumerate(getattr(qs, "_prior_art_candidates", []))
                            if c.get("key", "") and c.get("key", "") in rejected_keys
                        }
                        self.state["_prior_art_preview"] = highlight
                    self.choices = ChoiceRows(
                        options=list(view.choices),
                        highlight=highlight,
                        multi=view.multi_select,
                        auto_submit=view.auto_submit,
                        carousel=view.prior_art,
                        banned=banned,
                    )
                else:
                    self.choices = None
                    # not has_stash(): a box emptied by Ctrl+U is waiting for its
                    # undo, and prefilling the suggestion into it would make the
                    # second Ctrl+U clear the suggestion instead of restoring.
                    if (
                        view.suggestion
                        and self.composer.is_empty()
                        and not self.composer.has_stash()
                        and self._prefilled_q != view.current_question
                    ):
                        self.composer.set_text(view.suggestion)
                        # set_text resets the cursor to 0,0 — typing must land
                        # after the suggestion, not before it.
                        self.composer.row = len(self.composer.lines) - 1
                        self.composer.col = len(self.composer.lines[-1])
                        self._prefilled_q = view.current_question
            else:
                self.choices = None
                self.subtitle = self._fast_prefix() + (
                    "" if stage != "chat" else "Plan complete — keep refining, or /export"
                )
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
                elif getattr(qs, "_prior_art_stage", "") in ("ask", "reason", "empty"):
                    # Prior art owns the turn before the confirmation gate does
                    # — both run with awaiting_confirmation set, and the confirm
                    # mapper would turn "1" into "accept".
                    answer = self._prior_art_pick(answer)
                elif qs.awaiting_confirmation:
                    mapped = self._confirm_pick(answer)
                    if mapped is None:
                        continue  # handled locally (Edit prefill / free-text nudge)
                    answer = mapped
                else:
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

    def _confirm_pick(self, submit: str) -> str | None:
        """Map a confirmation-gate pick to what the node understands.

        Returns the literal to send ("accept"/"override"), the typed text
        unchanged, or None when the pick was handled locally and no graph
        turn should run. The raw labels never reach the node: a bare digit
        would read as an edit intent and the labels match no confirm keyword.
        """
        from ._question_view import CONFIRM_ACCEPT, CONFIRM_EDIT, CONFIRM_FREETEXT, CONFIRM_OVERRIDE_VELOCITY

        if submit == CONFIRM_ACCEPT:
            return "accept"
        if submit == CONFIRM_OVERRIDE_VELOCITY:
            return "override"
        if submit == CONFIRM_EDIT:
            # The full-screen browser, not a composer prefill: "edit N" only
            # helps someone who already knows which N, and the chat's own list
            # shows this run's planned questions rather than everything the
            # summary is built from.
            logger.info("Chat: confirm pick -> answer browser")
            self._edit_answers()
            return None
        if submit == CONFIRM_FREETEXT:
            # Drop the menu until the reply is sent: re-arming auto-submit
            # over the free text just solicited would hijack a reply that
            # starts with a digit ("3 sprints is too many" -> "override").
            self._confirm_free_text = True
            self._note("Go ahead — tell me what's off and I'll update the summary.")
            logger.info("Chat: confirm pick -> free text")
            return None
        return submit

    def _prior_art_pick(self, submit: str) -> str:
        """Map a prior-art submission to what the node understands.

        The "ask" stage needs no mapping any more: both the widget
        (:meth:`_choice_answer`'s carousel branch) and a typed reply already
        speak the node's index grammar, so they pass straight through. Only
        the empty card's "Continue" row is a label to translate — it is an
        acknowledgement, not a verdict, and just needs to not be mistaken for
        a confirmation-gate pick on the way through.
        """
        from ._question_view import PRIOR_ART_CONTINUE

        self.state.pop("_prior_art_preview", None)  # the preview dies with the menu
        if submit == PRIOR_ART_CONTINUE:
            logger.info("Chat: prior-art pick -> %s", submit)
            return "ok"
        return submit

    def _entertain_duck(self, tick: float) -> None:
        """Rotate working quips (plus the odd gag) through a long wait.

        Clock-derived: the quip slot is tick // _WORK_QUIP_SECONDS, so per
        frame this is one division and usually one compare — the say(), the
        gags and the log fire only when the slot changes, never per frame.
        Short turns (< one slot) stay silent. PRIORITY_COACH keeps real
        events (stage completions) winning the bubble.
        """
        from ._duck import COACH_HOLD, PRIORITY_COACH, WORKING_QUIPS

        if tick < _WORK_QUIP_SECONDS:
            return
        idx = int(tick // _WORK_QUIP_SECONDS)
        if idx == self._work_quip_idx:
            return
        self._work_quip_idx = idx
        quip = WORKING_QUIPS[idx % len(WORKING_QUIPS)]
        if self.duck.say(quip, priority=PRIORITY_COACH, hold=COACH_HOLD):
            logger.debug("Duck working quip: %s", quip)  # decor, not a user action
        if idx % 4 == 0:
            quack_duck()
        if idx == 5:
            # One shades-lift gag on a genuinely long wait (~25s in).
            poke_duck()

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
            return "/finish answers the rest with defaults"
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
        # Deliberately nothing else: rotating feature-tips were tried here and
        # read as noise over the composer (user feedback) — outside intake the
        # duck only reacts, never volunteers.

    def _toggle_duck(self) -> None:
        """/duck — mute or unmute the companion's speech bubble, app-wide."""
        from yeaboi.config import set_duck_enabled
        from yeaboi.ui.shared._duck_voice import set_duck_muted

        self.duck.mute(not self.duck.muted)
        # The mute is global: it also silences the shared voice every other
        # page speaks through, and persists so it survives restarts.
        set_duck_muted(self.duck.muted)
        set_duck_enabled(not self.duck.muted)
        logger.info("Duck bubble %s", "muted" if self.duck.muted else "unmuted")
        if self.duck.muted:
            self._note("Duck muted — he'll keep working quietly. /duck brings his bubble back.")
        else:
            self._note("Duck's bubble is back.")
            self._bubble("Quack!")

    def _show_questions(self) -> None:
        """/questions — the planned-question checklist for this run.

        Lists only the questions this run actually asks (user-answered +
        remaining essential gaps), not the 30-question bank — the same sets
        the "Question X of Y" subtitle counts.
        """
        from yeaboi.prompts.intake import QUESTION_SHORT_LABELS
        from yeaboi.ui.session.chat._question_view import planned_question_sets

        qs = self._qs()
        if qs is None:
            return
        sets = planned_question_sets(qs)
        if sets is None:
            self._note("Couldn't derive the question plan — /summary shows your answers so far.")
            return
        remaining, asked = sets
        planned = sorted(set(remaining) | asked)
        if not planned:
            self._note("Nothing left to ask — /summary shows everything I've got.")
            return
        remaining_set = set(remaining)
        lines = ["Planned questions for this run:"]
        for q in planned:
            label = QUESTION_SHORT_LABELS.get(q, f"Question {q}")
            if q == qs.current_question and q in remaining_set:
                lines.append(f"● Q{q} {label} — current")
            elif q in remaining_set:
                lines.append(f"○ Q{q} {label}")
            else:
                answer = str(qs.answers.get(q, "")).strip().replace("\n", " ")
                if len(answer) > 40:
                    answer = answer[:37] + "…"
                lines.append(f"✓ Q{q} {label}" + (f" — {answer}" if answer else ""))
        lines.append("")
        lines.append(
            "Everything else is filled from your description and defaults — /summary shows it, /edit N changes it."
        )
        logger.info("Chat: /questions (%d planned, %d remaining)", len(planned), len(remaining))
        self._note("\n".join(lines))

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

        # Typed digits must map to the rows the user saw — the chat may hide
        # mode-redundant rows (CHAT_MODE_HIDDEN_CHOICES), so canonical
        # meta.options and the display can disagree.
        labels = None
        if self.choices is not None and not self.choices.multi and self.choices.options:
            labels = tuple(label for label, _sel in self.choices.options)
        return _resolve_choice_input(answer, q_num, option_labels=labels)
