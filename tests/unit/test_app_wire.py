"""Wire-shape pins for the desktop backend contract (contracts/v1/app_http.md).

The desktop shell parses these shapes; a changed key is a broken shell. Keep
this file and the contract doc in step — the last test greps the doc for every
route so the prose cannot silently fall behind the table.
"""

from __future__ import annotations

import json
from pathlib import Path

from yeaboi.app.handshake import READY_PREFIX, Handshake, ready_line

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "v1" / "app_http.md"


class TestHandshakeWire:
    def test_ready_line_shape_is_pinned(self):
        line = ready_line(Handshake(url="u", token="t", pid=1, schema=2, version="v"))
        assert line == 'YEABOI_APP_READY {"pid":1,"schema":2,"token":"t","url":"u","version":"v"}'

    def test_prefix_is_pinned(self):
        assert READY_PREFIX == "YEABOI_APP_READY "


class TestEnvelopeWire:
    def test_success_envelope_keys(self):
        from yeaboi.mcp.runtime import envelope

        assert set(envelope({})) == {"ok", "llm_mode", "warnings", "data"}

    def test_error_envelope_keys(self):
        from yeaboi.mcp.runtime import error_envelope

        payload = error_envelope(ValueError("x"))
        assert {"ok", "llm_mode", "error"} <= set(payload)
        assert set(payload["error"]) == {"type", "message"}

    def test_router_error_shape(self):
        from yeaboi.app.router import Request, Router

        resp = Router().dispatch(Request(method="GET", path="/api/none", authed=True))
        assert json.loads(resp.body) == {"error": "not found"}


class TestSseWire:
    def test_data_frame_shape(self):
        from yeaboi.app.events import EventBus

        bus = EventBus()
        stream = bus.sse_stream()
        next(stream)
        bus.publish("progress", op_id="x", tool="t", progress=1.0, total=None, message=None)
        frame = next(stream)
        stream.close()
        payload = json.loads(frame[len(b"data: ") : -2])
        assert {"type", "seq", "ts", "op_id", "tool", "progress", "total", "message"} == set(payload)


class TestAmbienceWire:
    """The desktop hand-maintains a TypeScript mirror of this payload."""

    def test_saver_keys_are_pinned(self, monkeypatch):
        from pathlib import Path as _Path

        from yeaboi import ambience, config

        monkeypatch.setattr(config, "set_config_value", lambda _k, _v: _Path("/tmp/.env"))
        monkeypatch.delenv("SAVER_STYLE", raising=False)
        assert set(ambience.state()["saver"]) == {"idle_seconds", "style", "styles"}

    def test_off_is_always_offerable(self):
        # Both surfaces honour it; a catalogue without it takes away the only
        # way to turn the screensaver off.
        from yeaboi import ambience

        assert "off" in ambience.SAVER_STYLES
        assert ambience.DEFAULT_SAVER_STYLE in ambience.SAVER_STYLES


class TestContractDoc:
    def test_every_route_is_documented(self):
        from yeaboi.app.registry import ROUTES

        text = CONTRACT.read_text(encoding="utf-8")
        for route in ROUTES:
            assert f"`{route.path}`" in text, f"{route.path} missing from contracts/v1/app_http.md"

    def test_doc_pins_the_ready_prefix(self):
        assert "YEABOI_APP_READY" in CONTRACT.read_text(encoding="utf-8")


class TestSessionsWire:
    """The cross-mode sessions row the desktop's Sessions page and project pages parse."""

    def test_row_keys_are_pinned(self):
        from dataclasses import fields

        from yeaboi.sessions_recent import RecentSession

        assert [f.name for f in fields(RecentSession)] == [
            "session_id",
            "run_id",
            "mode",
            "title",
            "created_at",
            "last_modified",
            "project_id",
        ]

    def test_mode_vocabulary_is_pinned(self):
        from yeaboi.sessions_recent import MODES

        assert MODES == ("planning", "analysis", "standup", "retro", "reporting", "ship", "review")

    def test_doc_names_the_id_space(self):
        text = CONTRACT.read_text(encoding="utf-8")
        assert "## Projects and sessions" in text
        assert "proj-<8hex>" in text


class TestNewsWire:
    """The front page's item shape, pinned field for field against the contract."""

    def test_item_keys_are_pinned(self):
        from dataclasses import fields

        from yeaboi.news.parse import NewsItem

        assert [f.name for f in fields(NewsItem)] == [
            "id",
            "title",
            "url",
            "source_id",
            "source_name",
            "published",
            "summary",
            "image_url",
            "kind",
            "topic",
            "persona",
            "column",
        ]

    def test_paper_keys_are_pinned(self):
        from dataclasses import fields

        from yeaboi.news.paper import Paper, SourceStatus

        assert [f.name for f in fields(Paper)] == ["schema", "generated_at", "stale", "lead", "sections", "sources"]
        assert [f.name for f in fields(SourceStatus)] == [
            "id",
            "name",
            "home_url",
            "column",
            "ok",
            "fetched_at",
            "error",
            "item_count",
        ]
