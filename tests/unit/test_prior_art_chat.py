"""The prior-art step's chat surface.

The two guards are the point: both the prior-art sub-loop and the confirmation
gate run with awaiting_confirmation set and current_question past the last
question, so without an explicit ordering the Accept/Edit menu renders over the
prior-art card and the summary card is posted mid-loop. The step is one batch
now: a multi-select row per candidate, a card that previews whichever row the
carousel highlights, and a submission in the node's index grammar.
"""

from __future__ import annotations

from yeaboi.agent.chat_session import ChatSession, at_intake_summary, at_prior_art
from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.ui.session.chat._question_view import (
    CONFIRM_ACCEPT,
    PRIOR_ART_CONTINUE,
    derive_question_view,
)
from yeaboi.ui.session.chat._screen import ChoiceRows, _placeholder
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
    def test_ask_stage_offers_one_multi_row_per_candidate(self):
        view = derive_question_view({"questionnaire": _qs("ask")})
        assert [label for label, _ in view.choices] == ["acme/auth", "acme/pay"]
        assert view.multi_select is True
        assert view.auto_submit is False
        assert view.prior_art is True

    def test_nothing_starts_checked_on_a_fresh_run(self):
        # Relevance is opt-in — a quick Enter must accept nothing by accident.
        view = derive_question_view({"questionnaire": _qs("ask")})
        assert all(checked is False for _, checked in view.choices)

    def test_a_legacy_half_loop_resumes_with_its_verdicts_pre_checked(self):
        qs = _qs("ask")
        qs._prior_art_accepted = [dict(qs._prior_art_candidates[1])]
        view = derive_question_view({"questionnaire": qs})
        assert [checked for _, checked in view.choices] == [False, True]

    def test_the_confirm_menu_does_not_render_over_the_card(self):
        # The guard this test exists for.
        view = derive_question_view({"questionnaire": _qs("ask")})
        assert CONFIRM_ACCEPT not in [label for label, _ in view.choices]

    def test_reason_stage_has_no_menu_so_the_composer_owns_it(self):
        # Legacy tolerance: only a session serialized by an older build can
        # still be in "reason"; its first input triggers the node's re-ask.
        assert derive_question_view({"questionnaire": _qs("reason")}).choices is None

    def test_the_confirm_menu_returns_once_prior_art_is_done(self):
        view = derive_question_view({"questionnaire": _qs("done")})
        assert CONFIRM_ACCEPT in [label for label, _ in view.choices]
        assert view.prior_art is False

    def test_untouched_when_prior_art_never_ran(self):
        view = derive_question_view({"questionnaire": _qs("")})
        assert CONFIRM_ACCEPT in [label for label, _ in view.choices]

    def test_empty_stage_offers_continue(self):
        view = derive_question_view({"questionnaire": _qs("empty")})
        assert [label for label, _ in view.choices] == [PRIOR_ART_CONTINUE]


class TestPlaceholder:
    def test_the_carousel_ghost_teaches_the_keys_and_the_grammar(self):
        rows = ChoiceRows(options=[("acme/auth", False)], multi=True, carousel=True)
        text = _placeholder("intake", {"questionnaire": _qs("ask"), "messages": ["x"]}, rows)
        assert "Space" in text and "X" in text
        assert "all" in text

    def test_ordinary_menus_keep_their_ghost(self):
        rows = ChoiceRows(options=[("Accept", True)])
        text = _placeholder("intake", {"questionnaire": _qs("done"), "messages": ["x"]}, rows)
        assert "↑/↓" in text


class TestCard:
    def _out(self, qs, preview=None, width=70):
        from rich.console import Console

        graph_state = {"questionnaire": qs}
        if preview is not None:
            graph_state["_prior_art_preview"] = preview
        console = Console(width=width, record=True)
        console.print(_artifact_renderable("prior_art", graph_state, width - 2))
        return console.export_text()

    def test_registered_with_a_title(self):
        assert _ARTIFACT_TITLES["prior_art"] == "You already have this"

    def test_renders_the_first_candidate_by_default(self):
        out = self._out(_qs("ask"))
        assert "acme/auth" in out
        assert "does OIDC" in out

    def test_the_preview_index_drives_the_card(self):
        # The carousel: the driver publishes the highlight and the card
        # follows it — node state never moves.
        out = self._out(_qs("ask"), preview=1)
        assert "takes cards" in out
        assert "does OIDC" not in out

    def test_an_out_of_range_preview_clamps_instead_of_crashing(self):
        out = self._out(_qs("ask"), preview=99)
        assert "takes cards" in out

    def test_a_card_with_no_candidates_still_renders_nothing(self):
        qs = _qs("ask")
        qs._prior_art_candidates = []
        assert _artifact_renderable("prior_art", {"questionnaire": qs}, 68) is None

    def test_no_questionnaire_renders_nothing(self):
        assert _artifact_renderable("prior_art", {}, 68) is None


