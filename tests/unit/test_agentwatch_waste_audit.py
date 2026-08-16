"""Tests for src/yeaboi/agentwatch/waste_audit.py — the Read-waste audit."""

import json

from yeaboi.agentwatch import waste_audit

# 600 bytes of payload — comfortably above the MIN_SIZE floor.
BIG_A = "A" * 600
BIG_B = "B" * 1000
BIG_C = "C" * 600
BIG_D = "D" * 600


def _assistant(idx: int, blocks: list, ts: str = "") -> str:
    line = {"message": {"role": "assistant", "content": blocks}}
    if ts:
        line["timestamp"] = ts
    return json.dumps(line)


def _tool_use(tool_id: str, name: str, **inp) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _result(tool_id: str, text: str, ts: str = "") -> str:
    line = {
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}],
        }
    }
    if ts:
        line["timestamp"] = ts
    return json.dumps(line)


def _write_transcript(tmp_path, name: str, lines: list[str]):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestMechanisms:
    def test_identical_repeat_counts_the_second_read_only(self, tmp_path):
        lines = [
            _assistant(1, [_tool_use("t1", "Read", file_path="/f.py")]),
            _result("t1", BIG_A),
            _assistant(2, [_tool_use("t2", "Read", file_path="/f.py")]),
            _result("t2", BIG_A),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.read_calls == 2
        assert report.dedup_identical_calls == 1
        assert report.dedup_identical_bytes == len(BIG_A)

    def test_subset_containment_needs_a_partial_read(self, tmp_path):
        subset = BIG_B[:600]
        lines = [
            _assistant(1, [_tool_use("t1", "Read", file_path="/g.py")]),
            _result("t1", BIG_B),
            _assistant(2, [_tool_use("t2", "Read", file_path="/g.py", offset=1, limit=20)]),
            _result("t2", subset),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.subset_calls == 1
        assert report.subset_bytes == len(subset)

    def test_write_readback(self, tmp_path):
        lines = [
            _assistant(1, [_tool_use("t1", "Write", file_path="/h.py", content=BIG_C + "\ntrailer")]),
            _result("t1", "ok"),
            _assistant(2, [_tool_use("t2", "Read", file_path="/h.py")]),
            _result("t2", BIG_C),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.write_readback_calls == 1
        assert report.write_readback_bytes == len(BIG_C)

    def test_stale_read_flagged_when_the_file_is_edited_later(self, tmp_path):
        lines = [
            _assistant(1, [_tool_use("t1", "Read", file_path="/i.py")]),
            _result("t1", BIG_D),
            _assistant(2, [_tool_use("t2", "Edit", file_path="/i.py")]),
            _result("t2", "edited"),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.stale_calls == 1
        assert report.stale_bytes == len(BIG_D)

    def test_line_number_scaffolding_bytes(self, tmp_path):
        content = "     1\tfoo\n     2\tbar\n" + BIG_A
        lines = [
            _assistant(1, [_tool_use("t1", "Read", file_path="/j.py")]),
            _result("t1", content),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.linenum_overhead_bytes == len("     1\t") + len("     2\t")

    def test_scaffolding_not_double_counted_on_classified_reads(self, tmp_path):
        # Regression: an identical repeat's bytes already include its cat -n
        # prefix; counting the prefix again would double-bill it in the
        # recoverable sum. Only the unclassified first read contributes.
        content = "     1\tfoo\n" + BIG_A
        lines = [
            _assistant(1, [_tool_use("t1", "Read", file_path="/j.py")]),
            _result("t1", content),
            _assistant(2, [_tool_use("t2", "Read", file_path="/j.py")]),
            _result("t2", content),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.dedup_identical_calls == 1
        assert report.linenum_overhead_bytes == len("     1\t")

    def test_small_reads_stay_below_the_floor(self, tmp_path):
        lines = [
            _assistant(1, [_tool_use("t1", "Read", file_path="/k.py")]),
            _result("t1", "tiny"),
            _assistant(2, [_tool_use("t2", "Read", file_path="/k.py")]),
            _result("t2", "tiny"),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.read_calls_small == 2
        # Below MIN_SIZE nothing is classified as waste, even an identical pair.
        assert report.dedup_identical_calls == 0


class TestCacheSignals:
    def test_gaps_and_residency(self, tmp_path):
        lines = [
            _assistant(1, [_tool_use("t1", "Read", file_path="/f.py")], ts="2026-08-07T10:00:00Z"),
            _result("t1", BIG_A, ts="2026-08-07T10:00:01Z"),
            # 10-minute gap (>5m), then a 2-hour gap (>1h, which also counts >5m).
            _assistant(2, [_tool_use("t2", "Bash", command="ls")], ts="2026-08-07T10:10:01Z"),
            _result("t2", "files", ts="2026-08-07T12:10:01Z"),
            _assistant(3, [_tool_use("t3", "Bash", command="pwd")], ts="2026-08-07T12:10:02Z"),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.gaps_over_5m == 2
        assert report.gaps_over_1h == 1
        assert report.sessions_with_gap == 1
        # The Read at assistant turn 1 stays in context for 2 more turns.
        assert report.residency_median == 2

    def test_residency_counts_turns_not_lines(self, tmp_path):
        # Regression: one API response spans several JSONL lines sharing a
        # requestId (the same split collector.py dedupes). Counting lines
        # inflated residency by exactly that split factor.
        def _assistant_req(req: str, blocks: list) -> str:
            return json.dumps({"requestId": req, "message": {"role": "assistant", "content": blocks}})

        lines = [
            _assistant_req("r1", [_tool_use("t1", "Read", file_path="/f.py")]),
            _result("t1", BIG_A),
            # The same API response, continued across two more lines.
            _assistant_req("r2", [{"type": "text", "text": "thinking"}]),
            _assistant_req("r2", [{"type": "text", "text": "more"}]),
            _assistant_req("r2", [_tool_use("t2", "Bash", command="ls")]),
            _result("t2", "files"),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        # Two turns (r1, r2), so the Read from turn 1 has residency 1 — not 3.
        assert report.residency_median == 1

    def test_tool_bytes_attribution(self, tmp_path):
        lines = [
            _assistant(1, [_tool_use("t1", "Read", file_path="/f.py")]),
            _result("t1", BIG_A),
            _assistant(2, [_tool_use("t2", "Bash", command="ls")]),
            _result("t2", "some output"),
        ]
        report = waste_audit.audit_files([_write_transcript(tmp_path, "s.jsonl", lines)])
        assert report.tool_bytes["Read"] == len(BIG_A)
        assert report.tool_bytes["Bash"] == len("some output")
        assert report.read_bytes == len(BIG_A)


class TestRobustness:
    def test_missing_file_counts_skipped_and_does_not_raise(self, tmp_path):
        report = waste_audit.audit_files([tmp_path / "gone.jsonl"])
        assert report.files_skipped == 1
        assert report.sessions == 0

    def test_malformed_lines_are_ignored(self, tmp_path):
        path = _write_transcript(tmp_path, "s.jsonl", ["not json", '"a bare string"', "{}"])
        report = waste_audit.audit_files([path])
        assert report.sessions == 1
        assert report.read_calls == 0

    def test_progress_callback_fires_per_file(self, tmp_path):
        seen = []
        paths = [
            _write_transcript(tmp_path, "a.jsonl", ["{}"]),
            _write_transcript(tmp_path, "b.jsonl", ["{}"]),
        ]
        waste_audit.audit_files(paths, on_file=lambda cur, total: seen.append((cur, total)))
        assert seen == [(1, 2), (2, 2)]
