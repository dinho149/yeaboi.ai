"""Tests for the editable-artifact registry.

The security tests here are the point of the file. An allowlist is only worth
having if something fails when it grows the wrong entry, and "someone will
notice in review" is not that something.
"""

from __future__ import annotations

import dataclasses as dc

import pytest

from yeaboi.agent import state
from yeaboi.artifacts import registry


class TestRows:
    def test_every_row_is_keyed_by_its_own_kind(self):
        assert all(kind == spec.kind for kind, spec in registry.ARTIFACTS.items())

    def test_every_field_kind_is_known(self):
        for spec in registry.ARTIFACTS.values():
            for f in spec.fields:
                assert f.kind in registry.FIELD_KINDS, f"{spec.kind}.{f.chain} has kind {f.kind!r}"

    def test_every_field_has_a_label(self):
        for spec in registry.ARTIFACTS.values():
            for f in spec.fields:
                assert f.label.strip(), f"{spec.kind}.{f.chain} has no label"

    def test_every_field_has_a_positive_limit(self):
        for spec in registry.ARTIFACTS.values():
            for f in spec.fields:
                assert f.limit() > 0

    def test_a_row_with_no_editable_fields_explains_itself(self):
        # An empty allowlist is a decision, not an oversight, and has to read as
        # one to the next person who wonders why they cannot edit a team profile.
        for spec in registry.ARTIFACTS.values():
            if not spec.fields:
                assert len(spec.note) > 40, f"{spec.kind} has no fields and no explanation"

    def test_no_list_is_keyed_by_a_field_that_is_itself_editable(self):
        """A key you can edit is not a key.

        Retro's carried action items were keyed by ``text`` — which is also the
        one thing a reader corrects about them. The first edit would have moved
        the key out from under every path that named it, so the second edit to
        the same item would resolve to nothing and be recorded as stale.
        """
        for spec in registry.ARTIFACTS.values():
            for list_field, key in spec.list_keys.items():
                assert spec.field_for((list_field, key)) is None, (
                    f"{spec.kind}.{list_field} is keyed by {key!r}, which is editable"
                )

    def test_declared_list_keys_are_used_by_some_field(self):
        # A stale list_key is a quiet liability: it keeps an identity selector
        # resolving against a list nothing addresses any more.
        for spec in registry.ARTIFACTS.values():
            for list_field in spec.list_keys:
                assert any(f.chain[0] == list_field for f in spec.fields), (
                    f"{spec.kind}.list_keys[{list_field!r}] addresses nothing"
                )


class TestFieldsExistOnTheDataclasses:
    """The allowlist names real attributes — otherwise an edit resolves to None forever."""

    def _resolve_chain(self, cls, chain):
        for name in chain:
            fields = {f.name: f for f in dc.fields(cls)}
            assert name in fields, f"{cls.__name__} has no field {name!r}"
            annotation = str(fields[name].type)
            if name == chain[-1]:
                return annotation
            # Descend into the element type of a tuple[X, ...] field.
            inner = annotation.partition("[")[2].partition(",")[0].strip()
            cls = getattr(state, inner, None)
            assert cls is not None, f"cannot descend into {annotation!r}"
        return ""

    @pytest.mark.parametrize("kind", sorted(registry.ARTIFACTS))
    def test_chains_resolve(self, kind):
        spec = registry.ARTIFACTS[kind]
        cls = getattr(state, spec.dataclass_name, None)
        if cls is None:  # TeamProfile lives outside agent.state
            pytest.skip(f"{spec.dataclass_name} is not an agent.state artifact")
        for f in spec.fields:
            annotation = self._resolve_chain(cls, f.chain)
            if f.kind == registry.FIELD_ITEMS:
                assert "tuple" in annotation, f"{kind}.{f.chain} is items but typed {annotation}"
            else:
                assert annotation == "str", f"{kind}.{f.chain} is {f.kind} but typed {annotation}"


