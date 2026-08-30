"""Unit tests for the retro action-items engine (mocked LLM)."""

import json

from yeaboi.agent.state import RetroCard, RetroReport
from yeaboi.retro import engine
from yeaboi.retro.board import RetroBoard
from yeaboi.retro.store import RetroStore


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.response_metadata = {}


def _fake_llm(content):
    return lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(content)})()


class TestParse:
    def test_plain_json(self):
        assert engine._parse_action_items('{"action_items": ["a", "b"]}') == ["a", "b"]

    def test_markdown_fenced(self):
        raw = '```json\n{"action_items": ["x"]}\n```'
        assert engine._parse_action_items(raw) == ["x"]

    def test_bare_list(self):
        assert engine._parse_action_items('["only", "list"]') == ["only", "list"]

    def test_garbage_returns_empty(self):
        assert engine._parse_action_items("not json at all") == []

    def test_strips_blank_items(self):
        assert engine._parse_action_items('{"action_items": ["a", "  ", ""]}') == ["a"]


class TestFallback:
    def test_builds_address_items(self):
        items = engine._build_fallback_action_items(["flaky tests", "slow CI", ""])
        assert items == ["Address: flaky tests", "Address: slow CI"]


class TestGenerateActionItems:
    def _seed(self):
        b = RetroBoard("s", "Proj")
        b.add_card(grid="didnt_go_well", text="flaky tests", author="Rae")
        b.add_card(grid="went_well", text="fast deploys", author="Sam")
        return b

    def test_empty_board_returns_hint(self):
        b = RetroBoard("s")
        msg = engine.generate_action_items(b)
        assert "Add some cards" in msg
        assert b.total() == 0

    def test_empty_board_with_carried_is_a_true_noop(self):
        # Generate on an empty board must NOT mutate the grid, even with carried items.
        b = RetroBoard("s")
        b.seed_carried([RetroCard(id="k1", grid="action_items", text="keep me")])
        b.set_carried_status("k1", "carried_over")
        msg = engine.generate_action_items(b)
        assert "Add some cards" in msg
        assert b.total() == 0  # nothing re-added to the grid

    def test_happy_path_with_llm(self, monkeypatch):
        b = self._seed()
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
        payload = json.dumps({"action_items": ["Add a CI retry guard", "Split the flaky suite"]})
        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _fake_llm(payload))
        msg = engine.generate_action_items(b)
        assert "Generated 2" in msg
        assert len(b.cards_by_grid()["action_items"]) == 2

    def test_not_configured_uses_fallback(self, monkeypatch):
        b = self._seed()
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "ANTHROPIC_API_KEY not set"))

        def _should_not_call(**k):
            raise AssertionError("get_llm must not be called when unconfigured")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _should_not_call)
        msg = engine.generate_action_items(b)
        assert "unavailable" in msg.lower()
        # Deterministic fallback added the one problem card as an action item.
        assert len(b.cards_by_grid()["action_items"]) == 1

    def test_reaction_counts_annotate_prompt(self, monkeypatch):
        b = self._seed()
        # React to the "flaky tests" problem card.
        problem = b.cards_by_grid()["didnt_go_well"][0]
        b.toggle_reaction(problem.id, "👍", "p1")
        b.toggle_reaction(problem.id, "🔥", "p2")
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)

        captured = {}

        def _fake(**k):
            def _invoke(self, messages):
                captured["prompt"] = messages[0].content
                return _FakeResp('{"action_items": ["x"]}')

            return type("L", (), {"invoke": _invoke})()

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _fake)
        engine.generate_action_items(b)
        assert "[2 reactions]" in captured["prompt"]

    def test_llm_error_falls_back(self, monkeypatch):
        b = self._seed()
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)

        def boom(self, m):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": boom})())
        msg = engine.generate_action_items(b)
        assert "failed" in msg.lower()
        assert len(b.cards_by_grid()["action_items"]) == 1


