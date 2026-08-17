"""Tests for the user-global launch budget (ship/budget.py).

The fuse's contract is deny-by-default under doubt: every limit denies with a
named reason, an unreadable ledger denies, and only the explicit env escape
hatch bypasses. The clock is injected via ``_now`` so window arithmetic is
tested deterministically.
"""

from __future__ import annotations

import json
import os

import pytest

from yeaboi.ship import budget


@pytest.fixture()
def budget_dir(tmp_path, monkeypatch):
    """Redirect every budget file into an isolated temp dir."""
    d = tmp_path / "ship"
    d.mkdir()
    monkeypatch.setattr(budget, "SHIP_BUDGET_FILE", d / "ai-budget.json")
    monkeypatch.setattr(budget, "SHIP_BUDGET_LOCK", d / "ai-budget.lock")
    monkeypatch.setattr(budget, "SHIP_BUDGET_RECEIPTS", d / "ai-budget-receipts.jsonl")
    monkeypatch.setattr(budget, "get_ship_dir", lambda: d)
    return d


@pytest.fixture()
def clock(monkeypatch):
    """A settable wall clock for the ledger's window arithmetic."""
    state = [1_700_000_000.0]
    monkeypatch.setattr(budget, "_now", lambda: state[0])
    return state


class TestReserveRelease:
    def test_first_reserve_is_allowed_with_a_permit(self, budget_dir, clock):
        decision = budget.reserve()
        assert decision.allowed
        assert decision.permit_id.startswith("permit_")

    def test_concurrency_denies_while_a_permit_is_active(self, budget_dir, clock):
        first = budget.reserve()
        assert first.allowed
        second = budget.reserve()
        assert not second.allowed
        assert "global-concurrency" in second.reason

    def test_release_frees_the_slot_but_the_launch_stays_counted(self, budget_dir, clock):
        first = budget.reserve()
        budget.release(first.permit_id)
        second = budget.reserve()
        assert second.allowed
        # Two launches within the hour — the default hourly limit is spent.
        budget.release(second.permit_id)
        third = budget.reserve()
        assert not third.allowed
        assert "hourly-budget" in third.reason

    def test_hourly_window_slides(self, budget_dir, clock):
        for _ in range(2):
            budget.release(budget.reserve().permit_id)
        assert not budget.reserve().allowed
        clock[0] += budget.HOUR_S + 1
        assert budget.reserve().allowed

    def test_daily_limit_counts_all_launches_in_24h(self, budget_dir, clock, monkeypatch):
        monkeypatch.setenv("YEABOI_AI_MAX_PER_HOUR", "100")
        for _ in range(12):
            decision = budget.reserve()
            assert decision.allowed
            budget.release(decision.permit_id)
            clock[0] += 60
        denied = budget.reserve()
        assert not denied.allowed
        assert "daily-budget" in denied.reason
        clock[0] += budget.DAY_S
        assert budget.reserve().allowed

    def test_release_of_unknown_permit_is_harmless(self, budget_dir, clock):
        budget.release("permit_nonexistent")
        assert budget.reserve().allowed


class TestCircuitBreaker:
    def test_quota_error_opens_the_circuit(self, budget_dir, clock):
        budget.record_quota_error("HTTP 429 too many requests")
        denied = budget.reserve()
        assert not denied.allowed
        assert "circuit-open" in denied.reason

    def test_circuit_closes_after_the_pause_window(self, budget_dir, clock):
        budget.record_quota_error("rate limit")
        clock[0] += budget.DEFAULT_QUOTA_PAUSE_MINUTES * 60 + 1
        assert budget.reserve().allowed

    def test_quota_pattern_matches_real_quota_errors_only(self):
        assert budget.looks_like_quota_error("Error: HTTP 429")
        assert budget.looks_like_quota_error("rate_limit_error from API")
        assert budget.looks_like_quota_error("You have been rate limited")
        assert budget.looks_like_quota_error("rate limit exceeded")
        assert budget.looks_like_quota_error("overloaded_error")
        assert budget.looks_like_quota_error("usage limit reached")
        assert budget.looks_like_quota_error("quota exhausted")
        assert not budget.looks_like_quota_error("all tests passed")
        assert not budget.looks_like_quota_error("")

    def test_agent_prose_about_rate_limiting_does_not_trip_the_breaker(self):
        # The error text is the agent's stdout tail, which can echo the user's
        # own code — a topic word alone must never open a user-global pause.
        assert not budget.looks_like_quota_error("I refactored the rate limiter in quota.py")
        assert not budget.looks_like_quota_error("added a per-user quota field to the model")


