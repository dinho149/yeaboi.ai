"""Tests for configuration and environment variable handling."""

import os
from pathlib import Path

import pytest

from yeaboi import config
from yeaboi.config import (
    BETA_ACK_KEY,
    FORCE_BETA_NOTICE_ENV,
    VALID_LOG_LEVELS,
    beta_notices_acked,
    detect_proxy,
    disable_langsmith_tracing,
    get_anthropic_api_key,
    get_config_dir,
    get_config_file,
    get_log_level,
    get_session_prune_days,
    get_team_analysis_code_max_concurrency,
    get_team_analysis_doc_max_concurrency,
    get_team_analysis_doc_request_timeout_seconds,
    get_team_analysis_enrichment_timeout_seconds,
    get_team_analysis_fast_model,
    get_team_analysis_llm_max_concurrency,
    get_team_analysis_llm_target_seconds,
    get_tunnel_timeout_minutes,
    is_beta_notice_enabled,
    is_beta_notice_seen,
    is_langsmith_enabled,
    is_tips_enabled,
    load_user_config,
    mark_beta_notice_seen,
    set_log_level,
    set_tips_enabled,
)


def test_get_anthropic_api_key_returns_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    assert get_anthropic_api_key() == "test-key-123"


def test_analysis_enrichment_timeout_defaults_and_is_bounded(monkeypatch):
    monkeypatch.delenv("TEAM_ANALYSIS_ENRICHMENT_TIMEOUT_SECONDS", raising=False)
    assert get_team_analysis_enrichment_timeout_seconds() == 120
    monkeypatch.setenv("TEAM_ANALYSIS_ENRICHMENT_TIMEOUT_SECONDS", "2")
    assert get_team_analysis_enrichment_timeout_seconds() == 10
    monkeypatch.setenv("TEAM_ANALYSIS_ENRICHMENT_TIMEOUT_SECONDS", "9999")
    assert get_team_analysis_enrichment_timeout_seconds() == 600


def test_analysis_fast_model_override(monkeypatch):
    monkeypatch.delenv("TEAM_ANALYSIS_FAST_MODEL", raising=False)
    assert get_team_analysis_fast_model() is None
    monkeypatch.setenv("TEAM_ANALYSIS_FAST_MODEL", " custom-fast-model ")
    assert get_team_analysis_fast_model() == "custom-fast-model"


def test_analysis_llm_runtime_defaults_and_bounds(monkeypatch):
    monkeypatch.delenv("TEAM_ANALYSIS_LLM_TARGET_SECONDS", raising=False)
    monkeypatch.delenv("TEAM_ANALYSIS_LLM_MAX_CONCURRENCY", raising=False)
    assert get_team_analysis_llm_target_seconds() == 600
    assert get_team_analysis_llm_max_concurrency() == 6
    monkeypatch.setenv("TEAM_ANALYSIS_LLM_TARGET_SECONDS", "2")
    monkeypatch.setenv("TEAM_ANALYSIS_LLM_MAX_CONCURRENCY", "99")
    assert get_team_analysis_llm_target_seconds() == 60
    assert get_team_analysis_llm_max_concurrency() == 12


