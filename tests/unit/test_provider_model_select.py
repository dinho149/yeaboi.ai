"""Tests for the LLM model-selection step of the provider setup wizard.

Covers:
- ``_verify_model`` live-validation per provider (happy + error paths, mocked).
- The two new screen builders (render, plaintext-not-masked, states).
- Data integrity: each card's default model matches ``_PROVIDER_DEFAULTS``.
- ``run_setup_wizard`` propagates the chosen model to ``LLM_MODEL``.
"""

from io import StringIO

import pytest
from rich.console import Console
from rich.panel import Panel

from yeaboi.agent.llm import _PROVIDER_DEFAULTS
from yeaboi.llm_providers import OPENAI_COMPATIBLE
from yeaboi.provider_verification import (
    INVALID_KEY,
    _validate_key,
    _verify_api_key,
    _verify_confluence,
    _verify_model,
    fetch_available_models,
    pull_ollama_model,
)
from yeaboi.setup_wizard import run_setup_wizard
from yeaboi.ui.provider_select import _existing_model_for, _merge_model_presets
from yeaboi.ui.provider_select._config import _save_progress
from yeaboi.ui.provider_select._constants import (
    _AZDEVOPS_TRACKING_FIELDS,
    _CONFLUENCE_FIELDS,
    _CONFLUENCE_STANDALONE_FIELDS,
    _ISSUE_TRACKING_FIELDS,
    _NOTION_FIELDS,
    _PROVIDER_CARDS,
    _VC_OPTIONS,
    TOKEN_HELP,
)
from yeaboi.ui.provider_select._nav import (
    _LAST_STEP,
    _STEP_LLM,
    StepNav,
    nav_for_key,
)
from yeaboi.ui.provider_select.screens._screens import (
    _STEPS,
    _build_input_screen,
    _build_model_input_screen,
    _build_model_loading_screen,
    _build_model_select_screen,
    _build_progress,
    _build_screen_frame,
    _frame_title_rows,
    _link_target,
    _linkify,
)
from yeaboi.ui.provider_select.screens._screens_vc import (
    _build_hint_text,
    _build_issue_tracking_screen,
    _build_vc_input_screen,
)
from yeaboi.ui.shared._ansi_font import render_shadow_text
from yeaboi.ui.shared._wordmarks import get_shadow_wordmark

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _card(provider_val: str) -> dict:
    return next(c for c in _PROVIDER_CARDS if c["provider_val"] == provider_val)


# The wizard's provider registry now lives on the cards; these tests address it
# by the historical 1-5 numbering, so rebuild that mapping here.
_PROVIDERS = {
    "1": _card("anthropic"),
    "2": _card("openai"),
    "3": _card("google"),
    "4": _card("bedrock"),
    "5": _card("ollama"),
}


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


# Pin BOTH dimensions. The pickers size themselves from ``console.size`` (height
# included); left unpinned, a non-tty StringIO console's height is ambient and can
# collapse to a value too short for the picker, which then renders its body as a
# bare "…" — an order-dependent flake in test-fast. A fixed height makes it
# deterministic (and tall enough to render the whole picker).
def _render(panel: Panel) -> str:
    console = Console(file=StringIO(), width=80, height=40, highlight=False)
    console.print(panel)
    return console.file.getvalue()


def _console() -> Console:
    return Console(file=StringIO(), width=80, height=40)


class _FakeLive:
    """Collects the renderables passed to live.update() (no real terminal)."""

    def __init__(self):
        self.frames = []

    def update(self, renderable):
        self.frames.append(renderable)


class _KeySequence:
    def __init__(self, keys):
        self._keys = list(keys)

    def __call__(self, timeout=None):
        return self._keys.pop(0) if self._keys else ""


# ---------------------------------------------------------------------------
# _verify_model — Anthropic
# ---------------------------------------------------------------------------


class TestVerifyModelAnthropic:
    def test_success(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(200))
        ok, _ = _verify_model(_card("anthropic"), "sk-ant-key", "claude-opus-4-8")
        assert ok is True

    def test_unknown_model_404(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(404))
        ok, msg = _verify_model(_card("anthropic"), "sk-ant-key", "claude-nope")
        assert ok is False
        assert "not" in msg.lower()

    def test_bad_request_surfaces_detail(self, monkeypatch):
        import httpx

        payload = {"error": {"message": "model: claude-nope is not supported"}}
        monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(400, payload))
        ok, msg = _verify_model(_card("anthropic"), "sk-ant-key", "claude-nope")
        assert ok is False
        assert "not supported" in msg

    def test_bad_credentials_401(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: _FakeResponse(401))
        ok, msg = _verify_model(_card("anthropic"), "sk-ant-bad", "claude-opus-4-8")
        assert ok is False
        assert "Invalid" in msg

    def test_connection_error(self, monkeypatch):
        import httpx

        def _boom(*a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(httpx, "post", _boom)
        ok, msg = _verify_model(_card("anthropic"), "sk-ant-key", "claude-opus-4-8")
        assert ok is False
        assert "Connection error" in msg


# ---------------------------------------------------------------------------
# _verify_model — OpenAI / Google
# ---------------------------------------------------------------------------


class TestVerifyModelOpenAI:
    def test_success(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200))
        ok, _ = _verify_model(_card("openai"), "sk-key", "gpt-4o")
        assert ok is True

    def test_unknown_model(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(404))
        ok, msg = _verify_model(_card("openai"), "sk-key", "gpt-nope")
        assert ok is False
        assert "Unknown" in msg


class TestVerifyModelGoogle:
    def test_success(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200))
        ok, _ = _verify_model(_card("google"), "AIzaKey", "gemini-2.5-flash")
        assert ok is True

    def test_unknown_model(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(404))
        ok, msg = _verify_model(_card("google"), "AIzaKey", "gemini-nope")
        assert ok is False
        assert "Unknown" in msg


# ---------------------------------------------------------------------------
# _verify_model — Bedrock (api_key is the region)
# ---------------------------------------------------------------------------


class TestVerifyModelBedrock:
    def test_inference_profile_soft_accepted_without_api_call(self):
        # Leading us./eu./global. ids aren't in list_foundation_models — accept
        # once the region resolves. No boto3 needed, so no mock required.
        ok, _ = _verify_model(_card("bedrock"), "us-east-1", "us.anthropic.claude-sonnet-4-20250514-v1:0")
        assert ok is True

    def _inject_boto3(self, monkeypatch, model_summaries):
        """Inject a fake boto3 module (the real one is an optional extra)."""
        import sys
        import types

        class _Client:
            def list_foundation_models(self, **kw):
                return {"modelSummaries": model_summaries}

        class _Session:
            def __init__(self, *a, **kw):
                pass

            def client(self, *a, **kw):
                return _Client()

        fake = types.ModuleType("boto3")
        fake.Session = _Session
        monkeypatch.setitem(sys.modules, "boto3", fake)
        monkeypatch.setattr("yeaboi.config.get_aws_profile", lambda: None)

    def test_plain_model_id_found_in_region(self, monkeypatch):
        self._inject_boto3(monkeypatch, [{"modelId": "anthropic.claude-3-sonnet"}])
        ok, _ = _verify_model(_card("bedrock"), "us-east-1", "anthropic.claude-3-sonnet")
        assert ok is True

    def test_plain_model_id_missing_in_region(self, monkeypatch):
        self._inject_boto3(monkeypatch, [])
        ok, msg = _verify_model(_card("bedrock"), "us-east-1", "anthropic.claude-3-sonnet")
        assert ok is False
        assert "not available" in msg.lower()


class TestVerifyModelUnknownProvider:
    def test_unknown_provider_returns_false(self):
        ok, msg = _verify_model({"provider_val": "mystery"}, "key", "some-model")
        assert ok is False
        assert "Unknown provider" in msg


# ---------------------------------------------------------------------------
# Ollama — _validate_key / _verify_api_key / _verify_model / fetch_available_models
# (the "api_key" argument carries the local server base URL)
# ---------------------------------------------------------------------------

