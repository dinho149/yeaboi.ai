"""Tests for docs/install.sh — the `curl | sh` bootstrapper behind yeaboi.ai/install.sh.

The script exists because `pip` and `pipx` both install with the interpreter they
are run with, and hard-fail when it is too old — the single most common reason a
first-time user never reaches the product. `uv tool install` fetches its own
interpreter instead, so this script's whole job is to get uv onto the machine and
then hand it a version specifier.

Two properties are worth testing rather than trusting. The script is served
straight off `main` by GitHub Pages with no build step and no deploy job, so
nothing else in the pipeline would notice it breaking. And it is executed by
strangers over a pipe, which constrains it in ways a normal script is not
constrained: it must never read stdin (under `curl | sh` the script *is* stdin,
so one `read` would swallow the rest of itself), and it must be POSIX sh (the
documented invocation pipes into `sh`, which is dash on Debian and Ubuntu).

The behavioural tests follow tests/unit/test_wt_script.py: build a stub PATH in
tmp_path, run the real script, and inspect what it did.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "docs" / "install.sh"
README = ROOT / "README.md"

# The command the whole change exists to make the headline. Any drift between
# this string and what the README/landing page actually show is a bug in the
# funnel, not a formatting nit.
CURL_COMMAND = "curl -LsSf https://yeaboi.ai/install.sh | sh"


def _run(script_env: dict[str, str], *, stdin_closed: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the real installer with a stubbed PATH.

    ``stdin=DEVNULL`` by default: succeeding with no stdin is the property that
    makes `curl | sh` safe, so it is the default the tests assert against.
    """
    return subprocess.run(
        ["sh", str(INSTALL_SH)],
        capture_output=True,
        text=True,
        env=script_env,
        stdin=subprocess.DEVNULL if stdin_closed else None,
        check=False,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, object]:
    """A stub `uv` that records its argv and the env it was handed."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"

    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'echo "argv: $*" >> "$LOG"\n'
        'echo "downloads: ${UV_PYTHON_DOWNLOADS:-UNSET}" >> "$LOG"\n'
        'exit "${UV_EXIT:-0}"\n'
    )
    uv.chmod(0o755)

    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "LOG": str(log),
    }
    return {"env": env, "log": log, "bin": bin_dir, "home": home}


def _code(path: Path = INSTALL_SH) -> str:
    """The script with whole-line comments stripped.

    The static guards below are about what the script *does*. Its header comment
    names the constructs it must avoid ("no [[", "never calls sudo"), and a guard
    that trips over its own documentation is a guard people delete.
    """
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))


def _log(sandbox: dict[str, object]) -> str:
    log = sandbox["log"]
    assert isinstance(log, Path)
    return log.read_text() if log.exists() else ""


class TestBehaviour:
    def test_installs_with_a_specifier_not_a_pinned_version(self, sandbox):
        result = _run(sandbox["env"])
        assert result.returncode == 0, result.stderr
        # A specifier, not a version: `--python 3.11` would pin every user to the
        # oldest supported runtime and download a ~30MB interpreter onto machines
        # that already have a usable one.
        assert "argv: tool install --python >=3.11 yeaboi" in _log(sandbox)

    def test_forces_automatic_python_downloads(self, sandbox):
        """The one setting the script cannot leave to a default.

        Automatic downloads are uv's default, but a ~/.config/uv/uv.toml or a
        corporate image setting python-downloads = "never" collapses the install
        back onto system Python — exactly the failure this script exists to fix.
        """
        _run(sandbox["env"])
        assert "downloads: automatic" in _log(sandbox)

    def test_succeeds_with_stdin_closed(self, sandbox):
        # Under `curl | sh` the script is itself stdin. A `read` anywhere would
        # consume the remainder of the program and execute a truncated file.
        result = _run(sandbox["env"], stdin_closed=True)
        assert result.returncode == 0, result.stderr

    def test_overrides_reach_the_uv_command(self, sandbox):
        """What makes the release checklist able to test a specific rc."""
        env = {**sandbox["env"], "YEABOI_PACKAGE": "yeaboi==9.9.9rc1", "YEABOI_UV_ARGS": "--pre"}
        result = _run(env)
        assert result.returncode == 0, result.stderr
        assert "argv: tool install --python >=3.11 --pre yeaboi==9.9.9rc1" in _log(sandbox)

    def test_a_failing_uv_fails_the_script(self, sandbox):
        env = {**sandbox["env"], "UV_EXIT": "1"}
        result = _run(env)
        assert result.returncode != 0, "a failed install must not report success"

    def test_native_windows_is_refused_before_anything_is_installed(self, sandbox):
        """Installing successfully into a shell that cannot run yeaboi is worse than refusing.

        src/yeaboi/ui/shared/_input.py imports termios and tty at module scope, so
        the TUI cannot start on native Windows at all.
        """
        bin_dir = sandbox["bin"]
        assert isinstance(bin_dir, Path)
        uname = bin_dir / "uname"
        uname.write_text('#!/bin/sh\necho "MINGW64_NT-10.0-22631"\n')
        uname.chmod(0o755)

        result = _run(sandbox["env"])
        assert result.returncode != 0
        assert "WSL" in result.stderr
        assert _log(sandbox) == "", "uv must not be invoked on a platform that cannot run yeaboi"

    def test_bootstraps_uv_from_a_pinned_url_when_missing(self, tmp_path: Path):
        """uv absent is the case that matters: it must become usable in this same run."""
        home = tmp_path / "home"
        home.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "log"

        # Stand in for Astral's installer: drop a uv on PATH and write the env
        # file the real one writes, which is how the script picks it up mid-run.
        curl = bin_dir / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            'echo "curl: $*" >> "$LOG"\n'
            'mkdir -p "$HOME/.local/bin"\n'
            'printf \'#!/bin/sh\\necho "argv: $*" >> "$LOG"\\nexit 0\\n\' > "$HOME/.local/bin/uv"\n'
            'chmod +x "$HOME/.local/bin/uv"\n'
            'printf \'export PATH="$HOME/.local/bin:$PATH"\\n\' > "$HOME/.local/bin/env"\n'
            "echo ':'\n"
        )
        curl.chmod(0o755)

        env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "LOG": str(log)}
        result = _run(env)

        assert result.returncode == 0, result.stderr
        body = log.read_text()
        assert "https://astral.sh/uv/" in body, "the uv installer must be fetched"
        assert "argv: tool install" in body, "uv must be usable in the same run, not after a reshell"

    def test_is_idempotent(self, sandbox):
        first = _run(sandbox["env"])
        second = _run(sandbox["env"])
        assert first.returncode == 0 and second.returncode == 0


class TestStatic:
    def test_parses_as_posix_sh(self):
        subprocess.run(["sh", "-n", str(INSTALL_SH)], check=True, capture_output=True)

    @pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
    def test_shellcheck_is_clean(self):
        result = subprocess.run(
            ["shellcheck", "-s", "sh", str(INSTALL_SH)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stdout

    @pytest.mark.parametrize("bashism", ["[[", "function ", "source ", "declare ", "local "])
    def test_has_no_bashisms(self, bashism: str):
        # The documented invocation is `| sh`, which is dash on Debian/Ubuntu.
        assert bashism not in _code(), f"{bashism!r} is not POSIX sh"

    def test_never_reads_stdin(self):
        assert not re.search(r"^\s*read\b", _code(), re.MULTILINE), "a read would swallow the rest of the script"

    def test_never_escalates_and_writes_only_under_home(self):
        assert "sudo" not in _code()

    def test_is_executable_and_fails_fast(self):
        assert os.access(INSTALL_SH, os.X_OK)
        assert "set -eu" in INSTALL_SH.read_text()

    def test_python_specifier_matches_the_packaged_floor(self):
        """The coupling that keeps the installer honest as the floor moves.

        install.sh's default and pyproject's requires-python are the same
        constraint expressed twice; asserting equality is what stops one moving
        without the other.
        """
        requires = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["requires-python"]
        match = re.search(r'YEABOI_PYTHON="\$\{YEABOI_PYTHON:-(.+?)\}"', INSTALL_SH.read_text())
        assert match, "install.sh no longer defines a YEABOI_PYTHON default"
        assert match.group(1) == requires, (
            f"install.sh installs on {match.group(1)!r} but the package requires {requires!r}"
        )

    def test_pins_the_uv_installer(self):
        # Pinned for the same reason this repo pins its GitHub Actions by SHA.
        body = INSTALL_SH.read_text()
        assert re.search(r'UV_INSTALLER_VERSION="\$\{UV_INSTALLER_VERSION:-\d+\.\d+\.\d+\}"', body)
        assert "astral.sh/uv/${UV_INSTALLER_VERSION}/install.sh" in body


class TestDocumentedCommands:
    """The install commands users actually see must be the ones that cannot fail."""

    def test_readme_leads_with_the_curl_command(self):
        """README.md is the PyPI project page (pyproject sets readme = "README.md").

        Whoever hits pip's Requires-Python error lands here next, so the command
        that works has to be the first one on the page.
        """
        body = README.read_text()
        quick_start = body.index("## 🚀 Quick Start")
        first_block = body.index("```bash", quick_start)
        assert CURL_COMMAND in body[first_block : first_block + 400]

    def test_no_bare_pipx_install_is_advertised(self):
        """`pipx install yeaboi` is the exact command that sends users to upgrade Python.

        pipx uses the interpreter it is running under and will not fetch one
        unless asked, so it may only appear with --python or --fetch-missing-python.
        """
        surfaces = [README, ROOT / "docs" / "index.html", *(ROOT / "docs" / "docs").glob("*.html")]
        for path in surfaces:
            for line in path.read_text().splitlines():
                if "pipx install" not in line:
                    continue
                assert "--python" in line or "--fetch-missing-python" in line, (
                    f"{path.name}: bare `pipx install` fails on an old Python — {line.strip()!r}"
                )

    def test_landing_hero_offers_the_curl_command(self):
        assert CURL_COMMAND in (ROOT / "docs" / "index.html").read_text()

    def test_copy_buttons_copy_what_they_display(self):
        """A copy button that pastes something other than what it shows is invisible in review.

        docs/assets/site.js reads data-copy verbatim; nothing else compares the two.
        """
        html = (ROOT / "docs" / "index.html").read_text()
        blocks = re.findall(
            r'<code>([^<]+)</code>\s*<button class="copy" data-copy="([^"]+)"',
            html,
        )
        assert blocks, "no copy-buttons found — has the landing markup changed?"
        for shown, copied in blocks:
            assert shown.strip() == copied.strip(), f"shows {shown!r} but copies {copied!r}"
