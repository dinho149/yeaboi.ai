"""AppServer.handle() — the whole surface, no socket. Plus one bound-port pass."""

from __future__ import annotations

import json
import threading

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer, serve

TOKEN = "test-token"


def request(app: AppServer, method: str, path: str, body: bytes = b"", *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    return app.handle(parse_request(method, path, headers, body))


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


class TestMetaRoutes:
    def test_health_answers_without_auth(self, app):
        resp = request(app, "GET", "/api/health", authed=False)
        payload = json.loads(resp.body)
        assert resp.code == 200
        assert payload["ok"] is True
        assert set(payload) == {"ok", "pid", "version", "schema"}

    def test_everything_else_requires_auth(self, app):
        for path in ("/api/meta/version", "/api/meta/tips", "/api/meta/changelog", "/api/tools", "/api/events"):
            assert request(app, "GET", path, authed=False).code == 401, path

    def test_version_shape(self, app):
        payload = json.loads(request(app, "GET", "/api/meta/version").body)
        assert set(payload) == {"version", "schema_version", "python", "platform"}

    def test_capabilities_serves_the_card_inventory(self, app):
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS, _SOLO_CARDS

        payload = json.loads(request(app, "GET", "/api/meta/capabilities").body)
        assert set(payload) == {"categories", "solo", "modes", "agents", "intake"}
        assert [card["key"] for card in payload["solo"]] == [card["key"] for card in _SOLO_CARDS]
        assert [card["key"] for card in payload["modes"]] == [card["key"] for card in _MODE_CARDS]
        assert [card["key"] for card in payload["agents"]] == [card["key"] for card in _AGENT_CARDS]

    def test_tips_serves_the_desktop_rotation(self, app):
        # The registry holds both surfaces' tips; this endpoint is the desktop
        # app's, so a terminal keycap or CLI flag must never reach it.
        payload = json.loads(request(app, "GET", "/api/meta/tips").body)
        keys = {tip["key"] for tip in payload["tips"]}
        assert "planning" in keys and "voice" in keys
        assert "meta:headless" not in keys and "music" not in keys
        assert "desktop:projects" in keys
        assert not [t for t in payload["tips"] if "--" in t["text"] or "press " in t["text"]]
        # Pin the shape: a field added to FeatureTip lands on the wire for free,
        # so the contract doc only stays true if something notices.
        assert set(payload["tips"][0]) == {"key", "text", "mode_key", "is_new", "is_beta", "surfaces", "worlds"}

    def test_changelog_serves_entries(self, app):
        payload = json.loads(request(app, "GET", "/api/meta/changelog").body)
        assert payload["entries"], "bundled changelog should never be empty"
        entry = payload["entries"][0]
        assert {"version", "date", "headline", "summary", "highlights"} <= set(entry)
        assert entry["headline"], "every entry carries a headline on the wire"
        assert {"text", "areas", "surfaces"} <= set(entry["highlights"][0])

    def test_changelog_serves_area_accents(self, app):
        """The desktop colours an area tag from this table — see contracts/v1."""
        from yeaboi.changelog import AREA_COLORS

        payload = json.loads(request(app, "GET", "/api/meta/changelog").body)
        served = {row["name"]: row["color"] for row in payload["areas"]}
        assert served == AREA_COLORS


class TestToolRoutes:
    def test_tools_lists_unavailable_without_dispatcher(self, app):
        payload = json.loads(request(app, "GET", "/api/tools").body)
        assert payload == {"available": False, "tools": []}

    def test_tool_call_without_dispatcher_is_503(self, app):
        resp = request(app, "POST", "/api/tool/sessions_list", b"{}")
        assert resp.code == 503

    def test_unknown_tool_is_404(self):
        class FakeDispatcher:
            available = True

            def tool_names(self):
                return frozenset({"real_tool"})

        app = AppServer(token=TOKEN, dispatcher=FakeDispatcher())
        assert request(app, "POST", "/api/tool/not_real", b"{}").code == 404

    def test_tool_call_passes_arguments_and_op_id(self):
        calls = []

        class FakeDispatcher:
            available = True

            def tool_names(self):
                return frozenset({"real_tool"})

            def call_tool(self, name, arguments, *, op_id=None):
                calls.append((name, arguments, op_id))
                return {"ok": True, "llm_mode": "n/a", "warnings": [], "data": {}}

        app = AppServer(token=TOKEN, dispatcher=FakeDispatcher())
        body = json.dumps({"arguments": {"a": 1}, "op_id": "op9"}).encode()
        resp = request(app, "POST", "/api/tool/real_tool", body)
        assert resp.code == 200
        assert calls == [("real_tool", {"a": 1}, "op9")]

    def test_non_object_arguments_is_400(self):
        class FakeDispatcher:
            available = True

            def tool_names(self):
                return frozenset({"real_tool"})

        app = AppServer(token=TOKEN, dispatcher=FakeDispatcher())
        resp = request(app, "POST", "/api/tool/real_tool", b'{"arguments": [1]}')
        assert resp.code == 400


class TestOpsAndEvents:
    def test_cancel_round_trip(self, app):
        op = app.ops.create("op1")
        resp = request(app, "POST", "/api/ops/op1/cancel")
        assert json.loads(resp.body) == {"cancelled": True, "op_id": "op1"}
        assert op.cancel.is_set()

    def test_events_is_a_stream(self, app):
        resp = request(app, "GET", "/api/events")
        assert resp.content_type == "text/event-stream"
        assert resp.stream is not None
        assert next(resp.stream) == b": connected\n\n"
        resp.stream.close()


class TestShutdown:
    def test_shutdown_invokes_callback_once(self):
        fired = []
        stop = threading.Event()

        def on_shutdown():
            fired.append(1)
            stop.set()

        app = AppServer(token=TOKEN, on_shutdown=on_shutdown)
        assert request(app, "POST", "/api/shutdown").code == 200
        assert request(app, "POST", "/api/shutdown").code == 200
        assert stop.wait(5)
        assert fired == [1]

    def test_shutdown_without_callback_is_still_ok(self, app):
        assert request(app, "POST", "/api/shutdown").code == 200


class TestBoundServer:
    def test_real_http_round_trip(self, app):
        """One pass through the actual handler plumbing on a real socket."""
        import urllib.request

        httpd = serve("127.0.0.1", 0, app=app)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
                assert resp.status == 200
                assert json.loads(resp.read())["ok"] is True
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/meta/version",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()

    def test_a_finished_stream_reaches_end_of_body(self, app):
        """A streamed body has no Content-Length, so keep-alive would leave the
        client reading a finished run forever — the read must return EOF."""
        import urllib.request

        from yeaboi.app.router import Response, Router

        router = Router()
        router.get("/api/drip", lambda request: Response(content_type="application/x-ndjson", stream=iter([b"1\n"])))
        app.router = router
        httpd = serve("127.0.0.1", 0, app=app)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_address[1]}/api/drip",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.read() == b"1\n"  # returns rather than blocking
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()