_OLLAMA_TAGS = {
    "models": [
        {"name": "qwen3:8b", "modified_at": "2026-07-01T10:00:00Z"},
        {"name": "llama3.1:8b", "modified_at": "2026-06-01T10:00:00Z"},
    ]
}


class TestValidateKeyOllama:
    def test_url_accepted(self):
        from yeaboi.provider_verification import _validate_key

        status, hint = _validate_key(_card("ollama"), "http://localhost:11434")
        assert status == "valid_format"
        assert "Ollama" in hint

    def test_non_url_rejected(self):
        from yeaboi.provider_verification import _validate_key

        status, _ = _validate_key(_card("ollama"), "localhost:11434")
        assert status == "bad_prefix"

    def test_empty(self):
        from yeaboi.provider_verification import _validate_key

        assert _validate_key(_card("ollama"), "")[0] == "empty"


@pytest.fixture
def ollama_pkg_installed(monkeypatch):
    """Pretend langchain-ollama is importable.

    CI runs without the optional ``ollama`` extra, so these tests must not
    depend on the real environment: any test exercising the post-install
    verification paths stubs the package check to "installed" here (and
    the missing-package test stubs it to None), making both paths
    deterministic everywhere.
    """
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "langchain_ollama":
            return object()  # any non-None value means "installed"
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)


class TestVerifyApiKeyOllama:
    def test_server_up_with_models(self, monkeypatch, ollama_pkg_installed):
        import httpx

        from yeaboi.provider_verification import _verify_api_key

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, _OLLAMA_TAGS))
        ok, msg = _verify_api_key(_card("ollama"), "http://localhost:11434")
        assert ok is True
        assert "verified" in msg.lower()

    def test_server_up_no_models_still_ok_with_pull_hint(self, monkeypatch, ollama_pkg_installed):
        import httpx

        from yeaboi.provider_verification import _verify_api_key

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, {"models": []}))
        ok, msg = _verify_api_key(_card("ollama"), "http://localhost:11434")
        assert ok is True
        assert "ollama pull" in msg

    def test_server_down_actionable_message(self, monkeypatch, ollama_pkg_installed):
        import httpx

        from yeaboi.provider_verification import _verify_api_key

        def _boom(*a, **kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", _boom)
        ok, msg = _verify_api_key(_card("ollama"), "http://localhost:11434")
        assert ok is False
        assert "ollama serve" in msg


class TestVerifyModelOllama:
    def test_pulled_model_verified(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, _OLLAMA_TAGS))
        ok, _ = _verify_model(_card("ollama"), "http://localhost:11434", "qwen3:8b")
        assert ok is True

    def test_latest_suffix_matches(self, monkeypatch):
        import httpx

        tags = {"models": [{"name": "qwen3:8b:latest"}]}
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, tags))
        ok, _ = _verify_model(_card("ollama"), "http://localhost:11434", "qwen3:8b")
        assert ok is True

    def test_missing_model_pull_hint(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, _OLLAMA_TAGS))
        ok, msg = _verify_model(_card("ollama"), "http://localhost:11434", "qwen3:14b")
        assert ok is False
        assert "ollama pull qwen3:14b" in msg

    def test_server_down(self, monkeypatch):
        import httpx

        def _boom(*a, **kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", _boom)
        ok, msg = _verify_model(_card("ollama"), "http://localhost:11434", "qwen3:8b")
        assert ok is False
        assert "ollama serve" in msg


class TestFetchAvailableModelsOllama:
    def test_lists_newest_first(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, _OLLAMA_TAGS))
        models = fetch_available_models(_card("ollama"), "http://localhost:11434")
        assert models == ["qwen3:8b", "llama3.1:8b"]

    def test_server_down_returns_empty(self, monkeypatch):
        import httpx

        def _boom(*a, **kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", _boom)
        assert fetch_available_models(_card("ollama"), "http://localhost:11434") == []


class TestOllamaPackageCheck:
    """Setup must not finish green when the langchain-ollama extra isn't installed."""

    def test_missing_package_blocks_before_network(self, monkeypatch):
        import importlib.util

        import httpx

        from yeaboi.provider_verification import _verify_api_key

        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

        def _no_network(*a, **kw):
            raise AssertionError("must not touch the network when the package is missing")

        monkeypatch.setattr(httpx, "get", _no_network)
        ok, msg = _verify_api_key(_card("ollama"), "http://localhost:11434")
        assert ok is False
        assert "uv sync --extra ollama" in msg

    def test_installed_package_proceeds_to_server_check(self, monkeypatch, ollama_pkg_installed):
        # With the package present, the check passes through to the normal
        # /api/tags verification.
        import httpx

        from yeaboi.provider_verification import _verify_api_key

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, _OLLAMA_TAGS))
        ok, _ = _verify_api_key(_card("ollama"), "http://localhost:11434")
        assert ok is True


class TestOllamaInstallGuidance:
    def test_not_installed_message_says_how_to_install(self, monkeypatch, ollama_pkg_installed):
        import httpx

        import yeaboi.ollama_control as ollama_control
        from yeaboi.provider_verification import _verify_api_key

        def _boom(*a, **kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", _boom)
        monkeypatch.setattr(ollama_control, "is_ollama_installed", lambda: False)
        _, msg = _verify_api_key(_card("ollama"), "http://localhost:11434")
        assert "https://ollama.com" in msg
        assert "ollama serve" in msg
        _, msg = _verify_model(_card("ollama"), "http://localhost:11434", "qwen3:8b")
        assert "https://ollama.com" in msg

    def test_installed_but_down_message_says_start_not_install(self, monkeypatch, ollama_pkg_installed):
        import httpx

        import yeaboi.ollama_control as ollama_control
        from yeaboi.provider_verification import _verify_api_key

        def _boom(*a, **kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "get", _boom)
        monkeypatch.setattr(ollama_control, "is_ollama_installed", lambda: True)
        _, msg = _verify_api_key(_card("ollama"), "http://localhost:11434")
        assert "not running" in msg
        assert "ollama serve" in msg
        assert "https://ollama.com" not in msg


class _FakePullStream:
    """Context-manager stand-in for httpx.stream() yielding /api/pull JSON lines."""

    def __init__(self, status_code: int, lines: list[str]):
        self.status_code = status_code
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self):
        yield from self._lines


class TestPullOllamaModel:
    def test_success_streams_progress(self, monkeypatch):
        import httpx

        lines = [
            '{"status": "pulling manifest"}',
            '{"status": "downloading", "total": 100, "completed": 50}',
            '{"status": "success"}',
        ]
        monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakePullStream(200, lines))
        seen: list[tuple[str, float | None]] = []
        ok, msg = pull_ollama_model("http://localhost:11434", "qwen3:8b", lambda s, f: seen.append((s, f)))
        assert ok is True
        assert msg == "Model downloaded"
        assert ("downloading", 0.5) in seen

    def test_server_error_event(self, monkeypatch):
        import httpx

        lines = ['{"error": "pull model manifest: file does not exist"}']
        monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakePullStream(200, lines))
        ok, msg = pull_ollama_model("http://localhost:11434", "nope:1b", lambda s, f: None)
        assert ok is False
        assert "does not exist" in msg

    def test_non_200_response(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakePullStream(500, []))
        ok, msg = pull_ollama_model("http://localhost:11434", "qwen3:8b", lambda s, f: None)
        assert ok is False
        assert "500" in msg

    def test_cancel_event_aborts(self, monkeypatch):
        import threading

        import httpx

        lines = ['{"status": "downloading", "total": 100, "completed": 1}'] * 5
        monkeypatch.setattr(httpx, "stream", lambda *a, **kw: _FakePullStream(200, lines))
        cancel = threading.Event()
        cancel.set()
        ok, msg = pull_ollama_model("http://localhost:11434", "qwen3:8b", lambda s, f: None, cancel_event=cancel)
        assert ok is False
        assert "cancel" in msg.lower()

    def test_network_error_never_raises(self, monkeypatch):
        import httpx

        def _boom(*a, **kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "stream", _boom)
        ok, msg = pull_ollama_model("http://localhost:11434", "qwen3:8b", lambda s, f: None)
        assert ok is False
        assert "failed" in msg.lower()


