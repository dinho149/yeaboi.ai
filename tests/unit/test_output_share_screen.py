"""Render coverage for the shared online-output screen."""

import time

from rich.console import Console

from yeaboi.sharing.server import ShareDocument
from yeaboi.ui.shared._components import STANDUP_THEME, standup_title
from yeaboi.ui.shared._output_share import _build_output_share_screen, run_output_share


def _text(panel) -> str:
    console = Console(width=100, record=True)
    console.print(panel)
    return console.export_text()


def test_starting_state_renders_warning_and_back():
    panel = _build_output_share_screen(
        title_fn=standup_title,
        theme=STANDUP_THEME,
        document_title="Daily Standup",
        status="Starting…",
        loading=True,
        shimmer_tick=2.5,
        width=100,
        height=30,
    )
    out = _text(panel)
    assert "Share this output online" in out
    assert "Anyone with the temporary URL" in out
    assert "Establishing secure share" in out
    assert "Elapsed: 2s" in out
    assert "Esc cancels" in out
    assert "Back" in out


def test_starting_animation_changes_between_frames():
    def render(tick: float) -> str:
        return _text(
            _build_output_share_screen(
                title_fn=standup_title,
                theme=STANDUP_THEME,
                document_title="Daily Standup",
                status="Starting the secure tunnel…",
                loading=True,
                shimmer_tick=tick,
                width=100,
                height=30,
            )
        )

    first = render(0.0)
    second = render(0.25)
    assert "◐  Establishing secure share" in first
    assert "◓  Establishing secure share" in second
    assert first != second


def test_ready_state_renders_url_code_and_actions():
    panel = _build_output_share_screen(
        title_fn=standup_title,
        theme=STANDUP_THEME,
        document_title="Daily Standup",
        status="Sharing is live.",
        public_url="https://example.trycloudflare.com/",
        join_code="ABCD-2345",
        actions=["Copy Invite", "Stop Sharing", "Back"],
        width=100,
        height=34,
    )
    out = _text(panel)
    assert "example.trycloudflare.com" in out
    assert "ABCD-2345" in out
    assert "Copy Invite" in out
    assert "Stop Sharing" in out


def test_runner_stops_server_and_tunnel(monkeypatch):
    events: list[str] = []

    class FakeServer:
        port = 54321
        display_code = "ABCD-2345"

        def __init__(self, document):
            self.document = document

        def start(self):
            events.append("server-start")

        def stop(self):
            events.append("server-stop")

    class FakeTunnel:
        def __init__(self, port, *, binary):
            assert port == 54321

        def start(self, *, timeout):
            events.append("tunnel-start")
            return "https://example.trycloudflare.com"

        def stop(self):
            events.append("tunnel-stop")

    class FakeConsole:
        size = (100, 34)

    class FakeLive:
        def update(self, _panel):
            pass

    keys = iter(("right", "enter"))

    def read_key(timeout=None):
        time.sleep(0.01)
        return next(keys, "enter")

    monkeypatch.setattr("yeaboi.ui.shared._output_share.OutputShareServer", FakeServer)
    monkeypatch.setattr("yeaboi.sharing.tunnel.ensure_cloudflared", lambda: "/bin/cloudflared")
    monkeypatch.setattr("yeaboi.sharing.tunnel.CloudflareTunnel", FakeTunnel)

    run_output_share(
        FakeConsole(),
        FakeLive(),
        read_key,
        0.001,
        True,
        document=ShareDocument("Daily Standup", "<html></html>", "standup"),
        theme=STANDUP_THEME,
        title_fn=standup_title,
    )
    assert events[:2] == ["server-start", "tunnel-start"]
    assert "tunnel-stop" in events
    assert "server-stop" in events


def test_copy_invite_puts_one_self_contained_link_on_the_clipboard(monkeypatch):
    """The payload the original bug was about, on the surface it reached furthest.

    Share Online is how standup, performance, reporting, roadmap and every export
    get shared, so this is the widest of the three Copy Invite buttons. It used to
    copy the URL, a newline, and the sentence ``Access code: XXXX-XXXX``; any
    paste target that flattens a newline glued them into one 404ing path. What
    goes on the clipboard now is one URL and nothing else.
    """
    copied: list[str] = []

    class FakeServer:
        port = 54321
        display_code = "ABCD-2345"

        def __init__(self, document):
            self.document = document

        def start(self):
            pass

        def stop(self):
            pass

    class FakeTunnel:
        def __init__(self, port, *, binary):
            pass

        def start(self, *, timeout):
            # No trailing slash, exactly as cloudflared reports it — the screen
            # adds one, and invite_url has to cope with either.
            return "https://example.trycloudflare.com"

        def stop(self):
            pass

    class FakeConsole:
        size = (100, 34)

    class FakeLive:
        def update(self, _panel):
            pass

    # Copy Invite is the first action once sharing is live, so a bare Enter
    # presses it; the second leaves.
    keys = iter(("enter", "right", "enter"))

    def read_key(timeout=None):
        time.sleep(0.01)
        return next(keys, "enter")

    monkeypatch.setattr("yeaboi.ui.shared._output_share.OutputShareServer", FakeServer)
    monkeypatch.setattr("yeaboi.sharing.tunnel.ensure_cloudflared", lambda: "/bin/cloudflared")
    monkeypatch.setattr("yeaboi.sharing.tunnel.CloudflareTunnel", FakeTunnel)
    monkeypatch.setattr("yeaboi.clipboard.copy_text", lambda text: copied.append(text) or True)

    run_output_share(
        FakeConsole(),
        FakeLive(),
        read_key,
        0.001,
        True,
        document=ShareDocument("Daily Standup", "<html></html>", "standup"),
        theme=STANDUP_THEME,
        title_fn=standup_title,
    )

    assert copied == ["https://example.trycloudflare.com/#code=ABCD-2345"]
    # One line, no whitespace anywhere: there is nothing for a paste target to
    # flatten, and nothing to glue onto the end of the URL.
    assert not any(char.isspace() for char in copied[0])