class TestNothingUrlBearingIsEditable:
    """The sharpest attack this feature opens, and the check that closes it.

    A reader who can rewrite a link can turn the team's own standup into a
    phishing page wearing its chrome — the reader trusts the document because of
    where it came from, and the link is the one part where that trust converts
    into a click. So no field that carries a URL is editable, and it stays that
    way because this fails when someone adds one.
    """

    FORBIDDEN_SUFFIXES = ("_links", "_evidence", "url", "_url", "images")
    FORBIDDEN_EXACT = {"links", "images", "session_id", "generated_at", "id"}

    def test_no_field_chain_touches_a_url_bearing_name(self):
        for spec in registry.ARTIFACTS.values():
            for f in spec.fields:
                for name in f.chain:
                    assert not name.endswith(self.FORBIDDEN_SUFFIXES), f"{spec.kind}.{f.chain} names {name!r}"
                    assert name not in self.FORBIDDEN_EXACT, f"{spec.kind}.{f.chain} names {name!r}"

    def test_the_check_would_actually_fire(self):
        # A guard nobody has seen fail is a guard nobody knows works.
        rogue = registry.FieldSpec(chain=("member_updates", "code_links"), kind=registry.FIELD_LINE, label="x")
        assert rogue.chain[-1].endswith(self.FORBIDDEN_SUFFIXES)

    def test_computed_numbers_are_not_editable(self):
        # Not a security property — an honesty one. A hand-edited confidence
        # percentage makes every trend chart drawn from history a lie.
        standup = registry.ARTIFACTS["standup"]
        for name in ("confidence_pct", "sprint_day", "activity_counts"):
            assert standup.field_for((name,)) is None

    def test_a_private_transcript_is_not_editable_or_shared(self):
        record = registry.ARTIFACTS["performance_completion"]
        assert record.field_for(("transcript",)) is None


class TestLookup:
    def test_editable_field_finds_a_declared_field(self):
        spec = registry.editable_field("standup", ("member_updates", "blockers"))
        assert spec is not None and spec.kind == registry.FIELD_TEXT

    def test_editable_field_rejects_an_undeclared_field(self):
        assert registry.editable_field("standup", ("my_name",)) is None

    def test_editable_field_rejects_an_unknown_artifact(self):
        assert registry.editable_field("nope", ("team_summary",)) is None

    def test_spec_for_artifact_matches_by_dataclass(self):
        spec = registry.spec_for_artifact(state.StandupReport())
        assert spec is not None and spec.kind == "standup"

    def test_spec_for_artifact_returns_none_for_a_stranger(self):
        assert registry.spec_for_artifact(object()) is None


class TestReconstructors:
    """The mapping anonymize used to keep its own, drifted copy of."""

    @pytest.mark.parametrize("name", sorted(registry._RECONSTRUCTORS))
    def test_every_registered_reconstructor_imports_and_round_trips(self, name):
        cls = getattr(state, name, None)
        rebuild = registry.reconstructor_for(cls) if cls else None
        if cls is None:
            # TeamProfile / PokerReport live outside agent.state; just prove the
            # import resolves.
            assert registry._reconstructor(name) is not None
            return
        assert rebuild is not None
        assert rebuild(dc.asdict(cls())) == cls()

    def test_unknown_type_returns_none_rather_than_raising(self):
        assert registry.reconstructor_for(object) is None

    @pytest.mark.parametrize("name", ["OneOnOnePrep", "OneOnOneRecord", "SixMonthReview"])
    def test_performance_artifacts_are_registered(self, name):
        # The regression this consolidation exists to fix: anonymize's private
        # copy had no performance entries, so masking a 1:1 returned it
        # unmasked — silently, on the one path whose whole purpose is to make
        # something safe to publish.
        from yeaboi.anonymize.apply import _reconstructor_for

        assert _reconstructor_for(getattr(state, name)) is not None

    def test_anonymize_delegates_to_this_registry(self):
        from yeaboi.anonymize.apply import _reconstructor_for

        assert _reconstructor_for(state.StandupReport) is registry.reconstructor_for(state.StandupReport)
