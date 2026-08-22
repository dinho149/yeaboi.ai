"""Secret redaction for log output.

Every log handler in the app (see ``logging_setup._attach``) formats records
through :class:`RedactingFormatter`, so credentials can never land in a log
file verbatim — not in messages, not in ``%``-args, and not in exception
tracebacks (HTTP client errors routinely embed the ``Authorization`` header or
the request URL in their message).

Why a Formatter wrapper and not a ``logging.Filter``: a filter that rewrites
``record.msg`` misses ``exc_text`` (the cached, formatted traceback) and
mutates state shared with other handlers. Formatting is the one point where
message, args, stack info, and traceback have all been flattened into a single
string — redacting that string covers everything without touching the record.
Handler-level installation also matters: logger-level filters do NOT apply to
records propagated up from child loggers, so per-handler formatters in
``logging_setup._attach`` are the only complete choke point.

Two matching layers:

1. **Value-based** — the current values of the known secret env vars
   (:data:`SECRET_ENV_KEYS`). Catches our own credentials wherever they
   appear, whatever their shape. Values shorter than ``_MIN_SECRET_LEN`` are
   ignored so trivial strings ("true", "8080") never trigger redaction.
2. **Pattern-based** — well-known token shapes (Anthropic/OpenAI keys, GitHub
   tokens, Slack tokens/webhooks, AWS key ids, Atlassian/Notion tokens,
   ``Bearer``/``Basic`` auth headers). Catches secrets that did NOT come from
   our env: pasted by the user, or echoed back in an API error body.

Everything compiles into one alternation regex, cached and rebuilt lazily only
when the tuple of current env values changes (the setup wizard can write keys
mid-process). Redaction runs once per *emitted* record — after the level
check — and the never-log-per-frame rule keeps record volume low, so a single
``re.sub`` per record is cheap.

:func:`log_safe` is the module's other half, and it solves the opposite
problem. Redaction is about what a log line may *say*; ``log_safe`` is about
where a log line may *end*. It cannot live in the formatter — by the time a
record is formatted, the trusted format string and the tainted arguments have
been flattened into one string and are no longer distinguishable — so it is
applied at the call site instead.
"""

from __future__ import annotations

import logging
import os
import re

REDACTED = "[REDACTED]"

# C0 control characters minus the three whitespace ones (\t, \n, \r), plus DEL.
# Nothing legitimate emits these into text bound for a log line or an artifact
# field; something that sends them is broken or probing what the renderer does
# with them. Each caller layers its own newline policy on top, because the two
# want opposite things: ``artifacts.edits`` normalises CRLF and *keeps* the line
# breaks, ``log_safe`` collapses them so a value cannot end its own log line.
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# How much of one interpolated value a log line will carry. Long enough to
# identify a ticket key, a member name or an upstream error; short enough that
# a megabyte of request body cannot push the surrounding evidence out of a
# rotated file, which is the quieter half of a log-injection attack.
LOG_VALUE_LIMIT = 200

# Env vars whose values are credentials. Order does not matter — value
# matching sorts longest-first at compile time so substring overlaps
# (e.g. a token that contains another) cannot leave a partial secret behind.
SECRET_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "LANGSMITH_API_KEY",
    "GITHUB_TOKEN",
    "JIRA_API_TOKEN",
    "AZURE_DEVOPS_TOKEN",
    "CONFLUENCE_API_TOKEN",
    "NOTION_TOKEN",
    "STANDUP_SMTP_PASSWORD",
    "SLACK_WEBHOOK_URL",
    # Deliberately NOT joined by SLACK_CHANNEL_ID or SLACK_ALLOWED_MEMBER_IDS:
    # neither is a secret, and redacting member ids would gut exactly the log
    # lines that answer "whose reaction was that".
    "SLACK_BOT_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)

# Never value-match short strings — a 4-char "key" like "true" or "8080"
# would redact half the log. Real credentials are always longer than this.
_MIN_SECRET_LEN = 8

