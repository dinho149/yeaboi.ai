"""Unit tests for standup transcript discovery, reading and parsing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from yeaboi.standup import transcripts

FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def managed(tmp_path, monkeypatch):
    """Point the managed transcript folder at a temp dir."""
    d = tmp_path / "transcripts"
    d.mkdir()
    monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", d)
    return d


def _copy(name: str, into: Path) -> Path:
    dest = into / name
    dest.write_bytes((FIXTURES / name).read_bytes())
    return dest


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParseText:
    def test_speaker_lines_become_turns(self):
        turns = transcripts.parse("Alice: hello\nBob: hi there\n", "txt")
        assert [(t.speaker, t.text) for t in turns] == [("Alice", "hello"), ("Bob", "hi there")]

    def test_timestamped_labels(self):
        turns = transcripts.parse("Alice (09:31): I picked up YB-14.\n", "txt")
        assert turns[0].speaker == "Alice"
        assert turns[0].text == "I picked up YB-14."

    def test_continuation_lines_join_the_turn(self):
        turns = transcripts.parse("Alice: one\ntwo\nthree\nBob: other\n", "txt")
        assert turns[0].text == "one two three"
        assert len(turns) == 2

    def test_consecutive_same_speaker_merges(self):
        turns = transcripts.parse("Alice: one\nAlice: two\n", "txt")
        assert len(turns) == 1
        assert turns[0].text == "one two"

    def test_prose_with_a_colon_is_not_a_speaker(self):
        """'Note: we shipped' is a sentence, not a speaker label."""
        turns = transcripts.parse("Alice: hello\nNote that we shipped it, finally: yes.\n", "txt")
        assert len(turns) == 1
        assert "finally" in turns[0].text

    def test_leading_prose_kept_unattributed(self):
        turns = transcripts.parse("some preamble\nAlice: hello\n", "txt")
        assert turns[0].speaker == ""
        assert turns[0].text == "some preamble"

    def test_markdown_bullets_stripped(self):
        turns = transcripts.parse("- **Alice**: shipped it\n", "md")
        assert turns[0].text == "shipped it"

    def test_empty_text(self):
        assert transcripts.parse("", "txt") == ()

    def test_indices_are_sequential(self):
        turns = transcripts.parse("Alice: a\nBob: b\nCarol: c\n", "txt")
        assert [t.index for t in turns] == [0, 1, 2]


class TestParseVtt:
    def test_voice_tags(self):
        turns = transcripts.parse((FIXTURES / "2026-07-30-standup.vtt").read_text(), "vtt")
        assert [t.speaker for t in turns] == ["Alice", "Bob", "Alice"]
        assert "design doc" in turns[2].text

    def test_header_notes_and_timings_dropped(self):
        text = "\n".join(t.text for t in transcripts.parse((FIXTURES / "2026-07-30-standup.vtt").read_text(), "vtt"))
        assert "WEBVTT" not in text
        assert "-->" not in text
        assert "Recorded" not in text

    def test_plain_cues_fall_back_to_name_colon(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nAlice: I shipped it.\n"
        turns = transcripts.parse(vtt, "vtt")
        assert turns[0].speaker == "Alice"

    def test_an_arrow_in_speech_is_not_a_timing_line(self):
        """Somebody's actual update, dropped because it contained an arrow.

        The timing matcher used to be a bare `-->` anywhere in the line, so a
        spoken "manual --> automated" was read as cue timing and thrown away —
        losing exactly the sentence the review exists to check the report
        against.
        """
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nAlice: we moved the deploy from manual --> automated.\n"
        turns = transcripts.parse(vtt, "vtt")
        assert turns[0].speaker == "Alice"
        assert "automated" in turns[0].text

    def test_timing_lines_are_still_dropped_in_both_dialects(self):
        for timing in ("00:00:01.000 --> 00:00:04.000", "00:01.000 --> 00:04.000", "00:00:01,000 --> 00:00:04,000"):
            turns = transcripts.parse(f"WEBVTT\n\n{timing}\nAlice: hi.\n", "vtt")
            assert [t.text for t in turns] == ["hi."], timing


class TestParseSrt:
    def test_index_and_timing_lines_dropped(self):
        turns = transcripts.parse((FIXTURES / "2026-07-27-standup.srt").read_text(), "srt")
        assert [t.speaker for t in turns] == ["Alice", "Bob"]
        assert "00:00" not in turns[0].text


class TestParseJson:
    def test_segments_shape(self):
        turns = transcripts.parse((FIXTURES / "2026-07-26-standup.json").read_text(), "json")
        assert [t.speaker for t in turns] == ["Alice", "Bob"]
        assert "YB-20" in turns[0].text

    def test_flat_list_shape(self):
        turns = transcripts.parse('[{"participant": "Alice", "content": "hi"}]', "json")
        assert (turns[0].speaker, turns[0].text) == ("Alice", "hi")

    def test_monologues_elements_shape(self):
        raw = '{"monologues": [{"speaker": "Alice", "elements": [{"value": "hello "}, {"value": "world"}]}]}'
        turns = transcripts.parse(raw, "json")
        assert turns[0].speaker == "Alice"
        assert "hello" in turns[0].text and "world" in turns[0].text

    def test_malformed_json_falls_back_to_text(self):
        turns = transcripts.parse((FIXTURES / "malformed.json").read_text(), "json")
        assert turns  # degraded, not empty — never raises

    def test_unrecognised_shape_falls_back(self):
        turns = transcripts.parse('{"nothing": "useful"}', "json")
        assert isinstance(turns, tuple)

    def test_empty_list(self):
        assert transcripts.parse("[]", "json") == ()


# ---------------------------------------------------------------------------
# Date attribution
# ---------------------------------------------------------------------------


class TestInferDate:
    def test_iso_in_filename_wins(self, tmp_path):
        p = tmp_path / "2026-07-30-standup.txt"
        p.write_text("meeting on 2020-01-01")
        assert transcripts.infer_date(p, p.read_text(), today=date(2026, 8, 1)) == "2026-07-30"

    def test_compact_date_in_filename(self, tmp_path):
        p = tmp_path / "standup20260730.txt"
        p.write_text("hello")
        assert transcripts.infer_date(p, "hello", today=date(2026, 8, 1)) == "2026-07-30"

    def test_json_date_field(self, tmp_path):
        p = tmp_path / "meeting.json"
        raw = '{"date": "2026-07-26", "segments": []}'
        p.write_text(raw)
        assert transcripts.infer_date(p, raw, today=date(2026, 8, 1)) == "2026-07-26"

    def test_date_in_head_of_content(self, tmp_path):
        p = tmp_path / "meeting.txt"
        raw = "Standup notes for Jul 29, 2026\nAlice: hi"
        p.write_text(raw)
        assert transcripts.infer_date(p, raw, today=date(2026, 8, 1)) == "2026-07-29"

    def test_falls_back_to_mtime(self, tmp_path):
        import os
        import time

        p = tmp_path / "meeting.txt"
        p.write_text("Alice: hi")
        stamp = time.mktime(date(2026, 7, 20).timetuple())
        os.utime(p, (stamp, stamp))
        assert transcripts.infer_date(p, "Alice: hi", today=date(2026, 8, 1)) == "2026-07-20"

    def test_future_date_in_filename_is_rejected(self, tmp_path):
        """A future date is not a standup that happened."""
        import os
        import time

        p = tmp_path / "2099-01-01-standup.txt"
        p.write_text("Alice: hi")
        stamp = time.mktime(date(2026, 7, 20).timetuple())
        os.utime(p, (stamp, stamp))
        assert transcripts.infer_date(p, "Alice: hi", today=date(2026, 8, 1)) == "2026-07-20"

    def test_impossible_date_ignored(self, tmp_path):
        p = tmp_path / "2026-13-45-standup.txt"
        p.write_text("Alice: hi")
        assert transcripts.infer_date(p, "Alice: hi", today=date(2026, 8, 1)) != "2026-13-45"

    def test_date_deep_in_body_ignored(self, tmp_path):
        """A date far into the transcript is usually someone naming a deadline."""
        p = tmp_path / "meeting.txt"
        raw = ("Alice: filler line\n" * 400) + "Bob: let's ship by 2020-01-01\n"
        p.write_text(raw)
        assert transcripts.infer_date(p, raw, today=date(2026, 8, 1)) != "2020-01-01"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class TestReadTranscript:
    def test_a_file_that_vanished_raises_rather_than_reading_as_empty(self, managed):
        """The TOCTOU window between discover()'s scan and this read is real.

        Raising is the contract: the sweep turns it into a named warning, where
        returning an empty transcript would record the file as reviewed and
        conclude the meeting contained nothing.
        """
        import pytest

        with pytest.raises(OSError):
            transcripts.read_transcript(managed / "gone.txt", today=date(2026, 8, 1))

    def test_reads_and_describes(self, managed):
        path = _copy("2026-07-30-standup.vtt", managed)
        source, turns = transcripts.read_transcript(path, today=date(2026, 8, 1))
        assert source.fmt == "vtt"
        assert source.covered_date == "2026-07-30"
        assert source.filename == "2026-07-30-standup.vtt"
        assert source.attribution == "labelled"
        assert source.speakers == ("Alice", "Bob")
        assert source.truncated is False
        assert source.external is False
        assert len(turns) == 3

    def test_external_flag_recorded(self, managed):
        path = _copy("2026-07-29-standup.txt", managed)
        source, _ = transcripts.read_transcript(path, external=True, today=date(2026, 8, 1))
        assert source.external is True

    def test_unlabelled_transcript_is_marked(self, managed):
        path = _copy("unlabelled.txt", managed)
        source, turns = transcripts.read_transcript(path, today=date(2026, 8, 1))
        assert source.attribution == "unlabelled"
        assert source.speakers == ()
        assert turns

    def test_oversized_content_is_truncated_and_reported(self, managed, monkeypatch):
        monkeypatch.setattr(transcripts, "_MAX_CHARS", 100)
        path = managed / "big.txt"
        path.write_text("Alice: " + ("word " * 500))
        source, _ = transcripts.read_transcript(path, today=date(2026, 8, 1))
        assert source.truncated is True
        assert source.char_count == 100

    def test_symlink_escaping_the_managed_dir_is_denied(self, managed):
        """The sandbox check runs even inside ~/.yeaboi — a symlink can escape it.

        Targets ~/.ssh rather than a tmp_path file on purpose: conftest
        whitelists pytest's basetemp, so a link inside it proves nothing.
        """
        from yeaboi.fs_policy import SandboxViolationError

        link = managed / "sneaky.txt"
        link.symlink_to(Path.home() / ".ssh" / "id_rsa")
        with pytest.raises(SandboxViolationError):
            transcripts.read_transcript(link, today=date(2026, 8, 1))

    def test_invalid_utf8_does_not_raise(self, managed):
        path = managed / "binary.txt"
        path.write_bytes(b"Alice: hi \xff\xfe there")
        source, turns = transcripts.read_transcript(path, today=date(2026, 8, 1))
        assert source.char_count > 0
        assert turns


class TestContentHash:
    def test_stable_for_same_content(self, tmp_path):
        a, b = tmp_path / "a.txt", tmp_path / "b.txt"
        a.write_text("same")
        b.write_text("same")
        assert transcripts.content_hash(a) == transcripts.content_hash(b)

    def test_changes_with_content(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("one")
        first = transcripts.content_hash(p)
        p.write_text("two")
        assert transcripts.content_hash(p) != first


class TestToPromptText:
    def test_renders_speaker_lines(self):
        turns = transcripts.parse("Alice: hi\nBob: hello\n", "txt")
        assert transcripts.to_prompt_text(turns, limit=1000) == "Alice: hi\nBob: hello"

    def test_keeps_the_tail_when_over_limit(self):
        """Corrections land at the END of a standup — that is what must survive."""
        turns = transcripts.parse("\n".join(f"Alice: line {i}" for i in range(500)), "txt")
        out = transcripts.to_prompt_text(turns, limit=200)
        assert len(out) <= 260  # limit plus the elision marker
        assert "line 499" in out
        assert "omitted" in out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscover:
    def test_finds_managed_transcripts(self, managed, db_path):
        _copy("2026-07-30-standup.vtt", managed)
        found, warnings = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert [p.name for p, _ in found] == ["2026-07-30-standup.vtt"]
        assert warnings == []

    def test_orders_by_covered_date(self, managed, db_path):
        for name in ("2026-07-30-standup.vtt", "2026-07-28-standup.md", "2026-07-29-standup.txt"):
            _copy(name, managed)
        found, _ = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert [p.name for p, _ in found] == [
            "2026-07-28-standup.md",
            "2026-07-29-standup.txt",
            "2026-07-30-standup.vtt",
        ]

    def test_a_big_json_transcript_is_dated_by_its_own_field(self, managed, db_path):
        """discover and read_transcript must never disagree about the date.

        infer_date's structured-field step runs json.loads, and the head of a
        real segments array is not valid JSON. Scanning only the head here made
        discover fall through to mtime (today), window the file out, and never
        review it — while read_transcript, given the whole document, would have
        dated it correctly. A silently skipped transcript in a feature whose one
        job is not missing one.
        """
        import json

        payload = {
            "date": "2026-07-29",
            "segments": [{"speaker": "Alice", "text": f"line {i} of the standup"} for i in range(200)],
        }
        big = managed / "meeting-export.json"
        big.write_text(json.dumps(payload))
        assert len(big.read_text()) > transcripts._DATE_SCAN_CHARS  # the head alone is invalid JSON

        found, _ = transcripts.discover("s1", db_path=db_path, before_date="2026-08-01", today=date(2026, 8, 1))
        assert [p.name for p, _ in found] == ["meeting-export.json"]
        source, _turns = transcripts.read_transcript(big, today=date(2026, 8, 1))
        assert source.covered_date == "2026-07-29"

    def test_skips_unsupported_suffixes(self, managed, db_path):
        (managed / "notes.pdf").write_text("nope")
        (managed / "recording.m4a").write_bytes(b"nope")
        _copy("2026-07-30-standup.vtt", managed)
        found, _ = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert [p.name for p, _ in found] == ["2026-07-30-standup.vtt"]

    def test_skips_dotfiles(self, managed, db_path):
        (managed / ".hidden.txt").write_text("Alice: hi")
        found, _ = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert found == []

    def test_skips_oversized_files(self, managed, db_path, monkeypatch):
        monkeypatch.setattr(transcripts, "_MAX_BYTES", 10)
        _copy("2026-07-30-standup.vtt", managed)
        found, _ = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert found == []

    def test_skips_already_reviewed_content(self, managed, db_path):
        from yeaboi.standup.store import StandupStore

        path = _copy("2026-07-30-standup.vtt", managed)
        with StandupStore(db_path) as store:
            store.mark_transcript_reviewed(
                "s1",
                path=str(path),
                content_hash=transcripts.content_hash(path),
                covered_date="2026-07-30",
                review_id=1,
            )
        found, _ = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert found == []

    def test_renamed_file_is_still_skipped(self, managed, db_path):
        """Content-keyed bookkeeping: renaming must not re-spend an LLM call."""
        from yeaboi.standup.store import StandupStore

        path = _copy("2026-07-30-standup.vtt", managed)
        digest = transcripts.content_hash(path)
        with StandupStore(db_path) as store:
            store.mark_transcript_reviewed(
                "s1", path=str(path), content_hash=digest, covered_date="2026-07-30", review_id=1
            )
        path.rename(managed / "2026-07-30-renamed.vtt")
        found, _ = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert found == []

    def test_include_reviewed_overrides(self, managed, db_path):
        from yeaboi.standup.store import StandupStore

        path = _copy("2026-07-30-standup.vtt", managed)
        with StandupStore(db_path) as store:
            store.mark_transcript_reviewed(
                "s1",
                path=str(path),
                content_hash=transcripts.content_hash(path),
                covered_date="2026-07-30",
                review_id=1,
            )
        found, _ = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1), include_reviewed=True)
        assert len(found) == 1

    def test_before_date_excludes_today_and_the_future(self, managed, db_path):
        _copy("2026-07-30-standup.vtt", managed)
        found, _ = transcripts.discover("s1", db_path=db_path, before_date="2026-07-30", today=date(2026, 8, 1))
        assert found == []

    def test_before_date_includes_earlier_dates(self, managed, db_path):
        _copy("2026-07-30-standup.vtt", managed)
        found, _ = transcripts.discover("s1", db_path=db_path, before_date="2026-07-31", today=date(2026, 8, 1))
        assert len(found) == 1

    def test_lookback_window_excludes_ancient_transcripts(self, managed, db_path):
        path = managed / "2026-01-05-standup.txt"
        path.write_text("Alice: old news")
        found, _ = transcripts.discover("s1", db_path=db_path, before_date="2026-07-31", today=date(2026, 8, 1))
        assert found == []

    def test_per_sweep_cap_defers_the_rest_and_says_so(self, managed, db_path, monkeypatch):
        monkeypatch.setattr(transcripts, "_MAX_FILES_PER_SWEEP", 2)
        for day in (26, 27, 28, 29):
            (managed / f"2026-07-{day}-standup.txt").write_text("Alice: hi")
        found, warnings = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert len(found) == 2
        assert any("next run" in w for w in warnings)

    def test_unwindowed_cap_keeps_the_newest(self, managed, db_path, monkeypatch):
        """An on-demand review has no window, so oldest-first would spend the
        whole budget on the oldest meetings on disk."""
        monkeypatch.setattr(transcripts, "_MAX_FILES_PER_SWEEP", 2)
        for day in (26, 27, 28, 29):
            (managed / f"2026-07-{day}-standup.txt").write_text("Alice: hi")
        found, warnings = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert [p.name for p, _ in found] == ["2026-07-28-standup.txt", "2026-07-29-standup.txt"]
        assert any("newest" in w for w in warnings)

    def test_windowed_sweep_cap_keeps_the_oldest(self, managed, db_path, monkeypatch):
        """The automatic sweep IS windowed, so draining a backlog in order is right."""
        monkeypatch.setattr(transcripts, "_MAX_FILES_PER_SWEEP", 2)
        for day in (26, 27, 28, 29):
            (managed / f"2026-07-{day}-standup.txt").write_text("Alice: hi")
        found, warnings = transcripts.discover("s1", db_path=db_path, before_date="2026-07-31", today=date(2026, 8, 1))
        assert [p.name for p, _ in found] == ["2026-07-26-standup.txt", "2026-07-27-standup.txt"]
        assert any("oldest" in w for w in warnings)

    def test_missing_managed_dir_is_created_not_fatal(self, tmp_path, monkeypatch, db_path):
        monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", tmp_path / "never-made")
        found, warnings = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert found == []
        assert warnings == []


class TestDiscoverExternalDir:
    def test_allowed_external_dir_is_swept(self, managed, db_path, tmp_path, monkeypatch):
        outside = tmp_path / "meetings"
        outside.mkdir()
        _copy("2026-07-30-standup.vtt", outside)
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", str(outside))
        found, warnings = transcripts.discover(
            "s1", config={"transcript_dir": str(outside)}, db_path=db_path, today=date(2026, 8, 1)
        )
        assert [(p.name, ext) for p, ext in found] == [("2026-07-30-standup.vtt", True)]
        assert warnings == []

    def test_external_dir_recurses_one_level(self, managed, db_path, tmp_path, monkeypatch):
        outside = tmp_path / "meetings" / "2026-07"
        outside.mkdir(parents=True)
        _copy("2026-07-30-standup.vtt", outside)
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", str(tmp_path / "meetings"))
        found, _ = transcripts.discover(
            "s1",
            config={"transcript_dir": str(tmp_path / "meetings")},
            db_path=db_path,
            today=date(2026, 8, 1),
        )
        assert len(found) == 1

    def test_denied_external_dir_degrades_with_a_warning(self, managed, db_path, tmp_path, monkeypatch):
        """The scheduled run cannot consent — say so once, don't silently do nothing."""
        monkeypatch.delenv("YEABOI_ALLOWED_PATHS", raising=False)
        outside = tmp_path / "not-allowed"
        outside.mkdir()
        _copy("2026-07-30-standup.vtt", managed)
        found, warnings = transcripts.discover(
            "s1", config={"transcript_dir": str(outside)}, db_path=db_path, today=date(2026, 8, 1)
        )
        # The managed folder still worked.
        assert [p.name for p, _ in found] == ["2026-07-30-standup.vtt"]
        assert any("Transcript folder skipped" in w for w in warnings)
        assert any("YEABOI_ALLOWED_PATHS" in w for w in warnings)

    def test_missing_external_dir_warns(self, managed, db_path, tmp_path, monkeypatch):
        gone = tmp_path / "gone"
        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", str(tmp_path))
        found, warnings = transcripts.discover(
            "s1", config={"transcript_dir": str(gone)}, db_path=db_path, today=date(2026, 8, 1)
        )
        assert found == []
        assert any("not found" in w for w in warnings)

    def test_blank_config_dir_is_ignored(self, managed, db_path):
        found, warnings = transcripts.discover(
            "s1", config={"transcript_dir": "   "}, db_path=db_path, today=date(2026, 8, 1)
        )
        assert warnings == []
        assert found == []


