"""The /api/boards, /api/poker, /api/export, /api/shares and /api/anonymize routes.

Socketless, over ``AppServer.handle()``. The board and share *lifecycles* are
tested in test_app_supervisor.py; here the subject is the wire — what a snapshot
carries, which requests are refused, and the NDJSON line order.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.sharing.link import SecureLink
from yeaboi.sharing.resolve import Resolved

TOKEN = "test-token"


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body))


def drain(response) -> list[dict]:
    assert response.code == 200, response.body
    assert response.content_type == "application/x-ndjson"
    return [json.loads(line) for line in b"".join(response.stream).decode().splitlines()]


class FakeServer:
    def __init__(self) -> None:
        self.port = 5173
        self.url = "http://127.0.0.1:5173/?token=secret&admin=alsosecret"
        self.share_url = "https://x.example/"
        self.display_code = "DUCK-42"
        self.stopped = False

    def set_public_url(self, url) -> None:
        pass

    def set_access_gate(self, gate) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


class FakeBoard:
    def cards_by_grid(self):
        return {"went_well": []}

    def carried_snapshot(self):
        return []

    def state_snapshot(self):
        return {"tickets": []}


def _session(app, kind="retro", board_id="b1"):
    from yeaboi.app.supervisor import BoardSession

    server = FakeServer()
    session = BoardSession(
        board_id=board_id,
        kind=kind,
        title="Apollo",
        session_id="s1",
        board=FakeBoard(),
        server=server,
        link=SecureLink(server, surface=kind),
        started_at="2026-08-23T10:00:00+00:00",
    )
    app.boards._boards[board_id] = session
    return session


class TestBoardReads:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/boards", authed=False).code == 401

    def test_empty_with_nothing_open(self, app):
        assert json.loads(request(app, "GET", "/api/boards").body)["boards"] == []

    def test_snapshot_carries_the_host_controls(self, app):
        _session(app)
        body = json.loads(request(app, "GET", "/api/boards/b1").body)
        assert body["kind"] == "retro"
        assert body["display_code"] == "DUCK-42"
        assert body["link"]["state"] == "idle"
        assert set(body["state"]) == {"grids", "carried"}

    def test_a_poker_snapshot_carries_the_table(self, app):
        _session(app, kind="poker", board_id="p1")
        body = json.loads(request(app, "GET", "/api/boards/p1").body)
        assert body["state"] == {"tickets": []}

    def test_no_snapshot_carries_the_admin_token(self, app):
        # The host link is served by its own route, so that listing boards or
        # drawing one never hands the admin secret to the caller.
        _session(app)
        for path in ("/api/boards", "/api/boards/b1"):
            assert "admin=" not in request(app, "GET", path).body.decode()

    def test_the_host_link_has_its_own_route(self, app):
        _session(app)
        body = json.loads(request(app, "GET", "/api/boards/b1/host").body)
        assert "admin=" in body["host_url"]

    def test_an_unknown_boards_host_link_is_404(self, app):
        assert request(app, "GET", "/api/boards/nope/host").code == 404

    def test_unknown_board_is_404(self, app):
        assert request(app, "GET", "/api/boards/nope").code == 404

    def test_invite_is_empty_until_the_tunnel_lands(self, app):
        session = _session(app)
        session.server.share_url = ""
        body = json.loads(request(app, "GET", "/api/boards/b1/invite").body)
        # A code with no address sends the host into a chat window with nothing
        # to click, so the invite stays empty rather than half-formed.
        assert body["invite"] == ""
        assert body["display_code"] == "DUCK-42"

    def test_invite_carries_the_code_once_it_is_up(self, app):
        _session(app)
        invite = json.loads(request(app, "GET", "/api/boards/b1/invite").body)["invite"]
        assert invite.startswith("https://x.example/")
        assert "DUCK-42" in invite
        # Never the host link: that one carries the admin secret.
        assert "admin=" not in invite


class TestBoardActions:
    def test_retry_link_returns_the_new_state(self, app, monkeypatch):
        monkeypatch.setenv("YEABOI_NO_TUNNEL", "1")
        _session(app)
        body = json.loads(request(app, "POST", "/api/boards/b1/link", {}).body)
        assert body["link"]["state"] == "off"

    def test_action_items_are_a_retro_control(self, app):
        _session(app, kind="poker", board_id="p1")
        response = request(app, "POST", "/api/boards/p1/actions", {})
        assert response.code == 400

    def test_action_items_return_the_message_and_the_state(self, app, monkeypatch):
        _session(app)
        import yeaboi.retro.engine as engine

        monkeypatch.setattr(engine, "generate_action_items", lambda _b: "Add some cards first")
        body = json.loads(request(app, "POST", "/api/boards/b1/actions", {}).body)
        assert body["message"] == "Add some cards first"
        assert "grids" in body["state"]

    def test_close_flushes_and_forgets(self, app, monkeypatch, tmp_path):
        session = _session(app)
        monkeypatch.setattr(type(session), "_flush", lambda _self, _db: 7)
        body = json.loads(request(app, "POST", "/api/boards/b1/close", {}).body)
        assert body == {"closed": True, "board_id": "b1", "run_id": 7}
        assert session.server.stopped
        assert request(app, "GET", "/api/boards/b1").code == 404

    def test_closing_twice_is_a_404_not_a_double_flush(self, app, monkeypatch):
        session = _session(app)
        monkeypatch.setattr(type(session), "_flush", lambda _self, _db: 7)
        request(app, "POST", "/api/boards/b1/close", {})
        assert request(app, "POST", "/api/boards/b1/close", {}).code == 404


class TestStartBoards:
    def test_retro_without_a_session_is_a_409_with_the_remedy(self, app, monkeypatch):
        import yeaboi.app.supervisor as supervisor

        def boom(_self):
            raise ValueError("no project session yet")

        monkeypatch.setattr(supervisor.BoardSupervisor, "start_retro", boom)
        response = request(app, "POST", "/api/boards/retro", {})
        assert response.code == 409
        assert "Planning" in json.loads(response.body)["error"]

    def test_poker_refuses_an_empty_table(self, app):
        response = request(app, "POST", "/api/boards/poker", {"source": "demo", "tickets": []})
        assert response.code == 400
        assert "tickets" in json.loads(response.body)["error"]


class TestPokerSetup:
    def test_options_carry_the_steps_and_the_sources(self, app):
        body = json.loads(request(app, "GET", "/api/poker/options").body)
        assert body["steps"] == ["source", "scope", "sprint", "types"]
        assert body["sources"][-1]["key"] == "demo"
        assert [o["key"] for o in body["scopes"]] == ["sprint", "backlog"]

    def test_sprints_need_a_source(self, app):
        assert request(app, "GET", "/api/poker/sprints").code == 400

    def test_sprints_carry_the_cursor(self, app, monkeypatch):
        import yeaboi.poker.tickets as tickets

        monkeypatch.setattr(tickets, "list_sprints", lambda _s: [{"name": "S1"}, {"name": "S2", "state": "active"}])
        body = json.loads(request(app, "GET", "/api/poker/sprints?source=jira").body)
        assert body["default_index"] == 1
        assert [o["label"] for o in body["options"]] == ["S1", "S2"]

    def test_types_need_a_source(self, app):
        assert request(app, "GET", "/api/poker/types").code == 400

    def test_types_are_prechecked_per_source(self, app):
        body = json.loads(request(app, "GET", "/api/poker/types?source=azdevops").body)
        assert {t["key"]: t["checked"] for t in body["types"]} == {"story": True, "bug": True, "task": False}

    def test_tickets_need_a_source(self, app):
        assert request(app, "POST", "/api/poker/tickets", {}).code == 400

    def test_tickets_name_the_scope_and_stay_quiet_on_success(self, app, monkeypatch):
        import yeaboi.poker.tickets as tickets

        monkeypatch.setattr(tickets, "fetch_tickets", lambda *_a, **_k: [{"key": "Y-1"}])
        body = json.loads(request(app, "POST", "/api/poker/tickets", {"source": "jira", "scope": "backlog"}).body)
        assert body["scope_label"] == "Backlog"
        assert body["message"] == ""

    def test_an_empty_fetch_explains_itself(self, app, monkeypatch):
        import yeaboi.poker.tickets as tickets

        monkeypatch.setattr(tickets, "fetch_tickets", lambda *_a, **_k: [])
        body = json.loads(request(app, "POST", "/api/poker/tickets", {"source": "jira", "scope": "backlog"}).body)
        assert "Backlog" in body["message"]


# ---------------------------------------------------------------------------
# Export / share / anonymize
# ---------------------------------------------------------------------------


def _resolved(kind="standup", **kw):
    base = {
        "kind": kind,
        "artifact": object(),
        "title": "Daily Standup — 2026-07-10",
        "project_name": "Apollo",
        "run_id": 4,
        "session_id": "s1",
    }
    return Resolved(**{**base, **kw})


@pytest.fixture
def resolved(monkeypatch):
    import yeaboi.sharing.resolve as resolver

    target = _resolved()
    monkeypatch.setattr(resolver, "load", lambda *_a, **_k: target)
    monkeypatch.setattr(resolver, "markdown", lambda _r: "# Daily Standup\n")
    return target


class TestExport:
    def test_destinations_are_offered_per_mode(self, app):
        body = json.loads(request(app, "GET", "/api/export/destinations?mode=retro").body)
        keys = [d["key"] for d in body["destinations"]]
        assert keys[:2] == ["files", "copy"]
        assert body["mode"] == "retro"

    def test_extras_ride_along(self, app):
        body = json.loads(request(app, "GET", "/api/export/destinations?mode=poker&extras=jira").body)
        assert "jira" in [d["key"] for d in body["destinations"]]

    def test_unknown_destination_is_refused(self, app, resolved):
        response = request(app, "POST", "/api/export", {"destination": "carrier-pigeon", "kind": "standup"})
        assert response.code == 400

    def test_copy_returns_the_markdown_and_does_nothing(self, app, resolved):
        body = json.loads(request(app, "POST", "/api/export", {"destination": "copy", "kind": "standup"}).body)
        assert body["markdown"] == "# Daily Standup\n"
        assert body["title"] == "Daily Standup — 2026-07-10"

    def test_files_report_where_they_landed(self, app, resolved, monkeypatch, tmp_path):
        import yeaboi.sharing.resolve as resolver

        monkeypatch.setattr(
            resolver, "export_files", lambda _r: {"markdown": tmp_path / "a.md", "html": tmp_path / "a.html"}
        )
        body = json.loads(request(app, "POST", "/api/export", {"destination": "files", "kind": "standup"}).body)
        assert body["ok"] and str(tmp_path) in body["message"]
        assert set(body["paths"]) == {"markdown", "html"}

    def test_a_blocked_destination_is_a_409_carrying_the_hint(self, app, resolved, monkeypatch):
        monkeypatch.setenv("NOTION_TOKEN", "t")
        monkeypatch.delenv("NOTION_ROOT_PAGE_ID", raising=False)
        monkeypatch.delenv("NOTION_EXPORT_PARENT_PAGE_ID", raising=False)
        response = request(app, "POST", "/api/export", {"destination": "notion", "kind": "standup"})
        assert response.code == 409
        assert json.loads(response.body)["error"]

    def test_an_unresolvable_kind_is_a_400(self, app):
        response = request(app, "POST", "/api/export", {"destination": "copy", "kind": "ceremonies"})
        assert response.code == 400
        assert "ceremonies" in json.loads(response.body)["error"]

    def test_a_missing_run_is_a_404(self, app, monkeypatch):
        import yeaboi.sharing.resolve as resolver

        monkeypatch.setattr(resolver, "load", lambda *_a, **_k: None)
        assert request(app, "POST", "/api/export", {"destination": "copy", "kind": "standup"}).code == 404


class TestKindCapabilities:
    def test_poker_exports_and_nothing_else(self, app):
        """A surface reads this instead of keeping its own table, so it never
        offers a button the backend would refuse."""
        kinds = {row["kind"]: row for row in json.loads(request(app, "GET", "/api/artifacts/kinds").body)["kinds"]}
        assert kinds["poker"]["export"] and not kinds["poker"]["share"]

    def test_a_team_profile_shares_but_is_not_correctable(self, app):
        kinds = {row["kind"]: row for row in json.loads(request(app, "GET", "/api/artifacts/kinds").body)["kinds"]}
        assert kinds["analysis"]["share"] and not kinds["analysis"]["edit"]


class TestShare:
    def test_refuses_when_tunnels_are_off(self, app, resolved, monkeypatch):
        monkeypatch.setenv("YEABOI_NO_TUNNEL", "1")
        response = request(app, "POST", "/api/shares", {"kind": "standup"})
        assert response.code == 409
        assert "YEABOI_NO_TUNNEL" in json.loads(response.body)["error"]

    def test_empty_with_nothing_published(self, app):
        assert json.loads(request(app, "GET", "/api/shares").body)["shares"] == []

    def test_unknown_share_is_404(self, app):
        assert request(app, "GET", "/api/shares/nope").code == 404
        assert request(app, "POST", "/api/shares/nope/close", {}).code == 404

    def test_discard_needs_an_editable_share(self, app):
        from yeaboi.app.supervisor import ShareSession

        server = FakeServer()
        app.boards._shares["s9"] = ShareSession(
            share_id="s9",
            kind="analysis",
            title="Team Profile",
            session_id="t1",
            run_id=0,
            server=server,
            link=SecureLink(server, surface="share"),
            started_at="2026-08-23T10:00:00+00:00",
        )
        assert request(app, "POST", "/api/shares/s9/discard", {}).code == 400


class TestAnonymize:
    def test_streams_op_progress_done(self, app, resolved, monkeypatch):
        import yeaboi.anonymize.engine as engine
        from yeaboi.agent.state import AnonymizedOutput

        def fake(_text, *, on_progress=None, **_kw):
            if on_progress:
                on_progress("Masking known terms")
            return AnonymizedOutput(replacements=[("Acme", "Company A")], anonymized_text="masked")

        monkeypatch.setattr(engine, "run_anonymize", fake)
        lines = drain(request(app, "POST", "/api/anonymize", {"kind": "standup"}))
        assert [line["type"] for line in lines] == ["op", "progress", "done"]
        assert lines[-1]["replacements"] == [["Acme", "Company A"]]
        assert "1 masked" in lines[-1]["note"]

    def test_a_failure_is_a_line_not_a_crash(self, app, resolved, monkeypatch):
        import yeaboi.anonymize.engine as engine

        def boom(*_a, **_k):
            raise RuntimeError("model unavailable")

        monkeypatch.setattr(engine, "run_anonymize", boom)
        lines = drain(request(app, "POST", "/api/anonymize", {"kind": "standup"}))
        assert lines[-1]["type"] == "error"
        # Never the exception text: an SDK error's str() is its whole response.
        assert lines[-1]["message"] == "Anonymize failed (see logs)."

    def test_the_op_is_removed_when_the_stream_ends(self, app, resolved, monkeypatch):
        import yeaboi.anonymize.engine as engine
        from yeaboi.agent.state import AnonymizedOutput

        monkeypatch.setattr(engine, "run_anonymize", lambda *_a, **_k: AnonymizedOutput())
        lines = drain(request(app, "POST", "/api/anonymize", {"kind": "standup"}))
        assert app.ops.get(lines[0]["op_id"]) is None


class TestArtifactEdits:
    def test_unknown_kind_is_404(self, app):
        assert request(app, "GET", "/api/artifacts/duck/edits").code == 404

    def test_fields_and_history_arrive_together(self, app, tmp_path, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "get_db_path", lambda: tmp_path / "sessions.db")
        body = json.loads(request(app, "GET", "/api/artifacts/standup/edits?session_id=s1&run_id=4").body)
        assert body["kind"] == "standup"
        assert body["artifact"]["kind"] == "standup"
        assert body["ops"]
        assert body["count"] == 0
        assert body["attribution"] == "self-declared"

    def test_a_bad_run_id_is_a_400(self, app):
        assert request(app, "GET", "/api/artifacts/standup/edits?run_id=soon").code == 400
