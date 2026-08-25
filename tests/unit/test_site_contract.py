"""Guards for ``contracts/site.json`` — the package facts the website vendors.

The website is its own repo now. It states this package's Python floor, repo URL
and install target, and it takes them from a pinned copy of this file rather
than restating them. That makes staleness here invisible from over there, so
the freshness assertion has to live on this side.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "site.json"
_MODULE_PATH = ROOT / "scripts" / "gen_site_contract.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_site_contract", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_site_contract"] = module
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


@pytest.fixture(scope="module")
def contract() -> dict[str, str]:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


class TestFreshness:
    def test_the_committed_contract_matches_pyproject(self) -> None:
        """The whole point: the site cannot advertise a floor this repo has moved off."""
        assert CONTRACT.read_text(encoding="utf-8") == gen.render(gen.build()), (
            "contracts/site.json is stale — run `make site-contract` and commit the result"
        )

    def test_every_field_is_derived_from_pyproject(self, contract: dict[str, str]) -> None:
        meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        assert contract["package"] == meta["name"]
        assert contract["requires_python"] == meta["requires-python"]
        assert contract["description"] == meta["description"]
        assert contract["homepage"] == meta["urls"]["Homepage"]
        assert contract["repository"] == meta["urls"]["Repository"]


class TestShape:
    def test_it_carries_no_version(self, contract: dict[str, str]) -> None:
        """A version here would churn the contract on every merged PR.

        ``auto-version.yml`` bumps ``pyproject.toml`` on every PR branch, so a
        version field would make the site's pin stale once per release while the
        site displays no version anywhere. The same reasoning keeps
        ``softwareVersion`` out of the site's structured data.
        """
        assert "version" not in contract

    def test_the_floor_is_a_usable_specifier(self, contract: dict[str, str]) -> None:
        """The site parses this to render "Python X.Y+" — it must stay parseable."""
        floor = contract["requires_python"]
        assert floor.startswith(">="), f"the site's floor parser expects a >= specifier, got {floor!r}"
        major, _, minor = floor.lstrip(">=").split(",")[0].strip().partition(".")
        assert major.isdigit() and minor.isdigit(), f"cannot read a X.Y floor out of {floor!r}"

    def test_the_pypi_url_matches_the_package_name(self, contract: dict[str, str]) -> None:
        assert contract["pypi"].rstrip("/").rsplit("/", 1)[-1] == contract["package"]

    def test_urls_are_absolute_https(self, contract: dict[str, str]) -> None:
        for key in ("homepage", "repository", "pypi"):
            assert contract[key].startswith("https://"), f"{key} is not an absolute https URL"
