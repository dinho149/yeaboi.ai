"""Tests for scripts/bump_version.py — the deterministic version bumper."""

import importlib.util
from pathlib import Path

import pytest

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"
_spec = importlib.util.spec_from_file_location("bump_version", _MODULE_PATH)
bump_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_version)


@pytest.mark.parametrize(
    ("version", "level", "expected"),
    [
        ("1.5.0", "patch", "1.5.1"),
        ("1.5.0", "minor", "1.6.0"),
        ("1.5.0", "major", "2.0.0"),
        ("1.5.3", "minor", "1.6.0"),  # minor resets patch
        ("1.5.3", "major", "2.0.0"),  # major resets minor + patch
        ("0.0.0", "patch", "0.0.1"),
        ("10.9.9", "major", "11.0.0"),
    ],
)
def test_bump(version, level, expected):
    assert bump_version.bump(version, level) == expected


def test_bump_rejects_bad_level():
    with pytest.raises(ValueError):
        bump_version.bump("1.2.3", "mega")


@pytest.mark.parametrize("bad", ["1.2", "1.2.3.4", "1.x.0", "v1.2.3", ""])
def test_bump_rejects_bad_version(bad):
    with pytest.raises(ValueError):
        bump_version.bump(bad, "patch")


def _write_pyproject(tmp_path: Path, version: str) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(f'[project]\nname = "yeaboi"\nversion = "{version}"\nrequires-python = ">=3.10"\n')
    return p


def test_read_current(tmp_path):
    p = _write_pyproject(tmp_path, "1.5.0")
    assert bump_version.read_current(p) == "1.5.0"


def test_write_version_round_trip(tmp_path):
    p = _write_pyproject(tmp_path, "1.5.0")
    bump_version.write_version("1.6.0", p)
    assert bump_version.read_current(p) == "1.6.0"
    # Only the version line changes; the rest of the file is preserved.
    assert 'name = "yeaboi"' in p.read_text()
    assert 'requires-python = ">=3.10"' in p.read_text()


def test_read_current_missing_version(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "yeaboi"\n')
    with pytest.raises(SystemExit):
        bump_version.read_current(p)