class TestExistingModelFor:
    """A saved LLM_MODEL is only offered for the provider it was saved with."""

    def test_same_provider_carries_over(self):
        cfg = {"LLM_PROVIDER": "ollama", "LLM_MODEL": "qwen3:14b"}
        assert _existing_model_for(cfg, "ollama") == "qwen3:14b"

    def test_switched_provider_drops_stale_model(self):
        cfg = {"LLM_PROVIDER": "anthropic", "LLM_MODEL": "claude-sonnet-4-6"}
        assert _existing_model_for(cfg, "ollama") == ""

    def test_no_config(self):
        assert _existing_model_for(None, "ollama") == ""
        assert _existing_model_for({}, "anthropic") == ""


class TestMergeModelPresets:
    """Live-discovered models merge with curated recommendations (all providers)."""

    CURATED = ["qwen3:8b", "qwen3:14b", "qwen2.5:14b", "llama3.1:8b"]

    def test_ollama_one_installed_keeps_recommendations(self):
        # The reported case: only qwen3:8b pulled — the others must still show,
        # tagged as not-pulled, instead of vanishing.
        presets, unpulled = _merge_model_presets(["qwen3:8b"], self.CURATED, cap=8, is_ollama=True)
        assert presets == ["qwen3:8b", "qwen3:14b", "qwen2.5:14b", "llama3.1:8b"]
        assert unpulled == {"qwen3:14b", "qwen2.5:14b", "llama3.1:8b"}
        assert "qwen3:8b" not in unpulled  # installed → no "not pulled" tag

    def test_ollama_nothing_installed_all_unpulled(self):
        presets, unpulled = _merge_model_presets([], self.CURATED, cap=8, is_ollama=True)
        assert presets == self.CURATED
        assert unpulled == set(self.CURATED)

    def test_ollama_extra_installed_model_listed_first(self):
        # A model the user pulled that isn't a curated preset still appears.
        presets, unpulled = _merge_model_presets(["mistral:7b", "qwen3:8b"], self.CURATED, cap=8, is_ollama=True)
        assert presets[:2] == ["mistral:7b", "qwen3:8b"]
        assert "mistral:7b" not in unpulled
        assert "qwen3:8b" not in unpulled
        assert unpulled == {"qwen3:14b", "qwen2.5:14b", "llama3.1:8b"}

    def test_cloud_merges_without_unpulled_flag(self):
        curated = ["claude-sonnet-4-6", "claude-opus-4-8"]
        presets, unpulled = _merge_model_presets(["claude-sonnet-4-6"], curated, cap=8, is_ollama=False)
        assert presets == ["claude-sonnet-4-6", "claude-opus-4-8"]  # recommendation retained
        assert unpulled == set()  # cloud has no "not pulled" concept

    def test_cap_limits_discovered_but_keeps_curated(self):
        discovered = [f"m{i}" for i in range(10)]
        curated = ["reco-a", "reco-b"]
        presets, _ = _merge_model_presets(discovered, curated, cap=3, is_ollama=False)
        assert presets == ["m0", "m1", "m2", "reco-a", "reco-b"]

    def test_no_duplicate_when_discovered_matches_curated(self):
        presets, _ = _merge_model_presets(["qwen3:8b", "qwen3:14b"], self.CURATED, cap=8, is_ollama=True)
        assert presets == ["qwen3:8b", "qwen3:14b", "qwen2.5:14b", "llama3.1:8b"]
        assert presets.count("qwen3:8b") == 1


# ---------------------------------------------------------------------------
# Screen builders
# ---------------------------------------------------------------------------


class TestModelSelectScreen:
    def test_returns_panel(self):
        panel = _build_model_select_screen(_card("anthropic"), ["claude-opus-4-8", "Custom…"], 0)
        assert isinstance(panel, Panel)

    def test_single_entry(self):
        panel = _build_model_select_screen(_card("openai"), ["Custom…"], 0)
        assert isinstance(panel, Panel)

    def test_long_model_id_renders(self):
        entries = ["us.anthropic.claude-sonnet-4-20250514-v1:0", "Custom…"]
        out = _render(_build_model_select_screen(_card("bedrock"), entries, 0))
        # The id is long enough it may wrap/crop; a distinctive fragment survives.
        assert "claude-sonnet-4" in out

    def test_error_line_shown(self):
        out = _render(_build_model_select_screen(_card("openai"), ["gpt-4o", "Custom…"], 0, error="Unknown model"))
        assert "Unknown model" in out

    def test_renders_as_rounded_cards(self):
        # Each model is a rounded box-drawing card (matches app cards/buttons),
        # not plain text — the corner glyphs must be present.
        out = _render(_build_model_select_screen(_card("anthropic"), ["claude-opus-4-8", "Custom…"], 0))
        assert "╭" in out and "╰" in out
        assert "claude-opus-4-8" in out

    def test_long_list_windows_with_more_hint(self):
        # More models than fit a short screen → window + a "N more" affordance,
        # and the render must not exceed the frame height.
        entries = [f"model-{i}" for i in range(12)] + ["Custom…"]
        panel = _build_model_select_screen(_card("openai"), entries, 6, width=80, height=24)
        out = _render(panel)
        assert "more" in out
        assert out.count("\n") <= 24


class TestModelInputScreen:
    def test_returns_panel(self):
        assert isinstance(_build_model_input_screen(_card("anthropic"), ""), Panel)

    def test_value_is_plaintext_not_masked(self):
        # Model ids are not secrets — the typed value must render as-is, no bullets.
        out = _render(_build_model_input_screen(_card("anthropic"), "claude-opus-4-8"))
        assert "claude-opus-4-8" in out
        assert "•" not in out

    def test_verified_state(self):
        assert isinstance(_build_model_input_screen(_card("openai"), "gpt-4o", verified=True), Panel)

    def test_error_state(self):
        out = _render(_build_model_input_screen(_card("openai"), "gpt-nope", verified=False, error="Unknown model"))
        assert "Unknown model" in out

    def test_verifying_state(self):
        panel = _build_model_input_screen(
            _card("google"), "gemini-2.5-flash", verifying=True, border_override="rgb(1,1,1)"
        )
        assert isinstance(panel, Panel)


class TestModelLoadingScreen:
    def test_returns_panel_with_discovery_text(self):
        # Shown (animated) while live discovery runs on a background thread so the
        # Live loop never freezes on a blocking HTTP call.
        out = _render(_build_model_loading_screen(_card("anthropic"), 0.3, width=80, height=24))
        assert "Discovering available models" in out

    def test_title_is_provider_name(self):
        # The per-screen title carries the provider identity (tall ANSI-Shadow).
        out = _render(_build_model_loading_screen(_card("anthropic"), 0.0))
        assert "█" in out


class TestFrameTitleFont:
    """The frame title renders in the tall ANSI-Shadow font, with a compact fallback."""

    def _frame(self, title, width):
        from rich.text import Text

        return _build_screen_frame(
            subtitle="x",
            step=0,
            body_items=[Text("body")],
            body_height=1,
            width=width,
            height=30,
            title_text=title,
        )

    def test_tall_wordmark_at_wide_width(self):
        # A baked word (provider name) renders as the tall blocky wordmark.
        out = _render(self._frame("Anthropic", 100))
        assert "█" in out

    def test_compact_fallback_at_narrow_width(self):
        # Too narrow for the ~70-col wordmark → falls back to the compact 2-line
        # block font (still block chars, but the wide wordmark row can't fit).
        panel = self._frame("Anthropic", 40)
        console = Console(file=StringIO(), width=40, highlight=False)
        console.print(panel)
        out = console.file.getvalue()
        # Compact font uses ▀/▄ half-blocks the tall wordmark never emits.
        assert "▀" in out or "▄" in out

    def test_default_title_is_setup_not_setup_wizard(self):
        out = _render(self._frame("", 100))
        # "SETUP" wordmark present; the old "Setup Wizard" compact string is gone.
        assert "█" in out

    @pytest.mark.parametrize("card", _PROVIDER_CARDS, ids=[c["provider_val"] for c in _PROVIDER_CARDS])
    def test_every_provider_gets_the_tall_face(self, card):
        # Only four names are baked in _wordmarks; the title is built per-word so
        # the other seven don't arrive in the small font on the first screen of
        # setup. Compared against the face itself rather than sniffed out of the
        # render — Qwen's Q and W use the same half-blocks the compact font does.
        assert _frame_title_rows(card["name"], 94) == render_shadow_text(card["name"])


