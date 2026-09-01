"""User-created connections: the store, the validator, and the guarantees.

The spec suite guards the built-ins at build time; these tests guard the same
invariants where a user-authored descriptor arrives — at runtime. The three
load-bearing cases: a derived env can never collide with an existing one, a
custom secret is masked and redacted like any built-in's, and a hostile or
damaged descriptor is a message, never a crash.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.connectors import custom, registry
from yeaboi.connectors.custom import CustomFieldSpec, CustomSpec, EventsMapping, spec_from_dict
from yeaboi.connectors.validation import descriptor_problems

VALID = {
    "key": "custom_statuspage",
    "label": "Statuspage",
    "family": "incidents",
    "summary": "Component status changes, so an outage is visible before the retro",
    "detail": "Reads component status only. It never reads subscriber data and never writes.",
    "docs_url": "https://developer.statuspage.io/",
    "glyph": "📟",
    "accent": "rgb(20,90,50)",
    "kind": "api",
    "auth_scheme": "bearer",
    "probe_path": "/v1/pages",
    "probe_ok_status": 200,
    "events": {
        "path": "/v1/incidents",
        "items_key": "incidents",
        "kind": "incident",
        "title_path": "name",
        "ref_path": "id",
        "severity_path": "impact",
        "status_path": "status",
        "url_path": "shortlink",
        "started_at_path": "created_at",
    },
}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    path = tmp_path / "custom_connectors.json"
    monkeypatch.setattr("yeaboi.connectors.custom._store_path", lambda: path)
    custom.invalidate()
    from yeaboi.settings.engine import _invalidate_fields_cache

    _invalidate_fields_cache()
    yield path
    custom.invalidate()
    _invalidate_fields_cache()


def _no_problems(spec: CustomSpec) -> list[str]:
    return descriptor_problems(
        spec,
        existing_keys=frozenset({c.key for c in registry.builtin_connectors()}),
        existing_envs=frozenset(registry.all_envs()),
        existing_accents=frozenset(c.accent for c in registry.builtin_connectors()),
    )


class TestValidator:
    def test_the_valid_descriptor_passes(self):
        assert _no_problems(spec_from_dict(VALID)) == []

    def test_every_builtin_would_pass_the_runtime_rules_it_shares(self):
        # The validator restates the spec suite; a rule the built-ins could not
        # satisfy would be a different rulebook, not a stricter one.
        for connector in registry.builtin_connectors():
            probe = spec_from_dict(
                {
                    **VALID,
                    "key": f"custom_{connector.key}",
                    "label": connector.label,
                    "family": connector.family,
                    "summary": connector.summary,
                    "accent": "rgb(1,2,3)",
                }
            )
            assert _no_problems(probe) == [], f"{connector.key} would fail the runtime rules"

    @pytest.mark.parametrize(
        ("patch", "needle"),
        [
            ({"key": "statuspage"}, "custom_"),
            ({"family": "weather"}, "family"),
            ({"summary": ""}, "summary"),
            ({"summary": "x" * 120}, "90"),
            ({"accent": "#145a32"}, "rgb"),
            ({"accent": "rgb(999,0,0)"}, "rgb"),
            ({"docs_url": "http://plain.example"}, "https"),
            ({"kind": "outbound"}, "kind"),
            ({"kind": "webhook", "webhook_verify": "signature"}, "verify mode"),
            ({"kind": "webhook", "events": None}, "events mapping"),
            ({"auth_scheme": "cookie"}, "scheme"),
            ({"probe_path": "https://evil.example/x"}, "path"),
            ({"probe_path": "/a/../b"}, "traverse"),
            ({"probe_path": "no-slash"}, "start"),
            ({"probe_ok_status": 302}, "2xx"),
            ({"events": {**VALID["events"], "kind": "party"}}, "event kind"),
            ({"events": {**VALID["events"], "title_path": ""}}, "title_path"),
            ({"events": {**VALID["events"], "path": "/x/../y"}}, "traverse"),
        ],
    )
    def test_each_broken_shape_is_named(self, patch, needle):
        problems = _no_problems(spec_from_dict({**VALID, **patch}))
        assert problems, f"{patch} was accepted"
        assert any(needle in p for p in problems), f"{needle!r} not named in {problems}"

    def test_a_key_collision_with_a_builtin_is_fatal(self):
        spec = spec_from_dict({**VALID, "key": "custom_x"})
        problems = descriptor_problems(
            spec,
            existing_keys=frozenset({"custom_x"}),
            existing_envs=frozenset(),
            existing_accents=frozenset(),
        )
        assert any("taken" in p for p in problems)

    def test_a_derived_env_collision_is_fatal(self):
        spec = spec_from_dict(VALID)
        problems = descriptor_problems(
            spec,
            existing_keys=frozenset(),
            existing_envs=frozenset({"YEABOI_CUSTOM_STATUSPAGE_TOKEN"}),
            existing_accents=frozenset(),
        )
        assert any("already in use" in p for p in problems)

    def test_the_header_scheme_needs_a_lawful_header(self):
        bad = spec_from_dict({**VALID, "auth_scheme": "header", "header_name": "Cookie"})
        assert any("may not be" in p for p in _no_problems(bad))
        good = spec_from_dict({**VALID, "auth_scheme": "header", "header_name": "X-Api-Key"})
        assert _no_problems(good) == []


class TestStore:
    def test_save_load_round_trip(self, _store):
        custom.save_custom(spec_from_dict(VALID))
        (loaded,) = custom.load_specs()
        assert loaded.key == "custom_statuspage"
        assert loaded.events == EventsMapping(**VALID["events"])

    def test_saving_an_invalid_descriptor_raises_with_the_problems(self, _store):
        with pytest.raises(ValueError, match="summary"):
            custom.save_custom(spec_from_dict({**VALID, "summary": ""}))
        assert not _store.exists()

    def test_a_damaged_file_is_a_warning_not_a_crash(self, _store):
        _store.write_text("{not json", encoding="utf-8")
        custom.invalidate()
        assert custom.load_specs() == ()

    def test_an_unknown_version_is_ignored_whole(self, _store):
        _store.write_text(json.dumps({"version": 99, "connectors": [VALID]}), encoding="utf-8")
        custom.invalidate()
        assert custom.load_specs() == ()

    def test_the_descriptor_file_never_holds_a_credential(self, _store, monkeypatch):
        monkeypatch.setenv("YEABOI_CUSTOM_STATUSPAGE_TOKEN", "sp-secret-token")
        custom.save_custom(spec_from_dict(VALID))
        assert "sp-secret-token" not in _store.read_text(encoding="utf-8")

    def test_delete_removes_only_the_named_one(self, _store):
        custom.save_custom(spec_from_dict(VALID))
        other = {**VALID, "key": "custom_other", "label": "Other", "accent": "rgb(9,9,9)"}
        custom.save_custom(spec_from_dict(other))
        assert custom.delete_custom("custom_statuspage") is True
        assert [s.key for s in custom.load_specs()] == ["custom_other"]
        assert custom.delete_custom("custom_statuspage") is False


class TestRegistryMerge:
    def test_a_saved_connection_joins_the_catalog(self, _store):
        custom.save_custom(spec_from_dict(VALID))
        assert registry.by_key("custom_statuspage") is not None
        assert "custom_statuspage" in {c.key for c in registry.all_connectors()}
        assert "custom_statuspage" not in {c.key for c in registry.builtin_connectors()}
        assert "YEABOI_CUSTOM_STATUSPAGE_TOKEN" in registry.secret_envs()

    def test_the_settings_engine_masks_the_custom_secret(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(VALID))
        secret = "sp-token-never-shown-whole"
        monkeypatch.setenv("YEABOI_CUSTOM_STATUSPAGE_TOKEN", secret)
        from dataclasses import asdict

        from yeaboi.settings.engine import get_settings

        payload = get_settings()
        row = next(f for f in payload.fields if f.env == "YEABOI_CUSTOM_STATUSPAGE_TOKEN")
        assert row.secret is True
        assert secret not in json.dumps(asdict(payload))

    def test_deleting_leaves_no_settings_field_behind(self, _store):
        custom.save_custom(spec_from_dict(VALID))
        custom.delete_custom("custom_statuspage")
        from yeaboi.settings.engine import get_settings

        envs = {f.env for f in get_settings().fields}
        assert not any(e.startswith("YEABOI_CUSTOM_STATUSPAGE") for e in envs)

    def test_a_custom_secret_set_after_import_is_redacted(self, monkeypatch):
        from yeaboi.redaction import redact

        monkeypatch.setenv("YEABOI_CUSTOM_ANYTHING_TOKEN", "tok-set-mid-process-123")
        assert "tok-set-mid-process-123" not in redact("leaked tok-set-mid-process-123 here")


class TestVerify:
    def test_the_generic_probe_builds_the_declared_request(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(VALID))
        seen = {}

        def fake_probe(url, *, headers):
            seen.update({"url": url, "headers": headers})
            return 200, ""

        monkeypatch.setattr("yeaboi.connectors.http.probe_status", fake_probe)
        from yeaboi.provider_verification import _verify_custom_api

        ok, message = _verify_custom_api(key="custom_statuspage", base_url="https://api.statuspage.io/", token="tok")
        assert (ok, message) == (True, "Statuspage verified")
        assert seen["url"] == "https://api.statuspage.io/v1/pages"
        assert seen["headers"] == {"Authorization": "Bearer tok"}

    def test_verify_connection_reaches_it_through_the_registry(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(VALID))
        monkeypatch.setattr("yeaboi.connectors.http.probe_status", lambda url, *, headers: (200, ""))
        from yeaboi.settings.engine import verify_connection

        result = verify_connection("custom_statuspage", {"base_url": "https://api.statuspage.io", "token": "tok"})
        assert result["ok"] is True

    def test_a_private_base_url_never_leaves(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(VALID))
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("a request left for a private host"))
        from yeaboi.provider_verification import _verify_custom_api

        ok, _ = _verify_custom_api(key="custom_statuspage", base_url="https://127.0.0.1:9000", token="tok")
        assert ok is False


class TestFetch:
    BODY = {
        "incidents": [
            {
                "id": "inc-1",
                "name": "API degraded",
                "impact": "major",
                "status": "resolved",
                "shortlink": "https://stspg.io/x",
                "created_at": "2026-06-10T09:00:00Z",
                "postmortem_body": "PASSWORD=hunter2 in the logs",
            },
            {"id": "inc-2", "impact": "minor"},  # no name → dropped, not crashed
        ]
    }

    def _connected(self, monkeypatch):
        monkeypatch.setenv("YEABOI_CUSTOM_STATUSPAGE_BASE_URL", "https://api.statuspage.io")
        monkeypatch.setenv("YEABOI_CUSTOM_STATUSPAGE_TOKEN", "tok")

    def test_rows_become_ops_events_through_the_declared_paths(self, _store, monkeypatch):
        from yeaboi.ops.events import EVENT_KINDS

        custom.save_custom(spec_from_dict(VALID))
        self._connected(monkeypatch)
        monkeypatch.setattr("yeaboi.connectors.custom.spec_by_key", custom.spec_by_key)
        monkeypatch.setattr("yeaboi.connectors.fetching.read_json", lambda url, *, headers, source: self.BODY)
        connector = registry.by_key("custom_statuspage")
        (event,) = custom.fetch_events(connector, None, None)
        assert event.kind in EVENT_KINDS
        assert (event.kind, event.source, event.ref) == ("incident", "custom_statuspage", "inc-1")
        assert event.severity == "high"
        assert event.url == "https://stspg.io/x"
        assert "hunter2" not in repr(event)

    def test_gather_dispatches_through_the_fetch_module_seam(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(VALID))
        self._connected(monkeypatch)
        monkeypatch.setattr("yeaboi.connectors.fetching.read_json", lambda url, *, headers, source: self.BODY)
        from yeaboi.connectors.fetching import gather

        result = gather("custom_statuspage", since="14d")
        source = next(s for s in result.sources if s.key == "custom_statuspage")
        assert source.ok is True


class TestEngine:
    def test_create_returns_the_catalog_row(self, _store):
        from yeaboi.connectors.engine import create_custom_connection

        row = create_custom_connection(VALID)
        assert row["key"] == "custom_statuspage"
        assert row["managed_by"] == "connections"
        assert row["verify_kind"] == "custom_statuspage"

    def test_delete_clears_the_stored_values_too(self, _store, monkeypatch, tmp_path):
        from yeaboi.connectors.engine import create_custom_connection, delete_custom_connection

        cleared = []
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: cleared.append((k, v)))
        create_custom_connection(VALID)
        delete_custom_connection("custom_statuspage")
        assert ("YEABOI_CUSTOM_STATUSPAGE_TOKEN", "") in cleared
        assert registry.by_key("custom_statuspage") is None

    def test_draft_validates_and_never_saves(self, _store, monkeypatch):
        from types import SimpleNamespace

        from yeaboi.connectors.engine import draft_custom_connection

        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_json", lambda prompt, **kw: SimpleNamespace(content=json.dumps(VALID))
        )
        result = draft_custom_connection("connect statuspage")
        assert result["ok"] is True
        assert result["draft"]["key"] == "custom_statuspage"
        assert custom.load_specs() == ()  # drafting saves nothing

    def test_a_dangerous_draft_is_neutralized_by_the_validator(self, _store, monkeypatch):
        from types import SimpleNamespace

        bad = {**VALID, "probe_path": "https://evil.example/exfil", "auth_scheme": "header", "header_name": "Cookie"}
        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_json", lambda prompt, **kw: SimpleNamespace(content=json.dumps(bad))
        )
        from yeaboi.connectors.engine import draft_custom_connection

        result = draft_custom_connection("something hostile")
        assert result["ok"] is False
        assert result["problems"]

    def test_a_webhook_draft_is_judged_like_any_other(self, _store, monkeypatch):
        from types import SimpleNamespace

        from yeaboi.connectors.engine import draft_custom_connection

        webhook = {
            "key": "custom_pager",
            "label": "Pager",
            "family": "incidents",
            "summary": "Inbound incident deliveries, mapped to events",
            "glyph": "📟",
            "accent": "rgb(21,91,51)",
            "kind": "webhook",
            "webhook_verify": "hmac",
            "events": {"kind": "incident", "title_path": "incident.name"},
        }
        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_json", lambda prompt, **kw: SimpleNamespace(content=json.dumps(webhook))
        )
        result = draft_custom_connection("a service that can only push deliveries")
        assert result["ok"] is True
        assert result["draft"]["kind"] == "webhook"
        assert custom.load_specs() == ()

    def test_unusable_model_output_is_a_message_not_a_crash(self, _store, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_json", lambda prompt, **kw: SimpleNamespace(content="not json at all")
        )
        from yeaboi.connectors.engine import draft_custom_connection

        result = draft_custom_connection("gibberish")
        assert result["ok"] is False
        assert "draft" in result


MCP_VALID = {
    "key": "custom_context7",
    "label": "Context7",
    "family": "docs",
    "summary": "Docs lookup over MCP, so the agent can cite the current API",
    "glyph": "🧰",
    "accent": "rgb(30,120,90)",
    "kind": "mcp",
}


class _McpServer:
    """A post_json stand-in answering the three-step handshake, recording each call."""

    def __init__(self, *, status: int = 200, sse: bool = False, name: str = "Context7", tools: int = 2):
        self.status, self.sse, self.name, self.tools = status, sse, name, tools
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url, *, headers, payload, timeout=None):
        import json as _json
        from types import SimpleNamespace

        self.calls.append((url, dict(headers), payload))
        if payload.get("method") == "initialize":
            body = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": self.name}}}
            resp_headers = {"mcp-session-id": "sess-1", "content-type": "application/json"}
        elif payload.get("method") == "tools/list":
            body = {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{} for _ in range(self.tools)]}}
            resp_headers = {"content-type": "application/json"}
        else:  # notifications/initialized
            body = {}
            resp_headers = {"content-type": "application/json"}
        if self.sse:
            text = f"event: message\ndata: {_json.dumps(body)}\n\n"
            resp_headers["content-type"] = "text/event-stream"
            return SimpleNamespace(status_code=self.status, headers=resp_headers, text=text, content=text.encode())
        raw = _json.dumps(body)
        return SimpleNamespace(
            status_code=self.status,
            headers=resp_headers,
            text=raw,
            content=raw.encode(),
            json=lambda b=body: b,
        )


class TestMcpKind:
    """The third custom kind: a server URL, an optional token, and a handshake."""

    def test_the_valid_descriptor_passes(self):
        assert _no_problems(spec_from_dict(MCP_VALID)) == []

    @pytest.mark.parametrize(
        ("patch", "needle"),
        [
            ({"events": VALID["events"]}, "gathers nothing"),
            ({"header_name": "X-Api-Key"}, "header name"),
        ],
    )
    def test_the_http_shape_is_refused(self, patch, needle):
        problems = _no_problems(spec_from_dict({**MCP_VALID, **patch}))
        assert any(needle in p for p in problems), f"{needle!r} not named in {problems}"

    def test_the_derived_envs_are_a_url_and_a_token(self):
        spec = spec_from_dict(MCP_VALID)
        assert spec.derived_envs() == ("YEABOI_CUSTOM_CONTEXT7_URL", "YEABOI_CUSTOM_CONTEXT7_TOKEN")

    def test_it_joins_the_catalog_connected_on_the_url_alone(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))
        connector = registry.by_key("custom_context7")
        assert connector is not None
        assert connector.verify == "_verify_custom_mcp"
        assert connector.fetch == ""  # config + verify only — it gathers nothing
        monkeypatch.setenv("YEABOI_CUSTOM_CONTEXT7_URL", "https://mcp.example.com/mcp")
        assert registry.is_connected(connector)  # the token is optional

    def test_the_row_carries_its_kind_on_the_wire(self, _store, monkeypatch):
        from yeaboi.connectors.engine import create_custom_connection, list_connections

        row = create_custom_connection(MCP_VALID)
        assert row["kind"] == "mcp"
        assert "webhook_secret" not in row  # no delivery secret is minted for mcp
        monkeypatch.setenv("YEABOI_CUSTOM_CONTEXT7_URL", "https://mcp.example.com/mcp")
        listed = next(r for r in list_connections()["connectors"] if r["key"] == "custom_context7")
        assert listed["kind"] == "mcp"

    def test_the_handshake_runs_in_order_with_bearer_and_session(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))
        server = _McpServer()
        monkeypatch.setattr("yeaboi.connectors.http.post_json", server)
        from yeaboi.provider_verification import _verify_custom_mcp

        ok, message = _verify_custom_mcp(key="custom_context7", url="https://mcp.example.com/mcp", token="tok")
        assert ok is True
        assert message == "MCP server 'Context7' verified — 2 tool(s)"
        methods = [payload.get("method") for _, _, payload in server.calls]
        assert methods == ["initialize", "notifications/initialized", "tools/list"]
        assert all(headers["Authorization"] == "Bearer tok" for _, headers, _ in server.calls)
        # The session id from initialize rides every later call.
        assert server.calls[1][1]["Mcp-Session-Id"] == "sess-1"
        assert server.calls[2][1]["Mcp-Session-Id"] == "sess-1"

    def test_a_tokenless_server_still_verifies(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))
        server = _McpServer(tools=0)
        monkeypatch.setattr("yeaboi.connectors.http.post_json", server)
        from yeaboi.provider_verification import _verify_custom_mcp

        ok, _ = _verify_custom_mcp(key="custom_context7", url="https://mcp.example.com/mcp")
        assert ok is True
        assert all("Authorization" not in headers for _, headers, _ in server.calls)

    def test_an_sse_shaped_body_is_parsed(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))
        monkeypatch.setattr("yeaboi.connectors.http.post_json", _McpServer(sse=True, tools=3))
        from yeaboi.provider_verification import _verify_custom_mcp

        ok, message = _verify_custom_mcp(key="custom_context7", url="https://mcp.example.com/mcp")
        assert ok is True
        assert "3 tool(s)" in message

    def test_a_rejected_credential_is_named(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))
        monkeypatch.setattr("yeaboi.connectors.http.post_json", _McpServer(status=401))
        from yeaboi.provider_verification import INVALID_KEY, _verify_custom_mcp

        assert _verify_custom_mcp(key="custom_context7", url="https://mcp.example.com/mcp") == (False, INVALID_KEY)

    def test_a_host_that_does_not_speak_mcp_is_named(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))
        monkeypatch.setattr("yeaboi.connectors.http.post_json", _McpServer(status=405))
        from yeaboi.provider_verification import _verify_custom_mcp

        ok, message = _verify_custom_mcp(key="custom_context7", url="https://mcp.example.com/mcp")
        assert ok is False
        assert "streamable HTTP" in message

    def test_a_non_https_url_never_leaves(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("a request left for an http URL"))
        from yeaboi.provider_verification import _verify_custom_mcp

        ok, _ = _verify_custom_mcp(key="custom_context7", url="http://mcp.example.com/mcp")
        assert ok is False

    def test_a_private_host_never_leaves(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("a request left for a private host"))
        from yeaboi.provider_verification import _verify_custom_mcp

        ok, _ = _verify_custom_mcp(key="custom_context7", url="https://127.0.0.1:8642/mcp")
        assert ok is False

    def test_the_token_never_appears_in_a_failure(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(MCP_VALID))

        def boom(url, *, headers, payload, timeout=None):
            raise OSError("connect failed for bearer super-secret-mcp-token")

        monkeypatch.setenv("YEABOI_CUSTOM_CONTEXT7_TOKEN", "super-secret-mcp-token")
        monkeypatch.setattr("yeaboi.connectors.http.post_json", boom)
        from yeaboi.provider_verification import _verify_custom_mcp

        ok, message = _verify_custom_mcp(
            key="custom_context7", url="https://mcp.example.com/mcp", token="super-secret-mcp-token"
        )
        assert ok is False
        assert "super-secret-mcp-token" not in message


APP_KEY_FIELD = {
    "label": "Application Key",
    "env_suffix": "APP_KEY",
    "secret": True,
    "header_name": "DD-Application-Key",
    "hint": "Organization settings → Application Keys",
}

EXTRAS_VALID = {**VALID, "extra_fields": [APP_KEY_FIELD, {"label": "Site", "env_suffix": "SITE", "secret": False}]}


class TestExtraFields:
    """The Datadog shape for customs: extra credentials/config beyond the auth scheme."""

    def test_the_datadog_shaped_descriptor_passes(self):
        assert _no_problems(spec_from_dict(EXTRAS_VALID)) == []

    @pytest.mark.parametrize(
        ("extras", "needle"),
        [
            ([{**APP_KEY_FIELD, "env_suffix": "app-key"}], "UPPER_SNAKE"),
            ([{**APP_KEY_FIELD, "env_suffix": "TOKEN"}], "reserved"),
            ([APP_KEY_FIELD, {**APP_KEY_FIELD, "header_name": ""}], "duplicate extra-field env suffix"),
            ([{**APP_KEY_FIELD, "label": " "}], "label"),
            ([{**APP_KEY_FIELD, "header_name": "Cookie"}], "may not be"),
            ([{**APP_KEY_FIELD, "header_name": "Bad Header"}], "letters, digits and hyphens"),
            ([{**APP_KEY_FIELD, "env_suffix": f"K{i}"} for i in range(5)], "at most 4"),
        ],
    )
    def test_each_broken_extra_is_named(self, extras, needle):
        problems = _no_problems(spec_from_dict({**VALID, "extra_fields": extras}))
        assert any(needle in p for p in problems), f"{needle!r} not named in {problems}"

    def test_an_extra_header_may_not_shadow_the_auth_header(self):
        spec = spec_from_dict(
            {
                **VALID,
                "auth_scheme": "header",
                "header_name": "X-Api-Key",
                "extra_fields": [{**APP_KEY_FIELD, "header_name": "x-api-key"}],
            }
        )
        assert any("duplicate header name" in p for p in _no_problems(spec))

    @pytest.mark.parametrize("base", [MCP_VALID, {**VALID, "kind": "webhook", "webhook_verify": "token"}])
    def test_only_the_api_kind_may_declare_extras(self, base):
        problems = _no_problems(spec_from_dict({**base, "extra_fields": [APP_KEY_FIELD]}))
        assert any("belong to the api kind" in p for p in problems)

    def test_the_derived_envs_include_the_suffixes(self):
        spec = spec_from_dict(EXTRAS_VALID)
        assert "YEABOI_CUSTOM_STATUSPAGE_APP_KEY" in spec.derived_envs()
        assert "YEABOI_CUSTOM_STATUSPAGE_SITE" in spec.derived_envs()

    def test_the_connector_carries_the_extra_fields(self):
        connector = custom.to_connector(spec_from_dict(EXTRAS_VALID))
        app_key = next(f for f in connector.fields if f.env == "YEABOI_CUSTOM_STATUSPAGE_APP_KEY")
        assert (app_key.label, app_key.secret, app_key.verify_arg) == ("Application Key", True, "app_key")
        site = next(f for f in connector.fields if f.env == "YEABOI_CUSTOM_STATUSPAGE_SITE")
        assert (site.secret, site.verify_arg) == (False, "site")

    def test_auth_headers_send_the_declared_extra_header(self):
        spec = spec_from_dict(EXTRAS_VALID)
        values = {"YEABOI_CUSTOM_STATUSPAGE_TOKEN": "tok", "YEABOI_CUSTOM_STATUSPAGE_APP_KEY": "app-secret"}
        headers = custom.auth_headers(spec, values)
        assert headers["Authorization"] == "Bearer tok"
        assert headers["DD-Application-Key"] == "app-secret"

    def test_round_trip_through_the_store(self, _store):
        custom.save_custom(spec_from_dict(EXTRAS_VALID))
        (loaded,) = custom.load_specs()
        assert loaded.extra_fields[0] == CustomFieldSpec(**APP_KEY_FIELD)
        assert loaded.extra_fields[1].secret is False

    def test_a_spec_without_extras_serializes_exactly_as_before(self):
        data = spec_from_dict(VALID).to_dict()
        assert "extra_fields" not in data
        assert "icon_data" not in data

    def test_the_settings_engine_masks_the_extra_secret(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(EXTRAS_VALID))
        secret = "dd-app-key-never-shown-whole"
        monkeypatch.setenv("YEABOI_CUSTOM_STATUSPAGE_APP_KEY", secret)
        from dataclasses import asdict

        from yeaboi.settings.engine import get_settings

        payload = get_settings()
        row = next(f for f in payload.fields if f.env == "YEABOI_CUSTOM_STATUSPAGE_APP_KEY")
        assert row.secret is True
        assert secret not in json.dumps(asdict(payload))

    def test_the_probe_carries_the_extra_header(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(EXTRAS_VALID))
        seen = {}

        def fake_probe(url, *, headers):
            seen.update({"url": url, "headers": headers})
            return 200, ""

        monkeypatch.setattr("yeaboi.connectors.http.probe_status", fake_probe)
        from yeaboi.provider_verification import _verify_custom_api

        ok, _ = _verify_custom_api(
            key="custom_statuspage", base_url="https://api.statuspage.io", token="tok", app_key="app-secret"
        )
        assert ok is True
        assert seen["headers"]["DD-Application-Key"] == "app-secret"

    def test_verify_connection_carries_the_extra_field(self, _store, monkeypatch):
        custom.save_custom(spec_from_dict(EXTRAS_VALID))
        monkeypatch.setattr("yeaboi.connectors.http.probe_status", lambda url, *, headers: (200, ""))
        from yeaboi.settings.engine import verify_connection

        fields = {"base_url": "https://api.statuspage.io", "token": "tok", "app_key": "app", "site": "eu"}
        assert verify_connection("custom_statuspage", fields)["ok"] is True

    def test_a_supplied_host_needs_every_secret_supplied(self, _store, monkeypatch):
        # Pairing a caller's host with the STORED app key would exfiltrate it.
        custom.save_custom(spec_from_dict(EXTRAS_VALID))
        monkeypatch.setenv("YEABOI_CUSTOM_STATUSPAGE_APP_KEY", "stored-app-key")
        monkeypatch.setenv("YEABOI_CUSTOM_STATUSPAGE_SITE", "eu")
        from yeaboi.settings.engine import verify_connection

        with pytest.raises(ValueError, match="app_key"):
            verify_connection("custom_statuspage", {"base_url": "https://attacker.example", "token": "tok"})


# A 1×1 transparent PNG — small, real, and byte-checkable.
PNG_ICON = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestIcon:
    """An uploaded icon is a validated raster data URI — never SVG, never oversized."""

    def test_a_small_png_icon_passes(self):
        assert _no_problems(spec_from_dict({**VALID, "icon_data": PNG_ICON})) == []

    @pytest.mark.parametrize(
        ("icon", "needle"),
        [
            ("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "SVG"),
            ("https://cdn.example/icon.png", "SVG"),  # only a data URI is accepted
            ("data:image/png;base64,AAA", "not decodable"),
            ("data:image/png;base64," + PNG_ICON.split(",", 1)[1].replace("iVBOR", "aVBOR"), "do not match"),
        ],
    )
    def test_each_bad_icon_is_named(self, icon, needle):
        problems = _no_problems(spec_from_dict({**VALID, "icon_data": icon}))
        assert any(needle in p for p in problems), f"{needle!r} not named in {problems}"

    def test_an_oversized_icon_is_refused_before_decoding(self):
        import base64

        blob = b"\x89PNG\r\n\x1a\n" + b"\x00" * (80 * 1024)
        icon = "data:image/png;base64," + base64.b64encode(blob).decode()
        problems = _no_problems(spec_from_dict({**VALID, "icon_data": icon}))
        assert any("64KB" in p for p in problems)

    def test_the_icon_rides_the_catalog_row(self, _store):
        from yeaboi.connectors.engine import create_custom_connection, list_connections

        row = create_custom_connection({**VALID, "icon_data": PNG_ICON})
        assert row["icon"] == PNG_ICON
        builtin = next(
            r for r in list_connections(connected_only=False)["connectors"] if not r["key"].startswith("custom_")
        )
        assert builtin["icon"] == ""

    def test_the_icon_survives_the_store_round_trip(self, _store):
        custom.save_custom(spec_from_dict({**VALID, "icon_data": PNG_ICON}))
        (loaded,) = custom.load_specs()
        assert loaded.icon_data == PNG_ICON
