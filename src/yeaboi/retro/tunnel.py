"""Optional public tunnel for the Retro board — off-network joining, zero setup.

The board's server (retro/server.py) binds loopback, so on its own it reaches
nobody. This is what lets anyone in: a **Cloudflare quick tunnel**, started
automatically when a board opens, exposing ``http://localhost:<port>`` at a
random ``https://…trycloudflare.com`` URL. Crucially this needs **no Cloudflare account, no token, no signup** — so the
app can own the whole flow: it downloads the ``cloudflared`` binary on first use
(cached under ``~/.scrum-agent/bin/``) and runs it. (ngrok, by contrast, forces a
per-user authtoken, so it can't be truly zero-setup.)

The tunnel forwards to our existing token-gated server, so ``/api/*`` stays
protected, and it upgrades the hop to HTTPS. This makes the retro "anyone with
the link *and* the join code can join" — fine for a retrospective, but it is
internet-reachable while the tunnel is up.

Everything here is best-effort and never raises into the TUI: a failed download
or tunnel start returns ``None`` / a status string. The board keeps working for
the host on ``127.0.0.1``, but with nothing to share until a tunnel comes up —
the TUI shows the failure and offers a retry.

# See docs: "Retro" — remote joining via Cloudflare tunnel
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# The single seam through which cloudflared is spawned, so tests can block the
# real binary in one place (tests/conftest.py::_no_real_tunnel_spawn).
_popen = subprocess.Popen

# cloudflared prints the assigned URL to stderr inside a banner box; match it anywhere.
_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# The URL banner only means the hostname was *allocated* — it prints before cloudflared
# has connected to Cloudflare's edge. A visitor who opens the URL in that window (or when
# the connection never comes up at all, e.g. the network blocks QUIC/UDP 7844 and the
# http2 fallback too) gets Cloudflare error 1033: the hostname resolves, but no tunnel is
# registered behind it. Readiness therefore additionally gates on this log line, which
# cloudflared emits once per established edge connection.
_REGISTERED_RE = re.compile(r"Registered tunnel connection")

# Minimum time to wait for edge registration after the URL banner. Registration normally
# lands within a second, but when QUIC (UDP 7844) is blocked cloudflared only falls back
# to http2 after its QUIC attempts time out, which takes a while.
_REGISTER_GRACE = 20.0

# cloudflared discovers the edge via DNS SRV records for two global regions and exits when
# the local resolver answers incompletely ("expected at least 2 Cloudflare Regions…") —
# seen with ISP-router/filtering DNS. Pinning ``--region us`` queries per-region hostnames
# instead and drops the two-region requirement, so start() retries with it on this error.
_REGION_SRV_RE = re.compile(r"expected at least \d+ Cloudflare Regions")

# We pin an exact cloudflared release (not the moving ``latest`` tag) and verify the
# downloaded bytes against a bundled SHA-256 map before we ever mark the file
# executable or run it. This closes the supply-chain gap: even if GitHub served a
# tampered payload, or ``latest`` moved to a backdoored release, the hash mismatch
# makes us fail closed (delete the temp file, raise — the caller stays LAN-only).
# To bump: pick a new tag, recompute hashes for every asset below, update both.
_CLOUDFLARED_VERSION = "2026.7.2"
_RELEASE_BASE = f"https://github.com/cloudflare/cloudflared/releases/download/{_CLOUDFLARED_VERSION}"

# SHA-256 of each supported release asset (the downloaded bytes: the ``.tgz`` on
# macOS, the raw binary elsewhere). An asset absent from this map cannot be
# verified and is therefore refused.
_ASSET_SHA256 = {
    "cloudflared-darwin-arm64.tgz": "2086e51c61d6565781d84117a5007d0c826d03ffdc74acb91c08c167f9f8cd7c",
    "cloudflared-darwin-amd64.tgz": "4ee0d3b48a990a2f9b5faec5838f73ec1f400aa8e0a4864be576adfafec406cb",
    "cloudflared-linux-amd64": "ec905ea7b7e327ff8abdde8cb64697a2152de74dbcdbf6aec9db8364eb3886cd",
    "cloudflared-linux-arm64": "405df476437e027fc6d18729a5a77155c0a33a6082aeee60a799a688f3052e66",
    "cloudflared-linux-386": "cbad04f2700ae4d4971fe07e9ded67327142f2d3338aef86ae04e6042f7ce990",
    "cloudflared-windows-amd64.exe": "cdb5d4432f6ae1595654a692a51308b69d2bf7af961f5578d9391837cf072df9",
    "cloudflared-windows-386.exe": "32decf512bb37dfcf8f915e923b8132803cb0f7262995d0b168495694b1ee2d7",
}


# The only environment cloudflared gets. Withholding the rest keeps this
# process's API keys away from a third-party binary, and keeps TUNNEL_* out of
# its configuration — TUNNEL_LOGLEVEL=debug would make it log request URLs,
# which carry ?token=…&admin=…, into yeaboi's own log.
_CHILD_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TZ",
    # Windows needs these to resolve anything at all.
    "SYSTEMROOT",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "COMSPEC",
    "PATHEXT",
    # A corporate network may route the tunnel through a proxy; without these
    # cloudflared simply cannot connect, and they carry no secret of ours.
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


def _child_env() -> dict[str, str]:
    """The allowlisted environment handed to cloudflared. See _CHILD_ENV_KEYS."""
    return {key: os.environ[key] for key in _CHILD_ENV_KEYS if key in os.environ}


def _asset_name(system: str | None = None, machine: str | None = None) -> tuple[str, bool]:
    """Return (github_asset_filename, is_tgz) for the current platform.

    macOS assets ship as ``.tgz`` archives; Linux/Windows are raw binaries.
    """
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("i386", "i686", "x86"):
        arch = "386"
    else:
        arch = machine
    if system == "darwin":
        return f"cloudflared-darwin-{arch}.tgz", True
    if system == "linux":
        return f"cloudflared-linux-{arch}", False
    if system == "windows":
        return f"cloudflared-windows-{arch}.exe", False
    raise OSError(f"unsupported platform for cloudflared: {system}/{machine}")


def _cached_binary_path() -> Path:
    """Return the path where the app caches its own cloudflared binary."""
    from yeaboi.paths import get_bin_dir

    name = "cloudflared.exe" if platform.system().lower() == "windows" else "cloudflared"
    return get_bin_dir() / name


def _make_executable(path: Path) -> None:
    """Make ``path`` runnable by its owner and by nobody else.

    Group and other bits are masked off explicitly rather than left to the umask.
    """
    mode = path.stat().st_mode & ~(stat.S_IRWXG | stat.S_IRWXO)
    path.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def _verify_sha256(asset: str, data: bytes) -> None:
    """Raise unless ``data`` matches the pinned SHA-256 for ``asset``.

    An asset with no pinned hash is refused (fail closed) rather than trusted.
    """
    expected = _ASSET_SHA256.get(asset)
    if expected is None:
        raise OSError(f"no pinned checksum for cloudflared asset {asset!r}; refusing to install")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise OSError(f"cloudflared checksum mismatch for {asset!r}: expected {expected}, got {actual}")


def _download_cloudflared(dest: Path, *, timeout: int = 120) -> Path:
    """Download (and extract, on macOS) the cloudflared binary to ``dest``.

    Downloads over HTTPS from a **pinned** ``cloudflare/cloudflared`` GitHub
    release and verifies the bytes against a bundled SHA-256 before installing.
    Raises on failure (including checksum mismatch); the caller degrades
    gracefully to LAN-only.
    """
    import urllib.request

    asset, is_tgz = _asset_name()
    url = f"{_RELEASE_BASE}/{asset}"
    logger.info("retro: downloading cloudflared from %s", url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - trusted, pinned GitHub release
        data = resp.read()
    # Verify BEFORE writing/extracting/executing: a tampered payload never lands on disk.
    _verify_sha256(asset, data)
    if is_tgz:
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member = next((m for m in tar.getmembers() if m.name.endswith("cloudflared")), None)
            if member is None:
                raise OSError("cloudflared not found inside downloaded archive")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise OSError("could not extract cloudflared from archive")
            tmp.write_bytes(extracted.read())
    else:
        tmp.write_bytes(data)
    tmp.replace(dest)
    _make_executable(dest)
    # Record what we just installed, so later launches can prove the file on
    # disk is still this one (the pinned map covers the .tgz, not the binary).
    _record_installed_digest(dest)
    logger.info("retro: cloudflared cached at %s", dest)
    return dest


def _sidecar_path(binary: Path) -> Path:
    """Where the installed binary's own SHA-256 is recorded."""
    return binary.with_name(binary.name + ".sha256")