def _prior_report(session_id="prev", project_name="", date="2026-07-01"):
    return RetroReport(
        session_id=session_id,
        project_name=project_name,
        date=date,
        cards=(
            RetroCard(id="a1", grid="action_items", text="ship the docs", author="AI", origin="ai"),
            RetroCard(id="a2", grid="action_items", text="  ", author="AI"),  # blank
            RetroCard(id="w1", grid="went_well", text="fast deploys", author="Sam"),  # not an action
        ),
    )


class TestCarriedActionItemsForSession:
    def test_carries_from_a_different_prior_session(self, tmp_path):
        # The real-world case: the previous retro ran under a DIFFERENT session, so a
        # same-session lookup would miss it. Cross-session sourcing must find it.
        db = tmp_path / "sessions.db"
        with RetroStore(db) as store:
            store.record_run(_prior_report("prev-session"))
        carried = engine.carried_action_items_for_session("brand-new-session", db_path=db)
        assert [c.text for c in carried] == ["ship the docs"]  # blank + non-action dropped
        assert carried[0].status == "pending" and carried[0].origin == "carryover"

    def test_carries_manually_added_web_action_item(self, tmp_path):
        # A teammate typed an action item straight into the board (origin="web"),
        # never clicking Generate. Carry-forward is origin-agnostic, so it must still
        # carry forward next sprint — this pins that against an "AI-only" refactor.
        db = tmp_path / "sessions.db"
        report = RetroReport(
            session_id="prev",
            date="2026-07-01",
            cards=(RetroCard(id="m1", grid="action_items", text="update the runbook", author="Sam", origin="web"),),
        )
        with RetroStore(db) as store:
            store.record_run(report)
        carried = engine.carried_action_items_for_session("cur", db_path=db)
        assert [c.text for c in carried] == ["update the runbook"]
        assert carried[0].status == "pending" and carried[0].origin == "carryover"

    def test_reopen_same_session_carries_forward(self, tmp_path):
        # Closing a retro and reopening it under the SAME session must carry the
        # just-closed run's action items forward. Regression: the old "skip current
        # session" guard ate the only prior report whenever retros reused the latest
        # quick session, so nothing ever carried.
        db = tmp_path / "sessions.db"
        with RetroStore(db) as store:
            store.record_run(_prior_report("same-session"))
        carried = engine.carried_action_items_for_session("same-session", db_path=db)
        assert [c.text for c in carried] == ["ship the docs"]
        assert carried[0].status == "pending" and carried[0].origin == "carryover"

    def test_project_first_prefers_matching_project(self, tmp_path):
        db = tmp_path / "sessions.db"
        with RetroStore(db) as store:
            # Newer retro is a different project; older one matches "Alpha".
            store.record_run(_prior_report("s-alpha", project_name="Alpha", date="2026-07-01"))
            store.record_run(_prior_report("s-beta", project_name="Beta", date="2026-07-09"))
        carried = engine.carried_action_items_for_session("cur", project_name="Alpha", db_path=db)
        # Project-first ordering surfaces Alpha's retro even though Beta's is newer.
        assert carried and all(c.origin == "carryover" for c in carried)

    def test_no_history_returns_empty(self, tmp_path):
        assert engine.carried_action_items_for_session("nope", db_path=tmp_path / "sessions.db") == ()

    def test_carries_kept_open_items_not_in_grid(self, tmp_path):
        # A prior retro marked an item "Carried Over" in its review column but never
        # re-added it to the grid (no Generate click). It must still carry forward.
        db = tmp_path / "sessions.db"
        report = RetroReport(
            session_id="prev",
            date="2026-07-01",
            cards=(RetroCard(id="g1", grid="action_items", text="grid item", origin="ai"),),
            carried_action_items=(
                RetroCard(id="k1", grid="action_items", text="kept for next sprint", status="carried_over"),
                RetroCard(id="k2", grid="action_items", text="already done", status="done"),
            ),
        )
        with RetroStore(db) as store:
            store.record_run(report)
        texts = [c.text for c in engine.carried_action_items_for_session("cur", db_path=db)]
        assert "grid item" in texts
        assert "kept for next sprint" in texts  # kept-open carried item survives
        assert "already done" not in texts  # resolved item does not

    def test_dedupes_grid_and_kept_open(self, tmp_path):
        db = tmp_path / "sessions.db"
        report = RetroReport(
            session_id="prev",
            cards=(RetroCard(id="g1", grid="action_items", text="Ship API", origin="ai"),),
            carried_action_items=(RetroCard(id="k1", grid="action_items", text="ship api", status="carried_over"),),
        )
        with RetroStore(db) as store:
            store.record_run(report)
        texts = [c.text for c in engine.carried_action_items_for_session("cur", db_path=db)]
        assert texts == ["Ship API"]  # normalised-dedup keeps the grid wording once


