"""The guided Cloudflare Access setup engine.

Pure functions first, because they carry the parts that are expensive to get
wrong: a mis-parsed ``tunnel create`` leaves an orphan tunnel in someone's real
Cloudflare account that yeaboi cannot see it made, and a hostname we accept but
Cloudflare will not route becomes a board that silently never publishes.

Nothing here spawns cloudflared. ``tests/conftest.py::_no_real_tunnel_spawn``
also refuses the real binary through this module's ``_popen`` seam.
"""

from __future__ import annotations

import threading

import pytest

from yeaboi.sharing import access_setup as setup


class TestParsingTheTunnelCreate:
    """The one answer that must not be lost — see the module docstring."""

    def test_reads_the_json_shape(self):
        out = (
            '{"id":"3f1e2d4c-5b6a-7980-91a2-b3c4d5e6f708","name":"yeaboi",'
            '"credentials_file":"/Users/h/.cloudflared/3f1e2d4c-5b6a-7980-91a2-b3c4d5e6f708.json"}'
        )
        info = setup.parse_created_tunnel(out, "yeaboi")
        assert info is not None
        assert info.id == "3f1e2d4c-5b6a-7980-91a2-b3c4d5e6f708"
        assert info.name == "yeaboi"
        assert info.credentials.endswith(".json")

    def test_falls_back_to_the_human_text(self):
        """cloudflared's create output has changed shape across releases, and a
        tunnel we created but cannot name is worse than one we never created."""
        out = (
            "Tunnel credentials written to "
            "/Users/h/.cloudflared/3f1e2d4c-5b6a-7980-91a2-b3c4d5e6f708.json.\n"
            "Created tunnel yeaboi with id 3f1e2d4c-5b6a-7980-91a2-b3c4d5e6f708"
        )
        info = setup.parse_created_tunnel(out, "yeaboi")
        assert info is not None
        assert info.id == "3f1e2d4c-5b6a-7980-91a2-b3c4d5e6f708"
        assert info.credentials == "/Users/h/.cloudflared/3f1e2d4c-5b6a-7980-91a2-b3c4d5e6f708.json"

    def test_returns_none_when_there_is_no_id(self):
        assert setup.parse_created_tunnel("something went wrong", "yeaboi") is None


class TestParsingTheTunnelList:
    def test_finds_the_array_among_log_lines(self):
        """cloudflared writes its own structured log to the same stream, so the
        JSON is located rather than assumed to be the whole payload."""
        out = (
            '{"level":"info","message":"starting"}\n'
            '[{"id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","name":"retro"},'
            '{"id":"11111111-2222-3333-4444-555555555555","name":"poker"}]'
        )
        found = setup.parse_tunnel_list(out)
        assert [t.name for t in found] == ["retro", "poker"]

    def test_skips_deleted_tunnels(self):
        out = '[{"id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","name":"old","deleted_at":"2026-01-01T00:00:00Z"}]'
        assert setup.parse_tunnel_list(out) == ()

    def test_the_zero_date_means_live_not_deleted(self):
        """A live tunnel's deleted_at is the JSON zero date, not null — a truthy
        string. Treating it as "deleted" hid every real tunnel, so the wizard
        re-created one that existed and hit the name collision instead."""
        out = (
            '[{"id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","name":"yeaboi",'
            '"created_at":"2026-08-23T06:30:31.434305Z","deleted_at":"0001-01-01T00:00:00Z","connections":[]}]'
        )
        found = setup.parse_tunnel_list(out)
        assert [t.name for t in found] == ["yeaboi"]

    def test_survives_garbage(self):
        assert setup.parse_tunnel_list("not json at all") == ()
        assert setup.parse_tunnel_list("[{oops}]") == ()


class TestValidation:
    @pytest.mark.parametrize("host", ["retro.example.com", "a.b.co", "board.team.example.co.uk"])
    def test_accepts_real_hostnames(self, host):
        assert setup.valid_hostname(host)

    @pytest.mark.parametrize(
        "host",
        ["", "localhost", "example", "http://retro.example.com", "retro.example.com/path", "-bad.example.com", "a..b"],
    )
    def test_refuses_everything_else(self, host):
        """This value is handed to Cloudflare and then *asserted on* by the
        Host-header rule, so anything that is not a plain dotted DNS name is
        refused here rather than discovered as a board that will not route."""
        assert not setup.valid_hostname(host)

    def test_emails_may_be_empty(self):
        # "no remote visitor gets host powers" is a legitimate, safe choice.
        assert setup.valid_emails("")

    def test_email_list_is_all_or_nothing(self):
        assert setup.valid_emails("a@x.com, b@y.com")
        assert not setup.valid_emails("a@x.com, nonsense")