def _record_installed_digest(binary: Path) -> None:
    """Write the installed binary's SHA-256 beside it, for re-verification.

    The pinned ``_ASSET_SHA256`` map covers the *downloaded asset*, which on
    macOS is a ``.tgz`` — so it cannot be used to re-check the extracted binary
    later. We therefore record what we installed at the moment we installed it,
    immediately after the asset's pinned hash checked out. That is what makes a
    later re-check meaningful: it proves the file on disk is still the one that
    came out of the verified archive.
    """
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    _sidecar_path(binary).write_text(digest, encoding="utf-8")


def _cached_binary_is_intact(binary: Path) -> bool:
    """True if the cached binary still matches the digest recorded at install.

    A missing sidecar counts as *not* intact: it means the copy predates this
    check (or was placed there by something else), and re-downloading a 38 MB
    file once is a much better trade than executing an unverified binary.
    """
    sidecar = _sidecar_path(binary)
    try:
        expected = sidecar.read_text(encoding="utf-8").strip()
    except OSError:
        logger.info("retro: cached cloudflared has no recorded digest — re-installing")
        return False
    actual = hashlib.sha256(binary.read_bytes()).hexdigest()
    if actual != expected:
        logger.warning(
            "retro: cached cloudflared digest changed since install (expected %s, got %s) — re-installing",
            expected,
            actual,
        )
        return False
    return True


