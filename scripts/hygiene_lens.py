#!/usr/bin/env python3
"""Mechanical hygiene detectors, scoped to one cowork workstream's paths.

A cowork sweep surveys one charter and nothing else. That is what makes the
fleet reviewable, and it is also why the fleet has never found dead code: you
cannot prove a symbol is unused by reading a tenth of the repository. The rule
that resolves it, stated once in `cowork/hygiene-lenses.md` and implemented
here:

    **Survey narrow, confirm wide, change narrow.**

A lens *finds* only inside the charter's ``**Owns**`` paths. To *confirm* a
find it reads the whole repository — proving a negative is a read, and a read
changes nothing about who may edit. What stays scoped is the find itself: the
symbol lives in your paths, and only your builder may delete it.

Three lenses, all deterministic:

``dead-code``
    Module-level functions and classes whose name occurs nowhere else in the
    repository — Python, Go, TypeScript, markdown, YAML and JSON alike.
``assertion-free-tests``
    ``test_*`` functions with no ``assert``, no ``pytest.raises``, and no call
    to a recognised assertion helper.
``layering``
    A pattern a charter has declared must not appear inside its own paths,
    together with the helper that should be there instead.

**Every heuristic here errs toward finding nothing.** The dead-code scan counts
bare identifier tokens rather than resolving call graphs, so a symbol mentioned
in a comment, a string, a Go header or a docs page reads as live. That produces
false negatives freely and false positives almost never, which is the only
direction that is safe: a missed deletion costs nothing, and a wrong one merges
green and takes a capability with it.

The exclusions live in ``.github/hygiene/lens-policy.yml``, each with the
reason it exists. This file is the comparison; that file is the judgement.

Usage::

    uv run python scripts/hygiene_lens.py --lens dead-code --workstream tui-ux
    uv run python scripts/hygiene_lens.py --lens layering --workstream platform --json
    uv run python scripts/hygiene_lens.py --paths tui-ux    # audit the resolver
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tokenize
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSTREAMS_DIR = REPO_ROOT / "cowork" / "workstreams"
POLICY_PATH = REPO_ROOT / ".github" / "hygiene" / "lens-policy.yml"

# --- charter paths -----------------------------------------------------------
#
# A charter's `**Owns**` block is prose with the paths in backticks. It is read
# rather than duplicated into a registry for the same reason `parse_tiers` reads
# `models.md`: the charter is what a scout is handed at run time, so a second
# list of paths is a description of a fleet that may no longer exist.

_OWNS = re.compile(r"^\*\*Owns\*\*(.*?)(?:\n\s*\n|\Z)", re.MULTILINE | re.DOTALL)
# `**except `ui/session/`**`, `**except `access.py` and `gate.py`**` — the two
# spellings in use. Whatever is ticked inside the span is subtracted.
_EXCEPT = re.compile(r"\*\*except\b(.*?)\*\*", re.DOTALL)
_TICKED = re.compile(r"`([^`]+)`")
_BRACE = re.compile(r"\{([^{}]*)\}")


def _expand_braces(token: str) -> list[str]:
    """`test_{a,b}_*.py` → two tokens. Charters use this shorthand for test globs."""
    match = _BRACE.search(token)
    if not match:
        return [token]
    out: list[str] = []
    for alt in match.group(1).split(","):
        out.extend(_expand_braces(token[: match.start()] + alt.strip() + token[match.end() :]))
    return out


def _resolve(token: str) -> tuple[list[Path], str]:
    """One backticked token → the paths it names, or a reason it named none.

    Tried against the four roots charters actually write relative to, in that
    order, because a single sentence mixes them (`tests/unit/…`,
    `mcp/tools_poker.py` and `scripts/dev_board.py` are all charter tokens). A
    package-wide search for a bare basename only counts **when it is
    unambiguous**: `server.py` exists in three modes and `__init__.py` in
    thirty, and treating either as a claim would hand one workstream another's
    files.

    Leading dots are never stripped. `.github/workflows/` is a path, and the
    strip that tidies a trailing comma out of prose turned it into
    `github/workflows` — silently dropping every workflow file out of
    **platform** and every CodeQL file out of **security**.
    """
    token = token.strip().rstrip(",.")
    if not token or " " in token or "\n" in token:
        return [], "not a path"
    # `_INTAKE_CARDS`, `usage_get`, `yeaboi-core` — a charter ticks identifiers
    # and mode-card names in the same sentence as its paths. Neither a slash
    # nor a dot means it never was one.
    if "/" not in token and "." not in token:
        return [], "not a path"

    globby = any(ch in token for ch in "*?[")
    for prefix in ("", "src/yeaboi/", "scripts/", "tests/unit/"):
        pattern = prefix + token
        hits = sorted(REPO_ROOT.glob(pattern)) if globby else [REPO_ROOT / pattern]
        hits = [h for h in hits if h.exists()]
        if hits:
            return hits, ""

    if "/" not in token and token.endswith(".py"):
        deep = sorted((REPO_ROOT / "src" / "yeaboi").rglob(token))
        if len(deep) == 1:
            return deep, ""
        if len(deep) > 1:
            return [], f"ambiguous — {len(deep)} files named {token}"
    return [], "no such path"


@dataclass(frozen=True)
class Charter:
    """One workstream's declared surface, resolved to real paths on disk."""

    workstream: str
    owns: tuple[Path, ...]
    excludes: tuple[Path, ...]
    unresolved: tuple[tuple[str, str], ...] = ()

    def covers(self, path: Path) -> bool:
        if any(path == ex or ex in path.parents for ex in self.excludes):
            return False
        return any(path == own or own in path.parents for own in self.owns)