class TestTranscriptNudge:
    """ "A standup ran on date D but no transcript covering D was ever reviewed."

    The whole signal is a set difference over two indexed queries — nothing is
    stored, so there is no "last nudged" state to migrate or get wrong.
    """

    def _seed(self, db_path, *, ran=(), reviewed=(), status="success", session="s1"):
        from yeaboi.agent.state import StandupReport
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            for day in ran:
                store.record_run(StandupReport(session_id=session, date=day), status=status)
            for i, day in enumerate(reviewed):
                # Hash keyed by date: the ledger is content-keyed, so a reused
                # hash would UPDATE an earlier row instead of adding one.
                store.mark_transcript_reviewed(
                    session, path=f"/t/{day}.vtt", content_hash=f"h-{day}", covered_date=day, review_id=i + 1
                )

    def _nudge(self, db_path, *, config=None, today=date(2026, 8, 1), session="s1"):
        return transcripts.transcript_nudge(session, config=config, db_path=db_path, today=today)

    def test_no_standups_means_nothing_to_say(self, db_path):
        assert not self._nudge(db_path)

    def test_a_fully_transcribed_history_is_quiet(self, db_path):
        days = ["2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"]
        self._seed(db_path, ran=days, reviewed=days)
        assert not self._nudge(db_path)

    def test_a_new_user_gets_a_grace_period(self, db_path):
        """Greeting somebody's first week with a chore is how a feature gets turned off."""
        self._seed(db_path, ran=["2026-07-30", "2026-07-31"])
        assert not self._nudge(db_path)

    def test_invite_after_the_grace_period(self, db_path):
        self._seed(db_path, ran=["2026-07-29", "2026-07-30", "2026-07-31"])
        nudge = self._nudge(db_path)
        assert nudge.level == "invite"
        assert "2026-07-31" in nudge.message

    def test_the_first_thing_anyone_hears_is_the_quiet_invite(self, db_path):
        """The invite tier is TUI-only; the reminder tier broadcasts. A user must
        never meet this feature for the first time in their team's Slack."""
        self._seed(db_path, ran=[f"2026-07-{d}" for d in range(27, 32)][:4])
        assert self._nudge(db_path).level == "invite"

    def test_reminder_at_the_streak_threshold(self, db_path):
        """Someone who has used the feature before, then stopped."""
        self._seed(
            db_path,
            ran=["2026-07-25", "2026-07-26", "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30"],
            reviewed=["2026-07-25"],
        )
        nudge = self._nudge(db_path)
        assert nudge.level == "reminder"
        assert nudge.streak == 5

    def test_escalation_offers_the_off_switch(self, db_path):
        """After enough misses the honest reading is 'this team doesn't record
        standups' — so stop asking and point at the setting."""
        days = [f"2026-07-{d}" for d in range(22, 31)]
        self._seed(db_path, ran=days, reviewed=["2026-07-21"])
        nudge = self._nudge(db_path)
        assert nudge.level == "escalated"
        assert "turn this off" in nudge.message

    def test_the_streak_counts_back_from_the_most_recent_standup(self, db_path):
        """A team that transcribed yesterday is not behind, whatever last month
        looked like."""
        self._seed(
            db_path,
            ran=["2026-07-24", "2026-07-25", "2026-07-28", "2026-07-29", "2026-07-30"],
            reviewed=["2026-07-30"],
        )
        nudge = self._nudge(db_path)
        assert nudge.streak == 0
        assert nudge.level == "invite"  # there are older misses, but nothing urgent

    def test_a_transcript_this_morning_self_clears_the_streak(self, db_path):
        """The sweep runs before the report's warnings are assembled, so a file
        dropped this morning is already recorded by the time this is computed."""
        self._seed(
            db_path,
            ran=[f"2026-07-{d}" for d in range(26, 32)],
            reviewed=["2026-07-25"],
        )
        assert self._nudge(db_path).level == "reminder"
        self._seed(db_path, reviewed=["2026-07-31"])
        assert self._nudge(db_path).streak == 0

    def test_the_opt_out_silences_it(self, db_path):
        """The off switch already exists; a nudge for a disabled feature is noise."""
        self._seed(db_path, ran=[f"2026-07-{d}" for d in range(22, 31)])
        assert not self._nudge(db_path, config={"transcript_review_enabled": False})

    def test_enabled_config_still_nudges(self, db_path):
        self._seed(db_path, ran=["2026-07-29", "2026-07-30", "2026-07-31"])
        assert self._nudge(db_path, config={"transcript_review_enabled": True})

    def test_failed_runs_are_not_held_against_the_user(self, db_path):
        """You can't transcribe a standup that never produced a report."""
        self._seed(db_path, ran=["2026-07-29", "2026-07-30", "2026-07-31"], status="error")
        assert not self._nudge(db_path)

    def test_the_window_ignores_ancient_standups(self, db_path):
        self._seed(db_path, ran=["2026-01-05", "2026-01-06", "2026-01-07"])
        assert not self._nudge(db_path)

    def test_weekends_and_time_off_cost_nothing(self, db_path):
        """The population is dates a standup RAN, not calendar days."""
        self._seed(db_path, ran=["2026-07-24", "2026-07-31"], reviewed=["2026-07-24", "2026-07-31"])
        assert not self._nudge(db_path)

    def test_a_rerun_on_the_same_day_does_not_ratchet(self, db_path):
        """Rate limiting is structural: level is a pure function of the streak."""
        self._seed(db_path, ran=["2026-07-29", "2026-07-30", "2026-07-31"])
        first = self._nudge(db_path)
        self._seed(db_path, ran=["2026-07-31"])  # standup run again the same day
        second = self._nudge(db_path)
        assert first.level == second.level
        assert first.message == second.message

    def test_todays_own_standup_is_not_counted_against_it(self, db_path):
        """The run being recorded right now is not a standup that went unchecked.

        A run is written when it finishes, so a SECOND standup on the same day
        sees the first one in history with no transcript against it — for a
        meeting that has only just happened. Without ``before_date`` that extra
        "miss" pushes the streak over the reminder threshold, and `reminder` is
        the rung that enters report.warnings and broadcasts to Slack and email.
        """
        self._seed(db_path, ran=["2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31", "2026-08-01"])
        rerun = transcripts.transcript_nudge("s1", db_path=db_path, today=date(2026, 8, 1))
        in_run = transcripts.transcript_nudge("s1", db_path=db_path, before_date="2026-08-01", today=date(2026, 8, 1))
        assert "2026-08-01" in rerun.missed_dates
        assert "2026-08-01" not in in_run.missed_dates
        assert rerun.level == "reminder"  # …which broadcasts
        assert in_run.level == "invite"  # …which stays in the TUI

    def test_the_reminder_job_still_asks_about_today(self, db_path):
        """It fires AFTER the meeting, so today is exactly what it asks about."""
        self._seed(db_path, ran=["2026-07-30", "2026-07-31", "2026-08-01"])
        assert "2026-08-01" in transcripts.transcript_nudge("s1", db_path=db_path, today=date(2026, 8, 1)).missed_dates

    def test_missed_dates_are_newest_first(self, db_path):
        self._seed(db_path, ran=["2026-07-29", "2026-07-30", "2026-07-31"])
        assert self._nudge(db_path).missed_dates == ("2026-07-31", "2026-07-30", "2026-07-29")

    def test_other_sessions_do_not_leak_in(self, db_path):
        self._seed(db_path, ran=["2026-07-29", "2026-07-30", "2026-07-31"], session="other")
        assert not self._nudge(db_path, session="s1")

    def test_bool_is_the_has_something_to_say_test(self, db_path):
        from yeaboi.agent.state import TranscriptNudge

        assert not TranscriptNudge()
        assert TranscriptNudge(level="invite", message="x")


