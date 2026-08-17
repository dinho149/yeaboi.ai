"""The Slack Web API client.

Two things carry real weight here and the rest is plumbing: **nothing raises**
(every caller is an unattended poll or a delivery channel that has promised not
to let one failure block the others), and **a retry only happens where a retry
can help** — a revoked token does not un-revoke in two seconds, and napping
through the poll's own cadence is worse than giving up and letting the next run
re-read the same window.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from yeaboi.tools import slack


class _Resp:
    def __init__(self, body: dict, status: int = 200, headers: dict | None = None):
        self._body = json.dumps(body).encode()
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(monkeypatch, *responses):
    """Serve the given responses (or exceptions) in order; record the requests."""
    seen: list = []
    queue = list(responses)

    def fake(req, timeout=None):
        seen.append(req)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(slack.urllib.request, "urlopen", fake)
    return seen


def _http_error(code: int, body: dict | None = None, headers: dict | None = None):
    import io

    return urllib.error.HTTPError(
        "https://slack.com/api/x", code, "err", headers or {}, io.BytesIO(json.dumps(body or {}).encode())
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(slack.time, "sleep", lambda _s: None)


class TestCall:
    def test_a_read_puts_its_params_in_the_query_string(self, monkeypatch):
        # Not cosmetic: this repo's VCR config matches on the path and NOT the
        # body, so two calls to one method are only distinguishable when the
        # cursor rides in the query.
        seen = _urlopen(monkeypatch, _Resp({"ok": True, "messages": []}))
        slack.call("conversations.history", {"channel": "C1", "cursor": "abc"}, token="xoxb-1")
        assert "channel=C1" in seen[0].full_url and "cursor=abc" in seen[0].full_url
        assert seen[0].data is None
        assert seen[0].headers["Authorization"] == "Bearer xoxb-1"

    def test_a_write_sends_json(self, monkeypatch):
        seen = _urlopen(monkeypatch, _Resp({"ok": True, "ts": "1.2"}))
        slack.call("chat.postMessage", {"channel": "C1", "text": "hi"}, token="xoxb-1", http_method="POST")
        assert json.loads(seen[0].data.decode()) == {"channel": "C1", "text": "hi"}

    def test_no_token_is_not_authed_and_never_calls_out(self, monkeypatch):
        seen = _urlopen(monkeypatch, _Resp({"ok": True}))
        monkeypatch.setattr(slack, "_token", lambda: "")
        resp = slack.call("auth.test")
        assert (resp.ok, resp.error) == (False, "not_authed")
        assert seen == []

    def test_slacks_own_error_envelope_becomes_a_response(self, monkeypatch):
        _urlopen(monkeypatch, _Resp({"ok": False, "error": "channel_not_found"}))
        resp = slack.call("conversations.history", {"channel": "C1"}, token="xoxb-1")
        assert (resp.ok, resp.error, resp.data) == (False, "channel_not_found", {})

    @pytest.mark.parametrize(
        "boom",
        [urllib.error.URLError("down"), OSError("no route"), TimeoutError("slow")],
    )
    def test_transport_failures_never_raise(self, monkeypatch, boom):
        _urlopen(monkeypatch, boom)
        resp = slack.call("auth.test", token="xoxb-1")
        assert resp.ok is False
        assert resp.error.startswith("transport_error")


class TestRetries:
    def test_a_429_is_retried_after_the_slack_supplied_delay(self, monkeypatch):
        seen = _urlopen(
            monkeypatch,
            _http_error(429, {"error": "ratelimited"}, {"Retry-After": "1"}),
            _Resp({"ok": True, "messages": []}),
        )
        resp = slack.call("conversations.history", {"channel": "C1"}, token="xoxb-1")
        assert resp.ok is True
        assert len(seen) == 2

    def test_a_revoked_token_is_never_retried(self, monkeypatch):
        # It will not fix itself in two seconds, and each retry is a round trip
        # a five-minute job pays for on every fire until someone notices.
        seen = _urlopen(monkeypatch, _Resp({"ok": False, "error": "token_revoked"}))
        resp = slack.call("auth.test", token="xoxb-1")
        assert slack.is_fatal_auth_error(resp)
        assert len(seen) == 1

    def test_an_ordinary_slack_error_is_never_retried(self, monkeypatch):
        seen = _urlopen(monkeypatch, _Resp({"ok": False, "error": "not_in_channel"}))
        slack.call("conversations.history", {"channel": "C1"}, token="xoxb-1")
        assert len(seen) == 1

    def test_retries_are_bounded(self, monkeypatch):
        seen = _urlopen(monkeypatch, _http_error(500, {"error": "internal_error"}))
        resp = slack.call("auth.test", token="xoxb-1")
        assert resp.ok is False
        assert len(seen) == slack._MAX_RETRIES + 1

    def test_the_budget_is_shared_across_a_whole_poll(self, monkeypatch):
        # Three methods each napping politely for their own 30s is a job that
        # outlives its own cadence and collides with the next fire.
        _urlopen(monkeypatch, _http_error(429, {"error": "ratelimited"}, {"Retry-After": "30"}))
        budget = slack.RetryBudget(total=30.0)
        slack.call("conversations.history", {"channel": "C1"}, token="xoxb-1", budget=budget)
        assert budget.remaining == 0
        assert budget.sleep(1) is False

    def test_a_spent_budget_stops_retrying_rather_than_sleeping(self, monkeypatch):
        seen = _urlopen(monkeypatch, _http_error(429, {"error": "ratelimited"}, {"Retry-After": "5"}))
        resp = slack.call("auth.test", token="xoxb-1", budget=slack.RetryBudget(total=0))
        assert (resp.ok, resp.error) == (False, "ratelimited")
        assert len(seen) == 1


class TestErrorMessage:
    def test_names_the_fix_not_the_symptom(self, monkeypatch):
        # `not_in_channel` is the most common real-world Slack failure, and a
        # generic message for it costs somebody an afternoon.
        msg = slack.error_message(slack.SlackResponse(ok=False, error="not_in_channel"))
        assert "/invite @yeaboi" in msg

    def test_every_known_code_carries_help(self):
        for code in slack._ERROR_HELP:
            assert "—" in slack.error_message(slack.SlackResponse(ok=False, error=code))

    def test_an_unknown_code_still_says_what_slack_said(self):
        assert "'weird_new_code'" in slack.error_message(slack.SlackResponse(ok=False, error="weird_new_code"))

    def test_a_successful_response_has_no_message(self):
        assert slack.error_message(slack.SlackResponse(ok=True)) == ""


class TestMethods:
    def test_post_message_threads_when_asked(self, monkeypatch):
        seen = _urlopen(monkeypatch, _Resp({"ok": True, "ts": "2.0"}))
        slack.post_message("C1", "hi", thread_ts="1.0", token="xoxb-1")
        assert json.loads(seen[0].data.decode())["thread_ts"] == "1.0"

    def test_post_message_omits_thread_ts_when_not(self, monkeypatch):
        seen = _urlopen(monkeypatch, _Resp({"ok": True, "ts": "2.0"}))
        slack.post_message("C1", "hi", token="xoxb-1")
        assert "thread_ts" not in json.loads(seen[0].data.decode())

    def test_add_reaction_uses_slacks_timestamp_key(self, monkeypatch):
        # reactions.add says `timestamp`, not `ts` — a silent no-op otherwise.
        seen = _urlopen(monkeypatch, _Resp({"ok": True}))
        slack.add_reaction("C1", "1.2", "white_check_mark", token="xoxb-1")
        assert json.loads(seen[0].data.decode())["timestamp"] == "1.2"


class TestNoToolsHere:
    def test_the_module_publishes_no_langchain_tool(self):
        # An LLM-callable Slack post would let prompt-injected text in a ticket
        # title reach a team channel — the exact hole this lane's posture
        # exists to close. Every other tools/ module publishes BaseTools; this
        # one must publish none, asserted rather than left to a docstring.
        from langchain_core.tools import BaseTool

        published = [name for name in dir(slack) if isinstance(getattr(slack, name), BaseTool)]
        assert published == []
