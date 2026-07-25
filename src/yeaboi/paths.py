"""Centralised path definitions for the ~/.yeaboi directory structure.

All file and directory paths for the yeaboi.ai application should be
accessed through this module to ensure consistency across the codebase.
(The config dir was ``~/.scrum-agent`` before the yeaboi.ai rebrand; an
existing tree is migrated automatically — see ``migrate_root_dir``.)

The whole tree can be relocated with ``YEABOI_HOME`` (Settings → Data Dir);
only ``.env`` stays at ``~/.yeaboi/.env`` — it's the bootstrap file that
holds ``YEABOI_HOME`` itself.

Directory structure:
    ~/.yeaboi/                    # or $YEABOI_HOME
    ├── data/
    │   ├── sessions.db           # SQLite: sessions, team profiles, token usage
    │   ├── states/               # Legacy checkpoint JSON files
    │   └── projects.json         # Project metadata
    ├── exports/
    │   ├── analysis/             # Team analysis exports (HTML + MD)
    │   │   └── {project_key}/
    │   ├── planning/             # Planning exports (HTML + MD + scrum-docs)
    │   │   └── {project_key}/
    │   ├── standup/              # Daily Standup exports (HTML + MD)
    │   │   └── {project_key}/
    │   ├── retro/                # Retro exports (HTML + MD)
    │   │   └── {project_key}/
    │   ├── poker/                # Scrum Poker exports (HTML + MD)
    │   │   └── {project_key}/
    │   └── anonymize/            # Privacy-masked, shareable copies (HTML + MD)
    │       └── {project_key}/
    ├── logs/
    │   ├── tui/                  # Main TUI log (yeaboi.log + rotations)
    │   ├── analysis/             # Per-analysis-run logs
    │   ├── planning/             # Per-planning-session logs
    │   ├── standup/              # Daily Standup logs
    │   ├── retro/                # Retro logs
    │   └── poker/                # Scrum Poker logs
    ├── attachments/              # Screenshots pasted into TUI textboxes (Ctrl+V)
    │   └── {scope_id}/           #   per session/project scope
    ├── scrum-docs/               # SCRUM.md files for each project
    ├── .env                      # Environment variables (always at ~/.yeaboi/.env)
    └── repl-history              # REPL command history
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

# The default data home. ~/.yeaboi/.env is *always* read from here (it's the
# bootstrap file that can itself set YEABOI_HOME — deriving its location from
# the override would be circular).
DEFAULT_ROOT_DIR = Path.home() / ".yeaboi"


def _resolve_root() -> Path:
    """Resolve the data home: $YEABOI_HOME when set, else ~/.yeaboi.

    Read once at import time — every constant below derives from ROOT_DIR, so
    changing the setting mid-run takes effect on the next start (the Settings
    flow says so). Kept as a named function so tests can exercise the
    resolution with a monkeypatched environment.
    """
    raw = os.getenv("YEABOI_HOME", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_ROOT_DIR


ROOT_DIR = _resolve_root()

# Pre-rebrand config dir (yeaboi.ai was "Scrum AI Agent"). If present and the
# new ROOT_DIR isn't, the whole tree is migrated once at startup.
LEGACY_ROOT_DIR = Path.home() / ".scrum-agent"

# ---------------------------------------------------------------------------
# Data (DB, states, project metadata)
# ---------------------------------------------------------------------------

DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "sessions.db"
STATES_DIR = DATA_DIR / "states"
PROJECTS_FILE = DATA_DIR / "projects.json"

# Legacy paths (for backward compatibility / migration)
LEGACY_DB_PATH = ROOT_DIR / "sessions.db"
LEGACY_STATES_DIR = ROOT_DIR / "states"
LEGACY_PROJECTS_FILE = ROOT_DIR / "projects.json"

# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

EXPORTS_DIR = ROOT_DIR / "exports"
ANALYSIS_EXPORTS_DIR = EXPORTS_DIR / "analysis"
PLANNING_EXPORTS_DIR = EXPORTS_DIR / "planning"
STANDUP_EXPORTS_DIR = EXPORTS_DIR / "standup"
RETRO_EXPORTS_DIR = EXPORTS_DIR / "retro"
POKER_EXPORTS_DIR = EXPORTS_DIR / "poker"
PERFORMANCE_EXPORTS_DIR = EXPORTS_DIR / "performance"
REPORTING_EXPORTS_DIR = EXPORTS_DIR / "reporting"
ROADMAP_EXPORTS_DIR = EXPORTS_DIR / "roadmap"
ANONYMIZE_EXPORTS_DIR = EXPORTS_DIR / "anonymize"  # privacy-masked, shareable copies of any mode's output

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

LOGS_DIR = ROOT_DIR / "logs"
TUI_LOGS_DIR = LOGS_DIR / "tui"
STANDUP_LOGS_DIR = LOGS_DIR / "standup"
RETRO_LOGS_DIR = LOGS_DIR / "retro"
POKER_LOGS_DIR = LOGS_DIR / "poker"
PERFORMANCE_LOGS_DIR = LOGS_DIR / "performance"
REPORTING_LOGS_DIR = LOGS_DIR / "reporting"
ROADMAP_LOGS_DIR = LOGS_DIR / "roadmap"
ANALYSIS_LOGS_DIR = LOGS_DIR / "analysis"
PLANNING_LOGS_DIR = LOGS_DIR / "planning"
MCP_LOGS_DIR = LOGS_DIR / "mcp"

# Legacy log paths
LEGACY_TUI_LOG = ROOT_DIR / "scrum-agent.log"

# ---------------------------------------------------------------------------
# Other
# ---------------------------------------------------------------------------

SCRUM_DOCS_DIR = ROOT_DIR / "scrum-docs"
# Pinned to the default home on purpose — see DEFAULT_ROOT_DIR: this file can
# set YEABOI_HOME, so it can't live inside the directory it relocates.
ENV_FILE = DEFAULT_ROOT_DIR / ".env"
REPL_HISTORY = ROOT_DIR / "repl-history"
BIN_DIR = ROOT_DIR / "bin"  # app-managed helper binaries (e.g. cloudflared for retro tunnels)
ATTACHMENTS_DIR = ROOT_DIR / "attachments"  # screenshots pasted into TUI textboxes (Ctrl+V)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_db_path() -> Path:
    """Return the sessions DB path, migrating from legacy location if needed.

    If both old and new DB exist, merges team_profiles and token_usage from the
    old DB into the new one, then removes the old DB to prevent divergence.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if DB_PATH.exists() and LEGACY_DB_PATH.exists():
        # Both exist — merge legacy data into new DB, then remove legacy
        try:
            import sqlite3

            old = sqlite3.connect(str(LEGACY_DB_PATH))
            new = sqlite3.connect(str(DB_PATH))
            # Copy team_profiles that don't exist in new DB
            try:
                rows = old.execute(
                    "SELECT team_id, profile_json, examples_json, updated_at FROM team_profiles"
                ).fetchall()
                for team_id, pjson, ejson, updated in rows:
                    # Extract source and project_key from team_id (format: "source-key")
                    parts = team_id.split("-", 1)
                    source = parts[0] if len(parts) > 1 else ""
                    proj_key = parts[1] if len(parts) > 1 else team_id
                    new.execute(
                        "INSERT OR IGNORE INTO team_profiles "
                        "(team_id, project_key, source, profile_json, examples_json, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (team_id, proj_key, source, pjson, ejson or "{}", updated or "", updated or ""),
                    )
                new.commit()
            except Exception:
                pass
            old.close()
            new.close()
            LEGACY_DB_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        return DB_PATH

    if not DB_PATH.exists() and LEGACY_DB_PATH.exists():
        LEGACY_DB_PATH.rename(DB_PATH)
        return DB_PATH

    return DB_PATH


