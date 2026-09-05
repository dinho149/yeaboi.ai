"""Tests for scripts/record_demo.py — the deterministic demo-GIF recorder."""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL", reason="pillow arrives transitively with matplotlib (charts extra)")
from PIL import Image  # noqa: E402

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "record_demo.py"
_spec = importlib.util.spec_from_file_location("record_demo", _MODULE_PATH)
record_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record_demo)


def _read_cast(path: Path) -> tuple[dict, list]:
    lines = path.read_text().splitlines()
    return json.loads(lines[0]), [json.loads(line) for line in lines[1:] if line]


class TestCastWriter:
    def test_header_is_valid_v2(self, tmp_path):
        cast = record_demo.CastWriter(tmp_path / "t.cast")
        cast.close()
        header, events = _read_cast(tmp_path / "t.cast")
        assert header["version"] == 2
        assert (header["width"], header["height"]) == (record_demo.COLS, record_demo.ROWS)
        assert events == []

    def test_events_are_timestamp_o_text(self, tmp_path):
        cast = record_demo.CastWriter(tmp_path / "t.cast")
        cast.feed(b"hello", now=10.0)
        cast.feed(b" world", now=11.0)  # >25ms later -> first buffer flushed
        cast.close()
        _, events = _read_cast(tmp_path / "t.cast")
        assert [type(e[0]) for e in events] == [float, float]
        assert [e[1] for e in events] == ["o", "o"]
        assert "".join(e[2] for e in events) == "hello world"

    def test_timestamps_rebase_to_lead_in(self, tmp_path):
        """Recording starts at ~0.1s regardless of how late the first byte lands."""
        cast = record_demo.CastWriter(tmp_path / "t.cast")
        cast.feed(b"first", now=1234.5)
        cast.close()
        _, events = _read_cast(tmp_path / "t.cast")
        assert events[0][0] == pytest.approx(record_demo.CastWriter.LEAD_IN_S)

    def test_split_multibyte_glyph_decodes_intact(self, tmp_path):
        """The shipped-GIF corruption: a glyph split across two os.read()s must
        never decode into U+FFFD replacement characters."""
        cast = record_demo.CastWriter(tmp_path / "t.cast")
        duck = "🦆".encode()
        cast.feed(duck[:2], now=5.0)
        cast.feed(duck[2:], now=5.0)
        cast.close()
        _, events = _read_cast(tmp_path / "t.cast")
        assert "".join(e[2] for e in events) == "🦆"
        assert "�" not in "".join(e[2] for e in events)

    def test_sub_flush_window_chunks_coalesce_into_one_event(self, tmp_path):
        cast = record_demo.CastWriter(tmp_path / "t.cast")
        for i in range(10):
            cast.feed(f"c{i}".encode(), now=5.0 + i * 0.001)  # all within 25ms
        cast.close()
        _, events = _read_cast(tmp_path / "t.cast")
        assert len(events) == 1
        assert events[0][2] == "".join(f"c{i}" for i in range(10))

    def test_gz_suffix_writes_gzip(self, tmp_path):
        cast = record_demo.CastWriter(tmp_path / "t.cast.gz")
        cast.feed(b"data", now=1.0)
        cast.close()
        header = json.loads(gzip.decompress((tmp_path / "t.cast.gz").read_bytes()).splitlines()[0])
        assert header["version"] == 2

    def test_alt_screen_watch_survives_chunk_split(self, tmp_path):
        cast = record_demo.CastWriter(tmp_path / "t.cast")
        seq = b"\x1b[?1049h"
        cast.feed(seq[:3], now=1.0)
        assert not cast.saw_alt_screen
        cast.feed(seq[3:], now=1.0)
        assert cast.saw_alt_screen
        cast.close()

    def test_recording_ends_at_alt_screen_exit(self, tmp_path):
        """Post-quit terminal output must not become the held final GIF frame."""
        cast = record_demo.CastWriter(tmp_path / "t.cast")
        cast.feed(b"app content", now=1.0)
        cast.feed(b"last frame\x1b[?1049lbare terminal junk", now=2.0)
        cast.feed(b"more junk after quit", now=3.0)
        cast.close()
        _, events = _read_cast(tmp_path / "t.cast")
        joined = "".join(e[2] for e in events)
        assert joined == "app contentlast frame"

    def test_alt_screen_exit_detected_across_chunk_split(self, tmp_path):
        cast = record_demo.CastWriter(tmp_path / "t.cast")
        seq = record_demo._ALT_SCREEN_EXIT.encode()
        cast.feed(b"visible" + seq[:4], now=1.0)
        cast.feed(seq[4:] + b"junk", now=2.0)
        cast.close()
        _, events = _read_cast(tmp_path / "t.cast")
        joined = "".join(e[2] for e in events)
        assert "junk" not in joined
        assert joined.startswith("visible")


