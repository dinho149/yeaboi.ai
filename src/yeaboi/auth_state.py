"""Whether the configured LLM credentials are still good, and who needs telling.

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

:func:`check_llm_credentials` generalizes the same idea to every provider and
auth mode (not just a Claude subscription token): a live, synchronous ping used
to gate mode entry in the TUI (``ui.shared._llm_gate``), so a revoked/expired
API key is caught before a mode silently degrades to deterministic fallback
output instead of after.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_stale = False
_reason = ""
# (provider, credential) whose live ping last came back good, so the happy path
# does not pay a round trip on every mode entry. Only ever holds a *success*;
# see check_llm_credentials.
_verified_provider: tuple[str, str] | None = None

# Display name per LLM_PROVIDER value, for messages like "Your Anthropic API key
# looks invalid" — a lookup table because config.get_llm_provider() only ever
# returns the raw env-var string.
_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "bedrock": "AWS Bedrock",
    "ollama": "Ollama",
}


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


@dataclass(frozen=True)
class CredentialStatus:
    """Result of a live check of the currently configured LLM credentials."""

    ok: bool
    configured: bool  # an env var / credential is present at all
    reason: str | None  # human-readable cause, set whenever ok is False
    provider_label: str  # e.g. "Anthropic", "Ollama"


def provider_label(provider: str | None = None) -> str:
    """Display name for a provider value (defaults to the active provider)."""
    from yeaboi.config import get_llm_provider

    provider = provider or get_llm_provider()
    return _PROVIDER_LABELS.get(provider, provider)


def _current_credential(provider: str) -> str:
    """The raw credential value ``provider_verification`` needs to ping with.

    Bedrock and Ollama don't use an API key — Bedrock authenticates via IAM and
    is pinged with its region, Ollama is a local server pinged by its base URL.
    """
    from yeaboi.config import (
        get_anthropic_api_key,
        get_bedrock_region,
        get_google_api_key,
        get_ollama_base_url,
        get_openai_api_key,
    )

    if provider == "anthropic":
        # Raises when unset, which cannot happen here — is_llm_configured()
        # gates this call — but a probe must not be the thing that crashes.
        try:
            return get_anthropic_api_key()
        except OSError:
            return ""
    if provider == "openai":
        return get_openai_api_key() or ""
    if provider == "google":
        return get_google_api_key() or ""
    if provider == "bedrock":
        return get_bedrock_region()
    if provider == "ollama":
        return get_ollama_base_url()
    return ""


def clear_credential_cache() -> None:
    """Forget a cached good result — call after credentials change in Settings."""
    global _verified_provider
    with _lock:
        _verified_provider = None


def check_llm_credentials() -> CredentialStatus:
    """Live-check whether the active provider's credentials actually work.

    Unlike :func:`probe_subscription_token` (Claude subscription auth only),
    this covers every provider and both auth modes, and is meant to be called
    synchronously, right before a mode starts — the TUI gate is the one caller
    today. A missing credential short-circuits before any network call; a
    present one gets a real, cheap ping (subscription: ``count_tokens``;
    API-key: the same one-token request the setup wizard verifies with) so an
    expired or revoked credential is caught before it silently degrades a
    mode's output to its deterministic fallback.

    **Only a definite rejection counts as a failure.** A timeout, a proxy or an
    unexpected status returns ``ok=True``: an inconclusive probe must not
    accuse a working key, which is the rule ``probe_subscription_token`` has
    always followed and the reason this does not simply negate the wizard's
    pass/fail.

    A *good* answer is cached for the process (keyed on the credential, so
    editing it in Settings re-checks), which keeps the happy path off the
    network on every single mode entry. A bad answer is never cached — a still
    broken key must be reported every time it is still broken.
    """
    global _verified_provider
    from yeaboi.config import get_anthropic_subscription_token, get_llm_provider, is_llm_configured

    provider = get_llm_provider()
    label = provider_label(provider)

    configured, message = is_llm_configured()
    if not configured:
        logger.warning("credential check: %s not configured (%s)", label, message)
        return CredentialStatus(ok=False, configured=False, reason=message, provider_label=label)

    if provider == "anthropic" and get_anthropic_subscription_token():
        ok = probe_subscription_token()
        reason = None if ok else (stale_reason() or "Subscription token looks expired")
        return CredentialStatus(ok=ok, configured=True, reason=reason, provider_label=label)

    from yeaboi.provider_verification import credential_verdict

    credential = _current_credential(provider)
    fingerprint = (provider, credential)
    with _lock:
        if _verified_provider == fingerprint:
            return CredentialStatus(ok=True, configured=True, reason=None, provider_label=label)

    from yeaboi.agent.llm import resolve_model_name
    from yeaboi.provider_verification import log_category
    from yeaboi.redaction import redact

    logger.info("credential check: pinging %s", label)
    # The model the modes will actually call, not the verifier's hardcoded
    # default — otherwise this proves the wrong thing, and blocks on a 404 the
    # day that default retires.
    provider_spec = {"provider_val": provider, "models": {"default": resolve_model_name()}}
    verdict, raw_message = credential_verdict(provider_spec, credential)

    # Logs get a fixed-vocabulary label, never the provider's own text: that text
    # quotes the request it failed on, and Google's puts the API key in the URL.
    # The screen gets the message, redacted — that is where the detail is worth
    # the handling, and where the user can act on it.
    category = log_category(raw_message)
    logger.info("credential check: %s → %s (%s)", label, verdict, category)

    if verdict == "ok":
        with _lock:
            _verified_provider = fingerprint
        return CredentialStatus(ok=True, configured=True, reason=None, provider_label=label)
    if verdict == "inconclusive":
        logger.warning("credential check inconclusive for %s (%s) — not blocking", label, category)
        return CredentialStatus(ok=True, configured=True, reason=None, provider_label=label)

    return CredentialStatus(ok=False, configured=True, reason=redact(raw_message), provider_label=label)
