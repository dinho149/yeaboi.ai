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
    from yeaboi.connectors import custom, legacy

    # A custom connection's kind (api/webhook/mcp) rides its row so a surface
    # can shape the form without sniffing derived env names. Builtins send "".
    custom_kinds = {spec.key: spec.kind for spec in custom.load_specs()}
    # A custom connection's uploaded icon (a validated raster data URI, never
    # SVG). Builtins send "" — their marks ship with the client.
    custom_icons = {spec.key: spec.icon_data for spec in custom.load_specs()}

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
                "kind": custom_kinds.get(connector.key, ""),
                "docs_url": connector.docs_url,
                "glyph": connector.mark,
                "icon": custom_icons.get(connector.key, ""),
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


def create_custom_connection(spec: dict) -> dict:
    """Validate and save one user-created connection; return its catalog row.

    ``spec`` is descriptor JSON only — never a credential; the values are typed
    into the ordinary settings write path afterwards. The validator is the
    gate: any problem raises ValueError carrying every user-facing line.
    """
    from yeaboi.connectors.custom import save_custom, spec_from_dict

    parsed = spec_from_dict(spec if isinstance(spec, dict) else {})
    save_custom(parsed)  # raises ValueError with the problems
    logger.info("connectors: custom connection %s created", parsed.key)
    minted = ""
    if parsed.kind == "webhook":
        # The delivery secret is yeaboi's to mint, not the user's to invent —
        # unguessable by construction, persisted through the masked path, and
        # returned ONCE here (webhook-url can show it again on request).
        env = f"{parsed.env_stem}_WEBHOOK_SECRET"
        if not os.environ.get(env, "").strip():
            from yeaboi.config import apply_config_value
            from yeaboi.connectors.webhooks.server import mint_secret

            minted = mint_secret()
            apply_config_value(env, minted)
            os.environ[env] = minted
    payload = list_connections(connected_only=False, include_legacy=False)
    row = next(row for row in payload["connectors"] if row["key"] == parsed.key)
    if minted:
        row = {**row, "webhook_secret": minted}
    return row


def delete_custom_connection(key: str) -> dict:
    """Remove one user-created connection AND its stored credential values.

    A definition-less credential is an orphan nothing can read or mask by
    name, so the envs are cleared in the same act.
    """
    from yeaboi.config import apply_config_value
    from yeaboi.connectors.custom import delete_custom, spec_by_key

    spec = spec_by_key(str(key or ""))
    if spec is None:
        raise ValueError(f"no custom connection named {key!r}")
    for env in spec.derived_envs():
        apply_config_value(env, "")
        os.environ.pop(env, None)
    delete_custom(spec.key)
    logger.info("connectors: custom connection %s deleted", spec.key)
    return {"deleted": spec.key}


def draft_custom_connection(description: str) -> dict:
    """One LLM pass from a service description to a candidate descriptor.

    The model proposes identity, look and HTTP shape — never env names or
    verify wiring, which are derived. Nothing is saved here: the caller shows
    the draft, the user edits or accepts, and ``create_custom_connection``
    judges it. Returns ``{ok, draft, problems}`` — a draft with problems is a
    pre-filled form, not a dead end.
    """
    import json as _json

    from yeaboi.agent.llm import invoke_json  # See docs: "Agentic Blueprint Reference" — one-shot JSON calls
    from yeaboi.connectors import registry
    from yeaboi.connectors.custom import load_specs, spec_from_dict
    from yeaboi.connectors.validation import descriptor_problems
    from yeaboi.prompts.connector_builder import create_connector_builder_prompt

    response = invoke_json(create_connector_builder_prompt(str(description or "")), temperature=0.0)
    try:
        raw = _json.loads(response.content)
    except (TypeError, ValueError):
        return {"ok": False, "draft": {}, "problems": ["the model did not return a usable draft — try rephrasing"]}
    if not isinstance(raw, dict):
        return {"ok": False, "draft": {}, "problems": ["the model did not return a usable draft — try rephrasing"]}

    draft = spec_from_dict(raw)
    others = load_specs()
    existing_keys = (
        {c.key for c in registry.builtin_connectors()}
        | {c.key for c in registry.legacy_entries()}
        | {s.key for s in others}
    )
    # Builtin + legacy envs by hand — all_envs() reads the merged roster, and a
    # redraft of an existing key would collide with its own derived envs.
    existing_envs = {f.env for c in registry.builtin_connectors() for f in c.fields} | set(registry.legacy_envs())
    for other in others:
        existing_envs |= set(other.derived_envs())
    existing_accents = {c.accent for c in registry.builtin_connectors()} | {c.accent for c in registry.legacy_entries()}
    existing_accents |= {s.accent for s in others}
    problems = descriptor_problems(
        draft,
        existing_keys=frozenset(existing_keys),
        existing_envs=frozenset(existing_envs),
        existing_accents=frozenset(existing_accents),
    )
    logger.info("connectors: drafted %s (%d problem(s))", draft.key or "<unnamed>", len(problems))
    return {"ok": not problems, "draft": draft.to_dict(), "problems": problems}


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