def get_analysis_export_dir(project_key: str) -> Path:
    """Return the analysis export directory for a project, creating it if needed."""
    d = ANALYSIS_EXPORTS_DIR / project_key.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_planning_export_dir(project_key: str) -> Path:
    """Return the planning export directory for a project, creating it if needed."""
    d = PLANNING_EXPORTS_DIR / project_key.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_standup_export_dir(project_key: str) -> Path:
    """Return the Daily Standup export directory for a project, creating it if needed."""
    d = STANDUP_EXPORTS_DIR / project_key.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_retro_export_dir(project_key: str) -> Path:
    """Return the Retro export directory for a project, creating it if needed."""
    d = RETRO_EXPORTS_DIR / project_key.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_poker_export_dir(project_key: str) -> Path:
    """Return the Scrum Poker export directory for a project, creating it if needed."""
    d = POKER_EXPORTS_DIR / project_key.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_performance_export_dir(engineer_key: str) -> Path:
    """Return the Performance export directory for an engineer, creating it if needed.

    Exports are per-engineer (1:1 prep/completion summaries, 6-month reviews) so a
    lead can find one person's documents together — mirrors the per-project layout
    the other modes use.
    """
    d = PERFORMANCE_EXPORTS_DIR / (engineer_key.lower() or "engineer")
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_reporting_export_dir(project_key: str) -> Path:
    """Return the Reporting export directory for a project, creating it if needed."""
    d = REPORTING_EXPORTS_DIR / (project_key.lower() or "report")
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_roadmap_export_dir(roadmap_key: str) -> Path:
    """Return the Roadmap export directory for a roadmap, creating it if needed."""
    d = ROADMAP_EXPORTS_DIR / (roadmap_key.lower() or "roadmap")
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_anonymize_export_dir(project_key: str) -> Path:
    """Return the Anonymize export directory for a project, creating it if needed.

    Holds the privacy-masked copies of a mode's output (the shareable versions), kept
    separate from the un-masked exports so the two can't be confused.
    """
    d = ANONYMIZE_EXPORTS_DIR / (project_key.lower() or "output")
    d.mkdir(parents=True, exist_ok=True)
    return d