def test_analysis_documentation_runtime_defaults_and_bounds(monkeypatch):
    monkeypatch.delenv("TEAM_ANALYSIS_DOC_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TEAM_ANALYSIS_DOC_MAX_CONCURRENCY", raising=False)
    assert get_team_analysis_doc_request_timeout_seconds() == 30
    assert get_team_analysis_doc_max_concurrency() == 8
    monkeypatch.setenv("TEAM_ANALYSIS_DOC_REQUEST_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("TEAM_ANALYSIS_DOC_MAX_CONCURRENCY", "99")
    assert get_team_analysis_doc_request_timeout_seconds() == 5
    assert get_team_analysis_doc_max_concurrency() == 16


def test_analysis_code_runtime_defaults_and_bounds(monkeypatch):
    monkeypatch.delenv("TEAM_ANALYSIS_CODE_MAX_CONCURRENCY", raising=False)
    assert get_team_analysis_code_max_concurrency() == 6
    monkeypatch.setenv("TEAM_ANALYSIS_CODE_MAX_CONCURRENCY", "99")
    assert get_team_analysis_code_max_concurrency() == 16
    monkeypatch.setenv("TEAM_ANALYSIS_CODE_MAX_CONCURRENCY", "invalid")
    assert get_team_analysis_code_max_concurrency() == 6


def test_analysis_tracker_runtime_defaults_and_bounds(monkeypatch):
    from yeaboi.config import get_team_analysis_tracker_max_concurrency

    monkeypatch.delenv("TEAM_ANALYSIS_TRACKER_MAX_CONCURRENCY", raising=False)
    assert get_team_analysis_tracker_max_concurrency() == 4
    monkeypatch.setenv("TEAM_ANALYSIS_TRACKER_MAX_CONCURRENCY", "99")
    assert get_team_analysis_tracker_max_concurrency() == 12
    monkeypatch.setenv("TEAM_ANALYSIS_TRACKER_MAX_CONCURRENCY", "0")
    assert get_team_analysis_tracker_max_concurrency() == 1
    monkeypatch.setenv("TEAM_ANALYSIS_TRACKER_MAX_CONCURRENCY", "invalid")
    assert get_team_analysis_tracker_max_concurrency() == 4


def test_analysis_max_change_lookups_defaults_and_bounds(monkeypatch):
    from yeaboi.config import get_team_analysis_max_change_lookups

    monkeypatch.delenv("TEAM_ANALYSIS_MAX_CHANGE_LOOKUPS", raising=False)
    assert get_team_analysis_max_change_lookups() == 500
    monkeypatch.setenv("TEAM_ANALYSIS_MAX_CHANGE_LOOKUPS", "9")
    assert get_team_analysis_max_change_lookups() == 50
    monkeypatch.setenv("TEAM_ANALYSIS_MAX_CHANGE_LOOKUPS", "99999")
    assert get_team_analysis_max_change_lookups() == 5000
    monkeypatch.setenv("TEAM_ANALYSIS_MAX_CHANGE_LOOKUPS", "invalid")
    assert get_team_analysis_max_change_lookups() == 500


def test_azure_devops_org_url_normalised(monkeypatch):
    from yeaboi.config import get_azure_devops_org_url

    monkeypatch.delenv("AZURE_DEVOPS_ORG_URL", raising=False)
    assert get_azure_devops_org_url() is None
    # Regression: a scheme-less value reached the SDK's URL joining and produced
    # "MissingSchema: Invalid URL 'dev.azure.com/org/dev.azure.com/org/_apis'".
    monkeypatch.setenv("AZURE_DEVOPS_ORG_URL", "dev.azure.com/youlend")
    assert get_azure_devops_org_url() == "https://dev.azure.com/youlend"
    monkeypatch.setenv("AZURE_DEVOPS_ORG_URL", " https://dev.azure.com/youlend/ ")
    assert get_azure_devops_org_url() == "https://dev.azure.com/youlend"
    monkeypatch.setenv("AZURE_DEVOPS_ORG_URL", "http://azdo.internal/org")
    assert get_azure_devops_org_url() == "http://azdo.internal/org"


def test_get_anthropic_api_key_raises_when_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(OSError, match="ANTHROPIC_API_KEY is not set"):
        get_anthropic_api_key()


def test_langsmith_enabled_when_configured(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test-key")
    assert is_langsmith_enabled() is True


def test_langsmith_disabled_when_no_key(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert is_langsmith_enabled() is False


def test_langsmith_disabled_when_tracing_off(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test-key")
    assert is_langsmith_enabled() is False


def test_tips_enabled_by_default(monkeypatch):
    monkeypatch.delenv("TIPS_ENABLED", raising=False)
    assert is_tips_enabled() is True


def test_tips_enabled_true_value(monkeypatch):
    monkeypatch.setenv("TIPS_ENABLED", "true")
    assert is_tips_enabled() is True


def test_tips_disabled_when_false(monkeypatch):
    monkeypatch.setenv("TIPS_ENABLED", "false")
    assert is_tips_enabled() is False


def test_tips_disabled_case_insensitive(monkeypatch):
    monkeypatch.setenv("TIPS_ENABLED", "FALSE")
    assert is_tips_enabled() is False


def test_duck_enabled_by_default(monkeypatch):
    from yeaboi.config import is_duck_enabled

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    assert is_duck_enabled() is True


def test_duck_disabled_when_false(monkeypatch):
    from yeaboi.config import is_duck_enabled

    monkeypatch.setenv("DUCK_ENABLED", "FALSE")
    assert is_duck_enabled() is False


def test_set_duck_enabled_round_trips(monkeypatch, tmp_path):
    from yeaboi.config import is_duck_enabled, set_duck_enabled

    config_file = tmp_path / ".env"
    monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
    monkeypatch.delenv("DUCK_ENABLED", raising=False)

    set_duck_enabled(False)
    assert os.environ["DUCK_ENABLED"] == "false"
    assert "DUCK_ENABLED" in config_file.read_text()
    assert is_duck_enabled() is False

    set_duck_enabled(True)
    assert os.environ["DUCK_ENABLED"] == "true"
    assert is_duck_enabled() is True


def test_set_tips_enabled_round_trips(monkeypatch, tmp_path):
    # Point config at a temp file so we don't touch the real ~/.yeaboi/.env.
    config_file = tmp_path / ".env"
    monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
    monkeypatch.delenv("TIPS_ENABLED", raising=False)

    set_tips_enabled(False)
    assert os.environ["TIPS_ENABLED"] == "false"
    assert "TIPS_ENABLED" in config_file.read_text()
    assert is_tips_enabled() is False

    set_tips_enabled(True)
    assert os.environ["TIPS_ENABLED"] == "true"
    assert is_tips_enabled() is True


class TestBetaNotices:
    """The one-time beta-notice acknowledgement (persisted to ~/.yeaboi/.env)."""

    @pytest.fixture(autouse=True)
    def _isolated_env(self, monkeypatch, tmp_path):
        self.config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: self.config_file)
        monkeypatch.delenv(BETA_ACK_KEY, raising=False)
        monkeypatch.delenv(FORCE_BETA_NOTICE_ENV, raising=False)

    def test_unseen_by_default(self):
        assert is_beta_notice_seen("performance") is False
        assert beta_notices_acked() == set()

    def test_mark_round_trips(self):
        mark_beta_notice_seen("performance")

        assert os.environ[BETA_ACK_KEY] == "performance"
        assert BETA_ACK_KEY in self.config_file.read_text()
        assert is_beta_notice_seen("performance") is True

    def test_other_modes_stay_unseen(self):
        mark_beta_notice_seen("performance")
        assert is_beta_notice_seen("reporting") is False

    def test_accumulates_without_duplicating(self):
        mark_beta_notice_seen("performance")
        mark_beta_notice_seen("reporting")
        mark_beta_notice_seen("performance")

        assert beta_notices_acked() == {"performance", "reporting"}
        assert os.environ[BETA_ACK_KEY] == "performance,reporting"

    @pytest.mark.parametrize("raw", ["", "   ", ",,", " , performance ,"])
    def test_tolerates_hand_edited_values(self, monkeypatch, raw):
        # The key lives in a file users open to edit their API keys.
        monkeypatch.setenv(BETA_ACK_KEY, raw)
        assert is_beta_notice_seen("reporting") is False
        assert is_beta_notice_seen("performance") is ("performance" in raw)

    @pytest.mark.parametrize("forced", ["1", "true", "TRUE", "yes", "on"])
    def test_force_flag_regates_an_acked_mode(self, monkeypatch, forced):
        mark_beta_notice_seen("performance")
        monkeypatch.setenv(FORCE_BETA_NOTICE_ENV, forced)
        assert is_beta_notice_seen("performance") is False

    def test_force_flag_accepts_a_mode_list(self, monkeypatch):
        mark_beta_notice_seen("performance")
        mark_beta_notice_seen("reporting")
        monkeypatch.setenv(FORCE_BETA_NOTICE_ENV, "performance")

        assert is_beta_notice_seen("performance") is False
        assert is_beta_notice_seen("reporting") is True

    def test_unwritable_config_still_suppresses_for_this_session(self, monkeypatch):
        def _boom(key, value):
            raise OSError("read-only file system")

        monkeypatch.setattr("yeaboi.config.set_config_value", _boom)

        mark_beta_notice_seen("performance")  # must not raise

        assert is_beta_notice_seen("performance") is True


class TestIsBetaNoticeEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("BETA_NOTICES_ENABLED", raising=False)
        assert is_beta_notice_enabled() is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "  false  "])
    def test_disabled_when_false(self, monkeypatch, value):
        monkeypatch.setenv("BETA_NOTICES_ENABLED", value)
        assert is_beta_notice_enabled() is False

    @pytest.mark.parametrize("value", ["0", "off", "nonsense"])
    def test_only_false_disables_it(self, monkeypatch, value):
        # Opt-out semantics: a typo must not silently hide the caveat.
        monkeypatch.setenv("BETA_NOTICES_ENABLED", value)
        assert is_beta_notice_enabled() is True


class TestSetLogLevel:
    def test_round_trips(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        set_log_level("INFO")
        assert os.environ["LOG_LEVEL"] == "INFO"
        assert "LOG_LEVEL" in config_file.read_text()
        assert get_log_level() == "INFO"

    def test_lowercase_normalized(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)

        set_log_level("debug")
        assert get_log_level() == "DEBUG"

    def test_invalid_level_raises_and_writes_nothing(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)

        with pytest.raises(ValueError, match="invalid log level"):
            set_log_level("VERBOSE")
        assert not config_file.exists()

    def test_critical_not_settable_from_cycle(self, monkeypatch, tmp_path):
        # CRITICAL stays readable from .env but is not in the settable cycle.
        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)

        assert "CRITICAL" not in VALID_LOG_LEVELS
        with pytest.raises(ValueError):
            set_log_level("CRITICAL")

    def test_preserves_other_keys(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".env"
        config_file.write_text("ANTHROPIC_API_KEY=sk-existing\n")
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)

        set_log_level("ERROR")

        contents = config_file.read_text()
        assert "ANTHROPIC_API_KEY=sk-existing" in contents
        assert "LOG_LEVEL" in contents


def test_set_tips_enabled_preserves_other_keys(monkeypatch, tmp_path):
    config_file = tmp_path / ".env"
    config_file.write_text("ANTHROPIC_API_KEY=sk-existing\n")
    monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)

    set_tips_enabled(False)

    contents = config_file.read_text()
    assert "ANTHROPIC_API_KEY=sk-existing" in contents
    assert "TIPS_ENABLED" in contents


