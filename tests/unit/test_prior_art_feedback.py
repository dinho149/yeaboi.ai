"""Unit tests for the prior-art feedback ledger.

The load-bearing properties: a rejection is permanent and global, a re-vote
flips rather than stacks, and nothing here may raise — a prior-art step that
cannot read its ledger must still run.
"""

from __future__ import annotations

import pytest

from yeaboi.agent import prior_art_feedback as pf
from yeaboi.sessions import SessionStore


@pytest.fixture
def db(tmp_path):
    """A real migrated sessions database — v29 creates the table."""
    path = tmp_path / "sessions.db"
    with SessionStore(path) as store:
        assert store.schema_mismatch is False
    return path


class TestMigration:
    def test_v29_creates_the_table(self, db):
        import sqlite3

        with sqlite3.connect(str(db)) as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "planning_prior_art_feedback" in names


class TestApplyVerdict:
    def test_records_a_rejection_with_a_reason(self, db):
        assert pf.apply_verdict(
            repo_key="github:acme/legacy-billing",
            verdict=pf.VERDICT_DOWN,
            reason="that's the service we're retiring",
            repo_name="acme/legacy-billing",
            db_path=db,
        )
        ledger = pf.load(db_path=db)
        assert ledger.is_rejected("github:acme/legacy-billing")
        assert ledger.accepted == frozenset()

    def test_key_is_case_insensitive_on_both_write_and_read(self, db):
        pf.apply_verdict(repo_key="GitHub:Acme/API", verdict=pf.VERDICT_DOWN, db_path=db)
        assert pf.load(db_path=db).is_rejected("github:acme/api")

    def test_revote_flips_instead_of_stacking(self, db):
        pf.apply_verdict(repo_key="github:acme/api", verdict=pf.VERDICT_DOWN, reason="no", db_path=db)
        pf.apply_verdict(repo_key="github:acme/api", verdict=pf.VERDICT_UP, db_path=db)
        ledger = pf.load(db_path=db)
        assert not ledger.is_rejected("github:acme/api")
        assert "github:acme/api" in ledger.accepted
        # One row, not two — the old opinion must not stay half-live.
        assert len(ledger.examples) <= 1

    def test_unknown_verdict_is_refused(self, db):
        assert pf.apply_verdict(repo_key="github:acme/api", verdict="maybe", db_path=db) is False
        assert pf.load(db_path=db) == pf.Ledger()

    def test_empty_key_is_refused(self, db):
        assert pf.apply_verdict(repo_key="   ", verdict=pf.VERDICT_DOWN, db_path=db) is False

    def test_reason_is_clipped(self, db):
        pf.apply_verdict(
            repo_key="github:acme/api", verdict=pf.VERDICT_DOWN, reason="x" * 999, repo_name="a", db_path=db
        )
        (example,) = pf.load(db_path=db).examples
        assert len(example.reason) == pf._REASON_CLIP


class TestLoad:
    def test_missing_database_is_an_empty_ledger_not_a_crash(self, tmp_path):
        assert pf.load(db_path=tmp_path / "nope.db") == pf.Ledger()

    def test_missing_database_is_not_created_by_a_read(self, tmp_path):
        path = tmp_path / "nope.db"
        pf.load(db_path=path)
        assert not path.exists()

    def test_corrupt_rows_are_skipped_not_fatal(self, db):
        import sqlite3

        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO planning_prior_art_feedback (repo_key, verdict, created_at) VALUES (?, ?, ?)",
                ("github:acme/ok", "down", "2026-01-01"),
            )
            conn.execute(
                "INSERT INTO planning_prior_art_feedback (repo_key, verdict, created_at) VALUES (?, ?, ?)",
                ("", "down", "2026-01-01"),
            )
            conn.execute(
                "INSERT INTO planning_prior_art_feedback (repo_key, verdict, created_at) VALUES (?, ?, ?)",
                ("github:acme/weird", "sideways", "2026-01-01"),
            )
        ledger = pf.load(db_path=db)
        assert ledger.rejected == frozenset({"github:acme/ok"})

    def test_examples_need_a_name_or_a_reason(self, db):
        # A bare key teaches the pitch model nothing.
        pf.apply_verdict(repo_key="github:acme/bare", verdict=pf.VERDICT_DOWN, db_path=db)
        assert pf.load(db_path=db).examples == ()


class TestCorrections:
    def _ledger(self, rejections, acceptances):
        examples = [pf.FeedbackExample(pf.VERDICT_DOWN, f"r{i}", "no") for i in range(rejections)]
        examples += [pf.FeedbackExample(pf.VERDICT_UP, f"a{i}", "yes") for i in range(acceptances)]
        return pf.Ledger(examples=tuple(examples))

    def test_capped_separately_per_verdict(self):
        out = self._ledger(50, 50).corrections()
        assert sum(1 for c in out if c["verdict"] == pf.VERDICT_DOWN) == pf._MAX_REJECTIONS
        assert sum(1 for c in out if c["verdict"] == pf.VERDICT_UP) == pf._MAX_ACCEPTANCES

    def test_carries_no_project_identity(self):
        # The prompt learns what kind of repo gets dismissed, not which plan
        # the user happened to be writing.
        (correction,) = self._ledger(1, 0).corrections()
        assert set(correction) == {"verdict", "repo", "reason"}

    def test_empty_ledger_is_empty(self):
        assert pf.Ledger().corrections() == ()


class TestIsRejected:
    def test_whitespace_and_case_tolerant(self):
        ledger = pf.Ledger(rejected=frozenset({"github:acme/api"}))
        assert ledger.is_rejected("  GitHub:Acme/API  ")

    def test_unknown_key_is_not_rejected(self):
        assert not pf.Ledger().is_rejected("github:acme/api")
