"""The self-contained browser poker page served to teammates.

``build_poker_html()`` returns ONE HTML string with inline CSS + JS and no
external requests (no CDN, no third-party iframe) so it works on any LAN device
— and over the Cloudflare tunnel — without the app installed, fully offline.
It grew as a sibling of the retro board's hand-written page and keeps its whole
shell verbatim — retro has since moved to React (``frontend/src/retro``), so
this is the last surface built this way, and it is next: the
token-free page + code-entry gate (``POST /api/join``), the profile modal
(avatar picker + 🎲 random names), the ~1.2 s poll loop, the theme switcher,
internet-radio music with a visualizer, the shared countdown timer with
confetti + alarm, host broadcasts (theme/music/lock), and the invite QR.

Poker replaces the retro grids with: a **ticket panel** (summary, chips,
collapsible description), a **ticket rail** (all tickets + status dots, admin
click-to-jump), a **voting deck** (the Fibonacci cards), a **voter strip**
(✓ badges pre-reveal, values + a distribution bar post-reveal), an **AI note
card**, and an **admin dock** (Reveal / Re-vote / Finalize with prefilled
points / Edit-ticket modal / AI perspective / Prev / Next).

Vote secrecy is server-enforced (board.state_snapshot) — this client simply
never receives other people's values before the reveal.

The big CSS/JS blocks are plain (non-f-string) module constants with
``__PLACEHOLDER__`` markers filled by :func:`build_poker_html` via
``str.replace``. Untrusted strings (ticket text, names) are rendered via
``textContent`` (``esc()``), never raw ``innerHTML``.

# See docs: "Guardrails" — output validation / escaping
"""

from __future__ import annotations

import json
import logging

from yeaboi.music import CHANNELS
from yeaboi.poker.board import POKER_DECK
from yeaboi.retro.board import AVATARS, RETRO_THEMES

# Reuse retro's random-name word lists — same join experience across modes.
from yeaboi.retro.page import _ADJECTIVES, _NOUNS

logger = logging.getLogger(__name__)


