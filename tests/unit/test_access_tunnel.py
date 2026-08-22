"""Tests for the Access tier's named tunnel and the tier factory.

Two properties carry the weight here.

**The generated ingress is parsed by a real YAML parser**, not by the code that
wrote it. yeaboi hand-emits this file (see ``_yaml_quote`` for why), so a test
that checked it with string matching would be checking the emitter against
itself. The ``httpHostHeader`` pin in particular is load-bearing: the
``Host``-header rule in ``sharing/identity.py`` is only sound while cloudflared
actually sends the hostname we asked for.

**The tier never silently falls back to a quick tunnel.** That is the invariant
the whole tier exists to protect — a host who configured Cloudflare Access and
got a public ``trycloudflare.com`` URL is worse off than one who got no share at
all, because they believe something untrue about who can reach their board. It
is asserted by making the named path fail and checking that ``CloudflareTunnel``
is never constructed.
"""

from __future__ import annotations

import os

import pytest
import yaml

from yeaboi.sharing import access_tunnel
from yeaboi.sharing.access_tunnel import (
    AccessTunnel,
    _sweep_stale_ingress,
    claim_hostname,
    release_hostname,
    render_ingress,
)
from yeaboi.sharing.tunnel import open_tunnel

HOSTNAME = "retro.example.com"


@pytest.fixture(autouse=True)
def _no_leaked_claims():
    """A claim leaking between tests would make the next one fail mysteriously."""
    yield
    access_tunnel._claims.clear()


class TestTheGeneratedIngress:
    """Parsed back with PyYAML — the emitter must not be its own judge."""

    def _doc(self, port: int = 5173) -> dict:
        from pathlib import Path

        return yaml.safe_load(render_ingress("uuid-1", Path("/home/ada/.cloudflared/uuid-1.json"), HOSTNAME, port))

    def test_it_names_the_tunnel_and_its_credentials(self):
        doc = self._doc()
        assert doc["tunnel"] == "uuid-1"
        assert doc["credentials-file"] == "/home/ada/.cloudflared/uuid-1.json"

    def test_it_routes_the_hostname_to_the_port_we_actually_got(self):
        """The reason this file is generated at all.

        Every server here picks its port at bind time, so a dashboard-managed
        tunnel would need the port written down in advance — and pinning a fixed
        port reintroduces "port busy ⇒ no sharing" for exactly the host running
        two boards.
        """
        rule = self._doc(port=5291)["ingress"][0]
        assert rule["hostname"] == HOSTNAME
        assert rule["service"] == "http://localhost:5291"

    def test_it_pins_the_host_header(self):
        """The pin the Host-header rule depends on.

        ``AccessGate.requires_verification`` decides whether a request must
        carry a verified token by looking at ``Host``. That is only ours to
        assert because this pins what cloudflared sends, rather than leaving it
        to cloudflared's default.
        """
        assert self._doc()["ingress"][0]["originRequest"]["httpHostHeader"] == HOSTNAME

    def test_it_ends_with_a_catch_all_that_serves_nothing(self):
        last = self._doc()["ingress"][-1]
        assert last == {"service": "http_status:404"}
        assert "hostname" not in last

    def test_it_serves_exactly_one_hostname(self):
        # A second ingress rule would be a second origin. There is one board.
        doc = self._doc()
        assert len([r for r in doc["ingress"] if "hostname" in r]) == 1

    def test_a_hostile_hostname_cannot_break_out_of_its_scalar(self):
        """The values come from the environment, so they are not ours to trust.

        A quote or a newline that escaped its scalar could add an ingress rule —
        which is to say, add an origin — so the emitter escapes rather than
        interpolating.
        """
        from pathlib import Path

        nasty = 'evil"\n  - hostname: other.example.com\n    service: "http://localhost:22'
        doc = yaml.safe_load(render_ingress("uuid-1", Path("/c.json"), nasty, 5173))
        # Two rules — the one we wrote and the catch-all — not three.
        assert len(doc["ingress"]) == 2
        # The whole payload stayed inside the scalar it was written into.
        assert doc["ingress"][0]["hostname"] == nasty
        assert doc["ingress"][0]["service"] == "http://localhost:5173"
        assert doc["ingress"][-1] == {"service": "http_status:404"}


