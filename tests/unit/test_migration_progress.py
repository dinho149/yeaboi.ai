"""Tests for scripts/migration_progress.py — the Go-migration Slack renderer.

Two routines post its ``lines`` verbatim (``cron/go-migration-progress.md`` and
``events/go-migration-wave-merged.md``), so every judgement a reader could act
on — the bar, the counts, a stalled wave, a blind read — is asserted here, and
the rendered lines are re-linted against the Slack dialect rules the fleet's
templates live under. Nothing at post time would notice a drift.

No test here touches the network: ``build_payload`` is exercised through
hand-built payloads, and the two GitHub reads are covered by stubbing the
transport seam.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = ROOT / "scripts" / "migration_progress.py"
_spec = importlib.util.spec_from_file_location("migration_progress", _MODULE_PATH)
progress = importlib.util.module_from_spec(_spec)
sys.modules["migration_progress"] = progress
_spec.loader.exec_module(progress)

# The same title shape TestSlackTemplates pins for the routine templates.
TITLE = re.compile(r"^(?P<emoji>\S+) \*\*(?P<name>[^*]+)\*\* — .+")

PROGRAM_TEXT = """\
## 3. The 13 PRs

| ✔ | PR | Wave | Contents (phase commits inside) | Size | Gate |
|---|---|---|---|---|---|
| ✔ | 1 | W7 | Retro/poker export builders | S | existing parity harness |
| ☐ | 2 | W8 | Foundations: config/paths, 85 env vars, CLI parser skeleton | M | golden-subprocess diff |
| ☐ | 3 | W9 | Persistence: sessions v1→v27 ladder | L | migrate fixture DBs both sides |
"""


def payload(**overrides) -> dict:
    """A hand-built payload with every field present; tests override the bit
    they are about."""
    base = {
        "today": "Tue 19 Aug",
        "waves_total": 19,
        "waves_shipped": 7,
        "program_total": 13,
        "program_done": 1,
        "blind": False,
        "in_flight": [],
        "next_wave": {"wave": "W8", "contents": "Foundations: config/paths"},
        "core_version": "0.5.0",
        "parity_tests": 41,
        "program_url": "https://github.com/o/r/blob/main/cowork/migration/program.md",
    }
    base.update(overrides)
    return base


MERGED = {
    "title": "migration(w7): retro/poker export builders",
    "number": 231,
    "url": "https://github.com/o/r/pull/231",
    "wave": 7,
    "merged_on": "Fri 22 Aug",
}


def assert_dialect(lines: list[str]) -> None:
    """The Slack grammar rules, re-applied to rendered output — the same checks
    ``TestSlackTemplates`` runs over the worked examples in the routine files,
    with the same ▰/▱ meter carve-out."""
    body = "\n".join(lines)
    assert TITLE.match(lines[0]), f"not a title line: {lines[0]!r}"
    stripped = re.sub(r"\]\(https?://[^)]+\)", "]()", body)
    assert "http" not in stripped, "a URL outside a [title](url) link"
    assert "\U0001f916" not in body, "🤖 is a reserved marker"
    for line in lines:
        assert not re.search(r"(?<!\*)\*(?!\*)", line), f"mrkdwn emphasis leaked: {line!r}"
        if line and set(line) == {"─"}:
            continue
        symbols = [char for char in line if unicodedata.category(char) == "So" and char not in {"✅", "❌", "▰", "▱"}]
        if symbols:
            assert len(symbols) == 1, f"more than one emoji on a line: {line!r}"
            assert line.startswith(symbols[0]), f"an anchor belongs at the start: {line!r}"


class TestMeter:
    def test_one_glyph_per_wave(self):
        bar = progress.meter(7, 19)
        assert bar == "▰" * 7 + "▱" * 12
        assert len(bar) == 19

    def test_clamped_outside_the_track(self):
        assert progress.meter(25, 19) == "▰" * 19
        assert progress.meter(-3, 19) == "▱" * 19

    def test_empty_and_full(self):
        assert progress.meter(0, 19) == "▱" * 19
        assert progress.meter(19, 19) == "▰" * 19


class TestParseProgram:
    def test_reads_checkboxes_pr_numbers_and_waves(self):
        waves = progress.parse_program(PROGRAM_TEXT)
        assert [wave.wave for wave in waves] == ["W7", "W8", "W9"]
        assert [wave.done for wave in waves] == [True, False, False]
        assert waves[0].pr == 1
        assert waves[1].contents == "Foundations: config/paths, 85 env vars, CLI parser skeleton"

    def test_the_committed_program_doc_parses_to_thirteen_rows(self):
        # The renderer reads the real file at run time, so the real file has to
        # keep parsing: a reworded table that stops matching `_ROW` renders a
        # bar stuck at the pilot baseline with nothing failing anywhere else.
        waves = progress.parse_program(progress.PROGRAM_DOC.read_text(encoding="utf-8"))
        assert len(waves) == 13
        assert [wave.pr for wave in waves] == list(range(1, 14))
        assert waves[0].wave == "W7"
        assert waves[-1].wave == "W19"

    def test_a_checked_row_counts_as_done(self):
        checked = PROGRAM_TEXT.replace("| ☐ | 2 |", "| ✔ | 2 |")
        waves = progress.parse_program(checked)
        assert [wave.done for wave in waves] == [True, True, False]


class TestWeeklyLines:
    def test_the_happy_path_renders_the_bar_and_counts(self):
        lines = progress.weekly_lines(payload())
        assert lines[0] == "🐹 **Go Migration** — 7 of 19 waves shipped · Tue 19 Aug"
        assert lines[1] == ("▰" * 7 + "▱" * 12 + " 7/19 waves · 1/13 program wave-PRs merged")
        assert_dialect(lines)

    def test_in_flight_prs_render_as_a_section(self):
        item = {
            "title": "migration(w8): foundations",
            "number": 240,
            "url": "https://github.com/o/r/pull/240",
            "opened": "Mon 18 Aug",
            "stalled": False,
        }
        lines = progress.weekly_lines(payload(in_flight=[item]))
        body = "\n".join(lines)
        assert "🚧 **In flight** (1)" in lines
        assert "[migration(w8): foundations #240](https://github.com/o/r/pull/240)" in body
        assert "— open since Mon 18 Aug" in body
        assert "───────────────────────────" in lines
        assert_dialect(lines)

    def test_a_stalled_wave_is_named(self):
        item = {
            "title": "migration(w8): foundations",
            "number": 240,
            "url": "https://github.com/o/r/pull/240",
            "opened": "Mon 4 Aug",
            "stalled": True,
        }
        body = "\n".join(progress.weekly_lines(payload(in_flight=[item])))
        assert "stalled — see the wave's Linear ticket" in body

    def test_a_failed_pr_read_is_blind_not_zero(self):
        # `blind=True` means a GitHub read failed. The message must say so and
        # the merged count must render `?` — a queue reported empty when it
        # could not be asked is a migration that looks idle rather than blind.
        lines = progress.weekly_lines(payload(in_flight=None, blind=True))
        body = "\n".join(lines)
        assert "could not fully read GitHub" in body
        assert "?/13 program wave-PRs merged" in lines[1]
        assert "🚧" not in body
        assert_dialect(lines)

    def test_degraded_inputs_drop_their_fragment_rather_than_guess(self):
        lines = progress.weekly_lines(payload(core_version=None, parity_tests=None))
        body = "\n".join(lines)
        assert "📦" not in body
        assert "None" not in body
        assert_dialect(lines)

    def test_no_program_url_degrades_to_a_path(self):
        lines = progress.weekly_lines(payload(program_url=None))
        assert lines[-1] == "Next wave and the full plan: `cowork/migration/program.md`"
        assert_dialect(lines)


class TestWaveMergedLines:
    def test_the_happy_path_names_the_wave_and_the_next_one(self):
        lines = progress.wave_merged_lines(payload(), MERGED)
        assert lines[0] == "🌊 **Go Migration** — Wave 7 merged · Fri 22 Aug"
        body = "\n".join(lines)
        assert "merged with its parity gate green" in body
        assert "yeaboi-core is at 0.5.0" in body
        assert "Next: W8, Foundations: config/paths — [the program of record]" in body
        assert_dialect(lines)

    def test_a_wave_it_cannot_number_still_announces(self):
        merged = dict(MERGED, wave=None)
        lines = progress.wave_merged_lines(payload(), merged)
        assert lines[0].startswith("🌊 **Go Migration** — a wave merged · ")
        assert_dialect(lines)

    def test_the_last_wave_says_the_program_is_complete(self):
        lines = progress.wave_merged_lines(payload(next_wave=None), MERGED)
        assert "That was the last wave" in "\n".join(lines)
        assert_dialect(lines)


class TestMergedPrFacts:
    def test_wave_number_parsed_from_the_title(self):
        assert progress._WAVE_TITLE.match("migration(w12): LLM layer").group("wave") == "12"

    def test_the_pilot_pr_maps_to_wave_six(self, monkeypatch):
        monkeypatch.setattr(
            progress,
            "_pr",
            lambda number: {
                "merged": True,
                "title": "move doc-quality scoring behind the go sidecar",
                "html_url": "https://github.com/o/r/pull/224",
                "merged_at": "2026-08-20T10:00:00Z",
                "labels": [{"name": "cowork"}, {"name": progress.LABEL}],
            },
        )
        facts = progress.merged_pr_facts(progress.WAVE6_PR)
        assert facts is not None and facts["wave"] == 6

    def test_an_unlabelled_or_unmerged_pr_is_refused(self, monkeypatch):
        monkeypatch.setattr(progress, "_pr", lambda number: {"merged": False, "labels": []})
        assert progress.merged_pr_facts(300) is None
        monkeypatch.setattr(progress, "_pr", lambda number: {"merged": True, "labels": [{"name": "cowork"}]})
        assert progress.merged_pr_facts(300) is None


class TestParseProgramBounds:
    def test_a_row_quoted_outside_section_three_does_not_count(self):
        # The campaign appends `## PR N — Wave X` spec sections that may quote a
        # table row; the bar reads §3 and nothing else.
        text = PROGRAM_TEXT + "\n## 6. PR 1 — Wave 7\n\n| ☐ | 9 | W99 | a quoted row | S | gate |\n"
        assert len(progress.parse_program(text)) == 3


class TestBuildPayload:
    """The degradation seams the routines depend on, over stubbed reads."""

    NOW = __import__("datetime").datetime(2026, 8, 25, 9, 0, tzinfo=__import__("datetime").UTC)

    def _stub(self, monkeypatch, tmp_path, *, wave6=True, prs=None):
        doc = tmp_path / "program.md"
        doc.write_text(PROGRAM_TEXT, encoding="utf-8")
        monkeypatch.setattr(progress, "PROGRAM_DOC", doc)
        monkeypatch.setattr(progress, "_wave6_merged", lambda: wave6)
        monkeypatch.setattr(progress, "_labeled_prs", lambda state: prs)
        monkeypatch.setattr(progress, "core_version", lambda: "0.5.0")
        monkeypatch.setattr(progress, "parity_test_count", lambda: 36)
        monkeypatch.setattr(
            progress, "_program_url", lambda: "https://github.com/o/r/blob/main/cowork/migration/program.md"
        )

    def test_the_happy_path_counts_and_sees(self, monkeypatch, tmp_path):
        self._stub(monkeypatch, tmp_path, prs=[])
        built = progress.build_payload(now=self.NOW)
        # PROGRAM_TEXT: 3 rows, 1 checked; pilot 5 + wave6 1 + program 1 = 7.
        assert built["waves_total"] == progress.PILOT_WAVES + 3
        assert built["waves_shipped"] == 7
        assert built["blind"] is False
        assert built["in_flight"] == []
        assert built["next_wave"]["wave"] == "W8"

    def test_an_unknown_wave_six_is_blind_and_never_counted(self, monkeypatch, tmp_path):
        self._stub(monkeypatch, tmp_path, wave6=None, prs=[])
        built = progress.build_payload(now=self.NOW)
        assert built["blind"] is True
        assert built["waves_shipped"] == 6  # 5 pilot + 1 program; the unknown is not guessed in

    def test_a_failed_queue_read_is_blind(self, monkeypatch, tmp_path):
        self._stub(monkeypatch, tmp_path, prs=None)
        built = progress.build_payload(now=self.NOW)
        assert built["blind"] is True
        assert built["in_flight"] is None

    def test_stalled_arithmetic_and_a_bad_timestamp(self, monkeypatch, tmp_path):
        def pr(number: int, created: str) -> dict:
            return {
                "title": f"pr {number}",
                "number": number,
                "html_url": f"https://github.com/o/r/pull/{number}",
                "created_at": created,
            }

        prs = [pr(1, "2026-08-01T00:00:00Z"), pr(2, "2026-08-24T00:00:00Z"), pr(3, "not-a-date")]
        self._stub(monkeypatch, tmp_path, prs=prs)
        built = progress.build_payload(now=self.NOW)
        stalled = {item["number"]: item["stalled"] for item in built["in_flight"]}
        assert stalled == {1: True, 2: False, 3: False}
        assert built["in_flight"][2]["opened"] == ""


class TestHelpers:
    def test_day_renders_the_digest_date_shape(self):
        assert progress._day("2026-08-22T10:00:00Z") == "Sat 22 Aug"
        assert progress._day("not-a-date") == ""
        assert progress._day(None) == ""

    def test_core_version_reads_the_packaging_pin(self, monkeypatch, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "yeaboi-core"\nversion = "0.7.0"\n', encoding="utf-8")
        monkeypatch.setattr(progress, "CORE_PYPROJECT", pyproject)
        assert progress.core_version() == "0.7.0"
        monkeypatch.setattr(progress, "CORE_PYPROJECT", tmp_path / "missing.toml")
        assert progress.core_version() is None

    def test_parity_test_count_parses_and_degrades(self, monkeypatch):
        class Done:
            stdout = "36 tests collected in 0.5s"

        monkeypatch.setattr(progress.subprocess, "run", lambda *a, **k: Done())
        assert progress.parity_test_count() == 36

        def boom(*a, **k):
            raise OSError("no pytest")

        monkeypatch.setattr(progress.subprocess, "run", boom)
        assert progress.parity_test_count() is None

    def test_labeled_prs_keeps_only_pull_requests(self, monkeypatch):
        monkeypatch.setattr(progress.transport, "resolve_slug", lambda root: "o/r")
        rows = [
            {"number": 1, "pull_request": {}, "title": "a pr"},
            {"number": 2, "title": "a plain issue"},
        ]
        monkeypatch.setattr(progress, "_get", lambda path: rows)
        found = progress._labeled_prs("open")
        assert [item["number"] for item in found] == [1]
        monkeypatch.setattr(progress, "_get", lambda path: None)
        assert progress._labeled_prs("open") is None


class TestMain:
    def test_weekly_prints_payload_and_lines(self, monkeypatch, capsys):
        monkeypatch.setattr(progress, "build_payload", lambda: payload())
        assert progress.main(["--weekly"]) == 0
        printed = __import__("json").loads(capsys.readouterr().out)
        assert printed["lines"][0].startswith("🐹 **Go Migration** — ")

    def test_wave_merged_needs_a_pr(self, monkeypatch, capsys):
        monkeypatch.setattr(progress, "build_payload", lambda: payload())
        assert progress.main(["--wave-merged"]) == 2

    def test_a_non_wave_pr_exits_nonzero_and_posts_nothing(self, monkeypatch, capsys):
        monkeypatch.setattr(progress, "build_payload", lambda: payload())
        monkeypatch.setattr(progress, "merged_pr_facts", lambda number: None)
        assert progress.main(["--wave-merged", "--pr", "300"]) == 1
        assert capsys.readouterr().out == ""
