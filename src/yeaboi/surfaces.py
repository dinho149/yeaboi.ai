"""The surfaces a piece of user-facing copy can be true on.

One vocabulary, shared by the release notes (:mod:`yeaboi.changelog`) and the
discoverability tips (:mod:`yeaboi.ui.shared._tips`): the terminal app + CLI, the
Electron desktop app, and the browser-served share/board pages. Copy that names a
gesture belongs to the surface that has it; copy that describes the product
belongs to all of them.
"""

from __future__ import annotations

VALID_SURFACES = frozenset({"tui", "desktop", "web"})
ALL_SURFACES: tuple[str, ...] = ("tui", "desktop", "web")

# The landing worlds a piece of copy can be true in — the same keys as the
# landing split's cards. A tip that names a room full of teammates is Team-only.
VALID_WORLDS = frozenset({"solo", "team", "agents"})
ALL_WORLDS: tuple[str, ...] = ("solo", "team", "agents")
