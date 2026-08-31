"""The connector catalog, as every surface reads it.

Deliberately ONE public entry point: ``test_surface_parity`` globs
``*/engine.py`` and forces every public name here into the capability registry,
so the query helpers live in ``registry.py`` and the shapes in ``spec.py``.

Verification is deliberately absent. ``settings.engine.verify_connection`` is
already registered on every surface and already owns the credential semantics
(stored-value fallback, the exfiltration guard, https-only); once its table is
registry-derived, a new connector is verifiable everywhere for free.

The second entry point, :func:`fetch_ops_events`, is the read side of the same
capability: the catalog says what is connected, this says what it saw.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import datetime

from yeaboi.connectors import registry
from yeaboi.connectors.spec import FAMILY_LABELS, FAMILY_ORDER

logger = logging.getLogger(__name__)


def list_connections(*, family: str = "", connected_only: bool = True, include_legacy: bool = False) -> dict:
    """The connector catalog: what exists, what is connected, and what it needs.

    Never returns a credential — a field reports ``is_set`` and nothing more, so
    this payload is safe on any surface, including one an agent can read.

    ``connected_only`` defaults to True: that default IS "hidden until
    connected". Pass False for the "add a connection" picker, which is the one
    place a user has asked to see everything. ``include_legacy`` (honoured only
    there) adds the pre-connector integrations as ``managed_by:"credentials"``
    rows, so a catalog can show the whole roster while a connect form knows to
    hand those to Credentials/setup instead of rendering fields.
    """
    from yeaboi.connectors import legacy

    connectors = registry.all_connectors()
    if not connected_only and include_legacy:
        connectors = connectors + registry.legacy_entries()
    if family:
        connectors = tuple(c for c in connectors if c.family == family)

    rows = []
    for connector in connectors:
        is_legacy = legacy.by_key(connector.key) is connector
        linked = legacy.is_connected(connector) if is_legacy else registry.is_connected(connector)
        if connected_only and not linked:
            continue
        rows.append(
            {
                "key": connector.key,
                "label": connector.label,
                "summary": connector.summary,
                "detail": connector.detail,
                "family": connector.family,
                "family_label": FAMILY_LABELS.get(connector.family, connector.family.title()),
                "section": connector.section,
                "connected": linked,
                "read_only": connector.read_only,
                # Where configuring happens: "connections" rows carry their own
                # add flow; "credentials" rows deep-link to Credentials/setup.
                "managed_by": "credentials" if is_legacy else "connections",
                "docs_url": connector.docs_url,
                "glyph": connector.mark,
                "accent": connector.accent,
                "verify_kind": _verify_kind(connector, is_legacy),
                # The ways in, and which one is in force. A connector with one
                # way sends an empty list and no selector, so a surface that
                # ignores these keys renders exactly as it did before.
                "auth_env": connector.auth_env,
                "auth_methods": [
                    {
                        "key": m.key,
                        "label": m.label,
                        "summary": m.summary,
                        "recommended": m.recommended,
                        "warning": m.warning,
                        "setup_url": m.setup_url,
                        "envs": list(m.envs),
                    }
                    for m in connector.auth_methods
                ],
                "fields": [
                    {
                        "env": f.env,
                        "label": f.label,
                        "secret": f.secret,
                        "required": f.required,
                        "is_set": bool(os.environ.get(f.env, "").strip()),
                        "choices": list(f.choices),
                        "default": f.default,
                        "placeholder": f.placeholder,
                        "hint": f.hint,
                        "help_url": f.help_url,
                        "help_scope": f.help_scope,
                        "auth_method": f.auth_method,
                    }
                    for f in connector.fields
                ],
            }
        )

    families = [
        {"key": name, "label": FAMILY_LABELS.get(name, name.title())}
        for name in FAMILY_ORDER
        if any(row["family"] == name for row in rows)
    ]
    logger.info("connectors: catalog listed %d connector(s), connected_only=%s", len(rows), connected_only)
    return {"connectors": rows, "families": families, "connected": registry.connected(family)}


def _verify_kind(connector, is_legacy: bool) -> str:
    """The ``verify_connection`` kind for a row, or ``""`` when nothing probes.

    Legacy kinds live in ``settings/engine``'s hand-written table rather than on
    the descriptor; :data:`~yeaboi.connectors.legacy.LEGACY_VERIFY_KINDS` names
    which entries that table covers.
    """
    from yeaboi.connectors.legacy import LEGACY_VERIFY_KINDS

    if is_legacy:
        return connector.key if connector.key in LEGACY_VERIFY_KINDS else ""
    return connector.key if connector.verify else ""


def fetch_ops_events(key: str = "", *, since: str = "14d", now: datetime | None = None) -> dict:
    """What production did over a window, as bounded events and rolled-up signals.

    ``key`` narrows to one connector; empty means every connected one that has
    something to gather. A connector that fails is reported as a failed source
    rather than raising — one vendor being down must not lose the other four.

    The payload carries identifiers, words, timestamps and URLs. No credential,
    and no field capable of holding a stack trace, a log line or a metric
    series: that guarantee is :class:`~yeaboi.ops.events.OpsEvent`'s shape, not
    a rule this function applies.

    The gathering itself lives in :func:`yeaboi.connectors.fetching.gather`,
    which returns the typed form an in-process caller wants; this is the wire
    shaping over it.
    """
    from yeaboi.connectors.fetching import gather

    result = gather(key, since=since, now=now)
    return {
        "window": {"since": result.since, "start": result.window_start, "end": result.window_end},
        "sources": [asdict(s) for s in result.sources],
        "events": [asdict(e) for e in result.events],
        "signals": [asdict(s) for s in result.signals],
    }
