"""Tests for scripts/_gh_transport.py — the one GitHub transport the scripts share.

`gh` when it is there, REST with a token when it is not. That second half is not
a convenience: the cloud routine session the fleet runs in has a token and no
CLI, and until this existed every script that touched GitHub from there failed —
`cd-deploy` loudly, because it runs under ``--strict``, and the rest silently.

Nothing here opens a socket. `urlopen` is the seam for the REST half and the
`subprocess` call is the seam for the CLI half, and an autouse fixture clears
both token variables so a developer who exports one does not turn a unit test
into a live call against their own repository.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# scripts/ is not a package, so the module is loaded straight from its file path.
_MODULE_PATH = ROOT / "scripts" / "_gh_transport.py"
_spec = importlib.util.spec_from_file_location("_gh_transport", _MODULE_PATH)
transport = importlib.util.module_from_spec(_spec)
sys.modules["_gh_transport"] = transport
_spec.loader.exec_module(transport)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """No ambient token, and no memo carried between tests."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    transport.reset_slug_cache()
    yield
    transport.reset_slug_cache()


class TestApiRequests:
    """``_api`` itself — the one function that touches a socket and the only
    place the token is handled.

    Everything else stubs it, so without this nothing covers the URL, the header
    set, or the promise in its docstring that it never raises and never logs the
    token. `urlopen` is the seam here; no socket is opened.
    """

    @pytest.fixture
    def urlopen(self, monkeypatch):
        """Record the Request objects; reply with a scripted body or raise."""
        sent: list = []
        outcome: dict = {"body": "{}", "raise": None}

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                body = outcome["body"]
                return body.encode() if isinstance(body, str) else body

        def fake(request, timeout=None):
            sent.append(request)
            if outcome["raise"] is not None:
                raise outcome["raise"]
            return _Response()

        monkeypatch.setattr(transport.urllib.request, "urlopen", fake)
        monkeypatch.setenv("GH_TOKEN", "ghp_notarealtoken")
        return type("U", (), {"sent": sent, "outcome": outcome})()

    def test_the_url_is_the_literal_host_plus_the_path(self, urlopen):
        transport.api("GET", "/repos/o/r/labels?per_page=100&page=1")
        assert urlopen.sent[0].full_url == "https://api.github.com/repos/o/r/labels?per_page=100&page=1"
        assert urlopen.sent[0].get_method() == "GET"

    def test_the_headers_authenticate_and_pin_the_api_version(self, urlopen):
        transport.api("GET", "/repos/o/r/labels")
        headers = {k.lower(): v for k, v in urlopen.sent[0].header_items()}
        assert headers["authorization"] == "Bearer ghp_notarealtoken"
        assert headers["accept"] == "application/vnd.github+json"
        assert headers["x-github-api-version"] == "2022-11-28"
        assert "user-agent" in headers

    def test_a_body_is_json_and_only_then_is_a_content_type_sent(self, urlopen):
        transport.api("GET", "/repos/o/r/labels")
        assert "Content-type" not in dict(urlopen.sent[0].header_items())
        transport.api("POST", "/repos/o/r/labels", {"name": "cowork", "color": "5319e7"})
        request = urlopen.sent[1]
        assert request.get_method() == "POST"
        assert json.loads(request.data.decode()) == {"name": "cowork", "color": "5319e7"}
        assert dict(request.header_items())["Content-type"] == "application/json"

    def test_no_token_never_reaches_the_socket(self, monkeypatch):
        opened = []
        monkeypatch.setattr(transport.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
        result = transport.api("GET", "/repos/o/r/labels")
        assert result.ok is False and "GH_TOKEN" in result.error
        assert opened == []

    def test_an_empty_body_is_success_with_no_data(self, urlopen):
        """A 204 is what `DELETE` and `PATCH` on a variable answer with."""
        urlopen.outcome["body"] = ""
        result = transport.api("DELETE", "/repos/o/r/labels/cowork")
        assert result.ok is True and result.data is None

    def test_an_http_error_carries_githubs_own_message(self, urlopen):
        urlopen.outcome["raise"] = urllib.error.HTTPError(
            "https://api.github.com/x",
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"message": "Resource not accessible by integration"}'),
        )
        result = transport.api("POST", "/repos/o/r/actions/variables", {"name": "X", "value": "y"})
        assert result.ok is False
        assert "403" in result.error and "not accessible by integration" in result.error

    def test_a_transport_error_is_a_result_not_an_exception(self, urlopen):
        urlopen.outcome["raise"] = urllib.error.URLError("name resolution failed")
        result = transport.api("GET", "/repos/o/r/labels")
        assert result.ok is False and "failed" in result.error

    def test_a_non_json_body_is_a_failure_not_a_traceback(self, urlopen):
        """What an egress proxy's HTML error page looks like — a plausible shape
        in exactly the cloud session this transport exists for."""
        urlopen.outcome["body"] = "<html>502 Bad Gateway</html>"
        result = transport.api("GET", "/repos/o/r/labels")
        assert result.ok is False and "not JSON" in result.error

    def test_the_token_is_in_no_error_it_returns(self, urlopen):
        urlopen.outcome["raise"] = urllib.error.URLError("connect to api.github.com failed")
        assert "ghp_notarealtoken" not in transport.api("GET", "/repos/o/r/labels").error
        urlopen.outcome["raise"] = urllib.error.HTTPError(
            "https://api.github.com/x", 401, "Unauthorized", {}, io.BytesIO(b"bad credentials")
        )
        assert "ghp_notarealtoken" not in transport.api("GET", "/repos/o/r/labels").error


