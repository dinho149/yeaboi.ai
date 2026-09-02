"""TUI parity — the constructs beneath a capability must reach the desktop too.

``test_surface_parity.py`` asks a coarse question: does this capability exist on
every surface? It is the right question for a mode, and the wrong one for the
things a mode is made of. A settings section, a slash command, a keyboard
gesture — none of them is a capability, and all of them can land in the terminal
alone without a single parity row noticing.

So this file is the second registry, and it works the way the first one does:
discover the terminal's own tables (never a hand-copy of them), hold them
against the desktop's committed manifest, and fail two-way. What the desktop
deliberately does differently is named in ``TERMINAL_ONLY`` with the reason —
and the reason has to be a real one, because every entry here exists because a
terminal could not do the ordinary thing and a window can.

This is the file the M12 parity flip added. The rollout ledger it retires — the
``Exempt("desktop: scheduled milestone M<n>")`` rows each milestone burned down
— is now closed by :class:`TestTheRolloutIsOver`: a scheduled exemption cannot
come back, because there is nothing left to schedule.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.unit.test_surface_parity import CAPABILITIES, Exempt

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "contracts" / "v1" / "routes_manifest.json"

_HOW_TO = (
    "Fix: give the desktop the construct (yeaboi-desktop's src/renderer/, then `make gen-manifest` "
    "there and land the regenerated manifest here), or record it in TERMINAL_ONLY with the reason."
)


@pytest.fixture(scope="module")
def manifest() -> dict:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2, "routes_manifest.json schema_version must be 2"
    return data


# ---------------------------------------------------------------------------
# What the desktop deliberately does differently.
#
# Every one of these exists because the terminal could not do the ordinary
# thing. A window can, so the ordinary thing is what it offers — and offering
# the terminal's workaround as well would be a second way to do one job.
# ---------------------------------------------------------------------------

TERMINAL_ONLY: dict[str, str] = {
    "/image": "a terminal escape hatch for a clipboard it cannot read; the window pastes an image with the paste key",
    "/paste": "the terminal's own paste mangles line breaks and applies flow control; a window's does neither",
    "/voice": "double-tap Space exists because a terminal never sees a key released; the composer has a mic button",
    "/quit": "a window is closed, or navigated away from — there is no single-screen takeover to leave",
    "double-tap-space": "a terminal cannot detect key release at all, which is the whole reason for the gesture",
    "esc-esc": "the double press disambiguates Esc from an escape sequence; a window has no ambiguity to resolve",
    "bracketed-paste": "a terminal mode, negotiated with the terminal — a DOM paste event carries the text whole",
    "too-small-guard": "the window has a minimum size, so it cannot reach the dimensions the guard was written for",
    "ctrl-u-upgrade": "pip-and-relaunch is how a terminal updates itself; the desktop updates through electron-updater",
}


class TestTerminalOnly:
    def test_every_absence_has_a_real_reason(self):
        for construct, reason in TERMINAL_ONLY.items():
            assert len(reason) > 30, f"{construct}: an absence needs a reason, not a label — got {reason!r}"

    def test_the_upgrade_reason_names_something_that_exists(self, manifest):
        """Every entry above says the desktop does the job another way. That is
        a claim, and this one is checkable.

        It used to read yeaboi-desktop's own files. Across two repos the fact
        travels instead: the desktop's route registry declares the affordance,
        the manifest carries it here, and a test over there asserts the entry is
        backed by electron-updater rather than merely listed. Neither half can
        lapse without the other going red.
        """
        offered = {entry["path"] for entry in manifest["routes"]}
        assert "action:check-for-updates" in offered, (
            "TERMINAL_ONLY says the desktop updates itself instead of offering Ctrl-U, "
            "but its manifest declares no update affordance"
        )

    def test_no_absence_is_secretly_present(self, manifest):
        # A construct listed here that the desktop actually ships is a stale
        # entry, and a stale entry is how a registry starts lying.
        offered = {f"/{command['tui']}" for command in manifest["commands"]}
        overlap = offered & set(TERMINAL_ONLY)
        assert not overlap, f"TERMINAL_ONLY claims {sorted(overlap)} is absent, but the desktop offers it\n{_HOW_TO}"


# ---------------------------------------------------------------------------
# 1. Slash commands — the chat's verb registry, both ways.
# ---------------------------------------------------------------------------


# The desktop ships no chat surface: the standalone planning chat folded into the
# project flow, and the conversation returns with the ChatSession-in-project work.
# An empty commands registry is the honest encoding of that — reverse this when
# the chat comes back.
DESKTOP_CHAT_RETIRED = (
    "the standalone planning chat folded into the project flow; the interactive "
    "chat returns with the ChatSession-in-project work"
)


class TestSlashCommands:
    @staticmethod
    def _terminal_verbs() -> set[str]:
        from yeaboi.ui.session.chat._commands import COMMANDS

        return {command.name for command in COMMANDS}

    def test_every_terminal_verb_reaches_the_desktop_or_is_exempt(self, manifest):
        if not manifest["commands"]:
            # The standalone chat retired with the planning pages, so there is no
            # verb registry to mirror. Skipped rather than passed, so the run
            # reports the guard as off instead of green; it re-arms the moment the
            # manifest carries one command, and a half-registered list still fails
            # below.
            pytest.skip(DESKTOP_CHAT_RETIRED)
        answered = {command["tui"] for command in manifest["commands"]}
        exempt = {name.lstrip("/") for name in TERMINAL_ONLY}
        missing = self._terminal_verbs() - answered - exempt
        assert not missing, f"terminal slash commands with no desktop answer: {sorted(missing)}\n{_HOW_TO}"

    def test_the_desktop_invents_no_verb_the_terminal_lacks(self, manifest):
        # Two-way: a desktop-only verb would be a feature the terminal user
        # never hears about, which is the same failure pointing the other way.
        answered = {command["tui"] for command in manifest["commands"]}
        extra = answered - self._terminal_verbs()
        assert not extra, f"desktop commands answering no terminal verb: {sorted(extra)}"

    def test_no_two_desktop_commands_answer_the_same_verb(self, manifest):
        names = [command["name"] for command in manifest["commands"]]
        answered = [command["tui"] for command in manifest["commands"]]
        assert len(set(names)) == len(names), "duplicate desktop command name"
        assert len(set(answered)) == len(answered), "two desktop commands claim the same terminal verb"

    def test_every_command_carries_help(self, manifest):
        for command in manifest["commands"]:
            assert len(command["help"]) > 8, f"/{command['name']} needs a help line"


# ---------------------------------------------------------------------------
# 2. Settings sections — every field group, on both surfaces.
# ---------------------------------------------------------------------------


class TestSettingsSections:
    def test_the_two_surfaces_group_the_same_sections(self, manifest):
        from yeaboi.ui.mode_select.screens._screens_secondary import _SETTINGS_TAB_SECTIONS

        terminal = {section for sections in _SETTINGS_TAB_SECTIONS.values() for section in sections}
        desktop = {section for tab in manifest["settings_tabs"] for section in tab["sections"]}
        assert terminal == desktop, (
            f"settings sections differ between the surfaces.\n"
            f"  in the terminal only: {sorted(terminal - desktop)}\n"
            f"  on the desktop only: {sorted(desktop - terminal)}\n{_HOW_TO}"
        )

    def test_no_section_is_claimed_by_two_tabs(self, manifest):
        sections = [section for tab in manifest["settings_tabs"] for section in tab["sections"]]
        assert len(set(sections)) == len(sections), "a settings section appears under two desktop tabs"

    def test_every_tab_is_a_registered_route(self, manifest):
        paths = {route["path"] for route in manifest["routes"]}
        for tab in manifest["settings_tabs"]:
            assert tab["route"] in paths, f"settings tab {tab['route']!r} is not a route"


# ---------------------------------------------------------------------------
# 3. The rollout ledger is closed.
# ---------------------------------------------------------------------------


class TestTheRolloutIsOver:
    def test_no_capability_is_still_waiting_for_a_milestone(self):
        """The M12 flip: every desktop absence is now a decision, not a queue.

        A ``desktop: scheduled milestone M<n>`` exemption meant "not yet" while
        the app was being built. The app is built. A new capability either
        reaches the desktop or says why it never will — "we will get to it" is
        no longer an answer the registry accepts.
        """
        waiting = [
            f"{cap}.{field}: {value.reason}"
            for cap, row in CAPABILITIES.items()
            for field, value in row.items()
            if isinstance(value, Exempt) and "scheduled milestone" in value.reason
        ]
        assert not waiting, (
            "the desktop rollout is finished — these rows still name a milestone:\n  "
            + "\n  ".join(waiting)
            + "\nGive the capability a desktop route, or replace the exemption with a reason that stands on its own."
        )

    def test_every_capability_declares_a_desktop_answer(self):
        # Belt and braces over test_surface_parity's required-keys check: a row
        # whose desktop column is an empty set would pass there and mean
        # nothing here.
        for cap, row in CAPABILITIES.items():
            value = row["desktop"]
            assert isinstance(value, Exempt) or value, f"capability {cap!r} has an empty desktop column"


# ---------------------------------------------------------------------------
# 4. The plan has to be reachable — the hole the flip found.
# ---------------------------------------------------------------------------


class TestPlanningIsWholeOnTheDesktop:
    def test_the_plan_a_conversation_produces_can_be_read_and_sent_somewhere(self, manifest):
        """A surface that can build a plan and not export it is not at parity.

        planning's tools cover four verbs beyond the conversation — read it,
        write it out, publish it, push it to a tracker — and until M12 the
        desktop had a window for none of them.
        """
        paths = {route["path"] for route in manifest["routes"]}
        assert "/projects/:id/plan" in paths, (
            "the desktop has no page for a finished plan — plan_get/plan_export/plan_publish/plan_sync "
            f"would have no window to be called from\n{_HOW_TO}"
        )
        assert "/projects/:id/plan" in CAPABILITIES["planning"]["desktop"]
