"""Guided setup for the Cloudflare Access tier.

Runs the four cloudflared steps (sign in, create or pick a tunnel, route DNS)
and the PyJWT install against the pinned binary this app already manages.
Creating the Access application stays manual: automating it needs a zone-scoped
Cloudflare API token that can also create tunnels and DNS records, and yeaboi
holds no Cloudflare API token.

Headless — no Rich, no TUI imports. The wizard drives it from a worker thread,
``yeaboi --setup-access`` drives it with prints.

The child never touches the TTY, cancellation is an ``Event`` the runner polls,
and nothing here raises: every step returns an :class:`Outcome` whose ``code``
is for branching and whose ``message`` is a sentence naming the fix.

# See docs: "Guardrails" — human-in-the-loop
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Spawn seam, so tests block the real binary in one place — see
# ``retro.tunnel._popen`` and tests/conftest.py::_no_real_tunnel_spawn.
_popen = subprocess.Popen

_POLL_SECONDS = 0.25
_DEFAULT_TIMEOUT = 120.0
# Long, because it is bounded by a human finishing a browser login, not by us.
_LOGIN_TIMEOUT = 300.0
_TAIL_LINES = 40

# The express flow's tunnel: reused when it exists, created when nothing does.
DEFAULT_TUNNEL_NAME = "yeaboi"
# The express flow's hostname: boards.<the host's domain>.
BOARDS_SUBDOMAIN = "boards"
# Where a host with no Cloudflare-managed domain goes to add one.
ADD_SITE_URL = "https://dash.cloudflare.com/?to=/:account/add-site"
# The dashboard's "create a self-hosted Access application" form — the one
# manual step of the tier. The ?to= deep link resolves the account after login;
# the path is the current dashboard's (Applications moved under Access controls).
ACCESS_APP_ADD_URL = "https://dash.cloudflare.com/?to=/:account/one/access-controls/apps/self-hosted/add"

# Where ``cloudflared login`` writes its account credential, and where every
# other cloudflared subcommand looks for it. Left at the tool's own convention
# rather than relocated under ~/.yeaboi: a host who later runs the CLI directly
# should find their tunnel exactly where the Cloudflare docs say it is.
CERT_DIRS: tuple[Path, ...] = (
    Path.home() / ".cloudflared",
    Path.home() / ".cloudflare-warp",
    Path("/etc/cloudflared"),
    Path("/usr/local/etc/cloudflared"),
)


@dataclass(frozen=True)
class Outcome:
    """What a step did. ``code`` is for tests and branching; ``message`` is for a person."""

    ok: bool
    message: str = ""
    code: str = ""


@dataclass(frozen=True)
class TunnelInfo:
    """One named tunnel, as Cloudflare knows it."""

    id: str
    name: str
    credentials: str = ""


@dataclass(frozen=True)
class SetupState:
    """The doctor's answer: what is already done, and what is not.

    Read cheaply and without the network, so a wizard can open on it and the
    CLI can print it. The one thing it cannot answer offline is whether the
    Access application exists — only a live verification proves that, which is
    what :func:`yeaboi.sharing.identity.preflight` is for.
    """

    binary: str = ""
    logged_in: bool = False
    cert_path: str = ""
    jwt_installed: bool = False
    missing_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return bool(self.binary) and self.logged_in and self.jwt_installed and not self.missing_keys


# --------------------------------------------------------------------------
# Pure helpers — parsing and validation, tested without spawning anything.
# --------------------------------------------------------------------------

_UUID_RE = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", re.I)
_CRED_RE = re.compile(r"(/[^\s\"']+\.json)")

# A hostname we are about to hand to Cloudflare and then *assert on* in the
# Host-header rule. Anything that is not a plain dotted DNS name is refused
# here rather than discovered later as a tunnel that will not route.
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_EMAIL_RE = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")
# The sign-in URL cloudflared prints and waits on.
_AUTH_URL_RE = re.compile(r"https://\S*cloudflare\S*\.com/\S+")

_FAILURES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"Cannot determine default origin certificate|Error locating origin cert", re.I),
        "NOT_LOGGED_IN",
        "Not signed in to Cloudflare yet — run the sign-in step first.",
    ),
    (
        re.compile(r"failed to (?:dial|connect)|no such host|network is unreachable|i/o timeout", re.I),
        "NO_NETWORK",
        "Could not reach Cloudflare. Check the network and try again.",
    ),
    (
        # Anchored on the tunnel wording: a bare "already exists" also matches
        # the DNS-collision message below, and reporting a hostname clash as a
        # name clash sends the host to fix the wrong thing.
        re.compile(r"tunnel with name\b.*already exists", re.I),
        "NAME_TAKEN",
        "A tunnel with that name already exists in your account — run setup again and it will be reused.",
    ),
    (
        re.compile(r"record with that host already exists|An A, AAAA, or CNAME record", re.I),
        "DNS_EXISTS",
        "That hostname already points somewhere else. Choose another, or repoint it in the Cloudflare dashboard.",
    ),
    (
        re.compile(r"zone [^\n]*not found|(?:could not|couldn't|failed to|unable to) (?:find|lookup) [^\n]*zone", re.I),
        "NO_ZONE",
        "That hostname is not under a domain in your Cloudflare account. "
        "Add the domain at dash.cloudflare.com \u2192 Add a site, or use a hostname on one you have added.",
    ),
    (
        re.compile(r"not authorized|permission denied|forbidden|401|403", re.I),
        "NOT_AUTHORIZED",
        "Cloudflare refused the request — the signed-in account may not own that zone.",
    ),
)

# One raw line in, one short phrase out — or "" meaning "ignore, keep the last
# phrase". Same contract as voice_install.narrate.
_NARRATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Please open the following URL|browser", re.I), "waiting for the browser sign-in"),
    (re.compile(r"You have successfully logged in", re.I), "signed in"),
    (re.compile(r"Created tunnel", re.I), "tunnel created"),
    (re.compile(r"Added CNAME|route added|successfully", re.I), "DNS route added"),
    (re.compile(r"^\s*Downloading (\S+)", re.I), "downloading packages"),
    (re.compile(r"^\s*(?:Resolved|Prepared|Collecting)\b", re.I), "resolving packages"),
    (re.compile(r"Successfully installed|Installed \d+ package", re.I), "installed"),
)


def narrate(line: str) -> str:
    """One installer/CLI line → one short phrase, or "" to keep the previous one."""
    for pattern, phrase in _NARRATIONS:
        if pattern.search(line):
            return phrase
    return ""


def classify_failure(returncode: int, output: str) -> tuple[str, str]:
    """``(code, message)`` for a failed command. Pure over its inputs."""
    for pattern, code, message in _FAILURES:
        if pattern.search(output):
            return code, message
    if returncode == 0:
        return "", ""
    return "UNKNOWN", "cloudflared reported an error — see the log for its output."


def valid_hostname(value: str) -> bool:
    """A public hostname we can both route and assert on.

    Refuses anything the loopback test would match: ``retro.localhost`` parses
    as a dotted name, but a board published on it would have verification
    switched off for every request.
    """
    from yeaboi.sharing.access import _is_loopback

    host = value.strip().lower()
    if not _HOSTNAME_RE.match(host):
        return False
    return not _is_loopback(host)


def valid_emails(value: str) -> bool:
    """Every comma-separated entry must be an address. Empty is allowed — it means
    "no remote visitor gets host powers", which is a legitimate, safe choice."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return all(_EMAIL_RE.match(p) for p in parts)


