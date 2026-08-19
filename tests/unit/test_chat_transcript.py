"""Tests for the chat transcript model — caching, roles, artifact cards."""

from io import StringIO

from rich.console import Console
from rich.text import Text

from yeaboi.ui.session.chat._transcript import ChatMessage, ChatTranscript
from yeaboi.ui.shared._components import PLANNING_THEME


def _console(width: int = 100) -> Console:
    return Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")


def _plain(lines: list[Text]) -> list[str]:
    return [line.plain for line in lines]


class TestBubbles:
    def test_roles_render_with_labels(self):
        t = ChatTranscript()
        t.add_user("hello")
        t.add_assistant("hi there")
        t.add_system("exported")
        text = "\n".join(_plain(t.lines(80, {}, _console(), theme=PLANNING_THEME)))
        assert "you ▐" in text
        assert "hello" in text
        assert "▌ yeaboi" in text
        assert "· exported" in text

    def test_user_bubble_right_aligned_and_tinted(self):
        t = ChatTranscript()
        t.add_user("hi")
        lines = t.lines(80, {}, _console(), theme=PLANNING_THEME)
        label, body = lines[0], lines[1]
        # Right-aligned: the label ends at the column edge, the body block too.
        assert label.plain.endswith("you ▐")
        assert len(label.plain) == 80
        assert body.plain.rstrip().endswith("hi") or "hi" in body.plain
        assert body.plain.startswith(" " * 40)  # pushed right, not at the gutter
        # Tinted: the bubble carries the theme's card background.
        assert any(PLANNING_THEME.card_bg in str(span.style) for span in body.spans)

    def test_system_lines_centered(self):
        t = ChatTranscript()
        t.add_system("exported")
        (line, _blank) = t.lines(80, {}, _console(), theme=PLANNING_THEME)[:2]
        left_pad = len(line.plain) - len(line.plain.lstrip())
        assert left_pad > 20  # centered, not at the gutter

    def test_long_text_word_wraps(self):
        t = ChatTranscript()
        t.add_assistant("word " * 50)
        lines = t.lines(60, {}, _console(60), theme=PLANNING_THEME)
        assert len(lines) > 3  # wrapped, not truncated
        assert all(len(line.plain) <= 60 for line in lines)

    def test_streaming_tail_has_cursor(self):
        t = ChatTranscript()
        text = "\n".join(_plain(t.lines(80, {}, _console(), theme=PLANNING_THEME, stream_text="partial rep")))
        assert "partial rep ▌" in text


class TestCaching:
    def test_cache_hit_at_same_width(self):
        t = ChatTranscript()
        t.add_user("hello")
        first = t.lines(80, {}, _console(), theme=PLANNING_THEME)
        again = t.lines(80, {}, _console(), theme=PLANNING_THEME)
        assert first[0] is again[0]  # same cached Text objects

    def test_width_change_invalidates(self):
        t = ChatTranscript()
        t.add_user("word " * 30)
        wide = t.lines(120, {}, _console(120), theme=PLANNING_THEME)
        narrow = t.lines(60, {}, _console(60), theme=PLANNING_THEME)
        assert len(narrow) > len(wide)

    def test_invalidate_artifacts_only_touches_artifacts(self):
        t = ChatTranscript()
        t.add_user("hello")
        t.add_artifact("analysis")
        t.lines(80, {}, _console(), theme=PLANNING_THEME)
        t.invalidate_artifacts()
        user_msg, artifact_msg = t.messages
        assert user_msg._cache is not None
        assert artifact_msg._cache is None

    def test_invalidate_one_artifact_leaves_the_others_cached(self):
        # The carousel invalidates only the prior-art card per keypress —
        # dropping every card's cache would re-render five panels per arrow.
        t = ChatTranscript()
        t.add_artifact("analysis")
        t.add_artifact("prior_art")
        t.lines(80, {}, _console(), theme=PLANNING_THEME)
        t.invalidate_artifact("prior_art")
        analysis_msg, prior_art_msg = t.messages
        assert analysis_msg._cache is not None
        assert prior_art_msg._cache is None

    def test_invalidate_a_kind_that_matches_nothing_is_a_no_op(self):
        t = ChatTranscript()
        t.add_artifact("analysis")
        t.lines(80, {}, _console(), theme=PLANNING_THEME)
        t.invalidate_artifact("prior_art")
        assert t.messages[0]._cache is not None


class TestArtifacts:
    def test_unavailable_artifact_shows_placeholder(self):
        t = ChatTranscript()
        t.add_artifact("sprints")
        text = "\n".join(_plain(t.lines(80, {}, _console(), theme=PLANNING_THEME)))
        assert "Sprint plan unavailable" in text

    def test_regenerated_artifact_replaces_card(self):
        t = ChatTranscript()
        t.add_artifact("analysis")
        t.add_user("edit it")
        t.add_artifact("analysis")
        kinds = [(m.role, m.artifact_kind) for m in t.messages]
        assert kinds == [("user", ""), ("artifact", "analysis")]

    def test_intake_summary_renders_from_state(self):
        from yeaboi.agent.state import QuestionnaireState

        qs = QuestionnaireState()
        qs.answers = {2: "Greenfield", 6: "4 engineers"}
        t = ChatTranscript()
        t.add_artifact("intake_summary")
        text = "\n".join(_plain(t.lines(100, {"questionnaire": qs}, _console(), theme=PLANNING_THEME)))
        assert "Your answers" in text
        assert "Greenfield" in text

    def test_artifact_renders_as_boxed_card(self):
        from yeaboi.agent.state import QuestionnaireState

        qs = QuestionnaireState()
        qs.answers = {2: "Greenfield"}
        t = ChatTranscript()
        t.add_artifact("intake_summary")
        text = "\n".join(_plain(t.lines(100, {"questionnaire": qs}, _console(), theme=PLANNING_THEME)))
        assert "╭" in text and "╰" in text  # a real rounded Panel, not rule lines


class TestRecapCard:
    def _complete_state(self) -> dict:
        from types import SimpleNamespace

        return {
            "features": ["f1", "f2"],
            "stories": [SimpleNamespace(story_points=5), SimpleNamespace(story_points=3)],
            "tasks": ["t1", "t2", "t3"],
            "sprints": ["s1", "s2"],
        }

    def test_recap_counts_points_and_next_steps(self):
        transcript = ChatTranscript()
        transcript.add_artifact("recap")
        lines = transcript.lines(90, self._complete_state(), _console(), theme=PLANNING_THEME)
        text = "\n".join(_plain(lines))
        assert "Plan complete" in text  # the card title
        assert "2 epics" in text and "2 stories" in text
        assert "3 tasks" in text and "2 sprints" in text
        assert "8 pts total" in text
        assert "/export" in text and "Esc Esc" in text

    def test_recap_without_sprints_shows_placeholder(self):
        transcript = ChatTranscript()
        transcript.add_artifact("recap")
        lines = transcript.lines(90, {}, _console(), theme=PLANNING_THEME)
        assert lines  # unavailable-data placeholder path, no crash


class TestChatMessage:
    def test_invalidate_clears_cache(self):
        m = ChatMessage("user", "hi")
        m._cache = (80, [Text("x")])
        m.invalidate()
        assert m._cache is None
