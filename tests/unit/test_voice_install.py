"""Unit tests for the in-app dictation installer.

Nothing here installs anything or reaches the network. Two layers are used
deliberately for the subprocess paths: a fake ``Popen`` for the progress and
failure sequences (fast, exhaustive), plus a handful of *real* short-lived child
processes, because only a real process proves the reader thread, the merged
stderr and the non-blocking wait actually work together.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest

from yeaboi import voice_install

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Point every persisted path at a tmp dir and clear the memoised verdict."""
    monkeypatch.setattr(voice_install, "_unsupported_cache", None)
    monkeypatch.setattr("yeaboi.paths.get_voice_install_path", lambda: tmp_path / "voice_install.json")
    monkeypatch.setattr("yeaboi.paths.get_bin_dir", lambda: tmp_path)
    yield
    voice_install.reset_unsupported_cache()


def _no_source_checkout(monkeypatch):
    """Make _is_source_checkout() False so the PyPI branches can be exercised."""
    monkeypatch.setattr(voice_install, "_is_source_checkout", lambda: False)


# ---------------------------------------------------------------------------
# Requirements must not drift from pyproject
# ---------------------------------------------------------------------------


class TestRequirementsMatchExtra:
    def test_voice_packages_equal_the_pyproject_extra(self):
        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        extra = data["project"]["optional-dependencies"]["voice"]
        assert set(voice_install.VOICE_PACKAGES) == set(extra)

    def test_every_model_size_has_a_download_estimate(self):
        assert voice_install.MODEL_SIZES == frozenset(voice_install.MODEL_MB)


# ---------------------------------------------------------------------------
# Platform gate
# ---------------------------------------------------------------------------


class TestPlatformSupport:
    def test_64bit_mac_arm_is_supported(self, monkeypatch):
        monkeypatch.setattr(sys, "maxsize", 2**63 - 1)
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        monkeypatch.setattr(sys, "platform", "darwin")
        assert voice_install.platform_support() == (True, "")

    def test_32bit_python_is_rejected(self, monkeypatch):
        monkeypatch.setattr(sys, "maxsize", 2**31 - 1)
        supported, reason = voice_install.platform_support()
        assert not supported
        assert "32-bit" in reason

    def test_unknown_architecture_is_rejected(self, monkeypatch):
        monkeypatch.setattr(sys, "maxsize", 2**63 - 1)
        monkeypatch.setattr("platform.machine", lambda: "armv7l")
        supported, reason = voice_install.platform_support()
        assert not supported
        assert "armv7l" in reason

    def test_musl_linux_is_rejected(self, monkeypatch):
        monkeypatch.setattr(sys, "maxsize", 2**63 - 1)
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("platform.libc_ver", lambda: ("", ""))
        supported, reason = voice_install.platform_support()
        assert not supported
        assert "musl" in reason

    def test_ancient_glibc_is_rejected(self, monkeypatch):
        monkeypatch.setattr(sys, "maxsize", 2**63 - 1)
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("platform.libc_ver", lambda: ("glibc", "2.12"))
        supported, reason = voice_install.platform_support()
        assert not supported
        assert "2.12" in reason

    def test_modern_glibc_is_supported(self, monkeypatch):
        monkeypatch.setattr(sys, "maxsize", 2**63 - 1)
        monkeypatch.setattr("platform.machine", lambda: "x86_64")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("platform.libc_ver", lambda: ("glibc", "2.35"))
        assert voice_install.platform_support()[0]

    def test_a_new_cpython_is_not_pre_gated(self, monkeypatch):
        """A missing cp3XX wheel resolves itself when the wheel lands, so it is
        classified after a real attempt rather than refused up front."""
        monkeypatch.setattr(sys, "maxsize", 2**63 - 1)
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "version_info", (3, 99, 0, "final", 0))
        assert voice_install.platform_support()[0]


# ---------------------------------------------------------------------------
# Sticky verdicts
# ---------------------------------------------------------------------------


