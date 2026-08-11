"""Exact-value pins for the deterministic doc-quality scoring core (analysis/doc_quality.py).

These tests pin the Python reference semantics for the Go twin
(go/internal/analysis/doc_quality.go): any value changed here must be mirrored there.
Every literal below was verified against the running Python implementation — treat the
asserted numbers, key orders, and strings as parity fixtures, not approximations.
"""

from __future__ import annotations

from yeaboi.analysis.doc_quality import (
    _aggregate_doc_assets,
    _analyse_page_asset,
    _clarity_metrics,
    _count_syllables,
    _doc_findings,
    _fallback_doc_quality_insights,
    _has_ai_disclosure,
    _prioritize_doc_actions,
    _usefulness_metrics,
    doc_small_sample,
)
from yeaboi.team_profile import DocQualitySignal

# A short, human paragraph whose Flesch value is hand-checkable (see the test).
_PLAIN_TWO_SENTENCES = "The team ships code every day. We check the logs after lunch."

# Jargon-heavy prose: the Flesch approximation goes deeply negative and clamps to 0.
_DENSE = (
    "The deployment process requires careful preparation. "
    "Every developer should verify the configuration before release."
)

# Headings + bullets + owner + purpose + actionable verb → structure bonus everywhere.
_STRUCTURED = (
    "# Setup Guide\n\n"
    "Purpose: install the tool.\n\n"
    "- Run the installer.\n"
    "- Check the output.\n\n"
    "## Notes\n\n"
    "Owner: Jane\n"
)

_CODE_FENCE = "```\nkubernetes_deployment_reconciliation_orchestrator --enable-multiregional-failover\n```\n"
_SHORT_PROSE = "Run the deploy. It is safe."

# One page exercising every asset field at once (heading, purpose, owner, bullets,
# code fence, and an explicit AI disclosure).
_RICH = (
    "# Deploy Runbook\n\n"
    "Purpose: ship the API safely.\n\n"
    "Owner: Jane\n\n"
    "- Run the deploy script.\n"
    "- Verify the dashboards.\n\n"
    "```\nrelease_orchestrator --region all\n```\n\n"
    "Generated with Claude Code.\n"
)

_CLARITY_KEYS = [
    "word_count",
    "sentence_count",
    "avg_sentence_words",
    "long_sentence_pct",
    "heading_count",
    "has_lists",
    "has_code_blocks",
    "clarity",
]


def _asset(**overrides) -> dict:
    base = {"platform": "notion", "title": "Page", "clarity": 80.0, "usefulness": 80.0, "url": ""}
    base.update(overrides)
    return base


class TestCountSyllables:
    def test_vowel_groups_count(self):
        assert _count_syllables("hello") == 2
        assert _count_syllables("banana") == 3
        assert _count_syllables("beautiful") == 3  # eau + i + u — groups, not real syllables

    def test_silent_e_is_trimmed(self):
        assert _count_syllables("cake") == 1
        # "ea" collapses into one group, then the final e trims: 1, not the true 2.
        assert _count_syllables("create") == 1
        assert _count_syllables("queue") == 1  # "ueue" is a single group; n==1 so no trim

    def test_single_group_word_keeps_its_syllable(self):
        assert _count_syllables("the") == 1  # ends in e but n == 1 → no trim
        assert _count_syllables("strength") == 1
        assert _count_syllables("rhythm") == 1  # y counts as a vowel

    def test_minimum_is_one_even_without_vowels(self):
        assert _count_syllables("") == 1
        assert _count_syllables("bcd") == 1

    def test_turkish_dotted_capital_i_lowercases_into_a_vowel(self):
        # "İ".lower() is "i" + U+0307 (combining dot); [aeiouy]+ still finds the i.
        assert _count_syllables("İ") == 1


