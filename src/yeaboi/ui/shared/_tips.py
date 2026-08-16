"""Rotating discoverability tips for the welcome screen.

Single source of truth for the tip list and the (pure) rotation math. Kept out of
the screen builders so it can be unit-tested without a Rich Console and reused if
other screens want to surface a tip.

# See docs: "Architecture" — pure helpers with no side effects; the screen
# builder decides how to render them.

Design notes:
- **Feature-keyed tips.** Every tip that describes a real product capability is a
  :class:`FeatureTip` keyed by its ``CAPABILITIES`` key (see
  ``tests/unit/test_surface_parity.py``). A parity test fails ``make test`` if a
  capability ships without a tip — so the tips stay current as features land.
- **Driven by the existing render tick.** The mode-select loop already re-renders
  at 60 FPS and threads a continuous ``shimmer_tick`` (seconds since the loop
  started) into the screen builder. :func:`current_tip` turns that float into a
  tip index with plain modulo arithmetic — no timer or background thread. The loop
  may also pass a manual ``override`` index (‹/› browsing) via :func:`resolve_index`.
- **Availability-aware ambient tips.** The first tip adapts to whether the voice
  extra is installed and the last to whether ffplay is present, mirroring
  ``_voice_hint()`` in the input screens.
- **Cached list.** Voice/music availability can't change during a process, so the
  tip list is memoised — this keeps the per-frame render from re-running the
  ``find_spec`` availability probe 60×/second.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from yeaboi.beta import BETA_LABEL

# Seconds each tip stays on screen before the next one rotates in.
TIP_ROTATE_SECONDS = 6.0


@dataclass(frozen=True)
class FeatureTip:
    """One discoverability tip.

    ``key`` is the parity axis: for feature tips it matches a ``CAPABILITIES`` key
    in ``tests/unit/test_surface_parity.py``; ambient tips (voice/music/meta) use
    synthetic keys and are exempt from parity. ``mode_key`` is the ``_MODE_CARDS``
    key to jump to when the user presses the open key, or ``None`` when the feature
    isn't reachable as a home-screen mode. ``is_new`` renders a small NEW badge.
    ``is_beta`` renders a BETA badge — the capability ships and works, but its
    output isn't verified yet. The two are different claims and must not be
    conflated; where both are set, BETA wins (see the render sites).
    """

    key: str
    text: str
    mode_key: str | None = None
    is_new: bool = False
    is_beta: bool = False


# Feature tips — one per user-facing capability. Each is short (one line) and
# action-oriented; they render centred and dimmed under the mode list. The
# ``key`` MUST match a CAPABILITIES row (TestTips enforces this two-way), and
# ``mode_key`` (when set) MUST be a _MODE_CARDS key so the jump-into-feature key
# lands on the right card.
_FEATURE_TIPS: tuple[FeatureTip, ...] = (
    FeatureTip(
        "team-analysis",
        "\U0001f50d Tip: Analysis reads your board for velocity, estimation & delivery signals",
        mode_key="team-analysis",
        is_new=True,
    ),
    # Two tips share the "planning" key on purpose: adding the new one by
    # overwriting the old one cost /form and /finish their only discoverability
    # surface. The registry keys by capability, not by tip, and both carry the
    # same mode_key, so the jump-into-feature target is unambiguous.
    FeatureTip(
        "planning",
        "\U0001f5fa️ Tip: Planning starts as a chat — /form gives a classic form, /finish defaults the rest",
        mode_key="project-planning",
    ),
    FeatureTip(
        "planning",
        "\U0001f5fa️ Tip: Planning a greenfield build shows you which of your own repos could help",
        mode_key="project-planning",
        is_new=True,
    ),
    FeatureTip(
        "planning",
        "\U0001f4c4 Tip: Export a publishable PRD of your plan — to file, Notion or Confluence",
        mode_key="project-planning",
        is_new=True,
    ),
    FeatureTip(
        "standup",
        "☀️ Tip: Paste or drop a standup transcript in Review — Standup learns what it missed",
        mode_key="daily-standup",
        is_new=True,
    ),
    FeatureTip(
        "retro-board",
        "\U0001f504 Tip: Retro runs a live board — teammates add cards from a browser, AI drafts actions",
        mode_key="retro",
    ),
    FeatureTip(
        "scrum-poker",
        "\U0001f0cf Tip: Poker runs live planning poker — teammates vote from a browser, points save to your board",
        mode_key="poker",
        is_new=True,
    ),
    FeatureTip(
        "performance",
        "\U0001f3af Tip: Performance preps 1:1s and 6-month reviews from real delivery data",
        mode_key="performance",
        # Not is_new: the mode isn't recent, it's unverified. The text stays a
        # plain capability description — the gallery strips the "Tip: " prefix,
        # so a caveat written into the prose renders inconsistently across the
        # two surfaces where a flag renders the same on both.
        is_beta=True,
    ),
    FeatureTip(
        "reporting",
        "\U0001f4ca Tip: Reporting turns delivered work into stakeholder slides, PowerPoint and HTML",
        mode_key="reporting",
        is_new=True,
    ),
    FeatureTip(
        "usage",
        "\U0001f4b0 Tip: Usage shows API token spend, session history and cost estimates",
        mode_key="usage",
    ),
    FeatureTip(
        "settings",
        "⚙️ Tip: Settings manages API keys, your LLM provider and board config",
        mode_key="settings",
    ),
    # Capabilities without a dedicated home-screen card (tui_mode Exempt) — they
    # still rotate to aid discovery, just with no jump target.
    FeatureTip(
        "sessions",
        "\U0001f5c2️ Tip: every plan is saved — resume any past session with --resume",
    ),
    FeatureTip(
        "team-learning",
        "\U0001f9e0 Tip: yeaboi learns your team's velocity & estimation patterns over time",
    ),
    FeatureTip(
        "roadmap",
        "\U0001f9ed Tip: point at your quarterly roadmap — AI extracts and ranks projects to plan",
    ),
    FeatureTip(
        "anonymize",
        "\U0001f576️ Tip: press Anonymize on any result screen to mask names before sharing",
    ),
    FeatureTip(
        "output-sharing",
        "🌐 Tip: Share Online publishes any generated output behind a temporary access code",
        is_new=True,
    ),
    FeatureTip(
        "artifact-editing",
        "✏️ Tip: teammates can correct a shared report in the browser — every change is attributed",
        is_new=True,
    ),
    # The Agents family — cards live on the Agents menu (_AGENT_CARDS); the `g`
    # jump switches category when the tip fires from the Humans menu.
    FeatureTip(
        "agent-usage",
        "\U0001f916 Tip: Agents → Usage shows what your AI agents cost — per model, project and day",
        mode_key="agent-usage",
        is_beta=True,
    ),
    FeatureTip(
        "agent-advisor",
        "\U0001f916 Tip: Agents → Advisor estimates how much of your agent spend is recoverable — and why",
        mode_key="agent-advisor",
        is_beta=True,
    ),
    FeatureTip(
        "agent-standup",
        "\U0001f916 Tip: Agents → Standup digests what your AI agents did yesterday — sessions, commits, PRs",
        mode_key="agent-standup",
        is_beta=True,
    ),
    FeatureTip(
        "agent-security",
        "\U0001f916 Tip: Agents → Security audits agent permissions, MCP servers and secrets exposure",
        mode_key="agent-security",
        is_beta=True,
    ),
    FeatureTip(
        "provenance",
        "\U0001f512 Tip: `yeaboi provenance audit` verifies the tamper-evident log behind every signal",
        is_new=True,
    ),
)

# Ambient tips — not tied to a capability, so exempt from parity. The generic
# meta tips sit between the (dynamic) voice and music tips assembled in get_tips().
_META_TIPS: tuple[FeatureTip, ...] = (
    FeatureTip("meta:theme", "\U0001f4a1 Tip: switch between --theme dark and --theme light"),
    FeatureTip("meta:headless", "\U0001f4a1 Tip: run headless with --non-interactive for scripts & pipelines"),
    FeatureTip("meta:export", "\U0001f4a1 Tip: export a plan to HTML or JSON for sharing and CI/CD"),
)


@lru_cache(maxsize=1)
def get_tips() -> tuple[FeatureTip, ...]:
    """Return the ordered tips shown on the welcome screen.

    The first entry is the voice tip and the last is the music tip; both adapt to
    whether their optional dependency is installed (dictation extra / the ffplay
    binary), showing an install hint otherwise. Between them come the feature tips
    (one per capability) and the generic meta tips. Memoised because the tips are
    rebuilt on every welcome-screen frame; the memo is dropped by
    :func:`yeaboi.voice_install.refresh_imports` when an in-app install changes
    the answer part-way through a run.
    """
    from yeaboi.music import is_music_available
    from yeaboi.voice import unsupported_blocker, voice_install_command, voice_state

    state = voice_state()
    if state == "unsupported":
        # The actual blocker, not a fixed platform sentence: on Linux this is
        # usually a missing libportaudio2 and carries the command that fixes it,
        # which is worth more than restating the wheel matrix.
        voice_text = f"\U0001f3a4 Tip: dictation can't run here — {unsupported_blocker()}"
    else:
        voice_text = {
            "ready": "\U0001f3a4 Tip: double-tap Space in any text field to dictate",
            # The gesture is the install: naming a shell command here would send
            # the user to a second terminal for something one keystroke does.
            "installable": "\U0001f3a4 Tip: double-tap Space in any text field — one keystroke sets dictation up",
        }.get(state, f"\U0001f3a4 Tip: enable dictation with — {voice_install_command()}")
    voice_tip = FeatureTip("voice", voice_text)
    music_available, _music_reason = is_music_available()
    music_tip = FeatureTip(
        "music",
        "\U0001f3b5 Tip: press Ctrl+P for focus music · Ctrl+O to switch channel"
        if music_available
        else "\U0001f3b5 Tip: play focus music while you plan — brew install ffmpeg",
    )
    return (voice_tip, *_FEATURE_TIPS, *_META_TIPS, music_tip)


def build_tips_text() -> str:
    """Render every tip as a copy-pasteable Markdown list.

    Powers the "Copy all" action on the All Tips page, mirroring
    ``build_changelog_text``. Pure — :func:`get_tips` already resolves
    voice/music availability. Carded tips note the mode they open (by its
    friendly ``_MODE_CARDS`` title), freshly-shipped ones are marked ``(NEW)``
    and unverified ones ``(BETA)``.
    """
    # Lazy import to avoid a UI import cycle (screens import from this module).
    from yeaboi.ui.mode_select.screens._screens import _MODE_CARDS

    titles = {card["key"]: card["title"] for card in _MODE_CARDS}
    lines = ["# yeaboi — Tips", ""]
    for tip in get_tips():
        line = f"- {tip.text}"
        # BETA outranks NEW: a maturity caveat matters more than a freshness cue,
        # and a copied tip list that says both reads as neither.
        if tip.is_beta:
            line += f" ({BETA_LABEL})"
        elif tip.is_new:
            line += " (NEW)"
        if tip.mode_key and tip.mode_key in titles:
            line += f" → opens {titles[tip.mode_key]}"
        lines.append(line)
    return "\n".join(lines).rstrip() + "\n"


def tip_count() -> int:
    """Number of tips in rotation (used to render position dots)."""
    return len(get_tips())


def tip_at(index: int) -> FeatureTip:
    """Return the tip at ``index`` (wrapped modulo the tip count)."""
    tips = get_tips()
    return tips[index % len(tips)]


def resolve_index(tick: float, offset: int = 0, rotate_seconds: float = TIP_ROTATE_SECONDS) -> int:
    """Return the tip index to show at ``tick`` seconds, shifted by ``offset``.

    Auto-rotation advances the index every ``rotate_seconds`` off ``tick``. The
    home loop adds a manual ``offset`` (bumped by the [ / ] browse keys): it just
    relabels which tip occupies each rotation window, so browsing moves through
    the list *and auto-rotation keeps running* from the new position — no pause,
    no pinned index that could get stuck.
    """
    tips = get_tips()
    if not tips:  # pragma: no cover - defensive; the list is always populated
        return 0
    period = rotate_seconds if rotate_seconds > 0 else TIP_ROTATE_SECONDS
    return (int(max(0.0, tick) / period) + offset) % len(tips)


def current_tip(tick: float, rotate_seconds: float = TIP_ROTATE_SECONDS) -> tuple[int, FeatureTip]:
    """Return ``(index, tip)`` for the tip visible at ``tick`` seconds.

    ``tick`` is the monotonic elapsed time already threaded through the render
    loop. The tip advances every ``rotate_seconds``; the index wraps around the
    tip list so rotation is continuous.
    """
    idx = resolve_index(tick, 0, rotate_seconds)
    return idx, tip_at(idx)


# Fraction of each rotation window spent fading in (and, symmetrically, out).
_FADE_FRACTION = 0.16


def tip_brightness(tick: float, rotate_seconds: float = TIP_ROTATE_SECONDS) -> float:
    """Return a 0..1 brightness for the current tip so it can cross-fade.

    Each tip fades up from the background over the first ``_FADE_FRACTION`` of
    its window, holds at full brightness, then fades back down over the last
    ``_FADE_FRACTION`` — so one tip dissolves out as the next dissolves in. The
    caller lerps its text/dot colours by this value. Pure and testable; no I/O.
    """
    period = rotate_seconds if rotate_seconds > 0 else TIP_ROTATE_SECONDS
    phase = (max(0.0, tick) % period) / period  # position within this tip's window, 0..1
    if phase < _FADE_FRACTION:
        return phase / _FADE_FRACTION
    if phase > 1.0 - _FADE_FRACTION:
        return max(0.0, (1.0 - phase) / _FADE_FRACTION)
    return 1.0
