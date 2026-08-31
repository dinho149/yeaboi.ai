"""Descriptor invariants — what a connector must declare to be shippable.

These are the guards that replace the hand-maintained registries: a connector
that forgets to mark a secret, or to wear an icon, fails here rather than
shipping unmasked or unstyled.
"""

from __future__ import annotations

import re

import pytest

from yeaboi.connectors import registry
from yeaboi.connectors.spec import ACCENT_RE, FAMILIES, FAMILY_LABELS, FAMILY_ORDER

ALL = registry.all_connectors()
LEGACY = registry.legacy_entries()


class TestIdentity:
    def test_keys_are_unique(self):
        keys = [c.key for c in ALL]
        assert len(keys) == len(set(keys))

    def test_keys_are_slugs(self):
        for c in ALL:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", c.key), f"{c.key!r} is not a slug"

    def test_every_family_is_known_and_ordered(self):
        for c in ALL:
            assert c.family in FAMILIES, f"{c.key} has unknown family {c.family!r}"
            assert c.family in FAMILY_ORDER
            assert c.family in FAMILY_LABELS

    def test_every_family_has_a_fallback_glyph(self):
        # The terminal always draws the family mark — a logo is not something a
        # terminal can render, so a family with no glyph leaves a blank row.
        for family, glyph in FAMILIES.items():
            assert glyph.strip(), f"family {family!r} has no fallback glyph"


class TestStyling:
    """A connector cannot ship unstyled — the catalog needs a mark to land on."""

    def test_every_connector_resolves_a_glyph(self):
        for c in ALL:
            assert c.mark.strip(), f"{c.key} renders no glyph"

    def test_accents_are_distinct(self):
        # The point of an accent is that a catalog of several reads as several
        # things; two connectors sharing one defeats it.
        accents = [c.accent for c in ALL]
        assert len(accents) == len(set(accents)), "two connectors share an accent"

    def test_every_accent_is_well_formed(self):
        for c in ALL:
            assert c.accent, f"{c.key} declares no accent"
            m = ACCENT_RE.match(c.accent)
            assert m, f"{c.key} accent {c.accent!r} is not rgb(r,g,b)"
            assert all(0 <= int(part) <= 255 for part in m.groups()), f"{c.key} accent is out of range"


class TestItSaysWhatItDoes:
    """A catalog of vendor names is a list of things to go look up elsewhere."""

    def test_every_connector_has_a_summary(self):
        for c in ALL:
            assert c.summary.strip(), f"{c.key} does not say what it does"
            assert len(c.summary) <= 90, f"{c.key} summary is too long for a collapsed row"
            assert "\n" not in c.summary, f"{c.key} summary must be one line"

    def test_every_connector_has_a_detail(self):
        for c in ALL:
            assert len(c.detail.strip()) >= 80, f"{c.key} detail does not explain what is read"

    def test_the_detail_says_what_is_never_read(self):
        # Read-only is a promise about scope, not just about writes. Every
        # descriptor states the boundary rather than leaving it implied.
        for c in ALL:
            assert "never" in c.detail.lower(), f"{c.key} detail never says what it will not do"

    def test_every_connector_points_somewhere_for_the_credential(self):
        for c in ALL:
            assert c.docs_url.startswith("https://"), f"{c.key} offers no https docs link"


class TestFields:
    def test_env_names_are_env_shaped(self):
        for c in ALL:
            for f in c.fields:
                assert re.fullmatch(r"[A-Z][A-Z0-9_]*", f.env), f"{c.key}.{f.env} is not an env name"

    def test_a_choice_field_defaults_into_its_choices(self):
        for c in ALL:
            for f in c.fields:
                if f.choices:
                    assert f.default in f.choices, f"{c.key}.{f.env} defaults outside its choices"

    def test_required_envs_are_real_fields(self):
        for c in ALL:
            envs = {f.env for f in c.fields}
            assert set(c.required_envs) <= envs, f"{c.key} requires an env it does not declare"

    def test_every_connector_needs_something(self):
        for c in ALL:
            assert c.required_envs, f"{c.key} would count as connected on a machine with no config"


