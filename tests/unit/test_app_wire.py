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


class TestContractDoc:
    def test_every_route_is_documented(self):
        from yeaboi.app.registry import ROUTES

        text = CONTRACT.read_text(encoding="utf-8")
        for route in ROUTES:
            assert f"`{route.path}`" in text, f"{route.path} missing from contracts/v1/app_http.md"

    def test_doc_pins_the_ready_prefix(self):
        assert "YEABOI_APP_READY" in CONTRACT.read_text(encoding="utf-8")
