"""Tests for the LLM instance factory."""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel

from yeaboi.agent.llm import (
    _PROVIDER_DEFAULTS,
    DEFAULT_MODEL,
    _extract_local_perf,
    _supports_temperature,
    build_multimodal_content,
    estimate_tokens,
    get_analysis_fast_model,
    get_llm,
    get_usage_stats,
    invoke_json,
    invoke_with_images,
    llm_override,
    load_image_b64,
    reset_usage_stats,
    strip_json_fences,
    strip_think_tags,
    track_usage,
    warn_if_context_overflow,
)


class TestLlmOverride:
    """The llm_override() injection hook used by the MCP sampling mode."""

    def _fake_model(self):
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        return FakeListChatModel(responses=["ok"])

    def test_override_returned_ignoring_args(self, monkeypatch):
        # No API key needed — the override short-circuits provider selection,
        # and model/temperature args are ignored while it is active.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        fake = self._fake_model()
        with llm_override(fake):
            assert get_llm() is fake
            assert get_llm(model="anything", temperature=0.7) is fake

    def test_override_resets_after_block(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        fake = self._fake_model()
        with llm_override(fake):
            pass
        assert isinstance(get_llm(), ChatAnthropic)

    def test_override_resets_on_exception(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        fake = self._fake_model()
        with pytest.raises(ValueError):  # noqa: PT012 — asserting cleanup after the raise
            with llm_override(fake):
                raise ValueError("boom")
        assert isinstance(get_llm(), ChatAnthropic)

    def test_override_propagates_into_worker_threads(self, monkeypatch):
        # The MCP server sets the override in an async handler, then runs the
        # engine in an anyio worker thread — the ContextVar must follow.
        import anyio

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        fake = self._fake_model()
        result: list[object] = []

        async def main():
            with llm_override(fake):
                await anyio.to_thread.run_sync(lambda: result.append(get_llm()))

        anyio.run(main)
        assert result[0] is fake


class TestAnalysisFastModel:
    def test_cloud_provider_defaults(self, monkeypatch):
        monkeypatch.delenv("TEAM_ANALYSIS_FAST_MODEL", raising=False)
        expected = {
            "anthropic": "claude-haiku-4-5-20251001",
            "openai": "gpt-4o-mini",
            "google": "gemini-2.5-flash-lite",
        }
        for provider, model in expected.items():
            monkeypatch.setenv("LLM_PROVIDER", provider)
            assert get_analysis_fast_model() == model

    def test_local_and_account_scoped_providers_keep_primary_model(self, monkeypatch):
        monkeypatch.delenv("TEAM_ANALYSIS_FAST_MODEL", raising=False)
        for provider in ("ollama", "bedrock"):
            monkeypatch.setenv("LLM_PROVIDER", provider)
            assert get_analysis_fast_model() is None

    def test_explicit_override_and_disable(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("TEAM_ANALYSIS_FAST_MODEL", "my-fast-model")
        assert get_analysis_fast_model() == "my-fast-model"
        monkeypatch.setenv("TEAM_ANALYSIS_FAST_MODEL", "default")
        assert get_analysis_fast_model() is None

    def test_sampling_override_keeps_host_model(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        fake = TestLlmOverride()._fake_model()
        with llm_override(fake):
            assert get_analysis_fast_model() is None


class TestGetLlmAnthropic:
    """Tests for the default Anthropic provider."""

    def test_returns_base_chat_model(self, monkeypatch):
        """get_llm() must return a BaseChatModel regardless of provider."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        llm = get_llm()
        assert isinstance(llm, BaseChatModel)

    def test_default_provider_is_anthropic(self, monkeypatch):
        """Without LLM_PROVIDER set, the result must be a ChatAnthropic instance."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        llm = get_llm()
        assert isinstance(llm, ChatAnthropic)

    def test_explicit_anthropic_provider(self, monkeypatch):
        """LLM_PROVIDER=anthropic must return ChatAnthropic."""
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        llm = get_llm()
        assert isinstance(llm, ChatAnthropic)

    def test_default_model(self, monkeypatch):
        """Anthropic default model must be the claude-sonnet identifier."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        llm = get_llm()
        assert llm.model == DEFAULT_MODEL
        assert llm.model == "claude-sonnet-4-6"

    def test_default_temperature(self, monkeypatch):
        """Default temperature must be 0.0 for deterministic output."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        llm = get_llm()
        assert llm.temperature == 0.0

    def test_model_arg_overrides_default(self, monkeypatch):
        """Passing model= explicitly must override the provider default."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        llm = get_llm(model="claude-haiku-4-5-20251001")
        assert llm.model == "claude-haiku-4-5-20251001"

    def test_custom_temperature(self, monkeypatch):
        """Custom temperature must be forwarded to the chat model."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        llm = get_llm(temperature=0.7)
        assert llm.temperature == 0.7

    def test_raises_when_anthropic_key_missing(self, monkeypatch):
        """Must raise OSError when LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY is absent."""
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(OSError, match="ANTHROPIC_API_KEY is not set"):
            get_llm()

    def test_api_key_wired_from_env(self, monkeypatch):
        """The API key must be read from the environment and forwarded to ChatAnthropic."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        llm = get_llm()
        # ChatAnthropic stores the key as a SecretStr
        assert llm.anthropic_api_key.get_secret_value() == "sk-ant-test-key"


class TestTemperatureDeprecation:
    """Newer Claude models reject `temperature` by presence — it must be omitted.

    Sending the parameter at all (even a default) to Opus 4.6+ or the Claude 5
    family fails with HTTP 400 "temperature is deprecated for this model".
    """

    def test_older_models_support_temperature(self):
        for model in (
            "claude-sonnet-4-6",
            "claude-sonnet-4-20250514",
            "claude-haiku-4-5-20251001",
            "claude-3-5-sonnet-20241022",
            "claude-opus-4-5",
            "us.anthropic.claude-sonnet-4-6-v1:0",
        ):
            assert _supports_temperature(model), model

    def test_newer_models_reject_temperature(self):
        for model in (
            "claude-opus-4-6",
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-fable-5",
            "claude-sonnet-5",
            "us.anthropic.claude-opus-4-8-v1:0",
        ):
            assert not _supports_temperature(model), model

    def test_get_llm_omits_temperature_for_new_model(self, monkeypatch):
        """Opus 4.8 → the kwarg is never passed, so ChatAnthropic keeps its unset default."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        llm = get_llm(model="claude-opus-4-8", temperature=0.3)
        assert llm.temperature is None  # omitted → not sent in the request body

    def test_get_llm_keeps_temperature_for_old_model(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        llm = get_llm(model="claude-sonnet-4-6", temperature=0.3)
        assert llm.temperature == 0.3


class TestGetLlmModelOverride:
    """Tests for LLM_MODEL env-var override (applies to all providers)."""

    def test_llm_model_env_overrides_default(self, monkeypatch):
        """LLM_MODEL env var must override the provider's default model."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-6")
        llm = get_llm()
        assert llm.model == "claude-opus-4-6"

    def test_model_arg_takes_priority_over_llm_model_env(self, monkeypatch):
        """The model= argument must take priority over the LLM_MODEL env var."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-6")
        llm = get_llm(model="claude-haiku-4-5-20251001")
        assert llm.model == "claude-haiku-4-5-20251001"


class TestGetLlmOpenAI:
    """Tests for the OpenAI provider."""

    def test_openai_provider_returns_chat_openai(self, monkeypatch):
        """LLM_PROVIDER=openai must return a ChatOpenAI instance."""
        pytest.importorskip("langchain_openai", reason="langchain-openai not installed")
        from langchain_openai import ChatOpenAI

        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        llm = get_llm()
        assert isinstance(llm, ChatOpenAI)

    def test_openai_default_model(self, monkeypatch):
        """OpenAI provider default model must be gpt-4o."""
        pytest.importorskip("langchain_openai", reason="langchain-openai not installed")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        llm = get_llm()
        assert llm.model_name == _PROVIDER_DEFAULTS["openai"]

    def test_openai_raises_when_key_missing(self, monkeypatch):
        """Must raise OSError when LLM_PROVIDER=openai and OPENAI_API_KEY is absent."""
        pytest.importorskip("langchain_openai", reason="langchain-openai not installed")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(OSError, match="OPENAI_API_KEY is not set"):
            get_llm()

    def test_openai_missing_package_raises_import_error(self, monkeypatch):
        """Must raise ImportError with install instructions if langchain-openai is absent."""
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "langchain_openai":
                raise ImportError("No module named 'langchain_openai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(ImportError, match="langchain-openai is not installed"):
            get_llm()


class TestGetLlmGoogle:
    """Tests for the Google Gemini provider."""

    def test_google_provider_returns_chat_google(self, monkeypatch):
        """LLM_PROVIDER=google must return a ChatGoogleGenerativeAI instance."""
        pytest.importorskip("langchain_google_genai", reason="langchain-google-genai not installed")
        from langchain_google_genai import ChatGoogleGenerativeAI

        monkeypatch.setenv("LLM_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "google-test-key")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        llm = get_llm()
        assert isinstance(llm, ChatGoogleGenerativeAI)

    def test_google_raises_when_key_missing(self, monkeypatch):
        """Must raise OSError when LLM_PROVIDER=google and GOOGLE_API_KEY is absent."""
        pytest.importorskip("langchain_google_genai", reason="langchain-google-genai not installed")
        monkeypatch.setenv("LLM_PROVIDER", "google")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(OSError, match="GOOGLE_API_KEY is not set"):
            get_llm()

    def test_google_missing_package_raises_import_error(self, monkeypatch):
        """Must raise ImportError with install instructions if langchain-google-genai is absent."""
        monkeypatch.setenv("LLM_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "google-test")
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "langchain_google_genai":
                raise ImportError("No module named 'langchain_google_genai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(ImportError, match="langchain-google-genai is not installed"):
            get_llm()


class TestGetLlmOllama:
    """Tests for the local Ollama provider (keyless)."""

    def test_ollama_provider_returns_chat_ollama(self, monkeypatch):
        """LLM_PROVIDER=ollama must return a ChatOllama instance."""
        pytest.importorskip("langchain_ollama", reason="langchain-ollama not installed")
        from langchain_ollama import ChatOllama

        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        llm = get_llm()
        assert isinstance(llm, ChatOllama)

    def test_ollama_default_model(self, monkeypatch):
        """Ollama provider default model must match _PROVIDER_DEFAULTS."""
        pytest.importorskip("langchain_ollama", reason="langchain-ollama not installed")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        llm = get_llm()
        assert llm.model == _PROVIDER_DEFAULTS["ollama"]

    def test_ollama_needs_no_api_key(self, monkeypatch):
        """The keyless guarantee: no API key env vars set → no exception."""
        pytest.importorskip("langchain_ollama", reason="langchain-ollama not installed")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "AWS_REGION", "AWS_DEFAULT_REGION"):
            monkeypatch.delenv(var, raising=False)
        llm = get_llm()
        assert isinstance(llm, BaseChatModel)

    def test_ollama_base_url_from_env(self, monkeypatch):
        """OLLAMA_BASE_URL must be wired through to ChatOllama (trailing slash stripped)."""
        pytest.importorskip("langchain_ollama", reason="langchain-ollama not installed")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://10.0.0.5:11434/")
        llm = get_llm()
        assert llm.base_url == "http://10.0.0.5:11434"

    def test_ollama_default_base_url(self, monkeypatch):
        """Without OLLAMA_BASE_URL, the standard localhost address must be used."""
        pytest.importorskip("langchain_ollama", reason="langchain-ollama not installed")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        llm = get_llm()
        assert llm.base_url == "http://localhost:11434"

    def test_ollama_json_mode_sets_format(self, monkeypatch):
        """json_mode=True must turn on Ollama's constrained JSON decoding."""
        pytest.importorskip("langchain_ollama", reason="langchain-ollama not installed")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        assert get_llm(json_mode=True).format == "json"
        assert get_llm().format in ("", None)

    def test_ollama_json_mode_disables_thinking(self, monkeypatch):
        """Thinking models (qwen3) can burn the whole num_predict budget on a
        reasoning pass and return EMPTY constrained-JSON content — JSON calls
        must send think:false. Prose calls keep the model default (None)."""
        pytest.importorskip("langchain_ollama", reason="langchain-ollama not installed")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        assert get_llm(json_mode=True).reasoning is False
        assert get_llm().reasoning is None

    def test_ollama_num_ctx_from_env(self, monkeypatch):
        """OLLAMA_NUM_CTX must be wired through (default 16384)."""
        pytest.importorskip("langchain_ollama", reason="langchain-ollama not installed")
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
        assert get_llm().num_ctx == 16384
        monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
        assert get_llm().num_ctx == 8192

    def test_ollama_missing_package_raises_import_error(self, monkeypatch):
        """Must raise ImportError with install instructions if langchain-ollama is absent."""
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "langchain_ollama":
                raise ImportError("No module named 'langchain_ollama'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(ImportError, match="langchain-ollama is not installed"):
            get_llm()


class TestGetLlmUnknownProvider:
    """Tests for unknown/unsupported provider values."""

    def test_unknown_provider_raises_value_error(self, monkeypatch):
        """An unrecognised LLM_PROVIDER must raise ValueError with clear guidance."""
        monkeypatch.setenv("LLM_PROVIDER", "not-a-vendor")
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            get_llm()

    def test_error_message_lists_valid_providers(self, monkeypatch):
        """The ValueError message must name the valid providers."""
        monkeypatch.setenv("LLM_PROVIDER", "llama")
        with pytest.raises(ValueError, match="anthropic.*openai.*google"):
            get_llm()


class TestProviderDefaults:
    """Tests for the _PROVIDER_DEFAULTS constant."""

    def test_anthropic_default_is_sonnet(self):
        assert _PROVIDER_DEFAULTS["anthropic"] == "claude-sonnet-4-6"

    def test_openai_default_is_gpt4o(self):
        assert _PROVIDER_DEFAULTS["openai"] == "gpt-4o"

    def test_google_default_is_gemini_flash(self):
        assert _PROVIDER_DEFAULTS["google"] == "gemini-2.5-flash"

    def test_ollama_default_is_qwen3(self):
        assert _PROVIDER_DEFAULTS["ollama"] == "qwen3:8b"

    def test_default_model_constant_matches_anthropic(self):
        """DEFAULT_MODEL backward-compat constant must equal the Anthropic default."""
        assert DEFAULT_MODEL == _PROVIDER_DEFAULTS["anthropic"]


class TestTrackUsage:
    """Tests for token usage tracking."""

    def setup_method(self):
        """Reset usage stats before each test to avoid pollution."""
        reset_usage_stats()

    def test_anthropic_style_response(self):
        """track_usage with Anthropic-style response (response_metadata.usage.input_tokens/output_tokens)."""
        from types import SimpleNamespace

        resp = SimpleNamespace(response_metadata={"usage": {"input_tokens": 100, "output_tokens": 50}})
        track_usage(resp)
        stats = get_usage_stats()
        assert stats["input_tokens"] == 100
        assert stats["output_tokens"] == 50
        assert stats["total_tokens"] == 150
        assert stats["call_count"] == 1

    def test_openai_style_response(self):
        """track_usage with OpenAI-style response (response_metadata.token_usage.prompt_tokens/completion_tokens)."""
        from types import SimpleNamespace

        resp = SimpleNamespace(response_metadata={"token_usage": {"prompt_tokens": 200, "completion_tokens": 80}})
        track_usage(resp)
        stats = get_usage_stats()
        assert stats["input_tokens"] == 200
        assert stats["output_tokens"] == 80
        assert stats["total_tokens"] == 280
        assert stats["call_count"] == 1

    def test_accumulates_across_multiple_calls(self):
        """track_usage accumulates across multiple calls."""
        from types import SimpleNamespace

        resp1 = SimpleNamespace(response_metadata={"usage": {"input_tokens": 100, "output_tokens": 50}})
        resp2 = SimpleNamespace(response_metadata={"usage": {"input_tokens": 200, "output_tokens": 75}})
        track_usage(resp1)
        track_usage(resp2)
        stats = get_usage_stats()
        assert stats["input_tokens"] == 300
        assert stats["output_tokens"] == 125
        assert stats["total_tokens"] == 425
        assert stats["call_count"] == 2

    def test_empty_metadata_logs_warning(self, caplog):
        """track_usage with empty metadata logs warning."""
        import logging
        from types import SimpleNamespace

        resp = SimpleNamespace(response_metadata={})
        with caplog.at_level(logging.WARNING, logger="yeaboi.agent.llm"):
            track_usage(resp)
        assert "no token data found" in caplog.text
        stats = get_usage_stats()
        assert stats["call_count"] == 0

    def test_get_usage_stats_returns_accumulated_totals(self):
        """get_usage_stats returns accumulated totals."""
        from types import SimpleNamespace

        resp = SimpleNamespace(response_metadata={"usage": {"input_tokens": 500, "output_tokens": 200}})
        track_usage(resp)
        stats = get_usage_stats()
        assert stats["input_tokens"] == 500
        assert stats["output_tokens"] == 200
        assert stats["total_tokens"] == 700
        assert stats["call_count"] == 1

    def test_get_usage_stats_returns_copy_not_reference(self):
        """get_usage_stats returns copy not reference."""
        stats = get_usage_stats()
        stats["input_tokens"] = 999999
        fresh = get_usage_stats()
        assert fresh["input_tokens"] != 999999

    def test_usage_metadata_preferred(self):
        """LangChain-standard usage_metadata (ChatOllama populates it) wins over response_metadata."""
        from types import SimpleNamespace

        resp = SimpleNamespace(
            usage_metadata={"input_tokens": 40, "output_tokens": 15},
            response_metadata={},
        )
        track_usage(resp)
        stats = get_usage_stats()
        assert stats["input_tokens"] == 40
        assert stats["output_tokens"] == 15
        assert stats["call_count"] == 1

    def test_ollama_native_keys_in_response_metadata(self):
        """Ollama-native prompt_eval_count/eval_count keys are understood as a fallback."""
        from types import SimpleNamespace

        resp = SimpleNamespace(response_metadata={"prompt_eval_count": 120, "eval_count": 30})
        track_usage(resp)
        stats = get_usage_stats()
        assert stats["input_tokens"] == 120
        assert stats["output_tokens"] == 30
        assert stats["call_count"] == 1

    def test_reset_usage_stats_clears_counters(self):
        """reset_usage_stats clears counters."""
        from types import SimpleNamespace

        resp = SimpleNamespace(response_metadata={"usage": {"input_tokens": 100, "output_tokens": 50}})
        track_usage(resp)
        assert get_usage_stats()["total_tokens"] > 0
        reset_usage_stats()
        stats = get_usage_stats()
        assert stats["input_tokens"] == 0
        assert stats["output_tokens"] == 0
        assert stats["total_tokens"] == 0
        assert stats["call_count"] == 0


class TestExtractLocalPerf:
    """The Ollama timing extractor: nanoseconds → milliseconds + tokens/sec."""

    def _as_int(self, v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def test_ollama_metadata_converted(self):
        meta = {
            "total_duration": 2_000_000_000,  # 2 s
            "eval_duration": 1_000_000_000,  # 1 s
            "load_duration": 500_000_000,  # 0.5 s
            "eval_count": 40,
        }
        perf = _extract_local_perf(meta, self._as_int)
        assert perf["duration_ms"] == 2000.0
        assert perf["eval_duration_ms"] == 1000.0
        assert perf["load_duration_ms"] == 500.0
        assert perf["tokens_per_sec"] == 40.0  # 40 tokens / 1 s

    def test_cloud_metadata_returns_empty(self):
        # Anthropic-style metadata has no Ollama timing keys.
        assert _extract_local_perf({"usage": {"input_tokens": 1}}, self._as_int) == {}

    def test_non_dict_returns_empty(self):
        assert _extract_local_perf(None, self._as_int) == {}

    def test_missing_eval_count_leaves_tps_none(self):
        perf = _extract_local_perf({"total_duration": 1_000_000_000, "eval_duration": 500_000_000}, self._as_int)
        assert perf["tokens_per_sec"] is None


class TestTrackUsagePersistsPerf:
    """track_usage must forward the extracted perf kwargs to record_token_usage."""

    def setup_method(self):
        reset_usage_stats()

    def _capture(self, monkeypatch, resp, provider="ollama"):
        import yeaboi.agent.llm as llm_mod

        captured = {}

        class _FakeStore:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def record_token_usage(self, inp, out, model="", provider="", **perf):
                captured["inp"] = inp
                captured["out"] = out
                captured["perf"] = perf

        monkeypatch.setattr("yeaboi.sessions.SessionStore", _FakeStore)
        monkeypatch.setattr(llm_mod, "get_llm_provider", lambda: provider)
        monkeypatch.setattr(
            llm_mod, "get_llm_model", lambda: "qwen3:8b" if provider == "ollama" else "claude-sonnet-4-6"
        )
        track_usage(resp)
        return captured

    def test_ollama_perf_forwarded(self, monkeypatch):
        from types import SimpleNamespace

        resp = SimpleNamespace(
            usage_metadata={"input_tokens": 200, "output_tokens": 100},
            response_metadata={
                "total_duration": 3_000_000_000,
                "eval_duration": 2_000_000_000,
                "load_duration": 100_000_000,
                "eval_count": 100,
            },
        )
        captured = self._capture(monkeypatch, resp)
        assert captured["inp"] == 200
        assert captured["perf"]["duration_ms"] == 3000.0
        assert captured["perf"]["tokens_per_sec"] == 50.0  # 100 tokens / 2 s

    def test_cloud_response_forwards_no_perf(self, monkeypatch):
        from types import SimpleNamespace

        resp = SimpleNamespace(response_metadata={"usage": {"input_tokens": 100, "output_tokens": 50}})
        captured = self._capture(monkeypatch, resp, provider="anthropic")
        assert captured["perf"] == {}

    def test_local_call_logs_info_line(self, monkeypatch, caplog):
        import logging
        from types import SimpleNamespace

        resp = SimpleNamespace(
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
            response_metadata={"total_duration": 1_000_000_000, "eval_duration": 500_000_000, "eval_count": 20},
        )
        with caplog.at_level(logging.INFO, logger="yeaboi.agent.llm"):
            self._capture(monkeypatch, resp)
        assert "local call:" in caplog.text


class TestMultimodalHelpers:
    """Tests for the pasted-screenshot (vision) helpers."""

    PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    @pytest.fixture
    def png_file(self, tmp_path):
        p = tmp_path / "shot.png"
        p.write_bytes(self.PNG_BYTES)
        return p

    def test_load_image_b64_happy_path(self, png_file):
        import base64

        loaded = load_image_b64(png_file)
        assert loaded is not None
        b64, mime = loaded
        assert mime == "image/png"
        assert base64.b64decode(b64) == self.PNG_BYTES

    def test_load_image_b64_jpeg_mime(self, tmp_path):
        p = tmp_path / "shot.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        assert load_image_b64(p)[1] == "image/jpeg"

    def test_load_image_b64_missing_file_returns_none(self, tmp_path):
        assert load_image_b64(tmp_path / "gone.png") is None

    def test_build_content_no_images_returns_plain_string(self):
        assert build_multimodal_content("hello", []) == "hello"
        assert build_multimodal_content("hello", None) == "hello"

    def test_build_content_with_image_returns_blocks(self, png_file):
        content = build_multimodal_content("describe this", [str(png_file)])
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "describe this"}
        img = content[1]
        assert img["type"] == "image"
        assert img["source_type"] == "base64"
        assert img["mime_type"] == "image/png"
        assert img["data"]

    def test_build_content_all_missing_degrades_to_string(self, tmp_path):
        content = build_multimodal_content("text", [str(tmp_path / "gone.png")])
        assert content == "text"


class TestInvokeWithImages:
    """invoke_with_images must be a drop-in for invoke([HumanMessage(prompt)])."""

    class _FakeLLM:
        def __init__(self, fail_first=False):
            self.fail_first = fail_first
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            if self.fail_first and len(self.calls) == 1:
                raise ValueError("model does not support image input")
            return "response"

    def test_no_images_single_plain_invoke(self):
        llm = self._FakeLLM()
        assert invoke_with_images(llm, "prompt", []) == "response"
        assert len(llm.calls) == 1
        assert llm.calls[0][0].content == "prompt"

    def test_images_sent_as_blocks(self, tmp_path):
        p = tmp_path / "a.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        llm = self._FakeLLM()
        invoke_with_images(llm, "prompt", [str(p)])
        content = llm.calls[0][0].content
        assert isinstance(content, list)
        assert {b["type"] for b in content} == {"text", "image"}

    def test_rejected_images_retries_text_only(self, tmp_path):
        p = tmp_path / "a.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        llm = self._FakeLLM(fail_first=True)
        assert invoke_with_images(llm, "prompt", [str(p)]) == "response"
        assert len(llm.calls) == 2
        assert llm.calls[1][0].content == "prompt"  # retry dropped the image

    def test_text_only_failure_propagates(self):
        # With no images there is no retry — errors reach the caller's own
        # auth/billing handling untouched.
        llm = self._FakeLLM(fail_first=True)
        with pytest.raises(ValueError):
            invoke_with_images(llm, "prompt", [])


class TestStripThinkTags:
    """strip_think_tags removes local models' chain-of-thought from prose output."""

    def test_closed_block_removed(self):
        assert strip_think_tags("<think>hmm, let me plan</think>\nHello!") == "Hello!"

    def test_multiple_blocks_removed(self):
        out = strip_think_tags("<think>a</think>one<think>b</think> two")
        assert out == "one two"

    def test_unclosed_leading_block_removed(self):
        # Truncated generation: the model never closed its think block.
        assert strip_think_tags("<think>rambling that never ends") == ""

    def test_no_tags_passthrough(self):
        assert strip_think_tags("plain answer") == "plain answer"

    def test_non_string_passthrough(self):
        blocks = [{"type": "text", "text": "hi"}]
        assert strip_think_tags(blocks) is blocks


class TestStripJsonFences:
    """strip_json_fences must tolerate every common fencing style."""

    def test_bare_json_unchanged(self):
        assert strip_json_fences('{"a": 1}') == '{"a": 1}'

    def test_plain_fence(self):
        assert strip_json_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_json_tagged_fence(self):
        assert strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_whitespace_stripped(self):
        assert strip_json_fences('  {"a": 1}  \n') == '{"a": 1}'

    def test_empty_and_none_safe(self):
        assert strip_json_fences("") == ""
        assert strip_json_fences(None) == ""


class TestInvokeJson:
    """invoke_json: JSON-mode invoke with a one-shot repair re-ask."""

    class _FakeResp:
        def __init__(self, content):
            self.content = content
            self.response_metadata = {"usage": {"input_tokens": 1, "output_tokens": 1}}

    class _FakeLLM:
        """Returns queued responses in order; records every invoke's messages."""

        def __init__(self, contents):
            self.queue = list(contents)
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            return TestInvokeJson._FakeResp(self.queue.pop(0))

    def _patch(self, monkeypatch, llm):
        import yeaboi.agent.llm as llm_module

        captured = {}

        def fake_get_llm(model=None, temperature=0.0, json_mode=False):
            captured["model"] = model
            captured["json_mode"] = json_mode
            captured["temperature"] = temperature
            return llm

        monkeypatch.setattr(llm_module, "get_llm", fake_get_llm)
        return captured

    def setup_method(self):
        reset_usage_stats()

    def test_valid_first_try_single_invoke(self, monkeypatch):
        llm = self._FakeLLM(['{"ok": true}'])
        captured = self._patch(monkeypatch, llm)
        resp = invoke_json("prompt")
        assert resp.content == '{"ok": true}'
        assert len(llm.calls) == 1
        assert captured["json_mode"] is True

    def test_fenced_valid_json_accepted_without_reask(self, monkeypatch):
        llm = self._FakeLLM(['```json\n{"ok": true}\n```'])
        self._patch(monkeypatch, llm)
        resp = invoke_json("prompt")
        assert len(llm.calls) == 1
        assert "ok" in resp.content

    def test_invalid_then_valid_reasks_once_with_error(self, monkeypatch):
        llm = self._FakeLLM(["not json at all", '{"fixed": 1}'])
        self._patch(monkeypatch, llm)
        resp = invoke_json("prompt")
        assert resp.content == '{"fixed": 1}'
        assert len(llm.calls) == 2
        # The repair round must carry: original prompt, the bad reply, and the parse error.
        repair_messages = llm.calls[1]
        assert repair_messages[0].content == "prompt"
        assert repair_messages[1].content == "not json at all"
        assert "not valid JSON" in repair_messages[2].content

    def test_invalid_twice_returns_last_response(self, monkeypatch):
        """Caller-fallback contract: after the repair budget, the last reply is returned as-is."""
        llm = self._FakeLLM(["garbage one", "garbage two"])
        self._patch(monkeypatch, llm)
        resp = invoke_json("prompt")
        assert resp.content == "garbage two"
        assert len(llm.calls) == 2

    def test_tracks_usage_per_attempt(self, monkeypatch):
        llm = self._FakeLLM(["bad", '{"ok": 1}'])
        self._patch(monkeypatch, llm)
        invoke_json("prompt")
        assert get_usage_stats()["call_count"] == 2

    def test_temperature_forwarded(self, monkeypatch):
        llm = self._FakeLLM(['{"ok": 1}'])
        captured = self._patch(monkeypatch, llm)
        invoke_json("prompt", temperature=0.3)
        assert captured["temperature"] == 0.3

    def test_model_override_forwarded(self, monkeypatch):
        llm = self._FakeLLM(['{"ok": 1}'])
        captured = self._patch(monkeypatch, llm)
        invoke_json("prompt", model="fast-model")
        assert captured["model"] == "fast-model"

    def test_repair_reask_keeps_image_blocks(self, monkeypatch, tmp_path):
        """The repair round must rebuild the multimodal first message — a
        text-only re-ask would silently drop pasted screenshots."""
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG-fake-bytes")
        llm = self._FakeLLM(["not json at all", '{"fixed": 1}'])
        self._patch(monkeypatch, llm)
        invoke_json("prompt", image_paths=[str(img)])
        assert len(llm.calls) == 2
        first_repair_content = llm.calls[1][0].content
        assert isinstance(first_repair_content, list)
        assert any(isinstance(b, dict) and b.get("type") == "image" for b in first_repair_content)


class TestEstimateTokens:
    def test_roughly_four_chars_per_token(self):
        assert estimate_tokens("a" * 400) == 100

    def test_empty_string(self):
        assert estimate_tokens("") == 0


class TestWarnIfContextOverflow:
    """Ollama silently truncates past num_ctx — the guard must make it loggable."""

    def test_warns_for_oversized_ollama_prompt(self, monkeypatch, caplog):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
        # Default budget: 16384 ctx − 8192 output reserve → ~8192 prompt tokens
        # (~32 768 chars). 40k chars is safely over.
        with caplog.at_level("WARNING", logger="yeaboi.agent.llm"):
            warn_if_context_overflow("x" * 40_000)
        assert "OLLAMA_NUM_CTX" in caplog.text

    def test_silent_for_small_prompt(self, monkeypatch, caplog):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
        with caplog.at_level("WARNING", logger="yeaboi.agent.llm"):
            warn_if_context_overflow("a small prompt")
        assert "OLLAMA_NUM_CTX" not in caplog.text

    def test_noop_for_cloud_provider(self, monkeypatch, caplog):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        with caplog.at_level("WARNING", logger="yeaboi.agent.llm"):
            warn_if_context_overflow("x" * 200_000)
        assert "OLLAMA_NUM_CTX" not in caplog.text


class TestGetLlmAnthropicSubscription:
    """Subscription auth sends a bearer token, not an API key.

    The API rejects a request carrying both ``x-api-key`` and ``Authorization``,
    so these assert on the headers of a request the client actually builds — a
    check on the constructor arguments alone would pass while the wire form was
    wrong, which is the whole failure mode here.
    """

    @staticmethod
    def _headers(llm):
        import anthropic

        opts = anthropic._models.FinalRequestOptions.construct(method="post", url="/v1/messages", json_data={})
        return llm._client._build_request(opts).headers

    def test_bearer_replaces_the_api_key_header(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_MODE", "subscription")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-sent")
        headers = self._headers(get_llm())
        assert headers.get("authorization") == "Bearer sk-ant-oat01-abc"
        assert headers.get("x-api-key") is None
        assert headers.get("anthropic-beta") == "oauth-2025-04-20"

    def test_async_client_carries_the_bearer_too(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_MODE", "subscription")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-sent")
        llm = get_llm()
        assert llm._async_client.auth_token == "sk-ant-oat01-abc"
        assert llm._async_client.api_key is None

    def test_api_key_mode_is_untouched(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_MODE", "api_key")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        headers = self._headers(get_llm())
        assert headers.get("x-api-key") == "test-key-123"
        assert headers.get("authorization") is None
        assert headers.get("anthropic-beta") is None

    def test_unset_mode_still_uses_the_api_key(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        headers = self._headers(get_llm())
        assert headers.get("x-api-key") == "test-key-123"
        assert headers.get("authorization") is None

    def test_the_system_prompt_identifies_as_claude_code(self, monkeypatch):
        """Without this the API refuses the token — with a 429 whose body is
        literally `"Error"` and which carries no rate-limit headers, so it reads
        as a rate limit and is not one."""
        from langchain_core.messages import HumanMessage, SystemMessage

        from yeaboi.agent.llm import CLAUDE_CODE_SYSTEM

        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_MODE", "subscription")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc")
        payload = get_llm()._get_request_payload([SystemMessage("Be terse."), HumanMessage("hi")])
        # Prepended, not replaced: the caller's own prompt still gets asked.
        assert payload["system"] == [
            {"type": "text", "text": CLAUDE_CODE_SYSTEM},
            {"type": "text", "text": "Be terse."},
        ]

    def test_it_is_there_when_the_caller_sent_no_system_prompt(self, monkeypatch):
        from langchain_core.messages import HumanMessage

        from yeaboi.agent.llm import CLAUDE_CODE_SYSTEM

        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_AUTH_MODE", "subscription")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc")
        payload = get_llm()._get_request_payload([HumanMessage("hi")])
        assert payload["system"] == [{"type": "text", "text": CLAUDE_CODE_SYSTEM}]

    def test_key_auth_sends_the_callers_system_prompt_alone(self, monkeypatch):
        from langchain_core.messages import HumanMessage, SystemMessage

        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("ANTHROPIC_AUTH_MODE", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        payload = get_llm()._get_request_payload([SystemMessage("Be terse."), HumanMessage("hi")])
        assert payload["system"] == "Be terse."


class TestGetLlmOpenAiWireVendors:
    """The six vendors served by one ChatOpenAI branch (llm_providers.py)."""

    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        from yeaboi.llm_providers import OPENAI_COMPATIBLE

        monkeypatch.delenv("LLM_MODEL", raising=False)
        for spec in OPENAI_COMPATIBLE.values():
            monkeypatch.delenv(spec.key_env, raising=False)
            monkeypatch.delenv(spec.base_url_env, raising=False)

    def _specs(self):
        from yeaboi.llm_providers import OPENAI_COMPATIBLE

        return list(OPENAI_COMPATIBLE.values())

    def test_each_vendor_returns_chat_openai_at_its_own_base_url(self, monkeypatch):
        pytest.importorskip("langchain_openai", reason="langchain-openai not installed")
        from langchain_openai import ChatOpenAI

        for spec in self._specs():
            monkeypatch.setenv("LLM_PROVIDER", spec.key)
            monkeypatch.setenv(spec.key_env, "k" * 40)
            llm = get_llm()
            assert isinstance(llm, ChatOpenAI), spec.key
            assert str(llm.openai_api_base) == spec.base_url, spec.key
            assert llm.model_name == spec.default_model, spec.key

    def test_base_url_env_overrides_the_default_host(self, monkeypatch):
        pytest.importorskip("langchain_openai", reason="langchain-openai not installed")
        spec = self._specs()[0]
        monkeypatch.setenv("LLM_PROVIDER", spec.key)
        monkeypatch.setenv(spec.key_env, "k" * 40)
        monkeypatch.setenv(spec.base_url_env, "https://gateway.internal/v1")
        assert str(get_llm().openai_api_base) == "https://gateway.internal/v1"

    def test_missing_key_names_the_env_var(self, monkeypatch):
        pytest.importorskip("langchain_openai", reason="langchain-openai not installed")
        for spec in self._specs():
            monkeypatch.setenv("LLM_PROVIDER", spec.key)
            with pytest.raises(OSError, match=f"{spec.key_env} is not set"):
                get_llm()

    def test_streams_usage_so_token_counts_are_recorded(self, monkeypatch):
        # Without this opt-in a streamed turn records zero usage, exactly as it
        # would for OpenAI itself.
        pytest.importorskip("langchain_openai", reason="langchain-openai not installed")
        spec = self._specs()[0]
        monkeypatch.setenv("LLM_PROVIDER", spec.key)
        monkeypatch.setenv(spec.key_env, "k" * 40)
        assert get_llm().stream_usage is True

    def test_missing_package_raises_import_error(self, monkeypatch):
        import builtins

        spec = self._specs()[0]
        monkeypatch.setenv("LLM_PROVIDER", spec.key)
        monkeypatch.setenv(spec.key_env, "k" * 40)
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "langchain_openai":
                raise ImportError("No module named 'langchain_openai'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(ImportError, match="langchain-openai is not installed"):
            get_llm()

    def test_defaults_and_fast_models_come_from_the_one_table(self):
        # _PROVIDER_DEFAULTS is merged from the specs, so an id can never be
        # written down twice and drift.
        from yeaboi.agent.llm import _ANALYSIS_FAST_MODELS

        for spec in self._specs():
            assert _PROVIDER_DEFAULTS[spec.key] == spec.default_model
            if spec.fast_model:
                assert _ANALYSIS_FAST_MODELS[spec.key] == spec.fast_model

    def test_unknown_provider_error_names_them(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "not-a-vendor")
        with pytest.raises(ValueError, match="deepseek"):
            get_llm()