class TestSecrets:
    """The cross-check that replaces the TUI source-text grep for connector rows."""

    def test_every_secret_is_masked_by_the_settings_engine(self):
        from yeaboi.settings.engine import SECRET_ENVS

        for c in ALL:
            for env in c.secret_envs:
                assert env in SECRET_ENVS, f"{env} is secret but the settings engine would show it"

    def test_every_secret_is_redacted_in_logs(self):
        from yeaboi.redaction import SECRET_ENV_KEYS

        for c in ALL:
            for env in c.secret_envs:
                assert env in SECRET_ENV_KEYS, f"{env} is secret but could reach a log line"

    def test_a_choice_field_is_never_secret(self):
        # A masked value the user picks from a visible list is a contradiction.
        for c in ALL:
            for f in c.fields:
                assert not (f.choices and f.secret), f"{c.key}.{f.env} is both a choice and a secret"


class TestVerification:
    def test_every_named_probe_exists(self):
        from yeaboi import provider_verification

        for c in ALL:
            if c.verify:
                assert hasattr(provider_verification, c.verify), f"{c.key} names a missing probe {c.verify}"

    def test_a_field_is_never_both_a_request_arg_and_an_env_arg(self):
        for c in ALL:
            for f in c.fields:
                assert not (f.verify_arg and f.env_arg), f"{c.key}.{f.env} would be read from two places"

    def test_every_host_deciding_field_is_read_from_the_saved_value(self):
        # The exfiltration guard, as a descriptor rule: a field that picks a
        # host must never be settable by a verify request, or a caller could
        # send the stored token wherever they liked. A REQUIRED base_url is the
        # exception verify_connection already covers — it refuses to pair a
        # supplied host with a stored token.
        for c in ALL:
            for f in c.fields:
                names_host = any(word in f.env for word in ("SITE", "REGION", "BASE_URL"))
                if names_host and not f.required:
                    assert not f.verify_arg, f"{c.key}.{f.env} picks a host but a request could set it"

    def test_every_probe_accepts_the_arguments_its_descriptor_sends(self):
        import inspect

        from yeaboi import provider_verification

        for c in ALL:
            if not c.verify:
                continue
            params = set(inspect.signature(getattr(provider_verification, c.verify)).parameters)
            sent = {f.verify_arg for f in c.fields if f.verify_arg} | {f.env_arg for f in c.fields if f.env_arg}
            assert sent <= params, f"{c.key} sends {sorted(sent - params)} which {c.verify} does not take"

    def test_verify_args_are_unique_per_connector(self):
        for c in ALL:
            args = [f.verify_arg for f in c.fields if f.verify_arg]
            assert len(args) == len(set(args)), f"{c.key} sends two fields under one verify name"


class TestSections:
    def test_every_section_is_a_settings_section(self):
        from yeaboi.settings.engine import SECTIONS

        for c in ALL:
            assert c.section in SECTIONS, f"{c.key} renders under unknown section {c.section!r}"

    def test_the_terminal_and_the_desktop_agree_on_that_section(self):
        import json
        import pathlib

        manifest = json.loads(
            (pathlib.Path(__file__).resolve().parents[2] / "contracts" / "v1" / "routes_manifest.json").read_text()
        )
        desktop = {s for tab in manifest["settings_tabs"] for s in tab["sections"]}
        for c in ALL:
            assert c.section in desktop, f"{c.key}'s section {c.section!r} has no desktop home"


# Trackers whose descriptor powers a write path (sprint-plan sync through
# tools/ + *_sync.py, behind the human-review gate). Everything else must stay
# read-only — a write path is a credential-scope conversation, not a default.
_WRITE_CAPABLE = {"linear", "trello"}


@pytest.mark.parametrize("connector", ALL, ids=lambda c: c.key)
def test_read_only_connectors_declare_it(connector):
    if connector.key in _WRITE_CAPABLE:
        assert not connector.read_only, f"{connector.key} is a tracker and must declare its writes"
        assert "write" in connector.detail.lower(), f"{connector.key} hides its writes from the catalog"
        return
    assert connector.read_only, f"{connector.key} is not read-only"


