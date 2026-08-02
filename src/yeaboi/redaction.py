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
"""

from __future__ import annotations

import logging
import os
import re

REDACTED = "[REDACTED]"

# Env vars whose values are credentials. Order does not matter — value
# matching sorts longest-first at compile time so substring overlaps
# (e.g. a token that contains another) cannot leave a partial secret behind.
SECRET_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "LANGSMITH_API_KEY",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "JIRA_API_TOKEN",
    "AZURE_DEVOPS_TOKEN",
    "CONFLUENCE_API_TOKEN",
    "NOTION_TOKEN",
    "STANDUP_SMTP_PASSWORD",
    "SLACK_WEBHOOK_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)

# Never value-match short strings — a 4-char "key" like "true" or "8080"
# would redact half the log. Real credentials are always longer than this.
_MIN_SECRET_LEN = 8

# Token shapes for secrets that never passed through our env vars.
# Prefixes are anchored (sk-ant-, ghp_, xoxb-, AKIA…) so prose can't match;
# the Bearer/Basic pattern is the loosest and therefore requires a 16+ char
# token tail to avoid matching ordinary sentences.
_TOKEN_PATTERNS: tuple[str, ...] = (
    r"sk-ant-[\w-]{10,}",
    r"sk-[A-Za-z0-9_-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    # GitLab PATs (glpat-), plus the other glXXX- prefixes GitLab mints for
    # deploy/runner/feed tokens — all share the same "gl<kind>-" shape.
    r"gl(?:pat|dt|rt|ft|soat|ptt)-[A-Za-z0-9_-]{20,}",
    r"xox[abprs]-[\w-]{10,}",
    r"AIza[\w-]{35}",
    r"AKIA[0-9A-Z]{16}",
    r"ATATT[\w=+/-]{20,}",
    r"ntn_[A-Za-z0-9]{20,}",
    r"secret_[A-Za-z0-9]{30,}",
    r"hooks\.slack\.com/services/[\w/]+",
    r"(?i:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}",
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


class RedactingFormatter(logging.Formatter):
    """A ``logging.Formatter`` that scrubs secrets from the final output.

    Redacts the fully formatted string (message + args + stack + traceback)
    rather than mutating the record, so ``record.exc_text`` caching semantics
    and other handlers' views of the record are untouched.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
