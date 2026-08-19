"""Tests for the ceremony catalog (ceremonies/catalog.py).

The catalog is the admission test for the whole feature — every surface asks it
whether a mode may be scheduled — so the checks here are mostly about it being
*honest*: the engines and renderers it names really exist, the modes it refuses
carry a reason, and the kwargs it builds are the ones the engine takes.
"""

from __future__ import annotations

import inspect

import pytest

from yeaboi.ceremonies import catalog


class TestCatalogIsHonest:
    @pytest.mark.parametrize("mode", catalog.CATALOG, ids=lambda m: m.key)
    def test_the_engine_it_names_exists_and_takes_what_it_declares(self, mode):
        engine = catalog.engine_callable(mode)
        assert callable(engine)
        # Every declared param, every fixed flag and the session keyword must be
        # real parameters of that engine. A catalog naming a keyword the engine
        # dropped fails here, not at 06:00 on somebody's laptop.
        accepted = set(inspect.signature(engine).parameters)
        for param in mode.params:
            assert param.name in accepted, f"{mode.key}: engine has no {param.name!r}"
        for flag, _value in mode.fixed_flags:
            assert flag in accepted, f"{mode.key}: engine has no {flag!r}"
        if mode.session_param:
            assert mode.session_param in accepted

    @pytest.mark.parametrize("mode", catalog.CATALOG, ids=lambda m: m.key)
    def test_the_renderer_it_names_exists_and_takes_one_artifact(self, mode):
        renderer = catalog.renderer_callable(mode)
        assert callable(renderer)
        assert len(inspect.signature(renderer).parameters) == 1

    @pytest.mark.parametrize("mode", catalog.CATALOG, ids=lambda m: m.key)
    def test_every_param_kind_is_one_we_can_coerce(self, mode):
        for param in mode.params:
            assert param.kind in catalog.PARAM_KINDS

    def test_a_mode_is_either_schedulable_or_refused_never_both(self):
        keys = {mode.key for mode in catalog.CATALOG}
        assert not (keys & set(catalog.UNSCHEDULABLE)), "a mode cannot be both catalogued and refused"

    def test_names_are_unique(self):
        keys = [mode.key for mode in catalog.CATALOG]
        assert len(keys) == len(set(keys))


class TestLookupAndRefusal:
    def test_lookup_is_case_and_space_insensitive(self):
        assert catalog.lookup("  STANDUP ") is catalog.lookup("standup")

    def test_a_catalogued_mode_has_no_refusal(self):
        assert catalog.refuse_reason("standup") == ""

    def test_a_refused_mode_says_why(self):
        reason = catalog.refuse_reason("retro")
        assert "live board" in reason

    def test_an_unknown_mode_lists_the_alternatives(self):
        reason = catalog.refuse_reason("brainstorm")
        assert "unknown ceremony mode" in reason
        assert "standup" in reason


class TestEngineKwargs:
    def _mode(self, key: str):
        mode = catalog.lookup(key)
        assert mode is not None
        return mode

    def test_declared_ints_are_coerced(self):
        kwargs = catalog.engine_kwargs(self._mode("agents-usage"), (("window_days", "7"),))
        assert kwargs["window_days"] == 7

    def test_declared_bools_are_coerced(self):
        mode = self._mode("agents-security")
        assert catalog.engine_kwargs(mode, (("deep", "true"),))["deep"] is True
        assert catalog.engine_kwargs(mode, (("deep", "no"),))["deep"] is False

    def test_a_blank_value_leaves_the_engine_default_alone(self):
        # "" is not 0: days=0 is an empty window, days absent is the working-day
        # window the standup actually wants.
        kwargs = catalog.engine_kwargs(self._mode("standup"), (("days", ""),), session_id="s1")
        assert "days" not in kwargs

    def test_an_unparseable_value_falls_back_rather_than_raising(self):
        # A ceremony that refuses to run is a silence nobody notices; the
        # engine's own default is always a safe answer.
        kwargs = catalog.engine_kwargs(self._mode("agents-usage"), (("window_days", "lots"),))
        assert "window_days" not in kwargs

    def test_an_unknown_arg_is_dropped_not_passed(self):
        kwargs = catalog.engine_kwargs(self._mode("agents-usage"), (("nonsense", "1"),))
        assert "nonsense" not in kwargs

    def test_the_session_is_injected_only_where_the_engine_takes_one(self):
        assert catalog.engine_kwargs(self._mode("standup"), (), session_id="s1")["session_id"] == "s1"
        assert "session_id" not in catalog.engine_kwargs(self._mode("agents-usage"), (), session_id="s1")

    def test_the_standup_engine_is_told_not_to_deliver(self):
        # The trap this exists for: run_standup can deliver to the session's own
        # saved channels, and a ceremony that let it would post the standup
        # twice — once from the engine, once from the ceremony's channels.
        kwargs = catalog.engine_kwargs(self._mode("standup"), (), session_id="s1")
        assert kwargs["deliver"] is False

    def test_a_fixed_flag_cannot_be_overridden_by_a_declared_arg(self):
        kwargs = catalog.engine_kwargs(self._mode("standup"), (("deliver", "true"),), session_id="s1")
        assert kwargs["deliver"] is False