class TestRoster:
    """The card details one repo at a time, so the roster is the evidence the
    others exist — and the footer names the browse keys."""

    def _out(self, qs, preview=0, width=70):
        from rich.console import Console

        console = Console(width=width, record=True)
        console.print(
            _artifact_renderable("prior_art", {"questionnaire": qs, "_prior_art_preview": preview}, width - 2)
        )
        return console.export_text()

    def test_every_candidate_is_listed_not_just_the_previewed_one(self):
        out = self._out(_qs("ask"))
        assert "acme/auth" in out
        assert "acme/pay" in out

    def test_the_footer_names_the_browse_keys(self):
        out = self._out(_qs("ask"))
        assert "2 repositories" in out
        assert "browse" in out

    def test_no_verdicts_on_the_card(self):
        # Selection lives in the checkboxes below, bans in their ✗ rows — a ✓
        # here would duplicate (and race) the widget state.
        qs = _qs("ask")
        qs._prior_art_accepted = [dict(qs._prior_art_candidates[0])]
        out = self._out(qs, preview=1)
        assert "kept" not in out

    def test_a_lone_candidate_gets_no_roster(self):
        qs = _qs("ask")
        qs._prior_art_candidates = qs._prior_art_candidates[:1]
        out = self._out(qs)
        assert "browse" not in out


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
        qs._prior_art_rejected = [{"key": "github:acme/pay", "name": "acme/pay", "reason": ""}]
        out = self._out(qs)
        assert "Reviewed 2" in out
        assert "kept" in out
        assert "never suggest" in out

    def test_an_unpicked_candidate_reads_not_this_time(self):
        # Unchecked is a pass for this project only — distinguishable from
        # the permanent ✗ ban.
        qs = _qs("done")
        qs._prior_art_accepted = [dict(qs._prior_art_candidates[0])]
        out = self._out(qs)
        assert "not this time" in out


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
        assert "_prior_art_preview" in source, "the stale preview index must not outlive the reset loop"


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
    def test_one_static_line_names_the_keys_and_the_grammar(self):
        from yeaboi.agent.chat_session import PRIOR_ART_VERDICT_PROMPT

        prompt = PRIOR_ART_VERDICT_PROMPT
        assert "**Space**" in prompt
        assert "**X**" in prompt
        assert "**all**" in prompt


class TestDriverGuards:
    def test_summary_card_is_withheld_during_the_subloop(self):
        # Without this the markdown wall the card replaced comes back, on top
        # of the prior-art card.
        for stage in ("ask", "reason"):
            assert at_intake_summary({"questionnaire": _qs(stage)}) is False

    def test_summary_card_returns_once_the_subloop_is_done(self):
        assert at_intake_summary({"questionnaire": _qs("done")}) is True

    def test_at_prior_art_tracks_the_live_stages(self):
        assert at_prior_art({"questionnaire": _qs("ask")}) is True
        assert at_prior_art({"questionnaire": _qs("reason")}) is True
        assert at_prior_art({"questionnaire": _qs("done")}) is False


class TestPickMapping:
    def _driver(self):
        from yeaboi.ui.session.chat._driver import _ChatDriver

        driver = object.__new__(_ChatDriver)
        driver.session = ChatSession(None, {"_prior_art_preview": 1})
        return driver

    def test_continue_maps_to_the_nodes_ok(self):
        assert self._driver()._prior_art_pick(PRIOR_ART_CONTINUE) == "ok"

    def test_grammar_strings_pass_through_untouched(self):
        # The widget already submits the node's index grammar, and a typed
        # answer is the same grammar — there is nothing left to map.
        driver = self._driver()
        assert driver._prior_art_pick("1 3 !2") == "1 3 !2"
        assert driver._prior_art_pick("none") == "none"
        assert driver._prior_art_pick("") == ""

    def test_the_preview_dies_with_the_menu(self):
        driver = self._driver()
        driver._prior_art_pick("none")
        assert "_prior_art_preview" not in driver.state


class TestHeadless:
    def test_the_batch_is_answered_none(self):
        from yeaboi.agent.headless import _next_auto_input

        # Accepting on the user's behalf would put unvetted repos in a plan;
        # "none" writes nothing to the ledger, so nothing is suppressed either.
        assert _next_auto_input({"questionnaire": _qs("ask")}) == "none"

    def test_a_legacy_reason_stage_gets_none_too(self):
        from yeaboi.agent.headless import _next_auto_input

        # The node turns that into a batch re-ask; the next pass answers it.
        assert _next_auto_input({"questionnaire": _qs("reason")}) == "none"

    def test_confirm_still_works_once_prior_art_is_done(self):
        from yeaboi.agent.headless import _next_auto_input

        assert _next_auto_input({"questionnaire": _qs("done")}) == "confirm"


class TestFormPathParity:
    def test_the_form_loop_does_not_exit_mid_subloop(self):
        import inspect

        from yeaboi.ui.session.phases import _phases_intake

        source = inspect.getsource(_phases_intake._phase_intake_questions)
        assert "_prior_art_stage" in source, "the form path must not fall through the prior-art sub-loop"
