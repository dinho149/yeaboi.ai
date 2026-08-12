"""Python ↔ Go parity for analysis.classify_markers + analysis.score_code (contracts/v1).

Both implementations run over the same synthetic inputs documents; the whole
wire result must be equal (floats to 1e-9) AND every JSON object's key order
must match — object order is contractual for these methods (the health blob
and practices feed json.dumps downstream). Skipped when no ``yeaboi-core``
binary is available; ``make parity`` and CI run it unskipped.

The corpus is deliberately nastier than the unit fixtures: unicode authors,
titles and paths (including a Turkish dotted İ and an emoji), an
over-80-code-point sample title, jira false-prefix bait (SHA-256, CVE-…)
next to a real ticket key, branch and AB# and bare-# references, a squash
suffix, agent-authored bot traffic beside human AI-marked work, a same-scope
hotspot at both boundaries, churn at the 499/500 boundary, failed and
truncated and deleted and binary and generated and bare-``build`` changes,
and a JSON-null additions field.
"""

from __future__ import annotations

import os
import shutil

import pytest

from tests.parity._diff import approx_equal, key_orders
from yeaboi.analysis import aggregate
from yeaboi.gocore.client import CoreClient

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
    client = CoreClient(str(BINARY))
    try:
        client.hello()
        yield client
    finally:
        client.close()


# ── Corpus ────────────────────────────────────────────────────────────────

LONG_TITLE = "rénovation " + "é" * 70 + " end-marker-past-eighty"  # 93 code points, multi-byte


def _items() -> list[dict]:
    return [
        {
            "kind": "commit",
            "title": "PROJ-12 add streaming output (#34)",
            "body": "Co-Authored-By: Claude <noreply@anthropic.com>",
            "author": "José Çelik",
            "source": "github",
            "container": "acme",
            "repository": "app",
            "branch": "feature/PROJ-9-login",
            "key": "a1b2c3d4",
            "url": "https://github.com/acme/app/commit/a1b2c3d4",
            "matched_members": ["José Çelik"],
            "changed_file_paths": ["src/app.py", "tests/test_app.py"],
        },
        {
            "kind": "pr",
            "title": LONG_TITLE,
            "body": "\U0001f916 Generated with Claude Code",
            "author": "José Çelik",
            "source": "github",
            "container": "acme",
            "repository": "app",
            "key": "77",
            "url": "https://github.com/acme/app/pull/77",
            "matched_members": ["José Çelik"],
            "changed_file_paths": ["docs/readme.md"],
        },
        {
            "kind": "commit",
            "title": "İstanbul locale fix for SHA-256 digests and CVE-2026-1234",
            "body": "plain human commit referencing AB#12 and #12",
            "author": "Grace Hopper",
            "source": "azdo",
            "container": "corp",
            "repository": "web",
            "key": "deadbeef",
            "matched_members": ["Grace Hopper"],
        },
        {
            "kind": "pr",
            "title": "docs: update runbook",
            "body": "hand-written, no markers",
            "author": "Grace Hopper",
            "source": "azdo",
            "container": "corp",
            "repository": "web",
            "key": "78",
            "matched_members": ["Grace Hopper"],
            "changed_file_paths": [],
        },
        {
            "kind": "commit",
            "title": "chore(deps): bump lodash",
            "body": "",
            "author": "dependabot[bot]",
            "source": "github",
            "container": "acme",
            "repository": "app",
            "key": "0badf00d",
            "matched_members": [],
            "agent_authored": True,
        },
        {"kind": "review", "title": "LGTM ✅", "author": "Grace Hopper", "source": "azdo"},
        {"kind": "comment", "title": "why though?", "author": "José Çelik", "source": "github"},
        {"kind": "unknown-kind", "title": "ignored by the tally", "author": "Nobody", "source": "github"},
    ]


def _change(path: str, **kw) -> dict:
    base = {
        "provider": "github",
        "container": "acme",
        "repository": "app",
        "path": path,
        "status": "succeeded",
        "additions": 10,
        "deletions": 2,
        "url": f"https://github.com/acme/app/blob/main/{path}",
        "confidence": "high",
    }
    base.update(kw)
    return base


def _changed_files() -> list[dict]:
    hotspot = [_change("src/hot.py", additions=30 + i, url=f"https://x/{i}", confidence="medium") for i in range(5)]
    warm = [_change("src/warm.py") for _ in range(4)]
    return [
        *hotspot,
        *warm,
        _change("src/churny.py", additions=499, deletions=0),
        _change("src/churny2.py", additions=500, deletions=0),
        _change("src/broken.py", status="failed", error="timeout fetching diff"),
        _change("src/half.py", truncated=True),
        _change("src/gone.py", status="deleted"),
        _change("assets/logo.png"),
        _change("dist/bundle.js"),
        _change("build"),
        _change("café/menü.py"),
        _change("tests/test_hot.py"),
        _change("docs/guide.md", additions=None),
    ]


def base_classify_inputs() -> dict:
    return aggregate.build_classify_inputs(items=_items())