class TestNormalizeDroppedPath:
    """A path dragged from Finder arrives quoted (Terminal) or escaped (iTerm2)."""

    def test_strips_terminal_style_quotes_and_trailing_space(self):
        raw = "'/Users/me/My Meetings/a.vtt' "
        assert transcripts.normalize_dropped_path(raw) == "/Users/me/My Meetings/a.vtt"

    def test_strips_double_quotes(self):
        assert transcripts.normalize_dropped_path('"/tmp/a b.vtt"') == "/tmp/a b.vtt"

    def test_unescapes_iterm_style_backslashes(self):
        raw = "/Users/me/My\\ Meetings/a.vtt"
        assert transcripts.normalize_dropped_path(raw) == "/Users/me/My Meetings/a.vtt"

    def test_a_quoted_backslash_stays_literal(self):
        assert transcripts.normalize_dropped_path("'/tmp/a\\ b.vtt'") == "/tmp/a\\ b.vtt"

    def test_plain_path_is_untouched(self):
        assert transcripts.normalize_dropped_path("  /tmp/a.vtt  ") == "/tmp/a.vtt"

    def test_expands_a_tilde(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert transcripts.normalize_dropped_path("~/a.vtt") == str(tmp_path / "a.vtt")

    def test_empty_stays_empty(self):
        assert transcripts.normalize_dropped_path("   ") == ""


class TestInferDateFromText:
    def test_reads_an_iso_date_in_the_head(self):
        got = transcripts.infer_date_from_text("Standup 2026-07-30\nAlice: hi", today=date(2026, 8, 1))
        assert got == "2026-07-30"

    def test_reads_a_json_date_field(self):
        raw = '{"date": "2026-07-29", "segments": []}'
        assert transcripts.infer_date_from_text(raw, fmt="json", today=date(2026, 8, 1)) == "2026-07-29"

    def test_json_field_is_ignored_for_non_json_formats(self):
        raw = '{"date": "2026-07-29"}'
        assert transcripts.infer_date_from_text(raw, fmt="txt", today=date(2026, 8, 1)) == "2026-07-29"

    def test_a_future_date_is_refused(self):
        got = transcripts.infer_date_from_text("Ship by 2026-09-01", today=date(2026, 8, 1))
        assert got == ""

    def test_no_date_returns_blank(self):
        assert transcripts.infer_date_from_text("Alice: hi", today=date(2026, 8, 1)) == ""


class TestImportText:
    def test_writes_a_date_stamped_file_into_the_managed_folder(self, managed):
        path = transcripts.import_text("Alice: shipped auth", today=date(2026, 8, 1))
        assert path.parent == managed
        assert path.name == "2026-08-01-pasted.txt"
        assert path.read_text() == "Alice: shipped auth\n"

    def test_the_filename_date_is_what_makes_attribution_work(self, managed):
        """infer_date reads the filename first — no sidecar metadata needed."""
        path = transcripts.import_text("Alice: hi", covered_date="2026-07-29", today=date(2026, 8, 1))
        source, _turns = transcripts.read_transcript(path, today=date(2026, 8, 1))
        assert source.covered_date == "2026-07-29"

    def test_round_trips_to_labelled_attribution(self, managed):
        """The paste path must not silently degrade what the review may conclude."""
        text = "Alice: I shipped auth\nBob: I reviewed it\nCara: blocked on staging"
        path = transcripts.import_text(text, today=date(2026, 8, 1))
        source, turns = transcripts.read_transcript(path, today=date(2026, 8, 1))
        assert source.attribution == "labelled"
        assert source.speakers == ("Alice", "Bob", "Cara")
        assert len(turns) == 3

    def test_no_banner_is_prepended(self, managed):
        """A header line would land as an unattributed leading turn."""
        path = transcripts.import_text("Alice: hi\nBob: hey", today=date(2026, 8, 1))
        assert path.read_text().startswith("Alice:")

    def test_infers_the_date_from_the_text_when_not_given(self, managed):
        path = transcripts.import_text("Standup 2026-07-28\nAlice: hi", today=date(2026, 8, 1))
        assert path.name.startswith("2026-07-28-")

    def test_explicit_date_wins_over_the_text(self, managed):
        path = transcripts.import_text(
            "Standup 2026-07-28\nAlice: hi", covered_date="2026-07-30", today=date(2026, 8, 1)
        )
        assert path.name.startswith("2026-07-30-")

    def test_a_future_explicit_date_is_clamped_to_today(self, managed):
        path = transcripts.import_text("Alice: hi", covered_date="2027-01-01", today=date(2026, 8, 1))
        assert path.name.startswith("2026-08-01-")

    def test_a_malformed_explicit_date_raises(self, managed):
        with pytest.raises(ValueError, match="Invalid covered_date"):
            transcripts.import_text("Alice: hi", covered_date="last tuesday", today=date(2026, 8, 1))

    def test_label_becomes_a_slug(self, managed):
        path = transcripts.import_text("Alice: hi", label="Team Sync!! ", today=date(2026, 8, 1))
        assert path.name == "2026-08-01-team-sync.txt"

    def test_re_importing_identical_text_is_idempotent(self, managed):
        first = transcripts.import_text("Alice: hi", today=date(2026, 8, 1))
        second = transcripts.import_text("Alice: hi", today=date(2026, 8, 1))
        assert first == second
        assert len(list(managed.iterdir())) == 1

    def test_different_text_on_the_same_day_gets_a_suffix(self, managed):
        first = transcripts.import_text("Alice: hi", today=date(2026, 8, 1))
        second = transcripts.import_text("Bob: hey", today=date(2026, 8, 1))
        assert first.name == "2026-08-01-pasted.txt"
        assert second.name == "2026-08-01-pasted-2.txt"

    def test_blank_text_raises(self, managed):
        with pytest.raises(ValueError, match="empty"):
            transcripts.import_text("   \n  \n", today=date(2026, 8, 1))

    def test_oversized_text_raises(self, managed, monkeypatch):
        monkeypatch.setattr(transcripts, "_MAX_BYTES", 20)
        with pytest.raises(ValueError, match="larger than"):
            transcripts.import_text("Alice: " + "x" * 100, today=date(2026, 8, 1))

    def test_imported_file_is_owner_only(self, managed):
        path = transcripts.import_text("Alice: hi", today=date(2026, 8, 1))
        assert path.stat().st_mode & 0o777 == 0o600

    def test_an_import_is_discoverable(self, managed, db_path):
        transcripts.import_text("Alice: hi", covered_date="2026-07-30", today=date(2026, 8, 1))
        found, warnings = transcripts.discover("s1", db_path=db_path, today=date(2026, 8, 1))
        assert [p.name for p, _ in found] == ["2026-07-30-pasted.txt"]
        assert warnings == []


class TestScanBounds:
    """An external folder can be ~/Downloads or an Obsidian vault, and this walk
    runs in front of a standup — it must degrade, not stall."""

    def test_non_recursive_scan_stays_flat(self, tmp_path):
        (tmp_path / "top.txt").write_text("Alice: hi")
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "deep.txt").write_text("Alice: hi")
        found = transcripts._candidate_files(tmp_path, recurse=False)
        assert [p.name for p in found] == ["top.txt"]

    def test_recursive_scan_stops_at_the_depth_bound(self, tmp_path, monkeypatch):
        monkeypatch.setattr(transcripts, "_MAX_SCAN_DEPTH", 2)
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (tmp_path / "a" / "shallow.txt").write_text("Alice: hi")
        (deep / "buried.txt").write_text("Alice: hi")
        found = transcripts._candidate_files(tmp_path, recurse=True)
        assert [p.name for p in found] == ["shallow.txt"]

    def test_recursive_scan_stops_at_the_entry_bound(self, tmp_path, monkeypatch):
        monkeypatch.setattr(transcripts, "_MAX_SCAN_ENTRIES", 5)
        for i in range(20):
            (tmp_path / f"file{i:02d}.txt").write_text("Alice: hi")
        found = transcripts._candidate_files(tmp_path, recurse=True)
        assert len(found) == 5

    def test_recursive_scan_skips_dot_directories_wholesale(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "COMMIT_EDITMSG.txt").write_text("Alice: hi")
        (tmp_path / "real.txt").write_text("Alice: hi")
        found = transcripts._candidate_files(tmp_path, recurse=True)
        assert [p.name for p in found] == ["real.txt"]

    def test_unreadable_directory_is_skipped_not_fatal(self, tmp_path):
        (tmp_path / "ok.txt").write_text("Alice: hi")
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "inner.txt").write_text("Alice: hi")
        locked.chmod(0o000)
        try:
            found = transcripts._candidate_files(tmp_path, recurse=True)
            assert [p.name for p in found] == ["ok.txt"]
        finally:
            locked.chmod(0o755)
