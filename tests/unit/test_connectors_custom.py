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
from yeaboi.connectors.custom import CustomSpec, EventsMapping, spec_from_dict
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

    def test_unusable_model_output_is_a_message_not_a_crash(self, _store, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_json", lambda prompt, **kw: SimpleNamespace(content="not json at all")
        )
        from yeaboi.connectors.engine import draft_custom_connection

        result = draft_custom_connection("gibberish")
        assert result["ok"] is False
        assert "draft" in result
