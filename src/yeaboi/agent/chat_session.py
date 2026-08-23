"""One planning conversation, as a decision layer and an event stream.

# See docs: "Architecture" — the four layers; "The ReAct Loop"
# See docs: "Guardrails" — human-in-the-loop review gates

Everything here answers "what does this conversation need next?" without
knowing whether the answer becomes a Rich panel, an NDJSON line or a React
component — the TUI driver (``ui/session/chat/_driver.py``) renders these
events today and the desktop's chat route will stream the same ones.

:class:`ChatSession` owns the graph state and runs one turn at a time,
emitting typed events as they happen; everything above it is a pure function
of that state. Nothing in this module renders, reads keys or touches the
duck: those stay with the caller that owns a screen.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage

from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.agent.streaming import predict_next_node, stream_chat_turn

logger = logging.getLogger(__name__)

# The generation nodes that produce an artifact and park on a review gate.
PIPELINE_NODES = (
    "project_analyzer",
    "feature_skip",
    "feature_generator",
    "story_writer",
    "task_decomposer",
    "sprint_planner",
)

# What counts as "yes" at any review gate.
ACCEPT_WORDS = frozenset({"accept", "a", "ok", "yes", "looks good", "lgtm", "continue"})

# The line that stands in for a node's markdown summary once the card has
# rendered it. Module constants because the live turn and the resume replay
# both write them, and the two must not drift.
CONFIRM_VERDICT_PROMPT = (
    "Here's everything I've got. Pick an option below — or type **accept**, **edit N**, or just tell me what's off."
)

PRIOR_ART_VERDICT_PROMPT = (
    "You already own these. **Space** picks the relevant ones, **←/→** browses the details, "
    "**X** hides a repo forever, **Enter** confirms — or type e.g. **1 3**, **all**, or **none**."
)

# Which artifact card a parked review gate shows.
REVIEW_ARTIFACT_KINDS = {
    "project_analyzer": "analysis",
    "feature_generator": "features",
    "feature_skip": "features",
    "story_writer": "stories",
    "task_decomposer": "tasks",
    "sprint_planner": "sprints",
}

# Which state key proves a pipeline step has produced its artifact.
PROGRESS_DONE_KEYS = {
    "project_analyzer": "project_analysis",
    "epic_review": "_epic_reviewed",
    "feature_generator": "features",
    "story_writer": "stories",
    "task_decomposer": "tasks",
    "sprint_planner": "sprints",
}

# The review-state fields an accepted gate clears before the next invoke.
_REVIEW_STATE_KEYS = (
    "pending_review",
    "last_review_decision",
    "last_review_feedback",
    "review_feedback_images",
    "_small_project_oversized",
)

# Dry-run has no graph to predict against — the artifact keys carry the order.
_DRY_NODE_ORDER = (
    ("project_analysis", "project_analyzer"),
    ("features", "feature_generator"),
    ("stories", "story_writer"),
    ("tasks", "task_decomposer"),
    ("sprints", "sprint_planner"),
)


# --------------------------------------------------------------------- events


@dataclass(frozen=True)
class Token:
    """One streamed chunk of the reply being written."""

    text: str


@dataclass(frozen=True)
class Done:
    """The turn finished — state has moved on."""


@dataclass(frozen=True)
class Assistant:
    """A plain assistant bubble — the node's own words."""

    text: str


@dataclass(frozen=True)
class UserSaid:
    """Something the user said — only ever produced by the replay."""

    text: str


@dataclass(frozen=True)
class AskQuestion:
    """An intake question, decorated for chat."""

    text: str
    number: int


@dataclass(frozen=True)
class ShowArtifact:
    """An artifact card, rendered from state rather than from the reply."""

    kind: str


@dataclass(frozen=True)
class AwaitConfirm:
    """An intake-side gate: a card plus the one line that asks for a verdict."""

    kind: str
    prompt: str


@dataclass(frozen=True)
class AwaitReview:
    """A pipeline review gate: the node, its card, and the verdict grammar."""

    node: str
    kind: str
    prompt: str


ReplyEvent = Assistant | AskQuestion | AwaitConfirm
ChatEvent = Token | ReplyEvent | Done
EventSink = Callable[["ChatEvent"], None]


# ----------------------------------------------------------------- predicates


def questionnaire(state: dict) -> QuestionnaireState | None:
    qs = state.get("questionnaire")
    return qs if isinstance(qs, QuestionnaireState) else None