class TestTheFetchSeam:
    """``fetch`` names a real function with the one signature every mode calls."""

    def test_a_declared_fetch_exists_and_is_callable(self):
        import importlib

        for connector in registry.all_connectors():
            if not connector.fetch:
                continue
            module = importlib.import_module(f"yeaboi.connectors.{connector.key}")
            assert callable(getattr(module, connector.fetch, None)), f"{connector.key}.{connector.fetch}"

    def test_every_fetcher_takes_a_window_and_nothing_else(self):
        # Credentials come from the environment inside the function, never from
        # a caller — the same rule ``env_arg`` enforces for verification.
        import importlib
        import inspect

        for connector in registry.all_connectors():
            if not connector.fetch:
                continue
            module = importlib.import_module(f"yeaboi.connectors.{connector.key}")
            params = list(inspect.signature(getattr(module, connector.fetch)).parameters)
            assert params == ["window_start", "window_end"], f"{connector.key} takes {params}"

    # Delivery trackers verify a credential and feed the catalog, but the
    # ops-event vocabulary has no delivery kind — their read/write path is the
    # tracker integration, not a gather. Verify stays: a credential the user
    # just typed must be probeable.
    _CATALOG_ONLY = {"linear", "trello"}

    def test_a_connector_that_can_be_verified_can_be_gathered_from(self):
        # An entry in the catalog that verifies but returns nothing is a settings
        # screen pretending to be a feature.
        missing = [
            c.key for c in registry.all_connectors() if c.verify and not c.fetch and c.key not in self._CATALOG_ONLY
        ]
        assert missing == [], f"{missing} verify but gather nothing"


class TestLegacyEntries:
    """The display-only catalog entries hold to the same identity rules.

    They share the catalog with the real connectors, so a duplicate key or a
    reused accent would make two rows read as one thing.
    """

    def test_they_never_join_the_registry(self):
        # Everything derived from _CONNECTORS (settings fields, verify tables,
        # secret masks) must keep deriving from connectors alone.
        assert not {c.key for c in LEGACY} & {c.key for c in ALL}
        for entry in LEGACY:
            assert registry.by_key(entry.key) is None, f"{entry.key} leaked into _CONNECTORS"

    def test_keys_are_unique_slugs_across_the_whole_catalog(self):
        keys = [c.key for c in ALL] + [c.key for c in LEGACY]
        assert len(keys) == len(set(keys))
        for entry in LEGACY:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", entry.key), f"{entry.key!r} is not a slug"

    def test_families_are_known_and_marks_resolve(self):
        for entry in LEGACY:
            assert entry.family in FAMILIES, f"{entry.key} family {entry.family!r} is unknown"
            assert entry.mark.strip(), f"{entry.key} renders no glyph"

    def test_accents_are_distinct_across_the_whole_catalog(self):
        accents = [c.accent for c in ALL] + [c.accent for c in LEGACY]
        assert len(accents) == len(set(accents)), "two catalog entries share an accent"
        for entry in LEGACY:
            m = ACCENT_RE.match(entry.accent)
            assert m, f"{entry.key} accent {entry.accent!r} is not rgb(r,g,b)"
            assert all(0 <= int(part) <= 255 for part in m.groups())

    def test_credential_fields_are_marked_secret(self):
        # SPACE_KEY is an identifier, not a credential — the words that matter
        # are the ones that name a bearer value.
        for entry in LEGACY:
            for field in entry.fields:
                if any(word in field.env for word in ("TOKEN", "API_KEY", "WEBHOOK")):
                    assert field.secret, f"{entry.key}.{field.env} is a credential but not secret"

    def test_connectedness_matches_the_config_getters(self, monkeypatch):
        from yeaboi.connectors import legacy

        for env in registry.legacy_envs():
            monkeypatch.delenv(env, raising=False)
        # Confluence rides Jira's Atlassian identity plus its own space key.
        confluence = legacy.by_key("confluence")
        assert not legacy.is_connected(confluence)
        for env in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "CONFLUENCE_SPACE_KEY"):
            monkeypatch.setenv(env, "x")
        assert legacy.is_connected(confluence)
        # Slack counts with either credential alone.
        slack = legacy.by_key("slack")
        assert not legacy.is_connected(slack)
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-1")
        assert legacy.is_connected(slack)
