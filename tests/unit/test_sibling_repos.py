"""The sibling-checkout resolver the generators use (scripts/_sibling_repos.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def repos():
    spec = importlib.util.spec_from_file_location("_sibling_repos", Path("scripts/_sibling_repos.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _desktop(tmp_path: Path, *, with_art: bool) -> Path:
    root = tmp_path / "yeaboi-desktop"
    brand = root / "src" / "renderer" / "assets" / "brand"
    brand.mkdir(parents=True)
    if with_art:
        (brand / "persona-wizard.png").write_bytes(b"png")
    return root


class TestDesktopRoot:
    def test_the_env_var_wins_when_it_holds_the_art(self, repos, tmp_path, monkeypatch):
        root = _desktop(tmp_path, with_art=True)
        monkeypatch.setenv("YEABOI_DESKTOP", str(root))
        assert repos.desktop_root() == root
        assert repos.desktop_brand() == root / "src" / "renderer" / "assets" / "brand"

    def test_a_checkout_without_the_art_is_no_use(self, repos, tmp_path, monkeypatch):
        monkeypatch.setenv("YEABOI_DESKTOP", str(_desktop(tmp_path, with_art=False)))
        assert repos.desktop_root(required=False) is None
        with pytest.raises(SystemExit, match="persona art"):
            repos.desktop_root()

    def test_a_missing_directory_says_so(self, repos, tmp_path, monkeypatch):
        monkeypatch.setenv("YEABOI_DESKTOP", str(tmp_path / "nowhere"))
        with pytest.raises(SystemExit, match="YEABOI_DESKTOP"):
            repos.desktop_root()
