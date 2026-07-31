"""Tests for the shared served-document headers and the policy builder.

Every yeaboi surface a browser can reach goes through ``web.security``. These
tests are the record of what that header set is, and of the two policies that
predate the module keeping their exact shape as they moved.
"""

from __future__ import annotations

from yeaboi.web.security import (
    ARTIFACT_CSP,
    DOCUMENT_HEADERS,
    GATE_CSP,
    policy,
    send_document,
)


def _directives(csp: str) -> dict[str, str]:
    parts = [d.strip() for d in csp.split(";") if d.strip()]
    return {d.split(" ", 1)[0]: d.split(" ", 1)[1] if " " in d else "" for d in parts}


class _FakeHandler:
    """A BaseHTTPRequestHandler stand-in with no socket behind it.

    This is the reason ``send_document`` is a free function taking the handler
    rather than a mixin the three handler classes inherit: the header set can be
    asserted without binding a port.
    """

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: list[tuple[str, str]] = []
        self.ended = False
        self.written = b""
        self.wfile = self

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def end_headers(self) -> None:
        self.ended = True

    def write(self, body: bytes) -> None:
        self.written = body


class TestPolicyBuilder:
    def test_overrides_replace_a_base_directive_in_place(self):
        # img-src exists in the base, so overriding it must not append a second
        # copy — a duplicate directive is ignored by browsers in a way that is
        # invisible until something breaks on someone else's phone.
        built = _directives(policy(img_src="'self' data:"))
        assert built["img-src"] == "'self' data:"
        assert policy(img_src="'self' data:").count("img-src") == 1

    def test_new_directives_are_appended(self):
        assert _directives(policy(media_src="https:"))["media-src"] == "https:"

    def test_underscores_become_hyphens(self):
        assert "connect-src 'self'" in policy(connect_src="'self'")

    def test_output_is_stable(self):
        assert policy(connect_src="'none'") == policy(connect_src="'none'")


class TestDocumentHeaders:
    def test_carries_the_five_protective_headers(self):
        names = {name for name, _ in DOCUMENT_HEADERS}
        assert names == {
            "Cache-Control",
            "Pragma",
            "Referrer-Policy",
            "X-Content-Type-Options",
            "X-Frame-Options",
        }

    def test_nothing_is_cacheable(self):
        # A share URL dies with the TUI screen. A cached artifact outliving the
        # share is exactly what the code gate exists to prevent.
        headers = dict(DOCUMENT_HEADERS)
        assert headers["Cache-Control"] == "no-store, max-age=0"
        assert headers["Pragma"] == "no-cache"

    def test_framing_and_sniffing_are_denied(self):
        headers = dict(DOCUMENT_HEADERS)
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"

    def test_referrer_never_leaks_the_tunnel_hostname(self):
        assert dict(DOCUMENT_HEADERS)["Referrer-Policy"] == "no-referrer"


class TestSendDocument:
    def test_writes_status_headers_and_body(self):
        handler = _FakeHandler()
        send_document(handler, 200, b"hello", "text/html; charset=utf-8")
        assert handler.status == 200
        assert handler.ended is True
        assert handler.written == b"hello"

    def test_sets_content_length_from_the_body(self):
        handler = _FakeHandler()
        send_document(handler, 200, b"abcd", "text/plain")
        assert dict(handler.headers)["Content-Length"] == "4"

    def test_includes_every_shared_header(self):
        handler = _FakeHandler()
        send_document(handler, 200, b"x", "text/plain")
        sent = dict(handler.headers)
        for name, value in DOCUMENT_HEADERS:
            assert sent[name] == value

    def test_csp_is_omitted_when_not_given(self):
        handler = _FakeHandler()
        send_document(handler, 200, b"x", "application/json")
        assert "Content-Security-Policy" not in dict(handler.headers)

    def test_csp_is_sent_when_given(self):
        handler = _FakeHandler()
        send_document(handler, 200, b"x", "text/html", csp=GATE_CSP)
        assert dict(handler.headers)["Content-Security-Policy"] == GATE_CSP


class TestArtifactAndGateAreUnchanged:
    """The two policies that predate this module keep their exact shape."""

    def test_artifact_reaches_nowhere(self):
        assert _directives(ARTIFACT_CSP)["connect-src"] == "'none'"

    def test_gate_reaches_only_its_own_origin(self):
        assert _directives(GATE_CSP)["connect-src"] == "'self'"

    def test_neither_names_an_external_origin(self):
        for csp in (GATE_CSP, ARTIFACT_CSP):
            for value in _directives(csp).values():
                assert "http" not in value, f"external origin allowed: {csp}"