class TestPagination:
    def test_a_second_page_is_fetched(self, monkeypatch):
        """`gh label list --limit 200` was one oversized page. REST caps at 100,
        and this repo already carries more cowork labels than fit in one."""
        pages = {1: [{"name": f"l{i}"} for i in range(100)], 2: [{"name": "last"}]}
        seen: list[int] = []

        def fake(method: str, path: str, body: dict | None = None):
            page = int(re.search(r"[?&]page=(\d+)", path).group(1))
            seen.append(page)
            return transport.ApiResult(True, pages.get(page, []))

        monkeypatch.setattr(transport, "api", fake)
        result = transport.api_paged("/repos/o/r/labels")
        assert seen == [1, 2]
        assert len(result.data) == 101


class TestGhCalls:
    """The CLI half. `gh` is preferred wherever it is installed, and a missing
    binary is a failure the caller can read rather than an exception."""

    def test_a_missing_binary_is_127_not_a_traceback(self, monkeypatch):
        """`resolve_slug()` answers from the git remote now, so code that used to
        stop for want of a slug runs on and can reach a `gh` call on a machine
        with no `gh`. That used to be a FileNotFoundError out of a script whose
        whole contract is to degrade with a remedy printed."""

        def boom(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", "gh")

        monkeypatch.setattr(transport.subprocess, "run", boom)
        result = transport.gh("pr", "list")
        assert result.returncode == 127
        assert "gh" in result.stderr

    def test_gh_ready_is_false_without_the_binary(self, monkeypatch):
        monkeypatch.setattr(transport.shutil, "which", lambda name: None)
        assert transport.gh_ready() is False

    def test_gh_ready_is_false_when_logged_out(self, monkeypatch):
        monkeypatch.setattr(transport.shutil, "which", lambda name: "/usr/bin/gh")
        monkeypatch.setattr(
            transport.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "not logged in")
        )
        assert transport.gh_ready() is False


class TestGraphql:
    """GraphQL is not optional: whether a review thread is resolved exists in the
    v4 schema and nowhere in v3, and `pr_feedback.py`'s whole gate rests on it.

    It is also the one call that routes itself, because `gh api graphql` takes a
    shape (`-f query=`, `-F var=`) no caller should have to know.
    """

    QUERY = "query($n: Int!) { x(n: $n) }"

    def test_the_cli_half_spells_the_gh_flags(self, monkeypatch):
        seen = {}

        def fake_gh(*args):
            seen["args"] = args
            return subprocess.CompletedProcess(args, 0, json.dumps({"data": {"x": 1}}), "")

        monkeypatch.setattr(transport, "gh_available", lambda: True)
        monkeypatch.setattr(transport, "gh", fake_gh)
        result = transport.graphql(self.QUERY, {"n": 3})
        assert result.ok and result.data == {"data": {"x": 1}}
        assert seen["args"][:2] == ("api", "graphql")
        assert f"query={self.QUERY}" in seen["args"]
        assert "n=3" in seen["args"]

    def test_the_rest_half_posts_query_and_variables(self, monkeypatch):
        sent = {}

        def fake_api(method, path, body=None):
            sent.update(method=method, path=path, body=body)
            return transport.ApiResult(True, {"data": {"x": 1}})

        monkeypatch.setattr(transport, "gh_available", lambda: False)
        monkeypatch.setattr(transport, "api", fake_api)
        assert transport.graphql(self.QUERY, {"n": 3}).ok
        assert sent["method"] == "POST" and sent["path"] == "/graphql"
        assert sent["body"] == {"query": self.QUERY, "variables": {"n": 3}}

    def test_an_error_inside_a_200_is_a_failure(self, monkeypatch):
        """A GraphQL error arrives with a 200. A caller checking only the status
        code would read a half-answer as a whole one."""
        monkeypatch.setattr(transport, "gh_available", lambda: False)
        monkeypatch.setattr(
            transport,
            "api",
            lambda *a, **k: transport.ApiResult(True, {"data": None, "errors": [{"message": "field missing"}]}),
        )
        result = transport.graphql(self.QUERY, {"n": 3})
        assert result.ok is False and "field missing" in result.error


