"""Tests for yeaboi.analysis.cancellation."""

import threading

import pytest

from yeaboi.analysis.cancellation import AnalysisCancelledError, raise_if_cancelled


class TestRaiseIfCancelled:
    def test_none_event_is_a_no_op(self):
        raise_if_cancelled(None)

    def test_unset_event_is_a_no_op(self):
        raise_if_cancelled(threading.Event())

    def test_set_event_raises(self):
        event = threading.Event()
        event.set()
        with pytest.raises(AnalysisCancelledError):
            raise_if_cancelled(event)

    def test_engine_reexports_the_error(self):
        from yeaboi.analysis import engine

        assert engine.AnalysisCancelledError is AnalysisCancelledError
