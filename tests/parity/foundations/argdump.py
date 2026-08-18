"""Python-side argv dumper for the W8 CLI parse gate.

Runs every ``argvectors.VECTORS`` argv through ``cli.build_parser()`` and
records the outcome:

- a successful parse → ``{"status": "ok", "args": vars(namespace)}``
- an argparse error → ``{"status": "error", "exit": 2, "prog": ...,
  "message": ...}`` — the parser that errored and the text argparse printed
  after ``"prog: error: "`` (the usage block above it is phase 4's help
  goldens, not this gate's)
- a clean exit (help/version — kept out of the corpus by a self-guard) →
  ``{"status": "exit", "code": ...}``

``yeaboi __dump-args ARGS...`` is the Go twin, printing the same document
for one argv. Unlike the foundations dump this needs no subprocess per
vector: build_parser() reads nothing from the environment, and argparse
writes its error through ``sys.stderr`` at call time, so an in-process
redirect captures it.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path

from tests.parity.foundations.argvectors import VECTORS

GOLDEN_PATH = Path(__file__).parent.parent / "goldens" / "cli" / "args.json"

_ERROR_LINE = re.compile(r"^(?P<prog>.+?): error: (?P<message>.*)$")


def _outcome(argv: list[str]) -> dict:
    """One vector's outcome, from a fresh parser."""
    from yeaboi.cli import build_parser

    parser = build_parser()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            namespace = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code == 0:
            return {"status": "exit", "code": 0}
        lines = [line for line in stderr.getvalue().splitlines() if line]
        match = _ERROR_LINE.match(lines[-1]) if lines else None
        if match is None:  # pragma: no cover — argparse always prints one
            return {"status": "error", "exit": code, "prog": "", "message": stderr.getvalue()}
        return {
            "status": "error",
            "exit": code,
            "prog": match.group("prog"),
            "message": match.group("message"),
        }
    return {"status": "ok", "args": vars(namespace)}


def build_results() -> list[dict]:
    """Every vector with its outcome, in corpus order."""
    return [{"name": name, "argv": list(argv), "result": _outcome(argv)} for name, argv in VECTORS]


def render_golden() -> str:
    return json.dumps({"vectors": build_results()}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render_golden())
