"""Whether the Claude subscription token is still good, and who needs telling.

A subscription token expires, and nothing tells us when: it is opaque and the CLI
prints no expiry. So staleness is *observed* rather than predicted, from two
places that agree on one flag:

- **A real auth failure.** Every mode's engine funnels its LLM errors through
  ``agent/nodes.py::_is_llm_auth_or_billing_error``; when that fires under
  subscription auth, the token is the likeliest cause.
- **A probe at launch.** ``count_tokens`` authenticates but generates nothing and
  is not billed, so it is a free way to find out before the user hits a wall.

The flag is deliberately **not persisted**. It does not need to be: the launch
probe re-derives it every run, so a stale token is still reported tomorrow, and
there is no stored bit that can disagree with reality or be left behind after a
refresh. A predicate with a config write in it would also make any test that
touched an auth error edit the user's real credentials file.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_stale = False
_reason = ""


def mark_subscription_stale(reason: str = "") -> None:
    """Record that the subscription token looks expired or rejected.

    Only meaningful under subscription auth; callers that cannot cheaply tell
    should use :func:`note_auth_failure`, which checks first.
    """
    global _stale, _reason
    with _lock:
        if not _stale:
            logger.warning("subscription token looks stale: %s", reason or "auth rejected")
        _stale, _reason = True, reason


def clear_subscription_stale() -> None:
    """Forget the warning — called when a fresh token is saved."""
    global _stale, _reason
    with _lock:
        if _stale:
            logger.info("subscription token refreshed — clearing the stale flag")
        _stale, _reason = False, ""


def subscription_stale() -> bool:
    """True while the stored subscription token is believed to be no good."""
    with _lock:
        return _stale


def stale_reason() -> str:
    with _lock:
        return _reason


def note_auth_failure(exc: Exception) -> None:
    """Flag the token when an LLM auth failure happens under subscription auth.

    A no-op for API-key auth: a rejected key is the user's own key, and pointing
    them at the subscription sign-in would be wrong.
    """
    from yeaboi.config import get_anthropic_subscription_token

    if get_anthropic_subscription_token():
        mark_subscription_stale(type(exc).__name__)


def probe_subscription_token() -> bool:
    """Check the stored subscription token against the API. True when it is good.

    Uses ``count_tokens``: it authenticates, generates nothing, and is not billed,
    so this costs a round trip and no money. Any *auth* rejection marks the token
    stale; every other failure (offline, rate limited, a 500) is left alone —
    telling someone on a train that their credentials expired would be worse than
    saying nothing.
    """
    from yeaboi.config import get_anthropic_subscription_token

    token = get_anthropic_subscription_token()
    if not token:
        return True  # not on subscription auth — nothing to check

    try:
        from yeaboi.agent.llm import get_llm

        llm = get_llm()
        # A single trivial message: the smallest request that still authenticates.
        llm._client.messages.count_tokens(
            model=llm.model,
            messages=[{"role": "user", "content": "ping"}],
        )
    except Exception as exc:  # noqa: BLE001 - a probe must never break startup
        from yeaboi.agent.nodes import _is_llm_auth_or_billing_error

        if _is_llm_auth_or_billing_error(exc):
            mark_subscription_stale(type(exc).__name__)
            return False
        logger.debug("subscription probe inconclusive (not an auth error): %s", exc)
        return True

    clear_subscription_stale()
    logger.info("subscription token probe: ok")
    return True


def probe_in_background() -> None:
    """Run :func:`probe_subscription_token` off the startup path.

    Startup must not wait on the network, and nothing on the first screen depends
    on the answer — the duck picks the warning up on a later frame.
    """
    from yeaboi.config import get_anthropic_subscription_token

    if not get_anthropic_subscription_token():
        return
    thread = threading.Thread(target=probe_subscription_token, name="subscription-probe", daemon=True)
    thread.start()
