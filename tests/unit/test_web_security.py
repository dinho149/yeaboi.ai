"""Tests for the shared served-document headers and the policy builder.

Every yeaboi surface a browser can reach goes through ``web.security``. These
tests are the record of what that header set is and why each loose directive in
``BOARD_CSP`` is loose — the board policy is the only one that names anything
other than ``'self'``, and each exception is a fact about the boards rather
than a convenience.
"""

from __future__ import annotations

from yeaboi.music import CHANNELS
from yeaboi.web.security import (
    ARTIFACT_CSP,
    BOARD_CSP,
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
    def test_carries_the_six_protective_headers(self):
        names = {name for name, _ in DOCUMENT_HEADERS}
        assert names == {
            "Cache-Control",
            "Pragma",
            "Referrer-Policy",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Permissions-Policy",
        }

    def test_devices_are_denied_except_the_duel_mic(self):
        """One feature is real, the rest are denied outright.

        The poker duel records each duelist's turn in their own browser
        (getUserMedia + POST /api/duel/audio), so `microphone` must allow the
        document's own origin — `microphone=()` would reject every duelist's
        recording with NotAllowedError and the duel would transcribe silence.
        Everything else stays an empty allowlist so an injected script cannot
        quietly ask.
        """
        policy_value = dict(DOCUMENT_HEADERS)["Permissions-Policy"]
        assert "microphone=(self)" in policy_value
        for feature in ("camera", "geolocation", "payment", "usb"):
            assert f"{feature}=()" in policy_value

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


class TestBoardPolicy:
    """The board policy's three loose directives, and why each one is loose."""

    def test_denies_everything_by_default(self):
        assert _directives(BOARD_CSP)["default-src"] == "'none'"

    def test_does_not_allow_eval(self):
        assert "unsafe-eval" not in BOARD_CSP

    def test_talks_only_to_its_own_origin(self):
        # The transport is long polling, not SSE and not a WebSocket, and every
        # request is built from a relative path in runtime/api.ts. 'self' costs
        # the boards nothing.
        assert _directives(BOARD_CSP)["connect-src"] == "'self'"

    def test_allows_its_own_images_for_the_invite_qr(self):
        # GET /api/qr answers with image/svg+xml from the board itself. It is
        # not a data URI, so dropping 'self' would silently blank the one thing
        # a host shows a teammate.
        assert _directives(BOARD_CSP)["img-src"] == "'self' data:"

    def test_allows_https_media_for_the_radio(self):
        assert _directives(BOARD_CSP)["media-src"] == "https:"

    def test_every_radio_channel_is_https(self):
        """``media-src https:`` is only sufficient while every station is https.

        A scheme-source rather than an origin allowlist because one channel is a
        redirector that lands on a different host, and CSP re-checks the
        redirect target rather than the URL we wrote. This test is what keeps
        the directive honest as stations change.
        """
        for channel in CHANNELS:
            assert channel["url"].startswith("https://"), channel

    def test_https_is_the_only_named_exception(self):
        # Every other directive stays 'self' or narrower. Written as its own
        # assertion because the gate and artifact policies are checked with a
        # blanket "no 'http' anywhere", which the board cannot pass.
        loose = {name: value for name, value in _directives(BOARD_CSP).items() if "http" in value}
        assert loose == {"media-src": "https:"}

    def test_base_and_form_targets_are_locked_down(self):
        directives = _directives(BOARD_CSP)
        assert directives["base-uri"] == "'none'"
        assert directives["form-action"] == "'none'"
        assert directives["frame-ancestors"] == "'none'"


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


class TestEditPolicy:
    """The editable document's policy: an artifact that also takes input.

    It sits between the other two, and the interesting assertions are about
    where it stops — a document is not a board, so it must not have acquired a
    board's freedoms along with a board's transport.
    """

    def test_denies_everything_by_default(self):
        from yeaboi.web.security import EDIT_CSP

        assert _directives(EDIT_CSP)["default-src"] == "'none'"

    def test_does_not_allow_eval(self):
        from yeaboi.web.security import EDIT_CSP

        assert "unsafe-eval" not in EDIT_CSP

    def test_talks_only_to_its_own_origin(self):
        # The one directive ARTIFACT_CSP forbids outright, and the whole reason
        # an editable document cannot be served under it: connect-src 'none'
        # makes the edit POST and the long poll impossible by construction.
        from yeaboi.web.security import EDIT_CSP

        assert _directives(EDIT_CSP)["connect-src"] == "'self'"

    def test_the_finished_artifact_still_talks_to_nothing(self):
        assert _directives(ARTIFACT_CSP)["connect-src"] == "'none'"

    def test_serves_only_embedded_images(self):
        """No `'self'`: the boards earn theirs with `<img src="/api/qr?…">`.

        This server has no QR route, so the directive would be permission
        granted for nothing — the same reasoning the board policy applies to
        its own three loose directives, turned on this one.
        """
        from yeaboi.web.security import EDIT_CSP

        assert _directives(EDIT_CSP)["img-src"] == "data:"

    def test_has_no_media_source(self):
        """There is no radio on a document.

        A directive nothing needs is a directive nobody will notice is wrong, so
        this asserts the absence rather than trusting that nobody pasted the
        board policy across.
        """
        from yeaboi.web.security import EDIT_CSP

        assert "media-src" not in _directives(EDIT_CSP)

    def test_forms_cannot_navigate(self):
        from yeaboi.web.security import EDIT_CSP

        assert _directives(EDIT_CSP)["form-action"] == "'none'"

    def test_it_is_not_simply_the_board_policy(self):
        from yeaboi.web.security import EDIT_CSP

        assert EDIT_CSP != BOARD_CSP

    def test_it_differs_from_the_artifact_in_exactly_one_directive(self):
        """The claim the policy's comment makes, pinned as a whole-policy diff.

        The assertions above each check one directive they expected to think
        about. This one catches the directive nobody thought about: loosen
        anything here beyond connect-src and it fails, whether or not a test
        exists for that directive by name.
        """
        from yeaboi.web.security import EDIT_CSP

        artifact, editable = _directives(ARTIFACT_CSP), _directives(EDIT_CSP)
        differing = {k for k in artifact.keys() | editable.keys() if artifact.get(k) != editable.get(k)}
        assert differing == {"connect-src"}