class TestIssueTrackingHint:
    """The multi-field form (Jira/AzDO/Notion) shows a keyboard hint, hidden on verify."""

    def test_hint_shown_by_default(self):
        out = _render(
            _build_issue_tracking_screen(0, {0: "", 1: ""}, width=100, height=30, title_text="Notion", subtitle="Docs")
        )
        assert "Enter to verify" in out
        assert "Esc back" in out

    def test_hint_hidden_while_verifying(self):
        # border_overrides drives the verify pulse / success flash — hide the hint.
        out = _render(
            _build_issue_tracking_screen(
                0, {0: "tok"}, width=100, height=30, title_text="Notion", border_overrides={0: "rgb(1,1,1)"}
            )
        )
        assert "Enter to verify" not in out

    def test_docs_step_renders_docs_chip(self):
        # The shared form is reused for the Docs step (Notion/Confluence); step=2
        # must surface the "Docs" progress chip in the footer.
        out = _render(
            _build_issue_tracking_screen(
                0, {0: ""}, width=100, height=30, title_text="Confluence", subtitle="Docs", step=2
            )
        )
        assert "Docs" in out

    def test_active_field_shows_where_to_get_hint(self):
        # The focused field surfaces its "where to get it" hint, like the LLM and
        # GitHub steps. Field 2 = JIRA_API_TOKEN → the token-creation URL.
        out = _render(
            _build_issue_tracking_screen(2, {}, width=100, height=30, fields=_ISSUE_TRACKING_FIELDS, title_text="Jira")
        )
        assert "id.atlassian.com" in out

    def test_where_to_get_hint_hidden_while_verifying(self):
        # The verify pulse / success flash (border_overrides) should read cleanly —
        # the where-to-get hint is suppressed just like the keyboard hint.
        out = _render(
            _build_issue_tracking_screen(
                2,
                {},
                width=100,
                height=30,
                fields=_ISSUE_TRACKING_FIELDS,
                title_text="Jira",
                border_overrides={2: "rgb(1,1,1)"},
            )
        )
        assert "id.atlassian.com" not in out

    def test_error_replaces_where_to_get_hint(self):
        # A validation error on the active field takes the slot instead of the hint.
        out = _render(
            _build_issue_tracking_screen(
                2,
                {},
                width=100,
                height=30,
                fields=_ISSUE_TRACKING_FIELDS,
                title_text="Jira",
                errors={2: "Invalid token"},
            )
        )
        assert "Invalid token" in out
        assert "id.atlassian.com" not in out


class TestConnectionFieldHints:
    """Every connection field carries a non-empty 'where to get it' hint."""

    @pytest.mark.parametrize(
        "fields",
        [_ISSUE_TRACKING_FIELDS, _AZDEVOPS_TRACKING_FIELDS, _NOTION_FIELDS, _CONFLUENCE_FIELDS],
    )
    def test_all_fields_have_hints(self, fields):
        for field in fields:
            assert field.get("hint"), f"{field['env_var']} is missing a where-to-get-it hint"


class TestLinkify:
    """`_linkify` / `_link_target` — clickable-URL rendering helpers."""

    def test_link_target_keeps_full_url(self):
        assert _link_target("https://github.com/settings/tokens") == "https://github.com/settings/tokens"

    def test_link_target_prefixes_bare_domain(self):
        assert _link_target("id.atlassian.com/x") == "https://id.atlassian.com/x"

    def test_linkify_wraps_url_span(self):
        text = _linkify("Get yours at: https://example.com/x", lead_style="dim", url_style="blue")
        assert text.plain == "Get yours at: https://example.com/x"
        assert any("link https://example.com/x" in str(sp.style) for sp in text.spans)

    def test_linkify_no_url_renders_plainly(self):
        text = _linkify("no link here at all", lead_style="dim", url_style="blue")
        assert text.plain == "no link here at all"
        assert not any("link " in str(sp.style) for sp in text.spans)


class TestTokenHelp:
    """Credential-token screens surface the required scope + a clickable link."""

    def test_hint_url_is_clickable(self):
        # The where-to-get-it URL is rendered as an OSC-8 hyperlink (Rich `link`
        # style) so it's click/cmd-clickable in modern terminals.
        text = _build_hint_text("Create at: id.atlassian.com/manage-profile/security/api-tokens")
        assert any("link https://id.atlassian.com" in str(sp.style) for sp in text.spans)

    def test_scope_line_shown_for_token_field(self):
        # Field 2 of the Azure DevOps form is AZURE_DEVOPS_TOKEN → its scope shows.
        out = _render(
            _build_issue_tracking_screen(
                2, {}, width=100, height=30, fields=_AZDEVOPS_TRACKING_FIELDS, title_text="Azure"
            )
        )
        assert "Work Items" in out

    def test_no_scope_line_for_non_token_field(self):
        # Field 0 is AZURE_DEVOPS_ORG_URL (not a credential) → no scope line.
        out = _render(
            _build_issue_tracking_screen(
                0, {}, width=100, height=30, fields=_AZDEVOPS_TRACKING_FIELDS, title_text="Azure"
            )
        )
        assert "Scope:" not in out

    def test_github_pat_screen_shows_scope(self):
        # _VC_OPTIONS[0] is GitHub → the PAT screen shows the required scope,
        # including the SSO-authorization step an org-owned token also needs.
        out = _render(_build_vc_input_screen(_VC_OPTIONS[0], "ghp_x", width=100, height=30))
        assert "'repo' scope" in out
        assert "Configure SSO" in out

    def test_llm_key_screen_has_no_scope(self):
        # Anthropic's key has no granular scopes and no endpoint to move — nothing
        # to say, so the line must not appear.
        out = _render(_build_input_screen(_PROVIDER_CARDS[0], "sk-ant-x", width=100, height=30))
        assert "Scope:" not in out

    @pytest.mark.parametrize("provider_val", sorted(OPENAI_COMPATIBLE))
    def test_openai_wire_key_screen_shows_its_note(self, provider_val):
        # These do have something to say: whether a default key suffices, and which
        # env var moves the endpoint.
        card = next(c for c in _PROVIDER_CARDS if c["provider_val"] == provider_val)
        out = _render(_build_input_screen(card, "x" * 40, width=100, height=30))
        assert "Scope:" in out

    @pytest.mark.parametrize("provider_val", sorted(OPENAI_COMPATIBLE))
    def test_scope_note_is_not_truncated_at_80_columns(self, provider_val):
        # The note sits on one centred line; too long and Rich clips it with an
        # ellipsis, taking the env var name with it. 80 is four columns under the
        # app's own floor (_MIN_WIDTH) on purpose — headroom, not the real frame.
        card = next(c for c in _PROVIDER_CARDS if c["provider_val"] == provider_val)
        out = _render(_build_input_screen(card, "x" * 40, width=80, height=24))
        assert "…" not in out

    def test_registry_entries_complete(self):
        for env_var, entry in TOKEN_HELP.items():
            assert entry.get("url", "").startswith("http"), f"{env_var} url"
            assert entry.get("scope"), f"{env_var} scope"

    def test_masked_token_fields_are_covered(self):
        # Every API-token credential in the connection forms is covered by
        # TOKEN_HELP so the user always sees what access to grant. (Space keys /
        # page ids are masked=False and not credentials — they stay uncovered.)
        credential_env = {"JIRA_API_TOKEN", "AZURE_DEVOPS_TOKEN", "NOTION_TOKEN", "CONFLUENCE_API_TOKEN"}
        for fields in (
            _ISSUE_TRACKING_FIELDS,
            _AZDEVOPS_TRACKING_FIELDS,
            _NOTION_FIELDS,
            _CONFLUENCE_STANDALONE_FIELDS,
        ):
            for field in fields:
                if field["env_var"] in credential_env:
                    assert field["env_var"] in TOKEN_HELP, f"{field['env_var']} missing scope help"


