"""How the desktop app is packaged, signed and shipped.

Everything here fails in the same expensive place otherwise: a three-OS signed
build, twenty minutes in, or — worse — an installed app that starts with no
backend because the bundled Python landed somewhere the sidecar does not look.
None of it needs Node, electron-builder or a certificate to check.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from gen_desktop_icons import DMG_ICON_Y, DMG_SIZE  # noqa: E402

DESKTOP = ROOT / "desktop"
BUILDER = DESKTOP / "electron-builder.yml"
RELEASE = ROOT / ".github" / "workflows" / "desktop-release.yml"


@pytest.fixture(scope="module")
def builder() -> dict:
    return yaml.safe_load(BUILDER.read_text())


@pytest.fixture(scope="module")
def release() -> dict:
    return yaml.safe_load(RELEASE.read_text())


class TestThePythonBundleLandsWhereTheSidecarLooks:
    """`sidecar.ts` spawns `<resourcesPath>/py/bin/python3`. Three files have to
    agree on that path, and nothing at build time notices when they stop."""

    def test_the_stage_is_shipped_as_an_extra_resource_named_py(self, builder: dict):
        extras = {(entry["from"], entry["to"]) for entry in builder["extraResources"]}
        assert ("resources/py", "py") in extras

    def test_the_stage_is_excluded_from_the_asar(self, builder: dict):
        # An executable inside an asar cannot be spawned; packing it there would
        # also double the app's size, silently.
        assert "!resources/py/**" in builder["files"]

    def test_the_staging_script_writes_into_that_stage(self):
        script = (DESKTOP / "scripts" / "fetch-python.mjs").read_text()
        assert "join(DESKTOP, 'resources', 'py')" in script

    def test_the_sidecar_spawns_it_from_resources(self):
        sidecar = (DESKTOP / "src" / "main" / "sidecar.ts").read_text()
        assert "${process.resourcesPath}/py/${python}" in sidecar
        assert "'bin/python3'" in sidecar and "'python.exe'" in sidecar


class TestSigning:
    def test_the_hardened_runtime_is_on_and_notarized(self, builder: dict):
        assert builder["mac"]["hardenedRuntime"] is True
        assert builder["mac"]["notarize"] is True

    @pytest.mark.parametrize("name", ["entitlements.mac.plist", "entitlements.mac.inherit.plist"])
    def test_both_entitlement_files_exist_and_are_referenced(self, builder: dict, name: str):
        assert (DESKTOP / "build" / name).exists()
        assert f"build/{name}" in {builder["mac"]["entitlements"], builder["mac"]["entitlementsInherit"]}

    def test_the_microphone_is_granted_and_explained(self, builder: dict):
        """M11 put dictation in the renderer. Without the entitlement macOS
        denies the device; without the usage string the app is rejected at
        notarization, and the prompt a person sees is blank either way."""
        for name in ("entitlements.mac.plist", "entitlements.mac.inherit.plist"):
            assert "com.apple.security.device.audio-input" in (DESKTOP / "build" / name).read_text()
        description = builder["mac"]["extendInfo"]["NSMicrophoneUsageDescription"]
        assert "microphone" in description.lower() and len(description) > 40

    def test_the_loopback_server_is_granted(self, builder: dict):
        # The backend binds 127.0.0.1 and the board servers bind their own ports.
        entitlements = (DESKTOP / "build" / "entitlements.mac.plist").read_text()
        assert "com.apple.security.network.server" in entitlements
        assert "com.apple.security.network.client" in entitlements

    def test_the_dock_climb_declares_its_apple_events(self, builder: dict):
        assert "NSAppleEventsUsageDescription" in builder["mac"]["extendInfo"]

    def test_windows_signs_through_azure_trusted_signing(self, builder: dict):
        assert set(builder["win"]["azureSignOptions"]) >= {
            "publisherName",
            "endpoint",
            "certificateProfileName",
            "codeSigningAccountName",
        }


class TestUpdates:
    def test_mac_ships_a_zip_beside_the_dmg(self, builder: dict):
        """electron-updater updates a mac app from the zip. Publishing only the
        dmg leaves every mac install permanently on the version it was
        downloaded at, with a working-looking updater."""
        targets = {entry["target"] for entry in builder["mac"]["target"]}
        assert {"dmg", "zip"} <= targets

    def test_the_publish_target_is_this_repository(self, builder: dict):
        assert builder["publish"]["provider"] == "github"
        assert builder["publish"]["owner"] == "dinho149"
        assert builder["publish"]["repo"] == "yeaboi.ai"


class TestTheReleaseWorkflow:
    def test_it_refuses_anything_that_is_not_a_final(self):
        # An rc reaches PyPI on every version-moving push to main; a signed
        # installer around one would carry a notarized ticket for unreviewed code.
        assert "^[0-9]+\\.[0-9]+\\.[0-9]+$" in RELEASE.read_text()

    def test_it_checks_pypi_before_three_runners_start(self):
        assert "pypi.org/pypi/yeaboi/$version/json" in RELEASE.read_text()

    def test_every_matrix_target_has_a_pinned_python_runtime(self, release: dict):
        """The staging script's TARGETS table and this matrix must cover the
        same set — a runner with no entry fails after `npm ci`, not before."""
        script = (DESKTOP / "scripts" / "fetch-python.mjs").read_text()
        staged = set(re.findall(r"'(darwin|win32|linux)-(arm64|x64)':", script))
        runners = {
            ("darwin" if "macos" in entry["os"] else "win32" if "windows" in entry["os"] else "linux", entry["arch"])
            for entry in release["jobs"]["build"]["strategy"]["matrix"]["include"]
        }
        assert runners <= staged, f"no staged runtime for {sorted(runners - staged)}"

    def test_it_asks_gatekeeper_what_it_thinks_of_the_mac_build(self):
        assert "spctl --assess" in RELEASE.read_text()

    def test_the_app_carries_the_version_of_the_yeaboi_it_bundles(self, release: dict):
        steps = release["jobs"]["build"]["steps"]
        stamp = next(step for step in steps if step.get("name") == "Stamp the app version")
        assert "npm version" in stamp["run"]
        assert "needs.resolve.outputs.version" in stamp["run"]

    def test_it_never_rides_the_python_release(self, release: dict):
        """Deliberately not `push: branches: [main]`. The desktop wraps a wheel
        that already exists; nothing about merging to main should build one."""
        # PyYAML parses the `on:` key as the boolean True.
        triggers = release[True]
        assert set(triggers) == {"workflow_dispatch", "push"}
        assert triggers["push"] == {"tags": ["desktop-v*"]}


class TestTheDmgWindowAndItsBackdropAgree:
    def test_the_icons_sit_on_the_line_the_backdrop_draws(self, builder: dict):
        assert builder["dmg"]["window"] == {"width": DMG_SIZE[0], "height": DMG_SIZE[1]}
        assert {entry["y"] for entry in builder["dmg"]["contents"]} == {DMG_ICON_Y}