class TestClarityMetrics:
    def test_key_order_is_pinned_for_both_branches(self):
        assert list(_clarity_metrics("One two. Three four.")) == _CLARITY_KEYS
        assert list(_clarity_metrics("")) == _CLARITY_KEYS

    def test_empty_text_returns_exact_zero_dict(self):
        assert _clarity_metrics("") == {
            "word_count": 0,
            "sentence_count": 0,
            "avg_sentence_words": 0.0,
            "long_sentence_pct": 0.0,
            "heading_count": 0,
            "has_lists": False,
            "has_code_blocks": False,
            "clarity": 0.0,
        }

    def test_plain_two_sentence_paragraph_flesch_is_exact(self):
        # 12 words, 2 sentences, 15 counted syllables ("every"→3, "after"→2, rest 1):
        # 206.835 − 1.015·6.0 − 84.6·(15/12) = 94.995 → rounds to 95.0. No structure bonus.
        assert _clarity_metrics(_PLAIN_TWO_SENTENCES) == {
            "word_count": 12,
            "sentence_count": 2,
            "avg_sentence_words": 6.0,
            "long_sentence_pct": 0.0,
            "heading_count": 0,
            "has_lists": False,
            "has_code_blocks": False,
            "clarity": 95.0,
        }

    def test_negative_flesch_clamps_to_zero(self):
        # Real words, real sentences — but the syllable load drives Flesch below 0.
        assert _clarity_metrics(_DENSE) == {
            "word_count": 14,
            "sentence_count": 2,
            "avg_sentence_words": 7.0,
            "long_sentence_pct": 0.0,
            "heading_count": 0,
            "has_lists": False,
            "has_code_blocks": False,
            "clarity": 0.0,
        }

    def test_headings_and_bullets_are_counted_and_add_bonus(self):
        # Flesch 73.3 + 4 (headings) + 3 (lists) = 80.3.
        assert _clarity_metrics(_STRUCTURED) == {
            "word_count": 15,
            "sentence_count": 4,
            "avg_sentence_words": 3.8,
            "long_sentence_pct": 0.0,
            "heading_count": 2,
            "has_lists": True,
            "has_code_blocks": False,
            "clarity": 80.3,
        }

    def test_heading_indent_allows_at_most_three_spaces(self):
        assert _clarity_metrics("   # ok heading\n\nSome words here now.")["heading_count"] == 1
        assert _clarity_metrics("    # too deep\n\nSome words here now.")["heading_count"] == 0

    def test_bullet_glyph_counts_as_a_list(self):
        assert _clarity_metrics("• item one\nSome words here now.")["has_lists"] is True

    def test_long_sentence_boundary_is_strictly_more_than_25_words(self):
        s25 = " ".join(["word"] * 25) + ". Short one."
        s26 = " ".join(["word"] * 26) + ". Short one."
        assert _clarity_metrics(s25)["long_sentence_pct"] == 0.0
        assert _clarity_metrics(s26)["long_sentence_pct"] == 50.0

    def test_code_fences_are_stripped_before_scoring(self):
        plain = _clarity_metrics(_SHORT_PROSE)
        with_code = _clarity_metrics(_CODE_FENCE + _SHORT_PROSE)
        assert with_code == {
            "word_count": 6,
            "sentence_count": 2,
            "avg_sentence_words": 3.0,
            "long_sentence_pct": 0.0,
            "heading_count": 0,
            "has_lists": False,
            "has_code_blocks": True,
            "clarity": 100.0,
        }
        assert plain == {**with_code, "has_code_blocks": False}

    def test_nbsp_after_period_splits_the_sentence(self):
        # Python's unicode \s matches U+00A0, so the NBSP ends the sentence. Go's
        # regexp \s is ASCII-only — the #1 divergence risk for the Go twin.
        assert _clarity_metrics("One two three.\u00a0Four five six.") == {
            "word_count": 6,
            "sentence_count": 2,
            "avg_sentence_words": 3.0,
            "long_sentence_pct": 0.0,
            "heading_count": 0,
            "has_lists": False,
            "has_code_blocks": False,
            "clarity": 100.0,
        }

    def test_period_followed_directly_by_a_letter_does_not_split(self):
        m = _clarity_metrics("One two three.Four five six.")
        assert m["sentence_count"] == 1
        assert m["avg_sentence_words"] == 6.0

    def test_punctuation_only_text_returns_zero_dict(self):
        m = _clarity_metrics("?! ... !!!")
        assert m == {
            "word_count": 0,
            "sentence_count": 0,
            "avg_sentence_words": 0.0,
            "long_sentence_pct": 0.0,
            "heading_count": 0,
            "has_lists": False,
            "has_code_blocks": False,
            "clarity": 0.0,
        }

    def test_arabic_indic_digit_counts_as_a_list_marker(self):
        # Python's unicode \d matches ٣/٤, so "٣." is a numbered-list marker. The
        # bare "٣" before the first ". " also splits off as its own sentence.
        assert _clarity_metrics("٣. First step\n٤. Second step\nSome prose here.") == {
            "word_count": 7,
            "sentence_count": 3,
            "avg_sentence_words": 2.3,
            "long_sentence_pct": 0.0,
            "heading_count": 0,
            "has_lists": True,
            "has_code_blocks": False,
            "clarity": 100.0,
        }

    def test_turkish_dotted_capital_i_splits_the_word(self):
        # İ is outside [A-Za-z'], so "VERİFY" tokenizes as two words (VER, FY): 5 words.
        assert _clarity_metrics("VERİFY the output now.")["word_count"] == 5

    def test_apostrophes_stay_inside_words(self):
        assert _clarity_metrics("Don't stop now.")["word_count"] == 3


