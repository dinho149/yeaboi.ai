"""Hatchling build hook: compile the Go sidecar into the wheel being built.

Target selection comes from ``YEABOI_CORE_TARGET`` as ``<goos>/<goarch>``
(e.g. ``linux/arm64``); unset means the host platform. Pure Go with
``CGO_ENABLED=0``, so any host cross-compiles every target — the release
workflow builds all wheels on one runner by looping this env var.

The linux wheels carry both a manylinux and a musllinux tag: the binary is
static (no libc at all), so one artifact genuinely serves both.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# goos/goarch → wheel platform tag. The release workflow loops over these keys.
WHEEL_TAGS = {
    "linux/amd64": "manylinux2014_x86_64.musllinux_1_1_x86_64",
    "linux/arm64": "manylinux2014_aarch64.musllinux_1_1_aarch64",
    "darwin/amd64": "macosx_10_12_x86_64",
    "darwin/arm64": "macosx_11_0_arm64",
    "windows/amd64": "win_amd64",
}


def _host_target() -> str:
    goos = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system(), "")
    goarch = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine(), "")
    return f"{goos}/{goarch}"


class CoreBinaryHook(BuildHookInterface):
    """Builds ``go/cmd/yeaboi-core`` and force-includes it as package data."""

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return
        target = os.environ.get("YEABOI_CORE_TARGET", "").strip() or _host_target()
        if target not in WHEEL_TAGS:
            raise RuntimeError(f"unsupported yeaboi-core target {target!r}; known: {sorted(WHEEL_TAGS)}")
        goos, goarch = target.split("/")

        repo_root = Path(self.root).resolve().parents[1]
        go_dir = repo_root / "go"
        if not (go_dir / "go.mod").is_file():
            raise RuntimeError(f"yeaboi-core wheels build from the repo checkout only ({go_dir}/go.mod not found)")

        binary_name = "yeaboi-core.exe" if goos == "windows" else "yeaboi-core"
        out = Path(self.root) / "build" / f"{goos}-{goarch}" / binary_name
        out.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "CGO_ENABLED": "0", "GOOS": goos, "GOARCH": goarch}
        subprocess.run(  # noqa: S603 — argv is fixed; only the target dirs vary
            ["go", "build", "-trimpath", "-ldflags", "-s -w", "-o", str(out), "./cmd/yeaboi-core"],
            cwd=go_dir,
            env=env,
            check=True,
        )

        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{WHEEL_TAGS[target]}"
        build_data["force_include"][str(out)] = f"yeaboi_core/bin/{binary_name}"
