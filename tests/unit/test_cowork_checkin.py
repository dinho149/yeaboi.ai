"""Tests for scripts/cowork_checkin.py — the check-in every routine closes with.

The message is measured, not written: `cowork/check-in.md` tells a routine to post
what the script prints and change nothing. That only holds if the script is right
about three things nobody can check by eye at 06:00 — the arithmetic over a
transcript, the glyphs, and what happens when a fact is missing.

``TestGlyphs`` is the one that would fail silently in production: a ✅ in a
check-in reads as a human's approval verb, and `cron/slack-relay.md` is built
around those two codepoints meaning exactly one thing.
"""

from __future__ import annotations

import importlib.util
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = ROOT / "scripts" / "cowork_checkin.py"
_spec = importlib.util.spec_from_file_location("cowork_checkin", _MODULE_PATH)
checkin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checkin)


def _transcript(tmp_path: Path, records: list[dict], name: str = "s1") -> Path:
    root = tmp_path / "projects"
    (root / "-home-user-yeaboi-ai").mkdir(parents=True, exist_ok=True)
    path = root / "-home-user-yeaboi-ai" / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return root


def _assistant(
    request_id: str,
    *,
    model: str = "claude-opus-5",
    out: int = 100,
    stamp: str = "2026-08-14T06:01:00Z",
) -> dict:
    return {
        "type": "assistant",
        "sessionId": "s1",
        "requestId": request_id,
        "timestamp": stamp,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": 10,
                "output_tokens": out,
                "cache_read_input_tokens": 1_000,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 500},
            },
        },
    }


def _opened(stamp: str = "2026-08-14T06:00:00Z") -> dict:
    return {
        "type": "user",
        "sessionId": "s1",
        "cwd": "/home/user/yeaboi.ai",
        "timestamp": stamp,
        "message": {"role": "user", "content": "go"},
    }


class TestUsage:
    def test_sums_one_run_and_dedupes_repeated_requests(self, tmp_path):
        """One API message spans several transcript lines repeating identical usage.

        `agentwatch.collector` dedupes on requestId; a check-in that did not would
        report a number the product's own `yeaboi agents cost` disagrees with.
        """
        root = _transcript(tmp_path, [_opened(), _assistant("r1"), _assistant("r1"), _assistant("r2")])
        usage = checkin.usage_report(root)
        assert usage["available"] is True
        assert usage["tokens"]["output"] == 200, "the repeated r1 must count once"
        assert usage["tokens"]["cache_write_1h"] == 1_000
        assert usage["total_tokens"] == 10 * 2 + 200 + 1_000 * 2 + 500 * 2

    def test_counts_every_transcript_in_the_tree(self, tmp_path):
        """A routine sandbox is fresh per firing, so every file in it is this run's.

        That is what lets the total be right without the session knowing its own id
        — which it has no way to learn, `RemoteTrigger` being absent from the
        runtime. A subagent's turns count whether they land in this file or beside it.
        """
        root = _transcript(tmp_path, [_opened(), _assistant("r1")])
        _transcript(tmp_path, [_opened(), _assistant("r2")], name="subagent")
        assert checkin.usage_report(root)["tokens"]["output"] == 200

    def test_duration_spans_first_start_to_last_end(self, tmp_path):
        root = _transcript(
            tmp_path,
            [_opened(), _assistant("r1", stamp="2026-08-14T06:04:16Z")],
        )
        assert checkin.usage_report(root)["duration_seconds"] == 256

    def test_missing_directory_degrades_with_a_reason(self, tmp_path):
        """A run that cannot price itself still has to report that it happened."""
        usage = checkin.usage_report(tmp_path / "nothing-here")
        assert usage["available"] is False
        assert "no transcript directory" in usage["reason"]

    def test_unknown_model_is_declared(self, tmp_path):
        """A fallback-priced model must be visible, not folded into a clean figure."""
        root = _transcript(tmp_path, [_opened(), _assistant("r1", model="some-unreleased-model")])
        assert checkin.usage_report(root)["known_models"] is False


