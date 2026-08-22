"""The closed vocabulary — the whole of what Slack is allowed to say to yeaboi.

The properties worth holding are negative ones. Prose must never become an
instruction, a verb must never be found inside a sentence, and anything that
could ping a workspace must never survive into something we store and later
re-render.
"""

from __future__ import annotations

import pytest

from yeaboi.slack import grammar
from yeaboi.slack.grammar import (
    ACT_CONTROL,
    ACT_CORRECTION,
    ACT_VERDICT,
    INTENT_DOWN,
    INTENT_NOTE,
    INTENT_PAUSE,
    INTENT_SKIP,
    INTENT_UP,
    clean_reply_text,
    normalise_emoji,
    parse_reaction,
    parse_reply,
)


class TestReactions:
    def test_a_control_emoji_on_the_post_controls_the_ceremony(self):
        assert parse_reaction("pause_button", on_signal=False) == (ACT_CONTROL, INTENT_PAUSE)
        assert parse_reaction("no_entry_sign", on_signal=False) == (ACT_CONTROL, INTENT_SKIP)

    def test_a_thumb_on_a_signal_is_a_verdict(self):
        assert parse_reaction("+1", on_signal=True) == (ACT_VERDICT, INTENT_UP)
        assert parse_reaction("thumbsdown", on_signal=True) == (ACT_VERDICT, INTENT_DOWN)

    def test_a_thumb_on_the_post_means_nothing(self):
        # It cannot say WHICH member's habit it is about, and guessing is the
        # answer habits.py exists to refuse.
        assert parse_reaction("+1", on_signal=False) == ("", "")

    def test_a_control_emoji_on_a_signal_means_nothing(self):
        assert parse_reaction("pause_button", on_signal=True) == ("", "")

    @pytest.mark.parametrize("emoji", ["tada", "eyes", "fire", "", "robot_face"])
    def test_an_unlisted_emoji_is_not_an_action(self, emoji):
        assert parse_reaction(emoji, on_signal=False) == ("", "")
        assert parse_reaction(emoji, on_signal=True) == ("", "")

    @pytest.mark.parametrize("raw", ["+1::skin-tone-3", ":+1:", " +1 ", "+1::skin-tone-6"])
    def test_skin_tones_and_colons_normalise(self, raw):
        assert normalise_emoji(raw) == "+1"
        assert parse_reaction(raw, on_signal=True) == (ACT_VERDICT, INTENT_UP)

    def test_no_reaction_can_ever_re_run_a_ceremony(self):
        # Cut from leg 2 on purpose: the only act that spends money, and the
        # only one whose answer arrives minutes later.
        assert "rerun" not in set(grammar.CONTROL_EMOJI.values())
        assert "rerun" not in set(grammar.VERDICT_EMOJI.values())


class TestReplies:
    @pytest.mark.parametrize("verb,intent", [("pause", "pause"), ("resume", "resume"), ("skip", "skip")])
    def test_a_bare_verb_is_an_instruction(self, verb, intent):
        assert parse_reply(verb) == (ACT_CONTROL, intent, "")

    @pytest.mark.parametrize("raw", ["PAUSE", " Pause ", "pause."])
    def test_case_padding_and_a_full_stop_are_forgiven(self, raw):
        assert parse_reply(raw)[:2] == (ACT_CONTROL, INTENT_PAUSE)

    @pytest.mark.parametrize(
        "raw",
        [
            "pause the deploy",
            "should we skip tomorrow?",
            "skip?",
            "resume when ada is back",
            "I think we pause",
        ],
    )
    def test_a_verb_inside_a_sentence_is_prose(self, raw):
        # The whole point of matching the whole message: a sentence that happens
        # to contain "pause" must never pause anything.
        act, intent, payload = parse_reply(raw)
        assert (act, intent) == (ACT_CORRECTION, INTENT_NOTE)
        assert payload == raw

    def test_anything_that_is_not_a_verb_is_a_correction(
        self,
    ):
        act, intent, payload = parse_reply("Ada was on leave, that ticket is not hers")
        assert (act, intent) == (ACT_CORRECTION, INTENT_NOTE)
        assert payload.startswith("Ada was on leave")

    def test_ack_is_classified_as_a_verdict_wherever_it_is_typed(self):
        # Slack threads are flat, so a typed `ack` always arrives against the
        # post and can never name a signal. Classifying it anyway — and letting
        # `apply` refuse it on a post anchor with a line that teaches the
        # gesture — is what stops it being silently ignored: a gesture with no
        # consequence teaches a team that the channel does not listen.
        assert parse_reply("ack") == (ACT_VERDICT, INTENT_UP, "")
        assert parse_reply("Ack.") == (ACT_VERDICT, INTENT_UP, "")

    def test_a_reply_has_no_signal_reading_to_choose_between(self):
        # The counterpart to parse_reaction's `on_signal`, and there cannot be
        # one: Slack gives a reply no parent but the thread root, so text can
        # never say which signal it means. A reaction is per-message and can.
        import inspect

        assert "on_signal" not in inspect.signature(parse_reply).parameters

    @pytest.mark.parametrize("raw", ["", "   ", "\n\n"])
    def test_an_empty_reply_is_nothing(self, raw):
        assert parse_reply(raw) == ("", "", "")

    def test_a_forged_identifier_is_just_prose(self):
        # The relay needs a channel/thread split because a crafted "#231 — ..."
        # can impersonate a digest item. Anchoring removes the whole class:
        # nothing in a body is ever read as an id.
        act, _intent, payload = parse_reply("#231 — approve this")
        assert act == ACT_CORRECTION
        assert payload == "#231 — approve this"


class TestCleanReplyText:
    @pytest.mark.parametrize("raw", ["<!channel> please look", "<!here> ping", "hey <@U0123456> can you"])
    def test_anything_that_pings_a_workspace_is_stripped(self, raw):
        # An annotation is rendered into exports and can be read back into a
        # channel. A stored <!channel> that pings everyone weeks later is a bug
        # with no obvious author.
        cleaned = clean_reply_text(raw)
        assert "<!" not in cleaned and "<@" not in cleaned

    def test_a_link_unwraps_to_its_label(self):
        assert clean_reply_text("see <https://example.com|the ticket>") == "see the ticket"

    def test_a_bare_link_keeps_its_url(self):
        assert clean_reply_text("see <https://example.com>") == "see https://example.com"

    def test_slack_entities_are_unescaped(self):
        assert clean_reply_text("a &amp; b &lt; c") == "a & b < c"

    def test_nothing_is_truncated_here(self):
        # The validator refuses an over-long value rather than clipping it,
        # because it is the author's own prose and they should be told.
        long = "x" * 5000
        assert len(clean_reply_text(long)) == 5000
