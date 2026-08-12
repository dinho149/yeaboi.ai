"""The chat → card-pipeline hand-off in ui/session/__init__.py.

Lives in tests/unit/ rather than beside the rest of the session tests in
tests/test_session.py because that file sits at the tests/ root, which no gate
runs: `make test` and `make test-fast` both enumerate tests/unit,
tests/integration and tests/contract explicitly. A hand-off test that never
executes is worse than none — it reads as covered.
"""

from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console


def _make_console(width: int = 100, height: int = 30) -> Console:
    c = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
    c._size = (width, height)
    return c


class TestIntakeComplete:
    """_intake_complete is the single predicate behind two decisions — whether
    to open the chat at all, and whether it handed off or the user walked away.
    Both read it, so every branch is exercised here on its own."""

    def _qs(self, *, completed: bool, awaiting: bool = False):
        from yeaboi.agent.state import QuestionnaireState

        qs = QuestionnaireState(intake_mode="small_project")
        qs.completed = completed
        qs.awaiting_confirmation = awaiting
        return qs

    def test_no_questionnaire_is_not_complete(self):
        from yeaboi.ui.session import _intake_complete

        assert _intake_complete({}) is False
        assert _intake_complete({"questionnaire": None}) is False

    def test_non_questionnaire_value_is_not_complete(self):
        # Persisted state can round-trip a dict where a QuestionnaireState is
        # expected; the isinstance guard must not be an AttributeError.
        from yeaboi.ui.session import _intake_complete

        assert _intake_complete({"questionnaire": {"completed": True}}) is False

    def test_mid_questionnaire_is_not_complete(self):
        from yeaboi.ui.session import _intake_complete

        assert _intake_complete({"questionnaire": self._qs(completed=False)}) is False

    def test_awaiting_the_summary_is_not_complete(self):
        from yeaboi.ui.session import _intake_complete

        state = {
            "questionnaire": self._qs(completed=False, awaiting=True),
            "pending_review": "project_intake",
        }
        assert _intake_complete(state) is False

    def test_completed_and_accepted_is_complete(self):
        from yeaboi.ui.session import _intake_complete

        assert _intake_complete({"questionnaire": self._qs(completed=True)}) is True

    def test_completed_but_gate_still_open_is_not_complete(self):
        # The second conjunct on its own: answers locked in, yet the intake
        # review gate is still posted. Accepting the summary must clear it
        # (the chat driver pops it before the confirm invoke), so this state
        # means the gate was never closed — not that intake is done.
        from yeaboi.ui.session import _intake_complete

        state = {"questionnaire": self._qs(completed=True), "pending_review": "project_intake"}
        assert _intake_complete(state) is False

    def test_a_later_pipeline_gate_does_not_reopen_intake(self):
        # Only the intake gate counts — a resumed session paused at a story
        # review is past intake and belongs to the card pipeline.
        from yeaboi.ui.session import _intake_complete

        state = {"questionnaire": self._qs(completed=True), "pending_review": "story_writer"}
        assert _intake_complete(state) is True


class TestChatToPipelineHandoff:
    """The chat runs the questionnaire; the card pipeline runs everything after."""

    def _completed_state(self) -> dict:
        from yeaboi.agent.state import QuestionnaireState

        qs = QuestionnaireState(intake_mode="small_project")
        qs.completed = True
        return {"messages": [], "questionnaire": qs, "_intake_mode": "small_project"}

    def _run_body(self, monkeypatch, *, chat_result, resume_state=None):
        """Run _run_session_body with the chat and every phase stubbed out."""
        import yeaboi.ui.session as session_mod

        calls: list[str] = []
        monkeypatch.setattr(session_mod, "create_graph", lambda *a, **k: MagicMock())
        monkeypatch.setattr(session_mod, "save_project_snapshot", lambda *a, **k: None)

        def fake_chat(*_a, **_kw):
            calls.append("chat")
            return chat_result

        monkeypatch.setattr("yeaboi.ui.session.chat.run_chat_session", fake_chat)

        def fake_description(*_a, **_kw):
            calls.append("description")
            return None

        def fake_pipeline(_live, _console, _graph, graph_state, *_a, **_kw):
            calls.append("pipeline")
            return graph_state

        def fake_intake_questions(_live, _console, _graph, graph_state, *_a, **_kw):
            calls.append("intake_questions")
            return graph_state

        monkeypatch.setattr(session_mod, "_phase_description_input", fake_description)
        monkeypatch.setattr(session_mod, "_phase_pipeline", fake_pipeline)
        monkeypatch.setattr(session_mod, "_phase_intake_questions", fake_intake_questions)

        session_mod._run_session_body(
            MagicMock(),
            _make_console(),
            "chat",
            "",  # project_id — empty so nothing persists
            lambda: "",
            lambda *_a, **_kw: "",
            questionnaire=None,
            resume_graph_state=resume_state,
            export_only=False,
            bell=False,
            dry_run=False,
        )
        return calls

    def test_completed_intake_hands_off_to_the_card_pipeline(self, monkeypatch):
        calls = self._run_body(monkeypatch, chat_result=self._completed_state())
        # The chat collected the description, so the old description editor
        # must stay skipped — and the pipeline must actually run.
        assert calls == ["chat", "pipeline"]

    def test_leaving_mid_questionnaire_returns_to_the_dashboard(self, monkeypatch):
        from yeaboi.agent.state import QuestionnaireState

        qs = QuestionnaireState(intake_mode="small_project")  # not completed
        state = {"messages": [], "questionnaire": qs}
        calls = self._run_body(monkeypatch, chat_result=state)
        assert calls == ["chat"]

    def test_summary_not_yet_accepted_is_not_complete(self, monkeypatch):
        state = self._completed_state()
        state["pending_review"] = "project_intake"
        state["questionnaire"].completed = False
        calls = self._run_body(monkeypatch, chat_result=state)
        assert calls == ["chat"]

    def test_resume_past_intake_skips_the_chat(self, monkeypatch):
        calls = self._run_body(monkeypatch, chat_result=None, resume_state=self._completed_state())
        assert calls == ["pipeline"]

    def test_quit_during_the_greeting_returns_none(self, monkeypatch):
        calls = self._run_body(monkeypatch, chat_result=None)
        assert calls == ["chat"]