class TestUsefulnessMetrics:
    def test_key_order_is_pinned(self):
        assert list(_usefulness_metrics("x")) == ["usefulness", "owned", "actionable", "structured", "has_purpose"]

    def test_fully_useful_page_scores_exactly_100(self):
        assert _usefulness_metrics(_STRUCTURED) == {
            "usefulness": 100.0,
            "owned": True,
            "actionable": True,
            "structured": True,
            "has_purpose": True,
        }

    def test_empty_text_keeps_the_base_score(self):
        assert _usefulness_metrics("") == {
            "usefulness": 20.0,
            "owned": False,
            "actionable": False,
            "structured": False,
            "has_purpose": False,
        }

    def test_each_signal_adds_twenty(self):
        assert _usefulness_metrics("VERIFY the output now.") == {
            "usefulness": 40.0,
            "owned": False,
            "actionable": True,
            "structured": False,
            "has_purpose": False,
        }

    def test_owner_line_shapes(self):
        assert _usefulness_metrics("Owner: Jane")["owned"] is True
        assert _usefulness_metrics("**Owner**: Jane")["owned"] is True
        assert _usefulness_metrics("Owner | Jane")["owned"] is True
        assert _usefulness_metrics("maintainer - Jane")["owned"] is True
        assert _usefulness_metrics("responsible: Ops")["owned"] is True
        assert _usefulness_metrics("CONTACT: someone")["owned"] is True

    def test_leading_pipe_table_row_is_not_owned(self):
        # "| Owner | Jane |" starts with a pipe the pattern never matches — a full
        # markdown table row is missed. Deliberate pin; the Go twin must miss it too.
        assert _usefulness_metrics("| Owner | Jane |")["owned"] is False

    def test_owner_match_crosses_blank_lines(self):
        # \s* after the keyword happily crosses "\n\n" to reach the colon.
        assert _usefulness_metrics("Owner\n\n: Jane")["owned"] is True

    def test_owner_keyword_must_start_the_line(self):
        assert _usefulness_metrics("The owner is Jane: yes")["owned"] is False

    def test_actionable_verbs_and_the_next_step_phrase(self):
        assert _usefulness_metrics("Take the next step now.")["actionable"] is True
        assert _usefulness_metrics("We must decide soon.")["actionable"] is True
        assert _usefulness_metrics("decision")["actionable"] is True

    def test_pluralised_next_steps_escapes_the_word_boundary(self):
        # "next steps" fails \b after "step"; "resolved" likewise never matches "resolve".
        assert _usefulness_metrics("Next steps for the team.")["actionable"] is False
        assert _usefulness_metrics("resolved")["actionable"] is False

    def test_purpose_markers(self):
        assert _usefulness_metrics("TL;DR: yes")["has_purpose"] is True
        assert _usefulness_metrics("The why matters.")["has_purpose"] is True
        assert _usefulness_metrics("goals")["has_purpose"] is False  # "goal" bounded, "goals" escapes

    def test_turkish_dotted_capital_i_defeats_verb_detection(self):
        # str.lower() maps İ → "i" + U+0307; the combining dot sits inside "veri̇fy"
        # so \bverify\b never matches. Go's strings.ToLower maps İ → "i̇" too, but
        # any Go twin using ASCII lowering would diverge — pin the Python result.
        assert _usefulness_metrics("VERİFY the output now.") == {
            "usefulness": 20.0,
            "owned": False,
            "actionable": False,
            "structured": False,
            "has_purpose": False,
        }


