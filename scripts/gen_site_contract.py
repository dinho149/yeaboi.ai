#!/usr/bin/env python3
"""Generate ``contracts/site.json`` — the package facts the website advertises.

The website lives in its own repo (``yeaboi-site``) and states things about this
package: the Python floor in its structured data, the repo URL in its JSON-LD
``codeRepository``, the ``pip install`` target. Those are facts about *this*
repo, and before the split the site's generator simply read ``pyproject.toml``
next to it.

Across repos it vendors this file instead, pinned by sha (``make
contracts-sync``). So the rule is the same one that held before: the site never
restates a fact it can derive.

``version`` is deliberately absent. ``auto-version.yml`` bumps it on every merged
PR, and a contract that changes every release is one the site is permanently
behind on for no benefit — the site advertises no version anywhere, by design.

Usage::

    uv run python scripts/gen_site_contract.py           # write
    uv run python scripts/gen_site_contract.py --check   # assert, never write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 — tomllib landed in 3.11; the `dev` extra supplies the backport.
    import tomli as tomllib

# Import-free by design (tests/unit/test_beta.py enforces it), so importing it
# here costs nothing.
from yeaboi import beta

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CONTRACT = ROOT / "contracts" / "site.json"


def build() -> dict:
    """Derive the contract from pyproject's [project] table and yeaboi.beta."""
    meta = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    name = meta["name"]
    return {
        "package": name,
        "requires_python": meta["requires-python"],
        "description": meta["description"],
        "homepage": meta["urls"]["Homepage"],
        "repository": meta["urls"]["Repository"],
        "pypi": f"https://pypi.org/project/{name}/",
        # The site's pages and its .beta-pill carry hand-written copies of these.
        # They were pinned to yeaboi.beta by a test in this repo; across the
        # split the site pins them to this instead.
        "beta": {
            "label": beta.BETA_LABEL,
            "tag": beta.BETA_TAG,
            "rgb": list(beta.BETA_RGB),
            "performance_phrase": beta.PERFORMANCE_BETA_PHRASE,
            "agentwatch_phrase": beta.AGENTWATCH_BETA_PHRASE,
            "ship_phrase": beta.SHIP_BETA_PHRASE,
        },
    }


def render(contract: dict) -> str:
    return json.dumps(contract, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = parser.parse_args(argv)

    want = render(build())

    if args.check:
        have = CONTRACT.read_text(encoding="utf-8") if CONTRACT.exists() else ""
        if have != want:
            print(
                f"{CONTRACT.relative_to(ROOT)} is stale — run `make site-contract` and commit the result",
                file=sys.stderr,
            )
            return 1
        print(f"{CONTRACT.relative_to(ROOT)} is up to date")
        return 0

    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.write_text(want, encoding="utf-8")
    print(f"wrote {CONTRACT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
