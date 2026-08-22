"""What a reaction or a reply is allowed to mean. Pure: no IO, no model.

This is the file to read first when asking whether the two-way lane is safe,
because it is the whole of what Slack can say to yeaboi. Three rules hold:

1. **Text never selects an action.** Only two things do: an allowlisted human's
   reaction on a message yeaboi posted, and a whole-message verb from a fixed
   list. Prose is data.
2. **Text never identifies a target.** Every id — session, ceremony, run,
   member, rule — comes from the anchor row for the message being answered.
   There is no ``#<number>`` in a body for anyone to forge.
3. **A verb is the whole message, not a leading token.** That makes the verb
   and correction readings partition the message space with no overlap and no
   precedence to get wrong: *anything that is not exactly a verb is a
   correction*. The fleet's own relay uses a leading token because there it is
   an identifier with prose after it; here the anchor already said which thing
   is being talked about, so the verb is all that is left.

The emoji are chosen for having no skin-tone variants, and the few that do
(``+1``, ``-1``) are normalised before lookup.
"""

from __future__ import annotations

import html
import logging
import re

logger = logging.getLogger(__name__)

# ── the acts ───────────────────────────────────────────────────────────────

ACT_CONTROL = "control"
ACT_VERDICT = "verdict"
ACT_CORRECTION = "correction"

INTENT_PAUSE = "pause"
INTENT_RESUME = "resume"
INTENT_SKIP = "skip"
INTENT_UP = "up"
INTENT_DOWN = "down"
INTENT_NOTE = "note"

#: Reactions on the POST itself: the post is about a ceremony, so a reaction on
#: it is about that ceremony. Re-run is deliberately absent — it is the only
#: gesture that would spend money, and its guard semantics are unresolved.
CONTROL_EMOJI: dict[str, str] = {
    "pause_button": INTENT_PAUSE,
    "double_vertical_bar": INTENT_PAUSE,
    "arrow_forward": INTENT_RESUME,
    "play_or_pause_button": INTENT_RESUME,
    "no_entry_sign": INTENT_SKIP,
    "no_entry": INTENT_SKIP,
}

#: Reactions on a SIGNAL reply: those carry the member and rule, so a thumb is
#: unambiguous. On the post it would have to be guessed, and guessing which
#: member's habit somebody meant is the wrong answer to give confidently.
VERDICT_EMOJI: dict[str, str] = {
    "+1": INTENT_UP,
    "thumbsup": INTENT_UP,
    "-1": INTENT_DOWN,
    "thumbsdown": INTENT_DOWN,
}

#: Typed instead of clicked. `ack` is only meaningful on a signal (where it is a
#: synonym for a thumbs-up); on a post it is answered rather than silently
#: doing nothing, because a gesture with no consequence teaches a team that the
#: channel does not listen.
_VERB_RE = re.compile(r"^(skip|pause|resume|ack)\.?$")

#: Shorter than this, after cleaning, and a reply is an acknowledgement rather
#: than a correction. ``ok``, ``ty``, ``lol``, ``+1`` and a bare 👍 or 🎉 are what
#: a thread is mostly made of, and every one of them would otherwise become a
#: permanent annotation on somebody's standup. The verbs are matched *first*, so
#: ``ack`` and ``skip`` are untouched; this can only ever make the lane quieter.
MIN_CORRECTION = 4

_MENTION_RE = re.compile(r"<[@#!][^>]*>")
_LINK_RE = re.compile(r"<(https?://[^|>]+)(?:\|([^>]*))?>")


def normalise_emoji(name: str) -> str:
    """Strip Slack's decorations: colons, and a skin-tone suffix."""
    return (name or "").strip().strip(":").split("::")[0].lower()


def clean_reply_text(raw: str) -> str:
    """Slack's wire text as plain prose, with the things that can ping stripped.

    Mentions and broadcasts go first and unconditionally. An annotation is
    rendered into exports and can be read back out into a channel, and a stored
    ``<!channel>`` that pings a workspace weeks later — from a note somebody
    wrote as a throwaway remark — is a bug with no obvious author.

    Link syntax unwraps to its label so a URL somebody pasted still reads. This
    runs *before* the artifact validator, which then does the control
    characters, the length caps and the injection sweep; nothing here truncates,
    because a too-long value is the author's own prose and they should be told
    it did not fit rather than have it quietly clipped.
    """
    text = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), raw or "")
    text = _MENTION_RE.sub("", text)
    return html.unescape(text).strip()


def parse_reaction(emoji: str, *, on_signal: bool) -> tuple[str, str]:
    """(act, intent) for a reaction, or ('', '') when it means nothing here.

    ``on_signal`` decides which vocabulary applies, and the split is what makes
    a thumb unambiguous: a signal reply carries one member and one rule, so a
    verdict on it needs no inference at all.
    """
    name = normalise_emoji(emoji)
    if on_signal:
        intent = VERDICT_EMOJI.get(name, "")
        return (ACT_VERDICT, intent) if intent else ("", "")
    intent = CONTROL_EMOJI.get(name, "")
    return (ACT_CONTROL, intent) if intent else ("", "")


def parse_reply(raw: str) -> tuple[str, str, str]:
    """(act, intent, payload) for a threaded reply.

    A whole-message verb is an instruction; everything else long enough to say
    something is a correction carrying its own text. An empty reply — or one too
    short to be anything but an acknowledgement — is neither.

    No ``on_signal`` counterpart to :func:`parse_reaction`, because there cannot
    be one: Slack threads do not nest, so a reply always arrives against the
    post at the root and never against one of the signal replies hanging under
    it. A reaction is per-message and can tell them apart; typed text cannot.
    """
    text = clean_reply_text(raw)
    if not text:
        return "", "", ""
    match = _VERB_RE.match(text.lower())
    if match:
        verb = match.group(1)
        if verb == "ack":
            # Always classified as a verdict, even though a *typed* one can
            # never reach a signal: **Slack threads are flat**, so every reply
            # carries `thread_ts = root_ts` and arrives against the post. Saying
            # so here and letting `apply` refuse it on a post anchor is what
            # turns `ack` into a line that teaches the gesture; returning
            # ("", "", "") would leave the writer with a bot that ignored them.
            return ACT_VERDICT, INTENT_UP, ""
        return ACT_CONTROL, verb, ""
    if len(text) < MIN_CORRECTION:
        return "", "", ""
    return ACT_CORRECTION, INTENT_NOTE, text