class TestHasAiDisclosure:
    def test_generated_with_claude_code_is_disclosed(self):
        assert _has_ai_disclosure("Generated with Claude Code") is True
        assert _has_ai_disclosure("Generated with [Claude Code](https://claude.com/claude-code)") is True

    def test_matching_is_case_insensitive(self):
        assert _has_ai_disclosure("GENERATED WITH CLAUDE CODE") is True
        assert _has_ai_disclosure("co-authored-by: claude") is True

    def test_generated_with_claude_without_code_is_not_a_marker(self):
        # The claude marker regex demands the literal phrase "claude code" — the
        # shorter disclosure fails the marker gate even though the context matches.
        assert _has_ai_disclosure("Generated with Claude") is False

    def test_co_author_trailer_is_disclosed(self):
        assert _has_ai_disclosure("Co-Authored-By: Claude <noreply@anthropic.com>") is True

    def test_nbsp_inside_the_context_phrase_still_matches(self):
        # The context regex uses \s+, and Python's unicode \s matches U+00A0. The
        # marker (noreply@anthropic.com) is satisfied independently. Go's \s would
        # not match the NBSP — pin the Python behaviour.
        text = "Drafted\u00a0with help from tooling. Contact noreply@anthropic.com for details."
        assert _has_ai_disclosure(text) is True
        # Without the marker the same context phrase is not enough.
        assert _has_ai_disclosure("Drafted\u00a0with help from tooling.") is False

    def test_page_about_ai_with_no_authorship_context_is_not_marked(self):
        about = "AI tooling guide. Example trailer address: copilot@github.com. See https://claude.com/claude-code."
        assert _has_ai_disclosure(about) is False

    def test_empty_text_is_not_disclosed(self):
        assert _has_ai_disclosure("") is False


class TestAnalysePageAsset:
    _PAGE = {
        "title": "Deploy Runbook",
        "platform": "confluence",
        "text": _RICH,
        "url": "https://x/p1",
        "key": "p1",
        "container": "OPS",
        "version": "7",
    }

    def test_key_order_is_pinned(self):
        assert list(_analyse_page_asset(self._PAGE)) == [
            "title",
            "platform",
            "clarity",
            "usefulness",
            "owned",
            "actionable",
            "structured",
            "has_code_blocks",
            "marked",
            "url",
            "key",
            "container",
            "version",
        ]

    def test_rich_page_scores_exactly(self):
        assert _analyse_page_asset(self._PAGE) == {
            "title": "Deploy Runbook",
            "platform": "confluence",
            "clarity": 64.9,
            "usefulness": 100.0,
            "owned": True,
            "actionable": True,
            "structured": True,
            "has_code_blocks": True,
            "marked": True,
            "url": "https://x/p1",
            "key": "p1",
            "container": "OPS",
            "version": "7",
        }

    def test_empty_page_produces_exact_default_asset(self):
        assert _analyse_page_asset({}) == {
            "title": "Untitled",
            "platform": "",
            "clarity": 0.0,
            "usefulness": 20.0,
            "owned": False,
            "actionable": False,
            "structured": False,
            "has_code_blocks": False,
            "marked": False,
            "url": "",
            "key": "",
            "container": "",
            "version": "",
        }

    def test_version_falls_back_to_timestamp(self):
        assert _analyse_page_asset({"timestamp": "2026-01-01"})["version"] == "2026-01-01"

    def test_title_is_truncated_to_80_chars(self):
        assert _analyse_page_asset({"title": "x" * 100})["title"] == "x" * 80