_TEAM_RE = re.compile(r"https://([a-z0-9-]+)\.cloudflareaccess\.com", re.I)
_AUD_RE = re.compile(r"^[0-9a-f]{64}$")


def _location_of(url: str, timeout: float) -> str:
    """One redirect hop: the Location header a bare GET gets, or ""."""
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    opener.addheaders = [("User-Agent", "yeaboi-access-setup")]
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.headers.get("Location") or ""
    except urllib.error.HTTPError as e:
        return (e.headers.get("Location") or "") if e.headers is not None else ""
    except Exception:  # noqa: BLE001 - no network, bad TLS, DNS not live: all mean "could not discover"
        return ""


def discover_app(hostname: str, *, timeout: float = 8.0) -> tuple[str, str]:
    """``(team, aud)`` read from the hostname's own sign-in redirect; either may be "".

    An anonymous request to an Access-protected hostname is answered at
    Cloudflare's edge with a redirect to
    ``https://<team>.cloudflareaccess.com/cdn-cgi/access/login/…?kid=<aud>`` —
    the team name and the application's audience tag, from the one place that
    cannot be wrong, with no credential involved. The aud is offered to the
    caller as a *default to confirm*, never saved unseen. Both come back empty
    when the hostname does not redirect there (the application does not exist
    yet, DNS is not live, or there is no network); the caller falls back to
    asking.
    """
    if not valid_hostname(hostname):
        return "", ""
    url = f"https://{hostname}/"
    for _ in range(3):
        location = _location_of(url, timeout)
        if not location:
            return "", ""
        match = _TEAM_RE.search(location)
        if match:
            from urllib.parse import parse_qs, urlparse

            kid = (parse_qs(urlparse(location).query).get("kid") or [""])[0].strip().lower()
            return match.group(1).lower(), kid if _AUD_RE.match(kid) else ""
        url = location if location.startswith("http") else f"https://{hostname}{location}"
    return "", ""