# Token shapes for secrets that never passed through our env vars.
# NOTE: agentwatch/collector.py attaches substring perf pre-checks to two of
# these, keyed on the EXACT pattern strings (see _PATTERN_GUARDS there). If
# you edit one of those patterns, its guard silently detaches — scans get
# slower but stay correct; re-key the guard to restore the speed.
# Prefixes are anchored (sk-ant-, ghp_, xoxb-, AKIA…) so prose can't match;
# the Bearer/Basic pattern is the loosest and therefore requires a 16+ char
# token tail to avoid matching ordinary sentences.
_TOKEN_PATTERNS: tuple[str, ...] = (
    r"sk-ant-[\w-]{10,}",
    r"sk-[A-Za-z0-9_-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"xox[abprs]-[\w-]{10,}",
    r"AIza[\w-]{35}",
    r"AKIA[0-9A-Z]{16}",
    r"ATATT[\w=+/-]{20,}",
    r"ntn_[A-Za-z0-9]{20,}",
    r"secret_[A-Za-z0-9]{30,}",
    r"hooks\.slack\.com/services/[\w/]+",
    r"(?i:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}",
    # user:password inside a URL. Lookarounds so the scheme and host survive and
    # only the credentials go: the voice installer logs the package-index URL,
    # and a corporate mirror routinely carries a token there
    # (https://svc:AKCp8…@nexus.corp/simple). Also covers Jira, SMTP and webhook
    # URLs pasted anywhere else.
    r"(?<=://)[^/\s:@]+:[^/\s@]{4,}(?=@)",
    # A live Cloudflare quick-tunnel hostname: not a credential, but the whole
    # address of an internet-reachable board. Redacted centrally so every call
    # site is covered, including cloudflared's echoed stderr.
    r"https?://[a-z0-9][a-z0-9-]*\.trycloudflare\.com",
)

# Compiled-regex cache: (env value snapshot) -> compiled alternation.
_cache_key: tuple[str, ...] | None = None
_cache_regex: re.Pattern[str] | None = None


def _current_secret_values() -> tuple[str, ...]:
    """Snapshot the env-var secret values worth matching, longest first."""
    values = {v for key in SECRET_ENV_KEYS if (v := os.environ.get(key, "")) and len(v) >= _MIN_SECRET_LEN}
    return tuple(sorted(values, key=len, reverse=True))


def _regex() -> re.Pattern[str]:
    """Return the alternation regex, rebuilding only when env values change."""
    global _cache_key, _cache_regex
    values = _current_secret_values()
    if _cache_regex is None or values != _cache_key:
        parts = [re.escape(v) for v in values] + list(_TOKEN_PATTERNS)
        _cache_key = values
        _cache_regex = re.compile("|".join(parts))
    return _cache_regex


def redact(text: str) -> str:
    """Replace every known secret value / token shape in `text` with [REDACTED].

    Pure and idempotent — safe to apply to already-redacted text.
    """
    return _regex().sub(REDACTED, text)


def log_safe(value: object, *, limit: int = LOG_VALUE_LIMIT) -> str:
    """Render `value` safe to interpolate into a log line.

    Log injection forges *records*, not characters: one CR or LF inside a
    user-controlled value closes the line it sits on and lets whatever follows
    be read as a new log entry — a fabricated ERROR, a fake admin action, or
    enough padding to push real evidence out of a rotated file. The guarantee
    this function makes is that a value can never end its own line.

    Wrap the tainted argument, never the format string::

        logger.info("share: vote for %s", log_safe(member))

    The two ``replace`` calls are deliberately explicit rather than folded into
    the regex: CodeQL's ``py/log-injection`` model recognises a ``str.replace``
    of ``"\\r"``/``"\\n"`` as a sanitizer barrier and does not recognise an
    equivalent ``re.sub``, so writing it the short way would leave every call
    site still reported as tainted. Keep them.

    Complements :class:`RedactingFormatter` rather than replacing it — that
    strips secrets from the assembled line, this stops a value forging a new
    one. Pure and idempotent.
    """
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = CONTROL_CHARS_RE.sub("", text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


class RedactingFormatter(logging.Formatter):
    """A ``logging.Formatter`` that scrubs secrets from the final output.

    Redacts the fully formatted string (message + args + stack + traceback)
    rather than mutating the record, so ``record.exc_text`` caching semantics
    and other handlers' views of the record are untouched.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
