"""Linear — a write-capable tracker, wearing its writes on the descriptor.

One API key, one fixed host. There is no ``fetch``: the ops-event vocabulary
has no delivery kind. What the credential powers instead is the tracker
integration — ``tools/linear.py`` reads the board and ``linear_sync.py`` writes
the epics, stories and cycles an approved sprint plan creates, which is why
``read_only`` is False here.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

API_URL = "https://api.linear.app/graphql"

CONNECTOR = Connector(
    key="linear",
    label="Linear",
    family="delivery",
    section="connections",
    summary="The tracker sprint plans sync to — projects, issues and cycles",
    detail=(
        "yeaboi reads your teams, projects, issues and cycles to ground "
        "planning in what the board already holds, and writes only what a "
        "sprint plan you approved creates. It never deletes anything, never "
        "changes a state another tool set, and never reads comments."
    ),
    verify="_verify_linear",
    docs_url="https://linear.app/docs/api-and-webhooks",
    glyph="\U0001f4d0",  # 📐 — linear geometry
    accent="rgb(94,106,210)",
    read_only=False,
    fields=(
        ConnectorField(
            env="LINEAR_API_KEY",
            label="API Key",
            secret=True,
            verify_arg="token",
            help_url="https://linear.app/settings/account/security",
            help_scope="A personal API key — create it under Security & access",
        ),
        ConnectorField(
            env="LINEAR_TEAM_KEY",
            label="Team Key",
            required=False,
            placeholder="ENG",
            hint="The ENG in ENG-123 — only needed when the workspace has several teams",
        ),
    ),
    connected_when=("LINEAR_API_KEY",),
)
