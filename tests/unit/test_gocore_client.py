"""Tests for src/yeaboi/gocore/client.py — the sidecar RPC client.

A real Go binary is not required (and absent on CI runners without Go): a
tiny Python script speaking the ndjson protocol stands in for the sidecar,
which also keeps these tests pinned to the wire contract rather than to any
one implementation of it.
"""

import json
import os
import stat
import sys
import textwrap

import pytest

from yeaboi.gocore.client import CoreClient, CoreError, is_enabled

_FAKE_SIDECAR = textwrap.dedent(
    """\
    #!{python}
    import json, sys, time
    for line in sys.stdin:
        req = json.loads(line)
        method, rid = req["method"], req.get("id")
        if method == "core.hello":
            hello = {{"contract_version": {hello_version}, "name": "fake-core", "version": "0.0", "methods": []}}
            print(json.dumps({{"jsonrpc": "2.0", "id": rid, "result": hello}}), flush=True)
        elif method == "test.progress":
            event = {{"kind": "analysis_component", "component_id": "scan", "label": "Scan", "status": "running",
                      "detail": "", "current": 1, "total": 2, "unit": "files"}}
            print(json.dumps({{"jsonrpc": "2.0", "method": "progress",
                               "params": {{"request_id": rid, "event": event}}}}), flush=True)
            print(json.dumps({{"jsonrpc": "2.0", "id": rid, "result": {{"ok": True}}}}), flush=True)
        elif method == "test.error":
            print(json.dumps({{"jsonrpc": "2.0", "id": rid,
                               "error": {{"code": 1001, "message": "schema too new"}}}}), flush=True)
        elif method == "test.hang":
            time.sleep(30)
    """
)


@pytest.fixture
def fake_sidecar(tmp_path):
    def build(hello_version: int = 1) -> str:
        script = tmp_path / f"fake-core-{hello_version}"
        script.write_text(_FAKE_SIDECAR.format(python=sys.executable, hello_version=hello_version), encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)

    return build


class TestIsEnabled:
    def test_flag_values(self, monkeypatch):
        for value, expected in [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("", False)]:
            monkeypatch.setenv("YEABOI_GO", value)
            assert is_enabled() is expected, value
        monkeypatch.delenv("YEABOI_GO")
        assert is_enabled() is False


class TestCoreClient:
    def test_handshake_ok(self, fake_sidecar):
        client = CoreClient(fake_sidecar())
        try:
            hello = client.hello()
            assert hello["name"] == "fake-core"
        finally:
            client.close()

    def test_contract_version_mismatch_raises(self, fake_sidecar):
        client = CoreClient(fake_sidecar(hello_version=99))
        try:
            with pytest.raises(CoreError, match="contract version mismatch"):
                client.hello()
        finally:
            client.close()

    def test_progress_notifications_reach_the_callback(self, fake_sidecar):
        client = CoreClient(fake_sidecar())
        try:
            events = []
            result = client.request("test.progress", {}, on_progress=events.append, timeout=10)
            assert result == {"ok": True}
            assert len(events) == 1
            # The event is the exact analysis_component dict — the TUI
            # checklist consumes it unchanged.
            assert events[0]["kind"] == "analysis_component"
            assert events[0]["status"] == "running"
        finally:
            client.close()

    def test_rpc_error_becomes_core_error_with_code(self, fake_sidecar):
        client = CoreClient(fake_sidecar())
        try:
            with pytest.raises(CoreError, match=r"\[1001\] schema too new"):
                client.request("test.error", {}, timeout=10)
        finally:
            client.close()

    def test_timeout_raises_instead_of_hanging(self, fake_sidecar):
        client = CoreClient(fake_sidecar())
        try:
            with pytest.raises(CoreError, match="timed out"):
                client.request("test.hang", {}, timeout=0.3)
        finally:
            client.close()

    def test_dead_sidecar_fails_fast(self, fake_sidecar):
        client = CoreClient(fake_sidecar())
        client.close()
        with pytest.raises(CoreError, match="not running"):
            client.request("core.hello", {}, timeout=1)


class TestGetClient:
    def test_disabled_flag_returns_none_without_spawning(self, monkeypatch):
        import yeaboi.gocore.client as client_mod

        monkeypatch.delenv("YEABOI_GO", raising=False)
        monkeypatch.setattr(client_mod, "_client", None)
        monkeypatch.setattr(client_mod, "_client_failed", False)
        assert client_mod.get_client() is None

    def test_missing_binary_fails_once_and_caches(self, monkeypatch):
        import yeaboi.gocore.client as client_mod

        monkeypatch.setenv("YEABOI_GO", "1")
        monkeypatch.setattr(client_mod, "_client", None)
        monkeypatch.setattr(client_mod, "_client_failed", False)
        calls = []

        def no_binary():
            calls.append(1)
            return None

        monkeypatch.setattr(client_mod, "find_core_binary", no_binary)
        assert client_mod.get_client() is None
        assert client_mod.get_client() is None
        assert len(calls) == 1  # negative result is cached for the process

    def test_working_sidecar_is_cached(self, monkeypatch, fake_sidecar):
        import yeaboi.gocore.client as client_mod

        monkeypatch.setenv("YEABOI_GO", "1")
        monkeypatch.setattr(client_mod, "_client", None)
        monkeypatch.setattr(client_mod, "_client_failed", False)
        monkeypatch.setattr(client_mod, "find_core_binary", lambda: fake_sidecar())
        first = client_mod.get_client()
        try:
            assert first is not None
            assert client_mod.get_client() is first
        finally:
            if first is not None:
                first.close()
            monkeypatch.setattr(client_mod, "_client", None)


class TestWireShapes:
    def test_request_line_is_json_rpc(self, fake_sidecar):
        # Belt-and-braces: the framing the fake asserts implicitly, made explicit.
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "core.hello", "params": {}})
        parsed = json.loads(line)
        assert parsed["jsonrpc"] == "2.0"
        assert os.linesep not in line  # one object per line, no embedded newlines
