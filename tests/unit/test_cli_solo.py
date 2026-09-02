"""The top-level ``--solo`` flag: off by default, forwarded to the headless planner."""

from __future__ import annotations

import ast
import inspect

from yeaboi import cli
from yeaboi.cli import _run_headless, build_parser


class TestSoloFlag:
    def test_off_by_default(self):
        assert build_parser().parse_args([]).solo is False

    def test_headless_forwards_solo_to_run_repl(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr("yeaboi.repl.run_repl", lambda **kw: captured.update(kw))
        args = build_parser().parse_args(["--non-interactive", "--description", "A todo app", "--solo"])
        _run_headless(args)
        assert captured["solo"] is True

    def test_headless_defaults_to_a_team_run(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr("yeaboi.repl.run_repl", lambda **kw: captured.update(kw))
        _run_headless(build_parser().parse_args(["--non-interactive", "--description", "A todo app"]))
        assert captured["solo"] is False

    def test_every_fresh_planning_launch_forwards_solo(self):
        # --quick / --questionnaire / --full-intake reach run_repl through main();
        # a resume restores the flag from the saved state instead.
        tree = ast.parse(inspect.getsource(cli))
        launches = [
            node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "run_repl"
        ]
        assert len(launches) >= 3
        for call in launches:
            keywords = {kw.arg for kw in call.keywords}
            if "resume_state" in keywords:
                continue
            assert "solo" in keywords, f"run_repl at line {call.lineno} does not forward --solo"
