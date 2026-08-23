"""Unit tests for the practice-feedback ledger.

Two properties carry this feature, and most of these tests are one or the other:

- A verdict is cast on a *signal* but remembered per *change*, so tomorrow's run
  rebuilds a shorter sentence rather than going silent for that person.
- The memory is scoped to ``(rule, handle)``, so excusing a pull request for
  "untracked work" says nothing about whether it is also an oversized change.
"""

import pytest

from yeaboi.agent.state import MemberUpdate, PracticeSignal, StandupReport
from yeaboi.standup import habits, practice_feedback
from yeaboi.standup.store import StandupStore


@pytest.fixture
def store(tmp_path):
    with StandupStore(tmp_path / "sessions.db") as s:
        yield s


def _signal(rule="untracked-work", handles=("url:https://x/pull/42",), title="Untracked work") -> PracticeSignal:
    return PracticeSignal(
        rule=rule,
        title=title,
        detail="PR #42 carries no ticket reference.",
        evidence=(("#42", "https://x/pull/42"),),
        handles=handles,
    )


def _report(*signals, member="Ada", date="2026-08-02") -> StandupReport:
    return StandupReport(
        session_id="s1",
        date=date,
        member_updates=(MemberUpdate(name=member, practices=tuple(signals)),),
        practice_rollup=habits.rollup({member: signals}),
    )


class TestChangeHandle:
    def test_the_same_change_gets_the_same_handle_on_a_later_run(self):
        # The whole point: a pull request open for a week must be recognised as
        # the one the team already excused.
        today = {"kind": "pr", "key": "#42", "url": "https://x/pull/42", "repository": "x"}
        tomorrow = {"kind": "pr", "key": "#42", "url": "https://x/pull/42", "repository": "x", "status": "merged"}
        assert habits.change_handle(today) == habits.change_handle(tomorrow)

    def test_a_url_wins_over_every_other_field(self):
        assert habits.change_handle({"url": "https://x/pull/42", "key": "#9"}) == "url:https://x/pull/42"

    def test_falls_back_to_repository_and_identifier(self):
        assert habits.change_handle({"kind": "commit", "repository": "x", "key": "ABC123"}) == "commit:x:abc123"

    def test_a_local_commit_with_no_id_hashes_its_subject(self):
        item = {"kind": "commit", "repository": "", "title": "wip"}
        handle = habits.change_handle(item)
        assert handle.startswith("commit::s")
        assert handle == habits.change_handle(dict(item))

    def test_different_changes_get_different_handles(self):
        a = habits.change_handle({"kind": "commit", "repository": "x", "title": "add login"})
        b = habits.change_handle({"kind": "commit", "repository": "x", "title": "add logout"})
        assert a != b


class TestSignalHandles:
    def test_a_signal_carries_a_handle_for_every_item_not_just_its_links(self):
        # Evidence is capped at four links; a thumbs-down still has to silence
        # the changes the sentence rolled into "and 3 others".
        items = [{"kind": "commit", "repository": "x", "key": f"sha{i}"} for i in range(6)]
        signal = habits._signal(habits.RULE_COMMIT_MESSAGES, "thin", items)
        assert len(signal.evidence) == habits._EVIDENCE_PER_SIGNAL
        assert len(signal.handles) == 6

    def test_duplicate_items_collapse_to_one_handle(self):
        item = {"kind": "pr", "url": "https://x/pull/42"}
        assert habits._signal("untracked-work", "d", [item, dict(item)]).handles == ("url:https://x/pull/42",)