class TestHintStyling:
    """`_build_hint_text` renders the info glyph + emphasized URL treatment."""

    def test_glyph_prefixes_the_line(self):
        t = _build_hint_text("Create at: id.atlassian.com/x/api-tokens")
        assert t.plain.startswith("ⓘ")

    def test_url_is_emphasized_and_underlined(self):
        t = _build_hint_text("Create at: id.atlassian.com/x/api-tokens")
        # The URL token keeps its plain text intact...
        assert "id.atlassian.com/x/api-tokens" in t.plain
        # ...and is rendered underlined (emphasis) via a dedicated span.
        assert any("underline" in str(s.style) for s in t.spans)

    def test_https_url_emphasized(self):
        t = _build_hint_text("Your org — https://dev.azure.com/<your-org>")
        assert any("underline" in str(s.style) and "dev.azure.com" in t.plain[s.start : s.end] for s in t.spans)

    def test_trailing_prose_not_swallowed_by_url(self):
        # "notion.so/my-integrations, then share …" — the comma bounds the URL so
        # the trailing instruction stays in the muted (non-underlined) style.
        t = _build_hint_text("Create at: notion.so/my-integrations, then share your pages with it")
        underlined = [t.plain[s.start : s.end] for s in t.spans if "underline" in str(s.style)]
        assert underlined == ["notion.so/my-integrations"]

    def test_no_url_renders_without_underline(self):
        t = _build_hint_text("The email you sign in to Atlassian with")
        assert "ⓘ" in t.plain
        assert "Atlassian" in t.plain
        assert not any("underline" in str(s.style) for s in t.spans)


class TestIssueTrackingPickerNav:
    """The Issue Tracking picker also honours ←/→/F section navigation."""

    _PROVIDER = {
        "full_name": "Anthropic (Claude)",
        "name": "Anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "provider_val": "anthropic",
        "prefix": "sk-ant-",
        "instructions": "x",
    }

    def _patch_tty(self, monkeypatch):
        import select as _select

        from yeaboi.ui.provider_select import _phase_issue_tracking as it

        class _FakeStdin:
            def fileno(self):
                return 0

            def read(self, n):
                return ""

        monkeypatch.setattr(it.sys, "stdin", _FakeStdin())
        monkeypatch.setattr(it.termios, "tcgetattr", lambda fd: None)
        monkeypatch.setattr(it.termios, "tcsetattr", lambda fd, when, attrs: None)
        monkeypatch.setattr(it.tty, "setcbreak", lambda fd: None)
        monkeypatch.setattr(_select, "select", lambda r, w, x, t: ([], [], []))

    def _run(self, monkeypatch, keys):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_issue_tracking import _run_issue_tracking

        return _run_issue_tracking(
            _console(),
            _KeySequence(keys),
            None,
            self._PROVIDER,
            "sk-ant-key",
            {"env_var": "", "name": ""},
            "",
            live=_FakeLive(),
            llm_model="claude-opus-4-8",
        )

    def test_right_arrow_navigates_to_docs(self, monkeypatch):
        # Issue Tracking is step 1 → → jumps to Docs (step 2).
        assert self._run(monkeypatch, ["right"]) == StepNav(target=2)

    def test_left_arrow_navigates_to_llm(self, monkeypatch):
        assert self._run(monkeypatch, ["left"]) == StepNav(target=0)

    def test_f_finishes(self, monkeypatch):
        assert self._run(monkeypatch, ["f"]) == StepNav(finish=True)

    def test_esc_still_returns_none(self, monkeypatch):
        assert self._run(monkeypatch, ["esc"]) is None


class TestStepNav:
    """`nav_for_key` maps ←/→/F to section jumps, clamped to the chip range."""

    def test_f_finishes_from_any_step(self):
        for step in range(4):
            assert nav_for_key("f", step) == StepNav(finish=True)
        # Upper-case F (some terminals) also finishes.
        assert nav_for_key("F", 2) == StepNav(finish=True)

    def test_left_goes_to_previous_chip(self):
        assert nav_for_key("left", 1) == StepNav(target=0)
        assert nav_for_key("left", 3) == StepNav(target=2)

    def test_right_goes_to_next_chip(self):
        assert nav_for_key("right", 0) == StepNav(target=1)
        assert nav_for_key("right", 2) == StepNav(target=3)

    def test_arrows_clamp_at_boundaries(self):
        # ← at the first chip and → at the last chip do nothing (fall through).
        assert nav_for_key("left", _STEP_LLM) is None
        assert nav_for_key("right", _LAST_STEP) is None

    def test_non_nav_keys_fall_through(self):
        for key in ("up", "down", "enter", "esc", "a", "tab", "backspace"):
            assert nav_for_key(key, 2) is None

    def test_stepnav_equality_and_defaults(self):
        assert StepNav(target=2) == StepNav(target=2)
        assert StepNav(target=2) != StepNav(target=3)
        assert StepNav(finish=True) != StepNav(target=None)
        assert StepNav(target=1).finish is False


class TestNotionPicker:
    """The Notion step offers an explicit Notion / Skip picker (like Issue Tracking)."""

    def _patch_tty(self, monkeypatch):
        import select as _select

        from yeaboi.ui.provider_select import _phase_notion as pn

        class _FakeStdin:
            def fileno(self):
                return 0

            def read(self, n):
                return ""

        monkeypatch.setattr(pn.sys, "stdin", _FakeStdin())
        monkeypatch.setattr(pn.termios, "tcgetattr", lambda fd: None)
        monkeypatch.setattr(pn.termios, "tcsetattr", lambda fd, when, attrs: None)
        monkeypatch.setattr(pn.tty, "setcbreak", lambda fd: None)
        monkeypatch.setattr(_select, "select", lambda r, w, x, t: ([], [], []))

    def test_skip_returns_empty_dict(self, monkeypatch):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_notion import _run_notion

        live = _FakeLive()
        # ↓ moves to "Skip", Enter selects it.
        result = _run_notion(_console(), _KeySequence(["down", "enter"]), None, live)
        assert result == {}
        # The picker screen rendered (tall Notion title + a "choose" affordance).
        rendered = "".join(_render(f) for f in live.frames)
        assert "█" in rendered and "choose" in rendered

    def test_esc_on_picker_returns_none(self, monkeypatch):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_notion import _run_notion

        assert _run_notion(_console(), _KeySequence(["esc"]), None, _FakeLive()) is None

    def test_right_arrow_navigates_to_version_control(self, monkeypatch):
        # Docs is step 2 → → jumps to Version Control (step 3).
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_notion import _run_notion

        result = _run_notion(_console(), _KeySequence(["right"]), None, _FakeLive())
        assert result == StepNav(target=3)

    def test_left_arrow_navigates_to_issue_tracking(self, monkeypatch):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_notion import _run_notion

        result = _run_notion(_console(), _KeySequence(["left"]), None, _FakeLive())
        assert result == StepNav(target=1)

    def test_f_finishes(self, monkeypatch):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_notion import _run_notion

        result = _run_notion(_console(), _KeySequence(["f"]), None, _FakeLive())
        assert result == StepNav(finish=True)


def _active_progress_label(step: int) -> str | None:
    """Return the label of the chip _build_progress marks active for *step*.

    The active chip is styled ``bold white on <accent>`` (see _build_progress);
    the done chips use a green bg and future chips a dim grey bg, so the accent
    background uniquely identifies the active step. This lets the test assert the
    full step→chip mapping without depending on exact colour constants.
    """
    text = _build_progress(step)
    for span in text.spans:
        if "on rgb(70,100,180)" in str(span.style):
            return text.plain[span.start : span.end].strip()
    return None


