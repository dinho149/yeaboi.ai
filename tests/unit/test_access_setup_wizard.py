"""The Cloudflare Access setup wizard's walker — the express flow.

The screens are the standup wizard's, already covered by its own render tests;
what is new and worth pinning here is the *walk* — which steps are skipped,
what is decided rather than asked, and above all **when config is written**.

That last one is the reason this file exists. These steps have side effects on a
real Cloudflare account: a tunnel created and then abandoned to an Esc is an
orphan the wizard cannot see it made, so a fact must be stored the moment it is
known rather than at the end.
"""

from __future__ import annotations

import pytest

from yeaboi.sharing import access_setup
from yeaboi.ui.mode_select import _maybe_offer_share_tier, _run_access_setup

ALL_KEYS = (
    "CLOUDFLARE_TUNNEL_ID",
    "CLOUDFLARE_TUNNEL_CREDENTIALS",
    "CLOUDFLARE_ACCESS_HOSTNAME",
    "CLOUDFLARE_ACCESS_TEAM",
    "CLOUDFLARE_ACCESS_AUD",
)


class FakeConsole:
    size = (100, 34)


class FakeLive:
    def update(self, _panel):
        pass


@pytest.fixture
def wired(monkeypatch):
    """Replace every step's engine call, and record what got saved."""
    saved: dict[str, str] = {}
    calls: list[str] = []

    monkeypatch.setattr(access_setup, "save", lambda **kw: saved.update({k: v for k, v in kw.items() if v}))
    monkeypatch.setattr(
        access_setup,
        "read_state",
        lambda: access_setup.SetupState(
            binary="/bin/cloudflared", logged_in=True, jwt_installed=True, missing_keys=ALL_KEYS
        ),
    )
    monkeypatch.setattr(access_setup, "jwt_installed", lambda: True)
    monkeypatch.setattr(access_setup, "discover_app", lambda *a, **kw: ("", ""))
    monkeypatch.setattr(
        access_setup,
        "list_tunnels",
        lambda **kw: ((access_setup.TunnelInfo(id="uuid-1", name="retro"),), access_setup.Outcome(True, "")),
    )
    monkeypatch.setattr(
        access_setup,
        "route_dns",
        lambda *a, **kw: calls.append("route") or access_setup.Outcome(True, "routed"),
    )
    monkeypatch.setattr(access_setup, "verify", lambda **kw: access_setup.Outcome(True, "Cloudflare Access is ready."))
    return saved, calls


def _drive(monkeypatch, *, choices, lines):
    """Feed the wizard scripted answers for its choice and text steps.

    A script that runs out means "Esc from here on", never "press Enter again":
    defaulting to Enter makes a step that auto-advances bounce against one that
    backs out, and the wizard walks between them forever. That is a property of
    the driver, not of the walker — a real user alternating Enter and Esc is
    entitled to walk in circles.
    """
    choice_iter = iter(choices)
    line_iter = iter(lines)
    monkeypatch.setattr(
        "yeaboi.ui.mode_select._run_schedule_choice_step",
        lambda *a, **kw: next(choice_iter, "back"),
    )
    monkeypatch.setattr(
        "yeaboi.ui.mode_select._standup_read_line",
        lambda *a, **kw: next(line_iter, None),
    )


def _spy_screens(monkeypatch, *, choices, lines):
    """Like _drive, but records every choice screen's heading and every prompt."""
    seen = {"headings": [], "prompts": [], "messages": []}
    choice_iter = iter(choices)
    line_iter = iter(lines)

    def _choice_spy(*a, **kw):
        seen["headings"].append(kw.get("heading", ""))
        seen["messages"].append(kw.get("message", ""))
        return next(choice_iter, "back")

    def _line_spy(*a, **kw):
        seen["prompts"].append(kw.get("prompt", ""))
        return next(line_iter, None)

    monkeypatch.setattr("yeaboi.ui.mode_select._run_schedule_choice_step", _choice_spy)
    monkeypatch.setattr("yeaboi.ui.mode_select._standup_read_line", _line_spy)
    return seen