class TestLedger:
    def test_a_verdict_is_scoped_to_its_rule(self, store):
        store.record_practice_feedback("s1", rule="untracked-work", handle="h1", verdict="down")
        ledger = practice_feedback.load(store, "s1")
        assert ledger.is_excused("untracked-work", "h1")
        assert not ledger.is_excused("large-change", "h1")

    def test_re_voting_flips_rather_than_stacking(self, store):
        store.record_practice_feedback("s1", rule="untracked-work", handle="h1", verdict="down")
        store.record_practice_feedback("s1", rule="untracked-work", handle="h1", verdict="up")
        ledger = practice_feedback.load(store, "s1")
        assert not ledger.is_excused("untracked-work", "h1")
        assert ("untracked-work", "h1") in ledger.confirmed
        assert len(store.load_practice_feedback("s1")) == 1

    def test_one_vote_on_a_multi_change_signal_teaches_the_prompt_once(self, store):
        # Otherwise a verdict on a five-commit signal would outvote five
        # separate verdicts that disagree with it.
        for handle in ("h1", "h2", "h3"):
            store.record_practice_feedback(
                "s1", rule="untracked-work", handle=handle, verdict="down", note="spike ticket", subject="#42"
            )
        ledger = practice_feedback.load(store, "s1")
        assert len(ledger.excused) == 3
        assert len(ledger.corrections()) == 1

    def test_only_the_adjudicated_rules_reach_the_prompt(self, store):
        # The other five rules never consult a model, so teaching it about them
        # would be answering a question it is never asked.
        store.record_practice_feedback("s1", rule="large-change", handle="h1", verdict="down", note="generated")
        store.record_practice_feedback("s1", rule="untracked-docs", handle="h2", verdict="down", note="the RFC")
        kinds = {c["kind"] for c in practice_feedback.load(store, "s1").corrections()}
        assert kinds == {"untracked-docs"}

    def test_a_row_with_nothing_to_say_still_suppresses(self, store):
        store.record_practice_feedback("s1", rule="untracked-work", handle="h1", verdict="down")
        ledger = practice_feedback.load(store, "s1")
        assert ledger.is_excused("untracked-work", "h1")
        assert ledger.corrections() == ()

    def test_notes_are_clipped(self, store):
        store.record_practice_feedback(
            "s1", rule="untracked-work", handle="h1", verdict="down", note="x" * 500, subject="#42"
        )
        assert len(practice_feedback.load(store, "s1").corrections()[0]["note"]) <= practice_feedback._NOTE_CLIP

    def test_examples_are_capped(self, store):
        for i in range(40):
            store.record_practice_feedback(
                "s1", rule="untracked-work", handle=f"h{i}", verdict="down", note=f"reason {i}"
            )
        assert len(practice_feedback.load(store, "s1").corrections()) <= practice_feedback._MAX_CORRECTIONS

    def test_an_unreadable_ledger_leaves_detection_unadjusted(self):
        class Broken:
            def load_practice_feedback(self, session_id):
                raise RuntimeError("no such table")

        ledger = practice_feedback.load(Broken(), "s1")
        assert not ledger
        assert not ledger.is_excused("untracked-work", "h1")

    def test_rows_from_another_session_are_not_visible(self, store):
        store.record_practice_feedback("other", rule="untracked-work", handle="h1", verdict="down")
        assert not practice_feedback.load(store, "s1").is_excused("untracked-work", "h1")


class TestApplyVerdict:
    def test_thumbs_down_removes_the_signal_and_recomputes_the_rollup(self, store):
        run_id = store.record_run(_report(_signal(), _signal(rule="large-change", handles=("h2",))))
        assert practice_feedback.apply_verdict(
            store, session_id="s1", member="Ada", rule="untracked-work", verdict="down", note="spike"
        )
        after = store.get_run_by_id(run_id)
        assert [s.rule for s in after.member_updates[0].practices] == ["large-change"]
        assert after.practice_rollup == (("large-change", 1),)

    def test_thumbs_down_remembers_every_change_behind_the_signal(self, store):
        store.record_run(_report(_signal(handles=("h1", "h2", "h3"))))
        practice_feedback.apply_verdict(store, session_id="s1", member="Ada", rule="untracked-work", verdict="down")
        ledger = practice_feedback.load(store, "s1")
        assert all(ledger.is_excused("untracked-work", h) for h in ("h1", "h2", "h3"))

    def test_thumbs_up_leaves_the_report_alone(self, store):
        run_id = store.record_run(_report(_signal()))
        assert practice_feedback.apply_verdict(
            store, session_id="s1", member="Ada", rule="untracked-work", verdict="up"
        )
        after = store.get_run_by_id(run_id)
        assert [s.rule for s in after.member_updates[0].practices] == ["untracked-work"]
        assert practice_feedback.load(store, "s1").confirmed

    def test_a_verdict_on_a_signal_that_is_gone_is_a_no_op(self, store):
        store.record_run(_report(_signal()))
        practice_feedback.apply_verdict(store, session_id="s1", member="Ada", rule="untracked-work", verdict="down")
        # Voting again from a stale screen must not write a row about a change
        # it can no longer identify.
        assert not practice_feedback.apply_verdict(
            store, session_id="s1", member="Ada", rule="untracked-work", verdict="down"
        )
        assert len(store.load_practice_feedback("s1")) == 1

    def test_a_verdict_for_an_unknown_member_is_a_no_op(self, store):
        store.record_run(_report(_signal()))
        assert not practice_feedback.apply_verdict(
            store, session_id="s1", member="Grace", rule="untracked-work", verdict="down"
        )

    def test_no_stored_run_at_all_is_a_no_op(self, store):
        assert not practice_feedback.apply_verdict(
            store, session_id="s1", member="Ada", rule="untracked-work", verdict="down"
        )

    def test_it_targets_the_latest_run_by_default(self, store):
        store.record_run(_report(_signal(), date="2026-08-01"))
        newest = store.record_run(_report(_signal(), date="2026-08-02"))
        practice_feedback.apply_verdict(store, session_id="s1", member="Ada", rule="untracked-work", verdict="down")
        assert store.get_run_by_id(newest).member_updates[0].practices == ()

    def test_an_explicit_run_id_is_honoured(self, store):
        older = store.record_run(_report(_signal(), date="2026-08-01"))
        newer = store.record_run(_report(_signal(), date="2026-08-02"))
        practice_feedback.apply_verdict(
            store, session_id="s1", member="Ada", rule="untracked-work", verdict="down", run_id=older
        )
        assert store.get_run_by_id(older).member_updates[0].practices == ()
        assert store.get_run_by_id(newer).member_updates[0].practices != ()

    @pytest.mark.parametrize("bad", ["maybe", "", "UP"])
    def test_an_unknown_verdict_is_rejected(self, store, bad):
        with pytest.raises(ValueError, match="verdict"):
            practice_feedback.apply_verdict(store, session_id="s1", member="Ada", rule="untracked-work", verdict=bad)

    def test_an_unknown_rule_is_rejected(self, store):
        with pytest.raises(ValueError, match="rule"):
            practice_feedback.apply_verdict(store, session_id="s1", member="Ada", rule="vibes", verdict="down")

    def test_a_signal_with_no_handles_is_refused_outright(self, store):
        # Hiding it while remembering nothing is the worst of both: it would
        # look answered and be back tomorrow. The MCP tool can name any signal
        # in any stored run, so the guard lives here, not only in the TUI.
        store.record_run(_report(PracticeSignal(rule="untracked-work", title="Untracked work")))
        assert not practice_feedback.apply_verdict(
            store, session_id="s1", member="Ada", rule="untracked-work", verdict="down"
        )
        assert store.load_practice_feedback("s1") == []


