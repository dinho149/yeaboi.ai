"""Round-trip tests for the `prior_art` state field and its two exporters.

`prior_art` is a tuple of frozen dataclasses, which neither persistence layer
handles for free — `_SCALAR_KEYS` does not cover it, and the JSON project store
has to turn tuples into lists and back. Both directions are pinned here, plus
the surfaces that read the field, because a dropped reference does not fail: it
just quietly makes the plan look like nobody had built anything before.
"""

import json

from yeaboi.agent.state import PriorArtRef, prior_art_from_dicts, prior_art_to_dicts

REF = PriorArtRef(
    key="github:acme/auth",
    name="acme/auth",
    url="https://github.com/acme/auth",
    platform="github",
    pitch=("does OIDC login", "has session refresh"),
    stack=("Python", "FastAPI"),
)


class TestDictHelpers:
    def test_round_trip_preserves_every_field(self):
        (back,) = prior_art_from_dicts(prior_art_to_dicts((REF,)))
        assert back == REF

    def test_tuples_survive_a_json_hop(self):
        """The project store writes JSON, so the tuples arrive back as lists —
        the reader has to re-tuple them or the frozen dataclass compares unequal
        to the one that was saved."""
        rows = json.loads(json.dumps(prior_art_to_dicts((REF,))))
        (back,) = prior_art_from_dicts(rows)
        assert back == REF
        assert isinstance(back.pitch, tuple)
        assert isinstance(back.stack, tuple)

    def test_empty_round_trips_to_empty(self):
        assert prior_art_to_dicts(()) == []
        assert prior_art_from_dicts([]) == ()
        assert prior_art_to_dicts(None) == []
        assert prior_art_from_dicts(None) == ()

    def test_non_refs_are_dropped_not_raised(self):
        """A resumed session from an older schema can hand back anything."""
        assert prior_art_to_dicts(("not a ref", None, REF)) == prior_art_to_dicts((REF,))

    def test_missing_keys_default_rather_than_crash(self):
        (back,) = prior_art_from_dicts([{"key": "github:acme/auth"}])
        assert back.key == "github:acme/auth"
        assert back.pitch == ()
        assert back.stack == ()


class TestSessionStoreRoundTrip:
    def test_prior_art_survives_save_and_load(self, tmp_path):
        from yeaboi.sessions import SessionStore

        with SessionStore(tmp_path / "s.db") as store:
            store.create_session("sess-1", "prior-art round trip")
            store.save_state("sess-1", {"prior_art": (REF,), "messages": []})
            loaded = store.load_state("sess-1")
        assert loaded["prior_art"] == (REF,)

    def test_absent_prior_art_loads_as_empty(self, tmp_path):
        from yeaboi.sessions import SessionStore

        with SessionStore(tmp_path / "s.db") as store:
            store.create_session("sess-2", "no prior art")
            store.save_state("sess-2", {"messages": []})
            loaded = store.load_state("sess-2")
        assert not loaded.get("prior_art")


class TestExports:
    def test_markdown_carries_prior_art(self):
        """Markdown is not just a file — `plan_publish` builds the Notion and
        Confluence pages from this same string."""
        from yeaboi.repl._io import build_plan_markdown

        md = build_plan_markdown({"prior_art": (REF,)})
        assert "## Prior Art" in md
        assert "[acme/auth](https://github.com/acme/auth)" in md
        assert "does OIDC login" in md
        assert "Python, FastAPI" in md

    def test_markdown_omits_the_section_when_there_is_none(self):
        from yeaboi.repl._io import build_plan_markdown

        assert "Prior Art" not in build_plan_markdown({"prior_art": ()})

    def test_json_export_carries_prior_art(self):
        from yeaboi.json_exporter import export_plan_json

        payload = json.loads(export_plan_json({"prior_art": (REF,)}))
        assert payload["prior_art"] == [
            {
                "key": "github:acme/auth",
                "name": "acme/auth",
                "url": "https://github.com/acme/auth",
                "platform": "github",
                "pitch": ["does OIDC login", "has session refresh"],
                "stack": ["Python", "FastAPI"],
            }
        ]

    def test_json_export_omits_the_key_when_there_is_none(self):
        from yeaboi.json_exporter import export_plan_json

        assert "prior_art" not in json.loads(export_plan_json({"prior_art": ()}))


class TestHeadlessRefs:
    def test_keys_become_refs_with_a_readable_name(self):
        from yeaboi.agent.state import prior_art_refs

        (ref,) = prior_art_refs(["github:acme/auth"])
        assert ref.key == "github:acme/auth"
        assert ref.name == "acme/auth"
        assert ref.platform == "github"

    def test_blanks_and_none_are_dropped(self):
        from yeaboi.agent.state import prior_art_refs

        assert prior_art_refs(None) == ()
        assert prior_art_refs(["", "   "]) == ()

    def test_a_bare_slug_still_yields_a_ref(self):
        """A caller who types `acme/auth` without the provider still means a
        repository; refusing it would be a worse answer than taking it."""
        from yeaboi.agent.state import prior_art_refs

        (ref,) = prior_art_refs(["acme/auth"])
        assert ref.name == "acme/auth"
        assert ref.platform == ""

    def test_keys_are_lowercased_so_they_match_the_ledger(self):
        from yeaboi.agent.state import prior_art_refs

        (ref,) = prior_art_refs(["GitHub:Acme/Auth"])
        assert ref.key == "github:acme/auth"


class TestPitchPrompt:
    def test_prompt_names_the_candidates_and_withholds_the_score(self):
        from yeaboi.prompts.prior_art import get_prior_art_pitch_prompt

        candidate = {
            "key": "github:acme/auth",
            "name": "acme/auth",
            "platform": "github",
            "url": "https://github.com/acme/auth",
            "description": "OIDC login service",
            "languages": ["Python"],
            "score": 42.5,
        }
        prompt = get_prior_art_pitch_prompt(candidates=[candidate], description="a new billing portal")
        assert "acme/auth" in prompt
        assert "OIDC login service" in prompt
        # The score is our ranking arithmetic, not evidence — showing it invites
        # the model to rationalise the order instead of judging the repository.
        assert "42.5" not in prompt
