"""Driver tests for the option-list schedule wizard (hub → Set up a schedule)."""

import yeaboi.ui.mode_select as ms
from yeaboi.standup.store import StandupStore


class _Console:
    size = (100, 30)

    def print(self, *a, **k):
        pass


class _Live:
    def update(self, *a, **k):
        pass


def _drive(keys, tmp_path, monkeypatch, *, session_id="s1", config=None, read_line=None):
    """Run the wizard headlessly with a scripted key sequence.

    Returns (message, saved_config, install_calls, remove_calls).
    """
    import yeaboi.standup.scheduler as scheduler

    db = tmp_path / "sessions.db"
    if config:
        with StandupStore(db) as store:
            store.save_config(session_id, **config)
    monkeypatch.setattr(ms, "_ana_dbp", db)

    install_calls, remove_calls = [], []
    monkeypatch.setattr(scheduler, "install_schedule", lambda *a, **k: install_calls.append(a) or "installed ok")
    monkeypatch.setattr(scheduler, "remove_schedule", lambda *a, **k: remove_calls.append(a) or "removed ok")
    if read_line is not None:
        values = iter(read_line)
        prompts = []

        def _fake_read_line(*a, **k):
            prompts.append(k.get("prompt", ""))
            return next(values, None)

        monkeypatch.setattr(ms, "_standup_read_line", _fake_read_line)
        _fake_read_line.prompts = prompts

    key_iter = iter(keys)

    def read_key(timeout=None):
        return next(key_iter, "esc")  # run out of keys → Esc back out (never hang)

    msg = ms._run_standup_schedule_wizard(_Console(), _Live(), read_key, 0.05, True, session_id)
    with StandupStore(db) as store:
        saved = store.load_config(session_id)
    return msg, saved, install_calls, remove_calls


