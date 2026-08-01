"""The editable share: the document holder, and the server in front of it.

Driven over real HTTP against a real loopback server rather than by calling
handlers, because the things most likely to be wrong here — which routes exist
without a token, which CSP a document is served under, what a conflict answers
with — are properties of the request, not of a method.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from yeaboi.agent.state import DeliveryReport, MemberUpdate, StandupReport
from yeaboi.artifacts.edits import Edit, EditError
from yeaboi.artifacts.registry import ARTIFACTS
from yeaboi.sharing.documents import editable_share, render_editable_page
from yeaboi.sharing.editable import ConflictError, EditableDocument
from yeaboi.sharing.server import OutputShareServer, ShareDocument

STANDUP = ARTIFACTS["standup"]


def report() -> StandupReport:
    return StandupReport(
        date="2026-08-01",
        team_summary="The team shipped auth.",
        member_updates=(MemberUpdate(name="Ada", summary="Landed login.", blockers="staging db"),),
    )


def an_edit(**kw) -> Edit:
    return Edit(
        edit_id=kw.pop("edit_id", "e1"),
        op=kw.pop("op", "set"),
        path=kw.pop("path", "team_summary"),
        author=kw.pop("author", "Ada"),
        **kw,
    )


@pytest.fixture
def document() -> EditableDocument:
    return EditableDocument(report(), STANDUP, kind="standup", ref="standup:1")


class TestDocument:
    def test_it_starts_at_the_generated_artifact(self, document):
        assert document.current() == report()
        assert document.revision == 0

    def test_an_edit_moves_the_document_and_the_revision(self, document):
        document.apply(an_edit(value="Corrected."))
        assert document.current().team_summary == "Corrected."
        assert document.revision == 1

    def test_the_base_never_moves(self, document):
        document.apply(an_edit(value="Corrected."))
        assert document.base() == report()

    def test_any_version_is_a_prefix_of_the_log(self, document):
        from yeaboi.artifacts.edits import apply_edits

        document.apply(an_edit(edit_id="e1", value="first"))
        document.apply(an_edit(edit_id="e2", value="second"))
        earlier, _ = apply_edits(document.base(), document.edits()[:1], STANDUP)
        assert earlier.team_summary == "first"
        assert document.current().team_summary == "second"

    def test_a_retried_append_is_not_applied_twice(self):
        doc = EditableDocument(DeliveryReport(highlights=("one",)), ARTIFACTS["reporting"])
        doc.apply(an_edit(edit_id="dup", op="append", path="highlights[-]", value="two"))
        doc.apply(an_edit(edit_id="dup", op="append", path="highlights[-]", value="two"))
        assert doc.current().highlights == ("one", "two")
        assert len(doc.edits()) == 1

    def test_a_stale_if_revision_is_a_conflict(self, document):
        document.apply(an_edit(edit_id="e1", value="first"))
        with pytest.raises(ConflictError):
            document.apply(an_edit(edit_id="e2", value="second"), if_revision=0)

    def test_a_matching_if_revision_applies(self, document):
        document.apply(an_edit(edit_id="e1", value="first"))
        document.apply(an_edit(edit_id="e2", value="second"), if_revision=1)
        assert document.current().team_summary == "second"

    def test_a_losing_compare_and_swap_is_a_conflict(self, document):
        with pytest.raises(ConflictError):
            document.apply(an_edit(value="mine", base="what I thought it said"))

    def test_a_refused_edit_is_never_recorded(self, document):
        with pytest.raises(ConflictError):
            document.apply(an_edit(value="mine", base="wrong"))
        assert document.edits() == ()
        assert document.revision == 0

    def test_locking_refuses_further_edits(self, document):
        document.set_locked(True)
        with pytest.raises(EditError, match="closed"):
            document.apply(an_edit(value="x"))

    def test_unlocking_lets_them_through_again(self, document):
        document.set_locked(True)
        document.set_locked(False)
        document.apply(an_edit(value="x"))
        assert document.current().team_summary == "x"

    def test_the_edit_cap_is_enforced(self, document, monkeypatch):
        # The constant is shrunk rather than met: materialisation replays the
        # whole log, so reaching the real cap honestly is quadratic.
        monkeypatch.setattr("yeaboi.sharing.editable.MAX_EDITS", 2)
        document.apply(an_edit(edit_id="e1", value="one"))
        document.apply(an_edit(edit_id="e2", value="two"))
        with pytest.raises(EditError, match="edit limit"):
            document.apply(an_edit(edit_id="e3", value="three"))

    def test_the_host_can_drop_the_last_edit(self, document):
        document.apply(an_edit(edit_id="e1", value="first"))
        document.apply(an_edit(edit_id="e2", value="oops"))
        dropped = document.drop_last()
        assert dropped.edit_id == "e2"
        assert document.current().team_summary == "first"

    def test_dropping_from_an_empty_log_is_harmless(self, document):
        assert document.drop_last() is None


class TestPresence:
    def test_a_heartbeat_lists_someone(self, document):
        document.heartbeat("pid-1", name="Ada", avatar="🦊")
        assert [p["name"] for p in document.presence_list()] == ["Ada"]

    def test_presence_does_not_bump_the_revision(self, document):
        """Heartbeats fire about once a second.

        Bumping here would make every long poll return immediately and turn the
        push transport back into a busy loop — the same reason the retro board's
        heartbeat leaves its revision alone.
        """
        document.heartbeat("pid-1", name="Ada")
        assert document.revision == 0

    def test_the_change_probe_still_sees_presence(self, document):
        """...which is why the watcher probes it separately.

        Otherwise the who's-here row would only refresh when something unrelated
        happened to change.
        """
        before = document.change_probe()
        document.heartbeat("pid-1", name="Ada")
        assert document.change_probe() != before

    def test_a_nameless_pid_is_ignored(self, document):
        document.heartbeat("", name="Nobody")
        assert document.presence_list() == []


class TestSnapshot:
    def test_it_carries_the_corrected_payload(self):
        share = editable_share(report(), kind="standup", ref="standup:1")
        share.document.apply(an_edit(value="Corrected."))
        frame = share.snapshot()
        assert frame["revision"] == 1
        assert frame["report"]["kind"] == "standup"
        # `summary` is a list of sentences, each a list of runs — the very
        # shape that makes the payload one-way and the artifact the thing
        # an edit addresses. Asserting on the serialised frame keeps this
        # test out of that structure.
        assert "Corrected." in json.dumps(frame["report"])

    def test_mine_is_true_only_for_the_asking_browser(self):
        share = editable_share(report(), kind="standup")
        share.document.apply(an_edit(value="x", pid="pid-1"))
        assert share.snapshot("pid-1")["edits"][0]["mine"] is True
        assert share.snapshot("pid-2")["edits"][0]["mine"] is False

    def test_raw_pids_never_reach_the_wire(self):
        share = editable_share(report(), kind="standup")
        share.document.apply(an_edit(value="x", pid="secret-pid"))
        assert "secret-pid" not in json.dumps(share.snapshot("other"))

    def test_an_uneditable_kind_is_refused(self):
        with pytest.raises(ValueError, match="not an editable artifact"):
            editable_share(report(), kind="nonsense")


class TestRenderedPage:
    def test_the_page_is_self_contained(self):
        from tests._pages import assert_self_contained

        share = editable_share(report(), kind="standup")
        assert_self_contained(render_editable_page(share))

    def test_the_editing_key_is_what_switches_the_bundle_on(self):
        from tests._pages import island

        share = editable_share(report(), kind="standup")
        boot = island(render_editable_page(share))
        assert "editing" in boot
        assert boot["editing"]["editable"] is True

    def test_a_file_export_never_carries_that_key(self):
        from tests._pages import island
        from yeaboi.standup.export import build_standup_html

        assert "editing" not in island(build_standup_html(report()))

    def test_no_secret_reaches_the_payload(self):
        # GET / is unauthenticated for the gate, and the same renderer writes
        # documents to disk. A token in the island would be in both.
        from tests._pages import island

        share = editable_share(report(), kind="standup")
        assert "token" not in json.dumps(island(render_editable_page(share)))


# ---------------------------------------------------------------------------
# Over real HTTP
# ---------------------------------------------------------------------------


class Client:
    def __init__(self, server: OutputShareServer) -> None:
        self.base = f"http://127.0.0.1:{server.port}"
        self.token = server.token
        self.admin = server.admin_token

    def get(self, path: str, *, token: str | None = None) -> tuple[int, str, dict]:
        url = f"{self.base}{path}"
        if token != "":
            url += ("&" if "?" in path else "?") + f"token={token or self.token}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 — loopback only
                return response.status, response.read().decode(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(), dict(exc.headers)

    def post(self, path: str, body: dict, *, token: str | None = None) -> tuple[int, dict]:
        url = f"{self.base}{path}"
        if token != "":
            url += f"?token={token or self.token}"
        request = urllib.request.Request(  # noqa: S310 — loopback only
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


@pytest.fixture
def served():
    share = editable_share(report(), kind="standup", ref="standup:1")
    recorded: list[Edit] = []
    server = OutputShareServer(
        ShareDocument(title="Daily Standup", html="<p>unused</p>", source_mode="standup"),
        editable=share,
        on_edit=lambda _share, edit, _ip: recorded.append(edit),
    )
    server.start()
    try:
        yield server, share, recorded
    finally:
        server.stop()


class TestServedEditable:
    def test_the_document_is_served_under_the_edit_csp(self, served):
        from yeaboi.web.security import ARTIFACT_CSP, EDIT_CSP

        server, _, _ = served
        status, body, headers = Client(server).get("/")
        assert status == 200
        assert headers["Content-Security-Policy"] == EDIT_CSP
        assert headers["Content-Security-Policy"] != ARTIFACT_CSP
        assert "yeaboi-data" in body

    def test_without_a_token_the_gate_is_served_instead(self, served):
        from yeaboi.web.security import GATE_CSP

        server, _, _ = served
        status, body, headers = Client(server).get("/", token="")  # "" = send no token
        assert status == 200
        assert headers["Content-Security-Policy"] == GATE_CSP
        assert "The team shipped auth" not in body

    def test_an_edit_applies_and_answers_with_fresh_state(self, served):
        server, share, _ = served
        status, body = Client(server).post(
            "/api/edit",
            {"edit_id": "e1", "op": "set", "path": "team_summary", "value": "Corrected.", "author": "Ada"},
        )
        assert status == 200 and body["ok"] is True
        assert body["state"]["revision"] == 1
        assert share.document.current().team_summary == "Corrected."

    def test_an_accepted_edit_is_handed_to_the_persistence_callback(self, served):
        server, _, recorded = served
        Client(server).post("/api/edit", {"edit_id": "e1", "op": "set", "path": "team_summary", "value": "x"})
        assert [e.edit_id for e in recorded] == ["e1"]

    def test_an_edit_without_a_token_is_refused(self, served):
        server, share, _ = served
        status, _ = Client(server).post("/api/edit", {"op": "set", "path": "team_summary", "value": "x"}, token="")
        assert status == 404
        assert share.document.revision == 0

    def test_an_uneditable_field_is_a_400_not_a_500(self, served):
        server, _, _ = served
        status, body = Client(server).post("/api/edit", {"op": "set", "path": "my_name", "value": "x"})
        assert status == 400 and "not editable" in body["error"]

    def test_a_conflict_answers_409_with_the_newer_text(self, served):
        server, share, _ = served
        Client(server).post("/api/edit", {"edit_id": "a", "op": "set", "path": "team_summary", "value": "theirs"})
        status, body = Client(server).post(
            "/api/edit",
            {"edit_id": "b", "op": "set", "path": "team_summary", "value": "mine", "base": "The team shipped auth."},
        )
        assert status == 409
        assert "state" in body, "a conflict must hand back what the document now says"

    def test_a_malformed_path_does_not_crash_the_handler(self, served):
        server, _, _ = served
        status, _ = Client(server).post("/api/edit", {"op": "set", "path": "member_updates[", "value": "x"})
        assert status == 400

    def test_an_oversized_body_is_refused(self, served):
        server, _, _ = served
        status, _ = Client(server).post("/api/edit", {"op": "set", "path": "team_summary", "value": "x" * 20000})
        assert status == 413

    def test_state_reflects_an_edit(self, served):
        server, _, _ = served
        client = Client(server)
        client.post("/api/edit", {"edit_id": "e1", "op": "set", "path": "team_summary", "value": "Corrected."})
        status, body, _ = client.get("/api/state")
        assert status == 200
        assert json.loads(body)["revision"] == 1

    def test_state_without_a_token_is_refused(self, served):
        server, _, _ = served
        status, _, _ = Client(server).get("/api/state", token="")
        assert status == 404

    def test_presence_answers_ok(self, served):
        server, share, _ = served
        status, body = Client(server).post("/api/presence", {"pid": "p1", "name": "Ada", "avatar": "🦊"})
        assert status == 200 and body["ok"] is True
        assert [p["name"] for p in share.document.presence_list()] == ["Ada"]


class TestAdminRoutes:
    def test_a_code_joiner_cannot_lock(self, served):
        server, share, _ = served
        status, _ = Client(server).post("/api/admin/lock", {"locked": True})
        assert status == 403
        assert share.document.locked is False

    def test_the_host_can_lock(self, served):
        server, share, _ = served
        client = Client(server)
        status, _ = client.post("/api/admin/lock", {"locked": True, "admin": client.admin})
        assert status == 200 and share.document.locked is True

    def test_a_locked_document_refuses_edits(self, served):
        server, _, _ = served
        client = Client(server)
        client.post("/api/admin/lock", {"locked": True, "admin": client.admin})
        status, body = client.post("/api/edit", {"op": "set", "path": "team_summary", "value": "x"})
        assert status == 400 and "closed" in body["error"]

    def test_the_host_can_drop_the_last_edit(self, served):
        server, share, _ = served
        client = Client(server)
        client.post("/api/edit", {"edit_id": "e1", "op": "set", "path": "team_summary", "value": "oops"})
        status, _ = client.post("/api/admin/revert", {"admin": client.admin})
        assert status == 200
        assert share.document.current().team_summary == "The team shipped auth."

    def test_the_admin_token_is_not_the_join_token(self, served):
        server, _, _ = served
        assert server.admin_token != server.token

    def test_the_admin_token_never_leaves_through_the_join_flow(self, served):
        server, _, _ = served
        status, body = Client(server).post("/api/join", {"code": server.join_code}, token="")
        assert status == 200
        assert body["token"] == server.token
        assert server.admin_token not in json.dumps(body)


class TestReadOnlyShareIsUnchanged:
    """The non-editable path must behave exactly as it did before any of this."""

    @pytest.fixture
    def plain(self):
        server = OutputShareServer(ShareDocument(title="T", html="<p>hello</p>", source_mode="standup"))
        server.start()
        try:
            yield server
        finally:
            server.stop()

    def test_the_artifact_is_still_served_under_the_artifact_csp(self, plain):
        from yeaboi.web.security import ARTIFACT_CSP

        status, body, headers = Client(plain).get("/")
        assert status == 200 and "<p>hello</p>" in body
        assert headers["Content-Security-Policy"] == ARTIFACT_CSP

    @pytest.mark.parametrize("path", ["/api/state", "/api/invite"])
    def test_the_editable_read_routes_do_not_exist(self, plain, path):
        assert Client(plain).get(path)[0] == 404

    @pytest.mark.parametrize("path", ["/api/edit", "/api/presence", "/api/admin/lock"])
    def test_the_editable_write_routes_do_not_exist(self, plain, path):
        assert Client(plain).post(path, {"admin": plain.admin_token})[0] == 404

    def test_joining_still_works(self, plain):
        status, body = Client(plain).post("/api/join", {"code": plain.join_code}, token="")
        assert status == 200 and body["token"] == plain.token

    def test_a_bad_code_is_still_refused(self, plain):
        assert Client(plain).post("/api/join", {"code": "WRON-GXXX"}, token="")[0] == 403
