"""yeaboi.ai — a team lead's best friend for project planning and delivery."""

import logging
import warnings
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Suppress LangChain's Pydantic V1 compatibility warning on Python 3.14+.
# langchain-core internally imports pydantic.v1 shims which don't work on
# Python 3.14. The warning is non-fatal — the agent runs correctly.
# Remove once langchain-core fully migrates off pydantic.v1.
warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality", category=UserWarning)


# ---------------------------------------------------------------------------
# Suppress LangSmith 429 rate-limit errors from appearing in the terminal.
#
# LangSmith's background trace uploader logs errors via the 'langsmith' Python
# logger. When the free-tier rate limit is hit, those messages interrupt the
# REPL output with noise the user can't act on.
#
# Fix: attach a filter + silent handler to logging.getLogger("langsmith") and
# set propagate=False so messages never reach the root StreamHandler.
#
# The filter also calls disable_langsmith_tracing() on the first 429 hit so
# the uploader stops retrying for the rest of the process.
#
# Why _SilentHandler instead of NullHandler?  NullHandler.handle() overrides
# the base and skips filter checks entirely — so a filter attached to it would
# never run.  _SilentHandler inherits the base Handler.handle() which calls
# self.filter() before emit(), giving us the interception point we need.
#
# # See docs: "Architecture" — the CLI layer manages all user-facing chrome,
# # including suppressing third-party noise that would confuse the user.
# ---------------------------------------------------------------------------


class _LangSmithRateLimitFilter(logging.Filter):
    """Block LangSmith 429 messages and auto-disable tracing when rate-limited."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "429" in msg or "Rate limit" in msg or "LangSmithRateLimitError" in msg:
            # Lazy import — config may not be importable in all test contexts.
            try:
                from yeaboi.config import disable_langsmith_tracing

                disable_langsmith_tracing()
            except Exception:
                pass
            return False  # suppress this log record
        return True


class _SilentHandler(logging.Handler):
    """No-op handler; discards all records. Present so propagate=False doesn't
    trigger Python's "No handlers found" last-resort warning."""

    def emit(self, record: logging.LogRecord) -> None:
        pass


_ls_handler = _SilentHandler()
_ls_handler.addFilter(_LangSmithRateLimitFilter())
_ls_logger = logging.getLogger("langsmith")
_ls_logger.addHandler(_ls_handler)
_ls_logger.propagate = False  # stop records reaching the root StreamHandler

# Single source of truth: the version lives only in pyproject.toml and is read
# here from the installed package metadata. Bump pyproject.toml to release.
try:
    __version__ = _pkg_version("yeaboi")
except PackageNotFoundError:  # running from a raw source tree without an install
    __version__ = "0.0.0+dev"