class TestVerdicts:
    def test_round_trip(self):
        voice_install.write_verdict("NO_WHEEL", "nope")
        assert voice_install.read_verdict() == ("NO_WHEEL", "nope")
        assert voice_install.unsupported_reason() == "nope"

    def test_a_different_environment_ignores_the_verdict(self, monkeypatch):
        voice_install.write_verdict("NO_WHEEL", "nope")
        monkeypatch.setattr(voice_install, "_verdict_key", lambda: "some other machine")
        assert voice_install.read_verdict() == ("", "")

    def test_clear_removes_it(self):
        voice_install.write_verdict("NO_WHEEL", "nope")
        voice_install.clear_verdict()
        assert voice_install.read_verdict() == ("", "")
        assert voice_install.unsupported_reason() == ""

    def test_unsupported_reason_is_memoised(self, monkeypatch):
        calls = []
        monkeypatch.setattr(voice_install, "read_verdict", lambda: (calls.append(1), ("", ""))[1])
        voice_install.unsupported_reason()
        voice_install.unsupported_reason()
        assert len(calls) == 1

    def test_platform_gate_wins_over_a_stored_verdict(self, monkeypatch):
        monkeypatch.setattr(voice_install, "platform_support", lambda: (False, "32-bit Python"))
        assert voice_install.unsupported_reason() == "32-bit Python"


# ---------------------------------------------------------------------------
# The install command
# ---------------------------------------------------------------------------


class TestInstallPlan:
    def test_source_checkout_uses_additive_uv_pip(self, monkeypatch):
        monkeypatch.setattr(voice_install, "_is_source_checkout", lambda: True)
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        plan = voice_install.install_plan()
        assert plan.method == "uv-project"
        assert plan.argv[:3] == ("/usr/bin/uv", "pip", "install")
        assert "--python" in plan.argv and sys.executable in plan.argv
        assert plan.follow_up == "uv sync --extra voice"

    def test_source_checkout_never_runs_uv_sync(self, monkeypatch):
        """`uv sync` is exact: it uninstalls whatever the lockfile omits, out
        from under the running process."""
        monkeypatch.setattr(voice_install, "_is_source_checkout", lambda: True)
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        assert "sync" not in voice_install.install_plan().argv

    def test_uv_tool_never_rebuilds_its_own_venv(self, monkeypatch):
        """`uv tool install --force` deletes the venv this process runs from."""
        _no_source_checkout(monkeypatch)
        monkeypatch.setattr(sys, "executable", "/home/u/.local/share/uv/tools/yeaboi/bin/python")
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        plan = voice_install.install_plan()
        assert plan.method == "uv-tool"
        assert "tool" not in plan.argv
        assert plan.follow_up == "uv tool install --force 'yeaboi[voice]'"
        assert plan.durable is False

    def test_uv_tool_without_uv_on_path_is_blocked(self, monkeypatch):
        _no_source_checkout(monkeypatch)
        monkeypatch.setattr(sys, "executable", "/home/u/.local/share/uv/tools/yeaboi/bin/python")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        plan = voice_install.install_plan()
        assert plan.method == "blocked"
        assert "uv" in plan.blocked

    def test_pipx_injects_by_venv_name(self, monkeypatch):
        _no_source_checkout(monkeypatch)
        monkeypatch.setattr(sys, "executable", "/home/u/.local/pipx/venvs/scrum-agent/bin/python")
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name == "pipx" else None)
        monkeypatch.setattr(Path, "resolve", lambda self, **_kw: self)
        plan = voice_install.install_plan()
        assert plan.method == "pipx"
        assert plan.argv[:3] == ("/usr/bin/pipx", "inject", "scrum-agent")
        assert plan.durable is True

    def test_plain_pip_targets_this_interpreter(self, monkeypatch):
        _no_source_checkout(monkeypatch)
        monkeypatch.setattr(sys, "executable", "/usr/local/venv/bin/python")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        plan = voice_install.install_plan()
        assert plan.method == "pip"
        assert plan.argv[:4] == ("/usr/local/venv/bin/python", "-m", "pip", "install")
        assert "--only-binary=:all:" in plan.argv

    def test_wheels_only_by_default_and_opt_out_via_env(self, monkeypatch):
        _no_source_checkout(monkeypatch)
        monkeypatch.setattr("shutil.which", lambda _name: None)
        assert "--only-binary=:all:" in voice_install.install_plan().argv
        monkeypatch.setenv(voice_install._ALLOW_BUILD_ENV, "1")
        assert "--only-binary=:all:" not in voice_install.install_plan().argv

    def test_externally_managed_python_is_blocked_before_spawning(self, monkeypatch):
        monkeypatch.setattr(voice_install, "_externally_managed", lambda: True)
        plan = voice_install.install_plan()
        assert plan.method == "blocked"
        assert plan.argv == ()
        assert "system-managed" in plan.blocked

    def test_unsupported_platform_is_blocked(self, monkeypatch):
        monkeypatch.setattr(voice_install, "platform_support", lambda: (False, "musl libc"))
        assert voice_install.install_plan().blocked == "musl libc"

    def test_nothing_from_the_environment_reaches_argv(self, monkeypatch):
        """argv is built from module constants and sys.executable only."""
        _no_source_checkout(monkeypatch)
        monkeypatch.setattr("shutil.which", lambda _name: None)
        monkeypatch.setenv("EVIL", "; rm -rf /")
        argv = voice_install.install_plan().argv
        assert "; rm -rf /" not in " ".join(argv)
        assert set(argv) & set(os.environ.values()) <= {sys.executable}

    def test_the_extra_is_never_installed_over_the_running_app(self, monkeypatch):
        """`yeaboi[voice]` would reinstall yeaboi itself, mid-run."""
        _no_source_checkout(monkeypatch)
        monkeypatch.setattr("shutil.which", lambda _name: None)
        joined = " ".join(voice_install.install_plan().argv)
        assert "yeaboi" not in joined.replace(sys.executable, "")