class TestProxyDetection:
    """Tests for proxy environment variable detection and LangSmith auto-disable."""

    def _clear_proxy_vars(self, monkeypatch):
        """Remove all proxy env vars so tests start from a clean state."""
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(var, raising=False)

    def test_detect_proxy_https(self, monkeypatch):
        self._clear_proxy_vars(monkeypatch)
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
        assert detect_proxy() == "http://proxy:8080"

    def test_detect_proxy_http(self, monkeypatch):
        self._clear_proxy_vars(monkeypatch)
        monkeypatch.setenv("HTTP_PROXY", "http://proxy:3128")
        assert detect_proxy() == "http://proxy:3128"

    def test_detect_proxy_lowercase(self, monkeypatch):
        self._clear_proxy_vars(monkeypatch)
        monkeypatch.setenv("https_proxy", "http://proxy:9090")
        assert detect_proxy() == "http://proxy:9090"

    def test_detect_proxy_none(self, monkeypatch):
        self._clear_proxy_vars(monkeypatch)
        assert detect_proxy() is None

    def test_disable_langsmith_tracing(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        disable_langsmith_tracing()
        assert "LANGSMITH_TRACING" not in os.environ


class TestGetConfigDir:
    """Tests for get_config_dir() — returns ~/.yeaboi/, creating it if absent."""

    def test_returns_yeaboi_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.config.Path.home", lambda: tmp_path)
        result = get_config_dir()
        assert result == tmp_path / ".yeaboi"

    def test_creates_directory_if_absent(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.config.Path.home", lambda: tmp_path)
        target = tmp_path / ".yeaboi"
        assert not target.exists()
        get_config_dir()
        assert target.is_dir()

    def test_no_error_if_directory_already_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.config.Path.home", lambda: tmp_path)
        (tmp_path / ".yeaboi").mkdir()
        # Should not raise
        get_config_dir()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_config_dir_is_owner_only(self, monkeypatch, tmp_path):
        import stat

        monkeypatch.setattr("yeaboi.config.Path.home", lambda: tmp_path)
        d = get_config_dir()
        assert stat.S_IMODE(d.stat().st_mode) == 0o700

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_config_dir_perms_repaired_when_too_open(self, monkeypatch, tmp_path):
        import stat

        monkeypatch.setattr("yeaboi.config.Path.home", lambda: tmp_path)
        (tmp_path / ".yeaboi").mkdir(mode=0o755)
        d = get_config_dir()  # a subsequent call must tighten an existing loose dir
        assert stat.S_IMODE(d.stat().st_mode) == 0o700


class TestSetConfigValue:
    """Tests for set_config_value() — the hardened choke point for secret writes."""

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_written_file_is_owner_only(self, monkeypatch, tmp_path):
        import stat

        from yeaboi.config import set_config_value

        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        set_config_value("SLACK_WEBHOOK_URL", "https://hooks.example/secret")
        assert config_file.exists()
        assert stat.S_IMODE(config_file.stat().st_mode) == 0o600

    def test_persists_value(self, monkeypatch, tmp_path):
        from yeaboi.config import set_config_value

        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        set_config_value("SLACK_WEBHOOK_URL", "https://hooks.example/secret")
        assert "SLACK_WEBHOOK_URL='https://hooks.example/secret'" in config_file.read_text()


class TestApplyConfigValue:
    """Tests for apply_config_value() — writes the .env AND updates os.environ so a
    running session (e.g. the Settings page, which re-reads the environment) shows
    the new value immediately instead of only after a restart."""

    def test_persists_and_exports(self, monkeypatch, tmp_path):
        from yeaboi.config import apply_config_value

        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        monkeypatch.delenv("JIRA_EMAIL", raising=False)
        apply_config_value("JIRA_EMAIL", "dev@example.com")
        assert "JIRA_EMAIL='dev@example.com'" in config_file.read_text()
        assert os.environ["JIRA_EMAIL"] == "dev@example.com"

    def test_empty_value_clears_the_variable(self, monkeypatch, tmp_path):
        from yeaboi.config import apply_config_value

        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        monkeypatch.setenv("JIRA_EMAIL", "old@example.com")
        apply_config_value("JIRA_EMAIL", "")
        assert "JIRA_EMAIL" not in os.environ  # cleared, not left stale


class TestGetSessionsDb:
    """get_sessions_db() — legacy DB path, hardened to 0o600 when present."""

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_existing_db_perms_repaired(self, monkeypatch, tmp_path):
        import stat

        from yeaboi.config import get_sessions_db

        monkeypatch.setattr("yeaboi.config.Path.home", lambda: tmp_path)
        db = tmp_path / ".yeaboi" / "sessions.db"
        db.parent.mkdir()
        db.touch(mode=0o644)
        db.chmod(0o644)
        assert get_sessions_db() == db
        assert stat.S_IMODE(db.stat().st_mode) == 0o600

    def test_missing_db_not_created(self, monkeypatch, tmp_path):
        from yeaboi.config import get_sessions_db

        monkeypatch.setattr("yeaboi.config.Path.home", lambda: tmp_path)
        assert not get_sessions_db().exists()


class TestGetConfigFile:
    """Tests for get_config_file() — returns ~/.yeaboi/.env path."""

    def test_returns_dot_env_inside_config_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.config.Path.home", lambda: tmp_path)
        result = get_config_file()
        assert result == tmp_path / ".yeaboi" / ".env"


class TestLoadUserConfig:
    """Tests for load_user_config() — loads ~/.yeaboi/.env without overriding existing vars."""

    def test_loads_vars_from_file(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".yeaboi" / ".env"
        config_file.parent.mkdir()
        config_file.write_text("TEST_LOAD_VAR=hello-from-file\n")
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        monkeypatch.delenv("TEST_LOAD_VAR", raising=False)
        load_user_config()
        assert os.environ.get("TEST_LOAD_VAR") == "hello-from-file"

    def test_does_not_override_existing_env_vars(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".yeaboi" / ".env"
        config_file.parent.mkdir()
        config_file.write_text("TEST_OVERRIDE_VAR=from-file\n")
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        monkeypatch.setenv("TEST_OVERRIDE_VAR", "from-shell")
        load_user_config()
        # Shell value should win (override=False)
        assert os.environ.get("TEST_OVERRIDE_VAR") == "from-shell"

    def test_noop_when_file_absent(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".yeaboi" / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        # Should not raise even though the file doesn't exist
        load_user_config()


class TestGetSessionPruneDays:
    """Tests for get_session_prune_days() — SESSION_PRUNE_DAYS env var."""

    def test_default_30(self, monkeypatch):
        monkeypatch.delenv("SESSION_PRUNE_DAYS", raising=False)
        assert get_session_prune_days() == 30

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("SESSION_PRUNE_DAYS", "60")
        assert get_session_prune_days() == 60

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("SESSION_PRUNE_DAYS", "0")
        assert get_session_prune_days() == 0

    def test_negative_falls_back_to_30(self, monkeypatch):
        monkeypatch.setenv("SESSION_PRUNE_DAYS", "-5")
        assert get_session_prune_days() == 30

    def test_invalid_falls_back_to_30(self, monkeypatch):
        monkeypatch.setenv("SESSION_PRUNE_DAYS", "abc")
        assert get_session_prune_days() == 30


class TestGetTunnelTimeoutMinutes:
    """Tests for get_tunnel_timeout_minutes() — TUNNEL_TIMEOUT_MINUTES env var."""

    def test_default_60(self, monkeypatch):
        monkeypatch.delenv("TUNNEL_TIMEOUT_MINUTES", raising=False)
        assert get_tunnel_timeout_minutes() == 60

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("TUNNEL_TIMEOUT_MINUTES", "15")
        assert get_tunnel_timeout_minutes() == 15

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("TUNNEL_TIMEOUT_MINUTES", "0")
        assert get_tunnel_timeout_minutes() == 0

    def test_negative_clamps_to_zero(self, monkeypatch):
        monkeypatch.setenv("TUNNEL_TIMEOUT_MINUTES", "-5")
        assert get_tunnel_timeout_minutes() == 0

    def test_invalid_falls_back_to_60(self, monkeypatch):
        monkeypatch.setenv("TUNNEL_TIMEOUT_MINUTES", "abc")
        assert get_tunnel_timeout_minutes() == 60

    def test_clamps_to_24h(self, monkeypatch):
        monkeypatch.setenv("TUNNEL_TIMEOUT_MINUTES", "999999")
        assert get_tunnel_timeout_minutes() == 1440


class TestStandupConfig:
    def test_github_repo(self, monkeypatch):
        from yeaboi.config import get_standup_github_repo

        monkeypatch.setenv("STANDUP_GITHUB_REPO", "owner/repo")
        assert get_standup_github_repo() == "owner/repo"

    def test_github_repo_default_empty(self, monkeypatch):
        from yeaboi.config import get_standup_github_repo

        monkeypatch.delenv("STANDUP_GITHUB_REPO", raising=False)
        assert get_standup_github_repo() == ""

    def test_slack_webhook(self, monkeypatch):
        from yeaboi.config import get_slack_webhook_url

        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
        assert get_slack_webhook_url() == "https://hooks.slack.com/x"

    def test_smtp_port_default(self, monkeypatch):
        from yeaboi.config import get_smtp_port

        monkeypatch.delenv("STANDUP_SMTP_PORT", raising=False)
        assert get_smtp_port() == 587

    def test_smtp_port_invalid_falls_back(self, monkeypatch):
        from yeaboi.config import get_smtp_port

        monkeypatch.setenv("STANDUP_SMTP_PORT", "notaport")
        assert get_smtp_port() == 587

    def test_smtp_sender_defaults_to_user(self, monkeypatch):
        from yeaboi.config import get_smtp_sender

        monkeypatch.delenv("STANDUP_SMTP_SENDER", raising=False)
        monkeypatch.setenv("STANDUP_SMTP_USER", "me@example.com")
        assert get_smtp_sender() == "me@example.com"

    def test_email_recipients_parsed(self, monkeypatch):
        from yeaboi.config import get_standup_email_recipients

        monkeypatch.setenv("STANDUP_EMAIL_RECIPIENTS", "a@x.com, b@x.com ,")
        assert get_standup_email_recipients() == ["a@x.com", "b@x.com"]

    def test_email_recipients_empty(self, monkeypatch):
        from yeaboi.config import get_standup_email_recipients

        monkeypatch.delenv("STANDUP_EMAIL_RECIPIENTS", raising=False)
        assert get_standup_email_recipients() == []

    def test_set_slack_webhook_persists(self, monkeypatch, tmp_path):
        from yeaboi import config as cfg

        monkeypatch.setattr(cfg, "get_config_file", lambda: tmp_path / ".env")
        cfg.set_slack_webhook_url("https://hooks.slack.com/persisted")
        assert os.environ["SLACK_WEBHOOK_URL"] == "https://hooks.slack.com/persisted"
        assert "SLACK_WEBHOOK_URL" in (tmp_path / ".env").read_text()

    def test_user_name_default(self, monkeypatch):
        from yeaboi.config import get_standup_user_name

        monkeypatch.delenv("STANDUP_USER_NAME", raising=False)
        assert get_standup_user_name() == "Me"

    def test_user_name_from_env(self, monkeypatch):
        from yeaboi.config import get_standup_user_name

        monkeypatch.setenv("STANDUP_USER_NAME", "Omar")
        assert get_standup_user_name() == "Omar"

    def test_is_llm_configured_anthropic(self, monkeypatch):
        from yeaboi.config import is_llm_configured

        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        assert is_llm_configured() == (True, "ANTHROPIC_API_KEY not set")

    def test_is_llm_configured_missing_key(self, monkeypatch):
        from yeaboi.config import is_llm_configured

        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        ok, msg = is_llm_configured()
        assert ok is False
        assert "ANTHROPIC_API_KEY" in msg

    def test_is_llm_configured_ollama_needs_no_credentials(self, monkeypatch):
        """The keyless local provider is always 'configured' — reachability is checked at call time."""
        from yeaboi.config import is_llm_configured

        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert is_llm_configured() == (True, "")

    def test_ollama_base_url_default(self, monkeypatch):
        from yeaboi.config import get_ollama_base_url

        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        assert get_ollama_base_url() == "http://localhost:11434"

    def test_ollama_base_url_env_and_trailing_slash(self, monkeypatch):
        from yeaboi.config import get_ollama_base_url

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://10.0.0.5:11434/")
        assert get_ollama_base_url() == "http://10.0.0.5:11434"

    def test_ollama_num_ctx_default_and_env(self, monkeypatch):
        from yeaboi.config import get_ollama_num_ctx

        monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
        assert get_ollama_num_ctx() == 16384
        monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
        assert get_ollama_num_ctx() == 8192

    def test_ollama_num_ctx_invalid_falls_back(self, monkeypatch):
        from yeaboi.config import get_ollama_num_ctx

        monkeypatch.setenv("OLLAMA_NUM_CTX", "not-a-number")
        assert get_ollama_num_ctx() == 16384


class TestConfluenceConfig:
    """Confluence reuses the Jira Atlassian creds, but the CONFLUENCE_* vars win when set
    so it can be configured standalone (see get_confluence_base_url)."""

    _KEYS = (
        "CONFLUENCE_BASE_URL",
        "CONFLUENCE_EMAIL",
        "CONFLUENCE_API_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    )

    def _clear(self, monkeypatch):
        for k in self._KEYS:
            monkeypatch.delenv(k, raising=False)

    def test_prefers_confluence_vars(self, monkeypatch):
        from yeaboi.config import get_confluence_base_url, get_confluence_email, get_confluence_token

        self._clear(monkeypatch)
        # Both sets present — CONFLUENCE_* must win over JIRA_*.
        monkeypatch.setenv("JIRA_BASE_URL", "https://jira.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "jira@x.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "jira-tok")
        monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://conf.atlassian.net")
        monkeypatch.setenv("CONFLUENCE_EMAIL", "conf@x.com")
        monkeypatch.setenv("CONFLUENCE_API_TOKEN", "conf-tok")
        assert get_confluence_base_url() == "https://conf.atlassian.net"
        assert get_confluence_email() == "conf@x.com"
        assert get_confluence_token() == "conf-tok"

    def test_falls_back_to_jira(self, monkeypatch):
        from yeaboi.config import get_confluence_base_url, get_confluence_email, get_confluence_token

        self._clear(monkeypatch)
        # Only Jira set — Confluence getters fall back to it (existing setups).
        monkeypatch.setenv("JIRA_BASE_URL", "https://jira.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "jira@x.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "jira-tok")
        assert get_confluence_base_url() == "https://jira.atlassian.net"
        assert get_confluence_email() == "jira@x.com"
        assert get_confluence_token() == "jira-tok"

    def test_none_when_neither_set(self, monkeypatch):
        from yeaboi.config import get_confluence_base_url, get_confluence_email, get_confluence_token

        self._clear(monkeypatch)
        assert get_confluence_base_url() is None
        assert get_confluence_email() is None
        assert get_confluence_token() is None


class TestNotionConfig:
    """Notion has its own integration token (no shared Atlassian auth)."""

    def test_token_returns_value(self, monkeypatch):
        from yeaboi.config import get_notion_token

        monkeypatch.setenv("NOTION_TOKEN", "ntn_secret")
        assert get_notion_token() == "ntn_secret"

    def test_token_none_when_absent(self, monkeypatch):
        from yeaboi.config import get_notion_token

        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        assert get_notion_token() is None

    def test_root_page_id_returns_value(self, monkeypatch):
        from yeaboi.config import get_notion_root_page_id

        monkeypatch.setenv("NOTION_ROOT_PAGE_ID", "root123")
        assert get_notion_root_page_id() == "root123"

    def test_root_page_id_none_when_absent(self, monkeypatch):
        from yeaboi.config import get_notion_root_page_id

        monkeypatch.delenv("NOTION_ROOT_PAGE_ID", raising=False)
        assert get_notion_root_page_id() is None


class TestAllowedPaths:
    """The sandbox whitelist setting (YEABOI_ALLOWED_PATHS)."""

    def test_empty_by_default(self, monkeypatch):
        from yeaboi.config import get_allowed_paths

        monkeypatch.delenv("YEABOI_ALLOWED_PATHS", raising=False)
        assert get_allowed_paths() == ()

    def test_csv_parsed_and_deduped(self, monkeypatch):
        from yeaboi.config import get_allowed_paths

        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", "/a, /b ,/a,,  ")
        assert get_allowed_paths() == ("/a", "/b")

    def test_set_round_trip(self, monkeypatch, tmp_path):
        from yeaboi.config import get_allowed_paths, set_allowed_paths

        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: tmp_path / ".env")
        monkeypatch.delenv("YEABOI_ALLOWED_PATHS", raising=False)
        set_allowed_paths(["/x", " /y ", "/x"])
        assert os.environ["YEABOI_ALLOWED_PATHS"] == "/x,/y"
        assert get_allowed_paths() == ("/x", "/y")
        assert "YEABOI_ALLOWED_PATHS" in (tmp_path / ".env").read_text()

    def test_add_allowed_path_appends(self, monkeypatch, tmp_path):
        from yeaboi.config import add_allowed_path, get_allowed_paths

        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: tmp_path / ".env")
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", "/existing")
        add_allowed_path("/new")
        assert get_allowed_paths() == ("/existing", "/new")


class TestTeamAnalysisGithubOwners:
    """The GitHub estate Analysis scans (TEAM_ANALYSIS_GITHUB_OWNERS).

    Now settable from Settings and pickable per run, so the parse and the legacy
    fallback are what the TUI's pre-checked defaults are built from.
    """

    def test_csv_parsed_and_deduped(self, monkeypatch):
        from yeaboi.config import get_team_analysis_github_owners

        monkeypatch.setenv("TEAM_ANALYSIS_GITHUB_OWNERS", "acme, zeta ,acme,,  ")
        assert get_team_analysis_github_owners() == ("acme", "zeta")

    def test_falls_back_to_the_standup_repo_owner(self, monkeypatch):
        from yeaboi.config import get_team_analysis_github_owners

        monkeypatch.delenv("TEAM_ANALYSIS_GITHUB_OWNERS", raising=False)
        monkeypatch.setenv("STANDUP_GITHUB_REPO", "acme/widget")
        assert get_team_analysis_github_owners() == ("acme",)

    def test_empty_when_nothing_is_configured(self, monkeypatch):
        from yeaboi.config import get_team_analysis_github_owners

        monkeypatch.delenv("TEAM_ANALYSIS_GITHUB_OWNERS", raising=False)
        monkeypatch.delenv("STANDUP_GITHUB_REPO", raising=False)
        assert get_team_analysis_github_owners() == ()


class TestStorageAndExportConfig:
    """Data-dir override + setup-owned publish destinations (with natural fallbacks)."""

    def test_data_dir_empty_by_default(self, monkeypatch):
        from yeaboi.config import get_data_dir

        monkeypatch.delenv("YEABOI_HOME", raising=False)
        assert get_data_dir() == ""

    def test_data_dir_from_env(self, monkeypatch):
        from yeaboi.config import get_data_dir

        monkeypatch.setenv("YEABOI_HOME", "/tmp/yb-home")
        assert get_data_dir() == "/tmp/yb-home"

    def test_notion_export_page_wins_over_root(self, monkeypatch):
        from yeaboi.config import get_notion_export_parent_page_id

        monkeypatch.setenv("NOTION_ROOT_PAGE_ID", "root123")
        monkeypatch.setenv("NOTION_EXPORT_PARENT_PAGE_ID", "exp123")
        assert get_notion_export_parent_page_id() == "exp123"

    def test_notion_export_page_falls_back_to_root(self, monkeypatch):
        from yeaboi.config import get_notion_export_parent_page_id

        monkeypatch.setenv("NOTION_ROOT_PAGE_ID", "root123")
        monkeypatch.delenv("NOTION_EXPORT_PARENT_PAGE_ID", raising=False)
        assert get_notion_export_parent_page_id() == "root123"

    def test_notion_export_page_none_when_neither_set(self, monkeypatch):
        from yeaboi.config import get_notion_export_parent_page_id

        monkeypatch.delenv("NOTION_ROOT_PAGE_ID", raising=False)
        monkeypatch.delenv("NOTION_EXPORT_PARENT_PAGE_ID", raising=False)
        assert get_notion_export_parent_page_id() is None

    def test_confluence_export_parent_optional(self, monkeypatch):
        from yeaboi.config import get_confluence_export_parent_page_id

        monkeypatch.delenv("CONFLUENCE_EXPORT_PARENT_PAGE_ID", raising=False)
        assert get_confluence_export_parent_page_id() is None

    def test_set_data_dir_persists_to_env_file(self, monkeypatch, tmp_path):
        from yeaboi import config as cfg

        # setenv (not delenv) so monkeypatch registers a teardown even when the
        # var was previously absent — the setter writes os.environ directly, and
        # delenv(raising=False) on a missing var records nothing to restore.
        monkeypatch.setenv("YEABOI_HOME", "")
        monkeypatch.setattr(cfg, "get_config_file", lambda: tmp_path / ".env")
        cfg.set_data_dir("/tmp/yb-home")
        content = (tmp_path / ".env").read_text()
        assert "YEABOI_HOME" in content
        assert os.environ["YEABOI_HOME"] == "/tmp/yb-home"

    def test_set_data_dir_clears_with_empty_string(self, monkeypatch, tmp_path):
        from yeaboi import config as cfg
        from yeaboi.config import get_data_dir

        monkeypatch.setenv("YEABOI_HOME", "")  # registers restore-to-absent (see above)
        monkeypatch.setattr(cfg, "get_config_file", lambda: tmp_path / ".env")
        cfg.set_data_dir("  ")
        assert os.environ["YEABOI_HOME"] == ""
        assert get_data_dir() == ""


class TestVoiceInstallOffer:
    """The permanent "never ask again" tier of the dictation install offer."""

    def test_defaults_on(self, monkeypatch):
        monkeypatch.delenv("VOICE_INSTALL_OFFER", raising=False)
        monkeypatch.delenv("YEABOI_FORCE_VOICE_OFFER", raising=False)
        assert config.is_voice_install_offer_enabled() is True

    @pytest.mark.parametrize("value", ["off", "false", "0", "no", "OFF"])
    def test_disabling_values(self, monkeypatch, value):
        monkeypatch.delenv("YEABOI_FORCE_VOICE_OFFER", raising=False)
        monkeypatch.setenv("VOICE_INSTALL_OFFER", value)
        assert config.is_voice_install_offer_enabled() is False

    def test_the_force_env_reopens_a_permanent_decline(self, monkeypatch):
        """A once-ever gate is otherwise impossible to demo or review."""
        monkeypatch.setenv("VOICE_INSTALL_OFFER", "off")
        monkeypatch.setenv("YEABOI_FORCE_VOICE_OFFER", "1")
        assert config.is_voice_install_offer_enabled() is True

    def test_setter_writes_env_and_disk(self, monkeypatch):
        written: list[tuple[str, str]] = []
        monkeypatch.setattr(config, "set_config_value", lambda k, v: written.append((k, v)) or Path("/tmp/.env"))
        monkeypatch.delenv("YEABOI_FORCE_VOICE_OFFER", raising=False)
        # The setter writes os.environ itself, so register the key for teardown.
        monkeypatch.delenv("VOICE_INSTALL_OFFER", raising=False)
        config.set_voice_install_offer(False)
        assert os.environ["VOICE_INSTALL_OFFER"] == "off"
        assert written == [("VOICE_INSTALL_OFFER", "off")]
        assert config.is_voice_install_offer_enabled() is False

    def test_a_failed_disk_write_still_honours_the_decline_this_session(self, monkeypatch):
        """Re-asking on the next launch is the lesser failure; re-asking on the
        next keystroke is not."""

        def _boom(_k, _v):
            raise OSError("read-only home")

        monkeypatch.setattr(config, "set_config_value", _boom)
        monkeypatch.delenv("YEABOI_FORCE_VOICE_OFFER", raising=False)
        monkeypatch.delenv("VOICE_INSTALL_OFFER", raising=False)
        config.set_voice_install_offer(False)
        assert config.is_voice_install_offer_enabled() is False


class TestVoiceExtraMarker:
    def test_round_trip(self, monkeypatch):
        monkeypatch.delenv("VOICE_EXTRA_INSTALLED", raising=False)
        monkeypatch.setattr(config, "set_config_value", lambda _k, _v: Path("/tmp/.env"))
        assert config.voice_extra_was_installed() is False
        config.mark_voice_extra_installed()
        assert config.voice_extra_was_installed() is True


class TestLastCategory:
    """The landing split's persisted category preselection."""

    def test_default_is_humans(self, monkeypatch):
        monkeypatch.delenv("YEABOI_LAST_CATEGORY", raising=False)
        from yeaboi.config import get_last_category

        assert get_last_category() == "humans"

    def test_round_trip(self, monkeypatch, tmp_path):
        from yeaboi import config as cfg

        monkeypatch.setenv("YEABOI_LAST_CATEGORY", "humans")
        monkeypatch.setattr(cfg, "get_config_file", lambda: tmp_path / ".env")
        cfg.set_last_category("agents")
        assert os.environ["YEABOI_LAST_CATEGORY"] == "agents"
        assert cfg.get_last_category() == "agents"
        assert "YEABOI_LAST_CATEGORY" in (tmp_path / ".env").read_text()

    def test_unknown_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("YEABOI_LAST_CATEGORY", "robots!!")
        from yeaboi.config import get_last_category

        assert get_last_category() == "humans"

    def test_setter_rejects_unknown(self, monkeypatch, tmp_path):
        from yeaboi import config as cfg

        monkeypatch.setenv("YEABOI_LAST_CATEGORY", "humans")
        monkeypatch.setattr(cfg, "get_config_file", lambda: tmp_path / ".env")
        cfg.set_last_category("nonsense")
        assert cfg.get_last_category() == "humans"
        assert not (tmp_path / ".env").exists()
