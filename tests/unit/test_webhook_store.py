"""The webhook delivery store: dedupe, retention, and the window read."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from yeaboi.connectors import webhook_store
from yeaboi.ops.events import OpsEvent


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: path)
    return path


def _event(title="API degraded", started="2026-06-10T09:00:00Z"):
    return OpsEvent(kind="incident", source="custom_x", ref="inc-1", title=title, started_at=started)


class TestRecord:
    def test_a_delivery_lands_once(self):
        assert webhook_store.record_delivery("custom_x", "hash-1", (_event(),)) == 1
        # The retried delivery presents the same hash and inserts nothing.
        assert webhook_store.record_delivery("custom_x", "hash-1", (_event(),)) == 0
        assert len(webhook_store.events_in_window("custom_x", None, None)) == 1

    def test_rows_of_one_delivery_are_kept_apart(self):
        events = (_event("first"), _event("second"))
        assert webhook_store.record_delivery("custom_x", "hash-2", events) == 2

    def test_connections_do_not_see_each_other(self):
        webhook_store.record_delivery("custom_x", "h", (_event(),))
        assert webhook_store.events_in_window("custom_y", None, None) == ()

    def test_old_deliveries_are_pruned_on_insert(self, _db):
        webhook_store.record_delivery("custom_x", "old", (_event("ancient"),))
        stale = (datetime.now(timezone.utc)).replace(year=2020).isoformat()
        with sqlite3.connect(_db) as conn:
            conn.execute("UPDATE webhook_events SET received_at = ?", (stale,))
        webhook_store.record_delivery("custom_x", "new", (_event("fresh"),))
        titles = [e.title for e in webhook_store.events_in_window("custom_x", None, None)]
        assert titles == ["fresh"]


class TestRead:
    def test_events_come_back_as_ops_events(self):
        webhook_store.record_delivery("custom_x", "h", (_event(),))
        (event,) = webhook_store.events_in_window("custom_x", None, None)
        assert isinstance(event, OpsEvent)
        assert (event.kind, event.source, event.title) == ("incident", "custom_x", "API degraded")

    def test_last_received_at_answers_waiting_with_empty(self):
        assert webhook_store.last_received_at("custom_x") == ""
        webhook_store.record_delivery("custom_x", "h", (_event(),))
        assert webhook_store.last_received_at("custom_x") != ""
