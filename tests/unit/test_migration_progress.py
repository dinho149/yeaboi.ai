"""Tests for scripts/migration_progress.py — the Go-migration Slack renderer.

Routines post its ``lines`` verbatim (``cron/go-migration-daily.md`` and
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
        "landed": [],
        "next_wave": {"wave": "W8", "contents": "Foundations: config/paths", "gate": "golden-subprocess diff"},
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

    def test_the_size_and_gate_columns_are_captured(self):
        # The Gate column is the per-wave "how it is proved" the daily post
        # quotes; nothing else in the program carries one.
        waves = progress.parse_program(PROGRAM_TEXT)
        assert [wave.size for wave in waves] == ["S", "M", "L"]
        assert waves[0].gate == "existing parity harness"
        assert waves[2].gate == "migrate fixture DBs both sides"

    def test_every_committed_row_carries_a_gate(self):
        # A row whose gate went missing renders a landed wave with no way to
        # check it — the one thing the daily post exists to carry.
        waves = progress.parse_program(progress.PROGRAM_DOC.read_text(encoding="utf-8"))
        assert all(wave.gate for wave in waves), [w.wave for w in waves if not w.gate]

    def test_a_row_missing_its_last_columns_still_counts(self):
        # size/gate are optional so a hand-edit that drops them degrades the
        # prose, never the bar.
        trimmed = "| ☐ | 4 | W10 | Mode engines headless |\n"
        waves = progress.parse_program(PROGRAM_TEXT + trimmed)
        assert [wave.wave for wave in waves] == ["W7", "W8", "W9", "W10"]
        assert waves[-1].gate == ""


class TestDailyLines:
    LANDED = [
        {
            "title": "migration(w7): retro/poker export builders",
            "number": 231,
            "url": "https://github.com/o/r/pull/231",
            "wave": "W7",
            "merged_on": "Mon 18 Aug",
            "gate": "existing parity harness",
            "contents": "Retro/poker export builders",
        }
    ]

    def test_a_quiet_day_still_posts_the_bar_and_how_to_test(self):
        # The lane's whole story is the bar plus a way to check it; a day with
        # no merge is still worth one line, which is why nothing here is
        # conditional on `landed`.
        lines = progress.daily_lines(payload())
        assert lines[0] == "🐹 **Go Migration** — nothing landed · Tue 19 Aug"
        assert any("How to test" in line for line in lines)
        assert_dialect(lines)

    def test_a_landed_wave_is_named_in_plain_language_with_its_gate(self):
        lines = progress.daily_lines(payload(landed=self.LANDED))
        body = "\n".join(lines)
        assert lines[0] == "🐹 **Go Migration** — 1 landed · Tue 19 Aug"
        # The §3 contents clause, not the PR title — the table is the prose.
        assert "[W7 — Retro/poker export builders](https://github.com/o/r/pull/231)" in body
        assert "— proved by existing parity harness" in body
        assert_dialect(lines)

    def test_it_asks_for_nothing(self):
        # The lane merges its own waves into the integration branch, so the
        # daily post is a pure TELL. An approval verb here would be a decision
        # arriving in a channel that has none to make.
        for kwargs in ({}, {"landed": self.LANDED}, {"blind": True}):
            body = "\n".join(progress.daily_lines(payload(**kwargs)))
            assert "✅" not in body and "❌" not in body
            assert "approve" not in body.lower()

    def test_how_to_test_points_at_the_integration_branch_not_main(self):
        # For the whole program `main` gains nothing, so a reader sent there
        # would find none of the migration.
        body = "\n".join(progress.daily_lines(payload(landed=self.LANDED)))
        assert progress.INTEGRATION_BRANCH in body
        assert "git switch main" not in body

    def test_a_blind_read_says_so(self):
        lines = progress.daily_lines(payload(blind=True))
        assert any(line.startswith("⚠️") for line in lines)
        assert_dialect(lines)

    def test_in_flight_replaces_next_up(self):
        # Both answer "what is moving"; printing both would say it twice.
        flight = [
            {
                "title": "migration(w8): foundations",
                "number": 240,
                "url": "https://github.com/o/r/pull/240",
                "opened": "Mon 18 Aug",
                "stalled": False,
            }
        ]
        body = "\n".join(progress.daily_lines(payload(in_flight=flight)))
        assert "In flight" in body and "Next up" not in body
        assert "Next up" in "\n".join(progress.daily_lines(payload()))


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
                "number": progress.WAVE6_PR,
                "title": "move doc-quality scoring behind the go sidecar",
                "html_url": "https://github.com/o/r/pull/224",
                "merged_at": "2026-08-20T10:00:00Z",
                "head": {"ref": "go-docs-score"},
                "labels": [{"name": "cowork"}, {"name": progress.LABEL}],
            },
        )
        facts = progress.merged_pr_facts(progress.WAVE6_PR)
        assert facts is not None and facts["wave"] == 6

    def test_the_wave_number_falls_back_to_the_branch(self, monkeypatch):
        monkeypatch.setattr(
            progress,
            "_pr",
            lambda number: {
                "merged": True,
                "title": "an off-convention title",
                "html_url": "https://github.com/o/r/pull/300",
                "merged_at": "2026-09-01T10:00:00Z",
                "head": {"ref": "cowork/migration-w9"},
                "labels": [{"name": progress.LABEL}],
            },
        )
        facts = progress.merged_pr_facts(300)
        assert facts is not None and facts["wave"] == 9

    def test_a_labelled_maintenance_merge_is_refused(self, monkeypatch):
        # Merged, labelled — and not a wave. The 🌊 post must never fire for a
        # PR whose parity checks were skipped by design.
        monkeypatch.setattr(
            progress,
            "_pr",
            lambda number: {
                "merged": True,
                "title": "fix the renderer's stalled arithmetic",
                "html_url": "https://github.com/o/r/pull/301",
                "head": {"ref": "cowork/go-migration-renderer-fix"},
                "labels": [{"name": "cowork"}, {"name": progress.LABEL}],
            },
        )
        assert progress.merged_pr_facts(301) is None

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

    NOW = __import__("datetime").datetime(2026, 8, 25, 9, 0, tzinfo=__import__("datetime").timezone.utc)

    def _stub(self, monkeypatch, tmp_path, *, wave6=True, prs=None, landed=()):
        doc = tmp_path / "program.md"
        doc.write_text(PROGRAM_TEXT, encoding="utf-8")
        monkeypatch.setattr(progress, "PROGRAM_DOC", doc)
        monkeypatch.setattr(progress, "_wave6_merged", lambda: wave6)
        monkeypatch.setattr(progress, "_open_wave_prs", lambda: prs)
        monkeypatch.setattr(progress, "_landed_waves", lambda moment: None if landed is None else list(landed))
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

    def test_a_failed_landed_read_is_blind(self, monkeypatch, tmp_path):
        # The third GitHub read, and the same rule as the other two: a lane
        # reported quiet when it could not be asked is worse than one that says
        # it could not look.
        self._stub(monkeypatch, tmp_path, prs=[], landed=None)
        built = progress.build_payload(now=self.NOW)
        assert built["blind"] is True
        assert built["landed"] is None

    def test_a_landed_wave_is_joined_to_its_program_row(self, monkeypatch, tmp_path):
        # The GitHub read knows the PR; the §3 table knows what it was for and
        # how it was proved. The join is what lets the post speak plainly.
        landed = [{"title": "migration(w7): x", "number": 231, "url": "u", "wave": "W7", "merged_on": "Mon 24 Aug"}]
        self._stub(monkeypatch, tmp_path, prs=[], landed=landed)
        built = progress.build_payload(now=self.NOW)
        assert built["landed"][0]["contents"] == "Retro/poker export builders"
        assert built["landed"][0]["gate"] == "existing parity harness"

    def test_a_landed_wave_with_no_matching_row_degrades(self, monkeypatch, tmp_path):
        # W99 is not in the table. The post should still name the PR rather
        # than raise on a lookup that a hand-edited table can always miss.
        landed = [{"title": "migration(w99): x", "number": 9, "url": "u", "wave": "W99", "merged_on": "Mon 24 Aug"}]
        self._stub(monkeypatch, tmp_path, prs=[], landed=landed)
        built = progress.build_payload(now=self.NOW)
        assert built["landed"][0]["gate"] == ""
        assert "migration(w99)" in "\n".join(progress.daily_lines(built))


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

    def test_open_wave_prs_filter_label_and_wave_both(self, monkeypatch):
        # The label alone is not a wave: a renderer bugfix under the label must
        # not render under 🚧 In flight (nor age into "stalled — see the wave's
        # Linear ticket" for a ticket that does not exist).
        monkeypatch.setattr(progress.transport, "resolve_slug", lambda root: "o/r")
        wave_label = [{"name": progress.LABEL}]
        rows = [
            {"number": 1, "labels": wave_label, "head": {"ref": "cowork/migration-w7"}},
            {"number": 2, "labels": wave_label, "head": {"ref": "cowork/go-migration-renderer-fix"}},
            {"number": 3, "labels": [{"name": "cowork"}], "head": {"ref": "cowork/migration-w8"}},
            {"number": progress.WAVE6_PR, "labels": wave_label, "head": {"ref": "go-docs-score"}},
        ]
        monkeypatch.setattr(progress, "_get", lambda path: rows)
        found = progress._open_wave_prs()
        assert [item["number"] for item in found] == [1, progress.WAVE6_PR]
        monkeypatch.setattr(progress, "_get", lambda path: None)
        assert progress._open_wave_prs() is None

    def test_landed_waves_wants_merged_labelled_waves_inside_the_window(self, monkeypatch):
        import datetime as dt

        now = dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc)
        monkeypatch.setattr(progress.transport, "resolve_slug", lambda root: "o/r")
        wave_label = [{"name": progress.LABEL}]
        rows = [
            # in the window, labelled, a wave → counted
            {
                "number": 1,
                "title": "migration(w7): x",
                "labels": wave_label,
                "head": {"ref": "cowork/migration-w7"},
                "merged_at": "2026-08-25T08:00:00Z",
                "html_url": "u1",
            },
            # closed but never merged — a human abandoning a branch is not a ship
            {
                "number": 2,
                "title": "migration(w8): x",
                "labels": wave_label,
                "head": {"ref": "cowork/migration-w8"},
                "merged_at": None,
                "html_url": "u2",
            },
            # merged three days ago — outside the window
            {
                "number": 3,
                "title": "migration(w9): x",
                "labels": wave_label,
                "head": {"ref": "cowork/migration-w9"},
                "merged_at": "2026-08-22T08:00:00Z",
                "html_url": "u3",
            },
            # labelled but not a wave — a renderer bugfix must not read as one
            {
                "number": 4,
                "title": "fix renderer",
                "labels": wave_label,
                "head": {"ref": "cowork/migration-fix"},
                "merged_at": "2026-08-25T08:30:00Z",
                "html_url": "u4",
            },
        ]
        monkeypatch.setattr(progress, "_get", lambda path: rows)
        landed = progress._landed_waves(now)
        assert [item["number"] for item in landed] == [1]
        assert landed[0]["wave"] == "W7"

    def test_landed_waves_asks_for_the_integration_branch(self, monkeypatch):
        # Scoped by base: for the whole program `main` gains nothing, so asking
        # about main would report an idle lane every single day.
        import datetime as dt

        seen = {}
        monkeypatch.setattr(progress.transport, "resolve_slug", lambda root: "o/r")
        monkeypatch.setattr(progress, "_get", lambda path: seen.setdefault("path", path) and [])
        progress._landed_waves(dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc))
        assert "base=chore%2Fgo-migration" in seen["path"]

    def test_landed_waves_is_none_when_the_read_fails(self, monkeypatch):
        import datetime as dt

        monkeypatch.setattr(progress.transport, "resolve_slug", lambda root: "o/r")
        monkeypatch.setattr(progress, "_get", lambda path: None)
        assert progress._landed_waves(dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc)) is None

    def test_a_full_page_is_blindness_not_an_empty_queue(self, monkeypatch):
        # The page bound is the repo's open-PR count, not the lane's: past 100
        # open PRs the wave PR falls off page 1 and would render as "nothing in
        # flight" with the blind marker suppressed — a guess, not a fact.
        monkeypatch.setattr(progress.transport, "resolve_slug", lambda root: "o/r")
        full_page = [{"number": n, "labels": [], "head": {"ref": "x"}} for n in range(100)]
        monkeypatch.setattr(progress, "_get", lambda path: full_page)
        assert progress._open_wave_prs() is None

    def test_a_wave_needs_a_digit_after_the_prefix(self):
        assert progress._is_wave({"number": 1, "head": {"ref": "cowork/migration-w18b"}})
        assert not progress._is_wave({"number": 1, "head": {"ref": "cowork/migration-workflow-fix"}})


class TestMain:
    def test_weekly_prints_payload_and_lines(self, monkeypatch, capsys):
        monkeypatch.setattr(progress, "build_payload", lambda: payload())
        assert progress.main(["--weekly"]) == 0
        printed = __import__("json").loads(capsys.readouterr().out)
        assert printed["lines"][0].startswith("🐹 **Go Migration** — ")

    def test_daily_prints_payload_and_lines(self, monkeypatch, capsys):
        monkeypatch.setattr(progress, "build_payload", lambda: payload())
        assert progress.main(["--daily"]) == 0
        printed = __import__("json").loads(capsys.readouterr().out)
        assert printed["lines"][0].startswith("🐹 **Go Migration** — ")
        assert any("How to test" in line for line in printed["lines"])

    def test_the_three_modes_are_mutually_exclusive(self, monkeypatch):
        # A routine passing two modes should fail loudly rather than silently
        # posting whichever branch happens to be checked first.
        import pytest

        monkeypatch.setattr(progress, "build_payload", lambda: payload())
        with pytest.raises(SystemExit):
            progress.main(["--daily", "--weekly"])

    def test_wave_merged_needs_a_pr(self, monkeypatch, capsys):
        monkeypatch.setattr(progress, "build_payload", lambda: payload())
        assert progress.main(["--wave-merged"]) == 2

    def test_a_non_wave_pr_exits_nonzero_and_posts_nothing(self, monkeypatch, capsys):
        monkeypatch.setattr(progress, "build_payload", lambda: payload())
        monkeypatch.setattr(progress, "merged_pr_facts", lambda number: None)
        assert progress.main(["--wave-merged", "--pr", "300"]) == 1
        assert capsys.readouterr().out == ""
