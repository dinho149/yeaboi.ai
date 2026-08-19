"""Standing guards over the hygiene lenses.

A lens is a detector a sweep runs unattended, and two of the three feed the
auto lane — so a false positive here is not a noisy report, it is an unwatched
deletion. Three classes of guard, and the middle one is the whole reason this
file exists:

* the **policy is well-formed**: every exclusion carries the reason it exists,
  and every lens speaks the scout's four-word type vocabulary rather than a
  second one;
* **survey narrow, confirm wide, change narrow** holds — every find lands
  inside the charter that asked for it, and the repo-wide index that *confirms*
  a find reads the halves of the tree a scoped survey cannot (`go/` above all,
  where a parity twin is the only thing standing between a "dead" symbol and a
  broken sidecar);
* the **detectors recognise every shape an assertion takes in this repo** —
  a statement, an `_assert_match` helper, a `pytest.raises`, an
  `AssertionError` trap, and a `# must not raise` comment. Each of those was a
  false-positive class found by hand-auditing the first run, and each would
  have handed a builder a live test to delete.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cowork_setup  # noqa: E402
import hygiene_lens as lens  # noqa: E402

POLICY = yaml.safe_load((REPO_ROOT / ".github" / "hygiene" / "lens-policy.yml").read_text())
LENSES = POLICY["lenses"]

# The dead-code index is a full-repository read. Built once and handed to every
# test that needs it — fifteen charters times one rebuild each is a minute of
# CI for an answer that cannot change between them.
_INDEX: lens.Index | None = None


def index() -> lens.Index:
    global _INDEX
    if _INDEX is None:
        _INDEX = lens.build_index()
    return _INDEX


def _fake_repo(monkeypatch, tmp_path: Path, files: dict[str, str], owns: str) -> None:
    """A miniature repo with one charter, so a detector can be driven end to end."""
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    charters = tmp_path / "cowork" / "workstreams"
    charters.mkdir(parents=True)
    (charters / "demo.md").write_text(f"# demo\n\n**Owns** — {owns}\n\n## Standing concerns\n")
    monkeypatch.setattr(lens, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(lens, "WORKSTREAMS_DIR", charters)


class TestThePolicyIsWellFormed:
    """An exclusion without a reason is indistinguishable from a silenced bug."""

    def test_every_lens_the_script_implements_is_configured(self):
        assert set(LENSES) == set(lens.LENSES)

    def test_every_lens_speaks_the_scouts_type_vocabulary(self):
        """`type` must be one of the four the scout can emit, not a fifth word.

        `parse_scout_types` reads the union out of `cowork-scout.md`'s JSON
        schema, so this compares against what an agent is actually handed at run
        time rather than against a constant that agrees with an older file.
        """
        allowed = set(cowork_setup.parse_scout_types())
        assert allowed, "the scout's type union could not be parsed"
        for name, settings in LENSES.items():
            assert settings["type"] in allowed, f"{name} emits a type no scout may return"

    def test_every_lens_declares_a_lane_and_a_batch_cap(self):
        for name, settings in LENSES.items():
            assert settings["lane"] in {"auto", "propose"}, name
            assert isinstance(settings["max_batch"], int) and settings["max_batch"] > 0, name
            assert settings["summary"].strip(), name

    @pytest.mark.parametrize("name", sorted(LENSES))
    def test_every_exclusion_records_why_it_exists(self, name):
        for rule in LENSES[name].get("excludes", ()):
            assert rule["id"], name
            assert rule.get("why", "").strip(), f"{name}/{rule['id']} excludes without saying why"

    def test_every_layering_invariant_is_owned_answerable_and_compiles(self):
        workstreams = set(cowork_setup.parse_workstreams())
        seen: set[str] = set()
        for invariant in LENSES["layering"]["invariants"]:
            assert invariant["id"] not in seen, f"duplicate invariant id {invariant['id']}"
            seen.add(invariant["id"])
            assert invariant["workstream"] in workstreams, invariant["id"]
            assert invariant.get("why", "").strip(), invariant["id"]
            # `instead` is what a builder is told to use. Without it the auto
            # lane's own condition — "only when the fix is an import swap" —
            # has nothing to point at.
            assert invariant.get("instead", "").strip(), invariant["id"]
            re.compile(invariant["forbid"])

    def test_the_exempt_paths_of_every_invariant_exist(self):
        """A path typo silently widens an invariant to a file it was meant to spare."""
        for invariant in LENSES["layering"]["invariants"]:
            for path in invariant.get("exempt", ()):
                assert (REPO_ROOT / path).exists(), f"{invariant['id']} exempts a path that is not there: {path}"


class TestSurveyNarrow:
    """A find must land inside the charter that asked for it, and nowhere else."""

    def test_a_charter_resolves_to_real_paths(self):
        spec = lens.charter("retro")
        assert spec.owns
        assert all(p.exists() for p in spec.owns)

    def test_an_except_clause_is_subtracted(self):
        """`ui/` **except `ui/session/`** — planning owns the session screens."""
        spec = lens.charter("tui-ux")
        assert REPO_ROOT / "src/yeaboi/ui" in spec.owns
        assert REPO_ROOT / "src/yeaboi/ui/session" in spec.excludes
        assert spec.covers(REPO_ROOT / "src/yeaboi/ui/splash.py")
        assert not spec.covers(REPO_ROOT / "src/yeaboi/ui/session/anything.py")

    def test_a_leading_dot_survives_resolution(self):
        """`.github/workflows/` is a path.

        The strip that tidies a trailing comma out of prose took the leading dot
        with it, which quietly emptied **platform** of every workflow file and
        **security** of every CodeQL one — with both charters still reporting a
        healthy path list.
        """
        assert REPO_ROOT / ".github/workflows" in lens.charter("platform").owns
        assert REPO_ROOT / ".github/codeql" in lens.charter("security").owns

    def test_an_ambiguous_basename_is_never_a_claim(self):
        """`server.py` exists in three modes; claiming it would hand over another's files."""
        hits, why = lens._resolve("server.py")
        assert hits == []
        assert "ambiguous" in why

    def test_a_ticked_identifier_is_not_a_path(self):
        """Charters tick `_INTAKE_CARDS` and `usage_get` beside their paths."""
        for token in ("_INTAKE_CARDS", "usage_get", "yeaboi-core"):
            assert lens._resolve(token) == ([], "not a path")

    def test_braces_expand(self):
        assert lens._expand_braces("test_{a,b}_x.py") == ["test_a_x.py", "test_b_x.py"]

    @pytest.mark.parametrize("workstream", sorted(cowork_setup.parse_workstreams()))
    def test_no_lens_reports_a_file_outside_the_charter(self, workstream, monkeypatch):
        """The invariant the whole design rests on, over every charter there is.

        `crash-fuzz` is driven through its seam rather than spawning six ptys
        per charter — and the fake it is given is deliberately hostile: a
        traceback in a file *nobody* in this repo could own alongside one the
        charter does. A lens that leaked the first would fail here, which is a
        stronger check than the real fuzzer happening not to crash today.
        """
        spec = lens.charter(workstream)
        elsewhere = "src/yeaboi/agent/nodes.py" if workstream != "planning" else "src/yeaboi/ui/shared/_tips.py"
        monkeypatch.setattr(
            lens,
            "_fuzz_runner",
            lambda settings: [
                {
                    "seed": 0,
                    "steps": 10,
                    "keys_sent": 10,
                    "verdict": "traceback",
                    "frames": [(elsewhere, 1, "f")],
                    "exception": "ValueError: x",
                }
            ],
        )
        for name in sorted(lens.LENSES):
            report = lens.run(name, workstream, policy=POLICY, index=index())
            for find in report["finds"]:
                assert spec.covers(REPO_ROOT / find["file"]), f"{name}/{workstream} reached {find['file']}"

    def test_tests_and_source_are_opposite_halves(self):
        """The dead-code scan must never see a `test_*` function; it is dead by design."""
        spec = lens.charter("retro")
        assert all("tests/" not in lens._relative(p) for p in lens.python_files(spec, tests=False))
        assert all(lens._relative(p).startswith("tests/") for p in lens.python_files(spec, tests=True))