class TestProgressSteps:
    """The setup wizard's progress bar has a dedicated 'Docs' chip, correctly indexed."""

    def test_steps_order(self):
        assert _STEPS == ["LLM Provider", "Issue Tracking", "Docs", "Version Control"]

    @pytest.mark.parametrize(
        "step,label",
        [
            (0, "LLM Provider"),
            (1, "Issue Tracking"),
            (2, "Docs"),
            (3, "Version Control"),
        ],
    )
    def test_active_chip_matches_step(self, step, label):
        # Regression guard for the step re-indexing: the Notion/Confluence (Docs)
        # step must highlight "Docs", not "Version Control" (the original bug).
        assert _active_progress_label(step) == label


class TestConfluenceVerify:
    """_verify_confluence checks a space is reachable with the shared Jira Atlassian auth."""

    def test_success(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200))
        ok, msg = _verify_confluence("https://org.atlassian.net", "u@x.com", "tok", "SPACE")
        assert ok is True
        assert "verified" in msg.lower()

    def test_bad_credentials(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(401))
        ok, msg = _verify_confluence("https://org.atlassian.net", "u@x.com", "bad", "SPACE")
        assert ok is False
        assert "credentials" in msg.lower()

    def test_space_not_found(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(404))
        ok, msg = _verify_confluence("https://org.atlassian.net", "u@x.com", "tok", "NOPE")
        assert ok is False
        assert "NOPE" in msg

    def test_connection_error(self, monkeypatch):
        import httpx

        def _boom(*a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(httpx, "get", _boom)
        ok, msg = _verify_confluence("https://org.atlassian.net", "u@x.com", "tok", "SPACE")
        assert ok is False
        assert "Connection error" in msg


class TestConfluencePicker:
    """The Docs step's Confluence sub-step offers an explicit Confluence / Skip picker."""

    _JIRA = {
        "JIRA_BASE_URL": "https://org.atlassian.net",
        "JIRA_EMAIL": "u@x.com",
        "JIRA_API_TOKEN": "tok",
    }

    def _patch_tty(self, monkeypatch):
        import select as _select

        # _drain (stdin flush) is shared from _phase_notion, so patch that module.
        from yeaboi.ui.provider_select import _phase_notion as pn

        class _FakeStdin:
            def fileno(self):
                return 0

            def read(self, n):
                return ""

        monkeypatch.setattr(pn.sys, "stdin", _FakeStdin())
        monkeypatch.setattr(pn.termios, "tcgetattr", lambda fd: None)
        monkeypatch.setattr(pn.termios, "tcsetattr", lambda fd, when, attrs: None)
        monkeypatch.setattr(pn.tty, "setcbreak", lambda fd: None)
        monkeypatch.setattr(_select, "select", lambda r, w, x, t: ([], [], []))

    def test_skip_returns_empty_dict(self, monkeypatch):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_confluence import _run_confluence

        live = _FakeLive()
        # ↓ moves to "Skip", Enter selects it.
        result = _run_confluence(_console(), _KeySequence(["down", "enter"]), None, live, jira_creds=self._JIRA)
        assert result == {}
        rendered = "".join(_render(f) for f in live.frames)
        assert "█" in rendered and "choose" in rendered

    def test_esc_on_picker_returns_none(self, monkeypatch):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_confluence import _run_confluence

        result = _run_confluence(_console(), _KeySequence(["esc"]), None, _FakeLive(), jira_creds=self._JIRA)
        assert result is None

    def test_right_arrow_navigates_to_version_control(self, monkeypatch):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_confluence import _run_confluence

        result = _run_confluence(_console(), _KeySequence(["right"]), None, _FakeLive(), jira_creds=self._JIRA)
        assert result == StepNav(target=3)

    def test_f_finishes(self, monkeypatch):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_confluence import _run_confluence

        result = _run_confluence(_console(), _KeySequence(["f"]), None, _FakeLive(), jira_creds=self._JIRA)
        assert result == StepNav(finish=True)


class TestDocsPicker:
    """The Docs step is one unified Notion / Confluence / Skip picker (like Issue Tracking)."""

    _JIRA = {
        "JIRA_BASE_URL": "https://org.atlassian.net",
        "JIRA_EMAIL": "u@x.com",
        "JIRA_API_TOKEN": "tok",
    }

    def _patch_tty(self, monkeypatch):
        import select as _select

        # _run_docs and both forms share _drain from _phase_notion.
        from yeaboi.ui.provider_select import _phase_notion as pn

        class _FakeStdin:
            def fileno(self):
                return 0

            def read(self, n):
                return ""

        monkeypatch.setattr(pn.sys, "stdin", _FakeStdin())
        monkeypatch.setattr(pn.termios, "tcgetattr", lambda fd: None)
        monkeypatch.setattr(pn.termios, "tcsetattr", lambda fd, when, attrs: None)
        monkeypatch.setattr(pn.tty, "setcbreak", lambda fd: None)
        monkeypatch.setattr(_select, "select", lambda r, w, x, t: ([], [], []))

    def _run(self, monkeypatch, keys, *, jira_creds=None, existing=None):
        self._patch_tty(monkeypatch)
        from yeaboi.ui.provider_select._phase_docs import _run_docs

        live = _FakeLive()
        result = _run_docs(_console(), _KeySequence(keys), existing, live, jira_creds=jira_creds)
        return result, live

    def test_picker_renders_three_cards(self, monkeypatch):
        # ↓↓ then Enter picks Skip (the 3rd card), proving there are three options.
        result, live = self._run(monkeypatch, ["down", "down", "enter"])
        assert result == {"notion": {}, "confluence": {}}
        # Card names render as ASCII art (block chars), with the picker affordance line.
        rendered = "".join(_render(f) for f in live.frames)
        assert "█" in rendered and "choose" in rendered

    def test_has_jira_creds_boundary(self):
        from yeaboi.ui.provider_select._phase_confluence import _has_jira_creds

        assert _has_jira_creds(self._JIRA) is True
        assert _has_jira_creds(None) is False
        assert _has_jira_creds({}) is False
        # A partial set (missing token) is not enough to reuse.
        assert _has_jira_creds({"JIRA_BASE_URL": "x", "JIRA_EMAIL": "y"}) is False

    def test_esc_returns_none(self, monkeypatch):
        result, _ = self._run(monkeypatch, ["esc"])
        assert result is None

    def test_right_arrow_navigates_to_version_control(self, monkeypatch):
        result, _ = self._run(monkeypatch, ["right"])
        assert result == StepNav(target=3)

    def test_left_arrow_navigates_to_issue_tracking(self, monkeypatch):
        result, _ = self._run(monkeypatch, ["left"])
        assert result == StepNav(target=1)

    def test_f_finishes(self, monkeypatch):
        result, _ = self._run(monkeypatch, ["f"])
        assert result == StepNav(finish=True)

    def test_notion_empty_token_records_empty(self, monkeypatch):
        # Enter selects Notion (pick 0); Enter again submits an empty token → skip.
        result, _ = self._run(monkeypatch, ["enter", "enter"])
        assert result == {"notion": {}, "confluence": {}}

    def test_confluence_reuse_empty_space_records_empty(self, monkeypatch):
        # ↓ + Enter selects Confluence; with Jira creds present, an empty space key skips.
        result, live = self._run(monkeypatch, ["down", "enter", "enter"], jira_creds=self._JIRA)
        assert result == {"notion": {}, "confluence": {}}
        # Reuse mode shows only the space-key field (not a full Atlassian login).
        rendered = "".join(_render(f) for f in live.frames)
        assert "Space Key" in rendered and "Base URL" not in rendered

    def test_confluence_standalone_nothing_entered_skips(self, monkeypatch):
        # No Jira creds → standalone form; submitting an empty form skips (optional).
        result, _ = self._run(monkeypatch, ["down", "enter", "enter"], jira_creds=None)
        assert result == {"notion": {}, "confluence": {}}

    def test_confluence_standalone_happy_path(self, monkeypatch):
        import httpx

        from yeaboi.ui.provider_select import _phase_confluence as pc

        # Pre-seed the form via existing config, mock verify + persistence + sleeps.
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200))
        monkeypatch.setattr(pc, "_save_progress", lambda data: None)
        monkeypatch.setattr(pc.time, "sleep", lambda *a, **kw: None)
        existing = {
            "CONFLUENCE_BASE_URL": "https://standalone.atlassian.net",
            "CONFLUENCE_EMAIL": "solo@x.com",
            "CONFLUENCE_API_TOKEN": "solo-tok",
            "CONFLUENCE_SPACE_KEY": "SOLO",
        }
        # ↓ + Enter selects Confluence; Enter submits the pre-filled login.
        result, _ = self._run(monkeypatch, ["down", "enter", "enter"], jira_creds=None, existing=existing)
        assert result["notion"] == {}
        assert result["confluence"] == existing


