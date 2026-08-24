"""The `_duck_mascot` panel stamps that steer the chrome's mascot family.

MusicLive.get_renderable reads this attribute off every frame's renderable and
draws the corner companion / entrance / screensaver in that family — Agents
surfaces must stamp "robo", everything else must stay unstamped (duck).
"""

from yeaboi.ui.mode_select.screens._screens import _build_mode_screen
from yeaboi.ui.mode_select.screens._screens_agents import (
    _build_agent_security_screen,
    _build_agent_standup_screen,
    _build_agent_usage_screen,
)
from yeaboi.ui.shared._beta_notice import _build_beta_notice_screen


class TestAgentsPagesStamp:
    def test_all_three_builders_stamp_robo_in_both_states(self):
        from tests.unit.test_agentwatch_export import make_report

        for builder, artifact in (
            (_build_agent_usage_screen, make_report()),
            (_build_agent_standup_screen, None),
            (_build_agent_security_screen, None),
        ):
            running = builder(None, width=100, height=40, shimmer_tick=0.1, status="working")
            assert getattr(running, "_duck_mascot", "") == "robo"
            if artifact is not None:
                done = builder(artifact, width=100, height=40, shimmer_tick=None)
                assert getattr(done, "_duck_mascot", "") == "robo"


class TestBetaNoticeStamp:
    def test_agent_gates_stamp_robo(self):
        panel = _build_beta_notice_screen(mode_key="agent-usage", width=100, height=40)
        assert getattr(panel, "_duck_mascot", "") == "robo"

    def test_performance_gate_stays_duck(self):
        panel = _build_beta_notice_screen(mode_key="performance", width=100, height=40)
        assert not hasattr(panel, "_duck_mascot")


class TestMenuStamp:
    def test_menu_carries_its_mascot_on_both_shapes(self):
        # Wide → _WelcomeFrame; narrow → bare Panel. Both must carry the stamp
        # (the screensaver global is fed from it even though the menu draws its
        # own in-panel companion).
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS

        wide = _build_mode_screen(0, width=120, height=40, cards=_AGENT_CARDS, mascot="robo")
        assert getattr(wide, "_duck_mascot", "") == "robo"
        narrow = _build_mode_screen(0, width=90, height=40, cards=_AGENT_CARDS, mascot="robo")
        assert getattr(narrow, "_duck_mascot", "") == "robo"
        humans = _build_mode_screen(0, width=120, height=40)
        assert getattr(humans, "_duck_mascot", "") == "duck"


class TestAgentsWorkingBob:
    def test_engine_worker_bobs_the_companion(self):
        # The Agents pages' worker thread must ride duck_working_thread so the
        # corner robo bobs for the engine's lifetime (same cue as Humans pages).
        import threading

        from yeaboi.ui.shared import _music_bar

        _music_bar._reset_duck_state()
        seen = {}
        release = threading.Event()

        def _work():
            seen["working"] = _music_bar._duck_working
            release.wait(timeout=5)

        worker = _music_bar.duck_working_thread(_work, name="agents-test")
        worker.start()
        for _ in range(100):
            if "working" in seen:
                break
            threading.Event().wait(0.01)
        release.set()
        worker.join(timeout=5)
        assert seen.get("working") is True
        assert _music_bar._duck_working is False  # settled after the run

    def test_agents_page_loop_uses_duck_working_thread(self):
        # Structural pin: the shared page loop builds its worker via the
        # bobbing wrapper, not a bare Thread.
        import inspect

        from yeaboi.ui.mode_select import _agents

        source = inspect.getsource(_agents._run_agent_page)
        assert "duck_working_thread(" in source
        assert "threading.Thread(" not in source
