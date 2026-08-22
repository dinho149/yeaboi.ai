"""The Cloudflare Access tier's named tunnel.

A quick tunnel gets a random name in Cloudflare's own zone, so no Access policy
can attach to it. This runs a *named* tunnel on a hostname the host controls,
from a generated ingress file that points at the port the server actually bound.

The ingress pins ``originRequest.httpHostHeader``, which is what makes the Host
rule in :mod:`yeaboi.sharing.identity` sound, and ``start()`` runs cloudflared's
own ``ingress validate`` before launching — an unknown ingress key would
otherwise leave that rule silently unenforced.

One named tunnel serves one hostname at a time: :func:`claim_hostname` refuses a
second board on the same hostname rather than let Cloudflare route requests to
whichever connector answers first.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from yeaboi.retro.tunnel import CloudflareTunnel, _child_env

logger = logging.getLogger(__name__)

# Spawn seam for the ingress validator — see ``retro.tunnel._popen``.
_run = subprocess.run

#: Named-tunnel setup failures, which are *host configuration* errors rather
#: than network weather. The whole usability of the tier rests on saying which
#: one happened: "tunnel failed to start" sends a host to their router, when the
#: answer is that they pointed at a credentials file that is not there.
_SETUP_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"[Tt]unnel credentials file .*not found|[Cc]annot determine default origin certificate"),
        "cloudflared could not read the tunnel credentials — check CLOUDFLARE_TUNNEL_CREDENTIALS",
    ),
    (
        re.compile(r"[Tt]unnel .* not found|couldn't find tunnel"),
        "Cloudflare does not know this tunnel — check CLOUDFLARE_TUNNEL_ID",
    ),
    (
        re.compile(r"failed to parse|[Uu]nused keys|[Cc]ouldn't start tunnel"),
        "cloudflared rejected the generated ingress file",
    ),
)

#: Ingress files older than this belong to a yeaboi that is no longer running —
#: a crash, a kill -9, anything that skipped ``stop()``. Swept on start so the
#: run directory cannot accumulate one file per crashed board forever.
# Above a 24h TUNNEL_TIMEOUT_MINUTES, so a board running a full timed life
# never has its own live ingress file swept out from under it. A board with
# expiry *disabled* (TUNNEL_TIMEOUT_MINUTES=0) can outlive this; harmless
# today — cloudflared reads the config once at launch — but a future reload
# feature must move the sweep to pid-liveness rather than age.
_STALE_INGRESS_AGE_SECONDS = 26 * 3600


# -- the hostname claim registry -------------------------------------------

_claims: set[str] = set()
_claims_lock = threading.Lock()


def claim_hostname(hostname: str) -> bool:
    """Reserve ``hostname`` for one tunnel in this process. False if already taken.

    **This guards a hazard the tier creates and the quick tier cannot have.** A
    named tunnel accepts many simultaneous connectors — that is how Cloudflare
    does high availability — so a host who opens a retro board *and* a poker
    board, both running the same tunnel with the same hostname, gets two
    connectors advertising ingress for one name. Cloudflare then sends each
    request to whichever answers, and teammates land on the retro board or the
    poker board essentially at random. A quick tunnel is immune because every
    launch mints a fresh hostname; here the hostname is the fixed point.

    Refusing the second board is the only safe answer, and the caller turns this
    into a remedy naming ``CLOUDFLARE_ACCESS_HOSTNAME_<SURFACE>``.
    """
    key = hostname.strip().lower()
    if not key:
        return False
    with _claims_lock:
        if key in _claims:
            return False
        _claims.add(key)
        return True


def release_hostname(hostname: str) -> None:
    """Release a claim taken by :func:`claim_hostname`. Safe to call twice."""
    with _claims_lock:
        _claims.discard(hostname.strip().lower())


# -- the generated ingress file --------------------------------------------


def _yaml_quote(value: str) -> str:
    """Quote a value for the generated ingress, escaping quotes and newlines."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        # Newlines and tabs need escaping too, and not only for containment: a
        # YAML double-quoted scalar *folds* a literal newline into a space, so
        # an unescaped one stays safely inside its scalar but silently arrives
        # as a different string than the host configured. Escaping makes the
        # value round-trip exactly, which is what the test asserts.
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def render_ingress(tunnel_id: str, credentials: Path, hostname: str, port: int) -> str:
    """The ingress document for one board on one hostname.

    ``httpHostHeader`` is not decoration. The server tells a tunnel-borne request
    from the host's own browser by its ``Host`` header (see
    :meth:`yeaboi.sharing.identity.AccessGate.requires_verification`), which is
    only ours to assert because this pins what cloudflared sends rather than
    leaving it to cloudflared's default.

    The catch-all ``http_status:404`` rule is required by cloudflared — an
    ingress list must end with a rule that has no hostname — and it is also the
    right behaviour: anything arriving for a name we do not serve gets nothing.
    """
    return (
        f"tunnel: {_yaml_quote(tunnel_id)}\n"
        f"credentials-file: {_yaml_quote(str(credentials))}\n"
        "ingress:\n"
        f"  - hostname: {_yaml_quote(hostname)}\n"
        f"    service: {_yaml_quote(f'http://localhost:{port}')}\n"
        "    originRequest:\n"
        f"      httpHostHeader: {_yaml_quote(hostname)}\n"
        '  - service: "http_status:404"\n'
    )