class TestConfirmWide:
    """Proving a negative is a read, and a read changes nothing about who may edit."""

    def test_the_index_reads_the_halves_a_scoped_survey_cannot(self):
        idx = index()
        # A Go twin's header names its Python twin. That mention is the only
        # thing between `make parity` and a builder deleting a mirrored symbol.
        assert idx.words["aggregate_ai_markers"] > idx.definitions["aggregate_ai_markers"]
        # `.claude/` is repo content — agents, skills and commands all name
        # symbols — and skipping it whole was a false-positive factory.
        assert idx.words["SCOUT_TYPES"] > 0

    def test_worktrees_of_this_same_tree_are_not_references(self):
        assert lens._SKIP_PREFIXES == (".claude/worktrees",)

    def test_a_symbol_is_dead_only_when_every_mention_is_a_definition(self):
        idx = lens.Index()
        idx.words.update({"lonely": 1, "popular": 4})
        idx.definitions.update({"lonely": 1, "popular": 1})
        assert not idx.referenced_elsewhere("lonely")
        assert idx.referenced_elsewhere("popular")


class TestDeadCode:
    def test_an_unreferenced_function_is_found_and_a_referenced_one_is_not(self, monkeypatch, tmp_path):
        _fake_repo(
            monkeypatch,
            tmp_path,
            {"src/mine.py": "def orphan():\n    pass\n\n\ndef used():\n    pass\n", "src/caller.py": "used()\n"},
            "`src/mine.py`",
        )
        report = lens.run("dead-code", "demo", policy=POLICY, index=lens.build_index(tmp_path))
        assert [f["symbol"] for f in report["finds"]] == ["orphan"]

    def test_a_mention_from_a_go_twin_keeps_a_symbol_alive(self, monkeypatch, tmp_path):
        """The one exclusion that would fail silently and merge green."""
        _fake_repo(
            monkeypatch,
            tmp_path,
            {
                "src/mine.py": "def mirrored():\n    pass\n",
                "go/internal/thing.go": "// Twin of src/mine.py's mirrored.\npackage thing\n",
            },
            "`src/mine.py`",
        )
        report = lens.run("dead-code", "demo", policy=POLICY, index=lens.build_index(tmp_path))
        assert report["finds"] == []

    def test_a_decorated_registration_is_excluded(self, monkeypatch, tmp_path):
        _fake_repo(monkeypatch, tmp_path, {"src/mine.py": "@tool\ndef handler():\n    pass\n"}, "`src/mine.py`")
        report = lens.run("dead-code", "demo", policy=POLICY, index=lens.build_index(tmp_path))
        assert report["finds"] == []
        assert report["skipped"] == [{"symbol": "handler", "file": "src/mine.py", "rule": "registry-decorated"}]

    def test_a_framework_discovered_class_is_excluded(self, monkeypatch, tmp_path):
        """Hatchling finds its build hook by subclass, so it has no caller by construction."""
        _fake_repo(
            monkeypatch,
            tmp_path,
            {"src/mine.py": "class CoreBinaryHook(BuildHookInterface):\n    pass\n"},
            "`src/mine.py`",
        )
        report = lens.run("dead-code", "demo", policy=POLICY, index=lens.build_index(tmp_path))
        assert report["finds"] == []

    def test_the_real_paths_module_is_spared(self):
        """Seven `get_*_log_dir` helpers have no caller — unadopted, not dead.

        Deleting them would remove the API CLAUDE.md's Observability rule
        requires every mode to log through, with `make test` green throughout.
        """
        report = lens.run("dead-code", "platform", policy=POLICY, index=index())
        assert not [f for f in report["finds"] if f["file"].endswith("paths.py")]

    def test_over_the_cap_is_held_and_counted_never_dropped(self, monkeypatch, tmp_path):
        source = "".join(f"def orphan_{n}():\n    pass\n\n\n" for n in range(9))
        _fake_repo(monkeypatch, tmp_path, {"src/mine.py": source}, "`src/mine.py`")
        report = lens.run("dead-code", "demo", policy=POLICY, index=lens.build_index(tmp_path))
        cap = LENSES["dead-code"]["max_batch"]
        assert len(report["finds"]) == cap
        assert report["held"] == 9 - cap