class TestLine:
    def test_the_shape(self, tmp_path):
        root = _transcript(tmp_path, [_opened(), _assistant("r1", stamp="2026-08-14T06:04:16Z")])
        line = checkin.check_in_line(
            {"name": "security-sweep", "status": "ok", "note": "1 PR (#261), 2 filed", "url": "https://x/y"},
            checkin.usage_report(root),
        )
        head, spend = line.split("\n")
        assert head.startswith("`")
        assert "**security-sweep**" in head
        assert "🟢" in head
        assert "4m" in head
        assert "1 PR (#261), 2 filed" in head
        assert spend.startswith("~") and "tok ≈ $" in spend
        assert spend.endswith("· [log](https://x/y)")

    def test_local_time_matches_the_agenda(self, tmp_path):
        """The reply is read against the 📅 line it closes out, so 06:00 UTC must
        render the way `--agenda` renders it, or the two are two different runs."""
        root = _transcript(tmp_path, [_opened(), _assistant("r1")])
        line = checkin.check_in_line({"name": "x", "status": "ok"}, checkin.usage_report(root))
        assert line.startswith("`07:00`"), "Europe/London in August is UTC+1"

    def test_unmeasured_usage_says_so_rather_than_reporting_zero(self, tmp_path):
        line = checkin.check_in_line({"name": "x", "status": "failed"}, checkin.usage_report(tmp_path / "gone"))
        assert "usage unmeasured" in line
        assert "$0.00" not in line, "an unmeasured run must not read as a free one"

    def test_no_url_leaves_no_dangling_link(self, tmp_path, monkeypatch):
        monkeypatch.delenv(checkin.RUN_SESSION_ENV, raising=False)
        line = checkin.check_in_line({"name": "x", "status": "ok"}, checkin.usage_report(tmp_path / "gone"))
        assert "[log]" not in line

    def test_a_missing_name_is_refused(self):
        with pytest.raises(ValueError, match="name"):
            checkin.check_in_line({"status": "ok"}, {"available": False})

    def test_an_unknown_status_is_refused(self):
        """Three statuses, and an invented fourth must fail loudly rather than
        render a check-in with no glyph at all."""
        with pytest.raises(ValueError, match="status"):
            checkin.check_in_line({"name": "x", "status": "fine"}, {"available": False})


class TestGlyphs:
    def test_status_glyphs_are_not_the_approval_verbs(self):
        """✅/❌ are how a human approves, and 🤖 is the relay's handled-marker.

        A check-in wearing one invites somebody to answer a heartbeat, or hides a
        digest item from every future relay run.
        """
        assert set(checkin.STATUS_GLYPH.values()).isdisjoint(set(checkin.RESERVED_GLYPHS))

    def test_status_glyphs_carry_no_variation_selector(self):
        """A trailing U+FE0F is a glyph that renders two ways across clients.

        Pinned by length rather than by trusting the comment beside them, exactly
        as `TestAgenda` pins `SECTION_EMOJI`.
        """
        for glyph in checkin.STATUS_GLYPH.values():
            assert len(glyph) == 1, f"{glyph!r} is a multi-codepoint sequence"
            assert "️" not in glyph

    @pytest.mark.parametrize("glyph", ["✅", "❌", "🤖"])
    def test_a_reserved_glyph_cannot_reach_a_note(self, glyph):
        assert glyph not in checkin.clean_note(f"done {glyph} all good")

    def test_a_note_is_one_line_and_bounded(self):
        """Twenty of these stack in one thread; a note that wraps four times, or
        carries a newline, breaks the two-line shape the reader scans by."""
        assert "\n" not in checkin.clean_note("first\nsecond")
        assert len(checkin.clean_note("x" * 500)) <= checkin.NOTE_LIMIT


class TestRunUrl:
    def test_builds_this_run_s_link_from_the_environment(self):
        """Probed 2026-08-14: the runtime exports the same `cse_…` id that
        `RemoteTrigger list_runs` reports and links — see
        tests/fixtures/cowork_run_self_live.json."""
        assert checkin.run_url("cse_01DBM5LwdWwgpUydtanGHuAt") == (
            "https://claude.ai/code/session_01DBM5LwdWwgpUydtanGHuAt"
        )

    @pytest.mark.parametrize("value", ["", "cse_", "session_01ABC", "01DBM5Lw", "  "])
    def test_an_unfamiliar_id_yields_no_link_rather_than_a_guess(self, value):
        """A check-in with no link says so; one with a link that 404s is worse."""
        assert checkin.run_url(value) == ""

    def test_the_fixture_agrees_with_the_builder(self):
        """The recorded probe is the only evidence the env var is the run id. If it
        and the builder drift, the link silently points at nothing."""
        recorded = json.loads((ROOT / "tests" / "fixtures" / "cowork_run_self_live.json").read_text())
        session = recorded["session"]
        assert checkin.run_url(session["run_session_id"]) == session["run_url"]
        assert any(item["name"] == checkin.RUN_SESSION_ENV for item in recorded["env"])