def ensure_cloudflared() -> Path | None:
    """Return a path to a runnable cloudflared, downloading it on first use.

    Resolution order: ``CLOUDFLARED_PATH`` env → a ``cloudflared`` already on
    PATH → the app's cached copy → download. Returns ``None`` if it cannot be
    obtained (caller shows a status message and stays LAN-only).

    Two of those four sources carry no guarantee at all: an env override and a
    binary on ``PATH`` are whatever the machine happens to offer, so the pinned
    checksum that guards the download path never sees them. That is a reasonable
    default — a user who installed cloudflared themselves should be able to use
    it — but it is not what someone hardening a share wants, so
    ``YEABOI_CLOUDFLARED_STRICT=1`` refuses both and accepts only the managed,
    hash-verified copy.

    The cached copy is now re-verified on every call rather than trusted because
    it exists; see :func:`_cached_binary_is_intact` for why that check is against
    a recorded digest rather than the pinned asset map.
    """
    from yeaboi.config import cloudflared_strict

    strict = cloudflared_strict()

    override = os.getenv("CLOUDFLARED_PATH")
    if override and Path(override).exists():
        if strict:
            logger.warning("retro: ignoring CLOUDFLARED_PATH — YEABOI_CLOUDFLARED_STRICT allows only the pinned build")
        else:
            logger.info("retro: using cloudflared from CLOUDFLARED_PATH (%s) — not checksum-verified", override)
            return Path(override)

    on_path = shutil.which("cloudflared")
    if on_path:
        if strict:
            logger.warning(
                "retro: ignoring cloudflared on PATH — YEABOI_CLOUDFLARED_STRICT allows only the pinned build"
            )
        else:
            logger.info("retro: using cloudflared from PATH (%s) — not checksum-verified", on_path)
            return Path(on_path)

    cached = _cached_binary_path()
    if cached.exists() and _cached_binary_is_intact(cached):
        logger.info("retro: using the managed cloudflared build (%s)", cached)
        return cached

    try:
        binary = _download_cloudflared(cached)
    except Exception as e:
        logger.warning("retro: failed to obtain cloudflared: %s", e)
        return None
    logger.info("retro: installed the pinned cloudflared %s at %s", _CLOUDFLARED_VERSION, binary)
    return binary


