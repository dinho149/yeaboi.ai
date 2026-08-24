"""The consent desk and the awareness watcher — the two ambient publishers.

Both are polled rather than pushed, so both are driven here by calling their
poll/drain directly. The threads they run in are one line each and are not the
subject; what is, is what reaches the bus and what deliberately does not.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import CeremonyRun
from yeaboi.app.awareness import NOTICES, AwarenessWatcher
from yeaboi.app.consent import ConsentDesk
from yeaboi.ceremonies.store import CeremonyStore


class FakeBus:
    """Records what was published, in order."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, type_: str, **fields: object) -> dict:
        event = {"type": type_, **fields}
        self.events.append(event)
        return event

    def of(self, type_: str) -> list[dict]:
        return [event for event in self.events if event["type"] == type_]


@pytest.fixture
def bus():
    return FakeBus()


@pytest.fixture
def sandbox(monkeypatch):
    """Denials actually denied — conftest whitelists the whole basetemp."""
    from yeaboi import fs_policy

    monkeypatch.setenv("YEABOI_ALLOWED_PATHS", "")
    fs_policy.set_interactive(True)
    yield fs_policy
    fs_policy.set_interactive(False)
    fs_policy.clear_session_grants()
    fs_policy.pop_pending_denials()


def deny(fs_policy, path, *, context: str = "read_codebase") -> None:
    with pytest.raises(fs_policy.SandboxViolationError):
        fs_policy.resolve_and_check(path, context=context)


class TestConsentDesk:
    def test_a_denial_becomes_an_event_with_the_choices_on_it(self, bus, sandbox, tmp_path):
        deny(sandbox, tmp_path / "repo" / "f.py")
        desk = ConsentDesk(bus)
        published = desk.drain()
        assert len(published) == 1
        event = published[0]
        assert event["type"] == "consent_request"
        assert event["path"] == str(tmp_path / "repo" / "f.py")
        assert event["mode"] == "read"
        assert event["context"] == "read_codebase"
        assert event["choices"] == ["allow_once", "allow_always", "deny"]

    def test_draining_twice_does_not_re_ask(self, bus, sandbox, tmp_path):
        deny(sandbox, tmp_path / "repo")
        desk = ConsentDesk(bus)
        desk.drain()
        assert desk.drain() == []

    def test_an_answer_closes_the_request_and_says_so_on_the_bus(self, bus, sandbox, tmp_path):
        deny(sandbox, tmp_path / "repo")
        desk = ConsentDesk(bus)
        req_id = desk.drain()[0]["req_id"]
        assert desk.resolve(req_id, "allow_once") is True
        assert desk.open_requests() == []
        assert bus.of("consent_resolved")[0]["granted"] is True

    def test_answering_an_unknown_request_raises(self, bus):
        with pytest.raises(KeyError):
            ConsentDesk(bus).resolve("fs-99", "deny")

    def test_the_table_is_bounded(self, bus, sandbox, tmp_path, monkeypatch):
        # An unanswered request is only a modal nobody answered; the access it
        # guarded already failed, so dropping the oldest costs nothing.
        monkeypatch.setattr("yeaboi.app.consent.MAX_OPEN", 3)
        desk = ConsentDesk(bus)
        for index in range(6):
            deny(sandbox, tmp_path / f"repo{index}")
            desk.drain()
        assert len(desk.open_requests()) == 3

    def test_a_headless_denial_never_reaches_the_desk(self, bus, tmp_path, monkeypatch):
        from yeaboi import fs_policy

        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", "")
        fs_policy.set_interactive(False)
        deny(fs_policy, tmp_path / "repo")
        assert ConsentDesk(bus).drain() == []


@pytest.fixture
def ceremonies(tmp_path, monkeypatch):
    """A throwaway ceremony store on a fixed session."""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    monkeypatch.setattr("yeaboi.ceremonies.setup.current_session", lambda: "s1")
    return db


def record(db, ceremony: str, fired_at: str, outcome: str = "ok") -> None:
    with CeremonyStore(db) as store:
        store.record_run(
            CeremonyRun(ceremony=ceremony, session_id="s1", fired_at=fired_at, outcome=outcome, scheduled=True)
        )