def _run():
    return _run_access_setup(FakeConsole(), FakeLive(), lambda timeout=None: "", 0.001, True)


class TestTheHappyWalk:
    def test_three_answers_reach_the_end(self, monkeypatch, wired):
        """The whole point of the express flow: hostname, team, AUD — nothing else."""
        saved, calls = wired
        # access app: "I've created it"; verify: Done
        _drive(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        result = _run()
        assert "ready" in result.lower()
        assert saved["CLOUDFLARE_TUNNEL_ID"] == "uuid-1"
        assert saved["CLOUDFLARE_ACCESS_HOSTNAME"] == "boards.example.com"
        assert saved["CLOUDFLARE_ACCESS_TEAM"] == "acme"
        assert saved["CLOUDFLARE_ACCESS_AUD"] == "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"
        assert saved["YEABOI_SHARE_MODE"] == "access"
        assert "route" in calls

    def test_the_done_screen_points_at_settings(self, monkeypatch, wired):
        """The defaults the wizard decided must advertise where to change them."""
        _drive(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        assert "Settings ▸ Sharing" in _run()

    def test_admin_emails_are_never_asked(self, monkeypatch, wired):
        """Blank means "no remote visitor gets host powers" — a safe default the
        Settings row can change later, not a question worth a setup stop."""
        saved, _ = wired
        seen = _spy_screens(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        _run()
        assert "CLOUDFLARE_ACCESS_ADMIN_EMAILS" not in saved
        assert not any("email" in p.lower() for p in seen["prompts"])


class TestEveryPromptCarriesItsOwnFraming:
    def test_text_steps_pass_context_and_the_step_strip(self, monkeypatch, wired):
        """Signed-in hosts skip the Sign-in screen, so a text field can be the
        wizard's first screen — a bare input box there explains nothing."""
        seen: list[dict] = []
        choice_iter = iter([1, 0])
        line_iter = iter(["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"])
        monkeypatch.setattr(
            "yeaboi.ui.mode_select._run_schedule_choice_step", lambda *a, **kw: next(choice_iter, "back")
        )

        def _line_spy(*a, **kw):
            seen.append(kw)
            return next(line_iter, None)

        monkeypatch.setattr("yeaboi.ui.mode_select._standup_read_line", _line_spy)
        _run()
        assert len(seen) == 3
        for kw in seen:
            assert kw.get("message"), kw.get("prompt")
            assert kw.get("step_names") == ["Sign in", "Hostname", "Access app", "Verify"]


class TestTheTunnelIsDecidedNotAsked:
    def test_a_single_tunnel_is_used_without_a_picker(self, monkeypatch, wired):
        saved, _ = wired
        seen = _spy_screens(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        _run()
        assert saved["CLOUDFLARE_TUNNEL_ID"] == "uuid-1"
        assert not any("Which tunnel" in h for h in seen["headings"])

    def test_no_tunnels_creates_the_default_without_asking_a_name(self, monkeypatch, wired):
        saved, _ = wired
        monkeypatch.setattr(access_setup, "list_tunnels", lambda **kw: ((), access_setup.Outcome(True, "")))
        created: list[str] = []

        def _create(name, **kw):
            created.append(name)
            return access_setup.TunnelInfo(id="uuid-new", name=name), access_setup.Outcome(True, "created")

        monkeypatch.setattr(access_setup, "create_tunnel", _create)
        seen = _spy_screens(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        _run()
        assert created == [access_setup.DEFAULT_TUNNEL_NAME]
        assert saved["CLOUDFLARE_TUNNEL_ID"] == "uuid-new"
        # No "name for the new tunnel" prompt — the name is DEFAULT_TUNNEL_NAME.
        assert not any("tunnel" in p.lower() for p in seen["prompts"])

    def test_genuine_ambiguity_still_shows_the_picker(self, monkeypatch, wired):
        """Several tunnels and none named "yeaboi" — the one case a choice remains."""
        saved, _ = wired
        monkeypatch.setattr(
            access_setup,
            "list_tunnels",
            lambda **kw: (
                (
                    access_setup.TunnelInfo(id="uuid-a", name="alpha"),
                    access_setup.TunnelInfo(id="uuid-b", name="beta"),
                ),
                access_setup.Outcome(True, ""),
            ),
        )
        # picker: take the second; access app: continue; verify: Done
        seen = _spy_screens(
            monkeypatch,
            choices=[1, 1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        _run()
        assert any("Which tunnel" in h for h in seen["headings"])
        assert saved["CLOUDFLARE_TUNNEL_ID"] == "uuid-b"


class TestSkippedStepsAreTransparent:
    def test_sign_in_is_skipped_when_the_cert_exists(self, monkeypatch, wired):
        """`login` must not even be called — re-running it would send someone to
        a browser for a file they already have."""
        spawned: list[str] = []
        monkeypatch.setattr(access_setup, "login", lambda **kw: spawned.append("login"))
        _drive(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        _run()
        assert spawned == []

    def test_pyjwt_install_is_skipped_when_installed(self, monkeypatch, wired):
        installs: list[str] = []
        monkeypatch.setattr(access_setup, "install_jwt", lambda **kw: installs.append("install"))
        _drive(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        _run()
        assert installs == []

    def test_pyjwt_installs_as_a_phase_of_verify(self, monkeypatch, wired):
        """Not a step of its own: it never needs input, so it never earns a stop."""
        monkeypatch.setattr(access_setup, "jwt_installed", lambda: False)
        installs: list[str] = []
        monkeypatch.setattr(
            access_setup,
            "install_jwt",
            lambda **kw: installs.append("install") or access_setup.Outcome(True, "installed"),
        )
        _drive(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        result = _run()
        assert installs == ["install"]
        assert "ready" in result.lower()


class TestFactsAreSavedAsTheyAreLearned:
    def test_abandoning_after_the_tunnel_still_kept_it(self, monkeypatch, wired):
        """The whole reason this wizard departs from commit-at-the-end."""
        saved, _ = wired
        # DNS fails hard after the tunnel fact is stored; the ack screen backs out.
        monkeypatch.setattr(access_setup, "route_dns", lambda *a, **kw: access_setup.Outcome(False, "boom", "UNKNOWN"))
        _drive(monkeypatch, choices=[0], lines=["example.com"])
        result = _run()
        assert result is None  # cancelled
        assert saved["CLOUDFLARE_TUNNEL_ID"] == "uuid-1"
        assert saved["CLOUDFLARE_TUNNEL_CREDENTIALS"].endswith("uuid-1.json")
        # ...and nothing from what never succeeded.
        assert "CLOUDFLARE_ACCESS_HOSTNAME" not in saved


class TestTheDnsCollisionIsNeverSilent:
    def test_an_existing_record_asks_before_continuing(self, monkeypatch, wired):
        """Never overwritten, never assumed benign — the record may point at this
        tunnel (a re-run) or at something else entirely."""
        saved, _ = wired
        monkeypatch.setattr(
            access_setup,
            "route_dns",
            lambda *a, **kw: access_setup.Outcome(False, "already points somewhere else", "DNS_EXISTS"),
        )
        # collision screen: "pick another hostname" → back out of setup
        seen = _spy_screens(monkeypatch, choices=[1], lines=["example.com"])
        _run()
        assert any("DNS record" in h for h in seen["headings"])
        assert "CLOUDFLARE_ACCESS_HOSTNAME" not in saved


class TestTheAccessAppScreenOpensTheDashboard:
    def test_the_dashboard_option_opens_the_apps_page_and_returns(self, monkeypatch, wired):
        """The one manual step must hand over a link, not a breadcrumb to type."""
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
        headings: list[str] = []
        # app screen: open dashboard → app screen again: continue; verify: Done
        choice_iter = iter([0, 1, 0])

        def _choice_spy(*a, **kw):
            headings.append(kw.get("heading", ""))
            return next(choice_iter, "back")

        monkeypatch.setattr("yeaboi.ui.mode_select._run_schedule_choice_step", _choice_spy)
        line_iter = iter(["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"])
        monkeypatch.setattr("yeaboi.ui.mode_select._standup_read_line", lambda *a, **kw: next(line_iter, None))
        _run()
        assert opened == [access_setup.ACCESS_APP_ADD_URL]
        app_screens = [h for h in headings if "Access application" in h]
        assert len(app_screens) == 2  # shown again after the browser opened


class TestPickAnotherHostnameDoesWhatItSays:
    def test_the_collision_choice_re_prompts_instead_of_closing_the_wizard(self, monkeypatch, wired):
        """With sign-in skipped, "back" from this step walks off the front of
        the wizard and closes it silently — the labelled action must instead
        show the hostname prompt again, and a second hostname must succeed."""
        saved, _ = wired
        routes: list[str] = []

        def _route_dns(tid, host, **kw):
            routes.append(host)
            if host == "boards.taken.com":
                return access_setup.Outcome(False, "already exists", "DNS_EXISTS")
            return access_setup.Outcome(True, "routed")

        monkeypatch.setattr(access_setup, "route_dns", _route_dns)
        # collision: "Pick another hostname"; then app continue; verify Done
        _drive(
            monkeypatch,
            choices=[1, 1, 0],
            lines=["taken.com", "free.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        result = _run()
        assert "ready" in result.lower()
        assert routes == ["boards.taken.com", "boards.free.com"]
        assert saved["CLOUDFLARE_ACCESS_HOSTNAME"] == "boards.free.com"


class TestTheDomainPrerequisite:
    def test_the_sign_in_screen_states_the_requirement(self, monkeypatch, wired):
        monkeypatch.setattr(
            access_setup,
            "read_state",
            lambda: access_setup.SetupState(
                binary="/bin/cloudflared", logged_in=False, jwt_installed=True, missing_keys=ALL_KEYS
            ),
        )
        monkeypatch.setattr(access_setup, "find_cert", lambda: "")
        messages: list[str] = []

        def _choice_spy(*a, **kw):
            messages.append(kw.get("message", ""))
            return "back"

        monkeypatch.setattr("yeaboi.ui.mode_select._run_schedule_choice_step", _choice_spy)
        monkeypatch.setattr("yeaboi.ui.mode_select._standup_read_line", lambda *a, **kw: None)
        _run()
        assert any("Requires: a Cloudflare account, and a domain added to it" in m for m in messages)

    def test_no_domain_option_opens_the_add_site_page(self, monkeypatch, wired):
        """The dead end the express flow exists to close: an empty zone picker in
        a browser tab with no obvious next action."""
        monkeypatch.setattr(
            access_setup,
            "read_state",
            lambda: access_setup.SetupState(
                binary="/bin/cloudflared", logged_in=False, jwt_installed=True, missing_keys=ALL_KEYS
            ),
        )
        monkeypatch.setattr(access_setup, "find_cert", lambda: "")
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
        headings: list[str] = []
        # "I don't have a domain" → help screen Back → sign-in again → Back out.
        choice_iter = iter([1, 0, 2])

        def _choice_spy(*a, **kw):
            headings.append(kw.get("heading", ""))
            return next(choice_iter, "back")

        monkeypatch.setattr("yeaboi.ui.mode_select._run_schedule_choice_step", _choice_spy)
        monkeypatch.setattr("yeaboi.ui.mode_select._standup_read_line", lambda *a, **kw: None)
        result = _run()
        assert opened == [access_setup.ADD_SITE_URL]
        assert "Add a domain to Cloudflare" in headings
        # The flow returned to the sign-in screen rather than advancing.
        assert headings.count("Sign in to Cloudflare") == 2
        assert result is None


class TestTheTeamNameIsReadNotAsked:
    def test_a_detected_team_skips_the_question(self, monkeypatch, wired):
        """The hostname's own sign-in redirect names the team — asking a person
        to transcribe it is one more screen than the truth requires."""
        saved, _ = wired
        monkeypatch.setattr(access_setup, "discover_app", lambda *a, **kw: ("acme", ""))
        seen = _spy_screens(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        result = _run()
        assert "ready" in result.lower()
        assert saved["CLOUDFLARE_ACCESS_TEAM"] == "acme"
        assert not any("team name" in p.lower() for p in seen["prompts"])

    def test_a_detected_aud_is_offered_as_the_default(self, monkeypatch, wired):
        """One Enter accepts the detected tag — nothing left to transcribe."""
        saved, _ = wired
        aud = "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"
        monkeypatch.setattr(access_setup, "discover_app", lambda *a, **kw: ("acme", aud))
        choice_iter = iter([1, 0])
        monkeypatch.setattr(
            "yeaboi.ui.mode_select._run_schedule_choice_step", lambda *a, **kw: next(choice_iter, "back")
        )
        defaults: list[str] = []
        line_iter = iter(["example.com", ""])

        def _line_spy(*a, **kw):
            defaults.append(kw.get("default", ""))
            # An empty Enter returns the default, per _standup_read_line's contract.
            answer = next(line_iter, None)
            return kw.get("default", "") if answer == "" else answer

        monkeypatch.setattr("yeaboi.ui.mode_select._standup_read_line", _line_spy)
        _run()
        assert aud in defaults
        assert saved["CLOUDFLARE_ACCESS_AUD"] == aud

    def test_detection_failure_falls_back_to_asking(self, monkeypatch, wired):
        saved, _ = wired
        seen = _spy_screens(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        _run()
        assert saved["CLOUDFLARE_ACCESS_TEAM"] == "acme"
        assert any("team name" in p.lower() for p in seen["prompts"])


class TestReRunsResumeAtTheFirstMissingFact:
    def test_a_stored_hostname_is_not_asked_again(self, monkeypatch, wired):
        """The user's report verbatim: 'i still see the hostname option'. A saved
        fact is a done step — changing it is the Settings row's job."""
        monkeypatch.setattr(
            access_setup,
            "read_state",
            lambda: access_setup.SetupState(
                binary="/bin/cloudflared",
                logged_in=True,
                jwt_installed=True,
                missing_keys=("CLOUDFLARE_ACCESS_TEAM", "CLOUDFLARE_ACCESS_AUD"),
            ),
        )
        monkeypatch.setattr("yeaboi.config.access_hostname", lambda: "boards.yeaboi.ai")
        seen = _spy_screens(
            monkeypatch,
            choices=[1, 0],
            lines=["acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        result = _run()
        assert "ready" in result.lower()
        assert not any("domain" in p.lower() for p in seen["prompts"])
        # The app instructions still name the stored hostname.
        assert any("boards.yeaboi.ai" in h for h in seen["messages"])

    def test_a_stored_tunnel_is_kept_when_only_the_hostname_is_missing(self, monkeypatch, wired):
        """Re-resolving could pick a different tunnel and silently overwrite the
        stored id — the hostname-only resume must reuse what is stored."""
        saved, calls = wired
        monkeypatch.setattr(
            access_setup,
            "read_state",
            lambda: access_setup.SetupState(
                binary="/bin/cloudflared",
                logged_in=True,
                jwt_installed=True,
                missing_keys=("CLOUDFLARE_ACCESS_HOSTNAME", "CLOUDFLARE_ACCESS_TEAM", "CLOUDFLARE_ACCESS_AUD"),
            ),
        )
        monkeypatch.setattr("yeaboi.config.access_tunnel_id", lambda: "stored-uuid")
        monkeypatch.setattr("yeaboi.config.access_credentials_file", lambda: "/home/x/.cloudflared/stored-uuid.json")
        listed: list[int] = []
        monkeypatch.setattr(
            access_setup,
            "list_tunnels",
            lambda **kw: listed.append(1) or ((), access_setup.Outcome(True, "")),
        )
        routed: list[tuple] = []
        monkeypatch.setattr(
            access_setup,
            "route_dns",
            lambda tid, host, **kw: routed.append((tid, host)) or access_setup.Outcome(True, "routed"),
        )
        _drive(
            monkeypatch,
            choices=[1, 0],
            lines=["example.com", "acme", "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"],
        )
        result = _run()
        assert "ready" in result.lower()
        assert listed == []  # never re-resolved
        assert routed == [("stored-uuid", "boards.example.com")]
        assert "CLOUDFLARE_TUNNEL_ID" not in saved  # the stored id was not rewritten
        assert saved["CLOUDFLARE_ACCESS_HOSTNAME"] == "boards.example.com"

    def test_fully_configured_goes_straight_to_verify(self, monkeypatch, wired):
        monkeypatch.setattr(
            access_setup,
            "read_state",
            lambda: access_setup.SetupState(
                binary="/bin/cloudflared", logged_in=True, jwt_installed=True, missing_keys=()
            ),
        )
        seen = _spy_screens(monkeypatch, choices=[0], lines=[])
        result = _run()
        assert "ready" in result.lower()
        assert seen["prompts"] == []  # nothing asked at all
        assert seen["headings"] == ["Verified"]


class TestRefusalsBeforeAnythingRuns:
    def test_no_binary_stops_immediately(self, monkeypatch):
        monkeypatch.setattr(access_setup, "read_state", lambda: access_setup.SetupState(binary=""))
        result = _run()
        assert "cloudflared" in result


class TestTheFirstSharePrompt:
    """One screen, once ever, right before the first share."""

    @pytest.fixture
    def clean_env(self, monkeypatch):
        for key in ("YEABOI_SHARE_MODE", "YEABOI_SHARE_TIER_PROMPTED", "YEABOI_NO_TUNNEL"):
            monkeypatch.delenv(key, raising=False)

    @pytest.fixture
    def offered(self, monkeypatch):
        saved: dict[str, str] = {}
        state = {"asked": 0, "setup": 0, "answer": 0}
        monkeypatch.setattr(access_setup, "save", lambda **kw: saved.update(kw))

        def _choice(*a, **kw):
            state["asked"] += 1
            return state["answer"]

        monkeypatch.setattr("yeaboi.ui.mode_select._run_schedule_choice_step", _choice)
        monkeypatch.setattr(
            "yeaboi.ui.mode_select._run_access_setup",
            lambda *a, **kw: state.__setitem__("setup", state["setup"] + 1) or "done",
        )
        return saved, state

    def _offer(self):
        _maybe_offer_share_tier(FakeConsole(), FakeLive(), lambda timeout=None: "", 0.001, True)

    def test_shown_once_and_the_default_stays_quick(self, clean_env, offered):
        saved, state = offered
        self._offer()
        assert state["asked"] == 1 and state["setup"] == 0
        assert saved == {"YEABOI_SHARE_TIER_PROMPTED": "1"}  # no share mode written

    def test_choosing_verified_enters_the_wizard(self, clean_env, offered):
        saved, state = offered
        state["answer"] = 1
        self._offer()
        assert state["setup"] == 1
        assert saved["YEABOI_SHARE_TIER_PROMPTED"] == "1"

    def test_esc_counts_as_answered(self, clean_env, offered):
        """ "Not now" must not become "nag on every share"."""
        saved, state = offered
        state["answer"] = "back"
        self._offer()
        assert state["setup"] == 0
        assert saved["YEABOI_SHARE_TIER_PROMPTED"] == "1"

    @pytest.mark.parametrize(
        "key, value",
        [
            ("YEABOI_SHARE_MODE", "access"),
            ("YEABOI_SHARE_MODE", "quick"),
            ("YEABOI_SHARE_TIER_PROMPTED", "1"),
            ("YEABOI_NO_TUNNEL", "1"),
        ],
    )
    def test_never_shown_when_already_answered_or_tunnels_off(self, clean_env, offered, monkeypatch, key, value):
        _, state = offered
        monkeypatch.setenv(key, value)
        self._offer()
        assert state["asked"] == 0
