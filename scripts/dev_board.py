"""Run a seeded retro board for front-end development.

Pairs with ``make web-dev``: this serves the real Python API on :5173 and Vite
proxies ``/api`` to it from :5399, so the TS bundles get HMR against genuine
server responses instead of hand-written fixtures.

    make dev-board          # terminal 1 — prints the URLs
    make web-dev            # terminal 2 — Vite on :5399

The board is in-memory only. Nothing is written to ``~/.yeaboi``, so this is
safe to run against a real install.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from yeaboi.retro.board import RetroBoard  # noqa: E402
from yeaboi.retro.server import RetroServer  # noqa: E402

# (grid, text, author) — enough volume and shape variety that layout bugs show
# up: uneven column heights, long text, and several cards from one author so
# the focus-mode walkthrough has something to walk through.
SEED: list[tuple[str, str, str]] = [
    ("went_well", "Shipped the tunnel long-polling change — remote board updates land in ~200ms now", "Ada"),
    ("went_well", "Pairing on the parser paid off", "Ada"),
    ("went_well", "Zero flaky tests this sprint", "Grace"),
    ("went_well", "The new export theme looks great on a phone", "Linus"),
    ("didnt_go_well", "Staging was down for most of Tuesday", "Grace"),
    ("didnt_go_well", "We found out about the API deprecation from a customer, not from the changelog", "Linus"),
    ("didnt_go_well", "Too much context switching", "Ada"),
    ("action_items", "Add an alert for staging health", "Grace"),
    ("action_items", "Subscribe the team channel to upstream release notes", "Linus"),
    ("demos", "New retro board walkthrough", "Ada"),
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    board = RetroBoard("dev-board", project_name="yeaboi", sprint_name="Sprint 42")
    for grid, text, author in SEED:
        if board.add_card(grid=grid, text=text, author=author, origin="seed", pid="dev-seed") is None:
            print(f"! seed rejected (unknown grid?): {grid}", file=sys.stderr)

    server = RetroServer(board)
    # Fixed credentials, as the poker dev board does it: a rebuild-and-restart
    # must not invalidate the token the tab you are looking at is holding.
    # Dev only — in-memory board, loopback socket.
    server.token = "dev-token"  # noqa: S105
    server.admin_token = "dev-admin"  # noqa: S105
    server.start()

    api = f"http://127.0.0.1:{server.port}"
    print("\n  dev board ready")
    print(f"    board      {server.url}")
    print(f"    api        {api}/api/state?token={server.token}&pid=dev&wait=25")
    # The same board through Vite, with HMR. `make web-dev` proxies /api here,
    # so the TS runs against genuine server responses rather than fixtures.
    # `board` above serves the committed bundle; this one serves the sources.
    print(f"    hmr        http://localhost:5399/dev/retro.html?token={server.token}&admin={server.admin_token}")
    print("\n  Ctrl-C to stop.\n")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