_CSS = """
:root, [data-theme="midnight"] {
  --bg:#0d1117; --panel:#161b22; --line:#30363d; --text:#c9d1d9;
  --muted:#8b949e; --accent:#5ac88a; --accent2:#a371f7; --card:#0d1117; --ink:#04211f;
}
[data-theme="light"] {
  --bg:#f6f8fa; --panel:#ffffff; --line:#d0d7de; --text:#1f2328;
  --muted:#59626b; --accent:#1a7f37; --accent2:#8250df; --card:#f6f8fa; --ink:#ffffff;
}
[data-theme="solarized"] {
  --bg:#002b36; --panel:#073642; --line:#11586a; --text:#eee8d5;
  --muted:#93a1a1; --accent:#2aa198; --accent2:#d33682; --card:#002b36; --ink:#002b36;
}
[data-theme="synthwave"] {
  --bg:#1a1033; --panel:#241847; --line:#3d2a6b; --text:#f5e6ff;
  --muted:#a48fd0; --accent:#ff5edb; --accent2:#36e0ff; --card:#150c29; --ink:#1a1033;
}
[data-theme="forest"] {
  --bg:#0c1a12; --panel:#12261b; --line:#1f3a2a; --text:#d7e8dc;
  --muted:#89a894; --accent:#4cc38a; --accent2:#d9c26a; --card:#0a160f; --ink:#04211a;
}
/* Design tokens — derived surfaces come from color-mix so the five theme
   blocks above stay untouched. The mono stack is the page's "numbers voice":
   every value, key, count, and readout is mono; prose stays sans. */
:root {
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px;
  --r-s:6px; --r-m:10px; --r-l:16px; --r-pill:999px;
  --fs-2xs:11px; --fs-xs:12.5px; --fs-s:13.5px; --fs-m:15px; --fs-l:17px; --fs-xl:21px;
  --font-mono:ui-monospace,"SF Mono",Menlo,Consolas,"Cascadia Mono",monospace;
  --shadow-1:0 1px 3px rgba(0,0,0,.25); --shadow-2:0 16px 40px rgba(0,0,0,.4);
  --danger:#f85149; --warn:#e3b341;
  --rail-w:260px; --console-w:300px;
  --panel-2:color-mix(in srgb, var(--text) 5%, var(--panel));
  --felt:color-mix(in srgb, var(--accent) 7%, var(--bg));
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
.eyebrow { font-size:var(--fs-2xs); text-transform:uppercase; letter-spacing:.08em;
           color:var(--muted); font-weight:600; margin:0 0 var(--s2); }
button:focus-visible, input:focus-visible, select:focus-visible {
  outline:2px solid var(--accent2); outline-offset:1px; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration:.01ms !important; animation-iteration-count:1 !important;
                           transition-duration:.01ms !important; }
}
/* ── Header / toolbar (shared shell with retro) ─────────────────── */
header { padding:11px 18px; border-bottom:1px solid var(--line);
         display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.brand { display:flex; align-items:baseline; gap:8px; }
.brand .title { color:var(--accent); font-size:17px; font-weight:700; letter-spacing:.01em; }
.brand .count { color:var(--muted); font-size:12.5px; }
header .spacer { flex:1; }
.presence { display:flex; align-items:center; gap:10px; }
.me-chip { display:flex; align-items:center; gap:6px; background:var(--panel);
           border:1px solid var(--line); border-radius:999px; padding:3px 6px 3px 8px;
           cursor:pointer; font:inherit; font-size:13px; color:var(--text); }
.me-chip:hover { border-color:var(--accent); }
.me-chip .pen { color:var(--muted); font-size:12px; }
.avatars { display:flex; align-items:center; }
.avatars .av-dot { width:26px; height:26px; border-radius:50%; background:var(--panel);
                   border:2px solid var(--bg); box-shadow:0 0 0 1px var(--line); margin-left:-8px;
                   display:flex; align-items:center; justify-content:center; font-size:14px; }
.avatars .av-dot:first-child { margin-left:0; }
.avatars .more { font-size:12px; color:var(--muted); margin-left:6px; }
.room-btn { display:flex; align-items:center; gap:5px; background:var(--panel); border:1px solid var(--line);
            border-radius:999px; padding:3px 10px; cursor:pointer; font:inherit; font-size:13px; color:var(--text); }
.room-btn:hover, .room-btn.open { border-color:var(--accent); }
.roster { display:flex; flex-direction:column; gap:8px; min-width:190px; max-height:280px; overflow:auto; }
.roster .r { display:flex; align-items:center; gap:8px; font-size:13.5px; }
.roster .r .nm { flex:1; }
.roster .r .tag.you { color:var(--accent); font-size:11px; }
.roster .empty { color:var(--muted); font-size:13px; }
.toolbar { display:flex; align-items:center; gap:8px; }
#viz { width:34px; height:22px; opacity:0; transition:opacity .2s; }
#viz.on { opacity:1; }
.tbtn { display:flex; align-items:center; gap:6px; height:34px; padding:0 11px;
        background:var(--panel); border:1px solid var(--line); border-radius:9px;
        color:var(--text); cursor:pointer; font:inherit; font-size:14px; line-height:1; }
.tbtn:hover { border-color:var(--accent); }
.tbtn.open { border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent); }
.tbtn.primary { background:var(--accent); color:var(--ink); border-color:transparent; font-weight:600; }
.tbtn.primary:hover { filter:brightness(1.08); }
.tbtn .ico { font-size:15px; }
.tbtn.playing { border-color:var(--accent); animation:pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 50% { box-shadow:0 0 0 4px color-mix(in srgb, var(--accent) 22%, transparent); } }
#timer-btn .rd { font-variant-numeric:tabular-nums; font-weight:700; color:var(--accent);
                 min-width:42px; text-align:right; }
#timer-btn .rd:empty { display:none; }
#timer-btn.running { border-color:var(--accent); }
#timer-btn.done .rd { color:#f85149; animation:blink 1s steps(2) infinite; }
@keyframes blink { 50% { opacity:.3; } }
.pop { position:fixed; top:56px; right:16px; z-index:25; width:auto;
       background:var(--panel); border:1px solid var(--line); border-radius:12px;
       box-shadow:0 12px 32px rgba(0,0,0,.35); padding:14px; animation:popin .14s ease-out; }
.pop.left { left:16px; right:auto; }
@keyframes popin { from { opacity:0; transform:translateY(-6px); } }
.pop .row { display:flex; align-items:center; gap:10px; }
.pop label { font-size:12px; color:var(--muted); display:block; margin:0 0 6px; }
.pop select, .pop input[type=number] { background:var(--card); color:var(--text);
        border:1px solid var(--line); border-radius:8px; padding:6px 8px; font:inherit; }
.pop input[type=number] { width:64px; }
.pop input[type=range] { accent-color:var(--accent); width:120px; }
.playbtn { display:flex; align-items:center; justify-content:center; width:34px; height:34px;
           background:var(--accent); color:var(--ink); border:0; border-radius:9px; cursor:pointer; font-size:15px; }
.seg { display:inline-flex; border:1px solid var(--line); border-radius:9px; overflow:hidden; }
.seg .preset { background:transparent; border:0; border-right:1px solid var(--line); color:var(--text);
               padding:6px 12px; cursor:pointer; font:inherit; font-size:13px; }
.seg .preset:last-child { border-right:0; }
.seg .preset:hover { background:color-mix(in srgb, var(--accent) 16%, transparent); }
.swatches { display:flex; gap:8px; }
.swatch { width:34px; height:34px; border-radius:9px; border:2px solid transparent; cursor:pointer;
          padding:0; position:relative; overflow:hidden; }
.swatch.sel { border-color:var(--accent); }
.swatch .dot { position:absolute; right:4px; bottom:4px; width:9px; height:9px; border-radius:50%; }

/* ── Poker layout: rail | main | host console (admin only) ──────── */
.layout { display:grid; grid-template-columns:var(--rail-w) minmax(0,1fr); gap:var(--s4);
          padding:var(--s4); max-width:1440px; margin:0 auto; align-items:start; }
body.is-admin .layout { grid-template-columns:var(--rail-w) minmax(0,1fr) var(--console-w); }
.layout.rail-closed { grid-template-columns:minmax(0,1fr); }
body.is-admin .layout.rail-closed { grid-template-columns:minmax(0,1fr) var(--console-w); }
@media (min-width:700px) { .layout.rail-closed #rail { display:none; } }
/* Low-chrome rail: hairline separators, no boxed panel — the ticket, table,
   and console are the page's only "cards". */
#rail { align-self:start; position:sticky; top:var(--s3); max-height:calc(100vh - var(--s5));
        overflow:auto; padding:var(--s1); }
#rail h2 { font-size:var(--fs-2xs); margin:0 0 var(--s2) var(--s2); color:var(--muted);
           letter-spacing:.08em; text-transform:uppercase; }
#rail-backdrop { display:none; }
/* Rail items are <button>s (click-to-peek for everyone, keyboard-friendly) —
   reset the button chrome so they look like the list rows they always were. */
.rail-item { display:flex; align-items:center; gap:var(--s2); padding:7px var(--s2); border-radius:var(--r-s);
             font-size:var(--fs-s); border:1px solid transparent; width:100%; text-align:left;
             background:transparent; color:inherit; font-family:inherit; cursor:pointer;
             border-bottom:1px solid color-mix(in srgb, var(--line) 45%, transparent); }
.rail-item:hover { border-color:var(--accent); }
.rail-item.current { background:color-mix(in srgb, var(--accent) 12%, transparent); border-color:var(--accent); }
.rail-item.peeking { border-color:var(--accent2); }
.rail-item .dot { width:8px; height:8px; border-radius:50%; background:var(--line); flex:none; }
.rail-item.done .dot { background:var(--accent); }
.rail-item .t { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.rail-item.done .t { color:var(--muted); }
.rail-item .pts { color:var(--accent); font-weight:700; font-size:var(--fs-xs); font-family:var(--font-mono); }

.main { display:flex; flex-direction:column; gap:var(--s4); min-width:0; }
.ticket { background:var(--panel); border:1px solid var(--line); border-radius:var(--r-l);
          padding:var(--s4) var(--s5); box-shadow:var(--shadow-1); }
.tkrow { display:flex; align-items:center; justify-content:space-between; gap:var(--s2); }
.ticket .key a { color:var(--accent2); text-decoration:none; font-weight:600; }
.ticket .key a:hover { text-decoration:underline; }
.ticket .key { font-weight:600; font-size:var(--fs-s); color:var(--accent2); font-family:var(--font-mono); }
.ticket h1 { font-size:var(--fs-xl); font-weight:650; letter-spacing:-.01em; margin:var(--s1) 0 var(--s3);
             line-height:1.3; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:var(--s3); }
.chipi { background:var(--card); border:1px solid var(--line); border-radius:var(--r-pill);
         padding:2px 10px; font-size:var(--fs-xs); color:var(--muted); }
.chipi b { color:var(--text); font-weight:600; }
.chipi.pts b { color:var(--accent); font-family:var(--font-mono); }
.desc, .acc { color:var(--text); white-space:pre-wrap; word-break:break-word; font-size:14px;
        max-height:120px; overflow:hidden; position:relative; }
.desc.open, .acc.open { max-height:none; }
.desc.clipped:not(.open)::after, .acc.clipped:not(.open)::after {
        content:""; position:absolute; left:0; right:0; bottom:0; height:44px;
        background:linear-gradient(transparent, var(--panel)); }
.desc-toggle { background:transparent; border:0; color:var(--accent); cursor:pointer; font:inherit;
               font-size:var(--fs-s); padding:4px 0 0; }
.desc.empty { color:var(--muted); font-style:italic; }
.acc-label { margin:var(--s3) 0 var(--s1); }
.ticket .phase-tag { font-size:12px; border:1px solid var(--line); border-radius:var(--r-pill);
                     padding:2px 10px; color:var(--muted); white-space:nowrap; }
.ticket .phase-tag.revealed { color:var(--accent); border-color:var(--accent); }
.ticket .phase-tag.peek { color:var(--accent2); border-color:var(--accent2); }

/* Peek banner — full-width strip at the top of the panel while previewing */
.peek-banner { display:flex; align-items:center; gap:var(--s2); flex-wrap:wrap; font-size:var(--fs-s);
               color:var(--muted); border:1px solid var(--accent2); border-radius:var(--r-m);
               background:color-mix(in srgb, var(--accent2) 8%, transparent);
               padding:6px 10px; margin-bottom:var(--s3); }
.peek-banner b { color:var(--text); font-family:var(--font-mono); }
.pkbtn { background:var(--card); border:1px solid var(--line); color:var(--text); border-radius:var(--r-s);
         padding:4px 10px; cursor:pointer; font:inherit; font-size:var(--fs-xs); }
.pkbtn:hover { border-color:var(--accent); }
.pkbtn.go { border-color:var(--accent2); color:var(--accent2); font-weight:600; }

/* The table — the page's signature region: a felt surface with the seated
   voters above and your hand (the deck) below, one visual unit. */
.table { background:radial-gradient(120% 130% at 50% 0%, var(--felt) 0%, var(--panel) 75%);
         border:1px solid var(--line); border-radius:var(--r-l); padding:var(--s4) var(--s5) var(--s4);
         box-shadow:var(--shadow-1); }
.vrow { display:flex; flex-wrap:wrap; gap:var(--s3); justify-content:center; min-height:70px;
        align-items:flex-start; padding:var(--s1) 0 var(--s3); }
.voter { display:flex; flex-direction:column; align-items:center; gap:4px; width:64px; }
.voter .face { position:relative; width:40px; height:40px; border-radius:50%; background:var(--card);
               border:1px solid var(--line); display:flex; align-items:center; justify-content:center; font-size:20px; }
.voter .face .tick { position:absolute; right:-4px; bottom:-4px; width:18px; height:18px; border-radius:50%;
               background:var(--accent); color:var(--ink); font-size:11px; display:flex; align-items:center;
               justify-content:center; font-weight:700; }
.voter .vcard { width:40px; height:56px; border-radius:var(--r-s); border:1.5px solid var(--accent);
                background:var(--card); color:var(--accent); font-weight:800; font-size:17px;
                font-family:var(--font-mono); display:flex; align-items:center; justify-content:center;
                animation:flip .35s ease-out backwards; animation-delay:calc(var(--i, 0) * 40ms); }
@keyframes flip { from { transform:rotateY(90deg); } to { transform:rotateY(0); } }
.voter .nm { font-size:11.5px; color:var(--muted); max-width:64px; overflow:hidden;
             text-overflow:ellipsis; white-space:nowrap; }
.vempty { color:var(--muted); font-size:var(--fs-s); }

/* Results — one region below the deck, so revealing never shoves the table
   around. Distribution, median → suggestion, AI take, and the duel all live
   here as flat, eyebrow-labelled sections. */
.results { display:flex; flex-direction:column; gap:var(--s4); padding:var(--s2) var(--s2) 0; }
.results-sum { font-size:var(--fs-s); color:var(--muted); }
.results-sum b { color:var(--accent); font-family:var(--font-mono); font-size:var(--fs-m); }
.dist { display:flex; flex-direction:column; gap:5px; }
.dist .drow { display:flex; align-items:center; gap:var(--s2); font-size:var(--fs-s); }
.dist .dval { width:32px; text-align:right; font-weight:700; font-family:var(--font-mono); }
.dist .dtrack { flex:1; }
.dist .dbar { display:block; height:10px; border-radius:5px; background:var(--accent); opacity:.55;
              max-width:260px; }
.dist .drow.top .dbar { opacity:1; }
.dist .dcount { color:var(--muted); font-size:12px; font-family:var(--font-mono); }

/* AI note card */
.ainote { background:var(--panel-2); border:1px solid var(--line); border-left:3px solid var(--accent2);
          border-radius:var(--r-m); padding:13px 16px; }
.ainote .hd { color:var(--accent2); font-size:13px; font-weight:700; margin-bottom:5px; }
.ainote .bd { white-space:pre-wrap; word-break:break-word; font-size:14px; }
.ainote .sug { margin-top:7px; font-size:13px; color:var(--muted); }
.ainote .sug b { color:var(--accent2); }
.ainote .conf { font-size:11px; font-weight:600; border-radius:9px; padding:1px 8px; margin-left:6px; background:var(--card); color:var(--muted); vertical-align:1px; }
.ainote .conf.c-high { color:var(--accent); }
.ainote .conf.c-medium { color:var(--accent2); }
.ainote .conf.c-low { color:var(--muted); }
.ainote .ev { margin:7px 0 0 18px; padding:0; font-size:13px; color:var(--muted); }
.ainote .ev li { margin:2px 0; }
.ainote.pending .bd { color:var(--muted); font-style:italic; }
/* Duel (open the floor): low vs high voter spotlight + transcript */
.duel { background:var(--panel-2); border:1px solid var(--line); border-left:3px solid var(--accent);
        border-radius:var(--r-m); padding:13px 16px; }
.duel .hd { color:var(--accent); font-size:13px; font-weight:700; margin-bottom:8px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.duel .recind { font-size:11px; color:#f85149; font-weight:600; display:inline-flex; align-items:center; gap:5px; }
.duel .norec { font-size:11px; color:var(--muted); font-weight:400; }
.rec-dot { width:8px; height:8px; border-radius:50%; background:#f85149; animation:blink 1s steps(1) infinite; display:inline-block; }
.duel .dualrow { display:flex; gap:12px; align-items:stretch; }
.duel .duelist { flex:1; background:var(--card); border:1px solid transparent; border-radius:10px; padding:10px 12px; text-align:center; }
.duel .duelist.speaking { border-color:var(--accent); animation:pulse 1.4s ease-in-out infinite; }
.duel .duelist .face { font-size:26px; }
.duel .duelist .dn { font-weight:700; margin-top:3px; }
.duel .duelist .dv { font-size:12.5px; color:var(--muted); }
.duel .duelist .dv b { color:var(--accent2); }
.duel .duelist .floor { font-size:11px; color:var(--accent); font-weight:600; margin-top:4px; }
.duel .duelist .micb { font-size:11px; color:var(--muted); margin-top:2px; }
.duel .vs { align-self:center; font-weight:800; color:var(--muted); }
.duel .youup { margin-top:8px; font-size:13px; color:var(--accent); font-weight:600; }
.duel .mic-row { margin-top:8px; display:flex; align-items:center; gap:10px; }
.duel .hint { color:var(--muted); font-size:12.5px; margin-top:6px; }
.duel .bd { font-size:14px; }
.duel .bd.muted { color:var(--muted); font-style:italic; }
.duel-tx { white-space:pre-wrap; word-break:break-word; font-size:13px; color:var(--muted); max-height:220px; overflow-y:auto; background:var(--card); border-radius:8px; padding:10px 12px; margin-top:4px; }
@keyframes dots { 0%,20% { content:"."; } 40% { content:".."; } 60%,100% { content:"..."; } }
.ainote.pending .bd::after { content:"..."; animation:dots 1.2s steps(1) infinite; }

/* Voting deck — "your hand". Real card anatomy: corner indices + centre value. */
.deck-zone { border-top:1px dashed color-mix(in srgb, var(--line) 70%, transparent); padding-top:var(--s3); }
.deck-status { text-align:center; font-size:var(--fs-xs); color:var(--muted); margin-bottom:var(--s2); }
.deck-status b { color:var(--accent); font-family:var(--font-mono); }
.deck { display:flex; flex-wrap:wrap; gap:10px; justify-content:center; padding:var(--s1) 0 var(--s2); }
.pcard { position:relative; width:58px; height:84px; border-radius:var(--r-m); border:1.5px solid var(--line);
         background:var(--panel); color:var(--text); cursor:pointer;
         display:flex; align-items:center; justify-content:center; transition:transform .12s, border-color .12s;
         font-family:var(--font-mono); }
.pcard .cv { font-weight:800; font-size:20px; }
.pcard .ci { position:absolute; top:5px; left:7px; font-size:10.5px; font-weight:700; opacity:.75; }
.pcard .ci.flip { top:auto; left:auto; right:7px; bottom:5px; transform:rotate(180deg); }
.pcard:hover:not(:disabled) { transform:translateY(-6px); border-color:var(--accent); }
.pcard.sel { transform:translateY(-10px); border-color:var(--accent); background:var(--accent);
             color:var(--ink); box-shadow:0 8px 20px color-mix(in srgb, var(--accent) 35%, transparent); }
.pcard:disabled { opacity:.4; cursor:not-allowed; }

/* ── Host console — the admin's control surface. Grouped by intent, stable
   geometry: controls disable rather than disappear, and the duel slot swaps
   its contents inside a reserved area so nothing shifts mid-round. ── */
.console { position:sticky; top:var(--s3); align-self:start; z-index:26;
           background:var(--panel); border:1px solid var(--line); border-radius:var(--r-l);
           box-shadow:var(--shadow-1); padding:var(--s4);
           max-height:calc(100vh - var(--s5)); overflow:auto; }
.console .cbar { display:none; }
.console .cbody { display:flex; flex-direction:column; gap:var(--s4); }
.cgroup { display:flex; flex-direction:column; gap:var(--s2); padding-bottom:var(--s3);
          border-bottom:1px solid color-mix(in srgb, var(--line) 60%, transparent); }
.cgroup:last-child { border-bottom:0; padding-bottom:0; }
.cgroup .eyebrow { margin-bottom:2px; }
.cbtn { min-height:40px; border-radius:var(--r-m); border:1px solid var(--line); background:var(--panel-2);
        color:var(--text); font:inherit; font-size:var(--fs-s); cursor:pointer;
        display:flex; align-items:center; justify-content:center; gap:6px; padding:0 var(--s3); }
.cbtn:hover:not(:disabled) { border-color:var(--accent); }
.cbtn:disabled { opacity:.4; cursor:not-allowed; }
.cbtn.primary { background:var(--accent); color:var(--ink); border-color:transparent;
                font-weight:700; min-height:44px; }
.cbtn.primary:hover:not(:disabled) { filter:brightness(1.08); }
#reveal-btn.pulse { animation:pulse 1.2s ease-in-out infinite; }
.crow { display:flex; gap:var(--s2); }
.crow .cbtn { flex:1; }
.duel-slot { min-height:48px; display:flex; flex-direction:column; justify-content:center; }
.duel-inline label { font-size:var(--fs-2xs); color:var(--muted); display:block; margin:0 0 6px; }
.duel-inline .dhint { font-size:var(--fs-xs); margin:8px 0 0; }
.readout { display:flex; justify-content:space-between; align-items:baseline;
           font-size:var(--fs-s); color:var(--muted); }
.readout b { font-family:var(--font-mono); color:var(--text); font-size:var(--fs-m); }
.finrow { display:flex; align-items:center; gap:var(--s2); }
.finrow .finlab { font-size:var(--fs-s); color:var(--muted); flex:1; }
.finrow input { width:72px; background:var(--card); color:var(--text); border:1px solid var(--line);
                border-radius:var(--r-s); padding:8px; font:inherit; font-family:var(--font-mono);
                font-weight:700; text-align:center; }
.finrow input:disabled { opacity:.4; }
#final-src { font-size:var(--fs-2xs); color:var(--muted); border:1px solid var(--line);
             border-radius:var(--r-pill); padding:1px 8px; }
.navrow { display:flex; align-items:center; gap:var(--s2); }
.navrow .nav { width:44px; flex:none; }
#console-pos { flex:1; text-align:center; font-family:var(--font-mono); font-size:var(--fs-s);
               color:var(--muted); }
#console-notice { color:var(--danger); font-size:var(--fs-xs); white-space:pre-wrap; margin-top:var(--s2); }
.toast { position:fixed; left:50%; bottom:74px; transform:translateX(-50%); z-index:27;
         background:#3d1216; color:#ffb3b8; border:1px solid var(--danger); border-radius:10px;
         padding:9px 16px; font-size:13.5px; max-width:80vw; }

/* ── Responsive: 700–1099 = console becomes a bottom sheet; <700 = single
   column, rail drawer, deck fixed in the thumb zone. ── */
@media (max-width:1099px) {
  body.is-admin .layout, body.is-admin .layout.rail-closed { grid-template-columns:var(--rail-w) minmax(0,1fr); }
  .layout.rail-closed { grid-template-columns:minmax(0,1fr); }
  .console { position:fixed; left:0; right:0; bottom:0; top:auto; z-index:26; max-height:none;
             border-radius:var(--r-l) var(--r-l) 0 0; box-shadow:var(--shadow-2);
             padding:var(--s2) var(--s4) calc(var(--s2) + env(safe-area-inset-bottom)); }
  .console .cbar { display:flex; align-items:center; gap:var(--s2); }
  .console .cbar .cphase { font-size:var(--fs-xs); color:var(--muted); text-transform:uppercase;
                           letter-spacing:.06em; min-width:64px; }
  .console .cbar #console-primary { flex:1; }
  .console .cbar #console-toggle { width:44px; flex:none; }
  .console.expanded #console-toggle { transform:rotate(180deg); }
  .console .cbody { display:none; }
  .console.expanded .cbody { display:flex; max-height:55vh; overflow:auto; padding-top:var(--s3); }
  body.is-admin .main { padding-bottom:84px; }
}
@media (max-width:699px) {
  .layout, body.is-admin .layout, .layout.rail-closed, body.is-admin .layout.rail-closed {
    grid-template-columns:minmax(0,1fr); padding:var(--s3); }
  #rail { position:fixed; top:0; bottom:0; left:0; width:min(78vw,320px); z-index:29;
          background:var(--panel); border-right:1px solid var(--line); padding:var(--s4);
          transform:translateX(-105%); transition:transform .18s ease-out; max-height:none; margin:0; }
  .layout.rail-open-m #rail { transform:none; }
  #rail-backdrop { display:block; position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:28; }
  #rail-backdrop.hidden { display:none; }
  .pop { left:8px; right:8px; }
  .deck-zone { position:fixed; left:0; right:0; bottom:0; z-index:24; background:var(--panel);
               border-top:1px solid var(--line); margin:0;
               padding:var(--s2) var(--s3) calc(var(--s2) + env(safe-area-inset-bottom)); }
  body.is-admin .deck-zone { bottom:58px; }
  .deck { flex-wrap:nowrap; overflow-x:auto; justify-content:flex-start;
          scroll-snap-type:x mandatory; padding:var(--s1) 0; }
  .pcard { flex:none; scroll-snap-align:center; width:52px; height:76px; }
  .main { padding-bottom:150px; }
  body.is-admin .main { padding-bottom:210px; }
}

.banner { position:fixed; left:50%; bottom:18px; transform:translateX(-50%); z-index:28;
          background:var(--panel); color:var(--text); border:1px solid var(--accent);
          border-radius:999px; padding:9px 18px; font-size:13.5px; cursor:pointer;
          box-shadow:0 8px 24px rgba(0,0,0,.4); }
.banner.lock { border-color:#f85149; cursor:default; }

.overlay { position:fixed; inset:0; background:rgba(0,0,0,.75); display:flex;
           align-items:center; justify-content:center; padding:16px; z-index:20; }
.overlay .box { background:var(--panel); border:1px solid var(--line); border-radius:14px;
                padding:20px; width:100%; max-width:460px; }
.overlay h2 { margin:0 0 12px; color:var(--accent); }
.overlay label { font-size:12px; color:var(--muted); display:block; margin:10px 0 4px; }
.namerow { display:flex; gap:8px; }
.field { flex:1; background:var(--card); color:var(--text); border:1px solid var(--line);
         border-radius:8px; padding:9px; font:inherit; width:100%; }
textarea.field { resize:vertical; }
.dice { background:var(--panel); border:1px solid var(--line); border-radius:8px;
        padding:0 12px; cursor:pointer; font-size:18px; }
.join-btn { background:var(--accent); color:var(--ink); border:0; border-radius:8px;
            padding:0 20px; font:inherit; font-weight:700; cursor:pointer; white-space:nowrap; }
.join-btn:hover { filter:brightness(1.1); }
.avatars-pick { display:flex; flex-wrap:wrap; gap:6px; margin:12px 0; }
.av { font-size:22px; background:var(--card); border:1px solid var(--line); border-radius:10px;
      width:40px; height:40px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
.av.sel { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 18%, transparent); }
.primary { width:100%; margin-top:6px; background:var(--accent); color:var(--ink); border:0;
           border-radius:8px; padding:10px; font:inherit; font-weight:700; cursor:pointer; }
.warn { color:#e3b341; font-size:12.5px; margin-top:8px; }
.muted { color:var(--muted); }
.qrwrap { background:#fff; border-radius:10px; padding:10px; display:flex; justify-content:center; }
.qrwrap img { width:220px; height:220px; }
.hidden { display:none !important; }
#confetti { position:fixed; inset:0; pointer-events:none; z-index:30; }
"""