class TestDemoScript:
    """Shape guard: future edits must not bloat, empty, or de-risk the demo."""

    def test_only_proven_safe_keys(self):
        allowed = {
            record_demo.KEY_UP,
            record_demo.KEY_DOWN,
            record_demo.KEY_RIGHT,
            record_demo.KEY_LEFT,
            record_demo.KEY_ENTER,
            record_demo.KEY_ESC,
            b"q",
        }
        keys = [step[1] for step in record_demo.DEMO_SCRIPT if step[0] == "key"]
        assert keys, "demo script sends no keys at all"
        assert set(keys) <= allowed

    def test_no_key_immediately_after_escape(self):
        """A bare \\x1b is only Escape if nothing follows within 100ms.

        read_key disambiguates Escape from an arrow-key prefix on a 100ms
        window (src/yeaboi/ui/shared/_input.py). A key step written straight
        after KEY_ESC lands inside that window and gets eaten as an escape
        sequence, so every step following an esc must be an await or a pause.
        """
        steps = record_demo.DEMO_SCRIPT
        for i, step in enumerate(steps[:-1]):
            if step == ("key", record_demo.KEY_ESC):
                assert steps[i + 1][0] != "key", f"step {i + 1} sends a key straight after esc: {steps[i + 1]!r}"

    def test_screen_changes_are_awaited_not_slept(self):
        """Entering a menu or backing out must sync on markers, never a pause.

        A pause long enough for a slow machine to build the menu is a pause the
        fast path also sits through; markers make the take both quick and
        machine-independent.
        """
        steps = record_demo.DEMO_SCRIPT
        transitions = {record_demo.KEY_ENTER, record_demo.KEY_ESC}
        for i, step in enumerate(steps[:-1]):
            if step[0] == "key" and step[1] in transitions:
                assert steps[i + 1][0] == "await", f"step {i} changes screen but step {i + 1} is {steps[i + 1]!r}"

    def test_tours_both_families(self):
        """The demo must show the split, the Humans menu, and the Agents menu.

        The whole point of the re-record: a take that never leaves one family
        sells half the product.
        """
        awaited = [step[1] for step in record_demo.DEMO_SCRIPT if step[0] == "await"]
        assert awaited.count(record_demo.CATEGORY_SCREEN_MARKERS) >= 2, "never returns to the landing split"
        assert awaited.count(record_demo.MODE_SCREEN_MARKERS) >= 2, "only one of the two mode menus is shown"

    def test_marker_sets_are_disjoint(self):
        """Each await must be able to tell the three screens apart.

        If a fragment appeared on two screens, an await would match the screen
        it was leaving and the recording would race ahead of the transition.
        """
        sets = (record_demo.CATEGORY_SCREEN_MARKERS, record_demo.DOOR_SCREEN_MARKERS, record_demo.MODE_SCREEN_MARKERS)
        for i, a in enumerate(sets):
            for b in sets[i + 1 :]:
                assert not set(a) & set(b)

    def test_tours_the_door_both_ways(self):
        awaited = [step[1] for step in record_demo.DEMO_SCRIPT if step[0] == "await"]
        assert awaited.count(record_demo.DOOR_SCREEN_MARKERS) >= 3, "the door is not shown on the way in and out"

    def test_markers_are_disjoint_on_the_rendered_screens(self):
        """The real guard: neither set may appear on the other's screen.

        Comparing the two literal tuples is not enough, and once wasn't — the
        mode menu's rotating tip bar carries tips containing the words "switch"
        and "choose", so a category marker set that included them matched the
        menu it was leaving. Every tip offset is rendered here because only some
        of them collide.
        """
        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens import _build_mode_screen
        from yeaboi.ui.mode_select.screens._screens_category import _build_category_screen
        from yeaboi.ui.mode_select.screens._screens_door import _build_door_screen

        w, h = 140, 40

        def plain(renderable) -> str:
            # Same pinned truecolor console the other screen tests use, then the
            # recorder's own ANSI stripper — matching exactly what the predicate
            # in record() sees.
            console = Console(width=w, height=h, force_terminal=True, color_system="truecolor")
            with console.capture() as cap:
                console.print(renderable)
            return record_demo._strip_ansi(cap.get().encode("utf-8"))

        category = plain(_build_category_screen(0, width=w, height=h))
        for marker in record_demo.MODE_SCREEN_MARKERS + record_demo.DOOR_SCREEN_MARKERS:
            assert marker not in category, f"marker {marker!r} renders on the landing split"

        # With the front page printed too: the recorder runs with news off, so
        # only the fixed strip copy and a release note can ever be on screen.
        from yeaboi.news.edition import Page
        from yeaboi.news.parse import NewsItem

        story = Page(
            item=NewsItem(id="r", title="yeaboi 4.2.0: Faster standups", url="https://pypi.org/p/", kind="release"),
            kicker="From yeaboi",
            counter="1 of 12",
            byline="yeaboi, yesterday",
            read="Read the release notes",
            persona="wizard",
            scene="dock",
            caption="The wizard, at the dock, with the crates.",
        )
        for tall in (h, 60):  # the folded strip, and the sheet
            printed = plain(
                _build_category_screen(
                    0,
                    width=w,
                    height=tall,
                    page=story,
                    edition="Refreshing.",
                    inside="Inside this edition, 11 more stories",
                    masthead="yeaboi · Saturday, 5 September 2026 · Vol. 4, No. 2",
                    colophon="Read from 14 outlets.",
                )
            )
            for marker in record_demo.MODE_SCREEN_MARKERS + record_demo.DOOR_SCREEN_MARKERS:
                assert marker not in printed, f"marker {marker!r} renders on the landing split's front page"

        for world in ("solo", "team", "agents"):
            door = plain(_build_door_screen(1, world=world, width=w, height=h, active_name="Apollo"))
            for marker in record_demo.MODE_SCREEN_MARKERS + record_demo.CATEGORY_SCREEN_MARKERS:
                assert marker not in door, f"marker {marker!r} renders on the door ({world})"

        # Tips rotate, so one screen is not one string — sweep the offsets.
        for tip_index in range(24):
            menu = plain(_build_mode_screen(0, width=w, height=h, tip_offset=tip_index, scope="Session · one-off"))
            for marker in record_demo.CATEGORY_SCREEN_MARKERS + record_demo.DOOR_SCREEN_MARKERS:
                assert marker not in menu, f"marker {marker!r} renders on the mode menu (tip {tip_index})"

    def test_starts_by_awaiting_the_landing_split(self):
        assert record_demo.DEMO_SCRIPT[0][0] == "await"
        assert record_demo.DEMO_SCRIPT[0][1] == record_demo.CATEGORY_SCREEN_MARKERS

    def test_ends_with_quit(self):
        assert record_demo.DEMO_SCRIPT[-1] == ("key", b"q")

    def test_pause_budget_keeps_gif_short(self):
        total = sum(step[1] for step in record_demo.DEMO_SCRIPT if step[0] == "pause")
        assert 5.0 <= total <= 20.0, f"scripted pauses total {total}s — demo would be too short or too long"


