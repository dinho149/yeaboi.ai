"""The operation table — cancel events over the wire."""

from __future__ import annotations

import time

import pytest

from yeaboi.app.ops import OperationTable


class TestOperationTable:
    def test_create_mints_an_id(self):
        table = OperationTable()
        op = table.create()
        assert op.op_id
        assert not op.cancel.is_set()
        assert len(table) == 1

    def test_create_accepts_caller_id(self):
        table = OperationTable()
        assert table.create("mine").op_id == "mine"

    def test_duplicate_id_is_refused(self):
        table = OperationTable()
        table.create("mine")
        with pytest.raises(ValueError, match="already in flight"):
            table.create("mine")

    def test_cancel_sets_the_event(self):
        table = OperationTable()
        op = table.create()
        assert table.cancel(op.op_id) is True
        assert op.cancel.is_set()

    def test_cancel_unknown_is_false(self):
        assert OperationTable().cancel("nope") is False

    def test_remove_is_idempotent(self):
        table = OperationTable()
        op = table.create()
        table.remove(op.op_id)
        table.remove(op.op_id)
        assert len(table) == 0

    def test_prune_drops_only_old_entries(self):
        table = OperationTable()
        old = table.create("old")
        object.__setattr__(old, "created", time.time() - 10_000)
        table.create("fresh")
        assert table.prune(max_age_seconds=3600) == 1
        assert table.get("old") is None
        assert table.get("fresh") is not None