class TestCarryForwardInGenerate:
    def _seed_with_carried(self, statuses):
        b = RetroBoard("s", "Proj")
        b.add_card(grid="didnt_go_well", text="flaky tests", author="Rae")
        cards = [
            RetroCard(id=f"k{i}", grid="action_items", text=text, origin="carryover", status=status)
            for i, (text, status) in enumerate(statuses)
        ]
        b.seed_carried(cards)
        # seed_carried resets status to pending; re-apply the intended statuses.
        for c in cards:
            b.set_carried_status(c.id, c.status)
        return b

    def _run(self, b, monkeypatch, captured):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)

        def _fake(**k):
            def _invoke(self, messages):
                captured["prompt"] = messages[0].content
                return _FakeResp('{"action_items": ["fresh action"]}')

            return type("L", (), {"invoke": _invoke})()

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", _fake)
        return engine.generate_action_items(b)

    def test_carried_over_item_reentered_and_done_excluded(self, monkeypatch):
        b = self._seed_with_carried(
            [("keep going", "carried_over"), ("already shipped", "done"), ("skip it", "not_relevant")]
        )
        captured: dict = {}
        self._run(b, monkeypatch, captured)
        action_texts = [c.text for c in b.cards_by_grid()["action_items"]]
        # carried_over re-entered as a card; done/not_relevant NOT re-added.
        assert "keep going" in action_texts
        assert "already shipped" not in action_texts and "skip it" not in action_texts
        carry = [c for c in b.cards_by_grid()["action_items"] if c.origin == "carryover"]
        assert [c.text for c in carry] == ["keep going"]

    def test_still_open_items_reach_prompt(self, monkeypatch):
        b = self._seed_with_carried([("in progress work", "in_progress"), ("done thing", "done")])
        captured: dict = {}
        self._run(b, monkeypatch, captured)
        # Open item is handed to the LLM as STILL_OPEN context; the resolved one isn't.
        assert "in progress work" in captured["prompt"]
        assert "done thing" not in captured["prompt"]


class TestCarriedActionItemsScope:
    def test_scope_filters_to_the_projects_sessions(self, tmp_path):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        with RetroStore(db) as store:
            store.record_run(_prior_report("proj-sess", date="2026-07-01"))
            # Newer, but belongs to another project's session — must not win.
            store.record_run(
                RetroReport(
                    session_id="other-sess",
                    date="2026-07-09",
                    cards=(RetroCard(id="o1", grid="action_items", text="other project's item", author="AI"),),
                )
            )
        scope = ProjectScope("proj-11112222", ("proj-sess",))
        carried = engine.carried_action_items_for_session("cur", db_path=db, scope=scope)
        assert [c.text for c in carried] == ["ship the docs"]

    def test_empty_scope_carries_nothing(self, tmp_path):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        with RetroStore(db) as store:
            store.record_run(_prior_report("prev"))
        scope = ProjectScope("proj-11112222", ())
        assert engine.carried_action_items_for_session("cur", db_path=db, scope=scope) == ()
