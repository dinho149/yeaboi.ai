"""Tests for src/yeaboi/analysis/progress.py — structured progress events."""

import pytest

from yeaboi.analysis.progress import (
    append_component_progress,
    format_analysis_progress,
    is_component_progress,
    send_component_progress,
)


class TestAppend:
    def test_appends_a_well_formed_event(self):
        events: list = []
        append_component_progress(
            events, component_id="scan", label="Scanning", status="running", current=3, total=10, unit="files"
        )
        assert len(events) == 1
        assert is_component_progress(events[0])
        assert events[0]["current"] == 3
        assert events[0]["total"] == 10
        assert events[0]["unit"] == "files"

    def test_none_list_is_a_no_op(self):
        append_component_progress(None, component_id="x", label="X", status="running")

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError, match="unknown analysis progress status"):
            append_component_progress([], component_id="x", label="X", status="doing-stuff")


class TestSend:
    def test_none_callback_is_a_no_op(self):
        send_component_progress(None, component_id="scan", label="Scanning", status="running")

    def test_delivers_one_validated_event(self):
        received: list = []
        send_component_progress(
            received.append,
            component_id="scan",
            label="Scanning",
            status="completed",
            detail="3 parsed",
            current=10,
            total=10,
        )
        assert len(received) == 1
        assert is_component_progress(received[0])
        assert received[0]["detail"] == "3 parsed"
        assert received[0]["status"] == "completed"

    def test_unknown_status_raises_before_delivery(self):
        received: list = []
        with pytest.raises(ValueError):
            send_component_progress(received.append, component_id="x", label="X", status="nope")
        assert received == []


class TestIsComponentProgress:
    def test_rejects_strings_and_partial_dicts(self):
        assert not is_component_progress("reading foo.jsonl")
        assert not is_component_progress({"kind": "analysis_component"})
        assert not is_component_progress({"component_id": "x", "label": "X", "status": "running"})

    def test_format_falls_back_to_str_for_legacy_items(self):
        assert format_analysis_progress("plain status") == "plain status"
