"""The prior-art step's chat surface.

The two guards are the point: both the prior-art sub-loop and the confirmation
gate run with awaiting_confirmation set and current_question past the last
question, so without an explicit ordering the Accept/Edit menu renders over the
prior-art card and the summary card is posted mid-loop.
"""

from __future__ import annotations

from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.ui.session.chat._question_view import (
    CONFIRM_ACCEPT,
    PRIOR_ART_NO,
    PRIOR_ART_SKIP,
    PRIOR_ART_YES,
    derive_question_view,
)
from yeaboi.ui.session.chat._screen import _placeholder
from yeaboi.ui.session.chat._transcript import _ARTIFACT_TITLES, _artifact_renderable


def _qs(stage=""):
    qs = QuestionnaireState(current_question=TOTAL_QUESTIONS + 1)
    qs.answers = {i: f"a{i}" for i in range(1, TOTAL_QUESTIONS + 1)}
    qs.awaiting_confirmation = True
    qs._prior_art_stage = stage
    qs._prior_art_candidates = [
        {
            "key": "github:acme/auth",
            "name": "acme/auth",
            "platform": "github",
            "pitch": ["does OIDC"],
            "stack": ["Python"],
        },
        {"key": "github:acme/pay", "name": "acme/pay", "platform": "github", "pitch": ["takes cards"], "stack": ["Go"]},
    ]
    qs._prior_art_index = 0
    return qs


class TestChoiceRows:
    def test_ask_stage_offers_the_three_verdicts(self):
        view = derive_question_view({"questionnaire": _qs("ask")})
        assert [label for label, _ in view.choices] == [PRIOR_ART_YES, PRIOR_ART_NO, PRIOR_ART_SKIP]
        assert view.auto_submit is True
        assert view.multi_select is False

    def test_yes_is_preselected(self):
        view = derive_question_view({"questionnaire": _qs("ask")})
        assert view.choices[0][1] is True

    def test_the_confirm_menu_does_not_render_over_the_card(self):
        # The guard this test exists for.
        view = derive_question_view({"questionnaire": _qs("ask")})
        assert CONFIRM_ACCEPT not in [label for label, _ in view.choices]

    def test_reason_stage_has_no_menu_so_the_composer_owns_it(self):
        assert derive_question_view({"questionnaire": _qs("reason")}).choices is None

    def test_the_confirm_menu_returns_once_prior_art_is_done(self):
        view = derive_question_view({"questionnaire": _qs("done")})
        assert CONFIRM_ACCEPT in [label for label, _ in view.choices]

    def test_untouched_when_prior_art_never_ran(self):
        view = derive_question_view({"questionnaire": _qs("")})
        assert CONFIRM_ACCEPT in [label for label, _ in view.choices]


class TestPlaceholder:
    def test_reason_stage_says_enter_skips(self):
        text = _placeholder("intake", {"questionnaire": _qs("reason"), "messages": ["x"]}, None)
        assert "Enter to skip" in text

    def test_other_stages_are_unaffected(self):
        text = _placeholder("intake", {"questionnaire": _qs("done"), "messages": ["x"]}, None)
        assert "Enter to skip" not in text


class TestCard:
    def test_registered_with_a_title(self):
        assert _ARTIFACT_TITLES["prior_art"] == "You already have this"

    def test_renders_the_current_candidate_with_position(self):
        from rich.console import Console

        console = Console(width=70, record=True)
        console.print(_artifact_renderable("prior_art", {"questionnaire": _qs("ask")}, 68))
        out = console.export_text()
        assert "acme/auth" in out
        assert "does OIDC" in out
        assert "1 of 2" in out

    def test_follows_the_index(self):
        from rich.console import Console

        qs = _qs("ask")
        qs._prior_art_index = 1
        console = Console(width=70, record=True)
        console.print(_artifact_renderable("prior_art", {"questionnaire": qs}, 68))
        out = console.export_text()
        assert "acme/pay" in out and "2 of 2" in out

    def test_exhausted_index_shows_the_verdicts_rather_than_vanishing(self):
        """A renderer that returns None prints "(<title> unavailable)" in the
        card's place, so the card the user just worked through would decay into
        an error string. It becomes the record of what was decided instead."""
        from rich.console import Console

        qs = _qs("ask")
        qs._prior_art_accepted = [dict(qs._prior_art_candidates[0])]
        qs._prior_art_index = 99
        console = Console(width=70, record=True)
        console.print(_artifact_renderable("prior_art", {"questionnaire": qs}, 68))
        out = console.export_text()
        assert "Reviewed 2" in out
        assert "kept" in out

    def test_a_card_with_no_candidates_still_renders_nothing(self):
        qs = _qs("ask")
        qs._prior_art_candidates = []
        assert _artifact_renderable("prior_art", {"questionnaire": qs}, 68) is None

    def test_no_questionnaire_renders_nothing(self):
        assert _artifact_renderable("prior_art", {}, 68) is None


