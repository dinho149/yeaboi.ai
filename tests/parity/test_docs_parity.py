"""Python ↔ Go parity for analysis.score_docs (contracts/v1).

Both implementations run over the same synthetic page corpus; the whole wire
result must be equal (floats to 1e-9) AND every JSON object's key order must
match — object order is contractual (the blob feeds json.dumps downstream and
is persisted with the team profile). Skipped when no ``yeaboi-core`` binary is
available; ``make parity`` and CI run it unskipped.

The corpus is deliberately nastier than the unit fixtures, aimed at the
RE2-vs-Python regex seams: an NBSP after a full stop (Python's unicode ``\\s``
splits the sentence there), an NBSP inside an AI-disclosure phrase, a Turkish
dotted İ whose ``str.lower()`` becomes two code points and defeats a word
boundary, an Arabic-Indic numbered list (unicode ``\\d``), an owner line whose
``\\s*`` crosses a blank line, a heading reached across blank lines, a fenced
code block, a cached asset passed through beside fresh bodies, an over-80-code-
point multi-byte title, an empty-bodied page that produces no asset, and an
empty platform string whose falsy scope must survive into the action plan.
"""

from __future__ import annotations

import os
import shutil

import pytest

from tests.parity._diff import approx_equal, key_orders
from yeaboi.analysis import aggregate

BINARY = os.environ.get("YEABOI_CORE_BIN") or shutil.which("yeaboi-core")

# Class-level, not module-level: the corpus self-guards are pure Python and
# must run in the ordinary suite too — a corpus that stops exercising what
# this file claims should fail `make test`, not just the parity job.
needs_binary = pytest.mark.skipif(
    not BINARY or not os.path.isfile(BINARY or ""),
    reason="yeaboi-core binary not available (run `make parity`)",
)


@pytest.fixture
def core():
    from yeaboi.gocore.client import CoreClient

    client = CoreClient(str(BINARY))
    try:
        client.hello()
        yield client
    finally:
        client.close()


# ── Corpus ────────────────────────────────────────────────────────────────

LONG_TITLE = "dökümantasyon " + "ö" * 70 + " end-marker-past-eighty"  # 96 code points, multi-byte

_RUNBOOK_TEXT = (
    "# Purpose\n\n"
    "This runbook explains the rollout.\nOwner | SRE\n\n"
    "- Run the check.\n"
    "- Verify the result.\n\n"
    "```\ndeploy --force # not a heading, not a verb that counts\n```\n"
)

# The NBSPs are real U+00A0 characters: one after a full stop (Python's
# unicode \\s splits the sentence there), one inside the disclosure context
# phrase (its \\s+ must cross it). The marker gate is satisfied independently
# by the noreply@anthropic.com marker — "generated with claude code" is
# itself a marker pattern with LITERAL spaces, so an NBSP inside it would
# fail the marker, not exercise the context.
_NBSP_TEXT = (
    "Deployment done. Next step follows here.\n\n"
    "Drafted with help (contact noreply@anthropic.com about the assistant).\n"
)

_TURKISH_TEXT = "VERİFY the İstanbul rollout budget. Ölçüm tamam.\n"

# Latin words keep the page out of the zero-word early return (words are
# [A-Za-z'] only), so the unicode-\\d list detection is actually reached.
_ARABIC_LIST_TEXT = "Steps for deployment.\n٣. تشغيل الفحص\n٤. مراجعة النتيجة\n"

_OWNER_GAP_TEXT = "Guide to the weekly report.\n\nOwner\n\n: Jane\n\nWrite the summary first.\n"

_HEADING_GAP_TEXT = "intro line\n\n\n   # Reached across blank lines\nshort tail.\n"

_DENSE_TEXT = (
    "Considering the multiplicity of interdependent organizational considerations "
    "which necessitate comprehensive deliberation regarding infrastructural "
    "modernization initiatives alongside continuous documentation stewardship "
    "obligations throughout heterogeneous engineering constituencies, stakeholders "
    "should conscientiously internalize the strategic ramifications communicated herein."
)


def _pages() -> list[dict]:
    return [
        {
            "platform": "confluence",
            "container": "PSO",
            "key": "cached-1",
            "version": "7",
            "title": "Cached runbook",
            "text": "",
            "cache_status": "hit",
            "asset": {
                "title": "Cached runbook",
                "platform": "confluence",
                "clarity": 72.5,
                "usefulness": 80.0,
                "owned": True,
                "actionable": True,
                "structured": True,
                "has_code_blocks": False,
                "marked": False,
                "url": "https://x/wiki/cached-1",
                "key": "cached-1",
                "container": "PSO",
                "version": "7",
            },
        },
        {
            "platform": "confluence",
            "container": "PSO",
            "key": "runbook",
            "version": "12",
            "title": "Fresh runbook",
            "url": "https://x/wiki/runbook",
            "text": _RUNBOOK_TEXT,
        },
        {
            "platform": "notion",
            "container": "root",
            "key": "nbsp",
            "version": "2026-01-05",
            "title": "NBSP page",
            "text": _NBSP_TEXT,
        },
        {
            "platform": "notion",
            "container": "root",
            "key": "turkish",
            "version": "2026-01-06",
            "title": "İstanbul notes",
            "text": _TURKISH_TEXT,
        },
        {
            "platform": "notion",
            "container": "root",
            "key": "arabic",
            "version": "2026-01-07",
            "title": "خطوات",
            "text": _ARABIC_LIST_TEXT,
        },
        {
            "platform": "confluence",
            "container": "OPS",
            "key": "owner-gap",
            "version": "3",
            "title": "Owner across a gap",
            "text": _OWNER_GAP_TEXT,
        },
        {
            "platform": "confluence",
            "container": "OPS",
            "key": "heading-gap",
            "version": "4",
            "title": LONG_TITLE,
            "text": _HEADING_GAP_TEXT,
        },
        {
            "platform": "",
            "container": "",
            "key": "no-platform",
            "timestamp": "2026-02-01T10:00:00Z",
            "title": "Dense wall",
            "text": _DENSE_TEXT,
        },
        {
            "platform": "notion",
            "container": "root",
            "key": "unreadable",
            "title": "Empty body",
            "text": "   ",
            "read_error": "empty page body",
        },
        {
            "platform": "notion",
            "container": "root",
            "key": "untitled",
            "version": "1",
            "text": "Short page. Nothing else.",
        },
    ]


