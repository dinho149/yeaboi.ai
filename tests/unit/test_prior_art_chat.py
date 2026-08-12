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

    def test_exhausted_index_renders_nothing_rather_than_crashing(self):
        qs = _qs("ask")
        qs._prior_art_index = 99
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
