"""Tests for the Copy-to-clipboard extraction path: status helper, text builders, dispatch."""

from __future__ import annotations

from yeaboi import changelog
from yeaboi.changelog import build_changelog_text
from yeaboi.usage_export import build_usage_text

# ---------------------------------------------------------------------------
# copy_markdown_status
# ---------------------------------------------------------------------------


class TestCopyStatus:
    def test_success(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip, "copy_text", lambda t: True)
        assert clip.copy_markdown_status("# plan") == "Copied to clipboard"

    def test_failure(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip, "copy_text", lambda t: False)
        assert "Couldn't copy" in clip.copy_markdown_status("# plan")

    def test_empty(self):
        from yeaboi.clipboard import copy_markdown_status

        assert copy_markdown_status("   ") == "Nothing to copy"


# ---------------------------------------------------------------------------
# read_clipboard_text — the only workable transcript paste path
# ---------------------------------------------------------------------------


class TestReadClipboardText:
    def _fake_run(self, stdout: bytes, returncode: int = 0):
        class _Proc:
            pass

        proc = _Proc()
        proc.stdout = stdout
        proc.returncode = returncode
        return lambda *a, **k: proc

    def test_reads_text_via_the_platform_helper(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "darwin")
        monkeypatch.setattr(clip.shutil, "which", lambda name: "/usr/bin/pbpaste")
        monkeypatch.setattr(clip.subprocess, "run", self._fake_run(b"Alice: hi\nBob: hey\n"))
        assert clip.read_clipboard_text() == "Alice: hi\nBob: hey\n"

    def test_newlines_survive(self, monkeypatch):
        """The whole point: bracketed paste would flatten this to one turn."""
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "darwin")
        monkeypatch.setattr(clip.shutil, "which", lambda name: "/usr/bin/pbpaste")
        monkeypatch.setattr(clip.subprocess, "run", self._fake_run(b"a\nb\nc"))
        assert (clip.read_clipboard_text() or "").count("\n") == 2

    def test_falls_through_to_the_second_linux_helper(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "linux")
        monkeypatch.setattr(clip.shutil, "which", lambda name: None if name == "wl-paste" else "/usr/bin/xclip")
        monkeypatch.setattr(clip.subprocess, "run", self._fake_run(b"Alice: hi"))
        assert clip.read_clipboard_text() == "Alice: hi"

    def test_no_helper_returns_none(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "darwin")
        monkeypatch.setattr(clip.shutil, "which", lambda name: None)
        assert clip.read_clipboard_text() is None

    def test_unsupported_platform_returns_none(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "sunos5")
        assert clip.read_clipboard_text() is None

    def test_helper_failure_returns_none(self, monkeypatch):
        import yeaboi.clipboard as clip

        monkeypatch.setattr(clip.sys, "platform", "darwin")
        monkeypatch.setattr(clip.shutil, "which", lambda name: "/usr/bin/pbpaste")
        monkeypatch.setattr(clip.subprocess, "run", self._fake_run(b"", returncode=1))
        assert clip.read_clipboard_text() is None

    def test_timeout_never_raises(self, monkeypatch):
        import subprocess as sp

        import yeaboi.clipboard as clip

        def _boom(*a, **k):
            raise sp.TimeoutExpired("pbpaste", 10)

        monkeypatch.setattr(clip.sys, "platform", "darwin")
        monkeypatch.setattr(clip.shutil, "which", lambda name: "/usr/bin/pbpaste")
        monkeypatch.setattr(clip.subprocess, "run", _boom)
        assert clip.read_clipboard_text() is None


# ---------------------------------------------------------------------------
# build_usage_text
# ---------------------------------------------------------------------------


class TestUsageText:
    def _data(self) -> dict:
        return {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key_status": "configured",
            "sessions": {"total": 3, "planning": 2, "analysis": 1, "last_used": "2026-07-21 10:00"},
            "tokens": {"input": 100, "output": 50, "total": 150, "calls": 2, "estimated_cost": 0.0012},
            "lifetime_tokens": {"input": 900, "output": 300, "total": 1200, "calls": 12, "estimated_cost": 0.01},
            "local_performance": {"avg_tokens_per_sec": 42.5},
            "profiles": [{"name": "core", "source": "jira", "sprints": 6}],
            "version": "2.24.0",
            "python_version": "3.11.0",
            "langsmith": "disabled",
            "db_path": "/x/y/sessions.db",
        }

    def test_includes_key_fields(self):
        t = build_usage_text(self._data())
        assert "Usage summary" in t
        assert "claude-sonnet-4-6" in t
        assert "Input tokens:    100" in t
        assert "$0.0012" in t
        assert "Lifetime:" in t
        assert "core (jira, 6 sprints)" in t
        assert "2.24.0" in t

    def test_empty_dict_is_safe(self):
        t = build_usage_text({})
        assert "Usage summary" in t
        assert t.endswith("\n")

    def test_none_is_safe(self):
        assert "Usage summary" in build_usage_text(None)


# ---------------------------------------------------------------------------
# build_changelog_text
# ---------------------------------------------------------------------------


class TestChangelogText:
    def test_lists_versions_and_highlights(self):
        entries = changelog.load_changelog()
        t = build_changelog_text(entries)
        assert "# yeaboi — Changelog" in t
        assert f"## {entries[0].version}" in t
        # every highlight bullet appears
        assert any(f"- {h.text}" in t for h in entries[0].highlights)

    def test_empty_entries(self):
        t = build_changelog_text([])
        assert "no changelog available" in t

    def test_loads_when_not_passed(self):
        # Defaults to load_changelog() when entries omitted.
        assert "# yeaboi — Changelog" in build_changelog_text()


# ---------------------------------------------------------------------------
# _export_via_picker copy dispatch
# ---------------------------------------------------------------------------


class TestTipsText:
    def test_copy_path_copies_all_tips(self, monkeypatch):
        import yeaboi.clipboard as clip
        from yeaboi.ui.shared._tips import build_tips_text, get_tips

        monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
        get_tips.cache_clear()
        copied: dict = {}
        monkeypatch.setattr(clip, "copy_text", lambda t: copied.setdefault("text", t) or True)

        # Mirrors the All Tips page "Copy all" action.
        msg = clip.copy_markdown_status(build_tips_text())
        assert msg == "Copied to clipboard"
        assert "# yeaboi — Tips" in copied["text"]
        get_tips.cache_clear()


class TestExportViaPickerCopy:
    def test_copy_dispatch_copies_document_markdown(self, monkeypatch):
        import yeaboi.clipboard as clip
        import yeaboi.ui.mode_select as ms

        copied: dict = {}
        monkeypatch.setattr(clip, "copy_text", lambda t: copied.setdefault("text", t) or True)
        # Stub the picker to choose "copy".
        monkeypatch.setattr(ms, "_pick_dest", lambda *a, **k: "copy")

        msg = ms._export_via_picker(
            None,
            None,
            None,
            0.05,
            False,
            mode="reporting",
            files_export=lambda: "should-not-run",
            get_document=lambda: ("Title", "# The Markdown"),
        )
        assert msg == "Copied to clipboard"
        assert copied["text"] == "# The Markdown"

    def test_copy_dispatch_surfaces_error_string(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        monkeypatch.setattr(ms, "_pick_dest", lambda *a, **k: "copy")
        # get_document returns an error string (nothing to export) — passed through.
        msg = ms._export_via_picker(
            None,
            None,
            None,
            0.05,
            False,
            mode="standup",
            files_export=lambda: "x",
            get_document=lambda: "Nothing to export yet",
        )
        assert msg == "Nothing to export yet"