class TestFormatting:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [(0, "0"), (940, "940"), (1_000, "1k"), (128_400, "128k"), (1_240_000, "1.2M")],
    )
    def test_compact(self, count, expected):
        assert checkin.compact(count) == expected

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(0, "0s"), (48, "48s"), (60, "1m"), (256, "4m"), (3_600, "1h"), (4_320, "1h 12m")],
    )
    def test_duration(self, seconds, expected):
        assert checkin.duration(seconds) == expected


class TestTheProbeAgreesWithTheReader:
    """`scripts/probe_run_self.py` re-implements the transcript walk on purpose —
    it has to run in a checkout with nothing installed, so it cannot import the
    reader whose shape it is there to verify. That duplication is only safe while
    the two agree, and its comment says so without checking it.

    This is the check. If `agentwatch.collector` changes how it dedupes or how it
    splits 5m from 1h cache writes, the probe's recorded fixture stops describing
    what the check-in actually measures, and the evidence the feature rests on
    quietly becomes wrong.
    """

    def test_the_two_walks_total_the_same_transcripts(self, tmp_path):
        import importlib.util

        spec = importlib.util.spec_from_file_location("probe_run_self", ROOT / "scripts" / "probe_run_self.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)

        root = _transcript(
            tmp_path,
            [_opened(), _assistant("r1"), _assistant("r1"), _assistant("r2", out=250)],
        )
        _transcript(tmp_path, [_opened(), _assistant("r3", out=7)], name="subagent")

        measured = checkin.usage_report(root)["tokens"]
        walked = probe.transcript_report(root)["totals"]

        assert walked["input_tokens"] == measured["input"]
        assert walked["output_tokens"] == measured["output"]
        assert walked["cache_read_input_tokens"] == measured["cache_read"]
        assert walked["cache_creation_5m"] == measured["cache_write_5m"]
        assert walked["cache_creation_1h"] == measured["cache_write_1h"]


class TestTheLedger:
    """``--record``: the half that makes a measured run survive the run.

    Everything above this class measures one run and prints it. Until now that
    was the end of it — two lines to Slack and the rest dropped, with no way to
    pull it back afterwards because ``agentwatch.collector`` reads a filesystem
    and a routine's sandbox dies with the container.
    """

    def _transport(self, monkeypatch, calls: list, issues: list | None = None):
        monkeypatch.setenv(checkin.RUN_SESSION_ENV, "cse_test")
        """Stub both REST verbs. Never reaches ``transport._urlopen``, which
        ``tests/conftest.py`` blocks anyway — belt and braces, because a test that
        asserts on writes is exactly the test that makes them when a seam moves."""
        monkeypatch.setattr(checkin.transport, "resolve_slug", lambda *a, **k: "o/r")
        monkeypatch.setattr(
            checkin.transport, "api_paged", lambda path, key=None: checkin.transport.ApiResult(True, issues or [])
        )

        def _api(method, path, body=None):
            calls.append((method, path, body))
            return checkin.transport.ApiResult(True, {"number": 42})

        monkeypatch.setattr(checkin.transport, "api", _api)

    def test_recording_outside_a_routine_refuses_rather_than_writing_the_machine(self, monkeypatch):
        """The trap the first live run walked into.

        ``usage_report`` reads all of ``~/.claude/projects``. In a routine sandbox
        that is one run; on a laptop it is everything you have ever done. The
        first real ``--record`` wrote a row claiming one check-in took 800 hours
        and cost $8,699 — which is not a run's cost measured badly, it is a
        different quantity, so there is nothing to clamp and the write is refused.
        """
        monkeypatch.delenv(checkin.RUN_SESSION_ENV, raising=False)
        monkeypatch.setattr(
            checkin.transport, "resolve_slug", lambda *a, **k: pytest.fail("must not reach the network")
        )
        wrote, error = checkin.record({"name": "x", "status": "ok"}, {"cost_usd": 8699.74})
        assert wrote is False
        assert "not a routine run" in error

    def test_a_run_becomes_one_comment_on_the_months_issue(self, monkeypatch):
        calls: list = []
        self._transport(monkeypatch, calls, issues=[{"number": 7, "title": "fleet ledger 2026-08"}])
        wrote, error = checkin.record(
            {"name": "security-sweep", "status": "ok", "note": "1 PR"},
            {"cost_usd": 0.98, "total_tokens": 263000, "duration_seconds": 244, "available": True},
            now=datetime(2026, 8, 14, tzinfo=UTC),
        )
        assert (wrote, error) == (True, "")
        assert [c[:2] for c in calls] == [("POST", "/repos/o/r/issues/7/comments")]
        assert "security-sweep" in calls[0][2]["body"]

    def test_the_months_issue_is_created_when_it_is_the_months_first_run(self, monkeypatch):
        # `day-ahead` opens it at 05:45, but four routines fire before then —
        # `cd-deploy` at 04:00 and the three event routines — so on the first of
        # the month they would otherwise have nowhere to write.
        calls: list = []
        self._transport(monkeypatch, calls, issues=[])
        assert checkin.record({"name": "cd-deploy", "status": "ok"}, {}, now=datetime(2026, 9, 1, tzinfo=UTC))[0]
        assert calls[0][0:2] == ("POST", "/repos/o/r/issues")
        assert calls[0][2]["title"] == "fleet ledger 2026-09"
        assert calls[1][1] == "/repos/o/r/issues/42/comments"

    def test_the_ledger_issue_carries_no_label_any_other_query_looks_for(self, monkeypatch):
        # `digest.md` closes an unanswered `cowork:proposal` after fourteen days,
        # and `codeql-triage.yml` reads `--label cowork --state all --limit 500` as
        # its dedupe corpus. Either would swallow this issue whole.
        calls: list = []
        self._transport(monkeypatch, calls, issues=[])
        checkin.record({"name": "x", "status": "ok"}, {}, now=datetime(2026, 9, 1, tzinfo=UTC))
        assert calls[0][2]["labels"] == ["fleet-ledger"]

    def test_a_pull_request_is_never_mistaken_for_the_ledger(self, monkeypatch):
        # `/issues` answers with pull requests too. One titled like the ledger
        # would otherwise collect every run's telemetry as review comments.
        calls: list = []
        self._transport(
            monkeypatch, calls, issues=[{"number": 9, "title": "fleet ledger 2026-08", "pull_request": {"url": "…"}}]
        )
        checkin.record({"name": "x", "status": "ok"}, {}, now=datetime(2026, 8, 2, tzinfo=UTC))
        assert calls[0][0:2] == ("POST", "/repos/o/r/issues")

    def test_an_unreachable_ledger_is_reported_and_never_raises(self, monkeypatch):
        monkeypatch.setenv(checkin.RUN_SESSION_ENV, "cse_test")
        monkeypatch.setattr(checkin.transport, "resolve_slug", lambda *a, **k: None)
        assert checkin.record({"name": "x", "status": "ok"}, {}) == (False, "could not resolve the repository slug")

    def test_a_ledger_failure_never_costs_the_check_in(self, monkeypatch, capsys):
        # The line is what a human is waiting for; the ledger is what a report
        # reads next month. `check-in.md` tells a routine to key off this exit
        # code, so failing the run over the second trades the urgent for the
        # eventual.
        monkeypatch.setattr(checkin, "usage_report", lambda root=None: {})
        monkeypatch.setattr(checkin, "record", lambda *a, **k: (False, "egress refused"))
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"name": "x", "status": "ok", "note": "n"})))
        assert checkin.main(["--line", "--record"]) == 0
        captured = capsys.readouterr()
        assert "**x**" in captured.out
        assert "egress refused" in captured.err

    def test_dry_run_writes_nothing_and_prints_valid_json(self, monkeypatch, capsys):
        monkeypatch.setattr(checkin, "usage_report", lambda root=None: {})
        monkeypatch.setattr(checkin, "record", lambda *a, **k: pytest.fail("--dry-run must not reach the write path"))
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"name": "x", "status": "ok", "note": "n"})))
        assert checkin.main(["--record", "--dry-run"]) == 0
        body = capsys.readouterr().out
        assert checkin.LEDGER_MARKER in body
        assert json.loads(body.split("```json")[1].split("```")[0])["name"] == "x"

    def test_no_routine_reads_the_ledger(self) -> None:
        """The safety property, asserted rather than promised.

        The fleet is stateless because no run's behaviour depends on another
        run's state. Appending to a record nobody consults does not touch that;
        a routine that *read* this would have quietly given the fleet a memory,
        and the failure mode of that is a run whose decision nobody can reproduce.
        """
        for path in (ROOT / "cowork" / "routines").rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            assert "fleet-ledger" not in text, f"{path.name} reads the ledger"
            assert "fleet ledger" not in text, f"{path.name} reads the ledger"
