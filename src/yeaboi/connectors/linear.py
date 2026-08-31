"""Linear — a delivery tracker in the catalog, verified but not yet gathered.

One API key, one fixed host. There is no ``fetch``: the ops-event vocabulary
has no delivery kind, and inventing one is a design decision, not a connector's
side effect. The descriptor is what the tracker integration builds on — sprint
sync reads these same credentials.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

API_URL = "https://api.linear.app/graphql"

CONNECTOR = Connector(
    key="linear",
    label="Linear",
    family="delivery",
    section="connections",
    summary="Your Linear workspace, verified and ready for planning to build on",
    detail=(
        "yeaboi verifies the key can reach your workspace. Reading teams, "
        "projects, issues and cycles — and syncing an approved sprint plan to "
        "them — is the tracker integration this credential unlocks. It never "
        "reads comments."
    ),
    verify="_verify_linear",
    docs_url="https://linear.app/docs/api-and-webhooks",
    accent="rgb(94,106,210)",
    fields=(
        ConnectorField(
            env="LINEAR_API_KEY",
            label="API Key",
            secret=True,
            verify_arg="token",
            help_url="https://linear.app/settings/account/security",
            help_scope="A personal API key — create it under Security & access",
        ),
    ),
)
