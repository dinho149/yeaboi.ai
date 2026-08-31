"""Serve a seeded reporting slide deck for front-end development.

The third sibling of ``dev_board.py`` / ``dev_poker.py``, and the odd one out:
a deck has no server. It is a single file written to disk, so this builds a real
one from a real ``DeliveryReport`` and serves the directory over HTTP.

    make dev-deck           # prints the URL

Over HTTP rather than handing you a ``file://`` path, for two reasons: reloading
is one keystroke, and some browser tooling refuses ``file://`` outright. The
deck itself is genuinely self-contained either way — nothing is fetched.

The report exercises every slide kind and a non-default style, because the
default resolves most knobs to values the deck would have anyway. Nothing is
written to ``~/.yeaboi``; the deck lands in a temp directory that is cleaned up
on exit. Custom palettes from ``reporting_themes.json`` *are* picked up, which
is the point if you are working on one.
"""

from __future__ import annotations

import http.server
import logging
import socketserver
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from yeaboi.agent.state import DeliveredItem, DeliveryReport, SupportingSignal  # noqa: E402
from yeaboi.config import get_deck_server_port  # noqa: E402
from yeaboi.reporting.presentation import build_presentation_html  # noqa: E402
from yeaboi.reporting.style import DeckStyle  # noqa: E402

PORT = get_deck_server_port()

REPORT = DeliveryReport(
    period_label="Last month (~2 sprints)",
    period_start="2026-06-15",
    period_end="2026-07-13",
    project_name="Acme Portal",
    sprint_names=("Sprint 11", "Sprint 12"),
    headline="Two sprints of strong delivery, with SSO finally live.",
    executive_summary=(
        "The team locked down access across the whole estate this month. "
        "Single sign-on went live for all internal staff on the 2nd, and multi-factor "
        "enrolment reached 94% by the end of the period. "
        "Separately, a long-running checkout performance thread closed out at roughly "
        "half the previous median latency."
    ),
    themes=(
        (
            "Security",
            (
                "SSO live for all internal staff",
                "MFA enrolment at 94%",
                "Break-glass access is now granted on demand and time-limited rather than standing",
            ),
        ),
        ("Performance", ("Checkout median latency halved", "Cold-start p99 down 1.4s")),
        ("Platform", ("Migrated the last two services off the legacy queue",)),
    ),
    highlights=("SSO live", "2x faster checkout", "Zero Sev-1s across the period"),
    metrics=(("Items delivered", "12"), ("Story points", "48"), ("Sev-1 incidents", "0")),
    delivered_items=(DeliveredItem(key="A-1", title="Ship SSO", status="Done"),),
    supporting_signals=(
        SupportingSignal(kind="pull_requests", source="github", count=24),
        SupportingSignal(kind="doc_updates", source="notion", count=5),
    ),
    emoji_theme=(
        ("headline", "🚀"),
        ("summary", "📋"),
        ("metrics", "📊"),
        ("themes", "🧩"),
        ("highlights", "⭐"),
        ("thanks", "🙌"),
    ),
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # A custom footer and slide numbers so the two optional corners render.
    style = DeckStyle(slide_numbers=True, footer_text="ACME Corp — Confidential")

    with tempfile.TemporaryDirectory(prefix="yeaboi-dev-deck-") as tmp:
        out = Path(tmp) / "deck.html"
        out.write_text(build_presentation_html(REPORT, style=style), encoding="utf-8")

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=tmp, **kwargs)

            def log_message(self, *args):  # quiet — the deck fetches nothing anyway
                pass

        # Rebinding matters here: this gets restarted after every `make web`,
        # since read_asset caches the bundle for the life of the process.
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
            print(f"\n  slide deck  →  http://127.0.0.1:{PORT}/deck.html")
            print(f"  {len(out.read_bytes()) // 1024} KB, one file, no network")
            print("\n  Rebuilt the bundle? Restart this — read_asset is cached.\n")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nstopped")


if __name__ == "__main__":
    main()