class TestAggregateDocAssets:
    def test_assets_built_from_pages_aggregate_exactly(self):
        pages = [
            {"platform": "confluence", "title": "Deploy Runbook", "text": _RICH, "url": "https://x/p1", "key": "p1"},
            {"platform": "confluence", "title": "Dense Wall", "text": _DENSE, "url": "https://x/p2", "key": "p2"},
            {"platform": "notion", "title": "İstanbul Notes", "text": _DENSE, "url": "https://x/p3", "key": "p3"},
        ]
        sig = _aggregate_doc_assets([_analyse_page_asset(p) for p in pages])
        # Dense Wall and İstanbul Notes both clamp to clarity 0.0 and stay flagged
        # once each (usefulness re-flagging dedupes on title). "verify" makes the
        # dense text actionable (usefulness 40.0): avg = (100+40+40)/3 = 60.0,
        # avg_clarity = (64.9+0+0)/3 = 21.633… → 21.6.
        assert sig == DocQualitySignal(
            pages_scanned=3,
            platforms_scanned=("confluence", "notion"),
            avg_clarity=21.6,
            avg_usefulness=60.0,
            clear_pages=1,
            mixed_pages=0,
            unclear_pages=2,
            owned_pages=1,
            actionable_pages=3,
            structured_pages=1,
            ai_marked_pages=1,
            per_platform=(("confluence", 2), ("notion", 1)),
            flagged_pages=(
                ("Dense Wall", "clarity 0/100 — dense or long-winded"),
                ("İstanbul Notes", "clarity 0/100 — dense or long-winded"),
            ),
            is_ai_estimate=False,
        )

    def test_empty_assets_return_the_default_signal(self):
        assert _aggregate_doc_assets([]) == DocQualitySignal()

    def test_averages_round_half_to_even(self):
        down = _aggregate_doc_assets([_asset(clarity=70.0, usefulness=80.0), _asset(clarity=70.5, usefulness=80.5)])
        assert down.avg_clarity == 70.2  # 70.25 → .2 (even), not .3
        assert down.avg_usefulness == 80.2  # 80.25 → .2
        up = _aggregate_doc_assets([_asset(clarity=70.0), _asset(clarity=71.5)])
        assert up.avg_clarity == 70.8  # 70.75 → .8 (even)

    def test_banding_boundaries_are_exact(self):
        def bands(clarity: float) -> tuple[int, int, int]:
            sig = _aggregate_doc_assets([_asset(clarity=clarity, usefulness=100.0)])
            return sig.clear_pages, sig.mixed_pages, sig.unclear_pages

        assert bands(60.0) == (1, 0, 0)  # >= 60 is clear
        assert bands(59.9) == (0, 1, 0)
        assert bands(40.0) == (0, 1, 0)  # 40 is still mixed
        assert bands(39.9) == (0, 0, 1)

    def test_flagged_ordering_clarity_then_usefulness_deduped_by_title(self):
        assets = [
            _asset(title="Clear", clarity=80.0, usefulness=100.0),
            _asset(title="Worst", clarity=10.0, usefulness=100.0),
            _asset(title="Bad", clarity=30.0, usefulness=100.0),
            _asset(title="Useless", clarity=90.0, usefulness=20.0),
            _asset(title="LessUseless", clarity=90.0, usefulness=40.0),
            _asset(title="Worst", clarity=15.0, usefulness=10.0),  # duplicate title → deduped
        ]
        assert _aggregate_doc_assets(assets).flagged_pages == (
            ("Worst", "clarity 10/100 — dense or long-winded"),
            ("Bad", "clarity 30/100 — dense or long-winded"),
            ("Useless", "usefulness 20/100 — missing purpose, ownership, or actions"),
            ("LessUseless", "usefulness 40/100 — missing purpose, ownership, or actions"),
        )

    def test_per_platform_sorts_by_count_desc_then_name(self):
        assets = [
            _asset(platform="notion"),
            _asset(platform="confluence"),
            _asset(platform="notion"),
            _asset(platform="wiki"),
        ]
        sig = _aggregate_doc_assets(assets)
        assert sig.per_platform == (("notion", 2), ("confluence", 1), ("wiki", 1))
        # platforms_scanned keeps first-seen order, unsorted.
        assert sig.platforms_scanned == ("notion", "confluence", "wiki")

    def test_missing_platform_counts_but_is_not_listed(self):
        sig = _aggregate_doc_assets([{"title": "t", "clarity": 80.0, "usefulness": 100.0}])
        assert sig.platforms_scanned == ()
        assert sig.per_platform == (("", 1),)