class TestFailureClassification:
    @pytest.mark.parametrize(
        ("output", "code"),
        [
            ("Cannot determine default origin certificate path", "NOT_LOGGED_IN"),
            ("Error locating origin cert: client didn't specify origincert path", "NOT_LOGGED_IN"),
            ("failed to dial to edge: no such host", "NO_NETWORK"),
            ("tunnel with name yeaboi already exists", "NAME_TAKEN"),
            ("failed to create tunnel: Create Tunnel API call failed: tunnel with name already exists", "NAME_TAKEN"),
            ("An A, AAAA, or CNAME record with that host already exists", "DNS_EXISTS"),
            ("api error 403: not authorized", "NOT_AUTHORIZED"),
            ("failed to add route: zone example.com not found in your account", "NO_ZONE"),
            ("error: unable to find zone for retro.example.com", "NO_ZONE"),
            ("Failed to lookup the zone for that hostname", "NO_ZONE"),
        ],
    )
    def test_names_the_cause(self, output, code):
        """Every failure here is a *setup* failure — "it didn't work" is useless
        when the fix is one specific command."""
        got, message = setup.classify_failure(1, output)
        assert got == code
        assert message

    def test_unknown_failure_still_gets_a_code(self):
        code, message = setup.classify_failure(1, "something unprecedented")
        assert code == "UNKNOWN"
        assert message


class TestTheNoDomainRemedies:
    """The tier's one prerequisite is a domain on Cloudflare; every failure that
    means "you don't have one" must say where to go, not just what broke."""

    def test_zone_not_found_points_at_add_a_site(self):
        _, message = setup.classify_failure(1, "zone example.com not found")
        assert "Add a site" in message

    def test_no_zone_wins_over_the_generic_permission_shape(self):
        """A route into a foreign zone can also 403 — the specific remedy must win."""
        code, _ = setup.classify_failure(1, "api error 403: zone example.com not found")
        assert code == "NO_ZONE"


class TestValidAud:
    """A wrong AUD is the one setup mistake nothing later catches loudly —
    verify() never reads it, so it must be caught at the prompt."""

    def test_a_real_tag_passes(self):
        assert setup.valid_aud("ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace")
        assert setup.valid_aud(
            "  BA3BA4F9A828505C0B06379D14B961165696F2E31A3925256C32645B16371ACE  "
        )  # pasted with whitespace/case

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "aud-tag",
            "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ac",
            "My Application",
            "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371acex",
        ],
    )
    def test_everything_else_is_refused(self, bad):
        assert not setup.valid_aud(bad)


class TestBoardsHostname:
    """The express default: the host types a domain, the boards live at boards.<domain>."""

    def test_a_bare_domain_gets_the_prefix(self):
        assert setup.boards_hostname("acme.com") == "boards.acme.com"

    def test_an_already_prefixed_value_is_untouched(self):
        assert setup.boards_hostname("boards.acme.com") == "boards.acme.com"

    def test_case_and_stray_dots_are_normalised(self):
        assert setup.boards_hostname("  Acme.COM. ") == "boards.acme.com"

    def test_a_cctld_apex_keeps_all_its_labels(self):
        assert setup.boards_hostname("acme.co.uk") == "boards.acme.co.uk"


class TestResolveTunnel:
    """The express flow's auto-pick: decided, not asked, except on real ambiguity."""

    def test_a_single_tunnel_is_the_answer(self):
        only = setup.TunnelInfo(id="uuid-1", name="whatever")
        assert setup.resolve_tunnel((only,)) is only

    def test_the_default_name_wins_among_many(self):
        ours = setup.TunnelInfo(id="uuid-2", name=setup.DEFAULT_TUNNEL_NAME)
        crowd = (setup.TunnelInfo(id="uuid-1", name="alpha"), ours, setup.TunnelInfo(id="uuid-3", name="beta"))
        assert setup.resolve_tunnel(crowd) is ours

    def test_genuine_ambiguity_returns_none(self):
        crowd = (setup.TunnelInfo(id="uuid-1", name="alpha"), setup.TunnelInfo(id="uuid-2", name="beta"))
        assert setup.resolve_tunnel(crowd) is None

    def test_no_tunnels_returns_none(self):
        assert setup.resolve_tunnel(()) is None


