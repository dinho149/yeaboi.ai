"""Tests for the join-gate document and its Content-Security-Policies.

The gate is the only yeaboi page an *unauthenticated* stranger can reach: the
tunnel URL is public, and anyone who has it gets this document. Two properties
therefore matter more than how it looks — it must tell them nothing about what
is behind it, and it must run under a policy that gives a compromised bundle
nowhere to send anything.
"""

from __future__ import annotations

import json
import re

from tests._pages import CREDIT_URL, assert_self_contained, island, markup
from yeaboi.sharing.gate import ARTIFACT_CSP, GATE_CSP, gate_boot, render_gate_page
from yeaboi.web.assets import read_asset
from yeaboi.web.brand import DEFAULT_FOOTER, MODE_LABELS, MODE_WORDMARKS, frame_title


def _html_tag(page: str) -> str:
    """The opening ``<html …>`` tag, where ``data-mode`` lives.

    Checked in isolation because the inlined bundle mentions `data-mode` too —
    `runtime/theme.ts` sets it — so a substring search over the document would
    pass or fail for the wrong reason.
    """
    match = re.search(r"<html[^>]*>", page)
    assert match is not None
    return match.group(0)


def _directives(csp: str) -> dict[str, str]:
    parts = [d.strip() for d in csp.split(";") if d.strip()]
    return {d.split(" ", 1)[0]: d.split(" ", 1)[1] if " " in d else "" for d in parts}


class TestGateDocument:
    def test_is_one_self_contained_document(self):
        page = render_gate_page()
        assert_self_contained(page)

    def test_names_no_external_origin_at_all(self):
        """Stricter than the shared helper, and only this page can be.

        Every other surface carries a payload that may legitimately *mention* a
        URL — the boards ship the radio stream list, a report ships ticket
        links. The gate has no payload and no content, so the absence of any
        origin is checkable here and nowhere else. www.w3.org is excluded: it
        appears as an SVG xmlns, which is an identifier, not a retrieval.

        The byline's link to the project site is blanked once before the scan —
        see ``CREDIT_URL``. It is the destination of a click, not a load, and a
        second occurrence would still fail here.
        """
        page = render_gate_page().replace(f'"{CREDIT_URL}"', '""', 1)
        assert not re.search(r"https?://(?!www\.w3\.org)", page)

    def test_inlines_the_gate_bundle(self):
        page = render_gate_page()
        assert read_asset("gate.js") in page
        assert read_asset("gate.css") in page

    def test_says_nothing_about_what_is_shared(self):
        """It may name the mode. It may not name the share.

        `ShareDocument.title` is "1:1 Prep — Ada", "Retro — Sprint 42",
        "Q3 headcount plan". The page is reachable by anyone holding the tunnel
        URL, so the document is still a constant *of the mode*: one word from a
        fixed vocabulary, and nothing that varies run to run.
        """
        page = render_gate_page("retro")
        assert render_gate_page("retro") == page  # constant per mode, not per share
        boot = island(page)
        assert set(boot) == {"mode", "wordmark", "frameTitle", "heading", "eyebrow", "cta", "footer"}
        assert boot["wordmark"] == "retro"
        assert boot["mode"] == "retro"

    def test_the_island_carries_only_the_mode_vocabulary(self):
        """Every value must be findable in web/brand.py, not in a ShareDocument."""
        allowed = set(MODE_WORDMARKS.values()) | {"", "yeaboi"}
        for mode in MODE_LABELS:
            boot = island(render_gate_page(mode))
            assert boot["wordmark"] in allowed
            assert boot["mode"] in set(MODE_LABELS) | {""}

    def test_two_modes_produce_two_documents(self):
        assert render_gate_page("retro") != render_gate_page("standup")

    def test_names_the_mode_and_wears_its_accent(self):
        page = render_gate_page("standup")
        assert 'data-mode="standup"' in page
        assert "<title>Daily Standup — shared with yeaboi</title>" in page
        assert island(page)["wordmark"] == "standup"

    def test_roadmap_borrows_plannings_accent_but_keeps_its_word(self):
        boot = island(render_gate_page("roadmap"))
        assert boot["mode"] == "planning"
        assert boot["wordmark"] == "roadmap"

    def test_performance_stays_anonymous(self):
        """The one mode whose name is itself the disclosure.

        Telling a stranger holding a quick-tunnel URL that somebody's 1:1 or
        six-month review is behind the door says something real about a named
        colleague. "A retro" does not.
        """
        page = render_gate_page("performance")
        assert _html_tag(page) == '<html lang="en">'
        assert "<title>Shared with yeaboi</title>" in page
        assert island(page)["wordmark"] == "yeaboi"
        # The word must not reach the visitor through the markup either — the
        # <noscript> body is the one thing rendered with scripting off.
        assert "performance" not in markup(page).lower()

    def test_an_unknown_mode_degrades_to_the_neutral_gate(self):
        for mode in ("", "nonsense", '" onload="alert(1)'):
            page = render_gate_page(mode)
            assert _html_tag(page) == '<html lang="en">', mode
            assert island(page)["wordmark"] == "yeaboi"

    def test_never_carries_the_share_title_host_or_code(self):
        """Scoped to the island and the markup — the inlined bundle is not ours.

        A blanket substring search over the whole document reads 40 KB of
        minified CSS and JS, where "token" is a design-token comment. What this
        test is about is what the *server* put on the page.

        The probes are all things that could only have come from a
        ``ShareDocument`` or the running server. Note that a mode's own label is
        not one of them — "Sprint Plan" is planning's name for itself, and the
        sprint a share is *about* never reaches here.
        """
        for mode in [*MODE_LABELS, ""]:
            page = render_gate_page(mode)
            blob = json.dumps(island(page)) + markup(page)
            for secret in ("token", "joinCode", "shareUrl", "trycloudflare", "127.0.0.1"):
                assert secret not in blob, f"{mode}: {secret}"

    def test_works_without_javascript(self):
        page = render_gate_page()
        assert "<noscript>" in page
        assert "Enter the access code" in page

    def test_is_not_indexable(self):
        # A quick-tunnel hostname is public and has been crawled before.
        assert 'content="noindex, nofollow"' in render_gate_page()

    def test_is_cached_per_process(self):
        assert render_gate_page("retro") is render_gate_page("retro")
        assert render_gate_page() is render_gate_page()


