"""Which connectors exist, and which of them this machine has.

The generalisation of ``analysis/setup.py``'s probes, and it keeps their
contract exactly: these functions answer *what is configured*, never *what
shall we read*. Selection is a caller's job.

``connected()`` is the whole of "hidden until connected" — a surface that lists
only what this returns cannot show a vendor the user has never heard of.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from yeaboi.connectors import (
    aws,
    azure_cloud,
    bitbucket,
    datadog,
    gcp,
    gitlab,
    grafana,
    incidentio,
    linear,
    pagerduty,
    sentry,
    trello,
)
from yeaboi.connectors.spec import FAMILY_ORDER, Connector

logger = logging.getLogger(__name__)

#: Every connector, in catalog order. New vendors are appended here and nowhere
#: else — the settings fields, the verify table and the secret lists all derive.
_CONNECTORS: tuple[Connector, ...] = (
    datadog.CONNECTOR,
    grafana.CONNECTOR,
    pagerduty.CONNECTOR,
    incidentio.CONNECTOR,
    sentry.CONNECTOR,
    aws.CONNECTOR,
    gcp.CONNECTOR,
    azure_cloud.CONNECTOR,
    gitlab.CONNECTOR,
    bitbucket.CONNECTOR,
    linear.CONNECTOR,
    trello.CONNECTOR,
)


def all_connectors() -> tuple[Connector, ...]:
    """Every connector, ordered by family then label."""
    order = {family: i for i, family in enumerate(FAMILY_ORDER)}
    return tuple(sorted(_CONNECTORS, key=lambda c: (order.get(c.family, len(order)), c.label.lower())))


def by_key(key: str) -> Connector | None:
    return next((c for c in _CONNECTORS if c.key == key), None)


def chosen_method(connector: Connector, values: Mapping[str, str] | None = None):
    """The auth method in force, or ``None`` for a connector that has one way in.

    Falls back to the recommended method when nothing is chosen, so a surface
    always has something to render — but see :func:`required_envs`: an unchosen
    method is never *connected*, because ``auth_env`` is itself required.
    """
    if not connector.auth_methods:
        return None
    read = os.environ if values is None else values
    return connector.method(str(read.get(connector.auth_env, "") or "").strip()) or connector.default_method


def required_envs(connector: Connector, values: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The envs that decide whether this connector is connected, here and now.

    ``values`` lets a surface holding its own snapshot ask the same question the
    environment answers — the settings screen renders from ``config_data``, and
    two resolvers would be two answers.
    """
    method = chosen_method(connector, values)
    if method is None:
        return connector.required_envs
    return connector.envs_for(method.key)


def is_connected(connector: Connector, values: Mapping[str, str] | None = None) -> bool:
    """Whether every env this connector needs is set."""
    read = os.environ if values is None else values
    return all(str(read.get(env, "") or "").strip() for env in required_envs(connector, values))


def connected(family: str = "") -> list[str]:
    """The keys of connectors whose credentials are present, in catalog order."""
    return [c.key for c in all_connectors() if (not family or c.family == family) and is_connected(c)]


def any_fetchable() -> bool:
    """Whether any connector has both credentials and something to gather.

    Every mode that reads ops asks this *before* announcing a progress step: a
    user with no ops vendor must not watch a phase go by for work that is not
    happening. Costs one walk of the descriptors and no network.
    """
    return any(c.fetch and is_connected(c) for c in _CONNECTORS)


def by_family() -> dict[str, list[Connector]]:
    """Connectors grouped by family, families in render order, empties dropped."""
    grouped: dict[str, list[Connector]] = {}
    for connector in all_connectors():
        grouped.setdefault(connector.family, []).append(connector)
    return grouped


def secret_envs() -> frozenset[str]:
    """Every secret env any connector declares — the masking source of truth."""
    return frozenset(env for c in _CONNECTORS for env in c.secret_envs)


def all_envs() -> tuple[str, ...]:
    """Every env any connector reads, in descriptor order."""
    return tuple(f.env for c in all_connectors() for f in c.fields)


def connection_kinds() -> dict[str, tuple[tuple[str, str], ...]]:
    """``verify_connection``'s table, derived.

    Field ORDER is load-bearing — ``verify_connection`` iterates it to resolve
    values — so this preserves descriptor order rather than sorting.
    """
    return {
        c.key: tuple((f.verify_arg, f.fallback_env or f.env) for f in c.fields if f.verify_arg)
        for c in all_connectors()
        if c.verify
    }


def accents() -> tuple[str, ...]:
    """The connector keys the front end must own a ``[data-connector]`` block for."""
    return tuple(c.key for c in all_connectors())
