"""`yeaboi app` — the CLI face of the desktop backend."""

from __future__ import annotations

import argparse


class TestParser:
    def test_app_subcommand_parses_with_defaults(self):
        from yeaboi.cli import build_parser

        args = build_parser().parse_args(["app"])
        assert args.command == "app"
        assert args.port == 0

    def test_port_flag(self):
        from yeaboi.cli import build_parser

        args = build_parser().parse_args(["app", "--port", "5599"])
        assert args.port == 5599


class TestDispatch:
    def test_cmd_app_attaches_the_app_log_and_runs(self, monkeypatch):
        from yeaboi import cli

        calls: dict[str, object] = {}
        monkeypatch.setattr("yeaboi.logging_setup.attach_mode_handler", lambda mode: calls.setdefault("log", mode))
        monkeypatch.setattr("yeaboi.app.run.run_app", lambda port: (calls.setdefault("port", port), 0)[1])
        rc = cli._cmd_app(argparse.Namespace(port=1234), console=None)
        assert rc == 0
        assert calls == {"log": "app", "port": 1234}

    def test_app_is_a_registered_subcommand_handler(self, monkeypatch):
        """_run_subcommand routes `yeaboi app` to _cmd_app."""
        from yeaboi import cli

        monkeypatch.setattr(cli, "_cmd_app", lambda args, console: 7)
        rc = cli._run_subcommand(argparse.Namespace(command="app", port=0))
        assert rc == 7
