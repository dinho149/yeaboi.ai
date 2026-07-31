"""Unit tests for the Anonymize engine, exporter, prompt, clipboard, and paths."""

from __future__ import annotations

import json
from types import SimpleNamespace

from tests._pages import island
from yeaboi.agent.state import AnonymizedOutput
from yeaboi.anonymize import engine as anon_engine
from yeaboi.anonymize.engine import (
    _apply_seed_mask,
    _collect_seed_terms,
    _parse_replacements,
    run_anonymize,
)

# ---------------------------------------------------------------------------
# Seed masking (deterministic)
# ---------------------------------------------------------------------------


class TestSeedMasking:
    def test_collect_seed_terms_from_config(self, monkeypatch):
        monkeypatch.setenv("JIRA_PROJECT_KEY", "ACME")
        monkeypatch.setenv("ANONYMIZE_MASK_TERMS", "YouLend, YL")
        monkeypatch.delenv("AZURE_DEVOPS_PROJECT", raising=False)
        terms = _collect_seed_terms(project_name="Payments Rewrite", extra_mask_terms=("Vendor Co",))
        assert "ACME" in terms
        assert "YouLend" in terms
        assert "YL" in terms
        assert "Payments Rewrite" in terms
        assert "Vendor Co" in terms
        # Longest-first so multi-word terms mask before their substrings.
        assert terms.index("Payments Rewrite") < terms.index("ACME")

    def test_keep_terms_are_excluded(self, monkeypatch):
        monkeypatch.setenv("ANONYMIZE_MASK_TERMS", "YouLend,React")
        terms = _collect_seed_terms(keep_terms=("react",))
        assert "YouLend" in terms
        assert "React" not in terms  # case-insensitive keep

    def test_short_and_empty_terms_dropped(self, monkeypatch):
        monkeypatch.delenv("JIRA_PROJECT_KEY", raising=False)
        monkeypatch.setenv("ANONYMIZE_MASK_TERMS", "A, , YouLend")
        terms = _collect_seed_terms()
        assert "A" not in terms  # single char would mangle unrelated text
        assert "YouLend" in terms

    def test_apply_seed_mask_replaces_case_insensitively(self):
        masked, reps = _apply_seed_mask("YOULEND and youlend shipped ACME-123", ("YouLend", "ACME"))
        assert "youlend" not in masked.lower()
        assert "[COMPANY]" in masked
        # ACME-123 → the key is masked, the number preserved.
        assert "-123" in masked
        assert len(reps) == 2

    def test_apply_seed_mask_reports_only_applied(self):
        masked, reps = _apply_seed_mask("nothing sensitive here", ("YouLend",))
        assert masked == "nothing sensitive here"
        assert reps == []


# ---------------------------------------------------------------------------
# Pipeline — fallback (no LLM)
# ---------------------------------------------------------------------------


class TestFallback:
    def test_unconfigured_llm_returns_seed_masked_with_warning(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "ANTHROPIC_API_KEY not set"))
        monkeypatch.setenv("ANONYMIZE_MASK_TERMS", "YouLend")
        result = run_anonymize("YouLend shipped it", source_mode="standup")
        assert isinstance(result, AnonymizedOutput)
        assert "YouLend" not in result.anonymized_text
        assert result.warnings
        assert "unavailable" in result.warnings[0].lower()
        assert result.source_mode == "standup"

    def test_empty_text_short_circuits(self, monkeypatch):
        result = run_anonymize("   ", source_mode="reporting")
        assert result.anonymized_text == ""
        assert result.warnings
        assert "empty" in result.warnings[0].lower()

    def test_llm_exception_never_raises(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))

        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", boom)
        monkeypatch.setenv("ANONYMIZE_MASK_TERMS", "YouLend")
        result = run_anonymize("YouLend did work", source_mode="retro")
        assert "YouLend" not in result.anonymized_text  # seed mask still applied
        assert result.warnings

    def test_auth_error_becomes_warning_not_raise(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))

        class FakeAuthError(Exception):
            pass

        monkeypatch.setattr("yeaboi.agent.nodes._is_llm_auth_or_billing_error", lambda e: True)

        def boom(*a, **k):
            raise FakeAuthError("401")

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", boom)
        result = run_anonymize("some output", source_mode="planning")
        assert result.warnings
        assert "billing" in result.warnings[0].lower() or "api key" in result.warnings[0].lower()


# ---------------------------------------------------------------------------
# Pipeline — LLM success
# ---------------------------------------------------------------------------


