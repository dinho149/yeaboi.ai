"""Trello — a write-capable tracker, wearing its writes on the descriptor.

Trello authenticates with an API key *pair* riding the query string, which is
why nothing here — or in any surface that renders a probe result — may ever log
a request URL. There is no ``fetch`` for the same reason as Linear: the
ops-event vocabulary has no delivery kind. What the credentials power is the
tracker integration — ``tools/trello.py`` reads the board and
``trello_sync.py`` writes the label, cards and lists an approved sprint plan
creates, which is why ``read_only`` is False here.
"""

from __future__ import annotations

from yeaboi.connectors.spec import Connector, ConnectorField

API_BASE = "https://api.trello.com/1"

CONNECTOR = Connector(
    key="trello",
    label="Trello",
    family="delivery",
    section="connections",
    summary="The tracker sprint plans sync to — a label, cards and a list per sprint",
    detail=(
        "yeaboi reads your boards, lists and cards to ground planning in what "
        "the board already holds, and writes only what a sprint plan you "
        "approved creates. It never deletes or archives anything, and never "
        "reads comments."
    ),
    verify="_verify_trello",
    docs_url="https://developer.atlassian.com/cloud/trello/guides/rest-api/api-introduction/",
    accent="rgb(0,121,191)",
    read_only=False,
    connected_when=("TRELLO_API_KEY", "TRELLO_TOKEN"),
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
        ConnectorField(
            env="TRELLO_BOARD_ID",
            label="Board",
            required=False,
            placeholder="board id or name",
            hint="Only needed when the account has several open boards",
        ),
    ),
)
