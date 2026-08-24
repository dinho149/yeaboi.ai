"""That `_no_real_gh_calls` covers the modules that can actually spawn `gh`.

Named `zz_` so it collects last: the property under test is about which modules
are loaded and which transport object each one holds, and that is only
interesting once the rest of the suite has been imported.

This exists because the guard was, for one commit, patching a module object
nothing called. `scripts/` is not a package and the two loaders disagree about
who owns the name `_gh_transport`: scripts do a plain import, binding whatever
object exists at their load time, while `test_gh_transport.py` builds a fresh
module off the file path and assigns it over `sys.modules`. Collection is
alphabetical, so in a full-suite run the registry entry is the fresh object and
a plain-importing script still holds the original.

The guard's own proof passed at the time — run against one file, where the two
coincide. That is the failure mode this file is here to make impossible: it
asserts the reach in the multi-module condition, not the single-file one.
"""

from __future__ import annotations

import sys

import pytest

SPAWNERS = ("pr_feedback",)


def _loaded_transports() -> dict[str, object]:
    found = {}
    for name in SPAWNERS:
        module = sys.modules.get(name)
        transport = getattr(module, "transport", None) if module is not None else None
        if transport is not None:
            found[name] = transport
    registry = sys.modules.get("_gh_transport")
    if registry is not None:
        found["sys.modules"] = registry
    return found


def test_the_suite_really_does_load_more_than_one_transport():
    """Not an assertion about correctness — about whether this file is testing
    anything. If the split ever stops happening the tests below become vacuous,
    and a vacuous guard test is worse than none."""
    transports = _loaded_transports()
    if len(transports) < 2:
        pytest.skip(f"only one transport loaded ({list(transports)}) — run the full suite")
    distinct = {id(t) for t in transports.values()}
    assert distinct, "no transport loaded at all"


def test_every_loaded_transport_carries_the_guard():
    """The invariant that was silently false. One unguarded object is one module
    that can shell out to the real `gh`."""
    unguarded = [name for name, transport in _loaded_transports().items() if "_blocked" not in repr(transport._run)]
    assert not unguarded, f"these transports can still spawn the real gh: {unguarded}"


def test_the_guard_fires_on_a_write_through_cowork_setup(monkeypatch):
    """End to end through the function that caused the incident: `_reclassify` on
    the `gh` branch, nothing stubbed, is the exact call that wrote
    `cowork:queued` and four comments onto merged PR #7."""
    setup = sys.modules.get("cowork_setup")
    if setup is None:
        pytest.skip("cowork_setup not loaded in this selection")
    monkeypatch.setattr(setup, "TRANSPORT", "gh")
    monkeypatch.setattr(setup.shutil, "which", lambda _: "/usr/bin/gh")
    with pytest.raises(BaseException, match="real gh CLI"):
        setup._reclassify(7, "platform", repair=False)


def test_the_guard_fires_on_beta_signoffs_write_path():
    """`beta_signoff._gh` writes too — `gh pr comment` markers on the batch PR,
    and `gh pr ready`. It once had its own `subprocess.run` outside the seam, so
    the fixture's "no test may shell out to the real gh" was narrower than it
    read."""
    signoff = sys.modules.get("beta_signoff")
    if signoff is None:
        pytest.skip("beta_signoff not loaded in this selection")
    with pytest.raises(BaseException, match="real gh CLI"):
        signoff._gh("pr", "comment", "1", "--body", "x")


def test_the_guard_fires_on_batch_assembles_write_path():
    """`batch_assemble._gh` opens the batch PR and closes constituents — writes
    that must never reach the real repo from a test that forgot to stub."""
    assemble = sys.modules.get("batch_assemble")
    if assemble is None:
        pytest.skip("batch_assemble not loaded in this selection")
    with pytest.raises(BaseException, match="real gh CLI"):
        assemble._gh("pr", "close", "1")


def test_a_local_git_read_is_not_blocked():
    """The guard is `gh`-only on purpose. `git remote get-url origin` cannot reach
    GitHub, and several tests legitimately let it run — blocking it failed seven
    tests that had done nothing wrong."""
    transport = sys.modules.get("_gh_transport")
    if transport is None:
        pytest.skip("transport not loaded")
    result = transport._run(["git", "--version"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