class TestDocSmallSample:
    def test_four_pages_are_a_small_sample(self):
        assert doc_small_sample(DocQualitySignal(pages_scanned=4)) is True

    def test_five_pages_are_not(self):
        assert doc_small_sample(DocQualitySignal(pages_scanned=5)) is False


class TestDocFindings:
    _ASSETS = [
        {"platform": "confluence", "title": "Dense Wall", "clarity": 30.0, "usefulness": 40.0, "url": "https://x/p2"},
        {"platform": "notion", "title": "Fine", "clarity": 75.0, "usefulness": 80.0, "url": "https://x/p3"},
        {"platform": "notion", "title": "HalfRound", "clarity": 59.5, "usefulness": 58.5, "url": ""},
    ]

    def test_ids_categories_and_ordering_are_exact(self):
        findings = _doc_findings(self._ASSETS)
        # Per asset: clarity finding then usefulness finding, assets in input order.
        assert [f["id"] for f in findings] == [
            "confluence:Dense Wall:clarity",
            "confluence:Dense Wall:usefulness",
            "notion:HalfRound:clarity",
            "notion:HalfRound:usefulness",
        ]
        assert [f["category"] for f in findings] == ["clarity", "usefulness", "clarity", "usefulness"]

    def test_key_order_puts_base_keys_first(self):
        # {**base, "id": …} → the four base keys lead every finding dict.
        assert list(_doc_findings(self._ASSETS)[0]) == [
            "link",
            "affected_scope",
            "owner_role",
            "confidence",
            "id",
            "category",
            "title",
            "detail",
            "priority",
            "impact",
            "evidence",
            "next_steps",
            "effort",
            "completion_check",
        ]

    def test_clarity_finding_is_exact(self):
        assert _doc_findings(self._ASSETS)[0] == {
            "link": "https://x/p2",
            "affected_scope": ["confluence:Dense Wall"],
            "owner_role": "Documentation owner",
            "confidence": "high",
            "id": "confluence:Dense Wall:clarity",
            "category": "clarity",
            "title": "Rewrite dense documentation",
            "detail": (
                "Lead with the outcome, shorten sentences, and split the page with descriptive headings and lists."
            ),
            "priority": "high",
            "impact": "Makes operational knowledge faster to understand and use.",
            "evidence": "Dense Wall scored 30/100 for clarity.",
            "next_steps": [
                "Rewrite the summary and longest sections.",
                "Have a target reader validate the instructions.",
            ],
            "effort": "small",
            "completion_check": "A target reader can identify the purpose and required action without author help.",
        }

    def test_usefulness_finding_is_exact(self):
        assert _doc_findings(self._ASSETS)[1] == {
            "link": "https://x/p2",
            "affected_scope": ["confluence:Dense Wall"],
            "owner_role": "Documentation owner",
            "confidence": "high",
            "id": "confluence:Dense Wall:usefulness",
            "category": "usefulness",
            "title": "Add purpose, ownership, and actions",
            "detail": (
                "State why the page exists, who maintains it, and the concrete procedure or decision it supports."
            ),
            "priority": "high",
            "impact": "Turns descriptive prose into maintainable, actionable team knowledge.",
            "evidence": "Dense Wall scored 40/100 for usefulness.",
            "next_steps": ["Add purpose and owner fields.", "Add verified steps, decisions, or next actions."],
            "effort": "small",
            "completion_check": "The page names an owner and provides a verifiable action or decision.",
        }

    def test_evidence_scores_round_half_to_even(self):
        # :.0f uses round-half-even: 59.5 → "60", 58.5 → "58".
        findings = _doc_findings(self._ASSETS)
        assert findings[2]["evidence"] == "HalfRound scored 60/100 for clarity."
        assert findings[3]["evidence"] == "HalfRound scored 58/100 for usefulness."

    def test_thresholds_are_strictly_below_60(self):
        assert _doc_findings([_asset(clarity=60.0, usefulness=60.0)]) == []
        assert [f["category"] for f in _doc_findings([_asset(clarity=59.9, usefulness=60.0)])] == ["clarity"]
        assert [f["category"] for f in _doc_findings([_asset(clarity=60.0, usefulness=59.9)])] == ["usefulness"]

    def test_empty_assets_yield_no_findings(self):
        assert _doc_findings([]) == []

    def test_missing_scores_default_to_zero_and_flag_everything(self):
        findings = _doc_findings([{}])
        assert [f["id"] for f in findings] == [":Untitled:clarity", ":Untitled:usefulness"]
        assert findings[0]["evidence"] == "Untitled scored 0/100 for clarity."