class TestScheduleWizard:
    def test_happy_path_defaults_and_enable(self, tmp_path, monkeypatch):
        # Enter through time (10:00) / lead (10) / days (Mon–Fri) / channels
        # (terminal), then Up+Enter picks "On" on the enable step.
        msg, saved, install_calls, remove_calls = _drive(
            ["enter", "enter", "enter", "enter", "up", "enter"], tmp_path, monkeypatch
        )
        assert msg == "installed ok"
        assert install_calls == [("s1", "10:00", "1-5", 10)]
        assert not remove_calls
        assert saved["enabled"] is True
        assert saved["time"] == "10:00"
        assert saved["lead_minutes"] == 10
        assert saved["weekdays"] == "1-5"
        assert saved["delivery_channels"] == ["terminal"]

    def test_esc_on_first_step_cancels_without_saving(self, tmp_path, monkeypatch):
        msg, saved, install_calls, remove_calls = _drive(["esc"], tmp_path, monkeypatch)
        assert msg == "Schedule setup cancelled."
        assert saved is None
        assert not install_calls and not remove_calls

    def test_esc_mid_wizard_steps_back_preserving_state(self, tmp_path, monkeypatch):
        # Pick 09:00 (up ×2 from the 10:00 default), reach lead, Esc back to time
        # — the cursor must re-seed on 09:00, so plain Enter keeps it — then Enter
        # through the rest (enable defaults to Off).
        msg, saved, install_calls, remove_calls = _drive(
            ["up", "up", "enter", "esc", "enter", "enter", "enter", "enter", "enter"], tmp_path, monkeypatch
        )
        assert saved["time"] == "09:00"
        assert saved["enabled"] is False
        assert msg == "removed ok"
        assert remove_calls == [("s1",)]

    def test_custom_time_reprompts_on_invalid(self, tmp_path, monkeypatch):
        # Down ×3 from 10:00 reaches Custom…; the line editor feeds an invalid
        # value then a valid one; the wizard re-prompts in between.
        msg, saved, _install, _remove = _drive(
            ["down", "down", "down", "enter", "enter", "enter", "enter", "enter"],
            tmp_path,
            monkeypatch,
            read_line=["25:99", "08:45"],
        )
        assert saved["time"] == "08:45"
        fake = ms._standup_read_line
        assert len(fake.prompts) == 2
        assert fake.prompts[1].startswith("Invalid value")

    def test_custom_time_esc_returns_to_option_list(self, tmp_path, monkeypatch):
        # Esc inside the Custom editor (read_line → None) returns to the time
        # option list — NOT the previous step — so Enter still confirms a preset.
        msg, saved, _install, _remove = _drive(
            ["down", "down", "down", "enter", "enter", "enter", "enter", "enter", "enter"],
            tmp_path,
            monkeypatch,
            read_line=[None],
        )
        assert saved is not None
        assert saved["time"] == "10:00"

    def test_empty_channels_blocked_until_one_selected(self, tmp_path, monkeypatch):
        # On the channels step: Space unchecks terminal, Enter is blocked (no
        # channels), Space re-checks, Enter proceeds.
        msg, saved, _install, _remove = _drive(
            ["enter", "enter", "enter", " ", "enter", " ", "enter", "enter"], tmp_path, monkeypatch
        )
        assert saved is not None
        assert saved["delivery_channels"] == ["terminal"]

    def test_days_toggle_persists_compressed_spec(self, tmp_path, monkeypatch):
        # On the days step: uncheck Tue and Thu (cursor starts on Monday) →
        # Mon/Wed/Fri remain → saved as "1,3,5".
        msg, saved, _install, _remove = _drive(
            ["enter", "enter", "down", " ", "down", "down", " ", "enter", "enter", "enter"],
            tmp_path,
            monkeypatch,
        )
        assert saved["weekdays"] == "1,3,5"

    def test_disable_path_calls_remove(self, tmp_path, monkeypatch):
        config = dict(enabled=True, time="09:30", lead_minutes=5, weekdays="1-5", delivery_channels=["terminal"])
        msg, saved, install_calls, remove_calls = _drive(
            ["enter", "enter", "enter", "enter", "down", "enter"], tmp_path, monkeypatch, config=config
        )
        assert msg == "removed ok"
        assert remove_calls == [("s1",)]
        assert not install_calls
        assert saved["enabled"] is False
        assert saved["time"] == "09:30"  # presets re-seed from saved config

    def test_preserves_identity_and_scope_fields(self, tmp_path, monkeypatch):
        config = dict(
            enabled=False,
            time="10:00",
            lead_minutes=10,
            weekdays="1-5",
            delivery_channels=["terminal"],
            repo_path="/tmp/repo",
            my_aliases="alice,ally",
            tracker_sources=["azdo"],
            team_members=["Alice"],
            roster_configured=True,
        )
        msg, saved, _install, _remove = _drive(
            ["enter", "enter", "enter", "enter", "enter"], tmp_path, monkeypatch, config=config
        )
        assert saved["repo_path"] == "/tmp/repo"
        assert saved["my_aliases"] == "alice,ally"
        assert saved["tracker_sources"] == ["azdo"]
        assert saved["team_members"] == ["Alice"]
        assert saved["roster_configured"] is True


class TestIdentityFlow:
    """The slimmed session-page Identity action (repo path + aliases only)."""

    def _run(self, tmp_path, monkeypatch, values, config=None):
        db = tmp_path / "sessions.db"
        if config:
            with StandupStore(db) as store:
                store.save_config("s1", **config)
        monkeypatch.setattr(ms, "_ana_dbp", db)
        answers = iter(values)
        monkeypatch.setattr(ms, "_standup_read_line", lambda *a, **k: next(answers, None))
        msg = ms._standup_identity_configure(_Console(), _Live(), lambda timeout=None: "esc", 0.05, True, "s1")
        with StandupStore(db) as store:
            return msg, store.load_config("s1")

    def test_saves_repo_and_aliases_without_touching_schedule(self, tmp_path, monkeypatch):
        config = dict(
            enabled=True, time="09:30", lead_minutes=5, weekdays="1,3,5", delivery_channels=["terminal", "desktop"]
        )
        msg, saved = self._run(tmp_path, monkeypatch, ["/tmp/repo", "alice, ally"], config=config)
        assert msg == "Identity saved."
        assert saved["repo_path"] == "/tmp/repo"
        assert saved["my_aliases"] == "alice, ally"
        # Schedule fields pass through untouched.
        assert saved["enabled"] is True
        assert saved["time"] == "09:30"
        assert saved["lead_minutes"] == 5
        assert saved["weekdays"] == "1,3,5"
        assert saved["delivery_channels"] == ["terminal", "desktop"]

    def test_esc_cancels_without_saving(self, tmp_path, monkeypatch):
        msg, saved = self._run(tmp_path, monkeypatch, [None])
        assert msg == "Identity cancelled."
        assert saved is None
