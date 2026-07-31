"""Canonical wording for features that ship in beta.

A feature is "beta" here when it works end to end but its *output* has not been
validated against real-world data yet. That is a different claim from "coming
soon" (not usable) and from "new" (usable and verified, just recent), and it
deserves its own vocabulary so every surface says the same thing.

This module is deliberately **import-free**. Every surface pulls from it —
``cli.py``, ``mcp/tools_performance.py``, the TUI screens, the tip registry —
and two of those are startup-latency sensitive:

* The constants cannot live in ``performance/``. ``mcp/tools_performance.py``
  reaches the engines through function-level imports precisely so the MCP
  server boots without them; a module-scope ``from yeaboi.performance.beta
  import …`` would execute ``performance/__init__.py``, which eagerly imports
  ``context``/``roster``/``store`` and through them ``langchain_core`` — about
  0.2s onto every server start. (Note ``performance/__init__.py``'s own
  docstring still claims importing the package "never drags in langchain";
  that is stale — ``import yeaboi.performance`` does pull ``langchain_core``
  today. Its lazy ``__getattr__`` defers only the three engine entry points.)
* ``ui/shared/`` is out too — ``mcp/`` importing a UI module inverts the layering.

``tests/unit/test_beta.py`` AST-scans this file and fails if an import ever
appears, because the cost would be silent.

HTML, Markdown and the plugin ``SKILL.md`` cannot import Python, so their copies
are hand-written; ``tests/unit/test_beta_surfaces.py`` pins them to these values.
"""

# The badge/pill/chip text. Short, uppercase, rendered as an inverse-video chip
# in the TUI and a rounded pill on the docs site.
BETA_LABEL = "BETA"

# The inline qualifier for running prose and one-line help strings, matching the
# lowercase-parenthetical house style of the other CLI subcommand help lines.
BETA_TAG = "(beta)"

# The load-bearing claim, kept short enough to survive HTML re-wrapping — this
# is the token the cross-surface sync test greps for in the docs and SKILL.md.
PERFORMANCE_BETA_PHRASE = "not yet verified against real delivery data"

# The full caveat. One sentence of status, one of instruction. "a draft to edit,
# not a verdict" is lifted from the performance plugin skill's existing voice so
# the caveat reads as part of the product rather than bolted on.
PERFORMANCE_BETA_NOTICE = (
    "Performance mode is in beta — its output is not yet verified against real "
    "delivery data. Treat every 1:1 prep, summary and review as a draft to edit, "
    "not a verdict."
)

# Amber caution. Deliberately *not* the warm gold (226,186,96) used by the NEW
# badge: beta is a warning, new is a freshness cue, and the two appear side by
# side in the tips gallery where they must not read as the same thing. The docs
# site's ``.beta-pill`` uses the same rgb() triple.
BETA_RGB = (224, 138, 72)
