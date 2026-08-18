"""Freeze + corpus guards for the W9 persistence parity gate.

Phase 1 pins the Python side: every fixture, migrated through the real
``SessionStore``, must dump to its committed golden — the drift detector for
anyone editing sessions.py's ladder — and the corpus self-guards keep the
fixture builder honest against the living source (registries vs sessions.py,
tables vs a fresh migration, thinness vs the ladder's own ALTERs, the
nasty-string palette actually reaching the seeds).

W9 phase 2 arms the binary side: ``yeaboi __migrate-db`` + ``__dump-db``
replay the same fixtures and the two dumps are byte-compared, skipping only
when ``YEABOI_CLI_BIN`` is absent (the existing pattern; CI builds it).

To regenerate after a deliberate behaviour change:
``uv run python -m tests.parity.persistence.regen``.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
import subprocess
import sys

import pytest

from tests.parity.persistence import dump as dump_mod
from tests.parity.persistence import make_fixture, regen
from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore


def _build(fixture: make_fixture.Fixture, tmp_path):
    db = tmp_path / f"{fixture.name}.db"
    fixture.build(db)
    return db


def _columns(db, table: str) -> list[str]:
    conn = sqlite3.connect(str(db))
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _tables(db) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {r[0] for r in rows} - {"sqlite_sequence"}
    finally:
        conn.close()


@pytest.fixture(scope="module")
def fresh_migrated_db(tmp_path_factory):
    """A database created by SessionStore alone — _run_migrations(0)'s full
    ladder, the reference every fixture must converge toward."""
    db = tmp_path_factory.mktemp("fresh") / "sessions.db"
    SessionStore(db).close()
    return db


@pytest.mark.parametrize("fixture", make_fixture.FIXTURES, ids=lambda f: f.name)
def test_migrated_dump_matches_committed_golden(fixture, tmp_path):
    """The Python side is frozen: a sessions.py ladder change must regenerate
    the goldens deliberately, never drift silently."""
    golden_file = regen.golden_path(fixture)
    assert golden_file.exists(), f"missing golden {golden_file} — run `uv run python -m tests.parity.persistence.regen`"
    expected = json.loads(golden_file.read_text(encoding="utf-8"))
    db = _build(fixture, tmp_path)
    got = dump_mod.migrate_and_dump(db)
    assert got == expected, (
        f"fixture {fixture.name}: live migration disagrees with the committed golden — if the "
        "sessions.py change is deliberate, regenerate (and mirror go/internal/sessions first)"
    )


def test_goldens_and_fixtures_correspond_one_to_one():
    committed = {p.stem for p in regen.GOLDENS_DIR.glob("*.json")}
    assert committed == {f.name for f in make_fixture.FIXTURES}, (
        "goldens and fixtures diverged — regenerate (stale files must be deleted, new fixtures must be dumped)"
    )


def test_build_and_dump_are_deterministic(tmp_path):
    """Two independent builds of the same fixture must migrate to
    byte-identical dumps — the property that makes the goldens hermetic."""
    fixture = make_fixture.fixture_by_name("v29")
    renders = []
    for arm in ("a", "b"):
        db = tmp_path / arm / "sessions.db"
        db.parent.mkdir()
        fixture.build(db)
        renders.append(dump_mod.render(dump_mod.migrate_and_dump(db)))
    assert renders[0] == renders[1]


def test_dump_script_runs_as_a_subprocess(tmp_path):
    """The script arm phase 2's E2E drives: same output as in-process."""
    db = _build(make_fixture.fixture_by_name("v05"), tmp_path)
    expected = regen.golden_path(make_fixture.fixture_by_name("v05")).read_text(encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(dump_mod.__file__), str(db)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout == expected


class TestCorpusSelfGuards:
    """Pure-Python guards that the fixture builder stays honest against the
    living sessions.py — they run in the ordinary suite, binary or not."""

    def test_fixture_names_are_unique(self):
        names = [f.name for f in make_fixture.FIXTURES]
        assert len(names) == len(set(names))

    def test_every_ladder_version_has_a_fixture(self):
        versions = {f.version for f in make_fixture.FIXTURES if f.version is not None}
        assert versions >= set(range(make_fixture.LADDER_MIN, make_fixture.LADDER_MAX + 1))
        assert make_fixture.LADDER_MAX == CURRENT_SCHEMA_VERSION - 1, (
            "the ladder moved under the wave — extend the fixtures to the new version and regenerate"
        )

    def test_ladder_alter_registry_matches_sessions_source(self):
        """Every named ALTER in sessions.py must be in LADDER_COLUMNS (and
        nothing stale). The v12 timing columns and the v21 provenance pairs
        are f-string-built in the ladder, so they are asserted structurally
        against their loop tuples instead."""
        source = inspect.getsource(sys.modules["yeaboi.sessions"])
        scanned = set()
        for table, column in re.findall(r"ALTER TABLE (\w+)\s+ADD COLUMN (\w+)", source):
            scanned.add((table, column))
        # The unconditional 8B ALTER is base schema, not a versioned step.
        scanned.discard(("sessions_meta", "session_state"))
        registered = {tc for tc, version in make_fixture.LADDER_COLUMNS.items() if version not in (12, 21)}
        assert scanned == registered, (
            "sessions.py's ALTERs and the fixture builder diverged — update LADDER_COLUMNS "
            f"(and regenerate): {sorted(scanned ^ registered)}"
        )
        v12 = {c for (t, c), version in make_fixture.LADDER_COLUMNS.items() if version == 12 and t == "token_usage"}
        body = inspect.getsource(SessionStore._run_migrations)
        for column in v12:
            assert f'"{column}"' in body, f"token_usage.{column} left the v12 loop tuple"
        provenance = {tc for tc, version in make_fixture.LADDER_COLUMNS.items() if version == 21}
        body = inspect.getsource(SessionStore._apply_edit_provenance)
        for table, column in provenance:
            assert table in body and column in body, f"{table}.{column} left _apply_edit_provenance"

    def test_store_column_registry_matches_store_sources(self):
        """STORE_COLUMNS must equal the stores' own ADD COLUMN sets minus
        what the ladder manages — scanning the sources so a new store-side
        ALTER cannot land without the builder learning it."""
        import yeaboi.performance.store
        import yeaboi.reporting.store
        import yeaboi.retro.store
        import yeaboi.roadmap.store
        import yeaboi.standup.store
        import yeaboi.team_profile

        scanned = set()
        for module in (
            yeaboi.standup.store,
            yeaboi.retro.store,
            yeaboi.reporting.store,
            yeaboi.roadmap.store,
            yeaboi.performance.store,
            yeaboi.team_profile,
        ):
            source = inspect.getsource(module)
            for table, column in re.findall(r"ALTER TABLE (\w+)\s+ADD COLUMN (\w+)", source):
                scanned.add((table, column))
        # Performance heals provenance through an f-string over its two
        # report tables — count those as ladder-managed like the rest.
        for table in ("performance_one_on_ones", "performance_reviews"):
            scanned.update({(table, "origin"), (table, "edited_from_id")})
        # analysis_runs is store-open-only (phase 4's subject); its ALTERs
        # never apply to a ladder-built fixture.
        scanned = {tc for tc in scanned if tc[0] != "analysis_runs"}
        expected = scanned - set(make_fixture.LADDER_COLUMNS)
        assert set(make_fixture.STORE_COLUMNS) == expected, (
            "store-side ALTERs and STORE_COLUMNS diverged — update the builder "
            f"(and regenerate): {sorted(set(make_fixture.STORE_COLUMNS) ^ expected)}"
        )

    def test_ladder_table_registry_matches_a_fresh_migration(self, fresh_migrated_db):
        """A fresh SessionStore database must contain exactly the registry's
        tables — a new table upstream fails here by name until the builder
        learns which version created it."""
        expected = set(make_fixture.LADDER_TABLES)
        assert _tables(fresh_migrated_db) == expected, (
            f"ladder tables diverged from LADDER_TABLES: {sorted(_tables(fresh_migrated_db) ^ expected)}"
        )

    def test_fixtures_are_historically_thin(self, tmp_path):
        """At every version N: a ladder table exists iff created at <= N, a
        ladder column exists iff added at <= N, and store-only columns never
        exist — the property that makes migrating the fixture exercise the
        real ALTERs instead of no-op'ing through try/except."""
        for fixture in make_fixture.FIXTURES:
            if fixture.version is None:
                continue
            db = _build(fixture, tmp_path)
            tables = _tables(db)
            for table, created in make_fixture.LADDER_TABLES.items():
                assert (table in tables) == (created <= fixture.version), (fixture.name, table)
            for (table, column), added in make_fixture.LADDER_COLUMNS.items():
                if table not in tables:
                    continue
                assert (column in _columns(db, table)) == (added <= fixture.version), (fixture.name, table, column)
            for table, column in make_fixture.STORE_COLUMNS:
                if table in tables:
                    assert column not in _columns(db, table), (fixture.name, table, column)

    def test_nasty_corpus_reaches_the_seeds(self, tmp_path):
        """The seeded data must keep the traps the spec names."""
        db = _build(make_fixture.fixture_by_name("v29"), tmp_path)
        conn = sqlite3.connect(str(db))
        try:
            dump = dump_mod.canonical_dump(conn)
        finally:
            conn.close()
        cells = [v for t in dump.values() for row in t["rows"] for v in row]
        text = "".join(v for v in cells if isinstance(v, str))
        assert "\u00a0" in text, "the NBSP vector left the corpus"
        assert "\u2028" in text, "the U+2028 line separator left the corpus"
        assert any(ord(c) > 0xFFFF for c in text), "the astral-plane vector left the corpus"
        assert "İ" in text, "the Turkish İ vector left the corpus"
        assert "|" in text and '"' in text and "\n" in text, "the markdown/quote/newline vectors left the corpus"
        assert "\x00" in text, "the embedded-NUL vector left the corpus"
        assert make_fixture.HUGE_INT in cells, "the huge-int vector left the corpus"
        assert any(isinstance(v, float) and v == int(v) for v in cells), (
            "the float-widening vector (a REAL with integral value) left the corpus"
        )
        assert None in cells, "the NULL vector left the corpus"

    def test_data_migration_seeds_still_take_their_branches(self):
        """The corpus guard for the three data migrations: the frozen goldens
        must show the v11 seeding, the v17 derivation and the v26 repair
        having actually moved data, not just DDL."""
        v10 = json.loads(regen.golden_path(make_fixture.fixture_by_name("v10")).read_text(encoding="utf-8"))
        (roadmap_row,) = v10["dump"]["roadmaps"]["rows"]
        columns = [c[1] for c in v10["dump"]["roadmaps"]["columns"]]
        row = dict(zip(columns, roadmap_row))
        assert row["label"] == "Roadmap Label" and row["source_type"] == "confluence"
        assert row["analysis_json"] == '{"projects": ["a", "b"], "score": 3.0}', (
            "the v11 seeding stopped taking the newest history row"
        )

        fallback = json.loads(
            regen.golden_path(make_fixture.fixture_by_name("v10-roadmap-history-fallback")).read_text(encoding="utf-8")
        )
        (fb_row,) = fallback["dump"]["roadmaps"]["rows"]
        fb = dict(zip(columns, fb_row))
        assert fb["label"] == fb["source_locator"] == "plans/road İ|map.pdf", (
            "the v11 locator-doubles-as-label fallback left the corpus"
        )

        v15 = json.loads(regen.golden_path(make_fixture.fixture_by_name("v15")).read_text(encoding="utf-8"))
        config_columns = [c[1] for c in v15["dump"]["standup_config"]["columns"]]
        configs = {
            row[config_columns.index("session_id")]: row[config_columns.index("azdo_projects")]
            for row in v15["dump"]["standup_config"]["rows"]
        }
        assert configs["azdo-derive"] == make_fixture.AZDO_DERIVED, (
            "the v17 derivation corpus (partition, str() coercion, ordered dedupe) stopped deriving"
        )
        assert configs["azdo-invalid-json"] == "[]" and configs["azdo-empty"] == "[]"


class TestPathologicalLineages:
    """The open-semantics behaviours the pathological fixtures exist to pin.
    The goldens freeze the full outcomes; these assert the branch each
    lineage is *for* actually fired, so the corpus cannot rot into a set of
    healthy databases."""

    def test_pre_8c_and_8a_take_the_full_ladder(self, tmp_path):
        for name in ("pre-8c-no-schema-info", "phase-8a-no-session-state"):
            db = _build(make_fixture.fixture_by_name(name), tmp_path)
            store = SessionStore(db)
            assert store.schema_mismatch is False
            store.close()
            assert "session_state" in _columns(db, "sessions_meta")
            assert "planning_prior_art_feedback" in _tables(db), name

    def test_duplicate_schema_info_dedupes_to_the_highest(self, tmp_path):
        db = _build(make_fixture.fixture_by_name("duplicate-schema-info-rows"), tmp_path)
        SessionStore(db).close()
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute("SELECT schema_version FROM schema_info").fetchall()
        finally:
            conn.close()
        assert rows == [(CURRENT_SCHEMA_VERSION,)], "dedupe must leave one row, migrated from the higher version"

    def test_v21_collision_gets_the_v26_repair(self, tmp_path):
        db = _build(make_fixture.fixture_by_name("v21-collision-lineage"), tmp_path)
        assert "origin" not in _columns(db, "standup_history"), "the lineage must start without provenance"
        assert "artifact_edits" not in _tables(db)
        SessionStore(db).close()
        assert "artifact_edits" in _tables(db)
        for table in ("standup_history", "retro_history", "performance_reviews"):
            assert {"origin", "edited_from_id"} <= set(_columns(db, table)), table

    def test_v31_future_self_heals_and_writes_nothing_else(self, tmp_path):
        db = _build(make_fixture.fixture_by_name("v31-from-the-future"), tmp_path)
        before = dump_mod.dump_db(db)
        store = SessionStore(db)
        assert store.schema_mismatch is True
        store.close()
        after = dump_mod.dump_db(db)
        assert after["schema_info"]["rows"] == [[31]], "the future stamp must survive untouched"
        # The only differences: artifact_edits appears, and the six history
        # tables gain the two provenance columns (existing rows defaulted).
        assert "artifact_edits" not in before and "artifact_edits" in after
        changed = {t for t in before if before[t] != after[t]}
        assert changed == set(make_fixture._PROVENANCE_TABLES), (
            f"the v26 self-heal wrote outside its remit: {sorted(changed ^ set(make_fixture._PROVENANCE_TABLES))}"
        )
        for table in make_fixture._PROVENANCE_TABLES:
            gained = [c[1] for c in after[table]["columns"] if [c[1]] not in [[b[1]] for b in before[table]["columns"]]]
            assert sorted(gained) == ["edited_from_id", "origin"], table

    def test_crash_mid_migration_recovers_idempotently(self, tmp_path):
        db = _build(make_fixture.fixture_by_name("crash-mid-migration"), tmp_path)
        store = SessionStore(db)
        assert store.schema_mismatch is False
        store.close()
        conn = sqlite3.connect(str(db))
        try:
            assert conn.execute("SELECT schema_version FROM schema_info").fetchall() == [(CURRENT_SCHEMA_VERSION,)]
            projects = dict(
                conn.execute(
                    "SELECT session_id, azdo_projects FROM standup_config WHERE session_id LIKE 'azdo-%'"
                ).fetchall()
            )
        finally:
            conn.close()
        assert projects["azdo-derive"] == make_fixture.AZDO_DERIVED, "the v17 re-run must derive the undived rows"
        assert projects["azdo-already-derived"] == '["Kept"]', "the v17 re-run must not touch a derived row"