_JS = r"""
const DECK = __DECK__;
const AVATARS = __AVATARS__;
const ADJS = __ADJS__;
const NOUNS = __NOUNS__;

let TOKEN = new URLSearchParams(location.search).get("token") || sessionStorage.getItem("poker_token") || "";
// The admin secret only ever rides in the host's private link (server.py appends
// &admin=…). Whoever has it gets the host controls (reveal/finalize/edit/AI/…).
let ADMIN = new URLSearchParams(location.search).get("admin") || sessionStorage.getItem("poker_admin") || "";
let IS_ADMIN = !!ADMIN;
let PID = localStorage.getItem("poker_pid");
if (!PID) { PID = (self.crypto && crypto.randomUUID) ? crypto.randomUUID() : "p" + Math.random().toString(36).slice(2); localStorage.setItem("poker_pid", PID); }
let NAME = localStorage.getItem("poker_name") || "";
let AVATAR = localStorage.getItem("poker_avatar") || AVATARS[0];
let THEME = localStorage.getItem("poker_theme") || "midnight";
let JOINED = false, LOOPING = false;
let TIMER = { running: false }, OFFSET = 0, firedFor = null;
let LAST_STATE = null;
let lastBcastTheme = null, lastMusicSeq = 0;
let LOCKED = false;
let DESC_OPEN = false;                 // collapsible description state (survives re-render)
let ACC_OPEN = false;                  // collapsible acceptance-criteria state
let FIN_SRC = "";                      // provenance of the final-points value: "median" | "edited"
let RAIL_OPEN = true;
let lastPhaseKey = null;               // "<ticket_index>:<phase>" — prefill finalize once per reveal
let lastNotice = "";                  // last error toast we showed (admin)
let lastTicketKey = null;              // reset DESC_OPEN when the ticket changes
let PEEK = null;                       // rail index being previewed locally, or null (live view)
let TICKET_CACHE = {};                 // index -> ticket from GET /api/ticket (carries .rev)
let PEEK_FETCHING = "";                // "index:rev" in flight — dedupes refetches across polls

// Escapes quotes too (not just &<>): esc()'d values land inside double-quoted
// HTML attributes (title="…", href="…"), where an unescaped quote would let a
// participant-chosen name break out of the attribute and run script.
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
// Ticket URLs come from the tracker, so they are attacker-influenced. esc()
// stops attribute breakout but does NOT neutralise a `javascript:` scheme —
// it contains none of the characters esc() rewrites, so it reaches href intact
// and runs on click. Allowlist the scheme; unsafe URLs render unlinked.
// Mirrors yeaboi.html_theme.safe_url on the Python side.
function safeUrl(u) {
  // Browsers remove TAB/LF/CR from anywhere in a URL before parsing it, so
  // `java&#9;script:` would otherwise slip past the scheme check below.
  const s = String(u == null ? "" : u).trim().replace(/[\t\n\r]/g, "");
  if (!s || s.slice(0, 2) === "//") return "";
  const m = /^([A-Za-z][A-Za-z0-9+.\-]*):/.exec(s);
  if (!m) return s;                                     // relative — inert
  return ["http", "https", "mailto"].indexOf(m[1].toLowerCase()) >= 0 ? s : "";
}
function api(path) { return path + (path.indexOf("?") < 0 ? "?" : "&") + "token=" + encodeURIComponent(TOKEN); }
function postJSON(path, body) {
  // `admin` is sent on every POST but only checked by the server on /api/admin/*
  // and /api/timer — harmless (empty) for teammates who never got the secret.
  return fetch(api(path), { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ pid: PID, admin: ADMIN }, body || {})) });
}
function randomName() { return ADJS[Math.floor(Math.random()*ADJS.length)] + " " + NOUNS[Math.floor(Math.random()*NOUNS.length)]; }
function applyTheme(t) { THEME = t; document.documentElement.setAttribute("data-theme", t); localStorage.setItem("poker_theme", t); }
function fmtPts(v) { if (v == null) return "—"; return (v === Math.trunc(v)) ? String(Math.trunc(v)) : String(v); }

/* ── Join code gate (identical to retro) ────────────────────── */
async function submitCode() {
  const code = (document.getElementById("code-in").value || "").trim();
  if (!code) return;
  const err = document.getElementById("code-err"); err.textContent = "";
  try {
    const r = await fetch("/api/join", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    if (!r.ok) { err.textContent = "That code didn't work — check the host's screen."; return; }
    const d = await r.json();
    TOKEN = d.token;
    document.getElementById("code-modal").classList.add("hidden");
    afterToken();
  } catch (e) { err.textContent = "Could not reach the session."; }
}

/* ── Profile (name + avatar), also used to rename later ─────── */
function buildAvatars() {
  const wrap = document.getElementById("avatars");
  wrap.innerHTML = AVATARS.map(a => '<button class="av" data-av="' + a + '">' + a + '</button>').join("");
  wrap.querySelectorAll(".av").forEach(b => b.addEventListener("click", () => {
    AVATAR = b.getAttribute("data-av");
    wrap.querySelectorAll(".av").forEach(x => x.classList.toggle("sel", x === b));
  }));
  markAvatar();
}
function markAvatar() { document.querySelectorAll(".av").forEach(x => x.classList.toggle("sel", x.getAttribute("data-av") === AVATAR)); }
function openProfile() { document.getElementById("name-in").value = NAME; markAvatar(); document.getElementById("modal").classList.remove("hidden"); document.getElementById("name-in").focus(); }
function saveProfile() {
  const v = (document.getElementById("name-in").value || "").trim();
  NAME = v || randomName();
  localStorage.setItem("poker_name", NAME);
  localStorage.setItem("poker_avatar", AVATAR);
  document.getElementById("modal").classList.add("hidden");
  JOINED = true;
  paintMe();
  startLoop();
}
function paintMe() { document.getElementById("me").innerHTML = (AVATAR || "🙂") + " " + esc(NAME) + ' <span class="pen">✎</span>'; }

/* ── Voting ─────────────────────────────────────────────────── */
async function vote(value) {
  // Tapping your selected card again withdraws the vote.
  const mine = LAST_STATE && LAST_STATE.mine_value;
  const path = (mine === value) ? "/api/vote/clear" : "/api/vote";
  try { const r = await postJSON(path, { value }); if (r.ok) render((await r.json()).state); } catch (e) {}
}
function buildDeck() {
  // Playing-card anatomy: small corner indices (mirrored bottom-right) + a
  // big centre value — the deck reads as a hand of cards, not buttons.
  const d = document.getElementById("deck");
  d.innerHTML = DECK.map(v => '<button class="pcard" data-v="' + esc(v) + '">' +
    '<span class="ci">' + esc(v) + '</span><span class="cv">' + esc(v) + '</span>' +
    '<span class="ci flip">' + esc(v) + "</span></button>").join("");
  d.querySelectorAll(".pcard").forEach(b => b.addEventListener("click", () => vote(b.getAttribute("data-v"))));
}
function paintDeck(state) {
  // PEEK guard: votes always apply to the LIVE ticket, so the deck must never
  // sit active under a previewed one — that would invite mis-votes.
  const disabled = state.locked || state.phase !== "voting" || !state.ticket || PEEK != null;
  document.querySelectorAll(".pcard").forEach(b => {
    b.disabled = disabled;
    b.classList.toggle("sel", b.getAttribute("data-v") === state.mine_value);
  });
  // The status line says WHY the deck is open/closed — disabled cards alone
  // don't explain themselves.
  const st = document.getElementById("deck-status");
  if (PEEK != null) st.innerHTML = "Previewing a ticket — <b>Back to live</b> to vote";
  else if (state.locked) st.textContent = "🔒 Voting locked by the host";
  else if (!state.ticket) st.textContent = "No tickets loaded";
  else if (state.phase !== "voting") st.textContent = "Voting closed — waiting for the host";
  else if (state.mine_value) st.innerHTML = "Your vote: <b>" + esc(state.mine_value) + "</b> — tap it again to withdraw";
  else st.textContent = "Voting open — pick a card";
  document.getElementById("deck-zone").setAttribute("data-state", disabled ? "closed" : "open");
}

/* ── Ticket panel + rail ────────────────────────────────────── */
function ticketBodyHtml(t, tag) {
  // Shared key/summary/chips/description/AC markup for the live AND peeked
  // ticket — one source of truth for how a ticket body looks.
  if (t.key !== lastTicketKey) { lastTicketKey = t.key; DESC_OPEN = false; ACC_OPEN = false; }
  const safeTicketUrl = safeUrl(t.url);
  const key = safeTicketUrl ? '<a href="' + esc(safeTicketUrl) + '" target="_blank" rel="noopener">' + esc(t.key) + " ↗</a>" : esc(t.key);
  const chips =
    (t.type ? '<span class="chipi">' + esc(t.type) + "</span>" : "") +
    (t.state ? '<span class="chipi">status <b>' + esc(t.state) + "</b></span>" : "") +
    (t.assignee ? '<span class="chipi">assignee <b>' + esc(t.assignee) + "</b></span>" : "") +
    '<span class="chipi pts">points <b>' + esc(fmtPts(t.story_points)) + "</b></span>" +
    (t.estimated ? '<span class="chipi">✓ <b>estimated ' + esc(fmtPts(t.final_points)) + "</b></span>" : "");
  const descText = t.description_text || "";
  const desc = descText
    ? '<div class="desc' + (DESC_OPEN ? " open" : "") + (descText.length > 350 ? " clipped" : "") + '" id="desc">' + esc(descText) + "</div>" +
      (descText.length > 350 ? '<button class="desc-toggle" id="desc-toggle">' + (DESC_OPEN ? "Show less ▲" : "Show more ▼") + "</button>" : "")
    : '<div class="desc empty">No description.</div>';
  // Acceptance criteria get their own labelled section (omitted when the
  // tracker has none — no empty-state noise).
  const accText = t.acceptance_text || "";
  const acc = accText
    ? '<div class="eyebrow acc-label">Acceptance criteria</div>' +
      '<div class="acc' + (ACC_OPEN ? " open" : "") + (accText.length > 350 ? " clipped" : "") + '" id="acc">' + esc(accText) + "</div>" +
      (accText.length > 350 ? '<button class="desc-toggle" id="acc-toggle">' + (ACC_OPEN ? "Show less ▲" : "Show more ▼") + "</button>" : "")
    : "";
  return '<div class="tkrow"><span class="key">' + key + "</span>" + (tag || "") + "</div>" +
    "<h1>" + esc(t.summary) + "</h1>" + '<div class="chips">' + chips + "</div>" + desc + acc;
}
function wireDescToggle() {
  const tg = document.getElementById("desc-toggle");
  if (tg) tg.onclick = () => { DESC_OPEN = !DESC_OPEN; renderTicket(LAST_STATE); };
  const ta = document.getElementById("acc-toggle");
  if (ta) ta.onclick = () => { ACC_OPEN = !ACC_OPEN; renderTicket(LAST_STATE); };
}
function peekTicket(i) {
  // Local, read-only preview — nothing goes to the server, nobody else's view
  // changes. Clicking the live ticket (or the peeked one again) returns live.
  if (!LAST_STATE) return;
  PEEK = (i === LAST_STATE.ticket_index || i === PEEK) ? null : i;
  render(LAST_STATE);
}
async function fetchTicket(i, rev) {
  const tag = i + ":" + rev;
  if (PEEK_FETCHING === tag) return;
  PEEK_FETCHING = tag;
  try {
    const r = await fetch(api("/api/ticket") + "&i=" + i);
    if (!r.ok) { if (PEEK === i) { PEEK = null; if (LAST_STATE) render(LAST_STATE); } return; }
    const t = await r.json();
    TICKET_CACHE[i] = t;
    // Apply only if this index is still the one being peeked (stale-response guard).
    if (PEEK === i && LAST_STATE) render(LAST_STATE);
  } catch (e) {}
  finally { if (PEEK_FETCHING === tag) PEEK_FETCHING = ""; }
}
function renderPeek(state, box) {
  const meta = (state.tickets_meta || [])[PEEK] || {};
  const cached = TICKET_CACHE[PEEK];
  // Refetch when the per-ticket content revision moved (edit/finalize) — the
  // peeked view is stale for at most one poll cycle.
  if (!cached || cached.rev !== meta.rev) fetchTicket(PEEK, meta.rev || 0);
  const live = (state.tickets_meta || [])[state.ticket_index] || {};
  const goBtn = (IS_ADMIN && state.phase !== "duel")
    ? '<button class="pkbtn go" id="peek-goto-btn">Vote on this ticket</button>' : "";
  const banner = '<div class="peek-banner" id="peek-banner">👁 Previewing <b>' + esc(meta.key || "ticket " + (PEEK + 1)) +
    "</b> — the team is voting on <b>" + esc(live.key || "ticket " + (state.ticket_index + 1)) + "</b>" +
    '<button class="pkbtn" id="peek-live-btn">Back to live</button>' + goBtn + "</div>";
  const body = cached
    ? ticketBodyHtml(cached, '<span class="phase-tag peek">👁 preview</span>')
    : '<div class="desc empty">Loading ticket…</div>';
  box.innerHTML = banner + body;
  wireDescToggle();
  document.getElementById("peek-live-btn").onclick = () => { PEEK = null; render(LAST_STATE); };
  const go = document.getElementById("peek-goto-btn");
  if (go) go.onclick = () => { const i = PEEK; PEEK = null; adminPost("/api/admin/goto", { index: i }); };
}
function renderTicket(state) {
  const box = document.getElementById("ticket");
  // Peek housekeeping: fall back to live when the peeked ticket became the
  // live one, or its index no longer exists.
  if (PEEK != null && (PEEK === state.ticket_index || PEEK >= state.ticket_count)) PEEK = null;
  if (PEEK != null) { renderPeek(state, box); return; }
  const t = state.ticket;
  if (!t) { box.innerHTML = '<div class="vempty">No tickets loaded.</div>'; return; }
  const phaseTag = state.phase === "revealed"
    ? '<span class="phase-tag revealed">votes revealed</span>'
    : state.phase === "duel"
      ? '<span class="phase-tag revealed">⚔ the floor is open</span>'
      : '<span class="phase-tag">voting ' + (state.ticket_index + 1) + "/" + state.ticket_count + '</span>';
  box.innerHTML = ticketBodyHtml(t, phaseTag);
  wireDescToggle();
}
function renderRail(state) {
  const box = document.getElementById("rail-list");
  const items = state.tickets_meta || [];
  box.innerHTML = items.map((t, i) => {
    const cls = "rail-item" + (i === state.ticket_index ? " current" : "") + (i === PEEK ? " peeking" : "") + (t.estimated ? " done" : "");
    const cur = i === state.ticket_index ? ' aria-current="true"' : "";
    const pts = t.estimated ? '<span class="pts">' + esc(fmtPts(t.final_points)) + "</span>" : "";
    return '<button type="button" class="' + cls + '" data-i="' + i + '"' + cur + ' title="' + esc(t.summary) + '">' +
      '<span class="dot"></span><span class="t">' + esc(t.key ? t.key + " · " : "") + esc(t.summary) + "</span>" + pts + "</button>";
  }).join("");
  // Everyone can click a ticket to read it (peek). Admins jump the room from
  // the explicit button inside the peek banner — a rail click never resets a
  // round by accident anymore.
  box.querySelectorAll(".rail-item").forEach(el =>
    el.onclick = () => { peekTicket(parseInt(el.getAttribute("data-i"), 10)); closeMobileRail(); });
}

/* ── The table: voter seats (values render post-reveal) ─────── */
let lastVrowHtml = "";
function renderVoters(state) {
  const box = document.getElementById("vrow");
  const revealed = state.phase !== "voting";  // duel keeps values visible too
  const people = state.votes || [];
  let html;
  if (!people.length) {
    html = '<div class="vempty">' + (revealed ? "No votes were cast." : "Waiting for the team — share the code to invite them.") + "</div>";
  } else if (revealed) {
    // --i staggers the flip 40ms per seat — the cards turn over around the
    // table instead of all at once.
    html = people.map((p, i) =>
      '<div class="voter"><div class="vcard" style="--i:' + i + '">' + esc(p.value) + '</div><span class="nm" title="' + esc(p.name) + '">' +
      (p.avatar ? esc(p.avatar) + " " : "") + esc(p.name) + "</span></div>").join("");
  } else {
    html = people.map(p =>
      '<div class="voter"><div class="face">' + (p.avatar || "🙂") + (p.voted ? '<span class="tick">✓</span>' : "") +
      '</div><span class="nm" title="' + esc(p.name) + '">' + esc(p.name) + "</span></div>").join("");
  }
  // Skip the write when nothing changed: replacing innerHTML every poll would
  // restart the flip animation and make the cards flicker each tick.
  if (html !== lastVrowHtml) { lastVrowHtml = html; box.innerHTML = html; }
}

/* ── Results: distribution + median + AI + duel, one region ──── */
function renderResults(state) {
  const box = document.getElementById("results");
  const revealed = state.phase === "revealed" || state.phase === "duel";
  const ai = state.ai || {};
  // Below the deck by design: appearing results never push the table around.
  const show = revealed || !!state.duel || !!ai.pending || !!ai.note;
  box.classList.toggle("hidden", !show);
  const sum = document.getElementById("results-sum");
  if (revealed && state.median != null) {
    sum.innerHTML = "median " + esc(fmtPts(state.median)) +
      (state.suggestion != null ? " → suggested <b>" + esc(fmtPts(state.suggestion)) + "</b>" : "");
    sum.classList.remove("hidden");
  } else { sum.classList.add("hidden"); sum.innerHTML = ""; }
  const dist = document.getElementById("dist");
  if (revealed && Object.keys(state.distribution || {}).length) {
    const entries = Object.entries(state.distribution);
    const max = Math.max.apply(null, entries.map(e => e[1]));
    dist.innerHTML = entries.map(([v, n]) =>
      '<div class="drow' + (n === max ? " top" : "") + '"><span class="dval">' + esc(v) + '</span>' +
      '<span class="dtrack"><span class="dbar" style="width:' + (n / max * 100) + '%"></span></span>' +
      '<span class="dcount">' + n + "</span></div>").join("");
    dist.classList.remove("hidden");
  } else { dist.classList.add("hidden"); dist.innerHTML = ""; }
}
function renderAi(state) {
  const box = document.getElementById("ainote");
  const ai = state.ai || {};
  if (ai.pending) {
    box.className = "ainote pending";
    box.innerHTML = '<div class="hd">🤖 AI perspective</div><div class="bd">Thinking</div>';
    box.classList.remove("hidden");
  } else if (ai.note) {
    box.className = "ainote";
    const conf = ai.confidence
      ? ' <span class="conf c-' + esc(ai.confidence) + '">' + esc(ai.confidence) + " confidence</span>"
      : "";
    const evidence = (ai.evidence || []).length
      ? '<ul class="ev">' + (ai.evidence || []).map((e) => "<li>" + esc(e) + "</li>").join("") + "</ul>"
      : "";
    box.innerHTML = '<div class="hd">🤖 AI perspective' + conf + '</div><div class="bd">' + esc(ai.note) + "</div>" +
      evidence +
      (ai.suggested != null ? '<div class="sug">AI suggests <b>' + esc(fmtPts(ai.suggested)) + " points</b></div>" : "");
    box.classList.remove("hidden");
  } else { box.classList.add("hidden"); }
}

/* ── Duel: open the floor (low vs high voter debate) ────────── */
// Browser mic capture needs a secure context (localhost or the HTTPS remote
// link) — plain-HTTP LAN pages can't record; the host's room mic covers them.
let MIC = { stream: null, rec: null, chunks: [], turnNo: 0 };
function micCapable() { return !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia); }
async function enableMic() {
  // Explicit tap = the user gesture + consent moment; the OS permission prompt follows.
  try {
    MIC.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    postJSON("/api/duel/mic", { on: true });
  } catch (e) {
    const h = document.getElementById("mic-hint");
    if (h) h.textContent = "Mic permission denied — the room mic covers you.";
  }
  if (LAST_STATE) renderDuel(LAST_STATE);
}
function startTurnRecorder(turnNo) {
  if (!MIC.stream || MIC.rec || !window.MediaRecorder) return;
  let opts = {};
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  for (let i = 0; i < types.length; i++) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(types[i])) { opts = { mimeType: types[i] }; break; }
  }
  try { MIC.rec = new MediaRecorder(MIC.stream, opts); }
  catch (e) { try { MIC.rec = new MediaRecorder(MIC.stream); } catch (e2) { return; } }
  MIC.chunks = []; MIC.turnNo = turnNo;
  MIC.rec.ondataavailable = e => { if (e.data && e.data.size) MIC.chunks.push(e.data); };
  MIC.rec.onstop = () => {
    const blob = new Blob(MIC.chunks); MIC.chunks = [];
    if (blob.size) uploadDuelAudio(blob, MIC.turnNo, 1);  // fits the server's post-close grace window
  };
  MIC.rec.start();
}
function stopTurnRecorder() { if (MIC.rec) { try { MIC.rec.stop(); } catch (e) {} MIC.rec = null; } }
function uploadDuelAudio(blob, turn, attempt) {
  fetch(api("/api/duel/audio") + "&pid=" + encodeURIComponent(PID) + "&turn=" + turn, { method: "POST", body: blob })
    .then(r => { if (!r.ok && r.status >= 500 && attempt === 1) setTimeout(() => uploadDuelAudio(blob, turn, 2), 1000); })
    .catch(() => { if (attempt === 1) setTimeout(() => uploadDuelAudio(blob, turn, 2), 1000); });
}
function releaseMic() {
  stopTurnRecorder();
  if (MIC.stream) {
    try { MIC.stream.getTracks().forEach(t => t.stop()); } catch (e) {}
    MIC.stream = null;
    postJSON("/api/duel/mic", { on: false });
  }
}
function duelistCard(duel, role) {
  const d = duel[role] || {};
  const speaking = duel.status === "live" && duel.turn === role;
  return '<div class="duelist' + (speaking ? " speaking" : "") + '">' +
    '<div class="face">' + (d.avatar || "🙂") + "</div>" +
    '<div class="dn">' + esc(d.name) + "</div>" +
    '<div class="dv">argues for <b>' + esc(d.value) + "</b></div>" +
    (speaking ? '<div class="floor">has the floor</div>' : "") +
    (duel.recording && duel.recording[role] ? '<div class="micb">🎙 mic on</div>' : "") +
    "</div>";
}
function renderDuel(state) {
  const box = document.getElementById("duel");
  const duel = state.duel;
  if (!duel) { box.classList.add("hidden"); box.innerHTML = ""; releaseMic(); return; }
  box.classList.remove("hidden");
  if (duel.status === "live") {
    const anyRec = duel.recording && (duel.recording.host || duel.recording.low || duel.recording.high);
    const recind = anyRec
      ? '<span class="recind"><span class="rec-dot"></span>RECORDING</span>'
      : '<span class="norec">no mic recording — the debate won\'t be transcribed</span>';
    const mine = duel.mine_role;
    let micRow = "";
    if (mine && !MIC.stream) {
      micRow = micCapable()
        ? '<div class="mic-row"><button class="tbtn primary" id="mic-btn">🎙 Start my mic</button>' +
          '<span class="hint">record your own turn — attributed to you in the transcript</span></div>'
        : '<div class="hint" id="mic-hint">Mic unavailable over http:// — the room mic covers you.</div>';
    }
    const youUp = mine && duel.turn === mine ? '<div class="youup">You\'re up — make your case!</div>' : "";
    box.innerHTML = '<div class="hd">⚔️ The floor is open ' + recind + "</div>" +
      '<div class="dualrow">' + duelistCard(duel, "low") + '<span class="vs">VS</span>' + duelistCard(duel, "high") + "</div>" +
      youUp + micRow +
      (duel.recording && duel.recording.host ? '<div class="hint">Host room mic is recording the debate.</div>' : "");
    const mb = document.getElementById("mic-btn");
    if (mb) mb.onclick = enableMic;
    // Poll-driven mic choreography: record only during MY turn.
    if (MIC.stream && mine) {
      if (duel.turn === mine && !MIC.rec) startTurnRecorder(duel.turn_no);
      else if (duel.turn !== mine && MIC.rec) stopTurnRecorder();
    }
  } else {
    if (MIC.rec) stopTurnRecorder();  // floor closed mid-turn: flush the upload
    if (duel.status === "transcribing") {
      box.innerHTML = '<div class="hd">⚔️ Duel</div>' +
        '<div class="bd muted">Transcribing the debate… (the first run may download the speech model)</div>';
    } else if (duel.status === "done") {
      box.innerHTML = '<div class="hd">⚔️ Duel — ' + esc(duel.low.name) + " vs " + esc(duel.high.name) + "</div>" +
        '<div class="duel-tx">' + esc(duel.transcript) + "</div>";
    } else {
      box.innerHTML = '<div class="hd">⚔️ Duel</div><div class="bd muted">' + esc(duel.error || "Recording failed.") + "</div>";
    }
    releaseMic();
  }
}

/* ── Admin dock ─────────────────────────────────────────────── */
async function adminPost(path, body) {
  try {
    const r = await postJSON(path, body);
    const d = await r.json().catch(() => null);
    if (d && d.error && IS_ADMIN) showToast(d.error);
    if (d && d.state) render(d.state);
  } catch (e) {}
}
let toastTimer = null;
function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 6000);
}
function paintConsole(state) {
  if (!IS_ADMIN) return;
  const revealed = state.phase === "revealed";
  const dueling = state.phase === "duel";
  const duel = state.duel;
  const transcribing = !!(duel && duel.status === "transcribing");
  const hasTicket = !!state.ticket;
  document.getElementById("console").setAttribute("data-phase", dueling ? "duel" : state.phase);
  // ROUND — controls disable rather than disappear (stable geometry).
  const reveal = document.getElementById("reveal-btn");
  reveal.disabled = !hasTicket || revealed || dueling;
  reveal.textContent = revealed || dueling ? "Revealed ✓" : "Reveal votes";
  // Pulse when everyone present has voted (the "all in" cue).
  const present = state.votes || [];
  const allIn = !revealed && !dueling && present.length > 0 && present.every(p => p.voted);
  reveal.classList.toggle("pulse", allIn);
  document.getElementById("revote-btn").disabled = !hasTicket || dueling;
  // INSIGHT — the duel slot swaps presets ↔ live controls in place.
  document.getElementById("ai-btn").disabled = !revealed || (state.ai && state.ai.pending);
  document.getElementById("duel-btn").disabled = !revealed || !!(duel && (duel.status === "live" || transcribing));
  const liveCtl = document.getElementById("duel-live-controls");
  liveCtl.classList.toggle("hidden", !dueling);
  if (dueling) document.getElementById("duel-pop").classList.add("hidden");
  document.getElementById("duel-next-btn").disabled = !dueling || !duel || duel.turn !== "low";
  document.getElementById("duel-close-btn").disabled = !dueling;
  // DECISION — median/AI readouts + the final-points input with provenance.
  document.getElementById("console-median").textContent = revealed && state.median != null ? fmtPts(state.median) : "—";
  document.getElementById("console-sug").textContent = state.ai && state.ai.suggested != null ? fmtPts(state.ai.suggested) : "—";
  // Finalize is locked while the duel transcript is still being produced —
  // finalizing then would silently drop the debate from the record.
  document.getElementById("finalize-btn").disabled = !revealed || transcribing;
  const fin = document.getElementById("final-pts");
  fin.disabled = !revealed || transcribing;
  // NAVIGATE
  document.getElementById("edit-btn").disabled = !hasTicket || dueling;
  document.getElementById("prev-btn").disabled = !hasTicket || dueling || state.ticket_index <= 0;
  document.getElementById("next-btn").disabled = !hasTicket || dueling || state.ticket_index >= state.ticket_count - 1;
  document.getElementById("console-pos").textContent = hasTicket ? state.ticket_index + 1 + " / " + state.ticket_count : "– / –";
  // Tracker errors stay visible here (the toast is transient).
  const notice = document.getElementById("console-notice");
  notice.textContent = state.notice || "";
  notice.classList.toggle("hidden", !state.notice);
  // Prefill the finalize input once per reveal (never clobber the admin mid-typing).
  const phaseKey = state.ticket_index + ":" + state.phase;
  if (phaseKey !== lastPhaseKey) {
    lastPhaseKey = phaseKey;
    if (revealed) {
      fin.value = state.suggestion != null ? fmtPts(state.suggestion) : "";
      FIN_SRC = state.suggestion != null ? "median" : "";
    }
  }
  const src = document.getElementById("final-src");
  src.textContent = FIN_SRC;
  src.classList.toggle("hidden", !FIN_SRC || fin.disabled);
  // Mobile collapsed bar: phase label + the one action that matters now.
  document.getElementById("console-phase").textContent =
    dueling ? "duel" : transcribing ? "transcribing" : hasTicket ? state.phase : "idle";
  const primary = document.getElementById("console-primary");
  if (dueling) { primary.textContent = "Close the floor"; primary.disabled = false; }
  else if (transcribing) { primary.textContent = "Transcribing…"; primary.disabled = true; }
  else if (revealed) { primary.textContent = "Save & next" + (fin.value ? " · " + fin.value : ""); primary.disabled = false; }
  else { primary.textContent = "Reveal votes"; primary.disabled = reveal.disabled; }
}
function finalize() {
  const v = parseFloat(document.getElementById("final-pts").value);
  if (isNaN(v)) { showToast("Enter the agreed story points first."); return; }
  adminPost("/api/admin/finalize", { points: v });
}

/* ── Edit-ticket modal (admin) ──────────────────────────────── */
function openEdit() {
  const t = LAST_STATE && LAST_STATE.ticket;
  if (!t) return;
  document.getElementById("edit-summary").value = t.summary || "";
  document.getElementById("edit-desc").value = t.description_text || "";
  document.getElementById("edit-pts").value = t.story_points != null ? fmtPts(t.story_points) : "";
  document.getElementById("edit-warn").classList.toggle("hidden", t.source !== "azdevops");
  document.getElementById("edit-modal").classList.remove("hidden");
}
function saveEdit() {
  const t = LAST_STATE && LAST_STATE.ticket;
  if (!t) return;
  const body = { key: t.key };
  const summary = document.getElementById("edit-summary").value.trim();
  const desc = document.getElementById("edit-desc").value;
  const ptsRaw = document.getElementById("edit-pts").value.trim();
  if (summary && summary !== t.summary) body.summary = summary;
  if (desc !== (t.description_text || "")) body.description = desc;
  if (ptsRaw !== "" && parseFloat(ptsRaw) !== t.story_points) body.points = parseFloat(ptsRaw);
  document.getElementById("edit-modal").classList.add("hidden");
  if (Object.keys(body).length > 1) adminPost("/api/admin/ticket/edit", body);
}

/* ── Timer (identical shell to retro; admin-only start/stop) ── */
async function startTimer(secs) { adminPost("/api/timer", { action: "start", duration: secs }); }
async function stopTimer() { adminPost("/api/timer", { action: "stop" }); }
function customTimer() { const m = parseInt(document.getElementById("custom-min").value || "0", 10); if (m > 0) startTimer(m * 60); }
function paintTimer() {
  const el = document.getElementById("timer-readout");
  const btn = document.getElementById("timer-btn");
  if (!TIMER.running || !TIMER.end_epoch) { el.textContent = ""; btn.classList.remove("running", "done"); return; }
  const rem = Math.max(0, Math.round(TIMER.end_epoch - (Date.now() / 1000 + OFFSET)));
  const m = Math.floor(rem / 60), s = rem % 60;
  el.textContent = (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  btn.classList.add("running");
  btn.classList.toggle("done", rem === 0);
  if (rem === 0 && firedFor !== TIMER.end_epoch) { firedFor = TIMER.end_epoch; celebrate(); }
}
setInterval(paintTimer, 250);

/* ── Host broadcasts (theme / music) + lock, applied by every browser ─ */
function applyBroadcast(state) {
  const b = state.broadcast || {};
  if (b.theme && b.theme !== lastBcastTheme) {
    lastBcastTheme = b.theme;
    if (b.theme !== THEME) { applyTheme(b.theme); markSwatch(); }
  }
  const m = b.music;
  if (m && m.seq && m.seq > lastMusicSeq) {
    lastMusicSeq = m.seq;
    Music.cast(m.channel, m.playing)
      .then(() => hideMusicBanner())
      .catch(() => { if (m.playing) showMusicBanner(); });
  }
  applyLock(!!state.locked);
}
function applyLock(locked) {
  LOCKED = locked;
  const banner = document.getElementById("lock-banner");
  if (banner) banner.classList.toggle("hidden", !locked);
  const lb = document.getElementById("lock-btn");
  if (lb) { lb.classList.toggle("open", locked); lb.title = locked ? "Unlock voting" : "Lock voting"; }
}
function showMusicBanner() { const b = document.getElementById("music-banner"); if (b) b.classList.remove("hidden"); }
function hideMusicBanner() { const b = document.getElementById("music-banner"); if (b) b.classList.add("hidden"); }

/* ── Render live state ──────────────────────────────────────── */
function render(state) {
  if (!state) return;
  LAST_STATE = state;
  applyBroadcast(state);
  if (state.timer) { TIMER = state.timer; OFFSET = state.timer.now_epoch - Date.now() / 1000; }
  renderTicket(state);
  renderRail(state);
  renderVoters(state);
  renderResults(state);
  renderDuel(state);
  renderAi(state);
  paintDeck(state);
  paintConsole(state);
  // The tracker-error notice is admin-facing (they own the write-backs).
  if (IS_ADMIN && state.notice && state.notice !== lastNotice) { lastNotice = state.notice; showToast(state.notice); }
  if (!state.notice) lastNotice = "";
  const pr = document.getElementById("presence");
  if (pr) {
    const others = (state.presence || []).filter(p => p.name !== NAME);
    const shown = others.slice(0, 5);
    pr.innerHTML = shown.map(p => '<span class="av-dot" title="' + esc(p.name) + '">' + (p.avatar || "🙂") + "</span>").join("") +
      (others.length > shown.length ? '<span class="more">+' + (others.length - shown.length) + "</span>" : "");
  }
  document.getElementById("roomcount").textContent = Math.max(1, (state.presence || []).length);
  renderRoom(state);
  const done = state.progress ? state.progress.estimated : 0;
  const total = state.progress ? state.progress.total : 0;
  document.getElementById("count").textContent = "· " + done + "/" + total + " estimated";
}
function renderRoom(state) {
  const list = document.getElementById("room-list");
  if (!list) return;
  const people = state.presence || [];
  list.innerHTML = people.length
    ? people.map(p => {
        const you = p.name === NAME;
        return '<div class="r"><span>' + (p.avatar || "🙂") + '</span><span class="nm">' + esc(p.name) + "</span>" +
          (you ? '<span class="tag you">you</span>' : "") + "</div>";
      }).join("")
    : '<div class="empty">Just you so far — share the code to invite the team.</div>';
}

/* ── Poll loop ──────────────────────────────────────────────── */
async function tick() {
  if (!TOKEN) return;
  try {
    let state;
    if (JOINED) {
      const r = await postJSON("/api/presence", { name: NAME, avatar: AVATAR });
      if (r.ok) state = await r.json();
    } else {
      const r = await fetch(api("/api/state") + "&pid=" + encodeURIComponent(PID));
      if (r.ok) state = await r.json();
    }
    if (state) render(state);
  } catch (e) {}
}
function startLoop() { if (LOOPING) return; LOOPING = true; tick(); setInterval(tick, 1200); }

/* ── Invite QR popover ──────────────────────────────────────── */
function toggleInvite() {
  const m = document.getElementById("invite-modal");
  if (m.classList.contains("hidden")) {
    document.getElementById("invite-img").src = api("/api/qr");
    m.classList.remove("hidden");
  } else { m.classList.add("hidden"); }
}

/* ── Toolbar popovers (one open at a time; the duel picker is an inline
   console slot now, not a popover) ──────────────────────────── */
const POPS = { "music-pop": "music-btn", "timer-pop": "timer-btn", "theme-pop": "theme-btn", "room-pop": "room-btn" };
function closePops() {
  Object.keys(POPS).forEach(id => {
    document.getElementById(id).classList.add("hidden");
    document.getElementById(POPS[id]).classList.remove("open");
  });
}
function togglePop(popId) {
  const open = !document.getElementById(popId).classList.contains("hidden");
  closePops();
  if (!open) {
    document.getElementById(popId).classList.remove("hidden");
    document.getElementById(POPS[popId]).classList.add("open");
  }
}
document.addEventListener("click", e => {
  if (e.target.closest(".pop") || e.target.closest(".tbtn") || e.target.closest(".room-btn")) return;
  closePops();
});
function closeMobileRail() {
  document.getElementById("layout").classList.remove("rail-open-m");
  document.getElementById("rail-backdrop").classList.add("hidden");
}
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (PEEK != null) { PEEK = null; if (LAST_STATE) render(LAST_STATE); }
  document.getElementById("duel-pop").classList.add("hidden");
  document.getElementById("console").classList.remove("expanded");
  closeMobileRail();
  closePops();
});

/* ── Theme swatches (identical to retro) ────────────────────── */
const THEMES = __THEMES__;
function buildSwatches() {
  const wrap = document.getElementById("swatches");
  wrap.innerHTML = THEMES.map(t =>
    '<button class="swatch" data-set-theme="' + t + '" title="' + t + '"></button>'
  ).join("");
  wrap.querySelectorAll(".swatch").forEach(b => {
    const t = b.getAttribute("data-set-theme");
    const probe = document.createElement("div"); probe.setAttribute("data-theme", t);
    probe.style.display = "none"; document.body.appendChild(probe);
    const cs = getComputedStyle(probe);
    b.style.background = cs.getPropertyValue("--bg") || "#0d1117";
    b.insertAdjacentHTML("beforeend", '<span class="dot" style="background:' + (cs.getPropertyValue("--accent") || "#5ac88a") + '"></span>');
    document.body.removeChild(probe);
    b.addEventListener("click", () => { applyTheme(t); markSwatch(); closePops(); });
  });
  markSwatch();
}
function markSwatch() { document.querySelectorAll(".swatch").forEach(s => s.classList.toggle("sel", s.getAttribute("data-set-theme") === THEME)); }
function buildChannels() {
  const sel = document.getElementById("music-mood");
  sel.innerHTML = Music.channels().map((c, i) => '<option value="' + i + '">' + esc(c.name) + "</option>").join("");
}

/* ── Internet-radio music + visualizer (identical to retro) ─── */
const CHANNELS = __MUSIC_CHANNELS__;
const Music = (function () {
  const audio = new Audio(); audio.preload = "none"; audio.crossOrigin = "anonymous";
  let channel = 0, volume = 0.35, playing = false, vizRAF = null;
  audio.volume = volume;
  audio.addEventListener("playing", () => { playing = true; paintBtn(); });
  audio.addEventListener("pause", () => { playing = false; paintBtn(); });
  audio.addEventListener("error", () => { playing = false; paintBtn(); });
  function paintBtn() {
    document.getElementById("music-play").textContent = playing ? "⏸" : "▶";
    document.getElementById("music-btn").classList.toggle("playing", playing);
    document.getElementById("viz").classList.toggle("on", playing);
    if (playing) drawViz();
  }
  function load(i) { channel = ((i % CHANNELS.length) + CHANNELS.length) % CHANNELS.length; audio.src = CHANNELS[channel].url; }
  function play() { if (!audio.src) load(channel); audio.play().catch(() => {}); }
  function stop() { audio.pause(); }
  function drawViz() {
    const cv = document.getElementById("viz"); if (!cv) return;
    const cx = cv.getContext("2d"), bars = 16, w = cv.width / bars;
    let phase = 0;
    if (vizRAF) cancelAnimationFrame(vizRAF);
    function frame() {
      if (!playing) { cx.clearRect(0, 0, cv.width, cv.height); vizRAF = null; return; }
      cx.clearRect(0, 0, cv.width, cv.height);
      const col = getComputedStyle(document.documentElement).getPropertyValue("--accent") || "#5ac88a";
      cx.fillStyle = col.trim() || "#5ac88a";
      phase += 0.18;
      for (let i = 0; i < bars; i++) {
        const v = (Math.sin(phase + i * 0.7) + Math.sin(phase * 1.7 + i) + 2) / 4;
        const h = Math.max(2, v * cv.height);
        cx.fillRect(i * w, cv.height - h, w - 1, h);
      }
      vizRAF = requestAnimationFrame(frame);
    }
    vizRAF = requestAnimationFrame(frame);
  }
  return {
    toggle() { playing ? stop() : play(); },
    setVolume(v) { volume = v; audio.volume = v; },
    setChannel(i) { const wasPlaying = playing || !audio.paused; load(i); if (wasPlaying) play(); },
    playing() { return playing; },
    channels() { return CHANNELS; },
    channelIndex() { return channel; },
    cast(i, on) { if (!on) { stop(); return Promise.resolve(); } load(i); return audio.play(); },
    playNow() { if (!audio.src) load(channel); return audio.play(); },
  };
})();

/* ── Timer finish: confetti + alarm (identical to retro) ────── */
function celebrate() { confetti(); alarm(); }
function confetti() {
  const cv = document.getElementById("confetti"); cv.width = innerWidth; cv.height = innerHeight;
  const cx = cv.getContext("2d"); const N = 140, cols = ["#5ac88a", "#a371f7", "#ff5edb", "#4cc38a", "#ffcf5e", "#ff5e5e"];
  const parts = []; for (let i = 0; i < N; i++) parts.push({ x: innerWidth / 2, y: innerHeight / 3, vx: (Math.random() - 0.5) * 14, vy: Math.random() * -12 - 4, c: cols[i % cols.length], r: 3 + Math.random() * 4, a: 1 });
  let frames = 0;
  (function step() {
    cx.clearRect(0, 0, cv.width, cv.height); frames++;
    parts.forEach(p => { p.vy += 0.35; p.x += p.vx; p.y += p.vy; p.a -= 0.008; cx.globalAlpha = Math.max(0, p.a); cx.fillStyle = p.c; cx.fillRect(p.x, p.y, p.r, p.r * 1.6); });
    cx.globalAlpha = 1;
    if (frames < 160) requestAnimationFrame(step); else cx.clearRect(0, 0, cv.width, cv.height);
  })();
}
function alarm() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext; const ctx = new AC();
    if (ctx.state === "suspended") ctx.resume();
    const t0 = ctx.currentTime;
    for (let k = 0; k < 4; k++) {
      [880, 1175].forEach(f => { const o = ctx.createOscillator(), g = ctx.createGain(); o.type = "square"; o.frequency.value = f;
        const t = t0 + k * 0.4; g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
        o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + 0.32); });
    }
    setTimeout(() => { try { ctx.close(); } catch (e) {} }, 2000);
  } catch (e) {}
}

/* ── Host (admin) controls: broadcast to every browser ──────── */
async function toggleLock() { adminPost("/api/admin/lock", { locked: !LOCKED }); }
async function castTheme() { adminPost("/api/admin/broadcast", { theme: THEME }); }
async function castMusic() { adminPost("/api/admin/broadcast", { music: { playing: Music.playing(), channel: Music.channelIndex() } }); }

/* ── Wire up + start ────────────────────────────────────────── */
applyTheme(THEME);
buildDeck();
buildAvatars();
buildSwatches();
buildChannels();
paintTimer();
// The console column only exists for the host — body.is-admin drives the grid.
document.body.classList.toggle("is-admin", IS_ADMIN);
document.querySelectorAll(".admin-only").forEach(el => el.classList.toggle("hidden", !IS_ADMIN));
document.querySelectorAll(".guest-only").forEach(el => el.classList.toggle("hidden", IS_ADMIN));
document.getElementById("music-banner").addEventListener("click", () => { Music.playNow().then(hideMusicBanner).catch(() => {}); });
document.getElementById("lock-btn").addEventListener("click", toggleLock);
document.getElementById("theme-cast").addEventListener("click", castTheme);
document.getElementById("music-cast").addEventListener("click", castMusic);
document.getElementById("dice").addEventListener("click", () => { document.getElementById("name-in").value = randomName(); });
document.getElementById("name-in").addEventListener("keydown", e => { if (e.key === "Enter") saveProfile(); });
document.getElementById("save-profile").addEventListener("click", saveProfile);
document.getElementById("me").addEventListener("click", openProfile);
document.getElementById("code-in").addEventListener("keydown", e => { if (e.key === "Enter") submitCode(); });
document.getElementById("code-join").addEventListener("click", submitCode);
document.getElementById("music-btn").addEventListener("click", () => togglePop("music-pop"));
document.getElementById("timer-btn").addEventListener("click", () => togglePop("timer-pop"));
document.getElementById("theme-btn").addEventListener("click", () => togglePop("theme-pop"));
document.getElementById("room-btn").addEventListener("click", () => togglePop("room-pop"));
document.getElementById("music-play").addEventListener("click", () => Music.toggle());
document.getElementById("music-vol").addEventListener("input", e => Music.setVolume(parseFloat(e.target.value)));
document.getElementById("music-mood").addEventListener("change", e => Music.setChannel(parseInt(e.target.value, 10)));
document.querySelectorAll(".preset").forEach(b => b.addEventListener("click", () => { startTimer(parseInt(b.getAttribute("data-secs"), 10)); closePops(); }));
document.getElementById("custom-go").addEventListener("click", () => { customTimer(); closePops(); });
document.getElementById("timer-stop").addEventListener("click", () => { stopTimer(); closePops(); });
document.getElementById("invite-btn").addEventListener("click", toggleInvite);
document.getElementById("invite-close").addEventListener("click", toggleInvite);
const MOBILE = window.matchMedia("(max-width:699px)");
document.getElementById("rail-toggle").addEventListener("click", () => {
  if (MOBILE.matches) {
    // Small screens: the rail is an overlay drawer, not a grid column.
    const open = document.getElementById("layout").classList.toggle("rail-open-m");
    document.getElementById("rail-backdrop").classList.toggle("hidden", !open);
  } else {
    RAIL_OPEN = !RAIL_OPEN;
    document.getElementById("layout").classList.toggle("rail-closed", !RAIL_OPEN);
  }
});
document.getElementById("rail-backdrop").addEventListener("click", closeMobileRail);
if (IS_ADMIN) {
  document.getElementById("reveal-btn").addEventListener("click", () => adminPost("/api/admin/reveal", {}));
  document.getElementById("revote-btn").addEventListener("click", () => adminPost("/api/admin/revote", {}));
  document.getElementById("ai-btn").addEventListener("click", () => adminPost("/api/admin/ai", {}));
  document.getElementById("finalize-btn").addEventListener("click", finalize);
  document.getElementById("final-pts").addEventListener("keydown", e => { if (e.key === "Enter") finalize(); });
  document.getElementById("final-pts").addEventListener("input", () => {
    FIN_SRC = "edited";
    const src = document.getElementById("final-src");
    src.textContent = FIN_SRC; src.classList.remove("hidden");
  });
  document.getElementById("edit-btn").addEventListener("click", openEdit);
  document.getElementById("edit-save").addEventListener("click", saveEdit);
  document.getElementById("edit-cancel").addEventListener("click", () => document.getElementById("edit-modal").classList.add("hidden"));
  document.getElementById("prev-btn").addEventListener("click", () => LAST_STATE && adminPost("/api/admin/goto", { index: LAST_STATE.ticket_index - 1 }));
  document.getElementById("next-btn").addEventListener("click", () => LAST_STATE && adminPost("/api/admin/goto", { index: LAST_STATE.ticket_index + 1 }));
  // The duel picker is an inline console slot — toggled directly, no popover.
  document.getElementById("duel-btn").addEventListener("click", () =>
    document.getElementById("duel-pop").classList.toggle("hidden"));
  document.querySelectorAll(".dpreset").forEach(b => b.addEventListener("click", () => {
    adminPost("/api/admin/duel/open", { seconds: parseInt(b.getAttribute("data-dsecs"), 10) });
    document.getElementById("duel-pop").classList.add("hidden");
  }));
  document.getElementById("duel-next-btn").addEventListener("click", () => adminPost("/api/admin/duel/next", {}));
  document.getElementById("duel-close-btn").addEventListener("click", () => adminPost("/api/admin/duel/close", {}));
  // Mobile console sheet: expander + the one contextual primary action.
  document.getElementById("console-toggle").addEventListener("click", () =>
    document.getElementById("console").classList.toggle("expanded"));
  document.getElementById("console-primary").addEventListener("click", () => {
    if (!LAST_STATE) return;
    if (LAST_STATE.phase === "duel") adminPost("/api/admin/duel/close", {});
    else if (LAST_STATE.phase === "revealed") finalize();
    else adminPost("/api/admin/reveal", {});
  });
}

function afterToken() {
  // Token known: keep it in sessionStorage (per-tab, survives refresh) and strip
  // it (and the admin secret) from the address bar, so copying the URL never
  // leaks access — or admin — to others.
  if (ADMIN) sessionStorage.setItem("poker_admin", ADMIN);
  if (TOKEN) { sessionStorage.setItem("poker_token", TOKEN); history.replaceState(null, "", "/"); }
  paintMe();
  if (NAME && localStorage.getItem("poker_avatar")) { JOINED = true; document.getElementById("modal").classList.add("hidden"); startLoop(); }
  else { openProfile(); }
}
if (TOKEN) { document.getElementById("code-modal").classList.add("hidden"); afterToken(); }
else { document.getElementById("code-modal").classList.remove("hidden"); document.getElementById("code-in").focus(); }
"""


