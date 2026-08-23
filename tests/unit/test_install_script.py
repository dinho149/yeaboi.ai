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
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 — tomllib landed in 3.11; the `dev` extra supplies the backport.
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "docs" / "install.sh"
README = ROOT / "README.md"

# The command the whole change exists to make the headline. Any drift between
# this string and what the README/landing page actually show is a bug in the
# funnel, not a formatting nit.
CURL_COMMAND = "curl -LsSf https://yeaboi.ai/install.sh | sh"

# Read, never hardcoded: the installer's pin and the packaged floor are the same
# string by design, and a test that spells it out is one more place to forget.
FLOOR = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["requires-python"]


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
    # It answers `--version` because the script gates on it: a uv older than
    # 0.4.30 cannot parse a `--python` specifier and is replaced rather than used.
    uv.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "uv ${UV_FAKE_VERSION:-0.11.2} (abc 2026-01-01)"; exit 0; fi\n'
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
        # A specifier, not a version: a bare `--python 3.10` would pin every user to the
        # oldest supported runtime and download a ~30MB interpreter onto machines
        # that already have a usable one.
        assert f"argv: tool install --python {FLOOR} yeaboi" in _log(sandbox)

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
        assert f"argv: tool install --python {FLOOR} --pre yeaboi==9.9.9rc1" in _log(sandbox)

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

    @staticmethod
    def _fake_installer_curl(bin_dir: Path, *, mode: str = "ok") -> Path:
        """Stand in for Astral's installer.

        `mode` picks the failure being exercised: "ok" writes a working installer,
        "fail" exits non-zero (a 404 or a dead proxy), "empty" exits 0 having
        written nothing (a connection dropped before the first byte).
        """
        # The payload the real installer would have been: drop a uv on PATH and
        # write the env file the script sources to pick it up mid-run.
        payload = bin_dir / "uv-installer-payload.sh"
        payload.write_text(
            "#!/bin/sh\n"
            'mkdir -p "$HOME/.local/bin"\n'
            "cat > \"$HOME/.local/bin/uv\" <<'EOF'\n"
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "uv 0.11.2 (abc 2026-01-01)"; exit 0; fi\n'
            'echo "argv: $*" >> "$LOG"\n'
            "exit 0\n"
            "EOF\n"
            'chmod +x "$HOME/.local/bin/uv"\n'
            'printf \'export PATH="$HOME/.local/bin:$PATH"\\n\' > "$HOME/.local/bin/env"\n'
        )

        take_output_path = 'while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
        bodies = {
            "ok": take_output_path + f'cat "{payload}" > "$out"\nexit 0\n',
            "fail": "exit 22\n",
            "empty": take_output_path + ': > "$out"\nexit 0\n',
        }
        curl = bin_dir / "curl"
        curl.write_text('#!/bin/sh\necho "curl: $*" >> "$LOG"\nout=""\n' + bodies[mode])
        curl.chmod(0o755)
        return curl

    @staticmethod
    def _bare_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
        home = tmp_path / "home"
        home.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "log"
        return {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "LOG": str(log)}, bin_dir, log

    def test_bootstraps_uv_from_a_pinned_url_when_missing(self, tmp_path: Path):
        """uv absent is the case that matters: it must become usable in this same run."""
        env, bin_dir, log = self._bare_env(tmp_path)
        self._fake_installer_curl(bin_dir)

        result = _run(env)

        assert result.returncode == 0, result.stderr
        body = log.read_text()
        assert "https://astral.sh/uv/" in body, "the uv installer must be fetched"
        assert "argv: tool install" in body, "uv must be usable in the same run, not after a reshell"

    def test_a_failed_installer_download_says_so_instead_of_blaming_path(self, tmp_path: Path):
        """`curl ... | sh` reported success when curl 404'd — POSIX sh has no
        pipefail and `set -e` takes the last command's status — and the user was
        then told uv "is not on PATH", which sends them somewhere that cannot help."""
        env, bin_dir, log = self._bare_env(tmp_path)
        self._fake_installer_curl(bin_dir, mode="fail")

        result = _run(env)

        assert result.returncode != 0
        assert "could not download" in result.stderr
        assert "not on PATH" not in result.stderr, "the misleading message this replaced"
        assert "argv: tool install" not in (log.read_text() if log.exists() else "")

    def test_a_truncated_installer_download_is_not_executed(self, tmp_path: Path):
        """A connection dropped mid-transfer would otherwise feed `sh` a partial
        script — the exact hazard the header comment guards this script's own body against."""
        env, bin_dir, log = self._bare_env(tmp_path)
        self._fake_installer_curl(bin_dir, mode="empty")

        result = _run(env)

        assert result.returncode != 0
        assert "empty" in result.stderr

    def test_a_uv_too_old_for_specifiers_is_replaced_not_used(self, sandbox, tmp_path: Path):
        """uv gained `--python` specifier support in 0.4.30 (measured). An older one
        would reject `--python '>=3.10'`, and the point of this script is that what
        the machine already has must not decide whether the install works."""
        env = dict(sandbox["env"])
        env["UV_FAKE_VERSION"] = "0.4.29"
        bin_dir = sandbox["bin"]
        assert isinstance(bin_dir, Path)
        self._fake_installer_curl(bin_dir, mode="fail")

        result = _run(env)

        # The bootstrap is attempted (and here, fails) rather than the old uv used.
        assert result.returncode != 0
        assert "predates" in result.stdout
        assert "could not download" in result.stderr

    @pytest.mark.parametrize("version", ["0.4.30", "0.5.0", "0.11.2", "1.0.0"])
    def test_a_new_enough_uv_is_kept(self, sandbox, version):
        env = dict(sandbox["env"])
        env["UV_FAKE_VERSION"] = version
        result = _run(env)
        assert result.returncode == 0, result.stderr
        assert "predates" not in result.stdout
        assert "argv: tool install" in _log(sandbox)

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
        # rglob, not glob: docs/docs/modes/ and docs/docs/agents/ carry install
        # snippets too, and a plain glob left both unscanned.
        surfaces = [README, ROOT / "docs" / "index.html", *(ROOT / "docs" / "docs").rglob("*.html")]
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
