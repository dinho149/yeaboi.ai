"""The shell document, and the invariant that keeps the design pass cheap.

The second class here is the one worth reading. ``docs/app-plan.md`` promises
that a re-skin is a token edit, and that promise is only true while no app code
writes a colour, a font stack, or a spacing literal of its own. That is exactly
the kind of rule which decays under an autonomous build, so it is asserted
rather than documented.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._pages import assert_self_contained, island
from yeaboi.app.page import render_app_page
from yeaboi.app.routes import SHELL_ROUTES
from yeaboi.app.server import AppServer
from yeaboi.app.store import AppStore

APP_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src" / "app"


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db")


class TestShellDocument:
    def test_signed_out_shell_boots_with_no_user(self, store):
        html = render_app_page(store, None)
        assert island(html) == {"user": None}

    def test_signed_in_shell_carries_the_user(self, store):
        user = store.create_user("ada@example.com", "Ada")
        payload = island(render_app_page(store, user.id))
        assert payload["user"]["email"] == "ada@example.com"

    def test_the_shell_is_self_contained(self, store):
        # Same rule as every other surface: one inlined bundle, one inlined
        # stylesheet, no external reference beyond the credit link.
        assert_self_contained(render_app_page(store, None))

    def test_a_stale_user_id_boots_signed_out_rather_than_failing(self, store):
        assert island(render_app_page(store, "usr_gone"))["user"] is None

    @pytest.mark.parametrize("path", ["/", "/projects", "/projects/prj_123", "/settings"])
    def test_every_shell_route_serves_the_document(self, store, path):
        # A hard refresh deep in the app must serve the shell, not a 404.
        from yeaboi.app.router import parse_request

        response = AppServer(store).handle(parse_request("GET", path, {}))
        assert response.code == 200
        assert response.content_type.startswith("text/html")

    def test_an_unknown_path_is_still_a_404(self, store):
        from yeaboi.app.router import parse_request

        # Listed shell routes rather than a catch-all, so a typo 404s like a
        # missing page instead of silently rendering the app.
        assert AppServer(store).handle(parse_request("GET", "/nope", {})).code == 404

    def test_shell_routes_do_not_shadow_the_api(self):
        assert not any(template.startswith("/api") for template in SHELL_ROUTES)


class TestDesignLayerIsTheOnlySourceOfStyle:
    """`docs/app-plan.md`: no raw colour, font stack, or spacing outside design/."""

    def _sources(self) -> list[Path]:
        return [p for p in APP_SRC.rglob("*") if p.suffix in {".css", ".ts", ".tsx"}]

    def test_no_hex_colour_literals(self):
        offenders = [
            f"{path.name}:{i}"
            for path in self._sources()
            for i, line in enumerate(path.read_text().splitlines(), 1)
            if re.search(r"#[0-9a-fA-F]{3,8}\b", line) and "yeaboi-data" not in line
        ]
        assert offenders == [], f"raw colour in the app layer: {offenders} — use a token from design/"

    def test_no_font_family_declarations(self):
        offenders = [
            f"{path.name}:{i}"
            for path in self._sources()
            for i, line in enumerate(path.read_text().splitlines(), 1)
            if "font-family:" in line and "var(--font-" not in line
        ]
        assert offenders == [], f"raw font stack in the app layer: {offenders}"

    def test_spacing_and_radius_come_from_tokens(self):
        """Bare `px` in a spacing property is the drift this rule exists to stop.

        `1px` borders are exempt: there is no token for a hairline and inventing
        one would be worse than the literal it replaces.
        """
        pattern = re.compile(r"^\s*(padding|margin|gap|border-radius)[^:]*:\s*([^;]+);")
        offenders = []
        for path in self._sources():
            if path.suffix != ".css":
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                match = pattern.match(line)
                if match and re.search(r"\b\d+px\b", match.group(2)):
                    offenders.append(f"{path.name}:{i} {line.strip()}")
        assert offenders == [], f"raw spacing in the app layer: {offenders} — use --s1..--s6 / --r-*"


class TestEveryTokenUsedIsDefined:
    """An undefined `var(--x)` fails silently — the property is just dropped.

    That is a nastier failure than a raw literal: the "no hex" guard above
    passes, the build passes, and the layout quietly collapses in the browser.
    Caught once already (`--rail-w` was used before it existed).
    """

    def test_no_app_token_is_undefined(self):
        design = APP_SRC.parent / "design"
        defined = set()
        for path in design.glob("*.css"):
            # Not anchored to line start: palette.css packs several
            # declarations onto one line, and `^\s*` misses all but the first.
            defined.update(re.findall(r"(--[a-z0-9-]+)\s*:", path.read_text()))
        used = set()
        for path in APP_SRC.rglob("*"):
            if path.suffix in {".css", ".tsx", ".ts"}:
                used.update(re.findall(r"var\((--[a-z0-9-]+)", path.read_text()))
        assert used <= defined, f"undefined token(s) used by the app layer: {sorted(used - defined)}"
