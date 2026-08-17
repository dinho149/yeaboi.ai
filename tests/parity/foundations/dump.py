"""Python-side dumper for the W8 foundations parity gate.

Run as a subprocess — one process per fixture, because ``yeaboi.paths``
resolves its root at import time, which is exactly the behaviour the gate
pins (the Go side resolves once per process; the equivalence only holds if
each fixture gets a fresh import). Prints one canonical JSON document of
every ``paths`` module constant and helper under the environment it was
launched with:

- ``constants`` — every module-level path constant, by its Python name
- ``helpers`` — every zero-argument getter (with its mkdir/permission
  side effects, exercised inside the sandbox the caller pointed HOME at)
- ``keyed_helpers`` — every getter taking a key, over ``KEYED_SAMPLES``
- ``safe_key`` — ``_safe_key`` over ``SAFE_KEY_VECTORS`` (fallback ``"fb"``)

``matrix.py`` builds the launch environment; ``go/internal/home``'s golden
test replays the committed result against the Go port, key by key. W8
phase 3 adds the ``yeaboi __dump-foundations`` twin of this script, and the
phase-2 config port extends the dump to the 85 env-var getters.
"""

from __future__ import annotations

import json
import sys

# Deliberate traps, mirrored from the W8 spec: unicode-aware lower/strip
# (Turkish İ, NBSP), backslash-to-slash normalisation, ".." that must never
# escape an export root, "..." which is a legitimate name, separators
# collapsing to "-", and emptiness falling back.
SAFE_KEY_VECTORS = [
    "Team/Alpha",
    "İstanbul CI",
    "  padded  ",
    "a\\b\\c",
    "../../../etc/passwd",
    "a//b",
    "",
    "...",
    "PROJ.1",
    ".",
    "a/./b/../c",
    "🚀 Launch/Q3",
    "\u00a0nbsp\u00a0",  # NBSP padding — str.strip() is unicode-aware
    "MiXeD CASE",
    "trailing/",
]

# Two keys per keyed getter: one that exercises _safe_key's separator + case
# + unicode handling, and the empty key that lands on each getter's own
# fallback name ("project" / "engineer" / "report" / "roadmap" / "output" /
# "misc").
KEYED_SAMPLES = ["Team/Alpha", "İZMİR \\ Ops", ""]

CONSTANT_NAMES = [
    "DEFAULT_ROOT_DIR",
    "ROOT_DIR",
    "LEGACY_ROOT_DIR",
    "DATA_DIR",
    "DB_PATH",
    "STATES_DIR",
    "PROJECTS_FILE",
    "REPORTING_THEMES_FILE",
    "REPORTING_PREFS_FILE",
    "VOICE_INSTALL_FILE",
    "LEGACY_DB_PATH",
    "LEGACY_STATES_DIR",
    "LEGACY_PROJECTS_FILE",
    "EXPORTS_DIR",
    "ANALYSIS_EXPORTS_DIR",
    "PLANNING_EXPORTS_DIR",
    "STANDUP_EXPORTS_DIR",
    "RETRO_EXPORTS_DIR",
    "POKER_EXPORTS_DIR",
    "PERFORMANCE_EXPORTS_DIR",
    "REPORTING_EXPORTS_DIR",
    "ROADMAP_EXPORTS_DIR",
    "ANONYMIZE_EXPORTS_DIR",
    "AGENTWATCH_EXPORTS_DIR",
    "SHIP_DIR",
    "SHIP_WORKTREES_DIR",
    "SHIP_WORKTREE_REGISTRY",
    "SHIP_BUDGET_FILE",
    "SHIP_BUDGET_LOCK",
    "SHIP_BUDGET_RECEIPTS",
    "LOGS_DIR",
    "TUI_LOGS_DIR",
    "STANDUP_LOGS_DIR",
    "RETRO_LOGS_DIR",
    "POKER_LOGS_DIR",
    "PERFORMANCE_LOGS_DIR",
    "REPORTING_LOGS_DIR",
    "ROADMAP_LOGS_DIR",
    "ANALYSIS_LOGS_DIR",
    "PLANNING_LOGS_DIR",
    "MCP_LOGS_DIR",
    "AGENTWATCH_LOGS_DIR",
    "SHIP_LOGS_DIR",
    "CEREMONIES_LOGS_DIR",
    "LEGACY_TUI_LOG",
    "SCRUM_DOCS_DIR",
    "ENV_FILE",
    "REPL_HISTORY",
    "BIN_DIR",
    "ATTACHMENTS_DIR",
    "TRANSCRIPTS_DIR",
]

ZERO_ARG_HELPERS = [
    "get_db_path",
    "get_reporting_themes_path",
    "get_reporting_prefs_path",
    "get_voice_install_path",
    "get_tui_log_path",
    "get_analysis_log_dir",
    "get_planning_log_dir",
    "get_standup_log_dir",
    "get_retro_log_dir",
    "get_poker_log_dir",
    "get_performance_log_dir",
    "get_reporting_log_dir",
    "get_roadmap_log_dir",
    "get_mcp_log_dir",
    "get_agentwatch_log_dir",
    "get_ceremonies_log_dir",
    "get_ship_log_dir",
    "get_ship_dir",
    "get_bin_dir",
    "get_transcripts_dir",
]

KEYED_HELPERS = [
    "get_analysis_export_dir",
    "get_planning_export_dir",
    "get_standup_export_dir",
    "get_retro_export_dir",
    "get_poker_export_dir",
    "get_performance_export_dir",
    "get_reporting_export_dir",
    "get_roadmap_export_dir",
    "get_anonymize_export_dir",
    "get_agentwatch_export_dir",
    "get_attachments_dir",
]


def build_dump() -> dict:
    """Import yeaboi.paths (resolving the root from this process's env) and dump it."""
    import yeaboi.paths as paths

    return {
        "constants": {name: str(getattr(paths, name)) for name in CONSTANT_NAMES},
        "helpers": {name: str(getattr(paths, name)()) for name in ZERO_ARG_HELPERS},
        "keyed_helpers": {
            name: {key: str(getattr(paths, name)(key)) for key in KEYED_SAMPLES} for name in KEYED_HELPERS
        },
        "safe_key": {key: paths._safe_key(key, "fb") for key in SAFE_KEY_VECTORS},
    }


if __name__ == "__main__":
    json.dump(build_dump(), sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
