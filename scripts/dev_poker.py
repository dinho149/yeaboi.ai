"""Run a seeded planning-poker board for front-end development.

The sibling of ``dev_board.py``. Serves the real Python API so the TS runs
against genuine server responses — including the phase machine, vote secrecy
and the duel — rather than hand-written fixtures.

    make dev-poker          # prints the URLs

The board is in-memory only. Nothing is written to ``~/.yeaboi``, so this is
safe to run against a real install. The tickets are fake but shaped like real
tracker rows: a long description, acceptance criteria, one already estimated,
and one with nothing but a summary.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from yeaboi.poker.board import PokerBoard  # noqa: E402
from yeaboi.poker.server import PokerServer  # noqa: E402

SEED: list[dict] = [
    {
        "key": "YB-101",
        "summary": "Long-poll the board so remote updates land in under a second",
        "description_text": (
            "Server-sent events buffer at the Cloudflare edge, so a teammate on the tunnel sees "
            "nothing until the connection is torn down. Replace the stream with a held GET that "
            "the change watcher releases, using the ETag as the cursor.\n\n"
            "This has to degrade to an ordinary conditional GET when the hold slots are full, or "
            "a busy board starves the last person to join."
        ),
        "acceptance_text": (
            "- A card added by A appears for B within one second, over the tunnel\n"
            "- A client that is already behind is answered immediately, not parked\n"
            "- No busy-polling when nothing is changing"
        ),
        "type": "Story",
        "state": "To Do",
        "assignee": "Ada",
        "url": "https://example.invalid/browse/YB-101",
        "story_points": None,
        "source": "jira",
    },
    {
        "key": "YB-102",
        "summary": "Quantise the duck sprites so they fit in an export",
        "description_text": "199 KB of source art will not inline into ten static reports.",
        "type": "Task",
        "state": "In Progress",
        "assignee": "Grace",
        "url": "https://example.invalid/browse/YB-102",
        "story_points": 3,
        "source": "jira",
    },
    {
        "key": "YB-103",
        "summary": "Audit contrast across every theme and mode",
        "type": "Task",
        "state": "Done",
        "assignee": "Linus",
        "story_points": 5,
        "source": "jira",
    },
    {"key": "YB-104", "summary": "Delete the hand-written poker page"},
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    board = PokerBoard("dev-poker", project_name="yeaboi", source="jira", scope_label="Sprint 42", tickets=SEED)
    # One ticket already estimated, so the rail's done state and the progress
    # count are visible without having to run a round first. The full sequence
    # is required: `finalize_current` refuses outside the revealed phase, so a
    # seed that skips the vote and the reveal silently estimates nothing.
    board.goto_ticket(2)
    board.heartbeat("dev-ada", name="Ada", avatar="🦊")
    board.cast_vote("dev-ada", "5")
    board.reveal()
    board.finalize_current(5.0)
    board.goto_ticket(0)

    # Two seated teammates, so the table is not empty on first load. Their
    # heartbeats expire after a few seconds — that is real presence behaviour,
    # not a bug in the seed.
    board.heartbeat("dev-ada", name="Ada", avatar="🦊")
    board.heartbeat("dev-grace", name="Grace", avatar="🐙")
    board.cast_vote("dev-ada", "3")

    server = PokerServer(board)
    # Fixed credentials, overwritten before start() binds anything.
    #
    # A real board mints a fresh token, admin secret and join code per session,
    # which is right — they are the access control. In development the server
    # gets restarted every time the bundles are rebuilt, and a new token each
    # time silently kills every tab that is already open: `loadSession` strips
    # the credentials out of the address bar after boot, so a reload falls back
    # to the token in sessionStorage, which the restart just invalidated. The
    # board then polls forever against a 403, which the stream reports as
    # `retrying` — an empty board reading "reconnecting…" with nothing to say
    # that the session is over rather than the network down.
    #
    # Safe only because of what this script is: an in-memory board on a
    # loopback socket with four fake tickets, never a real one, never tunnelled.
    # S105 is right about the shape and wrong about the risk here — see above.
    server.token = "dev-token"  # noqa: S105
    server.admin_token = "dev-admin"  # noqa: S105
    server.join_code = "DEVB-OARD"  # the XXXX-XXXX shape /api/join compares against
    server.start()

    api = f"http://127.0.0.1:{server.port}"
    print("\n  dev poker ready")
    print(f"    host       {server.url}")
    print(f"    guest      {api}/?token={server.token}")
    print(f"    join code  {server.display_code}")
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