def _write_gif(path: Path, frames: list[Image.Image], duration_ms: int) -> None:
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration_ms, loop=0)


def _colorful_frames(n: int) -> list[Image.Image]:
    frames = []
    for i in range(n):
        im = Image.new("RGB", (120, 80), (20, 24, 30))
        for x in range(100):
            im.putpixel((x, i % 80), ((x * 37 + i * 11) % 256, (x * 5) % 256, x % 256))
        frames.append(im)
    return frames


class TestVerify:
    def _cast(self, tmp_path: Path, span: float = 14.0, events: int = 150) -> Path:
        path = tmp_path / "ok.cast"
        with path.open("w") as fh:
            fh.write(json.dumps({"version": 2, "width": 140, "height": 40}) + "\n")
            fh.write(json.dumps([0.1, "o", "\x1b[?1049h"]) + "\n")
            for i in range(events - 1):
                fh.write(json.dumps([round(0.1 + span * (i + 1) / events, 4), "o", f"frame{i}"]) + "\n")
        return path

    def test_sane_pair_passes(self, tmp_path):
        gif = tmp_path / "ok.gif"
        _write_gif(gif, _colorful_frames(100), duration_ms=140)  # 14s total
        assert record_demo.verify(self._cast(tmp_path), gif) == []

    def test_absurd_frame_durations_fail(self, tmp_path):
        """The 3000ms-per-frame failure mode."""
        gif = tmp_path / "slow.gif"
        _write_gif(gif, _colorful_frames(100), duration_ms=3000)
        assert any("durations are broken" in p for p in record_demo.verify(self._cast(tmp_path), gif))

    def test_blank_frames_fail(self, tmp_path):
        gif = tmp_path / "blank.gif"
        frames = [Image.new("RGB", (120, 80), (i, i, i)) for i in (0, 1, 2, 1, 0) for _ in range(20)]
        _write_gif(gif, frames, duration_ms=140)
        assert any("blank recording" in p for p in record_demo.verify(self._cast(tmp_path), gif))

    def test_frozen_recording_fails(self, tmp_path):
        gif = tmp_path / "frozen.gif"
        _write_gif(gif, [_colorful_frames(1)[0]] * 100, duration_ms=140)
        assert any("frozen recording" in p for p in record_demo.verify(self._cast(tmp_path), gif))

    def test_thin_cast_fails(self, tmp_path):
        gif = tmp_path / "ok.gif"
        _write_gif(gif, _colorful_frames(100), duration_ms=140)
        problems = record_demo.verify(self._cast(tmp_path, events=5), gif)
        assert any("events" in p for p in problems)

    def test_gz_cast_is_read_transparently(self, tmp_path):
        gif = tmp_path / "ok.gif"
        _write_gif(gif, _colorful_frames(100), duration_ms=140)
        plain = self._cast(tmp_path)
        gz = tmp_path / "ok.cast.gz"
        gz.write_bytes(gzip.compress(plain.read_bytes()))
        assert record_demo.verify(gz, gif) == []