def move_data_tree(new_root: Path) -> tuple[bool, str]:
    """Best-effort move of the current data tree into *new_root*.

    Used by Settings → Data Dir after the user confirms the move. The source is
    the *currently effective* home (re-read from the environment, not the
    import-time ROOT_DIR constant, so a second change in one session moves from
    the right place). ``.env`` is skipped — it always stays at ~/.yeaboi/.env —
    and so is any child that already exists at the destination. Never raises;
    returns (ok, status message).
    """
    import logging
    import shutil

    raw = os.getenv("YEABOI_HOME", "").strip()
    src_root = Path(raw).expanduser() if raw else DEFAULT_ROOT_DIR
    new_root = new_root.expanduser()
    if src_root == new_root:
        return True, "Data already lives there — nothing to move"
    if not src_root.exists():
        return True, "No existing data to move"
    moved, skipped, failed = 0, 0, 0
    try:
        new_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not create %s: %s", new_root, exc)
        return False, f"Could not create {new_root}: {exc}"
    for child in src_root.iterdir():
        if child.name == ".env":
            skipped += 1
            continue
        target = new_root / child.name
        if target.exists():
            skipped += 1
            continue
        try:
            shutil.move(str(child), str(target))
            moved += 1
        except Exception as exc:
            failed += 1
            logging.getLogger(__name__).warning("Could not move %s -> %s: %s", child, target, exc)
    msg = f"Moved {moved} item(s) to {new_root}"
    if skipped:
        msg += f", skipped {skipped}"
    if failed:
        msg += f", failed {failed} (see log)"
    return failed == 0, msg


def get_tui_log_path() -> Path:
    """Return the main TUI log path."""
    TUI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return TUI_LOGS_DIR / "yeaboi.log"


def get_analysis_log_dir() -> Path:
    """Return the analysis logs directory, creating it if needed."""
    ANALYSIS_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return ANALYSIS_LOGS_DIR


def get_planning_log_dir() -> Path:
    """Return the planning session logs directory, creating it if needed."""
    PLANNING_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return PLANNING_LOGS_DIR


def get_standup_log_dir() -> Path:
    """Return the Daily Standup logs directory, creating it if needed."""
    STANDUP_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return STANDUP_LOGS_DIR


def get_retro_log_dir() -> Path:
    """Return the Retro logs directory, creating it if needed."""
    RETRO_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return RETRO_LOGS_DIR


def get_poker_log_dir() -> Path:
    """Return the Scrum Poker logs directory, creating it if needed."""
    POKER_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return POKER_LOGS_DIR


def get_performance_log_dir() -> Path:
    """Return the Performance logs directory, creating it if needed."""
    PERFORMANCE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return PERFORMANCE_LOGS_DIR


def get_reporting_log_dir() -> Path:
    """Return the Reporting logs directory, creating it if needed."""
    REPORTING_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTING_LOGS_DIR


def get_roadmap_log_dir() -> Path:
    """Return the Roadmap-intake logs directory, creating it if needed."""
    ROADMAP_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return ROADMAP_LOGS_DIR


def get_mcp_log_dir() -> Path:
    """Return the MCP server logs directory, creating it if needed."""
    MCP_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return MCP_LOGS_DIR


