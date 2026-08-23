"""What the cloudflared child process may see, and where it may reach.

The tunnel's whole security story rests on two properties that are currently
true by construction and would break silently:

* it forwards to **exactly one** loopback origin — the board's own port — so the
  blast radius of "the tunnel is up" is that one HTTP server and nothing else on
  the machine;
* it runs with an **allowlisted environment**, so the third-party binary never
  sees this process's API keys, and — just as importantly — cannot be
  reconfigured by an environment variable that found its way into ``.env``.

Neither property has a natural failure signal: adding an ingress or dropping the
``env=`` argument would work perfectly in every manual test. These tests are the
signal.

# See docs: "Guardrails" — the tunnel's trust boundary
"""

from __future__ import annotations

import platform
import stat

import pytest

from yeaboi.retro import tunnel


def _recording_cloudflared(tmp_path):
    """A fake cloudflared that records its argv and environment, then registers.

    Writes ``argv`` and ``env`` files next to itself so the test can assert on
    what the real ``Popen`` actually handed the child, rather than on what the
    code appears to pass.
    """
    script = tmp_path / "cloudflared"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > {tmp_path}/argv\n'
        f"env > {tmp_path}/env\n"
        'echo "INF |  https://fake-tunnel-abcd.trycloudflare.com  |" >&2\n'
        'echo "INF Registered tunnel connection connIndex=0 protocol=quic" >&2\n'
        "sleep 1\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _run(tmp_path, monkeypatch, port=5173):
    monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
    binary = _recording_cloudflared(tmp_path)
    t = tunnel.CloudflareTunnel(port, binary=binary)
    assert t.start(timeout=10), "fake tunnel should come up"
    t.stop()
    argv = (tmp_path / "argv").read_text().splitlines()
    env = dict(line.split("=", 1) for line in (tmp_path / "env").read_text().splitlines() if "=" in line)
    return argv, env


@pytest.mark.skipif(platform.system() == "Windows", reason="fake sh script is POSIX-only")
class TestOriginIsTheOnlyOne:
    def test_exactly_one_url_and_it_is_loopback(self, tmp_path, monkeypatch):
        """One origin, and it is this board's own port on localhost.

        A second ``--url`` (or a non-loopback one) would widen the tunnel from
        "this board" to "something else on the host's network" — which is the
        difference between sharing a retro and standing up an open proxy.
        """
        argv, _ = _run(tmp_path, monkeypatch, port=5199)
        assert argv.count("--url") == 1
        origin = argv[argv.index("--url") + 1]
        assert origin == "http://localhost:5199"

    def test_metrics_listener_is_pinned_to_loopback(self, tmp_path, monkeypatch):
        """cloudflared's own help warns its default metrics bind can be all-interfaces."""
        argv, _ = _run(tmp_path, monkeypatch)
        assert argv.count("--metrics") == 1
        assert argv[argv.index("--metrics") + 1].startswith("127.0.0.1:")

    def test_loglevel_is_never_debug(self, tmp_path, monkeypatch):
        """At debug, cloudflared logs request URLs and every header.

        This app's credentials travel as ``?token=…&admin=…``, so debug logging
        would put live tokens into the stream ``_drain`` reads and yeaboi's own
        log file. Pin it so an inherited TUNNEL_LOGLEVEL cannot turn it on.
        """
        argv, _ = _run(tmp_path, monkeypatch)
        assert argv.count("--loglevel") == 1
        assert argv[argv.index("--loglevel") + 1] == "info"

    def test_autoupdate_stays_off(self, tmp_path, monkeypatch):
        """A pinned, checksum-verified binary that updates itself is not pinned."""
        argv, _ = _run(tmp_path, monkeypatch)
        assert "--no-autoupdate" in argv


@pytest.mark.skipif(platform.system() == "Windows", reason="fake sh script is POSIX-only")
class TestChildEnvironment:
    def test_secrets_never_reach_the_child(self, tmp_path, monkeypatch):
        """config.load_dotenv() puts every API key in os.environ; none may travel."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-appear")
        monkeypatch.setenv("JIRA_API_TOKEN", "jira-should-never-appear")
        monkeypatch.setenv("NOTION_TOKEN", "ntn_should_never_appear")
        _, env = _run(tmp_path, monkeypatch)
        assert "ANTHROPIC_API_KEY" not in env
        assert "JIRA_API_TOKEN" not in env
        assert "NOTION_TOKEN" not in env
        assert not any("should-never-appear" in v or "should_never_appear" in v for v in env.values())

    def test_child_env_is_an_allowlist_not_a_denylist(self, tmp_path, monkeypatch):
        """Anything not named in _CHILD_ENV_KEYS is withheld, including unknowns.

        A denylist would need updating every time a new integration adds a
        credential; this asserts the shape that does not.
        """
        monkeypatch.setenv("SOME_FUTURE_INTEGRATION_TOKEN", "nope")
        _, env = _run(tmp_path, monkeypatch)
        assert "SOME_FUTURE_INTEGRATION_TOKEN" not in env
        # `env` in a POSIX shell may add its own `_`/PWD/SHLVL; ignore those.
        unexpected = set(env) - set(tunnel._CHILD_ENV_KEYS) - {"_", "PWD", "SHLVL"}
        assert not unexpected, f"unexpected variables reached cloudflared: {sorted(unexpected)}"

    def test_cloudflared_own_config_vars_are_withheld(self, tmp_path, monkeypatch):
        """The reason this is a control and not hygiene.

        TUNNEL_LOGLEVEL=debug makes cloudflared log every request URL and header;
        TUNNEL_URL retargets the origin; TUNNEL_METRICS moves the metrics
        listener. `.env` is read straight into os.environ, so any of these
        landing there would otherwise reconfigure the tunnel.
        """
        monkeypatch.setenv("TUNNEL_LOGLEVEL", "debug")
        monkeypatch.setenv("TUNNEL_URL", "http://localhost:9999")
        monkeypatch.setenv("TUNNEL_METRICS", "0.0.0.0:9998")
        _, env = _run(tmp_path, monkeypatch)
        assert "TUNNEL_LOGLEVEL" not in env
        assert "TUNNEL_URL" not in env
        assert "TUNNEL_METRICS" not in env

    def test_the_essentials_do_travel(self, tmp_path, monkeypatch):
        """The allowlist has to be usable: without PATH/HOME cloudflared cannot run."""
        _, env = _run(tmp_path, monkeypatch)
        assert env.get("PATH")

    def test_proxy_settings_travel(self, tmp_path, monkeypatch):
        """A corporate network routes the tunnel through a proxy; these carry no secret."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:8080")
        _, env = _run(tmp_path, monkeypatch)
        assert env.get("HTTPS_PROXY") == "http://proxy.corp:8080"