class TestFailClosed:
    def test_unreadable_ledger_denies(self, budget_dir, clock, monkeypatch):
        def _boom(path):
            raise OSError("disk error")

        monkeypatch.setattr(budget, "_read_ledger", _boom)
        denied = budget.reserve()
        assert not denied.allowed
        assert "budget-unavailable" in denied.reason

    def test_symlinked_ledger_denies(self, budget_dir, clock, tmp_path):
        target = tmp_path / "elsewhere.json"
        target.write_text("{}", encoding="utf-8")
        budget.SHIP_BUDGET_FILE.symlink_to(target)
        denied = budget.reserve()
        assert not denied.allowed
        assert "budget-unavailable" in denied.reason

    def test_held_lock_denies_after_the_deadline(self, budget_dir, clock, monkeypatch):
        monkeypatch.setattr(budget, "_LOCK_DEADLINE_S", 0.05)
        budget.SHIP_BUDGET_LOCK.write_text(str(os.getpid()), encoding="utf-8")
        denied = budget.reserve()
        assert not denied.allowed
        assert "budget-unavailable" in denied.reason

    def test_stale_lock_is_taken_over(self, budget_dir, clock):
        budget.SHIP_BUDGET_LOCK.write_text("99999", encoding="utf-8")
        stale = budget.SHIP_BUDGET_LOCK.stat().st_mtime - budget.LOCK_STALE_S - 5
        os.utime(budget.SHIP_BUDGET_LOCK, (stale, stale))
        assert budget.reserve().allowed

    def test_corrupt_ledger_starts_fresh_instead_of_blocking(self, budget_dir, clock):
        budget.SHIP_BUDGET_FILE.write_text("{not json", encoding="utf-8")
        assert budget.reserve().allowed


class TestSelfHealing:
    def test_dead_process_frees_its_reservation(self, budget_dir, clock, monkeypatch):
        first = budget.reserve()
        assert first.allowed
        monkeypatch.setattr(budget, "_is_process_alive", lambda pid: False)
        second = budget.reserve()
        assert second.allowed, second.reason

    def test_stale_reservation_expires_even_with_a_live_process(self, budget_dir, clock):
        assert budget.reserve().allowed
        clock[0] += budget.ACTIVE_STALE_S + 1
        assert budget.reserve().allowed


class TestEscapeHatch:
    def test_disable_env_bypasses_every_limit(self, budget_dir, clock, monkeypatch):
        monkeypatch.setenv("YEABOI_AI_BUDGET_DISABLE", "1")
        budget.record_quota_error("429")
        decision = budget.reserve()
        assert decision.allowed
        assert decision.permit_id.startswith("bypass_")

    def test_bypass_permit_release_touches_nothing(self, budget_dir, clock):
        budget.release("bypass_123")
        assert not budget.SHIP_BUDGET_FILE.exists()


class TestTelemetry:
    def test_receipts_record_launches_and_denials(self, budget_dir, clock):
        budget.reserve()
        budget.reserve()  # denied: concurrency
        lines = [json.loads(line) for line in budget.SHIP_BUDGET_RECEIPTS.read_text(encoding="utf-8").splitlines()]
        events = [entry["event"] for entry in lines]
        assert "launch" in events
        assert "deny" in events

    def test_receipt_failure_never_affects_enforcement(self, budget_dir, clock, monkeypatch):
        def _boom(*args, **kwargs):
            raise OSError("disk full")

        # The ledger itself uses Path methods; only the receipt append opens a
        # file handle, so this breaks telemetry and nothing else.
        monkeypatch.setattr(budget, "open", _boom, raising=False)
        assert budget.reserve().allowed

    def test_status_reports_counts_and_pause(self, budget_dir, clock):
        budget.reserve()
        snapshot = budget.status()
        assert snapshot.active == 1
        assert snapshot.launched_last_hour == 1
        assert snapshot.paused_until == 0.0
        budget.record_quota_error("quota")
        paused = budget.status()
        assert paused.paused_until > 0
        assert paused.paused_reason == "quota"


class TestLimitsEnv:
    def test_env_overrides_are_read(self, monkeypatch):
        monkeypatch.setenv("YEABOI_AI_MAX_CONCURRENT", "3")
        monkeypatch.setenv("YEABOI_AI_MAX_PER_HOUR", "7")
        assert budget.limits()[:2] == (3, 7)

    def test_garbage_and_negative_env_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("YEABOI_AI_MAX_CONCURRENT", "banana")
        monkeypatch.setenv("YEABOI_AI_MAX_PER_HOUR", "-1")
        assert budget.limits()[:2] == (budget.DEFAULT_MAX_CONCURRENT, budget.DEFAULT_MAX_PER_HOUR)

    def test_zero_is_honoured_as_deny_everything(self, budget_dir, clock, monkeypatch):
        # Fail-closed: 0 is the user saying "no launches", never "use the default".
        monkeypatch.setenv("YEABOI_AI_MAX_CONCURRENT", "0")
        denied = budget.reserve()
        assert not denied.allowed
        assert "global-concurrency" in denied.reason


class TestHeartbeat:
    def test_heartbeat_keeps_a_long_run_alive(self, budget_dir, clock):
        first = budget.reserve()
        assert first.allowed
        clock[0] += budget.ACTIVE_STALE_S - 60
        budget.heartbeat(first.permit_id)
        clock[0] += budget.ACTIVE_STALE_S - 60
        # Without the heartbeat the permit would have been reaped by now and
        # this second reserve would (wrongly) be allowed.
        second = budget.reserve()
        assert not second.allowed
        assert "global-concurrency" in second.reason

    def test_heartbeat_on_unknown_or_bypass_permit_is_harmless(self, budget_dir, clock):
        budget.heartbeat("permit_nonexistent")
        budget.heartbeat("bypass_123")
        assert not budget.SHIP_BUDGET_FILE.exists() or budget.reserve().allowed
