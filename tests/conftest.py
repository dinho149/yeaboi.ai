"""Top-level test configuration.

Provides VCR.py (pytest-recording) settings for contract tests:
- Cassettes: stored per-module at <test_dir>/cassettes/<module_name>/
- Token scrubbing: strips Authorization headers, API keys, and PATs
  from recorded cassettes so they're safe to commit.

See README: "Testing — Contract Tests" for background on VCR.py replay.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _sandbox_allows_test_dirs(tmp_path_factory):
    """Whitelist pytest temp dirs + repo fixtures in the filesystem sandbox.

    The app is sandboxed to ~/.yeaboi (fs_policy.py); tests exercise exports,
    imports, and repo reads against pytest tmp dirs and tests/fixtures/, which
    would otherwise all be denied. Session-scoped (a plain MonkeyPatch, since
    the monkeypatch fixture is function-scoped) so class/module-scoped fixtures
    are covered too. Every per-test tmp_path lives under the session basetemp.
    Tests that verify denial behaviour (test_fs_policy.py) override this via
    their own monkeypatch of YEABOI_ALLOWED_PATHS.
    """
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    basetemp = tmp_path_factory.getbasetemp()
    fixtures_dir = Path(__file__).parent / "fixtures"
    # CWD too: the (unmodifiable) REPL integration suite exports scrum-plan.*
    # into the repo root it runs from.
    mp.setenv("YEABOI_ALLOWED_PATHS", f"{basetemp},{fixtures_dir},{Path.cwd()}")
    yield
    mp.undo()


class RealBrowserBlocked(BaseException):
    """Raised when a test reaches a real ``webbrowser`` call.

    Deliberately a ``BaseException``, not an ``Exception``: all three production
    call sites wrap their ``webbrowser.open`` in ``except Exception`` and degrade
    to a "copy this URL" branch, so an ``Exception`` here would be swallowed and
    the guard would silently reroute the test instead of failing it.
    """


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    """No test may open a real browser tab.

    Three production paths call webbrowser (standup/gap_issues.py, feedback.py, and the
    TUI mode_select handler); a test that forgets to patch one hijacks the developer's
    browser on every `make test-fast`. Tests that legitimately exercise those paths patch
    webbrowser themselves — they share this MonkeyPatch instance, so their setattr lands
    after ours and wins.

    Patching the webbrowser module once covers every call site: each importer holds a
    reference to the same module object, including the function-local ``import webbrowser``
    in mode_select (resolved from ``sys.modules`` at call time).

    Function-scoped on purpose, unlike the session-scoped sandbox fixture above: a test
    overriding this guard is the intended path, and that only works when it shares the
    ``monkeypatch`` instance. Nothing reaches webbrowser from a higher-scoped fixture today.
    """

    def _blocked(url, *args, **kwargs):
        raise RealBrowserBlocked(f"test tried to open a real browser: {url}")

    # ``get`` too: ``webbrowser.get(...).open(url)`` would otherwise bypass the stubs.
    for name in ("open", "open_new", "open_new_tab", "get"):
        monkeypatch.setattr(webbrowser, name, _blocked)


# ---------------------------------------------------------------------------
# Sensitive headers / query params to scrub from recorded cassettes
# ---------------------------------------------------------------------------
_SCRUBBED_HEADERS = [
    "Authorization",
    "X-Api-Key",
    "Private-Token",
    "Cookie",
    "Set-Cookie",
]

_SCRUBBED_QUERY_PARAMS = [
    "api_key",
    "token",
    "access_token",
]


def _scrub_response(response: dict) -> dict:
    """Remove sensitive headers from recorded responses."""
    headers = response.get("headers", {})
    for header in _SCRUBBED_HEADERS:
        headers.pop(header, None)
        headers.pop(header.lower(), None)
    return response


def _scrub_request(request):
    """Remove sensitive headers and query params from recorded requests.

    VCR.py's Request.query is a read-only property derived from the URI,
    so we scrub sensitive query params by rewriting the URI directly.
    """
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    for header in _SCRUBBED_HEADERS:
        if header in request.headers:
            request.headers[header] = "SCRUBBED"
        if header.lower() in request.headers:
            request.headers[header.lower()] = "SCRUBBED"

    # Scrub sensitive query parameters by rewriting the URI
    parsed = urlparse(request.uri)
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        changed = False
        for key in _SCRUBBED_QUERY_PARAMS:
            if key in params:
                params[key] = ["SCRUBBED"]
                changed = True
        if changed:
            request.uri = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
    return request


# ---------------------------------------------------------------------------
# pytest-recording VCR configuration fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vcr_config():
    """VCR.py configuration applied to all @pytest.mark.vcr tests.

    pytest-recording picks up this fixture automatically. Key settings:
    - before_record_request / before_record_response: scrub tokens
    - decode_compressed_response: store readable JSON, not gzipped blobs
    - filter_headers: belt-and-suspenders scrubbing on record
    """
    return {
        "before_record_request": _scrub_request,
        "before_record_response": _scrub_response,
        "decode_compressed_response": True,
        "filter_headers": _SCRUBBED_HEADERS,
        # Match on endpoint identity, not exact query strings. PyJira adds
        # defaults like maxResults=50 that vary by version — we care about
        # the response shape, not the exact query params sent.
        "match_on": ["method", "scheme", "host", "port", "path"],
    }


class RealPackageInstallBlocked(BaseException):
    """Raised when a test reaches a real dictation-install or model-download spawn.

    A ``BaseException`` for the same reason as :class:`RealBrowserBlocked`:
    ``install_packages`` and ``download_model`` both wrap their spawn in
    ``except OSError`` and degrade to a "could not start" message, and the
    ``_run_install`` worker now catches ``Exception`` — so a plain exception here
    would be swallowed and the guard would quietly report a failed install
    instead of failing the test.
    """


@pytest.fixture(autouse=True)
def _no_real_package_install(monkeypatch):
    """No test may spawn a real package manager or model download.

    This is not hypothetical. ``TestDoubleTapInDescriptionLoop`` patched
    ``is_voice_available`` but not the strict ``probe_voice_backend``, so on a
    machine *without* the voice extra — which is every CI runner — the double-tap
    fell through to the install offer, its leftover ``"enter"`` keystroke accepted
    it, and the job ran a real ``uv pip install`` plus a 145 MB model fetch until
    the runner was killed. It passed locally, where the extra is installed and the
    offer never appears.

    Blocks ``voice_install._popen``, the module's single spawn seam, rather than
    ``subprocess.Popen`` — patching the latter would patch the shared module for
    every other test in the suite. Tests that legitimately drive the installer
    patch the same name and share this MonkeyPatch instance, so their setattr
    lands after ours and wins.
    """

    def _blocked(argv, *args, **kwargs):
        raise RealPackageInstallBlocked(f"test tried to spawn a real installer: {argv}")

    monkeypatch.setattr("yeaboi.voice_install._popen", _blocked)


@pytest.fixture(autouse=True)
def _no_ambient_sidecar(monkeypatch):
    """Unit and integration tests always exercise the pure-Python path.

    YEABOI_GO unset means *auto* since the yeaboi[core] wheel shipped: a dev
    with the wheel installed (or yeaboi-core on PATH) would otherwise have the
    whole agentwatch suite silently served by the Go sidecar — passing locally
    against Go and failing CI against Python, or vice versa. Tests that
    exercise the dispatch itself set their own YEABOI_GO / fake binary; they
    share this MonkeyPatch instance, so their setenv lands after ours and wins.
    The parity suite is unaffected — it constructs CoreClient directly from
    YEABOI_CORE_BIN and never consults the flag.
    """
    monkeypatch.setenv("YEABOI_GO", "0")