class TestRender:
    def test_pins_agg_flags(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(record_demo.shutil, "which", lambda name: "/usr/bin/agg")
        monkeypatch.setattr(record_demo.subprocess, "run", lambda cmd, check: calls.append(cmd))
        cast = tmp_path / "a.cast"
        cast.write_text("{}\n")
        gif = tmp_path / "a.gif"
        gif.write_bytes(b"GIF89a")
        record_demo.render(cast, gif)
        assert calls[0][:3] == ["/usr/bin/agg", str(cast), str(gif)]
        assert calls[0][3:] == record_demo.AGG_FLAGS

    def test_missing_agg_exits_with_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(record_demo.shutil, "which", lambda name: None)
        with pytest.raises(SystemExit, match="brew install agg"):
            record_demo.render(tmp_path / "a.cast", tmp_path / "a.gif")

    def test_gz_cast_is_inflated_for_agg(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(record_demo.shutil, "which", lambda name: "/usr/bin/agg")
        monkeypatch.setattr(record_demo.subprocess, "run", lambda cmd, check: calls.append(cmd))
        cast = tmp_path / "a.cast.gz"
        cast.write_bytes(gzip.compress(b'{"version": 2}\n'))
        gif = tmp_path / "a.gif"
        gif.write_bytes(b"GIF89a")
        record_demo.render(cast, gif)
        src = Path(calls[0][1])
        assert src.suffix == ".cast"  # inflated temp copy, not the .gz


class TestRecordingEnv:
    """The hardening this env carries is only connected to the app by string
    literals — these assertions are what keeps a deleted line from silently
    reintroducing the mid-capture PyPI repaint."""

    def test_hardening_keys(self, tmp_path):
        env = record_demo._recording_env(tmp_path)
        assert env["YEABOI_UPDATE_CHECK"] == "0"
        assert env["YEABOI_NO_TUNNEL"] == "1"
        assert env["YEABOI_TELEMETRY"] == "off"
        assert env["YEABOI_NEWS"] == "off"
        assert env["HOME"] == str(tmp_path)
        assert env["TERM"] == "xterm-256color"
        assert env["ANTHROPIC_API_KEY"] == "test-key-dry-run-only"

    def test_yeaboi_home_never_leaks_in(self, tmp_path, monkeypatch):
        """The developer's real tree must not reach a recording — and nor must a worktree's.

        This used to assert the variable was absent, which was the same thing
        while unset meant ~/.yeaboi. It no longer is: unset now falls through to
        the checkout's .worktree.env, so the recording has to pin the temp home
        explicitly rather than by omission.
        """
        monkeypatch.setenv("YEABOI_HOME", "/somewhere/real")
        assert record_demo._recording_env(tmp_path)["YEABOI_HOME"] == str(tmp_path / ".yeaboi")


class TestMain:
    @pytest.fixture(autouse=True)
    def _stub_stages(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(record_demo, "record", lambda *a, **kw: self.calls.append("record"))
        monkeypatch.setattr(record_demo, "render", lambda *a, **kw: self.calls.append("render"))
        monkeypatch.setattr(record_demo, "verify", lambda *a, **kw: self.calls.append("verify") or [])
        # These stage tests are about ordering, not paths. The real defaults
        # resolve a yeaboi-site checkout, which CI does not have.
        monkeypatch.setattr(record_demo, "default_cast", lambda: Path("demo.cast.gz"))
        monkeypatch.setattr(record_demo, "default_gif", lambda: Path("demo.gif"))

    def test_full_run_records_renders_verifies(self):
        assert record_demo.main([]) == 0
        assert self.calls == ["record", "render", "verify"]

    def test_verify_problems_exit_nonzero(self, monkeypatch):
        monkeypatch.setattr(record_demo, "verify", lambda *a, **kw: ["gif is broken"])
        assert record_demo.main(["--check-only"]) == 1

    def test_check_only_skips_record_and_render(self):
        assert record_demo.main(["--check-only"]) == 0
        assert self.calls == ["verify"]

    def test_render_only_skips_record(self):
        assert record_demo.main(["--render-only"]) == 0
        assert self.calls == ["render", "verify"]

    def test_windows_cannot_record(self, monkeypatch):
        monkeypatch.setattr(record_demo.sys, "platform", "win32")
        with pytest.raises(SystemExit, match="POSIX"):
            record_demo.main([])