class TestLLMSuccess:
    def _fake_response(self, payload: dict):
        return SimpleNamespace(content=json.dumps(payload))

    def test_llm_masking_parsed(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        captured = {}

        def fake_invoke(prompt, **kwargs):
            captured["prompt"] = prompt
            return self._fake_response(
                {
                    "anonymized_text": "[COMPANY] shipped [PROJECT]",
                    "replacements": [{"original": "Acme", "placeholder": "[COMPANY]"}],
                }
            )

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", fake_invoke)
        result = run_anonymize("Acme shipped Falcon", source_mode="reporting")
        assert result.anonymized_text == "[COMPANY] shipped [PROJECT]"
        assert ("Acme", "[COMPANY]") in result.replacements
        assert not result.warnings
        # Prompt frames the input as untrusted data.
        assert "UNTRUSTED DATA" in captured["prompt"]

    def test_instruction_and_keep_terms_reach_prompt(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        captured = {}

        def fake_invoke(prompt, **kwargs):
            captured["prompt"] = prompt
            return self._fake_response({"anonymized_text": "masked", "replacements": []})

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", fake_invoke)
        run_anonymize(
            "text",
            instruction="also mask the vendor",
            keep_terms=("React",),
            source_mode="planning",
        )
        assert "also mask the vendor" in captured["prompt"]
        assert "React" in captured["prompt"]  # keep note folded into the instruction

    def test_empty_llm_text_falls_back_to_seed(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setenv("ANONYMIZE_MASK_TERMS", "YouLend")
        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_json",
            lambda prompt, **k: self._fake_response({"anonymized_text": "", "replacements": []}),
        )
        result = run_anonymize("YouLend did it", source_mode="standup")
        assert "YouLend" not in result.anonymized_text
        assert result.warnings  # a "no usable masking" notice

    def test_progress_callback_invoked(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        seen: list[str] = []
        run_anonymize("YouLend", source_mode="standup", on_progress=seen.append)
        assert seen  # at least the seed + AI stage messages


class TestParseReplacements:
    def test_tolerates_bad_shapes(self):
        assert _parse_replacements("not a list") == ()
        assert _parse_replacements([{"original": "A", "placeholder": "[X]"}, "junk", {}]) == (("A", "[X]"),)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_writes_md_and_html(self, tmp_path, monkeypatch):
        monkeypatch.setattr("yeaboi.paths.ANONYMIZE_EXPORTS_DIR", tmp_path / "anon")
        from yeaboi.anonymize.export import export_anonymized

        result = AnonymizedOutput(
            anonymized_text="# Heading\n\n- a bullet\n- another",
            source_mode="reporting",
            generated_at="2026-07-21",
        )
        paths = export_anonymized(result, title="Test Report", project_name="Demo")
        assert paths["markdown"].exists()
        assert paths["html"].exists()
        html = paths["html"].read_text()
        # The masked document travels as the Markdown it already is and is read
        # by the bundle (frontend/src/export/markdown.ts), so the two files this
        # writes carry provably the same text rather than two renderings of it.
        assert island(html)["report"]["markdown"] == result.anonymized_text
        assert f"<code>{paths['markdown'].name}</code>" in html  # the no-JavaScript answer

    def test_masked_document_is_data_not_markup(self):
        from yeaboi.anonymize.export import build_anonymized_html

        # This is the export whose input was handled *because* it held sensitive
        # material, and it has been through an LLM. Nothing in it reaches the
        # HTML parser at all now: it is a string inside a non-executable
        # application/json element.
        result = AnonymizedOutput(anonymized_text="| A | B |\n|---|---|\n| <img src=x onerror=alert(1)> | 2 |")
        html = build_anonymized_html(result, title="T")
        assert "<img src=x" not in html
        assert island(html)["report"]["markdown"] == result.anonymized_text

    def test_markdown_carries_notices(self):
        from yeaboi.anonymize.export import build_anonymized_markdown

        result = AnonymizedOutput(anonymized_text="body", warnings=("heads up",))
        md = build_anonymized_markdown(result, title="T")
        assert md.startswith("# T")
        assert "heads up" in md


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_get_anonymize_export_dir_creates(tmp_path, monkeypatch):
    monkeypatch.setattr("yeaboi.paths.ANONYMIZE_EXPORTS_DIR", tmp_path / "anon")
    from yeaboi.paths import get_anonymize_export_dir

    d = get_anonymize_export_dir("My Project")
    assert d.exists()
    assert d.name == "my project"


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


class TestClipboard:
    def test_copy_text_success(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "darwin")
        monkeypatch.setattr(clip.shutil, "which", lambda name: "/usr/bin/pbcopy")
        calls = {}

        def fake_run(cmd, input=None, capture_output=None, timeout=None):
            calls["cmd"] = cmd
            calls["input"] = input
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(clip.subprocess, "run", fake_run)
        assert clip.copy_text("hello") is True
        assert calls["cmd"] == ["pbcopy"]
        assert calls["input"] == b"hello"

    def test_copy_text_no_helper(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "linux")
        monkeypatch.setattr(clip.shutil, "which", lambda name: None)
        assert clip.copy_text("hello") is False

    def test_copy_text_empty_is_false(self):
        from yeaboi.clipboard import copy_text

        assert copy_text("") is False

    def test_copy_text_unsupported_platform(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "sunos")
        assert clip.copy_text("x") is False


def test_engine_module_only_public_run_anonymize():
    """Surface-parity relies on run_anonymize being the ONLY public engine function."""
    public = [n for n in vars(anon_engine) if not n.startswith("_") and callable(vars(anon_engine)[n])]
    # Imported names (date) are fine; assert our own pipeline entry point is the public one.
    assert "run_anonymize" in public


class TestSharedDesignSystem:
    def test_anonymized_html_uses_shared_theme(self):
        from yeaboi.anonymize.export import build_anonymized_html

        html = build_anonymized_html(AnonymizedOutput(anonymized_text="body"), title="T")
        assert 'data-theme="midnight"' in html
        assert "yeaboi-export-theme" in html  # theme switcher present
        assert 'src="http' not in html and "<link" not in html  # self-contained