def base_score_inputs(**overrides) -> dict:
    kwargs = dict(
        items=_items(),
        changed_files=_changed_files(),
        selected_users=["José Çelik", "Grace Hopper", "Idle Member"],
        window_days=45,
        health_enabled=True,
        changed_file_cache_hits=3,
    )
    kwargs.update(overrides)
    return aggregate.build_score_inputs(**kwargs)


# ── Comparison ────────────────────────────────────────────────────────────


def _go(core: CoreClient, method: str, inputs: dict) -> dict:
    result = core.request(method, inputs)
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
class TestClassifyMarkersParity:
    def test_full_corpus_matches(self, core):
        inputs = base_classify_inputs()
        _assert_match(aggregate.classify_markers(inputs), _go(core, "analysis.classify_markers", inputs))

    def test_empty_items_match(self, core):
        inputs = aggregate.build_classify_inputs(items=[])
        _assert_match(aggregate.classify_markers(inputs), _go(core, "analysis.classify_markers", inputs))


@needs_binary
class TestScoreCodeParity:
    def test_full_corpus_matches(self, core):
        inputs = base_score_inputs()
        _assert_match(aggregate.score_code(inputs), _go(core, "analysis.score_code", inputs))

    def test_health_disabled_matches(self, core):
        inputs = base_score_inputs(health_enabled=False)
        _assert_match(aggregate.score_code(inputs), _go(core, "analysis.score_code", inputs))

    def test_empty_corpus_matches(self, core):
        inputs = aggregate.build_score_inputs(
            items=[],
            changed_files=[],
            selected_users=[],
            window_days=120,
            health_enabled=True,
            changed_file_cache_hits=0,
        )
        _assert_match(aggregate.score_code(inputs), _go(core, "analysis.score_code", inputs))

    def test_no_annotations_match(self, core):
        """Items without changed_file_paths stay out of the file-based denominators."""
        items = [{k: v for k, v in item.items() if k != "changed_file_paths"} for item in _items()]
        inputs = base_score_inputs(items=items)
        _assert_match(aggregate.score_code(inputs), _go(core, "analysis.score_code", inputs))


class TestCorpusSelfGuards:
    """Guard the corpus itself — pure Python, deliberately NOT behind
    ``needs_binary``: if a refactor mutes these signals, the parity runs above
    are no longer covering what this file's docstring says they cover, and
    that must fail the ordinary suite, not just the parity job."""

    def test_classify_corpus_exercises_the_signals_it_claims_to(self):
        result = aggregate.classify_markers(base_classify_inputs())
        signal = result["signal"]
        assert signal["ai_commits"] >= 1 and signal["ai_prs"] >= 1, "expected AI-marked work in both kinds"
        assert signal["per_tool"], "expected at least one tool attribution"
        assert dict(signal["per_author"]).get("José Çelik"), "expected the unicode author attributed"
        assert set(signal["sources_scanned"]) == {"github", "azdo"}
        truncated = [s for s in result["samples"] if s["title"] == LONG_TITLE[:80]]
        assert truncated, "expected the 93-code-point title truncated to exactly 80 code points"

    def test_reference_bait_is_really_bait(self):
        """The SHA-256/CVE title must be a jira near-miss and the real key a hit,
        or the false-prefix denylist is not being exercised at all."""
        from yeaboi.analysis.practices import _jira_hit

        assert not _jira_hit("İstanbul locale fix for SHA-256 digests and CVE-2026-1234")
        assert _jira_hit("PROJ-12 add streaming output (#34)")

    def test_score_corpus_exercises_the_signals_it_claims_to(self):
        result = aggregate.score_code(base_score_inputs())
        finding_ids = {f.get("id", "") for f in result["health"]["findings"]}
        assert any("hotspot" in i for i in finding_ids), "expected the 5-touch hotspot"
        # The churn boundary is exercised from BOTH sides: 500 fires, 499 does not.
        assert "app:src/churny2.py:large-change" in finding_ids, "expected the 500-churn change-size finding"
        assert "app:src/churny.py:large-change" not in finding_ids, "499 churn must stay under the boundary"
        assert result["health"]["action_plan"], "expected a prioritized action plan"
        assert result["health"]["repository_health"]["cached_change_lookups"] == 3
        assert result["health"]["coverage_notes"], "expected coverage notes from the failed/truncated changes"
        statuses = {r.get("analysis_status") for r in result["health"]["file_reports"]}
        assert {"succeeded", "truncated", "failed", "excluded"} <= statuses, (
            "expected the failed/truncated/deleted/binary/generated changes all represented"
        )
        assert result["practices"]["file_data"]["total"], "expected file-based practice denominators"
        rows = {row["member"] for row in result["member_activity"]}
        assert "AI agent accounts" in rows, "expected the dependabot commit routed to the agents row"
        assert "Idle Member" in rows, "expected a zero row for the idle member"
        assert result["activity_counts"] == {"commits": 3, "prs": 2, "reviews": 1, "comments": 1}