class TestPipxVenvName:
    def test_legacy_distribution_name_is_read_off_the_path(self, monkeypatch):
        monkeypatch.setattr(sys, "executable", "/home/u/.local/pipx/venvs/scrum-agent/bin/python")
        monkeypatch.setattr(Path, "resolve", lambda self, **_kw: self)
        assert voice_install._pipx_venv_name() == "scrum-agent"

    def test_a_non_pipx_path_yields_nothing(self, monkeypatch):
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
        monkeypatch.setattr(Path, "resolve", lambda self, **_kw: self)
        assert voice_install._pipx_venv_name() == ""

    def test_a_hostile_venv_name_is_refused(self, monkeypatch):
        monkeypatch.setattr(sys, "executable", "/home/u/.local/pipx/venvs/a b;rm -rf/bin/python")
        monkeypatch.setattr(Path, "resolve", lambda self, **_kw: self)
        assert voice_install._pipx_venv_name() == ""


# ---------------------------------------------------------------------------
# Narration and failure classification
# ---------------------------------------------------------------------------


class TestNarrate:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("Collecting faster-whisper", "resolving faster-whisper"),
            (
                "  Downloading ctranslate2-4.8.1-cp313-macosx_11_0_arm64.whl (37.3 MB)",
                "downloading ctranslate2 (37.3 MB)",
            ),
            ("  Downloading numpy-2.4.3-cp313.whl", "downloading numpy"),
            ("  Using cached tokenizers-0.23.1-cp310-abi3.whl", "using cached tokenizers"),
            ("Installing collected packages: numpy, av", "installing packages"),
            ("Resolved 14 packages in 431ms", "resolved 14 packages"),
            ("Prepared 9 packages in 12.30s", "downloaded 9 packages"),
            ("Installed 9 packages in 88ms", "installed 9 packages"),
            ("  Building wheel for av (pyproject.toml)", "building av"),
        ],
    )
    def test_known_shapes(self, line, expected):
        assert voice_install.narrate(line) == expected

    def test_unrecognised_noise_is_dropped_so_the_previous_phrase_survives(self):
        assert voice_install.narrate("  WARNING: something about a cache directory") == ""
        assert voice_install.narrate("") == ""


class TestClassifyFailure:
    @pytest.mark.parametrize(
        ("output", "code"),
        [
            ("ERROR: No matching distribution found for onnxruntime", "NO_WHEEL"),
            ("Could not find a version that satisfies the requirement ctranslate2", "NO_WHEEL"),
            ("ctranslate2 does not have a wheel for the current platform", "NO_WHEEL"),
            ("faster-whisper requires a different Python: 3.99 not in <3.13", "NO_WHEEL"),
            ("error: externally-managed-environment", "EXTERNALLY_MANAGED"),
            ("Temporary failure in name resolution", "NO_NETWORK"),
            ("SSLError: certificate verify failed", "NO_NETWORK"),
            ("OSError: [Errno 28] No space left on device", "DISK_FULL"),
            ("PermissionError: [Errno 13] Permission denied: '/usr/lib'", "PERMISSION"),
        ],
    )
    def test_table(self, output, code):
        assert voice_install.classify_failure(1, output)[0] == code

    def test_no_wheel_names_the_python_version_not_just_the_platform(self):
        _code, message = voice_install.classify_failure(1, "No matching distribution found")
        assert f"CPython {sys.version_info[0]}.{sys.version_info[1]}" in message

    def test_unknown_keeps_the_tail_for_the_log_pointer(self):
        code, message = voice_install.classify_failure(2, "line one\nline two\nsomething odd")
        assert code == "UNKNOWN"
        assert "something odd" in message

    def test_only_hopeless_codes_are_permanent(self):
        assert voice_install.PERMANENT_CODES == {"NO_WHEEL", "EXTERNALLY_MANAGED"}


