"""`yeaboi connections …` — the catalog on the command line."""

from __future__ import annotations

import io
import json

import pytest
from rich.console import Console

from yeaboi.cli import _cmd_connections, build_parser
from yeaboi.connectors import registry

API_KEY = "dd-api-key-abcdefghijkl"
APP_KEY = "dd-app-key-abcdefghijkl"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in registry.all_envs():
        monkeypatch.delenv(env, raising=False)


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    console = Console(file=buf, width=100, force_terminal=False)
    args = build_parser().parse_args(argv)
    code = _cmd_connections(args, console)
    return code, buf.getvalue()


@pytest.fixture
def _connected(monkeypatch):
    monkeypatch.setenv("DATADOG_API_KEY", API_KEY)
    monkeypatch.setenv("DATADOG_APP_KEY", APP_KEY)


@pytest.fixture
def _custom_store(tmp_path, monkeypatch):
    path = tmp_path / "custom_connectors.json"
    monkeypatch.setattr("yeaboi.connectors.custom._store_path", lambda: path)
    from yeaboi.connectors import custom
    from yeaboi.settings.engine import _invalidate_fields_cache

    custom.invalidate()
    _invalidate_fields_cache()
    yield path
    custom.invalidate()
    _invalidate_fields_cache()


class TestCreate:
    """The interactive flow — one prompt script per kind."""

    def _answers(self, monkeypatch, answers: list[str]) -> list[str]:
        script = list(answers)
        prompts: list[str] = []

        def _fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            return script.pop(0) if script else ""

        monkeypatch.setattr("builtins.input", _fake_input)
        return prompts

    def test_the_mcp_kind_skips_the_http_shape_prompts(self, monkeypatch, _custom_store):
        from yeaboi.connectors import custom

        prompts = self._answers(
            monkeypatch,
            ["Context7", "", "docs", "Docs lookup over MCP for the agent", "🧰", "rgb(30,120,90)", "", "mcp"],
        )
        code, out = _run(["connections", "create"])
        assert code == 0, out
        assert "Created." in out
        (spec,) = custom.load_specs()
        assert (spec.key, spec.kind) == ("custom_context7", "mcp")
        # Nothing HTTP-shaped was asked once the kind was mcp.
        assert not any("Auth scheme" in p or "Probe path" in p for p in prompts)

    def test_the_api_kind_still_asks_for_its_shape(self, monkeypatch, _custom_store):
        from yeaboi.connectors import custom

        prompts = self._answers(
            monkeypatch,
            [
                "Statuspage",
                "",
                "incidents",
                "Component status changes",
                "📟",
                "rgb(20,90,50)",
                "",
                "api",
                "bearer",
                "/v1/pages",
            ],
        )
        code, out = _run(["connections", "create"])
        assert code == 0, out
        (spec,) = custom.load_specs()
        assert (spec.kind, spec.probe_path) == ("api", "/v1/pages")
        assert any("Auth scheme" in p for p in prompts)

    def test_the_webhook_kind_asks_for_its_mapping_and_shows_the_secret_once(self, monkeypatch, _custom_store):
        from yeaboi.connectors import custom

        # The minted delivery secret must not land in the real shared .env.
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: None)
        prompts = self._answers(
            monkeypatch,
            [
                "Pager",
                "",
                "incidents",
                "Inbound incident deliveries",
                "📟",
                "rgb(21,91,51)",
                "",
                "webhook",
                "hmac",
                "incident",
                "incident.name",
            ],
        )
        code, out = _run(["connections", "create"])
        assert code == 0, out
        (spec,) = custom.load_specs()
        assert (spec.kind, spec.webhook_verify) == ("webhook", "hmac")
        assert (spec.events.kind, spec.events.title_path) == ("incident", "incident.name")
        assert not any("Auth scheme" in p or "Probe path" in p for p in prompts)
        # The minted delivery secret is shown once instead of the add-credentials tip.
        assert "shown once" in out
        assert "connections add" not in out

    def test_the_api_kind_offers_the_extra_fields_loop(self, monkeypatch, _custom_store):
        from yeaboi.connectors import custom

        self._answers(
            monkeypatch,
            [
                "Datadogish",
                "",
                "observability",
                "Metrics with two keys",
                "📈",
                "rgb(99,44,190)",
                "",
                "api",
                "bearer",
                "/api/v1/validate",
                "y",
                "Application Key",
                "APP_KEY",
                "",
                "DD-Application-Key",
                "n",
            ],
        )
        code, out = _run(["connections", "create"])
        assert code == 0, out
        (spec,) = custom.load_specs()
        (extra,) = spec.extra_fields
        assert (extra.label, extra.env_suffix, extra.secret, extra.header_name) == (
            "Application Key",
            "APP_KEY",
            True,
            "DD-Application-Key",
        )

    def test_from_json_carries_extras_and_icon(self, monkeypatch, _custom_store, tmp_path):
        from yeaboi.connectors import custom

        icon = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        descriptor = {
            "key": "custom_ddish",
            "label": "Datadogish",
            "family": "observability",
            "summary": "Metrics with two keys",
            "glyph": "📈",
            "accent": "rgb(98,43,189)",
            "kind": "api",
            "icon_data": icon,
            "extra_fields": [{"label": "Application Key", "env_suffix": "APP_KEY"}],
        }
        path = tmp_path / "descriptor.json"
        path.write_text(json.dumps(descriptor), encoding="utf-8")
        code, out = _run(["connections", "create", "--from-json", str(path)])
        assert code == 0, out
        (spec,) = custom.load_specs()
        assert spec.icon_data == icon
        assert spec.extra_fields[0].env_suffix == "APP_KEY"

    def test_a_bad_kind_is_a_message_not_a_crash(self, monkeypatch, _custom_store):
        self._answers(
            monkeypatch,
            ["Thing", "", "docs", "A thing", "🧰", "rgb(31,121,91)", "", "carrier-pigeon"],
        )
        code, _ = _run(["connections", "create"])
        assert code == 1