class FakeShips:
    def __init__(self, runs: list[dict]) -> None:
        self._runs = runs

    def runs(self) -> list[dict]:
        return self._runs


class TestAwarenessWatcher:
    def test_the_first_look_announces_nothing(self, bus, ceremonies):
        # Everything already in the store happened before the app opened.
        record(ceremonies, "standup", "2026-08-23T09:00:00")
        watcher = AwarenessWatcher(bus, db_path=ceremonies)
        assert watcher.poll() == []

    def test_a_ceremony_that_fires_afterwards_is_announced(self, bus, ceremonies):
        record(ceremonies, "standup", "2026-08-23T09:00:00")
        watcher = AwarenessWatcher(bus, db_path=ceremonies)
        watcher.poll()
        record(ceremonies, "standup", "2026-08-24T09:00:00")
        events = watcher.poll()
        assert len(events) == 1
        assert events[0]["kind"] == "ceremony_ran"
        assert events[0]["ceremony"] == "standup"
        assert events[0]["sticky"] is False
        assert events[0]["route"] == "/ceremonies"

    def test_the_same_run_is_announced_once(self, bus, ceremonies):
        watcher = AwarenessWatcher(bus, db_path=ceremonies)
        watcher.poll()
        record(ceremonies, "standup", "2026-08-24T09:00:00")
        watcher.poll()
        assert watcher.poll() == []

    def test_a_failed_ceremony_gets_its_own_line(self, bus, ceremonies):
        watcher = AwarenessWatcher(bus, db_path=ceremonies)
        watcher.poll()
        record(ceremonies, "standup", "2026-08-24T09:00:00", outcome="failed")
        assert watcher.poll()[0]["kind"] == "ceremony_failed"

    def test_no_session_means_nothing_to_watch(self, bus, ceremonies, monkeypatch):
        monkeypatch.setattr("yeaboi.ceremonies.setup.current_session", lambda: "")
        watcher = AwarenessWatcher(bus, db_path=ceremonies)
        watcher.poll()
        assert watcher.poll() == []

    def test_a_gate_that_opens_is_announced_and_is_sticky(self, bus, ceremonies):
        ships = FakeShips([])
        watcher = AwarenessWatcher(bus, ships=ships, db_path=ceremonies)
        watcher.poll()
        ships._runs = [{"key": "r1", "story_title": "Add the thing", "gate": {"status": "awaiting_approval"}}]
        events = watcher.poll()
        assert len(events) == 1
        assert events[0]["kind"] == "ship_gate"
        assert events[0]["sticky"] is True  # a question must not fade unanswered
        assert events[0]["story"] == "Add the thing"

    def test_a_gate_already_open_at_startup_is_not_news(self, bus, ceremonies):
        ships = FakeShips([{"key": "r1", "story_title": "t", "gate": {"status": "awaiting_approval"}}])
        watcher = AwarenessWatcher(bus, ships=ships, db_path=ceremonies)
        watcher.poll()
        assert watcher.poll() == []

    def test_a_gate_re_opening_after_it_closed_is_announced_again(self, bus, ceremonies):
        ships = FakeShips([])
        watcher = AwarenessWatcher(bus, ships=ships, db_path=ceremonies)
        watcher.poll()
        gate = {"key": "r1", "story_title": "t", "gate": {"status": "awaiting_approval"}}
        ships._runs = [gate]
        watcher.poll()
        ships._runs = [{**gate, "gate": None}]
        watcher.poll()
        ships._runs = [gate]
        assert watcher.poll()[0]["kind"] == "ship_gate"

    def test_every_notice_fits_the_bubble_and_names_a_route(self):
        for kind, notice in NOTICES.items():
            assert notice.kind == kind
            assert len(notice.quip) <= 40, f"{kind} quip is {len(notice.quip)} chars"
            assert notice.route.startswith("/")

    def test_only_the_question_is_sticky(self):
        assert [kind for kind, notice in NOTICES.items() if notice.sticky] == ["ship_gate"]
