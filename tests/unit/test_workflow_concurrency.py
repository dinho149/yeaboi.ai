"""Standing guard over `codeql.yml`'s concurrency group.

CodeQL's starter template keys its concurrency group on ``github.ref`` with
``cancel-in-progress: true``. In this repo that combination is actively wrong,
and the way it fails is invisible:

``auto-version.yml`` pushes a ``chore: bump version`` commit to the PR branch
roughly a minute after the human's push, using ``AUTO_VERSION_PAT`` — a real
identity, so it emits a ``pull_request: synchronize`` event. Under a ref-keyed
group both commits resolve to ``codeql-refs/pull/N/merge``, so the bump cancels
the first run mid-flight. ``Analyze (actions)`` takes ~47s and has already
uploaded its SARIF; ``Analyze (python)`` takes ~2m24s and is killed before it
uploads. GitHub Advanced Security then finds 1 of the 2 configurations it
expects from ``main`` and posts the results check as ``neutral`` — which the PR
UI draws with the same grey icon as "Skipped".

Nothing in CI notices: the run is *cancelled*, not failed; CodeQL is not in the
main-branch ruleset; and the grey check lands on a commit that is superseded
seconds later and never revisited. It surfaces only as an occasional "why did
CodeQL skip?" So the invariant is pinned here instead.

**`ci.yml` deliberately still keys on `github.ref` and is not asserted here.**
The asymmetry is intentional, not an oversight waiting to be tidied up: a
superseded commit's CI genuinely is irrelevant, because the bump commit re-runs
every required check itself. CodeQL is the odd one out because its results check
is assembled from *two* uploads and renders a partial set as a grey pass.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEQL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "codeql.yml"

# The one correct value. Asserted whole rather than by substring so that the
# operand *order* is pinned: `github.sha || github.event.pull_request.head.sha`
# would satisfy any per-token check while reintroducing the merge-commit key for
# every PR run, which is most of the bug.
EXPECTED_GROUP = "codeql-${{ github.event.pull_request.head.sha || github.sha }}"

# Events that carry no `pull_request` payload, and so depend on the `|| github.sha`
# fallback. If a trigger is added to codeql.yml, decide which side it belongs on
# and add it here — `merge_group`, for one, has no pull_request context either.
FALLBACK_EVENTS = {"push", "schedule"}
PR_EVENTS = {"pull_request"}


def _codeql() -> dict:
    """Parse codeql.yml.

    Note the YAML 1.1 quirk: a bare ``on:`` key parses as the boolean ``True``,
    not the string ``"on"``, so the trigger block is ``doc[True]``.
    """
    return yaml.safe_load(CODEQL_WORKFLOW.read_text())


def _group() -> str:
    """The concurrency group with its whitespace normalised.

    GitHub is indifferent to spacing inside ``${{ … }}``, so the test must be too
    — otherwise a reformat fails with a message about the wrong thing entirely.
    """
    return " ".join(_codeql()["concurrency"]["group"].split())


class TestCodeQLConcurrency:
    def test_group_is_scoped_to_the_commit_not_the_ref(self):
        """A new commit must never cancel the previous commit's analysis."""
        group = _group()

        assert "github.ref" not in group, (
            f"codeql.yml's concurrency group is `{group}`. Keying on `github.ref` "
            "makes every commit on a PR share one group, which is the bug this "
            "test exists to prevent — see the module docstring."
        )
        assert group == EXPECTED_GROUP, (
            f"codeql.yml's concurrency group is `{group}`, expected "
            f"`{EXPECTED_GROUP}`. Each PR commit needs its own group, keyed on "
            "`github.event.pull_request.head.sha` *first* — otherwise "
            "auto-version.yml's bump push cancels the in-flight run after only "
            "one language has uploaded SARIF, and Code Scanning reports the "
            'half-upload as a grey "Skipped" check. The `codeql-` prefix keeps '
            "the group out of every other workflow's namespace."
        )

    def test_every_trigger_is_covered_by_the_group(self):
        """`push` and `schedule` runs carry no pull_request payload."""
        # `True`, not `"on"` — see `_codeql`.
        triggers = set(_codeql()[True])

        assert triggers == FALLBACK_EVENTS | PR_EVENTS, (
            f"codeql.yml triggers on {sorted(triggers)}, but the concurrency "
            f"group is only known to be correct for {sorted(FALLBACK_EVENTS | PR_EVENTS)}. "
            "A new trigger needs a decision: does it carry a `pull_request` "
            "payload (keyed on the head SHA) or not (falls back to `github.sha`)? "
            "Record it in FALLBACK_EVENTS or PR_EVENTS."
        )
        assert "|| github.sha" in _group(), (
            f"codeql.yml's concurrency group is `{_group()}`. Without a "
            f"`|| github.sha` fallback the {sorted(FALLBACK_EVENTS)} runs all "
            "render the same empty group and cancel each other."
        )
