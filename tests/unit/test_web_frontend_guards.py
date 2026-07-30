"""Static guards over the TypeScript sources, enforced from the Python suite.

``make test`` is pytest-only and never runs Node, so anything checked only by
``npm test`` is invisible to the merge gate a contributor actually runs. These
are the rules that must hold even when nobody has a front-end toolchain
installed — each one guards a failure that is silent in development and shows up
only for the remote teammate on the tunnel, or in a security review.

The scope is `frontend/src`, which ships in the sdist. When the front end is
absent (an installed wheel, which carries only the built bundles) every test
here skips rather than failing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

pytestmark = pytest.mark.skipif(not FRONTEND.is_dir(), reason="frontend sources are not part of an installed wheel")


def _sources(*suffixes: str, tests: bool = False) -> list[Path]:
    """Every source file with one of ``suffixes``, test files excluded by default."""
    found = [p for suffix in suffixes for p in FRONTEND.rglob(f"*{suffix}")]
    if not tests:
        found = [p for p in found if ".test." not in p.name and "/test/" not in p.as_posix()]
    return sorted(found)


def _rel(path: Path) -> str:
    return path.relative_to(FRONTEND.parent.parent).as_posix()


def _code_hits(pattern: re.Pattern[str], paths: list[Path]) -> list[str]:
    """Where ``pattern`` matches, ignoring comment lines.

    Comment lines are skipped because these files document the very patterns
    they ban — CardView explains what `innerHTML =` used to do and why it no
    longer has to, which is the most useful comment in the file and also an
    exact match for the rule. A commented mention cannot execute, so the guard
    loses nothing; and the alternative, rewording every explanation around the
    grep, makes the code worse to protect a test.
    """
    hits: list[str] = []
    for path in paths:
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith(("*", "//", "/*")):
                continue
            if pattern.search(line):
                hits.append(f"{_rel(path)}:{lineno}")
    return hits


class TestNoRawHtml:
    """`dangerouslySetInnerHTML` is banned outright, and it is not needed."""

    def test_no_dangerously_set_inner_html(self):
        """The three places someone will be tempted, and what to use instead.

        * The invite QR — keep ``<img src={apiUrl('/api/qr')}>``.
        * Ticket descriptions — already plain text (``tickets.py`` strips their
          HTML), so ``<Prose>`` with ``white-space: pre-wrap`` is enough.
        * ``standup._linkify``, which splices anchors into escaped text — use
          the ``Run[]`` rich-text contract (``<RichText>``) instead.

        React escapes children, so every other path is safe by construction;
        this attribute is the single documented way to opt out of that, which is
        exactly why it is worth banning rather than reviewing case by case.
        """
        offenders = _code_hits(re.compile(r"dangerouslySetInnerHTML"), _sources(".ts", ".tsx"))
        assert offenders == [], f"dangerouslySetInnerHTML is banned — see Prose.tsx: {offenders}"

    def test_no_inner_html_assignment(self):
        """`el.innerHTML = …` is the same hole with a different spelling.

        It is also how the entire pre-React board rendered, so it is the habit
        most likely to come back during the migration.
        """
        offenders = _code_hits(re.compile(r"\.innerHTML\s*="), _sources(".ts", ".tsx"))
        assert offenders == [], f"assign to innerHTML is banned — render children instead: {offenders}"


class TestNoEval:
    """The tunnel CSP has no `unsafe-eval` (`sharing/gate.py`)."""

    def test_sources_do_not_eval(self):
        """A build-time check on top of the bundle-level one in test_web_assets.

        The bundle guard catches a *dependency* reaching for eval; this catches
        our own code, and names the file rather than the minified blob.
        """
        pattern = re.compile(r"\beval\s*\(|new\s+Function\s*\(")
        offenders = [_rel(p) for p in _sources(".ts", ".tsx", tests=True) if pattern.search(p.read_text())]
        assert offenders == [], f"eval/new Function are blocked by the tunnel CSP: {offenders}"


class TestOnePaletteSource:
    """The five themes were hand-copied into three files and drifted. Never again."""

    def test_theme_blocks_exist_only_in_palette_css(self):
        stylesheets = _sources(".css")
        offenders = [
            _rel(p)
            for p in stylesheets
            if p.name != "palette.css" and re.search(r'\[data-theme="\w+"\]', p.read_text())
        ]
        # tokens.css may reference bare `[data-theme]` for the print override —
        # that is a selector over *any* theme, not a redefinition of one.
        assert offenders == [], f"palettes must live only in design/palette.css: {offenders}"

    def test_component_styles_use_tokens_not_literal_colours(self):
        """Component CSS may not hardcode a colour: it would not follow the theme.

        Scoped to the component modules. palette.css *is* the colours, and
        tokens.css carries the print override, which is deliberately fixed.
        """
        # A QR code has to be scanned. White quiet-zone regardless of theme is a
        # property of the format, not a styling choice.
        allowed = {"shared.module.css": {".qr"}}
        hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")

        offenders: list[str] = []
        for path in _sources(".module.css"):
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                if not hex_re.search(line) or line.lstrip().startswith(("*", "/*")):
                    continue
                if any(selector in line for selector in allowed.get(path.name, set())):
                    continue
                offenders.append(f"{_rel(path)}:{lineno}")
        assert offenders == [], f"use a token from design/tokens.css, not a literal colour: {offenders}"


class TestGeneratedEnums:
    def test_enums_ts_is_current(self):
        """`frontend/src/types/enums.ts` must match the board tuples it mirrors.

        Regenerate with ``uv run python scripts/gen_web_types.py``. Stale here
        means a literal union in the browser disagrees with the set the server
        validates against, so the client can offer a value the board refuses.
        """
        import sys

        sys.path.insert(0, str(FRONTEND.parents[1] / "scripts"))
        from gen_web_types import OUTPUT, render  # noqa: PLC0415 - path is set up above

        assert OUTPUT.is_file(), "run: uv run python scripts/gen_web_types.py"
        assert OUTPUT.read_text(encoding="utf-8") == render(), (
            "enums.ts is stale — run: uv run python scripts/gen_web_types.py"
        )