def next_node(state: dict, *, dry_run: bool = False) -> str:
    """The graph node that will run next (or its dry-run stand-in)."""
    if not dry_run:
        return predict_next_node(state)
    for key, node in _DRY_NODE_ORDER:
        if not state.get(key):
            return node
    return "agent"


def stage_of(state: dict, *, dry_run: bool = False) -> str:
    """What the conversation needs next: the one predicate every caller routes on.

    Resume, the PTO sub-loop and mid-chat size switches fall out of state
    inspection rather than control flow — which is why this is a function of
    state alone.
    """
    if state.get("capacity_override_target", 0) < -1 and not dry_run:
        return "capacity"
    if state.get("_spike_prompt") and not state.get("spike_choice") and not dry_run:
        return "spike"
    pending = state.get("pending_review")
    if pending == "project_intake":
        return "intake"  # confirmation gate — the node consumes the reply
    if pending in PIPELINE_NODES:
        return "review"
    node = next_node(state, dry_run=dry_run)
    if node == "project_intake":
        return "intake"
    if node in PIPELINE_NODES:
        if (
            node in ("feature_generator", "feature_skip")
            and state.get("project_analysis")
            and not state.get("_epic_reviewed")
        ):
            return "epic"
        return "pipeline"
    return "chat"


def at_intake_summary(state: dict) -> bool:
    """True when the newest reply is the intake summary awaiting a verdict.

    One predicate for both paths that render it — the live turn and the resume
    replay — or reopening a session parked on the gate resurrects the markdown
    wall the card replaced. The sub-states are excluded because each one
    re-asks something instead of re-showing the summary: a PTO prompt, a
    velocity prompt, the prior-art verdict, or the re-ask of the answer being
    edited.
    """
    qs = questionnaire(state)
    return (
        qs is not None
        and qs.awaiting_confirmation
        and not qs._awaiting_leave_input
        and not qs._awaiting_velocity_input
        and not at_prior_art(state)
        and qs.editing_question is None
        and qs.current_question > TOTAL_QUESTIONS
    )


def at_prior_art(state: dict) -> bool:
    """True while the prior-art sub-loop owns the turn.

    The summary card's condition is defined as "not this", so the two can never
    drift into both claiming the same turn.
    """
    qs = questionnaire(state)
    return qs is not None and getattr(qs, "_prior_art_stage", "") in ("ask", "reason", "empty")


def newest_reply(state: dict) -> str:
    messages = state.get("messages", [])
    return messages[-1].content if messages and isinstance(messages[-1], AIMessage) else ""


# ------------------------------------------------------------- reply routing


def reply_event(state: dict) -> ReplyEvent | None:
    """Route the newest assistant reply to what should be shown for it.

    None means there is nothing to show (no reply on this turn).
    """
    reply = newest_reply(state)
    if not reply:
        return None

    qs = questionnaire(state)
    # Intake confirmation summary → card + short prompt instead of the node's
    # markdown wall (the card is the same data, rendered properly).
    if at_intake_summary(state):
        return AwaitConfirm(kind="intake_summary", prompt=CONFIRM_VERDICT_PROMPT)

    prior_art_stage = getattr(qs, "_prior_art_stage", "") if qs is not None else ""
    if prior_art_stage == "ask":
        from yeaboi.agent.nodes import _PRIOR_ART_GRAMMAR_HINT

        if reply.strip() == _PRIOR_ART_GRAMMAR_HINT:
            # The node rejected a typed answer. Swallowing this and re-posting
            # the same card would read as a no-op — the one turn where the
            # node's own words must go out as prose.
            return Assistant(reply)
        return AwaitConfirm(kind="prior_art", prompt=PRIOR_ART_VERDICT_PROMPT)

    # Nothing found — the node's message is already the whole statement. An
    # explicit branch rather than falling through: the tail decorates replies
    # as intake questions, and this one is not a question.
    if prior_art_stage == "empty":
        return Assistant(reply)

    if qs is not None and not qs.completed:
        from yeaboi.prompts.intake import decorate_question_for_chat

        mode = qs.intake_mode or state.get("_intake_mode") or None
        return AskQuestion(
            text=decorate_question_for_chat(qs.current_question, reply, intake_mode=mode),
            number=qs.current_question,
        )
    return Assistant(reply)


def review_gate(state: dict, node: str) -> AwaitReview:
    """The card and verdict grammar for a parked pipeline review."""
    prompts = [
        "Reply **accept** to continue",
        "**edit** + your changes to refine",
        "/export to save",
        "/finish auto-accepts the rest",
    ]
    if node == "project_analyzer" and state.get("_small_project_oversized"):
        prompts.insert(1, "**switch to large** for a fuller plan (this looks bigger than a small project)")
    return AwaitReview(
        node=node,
        kind=REVIEW_ARTIFACT_KINDS.get(node, "analysis"),
        prompt=" · ".join(prompts) + ".",
    )


