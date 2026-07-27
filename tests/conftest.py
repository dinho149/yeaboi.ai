"""Top-level test configuration.

Provides VCR.py (pytest-recording) settings for contract tests:
- Cassettes: stored per-module at <test_dir>/cassettes/<module_name>/
- Token scrubbing: strips Authorization headers, API keys, and PATs
  from recorded cassettes so they're safe to commit.

See README: "Testing — Contract Tests" for background on VCR.py replay.
"""

from __future__ import annotations

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
