"""Slack Web API client — the edge of the two-way Slack lane.

**This module exposes no ``@tool``, and never will.** Every other file in
``tools/`` publishes LangChain tools; this one must not. An LLM-callable
``slack_post_message`` would let prompt-injected text in a Jira title reach a
team channel, which is precisely the hole the two-way lane's whole posture —
*everything read from the channel is data, never instructions* — exists to
close. The same reasoning keeps the poller off the MCP surface.

Two further divergences from its sibling ``tools/notion.py``, both deliberate:

- **stdlib ``urllib``, no new dependency.** ``ceremonies/delivery.py`` is
  already stdlib-only by design, ``httpx`` is only transitively present, and
  Slack's ``{"ok": false, "error": …}`` envelope means the response is
  hand-inspected either way. Connection pooling buys nothing for a job that
  makes a handful of requests and exits.
- **Structured returns, not ``str``.** Notion's "every function returns a
  string, errors are ``"Error: …"`` strings" convention exists because those
  functions *are* tools and a string is a tool's return type. The poller needs
  ``ts`` values, cursors and user ids. So calls return a frozen
  :class:`SlackResponse` and one :func:`error_message` renders the ``"Error: …"``
  string for human-facing surfaces — the convention kept where it means
  something, dropped where it would be cargo cult.

Nothing here raises. Transport failures, HTTP errors and Slack's own ``ok:
false`` all come back as a ``SlackResponse`` with ``ok=False``, because every
caller is either an unattended poll or a delivery channel that has promised
never to let one channel's failure block the others.

# See docs: "Integrations" — Slack
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api/"

_TIMEOUT = 15
_MAX_RETRIES = 2
# One nap, and a whole-process ceiling. Past the budget a call gives up and the
# caller stops cleanly — the poller's overlapping read window means the next
# run re-reads the same messages, so nothing is lost by being impatient here.
_MAX_SLEEP_S = 30.0
_MAX_TOTAL_SLEEP_S = 60.0

# Slack error codes that will not fix themselves on a retry, and that mean the
# credential itself is wrong rather than the request.
_FATAL_AUTH = frozenset({"invalid_auth", "token_revoked", "account_inactive", "not_authed"})

# The message a user can act on, per Slack error code. `not_in_channel` is the
# single most common real-world failure and a generic string for it wastes an
# afternoon, so every one of these names the fix rather than the symptom.
_ERROR_HELP: dict[str, str] = {
    "not_in_channel": "the bot is not in that channel — invite it with `/invite @yeaboi`",
    "channel_not_found": "no such channel — check SLACK_CHANNEL_ID (it is an id like C0123456789, not a name)",
    "is_archived": "that channel is archived",
    "invalid_auth": "SLACK_BOT_TOKEN was rejected — regenerate it in your Slack app's OAuth page",
    "token_revoked": "SLACK_BOT_TOKEN has been revoked — reinstall the app and paste the new token",
    "account_inactive": "that token's Slack account is deactivated",
    "not_authed": "no SLACK_BOT_TOKEN was sent",
    "missing_scope": "the token is missing a scope — add it in the app's OAuth page and reinstall",
    "ratelimited": "Slack is rate limiting — the next poll re-reads the same window, so nothing is lost",
}


@dataclass(frozen=True)
class SlackResponse:
    """One Web API call's outcome. Never an exception, always one of these."""

    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""  # Slack's error code, or a description of a transport failure
    status: int = 0
    retry_after: int = 0


