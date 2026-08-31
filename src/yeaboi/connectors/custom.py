"""User-created connections: the descriptor store and the generic drivers.

A custom connection is DATA, never code: a :class:`CustomSpec` saved in
``~/.yeaboi/data/custom_connectors.json``, rendered everywhere through the same
frozen :class:`~yeaboi.connectors.spec.Connector` the built-ins use. Three
rules keep it inside the layer's guarantees:

- **Env names are derived, never authored.** A custom field's env is
  ``YEABOI_CUSTOM_<KEY>_<FIELD>`` by construction, so the settings
  write-allowlist and the masking table cannot be aimed at another
  connector's credential. The descriptor file itself never holds a value.
- **Every request rides the shared HTTP guard.** The probe goes through
  ``provider_verification._verify_custom_api`` and the fetch through
  :func:`fetch_events`, both on :mod:`yeaboi.connectors.http` — SSRF guard and
  redaction inherited, not reimplemented.
- **The validator is the gate.** Nothing reaches the file while
  :func:`yeaboi.connectors.validation.descriptor_problems` returns anything.

Stdlib-only at import time, like ``spec.py`` — the registry merges this module
on the startup path.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass

from yeaboi.connectors.spec import FAMILIES, Connector, ConnectorField

logger = logging.getLogger(__name__)

FILE_VERSION = 1

_ENV_PREFIX = "YEABOI_CUSTOM_"


@dataclass(frozen=True)
class EventsMapping:
    """Where a connection's events endpoint is, and how its rows become OpsEvents.

    Every ``*_path`` is a dot path into one JSON row. ``kind`` is fixed per
    descriptor and validated against the closed vocabulary — a mapping cannot
    invent an event kind.
    """

    path: str = ""
    items_key: str = ""
    kind: str = "alert"
    title_path: str = ""
    ref_path: str = ""
    severity_path: str = ""
    status_path: str = ""
    url_path: str = ""
    started_at_path: str = ""
    service_path: str = ""


@dataclass(frozen=True)
class CustomSpec:
    """One user-created connection, as saved. Never carries a credential."""

    key: str
    label: str
    family: str = "observability"
    summary: str = ""
    detail: str = ""
    docs_url: str = ""
    glyph: str = ""
    accent: str = ""
    kind: str = "api"
    auth_scheme: str = "bearer"
    header_name: str = ""
    probe_path: str = "/"
    probe_ok_status: int = 200
    events: EventsMapping | None = None
    created_at: str = ""

    @property
    def env_stem(self) -> str:
        """``custom_statuspage`` → ``YEABOI_CUSTOM_STATUSPAGE``."""
        bare = self.key.removeprefix("custom_")
        return _ENV_PREFIX + re.sub(r"[^A-Za-z0-9]", "_", bare).upper()

    def derived_envs(self) -> tuple[str, ...]:
        """Every env this connection reads — derived, never authored."""
        envs = [f"{self.env_stem}_BASE_URL"]
        if self.auth_scheme == "basic":
            envs += [f"{self.env_stem}_USERNAME", f"{self.env_stem}_PASSWORD"]
        else:
            envs.append(f"{self.env_stem}_TOKEN")
        return tuple(envs)

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.events is None:
            data.pop("events")
        return data


def spec_from_dict(raw: dict) -> CustomSpec:
    """A CustomSpec from stored/submitted JSON, unknown keys dropped.

    Shapes only — validity is :func:`~yeaboi.connectors.validation.descriptor_problems`'
    job, so a draft can round-trip here before it is judged.
    """
    events = None
    if isinstance(raw.get("events"), dict):
        known = {f.name for f in EventsMapping.__dataclass_fields__.values()}
        events = EventsMapping(**{k: v for k, v in raw["events"].items() if k in known})
    known = {f.name for f in CustomSpec.__dataclass_fields__.values()} - {"events"}
    kwargs = {k: v for k, v in raw.items() if k in known}
    kwargs.setdefault("key", "")
    kwargs.setdefault("label", "")
    try:
        kwargs["probe_ok_status"] = int(kwargs.get("probe_ok_status", 200))
    except (TypeError, ValueError):
        kwargs["probe_ok_status"] = 0
    return CustomSpec(events=events, **kwargs)


def to_connector(spec: CustomSpec) -> Connector:
    """The Connector every surface renders — fields derived, never authored.

    ``base_url`` is a required ``verify_arg`` exactly like Grafana's: the
    stored-token exfiltration guard in ``verify_connection`` refuses to pair a
    caller-supplied host with a stored credential, so the request path is
    already covered.
    """
    fields = [
        ConnectorField(
            env=f"{spec.env_stem}_BASE_URL",
            label="Base URL",
            verify_arg="base_url",
            placeholder="https://api.example.com",
            hint="https only — private and local addresses are refused",
        )
    ]
    if spec.auth_scheme == "basic":
        fields.append(ConnectorField(env=f"{spec.env_stem}_USERNAME", label="Username", verify_arg="username"))
        fields.append(
            ConnectorField(env=f"{spec.env_stem}_PASSWORD", label="Password", secret=True, verify_arg="password")
        )
    else:
        fields.append(ConnectorField(env=f"{spec.env_stem}_TOKEN", label="Token", secret=True, verify_arg="token"))
    has_events = spec.events is not None and bool(spec.events.path)
    return Connector(
        key=spec.key,
        label=spec.label,
        family=spec.family if spec.family in FAMILIES else "observability",
        section="connections",
        summary=spec.summary,
        detail=spec.detail,
        verify="_verify_custom_api",
        fetch="fetch_events" if has_events else "",
        fetch_module="yeaboi.connectors.custom" if has_events else "",
        docs_url=spec.docs_url,
        glyph=spec.glyph,
        accent=spec.accent,
        fields=tuple(fields),
    )


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

_cache: dict = {"mtime": None, "specs": (), "connectors": ()}


def _store_path():
    from yeaboi.paths import get_custom_connectors_path

    return get_custom_connectors_path()


def invalidate() -> None:
    """Drop the cache — called after every save/delete, and by tests."""
    _cache.update({"mtime": None, "specs": (), "connectors": ()})


def load_specs() -> tuple[CustomSpec, ...]:
    """Every saved CustomSpec, tolerant of a damaged file.

    A malformed file or entry is a warning and a skip, never a crash — the
    catalog must render whatever else the user has (the reporting-themes
    precedent).
    """
    path = _store_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return ()
    if _cache["mtime"] == mtime:
        return _cache["specs"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("custom connectors: %s is unreadable — ignoring it", path.name)
        return ()
    if not isinstance(raw, dict) or raw.get("version") != FILE_VERSION:
        logger.warning("custom connectors: %s has an unknown shape — ignoring it", path.name)
        return ()
    specs = []
    for entry in raw.get("connectors", []):
        if not isinstance(entry, dict):
            continue
        spec = spec_from_dict(entry)
        if spec.key and spec.label:
            specs.append(spec)
        else:
            logger.warning("custom connectors: skipped an entry with no identity")
    _cache.update({"mtime": mtime, "specs": tuple(specs), "connectors": tuple(to_connector(s) for s in specs)})
    return _cache["specs"]


def load_custom() -> tuple[Connector, ...]:
    """The saved custom connections as Connectors, cache shared with load_specs."""
    load_specs()
    return _cache["connectors"]


def spec_by_key(key: str) -> CustomSpec | None:
    return next((s for s in load_specs() if s.key == key), None)


def _write(specs: tuple[CustomSpec, ...]) -> None:
    path = _store_path()
    payload = {"version": FILE_VERSION, "connectors": [s.to_dict() for s in specs]}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    invalidate()
    from yeaboi.settings.engine import _invalidate_fields_cache

    _invalidate_fields_cache()


def save_custom(spec: CustomSpec) -> None:
    """Validate and persist one new connection. Raises ValueError with the problems."""
    from yeaboi.connectors import registry
    from yeaboi.connectors.validation import descriptor_problems

    others = tuple(s for s in load_specs() if s.key != spec.key)
    existing_keys = (
        {c.key for c in registry.builtin_connectors()}
        | {c.key for c in registry.legacy_entries()}
        | {s.key for s in others}
    )
    existing_envs = set(registry.all_envs()) | set(registry.legacy_envs())
    for other in others:
        existing_envs |= set(other.derived_envs())
    existing_accents = {c.accent for c in registry.builtin_connectors()} | {c.accent for c in registry.legacy_entries()}
    existing_accents |= {s.accent for s in others}

    problems = descriptor_problems(
        spec,
        existing_keys=frozenset(existing_keys),
        existing_envs=frozenset(existing_envs),
        existing_accents=frozenset(existing_accents),
    )
    if problems:
        raise ValueError("; ".join(problems))
    _write((*others, spec))
    logger.info("custom connectors: saved %s", spec.key)


def delete_custom(key: str) -> bool:
    """Remove one connection's descriptor. Returns False when it never existed."""
    specs = load_specs()
    kept = tuple(s for s in specs if s.key != key)
    if len(kept) == len(specs):
        return False
    _write(kept)
    logger.info("custom connectors: deleted %s", key)
    return True