class TestSlugResolution:
    """Three sources, cheapest first, and independent of transport — the
    transport selection itself needs the answer before it can choose."""

    def test_gh_answers_first_when_it_is_installed(self, monkeypatch):
        monkeypatch.setattr(transport.shutil, "which", lambda name: "/usr/bin/gh")
        monkeypatch.setattr(
            transport,
            "gh",
            lambda *a: subprocess.CompletedProcess(a, 0, json.dumps({"nameWithOwner": "o/from-gh"}), ""),
        )
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/from-env")
        assert transport.resolve_slug() == "o/from-gh"

    def test_the_env_answers_when_gh_cannot(self, monkeypatch):
        monkeypatch.setattr(transport.shutil, "which", lambda name: None)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/name")
        assert transport.resolve_slug() == "owner/name"

    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:owner/name.git",
            "https://github.com/owner/name.git",
            "https://github.com/owner/name",
            "ssh://git@github.com/owner/name.git",
        ],
    )
    def test_the_origin_remote_answers_last(self, monkeypatch, url):
        """What actually resolves it in a routine session: step 1 of
        `cron/cd-deploy.md` runs `git fetch origin main`, so a remote is there
        even though `gh` is not."""
        monkeypatch.setattr(transport.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            transport.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, url + "\n", "")
        )
        assert transport.resolve_slug() == "owner/name"

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com.example.net/owner/name.git",
            "https://notgithub.com/owner/name",
            "git@github.com.evil.test:owner/name.git",
            "https://example.net/github.com/owner/name",
        ],
    )
    def test_a_lookalike_host_is_not_github(self, monkeypatch, url):
        """`"github.com" in url` matched every one of these.

        The slug parsed out of a lookalike would then be sent to the real
        api.github.com with this machine's token attached — asking about, or
        writing to, somebody else's repository. CodeQL flagged the substring
        test on PR #235 and was right.
        """
        monkeypatch.setattr(transport.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            transport.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, url + "\n", "")
        )
        assert transport.resolve_slug() is None

    def test_a_non_github_remote_is_none(self, monkeypatch):
        monkeypatch.setattr(transport.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            transport.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "git@gitlab.com:o/n.git\n", ""),
        )
        assert transport.resolve_slug() is None

    def test_the_answer_is_memoised_including_a_miss(self, monkeypatch):
        """Caching a miss matters as much as caching a hit: `create_label` asks
        once per label, and without the memo every one re-runs the lookup."""
        calls = []
        monkeypatch.setattr(transport.shutil, "which", lambda name: None)

        def counted(*args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args[0], 1, "", "no remote")

        monkeypatch.setattr(transport.subprocess, "run", counted)
        assert transport.resolve_slug() is None
        assert transport.resolve_slug() is None
        assert len(calls) == 1
        transport.reset_slug_cache()
        assert transport.resolve_slug() is None
        assert len(calls) == 2


class TestSegment:
    def test_a_label_name_is_escaped(self):
        """Label names carry a `:` (`workstream:security`), so an unescaped
        segment is a 404 nobody expects."""
        assert transport.segment("workstream:security") == "workstream%3Asecurity"
        assert transport.segment("YEABOI_MODEL_HEAVY") == "YEABOI_MODEL_HEAVY"