class TestVotable:
    def test_a_signal_with_no_handles_cannot_be_voted_on(self):
        # Reports written before this feature existed have nothing to remember,
        # so offering a thumbs-down on them would half-work in silence.
        old = PracticeSignal(rule="untracked-work", title="Untracked work")
        assert practice_feedback.votable([old, _signal()]) == (_signal(),)


class TestDeferredRewrite:
    """``rewrite_report=False``: keep the permanent half, skip the cosmetic one.

    The two halves of a verdict have different numbers of writers. The per-handle
    excusal rows have exactly one; the in-place removal from today's stored
    report shares that row with an open editable share, which replays base + its
    own log and would resurrect the signal on commit.
    """

    def test_the_excusal_lands_and_the_stored_report_does_not_move(self, store):
        run_id = store.record_run(_report(_signal()))
        before = store.get_run_by_id(run_id)

        assert practice_feedback.apply_verdict(
            store,
            session_id="s1",
            member="Ada",
            rule="untracked-work",
            verdict="down",
            run_id=run_id,
            rewrite_report=False,
        )

        assert store.get_run_by_id(run_id) == before, "today's report must be untouched"
        assert ("untracked-work", "url:https://x/pull/42") in practice_feedback.load(store, "s1").excused

    def test_the_signal_is_gone_from_the_next_run(self, store):
        # Which is the whole reason a deferral is not a lost vote: the excusal
        # is permanent, so tomorrow's report never builds the signal at all.
        run_id = store.record_run(_report(_signal()))
        practice_feedback.apply_verdict(
            store,
            session_id="s1",
            member="Ada",
            rule="untracked-work",
            verdict="down",
            run_id=run_id,
            rewrite_report=False,
        )
        assert practice_feedback.load(store, "s1").is_excused("untracked-work", "url:https://x/pull/42")

    def test_a_thumbs_up_is_unaffected_by_the_flag(self, store):
        # It never rewrote the report in the first place.
        run_id = store.record_run(_report(_signal()))
        before = store.get_run_by_id(run_id)
        for flag in (True, False):
            assert practice_feedback.apply_verdict(
                store,
                session_id="s1",
                member="Ada",
                rule="untracked-work",
                verdict="up",
                run_id=run_id,
                rewrite_report=flag,
            )
        assert store.get_run_by_id(run_id) == before

    def test_the_default_still_rewrites(self, store):
        run_id = store.record_run(_report(_signal()))
        practice_feedback.apply_verdict(
            store, session_id="s1", member="Ada", rule="untracked-work", verdict="down", run_id=run_id
        )
        assert store.get_run_by_id(run_id).member_updates[0].practices == ()