class RetryBudget:
    """A ceiling on how long a whole poll may spend asleep in 429 backoff.

    Shared across every call in one run rather than per call: three methods
    each napping politely for their own 30 seconds is a job that outlives its
    own cadence and collides with the next fire.
    """

    def __init__(self, total: float = _MAX_TOTAL_SLEEP_S) -> None:
        self.remaining = total

    def sleep(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; return False when the budget is spent."""
        nap = min(float(seconds), _MAX_SLEEP_S, self.remaining)
        if nap <= 0:
            return False
        self.remaining -= nap
        time.sleep(nap)
        return True


def _retry_after(headers) -> int:
    try:
        return max(0, int(headers.get("Retry-After", "0") or 0))
    except (TypeError, ValueError):
        return 0


def _request(method: str, params: dict, token: str, http_method: str) -> SlackResponse:
    """One HTTP round trip. Returns a SlackResponse for every outcome."""
    url = SLACK_API + method
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data: bytes | None = None
    if http_method == "POST":
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(params).encode("utf-8")
    elif params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    # url is always SLACK_API + a literal method name, never user input.
    req = urllib.request.Request(url, data=data, headers=headers, method=http_method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 — fixed https host
            body = json.loads(resp.read().decode("utf-8") or "{}")
            ok = bool(body.get("ok"))
            return SlackResponse(
                ok=ok,
                data=body if ok else {},
                error="" if ok else str(body.get("error", "unknown_error")),
                status=resp.status,
                retry_after=_retry_after(resp.headers),
            )
    except urllib.error.HTTPError as e:
        # 429 and 5xx arrive here; Slack still sends a JSON body for most.
        error = "ratelimited" if e.code == 429 else f"http_{e.code}"
        try:
            error = str(json.loads(e.read().decode("utf-8") or "{}").get("error", error))
        except (ValueError, OSError):
            pass
        return SlackResponse(ok=False, error=error, status=e.code, retry_after=_retry_after(e.headers))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return SlackResponse(ok=False, error=f"transport_error: {e}")


def call(
    method: str,
    params: dict | None = None,
    *,
    token: str = "",
    http_method: str = "GET",
    budget: RetryBudget | None = None,
) -> SlackResponse:
    """Call one Web API method, honouring ``Retry-After`` within the budget.

    Reads go out as GET with the parameters — including the pagination cursor —
    in the query string. That is not only Slack's own preference for those
    methods: it is what makes two calls to the same method distinguishable in a
    VCR cassette, since this repo matches on the path and would otherwise
    replay page one twice.
    """
    resolved = token or _token()
    if not resolved:
        return SlackResponse(ok=False, error="not_authed")

    budget = budget or RetryBudget()
    attempt = 0
    while True:
        resp = _request(method, dict(params or {}), resolved, http_method)
        if resp.ok:
            logger.info("slack[%s]: ok", method)
            return resp
        if resp.error in _FATAL_AUTH or attempt >= _MAX_RETRIES:
            logger.warning("slack[%s]: %s (status=%s)", method, resp.error, resp.status)
            return resp
        # Retry only what a retry can fix: rate limits and server-side errors.
        if resp.error == "ratelimited":
            wait: float = resp.retry_after or 1.0
        elif resp.status >= 500 or resp.error.startswith("transport_error"):
            wait = float(2**attempt)
        else:
            logger.warning("slack[%s]: %s (status=%s)", method, resp.error, resp.status)
            return resp
        if not budget.sleep(wait):
            logger.warning("slack[%s]: %s — retry budget spent", method, resp.error)
            return resp
        attempt += 1


def _token() -> str:
    from yeaboi import config

    return config.get_slack_bot_token()


def is_fatal_auth_error(resp: SlackResponse) -> bool:
    """True when the credential itself is wrong — do not retry, do not continue."""
    return resp.error in _FATAL_AUTH


def error_message(resp: SlackResponse) -> str:
    """The ``"Error: …"`` string a human-facing surface should print."""
    if resp.ok:
        return ""
    help_text = _ERROR_HELP.get(resp.error, "")
    return f"Error: Slack said {resp.error!r}" + (f" — {help_text}" if help_text else "")


# ── the methods the two-way lane uses ──────────────────────────────────────


def auth_test(*, token: str = "", budget: RetryBudget | None = None) -> SlackResponse:
    """Who this token is. The identity probe behind ``yeaboi slack check``."""
    return call("auth.test", token=token, http_method="POST", budget=budget)


def post_message(
    channel: str, text: str, *, thread_ts: str = "", token: str = "", budget: RetryBudget | None = None
) -> SlackResponse:
    """Post, and get back the ``ts`` that makes the message answerable.

    This is the entire reason two-way needs a bot token: an incoming webhook
    replies with the literal body ``ok`` and no message id, so a reaction on
    what it posted can never be attributed back to the run that caused it.
    """
    params: dict = {"channel": channel, "text": text}
    if thread_ts:
        params["thread_ts"] = thread_ts
    return call("chat.postMessage", params, token=token, http_method="POST", budget=budget)


def replies(
    channel: str, ts: str, *, cursor: str = "", limit: int = 200, token: str = "", budget: RetryBudget | None = None
) -> SlackResponse:
    """The thread hanging under one of our messages."""
    params: dict = {"channel": channel, "ts": ts, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    return call("conversations.replies", params, token=token, budget=budget)


def reactions_get(channel: str, ts: str, *, token: str = "", budget: RetryBudget | None = None) -> SlackResponse:
    """Reactions on one message.

    ``full=true`` because Slack truncates the ``users`` list on a busy message
    otherwise, and a truncated list is indistinguishable from nobody having
    reacted — which would mean an act that silently never happened.
    """
    return call("reactions.get", {"channel": channel, "timestamp": ts, "full": "true"}, token=token, budget=budget)


def users_info(user: str, *, token: str = "", budget: RetryBudget | None = None) -> SlackResponse:
    """One member's profile, for naming a reaction in a listing."""
    return call("users.info", {"user": user}, token=token, budget=budget)


def users_list(*, cursor: str = "", limit: int = 200, token: str = "", budget: RetryBudget | None = None):
    """A page of workspace members, for building the allowlist by copy-paste."""
    params: dict = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    return call("users.list", params, token=token, budget=budget)


def paginate(fetch, key: str, *, max_pages: int = 10) -> tuple[list[dict], str]:
    """Walk a cursor-paginated method. Returns (items, error).

    Stops at ``max_pages`` rather than following a cursor forever: this runs
    unattended on a short cadence, and the overlapping read window means a
    truncated page is re-read next time rather than lost.
    """
    items: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        resp = fetch(cursor)
        if not resp.ok:
            return items, resp.error
        items.extend(resp.data.get(key) or [])
        cursor = ((resp.data.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    return items, ""


def add_reaction(
    channel: str, ts: str, name: str, *, token: str = "", budget: RetryBudget | None = None
) -> SlackResponse:
    """Add a reaction — the human-visible confirmation, never a record."""
    return call(
        "reactions.add",
        {"channel": channel, "timestamp": ts, "name": name},
        token=token,
        http_method="POST",
        budget=budget,
    )
