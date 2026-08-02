"""Unit tests for change-to-ticket relatedness.

One class per predicate, and for each: it matches the real thing, it stays quiet
on the near-miss, and it stays quiet on the specific false positive the gate
exists for. The polarity is inverted from most tests in this repo — a match
SUPPRESSES a report — so a missed match costs a nudge and a wrong match costs a
person being told their work is unapproved scope.
"""

from yeaboi.standup import relatedness


def _ticket(key: str, title: str = "A ticket", body: str = "", **over) -> dict:
    item = {
        "kind": "issue",
        "key": key,
        "title": title,
        "status": "In Progress",
        "source": "jira",
        "url": f"https://j/browse/{key}",
        "timestamp": "2026-08-01T09:00:00",
        "body": body,
    }
    item.update(over)
    return item


def _commit(title: str = "Do a thing", **over) -> dict:
    item = {
        "kind": "commit",
        "key": "a1b2c3d4",
        "title": title,
        "body": "",
        "branch": "",
        "repository": "acme/web",
        "url": "https://g/acme/web/commit/a1b2c3d4",
        "changed_paths": (),
        "pr_id": "",
    }
    item.update(over)
    return item


def _matches(change: dict, tickets: list[dict], *, own: bool = True, docs_only: bool = False) -> bool:
    corpus = relatedness.build_corpus(tickets)
    own_keys = relatedness.ticket_keys(tickets) if own else frozenset()
    profile = relatedness.build_change_profile(change, docs_only=docs_only)
    return relatedness.relates_to_ticket(profile, corpus, own_keys=own_keys)


# The case that prompted the feature.
_REAL_TICKET = _ticket(
    "PSOT-77",
    "Rename the approval plugins",
    "We agreed the pipeline approval plugin and the access request plugin should use their new "
    "names everywhere.\n\nDefinition of done:\n- [ ] Documentation\n- [ ] Proper Testing",
)
_REAL_COMMIT = _commit("Rename the plugins to pipeline-approval and access-request", key="bf132e43")


class TestTheReportedCase:
    def test_a_commit_matches_the_ticket_that_describes_it(self):
        assert _matches(_REAL_COMMIT, [_REAL_TICKET])

    def test_an_unrelated_ticket_does_not_rescue_it(self):
        unrelated = _ticket("PSOT-90", "Migrate the billing schema", "Update the billing tables and backfill.")
        assert not _matches(_REAL_COMMIT, [unrelated])

    def test_one_generic_word_in_common_is_not_a_match(self):
        vague = _ticket("PSOT-91", "Quarterly cleanup", "Some of the plugins may need a look at some point.")
        assert not _matches(_REAL_COMMIT, [vague])


class TestBackReference:
    def test_the_ticket_pasting_the_url_matches(self):
        ticket = _ticket("A-1", "Something", "Shipped as https://g/acme/web/pull/91 today.")
        change = _commit(kind="pr", key="#91", url="https://g/acme/web/pull/91")
        assert _matches(change, [ticket], own=False)  # strong enough for a teammate's ticket

    def test_a_trailing_slash_or_query_still_matches(self):
        ticket = _ticket("A-1", "Something", "See https://g/acme/web/pull/91/?utm=slack")
        change = _commit(kind="pr", key="#91", url="https://g/acme/web/pull/91")
        assert _matches(change, [ticket], own=False)

    def test_a_full_sha_in_the_ticket_matches(self):
        ticket = _ticket("A-1", "Something", "Fixed in a1b2c3d4 yesterday.")
        assert _matches(_commit(key="a1b2c3d4"), [ticket], own=False)

    def test_a_short_hex_string_is_not_a_sha(self):
        ticket = _ticket("A-1", "Something", "Colour is a1b2 in the palette.")
        assert not _matches(_commit(key="a1b2"), [ticket], own=False)

    def test_a_bare_number_needs_the_repository_named_too(self):
        # "#91" is a PR number on GitHub and a work-item id on Azure Boards —
        # the ambiguity references.py exists for.
        without = _ticket("A-1", "Something", "Blocked by #91 for now.")
        with_repo = _ticket("A-1", "Something", "Blocked by #91 in web for now.")
        change = _commit(kind="pr", key="#91", pr_id="91", url="", repository="acme/web")
        assert not _matches(change, [without], own=False)
        assert _matches(change, [with_repo], own=False)


