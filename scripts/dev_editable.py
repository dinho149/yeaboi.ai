"""Run a seeded, correctable standup document for front-end development.

Pairs with ``make web-dev``: this serves the real Python API on :5473 and Vite
proxies ``/api`` to it from :5399, so the editing UI gets HMR against genuine
server responses — real validation, real conflicts, real refusals — instead of
hand-written fixtures.

    make dev-editable       # terminal 1 — prints the URL and the token
    make web-dev            # terminal 2 — Vite on :5399

In memory only. Nothing is written to ``~/.yeaboi`` and no edit log is kept, so
this is safe to run against a real install — which also means every correction
is gone when you stop it.

The token is printed rather than hidden because there is nothing to protect: the
server binds loopback and the document is three invented sentences. Over a
tunnel the same server prints nothing and hands the token out only through the
join code.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from yeaboi.agent.state import ActivityEvidence, MemberUpdate, StandupReport  # noqa: E402
from yeaboi.sharing.documents import editable_share, standup_document  # noqa: E402
from yeaboi.sharing.server import OutputShareServer  # noqa: E402

PORT = 5473

# Enough shape variety that the editing affordances get exercised properly: a
# member with a blocker and one without, prose long enough to wrap, an outlook,
# and evidence rows — which must stay *un*editable, so they are here to be seen
# not to be offered.
REPORT = StandupReport(
    session_id="dev",
    date="2026-08-01",
    sprint_name="Sprint 42",
    sprint_day=6,
    sprint_total_days=10,
    confidence_pct=72,
    confidence_label="On track",
    confidence_rationale="Six of nine stories are done and the two open PRs are both in review.",
    team_summary=(
        "The team landed the tunnel long-polling change and closed out authentication. "
        "Ada is unblocked on staging; Grace is still waiting on a review for the billing work."
    ),
    activity_window="last 24 hours",
    # Timestamped bounds + evidence so the activity timeline has a real day to
    # draw: a burst that clusters, a PR with child commits, a review, an
    # undated WIP row (the "+1 undated" note), and a doc edit.
    activity_window_start="2026-08-01T00:00:00+00:00",
    activity_window_end="2026-08-01T18:00:00+00:00",
    member_updates=(
        MemberUpdate(
            name="Ada Lovelace",
            summary="Landed the login flow and cut the redirect loop that was failing on Safari.",
            blockers="Staging database has been down since Tuesday, so the migration is unverified.",
            outlook="Likely to pick up the session-expiry work once staging is back.",
            progress_note="Finished the login work that was still open yesterday.",
            ticketing_summary="Closed YB-12 and moved YB-14 into review.",
            ticketing_activity_count=2,
            ticketing_evidence=(
                ActivityEvidence(
                    kind="issue",
                    key="YB-12",
                    title="Login redirect loop",
                    status="Done",
                    timestamp="2026-08-01T15:40:00",
                ),
                ActivityEvidence(kind="wip", key="YB-14", title="Session expiry", status="In Progress"),
            ),
            code_summary="Merged the login PR after a morning of fixes.",
            code_activity_count=4,
            code_evidence=(
                ActivityEvidence(
                    kind="pr",
                    key="#91",
                    title="Fix login redirect",
                    url="https://example.invalid/pr/91",
                    repository="yeaboi/web",
                    status="merged",
                    timestamp="2026-08-01T14:05:00",
                    children=(
                        ActivityEvidence(
                            kind="commit",
                            key="aaa1",
                            title="Add redirect guard",
                            url="https://example.invalid/c/aaa1",
                            timestamp="2026-08-01T09:12:00",
                        ),
                        ActivityEvidence(
                            kind="commit",
                            key="bbb2",
                            title="Fix the Safari case",
                            url="https://example.invalid/c/bbb2",
                            timestamp="2026-08-01T09:14:30",
                        ),
                    ),
                ),
            ),
        ),
        MemberUpdate(
            name="Grace Hopper",
            summary="Reviewed three pull requests and started on the billing reconciliation job.",
            outlook="Likely to continue on billing reconciliation.",
            code_summary="Opened one PR against yeaboi/web; reviewed three others.",
            code_activity_count=4,
            code_evidence=(
                ActivityEvidence(
                    kind="review",
                    key="review:91:grace",
                    title="approved PR #91: Fix login redirect",
                    url="https://example.invalid/pr/91?discussionId=7",
                    repository="yeaboi/web",
                    status="approved",
                    timestamp="2026-08-01T11:20:00",
                ),
                ActivityEvidence(
                    kind="commit",
                    key="ccc3",
                    title="Start reconciliation job",
                    url="https://example.invalid/c/ccc3",
                    timestamp="2026-08-01T16:45:00",
                ),
            ),
            documentation_summary="Updated the billing runbook.",
            documentation_activity_count=1,
            documentation_evidence=(
                ActivityEvidence(
                    kind="page",
                    key="1892385692",
                    title="Billing runbook",
                    url="https://example.invalid/wiki/billing",
                    timestamp="2026-08-01T13:05:00",
                ),
            ),
        ),
    ),
    warnings=("Confluence was not reachable — documentation activity is missing from this run.",),
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    share = editable_share(REPORT, kind="standup", ref="standup:dev")
    server = OutputShareServer(
        standup_document(REPORT),
        port=PORT,
        editable=share,
        # No store: corrections live in the document for as long as it runs.
        # That is the point of a dev shell — it must not touch a real database.
        on_edit=lambda _share, edit, _ip: logging.getLogger("dev").info(
            "edit %s %s (%s)", edit.op, edit.path, edit.author or "anonymous"
        ),
    )
    server.start()

    url = f"http://127.0.0.1:{server.port}/?token={server.token}"
    admin = f"{url}&admin={server.admin_token}"
    print()
    print("  Editable standup, seeded and in memory.")
    print(f"    reader : {url}")
    print(f"    host   : {admin}")
    print(f"    via Vite (HMR): http://127.0.0.1:5399/?token={server.token}")
    print()
    print("  Restart this after `make web` — read_asset is lru_cached.")
    print("  Ctrl-C to stop. Nothing is persisted.")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  stopping…")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