def boards_hostname(value: str) -> str:
    """The express default hostname: ``boards.<domain>``.

    Accepts a bare domain or an already-prefixed hostname, so a saved value or
    a re-typed one is never doubled up. The full hostname stays editable in
    Settings afterwards — this only decides the default nobody has to type.
    """
    v = value.strip().lower().strip(".")
    return v if v.startswith(f"{BOARDS_SUBDOMAIN}.") else f"{BOARDS_SUBDOMAIN}.{v}"


def parse_tunnel_list(text: str) -> tuple[TunnelInfo, ...]:
    """Parse ``cloudflared tunnel list --output json``.

    Tolerant on purpose: cloudflared writes its own structured log lines to the
    same stream, so the JSON array is located rather than assumed to be the
    whole payload.
    """
    start = text.find("[")
    if start < 0:
        return ()
    try:
        rows = json.loads(text[start : text.rindex("]") + 1])
    except ValueError:
        return ()
    found: list[TunnelInfo] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("id", "")).strip()
        name = str(row.get("name", "")).strip()
        # A live tunnel's deleted_at is the JSON zero date ("0001-01-01T…"), not
        # null — a truthy string, so a bare falsiness check drops every tunnel.
        deleted = str(row.get("deleted_at") or "").strip()
        if tid and name and (not deleted or deleted.startswith("0001-01-01")):
            found.append(TunnelInfo(id=tid, name=name))
    return tuple(found)


def resolve_tunnel(tunnels: tuple[TunnelInfo, ...]) -> TunnelInfo | None:
    """The tunnel the express flow uses without asking.

    The only tunnel, or the one named ``DEFAULT_TUNNEL_NAME``. ``None`` means
    genuinely ambiguous — several tunnels and none of them ours — and is the
    one case where the caller still shows a picker.
    """
    if len(tunnels) == 1:
        return tunnels[0]
    for tunnel in tunnels:
        if tunnel.name == DEFAULT_TUNNEL_NAME:
            return tunnel
    return None


def parse_created_tunnel(text: str, name: str) -> TunnelInfo | None:
    """Recover the new tunnel's id and credentials path from ``tunnel create``.

    Tries the JSON shape first and falls back to the human text, because the two
    have diverged across cloudflared releases and this is the one place where
    losing the answer means an orphaned tunnel in someone's Cloudflare account
    that yeaboi cannot see it made.
    """
    start = text.find("{")
    if start >= 0:
        try:
            blob = json.loads(text[start : text.rindex("}") + 1])
            tid = str(blob.get("id", "")).strip()
            if tid:
                return TunnelInfo(
                    id=tid,
                    name=str(blob.get("name", name)).strip() or name,
                    credentials=str(blob.get("credentials_file", "")).strip(),
                )
        except (ValueError, KeyError):
            pass
    uuid = _UUID_RE.search(text)
    if not uuid:
        return None
    cred = _CRED_RE.search(text)
    return TunnelInfo(id=uuid.group(1), name=name, credentials=cred.group(1) if cred else "")


def find_cert() -> str:
    """The origin certificate ``cloudflared login`` writes, if it exists."""
    for directory in CERT_DIRS:
        candidate = directory / "cert.pem"
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def default_credentials(tunnel_id: str) -> str:
    """Where ``cloudflared tunnel create`` puts a tunnel's credentials by default.

    Used when reusing an *existing* tunnel, where nothing tells us the path:
    create reports it, list does not. Returned even when the file is absent so
    the caller can offer it as an editable default rather than a blank —
    ``preflight`` refuses a path that is not a file, so a wrong guess surfaces
    as a named setup error rather than as a board that fails at publish time.
    """
    return str(Path.home() / ".cloudflared" / f"{tunnel_id}.json")


