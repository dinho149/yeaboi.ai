"""Trello — a delivery tracker in the catalog, verified but not yet gathered.

Trello authenticates with an API key *pair* riding the query string, which is
why nothing here — or in any surface that renders a probe result — may ever log
a request URL. There is no ``fetch`` for the same reason as Linear: the
ops-event vocabulary has no delivery kind.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

API_BASE = "https://api.trello.com/1"

CONNECTOR = Connector(
    key="trello",
    label="Trello",
    family="delivery",
    section="connections",
    summary="Your Trello boards, verified and ready for planning to build on",
    detail=(
        "yeaboi verifies the key and token can reach Trello. Reading boards, "
        "lists and cards — and syncing an approved sprint plan to them — is "
        "the tracker integration this credential unlocks. It never reads "
        "comments."
    ),
    verify="_verify_trello",
    docs_url="https://developer.atlassian.com/cloud/trello/guides/rest-api/api-introduction/",
    accent="rgb(0,121,191)",
    fields=(
        ConnectorField(
            env="TRELLO_API_KEY",
            label="API Key",
            verify_arg="api_key",
            help_url="https://trello.com/power-ups/admin",
            help_scope="From a Power-Up's API key page — the key names the app, the token grants access",
        ),
        ConnectorField(
            env="TRELLO_TOKEN",
            label="API Token",
            secret=True,
            verify_arg="token",
            help_url="https://trello.com/power-ups/admin",
            help_scope="Generate a token from the same page, scoped read/write to your boards",
        ),
    ),
)