# ------------------------------------------------------------- resume replay


@dataclass(frozen=True)
class ReplayPlan:
    """Which replayed message indices become cards instead of prose.

    -1 means "no message qualifies". The two are mutually exclusive —
    :func:`at_intake_summary` excludes prior art.
    """

    summary_at: int
    prior_art_at: int


def replay_plan(state: dict) -> ReplayPlan:
    """Decide which stored replies a resumed session renders as cards.

    A live turn renders the intake summary and the prior-art batch as cards
    (:func:`reply_event`), so replaying their markdown would hand a resumed
    session the wall of text the cards exist to replace.
    """
    messages = state.get("messages", [])

    def newest(predicate) -> int:
        return max(
            (
                i
                for i, m in enumerate(messages)
                if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content and predicate(m.content)
            ),
            default=-1,
        )

    summary_at = newest(lambda _content: True) if at_intake_summary(state) else -1

    prior_art_at = -1
    qs = questionnaire(state)
    if qs is not None and getattr(qs, "_prior_art_stage", "") == "ask" and qs._prior_art_candidates:
        from yeaboi.agent.nodes import _PRIOR_ART_GRAMMAR_HINT

        # The card belongs to the newest reply that is NOT the grammar hint: a
        # rejected typed answer leaves [..., AI(batch prompt), Human, AI(hint)],
        # and pinning to the newest reply outright would card the one-liner
        # while the batch-prompt wall above it replayed raw.
        prior_art_at = newest(lambda content: content.strip() != _PRIOR_ART_GRAMMAR_HINT)
    return ReplayPlan(summary_at=summary_at, prior_art_at=prior_art_at)


# Which artifact card each stored artifact resumes as, in pipeline order.
ARTIFACT_STATE_KEYS = (
    ("analysis", "project_analysis"),
    ("features", "features"),
    ("stories", "stories"),
    ("tasks", "tasks"),
    ("sprints", "sprints"),
)

ReplayItem = UserSaid | Assistant | AwaitConfirm | ShowArtifact


def replay(state: dict) -> list[ReplayItem]:
    """Rebuild the whole conversation from stored state, in order.

    The greeting exchange lives in ``_chat_preamble`` rather than in messages
    (project_intake reads ``messages[0]`` as the description), so it leads;
    then the messages, with the gate replies routed through
    :func:`replay_plan`; then the artifacts the session already holds. A
    finished plan ends on its recap card — silently, because the celebration
    fired when the build completed and must not replay on every resume.
    """
    items: list[ReplayItem] = []
    for entry in state.get("_chat_preamble") or []:
        text = entry.get("text", "")
        items.append(UserSaid(text) if entry.get("role") == "user" else Assistant(text))

    plan = replay_plan(state)
    for i, message in enumerate(state.get("messages", [])):
        if not isinstance(message.content, str):
            continue
        if isinstance(message, HumanMessage):
            items.append(UserSaid(message.content))
        elif isinstance(message, AIMessage) and message.content:
            if i == plan.summary_at:
                items.append(AwaitConfirm(kind="intake_summary", prompt=CONFIRM_VERDICT_PROMPT))
            elif i == plan.prior_art_at:
                items.append(AwaitConfirm(kind="prior_art", prompt=PRIOR_ART_VERDICT_PROMPT))
            else:
                items.append(Assistant(message.content))

    for kind, key in ARTIFACT_STATE_KEYS:
        if state.get(key):
            items.append(ShowArtifact(kind))
    if state.get("sprints"):
        items.append(ShowArtifact("recap"))
    return items


# ------------------------------------------------------------ review verdicts


@dataclass(frozen=True)
class Accept:
    """The gate was accepted — the pipeline moves on."""


@dataclass(frozen=True)
class SwitchSize:
    """ "switch to large" at the analysis gate."""

    target: str


@dataclass(frozen=True)
class TrackerSync:
    """A sync request. An empty tracker means "whichever is configured"."""

    tracker: str


@dataclass(frozen=True)
class EditFeedback:
    """Anything else — refine by chatting."""

    text: str


ReviewVerdict = Accept | SwitchSize | TrackerSync | EditFeedback


