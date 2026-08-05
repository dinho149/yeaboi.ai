"""Standing guard over the cowork model table (``cowork/models.md``).

The cowork fleet picks a model in four unrelated places — the routine dropdown
at claude.ai (account-side), agent frontmatter, ``--model`` in a workflow's
``claude_args``, and the implicit action default. ``cowork/models.md`` exists so
that changing what the fleet runs on is one edit rather than eight, and that
only holds while every other file names a *tier* instead of a model.

Nothing else in the Python suite would notice a model id being pasted back into
a routine file: the failure is silent by construction (the routine still runs,
just on the wrong model, and says nothing about it), and it only surfaces on a
bill or in a run log nobody reads. So it is caught statically here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COWORK = REPO_ROOT / "cowork"
AGENTS = REPO_ROOT / ".claude" / "agents"
COMMANDS = REPO_ROOT / ".claude" / "commands"
SETUP_SCRIPT = REPO_ROOT / "scripts" / "cowork_setup.py"
MODELS_DOC = COWORK / "models.md"

# Any Claude model id, in either the alias (``claude-sonnet-5``) or the dated
# (``claude-haiku-4-5-20251001``) form. Deliberately broad: a family this misses
# is a family that could be hardcoded without the guard firing.
MODEL_ID = re.compile(r"claude-(?:opus|sonnet|haiku|fable)-[\w.-]*\d", re.IGNORECASE)

# The tier vocabulary. ``inherit`` is a tier in the table AND the literal value
# of every agent's ``model:`` frontmatter, which is the point — an agent pins no
# model, so a spawn that forgets its override lands on the caller's model rather
# than something cheap and wrong.
TIERS = ("heavy", "deep", "standard", "fast", "inherit")


def _markdown_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.md"))


class TestModelsDocIsTheOnlySource:
    def test_the_table_names_every_tier(self):
        text = MODELS_DOC.read_text()
        for tier in TIERS:
            assert f"| `{tier}` |" in text, f"models.md has no table row for the `{tier}` tier"

    def test_the_table_names_real_models(self):
        # If this fails the table has drifted into naming tiers only, which would
        # leave the workflows' repo variables with nothing to be set from.
        assert MODEL_ID.search(MODELS_DOC.read_text()), "models.md names no model at all"

    @pytest.mark.parametrize(
        "path",
        [p for p in _markdown_files(COWORK) if p != MODELS_DOC],
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_no_cowork_file_hardcodes_a_model(self, path: Path):
        found = MODEL_ID.findall(path.read_text())
        assert not found, (
            f"{path.relative_to(REPO_ROOT)} names {found} directly. "
            "Name a tier from cowork/models.md instead — hardcoding a model here is "
            "exactly the drift that file exists to prevent."
        )

    @pytest.mark.parametrize(
        "path",
        _markdown_files(AGENTS),
        ids=lambda p: p.name,
    )
    def test_no_agent_pins_a_model(self, path: Path):
        found = MODEL_ID.findall(path.read_text())
        assert not found, (
            f".claude/agents/{path.name} pins {found}. Agents carry `model: inherit` "
            "and take their tier from the caller — see cowork/models.md."
        )

    @pytest.mark.parametrize("path", _markdown_files(COMMANDS), ids=lambda p: p.name)
    def test_no_slash_command_hardcodes_a_model(self, path: Path):
        """The commands spawn agents and register routines, so they pick tiers too.

        ``/cowork`` is the sharp case: it writes the model onto seventeen
        account-side routines, where a wrong id is invisible from the repo — the
        routine still fires and nothing anywhere reports which model read the
        code.
        """
        found = MODEL_ID.findall(path.read_text())
        assert not found, (
            f".claude/commands/{path.name} names {found} directly. Name a tier and "
            "resolve it through cowork/models.md — a command is a caller, and a "
            "caller that pins a model bypasses the one table."
        )

    def test_the_setup_script_hardcodes_no_model(self):
        """``scripts/cowork_setup.py`` is the only code that reads the tier table.

        It exists so the labels, variables and routine models are all derived from
        ``models.md`` rather than typed twice. A model id pasted into it would
        recreate exactly the duplication it was written to remove — and, being
        Python rather than markdown, would slip past every check above.
        """
        found = MODEL_ID.findall(SETUP_SCRIPT.read_text())
        assert not found, (
            f"scripts/cowork_setup.py names {found} directly. It must parse the id "
            "out of cowork/models.md at run time — see parse_tiers()."
        )

    @pytest.mark.parametrize("path", _markdown_files(AGENTS), ids=lambda p: p.name)
    def test_every_agent_inherits(self, path: Path):
        assert "\nmodel: inherit\n" in path.read_text(), (
            f".claude/agents/{path.name} must declare `model: inherit` so the caller's "
            "tier wins and a missing override degrades safely."
        )


class TestTierReferencesResolve:
    """Every tier named in cowork/ has to exist in the table."""

    # ``**Model** — `fast` `` in a routine file, `` the `deep` tier `` in the
    # shared procedure. Both forms mean "resolve this against models.md". The
    # bare parenthesised form next to an agent name (`` `cowork-builder` (`deep`) ``)
    # is deliberately not matched — a pattern loose enough to catch it also
    # catches every other backticked word in a table cell.
    TIER_REF = re.compile(r"(?:\*\*Model\*\*\s*—\s*`(\w+)`|`(\w+)` tier)")

    @classmethod
    def _tiers_in(cls, text: str) -> list[str]:
        return [name for match in cls.TIER_REF.findall(text) for name in match if name]

    @pytest.mark.parametrize(
        "path",
        [p for p in _markdown_files(COWORK) if p != MODELS_DOC],
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_named_tiers_exist(self, path: Path):
        for tier in self._tiers_in(path.read_text()):
            assert tier in TIERS, (
                f"{path.relative_to(REPO_ROOT)} refers to a `{tier}` tier, "
                f"which models.md does not define. Known tiers: {', '.join(TIERS)}."
            )


class TestWorkflowsReadTheRepoVariables:
    """The workflows cannot read a markdown table, so they read repo variables.

    The ``|| 'fallback'`` is mandatory, not stylistic: an unset variable renders
    empty and a bare ``--model`` breaks the argument. The fallback is pinned to
    what each job ran on *before* the tier table existed, so a variable nobody
    set reverts one job instead of breaking or surprising anyone with a bill.
    """

    WORKFLOWS = REPO_ROOT / ".github" / "workflows"
    MODEL_FLAG = re.compile(r"--model\s+(\S+)")

    @pytest.mark.parametrize(
        "path",
        sorted(p for p in (REPO_ROOT / ".github" / "workflows").glob("*.yml")),
        ids=lambda p: p.name,
    )
    def test_every_model_flag_is_a_variable_with_a_fallback(self, path: Path):
        text = path.read_text()
        for match in re.finditer(r"--model\s+\$\{\{([^}]*)\}\}", text):
            expr = match.group(1)
            assert "vars.YEABOI_MODEL_" in expr, (
                f"{path.name} interpolates a model from `{expr.strip()}` rather than a "
                "YEABOI_MODEL_* repo variable — see cowork/models.md."
            )
            assert "||" in expr, (
                f"{path.name} has no `||` fallback on its --model expression. An unset "
                "variable renders empty and `--model ` breaks the argument."
            )

    @pytest.mark.parametrize(
        "path",
        sorted(p for p in (REPO_ROOT / ".github" / "workflows").glob("*.yml")),
        ids=lambda p: p.name,
    )
    def test_no_workflow_hardcodes_a_model_outside_a_fallback(self, path: Path):
        for match in self.MODEL_FLAG.finditer(path.read_text()):
            assert match.group(1).startswith("${{"), (
                f"{path.name} hardcodes `--model {match.group(1)}`. Read it from a "
                "YEABOI_MODEL_* repo variable instead — see cowork/models.md."
            )
