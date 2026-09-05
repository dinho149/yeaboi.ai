"""Tests for src/yeaboi/agentwatch/dismissals.py — the reasoned allowlist."""

import json

import pytest

from yeaboi.agentwatch import dismissals


@pytest.fixture
def allow(tmp_path):
    return tmp_path / "data" / "security_allow.json"


class TestDismiss:
    def test_records_key_reason_and_stamp(self, allow):
        entry = dismissals.dismiss("secret:p:/a", reason="fixture", by="me", path=allow)
        assert entry.key == "secret:p:/a" and entry.reason == "fixture" and entry.by == "me" and entry.at
        on_disk = json.loads(allow.read_text())
        assert on_disk["version"] == 1 and on_disk["dismissed"][0]["key"] == "secret:p:/a"

    def test_refuses_an_empty_reason_or_key(self, allow):
        with pytest.raises(ValueError, match="reason"):
            dismissals.dismiss("secret:p:/a", reason="", path=allow)
        with pytest.raises(ValueError, match="key"):
            dismissals.dismiss("  ", reason="why", path=allow)
        assert not allow.exists()

    def test_redismissing_replaces_the_entry(self, allow):
        dismissals.dismiss("k", reason="first", path=allow)
        dismissals.dismiss("k", reason="second", path=allow)
        assert [d.reason for d in dismissals.load(allow)] == ["second"]

    def test_expiry_is_validated_and_honoured(self, allow):
        with pytest.raises(ValueError):
            dismissals.dismiss("k", reason="r", expires="next week", path=allow)
        dismissals.dismiss("k", reason="r", expires="2026-08-01", path=allow)
        assert "k" not in dismissals.active("2026-08-02", path=allow)
        assert "k" in dismissals.active("2026-07-31", path=allow)

    def test_undismiss(self, allow):
        dismissals.dismiss("k", reason="r", path=allow)
        assert dismissals.undismiss("k", path=allow) is True
        assert dismissals.undismiss("k", path=allow) is False
        assert dismissals.load(allow) == []

    def test_a_corrupt_file_reads_as_empty(self, allow):
        allow.parent.mkdir(parents=True)
        allow.write_text("{oops")
        assert dismissals.load(allow) == []