class TestTheRunnerNeverRaises:
    def test_a_binary_that_does_not_exist_is_reported_not_raised(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise OSError("no such file")

        monkeypatch.setattr(setup, "_popen", _boom)
        rc, output = setup._run(["/nope/cloudflared", "tunnel", "list"])
        assert rc == -1
        assert "no such file" in output

    def test_cancel_is_honoured(self, monkeypatch):
        class SlowProc:
            returncode = None
            stdout = iter(())

            def poll(self):
                return None

            def terminate(self):
                self.returncode = -15

            def wait(self, timeout=None):
                return -15

            def kill(self):
                pass

        monkeypatch.setattr(setup, "_popen", lambda *a, **k: SlowProc())
        monkeypatch.setattr(setup, "_POLL_SECONDS", 0.01)
        cancel = threading.Event()
        cancel.set()
        rc, _ = setup._run(["cloudflared"], cancel=cancel)
        assert rc == -2  # cancelled, distinct from failed


class TestOutcomeMapping:
    def test_each_runner_code_becomes_its_own_outcome(self):
        assert setup._outcome(0, "", "done").ok
        assert setup._outcome(-1, "", "").code == "NO_BINARY"
        assert setup._outcome(-2, "", "").code == "CANCELLED"
        assert setup._outcome(-3, "", "").code == "TIMEOUT"
        assert setup._outcome(1, "no such host", "").code == "NO_NETWORK"


class TestStepGuards:
    """Each step refuses before spawning when its precondition is unmet."""

    def _state(self, **kw):
        base = {"binary": "/bin/cloudflared", "logged_in": True, "jwt_installed": True, "missing_keys": ()}
        base.update(kw)
        return setup.SetupState(**base)

    def test_create_refuses_a_bad_name(self, monkeypatch):
        monkeypatch.setattr(setup, "read_state", lambda: self._state())
        info, outcome = setup.create_tunnel("not a valid name!")
        assert info is None
        assert outcome.code == "BAD_NAME"

    def test_create_refuses_when_not_signed_in(self, monkeypatch):
        monkeypatch.setattr(setup, "read_state", lambda: self._state(logged_in=False))
        info, outcome = setup.create_tunnel("yeaboi")
        assert info is None
        assert outcome.code == "NOT_LOGGED_IN"

    def test_route_refuses_a_non_hostname(self, monkeypatch):
        monkeypatch.setattr(setup, "read_state", lambda: self._state())
        assert setup.route_dns("yeaboi", "localhost").code == "BAD_HOSTNAME"

    def test_login_is_a_no_op_when_the_cert_already_exists(self, monkeypatch):
        """Re-running login would send someone to a browser for a file they have."""
        monkeypatch.setattr(setup, "find_cert", lambda: "/Users/h/.cloudflared/cert.pem")
        spawned = []
        monkeypatch.setattr(setup, "_popen", lambda *a, **k: spawned.append(a))
        outcome = setup.login()
        assert outcome.ok
        assert outcome.code == "ALREADY"
        assert spawned == []


class TestTheDnsRouteNeverOverwrites:
    def test_overwrite_dns_is_never_passed(self, monkeypatch):
        """This runs against the host's real zone. Silently repointing a record
        that already serves something else is not a setup step, it is an outage.
        """
        seen: dict = {}

        def _fake_run(argv, **kwargs):
            seen["argv"] = argv
            return 0, "ok"

        monkeypatch.setattr(setup, "read_state", lambda: setup.SetupState(binary="/bin/cloudflared", logged_in=True))
        monkeypatch.setattr(setup, "_run", _fake_run)
        assert setup.route_dns("yeaboi", "retro.example.com").ok
        assert "--overwrite-dns" not in seen["argv"]
        assert "-f" not in seen["argv"]


class TestTheJwtInstallPlan:
    def test_installs_the_package_not_the_extra(self, monkeypatch):
        """``yeaboi[access]`` would reinstall yeaboi over itself, and ``uv sync``
        would rebuild the venv this process is executing out of."""
        monkeypatch.setattr(setup, "jwt_installed", lambda: False)
        captured: dict = {}

        def _fake_run(argv, **kwargs):
            captured["argv"] = argv
            return 0, ""

        monkeypatch.setattr(setup, "_run", _fake_run)
        monkeypatch.setattr("yeaboi.voice_install.refresh_imports", lambda: None)
        # Second call reports success, mirroring a real post-install probe.
        calls = iter([False, True])
        monkeypatch.setattr(setup, "jwt_installed", lambda: next(calls, True))

        setup.install_jwt()
        argv = captured["argv"]
        joined = " ".join(argv)
        assert "PyJWT[crypto]" in argv
        assert "yeaboi[access]" not in joined
        assert "sync" not in joined
        assert "sounddevice" not in joined and "faster-whisper" not in joined

    def test_already_installed_short_circuits(self, monkeypatch):
        monkeypatch.setattr(setup, "jwt_installed", lambda: True)
        outcome = setup.install_jwt()
        assert outcome.ok
        assert outcome.code == "ALREADY"


class TestSavePersistsEachFactAsItIsLearned:
    def test_writes_through_apply_config_value(self, monkeypatch):
        written: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: written.__setitem__(k, v))
        setup.save(CLOUDFLARE_TUNNEL_ID="abc", CLOUDFLARE_ACCESS_HOSTNAME="")
        # Empty values are skipped rather than written as blanks, so a skipped
        # wizard step does not clear a key the host already set.
        assert written == {"CLOUDFLARE_TUNNEL_ID": "abc"}


