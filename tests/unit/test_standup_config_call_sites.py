"""Guard: every full-pass ``StandupStore.save_config`` caller passes every field.

``save_config`` writes EVERY column of ``standup_config`` — a keyword the caller
omits is silently reset to its default, not left alone. There are six call sites
across the TUI and the MCP server, and each time a column has been added the
sweep has had to be done by hand. That is exactly the kind of bug the test suite
should own instead of a comment: it is invisible at write time, and only shows up
later as "my standup forgot my team again".

So: parse the call sites and assert each one names every keyword-only parameter
of ``save_config``. Adding a column now fails here, with the file, line, and the
missing names, until every caller is updated.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from yeaboi.standup.store import StandupStore

# Modules that build a whole config row. Anything else calling save_config would
# be a new surface, and _iter_calls below would pick it up only if listed here —
# so the directory scan is deliberately broad rather than a hardcoded file list.
_SCAN_DIRS = ("ui", "mcp")


def _required_keywords() -> set[str]:
    """The keyword-only parameters of save_config (session_id is positional)."""
    signature = inspect.signature(StandupStore.save_config)
    return {name for name, param in signature.parameters.items() if param.kind is inspect.Parameter.KEYWORD_ONLY}


def _source_root() -> Path:
    import yeaboi

    return Path(yeaboi.__file__).parent


def _iter_calls() -> list[tuple[Path, ast.Call]]:
    """Every ``*.save_config(...)`` call in the scanned source directories."""
    found: list[tuple[Path, ast.Call]] = []
    root = _source_root()
    for directory in _SCAN_DIRS:
        for path in sorted((root / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "save_config"
                ):
                    found.append((path, node))
    return found


class TestSaveConfigCallSites:
    def test_call_sites_exist(self):
        """A scan that silently finds nothing would pass every other test here."""
        assert _iter_calls(), "no save_config call sites found — the AST scan is broken"

    def test_every_call_site_passes_every_field(self):
        required = _required_keywords()
        problems: list[str] = []
        for path, call in _iter_calls():
            # ``store.save_config(resolved, **merged)`` (the MCP path) forwards a
            # dict built from _CONFIG_DEFAULTS; that dict is checked separately
            # by test_config_defaults_cover_every_field.
            if any(kw.arg is None for kw in call.keywords):
                continue
            passed = {kw.arg for kw in call.keywords if kw.arg}
            missing = required - passed
            if missing:
                problems.append(f"{path.name}:{call.lineno} missing {sorted(missing)}")
        assert not problems, "save_config call sites drop fields (they will reset to defaults):\n" + "\n".join(problems)

    def test_config_defaults_cover_every_field(self):
        """The MCP ``**merged`` path is only safe if its defaults are complete."""
        from yeaboi.mcp.tools_standup import _CONFIG_DEFAULTS

        missing = _required_keywords() - set(_CONFIG_DEFAULTS)
        assert not missing, f"_CONFIG_DEFAULTS is missing {sorted(missing)}"

    def test_load_config_round_trips_every_saved_field(self, tmp_path):
        """A column that save_config writes but load_config never reads is dead."""
        db = tmp_path / "sessions.db"
        with StandupStore(db) as store:
            store.save_config("s1", enabled=True, time="09:30", weekdays="1-5", delivery_channels=["terminal"])
            loaded = store.load_config("s1") or {}
        missing = _required_keywords() - set(loaded)
        assert not missing, f"load_config never returns {sorted(missing)}"