def get_bin_dir() -> Path:
    """Return the app-managed helper-binary directory, creating it if needed."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    return BIN_DIR


def get_attachments_dir(scope_id: str) -> Path:
    """Return the pasted-image directory for a session/project scope, creating it if needed.

    Only file *paths* are stored in session state — the PNG/JPEG bytes live here,
    so sessions stay small and pasted screenshots survive ``--resume``.
    """
    d = ATTACHMENTS_DIR / ((scope_id or "misc").lower())
    d.mkdir(parents=True, exist_ok=True)
    return d


def migrate_root_dir() -> None:
    """Migrate the whole config tree from the pre-rebrand ~/.scrum-agent dir.

    yeaboi.ai used to store everything under ``~/.scrum-agent``. On the first
    run after the rebrand, move the entire tree to ``~/.yeaboi`` so existing
    users keep their sessions, credentials, and exports seamlessly. Best-effort
    and idempotent: does nothing once ``~/.yeaboi`` exists, and never raises.
    """
    import logging
    import shutil

    if ROOT_DIR.exists() or not LEGACY_ROOT_DIR.exists():
        return
    try:
        shutil.move(str(LEGACY_ROOT_DIR), str(ROOT_DIR))
    except Exception as exc:  # pragma: no cover - defensive; migration is best-effort
        logging.getLogger(__name__).warning("Could not migrate %s -> %s: %s", LEGACY_ROOT_DIR, ROOT_DIR, exc)


def migrate_legacy_paths() -> None:
    """Migrate files from legacy flat structure to new organised structure.

    Called once at startup. Safe to call multiple times — skips if already migrated.
    """
    import shutil

    # First, move the whole tree over from the pre-rebrand ~/.scrum-agent dir.
    migrate_root_dir()

    # Migrate sessions.db
    if LEGACY_DB_PATH.exists() and not DB_PATH.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_DB_PATH.rename(DB_PATH)

    # Migrate states/
    if LEGACY_STATES_DIR.exists() and not STATES_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_STATES_DIR.rename(STATES_DIR)

    # Migrate projects.json
    if LEGACY_PROJECTS_FILE.exists() and not PROJECTS_FILE.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_PROJECTS_FILE.rename(PROJECTS_FILE)

    # Migrate main log (flat ROOT_DIR/scrum-agent.log → logs/tui/yeaboi.log)
    new_tui_log = TUI_LOGS_DIR / "yeaboi.log"
    if LEGACY_TUI_LOG.exists() and not new_tui_log.exists():
        TUI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        LEGACY_TUI_LOG.rename(new_tui_log)
        # Also move rotated logs
        for rot in ROOT_DIR.glob("scrum-agent.log.*"):
            rot.rename(TUI_LOGS_DIR / rot.name.replace("scrum-agent.log", "yeaboi.log"))

    # Migrate a previously-organised pre-rebrand log (logs/tui/scrum-agent.log → yeaboi.log)
    old_organised_log = TUI_LOGS_DIR / "scrum-agent.log"
    if old_organised_log.exists() and not new_tui_log.exists():
        old_organised_log.rename(new_tui_log)
        for rot in TUI_LOGS_DIR.glob("scrum-agent.log.*"):
            rot.rename(TUI_LOGS_DIR / rot.name.replace("scrum-agent.log", "yeaboi.log"))

    # Migrate analysis logs (team-analysis-*.log → logs/analysis/)
    if LOGS_DIR.exists():
        for f in LOGS_DIR.glob("team-analysis-*.log"):
            ANALYSIS_LOGS_DIR.mkdir(parents=True, exist_ok=True)
            f.rename(ANALYSIS_LOGS_DIR / f.name)

    # Migrate planning session logs (UUID.log → logs/planning/)
    if LOGS_DIR.exists():
        import re

        uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-.*\.log$")
        for f in LOGS_DIR.glob("*.log"):
            if uuid_re.match(f.name):
                PLANNING_LOGS_DIR.mkdir(parents=True, exist_ok=True)
                f.rename(PLANNING_LOGS_DIR / f.name)

    # Migrate exports/{project_key}/ → exports/analysis/{project_key}/
    if EXPORTS_DIR.exists():
        for d in EXPORTS_DIR.iterdir():
            if d.is_dir() and d.name not in ("analysis", "planning"):
                # Check if it has team-profile files (analysis exports)
                has_analysis = any(f.name.startswith("team-profile") for f in d.iterdir() if f.is_file())
                if has_analysis:
                    target = ANALYSIS_EXPORTS_DIR / d.name
                    if not target.exists():
                        ANALYSIS_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(d), str(target))
