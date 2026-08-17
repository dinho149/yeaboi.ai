"""Environment fixture matrix for the W8 foundations parity gate.

Each fixture is one launch environment for ``dump.py`` (and, from W8 phase 3
on, for ``yeaboi __dump-foundations``). Values are templates: ``{tmp}`` is
substituted with the per-run sandbox directory, and the committed goldens
store the template form back, so they are hermetic — independent of where the
sandbox lives on any given machine. ``HOME`` always points inside the sandbox
(``{tmp}/home`` unless a fixture overrides it), so no fixture can read or
write the real user home.

The traps, per the W8 spec: ``~`` expansion (bare and with a tail), pathlib's
lexical normalisation (repeated slashes, ``.`` dropped, ``..`` kept — never
resolved), a relative ``YEABOI_HOME``, ``str.strip()``'s unicode whitespace,
``HOME`` itself needing normalisation, and NOT XDG — ``~/.yeaboi`` plus
``expanduser`` only, no ``$VAR`` expansion anywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
GOLDENS_DIR = HERE.parent / "goldens" / "foundations"
DUMP_SCRIPT = HERE / "dump.py"
TMP_TOKEN = "{tmp}"


@dataclass(frozen=True)
class Fixture:
    """One launch environment. ``env`` overlays the base ``HOME={tmp}/home``;
    ``YEABOI_HOME`` is unset unless a fixture sets it."""

    name: str
    env: dict[str, str] = field(default_factory=dict)


FIXTURES = [
    # The bootstrap default: no YEABOI_HOME, everything under ~/.yeaboi.
    Fixture("default"),
    # The straightforward relocation.
    Fixture("home-absolute", {"YEABOI_HOME": "{tmp}/custom-home"}),
    # ~ with a tail — expanduser against HOME, spaces preserved.
    Fixture("home-tilde", {"YEABOI_HOME": "~/data dir/yeaboi"}),
    # Bare ~ — the root IS the home directory (ENV_FILE still lands in ~/.yeaboi).
    Fixture("home-tilde-bare", {"YEABOI_HOME": "~"}),
    # pathlib drops the trailing slash.
    Fixture("home-trailing-slash", {"YEABOI_HOME": "{tmp}/custom-home/"}),
    # pathlib collapses repeated slashes (a leading "//" would survive; an
    # interior one never does).
    Fixture("home-double-slashes", {"YEABOI_HOME": "{tmp}//custom//deep"}),
    # "." components drop, ".." stays — normalisation is lexical, never resolved.
    Fixture("home-dot-segments", {"YEABOI_HOME": "{tmp}/./custom/../elsewhere"}),
    # A relative YEABOI_HOME stays relative (resolved against the cwd only by
    # the filesystem calls, never in the strings).
    Fixture("home-relative", {"YEABOI_HOME": "rel/yeaboi-home"}),
    # Whitespace-only strips to empty and falls back to the default root.
    Fixture("home-whitespace-only", {"YEABOI_HOME": "   "}),
    # str.strip() strips unicode whitespace (NBSP, em-space), not just ASCII.
    Fixture("home-unicode-whitespace-padding", {"YEABOI_HOME": "\u00a0{tmp}/nbsp-home\u2003"}),
    # Non-ASCII path components pass through untouched.
    Fixture("home-unicode", {"YEABOI_HOME": "{tmp}/données/yeaboi-путь"}),
    # Path.home() normalises too: a trailing slash on HOME itself
    # (posixpath.expanduser rstrips it)...
    Fixture("home-env-trailing-slash", {"HOME": "{tmp}/home2/"}),
    # ...and an interior double slash (pathlib's parse collapses it).
    Fixture("home-env-double-slash", {"HOME": "{tmp}//home3"}),
]


def template_env(fixture: Fixture) -> dict[str, str]:
    """The fixture's full launch-environment template (base + overlay)."""
    return {"HOME": "{tmp}/home", **fixture.env}


def realized_env(fixture: Fixture, tmp: Path) -> dict[str, str]:
    """The template with ``{tmp}`` substituted for this run's sandbox."""
    return {k: v.replace(TMP_TOKEN, str(tmp)) for k, v in template_env(fixture).items()}


def launch_env(fixture: Fixture, tmp: Path) -> dict[str, str]:
    """A full subprocess environment: the parent's, minus any real
    YEABOI_HOME/HOME leakage, plus the fixture's realized variables."""
    env = dict(os.environ)
    env.pop("YEABOI_HOME", None)
    env.pop("HOME", None)
    env.update(realized_env(fixture, tmp))
    return env


def run_dump(fixture: Fixture, tmp: Path) -> dict:
    """Run dump.py in a fresh interpreter under the fixture's environment.

    ``cwd=tmp`` so a relative-root fixture's mkdirs land in the sandbox — the
    Go golden test chdirs the same way before calling the helpers.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        [sys.executable, str(DUMP_SCRIPT)],
        cwd=tmp,
        env=launch_env(fixture, tmp),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def normalize(dump: dict, tmp: Path) -> dict:
    """Substitute this run's sandbox path back to ``{tmp}`` throughout."""
    return json.loads(json.dumps(dump, ensure_ascii=False).replace(str(tmp), TMP_TOKEN))


def golden_for(fixture: Fixture, tmp: Path) -> dict:
    """What the committed golden must contain: the template env (so the Go
    side can rebuild the fixture without importing this file) + the dump."""
    return {"env": template_env(fixture), "dump": normalize(run_dump(fixture, tmp), tmp)}


def golden_path(fixture: Fixture) -> Path:
    return GOLDENS_DIR / f"{fixture.name}.json"


def render_golden(golden: dict) -> str:
    return json.dumps(golden, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
