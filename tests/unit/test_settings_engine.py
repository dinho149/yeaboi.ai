"""The settings engine — masked reads, allowlisted writes, and the TUI cross-checks.

The engine's field registry is a second declaration of the settings vocabulary
the TUI already renders, so the classes at the bottom hold the two against each
other: every env the TUI settings page collects, every choice table, and every
masked credential must agree with the engine's registry.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from yeaboi.settings import engine

SCREENS_SECONDARY = (
    Path(__file__).resolve().parents[2] / "src" / "yeaboi" / "ui" / "mode_select" / "screens" / "_screens_secondary.py"
)
MODE_SELECT_INIT = Path(__file__).resolve().parents[2] / "src" / "yeaboi" / "ui" / "mode_select" / "__init__.py"


def field_by_env(env: str) -> engine.SettingField:
    fld = next((f for f in engine._fields() if f.env == env), None)
    assert fld is not None, f"engine has no field for {env}"
    return fld


class TestMasking:
    def test_mask_keeps_a_short_prefix(self):
        assert engine._mask("sk-ant-abcdef123456789") == "sk-a" + "•" * 12
        assert engine._mask("abcde") == "abcd•"
        assert engine._mask("abc") == "•••"
        assert engine._mask("") == ""

    def test_snapshot_never_carries_a_raw_secret(self, monkeypatch):
        secret = "sk-ant-super-secret-value-123456"
        monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
        snap = engine.get_settings()
        assert secret not in repr(snap)
        row = next(f for f in snap.fields if f.env == "ANTHROPIC_API_KEY")
        assert row.value == engine._mask(secret)
        assert row.is_set is True

    def test_non_secret_values_pass_through(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")
        snap = engine.get_settings()
        row = next(f for f in snap.fields if f.env == "JIRA_BASE_URL")
        assert row.value == "https://org.atlassian.net"
        assert row.secret is False

    def test_every_secret_field_is_flagged(self):
        flagged = {f.env for f in engine._fields() if f.secret}
        assert flagged == set(engine.SECRET_ENVS)

    def test_snapshot_sections_cover_every_field(self):
        snap = engine.get_settings()
        assert {f.section for f in snap.fields} == set(snap.sections)

    def test_token_help_reaches_the_fields(self):
        snap = engine.get_settings()
        github = next(f for f in snap.fields if f.env == "GITHUB_TOKEN")
        assert github.help_url.startswith("https://")
        assert "repo" in github.help_scope

    def test_voice_video_keys_are_masked_voice_fields(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-super-secret-123456")
        monkeypatch.setenv("TAVUS_API_KEY", "tv-super-secret-123456")
        snap = engine.get_settings()
        for env in ("ELEVENLABS_API_KEY", "TAVUS_API_KEY"):
            row = next(f for f in snap.fields if f.env == env)
            assert row.section == "voice" and row.secret
            assert "secret" not in row.value
            assert row.help_url.startswith("https://")
        model = next(f for f in snap.fields if f.env == "ELEVENLABS_MODEL_ID")
        assert model.default == "eleven_turbo_v2_5"


class TestChoiceResolution:
    def test_unset_resolves_to_the_asymmetric_defaults(self):
        assert engine._resolve_choice(field_by_env("LOG_LEVEL"), "") == "WARNING"
        assert engine._resolve_choice(field_by_env("TIPS_ENABLED"), "") == "true"
        assert engine._resolve_choice(field_by_env("LANGSMITH_TRACING"), "") == "false"

    def test_stored_value_folds_to_option_casing(self):
        assert engine._resolve_choice(field_by_env("LOG_LEVEL"), "debug") == "DEBUG"
        assert engine._resolve_choice(field_by_env("LOG_LEVEL"), "bogus") == "WARNING"


class TestWrites:
    @pytest.fixture(autouse=True)
    def _no_disk(self, monkeypatch):
        self.applied: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: self.applied.__setitem__(k, v))

    def test_unknown_key_is_refused(self):
        with pytest.raises(ValueError, match="unknown setting"):
            engine.set_setting("NOT_A_KEY", "x")

    def test_dedicated_writers_are_enforced(self):
        with pytest.raises(ValueError, match="set_data_dir"):
            engine.set_setting("YEABOI_HOME", "/tmp/elsewhere")
        with pytest.raises(ValueError, match="set_allowed_paths"):
            engine.set_setting("YEABOI_ALLOWED_PATHS", "/tmp")

    def test_choice_values_are_validated_and_folded(self):
        with pytest.raises(ValueError, match="must be one of"):
            engine.set_setting("TIPS_ENABLED", "maybe")
        result = engine.set_setting("TIPS_ENABLED", "TRUE")
        assert result.ok and self.applied == {"TIPS_ENABLED": "true"}

    def test_clearing_is_allowed(self):
        result = engine.set_setting("ANTHROPIC_API_KEY", "")
        assert result.ok and self.applied == {"ANTHROPIC_API_KEY": ""}
        assert "cleared" in result.message

    def test_telemetry_write_says_it_needs_a_restart(self):
        # TELEMETRY_ENABLED is baked at import (telemetry.py) — the write must
        # not pretend the flip is live.
        result = engine.set_setting("YEABOI_TELEMETRY", "true")
        assert result.ok and self.applied == {"YEABOI_TELEMETRY": "true"}
        assert result.restart_required is True
        assert "next launch" in result.message

    def test_log_level_retunes_the_live_handlers(self, monkeypatch):
        calls: list[tuple[str, str]] = []
        monkeypatch.setattr("yeaboi.config.set_log_level", lambda level: calls.append(("set", level)))
        monkeypatch.setattr("yeaboi.logging_setup.apply_level", lambda level: calls.append(("apply", level)))
        result = engine.set_setting("LOG_LEVEL", "debug")
        assert result.ok
        assert calls == [("set", "DEBUG"), ("apply", "DEBUG")]
        assert "LOG_LEVEL" not in self.applied

    def test_allowed_paths_validates_and_delegates(self, monkeypatch):
        saved: list[list[str]] = []
        monkeypatch.setattr("yeaboi.config.set_allowed_paths", lambda paths: saved.append(list(paths)))
        monkeypatch.setattr("yeaboi.config.get_allowed_paths", lambda: ("/a", "/b"))
        with pytest.raises(ValueError, match="list of strings"):
            engine.set_allowed_paths("not-a-list")
        result = engine.set_allowed_paths(["/a", "/b"])
        assert result.ok and saved == [["/a", "/b"]]
        assert "2 entries" in result.message

    def test_data_dir_without_move(self, monkeypatch):
        written: list[str] = []
        monkeypatch.setattr("yeaboi.config.set_data_dir", written.append)
        monkeypatch.setattr(
            "yeaboi.paths.move_data_tree", lambda root: pytest.fail("move_data_tree called without move=True")
        )
        result = engine.set_data_dir("  /tmp/newhome  ")
        assert result.restart_required is True
        assert written == ["/tmp/newhome"]

    def test_data_dir_with_move(self, monkeypatch):
        moved: list[Path] = []
        monkeypatch.setattr("yeaboi.config.set_data_dir", lambda v: None)
        monkeypatch.setattr(
            "yeaboi.paths.move_data_tree", lambda root: (moved.append(root), (True, "Moved 3 items"))[1]
        )
        result = engine.set_data_dir("/tmp/newhome", move=True)
        assert moved == [Path("/tmp/newhome")]
        assert "Moved 3 items" in result.message and result.restart_required


class TestProviderCatalog:
    def test_catalog_matches_the_wizard_cards(self):
        from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS

        catalog = engine.provider_catalog()
        assert [p["provider_val"] for p in catalog["providers"]] == [c["provider_val"] for c in _PROVIDER_CARDS]
        assert all("color" not in p for p in catalog["providers"])
        assert catalog["anthropic_auth_modes"] == ["api_key", "subscription"]
        assert "GITHUB_TOKEN" in catalog["token_help"]

    def test_verify_unknown_provider_is_refused(self):
        with pytest.raises(ValueError, match="unknown provider"):
            engine.verify_provider("skynet", "key")

    def test_verify_chains_key_then_model(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_api_key",
            lambda card, key: (calls.append("key"), (True, "key ok"))[1],
        )
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_model",
            lambda card, key, model: (calls.append("model"), (True, f"{model} ok"))[1],
        )
        result = engine.verify_provider("anthropic", "sk-ant-x", model="claude-sonnet-4-6")
        assert result == {"ok": True, "message": "claude-sonnet-4-6 ok"}
        assert calls == ["key", "model"]

    def test_discover_merges_discovered_first_and_dedupes(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.provider_verification.fetch_available_models",
            lambda card, key: ["claude-new-1", "claude-sonnet-4-6"],
        )
        result = engine.discover_models("anthropic", "sk-ant-x")
        assert result["models"][0] == "claude-new-1"
        assert result["models"].count("claude-sonnet-4-6") == 1
        assert result["default"] == "claude-sonnet-4-6"

    def test_bedrock_skips_live_discovery(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.provider_verification.fetch_available_models",
            lambda card, key: pytest.fail("bedrock has no per-key model listing"),
        )
        result = engine.discover_models("bedrock", "us-east-1")
        assert result["models"] == ["us.anthropic.claude-sonnet-4-6-v1:0"]


class TestConnectionVerify:
    def test_unknown_kind_is_refused(self):
        with pytest.raises(ValueError, match="unknown connection kind"):
            engine.verify_connection("gitlab", {})

    def test_missing_field_with_empty_env_is_refused(self, monkeypatch):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        with pytest.raises(ValueError, match="notion verification needs token"):
            engine.verify_connection("notion", {})

    def test_explicit_fields_win_over_env(self, monkeypatch):
        seen: dict[str, str] = {}
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_stored")
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_vc_token",
            lambda vc, token: (seen.__setitem__("token", token), (True, "ok"))[1],
        )
        result = engine.verify_connection("github", {"token": "ghp_typed"})
        assert result == {"ok": True, "message": "ok"}
        assert seen["token"] == "ghp_typed"

    def test_env_fallback_covers_omitted_fields(self, monkeypatch):
        seen: dict[str, tuple] = {}
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "dev@org.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "jira-stored")
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_jira",
            lambda base_url, email, token: (seen.__setitem__("args", (base_url, email, token)), (True, "jira ok"))[1],
        )
        result = engine.verify_connection("jira", {})
        assert result["ok"] is True
        assert seen["args"] == ("https://org.atlassian.net", "dev@org.com", "jira-stored")

    def test_stored_token_refuses_a_caller_supplied_host(self, monkeypatch):
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "dev@org.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "jira-stored")
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_jira",
            lambda *a: pytest.fail("the stored token must never travel to a caller-chosen host"),
        )
        with pytest.raises(ValueError, match="supply the token"):
            engine.verify_connection("jira", {"base_url": "https://attacker.example"})
        with pytest.raises(ValueError, match="supply the token"):
            engine.verify_connection("jira", {"email": "attacker@example.com"})

    def test_caller_supplied_base_url_must_be_https(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_jira",
            lambda *a: pytest.fail("credentials must not go out over plain http"),
        )
        fields = {"base_url": "http://org.atlassian.net", "email": "dev@org.com", "token": "t"}
        with pytest.raises(ValueError, match="https"):
            engine.verify_connection("jira", fields)

    def test_confluence_takes_the_atlassian_account_plus_space(self, monkeypatch):
        seen: dict[str, tuple] = {}
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_confluence",
            lambda base_url, email, token, space_key: (
                seen.__setitem__("args", (base_url, email, token, space_key)),
                (False, "space not found"),
            )[1],
        )
        fields = {"base_url": "https://org.atlassian.net", "email": "dev@org.com", "token": "t", "space_key": "ENG"}
        result = engine.verify_connection("confluence", fields)
        assert result == {"ok": False, "message": "space not found"}
        assert seen["args"] == ("https://org.atlassian.net", "dev@org.com", "t", "ENG")

    def test_elevenlabs_dispatches_the_typed_key(self, monkeypatch):
        seen: dict[str, str] = {}
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_elevenlabs",
            lambda token: (seen.__setitem__("token", token), (True, "ElevenLabs verified"))[1],
        )
        result = engine.verify_connection("elevenlabs", {"token": "xi-typed"})
        assert result == {"ok": True, "message": "ElevenLabs verified"}
        assert seen["token"] == "xi-typed"

    def test_tavus_falls_back_to_the_stored_key(self, monkeypatch):
        seen: dict[str, str] = {}
        monkeypatch.setenv("TAVUS_API_KEY", "tv-stored")
        monkeypatch.setattr(
            "yeaboi.provider_verification._verify_tavus",
            lambda token: (seen.__setitem__("token", token), (False, "Invalid Tavus API key"))[1],
        )
        result = engine.verify_connection("tavus", {})
        assert result == {"ok": False, "message": "Invalid Tavus API key"}
        assert seen["token"] == "tv-stored"


class TestTuiParity:
    """The engine registry vs the TUI's own settings tables — no silent drift."""

    def _tui_collected_keys(self) -> set[str]:
        """The `_keys` list inside _collect_settings_data, read via AST."""
        tree = ast.parse(MODE_SELECT_INIT.read_text(encoding="utf-8"))
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_collect_settings_data"
        )
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_keys" for t in node.targets):
                return {elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)}
        pytest.fail("_collect_settings_data no longer assigns _keys — update this parser")

    def _tui_env_inventory(self) -> set[str]:
        """Every env the page collects: the literal `_keys` plus the registry's.

        Connector envs are derived at the call site rather than listed, so the
        AST parser above cannot see them — they are added here from the same
        source the page reads.
        """
        from yeaboi.connectors import registry

        return self._tui_collected_keys() | set(registry.all_envs())

    def test_every_tui_collected_env_is_an_engine_field(self):
        engine_envs = {f.env for f in engine._fields()}
        missing = self._tui_env_inventory() - engine_envs
        assert not missing, f"TUI settings page collects envs the engine registry lacks: {sorted(missing)}"

    def test_engine_only_fields_are_the_known_extras(self):
        # VOICE_INSTALL_OFFER is rendered by the TUI from config rather than the
        # collected env list; anything else engine-only is drift.
        engine_envs = {f.env for f in engine._fields()}
        extras = engine_envs - self._tui_env_inventory()
        assert extras == {"VOICE_INSTALL_OFFER"}, (
            f"engine registry grew envs the TUI page never shows: {sorted(extras)}"
        )

    def test_choice_tables_agree_with_the_tui(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            SETTINGS_CHOICE_DEFAULTS,
            SETTINGS_CHOICE_LABELS,
            SETTINGS_CHOICES,
        )

        for env, choices in SETTINGS_CHOICES.items():
            fld = field_by_env(env)
            assert fld.choices == tuple(choices), f"{env}: engine choices differ from the TUI's"
            assert fld.default == SETTINGS_CHOICE_DEFAULTS[env], f"{env}: engine default differs from the TUI's"
            assert fld.choice_labels == SETTINGS_CHOICE_LABELS.get(env, {}), f"{env}: labels differ from the TUI's"

    def test_masked_tui_rows_are_engine_secrets(self):
        source = SCREENS_SECONDARY.read_text(encoding="utf-8")
        masked = set(re.findall(r'masked=True,\s*env="([A-Z_]+)"', source))
        assert masked, "the masked-row scan found nothing — the TUI source changed shape"
        not_secret = masked - engine.SECRET_ENVS
        assert not not_secret, f"TUI masks envs the engine serves unmasked: {sorted(not_secret)}"

    def test_provider_choices_come_from_the_wizard_cards(self):
        from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS

        assert field_by_env("LLM_PROVIDER").choices == tuple(c["provider_val"] for c in _PROVIDER_CARDS)