class TestIdentifiers:
    def test_a_rare_compound_matches_the_ticket_prose_that_spells_it_out(self):
        # The asymmetry that makes this work: the change wrote it as one token,
        # the ticket wrote it as two words.
        ticket = _ticket("A-1", "Plugin rename", "The pipeline approval plugin needs its new name.")
        assert _matches(_commit("Rename pipeline-approval"), [ticket], own=False)

    def test_separator_and_case_variants_are_the_same_identifier(self):
        ticket = _ticket("A-1", "Plugin rename", "The pipeline approval plugin needs its new name.")
        for spelling in ("pipeline_approval", "PipelineApproval", "pipeline.approval", "pipeline/approval"):
            assert _matches(_commit(f"Rename {spelling}"), [ticket], own=False), spelling

    def test_short_compounds_are_not_identifiers(self):
        ticket = _ticket("A-1", "Locale work", "Set en US as the default and bump to v 2.")
        assert not _matches(_commit("Set en-US and v-2"), [ticket], own=False)

    def test_a_common_identifier_across_the_corpus_is_not_rare(self):
        tickets = [_ticket(f"A-{i}", f"Piece {i}", "Touches the pipeline approval plugin.") for i in range(8)]
        assert not _matches(_commit("Rename pipeline-approval"), tickets, own=False)


class TestBranchSlug:
    def test_a_workflow_namespace_is_stripped(self):
        ticket = _ticket("A-1", "Webhook retry backoff", "Add retry and backoff to the webhook sender.")
        assert _matches(_commit("Assorted", branch="feature/retry-backoff-webhook"), [ticket])

    def test_an_author_segment_is_stripped_too(self):
        ticket = _ticket("A-1", "Webhook retry backoff", "Add retry and backoff to the webhook sender.")
        assert _matches(_commit("Assorted", branch="users/alice/retry-backoff-webhook"), [ticket])

    def test_a_placeholder_branch_names_nothing(self):
        ticket = _ticket("A-1", "Webhook retry backoff", "Add retry and backoff to the webhook sender.")
        for branch in ("patch-1", "dev", "alice/wip"):
            assert not _matches(_commit("Assorted", branch=branch), [ticket]), branch


class TestSubjectWords:
    def test_a_matching_title_is_enough(self):
        ticket = _ticket("A-1", "Add retry and backoff to the webhook sender")
        assert _matches(_commit("Add retry and backoff to the webhook sender"), [ticket])

    def test_words_alone_never_reach_a_teammates_ticket(self):
        # Tier B admits only the strong predicates: a lead pushing on someone
        # else's ticket is covered by identifiers, not by vocabulary.
        ticket = _ticket("A-1", "Add retry and backoff to the webhook sender")
        assert not _matches(_commit("Add retry and backoff to the webhook sender"), [ticket], own=False)

    def test_words_alone_cannot_match_a_huge_ticket(self):
        filler = " ".join(f"topic{i}" for i in range(320))
        ticket = _ticket("A-1", "Platform", f"{filler} retry backoff webhook sender")
        assert not _matches(_commit("Retry backoff webhook sender rewrite"), [ticket])

    def test_definition_of_done_boilerplate_matches_nothing(self):
        # The failure mode this module was designed against: a DoD block is
        # copied onto every ticket, so it must self-cancel through rarity.
        tickets = [
            _ticket(
                f"A-{i}",
                f"Piece {i}",
                "Definition of done:\n- [ ] Documentation\n- [ ] Proper Testing\n- [ ] Code Merged to Main",
            )
            for i in range(8)
        ]
        assert not _matches(_commit("Update documentation and testing"), tickets)