def jwt_installed() -> bool:
    try:
        import jwt  # noqa: F401, PLC0415 - presence probe for the optional `access` extra

        return True
    except ImportError:
        return False


def missing_config_keys() -> tuple[str, ...]:
    """Which required Access keys are still unset, in the order the wizard asks."""
    from yeaboi.config import access_aud, access_credentials_file, access_hostname, access_team, access_tunnel_id

    checks = (
        ("CLOUDFLARE_TUNNEL_ID", access_tunnel_id()),
        ("CLOUDFLARE_TUNNEL_CREDENTIALS", access_credentials_file()),
        ("CLOUDFLARE_ACCESS_HOSTNAME", access_hostname()),
        ("CLOUDFLARE_ACCESS_TEAM", access_team()),
        ("CLOUDFLARE_ACCESS_AUD", access_aud()),
    )
    return tuple(key for key, value in checks if not value)


def read_state() -> SetupState:
    """What is already set up, and what is not.

    **Not cheap and not frame-safe**: ``ensure_cloudflared`` may download ~38 MB
    on first use, and re-reads and hashes the cached binary on every call. Call
    it from a worker, never from a render loop.
    """
    from yeaboi.retro.tunnel import ensure_cloudflared

    binary = ensure_cloudflared()
    cert = find_cert()
    return SetupState(
        binary=str(binary or ""),
        logged_in=bool(cert),
        cert_path=cert,
        jwt_installed=jwt_installed(),
        missing_keys=missing_config_keys(),
    )


# --------------------------------------------------------------------------
# The runner — one place that spawns, so one place gets the discipline right.
# --------------------------------------------------------------------------


