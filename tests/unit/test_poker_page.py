"""Tests for the poker browser page builder (poker/page.py)."""

from yeaboi.poker.board import POKER_DECK
from yeaboi.poker.page import build_poker_html
from yeaboi.retro.board import AVATARS, RETRO_THEMES


class TestBuildPokerHtml:
    def test_placeholders_all_injected(self):
        html = build_poker_html()
        assert "__DECK__" not in html
        assert "__AVATARS__" not in html
        assert "__THEMES__" not in html
        assert "__ADJS__" not in html
        assert "__NOUNS__" not in html
        assert "__MUSIC_CHANNELS__" not in html

    def test_deck_and_enums_present(self):
        html = build_poker_html()
        for card in POKER_DECK:
            assert f'"{card}"' in html
        assert AVATARS[0] in html
        for theme in RETRO_THEMES:
            assert f'[data-theme="{theme}"]' in html

    def test_shell_and_poker_markup(self):
        html = build_poker_html()
        assert "<title>Planning Poker</title>" in html
        # Shared shell: join gate, profile modal, invite QR, music, timer, themes.
        assert 'id="code-modal"' in html
        assert 'id="modal"' in html
        assert 'id="invite-modal"' in html
        assert 'id="music-pop"' in html
        assert 'id="timer-pop"' in html
        assert 'id="swatches"' in html
        # Poker UI: ticket panel, rail, table (voters + deck), results, console, edit modal.
        assert 'id="ticket"' in html
        assert 'id="rail-list"' in html
        assert 'id="deck"' in html
        assert 'id="vrow"' in html
        assert 'id="ainote"' in html
        assert 'id="reveal-btn"' in html
        assert 'id="final-pts"' in html
        assert 'id="edit-modal"' in html
        assert 'id="results"' in html
        assert 'id="deck-status"' in html
        assert 'id="console"' in html
        assert 'id="console-median"' in html
        assert 'id="console-sug"' in html
        assert 'id="console-notice"' in html
        assert 'id="console-toggle"' in html
        assert 'id="rail-backdrop"' in html

    def test_redesign_markers(self):
        # The redesign's load-bearing pieces: design tokens, the mono numbers
        # voice, reduced-motion support, and the acceptance-criteria section.
        html = build_poker_html()
        assert "--font-mono" in html
        assert "prefers-reduced-motion" in html
        assert 'id="acc"' in html
        assert "Acceptance criteria" in html
        assert "acceptance_text" in html
        # The old floating dock is gone; the host console replaced it.
        assert 'class="dock' not in html
        assert "paintConsole" in html

    def test_ai_confidence_and_evidence_markup(self):
        # The AI card renders a confidence pill + cited-evidence bullets.
        html = build_poker_html()
        assert 'class="conf c-' in html
        assert 'class="ev"' in html
        assert ".ainote .conf" in html
        assert ".ainote .ev" in html
        assert "confidence</span>" in html

    def test_duel_markup_and_mic_capture(self):
        html = build_poker_html()
        # Duel panel + admin controls.
        assert 'id="duel"' in html
        assert 'id="duel-btn"' in html
        assert 'id="duel-next-btn"' in html
        assert 'id="duel-close-btn"' in html
        assert 'id="duel-pop"' in html
        assert 'data-dsecs="90"' in html
        # Browser mic capture is secure-context gated (plain-HTTP LAN can't record).
        assert "isSecureContext" in html
        assert "getUserMedia" in html
        assert "MediaRecorder" in html
        assert "/api/duel/audio" in html
        # The recording state is never invisible to participants.
        assert "rec-dot" in html
        assert "RECORDING" in html
        # CSS hooks for the spotlight + transcript.
        assert ".duel .duelist.speaking" in html
        assert ".duel-tx" in html

    def test_peek_markup(self):
        # Read-only ticket peek: every participant can click a rail item to
        # read any ticket without touching the shared round.
        html = build_poker_html()
        assert "/api/ticket" in html
        assert "peekTicket" in html
        assert "peek-banner" in html
        assert "peek-live-btn" in html
        assert "peek-goto-btn" in html
        assert "Back to live" in html
        assert "Vote on this ticket" in html
        # Rail items are real buttons (keyboard-accessible) with live-item ARIA.
        assert 'aria-current="true"' in html
        assert ".rail-item.peeking" in html
        assert ".phase-tag.peek" in html

    def test_esc_escapes_attribute_breakers(self):
        # esc()'d values land inside double-quoted HTML attributes (title=…,
        # href=…): quotes must be escaped or a participant-chosen name could
        # break out of the attribute and run script in every viewer's browser.
        html = build_poker_html()
        assert '"&quot;"' in html
        assert '"&#39;"' in html

    def test_no_secrets_in_page(self):
        # The page is served token-free; it must never embed a token placeholder.
        html = build_poker_html()
        assert "?token=" not in html.replace('"token=" +', "")  # JS builds it at runtime only
        assert "admin_token" not in html

    def test_self_contained(self):
        html = build_poker_html()
        assert "https://cdn" not in html
        assert "<script src=" not in html
        assert '<link rel="stylesheet"' not in html