# ---------------------------------------------------------------------------
# Child environment
# ---------------------------------------------------------------------------


class TestChildEnv:
    def test_every_progress_bar_and_colour_source_is_disabled(self):
        env = voice_install._child_env()
        assert env["PIP_PROGRESS_BAR"] == "off"
        assert env["UV_NO_PROGRESS"] == "1"
        assert env["NO_COLOR"] == "1"
        assert env["TERM"] == "dumb"
        assert env["PIP_NO_INPUT"] == "1"
        # A wrapped package name would defeat narrate() as surely as a colour code.
        assert env["COLUMNS"] == "200"


# ---------------------------------------------------------------------------
# Running the installer — fake Popen
# ---------------------------------------------------------------------------


class _FakePopen:
    """Scripted stand-in for a package manager."""

    instances: list[_FakePopen] = []

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.stdout = iter(self.lines)
        self.terminated = False
        self.killed = False
        self._polls = 0
        _FakePopen.instances.append(self)

    lines: list[str] = []
    exit_code = 0
    stay_alive = 0

    def poll(self):
        self._polls += 1
        if self._polls <= self.stay_alive:
            return None
        self.returncode = self.exit_code
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):  # pragma: no cover - only when wait() times out
        self.killed = True

    def wait(self, timeout=None):
        self.returncode = getattr(self, "returncode", self.exit_code)
        return self.returncode


@pytest.fixture
def fake_popen(monkeypatch):
    _FakePopen.instances = []

    def make(lines, exit_code=0, stay_alive=0):
        cls = type("_Scripted", (_FakePopen,), {"lines": lines, "exit_code": exit_code, "stay_alive": stay_alive})
        monkeypatch.setattr(voice_install.subprocess, "Popen", cls)
        return cls

    return make


_PIP_TRANSCRIPT = [
    "Collecting sounddevice\n",
    "  Downloading sounddevice-0.5.5-py3-none-any.whl (32 kB)\n",
    "Collecting faster-whisper\n",
    "  Downloading ctranslate2-4.8.1-cp313.whl (37.3 MB)\n",
    "Installing collected packages: sounddevice, ctranslate2\n",
    "Successfully installed sounddevice-0.5.5 ctranslate2-4.8.1\n",
]


class TestInstallPackages:
    def _plan(self):
        return voice_install.InstallPlan("pip", ("/bin/true",), "/bin/true", True, "", "")

    def test_narrated_progress_in_order(self, fake_popen, monkeypatch):
        fake_popen(_PIP_TRANSCRIPT)
        monkeypatch.setattr(voice_install, "refresh_imports", lambda: None)
        monkeypatch.setattr(voice_install, "verify_installed", lambda: (True, ""))
        seen: list[str] = []
        ok, message = voice_install.install_packages(seen.append, plan=self._plan())
        assert ok and message == ""
        assert seen[0] == "resolving sounddevice"
        assert "downloading ctranslate2 (37.3 MB)" in seen
        assert seen[-1] == "installed packages"

    def test_pipes_are_never_inherited(self, fake_popen, monkeypatch):
        """A child writing to the TTY under a Rich Live corrupts the display."""
        fake_popen(_PIP_TRANSCRIPT)
        monkeypatch.setattr(voice_install, "refresh_imports", lambda: None)
        monkeypatch.setattr(voice_install, "verify_installed", lambda: (True, ""))
        voice_install.install_packages(lambda _line: None, plan=self._plan())
        kwargs = _FakePopen.instances[-1].kwargs
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.STDOUT

    def test_exit_zero_but_not_importable_asks_for_a_restart(self, fake_popen, monkeypatch):
        fake_popen(_PIP_TRANSCRIPT)
        monkeypatch.setattr(voice_install, "refresh_imports", lambda: None)
        monkeypatch.setattr(voice_install, "verify_installed", lambda: (False, "sounddevice is not importable"))
        ok, message = voice_install.install_packages(lambda _line: None, plan=self._plan())
        assert not ok
        assert "restart" in message.lower()

    def test_no_wheel_failure_is_recorded_as_permanent(self, fake_popen):
        fake_popen(["ERROR: No matching distribution found for onnxruntime\n"], exit_code=1)
        ok, message = voice_install.install_packages(lambda _line: None, plan=self._plan())
        assert not ok
        assert "No prebuilt speech-engine wheel" in message
        assert voice_install.read_verdict()[0] == "NO_WHEEL"

    def test_a_retryable_failure_is_not_recorded(self, fake_popen):
        fake_popen(["Temporary failure in name resolution\n"], exit_code=1)
        ok, _message = voice_install.install_packages(lambda _line: None, plan=self._plan())
        assert not ok
        assert voice_install.read_verdict() == ("", "")

    def test_a_blocked_plan_never_spawns_anything(self, monkeypatch):
        sentinel = lambda *_a, **_k: pytest.fail("Popen must not be called for a blocked plan")  # noqa: E731
        monkeypatch.setattr(voice_install.subprocess, "Popen", sentinel)
        plan = voice_install.InstallPlan("blocked", (), "", False, "musl libc", "")
        ok, message = voice_install.install_packages(lambda _line: None, plan=plan)
        assert not ok
        assert message == "musl libc"