class TestShadowWordmarks:
    @pytest.mark.parametrize(
        "word",
        ["ANTHROPIC", "OPENAI", "GEMINI", "BEDROCK", "GITHUB", "NOTION", "JIRA", "CONFLUENCE", "DOCS", "SETUP"],
    )
    def test_baked_wordmark_rows_equal_width(self, word):
        rows = get_shadow_wordmark(word)
        assert rows is not None and len(rows) == 6
        assert len({len(r) for r in rows}) == 1  # all rows same width
        assert any("█" in r for r in rows)


# ---------------------------------------------------------------------------
# Data integrity — preset defaults must match _PROVIDER_DEFAULTS
# ---------------------------------------------------------------------------


class TestModelDataIntegrity:
    @pytest.mark.parametrize("card", _PROVIDER_CARDS, ids=lambda c: c["provider_val"])
    def test_default_matches_provider_defaults(self, card):
        default = card["models"]["default"]
        assert default == _PROVIDER_DEFAULTS[card["provider_val"]]
        # The default is always offered as a preset the user can pick.
        assert default in card["models"]["presets"]


# ---------------------------------------------------------------------------
# Wizard propagation — LLM_MODEL is written when select_provider returns it
# ---------------------------------------------------------------------------


class TestWizardModelPropagation:
    def _patch_config_file(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.setup_wizard.get_config_file", lambda: config_file)
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        return config_file

    def test_llm_model_written_to_env(self, monkeypatch, tmp_path):
        config_file = self._patch_config_file(monkeypatch, tmp_path)
        result = dict(_PROVIDERS["1"])
        result["api_key"] = "sk-ant-key"
        result["llm_model"] = "claude-opus-4-8"
        monkeypatch.setattr("yeaboi.setup_wizard.select_provider", lambda *a, **kw: result)
        console = Console(file=StringIO(), highlight=False)
        run_setup_wizard(console)
        content = config_file.read_text()
        assert "LLM_MODEL=claude-opus-4-8" in content

    def test_no_llm_model_leaves_env_unset(self, monkeypatch, tmp_path):
        config_file = self._patch_config_file(monkeypatch, tmp_path)
        result = dict(_PROVIDERS["1"])
        result["api_key"] = "sk-ant-key"  # no llm_model key
        monkeypatch.setattr("yeaboi.setup_wizard.select_provider", lambda *a, **kw: result)
        console = Console(file=StringIO(), highlight=False)
        run_setup_wizard(console)
        content = config_file.read_text()
        assert "LLM_MODEL" not in content

    def test_provider_switch_drops_stale_model(self, monkeypatch, tmp_path):
        """Ollama→Anthropic without a model choice must not keep qwen3:8b on disk."""
        config_file = self._patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("LLM_PROVIDER=ollama\nLLM_MODEL=qwen3:8b\n")
        monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
        result = dict(_PROVIDERS["1"])  # anthropic
        result["api_key"] = "sk-ant-key"  # no llm_model key — model phase abandoned
        monkeypatch.setattr("yeaboi.setup_wizard.select_provider", lambda *a, **kw: result)
        console = Console(file=StringIO(), highlight=False)
        run_setup_wizard(console)
        content = config_file.read_text()
        assert "LLM_PROVIDER=anthropic" in content
        assert "LLM_MODEL" not in content
        import os

        assert "LLM_MODEL" not in os.environ


# ---------------------------------------------------------------------------
# _save_progress — the incremental .env writer must never leave a model from
# a different provider on disk (the "(current)" ghost-entry bug)
# ---------------------------------------------------------------------------


class TestSaveProgressProviderSwitch:
    def _patch_config_file(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".env"
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
        return config_file

    def test_switch_clears_model_from_file_and_env(self, monkeypatch, tmp_path):
        config_file = self._patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("LLM_PROVIDER=ollama\nLLM_MODEL=qwen3:8b\n")
        monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
        _save_progress({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-x"})
        content = config_file.read_text()
        assert "LLM_PROVIDER=anthropic" in content
        assert "LLM_MODEL" not in content
        import os

        assert "LLM_MODEL" not in os.environ

    def test_same_provider_keeps_model(self, monkeypatch, tmp_path):
        config_file = self._patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("LLM_PROVIDER=ollama\nLLM_MODEL=qwen3:8b\n")
        _save_progress({"LLM_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://localhost:11434"})
        assert "LLM_MODEL=qwen3:8b" in config_file.read_text()

    def test_switch_with_explicit_model_keeps_new_model(self, monkeypatch, tmp_path):
        config_file = self._patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("LLM_PROVIDER=ollama\nLLM_MODEL=qwen3:8b\n")
        _save_progress({"LLM_PROVIDER": "anthropic", "LLM_MODEL": "claude-opus-4-8"})
        content = config_file.read_text()
        assert "LLM_MODEL=claude-opus-4-8" in content
        assert "qwen3:8b" not in content

    def test_no_provider_in_data_keeps_model(self, monkeypatch, tmp_path):
        """Writes that don't touch the provider (e.g. Jira creds) leave the model alone."""
        config_file = self._patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("LLM_PROVIDER=ollama\nLLM_MODEL=qwen3:8b\n")
        _save_progress({"JIRA_BASE_URL": "https://x.atlassian.net"})
        assert "LLM_MODEL=qwen3:8b" in config_file.read_text()


# ---------------------------------------------------------------------------
# fetch_available_models — live discovery (the anti-staleness mechanism)
# ---------------------------------------------------------------------------


class TestFetchAvailableModels:
    def test_anthropic_returns_ids_in_order(self, monkeypatch):
        import httpx

        payload = {"data": [{"id": "claude-opus-4-8"}, {"id": "claude-sonnet-4-6"}]}
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, payload))
        assert fetch_available_models(_card("anthropic"), "sk-ant-key") == [
            "claude-opus-4-8",
            "claude-sonnet-4-6",
        ]

    def test_openai_filters_noise_newest_first(self, monkeypatch):
        import httpx

        payload = {
            "data": [
                {"id": "gpt-4o", "created": 100},
                {"id": "text-embedding-3-large", "created": 90},
                {"id": "whisper-1", "created": 80},
                {"id": "gpt-5.6", "created": 200},
                {"id": "dall-e-3", "created": 70},
                {"id": "o1", "created": 150},
            ]
        }
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, payload))
        result = fetch_available_models(_card("openai"), "sk-key")
        # embeddings/whisper/dall-e dropped; chat models newest-first by `created`.
        assert result == ["gpt-5.6", "o1", "gpt-4o"]

    def test_google_keeps_only_generate_content(self, monkeypatch):
        import httpx

        payload = {
            "models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
            ]
        }
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, payload))
        result = fetch_available_models(_card("google"), "AIzaKey")
        assert result == ["gemini-2.5-flash", "gemini-2.5-pro"]

    def test_non_200_returns_empty(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(401))
        assert fetch_available_models(_card("anthropic"), "sk-ant-key") == []

    def test_network_error_returns_empty(self, monkeypatch):
        import httpx

        def _boom(*a, **kw):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(httpx, "get", _boom)
        assert fetch_available_models(_card("openai"), "sk-key") == []

    def test_bedrock_excluded(self):
        # No API key model listing for Bedrock — returns [] without any call.
        assert fetch_available_models(_card("bedrock"), "us-east-1") == []


