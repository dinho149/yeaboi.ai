"""Canonical wording for what leaves this machine, and what never does.

yeaboi's honest privacy posture: everything the app makes stays on this
machine, telemetry is off unless the user turns it on, and nothing reaches us
unless the user sends feedback. The prompts a mode writes go to the AI provider
the user configured — and only there; picking Ollama keeps even those local.

Every surface renders from this module — the TUI privacy page, the setup
wizard's welcome panel, the desktop Privacy page (over ``GET
/api/meta/privacy``), the desktop onboarding welcome step — because a privacy
claim that differs between surfaces is worse than no claim (the ``beta.py``
rule). The wire handler serializes these values verbatim; it never defines.

This module **discloses** behavior, it never changes it: every ``off_switch``
below is a switch that already exists, named exactly. ``tests/unit/
test_privacy.py`` pins each named env var to the module that reads it.

Deliberately **import-free**, like ``beta.py``: startup-latency-sensitive
surfaces pull from it, and the test suite AST-scans it so a creeping import
cannot be silent.
"""

PRIVACY_HEADLINE = "yeaboi collects nothing about you."

# One sentence per claim, each true by construction and each verifiable in the
# named module. Rendered as separate paragraphs; keep them self-contained.
PRIVACY_STATEMENT: tuple[str, ...] = (
    "Everything yeaboi makes — sessions, boards, reports, settings — is stored on this machine under ~/.yeaboi.",
    "Your prompts and tracker data go only to the AI provider you configured, and never to us. "
    "Pick Ollama and they never leave this machine at all.",
    "Nothing reaches us unless you choose to send feedback — and that report is exactly what you typed, "
    "with home-directory paths stripped and screenshots never uploaded.",
)

# The state buckets every surface groups the disclosures under, in render
# order: the audit reads from "fires without you" down to "never without you".
# Each disclosure row names its bucket in ``group``.
EGRESS_GROUPS: tuple[dict, ...] = (
    {"key": "always", "title": "On out of the box"},
    {"key": "tunnel", "title": "Only when you share a live board"},
    {"key": "opt-in", "title": "Off until you turn them on"},
    {"key": "you", "title": "Only when you act"},
)

# The live toggles behind the disclosures: one entry per row whose off-switch
# is a real settings-engine field. ``on_value`` is the setting value under
# which the path fires — YEABOI_NO_TUNNEL inverts, so its on_value is "false".
# Surfaces render these as in-place switches; rows absent here have no toggle.
# tests/unit/test_privacy.py pins every env to a real settings-engine field.
EGRESS_SWITCHES: tuple[dict, ...] = (
    {"key": "update-check", "env": "YEABOI_UPDATE_CHECK", "on_value": "true"},
    {"key": "news", "env": "YEABOI_NEWS", "on_value": "true"},
    {"key": "tunnel", "env": "YEABOI_NO_TUNNEL", "on_value": "false"},
    {"key": "doh", "env": "YEABOI_NO_TUNNEL", "on_value": "false"},
    {"key": "cloudflared-download", "env": "YEABOI_NO_TUNNEL", "on_value": "false"},
    {"key": "telemetry", "env": "YEABOI_TELEMETRY", "on_value": "true"},
    {"key": "tracing", "env": "LANGSMITH_TRACING", "on_value": "true"},
)

# One row per way bytes can leave the machine. ``default`` says what happens
# out of the box; ``off_switch`` names the existing control, or says plainly
# that there is none yet. Surfaces render these as a table with the off-switch
# column — the point is that every path is visible and every switch is real.
EGRESS_DISCLOSURES: tuple[dict, ...] = (
    {
        "key": "llm",
        "group": "always",
        "what": "Your prompts and the tracker data a mode reads",
        "where": "The AI provider you configured (Anthropic, OpenAI, Google, Bedrock — or Ollama on this machine)",
        "when": "When a mode runs",
        "default": "on — it is the product",
        "off_switch": "Choose Ollama in Settings ▸ Credentials for a fully local model",
    },
    {
        "key": "update-check",
        "group": "always",
        "what": "A version query, carrying no identifiers",
        "where": "PyPI",
        "when": "Terminal app start",
        "default": "on",
        "off_switch": "YEABOI_UPDATE_CHECK=off (Settings ▸ System ▸ Privacy)",
    },
    {
        "key": "news",
        "group": "always",
        "what": "A request for each outlet's public headline feed, carrying nothing but a yeaboi User-Agent",
        "where": (
            "The front page's outlets — Google DeepMind, Google AI, OpenAI, Anthropic, Hacker News, Techmeme, "
            "MIT Technology Review, Ars Technica, Simon Willison, InfoQ, GitHub, The Pragmatic Engineer, arXiv, "
            "yeaboi.ai and, if you set a channel, YouTube"
        ),
        "when": "Opening the desktop home, at most every 30 minutes",
        "default": "on",
        "off_switch": "YEABOI_NEWS=off (Settings ▸ System ▸ Privacy) — the yeaboi column still shows the release notes",
    },
    {
        "key": "desktop-update",
        "group": "always",
        "what": "A version query for the desktop shell, carrying no identifiers",
        "where": "GitHub Releases",
        "when": "Desktop app launch (packaged builds)",
        "default": "on",
        "off_switch": "None yet — nothing downloads or installs until you click Update",
    },
    {
        "key": "tunnel",
        "group": "tunnel",
        "what": "The live board page, so the people you invite can open it",
        "where": "A Cloudflare quick tunnel on a random *.trycloudflare.com address",
        "when": "Opening a retro or poker board",
        "default": "on",
        "off_switch": "YEABOI_NO_TUNNEL=1 — the board still runs, loopback-only (Settings ▸ System ▸ Privacy)",
    },
    {
        "key": "doh",
        "group": "tunnel",
        "what": "A DNS lookup of the tunnel hostname — the hostname only",
        "where": "Google and Cloudflare DNS-over-HTTPS",
        "when": "While a fresh tunnel link is verified as reachable",
        "default": "on with any tunnel",
        "off_switch": "YEABOI_NO_TUNNEL=1 — no tunnel, no probe",
    },
    {
        "key": "cloudflared-download",
        "group": "tunnel",
        "what": "A one-time download of the checksum-pinned cloudflared binary (~38 MB in)",
        "where": "GitHub Releases (cloudflare/cloudflared)",
        "when": "The first time a tunnel is opened",
        "default": "on first tunnel use",
        "off_switch": "YEABOI_NO_TUNNEL=1, or preinstall it and set CLOUDFLARED_PATH",
    },
    {
        "key": "telemetry",
        "group": "opt-in",
        "what": "Anonymized planning-shape events — counts and hashes, never your text",
        "where": "yeaboi's collection endpoint",
        "when": "End of a completed planning session, only if you opted in",
        "default": "off",
        "off_switch": "Stays off unless you set YEABOI_TELEMETRY=true (Settings ▸ System ▸ Privacy)",
    },
    {
        "key": "tracing",
        "group": "opt-in",
        "what": "Full LLM traces (prompts and completions), for debugging",
        "where": "LangSmith",
        "when": "Only if you enabled tracing yourself",
        "default": "off",
        "off_switch": "Stays off unless you set LANGSMITH_TRACING=true and an API key",
    },
    {
        "key": "feedback",
        "group": "you",
        "what": "The report you wrote — home paths relativized, screenshots never uploaded",
        "where": "GitHub issues on the public yeaboi repository",
        "when": "Only when you press send",
        "default": "user-initiated",
        "off_switch": "Don't send it",
    },
)