class TestChangedPaths:
    def test_two_rare_path_tokens_match(self):
        ticket = _ticket("A-1", "Standup habits", "Rework how standup habits are detected.")
        change = _commit("Assorted", changed_paths=("src/yeaboi/standup/habits.py",))
        assert _matches(change, [ticket])

    def test_one_shared_path_token_is_not_evidence(self):
        ticket = _ticket("A-1", "Session expiry", "Sessions should expire after an hour.")
        change = _commit("Assorted", changed_paths=("src/auth/session.py",))
        assert not _matches(change, [ticket])

    def test_unknown_paths_never_match_and_never_crash(self):
        # Empty means UNKNOWN — the collectors cap detail lookups — so it must
        # contribute nothing in either direction.
        ticket = _ticket("A-1", "Standup habits", "Rework how standup habits are detected.")
        assert not _matches(_commit("Assorted", changed_paths=()), [ticket])

    def test_generic_basenames_never_match_alone(self):
        ticket = _ticket("A-1", "Index rewrite", "Rewrite the index and the utils module.")
        change = _commit("Assorted", changed_paths=("src/index.ts", "src/utils.ts"))
        assert not _matches(change, [ticket])


class TestDocumentationCarveOut:
    _DOD = _ticket(
        "A-1",
        "Checkout resilience",
        "Add retry and backoff on the checkout call.\nDefinition of done:\n- [ ] Documentation",
    )
    _NO_DOD = _ticket("A-1", "Checkout resilience", "Add retry and backoff on the checkout call.")
    _DOCS = _commit("Document retry and backoff behaviour", changed_paths=("docs/guide.md",))

    def test_docs_match_a_ticket_whose_definition_of_done_covers_them(self):
        assert _matches(self._DOCS, [self._DOD], docs_only=True)

    def test_the_same_docs_do_not_match_a_ticket_that_never_asked_for_them(self):
        # The relaxation is gated on positive evidence: it is not "docs get a
        # pass", it is "docs get a pass against a ticket that said docs".
        assert not _matches(self._DOCS, [self._NO_DOD], docs_only=True)

    def test_a_code_change_gets_no_discount_from_the_same_ticket(self):
        assert not _matches(self._DOCS, [self._DOD], docs_only=False)

    def test_prose_mentioning_documentation_is_not_a_definition_of_done(self):
        prose = _ticket("A-1", "Checkout resilience", "Add retry and backoff; this is documented elsewhere.")
        assert not _matches(self._DOCS, [prose], docs_only=True)


class TestCorpus:
    def test_text_is_merged_across_items_sharing_a_key(self):
        # `kind` does not predict which item carries the body: changelog and
        # comment items name the same ticket and carry no description at all.
        corpus = relatedness.build_corpus(
            [
                {"kind": "update", "key": "A-1", "summary": "Rename the plugins", "title": "moved A-1", "body": ""},
                _ticket("A-1", "Rename the plugins", "The pipeline approval plugin needs a new name."),
            ]
        )
        assert "pipeline-approval" in corpus.tickets["A-1"].idents

    def test_a_ticket_with_no_text_is_dropped(self):
        assert not relatedness.build_corpus([_ticket("A-1", "", "")]).tickets

    def test_an_empty_corpus_matches_nothing(self):
        corpus = relatedness.build_corpus([])
        assert not corpus
        assert not relatedness.relates_to_ticket(relatedness.build_change_profile(_REAL_COMMIT), corpus)

    def test_candidate_keys_come_from_every_tracker_kind(self):
        # Jira's WIP query skips issues the main search already returned, so an
        # actively working member has ZERO kind=="wip" items. Selecting on that
        # kind would empty the pool for exactly the busiest people.
        items = [_ticket("A-1", "One"), {"kind": "wip", "key": "A-2"}, {"kind": "comment", "key": "A-3"}]
        assert relatedness.ticket_keys(items) == frozenset({"A-1", "A-2", "A-3"})

    def test_reference_tickets_join_the_same_corpus(self):
        # An open ticket nobody touched today still has to be able to claim a
        # commit, which is the largest source of false untracked-work reports.
        corpus = relatedness.build_corpus([], [_ticket("A-1", "Rename plugins", "The pipeline approval plugin.")])
        assert "A-1" in corpus.tickets


class TestDeterminism:
    def test_shuffled_input_gives_the_same_answer(self):
        tickets = [_ticket(f"A-{i}", f"Piece {i}", f"Work on the widget number {i} handler.") for i in range(6)]
        forwards = _matches(_commit("Rewrite the widget number 3 handler"), tickets)
        backwards = _matches(_commit("Rewrite the widget number 3 handler"), list(reversed(tickets)))
        assert forwards == backwards
