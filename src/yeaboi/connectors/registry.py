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

from yeaboi.connectors import datadog, grafana, incidentio, pagerduty, sentry
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
)


def all_connectors() -> tuple[Connector, ...]:
    """Every connector, ordered by family then label."""
    order = {family: i for i, family in enumerate(FAMILY_ORDER)}
    return tuple(sorted(_CONNECTORS, key=lambda c: (order.get(c.family, len(order)), c.label.lower())))


def by_key(key: str) -> Connector | None:
    return next((c for c in _CONNECTORS if c.key == key), None)


def is_connected(connector: Connector) -> bool:
    """Whether every env this connector needs is set."""
    return all(os.environ.get(env, "").strip() for env in connector.required_envs)


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