class TestInstallPackagesRealChild:
    """The one place a real process runs — a fake Popen cannot prove the reader
    thread, the merged stderr and the non-blocking wait work together."""

    def test_streams_both_stdout_and_stderr_from_a_live_process(self, monkeypatch):
        program = (
            "import sys;"
            "print('Collecting sounddevice');"
            "print('  Downloading ctranslate2-4.8.1-cp313.whl (37.3 MB)', file=sys.stderr);"
            "print('Successfully installed sounddevice-0.5.5')"
        )
        plan = voice_install.InstallPlan("pip", (sys.executable, "-c", program), "python -c ...", True, "", "")
        monkeypatch.setattr(voice_install, "refresh_imports", lambda: None)
        monkeypatch.setattr(voice_install, "verify_installed", lambda: (True, ""))
        seen: list[str] = []
        ok, _message = voice_install.install_packages(seen.append, plan=plan)
        assert ok
        assert "resolving sounddevice" in seen
        assert "downloading ctranslate2 (37.3 MB)" in seen  # arrived via stderr

    def test_a_nonzero_exit_is_classified_from_the_tail(self):
        program = "import sys; print('ERROR: No matching distribution found for onnxruntime'); sys.exit(1)"
        plan = voice_install.InstallPlan("pip", (sys.executable, "-c", program), "python -c ...", True, "", "")
        ok, message = voice_install.install_packages(lambda _line: None, plan=plan)
        assert not ok
        assert "No prebuilt speech-engine wheel" in message

    def test_cancel_terminates_a_running_child(self):
        plan = voice_install.InstallPlan(
            "pip", (sys.executable, "-c", "import time; time.sleep(30)"), "sleep", True, "", ""
        )
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        started = time.monotonic()
        ok, message = voice_install.install_packages(lambda _line: None, cancel, plan=plan)
        assert not ok
        assert "cancelled" in message.lower()
        assert time.monotonic() - started < 10

    def test_a_timeout_points_at_the_manual_command(self):
        plan = voice_install.InstallPlan(
            "pip", (sys.executable, "-c", "import time; time.sleep(30)"), "pip install x", True, "", ""
        )
        ok, message = voice_install.install_packages(lambda _line: None, timeout=0.5, plan=plan)
        assert not ok
        assert "pip install x" in message


# ---------------------------------------------------------------------------
# Making the install visible in-process
# ---------------------------------------------------------------------------


class TestRefreshImports:
    def test_every_memo_that_answers_is_voice_installed_is_dropped(self, monkeypatch):
        """All three caches were written when the answer could not change
        mid-process. It can now."""
        from yeaboi import voice
        from yeaboi.ui.shared import _tips, _voice_input

        calls: list[str] = []
        monkeypatch.setattr("importlib.invalidate_caches", lambda: calls.append("importlib"))
        monkeypatch.setattr(voice, "reset_probe", lambda: calls.append("probe"))
        monkeypatch.setattr(_voice_input, "reset_voice_chip", lambda: calls.append("chip"))
        monkeypatch.setattr(_tips.get_tips, "cache_clear", lambda: calls.append("tips"))
        voice_install.refresh_imports()
        assert set(calls) == {"importlib", "probe", "chip", "tips"}

    def test_a_stale_chip_survives_until_refresh(self, monkeypatch):
        """The chip memo assumed availability could not change mid-process. It
        can now, so refresh_imports has to be the thing that drops it."""
        from yeaboi import voice
        from yeaboi.ui.shared import _voice_input

        _voice_input.reset_voice_chip()
        monkeypatch.setattr(voice, "voice_state", lambda: "unsupported")
        assert _voice_input.voice_chip()[0] == _voice_input._CHIP_OFF
        monkeypatch.setattr(voice, "voice_state", lambda: "ready")
        assert _voice_input.voice_chip()[0] == _voice_input._CHIP_OFF  # still memoised
        voice_install.refresh_imports()
        assert _voice_input.voice_chip() == (_voice_input._CHIP_ON, _voice_input._CHIP_ON_STYLE)