class TestPrioritizeDocActions:
    def test_empty_findings_yield_no_actions(self):
        assert _prioritize_doc_actions([]) == []

    def test_groups_by_category_and_title_with_pages_suffix(self):
        findings = _doc_findings(
            [
                {"platform": "confluence", "title": "Dense Wall", "clarity": 30.0, "usefulness": 40.0, "url": "u2"},
                {"platform": "notion", "title": "HalfRound", "clarity": 59.5, "usefulness": 58.5, "url": ""},
            ]
        )
        actions = _prioritize_doc_actions(findings)
        assert [(a["id"], a["breadth"]) for a in actions] == [
            ("confluence:Dense Wall:clarity", 2),
            ("confluence:Dense Wall:usefulness", 2),
        ]
        # The exemplar is the FIRST finding of each group; scopes are sorted and the
        # evidence suffix says "pages" (code_health's prioritize_actions says
        # "repositories" — the divergence is deliberate, do not unify).
        assert actions[0]["affected_scope"] == ["confluence:Dense Wall", "notion:HalfRound"]
        assert actions[0]["evidence"] == "Dense Wall scored 30/100 for clarity. Affects 2 pages."
        assert actions[0]["link"] == "u2"

    def test_single_page_action_keeps_evidence_verbatim(self):
        actions = _prioritize_doc_actions(_doc_findings([_asset(title="Solo", clarity=10.0, usefulness=100.0)]))
        assert len(actions) == 1
        assert actions[0]["evidence"] == "Solo scored 10/100 for clarity."
        assert actions[0]["breadth"] == 1

    def test_sorts_by_priority_then_breadth_with_no_title_tiebreak(self):
        # Wider group wins within a priority; equal keys keep grouping insertion
        # order (Python's stable sort) — there is NO title tiebreak here, unlike
        # code_health.prioritize_actions.
        findings = [
            {"category": "clarity", "title": "Zulu", "priority": "high", "evidence": "E.", "affected_scope": ["a"]},
            {"category": "usefulness", "title": "Alpha", "priority": "high", "evidence": "E.", "affected_scope": ["b"]},
            {"category": "usefulness", "title": "Alpha", "priority": "high", "evidence": "E.", "affected_scope": ["c"]},
            {"category": "misc", "title": "Low", "priority": "low", "evidence": "E.", "affected_scope": ["d"]},
            {"category": "misc", "title": "Odd", "priority": "urgent", "evidence": "E.", "affected_scope": ["e"]},
        ]
        actions = _prioritize_doc_actions(findings)
        # "urgent" is unknown → order 9 → sorts after everything known.
        assert [a["title"] for a in actions] == ["Alpha", "Zulu", "Low", "Odd"]

    def test_falsy_scopes_are_kept_not_dropped(self):
        # Unlike code_health, the scope set comprehension has no `if scope` filter:
        # an empty scope survives and inflates breadth. Pin the divergence.
        findings = [
            {"category": "c", "title": "T", "priority": "high", "evidence": "E.", "affected_scope": ["b", "", "a"]},
            {"category": "c", "title": "T", "priority": "high", "evidence": "ignored", "affected_scope": ["b"]},
        ]
        actions = _prioritize_doc_actions(findings)
        assert actions[0]["affected_scope"] == ["", "a", "b"]
        assert actions[0]["breadth"] == 3
        assert actions[0]["evidence"] == "E. Affects 3 pages."


