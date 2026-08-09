"""Pty integration tests for scripts/record_demo.py — the demo-GIF recorder.

Runs the real record() pipeline (pty spawn, marker sync, cast writing) against
a tiny stub child instead of the TUI, so it stays hermetic and ~2s. The pure
CastWriter/verify/render tests live in tests/unit/test_record_demo.py; the real
TUI-in-a-pty path is covered by test_tui_smoke.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="pty/termios are POSIX-only (the TUI itself is too)")

pytestmark = pytest.mark.slow

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "record_demo.py"
_spec = importlib.util.spec_from_file_location("record_demo", _MODULE_PATH)
record_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record_demo)


def _read_cast(path: Path) -> tuple[dict, list]:
    lines = path.read_text().splitlines()
    return json.loads(lines[0]), [json.loads(line) for line in lines[1:] if line]


class TestRecordPipeline:
    """End-to-end pty -> script -> cast, against a stub child instead of the TUI."""

    def test_records_stub_tui_to_cast(self, tmp_path):
        stub = textwrap.dedent(
            """
            import os, sys, time, tty
            tty.setcbreak(0)  # raw single keys, like the real TUI — a bare "q" never clears cooked mode
            sys.stdout.write("\\x1b[?1049h")
            sys.stdout.flush()
            for _ in range(40):
                sys.stdout.write("\\x1b[2J v2.59 c changelog  Tip: duck \\U0001f986  channel ")
                sys.stdout.flush()
                time.sleep(0.02)
            while True:
                if os.read(0, 1) == b"q":
                    sys.stdout.write("\\x1b[?1049l")
                    sys.stdout.flush()
                    sys.exit(0)
            """
        )
        script = [
            ("await", record_demo.MODE_SCREEN_MARKERS, 10.0),
            ("pause", 0.2),
            ("key", record_demo.KEY_DOWN),
            ("pause", 0.2),
            ("key", b"q"),
        ]
        cast_path = tmp_path / "stub.cast"
        record_demo.record(cast_path, cmd=[sys.executable, "-u", "-c", stub], script=script)
        header, events = _read_cast(cast_path)
        assert header["version"] == 2
        joined = "".join(e[2] for e in events)
        assert "\x1b[?1049h" in joined
        assert "changelog" in joined
        assert "�" not in joined  # multibyte duck survived chunking
        assert record_demo._ALT_SCREEN_EXIT not in joined  # cast ends before the quit teardown
        assert events[0][0] == pytest.approx(record_demo.CastWriter.LEAD_IN_S, abs=0.05)

    def test_missing_markers_raise(self, tmp_path):
        stub = "import sys; sys.stdout.write('\\x1b[?1049h nothing here'); sys.stdout.flush(); sys.stdin.read(1)"
        script = [("await", ("never-rendered-marker",), 1.0), ("key", b"q")]
        with pytest.raises(RuntimeError, match="never rendered"):
            record_demo.record(tmp_path / "bad.cast", cmd=[sys.executable, "-u", "-c", stub], script=script)