class TestTheHostnameClaim:
    """One hostname, one connector — the hazard the tier creates."""

    def test_the_first_claim_wins(self):
        assert claim_hostname(HOSTNAME) is True

    def test_a_second_board_on_the_same_hostname_is_refused(self):
        """A named tunnel accepts many simultaneous connectors — that is HA.

        Two boards advertising ingress for one hostname means Cloudflare sends
        each request to whichever answers, so teammates land on the retro board
        or the poker board essentially at random. A quick tunnel is immune
        because every launch mints a fresh hostname.
        """
        claim_hostname(HOSTNAME)
        assert claim_hostname(HOSTNAME) is False

    def test_the_check_is_case_insensitive(self):
        claim_hostname("Retro.Example.COM")
        assert claim_hostname("retro.example.com") is False

    def test_releasing_frees_it(self):
        claim_hostname(HOSTNAME)
        release_hostname(HOSTNAME)
        assert claim_hostname(HOSTNAME) is True

    def test_releasing_twice_is_safe(self):
        claim_hostname(HOSTNAME)
        release_hostname(HOSTNAME)
        release_hostname(HOSTNAME)

    def test_two_different_hostnames_coexist(self):
        assert claim_hostname("retro.example.com") is True
        assert claim_hostname("poker.example.com") is True

    def test_an_empty_hostname_is_never_claimable(self):
        assert claim_hostname("") is False


class TestTheCommandLine:
    def _tunnel(self, tmp_path) -> AccessTunnel:
        tunnel = AccessTunnel(5173, HOSTNAME, tunnel_id="uuid-1", credentials=tmp_path / "creds.json")
        tunnel._ingress = tmp_path / "ingress.yml"
        return tunnel

    def test_config_comes_before_run(self, tmp_path):
        """``--config`` is a *tunnel command* option, not a subcommand option.

        Verified against the pinned binary's help, which lists it under "TUNNEL
        COMMAND OPTIONS" while ``--credentials-file`` sits under "SUBCOMMAND
        OPTIONS". Putting it after ``run`` makes cloudflared ignore the ingress
        entirely — and an ignored ingress is a tunnel pointing nowhere.
        """
        argv = self._tunnel(tmp_path)._argv(tmp_path / "cloudflared", ())
        assert argv.index("--config") < argv.index("run")

    def test_there_is_no_url_flag(self, tmp_path):
        # The origin comes from the ingress file. A --url here would be a second,
        # contradictory answer to "what is behind this tunnel".
        assert "--url" not in self._tunnel(tmp_path)._argv(tmp_path / "cloudflared", ())

    def test_logging_is_never_debug(self, tmp_path):
        """At debug, cloudflared logs every request URL and all headers.

        This app's credentials ride in the query string, so debug logging would
        put live tokens into the stream ``_drain`` reads and then into yeaboi's
        own log file.
        """
        argv = self._tunnel(tmp_path)._argv(tmp_path / "cloudflared", ())
        assert argv[argv.index("--loglevel") + 1] == "info"

    def test_metrics_stay_on_loopback(self, tmp_path):
        argv = self._tunnel(tmp_path)._argv(tmp_path / "cloudflared", ())
        assert argv[argv.index("--metrics") + 1] == "127.0.0.1:0"

    def test_autoupdate_is_off(self, tmp_path):
        assert "--no-autoupdate" in self._tunnel(tmp_path)._argv(tmp_path / "cloudflared", ())

    def test_the_region_retry_is_not_carried_over(self, tmp_path):
        """That retry is for the quick tunnel's two-region SRV discovery.

        A named tunnel does not use it, so passing it through would be an
        unexplained flag on a code path that never needed it.
        """
        argv = self._tunnel(tmp_path)._argv(tmp_path / "cloudflared", ("--region", "us"))
        assert "--region" not in argv


class TestTheUrlIsKnownBeforeLaunch:
    def test_it_is_the_configured_hostname(self):
        tunnel = AccessTunnel(5173, HOSTNAME, tunnel_id="u", credentials=__import__("pathlib").Path("/c"))
        assert tunnel._initial_url() == f"https://{HOSTNAME}/"

    def test_there_is_no_dns_wait(self, monkeypatch):
        """A quick tunnel's record propagates after the URL is printed; this one
        was created once by the host with ``cloudflared tunnel route dns``.

        A 30 s propagation gate on every launch would be pure delay.
        """
        tunnel = AccessTunnel(5173, HOSTNAME, tunnel_id="u", credentials=__import__("pathlib").Path("/c"))
        called = []
        monkeypatch.setattr(tunnel, "_wait_dns_live", lambda *a, **k: called.append(1))
        tunnel._await_dns(f"https://{HOSTNAME}/")
        assert called == []


class TestStaleIngressSweep:
    def test_an_old_file_is_removed(self, tmp_path):
        old = tmp_path / "tunnel-999-5173.yml"
        old.write_text("x")
        os.utime(old, (0, 0))
        _sweep_stale_ingress(tmp_path)
        assert not old.exists()

    def test_a_fresh_file_is_left_alone(self, tmp_path):
        """Another *running* board owns it — deleting it would break that tunnel."""
        fresh = tmp_path / "tunnel-1-5273.yml"
        fresh.write_text("x")
        _sweep_stale_ingress(tmp_path)
        assert fresh.exists()

    def test_unrelated_files_are_never_touched(self, tmp_path):
        other = tmp_path / "notes.txt"
        other.write_text("x")
        os.utime(other, (0, 0))
        _sweep_stale_ingress(tmp_path)
        assert other.exists()


