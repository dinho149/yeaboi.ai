"""Tests for the canonical privacy wording (src/yeaboi/privacy.py)."""

import ast
from pathlib import Path

from yeaboi import privacy

SRC = Path(privacy.__file__).parent

# Every switch the disclosures name, mapped to the module that actually reads
# it. The table's honesty depends on these staying real: a renamed env var with
# a stale disclosure row is exactly the drift this pins down.
_SWITCH_OWNERS = {
    "YEABOI_UPDATE_CHECK": "update_check.py",
    "YEABOI_NO_TUNNEL": "config.py",
    "YEABOI_TELEMETRY": "telemetry.py",
    "LANGSMITH_TRACING": "config.py",
    "CLOUDFLARED_PATH": "retro/tunnel.py",
}

# The one disclosure whose off-switch honestly does not exist yet: the desktop
# shell's update check (yeaboi-desktop's updater.ts has no gate today).
_NO_SWITCH_KEYS = {"desktop-update"}


class TestStatement:
    def test_headline_and_statement_are_populated(self):
        assert privacy.PRIVACY_HEADLINE
        assert privacy.PRIVACY_STATEMENT
        assert all(paragraph.strip() for paragraph in privacy.PRIVACY_STATEMENT)

    def test_statement_makes_the_three_claims(self):
        joined = " ".join(privacy.PRIVACY_STATEMENT)
        assert "~/.yeaboi" in joined  # local storage
        assert "Ollama" in joined  # the fully-local option
        assert "feedback" in joined  # the only path to us


class TestDisclosures:
    def test_every_row_is_complete(self):
        for row in privacy.EGRESS_DISCLOSURES:
            assert set(row) == {"key", "group", "what", "where", "when", "default", "off_switch"}, row.get("key")
            for field, value in row.items():
                assert value.strip(), f"{row['key']}.{field} is empty"

    def test_keys_are_unique(self):
        keys = [row["key"] for row in privacy.EGRESS_DISCLOSURES]
        assert len(keys) == len(set(keys))

    def test_groups_are_complete_and_used(self):
        group_keys = [group["key"] for group in privacy.EGRESS_GROUPS]
        assert len(group_keys) == len(set(group_keys))
        for group in privacy.EGRESS_GROUPS:
            assert set(group) == {"key", "title"}
            assert group["title"].strip()
        # Every row belongs to a declared group, and no group is empty.
        rows_by_group = {row["group"] for row in privacy.EGRESS_DISCLOSURES}
        assert rows_by_group == set(group_keys)

    def test_the_off_by_default_paths_say_off(self):
        by_key = {row["key"]: row for row in privacy.EGRESS_DISCLOSURES}
        assert by_key["telemetry"]["default"] == "off"
        assert by_key["tracing"]["default"] == "off"
        assert by_key["feedback"]["default"] == "user-initiated"

    def test_every_named_switch_exists_where_claimed(self):
        for env, owner in _SWITCH_OWNERS.items():
            assert any(env in row["off_switch"] for row in privacy.EGRESS_DISCLOSURES), (
                f"{env} is pinned here but no disclosure names it any more — drop it from _SWITCH_OWNERS"
            )
            source = (SRC / owner).read_text(encoding="utf-8")
            assert env in source, f"{env} is not read by {owner} — the disclosure would lie"

    def test_switchless_rows_admit_it(self):
        for row in privacy.EGRESS_DISCLOSURES:
            if row["key"] in _NO_SWITCH_KEYS:
                assert row["off_switch"].lower().startswith("none"), row["key"]
            else:
                # Every other row names a real control (an env var, a settings
                # path, or a user action), never a vague reassurance.
                assert not row["off_switch"].lower().startswith("none"), row["key"]

    def test_unpinned_env_vars_do_not_sneak_into_disclosures(self):
        # A new env var named in a disclosure row must be added to
        # _SWITCH_OWNERS, so its existence keeps being verified.
        import re

        for row in privacy.EGRESS_DISCLOSURES:
            for env in re.findall(r"\b(?:YEABOI|LANGSMITH|CLOUDFLARED)_[A-Z_]+", row["off_switch"]):
                assert env in _SWITCH_OWNERS, f"{row['key']} names {env}, which _SWITCH_OWNERS does not pin"


class TestSwitches:
    """EGRESS_SWITCHES — the live-toggle mapping every surface renders."""

    def test_every_switch_names_a_real_disclosure(self):
        disclosure_keys = {row["key"] for row in privacy.EGRESS_DISCLOSURES}
        switch_keys = [entry["key"] for entry in privacy.EGRESS_SWITCHES]
        assert len(switch_keys) == len(set(switch_keys))
        assert set(switch_keys) <= disclosure_keys

    def test_every_switch_is_complete(self):
        for entry in privacy.EGRESS_SWITCHES:
            assert set(entry) == {"key", "env", "on_value"}, entry.get("key")
            assert entry["on_value"] in {"true", "false"}, entry["key"]

    def test_every_env_is_a_real_settings_field(self):
        # The toggle a surface draws from this mapping must write a field the
        # settings engine accepts — imported here, never in privacy.py.
        from yeaboi.settings.engine import get_settings

        fields = {field.env: field for field in get_settings().fields}
        for entry in privacy.EGRESS_SWITCHES:
            field = fields.get(entry["env"])
            assert field is not None, f"{entry['key']} maps to {entry['env']}, which the engine does not know"
            assert set(field.choices) == {"true", "false"}, entry["env"]

    def test_every_env_is_the_one_the_disclosure_names(self):
        by_key = {row["key"]: row for row in privacy.EGRESS_DISCLOSURES}
        for entry in privacy.EGRESS_SWITCHES:
            assert entry["env"] in by_key[entry["key"]]["off_switch"], entry["key"]


class TestModuleStaysImportFree:
    def test_privacy_module_has_no_imports(self):
        """Like beta.py: every surface pulls from this module, some at startup;
        an import here is a silent latency cost on all of them."""
        source = Path(privacy.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        assert imports == []
