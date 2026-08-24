"""The startup handshake — line format, file round-trip, permissions."""

from __future__ import annotations

import json
import stat

import pytest

from yeaboi.app.handshake import (
    READY_PREFIX,
    Handshake,
    clear_handshake,
    parse_ready_line,
    read_handshake,
    ready_line,
    write_handshake,
)

HS = Handshake(url="http://127.0.0.1:5599", token="tok", pid=42, schema=30, version="1.2.3")


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("yeaboi.paths.get_run_dir", lambda: tmp_path)
    return tmp_path


class TestReadyLine:
    def test_line_has_prefix_and_compact_json(self):
        line = ready_line(HS)
        assert line.startswith(READY_PREFIX)
        assert "\n" not in line
        payload = json.loads(line[len(READY_PREFIX) :])
        assert payload == {"pid": 42, "schema": 30, "token": "tok", "url": "http://127.0.0.1:5599", "version": "1.2.3"}

    def test_round_trip(self):
        assert parse_ready_line(ready_line(HS)) == HS

    def test_parse_rejects_non_handshake_line(self):
        with pytest.raises(ValueError, match="not a handshake line"):
            parse_ready_line('{"url": "x"}')


class TestHandshakeFile:
    def test_write_read_round_trip(self, run_dir):
        path = write_handshake(HS)
        assert path.parent == run_dir
        assert read_handshake() == HS

    def test_file_is_owner_only(self, run_dir):
        path = write_handshake(HS)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_read_missing_is_none(self, run_dir):
        assert read_handshake() is None

    def test_read_malformed_is_none(self, run_dir):
        (run_dir / "app-handshake.json").write_text("not json", encoding="utf-8")
        assert read_handshake() is None

    def test_clear_removes_and_is_idempotent(self, run_dir):
        write_handshake(HS)
        clear_handshake()
        assert read_handshake() is None
        clear_handshake()  # no raise on already-absent
