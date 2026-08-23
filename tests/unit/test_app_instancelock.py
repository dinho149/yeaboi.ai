"""Single-instance lock — exclusive create, liveness probe, stale takeover."""

from __future__ import annotations

import pytest

from yeaboi.app import instancelock
from yeaboi.app.handshake import Handshake

HS = Handshake(url="http://127.0.0.1:5599", token="tok", pid=42, schema=30, version="1.2.3")


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("yeaboi.paths.get_run_dir", lambda: tmp_path)
    return tmp_path


class TestAcquire:
    def test_fresh_acquire_owns_the_lock(self, run_dir):
        acquired = instancelock.acquire()
        assert isinstance(acquired, instancelock.Acquired)
        assert acquired.path.exists()

    def test_release_removes_the_file(self, run_dir):
        acquired = instancelock.acquire()
        instancelock.release(acquired)
        assert not acquired.path.exists()

    def test_conflict_with_live_instance_returns_its_handshake(self, run_dir, monkeypatch):
        instancelock.acquire()
        monkeypatch.setattr(instancelock, "_probe_existing", lambda: HS)
        result = instancelock.acquire()
        assert isinstance(result, instancelock.AlreadyRunning)
        assert result.handshake == HS

    def test_stale_lock_is_removed_and_taken(self, run_dir, monkeypatch):
        instancelock.acquire()
        monkeypatch.setattr(instancelock, "_probe_existing", lambda: None)
        result = instancelock.acquire()
        assert isinstance(result, instancelock.Acquired)

    def test_unremovable_conflict_raises(self, run_dir, monkeypatch):
        import os

        monkeypatch.setattr(instancelock, "_probe_existing", lambda: None)

        def always_held(*args, **kwargs):
            raise FileExistsError

        monkeypatch.setattr(os, "open", always_held)
        with pytest.raises(instancelock.InstanceLockError):
            instancelock.acquire()


class TestProbe:
    def test_no_handshake_means_dead(self, run_dir):
        assert instancelock._probe_existing() is None

    def test_unreachable_url_means_dead(self, run_dir, monkeypatch):
        monkeypatch.setattr(instancelock, "read_handshake", lambda: HS)
        # Nothing listens on the handshake's port in this test — URLError path.
        assert instancelock._probe_existing() is None

    def test_pid_mismatch_means_dead(self, run_dir, monkeypatch):
        """A different server on a recycled port must not pass as ours."""
        import io
        import urllib.request

        monkeypatch.setattr(instancelock, "read_handshake", lambda: HS)

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse(b'{"ok": true, "pid": 999}'))
        assert instancelock._probe_existing() is None

    def test_matching_pid_means_live(self, run_dir, monkeypatch):
        import io
        import urllib.request

        monkeypatch.setattr(instancelock, "read_handshake", lambda: HS)

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse(b'{"ok": true, "pid": 42}'))
        assert instancelock._probe_existing() == HS
