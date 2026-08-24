"""Router/Request/Response — the socketless dispatch core of the app server."""

from __future__ import annotations

import pytest

from yeaboi.app.router import HTTPError, Request, Router, json_response, parse_request


def _ok(request: Request):
    return json_response({"path": request.path, "params": dict(request.params)})


class TestRequest:
    def test_json_empty_body_is_empty_dict(self):
        assert Request(method="POST", path="/x").json() == {}

    def test_json_object_parses(self):
        req = Request(method="POST", path="/x", body=b'{"a": 1}')
        assert req.json() == {"a": 1}

    def test_json_non_object_raises_value_error(self):
        req = Request(method="POST", path="/x", body=b"[1, 2]")
        with pytest.raises(ValueError, match="expected a JSON object"):
            req.json()

    def test_parse_request_keeps_first_query_value(self):
        req = parse_request("get", "/api/x?a=1&a=2&b=3", {"H": "v"})
        assert req.method == "GET"
        assert req.query == {"a": "1", "b": "3"}
        assert req.headers == {"H": "v"}


class TestDispatch:
    def _router(self) -> Router:
        router = Router()
        router.get("/api/open", _ok, auth=False)
        router.get("/api/closed", _ok)
        router.post("/api/ops/{op_id}/cancel", _ok)
        return router

    def test_unknown_path_is_404(self):
        resp = self._router().dispatch(Request(method="GET", path="/api/nope", authed=True))
        assert resp.code == 404

    def test_wrong_method_is_405_not_404(self):
        resp = self._router().dispatch(Request(method="POST", path="/api/open", authed=True))
        assert resp.code == 405

    def test_auth_route_without_token_is_401(self):
        resp = self._router().dispatch(Request(method="GET", path="/api/closed"))
        assert resp.code == 401

    def test_open_route_answers_unauthenticated(self):
        resp = self._router().dispatch(Request(method="GET", path="/api/open"))
        assert resp.code == 200

    def test_path_params_are_extracted(self):
        resp = self._router().dispatch(Request(method="POST", path="/api/ops/abc123/cancel", authed=True))
        assert b'"op_id":"abc123"' in resp.body

    def test_path_params_are_percent_decoded(self):
        # A parameter is a URL-encoded segment by definition; a handler that
        # looked one up raw would miss every value with a space in it — which
        # is most people's names.
        resp = self._router().dispatch(Request(method="POST", path="/api/ops/Ada%20Lovelace/cancel", authed=True))
        assert b'"op_id":"Ada Lovelace"' in resp.body

    def test_param_never_spans_segments(self):
        resp = self._router().dispatch(Request(method="POST", path="/api/ops/a/b/cancel", authed=True))
        assert resp.code == 404

    def test_http_error_becomes_status(self):
        router = Router()

        def boom(request):
            raise HTTPError(418, "teapot")

        router.get("/api/tea", boom)
        resp = router.dispatch(Request(method="GET", path="/api/tea", authed=True))
        assert resp.code == 418
        assert b"teapot" in resp.body

    def test_value_error_becomes_400(self):
        router = Router()

        def bad(request):
            raise ValueError("bad field")

        router.get("/api/bad", bad)
        resp = router.dispatch(Request(method="GET", path="/api/bad", authed=True))
        assert resp.code == 400
        assert b"bad field" in resp.body


class TestRegistryAuthPosture:
    def test_every_route_requires_auth_unless_allowlisted(self):
        """The check that makes "someone forgot the auth line" unshippable."""
        from yeaboi.app.registry import UNAUTHENTICATED, build_router
        from yeaboi.app.server import AppServer

        app = AppServer(token="t")
        for route in build_router(app).routes:
            expected = route.template not in UNAUTHENTICATED
            assert route.auth is expected, f"{route.template} auth={route.auth}, expected {expected}"

    def test_allowlist_is_only_health(self):
        from yeaboi.app.registry import UNAUTHENTICATED

        assert UNAUTHENTICATED == {"/api/health"}
