"""Optional public tunnel for the Retro board — off-network joining, zero setup.

The LAN server (retro/server.py) only reaches teammates on the same network. To
let a remote teammate join, the host can start a **Cloudflare quick tunnel**,
which exposes ``http://localhost:<port>`` at a random ``https://…trycloudflare.com``
URL. Crucially this needs **no Cloudflare account, no token, no signup** — so the
app can own the whole flow: it downloads the ``cloudflared`` binary on first use
(cached under ``~/.scrum-agent/bin/``) and runs it. (ngrok, by contrast, forces a
per-user authtoken, so it can't be truly zero-setup.)

The tunnel forwards to our existing token-gated server, so ``/api/*`` stays
protected; the public URL simply carries the same ``?token=`` the LAN URL does,
now over HTTPS. This turns the retro into "anyone with the link can join" — fine
for a retrospective, but it is internet-reachable while the tunnel is up.

Everything here is best-effort and never raises into the TUI: a failed download
or tunnel start returns ``None`` / a status string, and the retro keeps working
on the LAN.

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
from pathlib import Path

logger = logging.getLogger(__name__)

# cloudflared prints the assigned URL to stderr inside a banner box; match it anywhere.
_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

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
    # Owner-only execute (drop group/other) — this is a cached, app-managed binary
    # in the user's home; no reason to expose it to other local accounts.
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


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
    logger.info("retro: cloudflared cached at %s", dest)
    return dest


def ensure_cloudflared() -> Path | None:
    """Return a path to a runnable cloudflared, downloading it on first use.

    Resolution order: ``CLOUDFLARED_PATH`` env → a ``cloudflared`` already on
    PATH → the app's cached copy → download. Returns ``None`` if it cannot be
    obtained (caller shows a status message and stays LAN-only).
    """
    override = os.getenv("CLOUDFLARED_PATH")
    if override and Path(override).exists():
        return Path(override)

    on_path = shutil.which("cloudflared")
    if on_path:
        return Path(on_path)

    cached = _cached_binary_path()
    if cached.exists():
        return cached

    try:
        return _download_cloudflared(cached)
    except Exception as e:
        logger.warning("retro: failed to obtain cloudflared: %s", e)
        return None


class CloudflareTunnel:
    """A Cloudflare quick tunnel forwarding a public HTTPS URL to a local port."""

    def __init__(self, port: int, *, binary: Path | None = None) -> None:
        self.port = port
        self._binary = binary
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._url = ""
        # Last stderr lines from cloudflared — surfaced on failure so the real reason
        # (QUIC blocked, trycloudflare 5xx, protocol deprecated, rate-limit) is visible.
        self._log_tail: deque[str] = deque(maxlen=15)

    @property
    def public_url(self) -> str:
        return self._url

    def start(self, *, timeout: float = 45.0) -> str | None:
        """Launch cloudflared and wait up to ``timeout`` s for the public URL.

        Returns the ``https://…trycloudflare.com`` URL, or ``None`` on failure
        (binary unavailable, process died, or no URL within the timeout).
        """
        binary = self._binary or ensure_cloudflared()
        if binary is None:
            return None
        self._binary = binary

        logger.info("retro: starting cloudflare tunnel for localhost:%d", self.port)
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - fixed, app-managed binary + args
                [str(binary), "tunnel", "--no-autoupdate", "--url", f"http://localhost:{self.port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            logger.warning("retro: could not launch cloudflared: %s", e)
            return None

        found = threading.Event()

        def _drain() -> None:
            # Keep reading stderr for the tunnel's whole life: capture the URL once,
            # then keep draining so cloudflared's pipe buffer never fills and blocks it.
            # Every line is logged (DEBUG) + kept in a small tail so a failure is
            # diagnosable — previously cloudflared's own output was discarded.
            assert self._proc is not None and self._proc.stderr is not None
            for line in self._proc.stderr:
                line = line.rstrip()
                if line:
                    self._log_tail.append(line)
                    logger.debug("cloudflared: %s", line)
                if not self._url:
                    m = _URL_RE.search(line)
                    if m:
                        self._url = m.group(0)
                        found.set()

        self._reader = threading.Thread(target=_drain, name="retro-tunnel", daemon=True)
        self._reader.start()

        # Wait for the URL, but bail early if the process exits first.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if found.wait(timeout=0.2):
                break
            if self._proc.poll() is not None:  # cloudflared exited before emitting a URL
                logger.warning("retro: cloudflared exited early (code %s)", self._proc.returncode)
                break

        if not self._url:
            # Surface cloudflared's own last words at warning level so the reason is
            # visible in retro.log without enabling DEBUG.
            if self._log_tail:
                logger.warning(
                    "retro: cloudflare tunnel failed to produce a URL — cloudflared said:\n%s",
                    "\n".join(self._log_tail),
                )
            else:
                logger.warning("retro: cloudflare tunnel failed to produce a URL (no cloudflared output)")
            self.stop()
            return None

        # cloudflared prints the URL several seconds BEFORE the quick-tunnel hostname's DNS
        # record actually goes live. Handing the URL out at that instant means a teammate
        # who opens it immediately hits NXDOMAIN — which their browser/OS then *negatively
        # caches*, so even retries keep failing for a while. Wait until the record is
        # globally resolvable before declaring the tunnel ready.
        host = self._url.split("://", 1)[-1].split("/", 1)[0]
        self._wait_dns_live(host, deadline=time.monotonic() + 30.0)
        logger.info("cloudflare quick tunnel ready (local_port=%d)", self.port)
        return self._url

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
        """Terminate the tunnel process and free its resources."""
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