class TestRoster:
    """The card replaces itself as the loop advances, so the roster is the only
    evidence the other candidates exist — without it "1 of 3" reads as a pager
    with no pager keys."""

    def _out(self, qs, width=70):
        from rich.console import Console

        console = Console(width=width, record=True)
        console.print(_artifact_renderable("prior_art", {"questionnaire": qs}, width - 2))
        return console.export_text()

    def test_every_candidate_is_listed_not_just_the_current_one(self):
        out = self._out(_qs("ask"))
        assert "acme/auth" in out
        assert "acme/pay" in out

    def test_the_current_one_is_marked_as_being_decided(self):
        assert "deciding now" in self._out(_qs("ask"))

    def test_a_decided_candidate_carries_its_verdict(self):
        qs = _qs("ask")
        qs._prior_art_accepted = [dict(qs._prior_art_candidates[0])]
        qs._prior_art_index = 1
        out = self._out(qs)
        assert "kept" in out

    def test_a_rejected_candidate_says_so(self):
        qs = _qs("ask")
        qs._prior_art_rejected = [{"key": "github:acme/auth", "name": "acme/auth", "reason": "too old"}]
        qs._prior_art_index = 1
        out = self._out(qs)
        assert "not relevant" in out

    def test_a_lone_candidate_gets_no_roster(self):
        """One row that says "deciding now" under a card about that one repo is
        noise, not orientation."""
        qs = _qs("ask")
        qs._prior_art_candidates = qs._prior_art_candidates[:1]
        out = self._out(qs)
        assert "1 of 1" in out
        assert "deciding now" not in out


class TestClosedCard:
    """What the card becomes after the loop — it stays as the record, because a
    renderer that returns None is drawn as "(You already have this
    unavailable)" for the rest of the session."""

    def _out(self, qs, width=70):
        from rich.console import Console

        console = Console(width=width, record=True)
        console.print(_artifact_renderable("prior_art", {"questionnaire": qs}, width - 2))
        return console.export_text()

    def test_a_done_stage_shows_the_verdicts(self):
        qs = _qs("done")
        qs._prior_art_accepted = [dict(qs._prior_art_candidates[0])]
        qs._prior_art_rejected = [{"key": "github:acme/pay", "name": "acme/pay", "reason": "retiring it"}]
        out = self._out(qs)
        assert "Reviewed 2" in out
        assert "kept" in out
        assert "not relevant" in out
        assert "deciding now" not in out

    def test_skip_the_rest_leaves_the_untouched_ones_marked_not_reviewed(self):
        """ "Skip the rest" ends the loop without moving the index, so keying off
        the index alone would freeze the card on the skipped candidate, still
        marked as the one being decided."""
        qs = _qs("done")
        qs._prior_art_accepted = [dict(qs._prior_art_candidates[0])]
        out = self._out(qs)
        assert "not reviewed" in out
        assert "deciding now" not in out


class TestSizeSwitchDropsTheCard:
    def test_transcript_can_drop_a_card_whose_data_is_gone(self):
        from yeaboi.ui.session.chat._transcript import ChatTranscript

        transcript = ChatTranscript()
        transcript.add_artifact("prior_art")
        transcript.add_artifact("intake_summary")
        transcript.drop_artifact("prior_art")
        kinds = [m.artifact_kind for m in transcript.messages if m.role == "artifact"]
        assert kinds == ["intake_summary"]

    def test_the_driver_drops_it_when_the_size_switch_clears_the_state(self):
        import inspect

        from yeaboi.ui.session.chat._driver import _ChatDriver

        source = inspect.getsource(_ChatDriver._switch_size)
        assert 'drop_artifact("prior_art")' in source, (
            "apply_size_switch clears the prior-art transients, so the card has "
            "nothing left to render from and would show as '(… unavailable)'"
        )