class TestTheSignInUrlReachesTheUser:
    """cloudflared blocks until the browser round-trip finishes, so the URL is
    the only way through if the browser did not open. It travels on its own
    callback because narrated status lines overwrite the phrase."""

    def test_the_url_is_reported_separately_from_narration(self, monkeypatch):
        lines = [
            "Please open the following URL and log in with your Cloudflare account:",
            "https://dash.cloudflare.com/argotunnel?aud=&callback=https%3A%2F%2Flogin.cloudflareaccess.org%2Fx",
            "Waiting for you to finish in the browser...",
        ]

        class FakeProc:
            returncode = 0

            def __init__(self):
                self.stdout = iter(f"{line}\n" for line in lines)

            def poll(self):
                return 0

        monkeypatch.setattr(setup, "_popen", lambda *a, **k: FakeProc())
        monkeypatch.setattr(setup, "find_cert", lambda: "")
        monkeypatch.setattr(setup, "read_state", lambda: setup.SetupState(binary="/bin/cloudflared", logged_in=False))
        phrases: list[str] = []
        urls: list[str] = []
        setup.login(on_line=phrases.append, on_url=urls.append, open_browser=False)

        assert urls and urls[0].startswith("https://dash.cloudflare.com/argotunnel")
        # The last narrated phrase mentions the browser, which is exactly what
        # used to bury the URL when the two shared one channel.
        assert any("browser" in p for p in phrases)


class TestDiscoverApp:
    """Team and AUD come from the hostname's own Access redirect — anonymous,
    credential-free, and from the one place that cannot be wrong."""

    AUD = "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"

    def test_reads_team_and_aud_from_a_direct_redirect(self, monkeypatch):
        monkeypatch.setattr(
            setup,
            "_location_of",
            lambda url, timeout: (
                f"https://acme.cloudflareaccess.com/cdn-cgi/access/login/boards.x.ai?kid={self.AUD}&meta=x"
            ),
        )
        assert setup.discover_app("boards.x.ai") == ("acme", self.AUD)

    def test_a_kid_that_is_not_an_aud_shape_is_dropped(self, monkeypatch):
        """The aud is offered as a default the user confirms — a value that is
        not even the right shape must not be offered at all."""
        monkeypatch.setattr(
            setup,
            "_location_of",
            lambda url, timeout: "https://acme.cloudflareaccess.com/cdn-cgi/access/login/boards.x.ai?kid=abc",
        )
        assert setup.discover_app("boards.x.ai") == ("acme", "")

    def test_follows_a_relative_hop_first(self, monkeypatch):
        hops = {
            "https://boards.x.ai/": "/cdn-cgi/access/login",
            "https://boards.x.ai/cdn-cgi/access/login": "https://Acme.cloudflareaccess.com/login",
        }
        monkeypatch.setattr(setup, "_location_of", lambda url, timeout: hops.get(url, ""))
        assert setup.discover_app("boards.x.ai") == ("acme", "")

    def test_no_redirect_means_not_discovered(self, monkeypatch):
        monkeypatch.setattr(setup, "_location_of", lambda url, timeout: "")
        assert setup.discover_app("boards.x.ai") == ("", "")

    def test_a_redirect_loop_gives_up(self, monkeypatch):
        calls = []
        monkeypatch.setattr(setup, "_location_of", lambda url, timeout: calls.append(url) or "https://boards.x.ai/")
        assert setup.discover_app("boards.x.ai") == ("", "")
        assert len(calls) == 3

    def test_an_invalid_hostname_is_never_fetched(self, monkeypatch):
        monkeypatch.setattr(setup, "_location_of", lambda url, timeout: pytest.fail("fetched"))
        assert setup.discover_app("not a hostname") == ("", "")


