"""How the user pays for the agent sessions agentwatch prices.

A Claude Code session log carries tokens, not money. agentwatch prices them at
public API rates, which is the right number for an API-key user and an
*API-equivalent* for a subscription user — included in the plan, never billed.
The distinction decides what every renderer writes beside the total, so it is
read once, here, from the same ``~/.claude.json`` the security audit already
opens read-only (fs_policy allows it).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

KIND_SUBSCRIPTION = "subscription"
KIND_API = "api"
KIND_UNKNOWN = ""


@dataclass(frozen=True)
class BillingContext:
    """What the account pays with, and the one-line label renderers use."""

    kind: str = KIND_UNKNOWN
    plan_label: str = ""

    @property
    def total_label(self) -> str:
        """The qualifier that follows a dollar total."""
        if self.kind == KIND_SUBSCRIPTION:
            return "API-equivalent — included in your subscription, not a bill"
        if self.kind == KIND_API:
            return "estimated at public API rates"
        return "estimated from local session logs at public rates"


def _claude_config_path() -> Path:
    """The Claude Code config file — overridable in tests."""
    return Path.home() / ".claude.json"


def detect_billing() -> BillingContext:
    """Read the account's billing type from Claude Code's config. Never raises."""
    path = _claude_config_path()
    try:
        if not path.exists():
            return BillingContext()
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        logger.warning("agentwatch billing: cannot read %s: %s", path, exc)
        return BillingContext()
    if not isinstance(parsed, dict):
        return BillingContext()
    account = parsed.get("oauthAccount")
    account = account if isinstance(account, dict) else {}
    billing_type = str(account.get("billingType") or "").lower()
    subscription_type = str(account.get("subscriptionType") or "").strip()
    if "subscription" in billing_type or parsed.get("hasAvailableSubscription") is True:
        label = subscription_type or "subscription"
        return BillingContext(kind=KIND_SUBSCRIPTION, plan_label=label)
    if billing_type:
        return BillingContext(kind=KIND_API, plan_label=billing_type)
    return BillingContext()


def label_for(kind: str) -> str:
    """The total qualifier for a stored ``billing_kind`` (renderers of saved reports)."""
    return BillingContext(kind=kind).total_label