class TestEveryShapeAnAssertionTakes:
    """Each of these was a false positive on the first run over the real tree."""

    def _flags(self, body: str) -> bool:
        tree = ast.parse(body)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        helpers = frozenset(LENSES["assertion-free-tests"]["assertion_helpers"])
        # Threaded, not set on the module: a global meant a second run in the same
        # process inherited the previous policy's allowances.
        intent = tuple(LENSES["assertion-free-tests"]["intent_comments"])
        return not lens._asserts(node, helpers, body, intent)

    def test_a_statement_counts(self):
        assert not self._flags("def test_x():\n    assert 1\n")

    def test_a_leading_underscore_helper_counts(self):
        """`tests/parity/` delegates to `_assert_match` — twelve tests, all flagged."""
        assert not self._flags("def test_x():\n    _assert_match(a, b)\n")

    def test_a_mock_method_counts(self):
        assert not self._flags("def test_x():\n    spy.assert_called_once()\n")

    def test_a_raises_block_counts(self):
        assert not self._flags("def test_x():\n    with pytest.raises(OSError):\n        boom()\n")

    def test_an_assertion_error_trap_counts(self):
        """A stub monkeypatched over the call that must never happen."""
        body = (
            "def test_x(monkeypatch):\n"
            "    def _boom(*a):\n"
            "        raise AssertionError('must not call GitHub')\n"
            "    monkeypatch.setattr('mod.fn', _boom)\n"
            "    run()\n"
        )
        assert not self._flags(body)

    def test_a_trailing_intent_comment_counts(self):
        """`# must not raise` sits at 23 sites; it is how this repo writes it.

        Read by whole lines rather than through `ast.get_source_segment`, which
        ends at the node's last column — precisely where the comment starts.
        """
        assert not self._flags("def test_x():\n    stop()\n    stop()  # must not raise\n")

    def test_a_bare_call_asserting_nothing_is_flagged(self):
        assert self._flags("def test_x():\n    do_something()\n")

    def test_a_skipped_test_is_excluded(self, monkeypatch, tmp_path):
        _fake_repo(
            monkeypatch,
            tmp_path,
            {"tests/test_a.py": "@pytest.mark.skip\ndef test_x():\n    do()\n"},
            "`tests/test_a.py`",
        )
        report = lens.run("assertion-free-tests", "demo", policy=POLICY)
        assert report["finds"] == []
        assert report["skipped"][0]["rule"] == "skipped"


class TestLayering:
    def test_a_violation_is_found_with_the_helper_to_use_instead(self, monkeypatch, tmp_path):
        policy = {"lenses": {"layering": dict(LENSES["layering"])}}
        policy["lenses"]["layering"]["invariants"] = [
            {
                "id": "demo-rule",
                "workstream": "demo",
                "forbid": r"forbidden_call\(",
                "instead": "`the_helper`",
                "why": "test",
            }
        ]
        _fake_repo(monkeypatch, tmp_path, {"src/mine.py": "x = forbidden_call()\n"}, "`src/mine.py`")
        report = lens.run("layering", "demo", policy=policy)
        assert len(report["finds"]) == 1
        assert "the_helper" in report["finds"][0]["evidence"]

    def test_an_inline_marker_waives_one_site_and_not_the_file(self, monkeypatch, tmp_path):
        policy = {"lenses": {"layering": dict(LENSES["layering"])}}
        policy["lenses"]["layering"]["invariants"] = [
            {
                "id": "demo-rule",
                "workstream": "demo",
                "forbid": r"forbidden_call\(",
                "instead": "`the_helper`",
                "why": "test",
            }
        ]
        source = "a = forbidden_call()  # lens-exempt: demo-rule — deliberate\nb = forbidden_call()\n"
        _fake_repo(monkeypatch, tmp_path, {"src/mine.py": source}, "`src/mine.py`")
        report = lens.run("layering", "demo", policy=policy)
        assert [f["line"] for f in report["finds"]] == [2]

    def test_a_workstream_with_no_invariant_finds_nothing(self):
        """A lens with nothing to say on a surface must say nothing."""
        assert lens.run("layering", "retro", policy=POLICY)["finds"] == []

    def test_the_documented_crossing_in_config_is_waived_in_place(self):
        """`config.py` keeps a live `Path.home()` on purpose, and says so.

        Waived by the marker on that line rather than by exempting the file, so
        a second, undocumented crossing in the same file still reports.
        """
        assert lens.run("layering", "platform", policy=POLICY)["finds"] == []
        text = (REPO_ROOT / "src" / "yeaboi" / "config.py").read_text()
        assert "lens-exempt: paths-through-paths-py" in text


