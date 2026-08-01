"""Tests for the artifact path grammar."""

from __future__ import annotations

import pytest

from yeaboi.artifacts.paths import (
    MAX_PATH_LENGTH,
    PathError,
    Segment,
    escape_value,
    parse_path,
    render_path,
    resolve,
)

MEMBER_KEYS = {"member_updates": "name", "projects": "name"}


class TestParsePath:
    def test_plain_field(self):
        assert parse_path("team_summary") == (Segment(field="team_summary"),)

    def test_nested_fields(self):
        assert parse_path("a.b.c") == (Segment(field="a"), Segment(field="b"), Segment(field="c"))

    def test_identity_selector(self):
        (seg,) = parse_path("member_updates[name=Ada]")
        assert (seg.field, seg.key, seg.value) == ("member_updates", "name", "Ada")

    def test_identity_selector_is_percent_decoded(self):
        (seg,) = parse_path("member_updates[name=Ada%20Lovelace]")
        assert seg.value == "Ada Lovelace"

    def test_positional_selector(self):
        (seg,) = parse_path("highlights[#2]")
        assert seg.index == 2 and not seg.key

    def test_append_selector(self):
        (seg,) = parse_path("highlights[-]")
        assert seg.append and seg.index == -1

    def test_selector_then_field(self):
        segs = parse_path("member_updates[name=Ada].blockers")
        assert len(segs) == 2
        assert segs[1] == Segment(field="blockers")

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "Upper",
            "9leading",
            "__dunder__.x",
            "field[",
            "field[name=x",
            "field[]",
            "field[#]",
            "field[#-1]",
            "field[#1_0]",
            "field[#+1]",
            "field[nokey]",
            "field[Bad=x]",
        ],
    )
    def test_malformed_paths_raise(self, bad):
        with pytest.raises(PathError):
            parse_path(bad)

    def test_overlong_path_is_refused(self):
        with pytest.raises(PathError):
            parse_path("a" * (MAX_PATH_LENGTH + 1))

    def test_too_deep_path_is_refused(self):
        with pytest.raises(PathError):
            parse_path(".".join(["a"] * 20))

    def test_path_error_is_a_value_error(self):
        # Handlers catch ValueError to turn a bad request into a 400; PathError
        # must not need its own except clause to be handled correctly.
        assert issubclass(PathError, ValueError)


class TestRenderPath:
    @pytest.mark.parametrize(
        "text",
        [
            "team_summary",
            "member_updates[name=Ada].blockers",
            "highlights[#2]",
            "highlights[-]",
            "projects[name=Payments%20API].description",
        ],
    )
    def test_round_trips(self, text):
        assert render_path(parse_path(text)) == text

    def test_awkward_value_round_trips(self):
        # The three characters that are grammar: a dot would split the path, a
        # bracket would end the selector, an equals would restart it.
        raw = "Release 1.0 [beta] = soon"
        text = f"projects[name={escape_value(raw)}].description"
        segs = parse_path(text)
        assert segs[0].value == raw
        assert render_path(segs) == text


class TestResolve:
    @pytest.fixture
    def tree(self):
        return {
            "team_summary": "All good",
            "member_updates": [
                {"name": "Ada", "blockers": "staging db"},
                {"name": "Grace", "blockers": ""},
            ],
            "highlights": ["shipped auth", "cut latency"],
        }

    def test_plain_field(self, tree):
        target = resolve(tree, parse_path("team_summary"), MEMBER_KEYS)
        assert target is not None and target.get() == "All good"

    def test_identity_then_field(self, tree):
        target = resolve(tree, parse_path("member_updates[name=Ada].blockers"), MEMBER_KEYS)
        assert target is not None and target.get() == "staging db"

    def test_identity_picks_the_right_row(self, tree):
        target = resolve(tree, parse_path("member_updates[name=Grace].blockers"), MEMBER_KEYS)
        assert target is not None and target.get() == ""

    def test_positional_into_a_string_list(self, tree):
        target = resolve(tree, parse_path("highlights[#1]"), MEMBER_KEYS)
        assert target is not None and target.get() == "cut latency"

    def test_append_slot_points_past_the_end(self, tree):
        target = resolve(tree, parse_path("highlights[-]"), MEMBER_KEYS)
        assert target is not None and target.append and target.key == 2
        assert not target.exists()
        with pytest.raises(PathError):
            target.get()

    def test_writing_through_the_target_mutates_the_tree(self, tree):
        target = resolve(tree, parse_path("member_updates[name=Ada].blockers"), MEMBER_KEYS)
        assert target is not None
        target.container[target.key] = "unblocked"
        assert tree["member_updates"][0]["blockers"] == "unblocked"

    def test_missing_member_resolves_to_none(self, tree):
        assert resolve(tree, parse_path("member_updates[name=Nobody].blockers"), MEMBER_KEYS) is None

    def test_missing_field_resolves_to_none(self, tree):
        assert resolve(tree, parse_path("nonexistent"), MEMBER_KEYS) is None

    def test_out_of_range_index_resolves_to_none(self, tree):
        assert resolve(tree, parse_path("highlights[#99]"), MEMBER_KEYS) is None

    def test_selector_on_a_non_list_resolves_to_none(self, tree):
        assert resolve(tree, parse_path("team_summary[#0]"), MEMBER_KEYS) is None

    def test_identity_on_a_list_with_no_declared_key_raises(self, tree):
        # Refusing beats scanning: a list with no declared key has no field we
        # are willing to promise is unique, and guessing lands the edit on the
        # wrong row.
        with pytest.raises(PathError):
            resolve(tree, parse_path("highlights[name=x]"), MEMBER_KEYS)

    def test_descending_into_an_append_slot_resolves_to_none(self, tree):
        assert resolve(tree, parse_path("member_updates[-].blockers"), MEMBER_KEYS) is None
