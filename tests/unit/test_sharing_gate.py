"""Tests for the join-gate document and its Content-Security-Policies.

The gate is the only yeaboi page an *unauthenticated* stranger can reach: the
tunnel URL is public, and anyone who has it gets this document. Two properties
therefore matter more than how it looks — it must tell them nothing about what
is behind it, and it must run under a policy that gives a compromised bundle
nowhere to send anything.
"""

from __future__ import annotations

import re

from yeaboi.sharing.gate import ARTIFACT_CSP, GATE_CSP, render_gate_page
from yeaboi.web.assets import read_asset


def _directives(csp: str) -> dict[str, str]:
    parts = [d.strip() for d in csp.split(";") if d.strip()]
    return {d.split(" ", 1)[0]: d.split(" ", 1)[1] if " " in d else "" for d in parts}


class TestGateDocument:
    def test_is_one_self_contained_document(self):
        page = render_gate_page()
        # No external reference of any kind: the tunnel CSP forbids other
        # origins, and a <link> or <script src> would leave a blank page.
        assert "<script src" not in page
        assert "<link" not in page
        assert not re.search(r"https?://(?!www\.w3\.org)", page)

    def test_inlines_the_gate_bundle(self):
        page = render_gate_page()
        assert read_asset("gate.js") in page
        assert read_asset("gate.css") in page

    def test_says_nothing_about_what_is_shared(self):
        """An unauthenticated visitor must not learn the artifact's subject.

        `ShareDocument.title` is something like "Q3 headcount plan". The page is
        reachable by anyone holding the tunnel URL, so the shell is a constant:
        there is no per-share data in it to leak in the first place.
        """
        page = render_gate_page()
        assert 'id="yeaboi-data"' not in page, "the gate must not carry a data island"
        assert render_gate_page() == page  # constant, not per-share

    def test_works_without_javascript(self):
        page = render_gate_page()
        assert "<noscript>" in page
        assert "Enter the access code" in page

    def test_is_not_indexable(self):
        # A quick-tunnel hostname is public and has been crawled before.
        assert 'content="noindex, nofollow"' in render_gate_page()

    def test_is_cached_per_process(self):
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