class TestConnectionErrorRedaction:
    """A transport exception quotes the failing request, and Google's URL carries
    the API key — so the message both the wizard renders and the credential gate
    logs must never be the raw string."""

    def test_a_google_key_in_the_url_is_scrubbed(self, monkeypatch):
        import httpx

        from yeaboi.provider_verification import _verify_api_key

        key = "AIzaSyEXAMPLEKEYVALUE0123456789abcdef"
        monkeypatch.setenv("GOOGLE_API_KEY", key)

        def _boom(*a, **kw):
            raise httpx.ConnectError(f"failed to connect to https://generativelanguage.googleapis.com/v1?key={key}")

        monkeypatch.setattr(httpx, "get", _boom)
        ok, message = _verify_api_key(_card("google"), key)

        assert ok is False
        assert key not in message
        assert message.startswith("Connection error:")


class TestOpenAiWireVerification:
    """The six vendors share one branch in each of the four verification chains."""

    def _specs(self):
        from yeaboi.llm_providers import OPENAI_COMPATIBLE

        return list(OPENAI_COMPATIBLE.values())

    _MODELS = {
        "data": [
            {"id": "chat-new", "created": 300},
            {"id": "chat-old", "created": 100},
            {"id": "text-embedding-3", "created": 900},
            {"id": "whisper-large-v3", "created": 900},
        ]
    }

    def test_key_verified_against_the_vendors_own_host(self, monkeypatch):
        import httpx

        for spec in self._specs():
            seen: list[str] = []
            monkeypatch.setattr(httpx, "get", lambda url, **kw: (seen.append(url), _FakeResponse(200, self._MODELS))[1])
            ok, msg = _verify_api_key(_card(spec.key), "k" * 40)
            assert (ok, msg) == (True, "Key verified"), spec.key
            assert seen == [f"{spec.base_url}/models"], spec.key

    @pytest.mark.parametrize("status", [400, 401, 403])
    def test_rejected_key_is_a_definite_rejection(self, monkeypatch, status):
        # 400 is in the set because that is what api.x.ai answers a bad key with
        # ("Incorrect API key provided"); without it Grok's most common setup
        # mistake surfaced as "Unexpected response: 400".
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(status))
        for spec in self._specs():
            assert _verify_api_key(_card(spec.key), "k" * 40) == (False, INVALID_KEY)
            # …and the model check must not report a bad key as "verified".
            assert _verify_model(_card(spec.key), "k" * 40, "anything") == (False, INVALID_KEY)

    def test_discovery_drops_non_chat_families_but_keeps_vendor_ids(self, monkeypatch):
        # The OpenAI filter allowlists gpt-/o1/o3 prefixes; using it here would
        # discard every id these vendors return.
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, self._MODELS))
        for spec in self._specs():
            assert fetch_available_models(_card(spec.key), "k" * 40) == ["chat-new", "chat-old"]

    def test_model_membership_decides(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, self._MODELS))
        card = _card("xai")
        assert _verify_model(card, "k" * 40, "chat-new") == (True, "Model verified")
        assert _verify_model(card, "k" * 40, "no-such-model")[0] is False

    def test_vendor_that_lists_nothing_is_soft_accepted(self, monkeypatch):
        # Same call the wizard would otherwise block on; mirrors how Bedrock
        # soft-accepts inference profiles it cannot enumerate.
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, {"data": []}))
        ok, msg = _verify_model(_card("zai"), "k" * 40, "glm-5.2")
        assert ok is True
        assert "does not list models" in msg

    def test_unexpected_status_is_not_reported_as_a_bad_key(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(503))
        ok, msg = _verify_api_key(_card("mistral"), "k" * 40)
        assert (ok, msg) == (False, "Unexpected response: 503")
        assert msg != INVALID_KEY

    def test_base_url_override_is_honoured(self, monkeypatch):
        import httpx

        monkeypatch.setenv("MISTRAL_BASE_URL", "https://gateway.internal/v1")
        seen: list[str] = []
        monkeypatch.setattr(httpx, "get", lambda url, **kw: (seen.append(url), _FakeResponse(200, {"data": []}))[1])
        _verify_api_key(_card("mistral"), "k" * 40)
        assert seen == ["https://gateway.internal/v1/models"]

    def test_key_format_floors_suit_each_vendor(self):
        # xAI keys are long; Mistral and Z.ai have no prefix to check, so a
        # strict 30-char floor would reject perfectly good keys.
        assert _validate_key(_card("xai"), "xai-" + "a" * 50)[0] == "valid_format"
        assert _validate_key(_card("xai"), "xai-short")[0] == "too_short"
        assert _validate_key(_card("mistral"), "m" * 32)[0] == "valid_format"
        assert _validate_key(_card("deepseek"), "wrong-" + "a" * 40)[0] == "bad_prefix"


class TestProviderScreenWindowing:
    """Eleven providers no longer fit the frame, so the list has to window.

    The frame does not clip: a body taller than its budget pushes the progress
    bar off the bottom of the terminal. These are the properties that stop that
    regression coming back the next time a provider is added.
    """

    def _screen(self, selected: int, width: int, height: int) -> str:
        from rich.console import Console

        from yeaboi.ui.provider_select.screens._screens import _build_select_screen

        console = Console(file=StringIO(), width=width, height=height, highlight=False)
        console.print(_build_select_screen(selected, width=width, height=height))
        return console.file.getvalue()

    @pytest.mark.parametrize("size", [(80, 24), (100, 30), (120, 40), (84, 44)])
    def test_the_selected_provider_is_always_on_screen(self, size):
        # The panel clips rather than overflows, so an unwindowed list fails
        # silently: pick the last provider and its name is simply not drawn.
        from yeaboi.ui.shared._ascii_font import render_ascii_text

        width, height = size
        for selected, card in enumerate(_PROVIDER_CARDS):
            rendered = self._screen(selected, width, height)
            art = render_ascii_text(card["name"])[0].strip()
            assert art in rendered, f"{width}x{height}: {card['name']} not drawn when selected"

    @pytest.mark.parametrize("size", [(80, 24), (100, 30), (120, 40), (84, 44)])
    def test_the_progress_footer_survives_every_selection(self, size):
        # A body taller than its budget pushes the step bar out of the frame.
        width, height = size
        for selected in range(len(_PROVIDER_CARDS)):
            assert "LLM Provider" in self._screen(selected, width, height), (
                f"{width}x{height} sel={selected}: progress bar clipped off the frame"
            )

    def test_selection_is_always_inside_the_window(self):
        from yeaboi.ui.provider_select.screens._screens import _fit_window

        for height in range(20, 60):
            body = max(3, (height - 4) - 9 - 2)
            for selected in range(len(_PROVIDER_CARDS)):
                window, _hidden = _fit_window(len(_PROVIDER_CARDS), selected, body, 3)
                assert selected in window, f"h={height} sel={selected} fell outside {list(window)}"

    def test_hidden_count_is_shown_and_disappears_when_all_fit(self):
        assert "more" in self._screen(0, 100, 24)
        # A terminal tall enough for all eleven shows no hint.
        assert "more" not in self._screen(0, 100, 60)

    def test_window_covers_every_provider_as_the_selection_moves(self):
        from yeaboi.ui.provider_select.screens._screens import _fit_window

        reachable: set[int] = set()
        for selected in range(len(_PROVIDER_CARDS)):
            window, _ = _fit_window(len(_PROVIDER_CARDS), selected, 25, 3)
            reachable |= set(window)
        assert reachable == set(range(len(_PROVIDER_CARDS)))

    def test_model_list_shares_the_same_window_helper(self):
        # One implementation, so the two lists cannot drift apart.
        from yeaboi.ui.provider_select.screens._screens import _fit_window

        assert _fit_window(3, 0, 25, 3) == (range(0, 3), 0)
        window, hidden = _fit_window(20, 10, 25, 3)
        assert hidden == 20 - len(window)
        assert 10 in window