class TestFallbackDocQualityInsights:
    def test_flagged_samples_produce_exact_action_led_insights(self):
        sig = DocQualitySignal(pages_scanned=6, owned_pages=2, actionable_pages=3, avg_usefulness=48.4)
        samples = [{"platform": "notion", "title": "Dense", "clarity": 20.0, "usefulness": 20.0, "url": "https://u/1"}]
        assert _fallback_doc_quality_insights(sig, samples) == {
            "start": [
                {
                    "title": "Rewrite dense documentation",
                    "detail": (
                        "Lead with the outcome, shorten sentences, and split the page "
                        "with descriptive headings and lists."
                    ),
                    "evidence": "Dense scored 20/100 for clarity.",
                    "link": "https://u/1",
                },
                {
                    "title": "Add purpose, ownership, and actions",
                    "detail": (
                        "State why the page exists, who maintains it, and the concrete "
                        "procedure or decision it supports."
                    ),
                    "evidence": "Dense scored 20/100 for usefulness.",
                    "link": "https://u/1",
                },
            ],
            "stop": [
                {
                    "title": "Stop publishing ownerless guidance",
                    "detail": "Every operational page should name a maintainer and a concrete validation step.",
                    "evidence": "4 page(s) lack an owner signal",
                }
            ],
            "keep": [
                {
                    "title": "Keep actionable pages current",
                    "detail": "Preserve the pages that already combine clear structure with executable guidance.",
                    "evidence": "3 actionable page(s) found",
                }
            ],
            "try": [
                {
                    "title": "Use a shared documentation template",
                    "detail": "Start pages with purpose, owner, last-reviewed date, procedure, and verification.",
                    "evidence": "Average usefulness 48/100",
                }
            ],
        }

    def test_no_actions_falls_back_to_exact_baseline_insights(self):
        # 71.5 → "72" and 62.5 → "62": :.0f rounds half to even in both directions.
        sig = DocQualitySignal(pages_scanned=2, avg_clarity=71.5, avg_usefulness=62.5)
        assert _fallback_doc_quality_insights(sig) == {
            "start": [
                {
                    "title": "Set a documentation quality baseline",
                    "detail": "Use purpose, owner, procedure, and verification fields for every shared page.",
                    "evidence": "2 page(s) scanned",
                }
            ],
            "stop": [
                {
                    "title": "Stop relying on implicit ownership",
                    "detail": "Name a maintainer so readers know who can verify and update the page.",
                    "evidence": "Ownership is assessed explicitly in the new documentation score.",
                }
            ],
            "keep": [
                {
                    "title": "Keep clear documentation patterns",
                    "detail": "Continue using concise sections and concrete procedures.",
                    "evidence": "Average clarity 72/100",
                }
            ],
            "try": [
                {
                    "title": "Review documentation with a target reader",
                    "detail": "Ask someone other than the author to execute or explain the documented process.",
                    "evidence": "Average usefulness 62/100",
                }
            ],
        }

    def test_healthy_samples_use_the_baseline_branch(self):
        sig = DocQualitySignal(pages_scanned=1)
        out = _fallback_doc_quality_insights(sig, [_asset(title="Good", clarity=80.0, usefulness=80.0)])
        assert out["start"][0]["title"] == "Set a documentation quality baseline"

    def test_grouping_makes_the_insight_cap_unreachable(self):
        # Five flagged pages collapse into ONE clarity action (same category+title),
        # so start never approaches _INSIGHT_MAX_ITEMS: at most 2 items are possible.
        samples = [_asset(title=f"P{i}", clarity=10.0 + i, usefulness=100.0) for i in range(5)]
        out = _fallback_doc_quality_insights(DocQualitySignal(pages_scanned=5), samples)
        assert len(out["start"]) == 1
        assert out["start"][0]["evidence"] == "P0 scored 10/100 for clarity. Affects 5 pages."

    def test_empty_url_omits_the_link_key(self):
        samples = [_asset(title="Dense", clarity=20.0, usefulness=100.0, url="")]
        item = _fallback_doc_quality_insights(DocQualitySignal(pages_scanned=1), samples)["start"][0]
        assert list(item) == ["title", "detail", "evidence"]

    def test_ownerless_count_clamps_at_zero(self):
        sig = DocQualitySignal(pages_scanned=1, owned_pages=5)
        samples = [_asset(title="Dense", clarity=20.0, usefulness=100.0)]
        out = _fallback_doc_quality_insights(sig, samples)
        assert out["stop"][0]["evidence"] == "0 page(s) lack an owner signal"