class TestTheFactory:
    """Which tier ``open_tunnel`` builds, and what it refuses to build."""

    def _clear(self, monkeypatch) -> None:
        for key in (
            "YEABOI_SHARE_MODE",
            "CLOUDFLARE_TUNNEL_ID",
            "CLOUDFLARE_TUNNEL_CREDENTIALS",
            "CLOUDFLARE_ACCESS_HOSTNAME",
            "CLOUDFLARE_ACCESS_HOSTNAME_RETRO",
            "CLOUDFLARE_ACCESS_HOSTNAME_POKER",
            "CLOUDFLARE_ACCESS_HOSTNAME_SHARE",
            "CLOUDFLARE_ACCESS_TEAM",
            "CLOUDFLARE_ACCESS_AUD",
            "CLOUDFLARE_ACCESS_ADMIN_EMAILS",
        ):
            monkeypatch.delenv(key, raising=False)

    def test_the_default_is_still_the_quick_tunnel(self, monkeypatch):
        from yeaboi.retro.tunnel import CloudflareTunnel

        self._clear(monkeypatch)
        transport = open_tunnel(5173, surface="retro")
        assert type(transport.tunnel) is CloudflareTunnel
        assert transport.gate is None
        assert transport.error == ""

    def test_an_unrecognised_mode_reads_as_quick(self, monkeypatch):
        """A typo must not enter the tier that promises *less*, silently — and
        must not enter the one that promises more, either. ``quick`` is the safe
        landing place because the caller shows a code gate for it regardless.
        """
        from yeaboi.retro.tunnel import CloudflareTunnel

        self._clear(monkeypatch)
        monkeypatch.setenv("YEABOI_SHARE_MODE", "acess")
        assert type(open_tunnel(5173, surface="retro").tunnel) is CloudflareTunnel

    def test_a_broken_access_config_never_builds_a_quick_tunnel(self, monkeypatch):
        """The invariant the whole tier exists to protect.

        A host who configured Cloudflare Access and silently got a public
        ``trycloudflare.com`` URL is worse off than one who got no share at all,
        because they believe something untrue about who can reach their board.
        """
        self._clear(monkeypatch)
        monkeypatch.setenv("YEABOI_SHARE_MODE", "access")
        monkeypatch.setenv("CLOUDFLARE_ACCESS_HOSTNAME", HOSTNAME)  # and nothing else

        built: list = []
        monkeypatch.setattr(
            "yeaboi.retro.tunnel.CloudflareTunnel.__init__",
            lambda self, *a, **k: built.append(1),
        )
        transport = open_tunnel(5173, surface="retro")
        assert transport.tunnel is None
        assert built == []
        assert "CLOUDFLARE_TUNNEL_ID" in transport.error

    def test_configured_but_never_switched_on_refuses_with_the_remedy(self, monkeypatch):
        """Access variables with YEABOI_SHARE_MODE never set is a host who
        followed the setup and missed the last line — they get the sentence,
        never a public trycloudflare.com URL."""
        self._clear(monkeypatch)
        monkeypatch.setenv("CLOUDFLARE_ACCESS_HOSTNAME", HOSTNAME)  # mode stays unset

        built: list = []
        monkeypatch.setattr(
            "yeaboi.retro.tunnel.CloudflareTunnel.__init__",
            lambda self, *a, **k: built.append(1),
        )
        transport = open_tunnel(5173, surface="retro")
        assert transport.tunnel is None
        assert built == []
        assert "verified users" in transport.error and "quick" in transport.error

    def test_an_explicit_quick_choice_with_stored_config_still_shares(self, monkeypatch):
        """Switching back to quick in Settings writes the mode explicitly — that
        host chose public links, and the stored Access config must not block them."""
        from yeaboi.retro.tunnel import CloudflareTunnel

        self._clear(monkeypatch)
        monkeypatch.setenv("YEABOI_SHARE_MODE", "quick")
        monkeypatch.setenv("CLOUDFLARE_ACCESS_HOSTNAME", HOSTNAME)
        assert type(open_tunnel(5173, surface="retro").tunnel) is CloudflareTunnel

    def test_unreachable_signing_keys_also_never_build_a_quick_tunnel(self, monkeypatch, tmp_path):
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        self._clear(monkeypatch)
        for key, value in (
            ("YEABOI_SHARE_MODE", "access"),
            ("CLOUDFLARE_TUNNEL_ID", "uuid-1"),
            ("CLOUDFLARE_TUNNEL_CREDENTIALS", str(creds)),
            ("CLOUDFLARE_ACCESS_HOSTNAME", HOSTNAME),
            ("CLOUDFLARE_ACCESS_TEAM", "acme"),
            ("CLOUDFLARE_ACCESS_AUD", "aud"),
        ):
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            "yeaboi.sharing.identity._fetch_json",
            lambda url: (_ for _ in ()).throw(OSError("no network")),
        )
        built: list = []
        monkeypatch.setattr(
            "yeaboi.retro.tunnel.CloudflareTunnel.__init__",
            lambda self, *a, **k: built.append(1),
        )
        transport = open_tunnel(5173, surface="retro")
        assert transport.tunnel is None
        assert built == []

    def test_a_complete_config_builds_the_named_tunnel_and_a_gate(self, monkeypatch, tmp_path):
        pytest.importorskip("jwt")
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        self._clear(monkeypatch)
        for key, value in (
            ("YEABOI_SHARE_MODE", "access"),
            ("CLOUDFLARE_TUNNEL_ID", "uuid-1"),
            ("CLOUDFLARE_TUNNEL_CREDENTIALS", str(creds)),
            ("CLOUDFLARE_ACCESS_HOSTNAME", HOSTNAME),
            ("CLOUDFLARE_ACCESS_TEAM", "acme"),
            ("CLOUDFLARE_ACCESS_AUD", "aud"),
            ("CLOUDFLARE_ACCESS_ADMIN_EMAILS", "ada@example.com"),
        ):
            monkeypatch.setenv(key, value)
        monkeypatch.setattr("yeaboi.sharing.identity.AccessVerifier.warm", lambda self: True)

        transport = open_tunnel(5291, surface="retro")
        assert isinstance(transport.tunnel, AccessTunnel)
        assert transport.tunnel.hostname == HOSTNAME
        assert transport.tunnel.port == 5291
        assert transport.gate is not None
        assert transport.error == ""


