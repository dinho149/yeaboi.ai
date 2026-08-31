"""What every fetcher shares: the environment, the guard, and one page.

A fetcher reads its credentials from the environment itself and never takes
them from a caller — the same rule ``env_arg`` enforces for verification, one
level up. Every request goes through :mod:`yeaboi.connectors.http`, so the SSRF
guard and the redaction path are inherited rather than reimplemented per vendor.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from yeaboi.ops.events import OpsEvent
from yeaboi.ops.signals import OpsSignal

logger = logging.getLogger(__name__)

#: How many rows a single fetch will take from one vendor. Ops feeds counts,
#: and a count that needed ten pages is already a signal on its own.
PAGE_LIMIT = 100


class FetchError(RuntimeError):
    """A fetch that could not complete. The message is already redacted."""


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def read_json(url: str, *, headers: dict[str, str], source: str) -> dict | list:
    """``GET`` a guarded URL and return its decoded body, or raise ``FetchError``.

    Status and byte count are logged; the URL is not, because a query string can
    carry a token on vendors that accept one there.
    """
    from yeaboi.connectors.http import UnsafeUrlError, get_json
    from yeaboi.provider_verification import _connection_error

    try:
        resp = get_json(url, headers=headers)
    except UnsafeUrlError as exc:
        raise FetchError(str(exc)) from None
    except Exception as exc:
        raise FetchError(_connection_error(exc)) from None

    # The vendor is named by the payload row and by every surface that renders
    # one, so the message says what went wrong and nothing else.
    if resp.status_code in (401, 403):
        raise FetchError(f"credentials rejected — re-run `yeaboi connections verify {source}`")
    if resp.status_code == 429:
        raise FetchError("rate limited — try a shorter window")
    if resp.status_code != 200:
        raise FetchError(f"unexpected response {resp.status_code}")

    try:
        body = resp.json()
    except Exception:
        raise FetchError("response was not JSON") from None
    logger.info("ops: fetched %s (%d bytes)", source, len(resp.content or b""))
    return body


def rows(body: dict | list, key: str) -> list[dict]:
    """The list under ``key`` (or the body itself when it is already one).

    Anything that is not a list of dicts becomes an empty list: a vendor that
    changed its shape must yield no events, never a traceback mid-standup.
    """
    found = body.get(key) if isinstance(body, dict) else body
    if not isinstance(found, list):
        return []
    return [row for row in found if isinstance(row, dict)]


@dataclass(frozen=True)
class SourceResult:
    """What one connector contributed to a gather, including a failure."""

    key: str = ""
    label: str = ""
    family: str = ""
    ok: bool = False
    error: str = ""  # already redacted; "" when ok
    count: int = 0


@dataclass(frozen=True)
class Gathered:
    """A window, what each connector said about it, and the result."""

    since: str = ""
    window_start: str = ""  # ISO 8601, UTC
    window_end: str = ""
    sources: tuple[SourceResult, ...] = ()
    events: tuple[OpsEvent, ...] = ()
    signals: tuple[OpsSignal, ...] = ()

    @property
    def failures(self) -> tuple[SourceResult, ...]:
        return tuple(s for s in self.sources if not s.ok)


def gather(
    key: str = "",
    *,
    since: str = "14d",
    window: tuple[datetime, datetime] | None = None,
    now: datetime | None = None,
) -> Gathered:
    """Read every connected connector that has something to gather.

    ``key`` narrows to one; empty means all of them. A connector that fails
    becomes a failed :class:`SourceResult` rather than an exception — one
    vendor being down must not lose the other four.

    ``since`` is a lookback from now and cannot express a window that *ended* in
    the past, which is exactly what a report on a finished sprint needs; such a
    caller passes ``window`` instead and ``since`` is left blank on the result
    rather than being back-computed into a spec nobody asked for.

    The window is resolved first, so a bad spec raises before any request
    leaves. Events are re-filtered here rather than trusted from each vendor:
    two of the five filter client-side anyway, and one authoritative place beats
    five.
    """
    import importlib

    from yeaboi.connectors import registry
    from yeaboi.ops.events import parse_window, within
    from yeaboi.ops.signals import roll_up

    if window is not None:
        window_start, window_end = window
        if window_start > window_end:
            raise ValueError("window start must not be after its end")
        since = ""
    else:
        window_start, window_end = parse_window(since, now=now)

    if key:
        connector = registry.by_key(key)
        if connector is None:
            raise ValueError(f"unknown connector {key!r}")
        if not connector.fetch:
            raise ValueError(f"{connector.label} has nothing to gather yet")
        targets = (connector,)
    else:
        targets = tuple(c for c in registry.all_connectors() if c.fetch and registry.is_connected(c))

    sources: list[SourceResult] = []
    collected: list[OpsEvent] = []
    for connector in targets:
        base = {"key": connector.key, "label": connector.label, "family": connector.family}
        if not registry.is_connected(connector):
            sources.append(SourceResult(**base, error=f"{connector.label} is not connected"))
            continue
        module = importlib.import_module(f"yeaboi.connectors.{connector.key}")
        try:
            found = getattr(module, connector.fetch)(window_start, window_end)
        except Exception as exc:
            # FetchError messages are already redacted; anything else is turned
            # into one rather than trusted to be free of a credential.
            from yeaboi.provider_verification import _connection_error

            message = str(exc) if isinstance(exc, FetchError) else _connection_error(exc)
            logger.warning("ops: %s fetch failed", connector.key)
            sources.append(SourceResult(**base, error=message))
            continue
        kept = tuple(e for e in found if isinstance(e, OpsEvent) and within(e, window_start, window_end))
        collected.extend(kept)
        sources.append(SourceResult(**base, ok=True, count=len(kept)))

    families = {c.key: c.family for c in registry.all_connectors()}
    start, end = window_start.isoformat(), window_end.isoformat()
    signals = roll_up(tuple(collected), family_of=families, window_start=start, window_end=end)
    logger.info("ops: gathered %d event(s) from %d source(s) over %s..%s", len(collected), len(sources), start, end)
    return Gathered(
        since=since,
        window_start=start,
        window_end=end,
        sources=tuple(sources),
        events=tuple(collected),
        signals=signals,
    )
