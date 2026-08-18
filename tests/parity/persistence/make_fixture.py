"""Fixture builder for the W9 persistence parity gate.

Builds deterministic seeded SQLite databases at every ladder version v1…v29,
plus the pathological lineages the W9 spec names. Both sides of the gate —
``SessionStore`` today, ``yeaboi __migrate-db`` from W9 phase 2 on — migrate a
copy of the same fixture and their canonical dumps (``dump.py``) are diffed.

**How a historical shape is reconstructed.** The squashed git history cannot
serve the era DDL, so a fixture at version N is built from *today's* schema
constants, thinned back to N by the two registries below:

- ``LADDER_TABLES`` — the version whose migration first created each table.
  Building at N executes only the CREATE statements of tables with
  ``version <= N`` (a table's indexes travel with it).
- ``LADDER_COLUMNS`` — every ``ALTER TABLE … ADD COLUMN`` the ladder itself
  issues, keyed by version. Columns added after N are dropped from the built
  fixture, so migrating it genuinely exercises those ALTERs.
- ``STORE_COLUMNS`` — columns only a mode store's open-time ALTER adds
  (``StandupStore.__init__`` etc., never the ladder). They are dropped at
  *every* version: a real DB that has only ever been opened through
  ``SessionStore`` lacks them, and the ladder must not be credited with adding
  them. Store-open DDL is W9 phase 4's subject (``OpenStoreDDL`` + the
  store-open-only fixtures), not this builder's.

The registries are pinned by the gate's self-guards: the ALTER set is
regex-checked against ``sessions.py``'s source, and the table set against a
fresh ``SessionStore`` migration — an upstream ladder change fails the guard
by name until this builder learns it.

Seeding is deterministic (no wall clock, no randomness, no hash()): every
table existing at N receives three rows drawn from the nasty-string palette —
NBSP, U+2028, astral emoji, Turkish İ, ``|``, embedded quotes/newlines, NULLs
in nullable columns, huge ints, and floats that exercise JSON widening — so
the v11 seeding, the v17 derivation and the v26 repair migrate *data*, not
just DDL. Timestamp-named columns get pinned ISO strings so the committed
goldens stay hermetic.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# The real, imported schema constants — the builder never copies DDL that has
# a living source (drift is caught by the fresh-migration self-guard).
from yeaboi.agent.prior_art_feedback import PRIOR_ART_FEEDBACK_SCHEMA
from yeaboi.agentwatch.store import _AGENTWATCH_SCHEMA
from yeaboi.artifacts.store import _ARTIFACT_EDITS_SCHEMA
from yeaboi.performance.store import _PERFORMANCE_SCHEMA
from yeaboi.poker.store import _POKER_SCHEMA
from yeaboi.reporting.store import _REPORTING_SCHEMA
from yeaboi.retro.store import _RETRO_SCHEMA
from yeaboi.roadmap.store import _ROADMAP_SCHEMA
from yeaboi.sessions import _SCHEMA, _SCHEMA_INFO
from yeaboi.standup.store import _STANDUP_SCHEMA
from yeaboi.team_profile import (
    _ANALYSIS_ENRICHMENT_CACHE_SCHEMA,
    _ANALYSIS_TICKET_CACHE_SCHEMA,
    _TEAM_PROFILES_SCHEMA,
)

# The one CREATE without an importable constant: sessions.py's v5 migration
# inlines it. Copied verbatim (thin — the v12 timing columns arrive by ALTER);
# the fresh-migration guard catches divergence via the column-set comparison.
_TOKEN_USAGE_V5 = """\
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT ''
)"""

# ---------------------------------------------------------------------------
# The two ladder registries
# ---------------------------------------------------------------------------

# {table: ladder version whose migration first created it}. Versions follow
# sessions.py's own migration comments (v6 created three standup tables; the
# transcript-review trio arrived with v22, the feedback ledger with v25).
# `agent_advisor_reports` is listed at 27 because *today's* v27 replays the
# whole agentwatch script — the store-open lineage that predates it is phase
# 4's subject. `roadmaps` is 11: a real v10 database lacks it, which is
# exactly what makes the v11 data seeding reachable.
LADDER_TABLES: dict[str, int] = {
    "sessions_meta": 0,
    "schema_info": 0,
    "team_profiles": 3,
    "token_usage": 5,
    "standup_config": 6,
    "standup_history": 6,
    "standup_updates": 6,
    "retro_history": 7,
    "performance_one_on_ones": 8,
    "performance_reviews": 8,
    "performance_notes": 8,
    "reporting_history": 9,
    "roadmap_config": 10,
    "roadmap_history": 10,
    "roadmaps": 11,
    "analysis_ticket_cache": 13,
    "poker_history": 18,
    "analysis_enrichment_cache": 19,
    "artifact_edits": 21,
    "standup_reviews": 22,
    "standup_transcripts": 22,
    "standup_gap_issues": 22,
    "standup_practice_feedback": 25,
    "agent_ingest_files": 27,
    "agent_sessions": 27,
    "agent_security_findings": 27,
    "agent_usage_reports": 27,
    "agent_standup_digests": 27,
    "agent_security_reports": 27,
    "agent_advisor_reports": 27,
    "planning_prior_art_feedback": 30,
}

# {(table, column): ladder version whose ALTER added it}. The self-guard
# regex-checks this against sessions.py's source; the v21 provenance pairs are
# spelled out here because _apply_edit_provenance builds its ALTERs from an
# f-string the regex cannot see.
_PROVENANCE_TABLES = (
    "standup_history",
    "retro_history",
    "reporting_history",
    "roadmap_history",
    "performance_one_on_ones",
    "performance_reviews",
)
LADDER_COLUMNS: dict[tuple[str, str], int] = {
    ("sessions_meta", "session_mode"): 4,
    ("token_usage", "duration_ms"): 12,
    ("token_usage", "eval_duration_ms"): 12,
    ("token_usage", "load_duration_ms"): 12,
    ("token_usage", "tokens_per_sec"): 12,
    ("standup_config", "tracker_sources"): 14,
    ("standup_config", "team_members"): 14,
    ("standup_config", "roster_configured"): 14,
    ("standup_config", "code_sources"): 15,
    ("standup_config", "github_repositories"): 15,
    ("standup_config", "azdo_repositories"): 15,
    ("standup_config", "code_scope_configured"): 15,
    ("standup_config", "documentation_sources"): 16,
    ("standup_config", "documentation_scope_configured"): 16,
    ("standup_config", "azdo_projects"): 17,
    ("analysis_runs", "features_json"): 20,
    ("standup_config", "transcript_dir"): 22,
    ("standup_config", "transcript_review_enabled"): 22,
    ("standup_config", "habit_detection"): 23,
    ("standup_config", "habit_rules"): 23,
    ("standup_config", "habit_ai_match"): 24,
    ("standup_config", "github_owners"): 28,
    ("standup_config", "github_excluded_repositories"): 29,
    **{(table, column): 21 for table in _PROVENANCE_TABLES for column in ("origin", "edited_from_id")},
}

# Columns that exist in today's fat CREATEs but are only ever added to an old
# database by a *store's* open-time ALTER — never by the ladder. Dropped at
# every version: a ladder-only database never gains them, and crediting the
# ladder with them would make the Go port look wrong for reproducing the
# truth. The self-guard scans the store modules' sources for this set.
STORE_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("standup_config", "lead_minutes"),
        ("standup_config", "my_aliases"),
        ("standup_config", "automation_markers"),
        ("standup_config", "automation_handling"),
        ("standup_updates", "images_json"),
        ("team_profiles", "examples_json"),
    }
)

# {version: schema script the ladder executes at that version}. v21 is the
# artifacts script (the ALTER half of _apply_edit_provenance is covered by
# LADDER_COLUMNS); replays (v11, v22) add nothing here because the builder
# filters CREATEs by LADDER_TABLES anyway.
_VERSION_SCRIPTS: dict[int, str] = {
    0: _SCHEMA + "\n" + _SCHEMA_INFO,
    3: _TEAM_PROFILES_SCHEMA,
    5: _TOKEN_USAGE_V5,
    6: _STANDUP_SCHEMA,
    7: _RETRO_SCHEMA,
    8: _PERFORMANCE_SCHEMA,
    9: _REPORTING_SCHEMA,
    10: _ROADMAP_SCHEMA,
    13: _ANALYSIS_TICKET_CACHE_SCHEMA,
    18: _POKER_SCHEMA,
    19: _ANALYSIS_ENRICHMENT_CACHE_SCHEMA,
    21: _ARTIFACT_EDITS_SCHEMA,
    27: _AGENTWATCH_SCHEMA,
    30: PRIOR_ART_FEEDBACK_SCHEMA,
}

# The ladder columns whose *base CREATE is thin* (it lives in sessions.py, not
# a store), so building a version at-or-past their ALTER must ADD them rather
# than finding them in the CREATE. DDL copied verbatim from the ladder; the
# alter-registry self-guard pins the names against sessions.py's source.
_THIN_BASE_ADDS: dict[tuple[str, str], str] = {
    ("sessions_meta", "session_mode"): "TEXT NOT NULL DEFAULT 'planning'",
    ("token_usage", "duration_ms"): "REAL",
    ("token_usage", "eval_duration_ms"): "REAL",
    ("token_usage", "load_duration_ms"): "REAL",
    ("token_usage", "tokens_per_sec"): "REAL",
}

LADDER_MIN = 1
LADDER_MAX = 29  # fixtures stop below CURRENT_SCHEMA_VERSION: v30 is only ever reached by migrating


# ---------------------------------------------------------------------------
# DDL plumbing
# ---------------------------------------------------------------------------


def _statements(script: str) -> list[str]:
    """Split a schema script into complete statements, comments stripped.

    ``sqlite3.complete_statement`` rather than a ``";"`` split — the artifact
    script carries a semicolon inside an SQL comment. Comments are stripped
    because SQLite's ``DROP COLUMN`` performs textual surgery on the stored
    CREATE and chokes ("incomplete input") when a comment sits against the
    dropped column; the canonical dump never reads ``sqlite_master.sql``, so
    nothing observable is lost.
    """
    statements, buffer = [], ""
    for line in script.splitlines():
        line = _strip_comment(line)
        if not line.strip():
            continue
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        statements.append(buffer.strip().rstrip(";"))
    return statements


def _strip_comment(line: str) -> str:
    """Cut a ``--`` comment off a line, respecting single-quoted literals."""
    index, in_string = 0, False
    while index < len(line) - 1:
        ch = line[index]
        if ch == "'":
            in_string = not in_string
        elif ch == "-" and line[index + 1] == "-" and not in_string:
            return line[:index].rstrip()
        index += 1
    return line


def _statement_table(statement: str) -> str:
    """The table a CREATE TABLE / CREATE INDEX statement belongs to."""
    import re

    m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", statement)
    if m:
        return m.group(1)
    m = re.search(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+\s+ON\s*(\w+)", statement)
    if m:
        return m.group(1)
    raise ValueError(f"unrecognised schema statement: {statement[:80]!r}")


def _apply_ddl(conn: sqlite3.Connection, version: int, omit_versions: frozenset[int]) -> None:
    """Execute every ladder CREATE that exists at *version*, then thin the
    columns added after it (and the store-only ones at any version)."""
    for step in sorted(_VERSION_SCRIPTS):
        if step > version or step in omit_versions:
            continue
        for statement in _statements(_VERSION_SCRIPTS[step]):
            table = _statement_table(statement)
            created = LADDER_TABLES.get(table)
            if created is None or created > version or created in omit_versions:
                continue
            conn.execute(statement)

    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    to_drop = [(t, c) for (t, c), added in LADDER_COLUMNS.items() if added > version or added in omit_versions]
    to_drop += [(t, c) for t, c in STORE_COLUMNS]
    for table, column in to_drop:
        if table in present:
            try:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            except sqlite3.OperationalError:
                # Already absent: the thin-base columns (_THIN_BASE_ADDS)
                # were never in their CREATE to begin with. The gate's
                # thinness self-guard pins the final shape column-by-column,
                # so a typo here cannot hide behind this except.
                pass

    for (table, column), ddl in _THIN_BASE_ADDS.items():
        added = LADDER_COLUMNS[(table, column)]
        if added <= version and added not in omit_versions and table in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# ---------------------------------------------------------------------------
# Deterministic seeding
# ---------------------------------------------------------------------------

# The nasty-string palette the spec names. Values are prefixes — a per-cell
# uniqueness tag is appended so PRIMARY KEY / UNIQUE columns never collide.
PALETTE = (
    "plain value",
    "nbsp\u00a0and em\u2003space",
    "line\u2028sep and\r\ncrlf",
    "emoji 😀🐍 astral 𝔘𝔫𝔦",
    "turkish İstanbul ı",
    "pipe | and *emphasis* _under_",
    "quotes \"double\" 'single' `tick`",
    "newline\nand\ttab",
    "escape \\back \x00 null-byte",
    "ünïcode côté ¡final!",
)

HUGE_INT = 9223372036854775807
_INTS = (7, -3, HUGE_INT)
# 3.0 pins the json widening trap (`3` vs `3.0`); 0.1 pins repr precision.
_FLOATS = (1.5, 3.0, 0.1)
ROWS_PER_TABLE = 3


def _pinned_timestamp(cid: int, row: int) -> str:
    return f"2026-01-{10 + row:02d}T{cid % 24:02d}:15:{row:02d}+00:00"


def _seed_value(table: str, cid: int, name: str, decltype: str, notnull: int, pk: int, row: int):
    """One deterministic cell. Returns ``...`` (Ellipsis) for omit-the-column
    (single-column INTEGER PRIMARY KEY — let SQLite assign rowids so
    ``sqlite_sequence`` is exercised)."""
    kind = decltype.upper()
    if pk and "INT" in kind:
        return ...
    if not pk and not notnull and row == 2:
        return None
    if name.endswith("_at") or name in ("timestamp", "last_modified"):
        return _pinned_timestamp(cid, row)
    if "INT" in kind:
        return _INTS[row] + (cid if row == 0 else 0)
    if "REAL" in kind or "FLOA" in kind or "DOUB" in kind:
        return _FLOATS[row]
    base = PALETTE[(cid * 3 + row) % len(PALETTE)]
    return f"{base} ⟨{table}:{cid}:{row}⟩"


def _seed_tables(conn: sqlite3.Connection) -> None:
    """Insert ROWS_PER_TABLE deterministic rows into every user table.

    Insert failures are expected and skipped deterministically — e.g.
    ``roadmap_config``'s CHECK (id = 1) admits a single row.
    """
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT IN ('schema_info', 'sqlite_sequence') ORDER BY name"
        )
    ]
    for table in tables:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        for row_index in range(ROWS_PER_TABLE):
            columns, values = [], []
            for cid, name, decltype, notnull, _default, pk in info:
                value = _seed_value(table, cid, name, decltype, notnull, pk, row_index)
                if value is ...:
                    continue
                columns.append(name)
                values.append(value)
            placeholders = ", ".join("?" for _ in values)
            try:
                conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", values)
            except sqlite3.IntegrityError:
                pass  # single-row CHECK constraints etc. — deterministic either way


# ---------------------------------------------------------------------------
# Targeted seeds — the data the ladder's data migrations actually read
# ---------------------------------------------------------------------------

# What the v17 derivation must produce for _AZDO_CORPUS: first-'/' partition,
# str() coercion of the numeric entry, empty-project and no-separator entries
# skipped, order-preserving dedupe. Asserted by the gate's corpus guard.
AZDO_CORPUS = '["Proj/Repo", "Proj/Repo2", "proj2/x", "Proj/Repo", "norepo", "/leading", "Deep/a/b", 123]'
AZDO_DERIVED = json.dumps(["Proj", "proj2", "Deep"])


def _seed_azdo_repositories(conn: sqlite3.Connection) -> None:
    """Rows the v17 azdo_projects derivation will read: the partition corpus,
    invalid JSON (the except branch), and an empty list (no UPDATE)."""
    for session_id, repositories in (
        ("azdo-derive", AZDO_CORPUS),
        ("azdo-invalid-json", "not-json["),
        ("azdo-empty", "[]"),
    ):
        conn.execute(
            "INSERT INTO standup_config (session_id, created_at, updated_at, azdo_repositories) VALUES (?, ?, ?, ?)",
            (session_id, _pinned_timestamp(0, 0), _pinned_timestamp(0, 1), repositories),
        )


def _seed_roadmap_singleton(conn: sqlite3.Connection) -> None:
    """The v10 singleton the v11 seeding reads: a saved config plus history
    rows whose run_at ordering picks the newer analysis."""
    conn.execute("DELETE FROM roadmap_config")
    conn.execute(
        "INSERT INTO roadmap_config (id, source_type, source_locator, source_label, updated_at) "
        "VALUES (1, 'confluence', 'SPACE/Roadmap|2026 😀', 'Roadmap Label', '2026-01-05T09:00:00+00:00')"
    )
    conn.execute("DELETE FROM roadmap_history")
    _seed_roadmap_history(conn)


def _seed_roadmap_history_fallback(conn: sqlite3.Connection) -> None:
    """The other v11 branch: no saved config row at all, so the seed falls
    back to the newest history row and the locator doubles as the label."""
    conn.execute("DELETE FROM roadmap_config")
    conn.execute("DELETE FROM roadmap_history")
    _seed_roadmap_history(conn)


def _seed_roadmap_history(conn: sqlite3.Connection) -> None:
    rows = (
        ("2026-01-03T08:00:00+00:00", "confluence", "SPACE/Old", 2, '{"projects": ["old"]}'),
        ("2026-01-04T08:00:00+00:00", "pdf", "plans/road İ|map.pdf", 3, '{"projects": ["a", "b"], "score": 3.0}'),
    )
    for run_at, source_type, locator, count, analysis in rows:
        conn.execute(
            "INSERT INTO roadmap_history (run_at, source_type, source_locator, project_count, analysis_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_at, source_type, locator, count, analysis),
        )


def _seed_azdo_already_derived(conn: sqlite3.Connection) -> None:
    """A row whose azdo_projects is already non-'[]' — the v17 re-run (the
    crash lineage) must leave it untouched."""
    conn.execute(
        "INSERT INTO standup_config (session_id, created_at, updated_at, azdo_repositories, azdo_projects) "
        "VALUES ('azdo-already-derived', ?, ?, '[\"Other/Repo\"]', '[\"Kept\"]')",
        (_pinned_timestamp(1, 0), _pinned_timestamp(1, 1)),
    )


# Fixture version → extra seeding after the generic pass. v15 and v16 carry
# the derivation corpus so the v17 data migration reads real rows; v10 seeds
# the singleton the v11 migration reads.
_TARGETED_SEEDS: dict[int, tuple[Callable[[sqlite3.Connection], None], ...]] = {
    10: (_seed_roadmap_singleton,),
    15: (_seed_azdo_repositories,),
    16: (_seed_azdo_repositories,),
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_at(
    path: Path,
    version: int,
    *,
    stamp: int | None = None,
    omit_versions: frozenset[int] = frozenset(),
    extra_seeds: tuple[Callable[[sqlite3.Connection], None], ...] = (),
) -> None:
    """Build a seeded database whose shape is ladder version *version*,
    stamped *stamp* (default: the shape's own version)."""
    conn = sqlite3.connect(str(path))
    conn.isolation_level = None
    try:
        _apply_ddl(conn, version, omit_versions)
        _seed_tables(conn)
        for seed in _TARGETED_SEEDS.get(version, ()):
            seed(conn)
        for seed in extra_seeds:
            seed(conn)
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (?)", (version if stamp is None else stamp,))
    finally:
        conn.close()


def _build_pre_8c(path: Path) -> None:
    """A pre-8C database: sessions_meta only, no schema_info table at all.
    Opening stamps the current version and takes the full ladder."""
    conn = sqlite3.connect(str(path))
    conn.isolation_level = None
    try:
        conn.execute(_SCHEMA)
        _seed_tables(conn)
    finally:
        conn.close()


def _build_phase_8a(path: Path) -> None:
    """A phase-8A database: sessions_meta without session_state, no
    schema_info. Opening exercises the unconditional 8B ALTER."""
    conn = sqlite3.connect(str(path))
    conn.isolation_level = None
    try:
        conn.execute(_SCHEMA)
        conn.execute("ALTER TABLE sessions_meta DROP COLUMN session_state")
        _seed_tables(conn)
    finally:
        conn.close()


def _build_duplicate_schema_info(path: Path) -> None:
    """Racing first-opens left two schema_info rows; the dedupe must keep the
    higher version and migrate from it."""
    build_at(path, 12)
    conn = sqlite3.connect(str(path))
    conn.isolation_level = None
    try:
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (7)")
    finally:
        conn.close()


def _build_v21_collision(path: Path) -> None:
    """The pre-rebase lineage: stamped 25 with v21's DDL (artifact_edits +
    the provenance columns) missing — the v26 repair's reason to exist."""
    build_at(path, 25, omit_versions=frozenset({21}))


def _build_v31_future(path: Path) -> None:
    """A database from the future: stamped past CURRENT_SCHEMA_VERSION with
    the provenance surface missing. Opening must set schema_mismatch, apply
    the v26 self-heal, and write nothing else."""
    build_at(path, 30, stamp=31, omit_versions=frozenset({21}))


def _build_crash_mid_migration(path: Path) -> None:
    """DDL ran ahead of the stamp (a crash between the two): shape v20,
    stamped 15. Re-migration must be idempotent, and the v17 derivation must
    re-run for undived rows while leaving the already-derived one alone."""
    build_at(
        path,
        20,
        stamp=15,
        extra_seeds=(_seed_azdo_repositories, _seed_azdo_already_derived),
    )


@dataclass(frozen=True)
class Fixture:
    """One buildable database. ``version`` is the shape for regular ladder
    fixtures and None for the pathological lineages."""

    name: str
    build: Callable[[Path], None]
    version: int | None = None


def _regular(version: int) -> Fixture:
    return Fixture(f"v{version:02d}", lambda path, v=version: build_at(path, v), version)


FIXTURES: list[Fixture] = [_regular(v) for v in range(LADDER_MIN, LADDER_MAX + 1)] + [
    Fixture("pre-8c-no-schema-info", _build_pre_8c),
    Fixture("phase-8a-no-session-state", _build_phase_8a),
    Fixture("duplicate-schema-info-rows", _build_duplicate_schema_info),
    Fixture("v21-collision-lineage", _build_v21_collision),
    Fixture("v31-from-the-future", _build_v31_future),
    Fixture("crash-mid-migration", _build_crash_mid_migration),
    Fixture(
        "v10-roadmap-history-fallback", lambda path: build_at(path, 10, extra_seeds=(_seed_roadmap_history_fallback,))
    ),
]


def fixture_by_name(name: str) -> Fixture:
    return {f.name: f for f in FIXTURES}[name]
