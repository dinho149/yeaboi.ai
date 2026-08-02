"""Tests for probing the folders a user's meeting recordings already land in.

``home`` is injected everywhere so nothing here touches a real ``$HOME``.
"""

from __future__ import annotations

from yeaboi.standup import transcript_sources


def _vtt(path, *, date_stem: str = "GMT20260730-093000_Recording"):
    path.mkdir(parents=True, exist_ok=True)
    f = path / f"{date_stem}.transcript.vtt"
    f.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v Alice>I shipped auth\n")
    return f


class TestDetect:
    def test_finds_a_zoom_folder(self, tmp_path):
        _vtt(tmp_path / "Documents" / "Zoom" / "2026-07-30 Standup")
        found = transcript_sources.detect(home=tmp_path)
        assert [c.label for c in found] == ["Zoom recordings"]
        assert found[0].file_count == 1
        assert found[0].path == str(tmp_path / "Documents" / "Zoom")

    def test_zoom_filenames_date_themselves(self, tmp_path):
        """The reason Zoom is the best case: no setup, correct attribution."""
        _vtt(tmp_path / "Documents" / "Zoom")
        found = transcript_sources.detect(home=tmp_path)
        assert found[0].newest_date == "2026-07-30"

    def test_finds_google_drive_meet_recordings(self, tmp_path):
        _vtt(tmp_path / "Library" / "CloudStorage" / "GoogleDrive-me@x.com" / "My Drive" / "Meet Recordings")
        found = transcript_sources.detect(home=tmp_path)
        assert [c.label for c in found] == ["Google Meet recordings"]

    def test_the_first_matching_pattern_wins(self, tmp_path):
        """~/Documents/Zoom and ~/Zoom shouldn't both be offered."""
        _vtt(tmp_path / "Documents" / "Zoom")
        _vtt(tmp_path / "Zoom")
        found = transcript_sources.detect(home=tmp_path)
        assert len(found) == 1
        assert found[0].path == str(tmp_path / "Documents" / "Zoom")

    def test_downloads_needs_a_real_transcript_to_be_offered(self, tmp_path):
        """Recursing a whole download history is slow, and the grant is read+write."""
        downloads = tmp_path / "Downloads"
        downloads.mkdir()
        (downloads / "invoice.txt").write_text("not a transcript")
        assert transcript_sources.detect(home=tmp_path) == []

    def test_downloads_is_offered_once_a_vtt_lands_in_it(self, tmp_path):
        _vtt(tmp_path / "Downloads")
        found = transcript_sources.detect(home=tmp_path)
        assert [c.label for c in found] == ["Downloads"]
        assert found[0].recurse is False

    def test_downloads_does_not_recurse(self, tmp_path):
        _vtt(tmp_path / "Downloads")
        _vtt(tmp_path / "Downloads" / "old" / "archive", date_stem="GMT20260101-090000_Recording")
        found = transcript_sources.detect(home=tmp_path)
        assert found[0].file_count == 1

    def test_empty_folders_are_not_offered(self, tmp_path):
        (tmp_path / "Documents" / "Zoom").mkdir(parents=True)
        assert transcript_sources.detect(home=tmp_path) == []

    def test_nothing_on_disk_is_not_an_error(self, tmp_path):
        assert transcript_sources.detect(home=tmp_path) == []

    def test_candidates_report_whether_the_sandbox_allows_them(self, tmp_path, monkeypatch):
        """These live outside ~/.yeaboi, so they need consent before they work."""
        _vtt(tmp_path / "Documents" / "Zoom")
        monkeypatch.delenv("YEABOI_ALLOWED_PATHS", raising=False)
        assert transcript_sources.detect(home=tmp_path)[0].allowed is False

    def test_a_whitelisted_folder_reports_allowed(self, tmp_path, monkeypatch):
        _vtt(tmp_path / "Documents" / "Zoom")
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", str(tmp_path / "Documents" / "Zoom"))
        assert transcript_sources.detect(home=tmp_path)[0].allowed is True

    def test_granola_and_obsidian_are_not_probed(self, tmp_path):
        """Excluded on purpose: a proprietary cache blob and somebody's diary."""
        _vtt(tmp_path / "Library" / "Application Support" / "Granola")
        _vtt(tmp_path / "Documents" / "MyVault")
        assert transcript_sources.detect(home=tmp_path) == []


class TestDescribe:
    def test_counts_and_dates(self):
        c = transcript_sources.SourceCandidate(file_count=14, newest_date="2026-07-30")
        assert transcript_sources.describe(c) == "14 transcripts, newest 2026-07-30"

    def test_singular(self):
        assert transcript_sources.describe(transcript_sources.SourceCandidate(file_count=1)) == "1 transcript"

    def test_undated_folder_omits_the_date(self):
        assert transcript_sources.describe(transcript_sources.SourceCandidate(file_count=3)) == "3 transcripts"

    def test_a_capped_count_says_it_is_capped(self, monkeypatch):
        c = transcript_sources.SourceCandidate(file_count=transcript_sources._MAX_COUNT)
        assert transcript_sources.describe(c).startswith(f"{transcript_sources._MAX_COUNT}+")