class TestTheCliTwinResumes:
    """`--setup-access` shares the wizard's resume rule: stored facts are done
    steps, and a fully configured host goes straight to verification."""

    def _wire(self, monkeypatch, missing, answers):
        import yeaboi.cli as cli

        monkeypatch.setattr(
            setup,
            "read_state",
            lambda: setup.SetupState(
                binary="/bin/cloudflared", logged_in=True, jwt_installed=True, missing_keys=missing
            ),
        )
        monkeypatch.setattr(setup, "jwt_installed", lambda: True)
        monkeypatch.setattr(setup, "discover_app", lambda *a, **kw: ("", ""))
        monkeypatch.setattr(setup, "verify", lambda **kw: setup.Outcome(True, "ready"))
        saved: dict[str, str] = {}
        monkeypatch.setattr(setup, "save", lambda **kw: saved.update(kw))
        monkeypatch.setattr("yeaboi.config.access_hostname", lambda: "boards.yeaboi.ai")
        it = iter(answers)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
        return cli._setup_access, saved

    def test_fully_configured_asks_nothing(self, monkeypatch, capsys):
        run, saved = self._wire(monkeypatch, missing=(), answers=[])
        assert run() == 0  # an exhausted iterator would raise if anything asked
        out = capsys.readouterr().out
        assert "already set" in out and "boards.yeaboi.ai" in out

    def test_missing_app_details_asks_only_those(self, monkeypatch, capsys):
        """The stored hostname is reused in the instructions — the NameError
        regression this class exists to hold down."""
        run, saved = self._wire(
            monkeypatch,
            missing=("CLOUDFLARE_ACCESS_TEAM", "CLOUDFLARE_ACCESS_AUD"),
            answers=["ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace", "acme"],
        )
        assert run() == 0
        out = capsys.readouterr().out
        assert "set the application domain to boards.yeaboi.ai" in out
        assert saved["CLOUDFLARE_ACCESS_TEAM"] == "acme"
        assert saved["CLOUDFLARE_ACCESS_AUD"] == "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"

    def test_a_detected_team_is_not_asked_for(self, monkeypatch, capsys):
        run, saved = self._wire(
            monkeypatch,
            missing=("CLOUDFLARE_ACCESS_TEAM", "CLOUDFLARE_ACCESS_AUD"),
            answers=[
                "ba3ba4f9a828505c0b06379d14b961165696f2e31a3925256c32645b16371ace"
            ],  # an exhausted iterator would raise on a team ask
        )
        monkeypatch.setattr(setup, "discover_app", lambda *a, **kw: ("acme", ""))
        assert run() == 0
        assert "Team name detected" in capsys.readouterr().out
        assert saved["CLOUDFLARE_ACCESS_TEAM"] == "acme"

    def test_a_detected_aud_becomes_the_default(self, monkeypatch, capsys):
        """Blank Enter accepts the detected tag — nothing left to transcribe."""
        aud = TestDiscoverApp.AUD
        run, saved = self._wire(
            monkeypatch,
            missing=("CLOUDFLARE_ACCESS_TEAM", "CLOUDFLARE_ACCESS_AUD"),
            answers=[""],  # accept the default
        )
        monkeypatch.setattr(setup, "discover_app", lambda *a, **kw: ("acme", aud))
        assert run() == 0
        assert saved["CLOUDFLARE_ACCESS_AUD"] == aud
        assert saved["CLOUDFLARE_ACCESS_TEAM"] == "acme"
