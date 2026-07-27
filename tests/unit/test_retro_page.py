"""Unit tests for the served browser board page + config getter."""

from yeaboi.retro.page import build_board_html


class TestBuildBoardHtml:
    def test_esc_escapes_attribute_breakers(self):
        # esc()'d values land inside double-quoted HTML attributes: quotes must
        # be escaped or a participant-chosen name breaks out of the attribute.
        html = build_board_html()
        assert '"&quot;"' in html
        assert '"&#39;"' in html

    def test_self_contained(self):
        html = build_board_html()
        assert "<!DOCTYPE html>" in html
        assert "<style>" in html and "<script>" in html
        # No external code/style resources (CSP-hostile) — no external script/style/
        # link tags, no CDN. The music streams (audio URLs in JS data) are the one
        # deliberate exception, so we forbid resource *tags* rather than any URL.
        assert 'src="http' not in html and 'href="http' not in html
        assert "<link" not in html
        assert "cdn" not in html.lower()

    def test_token_free_page(self):
        # Security: the served page must NOT bake the token (GET / is unauthenticated).
        # The client reads it from the URL instead.
        html = build_board_html()
        assert "let TOKEN = new URLSearchParams" in html
        assert "const TOKEN" not in html

    def test_has_four_grids(self):
        html = build_board_html()
        for label in ("What went well", "What didn't go well", "Action items", "Demos"):
            assert label in html

    def test_renders_via_textcontent_not_innerhtml(self):
        html = build_board_html()
        assert "function esc(" in html
        assert "textContent" in html

    def test_round1_features(self):
        html = build_board_html()
        assert "AudioContext" in html and "music-btn" in html and "music-mood" in html
        assert 'data-secs="60"' in html and "timer-readout" in html and "custom-min" in html
        assert "reactionBar" in html and "/api/react" in html
        assert 'id="avatars"' in html and 'id="dice"' in html and "randomName" in html
        assert "typing…" in html and 'id="presence"' in html and "/api/presence" in html

    def test_round2_features(self):
        html = build_board_html()
        # join code gate + invite QR
        assert 'id="code-modal"' in html and "/api/join" in html
        assert 'id="invite-modal"' in html and "/api/qr" in html
        # rename control
        assert 'id="me"' in html and "openProfile" in html
        # theme switcher: swatch buttons (built from THEMES) + the alt-theme CSS block
        assert "data-set-theme" in html and '[data-theme="synthwave"]' in html
        assert '"synthwave"' in html and "buildSwatches" in html
        assert 'id="theme-btn"' in html and 'id="theme-pop"' in html
        # internet-radio music: the TUI's SomaFM channels, played via <audio>
        assert "setChannel" in html and "buildChannels" in html
        assert '"Lofi"' in html and '"Jazz"' in html and "somafm" in html
        # visualizer + drag + edit/delete + confetti/alarm
        assert 'id="viz"' in html and "drawViz" in html
        assert 'draggable="true"' in html and "/api/card/move" in html
        assert "data-edit" in html and "/api/card/edit" in html and "/api/card/delete" in html
        assert 'id="confetti"' in html and "function confetti(" in html and "function alarm(" in html

    def test_compact_toolbar(self):
        html = build_board_html()
        # Toolbar icon buttons + their popovers (controls appear on demand).
        assert 'class="toolbar"' in html
        for tid in ("music-btn", "timer-btn", "theme-btn", "invite-btn"):
            assert f'id="{tid}"' in html
        for pid in ("music-pop", "timer-pop", "theme-pop"):
            assert f'id="{pid}"' in html
        assert "togglePop" in html and "closePops" in html
        # Distinct "you" chip + others-only presence stack (no duplicate self).
        assert 'class="me-chip"' in html and 'class="avatars"' in html
        assert "p.name !== NAME" in html  # self excluded from the teammate stack
        # Room count + roster of who's in the room.
        assert 'id="room-btn"' in html and 'id="roomcount"' in html
        assert 'id="room-pop"' in html and "function renderRoom" in html

    def test_stable_pid_generated_offline(self):
        html = build_board_html()
        assert "crypto.randomUUID" in html and "retro_pid" in html

    def test_no_dangling_element_ids(self):
        # Regression: toggleInvite once referenced a non-existent #invite-code and
        # threw. Every getElementById target used at runtime must exist in the DOM.
        import re

        html = build_board_html()
        referenced = set(re.findall(r'getElementById\("([^"]+)"\)', html))
        defined = set(re.findall(r'id="([^"]+)"', html))
        # Grid-scoped ids (cards-*/typing-*/in-*/edit-*) are created dynamically.
        dynamic = {r for r in referenced if r.split("-")[0] in ("cards", "typing", "in", "edit")}
        missing = referenced - defined - dynamic
        assert not missing, f"getElementById targets with no matching element: {missing}"

    def test_injected_sets_present(self):
        html = build_board_html()
        for emoji in ("👍", "❤️", "🔥"):
            assert emoji in html
        assert "🤠" in html  # an avatar

    def test_reaction_broadcast_and_react_button(self):
        html = build_board_html()
        # Collapsed reactions: a React button + a floating picker (not all chips).
        assert "react-btn" in html and "rx-picker" in html and "openReactPicker" in html
        # Floating-emoji broadcast overlay + the poll-driven event drain.
        assert 'id="rx-fx"' in html and "function floatEmoji(" in html
        assert "reaction_events" in html and "seededRx" in html

    def test_music_uses_audio_element_not_synth(self):
        html = build_board_html()
        # Real streams via <audio>; the old synth mood engine is gone.
        assert "new Audio()" in html and "Music.channels()" in html
        assert "MOODS" not in html and "boombap" not in html
        # The alarm no longer borrows the (removed) synth context.
        assert "Music.ctx()" not in html and "Music.out()" not in html

    def test_share_code_token_hardening(self):
        html = build_board_html()
        # Token is persisted per-tab and never re-written into the address bar,
        # so copying the URL doesn't leak access.
        assert 'sessionStorage.getItem("retro_token")' in html
        assert 'sessionStorage.setItem("retro_token"' in html
        assert '"/?token="' not in html  # never rebuild a token'd URL client-side

    def test_timestamps_on_cards(self):
        html = build_board_html()
        # Relative "age" helper + the .ago span in the card's who-row.
        assert "function fmtAgo(" in html and 'class="ago"' in html

    def test_focus_and_group_controls(self):
        html = build_board_html()
        assert 'id="focus-author"' in html and 'id="group-toggle"' in html
        # Grouping/focus render path + persisted group toggle.
        assert "function gridInner(" in html and "updateFocusOptions" in html
        assert 'localStorage.setItem("retro_grouped"' in html

    def test_edit_focuses_and_guards_render(self):
        html = build_board_html()
        # Entering edit focuses the box and moves the caret to the end…
        assert "setSelectionRange" in html
        # …and the poll re-render skips the grid holding the focused editbox.
        assert "editingHere" in html

    def test_theme_set_injected_from_board(self):
        html = build_board_html()
        # THEMES comes from board.RETRO_THEMES (no leftover placeholder).
        assert "const THEMES = __THEMES__" not in html
        assert '"midnight"' in html and '"forest"' in html

    def test_admin_controls_present(self):
        html = build_board_html()
        # Admin secret read from the host link + the four host controls.
        assert 'new URLSearchParams(location.search).get("admin")' in html
        for eid in ("lock-btn", "music-cast", "theme-cast", "music-banner", "lock-banner"):
            assert f'id="{eid}"' in html
        # Admin-gated broadcast/lock endpoints + autoplay-fallback banner logic.
        assert "/api/admin/broadcast" in html and "/api/admin/lock" in html
        assert "showMusicBanner" in html and "applyLock" in html


class TestConfig:
    def test_default_port(self, monkeypatch):
        from yeaboi import config

        monkeypatch.delenv("RETRO_PORT", raising=False)
        assert config.get_retro_server_port() == 5173

    def test_env_override(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("RETRO_PORT", "6000")
        assert config.get_retro_server_port() == 6000

    def test_bad_env_falls_back(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("RETRO_PORT", "notanint")
        assert config.get_retro_server_port() == 5173