def charter(workstream: str) -> Charter:
    path = WORKSTREAMS_DIR / f"{workstream}.md"
    if not path.is_file():
        raise SystemExit(f"no charter: {path.relative_to(REPO_ROOT)}")
    block = _OWNS.search(path.read_text(encoding="utf-8"))
    if not block:
        raise SystemExit(f"{workstream}.md has no **Owns** block")
    text = block.group(1)

    excluded_tokens: list[str] = []
    for span in _EXCEPT.finditer(text):
        excluded_tokens.extend(_TICKED.findall(span.group(1)))
    owns_text = _EXCEPT.sub(" ", text)

    owns: list[Path] = []
    excludes: list[Path] = []
    unresolved: list[tuple[str, str]] = []
    for raw, sink in ((owns_text, owns), (" ".join(f"`{t}`" for t in excluded_tokens), excludes)):
        for ticked in _TICKED.findall(raw):
            for token in _expand_braces(ticked):
                hits, why = _resolve(token)
                if hits:
                    sink.extend(hits)
                elif why != "not a path":
                    unresolved.append((token, why))

    return Charter(
        workstream=workstream,
        owns=tuple(sorted(set(owns))),
        excludes=tuple(sorted(set(excludes))),
        unresolved=tuple(sorted(set(unresolved))),
    )


def python_files(spec: Charter, *, tests: bool) -> list[Path]:
    """Every `.py` file the charter covers, on one side of the tests boundary.

    Split rather than filtered by the caller because the two lenses that read
    Python want opposite halves, and a `test_*` function is unreferenced by
    design — feeding one to the dead-code scan would flag the entire suite.
    """
    found: set[Path] = set()
    for own in spec.owns:
        candidates = [own] if own.is_file() else sorted(own.rglob("*.py"))
        for path in candidates:
            if path.suffix != ".py" or "__pycache__" in path.parts:
                continue
            if (path.relative_to(REPO_ROOT).parts[0] == "tests") != tests:
                continue
            if spec.covers(path):
                found.add(path)
    return sorted(found)


# --- the repo-wide reference index -------------------------------------------
#
# The "confirm wide" half. One pass over every text file in the repository,
# producing two counts per identifier: how often it appears at all, and how
# often it appears as a `def`/`class` name. A symbol is a dead-code candidate
# when those two are equal — every occurrence of the name in the entire
# repository is a definition of it.

_SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        "htmlcov",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
)
_SCAN_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".go",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".css",
        ".md",
        ".yml",
        ".yaml",
        ".toml",
        ".json",
        ".jsonl",
        ".html",
        ".txt",
        ".cfg",
        ".ini",
        ".sh",
        ".mod",
        ".sum",
        "",
    }
)
# Skipped by prefix rather than by directory name, because `.claude/` itself is
# repo content a symbol can be referenced from — an agent file, a skill, a slash
# command. Only the worktrees under it are copies of this same tree, and counting
# a copy's definitions as references is how a live symbol reads as dead.
_SKIP_PREFIXES = (".claude/worktrees",)

# Phrases a test uses to say "the assertion is that nothing was raised". Filled
# from the policy on every run; the default is empty so a caller that hands in a
# policy without them gets the strict reading rather than a silent allowance.
_INTENT_PHRASES: tuple[str, ...] = ()

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DEFSITE = re.compile(r"\b(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class Index:
    words: Counter = field(default_factory=Counter)
    definitions: Counter = field(default_factory=Counter)

    def referenced_elsewhere(self, name: str) -> bool:
        """True unless every occurrence of `name` in the repo is a definition of it."""
        return self.words[name] > self.definitions[name]


def build_index(root: Path | None = None) -> Index:
    index = Index()
    root = root or REPO_ROOT
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if _SKIP_DIRS.intersection(relative.parts[:-1]):
            continue
        if relative.as_posix().startswith(_SKIP_PREFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        index.words.update(_WORD.findall(text))
        index.definitions.update(_DEFSITE.findall(text))
    return index


# --- policy ------------------------------------------------------------------


def load_policy(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or POLICY_PATH).read_text(encoding="utf-8"))


def _decorator_name(node: ast.expr) -> str:
    """`@pytest.mark.skip` → `pytest.mark.skip`; `@tool` → `tool`; `@app.route(...)` → `app.route`."""
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _matches(rule: dict, *, path: Path, name: str, decorators: list[str], bases: list[str]) -> bool:
    """Does one exclusion entry cover this candidate?"""
    relative = path.relative_to(REPO_ROOT).as_posix()
    if any(relative == p or relative.startswith(p.rstrip("/") + "/") for p in rule.get("paths", ())):
        return True
    if name in rule.get("names", ()):
        return True
    if any(re.search(pattern, name) for pattern in rule.get("name_patterns", ())):
        return True
    if any(b in rule.get("base_classes", ()) for b in bases):
        return True
    return any(d in rule.get("decorators", ()) for d in decorators)


def _excluded(rules: list[dict], **candidate: Any) -> str:
    for rule in rules:
        if _matches(rule, **candidate):
            return str(rule["id"])
    return ""


# --- the lenses --------------------------------------------------------------


@dataclass(frozen=True)
class Find:
    symbol: str
    file: str
    line: int
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "file": self.file, "line": self.line, "evidence": self.evidence}


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# --- the one seam that leaves the process ------------------------------------


def _git(*args: str) -> str:
    """Every git read this module makes, in one place, so a test can replace it.

    Named for the same reason ``_gh_transport.py`` names ``_run``: a lens that
    shells out somewhere in its middle is one a test can only stub by accident.
    Failure returns the empty string rather than raising — a shallow clone, a
    detached worktree or a git that is not there at all must make a lens quiet,
    never make it wrong. Every caller below reads emptiness as "cannot tell",
    which is the direction that finds nothing.
    """
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv, no shell, repo-local
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _introduced_at(path: Path, line: int) -> str:
    """The commit that last wrote this line, or "" when git cannot say."""
    out = _git("blame", "-L", f"{line},{line}", "--porcelain", "--", _relative(path))
    sha = out.split(" ", 1)[0].strip() if out else ""
    # An uncommitted line blames to forty zeros. It is in no release by
    # definition, and reading it as "released long ago" would flag work that
    # has not shipped once.
    return "" if not sha or set(sha) == {"0"} else sha