# ---------------------------------------------------------------------------
# The generic fetch driver (the api kind's events endpoint)
# ---------------------------------------------------------------------------


def _dig(row: dict, dotted: str):
    """Follow a dot path into one row; None the moment anything is missing."""
    value = row
    for part in (dotted or "").split("."):
        if not part:
            return None
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def auth_headers(spec: CustomSpec, values=None) -> dict[str, str]:
    """The request headers this connection's scheme calls for, values from env."""
    import base64

    read = os.environ if values is None else values
    if spec.auth_scheme == "basic":
        user = str(read.get(f"{spec.env_stem}_USERNAME", "") or "")
        password = str(read.get(f"{spec.env_stem}_PASSWORD", "") or "")
        b64 = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {b64}"}
    token = str(read.get(f"{spec.env_stem}_TOKEN", "") or "")
    if spec.auth_scheme == "header":
        return {spec.header_name: token}
    return {"Authorization": f"Bearer {token}"}


def fetch_events(connector: Connector, window_start, window_end) -> tuple:
    """The one driver every api-kind custom connection gathers through.

    One GET of the declared events endpoint, rows through the declared dot
    paths, out as OpsEvents — whose shape, not this function, is what keeps a
    body from crossing. ``gather`` re-filters the window, so the endpoint need
    not support one.
    """
    from yeaboi.connectors.fetching import PAGE_LIMIT, env, read_json, rows
    from yeaboi.ops.events import OpsEvent, clean_severity, clean_title, iso, parse_ts

    spec = spec_by_key(connector.key)
    if spec is None or spec.events is None:
        return ()
    base = env(f"{spec.env_stem}_BASE_URL").rstrip("/")
    body = read_json(f"{base}{spec.events.path}", headers=auth_headers(spec), source=spec.key)

    events = []
    for row in rows(body, spec.events.items_key)[:PAGE_LIMIT]:
        title = clean_title(str(_dig(row, spec.events.title_path) or ""))
        if not title:
            continue  # a row with no name is a row a mode cannot reason about
        url = str(_dig(row, spec.events.url_path) or "")
        events.append(
            OpsEvent(
                kind=spec.events.kind,
                source=spec.key,
                ref=str(_dig(row, spec.events.ref_path) or ""),
                title=title,
                service=str(_dig(row, spec.events.service_path) or ""),
                severity=clean_severity(str(_dig(row, spec.events.severity_path) or "")),
                status=str(_dig(row, spec.events.status_path) or ""),
                started_at=iso(parse_ts(str(_dig(row, spec.events.started_at_path) or ""))),
                # A mapped URL is where a human clicks — anything but https is dropped.
                url=url if url.startswith("https://") else "",
            )
        )
    return tuple(events)
