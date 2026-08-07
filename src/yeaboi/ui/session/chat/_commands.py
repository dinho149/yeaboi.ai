"""Slash-command registry for the planning live chat.

One declarative table instead of if-chains: each command names itself, its
help line, an availability predicate, and a handler that acts through the
ChatContext callbacks the driver provides. The /-menu above the composer and
/help both read this same registry, so a new command is discoverable the
moment it is registered.

Security invariant (tested): slash input NEVER reaches the model. Commands
dispatch locally, bypassing input guardrails (there is nothing to guard — no
text is sent), and an unknown /word produces a local help notice, not a graph
turn.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ChatContext:
    """Driver-owned callbacks the command handlers act through.

    The driver rebinds graph_state across turns, so commands read it via
    ``state()`` rather than holding a reference that would go stale.
    """

    state: Callable[[], dict]
    run_turn: Callable[[str], None]  # submit a synthetic user turn (guardrail-exempt)
    add_system: Callable[[str], None]  # dim notice line in the transcript
    add_artifact: Callable[[str], None]  # push an artifact card by kind
    insert_text: Callable[[str], bool]  # composer insert; False = truncated
    trigger_voice: Callable[[], None]
    trigger_image: Callable[[], None]
    export: Callable[[str], None]  # scope: "plan" | "transcript" | "both" | "" (ask)
    switch_size: Callable[[str], None]  # target intake mode ("smart" | "small_project")
    edit_question: Callable[[int | None], None]  # N → re-ask; None → arm edit-feedback
    request_quit: Callable[[], None]
    intake_active: Callable[[], bool]  # questionnaire exists and not completed/confirmed
    questionnaire_exists: Callable[[], bool]
    enter_form: Callable[[], None]  # full-screen questionnaire takeover (/form)
    fast_forward: Callable[[], None]  # default every remaining answer (/finish)
    plan_complete: Callable[[], bool]  # sprints exist — nothing left to fast-forward
    toggle_duck: Callable[[], None]  # mute/unmute the companion duck's bubble (/duck)


@dataclass(frozen=True)
class SlashCommand:
    name: str
    help: str
    handler: Callable[[ChatContext, str], None]
    available: Callable[[ChatContext], bool] = field(default=lambda ctx: True)


def _cmd_help(ctx: ChatContext, args: str) -> None:
    lines = ["Commands:"]
    for cmd in COMMANDS:
        if cmd.available(ctx):
            lines.append(f"/{cmd.name} — {cmd.help}")
    lines.append("")
    lines.append(
        "Shortcuts: Enter send · Alt+Enter newline · Ctrl+V paste screenshot · "
        "double-tap Space voice · ↑/↓ choices or cursor · Esc Esc leave"
    )
    ctx.add_system("\n".join(lines))


def _cmd_export(ctx: ChatContext, args: str) -> None:
    scope = args.strip().lower()
    if scope not in ("", "plan", "transcript", "both"):
        ctx.add_system(f"Unknown export scope {scope!r} — use /export, /export plan, or /export transcript.")
        return
    ctx.export(scope)


def _cmd_skip(ctx: ChatContext, args: str) -> None:
    # The intake node already understands the literal — same as typing it.
    ctx.run_turn("skip")


def _cmd_defaults(ctx: ChatContext, args: str) -> None:
    ctx.run_turn("defaults")


def _cmd_form(ctx: ChatContext, args: str) -> None:
    ctx.enter_form()


def _cmd_finish(ctx: ChatContext, args: str) -> None:
    # The driver sends the "defaults all" literal to the node, which answers
    # every remaining question and shows the summary — see _fast_forward.
    ctx.fast_forward()


def _cmd_summary(ctx: ChatContext, args: str) -> None:
    ctx.add_artifact("intake_summary")


def _cmd_edit(ctx: ChatContext, args: str) -> None:
    arg = args.strip()
    if arg.isdigit():
        ctx.edit_question(int(arg))
    else:
        ctx.edit_question(None)


def _cmd_image(ctx: ChatContext, args: str) -> None:
    ctx.trigger_image()


def _cmd_voice(ctx: ChatContext, args: str) -> None:
    ctx.trigger_voice()


def _cmd_paste(ctx: ChatContext, args: str) -> None:
    # Newline-preserving paste: terminal bracketed paste strips every newline
    # (see ui/shared/_input.py), so long/structured text goes through the
    # clipboard directly — same escape hatch the standup transcript uses.
    from yeaboi.clipboard import read_clipboard_text

    text = read_clipboard_text()
    if not text:
        ctx.add_system("Clipboard is empty (or unreadable) — copy the text first, then /paste.")
        return
    if not ctx.insert_text(text):
        from yeaboi.input_guardrails import MAX_CHAT_INPUT_CHARS

        ctx.add_system(f"Paste truncated at {MAX_CHAT_INPUT_CHARS:,} characters.")


def _cmd_small(ctx: ChatContext, args: str) -> None:
    ctx.switch_size("small_project")


def _cmd_large(ctx: ChatContext, args: str) -> None:
    ctx.switch_size("smart")


def _cmd_quit(ctx: ChatContext, args: str) -> None:
    ctx.request_quit()


def _cmd_duck(ctx: ChatContext, args: str) -> None:
    ctx.toggle_duck()


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("help", "list commands and shortcuts", _cmd_help),
    SlashCommand("export", "save the plan and/or chat transcript", _cmd_export),
    SlashCommand("skip", "skip the current question", _cmd_skip, lambda ctx: ctx.intake_active()),
    SlashCommand("defaults", "accept defaults for all remaining questions", _cmd_defaults, lambda c: c.intake_active()),
    SlashCommand(
        "form",
        "fill out the remaining questions as a full-screen form",
        _cmd_form,
        # Pre-questionnaire availability lets a greeting-time /form defer
        # (the driver opens the form after the description) instead of
        # bouncing off the unknown-command notice.
        lambda c: c.intake_active() or not c.questionnaire_exists(),
    ),
    SlashCommand(
        "finish",
        "answer the remaining questions with defaults",
        _cmd_finish,
        # Available pre-questionnaire too (the greeting advertises it) — the
        # driver defers until the description exists, like /form.
        lambda c: not c.plan_complete(),
    ),
    SlashCommand("summary", "show your answers so far", _cmd_summary, lambda ctx: ctx.questionnaire_exists()),
    SlashCommand("edit", "re-answer a question (/edit 6) or refine the last artifact", _cmd_edit),
    SlashCommand("image", "attach a screenshot from the clipboard (same as Ctrl+V)", _cmd_image),
    SlashCommand("voice", "dictate (same as double-tap Space)", _cmd_voice),
    SlashCommand("paste", "paste from clipboard keeping line breaks", _cmd_paste),
    SlashCommand("small", "switch to a Small plan", _cmd_small),
    SlashCommand("large", "switch to a Large plan", _cmd_large),
    SlashCommand("duck", "mute or unmute the duck's speech bubble", _cmd_duck),
    SlashCommand("quit", "leave planning (progress is saved)", _cmd_quit),
)

_BY_NAME = {cmd.name: cmd for cmd in COMMANDS}


def matching_commands(ctx: ChatContext, prefix: str) -> list[SlashCommand]:
    """Commands whose name starts with prefix (for the /-menu), available only."""
    prefix = prefix.lstrip("/").lower()
    return [cmd for cmd in COMMANDS if cmd.name.startswith(prefix) and cmd.available(ctx)]


def exact_command(ctx: ChatContext, word: str) -> SlashCommand | None:
    """The command a bare "/word" token names — exact match, available only.

    Used for inline tokens typed mid-draft: only an exact name runs (a prose
    token like "/usr/bin" must fall through to the message untouched), unlike
    the prefix matching the /-menu uses for display.
    """
    command = _BY_NAME.get(word.lstrip("/").lower())
    if command is not None and command.available(ctx):
        return command
    return None


def dispatch(ctx: ChatContext, text: str) -> bool:
    """Execute a /command. Returns True when the input was consumed.

    Every /-prefixed input is consumed — including unknown names, which get a
    local notice — so slash text can never fall through to the model.
    """
    if not text.startswith("/"):
        return False
    word, _, args = text[1:].partition(" ")
    command = _BY_NAME.get(word.lower())
    if command is None or not command.available(ctx):
        logger.info("Chat command unknown/unavailable: %s", word.lower()[:20])
        ctx.add_system(f"Unknown command /{word}. Type /help to list commands.")
        return True
    logger.info("Chat command: %s", command.name)
    command.handler(ctx, args)
    return True