class TestSummaryCard:
    """The chat replaces the node's markdown summary with this card, so anything
    the markdown carries and the card does not is invisible on the default
    planning surface."""

    def _out(self, qs, width=100):
        from rich.console import Console

        from yeaboi.ui.session._utils import _render_tui_intake_summary

        console = Console(width=width, record=True)
        console.print(_render_tui_intake_summary(qs, width - 4))
        return console.export_text()

    def test_accepted_prior_art_appears(self):
        qs = _qs("done")
        qs._prior_art_accepted = [dict(qs._prior_art_candidates[0])]
        out = self._out(qs)
        assert "Prior art" in out
        assert "acme/auth" in out

    def test_no_section_when_nothing_was_accepted(self):
        assert "Prior art" not in self._out(_qs("done"))


class TestVerdictPrompt:
    def _prompt(self, index, total):
        from yeaboi.ui.session.chat._driver import _prior_art_verdict_prompt

        qs = _qs("ask")
        qs._prior_art_candidates = [{"key": f"k{i}", "name": f"r{i}"} for i in range(total)]
        qs._prior_art_index = index
        return _prior_art_verdict_prompt(qs)

    def test_more_than_one_left_names_the_count(self):
        assert "next of 2 more" in self._prompt(0, 3)

    def test_exactly_one_left_says_last(self):
        assert "the last one" in self._prompt(1, 3)

    def test_the_final_candidate_promises_nothing_further(self):
        assert "This is the last one." in self._prompt(2, 3)

    def test_the_pick_instructions_survive(self):
        assert "**yes**" in self._prompt(0, 3)


class TestDriverGuards:
    def _driver(self, qs):
        from yeaboi.ui.session.chat._driver import _ChatDriver

        driver = object.__new__(_ChatDriver)
        driver.state = {"questionnaire": qs}
        return driver

    def test_summary_card_is_withheld_during_the_subloop(self):
        # Without this the markdown wall the card replaced comes back, on top
        # of the prior-art card.
        for stage in ("ask", "reason"):
            assert self._driver(_qs(stage))._at_intake_summary() is False

    def test_summary_card_returns_once_the_subloop_is_done(self):
        assert self._driver(_qs("done"))._at_intake_summary() is True

    def test_at_prior_art_tracks_the_two_live_stages(self):
        assert self._driver(_qs("ask"))._at_prior_art() is True
        assert self._driver(_qs("reason"))._at_prior_art() is True
        assert self._driver(_qs("done"))._at_prior_art() is False


class TestPickMapping:
    def _driver(self):
        from yeaboi.ui.session.chat._driver import _ChatDriver

        return object.__new__(_ChatDriver)

    def test_labels_map_to_the_digits_the_node_reads(self):
        driver = self._driver()
        assert driver._prior_art_pick(PRIOR_ART_YES) == "1"
        assert driver._prior_art_pick(PRIOR_ART_NO) == "2"
        assert driver._prior_art_pick(PRIOR_ART_SKIP) == "3"

    def test_free_text_passes_through_untouched(self):
        # Including text that starts with a digit — a reason is prose, and
        # parsing it would turn "3 years old and unmaintained" into a skip.
        driver = self._driver()
        assert driver._prior_art_pick("3 years old and unmaintained") == "3 years old and unmaintained"
        assert driver._prior_art_pick("") == ""


class TestHeadless:
    def test_the_subloop_is_skipped_rather_than_answered(self):
        from yeaboi.agent.headless import _next_auto_input

        # Accepting on the user's behalf would put unvetted repos in a plan;
        # rejecting would write a permanent suppression. Bailing does neither.
        assert _next_auto_input({"questionnaire": _qs("ask")}) == "3"

    def test_a_reason_prompt_is_answered_with_silence(self):
        from yeaboi.agent.headless import _next_auto_input

        assert _next_auto_input({"questionnaire": _qs("reason")}) == ""

    def test_confirm_still_works_once_prior_art_is_done(self):
        from yeaboi.agent.headless import _next_auto_input

        assert _next_auto_input({"questionnaire": _qs("done")}) == "confirm"


class TestFormPathParity:
    def test_the_form_loop_does_not_exit_mid_subloop(self):
        import inspect

        from yeaboi.ui.session.phases import _phases_intake

        source = inspect.getsource(_phases_intake._phase_intake_questions)
        assert "_prior_art_stage" in source, "the form path must not fall through the prior-art sub-loop"