class TestStartRefusals:
    """``start()`` must clean up after itself on every refusal path."""

    def _tunnel(self, tmp_path) -> AccessTunnel:
        return AccessTunnel(5173, HOSTNAME, tunnel_id="uuid-1", credentials=tmp_path / "creds.json")

    def test_a_taken_hostname_refuses_with_a_remedy(self, tmp_path):
        claim_hostname(HOSTNAME)
        tunnel = self._tunnel(tmp_path)
        assert tunnel.start() is None
        assert "CLOUDFLARE_ACCESS_HOSTNAME_" in tunnel.last_error

    def test_a_refused_start_does_not_hold_the_claim(self, tmp_path, monkeypatch):
        """Otherwise one failed launch poisons the hostname for the whole session."""
        tunnel = self._tunnel(tmp_path)
        monkeypatch.setattr(tunnel, "_ensure_binary", lambda: None)
        assert tunnel.start() is None
        assert claim_hostname(HOSTNAME) is True

    def test_a_rejected_ingress_stops_the_launch(self, tmp_path, monkeypatch):
        """cloudflared's own validator is run before we publish anything.

        An unknown ingress key is treated as *unused* at run time rather than as
        an error, so a mistyped ``httpHostHeader`` would leave the Host rule
        silently unenforced while everything appeared to work.
        """
        tunnel = self._tunnel(tmp_path)
        monkeypatch.setattr(tunnel, "_ensure_binary", lambda: tmp_path / "cloudflared")
        monkeypatch.setattr(tunnel, "_validate_ingress", lambda b, i: "cloudflared said no")

        launched: list = []
        monkeypatch.setattr(
            "yeaboi.retro.tunnel.CloudflareTunnel.start",
            lambda self, **k: launched.append(1),
        )
        assert tunnel.start() is None
        assert launched == []
        assert tunnel.last_error == "cloudflared said no"

    def test_a_setup_error_is_translated_for_the_host(self, tmp_path, monkeypatch):
        """ "Tunnel failed to start" sends a host to their router.

        The answer is usually that they pointed at a credentials file that is
        not there, so say that instead.
        """
        tunnel = self._tunnel(tmp_path)
        monkeypatch.setattr(tunnel, "_ensure_binary", lambda: tmp_path / "cloudflared")
        monkeypatch.setattr(tunnel, "_validate_ingress", lambda b, i: "")
        monkeypatch.setattr("yeaboi.retro.tunnel.CloudflareTunnel.start", lambda self, **k: None)
        tunnel._log_tail.append("Tunnel credentials file /nope/creds.json not found")
        assert tunnel.start() is None
        assert "CLOUDFLARE_TUNNEL_CREDENTIALS" in tunnel.last_error
