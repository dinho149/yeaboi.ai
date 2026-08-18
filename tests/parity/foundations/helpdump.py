"""Python-side help renderer for the W8 CLI help gate (phase 4).

Walks every parser ``cli.build_parser()`` builds — the top-level parser and
each (nested) subcommand — and renders its ``--help`` screen exactly the
way argparse does at a terminal, pinned to ``COLUMNS=80``. The screens
freeze into ``tests/parity/goldens/cli/help/*.txt`` (one file per parser,
named by the parser's prog), and ``go/internal/argview`` reproduces them
byte-for-byte; the ``--version`` line freezes as a template because the
product version changes per release.

The width pin matters: argparse sizes help to the terminal
(``shutil.get_terminal_size().columns - 2``), and ``COLUMNS`` is the one
override every platform honours. 80 is the capture convention the W8 spec
names — the goldens are a fixed-width contract, not a responsive one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

GOLDENS_DIR = Path(__file__).parent.parent / "goldens" / "cli" / "help"
COLUMNS = "80"

# The committed --version golden is a template: both freeze tests substitute
# their own product version ({version}) before comparing.
VERSION_TEMPLATE = "yeaboi {version}\n"
VERSION_GOLDEN = GOLDENS_DIR / "version.txt"


def iter_parsers() -> list[tuple[str, argparse.ArgumentParser]]:
    """Every parser in the tree as ``(prog, parser)``, in add_parser order."""
    from yeaboi.cli import build_parser

    parsers: list[tuple[str, argparse.ArgumentParser]] = []

    def walk(parser: argparse.ArgumentParser) -> None:
        parsers.append((parser.prog, parser))
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                seen: set[int] = set()
                for sub in action.choices.values():
                    if id(sub) not in seen:  # aliases share one parser
                        seen.add(id(sub))
                        walk(sub)

    walk(build_parser())
    return parsers


def golden_name(prog: str) -> str:
    """``yeaboi perf prep`` → ``yeaboi-perf-prep.txt``."""
    return prog.replace(" ", "-") + ".txt"


def render_help(parser: argparse.ArgumentParser) -> str:
    """``parser.format_help()`` at the pinned width."""
    saved = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = COLUMNS
    try:
        return parser.format_help()
    finally:
        if saved is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = saved


def render_version() -> str:
    """The live --version line, for checking against the template."""
    from yeaboi import __version__

    return VERSION_TEMPLATE.format(version=__version__)


def build_screens() -> dict[str, str]:
    """Every golden file name with its rendered content."""
    screens = {golden_name(prog): render_help(parser) for prog, parser in iter_parsers()}
    screens[VERSION_GOLDEN.name] = VERSION_TEMPLATE
    return screens


if __name__ == "__main__":
    for name, text in build_screens().items():
        sys.stdout.write(f"── {name} ──\n{text}\n")