class TestVerifyInstalled:
    def test_a_namespace_package_does_not_count_as_installed(self, monkeypatch):
        import importlib.machinery

        spec = importlib.machinery.ModuleSpec("sounddevice", loader=None)  # origin stays None
        monkeypatch.setattr("importlib.util.find_spec", lambda _name: spec)
        ok, reason = voice_install.verify_installed()
        assert not ok
        assert "sounddevice" in reason

    def test_a_real_module_counts(self, monkeypatch):
        import importlib.machinery

        spec = importlib.machinery.ModuleSpec("x", loader=None, origin="/tmp/x.py")
        monkeypatch.setattr("importlib.util.find_spec", lambda _name: spec)
        assert voice_install.verify_installed() == (True, "")


# ---------------------------------------------------------------------------
# The speech model
# ---------------------------------------------------------------------------


class TestModelPaths:
    def test_repo_id(self):
        assert voice_install.model_repo_id("base") == "Systran/faster-whisper-base"

    def test_hf_hub_cache_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
        assert voice_install.model_cache_dir() == tmp_path / "hub"

    def test_hf_home_is_honoured(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
        monkeypatch.setenv("HF_HOME", str(tmp_path))
        assert voice_install.model_cache_dir() == tmp_path / "hub"

    def test_bytes_on_disk_counts_the_incomplete_blob(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
        blobs = tmp_path / "models--Systran--faster-whisper-base" / "blobs"
        blobs.mkdir(parents=True)
        (blobs / "abc").write_bytes(b"x" * 100)
        (blobs / "def.incomplete").write_bytes(b"y" * 50)
        assert voice_install.model_bytes_on_disk("base") == 150

    def test_not_cached_without_a_model_bin(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
        snap = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        assert voice_install.model_is_cached("base") is False
        (snap / "model.bin").write_bytes(b"weights")
        assert voice_install.model_is_cached("base") is True

    def test_total_bytes_is_zero_when_offline(self, monkeypatch):
        def boom(*_a, **_k):
            raise OSError("offline")

        monkeypatch.setattr(voice_install.urllib.request, "urlopen", boom)
        assert voice_install.model_total_bytes("base") == 0


@pytest.fixture
def child_program(monkeypatch):
    """Run a stand-in child instead of the real download, keeping a real Popen.

    Patching ``voice_install.subprocess.Popen`` patches the shared module, so the
    replacement has to close over the original or it calls itself.
    """
    real_popen = subprocess.Popen

    def substitute(program: str):
        monkeypatch.setattr(
            voice_install.subprocess,
            "Popen",
            lambda _argv, **kw: real_popen([sys.executable, "-c", program], **kw),
        )

    return substitute


class TestDownloadModel:
    def test_an_unknown_size_never_reaches_a_subprocess(self, monkeypatch):
        monkeypatch.setattr(
            voice_install.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not spawn for an unknown size")
        )
        ok, message = voice_install.download_model("'; rm -rf /", lambda *_a: None)
        assert not ok
        assert "Unknown speech model size" in message

    def test_an_already_cached_model_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: True)
        monkeypatch.setattr(
            voice_install.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not re-download a cached model")
        )
        assert voice_install.download_model("base", lambda *_a: None) == (True, "")

    def test_fraction_tracks_bytes_on_disk_and_ends_at_one(self, monkeypatch, child_program):
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setattr(voice_install, "model_total_bytes", lambda _s, **_k: 1000)
        monkeypatch.setattr(voice_install, "_POLL_SECONDS", 0.01)
        grown = iter([0, 250, 500, 1000, 1000, 1000])
        monkeypatch.setattr(voice_install, "model_bytes_on_disk", lambda _s: next(grown, 1000))
        seen: list[float | None] = []
        child_program("import time; time.sleep(0.15)")
        ok, _message = voice_install.download_model("base", lambda _s, f: seen.append(f))
        assert ok
        assert seen[-1] == 1.0
        fractions = [f for f in seen if f is not None]
        assert fractions == sorted(fractions)
        assert all(f <= 1.0 for f in fractions)

    def test_an_unknown_total_reports_an_indeterminate_fraction(self, monkeypatch, child_program):
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setattr(voice_install, "model_total_bytes", lambda _s, **_k: 0)
        monkeypatch.setattr(voice_install, "model_bytes_on_disk", lambda _s: 10)
        monkeypatch.setattr(voice_install, "_POLL_SECONDS", 0.01)
        seen: list[float | None] = []
        child_program("import time; time.sleep(0.1)")
        voice_install.download_model("base", lambda _s, f: seen.append(f))
        assert any(f is None for f in seen)
        assert all(f in (None, 1.0) for f in seen)

    def test_offline_is_a_warning_not_a_broken_feature(self, monkeypatch, child_program):
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setattr(voice_install, "model_total_bytes", lambda _s, **_k: 0)
        monkeypatch.setattr(voice_install, "model_bytes_on_disk", lambda _s: 0)
        monkeypatch.setattr(voice_install, "_POLL_SECONDS", 0.01)
        child_program("import sys; print('Temporary failure in name resolution'); sys.exit(1)")
        ok, message = voice_install.download_model("base", lambda *_a: None)
        assert not ok
        assert "first dictation" in message

    def test_cancel_promises_a_resume(self, monkeypatch, child_program):
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setattr(voice_install, "model_total_bytes", lambda _s, **_k: 100)
        monkeypatch.setattr(voice_install, "model_bytes_on_disk", lambda _s: 10)
        monkeypatch.setattr(voice_install, "_POLL_SECONDS", 0.01)
        child_program("import time; time.sleep(30)")
        cancel = threading.Event()
        threading.Timer(0.2, cancel.set).start()
        ok, message = voice_install.download_model("base", lambda *_a: None, cancel)
        assert not ok
        assert "resumes" in message


class TestModelChildEnv:
    def test_xet_is_disabled_so_the_progress_bar_can_move(self, monkeypatch, child_program):
        """With Xet on, bytes stage in a separate cache and the repo dir jumps
        from ~3% to 100% at the very end — the bar would be a lie."""
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setattr(voice_install, "model_total_bytes", lambda _s, **_k: 0)
        monkeypatch.setattr(voice_install, "model_bytes_on_disk", lambda _s: 0)
        monkeypatch.setattr(voice_install, "_POLL_SECONDS", 0.01)
        seen: dict = {}
        real_popen = subprocess.Popen

        def capture(_argv, **kw):
            seen.update(kw.get("env") or {})
            return real_popen([sys.executable, "-c", "pass"], **kw)

        monkeypatch.setattr(voice_install.subprocess, "Popen", capture)
        voice_install.download_model("base", lambda *_a: None)
        assert seen["HF_HUB_DISABLE_XET"] == "1"
        assert seen["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"  # no tqdm into the Live

    def test_the_size_is_whitelisted_before_it_reaches_the_child_program(self, monkeypatch):
        captured: list = []
        real_popen = subprocess.Popen

        def capture(argv, **kw):
            captured.append(argv)
            return real_popen([sys.executable, "-c", "pass"], **kw)

        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setattr(voice_install, "model_total_bytes", lambda _s, **_k: 0)
        monkeypatch.setattr(voice_install, "model_bytes_on_disk", lambda _s: 0)
        monkeypatch.setattr(voice_install, "_POLL_SECONDS", 0.01)
        monkeypatch.setattr(voice_install.subprocess, "Popen", capture)
        voice_install.download_model("large-v3", lambda *_a: None)
        assert "'large-v3'" in captured[0][2]


class TestSizeEstimate:
    def test_the_model_is_dropped_from_the_total_once_cached(self, monkeypatch):
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: True)
        assert voice_install.size_estimate_mb() == voice_install.PACKAGES_MB

    def test_a_bigger_model_makes_a_bigger_promise(self, monkeypatch):
        monkeypatch.setattr(voice_install, "model_is_cached", lambda _s: False)
        monkeypatch.setenv("VOICE_MODEL", "large-v3")
        assert voice_install.size_estimate_mb() == voice_install.PACKAGES_MB + 3100


class TestLockLiveness:
    """The stale-lock probe must never be a kill.

    ``os.kill(pid, 0)`` is a liveness probe on POSIX only. On Windows CPython it
    calls ``TerminateProcess`` for every signal but the two console events, so
    using it here would terminate the other yeaboi window mid-session — on a
    platform that is one of the four the speech-engine wheels target, where two
    open windows is an ordinary situation rather than an edge case.
    """

    def test_never_signals_on_windows(self, monkeypatch):
        monkeypatch.setattr(voice_install.sys, "platform", "win32")
        monkeypatch.setattr(
            voice_install.os,
            "kill",
            lambda *_a: pytest.fail("os.kill on Windows terminates the target"),
        )
        assert voice_install._pid_alive(4321) is None

    def test_probes_on_posix(self, monkeypatch):
        monkeypatch.setattr(voice_install.sys, "platform", "linux")
        assert voice_install._pid_alive(os.getpid()) is True

    def test_a_dead_pid_reads_as_dead_on_posix(self, monkeypatch):
        monkeypatch.setattr(voice_install.sys, "platform", "linux")

        def _boom(*_a):
            raise ProcessLookupError

        monkeypatch.setattr(voice_install.os, "kill", _boom)
        assert voice_install._pid_alive(4321) is False

    def test_windows_keeps_a_fresh_lock(self, monkeypatch, tmp_path):
        """With no probe available, age is the only evidence — and a lock
        written seconds ago belongs to a live install."""
        monkeypatch.setattr(voice_install.sys, "platform", "win32")
        lock = voice_install._Lockfile()
        lock.path.write_text("4321", encoding="utf-8")
        assert lock._stale() is False
        assert lock.path.exists()

    def test_windows_breaks_an_ancient_lock(self, monkeypatch):
        """Otherwise one crashed run leaves a machine that can never install."""
        monkeypatch.setattr(voice_install.sys, "platform", "win32")
        lock = voice_install._Lockfile()
        lock.path.write_text("4321", encoding="utf-8")
        old = time.time() - voice_install._LOCK_STALE_SECONDS - 60
        os.utime(lock.path, (old, old))
        assert lock._stale() is True
        assert not lock.path.exists()


class TestVerdictExpiry:
    """ "Permanent" means "nothing you can do right now", not "true forever"."""

    def test_a_fresh_verdict_is_honoured(self):
        voice_install.write_verdict("NO_WHEEL", "no wheel yet")
        assert voice_install.read_verdict() == ("NO_WHEEL", "no wheel yet")

    def test_an_expired_verdict_reads_as_absent(self, monkeypatch):
        """A too-new CPython is deliberately not pre-gated because it "resolves
        itself when the wheels land" — but the verdict it produces is keyed on
        platform, Python version and interpreter path, none of which change when
        the cp3XX wheel is finally published. Without expiry, one attempt during
        that window condemns the machine for good."""
        voice_install.write_verdict("NO_WHEEL", "no wheel yet")
        later = time.time() + voice_install._VERDICT_TTL_SECONDS + 60
        monkeypatch.setattr(voice_install.time, "time", lambda: later)
        voice_install.reset_unsupported_cache()
        assert voice_install.read_verdict() == ("", "")
        assert voice_install.unsupported_reason() == ""

    def test_a_stampless_verdict_is_treated_as_fresh(self, monkeypatch, tmp_path):
        """Files written by an earlier build carry no timestamp. Reading a
        missing stamp as 1970 would expire every one of them on sight."""
        path = tmp_path / "voice_install.json"
        path.write_text(
            json.dumps({"key": voice_install._verdict_key(), "code": "NO_WHEEL", "message": "old file"}),
            encoding="utf-8",
        )
        assert voice_install.read_verdict() == ("NO_WHEEL", "old file")


class TestInstallPlanVerdictBypass:
    def test_a_stored_verdict_blocks_the_plan(self):
        voice_install.write_verdict("NO_WHEEL", "no wheel for this host")
        assert voice_install.install_plan().blocked == "no wheel for this host"

    def test_ignore_verdict_gets_a_real_plan(self, monkeypatch):
        """--install-voice is the explicit retry, and refusing it on the
        strength of a cached failure leaves no way back at all."""
        _no_source_checkout(monkeypatch)
        monkeypatch.setattr(voice_install, "_externally_managed", lambda: False)
        monkeypatch.setattr(voice_install, "_pipx_venv_name", lambda: "")
        voice_install.write_verdict("NO_WHEEL", "no wheel for this host")
        plan = voice_install.install_plan(ignore_verdict=True)
        assert plan.blocked == ""
        assert plan.method == "pip"

    def test_ignore_verdict_still_honours_the_platform_gate(self, monkeypatch):
        """The one thing here that is genuinely certain, not cached."""
        monkeypatch.setattr(voice_install, "platform_support", lambda: (False, "32-bit Python"))
        assert voice_install.install_plan(ignore_verdict=True).blocked == "32-bit Python"


class TestRefreshImportsDropsTheVerdictMemo:
    def test_the_memoised_verdict_is_cleared(self, monkeypatch):
        """Four caches answer "is voice installed?" and refresh_imports has to
        drop all of them, or the app renders "off" for the rest of the run."""
        voice_install.write_verdict("NO_WHEEL", "stale answer")
        assert voice_install.unsupported_reason() == "stale answer"
        voice_install.clear_verdict()
        monkeypatch.setattr(voice_install, "_unsupported_cache", "stale answer")
        voice_install.refresh_imports()
        assert voice_install.unsupported_reason() == ""