class TestTheOutputIsHonestAboutItself:
    def test_a_lane_is_a_ceiling_and_the_report_says_so(self):
        report = lens.run("layering", "retro", policy=POLICY, index=lens.Index())
        assert report["lane"] == LENSES["layering"]["lane"]
        assert report["type"] == LENSES["layering"]["type"]

    def test_charter_tokens_that_resolved_to_nothing_are_reported(self):
        """A silently-dropped path is a charter half-surveyed with nothing to show it."""
        report = lens.run("layering", "platform", policy=POLICY)
        assert all({"token", "why"} == set(item) for item in report["unresolved"])

    def test_the_renderer_says_so_when_it_found_nothing(self):
        assert "nothing" in lens.render(lens.run("layering", "retro", policy=POLICY))


class TestTheWiring:
    """A lens nothing runs is a script, and a routine naming one that does not exist is a broken run."""

    ROUTINES = REPO_ROOT / "cowork" / "routines" / "cron"

    def _lens_blocks(self) -> dict[str, list[str]]:
        """Every `## Lenses` section, as {routine filename: [lens names]}."""
        found: dict[str, list[str]] = {}
        for path in sorted(self.ROUTINES.glob("*-sweep.md")):
            text = path.read_text()
            match = re.search(r"^## Lenses\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
            if match:
                found[path.name] = re.findall(r"^- `([a-z-]+)`", match.group(1), re.MULTILINE)
        return found

    def test_every_lens_a_routine_names_exists(self):
        for routine, names in self._lens_blocks().items():
            assert names, f"{routine} has a `## Lenses` section that names none"
            for name in names:
                assert name in lens.LENSES, f"{routine} runs a lens that does not exist: {name}"

    def test_a_lenses_section_does_not_break_the_inherited_check_in(self):
        """`routines_without_check_in` tells delegation from ownership on an exact `## Run`.

        A sweep delegates its steps to `sweep-procedure.md` and inherits its
        check-in from there. The heading match is exact for this reason — a
        `## Lenses` heading that read as "owns its steps" would make every
        piloted sweep report as never checking in, every evening, for a fault
        that is a markdown heading.
        """
        assert cowork_setup.routines_without_check_in() == []

    def test_every_layering_invariant_is_run_by_the_sweep_that_owns_it(self):
        blocks = self._lens_blocks()
        for invariant in LENSES["layering"]["invariants"]:
            routine = f"{invariant['workstream']}-sweep.md"
            assert "layering" in blocks.get(routine, []), (
                f"{invariant['id']} is declared for {invariant['workstream']}, "
                f"but {routine} does not run the layering lens"
            )

    def test_the_sweep_procedure_carries_the_rule_the_design_rests_on(self):
        text = (REPO_ROOT / "cowork" / "sweep-procedure.md").read_text()
        assert "Survey narrow, confirm wide, change narrow" in text
        assert "hygiene-lenses.md" in text

    def test_the_catalogue_is_reachable_from_the_readme(self):
        assert "hygiene-lenses.md" in (REPO_ROOT / "cowork" / "README.md").read_text()

    def test_the_scout_can_read_lens_output(self):
        """And its schema, type union and find cap are untouched by that.

        `parse_scout_types` regexes the vocabulary back out of the agent file,
        so an edit that reformatted the schema would take the fleet's type
        checking with it silently.
        """
        text = (REPO_ROOT / ".claude" / "agents" / "cowork-scout.md").read_text()
        assert "hygiene-lenses.md" in text
        assert cowork_setup.parse_scout_types() == ("bug", "chore", "docs", "security")
        assert "Return at most 10 finds" in text


# --- phase 4 -----------------------------------------------------------------


class TestStaleFlags:
    """A badge dated by git, and a lens that goes silent when git cannot date it."""

    SOURCE = (
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class Tip:\n"
        "    key: str\n"
        "    is_new: bool = False\n"
        "    is_beta: bool = False\n"
        "\n"
        "OLD = Tip('a', is_new=True)\n"
        "NEW = Tip('b', is_new=True)\n"
        "OFF = Tip('c', is_new=False)\n"
        "BETA = Tip('d', is_beta=True)\n"
    )

    def _run(self, monkeypatch, tmp_path, releases_by_line):
        _fake_repo(monkeypatch, tmp_path, {"src/tips.py": self.SOURCE}, "`src/tips.py`")
        monkeypatch.setattr(lens, "_introduced_at", lambda path, line: f"sha{line}")
        monkeypatch.setattr(lens, "_releases_containing", lambda sha: releases_by_line.get(sha, []))
        return lens.run("stale-flags", "demo", policy=POLICY, index=lens.Index())

    def test_a_flag_that_shipped_twice_is_found_and_a_fresh_one_is_not(self, monkeypatch, tmp_path):
        report = self._run(monkeypatch, tmp_path, {"sha9": ["v2.4.0", "v2.3.0"], "sha10": ["v2.4.0"]})
        assert [f["line"] for f in report["finds"]] == [9]
        assert "v2.3.0" in report["finds"][0]["evidence"]

    def test_a_line_git_cannot_date_is_never_a_find(self, monkeypatch, tmp_path):
        """The whole lens on a shallow clone, where `--contains` has no tags to answer with."""
        report = self._run(monkeypatch, tmp_path, {})
        assert report["finds"] == []

    def test_only_the_flags_the_policy_names(self, monkeypatch, tmp_path):
        """`is_beta` is a claim about whether output is verified, not about age.

        Clearing it on a timer would be a lie about the feature rather than a
        tidy-up, which is why it is deliberately absent from `flags`.
        """
        released = {f"sha{n}": ["v2.4.0", "v2.3.0"] for n in range(1, 20)}
        report = self._run(monkeypatch, tmp_path, released)
        assert [f["line"] for f in report["finds"]] == [9, 10]

    def test_the_real_tips_file_is_where_it_finds_them(self):
        """The seven `is_new=True` this lens was written for, against the real tree.

        Needs real history: the lens ages a flag by blaming its line and counting
        the release tags containing that commit. `actions/checkout` takes
        `fetch-depth: 1` and no tags, so in CI every flag blames to a grafted
        commit contained in zero releases and the lens correctly finds nothing —
        it errs toward silence by design. Asserting a count there asserts a fact
        about the checkout, not about the tree, so skip rather than pretend.
        `make ship-gate` cannot catch this class: locally the history is present.
        The aging logic itself is covered by the monkeypatched tests below.
        """
        if not lens._git("tag", "--list", "v*"):
            pytest.skip("shallow checkout with no release tags — the lens has nothing to age against")
        report = lens.run("stale-flags", "tui-ux", policy=POLICY, index=lens.Index())
        assert len(report["finds"]) + report["held"] >= 1
        assert all(f["file"].endswith("_tips.py") for f in report["finds"])

    def test_a_git_that_is_not_there_is_silence_and_not_a_crash(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(lens.subprocess, "run", _boom)
        assert lens._git("tag") == ""
        assert lens._releases_containing("deadbeef") == []

    def test_an_uncommitted_line_blames_to_zeros_and_counts_as_unreleased(self, monkeypatch):
        monkeypatch.setattr(lens, "_git", lambda *a: "0" * 40 + " 1 1 1\n")
        assert lens._introduced_at(REPO_ROOT / "README.md", 1) == ""

    def test_a_pre_release_tag_does_not_age_a_flag(self, monkeypatch):
        """`beta/*` is what the fleet published on its own; counting it would
        age a badge out on merges rather than on releases."""
        monkeypatch.setattr(lens, "_git", lambda *a: "beta/2.4.0rc7\nv2.3.0\nnot-a-tag\n")
        assert lens._releases_containing("abc") == ["v2.3.0"]


class TestDuplication:
    """Copy-paste, not similarity — and everything that is neither."""

    @staticmethod
    def _block(name: str, prefix: str) -> str:
        """A function long enough to clear both floors, with real vocabulary."""
        lines = [f"def {name}(payload, console, session, tracker, window):"]
        for n in range(10):
            lines.append(f"    {prefix}_value_{n} = payload.get('field_{n}') or session.lookup(tracker, {n})")
        lines.append("    return console.render(" + ", ".join(f"{prefix}_value_{n}" for n in range(10)) + ")")
        return "\n".join(lines) + "\n"

    def test_a_copied_block_is_one_find_naming_both_sites(self, monkeypatch, tmp_path):
        body = self._block("first", "a") + "\n\n" + self._block("second", "a").replace("def second", "def second")
        _fake_repo(monkeypatch, tmp_path, {"src/dup.py": body}, "`src/dup.py`")
        report = lens.run("duplication", "demo", policy=POLICY, index=lens.Index())
        assert len(report["finds"]) == 1, report["finds"]
        assert "repeats at" in report["finds"][0]["evidence"]

    def test_a_renamed_copy_is_not_a_clone(self, monkeypatch, tmp_path):
        """Identifiers compare exactly. A block someone adapted while copying is
        a false negative on purpose — this lens costs a proposal slot, so it has
        to be right the first time far more than the auto lenses do."""
        body = self._block("first", "a") + "\n\n" + self._block("second", "b")
        _fake_repo(monkeypatch, tmp_path, {"src/dup.py": body}, "`src/dup.py`")
        report = lens.run("duplication", "demo", policy=POLICY, index=lens.Index())
        assert report["finds"] == []

    def test_a_literal_table_does_not_duplicate_itself(self, monkeypatch, tmp_path):
        """`("a", "b", "c"), ("d", "e", "f"), …` normalises to a periodic run and
        matches itself at every period. This was the first version's worst false
        positive and it looked exactly like a real find."""
        rows = "\n".join(f'    ("row_{n}", "label {n}", "owner {n}"),' for n in range(60))
        _fake_repo(monkeypatch, tmp_path, {"src/seed.py": f"SEED = [\n{rows}\n]\n"}, "`src/seed.py`")
        report = lens.run("duplication", "demo", policy=POLICY, index=lens.Index())
        assert report["finds"] == []

    def test_it_proposes_and_never_fixes(self):
        assert LENSES["duplication"]["lane"] == "propose"

    def test_tests_are_out_of_scope_by_default(self, monkeypatch, tmp_path):
        """Tests repeat their setup on purpose, and turning this on for one
        charter is a policy edit somebody has to argue for."""
        body = self._block("first", "a") + "\n\n" + self._block("second", "a")
        _fake_repo(monkeypatch, tmp_path, {"tests/unit/test_dup.py": body}, "`tests/unit/test_dup.py`")
        report = lens.run("duplication", "demo", policy=POLICY, index=lens.Index())
        assert report["finds"] == []

    def test_a_file_that_will_not_tokenise_is_skipped_rather_than_fatal(self, tmp_path):
        broken = tmp_path / "broken.py"
        broken.write_text("def f(:\n    'unclosed\n")
        assert lens._token_stream(broken) == []

    def test_the_real_control_charter_stays_silent(self):
        """Retro, 2.5k LOC, fortnightly — the charter the rollout uses to tell a
        detector from a work generator."""
        assert lens.run("duplication", "retro", policy=POLICY, index=lens.Index())["finds"] == []


class TestCrashFuzz:
    """Whose crash it is. The run is wide; the find is narrow."""

    @staticmethod
    def _result(frames, *, verdict="traceback", exception="ValueError: nope", seed=3):
        return {
            "seed": seed,
            "steps": 120,
            "keys_sent": 118,
            "verdict": verdict,
            "returncode": 1,
            "keys": ["\\r", "j"],
            "frames": frames,
            "exception": exception,
            "excerpt": "",
        }

    def _run(self, monkeypatch, workstream, results):
        monkeypatch.setattr(lens, "_fuzz_runner", lambda settings: results)
        return lens.run("crash-fuzz", workstream, policy=POLICY, index=lens.Index())

    def test_a_crash_in_your_own_paths_is_yours(self, monkeypatch):
        frames = [("src/yeaboi/cli.py", 10, "main"), ("src/yeaboi/ui/shared/_tips.py", 42, "current_tip")]
        report = self._run(monkeypatch, "tui-ux", [self._result(frames)])
        assert len(report["finds"]) == 1
        assert report["finds"][0]["file"] == "src/yeaboi/ui/shared/_tips.py"
        assert "--seed 3" in report["finds"][0]["evidence"]

    def test_a_crash_in_somebody_elses_paths_is_reported_and_not_filed(self, monkeypatch):
        """Which is why both charters run this lens rather than one running it
        for everybody: a fuzzer cannot be confined to one charter's screens, so
        the run is wide and the find is narrow."""
        frames = [("src/yeaboi/agent/nodes.py", 99, "plan_node")]
        report = self._run(monkeypatch, "tui-ux", [self._result(frames)])
        assert report["finds"] == []
        assert report["skipped"] == [{"symbol": "seed 3", "file": "src/yeaboi/agent/nodes.py", "rule": "outside-owns"}]

    def test_the_charter_that_owns_the_file_does_find_it(self, monkeypatch):
        frames = [("src/yeaboi/agent/nodes.py", 99, "plan_node")]
        report = self._run(monkeypatch, "planning", [self._result(frames)])
        assert [f["file"] for f in report["finds"]] == ["src/yeaboi/agent/nodes.py"]

    def test_a_closed_network_is_containment_working_not_a_bug(self, monkeypatch):
        frames = [("src/yeaboi/agent/llm.py", 7, "_llm_invoke")]
        report = self._run(
            monkeypatch, "planning", [self._result(frames, exception="APIConnectionError: Connection error.")]
        )
        assert report["finds"] == []
        assert report["skipped"][0]["rule"] == "network-unreachable"

    def test_a_run_that_found_nothing_reports_nothing(self, monkeypatch):
        report = self._run(monkeypatch, "tui-ux", [self._result([], verdict="ok")])
        assert report["finds"] == [] and report["skipped"] == []

    def test_a_crash_with_no_frame_of_ours_is_reported_rather_than_dropped(self, monkeypatch):
        report = self._run(monkeypatch, "tui-ux", [self._result([])])
        assert report["skipped"][0]["rule"] == "no-frame-in-our-tree"

    def test_a_hang_says_so_in_the_evidence(self, monkeypatch):
        """The lane is a ceiling for both verdicts, and the scout proposes a
        hang: there is no mechanical regression test for "it stopped
        repainting", so somebody has to read it."""
        frames = [("src/yeaboi/ui/shared/_tips.py", 42, "current_tip")]
        report = self._run(monkeypatch, "tui-ux", [self._result(frames, verdict="hang", exception="")])
        assert "hang" in report["finds"][0]["evidence"]
        assert report["finds"][0]["symbol"].startswith("hang@seed-")

    @pytest.mark.parametrize("workstream", ["platform", "web-ux", "standup", "retro"])
    def test_a_charter_with_no_screens_is_never_offered_it(self, monkeypatch, workstream):
        """A misconfiguration to read in the output, not a reason to fail a sweep
        that still has thirteen other things to do."""
        report = self._run(monkeypatch, workstream, [self._result([("src/yeaboi/cli.py", 1, "main")])])
        assert report["finds"] == []
        assert report["skipped"][0]["rule"] == "lens-not-offered-here"

    def test_it_is_the_one_lens_that_returns_a_bug(self):
        assert LENSES["crash-fuzz"]["type"] == "bug"
        assert all(LENSES[name]["type"] == "chore" for name in LENSES if name != "crash-fuzz")


class TestTheFuzzerItself:
    """The pure half. Nothing here spawns a pty."""

    def test_a_seed_reproduces_its_key_sequence_exactly(self):
        """The seed *is* the regression test, which is the whole reason a crash
        found this way can clear the auto lane at all."""
        import tui_fuzz

        assert tui_fuzz.key_sequence(41, 200) == tui_fuzz.key_sequence(41, 200)
        assert tui_fuzz.key_sequence(41, 200) != tui_fuzz.key_sequence(42, 200)

    def test_the_alphabet_reaches_past_the_first_screen(self):
        """Uniform random bytes quit on the first `q` and re-test the one path
        the smoke test already covers. Navigation has to dominate."""
        import tui_fuzz

        keys = tui_fuzz.key_sequence(7, 400)
        navigation = (b"\x1b[A", b"\x1b[B", b"\x1b[C", b"\x1b[D", b"\r", b"\t", b"\x1b[5~", b"\x1b[6~")
        assert sum(keys.count(k) for k in navigation) > len(keys) * 0.3
        assert keys.count(b"q") < len(keys) * 0.03

    def test_only_frames_in_our_own_tree_survive(self):
        import tui_fuzz

        text = (
            'File "/usr/lib/python3.11/selectors.py", line 12, in select\n'
            f'File "{REPO_ROOT}/src/yeaboi/ui/shared/_tips.py", line 42, in current_tip\n'
        )
        assert tui_fuzz.traceback_frames(text) == [("src/yeaboi/ui/shared/_tips.py", 42, "current_tip")]

    def test_the_exception_line_closing_the_last_traceback_is_the_one_reported(self):
        import tui_fuzz

        text = (
            "Traceback (most recent call last):\nValueError: first\n"
            "Traceback (most recent call last):\n  File \"x\", line 1, in f\nKeyError: 'second'\n"
        )
        assert tui_fuzz.exception_line(text) == "KeyError: 'second'"

    def test_a_run_with_no_traceback_names_no_exception(self):
        import tui_fuzz

        assert tui_fuzz.exception_line("all fine here") == ""

    def test_the_pty_tail_is_bounded(self):
        """A 60 FPS screen paints tens of megabytes; `buf += chunk` over that is
        quadratic and wedges the fuzzer long before it wedges the app."""
        import tui_fuzz

        tail = tui_fuzz._Tail()
        for _ in range(40):
            tail.feed(b"x" * 100_000)
        assert tail.total == 4_000_000
        assert len(tail.text()) <= tui_fuzz._TAIL_BYTES

    def test_the_child_cannot_reach_the_network_or_the_operators_home(self, tmp_path):
        import tui_fuzz

        env = tui_fuzz._child_env(tmp_path)
        assert env["HOME"] == str(tmp_path)
        assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == "http://127.0.0.1:9"
        assert env["YEABOI_NO_TUNNEL"] == "1"
        assert "YEABOI_HOME" not in env
        assert env["PATH"].startswith(str(tmp_path / "shims"))

    def test_windows_says_so_and_exits_quietly(self, monkeypatch, capsys):
        import tui_fuzz

        monkeypatch.setattr(tui_fuzz.sys, "platform", "win32")
        assert tui_fuzz.main(["--seeds", "1"]) == 0
        assert "POSIX only" in capsys.readouterr().err


class TestABoundaryEveryoneRuns:
    """`applies_to: "*"` is survey-narrow working, not an exception to it."""

    def test_a_repo_wide_invariant_is_scanned_inside_the_surveying_charter(self, monkeypatch, tmp_path):
        _fake_repo(
            monkeypatch,
            tmp_path,
            {"src/mine.py": "import x\nd = Path.home() / '.yeaboi'\n"},
            "`src/mine.py`",
        )
        report = lens.run("layering", "demo", policy=POLICY, index=lens.Index())
        assert [f["symbol"] for f in report["finds"]] == ["paths-through-paths-py"]

    def test_the_crossing_in_the_tui_is_the_tuis_find_and_not_platforms(self):
        """Declared by platform, which owns `paths.py`; broken in `ui/`, which is
        tui-ux's. Platform's own run going quiet is the invariant working."""
        theirs = lens.run("layering", "tui-ux", policy=POLICY, index=lens.Index())
        mine = lens.run("layering", "platform", policy=POLICY, index=lens.Index())
        assert [f["file"] for f in theirs["finds"]] == ["src/yeaboi/ui/mode_select/__init__.py"]
        assert mine["finds"] == []

    def test_a_scoped_invariant_leaves_the_test_suite_alone(self, monkeypatch, tmp_path):
        """`test_paths.py` asserting what `DEFAULT_ROOT_DIR` equals is the test
        doing its job, not the convention drifting."""
        _fake_repo(
            monkeypatch,
            tmp_path,
            {"tests/unit/test_mine.py": "d = Path.home() / '.yeaboi'\n"},
            "`tests/unit/test_mine.py`",
        )
        assert lens.run("layering", "demo", policy=POLICY, index=lens.Index())["finds"] == []

    def test_the_web_boundaries_hold_across_every_charter_today(self):
        """Three invariants that say nothing, which is what a boundary guard
        looks like when the boundary is intact."""
        declared = {"static-through-assets", "headers-through-security", "chrome-through-brand"}
        for workstream in sorted(cowork_setup.parse_workstreams()):
            found = {f["symbol"] for f in lens.run("layering", workstream, policy=POLICY, index=lens.Index())["finds"]}
            assert not (found & declared), f"{workstream} crosses {found & declared}"

    def test_every_repo_wide_invariant_names_a_declaring_workstream(self):
        """`applies_to` says who runs it; `workstream` still says who wrote it
        down, so a false positive has somebody to argue with."""
        for invariant in LENSES["layering"]["invariants"]:
            assert invariant["workstream"] in cowork_setup.parse_workstreams()
            assert invariant.get("scope", "all") in ("all", "src", "tests")


class TestThePhaseFourWiring:
    def test_every_sweep_now_runs_a_lens(self):
        """Three charters piloted them; thirteen run them."""
        blocks = TestTheWiring()._lens_blocks()
        sweeps = {p.name for p in TestTheWiring.ROUTINES.glob("*-sweep.md")}
        assert set(blocks) == sweeps, sweeps - set(blocks)

    def test_the_two_lenses_that_need_a_screen_are_only_offered_where_there_is_one(self):
        blocks = TestTheWiring()._lens_blocks()
        for routine, names in blocks.items():
            if "crash-fuzz" not in names:
                continue
            workstream = routine[: -len("-sweep.md")]
            assert workstream in LENSES["crash-fuzz"]["workstreams"], routine

    def test_every_workstream_the_crash_lens_names_actually_runs_it(self):
        blocks = TestTheWiring()._lens_blocks()
        for workstream in LENSES["crash-fuzz"]["workstreams"]:
            assert "crash-fuzz" in blocks.get(f"{workstream}-sweep.md", []), workstream

    def test_the_catalogue_documents_every_lens_that_exists(self):
        text = (REPO_ROOT / "cowork" / "hygiene-lenses.md").read_text()
        for name in lens.LENSES:
            assert f"`{name}`" in text, f"{name} is implemented and undocumented"

    def test_the_fuzzer_is_reachable_from_the_charter_that_runs_it(self):
        assert "tui_fuzz" in (REPO_ROOT / "cowork" / "hygiene-lenses.md").read_text()

    def test_the_whole_session_is_killed_and_not_just_the_process(self, monkeypatch):
        """`start_new_session=True` is what gives the child a controlling
        terminal, and it is also what makes `proc.kill()` insufficient: the
        group survives, is reparented to init, and repaints a screen nobody is
        reading at 100% of a core forever. One of those was left behind on a
        real machine by a `Ctrl-C` before this existed.
        """
        import tui_fuzz

        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(tui_fuzz.os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(tui_fuzz.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

        class _Proc:
            pid = 4242
            returncode = None

            def poll(self):
                return None

            def kill(self):
                killed.append(("kill", 0))

            def wait(self, timeout=None):
                return 0

        tui_fuzz._terminate(_Proc())
        assert killed == [(4242, tui_fuzz.signal.SIGKILL)]

    def test_a_faulthandler_dump_is_read_deepest_last(self):
        """The only way a hang becomes a find.

        A traceback carries frames and a hang carries none, so before the
        `SIGABRT` dump every hang reported as "no frame in our tree" and was
        quietly dropped — the lens could see the failure and never say whose it
        was. The two formats disagree twice over: `line 12 in f` against
        `line 12, in f`, and most-recent-*first* against most-recent-last.
        """
        import tui_fuzz

        text = (
            "Fatal Python error: Aborted\n\n"
            "Current thread 0x01 (most recent call first):\n"
            f'  File "{REPO_ROOT}/src/yeaboi/ui/mode_select/__init__.py", line 11915 in _sweep_menu_in\n'
            f'  File "{REPO_ROOT}/src/yeaboi/ui/mode_select/__init__.py", line 12338 in select_mode\n'
        )
        frames = tui_fuzz.traceback_frames(text)
        assert frames[-1] == ("src/yeaboi/ui/mode_select/__init__.py", 11915, "_sweep_menu_in")

    def test_a_traceback_is_read_deepest_last_too(self):
        import tui_fuzz

        text = (
            "Traceback (most recent call last):\n"
            f'  File "{REPO_ROOT}/src/yeaboi/cli.py", line 1, in main\n'
            f'  File "{REPO_ROOT}/src/yeaboi/ui/shared/_tips.py", line 42, in current_tip\n'
        )
        assert tui_fuzz.traceback_frames(text)[-1][2] == "current_tip"

    def test_the_child_dumps_its_stacks_when_asked(self, tmp_path):
        import tui_fuzz

        assert tui_fuzz._child_env(tmp_path)["PYTHONFAULTHANDLER"] == "1"

    def test_the_dump_signal_goes_to_the_process_and_not_the_group(self, monkeypatch):
        """One interpreter's stacks, not an abort of every child it started."""
        import tui_fuzz

        sent = []

        class _Proc:
            def send_signal(self, sig):
                sent.append(sig)

            def poll(self):
                return None

        monkeypatch.setattr(tui_fuzz, "_drain", lambda *a, **k: 0)
        monkeypatch.setattr(tui_fuzz.os, "killpg", lambda *a: pytest.fail("aborted the whole group"))
        tui_fuzz._dump_stacks(_Proc(), 0, tui_fuzz._Tail())
        assert sent == [tui_fuzz.signal.SIGABRT]