class CloudflareTunnel:
    """A Cloudflare quick tunnel forwarding a public HTTPS URL to a local port."""

    def __init__(self, port: int, *, binary: Path | None = None, on_expire: Callable[[], None] | None = None) -> None:
        self.port = port
        self._binary = binary
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._url = ""
        # Last stderr lines from cloudflared — surfaced on failure so the real reason
        # (QUIC blocked, trycloudflare 5xx, protocol deprecated, rate-limit) is visible.
        # Sized to hold both launches' output when the --region retry also fails, so the
        # first attempt's root cause is still in the final warning.
        self._log_tail: deque[str] = deque(maxlen=30)
        # Set by stop(): cancels an in-flight start() so a teardown during the handshake
        # can never be followed by a retry launching a cloudflared nobody will stop.
        self._stopped = threading.Event()
        # Called once (from the timer thread) when the tunnel auto-expires — lets the
        # TUI screen that owns this tunnel update its own state (e.g. offer "Retry
        # Link"). Never called on a manual stop() — see stop()'s timer cancellation.
        self._on_expire = on_expire
        self._expire_timer: threading.Timer | None = None
        # Deadline for the armed expiry timer, in time.monotonic() terms — lets a
        # caller (a live board's per-frame status line) warn a host before the link
        # dies out from under a ceremony still in progress, rather than the tunnel
        # just vanishing with no notice. None whenever no timer is armed.
        self._expires_at: float | None = None

    @property
    def public_url(self) -> str:
        return self._url

    def time_until_expiry(self) -> float | None:
        """Seconds until the auto-expiry timer fires, or ``None`` if none is armed.

        A quick tunnel gets a fresh random hostname on every launch, so once this
        tunnel expires the old invite link is gone for good — Retry Link produces a
        *different* URL. Polling this lets a live board warn its host while there is
        still time to act, instead of the link just dying mid-ceremony.
        """
        if self._expire_timer is None or self._expires_at is None:
            return None
        return max(0.0, self._expires_at - time.monotonic())

    def start(self, *, timeout: float = 45.0) -> str | None:
        """Launch cloudflared and wait up to ``timeout`` s for a *connected* tunnel.

        ``timeout`` is one overall budget covering the URL banner, edge registration,
        and — if the first launch dies to the two-region SRV error — the single
        ``--region us`` retry; the ~30 s DNS-propagation gate runs after that. Returns
        the ``https://…trycloudflare.com`` URL once cloudflared has registered an edge
        connection behind it, or ``None`` on failure or when ``stop()`` cancels an
        in-flight start.
        """
        binary = self._binary or ensure_cloudflared()
        if binary is None:
            return None
        self._binary = binary
        self._stopped.clear()

        deadline = time.monotonic() + timeout
        url = self._attempt(binary, deadline=deadline)
        if url is None and not self._stopped.is_set() and any(_REGION_SRV_RE.search(line) for line in self._log_tail):
            # Broken local DNS (see _REGION_SRV_RE): cloudflared allocated the URL but
            # exited before connecting — the persistent-error-1033 shape. Pinning one
            # region uses per-region hostnames the same resolver answers fine. The tail
            # is kept, not cleared, so a failed retry's warning still shows this root cause.
            logger.warning(
                "retro: local DNS returned incomplete Cloudflare region records — retrying pinned to the US region"
            )
            url = self._attempt(binary, deadline=deadline, extra_args=("--region", "us"))
        if url is None:
            return None

        self._await_dns(url)
        logger.info("cloudflare tunnel ready (%s, local_port=%d)", type(self).__name__, self.port)
        self._schedule_expiry()
        return url

    def _initial_url(self) -> str:
        """The public URL when it is known before launch, or ``""`` to read it from stderr.

        A quick tunnel's hostname is assigned by Cloudflare and announced in a
        banner, so this is empty and :meth:`_attempt` waits for it. A *named*
        tunnel is served at a hostname the host already owns and already routed,
        so the URL is known before cloudflared starts — and there is no banner to
        wait for, which is why this is a hook rather than a regex the Access tier
        would have to pretend to match.
        """
        return ""

    def _await_dns(self, url: str) -> None:
        """Block until the tunnel hostname resolves publicly.

        cloudflared prints the URL several seconds BEFORE the quick-tunnel
        hostname's DNS record actually goes live. Handing the URL out at that
        instant means a teammate who opens it immediately hits NXDOMAIN — which
        their browser/OS then *negatively caches*, so even retries keep failing
        for a while. Wait until the record is globally resolvable before
        declaring the tunnel ready.

        The Access tier overrides this to do nothing: its hostname is a stable
        record the host created once with ``cloudflared tunnel route dns``, so
        there is no propagation race to wait out and a 30 s gate on every launch
        would be pure delay.
        """
        host = url.split("://", 1)[-1].split("/", 1)[0]
        self._wait_dns_live(host, deadline=time.monotonic() + 30.0)

    def _schedule_expiry(self) -> None:
        """Arm the auto-expiry timer per ``TUNNEL_TIMEOUT_MINUTES`` (0 = never).

        Only called after a *successful* start — a tunnel that never came up
        has nothing to expire, and arming a timer on a failed attempt would
        leak a live (if daemon) thread into callers/tests that never call
        stop() on a failure.
        """
        from yeaboi.config import get_tunnel_timeout_minutes

        minutes = get_tunnel_timeout_minutes()
        if minutes <= 0 or self._stopped.is_set():
            return
        timer = threading.Timer(minutes * 60.0, self._expire)
        timer.daemon = True
        self._expire_timer = timer
        self._expires_at = time.monotonic() + minutes * 60.0
        timer.start()
        logger.info("retro: tunnel will auto-expire after %d minute(s)", minutes)

    def _expire(self) -> None:
        """Timer callback: stop the tunnel and notify the owning screen."""
        if self._stopped.is_set():
            return
        logger.info("retro: cloudflare tunnel auto-expired (local_port=%d)", self.port)
        self.stop()
        if self._on_expire is not None:
            try:
                self._on_expire()
            except Exception:  # noqa: BLE001 - never let a UI callback crash the timer thread
                logger.warning("retro: on_expire callback raised", exc_info=True)

    def _attempt(self, binary: Path, *, deadline: float, extra_args: tuple[str, ...] = ()) -> str | None:
        """One cloudflared launch: wait for the URL banner, then for an edge connection.

        On any failure the process is stopped and ``None`` returned; ``_log_tail`` keeps
        cloudflared's last lines so the caller can see *why* (and decide to retry).
        """
        if self._stopped.is_set():
            return None
        logger.info(
            "retro: starting cloudflare tunnel for localhost:%d (binary=%s, extra_args=%s)",
            self.port,
            binary,
            " ".join(extra_args) or "-",
        )
        try:
            proc = _popen(  # noqa: S603 - fixed, app-managed binary + args
                self._argv(binary, extra_args),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                # Withhold this process's environment — see _CHILD_ENV_KEYS. This
                # is load-bearing for the flags above too: an inherited
                # TUNNEL_LOGLEVEL would otherwise override what we pass.
                env=_child_env(),
                # Its own process group. Sharing ours means a Ctrl-C or a
                # terminal hangup is delivered to both, and teardown ordering
                # becomes the OS's choice — while every caller here assumes the
                # child's lifetime is stop()'s to control.
                start_new_session=True,
            )
        except OSError as e:
            logger.warning("retro: could not launch cloudflared: %s", e)
            return None
        self._proc = proc
        self._url = ""
        if self._stopped.is_set():  # stop() raced the launch and missed the new process
            self._terminate()
            return None

        found = threading.Event()
        registered = threading.Event()
        # Per-attempt URL slot: a previous attempt's drain thread that outlives its
        # join(timeout=2) must never be able to write this attempt's (or the final) URL.
        # Pre-filled for a named tunnel, whose URL is known before launch — which
        # both skips the banner wait below and keeps _drain's regex from ever
        # firing on a line that merely mentions a URL.
        state = {"url": self._initial_url()}
        if state["url"]:
            found.set()

        def _drain() -> None:
            # Keep reading stderr for the tunnel's whole life: capture the URL once,
            # then keep draining so cloudflared's pipe buffer never fills and blocks it.
            # Every line is logged (DEBUG) + kept in a small tail so a failure is
            # diagnosable — previously cloudflared's own output was discarded.
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    self._log_tail.append(line)
                    logger.debug("cloudflared: %s", line)
                if not state["url"]:
                    m = _URL_RE.search(line)
                    if m:
                        state["url"] = m.group(0)
                        found.set()
                if not registered.is_set() and _REGISTERED_RE.search(line):
                    registered.set()

        self._reader = threading.Thread(target=_drain, name="retro-tunnel", daemon=True)
        self._reader.start()

        # Wait for the URL, but bail early if the process exits or stop() cancels us.
        while time.monotonic() < deadline:
            if found.wait(timeout=0.2):
                break
            if self._stopped.is_set():
                self._terminate()
                return None
            if proc.poll() is not None:  # cloudflared exited before emitting a URL
                logger.warning("retro: cloudflared exited early (code %s)", proc.returncode)
                break

        if not state["url"]:
            # Surface cloudflared's own last words at warning level so the reason is
            # visible in retro.log without enabling DEBUG.
            if self._log_tail:
                logger.warning(
                    "retro: cloudflare tunnel failed to produce a URL — cloudflared said:\n%s",
                    "\n".join(self._log_tail),
                )
            else:
                logger.warning("retro: cloudflare tunnel failed to produce a URL (no cloudflared output)")
            self._terminate()
            return None

        # The URL alone is not readiness: until cloudflared registers an edge connection,
        # visitors get Cloudflare error 1033 (hostname allocated, no tunnel behind it).
        # Registration normally lands within a second of the URL; cap the extra wait at
        # _REGISTER_GRACE (QUIC→http2 fallback time) while staying inside the caller's budget.
        if not self._wait_registered(proc, registered, deadline=min(deadline, time.monotonic() + _REGISTER_GRACE)):
            logger.warning(
                "retro: cloudflare tunnel URL was issued but no edge connection registered — visitors "
                "would see Cloudflare error 1033. The network may block the tunnel protocol "
                "(QUIC/UDP 7844 and the http2 fallback). binary=%s; cloudflared said:\n%s",
                binary,
                "\n".join(self._log_tail) or "(no output)",
            )
            self._terminate()
            return None
        self._url = state["url"]
        return self._url

    def _argv(self, binary: Path, extra_args: tuple[str, ...]) -> list[str]:
        """The full cloudflared command line for one launch.

        Extracted from :meth:`_attempt` so the Access tier's named tunnel can
        override just this, inheriting the drain thread, the readiness gate, the
        expiry timer and teardown unchanged.

        Two flags are pinned rather than left to cloudflared's defaults:

        ``--loglevel info`` — at ``debug`` cloudflared logs every request URL and
        all request and response headers. This app's credentials ride in the
        query string, so debug logging would put live tokens into the stream
        ``_drain`` reads. The default is already ``info``; pinning it means an
        environment variable cannot change that.

        ``--metrics 127.0.0.1:0`` — cloudflared's own help warns that its default
        metrics listener "binds to all interfaces" in virtualized environments.
        Nothing about this app wants a metrics port reachable off-box.
        """
        return [
            str(binary),
            "tunnel",
            "--no-autoupdate",
            "--loglevel",
            "info",
            "--metrics",
            "127.0.0.1:0",
            *extra_args,
            "--url",
            f"http://localhost:{self.port}",
        ]

    def _wait_registered(self, proc: subprocess.Popen, registered: threading.Event, *, deadline: float) -> bool:
        """Block until cloudflared registers an edge connection, or the deadline/process death.

        Returns True once ``Registered tunnel connection`` has been seen on stderr. Bails
        early (False) if cloudflared exits first — no connection is coming from a dead
        process — or if ``stop()`` cancelled the start.
        """
        while time.monotonic() < deadline:
            if registered.wait(timeout=0.2):
                logger.info("retro: cloudflare tunnel edge connection registered")
                return True
            if self._stopped.is_set():
                logger.info("retro: tunnel start cancelled while waiting for edge registration")
                return False
            if proc.poll() is not None:
                logger.warning("retro: cloudflared exited before registering a connection")
                return False
        return registered.is_set()

    def _dns_query(self, base: str, host: str) -> bool | None:
        """DoH A-record lookup. Returns True (resolves), False (NXDOMAIN), None (endpoint error)."""
        import json
        import urllib.parse
        import urllib.request

        try:
            q = urllib.parse.urlencode({"name": host, "type": "A"})
            # Fixed, trusted public DoH endpoints (dns.google / 1.1.1.1).
            req = urllib.request.Request(f"{base}?{q}", headers={"Accept": "application/dns-json"})  # noqa: S310
            with urllib.request.urlopen(req, timeout=4) as resp:  # noqa: S310
                data = json.load(resp)
            return bool(data.get("Status") == 0 and data.get("Answer"))
        except Exception:  # noqa: BLE001 - any DoH hiccup means "unknown, try again"
            return None

    def _wait_dns_live(self, host: str, *, deadline: float) -> bool:
        """Block until ``host`` resolves on an *external* resolver, so we never advertise a
        not-yet-propagated URL.

        A quick tunnel's DNS record propagates ~4 s after cloudflared prints the URL. If a
        browser opens it before then, the resolver *negatively caches* the NXDOMAIN for the
        full SOA window (30 min for trycloudflare.com) — so the URL is dead-on-arrival even
        once it goes live. We therefore wait until it's resolvable before handing it out.

        Crucially we gate on **Google DoH (dns.google)**, not Cloudflare's own 1.1.1.1:
        1.1.1.1 knows about Cloudflare's quick tunnels *instantly*, so it would report
        "ready" seconds before an ordinary (non-Cloudflare) resolver — exactly the window
        that poisons a joining teammate's cache. Google resolving it means external resolvers
        genuinely see it. We fall back to Cloudflare only if Google DoH is unreachable.

        Best-effort: on timeout we still return the URL (the tunnel is up per cloudflared),
        but log a warning — a persistently-unresolvable host usually means the joining
        network blocks ``trycloudflare.com``.
        """
        google, cloudflare = "https://dns.google/resolve", "https://1.1.1.1/dns-query"
        start = time.monotonic()
        google_reachable = False
        while time.monotonic() < deadline:
            ext = self._dns_query(google, host)
            if ext:  # an ordinary public resolver sees it → joining teammates will too
                logger.info("cloudflare quick tunnel DNS propagated")
                time.sleep(3.0)  # small settle for slower downstream resolvers
                return True
            if ext is not None:  # reachable (just NXDOMAIN for now) — keep waiting on Google
                google_reachable = True
            # Only fall back to Cloudflare's own resolver if Google DoH has been
            # *persistently* unreachable (restricted network) — never on a single hiccup,
            # which would forfeit the external-propagation guarantee and re-poison caches.
            elif not google_reachable and (time.monotonic() - start) > (deadline - start) * 0.5:
                if self._dns_query(cloudflare, host):
                    logger.info("cloudflare quick tunnel DNS live (cloudflare resolver; google unreachable)")
                    time.sleep(3.0)
                    return True
            time.sleep(1.5)
        logger.warning(
            "cloudflare quick tunnel host not resolvable via public DNS yet — give it a few more "
            "seconds, or the joining network may block/slow trycloudflare.com (NXDOMAIN).",
        )
        return False

    def stop(self) -> None:
        """Terminate the tunnel and cancel any in-flight ``start()`` (including its retry).

        The cancel flag is what makes teardown-during-setup safe: without it, a
        ``stop()`` that lands mid-handshake could be followed by the ``--region us``
        retry launching a fresh cloudflared that nothing ever stops. It also cancels
        a pending auto-expiry timer, so a host-initiated close can never race a
        stale timer into firing (and calling ``on_expire``) after teardown.
        """
        self._stopped.set()
        if self._expire_timer is not None:
            self._expire_timer.cancel()
            self._expire_timer = None
        self._terminate()

    def _terminate(self) -> None:
        """Kill the current cloudflared process and reap its reader thread."""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as e:
            logger.debug("retro: error stopping tunnel: %s", e)
        finally:
            self._proc = None
        if self._reader:
            self._reader.join(timeout=2)
            self._reader = None
        logger.info("retro: tunnel stopped")