def _sweep_stale_ingress(run_dir: Path) -> None:
    """Delete ingress files left behind by a yeaboi that never reached ``stop()``."""
    cutoff = time.time() - _STALE_INGRESS_AGE_SECONDS
    for path in run_dir.glob("tunnel-*.yml"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                logger.info("access: swept a stale ingress file (%s)", path.name)
        except OSError:  # noqa: PERF203 - one unreadable file must not stop the sweep
            continue


class AccessTunnel(CloudflareTunnel):
    """A named Cloudflare tunnel serving one board at one Access-protected hostname.

    Inherits the whole of :class:`CloudflareTunnel` — the drain thread, the edge
    registration gate, the expiry timer, teardown, the allowlisted child
    environment — and differs in exactly three places: the command line, the fact
    that the URL is known before launch instead of read from a banner, and that
    there is no DNS propagation to wait for.
    """

    def __init__(
        self,
        port: int,
        hostname: str,
        *,
        tunnel_id: str,
        credentials: Path,
        binary: Path | None = None,
        on_expire: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(port, binary=binary, on_expire=on_expire)
        self.hostname = hostname.strip().lower()
        self.tunnel_id = tunnel_id
        self.credentials = credentials
        #: Set when start() fails, in the host's terms rather than cloudflared's.
        self.last_error = ""
        self._ingress: Path | None = None
        self._claimed = False

    # -- the three differences ------------------------------------------

    def _initial_url(self) -> str:
        return f"https://{self.hostname}/"

    def _await_dns(self, url: str) -> None:
        """No-op: the hostname is a stable record the host created once."""
        return None

    def _argv(self, binary: Path, extra_args: tuple[str, ...]) -> list[str]:
        """``cloudflared tunnel --config <ingress> run``.

        ``--config`` is a *tunnel command* option and so must come before ``run``
        — verified against the pinned binary's help, which places it under
        "TUNNEL COMMAND OPTIONS" while ``--credentials-file`` sits under
        "SUBCOMMAND OPTIONS" after ``run``. The tunnel is named in the config
        file rather than passed positionally, which the same help documents as
        equivalent.

        ``extra_args`` (the ``--region us`` retry) is ignored: that retry exists
        for the quick tunnel's two-region SRV discovery, which a named tunnel
        does not use.

        The two pinned flags from the parent carry over for the same reasons —
        ``--loglevel info`` because at ``debug`` cloudflared logs every request
        URL and all headers, and ``--metrics 127.0.0.1:0`` because its default
        binds to all interfaces in virtualized environments.
        """
        assert self._ingress is not None
        return [
            str(binary),
            "tunnel",
            "--no-autoupdate",
            "--loglevel",
            "info",
            "--metrics",
            "127.0.0.1:0",
            "--config",
            str(self._ingress),
            "run",
            "--credentials-file",
            str(self.credentials),
        ]

    # -- lifecycle -------------------------------------------------------

    def start(self, *, timeout: float = 45.0) -> str | None:
        """Claim the hostname, generate and validate the ingress, then launch.

        Returns ``None`` on any failure with :attr:`last_error` set to something
        a host can act on. **It never falls back to a quick tunnel** — that is
        the invariant the whole tier exists to protect. A host who asked for
        Access and silently got a public ``trycloudflare.com`` URL is worse off
        than one who got no share at all, because they believe something untrue.
        """
        from yeaboi.paths import get_run_dir

        self.last_error = ""
        if not claim_hostname(self.hostname):
            self.last_error = (
                f"{self.hostname} is already serving another yeaboi share — "
                f"set CLOUDFLARE_ACCESS_HOSTNAME_<SURFACE> to give this one its own hostname"
            )
            logger.warning("access: %s", self.last_error)
            return None
        self._claimed = True

        binary = self._binary or self._ensure_binary()
        if binary is None:
            self.last_error = "cloudflared is not available"
            self._release()
            return None
        self._binary = binary

        run_dir = get_run_dir()
        _sweep_stale_ingress(run_dir)
        ingress = run_dir / f"tunnel-{os.getpid()}-{self.port}.yml"
        try:
            ingress.write_text(
                render_ingress(self.tunnel_id, self.credentials, self.hostname, self.port),
                encoding="utf-8",
            )
            # 0600: this names the loopback port a live board is served on and
            # the path to the tunnel credentials. Another local user being able
            # to rewrite it would let them retarget the tunnel on next launch.
            ingress.chmod(0o600)
        except OSError as e:
            self.last_error = f"could not write the tunnel ingress file: {e}"
            logger.warning("access: %s", self.last_error)
            self._release()
            return None
        self._ingress = ingress

        problem = self._validate_ingress(binary, ingress)
        if problem:
            self.last_error = problem
            self._cleanup_ingress()
            self._release()
            return None

        url = super().start(timeout=timeout)
        if url is None:
            self.last_error = self._explain_failure()
            self._cleanup_ingress()
            self._release()
            return None
        logger.info("access: named tunnel ready at %s (local_port=%d)", self.hostname, self.port)
        return url

    def _ensure_binary(self) -> Path | None:
        from yeaboi.retro.tunnel import ensure_cloudflared

        return ensure_cloudflared()

    def _validate_ingress(self, binary: Path, ingress: Path) -> str:
        """Run cloudflared's own ingress validator. Returns ``""`` when it is happy.

        This closes the one hazard that would otherwise be invisible: cloudflared
        treats an unknown ingress key as *unused* rather than as an error at run
        time, so a mistyped ``httpHostHeader`` would leave the Host-header rule
        silently unenforced while everything appeared to work. The validator
        names the offending field, so we can refuse to publish instead.
        """
        try:
            result = _run(  # noqa: S603 - fixed, app-managed binary + generated path
                [str(binary), "tunnel", "--config", str(ingress), "ingress", "validate"],
                capture_output=True,
                text=True,
                timeout=20,
                env=_child_env(),
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as e:
            # Could not run the validator at all. Do not treat this as a pass:
            # the Host rule's soundness is what is being checked.
            logger.warning("access: could not validate the generated ingress: %s", e)
            return f"could not validate the tunnel ingress file: {e}"
        output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode != 0 or "unused keys" in output.lower():
            logger.warning("access: cloudflared rejected the generated ingress:\n%s", output)
            return "cloudflared rejected the generated ingress file — this is a bug, please report it"
        logger.info("access: generated ingress validated by cloudflared")
        return ""

    def _explain_failure(self) -> str:
        """Turn cloudflared's last words into something the host can act on."""
        tail = "\n".join(self._log_tail)
        for pattern, hint in _SETUP_HINTS:
            if pattern.search(tail):
                return hint
        return "the named tunnel did not come up — see the log for cloudflared's output"

    def stop(self) -> None:
        super().stop()
        self._cleanup_ingress()
        self._release()

    def _cleanup_ingress(self) -> None:
        if self._ingress is None:
            return
        try:
            self._ingress.unlink(missing_ok=True)
        except OSError as e:
            logger.debug("access: could not remove the ingress file: %s", e)
        self._ingress = None

    def _release(self) -> None:
        if self._claimed:
            release_hostname(self.hostname)
            self._claimed = False
