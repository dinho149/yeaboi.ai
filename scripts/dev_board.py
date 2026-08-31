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
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from yeaboi.agent.state import RetroCard  # noqa: E402
from yeaboi.config import get_retro_server_port  # noqa: E402
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


# Last sprint's action items, up for review — the board opens on these, and a
# dev board with none of them never shows the strip that carries them.
CARRIED: list[str] = [
    "Chase the flaky deploy step",
    "Write down how a release actually gets cut",
    "Book the retro before the sprint ends, not after",
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    board = RetroBoard("dev-board", project_name="yeaboi", sprint_name="Sprint 42")
    for grid, text, author in SEED:
        if board.add_card(grid=grid, text=text, author=author, origin="seed", pid="dev-seed") is None:
            print(f"! seed rejected (unknown grid?): {grid}", file=sys.stderr)

    board.seed_carried(
        [RetroCard(id=f"carried-{i}", text=text, author="last sprint") for i, text in enumerate(CARRIED)]
    )

    # Two retros behind this one, so the back arrow has somewhere to go. Real
    # boards read these from the store (see engine.history_providers); this is
    # the same shape by hand.
    past = [
        {
            "id": 2,
            "run_at": "2026-08-01T10:00:00+00:00",
            "retro_date": "2026-08-01",
            "project_name": "yeaboi",
            "sprint_name": "Sprint 41",
            "card_count": 3,
            "cards": [
                ("went_well", "The tunnel held up for the whole ceremony", "Grace"),
                ("didnt_go_well", "Nobody could find the join code", "Linus"),
                ("action_items", "Put the join code on the invite screen", "Ada"),
            ],
        },
        {
            "id": 1,
            "run_at": "2026-07-18T10:00:00+00:00",
            "retro_date": "2026-07-18",
            "project_name": "yeaboi",
            "sprint_name": "Sprint 40",
            "card_count": 2,
            "cards": [
                ("went_well", "Shipped the first browser board", "Ada"),
                ("action_items", "Add an alert for staging health", "Grace"),
            ],
        },
    ]

    def _listing() -> list[dict]:
        return [{k: v for k, v in run.items() if k != "cards"} for run in past]

    def _one(run_id: int) -> dict | None:
        run = next((r for r in past if r["id"] == run_id), None)
        if run is None:
            return None
        return {
            "date": run["retro_date"],
            "sprint_name": run["sprint_name"],
            "project_name": run["project_name"],
            "participants": sorted({a for _, _, a in run["cards"]}),
            "cards": [
                {
                    "id": f"past-{run['id']}-{i}",
                    "grid": grid,
                    "text": text,
                    "author": author,
                    "created_at": run["run_at"],
                    "origin": "web",
                    "reactions": {},
                    "status": "",
                    "mine": False,
                }
                for i, (grid, text, author) in enumerate(run["cards"])
            ],
            "carried": [],
        }

    server = RetroServer(board, port=get_retro_server_port())
    server.history_list, server.history_report = _listing, _one
    # Fixed credentials, as the poker dev board does it: a rebuild-and-restart
    # must not invalidate the token the tab you are looking at is holding.
    # Dev only — in-memory board, loopback socket.
    server.token = "dev-token"  # noqa: S105
    server.admin_token = "dev-admin"  # noqa: S105
    server.start()
    # There is no tunnel here, so the invite panel has nothing to hand out.
    # SHARE picks which of the four states it renders — `pending` is the honest
    # default, and the others are the only way to see that copy without one.
    share = os.environ.get("SHARE", "pending")
    if share == "ready":
        server.set_public_url("https://dev-board.example.invalid/")
    else:
        server.set_share_state(share)

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