class TestParser:
    def test_every_verb_parses(self):
        parser = build_parser()
        for argv in (
            ["connections", "list"],
            ["connections", "list", "--all"],
            ["connections", "show", "datadog"],
            ["connections", "verify"],
            ["connections", "verify", "datadog"],
            ["connections", "add", "datadog"],
            ["connections", "remove", "datadog", "--yes"],
            ["connections", "fetch"],
            ["connections", "fetch", "datadog", "--since", "48h"],
        ):
            assert parser.parse_args(argv).command == "connections"

    def test_add_takes_no_credential_flag(self):
        # A credential on argv lands in shell history and the process table.
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["connections", "add", "datadog", "--token", "x"])


class TestList:
    def test_the_default_hides_what_is_not_connected(self):
        code, out = _run(["connections", "list"])
        assert code == 0
        assert "Datadog" not in out
        assert "Nothing connected" in out

    def test_all_shows_the_catalog(self):
        code, out = _run(["connections", "list", "--all"])
        assert code == 0
        assert "Datadog" in out
        assert "not connected" in out

    def test_a_connected_vendor_is_listed(self, _connected):
        code, out = _run(["connections", "list"])
        assert code == 0
        assert "Datadog" in out and "connected" in out

    def test_json_never_carries_a_value(self, _connected, capsys):
        code, _ = _run(["connections", "list", "--format", "json"])
        assert code == 0
        out = capsys.readouterr().out
        assert API_KEY not in out
        payload = json.loads(out)
        assert payload["connected"] == ["datadog"]


class TestShow:
    def test_reports_which_fields_are_set(self, _connected):
        code, out = _run(["connections", "show", "datadog"])
        assert code == 0
        assert "API Key" in out and "set" in out
        assert API_KEY not in out

    def test_an_unknown_name_is_an_error(self, capsys):
        code, _ = _run(["connections", "show", "nope"])
        assert code == 1
        assert "unknown connector" in capsys.readouterr().err


class TestVerify:
    def test_nothing_connected_is_not_a_failure(self):
        code, out = _run(["connections", "verify"])
        assert code == 0
        assert "Nothing connected" in out

    def test_verifies_each_connected_one(self, _connected, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.settings.engine.verify_connection", lambda kind, fields: {"ok": True, "message": "fine"}
        )
        code, out = _run(["connections", "verify"])
        assert code == 0
        assert "datadog" in out and "ok" in out

    def test_a_failure_is_a_nonzero_exit(self, _connected, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.settings.engine.verify_connection", lambda kind, fields: {"ok": False, "message": "bad key"}
        )
        code, out = _run(["connections", "verify", "datadog"])
        assert code == 1
        assert "bad key" in out


class TestRemove:
    def test_clears_every_field(self, _connected, monkeypatch):
        cleared: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: cleared.__setitem__(k, v))
        code, out = _run(["connections", "remove", "datadog", "--yes"])
        assert code == 0
        assert cleared == {"DATADOG_API_KEY": "", "DATADOG_APP_KEY": "", "DATADOG_SITE": ""}
        assert "disconnected" in out


class TestFetch:
    """`connections fetch` — the read-only look at what a vendor saw."""

    def test_nothing_connected_says_so_without_naming_a_vendor(self):
        code, out = _run(["connections", "fetch"])
        assert code == 0
        assert "anything to gather" in out.lower()
        assert "datadog" not in out.lower()

    def test_reports_counts_and_the_window(self, monkeypatch, _connected):
        from yeaboi.ops.events import OpsEvent

        monkeypatch.setattr(
            "yeaboi.connectors.datadog.fetch",
            lambda start, end: (OpsEvent(kind="alert", source="datadog", ref="1", title="checkout latency"),),
        )
        code, out = _run(["connections", "fetch", "--since", "7d"])
        assert code == 0
        assert "1 event(s)" in out
        assert "checkout latency" in out

    def test_a_failing_source_is_a_non_zero_exit_with_a_message(self, monkeypatch, _connected):
        from yeaboi.connectors.fetching import FetchError

        def boom(start, end):
            raise FetchError("datadog: rate limited — try a shorter window")

        monkeypatch.setattr("yeaboi.connectors.datadog.fetch", boom)
        code, out = _run(["connections", "fetch"])
        assert code == 1
        assert "rate limited" in out

    def test_a_bad_window_is_an_error_not_a_traceback(self, capsys, _connected):
        code, _ = _run(["connections", "fetch", "--since", "forever"])
        assert code == 1
        assert "invalid window" in capsys.readouterr().err

    def test_json_carries_no_credential(self, monkeypatch, capsys, _connected):
        from yeaboi.ops.events import OpsEvent

        monkeypatch.setattr(
            "yeaboi.connectors.datadog.fetch",
            lambda start, end: (OpsEvent(kind="alert", source="datadog", ref="1", title="x"),),
        )
        _run(["connections", "fetch", "--format", "json"])
        printed = capsys.readouterr().out
        assert API_KEY not in printed and APP_KEY not in printed
        assert json.loads(printed)["signals"][0]["count"] == 1