def base_inputs(**overrides) -> dict:
    pages = overrides.pop("pages", _pages())
    assert not overrides
    return aggregate.build_score_docs_inputs(pages=pages)


# ── Comparison ────────────────────────────────────────────────────────────


def _go(core, inputs: dict) -> dict:
    result = core.request("analysis.score_docs", inputs)
    assert result.pop("contract_version", None) == 1
    return result


def _assert_match(py: dict, go: dict) -> None:
    diffs = approx_equal(py, go, "result")
    assert not diffs, "\n".join(diffs[:40])
    # Object key order is contractual (the blob feeds json.dumps downstream).
    py_orders, go_orders = key_orders(py), key_orders(go)
    order_diffs = [
        f"{path}: {py_orders.get(path)} != {go_orders.get(path)}"
        for path in sorted(set(py_orders) | set(go_orders))
        if py_orders.get(path) != go_orders.get(path)
    ]
    assert not order_diffs, "\n".join(order_diffs[:40])


@needs_binary
class TestScoreDocsParity:
    def test_full_corpus_matches(self, core):
        inputs = base_inputs()
        _assert_match(aggregate.score_docs(inputs), _go(core, inputs))

    def test_empty_pages_match(self, core):
        inputs = base_inputs(pages=[])
        _assert_match(aggregate.score_docs(inputs), _go(core, inputs))

    def test_small_sample_boundary_matches(self, core):
        # Four scoreable pages sit under the five-page trend threshold; the
        # summary's small_sample flip must agree on both sides of the seam.
        inputs = base_inputs(pages=_pages()[:4])
        py = aggregate.score_docs(inputs)
        assert py["summary"]["small_sample"] is True
        _assert_match(py, _go(core, inputs))


class TestDocsCorpusSelfGuards:
    """Guard the corpus itself — pure Python, deliberately NOT behind
    ``needs_binary``: if a refactor mutes these signals, the parity runs above
    are no longer covering what this file's docstring says they cover, and
    that must fail the ordinary suite, not just the parity job."""

    def test_corpus_exercises_the_regex_seams_it_claims_to(self):
        result = aggregate.score_docs(base_inputs())
        assets = {asset["key"]: asset for asset in result["assets"]}

        # The empty-bodied page produced no asset; the cached one passed through.
        assert set(assets) == {
            "cached-1",
            "runbook",
            "nbsp",
            "turkish",
            "arabic",
            "owner-gap",
            "heading-gap",
            "no-platform",
            "untitled",
        }
        assert assets["cached-1"] == _pages()[0]["asset"]

        # NBSP: the disclosure phrase spans an NBSP, so unicode \s+ must match.
        assert assets["nbsp"]["marked"] is True
        # Turkish İ: str.lower() yields i+U+0307, defeating \bverify\b.
        assert assets["turkish"]["actionable"] is False
        # Arabic-Indic digits are unicode \d — the numbered list counts as structure.
        assert assets["arabic"]["structured"] is True
        # The owner line's \s* crosses a blank line.
        assert assets["owner-gap"]["owned"] is True
        # \s{0,3} reaches the heading across blank lines.
        assert assets["heading-gap"]["structured"] is True
        # 96-code-point multi-byte title truncates to exactly 80 code points.
        assert assets["heading-gap"]["title"] == LONG_TITLE[:80]
        assert len(assets["heading-gap"]["title"]) == 80
        # Missing title key falls back before truncation; code fence is neutral.
        assert assets["untitled"]["title"] == "Untitled"
        assert assets["runbook"]["has_code_blocks"] is True
        assert assets["runbook"]["owned"] is True

        signal = result["signal"]
        assert signal["pages_scanned"] == 9
        assert signal["ai_marked_pages"] >= 1
        # All three clarity bands are populated, so banding is exercised.
        assert signal["clear_pages"] >= 1
        assert signal["mixed_pages"] >= 1
        assert signal["unclear_pages"] >= 1
        assert signal["flagged_pages"], "expected flagged call-outs"
        # The empty platform string is tallied (falsy scopes are kept).
        assert ["", 1] in signal["per_platform"]
        assert signal["avg_ai_likelihood"] == 0 and signal["likely_ai_pages"] == 0

        assert result["summary"]["small_sample"] is False
        categories = {f["category"] for f in result["findings"]}
        assert categories == {"clarity", "usefulness"}
        # Groups collapse across pages: some action carries the breadth suffix.
        assert any("Affects" in a["evidence"] for a in result["action_plan"])
        assert any(
            any(scope.startswith(":") for scope in action["affected_scope"]) for action in result["action_plan"]
        ), "expected the empty-platform scope kept in the action plan"
        assert result["insights"]["start"], "expected evidence-linked start items"

    def test_small_sample_slice_is_really_small(self):
        result = aggregate.score_docs(base_inputs(pages=_pages()[:4]))
        assert result["signal"]["pages_scanned"] == 4
        assert result["summary"]["small_sample"] is True