def _run(
    argv: list[str],
    *,
    on_line: Callable[[str], None] | None = None,
    on_raw: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Spawn, drain, and return ``(returncode, output)``. Never raises.

    Returns ``(-1, ...)`` for "could not start", ``(-2, ...)`` for cancelled and
    ``(-3, ...)`` for timed out, so callers can tell a refusal from a failure
    without parsing text.
    """
    from yeaboi.retro.tunnel import _child_env

    logger.info("access setup: running %s", " ".join(argv[1:]) or argv[0])
    child_env = env if env is not None else _child_env()
    try:
        proc = _popen(  # noqa: S603 - fixed, app-managed binary + validated args
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=child_env,
            start_new_session=True,
        )
    except (OSError, ValueError) as e:
        logger.warning("access setup: could not start %s: %s", argv[0], e)
        return -1, str(e)

    tail: deque[str] = deque(maxlen=_TAIL_LINES)

    def _pump() -> None:
        stream = proc.stdout
        if stream is None:
            return
        for raw in stream:
            line = raw.rstrip()
            tail.append(line)
            logger.debug("access setup: %s", line)
            if on_raw is not None:
                on_raw(line)
            if on_line is not None:
                phrase = narrate(line)
                if phrase:
                    on_line(phrase)

    reader = threading.Thread(target=_pump, name="access-setup-read", daemon=True)
    reader.start()

    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            _terminate(proc)
            return -2, "\n".join(tail)
        if time.monotonic() > deadline:
            _terminate(proc)
            return -3, "\n".join(tail)
        time.sleep(_POLL_SECONDS)
    reader.join(timeout=5)
    return int(proc.returncode or 0), "\n".join(tail)


def _terminate(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001 - teardown must not raise over a TUI
        try:
            proc.kill()
        except Exception:  # noqa: BLE001, S110
            pass


def _outcome(rc: int, output: str, success: str) -> Outcome:
    if rc == 0:
        return Outcome(True, success)
    if rc == -1:
        return Outcome(False, "Could not run cloudflared.", "NO_BINARY")
    if rc == -2:
        return Outcome(False, "Cancelled.", "CANCELLED")
    if rc == -3:
        return Outcome(False, "cloudflared did not finish in time.", "TIMEOUT")
    code, message = classify_failure(rc, output)
    logger.warning("access setup: cloudflared failed (rc=%s, code=%s): %s", rc, code, output[-2000:])
    return Outcome(False, message, code)


# --------------------------------------------------------------------------
# The five steps.
# --------------------------------------------------------------------------


def login(
    *,
    on_line: Callable[[str], None] | None = None,
    on_url: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    open_browser: bool = True,
) -> Outcome:
    """``cloudflared tunnel login`` — the browser round-trip that mints ``cert.pem``.

    This is the one step that writes an *account-level* credential to disk. It is
    the same command the host would otherwise type themselves, against the same
    binary, and yeaboi never reads the file — but a setup flow that quietly
    obtains account authority would be doing something the host did not clearly
    agree to, so the caller is expected to say what this does before calling it.

    Already signed in? Returns success without spawning anything: re-running
    login would send someone to a browser for a file they already have.
    """
    existing = find_cert()
    if existing:
        logger.info("access setup: already signed in (%s)", existing)
        return Outcome(True, "Already signed in to Cloudflare.", "ALREADY")

    state = read_state()
    if not state.binary:
        return Outcome(False, "Could not obtain cloudflared.", "NO_BINARY")

    # cloudflared prints an authorisation URL and then blocks until the browser
    # round-trip finishes. Opening it saves a copy-paste out of a stream nobody
    # is watching — but it is a convenience only: the URL is reported through
    # ``on_line`` either way, so a headless or locked-down host is never stuck
    # waiting on a browser that will not open.
    opened: list[str] = []

    def _watch(line: str) -> None:
        match = _AUTH_URL_RE.search(line)
        if match is None or opened:
            return
        url = match.group(0)
        opened.append(url)
        # Its own callback, not on_line: the next narrated line would overwrite
        # the phrase, and this is the one thing the user has to be able to read.
        if on_url is not None:
            on_url(url)
        if open_browser:
            try:
                import webbrowser  # noqa: PLC0415 - matches the repo's other three URL call sites

                webbrowser.open(url)
            except Exception as e:  # noqa: BLE001 - a browser that will not open must not fail the step
                logger.warning("access setup: could not open the sign-in URL: %s", e)

    rc, output = _run(
        [state.binary, "tunnel", "login"],
        on_line=on_line,
        on_raw=_watch,
        cancel=cancel,
        timeout=_LOGIN_TIMEOUT,
    )
    if rc == 0 and not find_cert():
        # cloudflared exits 0 on some cancelled flows; the file is the truth.
        return Outcome(
            False,
            "Sign-in did not finish. If the authorize page showed no domains, your account "
            "has none yet — add one at dash.cloudflare.com \u2192 Add a site, then retry.",
            "NO_CERT",
        )
    return _outcome(rc, output, "Signed in to Cloudflare.")


def list_tunnels(*, cancel: threading.Event | None = None) -> tuple[tuple[TunnelInfo, ...], Outcome]:
    """Existing named tunnels on the signed-in account."""
    state = read_state()
    if not state.binary:
        return (), Outcome(False, "Could not obtain cloudflared.", "NO_BINARY")
    rc, output = _run([state.binary, "tunnel", "list", "--output", "json"], cancel=cancel)
    if rc != 0:
        return (), _outcome(rc, output, "")
    return parse_tunnel_list(output), Outcome(True, "")


def create_tunnel(
    name: str,
    *,
    on_line: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[TunnelInfo | None, Outcome]:
    """``cloudflared tunnel create`` — returns the tunnel *and* where its credentials landed.

    The credentials path matters as much as the id: it is what
    ``CLOUDFLARE_TUNNEL_CREDENTIALS`` must point at, and a created tunnel whose
    path we failed to capture is an orphan in the host's Cloudflare account.
    """
    clean = name.strip()
    if not clean or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$", clean):
        return None, Outcome(False, "Pick a name of letters, digits, dashes or underscores.", "BAD_NAME")

    state = read_state()
    if not state.binary:
        return None, Outcome(False, "Could not obtain cloudflared.", "NO_BINARY")
    if not state.logged_in:
        return None, Outcome(False, "Not signed in to Cloudflare yet.", "NOT_LOGGED_IN")

    rc, output = _run(
        [state.binary, "tunnel", "create", "--output", "json", clean],
        on_line=on_line,
        cancel=cancel,
    )
    if rc != 0:
        return None, _outcome(rc, output, "")
    info = parse_created_tunnel(output, clean)
    if info is None:
        return None, Outcome(
            False,
            "cloudflared created the tunnel but did not report its id — check 'cloudflared tunnel list'.",
            "NO_ID",
        )
    return info, Outcome(True, f"Created tunnel {info.name}.")


def route_dns(
    tunnel: str,
    hostname: str,
    *,
    on_line: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> Outcome:
    """``cloudflared tunnel route dns`` — point the hostname at the tunnel.

    Never passes ``--overwrite-dns``: this runs against the host's real zone, and
    silently repointing a record that already serves something else is not a
    setup step, it is an outage. A collision is reported so the host can decide.
    """
    host = hostname.strip().lower()
    if not valid_hostname(host):
        return Outcome(False, "That is not a hostname — use something like retro.example.com.", "BAD_HOSTNAME")
    state = read_state()
    if not state.binary:
        return Outcome(False, "Could not obtain cloudflared.", "NO_BINARY")
    if not state.logged_in:
        return Outcome(False, "Not signed in to Cloudflare yet.", "NOT_LOGGED_IN")

    rc, output = _run(
        [state.binary, "tunnel", "route", "dns", tunnel, host],
        on_line=on_line,
        cancel=cancel,
    )
    return _outcome(rc, output, f"{host} now points at the tunnel.")


def install_jwt(
    *,
    on_line: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> Outcome:
    """Install ``PyJWT[crypto]`` — the local half of the tier.

    Reuses :mod:`yeaboi.voice_install`'s method detection rather than inventing a
    second installer, because the rules it encodes are the expensive part: never
    run ``uv sync`` or ``uv tool install --force`` (they rebuild the very venv
    this process is executing out of), and never install the ``yeaboi[access]``
    extra, which would reinstall yeaboi over itself. We install the *package*.
    """
    if jwt_installed():
        return Outcome(True, "PyJWT is already installed.", "ALREADY")

    from yeaboi import voice_install

    # ignore_verdict: the stored verdict is dictation's, keyed on a past
    # faster-whisper failure that says nothing about PyJWT.
    # gate_platform: likewise — that gate is about ctranslate2 wheels.
    plan = voice_install.install_plan(
        ignore_verdict=True,
        packages=("PyJWT[crypto]",),
        extra="access",
        gate_platform=False,
    )
    if plan.blocked:
        return Outcome(False, plan.blocked, "BLOCKED")

    # Not _run(): that uses the *tunnel's* env allowlist, which drops
    # PIP_INDEX_URL / UV_INDEX_URL (corporate mirror) and SSL_CERT_FILE /
    # REQUESTS_CA_BUNDLE (TLS-intercepting proxy), and loses the progress-bar
    # suppression a package manager needs under a Rich Live. voice_install
    # already solved all of that.
    rc, output = _run(
        list(plan.argv),
        on_line=on_line,
        cancel=cancel,
        timeout=600.0,
        env=voice_install._child_env(),
    )
    if rc != 0:
        return _outcome(rc, output, "")
    voice_install.refresh_imports()
    if not jwt_installed():
        return Outcome(False, "Installed, but this Python cannot see PyJWT yet — restart yeaboi.", "RESTART")
    return Outcome(True, "Installed PyJWT.")


def save(**values: str) -> None:
    """Persist config as each fact is learned, not at the end of the wizard.

    Deliberately different from the standup wizard's commit-at-the-end idiom:
    these steps have side effects on a real Cloudflare account. Creating a
    tunnel and then losing its id to an Esc leaves an orphan the wizard cannot
    see it made, so a fact is written the moment it exists.
    """
    from yeaboi.config import apply_config_value

    for key, value in values.items():
        if value:
            apply_config_value(key, value)
            logger.info("access setup: set %s", key)


def verify(*, assume_mode: bool = False) -> Outcome:
    """The final step: does the tier actually come up?

    Delegates to :func:`yeaboi.sharing.identity.preflight`, so the wizard's
    "done" and the board's "will this publish" are the same answer rather than
    two checks that can disagree. ``assume_mode`` checks everything but the
    switch, which the setup flow only writes once this passes.
    """
    from yeaboi.sharing.identity import preflight

    gate, problem = preflight("retro", assume_mode=assume_mode)
    if gate is None:
        return Outcome(False, problem or "Cloudflare Access is not configured.", "NOT_READY")
    return Outcome(True, "Cloudflare Access is configured and reachable.")