def _releases_containing(sha: str) -> list[str]:
    """The `vX.Y.Z` tags containing a commit, newest first.

    `beta/*` tags are deliberately not counted: a pre-release is what the fleet
    published on its own, and counting them would age a flag out on merges
    rather than on releases.
    """
    if not sha:
        return []
    out = _git("tag", "--contains", sha, "--sort=-v:refname")
    return [t.strip() for t in out.splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", t.strip())]


def dead_code(spec: Charter, policy: dict, index: Index) -> tuple[list[Find], list[dict]]:
    rules = policy["excludes"]
    finds: list[Find] = []
    skipped: list[dict] = []
    for path in python_files(spec, tests=False):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            decorators = [_decorator_name(d) for d in node.decorator_list]
            bases = [_decorator_name(b) for b in getattr(node, "bases", [])]
            rule = _excluded(rules, path=path, name=node.name, decorators=decorators, bases=bases)
            if rule:
                skipped.append({"symbol": node.name, "file": _relative(path), "rule": rule})
                continue
            if index.referenced_elsewhere(node.name):
                continue
            finds.append(
                Find(
                    symbol=node.name,
                    file=_relative(path),
                    line=node.lineno,
                    evidence=(
                        f"{_relative(path)}:{node.lineno} defines `{node.name}`; "
                        f"the name occurs {index.words[node.name]} time(s) in the repository, "
                        f"all {index.definitions[node.name]} of them definitions"
                    ),
                )
            )
    return finds, skipped


def _asserts(node: ast.AST, helpers: frozenset[str], source: str) -> bool:
    """Does this test assert anything — by statement, by helper, or by intent?

    Three ways, because the repo genuinely uses all three. The leading
    underscore on ``_assert_match`` is stripped before the prefix test: every
    parity test in ``tests/parity/`` delegates to exactly such a helper, and
    reading twelve of them as assertion-free was this detector's largest false
    positive class by an order of magnitude.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        # A trap: a local stub monkeypatched over the thing that must not be
        # called, raising `AssertionError` if it ever is. The assertion is the
        # raise, and it is as real as a statement — several of the strongest
        # tests in the suite are written this way and no other shape can say
        # "this code path must never be reached".
        if isinstance(child, ast.Raise) and "AssertionError" in ast.dump(child):
            return True
        if isinstance(child, ast.Call):
            name = _decorator_name(child.func).rsplit(".", 1)[-1].lstrip("_")
            if name.startswith("assert") or name in helpers:
                return True
    # Sliced by whole lines rather than by `ast.get_source_segment`, which ends
    # at the node's last column and so cuts off the trailing comment on the
    # final statement — which is exactly where `# must not raise` is written.
    body = "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
    return any(phrase in body for phrase in _INTENT_PHRASES)


def assertion_free_tests(spec: Charter, policy: dict, index: Index) -> tuple[list[Find], list[dict]]:
    del index  # this lens confirms nothing outside the file it reads
    rules = policy["excludes"]
    helpers = frozenset(policy.get("assertion_helpers", ()))
    global _INTENT_PHRASES
    _INTENT_PHRASES = tuple(policy.get("intent_comments", ()))
    finds: list[Find] = []
    skipped: list[dict] = []
    for path in python_files(spec, tests=True):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            decorators = [_decorator_name(d) for d in node.decorator_list]
            rule = _excluded(rules, path=path, name=node.name, decorators=decorators, bases=[])
            if rule:
                skipped.append({"symbol": node.name, "file": _relative(path), "rule": rule})
                continue
            if _asserts(node, helpers, source):
                continue
            finds.append(
                Find(
                    symbol=node.name,
                    file=_relative(path),
                    line=node.lineno,
                    evidence=(
                        f"{_relative(path)}:{node.lineno} — `{node.name}` contains no assert "
                        f"and calls no assertion helper"
                    ),
                )
            )
    return finds, skipped


def layering(spec: Charter, policy: dict, index: Index) -> tuple[list[Find], list[dict]]:
    """Boundaries, checked inside the paths of whoever is surveying.

    An invariant names one `workstream` when only that charter's own files can
    break it, and `applies_to: "*"` when any of them can. The second sounds
    like it breaks survey-narrow and does the opposite: the scan still reads
    only `spec`'s files, so a repo-wide boundary crossed in `standup/` is
    **standup's** find and web-ux never sees it. That is the rule working
    rather than an exception to it — the charter that owns a boundary writes it
    down once, and each charter answers for its own side of the line.
    """
    del index
    mine = [
        i for i in policy.get("invariants", ()) if i.get("applies_to") == "*" or i.get("workstream") == spec.workstream
    ]
    finds: list[Find] = []
    for invariant in mine:
        pattern = re.compile(invariant["forbid"], re.MULTILINE)
        exempt = tuple(invariant.get("exempt", ()))
        # `scope: src` keeps a boundary out of the test suite, which is where a
        # rule is legitimately spelled out in full: `test_paths.py` asserting
        # what `DEFAULT_ROOT_DIR` equals is the test doing its job, not the
        # convention drifting.
        scope = invariant.get("scope", "all")
        candidates = (
            python_files(spec, tests=False)
            if scope == "src"
            else (
                python_files(spec, tests=True)
                if scope == "tests"
                else python_files(spec, tests=False) + python_files(spec, tests=True)
            )
        )
        for path in candidates:
            if _relative(path) in exempt:
                continue
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                # `# lens-exempt: <id> — why` waives one *site*, where `exempt:`
                # in the policy waives a whole file. A boundary usually has one
                # or two deliberate crossings and each has a reason; recording
                # it beside the code keeps the rest of the file live, which
                # exempting the file would not.
                if f"lens-exempt: {invariant['id']}" in lines[line - 1]:
                    continue
                finds.append(
                    Find(
                        symbol=invariant["id"],
                        file=_relative(path),
                        line=line,
                        evidence=(
                            f"{_relative(path)}:{line} matches `{invariant['id']}` "
                            f"({match.group(0).strip()!r}); use {invariant['instead']}"
                        ),
                    )
                )
    return finds, []


def stale_flags(spec: Charter, policy: dict, index: Index) -> tuple[list[Find], list[dict]]:
    """A `NEW` badge still on a feature that shipped two releases ago.

    `tui-ux.md` has listed this informally since it was written — "every
    `is_new=True` flag set more than two releases ago should be cleared" — with
    nothing able to answer *when* it was set. Git can: blame the line, count the
    `v*` tags that contain the commit.

    The whole lens goes quiet on a shallow clone, because `--contains` has no
    tags to answer with there. That is the safe direction and the deliberate
    one: a badge left on too long is a papercut, and a badge cleared off a
    feature that shipped yesterday is the fleet undoing someone's work.
    """
    del index
    rules = policy["excludes"]
    keywords = frozenset(policy.get("flags", ()))
    threshold = int(policy.get("releases_stale", 2))
    finds: list[Find] = []
    skipped: list[dict] = []
    for path in python_files(spec, tests=False):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg not in keywords:
                continue
            if not (isinstance(node.value, ast.Constant) and node.value.value is True):
                continue
            line = node.value.lineno
            rule = _excluded(rules, path=path, name=node.arg, decorators=[], bases=[])
            if rule:
                skipped.append({"symbol": f"{node.arg}@{line}", "file": _relative(path), "rule": rule})
                continue
            releases = _releases_containing(_introduced_at(path, line))
            if len(releases) < threshold:
                continue
            finds.append(
                Find(
                    symbol=f"{node.arg}@{line}",
                    file=_relative(path),
                    line=line,
                    evidence=(
                        f"{_relative(path)}:{line} sets `{node.arg}=True`; it first shipped in "
                        f"{releases[-1]} and has been in {len(releases)} releases since"
                    ),
                )
            )
    return finds, skipped


# --- duplication -------------------------------------------------------------
#
# Copy-paste detection over a token stream, deliberately *not* a similarity
# score. Identifiers are compared exactly, so a block someone renamed while
# adapting it does not match — that is a false negative on purpose. This lens
# proposes rather than fixes, and a proposal costs one of a workstream's two
# slots, so it has to be right the first time far more than the auto lenses do.
#
# It reads only the surveying charter's own files, which is what makes the
# Go-parity problem impossible rather than excluded: `standup/aggregate.py` and
# `go/internal/standup/aggregate.go` are not both Python, and two charters'
# files never meet in one scan.

_CLONE_SKIP = frozenset({tokenize.COMMENT, tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER})


def _token_stream(path: Path) -> list[tuple[str, int]]:
    """(normalised token, line) for one file, or [] if it will not tokenise.

    Numbers and strings collapse to a placeholder — two blocks differing only
    in a literal are still the same copied block — while every name and
    operator is kept verbatim. `NEWLINE`/`INDENT`/`DEDENT` are kept as
    structural markers so a window cannot straddle a block boundary and match
    something that does not look like it on the page.
    """
    stream: list[tuple[str, int]] = []
    try:
        with path.open("rb") as fh:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type in _CLONE_SKIP:
                    continue
                if tok.type == tokenize.NUMBER:
                    stream.append(("#num", tok.start[0]))
                elif tok.type == tokenize.STRING:
                    stream.append(("#str", tok.start[0]))
                else:
                    stream.append((tok.string, tok.start[0]))
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError, OSError):
        return []
    return stream


def duplication(spec: Charter, policy: dict, index: Index) -> tuple[list[Find], list[dict]]:
    del index
    rules = policy["excludes"]
    window = int(policy.get("min_tokens", 70))
    min_lines = int(policy.get("min_lines", 8))
    min_distinct = int(policy.get("min_distinct_tokens", 20))
    include_tests = bool(policy.get("include_tests", False))

    paths = python_files(spec, tests=False) + (python_files(spec, tests=True) if include_tests else [])
    skipped: list[dict] = []
    streams: dict[str, list[tuple[str, int]]] = {}
    windows: dict[bytes, list[tuple[str, int]]] = {}
    for path in paths:
        rule = _excluded(rules, path=path, name=path.name, decorators=[], bases=[])
        if rule:
            skipped.append({"symbol": path.name, "file": _relative(path), "rule": rule})
            continue
        stream = _token_stream(path)
        rel = _relative(path)
        streams[rel] = stream
        for start in range(0, max(0, len(stream) - window + 1)):
            chunk = [tok for tok, _ in stream[start : start + window]]
            # A literal table — `("a", "b", "c"), ("d", "e", "f"), …` — collapses
            # to a periodic run of `( #str , #str , #str ) ,` and matches itself
            # at every period, which is how the first version of this lens
            # reported `SEED` in `dev_board.py` as duplicating `SEED`. Requiring
            # the window to contain real vocabulary is what separates copied
            # code from a long list of strings, and it is a floor on how much
            # the window has to *say*, not on how long it is.
            if len(set(chunk)) < min_distinct:
                continue
            digest = hashlib.blake2b("\x00".join(chunk).encode(), digest_size=16).digest()
            windows.setdefault(digest, []).append((rel, start))

    # A clone longer than the window matches at every offset inside it, so the
    # raw groups are one real find repeated `length - window` times. Two
    # groups are the same clone when one is the other shifted a token along;
    # start only from a group with no predecessor, then walk forward to find
    # how far the match actually runs.
    groups = {tuple(sorted(set(sites))): None for sites in windows.values() if len(set(sites)) > 1}
    finds: list[Find] = []
    collected: list[tuple[tuple[str, int, int], list[tuple[str, int, int]], int]] = []
    for key in groups:
        if tuple((rel, k - 1) for rel, k in key) in groups:
            continue
        extent, cursor = 0, key
        while True:
            nxt = tuple((rel, k + 1) for rel, k in cursor)
            if nxt not in groups:
                break
            cursor, extent = nxt, extent + 1
        spans = []
        for rel, k in key:
            first = streams[rel][k][1]
            last = streams[rel][min(k + window - 1 + extent, len(streams[rel]) - 1)][1]
            spans.append((rel, first, last))
        if any(last - first + 1 < min_lines for _, first, last in spans):
            continue
        spans.sort()
        collected.append((spans[0], spans, extent))
    # A block that repeats on a period — four near-identical link loops one
    # after another — is one clone seen from each offset inside it. The shift
    # walk above only merges a match that moved a single token, so drop
    # anything whose head starts inside a clone already reported for that file.
    covered: dict[str, list[tuple[int, int]]] = {}
    for head, spans, extent in sorted(collected, key=lambda c: (c[0][0], c[0][1], -c[0][2])):
        rel, first, last = head
        if any(lo <= first <= hi for lo, hi in covered.get(rel, ())):
            continue
        covered.setdefault(rel, []).append((first, last))
        elsewhere = ", ".join(f"{other}:{lo}-{hi}" for other, lo, hi in spans[1:])
        finds.append(
            Find(
                symbol=f"clone@{head[0]}:{head[1]}",
                file=head[0],
                line=head[1],
                evidence=(
                    f"{head[0]}:{head[1]}-{head[2]} repeats at {elsewhere} — "
                    f"{window + extent} tokens identical once numbers and strings are normalised"
                ),
            )
        )
    return finds, skipped


# --- crash-fuzz --------------------------------------------------------------
#
# The one lens that runs the app instead of reading it, and the one whose find
# is a `bug` rather than a `chore`. `scripts/tui_fuzz.py` does the driving; this
# decides whose crash it is.
#
# Survey-narrow lands here in an unusual shape and the shape is the point. A
# fuzzer cannot be confined to one charter's screens — the TUI is one process
# and a keystroke goes wherever the app takes it. So the *run* is wide and the
# *find* is narrow: the deepest frame we own names a file, and the charter that
# owns that file owns the crash. A crash tui-ux finds in `agent/nodes.py` is
# planning's, and tui-ux reports it as skipped rather than filing it — which is
# why both charters run this lens rather than one running it for everybody.


def _fuzz_runner(settings: dict) -> list[dict]:
    """The seam. Spawning a pty is not something a unit test should do."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import tui_fuzz

    return tui_fuzz.fuzz(
        list(range(int(settings.get("seeds", 6)))),
        steps=int(settings.get("steps", 120)),
        hang_seconds=float(settings.get("hang_seconds", 6.0)),
        boot_seconds=float(settings.get("boot_seconds", 12.0)),
        wall_seconds=float(settings.get("wall_seconds", 90.0)),
    )


def crash_fuzz(spec: Charter, policy: dict, index: Index) -> tuple[list[Find], list[dict]]:
    del index
    rules = policy["excludes"]
    finds: list[Find] = []
    skipped: list[dict] = []
    for result in _fuzz_runner(policy):
        if result["verdict"] == "ok":
            continue
        replay = f"uv run python scripts/tui_fuzz.py --seed {result['seed']} --steps {result['steps']}"
        # The deepest frame in our own tree. A crash is ours where our code is,
        # and the innermost of those is the line a builder opens first.
        ours = result.get("frames") or []
        if not ours:
            skipped.append({"symbol": f"seed {result['seed']}", "file": "-", "rule": "no-frame-in-our-tree"})
            continue
        path, line, func = ours[-1]
        rule = _excluded(rules, path=REPO_ROOT / path, name=result.get("exception", ""), decorators=[], bases=[])
        if rule:
            skipped.append({"symbol": f"seed {result['seed']}", "file": path, "rule": rule})
            continue
        if not spec.covers(REPO_ROOT / path):
            skipped.append({"symbol": f"seed {result['seed']}", "file": path, "rule": "outside-owns"})
            continue
        finds.append(
            Find(
                symbol=f"{result['verdict']}@seed-{result['seed']}",
                file=path,
                line=line,
                evidence=(
                    f"{path}:{line} in `{func}` — {result['verdict']}"
                    f"{': ' + result['exception'] if result.get('exception') else ''}"
                    f" after {result.get('keys_sent', result['steps'])} keystrokes. Replay: {replay}"
                ),
            )
        )
    return finds, skipped


LENSES = {
    "dead-code": dead_code,
    "assertion-free-tests": assertion_free_tests,
    "layering": layering,
    "stale-flags": stale_flags,
    "duplication": duplication,
    "crash-fuzz": crash_fuzz,
}


def run(lens: str, workstream: str, *, policy: dict | None = None, index: Index | None = None) -> dict[str, Any]:
    """One lens over one charter. Pure enough to test: pass a policy and an index."""
    if lens not in LENSES:
        raise SystemExit(f"unknown lens {lens!r} — one of {', '.join(sorted(LENSES))}")
    settings = (policy or load_policy())["lenses"][lens]
    spec = charter(workstream)
    # `workstreams:` narrows a lens to the charters it makes sense on. Returning
    # an empty report rather than raising is deliberate: a routine only ever
    # runs the lenses its own file lists, so a mismatch here is a
    # misconfiguration to read in the output, not a reason to fail a sweep that
    # still has thirteen other things to do.
    offered = settings.get("workstreams")
    if offered and workstream not in offered:
        finds: list[Find] = []
        skipped = [{"symbol": lens, "file": "-", "rule": "lens-not-offered-here"}]
    else:
        needs_index = lens == "dead-code"
        finds, skipped = LENSES[lens](spec, settings, index or (build_index() if needs_index else Index()))

    cap = int(settings.get("max_batch", 10))
    finds.sort(key=lambda f: (f.file, f.line))
    return {
        "lens": lens,
        "type": settings["type"],
        # The ceiling, not a grant — the scout classifies. See the policy header.
        "lane": settings["lane"],
        "workstream": workstream,
        "paths": [_relative(p) for p in spec.owns],
        "excluded_paths": [_relative(p) for p in spec.excludes],
        "unresolved": [{"token": t, "why": w} for t, w in spec.unresolved],
        "finds": [f.as_dict() for f in finds[:cap]],
        # Reported rather than dropped: a silent cap reads as "that was all of
        # it", which is the one thing a hygiene number must never imply.
        "held": max(0, len(finds) - cap),
        "skipped": skipped,
    }


def render(report: dict[str, Any]) -> str:
    lines = [f"{report['lens']} · {report['workstream']} · {len(report['finds'])} find(s)"]
    if report["held"]:
        lines.append(f"  (+{report['held']} over max_batch, held for the next run)")
    for find in report["finds"]:
        lines.append(f"  {find['file']}:{find['line']}  {find['symbol']}")
        lines.append(f"      {find['evidence']}")
    if not report["finds"]:
        lines.append("  nothing — which is the expected outcome most weeks")
    if report["unresolved"]:
        lines.append("  charter tokens that resolved to nothing:")
        for item in report["unresolved"]:
            lines.append(f"      {item['token']}  ({item['why']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lens", choices=sorted(LENSES))
    parser.add_argument("--workstream", help="the charter to survey, e.g. tui-ux")
    parser.add_argument("--paths", metavar="WORKSTREAM", help="print how one charter's Owns block resolves, and exit")
    parser.add_argument("--json", action="store_true", help="machine-readable output for the scout")
    args = parser.parse_args(argv)

    if args.paths:
        spec = charter(args.paths)
        print(
            json.dumps(
                {
                    "workstream": spec.workstream,
                    "owns": [_relative(p) for p in spec.owns],
                    "excludes": [_relative(p) for p in spec.excludes],
                    "unresolved": [{"token": t, "why": w} for t, w in spec.unresolved],
                },
                indent=2,
            )
        )
        return 0

    if not args.lens or not args.workstream:
        parser.error("--lens and --workstream are both required (or use --paths)")

    report = run(args.lens, args.workstream)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
