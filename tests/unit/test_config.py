"""Tests for configuration and environment variable handling."""

import os

import pytest

from yeaboi.config import (
    VALID_LOG_LEVELS,
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
    is_langsmith_enabled,
    is_tips_enabled,
    load_user_config,
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