def build_poker_html() -> str:
    """Return the complete self-contained poker page (token-free)."""

    def _lit(v: object) -> str:
        # ensure_ascii=False keeps emojis literal (page is UTF-8); still escapes quotes.
        return json.dumps(v, ensure_ascii=False)

    js = (
        _JS.replace("__DECK__", _lit(list(POKER_DECK)))
        .replace("__AVATARS__", _lit(list(AVATARS)))
        .replace("__THEMES__", _lit(list(RETRO_THEMES)))
        .replace("__ADJS__", _lit(_ADJECTIVES))
        .replace("__NOUNS__", _lit(_NOUNS))
        # Same internet-radio library the TUI + retro use (yeaboi.music.CHANNELS).
        .replace("__MUSIC_CHANNELS__", _lit([{"name": c["name"], "url": c["url"]} for c in CHANNELS]))
    )
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Planning Poker</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        "<header>\n"
        '  <div class="brand"><span class="title">Planning Poker</span>'
        '<span class="count" id="count"></span></div>\n'
        '  <div class="presence"><button class="me-chip" id="me"></button>'
        '<div class="avatars" id="presence"></div>'
        '<button class="room-btn" id="room-btn" title="Who\'s in the room">'
        '👥 <span id="roomcount">1</span></button></div>\n'
        '  <span class="spacer"></span>\n'
        '  <div class="toolbar">\n'
        '    <canvas id="viz" width="34" height="22" title="Now playing"></canvas>\n'
        '    <button class="tbtn" id="rail-toggle" title="Show/hide the ticket list">☰</button>\n'
        '    <button class="tbtn admin-only hidden" id="lock-btn" title="Lock voting"><span class="ico">🔒</span></button>\n'
        '    <button class="tbtn" id="music-btn" title="Music"><span class="ico">♪</span></button>\n'
        '    <button class="tbtn" id="timer-btn" title="Timer"><span class="ico">⏱</span>'
        '<span class="rd" id="timer-readout"></span></button>\n'
        '    <button class="tbtn" id="theme-btn" title="Theme"><span class="ico">◑</span></button>\n'
        '    <button class="tbtn primary" id="invite-btn">Invite</button>\n'
        "  </div>\n"
        "</header>\n"
        # Music popover
        '<div id="music-pop" class="pop hidden"><div class="row">\n'
        '  <button class="playbtn" id="music-play">▶</button>\n'
        '  <input type="range" id="music-vol" min="0" max="1" step="0.05" value="0.35" title="Volume">\n'
        '  <select id="music-mood" title="Station"></select>\n'
        "</div>\n"
        '  <div class="row admin-only hidden" style="margin-top:10px">'
        '<button class="tbtn" id="music-cast" title="Play this for the whole team">📣 Play for everyone</button></div>\n'
        "</div>\n"
        # Timer popover — starting/stopping is admin-only; everyone sees the readout.
        '<div id="timer-pop" class="pop hidden">\n'
        "  <label>Countdown</label>\n"
        '  <div class="admin-only hidden">\n'
        '    <div class="row"><div class="seg">'
        '<button class="preset" data-secs="60">1m</button>'
        '<button class="preset" data-secs="120">2m</button>'
        '<button class="preset" data-secs="180">3m</button>'
        '<button class="preset" data-secs="300">5m</button></div></div>\n'
        '    <div class="row" style="margin-top:10px">'
        '<input type="number" id="custom-min" min="1" max="60" placeholder="min">'
        '<button class="tbtn" id="custom-go">Start</button>'
        '<button class="tbtn" id="timer-stop">Stop</button></div>\n'
        "  </div>\n"
        '  <p class="muted guest-only hidden" style="margin:6px 0 0">The host controls the timer.</p>\n'
        "</div>\n"
        # Theme popover
        '<div id="theme-pop" class="pop hidden">\n'
        "  <label>Theme</label>\n"
        '  <div class="swatches" id="swatches"></div>\n'
        '  <div class="row admin-only hidden" style="margin-top:10px">'
        '<button class="tbtn" id="theme-cast" title="Apply this theme for the whole team">📣 Apply to everyone</button></div>\n'
        "</div>\n"
        # Room roster popover
        '<div id="room-pop" class="pop left hidden">\n'
        "  <label>In the room</label>\n"
        '  <div class="roster" id="room-list"></div>\n'
        "</div>\n"
        # Main layout: rail | main (ticket / table / results) | host console.
        '<div class="layout" id="layout">\n'
        '  <aside id="rail"><h2>Tickets</h2><div id="rail-list"></div></aside>\n'
        '  <div class="main">\n'
        '    <div class="ticket" id="ticket"></div>\n'
        # Results sit between the ticket and the table: hidden while voting
        # (the table stays right under the ticket), and on reveal — when the
        # deck is closed anyway — the distribution/AI/duel become the page's
        # focus instead of a below-the-fold footnote.
        '    <section class="results hidden" id="results">\n'
        '      <div><div class="eyebrow">Results</div>'
        '<div class="results-sum hidden" id="results-sum"></div></div>\n'
        '      <div class="dist hidden" id="dist"></div>\n'
        '      <div class="duel hidden" id="duel"></div>\n'
        '      <div class="ainote hidden" id="ainote"></div>\n'
        "    </section>\n"
        # The table: seated voters + your hand, one felt surface.
        '    <section class="table">\n'
        '      <div class="eyebrow">The table</div>\n'
        '      <div class="vrow" id="vrow"></div>\n'
        '      <div class="deck-zone" id="deck-zone">\n'
        '        <div class="deck-status" id="deck-status"></div>\n'
        '        <div class="deck" id="deck"></div>\n'
        "      </div>\n"
        "    </section>\n"
        "  </div>\n"
        # Host console — rendered for everyone but hidden unless the URL carried
        # the admin secret; every action is re-verified server-side regardless.
        # Sticky right column on desktop, bottom sheet under 1100px.
        '  <aside class="console admin-only hidden" id="console">\n'
        '    <div class="cbar" id="console-bar">\n'
        '      <span class="cphase" id="console-phase">voting</span>\n'
        '      <button class="cbtn primary" id="console-primary">Reveal votes</button>\n'
        '      <button class="cbtn" id="console-toggle" title="More controls">⌃</button>\n'
        "    </div>\n"
        '    <div class="cbody" id="console-body">\n'
        '      <div class="cgroup"><div class="eyebrow">Round</div>\n'
        '        <button class="cbtn primary" id="reveal-btn">Reveal votes</button>\n'
        '        <button class="cbtn" id="revote-btn">Re-vote</button>\n'
        "      </div>\n"
        '      <div class="cgroup"><div class="eyebrow">Insight</div>\n'
        '        <button class="cbtn" id="ai-btn">🤖 AI perspective</button>\n'
        '        <button class="cbtn" id="duel-btn" title="Low vs high voter argue their estimates">🎤 Open the floor</button>\n'
        # The duel slot: turn-length picker and live controls swap inside one
        # reserved area — the console never jumps mid-round.
        '        <div class="duel-slot">\n'
        '          <div id="duel-pop" class="duel-inline hidden">\n'
        "            <label>Turn length</label>\n"
        '            <div class="seg">'
        '<button class="dpreset" data-dsecs="60">60s</button>'
        '<button class="dpreset" data-dsecs="90">90s</button>'
        '<button class="dpreset" data-dsecs="120">2m</button></div>\n'
        '            <p class="muted dhint">Lowest voter argues first, then the highest. '
        "The debate is recorded &amp; transcribed for the AI's verdict.</p>\n"
        "          </div>\n"
        '          <div class="crow hidden" id="duel-live-controls">'
        '<button class="cbtn" id="duel-next-btn" title="Hand the floor to the high voter">Next turn ›</button>'
        '<button class="cbtn" id="duel-close-btn" title="End the debate and transcribe">Close the floor</button>'
        "</div>\n"
        "        </div>\n"
        "      </div>\n"
        '      <div class="cgroup"><div class="eyebrow">Decision</div>\n'
        '        <div class="readout">Team median <b id="console-median">—</b></div>\n'
        '        <div class="readout">AI suggests <b id="console-sug">—</b></div>\n'
        '        <div class="finrow"><label class="finlab" for="final-pts">Final points</label>'
        '<span id="final-src" class="hidden"></span>'
        '<input type="number" id="final-pts" min="0" step="1" placeholder="—" title="Agreed story points">'
        "</div>\n"
        '        <button class="cbtn primary" id="finalize-btn" title="Save to the board and move on">Save &amp; next</button>\n'
        "      </div>\n"
        '      <div class="cgroup"><div class="eyebrow">Navigate</div>\n'
        '        <div class="navrow">'
        '<button class="cbtn nav" id="prev-btn" title="Previous ticket">‹</button>'
        '<span id="console-pos">– / –</span>'
        '<button class="cbtn nav" id="next-btn" title="Next ticket">›</button>'
        '<button class="cbtn" id="edit-btn" title="Edit the ticket on the board">✎</button>'
        "</div>\n"
        '        <div id="console-notice" class="hidden"></div>\n'
        "      </div>\n"
        "    </div>\n"
        "  </aside>\n"
        "</div>\n"
        '<div id="rail-backdrop" class="hidden"></div>\n'
        '<div id="toast" class="toast hidden"></div>\n'
        '<canvas id="confetti"></canvas>\n'
        # Host-broadcast banners
        '<div id="music-banner" class="banner hidden">▶ The host started music — tap to listen</div>\n'
        '<div id="lock-banner" class="banner lock hidden">🔒 The host locked voting</div>\n'
        # Code-entry gate (shown when the URL has no token)
        '<div id="code-modal" class="overlay hidden"><div class="box">\n'
        "  <h2>Join planning poker</h2>\n"
        '  <p class="muted">Enter the share code shown on the host\'s screen.</p>\n'
        '  <div class="namerow"><input id="code-in" class="field" placeholder="e.g. A3F9-1B2C" autofocus>'
        '<button class="join-btn" id="code-join">Join</button></div>\n'
        '  <p class="muted" id="code-err" style="color:#f85149"></p>\n'
        "</div></div>\n"
        # Profile modal (name + avatar; reused for rename)
        '<div id="modal" class="overlay hidden"><div class="box">\n'
        "  <h2>Your name &amp; avatar</h2>\n"
        '  <div class="namerow"><input id="name-in" class="field" placeholder="Your name (or roll the dice →)">'
        '<button class="dice" id="dice" title="Random name">🎲</button></div>\n'
        '  <div class="avatars-pick" id="avatars"></div>\n'
        '  <button class="primary" id="save-profile">Save</button>\n'
        "</div></div>\n"
        # Edit-ticket modal (admin) — saves push to the real tracker.
        '<div id="edit-modal" class="overlay hidden"><div class="box">\n'
        "  <h2>Edit ticket</h2>\n"
        "  <label>Summary</label>\n"
        '  <input id="edit-summary" class="field">\n'
        "  <label>Description</label>\n"
        '  <textarea id="edit-desc" class="field" rows="6"></textarea>\n'
        "  <label>Story points</label>\n"
        '  <input id="edit-pts" class="field" type="number" min="0" step="1" style="max-width:120px">\n'
        '  <p class="warn hidden" id="edit-warn">⚠ Saving replaces this ticket\'s rich formatting on the board with plain text.</p>\n'
        '  <p class="muted" style="font-size:12.5px;margin:8px 0 0">Saving updates the ticket on the real board immediately.</p>\n'
        '  <button class="primary" id="edit-save">Save to board</button>\n'
        '  <button class="tbtn" id="edit-cancel" style="width:100%;justify-content:center;margin-top:8px">Cancel</button>\n'
        "</div></div>\n"
        # Invite popover (QR)
        '<div id="invite-modal" class="overlay hidden"><div class="box">\n'
        "  <h2>Invite the team</h2>\n"
        '  <p class="muted">Scan to open the session, then enter the Share code from the host.</p>\n'
        '  <div class="qrwrap"><img id="invite-img" alt="join QR"></div>\n'
        '  <button class="primary" id="invite-close">Close</button>\n'
        "</div></div>\n"
        f"<script>{js}</script>\n</body>\n</html>"
    )
    logger.debug("poker: page built (%d bytes)", len(html.encode("utf-8")))
    return html