def review_verdict(text: str, pending: str) -> ReviewVerdict:
    """Classify a reply typed at a pipeline review gate."""
    lowered = text.lower().strip()
    if lowered in ACCEPT_WORDS:
        return Accept()
    if lowered in ("switch to large", "switch") and pending == "project_analyzer":
        return SwitchSize(target="smart")
    if lowered in ("sync jira", "sync azure", "sync azure devops", "sync"):
        tracker = "azdevops" if "azure" in lowered else ("jira" if "jira" in lowered else "")
        return TrackerSync(tracker=tracker)
    return EditFeedback(text=text.removeprefix("edit").removeprefix("regenerate").strip() or text)


def clear_review_state(state: dict) -> None:
    """Drop the review bookkeeping so the next invoke runs the next stage."""
    for key in _REVIEW_STATE_KEYS:
        state.pop(key, None)


def start_state(description: str, *, intake_mode: str = "") -> dict:
    """The state a fresh conversation starts from.

    The greeting and the size pick belong to ``_chat_preamble``, never to
    ``messages`` — project_intake reads ``messages[0]`` as the description, so
    anything else in front of it would be planned instead of the project. An
    unstated size is classified from the description, exactly as the chat's
    greeting does.
    """
    from yeaboi.agent.chat_intake import GREETING_TEXT, resolve_intake_mode

    mode = intake_mode
    if not mode:
        mode = resolve_intake_mode(description)[0] or "smart"
    label = "Small" if mode == "small_project" else "Large"
    logger.info("Chat session opened: mode=%s description_len=%d", mode, len(description))
    return {
        "messages": [],
        "questionnaire": None,
        "_intake_mode": mode,
        "_chat_greeting_done": True,
        # The opening line, held until it is sent as messages[0]. A caller that
        # never sends it gets an intake with nothing to plan, so the session
        # view carries it and the client's first turn is this text.
        "_chat_opening": description,
        # The description is deliberately absent: it becomes messages[0] on the
        # first turn, and replaying it here too would show it twice.
        "_chat_preamble": [
            {"role": "ai", "text": GREETING_TEXT},
            {"role": "ai", "text": f"Sounds like a {label} plan — switch any time with /small · /large."},
        ],
    }


# --------------------------------------------------------------- the session


class ChatSession:
    """One planning conversation over the graph, driven a turn at a time.

    Owns the graph state; the caller owns the screen (or the socket) and
    decides what each event becomes. :meth:`send` blocks, so the TUI runs it
    on a worker thread and paints meanwhile while an HTTP route streams the
    events out as they arrive.
    """

    def __init__(self, graph, state: dict, *, dry_run: bool = False) -> None:
        self.graph = graph
        self.state = state
        self.dry_run = dry_run

    @property
    def awaiting(self) -> str:
        """The stage this conversation is parked on — see :func:`stage_of`."""
        return stage_of(self.state, dry_run=self.dry_run)

    def send(
        self,
        text: str,
        on_event: EventSink,
        *,
        images: list[str] | None = None,
        cancel: threading.Event | None = None,
    ) -> bool:
        """Run one graph turn, emitting events as they happen.

        The first turn is an ordinary send: the description is ``messages[0]``
        and the graph builds the questionnaire from it. Returns True once the
        state has moved; provider and cancellation errors propagate, so the
        caller classifies them for its own surface.
        """
        messages = list(self.state.get("messages", []))
        if text:
            messages.append(HumanMessage(content=text))
        intake_turn = next_node(self.state) == "project_intake"
        if intake_turn:
            # The intake confirmation is the one turn sent while a review gate
            # is still open — project_intake consumes the reply itself rather
            # than a review card doing it. Its confirm branch returns no
            # "pending_review", and pending_review is a plain LastValue channel
            # (agent/state.py), so whatever is in the input state survives the
            # invoke: leave it set and the gate never closes and the stage
            # stays "intake" forever. Safe to drop unconditionally — the node
            # re-sets it when the summary needs showing again ("edit").
            self.state.pop("pending_review", None)
        invoke_state = {**self.state, "messages": messages}
        if images:
            if intake_turn:
                invoke_state["pasted_images"] = list(self.state.get("pasted_images") or []) + images
            else:
                invoke_state["chat_images"] = images

        result = stream_chat_turn(self.graph, invoke_state, lambda chunk: on_event(Token(chunk)), cancel=cancel)
        if result is None:
            # stream_chat_turn either returns a state or raises; a None here
            # would silently blank the session.
            logger.error("Chat turn produced no state")
            return False
        self.state = result
        if text:
            self.state.pop("_chat_opening", None)
        event = reply_event(self.state)
        if event is not None:
            on_event(event)
        on_event(Done())
        return True