class TestPolicies:
    def test_both_policies_deny_everything_by_default(self):
        for csp in (GATE_CSP, ARTIFACT_CSP):
            assert _directives(csp)["default-src"] == "'none'"

    def test_neither_policy_allows_eval(self):
        for csp in (GATE_CSP, ARTIFACT_CSP):
            assert "unsafe-eval" not in csp

    def test_only_the_gate_may_talk_to_the_server(self):
        # The gate POSTs the code back; the artifact is a finished snapshot and
        # has no reason to reach anywhere, including its own origin.
        assert _directives(GATE_CSP)["connect-src"] == "'self'"
        assert _directives(ARTIFACT_CSP)["connect-src"] == "'none'"

    def test_no_policy_allows_an_external_origin(self):
        for csp in (GATE_CSP, ARTIFACT_CSP):
            for value in _directives(csp).values():
                assert "http" not in value, f"external origin allowed: {csp}"

    def test_images_and_fonts_are_data_uris_only(self):
        for csp in (GATE_CSP, ARTIFACT_CSP):
            directives = _directives(csp)
            assert directives["img-src"] == "data:"
            assert directives["font-src"] == "data:"

    def test_base_and_form_targets_are_locked_down(self):
        """`<base>` would retarget the relative join POST; form-action backs it up.

        The gate's submit handler always calls preventDefault, so a real form
        navigation only happens when the script is broken — and that navigation
        would put the typed code in a URL. Denying it makes that a no-op.
        """
        for csp in (GATE_CSP, ARTIFACT_CSP):
            directives = _directives(csp)
            assert directives["base-uri"] == "'none'"
            assert directives["form-action"] == "'none'"
            assert directives["frame-ancestors"] == "'none'"


class TestGateBoot:
    """The payload on its own, which is what the TS-side guard checks.

    Split out of ``render_gate_page`` so ``test_web_wire_shapes`` can snapshot
    it and ``frontend/src/test/fixtures/wire.ts`` can assert it ``satisfies``
    ``GateBoot``. Without that tie, renaming a field on the TypeScript side
    typechecks and ships the neutral gate to every share, because
    ``gate/main.tsx`` treats every prop as optional by design.
    """

    def test_a_branded_mode_carries_its_whole_vocabulary(self):
        boot = gate_boot("standup")
        assert boot["mode"] == "standup"
        assert boot["wordmark"] == "standup"
        assert boot["frameTitle"] == frame_title("standup")
        assert "standup" in boot["heading"]

    def test_performance_stays_neutral(self):
        # The one mode whose name is itself the disclosure — see
        # GATE_BRANDED_MODES in web/brand.py.
        boot = gate_boot("performance")
        assert boot["mode"] == ""
        assert boot["wordmark"] == "yeaboi"
        assert boot["frameTitle"] == "yeaboi"
        assert "performance" not in boot["heading"].lower()

    def test_an_unknown_mode_is_neutral_too(self):
        assert gate_boot("not-a-mode")["wordmark"] == "yeaboi"
        assert gate_boot("")["mode"] == ""

    def test_the_byline_comes_from_python(self):
        """It was the last surface whose credit lived in the TSX.

        Every other one reads it off the island, so a change to the string had
        to be made in two languages to take effect everywhere.
        """
        assert gate_boot("retro")["footer"] == DEFAULT_FOOTER

    def test_the_frame_title_is_not_spelled_twice(self):
        for mode in MODE_LABELS:
            boot = gate_boot(mode)
            assert boot["frameTitle"] == frame_title(boot["mode"])
