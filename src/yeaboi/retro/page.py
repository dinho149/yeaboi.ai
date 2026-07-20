"""The self-contained browser board page served to teammates.

``build_board_html()`` returns ONE HTML string with inline CSS + JS and no
external requests (no CDN, no third-party iframe) so it works on any LAN device —
and over the Cloudflare tunnel — without the app installed, fully offline.

The page is **token-free**: ``GET /`` is unauthenticated, so baking the access
token into the HTML would leak it to any LAN peer. Instead the client reads the
token from its own URL (``?token=``) or obtains it by typing the short **join
code** into the code-entry gate (``POST /api/join``).

Features: four grids; **drag** cards to reorder / move between grids;
**edit/delete** your own cards (author-only, enforced server-side); emoji
**reactions**; a **who's-here** presence row + per-grid **typing indicators**; a
shared **countdown timer** with a **confetti + alarm** finish; a join modal with
an **avatar picker** + 🎲 **random names** (editable later); a **theme switcher**;
**Web-Audio** background music (ambient/lofi/focus/hip-hop/jazz) with a live
**visualizer**; and an **invite** popover with a scannable **QR**.

The big CSS/JS blocks are plain (non-f-string) module constants with
``__PLACEHOLDER__`` markers filled by :func:`build_board_html` via ``str.replace``
— this avoids f-string ``{{ }}`` brace-escaping. Untrusted strings (card text,
names) are rendered via ``textContent`` (``esc()``), never raw ``innerHTML``.

# See docs: "Guardrails" — output validation / escaping
"""

from __future__ import annotations

import json
import logging

from yeaboi.music import CHANNELS
from yeaboi.retro.board import (
    AVATARS,
    CARRIED_STATUS_LABELS,
    CARRIED_STATUSES,
    REACTION_EMOJIS,
    RETRO_GRID_LABELS,
    RETRO_GRIDS,
    RETRO_THEMES,
)

logger = logging.getLogger(__name__)

# Grid (key, label) pairs for the client, kept in server order.
_GRID_JS = [[k, RETRO_GRID_LABELS[k]] for k in RETRO_GRIDS]
# Carried-item status (value, label) pairs for the review column's <select>.
_CARRIED_STATUS_JS = [[k, CARRIED_STATUS_LABELS[k]] for k in CARRIED_STATUSES]

# Fun random-name word lists (cosmetic, client-only). Kept tasteful-but-silly.
_ADJECTIVES = [
    "Sexy",
    "Ghost",
    "Cosmic",
    "Sneaky",
    "Turbo",
    "Feral",
    "Velvet",
    "Rogue",
    "Disco",
    "Thunder",
    "Silent",
    "Funky",
    "Midnight",
    "Wild",
    "Neon",
    "Grumpy",
    "Cyber",
    "Lonesome",
    "Radical",
    "Mystic",
    "Spicy",
    "Chrome",
    "Groovy",
    "Danger",
]
_NOUNS = [
    "Cowboy",
    "Dude",
    "Llama",
    "Wizard",
    "Raccoon",
    "Pirate",
    "Ninja",
    "Yeti",
    "Goblin",
    "Falcon",
    "Panda",
    "Viking",
    "Phantom",
    "Otter",
    "Bandit",
    "Comet",
    "Walrus",
    "Samurai",
    "Gecko",
    "Nomad",
    "Badger",
    "Wombat",
    "Sphinx",
    "Hologram",
]


_CSS = """
:root, [data-theme="midnight"] {
  --bg:#0d1117; --panel:#161b22; --line:#30363d; --text:#c9d1d9;
  --muted:#8b949e; --accent:#50bebe; --accent2:#a371f7; --card:#0d1117; --ink:#04211f;
}
[data-theme="light"] {
  --bg:#f6f8fa; --panel:#ffffff; --line:#d0d7de; --text:#1f2328;
  --muted:#656d76; --accent:#0969da; --accent2:#8250df; --card:#f6f8fa; --ink:#ffffff;
}
[data-theme="solarized"] {
  --bg:#002b36; --panel:#073642; --line:#0a4b59; --text:#eee8d5;
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
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
/* ── Header / toolbar ─────────────────────────────────────────── */
header { padding:11px 18px; border-bottom:1px solid var(--line);
         display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.brand { display:flex; align-items:baseline; gap:8px; }
.brand .title { color:var(--accent); font-size:17px; font-weight:700; letter-spacing:.01em; }
.brand .count { color:var(--muted); font-size:12.5px; }
header .spacer { flex:1; }

/* Presence: a distinct "you" chip + an overlapping stack of teammates */
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
.roster .r .tag { font-size:11px; }
.roster .r .tag.you { color:var(--accent); }
.roster .r .tag.typing { color:var(--accent2); font-style:italic; }
.roster .empty { color:var(--muted); font-size:13px; }

/* Compact icon toolbar */
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
#timer-btn .rd:empty { display:none; }  /* collapse the readout when no timer runs */
#timer-btn.running { border-color:var(--accent); }
#timer-btn.done .rd { color:#f85149; animation:blink 1s steps(2) infinite; }
@keyframes blink { 50% { opacity:.3; } }

/* Popovers — one control panel per toolbar button */
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

/* ── Carried-over review column ("Last sprint's actions") ───────── */
.carried { margin:18px 18px 0; background:var(--panel); border:1px solid var(--line);
           border-radius:10px; padding:12px 14px; }
.carried h2 { font-size:14px; margin:0; color:var(--accent2); letter-spacing:.02em; }
.carried .sub { color:var(--muted); font-size:12px; margin:3px 0 11px; }
.carried .items { display:flex; flex-direction:column; gap:8px; }
.carried .ci { display:flex; align-items:flex-start; gap:10px; }
.carried .ci .txt { flex:1; white-space:pre-wrap; word-break:break-word; padding-top:4px; }
.carried .ci select { background:var(--card); color:var(--text); border:1px solid var(--line);
        border-radius:8px; padding:5px 7px; font:inherit; font-size:13px; cursor:pointer; }
.carried .ci select:hover { border-color:var(--accent); }
.carried .ci[data-status="done"] .txt,
.carried .ci[data-status="not_relevant"] .txt { color:var(--muted); text-decoration:line-through; }
.carried .ci[data-status="carried_over"] select { border-color:var(--accent2); }

.grids { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; padding:18px; }
@media (max-width:900px) { .grids { grid-template-columns:1fr 1fr; } }
@media (max-width:560px) { .grids { grid-template-columns:1fr; } }
.col { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px;
       display:flex; flex-direction:column; }
.col h2 { font-size:14px; margin:0 0 10px; color:var(--accent); letter-spacing:.02em; }
.cards { flex:1; min-height:44px; }
.cards.drop-target { outline:2px dashed var(--accent); outline-offset:3px; border-radius:8px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:9px 10px; margin:0 0 8px; cursor:grab; }
.card.dragging { opacity:.4; }
.card .body { white-space:pre-wrap; word-break:break-word; }
.card.ai { border-color:var(--accent); background:rgba(80,190,190,.06); }
.card .who { color:var(--muted); font-size:12px; margin-top:5px; display:flex; align-items:center; gap:8px; }
.card .who .spacer { flex:1; }
.act { background:transparent; border:0; color:var(--muted); cursor:pointer; font-size:13px; padding:0 2px; }
.act:hover { color:var(--accent); }
.act.danger:hover { color:#f85149; }
.editbox { width:100%; background:var(--card); color:var(--text); border:1px solid var(--accent);
           border-radius:6px; padding:6px; font:inherit; resize:vertical; }
.edit-actions { display:flex; gap:8px; margin-top:8px; }
.btn-save { background:var(--accent); color:var(--ink); border:0; border-radius:7px;
            padding:6px 18px; font:inherit; font-weight:600; cursor:pointer; }
.btn-save:hover { filter:brightness(1.08); }
.btn-cancel { background:transparent; color:var(--text); border:1px solid var(--line);
              border-radius:7px; padding:6px 14px; font:inherit; cursor:pointer; }
.btn-cancel:hover { border-color:var(--accent); }
.rx { display:flex; flex-wrap:wrap; gap:4px; margin-top:7px; }
.chip { background:transparent; border:1px solid var(--line); border-radius:999px;
        padding:1px 7px; cursor:pointer; font:inherit; font-size:13px; line-height:1.5; color:var(--text); }
.chip:hover { border-color:var(--accent); }
.chip.mine { background:rgba(80,190,190,.18); border-color:var(--accent); }
.chip span { color:var(--muted); font-size:12px; margin-left:3px; }
.react-btn { background:transparent; border:1px solid var(--line); border-radius:999px;
             padding:1px 8px; cursor:pointer; font:inherit; font-size:13px; line-height:1.5; color:var(--muted); }
.react-btn:hover { border-color:var(--accent); color:var(--text); }
.react-btn .plus { font-size:11px; margin-left:1px; }
.rx-picker { position:fixed; z-index:31; background:var(--panel); border:1px solid var(--line);
             border-radius:12px; padding:6px; display:flex; flex-wrap:wrap; gap:4px; max-width:220px;
             box-shadow:0 8px 24px rgba(0,0,0,.4); }
.rx-picker .pick { background:transparent; border:1px solid transparent; border-radius:8px;
                   padding:3px 6px; cursor:pointer; font-size:20px; line-height:1; }
.rx-picker .pick:hover { border-color:var(--accent); background:rgba(80,190,190,.14); }
.rx-picker .pick.mine { border-color:var(--accent); background:rgba(80,190,190,.22); }
.typing { color:var(--accent2); font-size:12px; min-height:16px; margin-top:6px; font-style:italic; }
.add { margin-top:6px; }
textarea { width:100%; background:var(--card); color:var(--text); border:1px solid var(--line);
           border-radius:8px; padding:8px; resize:vertical; font:inherit; }
.addbtn { margin-top:6px; background:var(--accent); color:var(--ink); border:0; border-radius:8px;
          padding:7px 14px; font:inherit; font-weight:600; cursor:pointer; }
.addbtn:hover { filter:brightness(1.1); }
.addbtn:disabled, .add textarea:disabled { opacity:.5; cursor:not-allowed; }
.add { margin-bottom:10px; }  /* input now sits at the top of the column */

/* Small relative timestamp beside the author badge. */
.card .who .ago { color:var(--muted); font-size:11px; }

/* Group-by-author clusters within a column. */
.author-group { margin-bottom:10px; }
.ag-head { font-size:12px; color:var(--muted); margin:2px 0 5px; letter-spacing:.02em; }

/* Header view controls: focus-one-person picker + group-by toggle. */
.viewctl { display:flex; align-items:center; gap:8px; }
.viewctl select { background:var(--panel); color:var(--text); border:1px solid var(--line);
                  border-radius:9px; padding:6px 8px; font:inherit; font-size:13px; cursor:pointer; }
.viewctl select:hover { border-color:var(--accent); }

/* Host-broadcast banners (autoplay tap-to-listen + board lock). */
.banner { position:fixed; left:50%; bottom:18px; transform:translateX(-50%); z-index:28;
          background:var(--panel); color:var(--text); border:1px solid var(--accent);
          border-radius:999px; padding:9px 18px; font-size:13.5px; cursor:pointer;
          box-shadow:0 8px 24px rgba(0,0,0,.4); }
.banner.lock { border-color:#f85149; cursor:default; }

.overlay { position:fixed; inset:0; background:rgba(0,0,0,.75); display:flex;
           align-items:center; justify-content:center; padding:16px; z-index:20; }
.overlay .box { background:var(--panel); border:1px solid var(--line); border-radius:14px;
                padding:20px; width:100%; max-width:420px; }
.overlay h2 { margin:0 0 12px; color:var(--accent); }
.namerow { display:flex; gap:8px; }
.field { flex:1; background:var(--card); color:var(--text); border:1px solid var(--line);
         border-radius:8px; padding:9px; font:inherit; }
.dice { background:var(--panel); border:1px solid var(--line); border-radius:8px;
        padding:0 12px; cursor:pointer; font-size:18px; }
.join-btn { background:var(--accent); color:var(--ink); border:0; border-radius:8px;
            padding:0 20px; font:inherit; font-weight:700; cursor:pointer; white-space:nowrap; }
.join-btn:hover { filter:brightness(1.1); }
.avatars { display:flex; flex-wrap:wrap; gap:6px; margin:12px 0; }
.av { font-size:22px; background:var(--card); border:1px solid var(--line); border-radius:10px;
      width:40px; height:40px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
.av.sel { border-color:var(--accent); background:rgba(80,190,190,.18); }
.primary { width:100%; margin-top:6px; background:var(--accent); color:var(--ink); border:0;
           border-radius:8px; padding:10px; font:inherit; font-weight:700; cursor:pointer; }
.qrwrap { background:#fff; border-radius:10px; padding:10px; display:flex; justify-content:center; }
.qrwrap img { width:220px; height:220px; }
.hidden { display:none !important; }
#confetti { position:fixed; inset:0; pointer-events:none; z-index:30; }
#rx-fx { position:fixed; inset:0; pointer-events:none; z-index:29; overflow:hidden; }
.floater { position:absolute; bottom:8vh; opacity:0; will-change:transform,opacity;
           animation:rxfloat 2.8s ease-out forwards; text-shadow:0 2px 6px rgba(0,0,0,.35); }
@keyframes rxfloat {
  0% { transform:translateY(0) scale(.6); opacity:0; }
  15% { opacity:1; }
  100% { transform:translateY(-64vh) scale(1.15); opacity:0; }
}
"""


_JS = r"""
const GRIDS = __GRIDS__;
const CARRIED_STATUSES = __CARRIED_STATUSES__;
const EMOJIS = __EMOJIS__;
const AVATARS = __AVATARS__;
const ADJS = __ADJS__;
const NOUNS = __NOUNS__;

let TOKEN = new URLSearchParams(location.search).get("token") || sessionStorage.getItem("retro_token") || "";
// The admin secret only ever rides in the host's private link (server.py appends
// &admin=…). Whoever has it gets the host controls (music/theme/timer/lock).
let ADMIN = new URLSearchParams(location.search).get("admin") || sessionStorage.getItem("retro_admin") || "";
let IS_ADMIN = !!ADMIN;
let PID = localStorage.getItem("retro_pid");
if (!PID) { PID = (self.crypto && crypto.randomUUID) ? crypto.randomUUID() : "p" + Math.random().toString(36).slice(2); localStorage.setItem("retro_pid", PID); }
let NAME = localStorage.getItem("retro_name") || "";
let AVATAR = localStorage.getItem("retro_avatar") || AVATARS[0];
let THEME = localStorage.getItem("retro_theme") || "midnight";
let JOINED = false, LOOPING = false;
let TYPING_GRID = "", typingTimer = null;
let TIMER = { running: false }, OFFSET = 0, firedFor = null;
let EDITING = null;                          // card id being edited inline
let LAST_STATE = null;                        // most recent poll payload (for local re-renders)
const MY_REACTIONS = {};                     // card_id -> Set(emoji) I reacted with
let lastRxEvent = -1, seededRx = false;       // high-water mark of animated reaction events
let RX_CARD = null;                           // card whose react-picker is open
let DRAG_ID = null;
let FOCUS = "";                               // when set, show only this author's cards (their turn)
let GROUPED = localStorage.getItem("retro_grouped") === "1";  // cluster cards by author
let focusSig = null;                          // cached author set so we rebuild the picker only on change
let lastBcastTheme = null, lastMusicSeq = 0;  // host broadcasts applied once, on change
let LOCKED = false;                           // board frozen by the host

function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
function api(path) { return path + (path.indexOf("?") < 0 ? "?" : "&") + "token=" + encodeURIComponent(TOKEN); }
function postJSON(path, body) {
  // `admin` is sent on every POST but only checked by the server on /api/admin/* and
  // /api/timer — harmless (empty) for teammates who never received the secret.
  return fetch(api(path), { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({ pid: PID, admin: ADMIN }, body || {})) });
}
function randomName() { return ADJS[Math.floor(Math.random()*ADJS.length)] + " " + NOUNS[Math.floor(Math.random()*NOUNS.length)]; }
function applyTheme(t) { THEME = t; document.documentElement.setAttribute("data-theme", t); localStorage.setItem("retro_theme", t); }

/* ── Join code gate ─────────────────────────────────────────── */
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
    afterToken();   // persists the token + strips it from the URL
  } catch (e) { err.textContent = "Could not reach the retro."; }
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
  localStorage.setItem("retro_name", NAME);
  localStorage.setItem("retro_avatar", AVATAR);
  document.getElementById("modal").classList.add("hidden");
  JOINED = true;
  paintMe();
  startLoop();
}
function paintMe() { document.getElementById("me").innerHTML = (AVATAR || "🙂") + " " + esc(NAME) + ' <span class="pen">✎</span>'; }

/* ── Grids + cards ──────────────────────────────────────────── */
function buildCols() {
  const g = document.getElementById("grids");
  // Add form sits directly under the heading so you never scroll to the bottom of a
  // tall column to write a card. Cards then fill below it (newest appended at the end).
  g.innerHTML = GRIDS.map(([k, label]) =>
    '<div class="col"><h2>' + esc(label) + '</h2>' +
    '<div class="add"><textarea rows="2" id="in-' + k + '" placeholder="Add a card…"></textarea>' +
    '<button class="addbtn" data-grid="' + k + '">Add</button></div>' +
    '<div class="cards" id="cards-' + k + '" data-grid="' + k + '"></div>' +
    '<div class="typing" id="typing-' + k + '"></div></div>'
  ).join("");
  g.querySelectorAll("button.addbtn").forEach(b => b.addEventListener("click", () => addCard(b.getAttribute("data-grid"))));
  g.querySelectorAll("textarea").forEach(t => {
    const grid = t.id.slice(3);
    t.addEventListener("input", () => onType(grid));
    t.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) addCard(grid); });
  });
  g.querySelectorAll(".cards").forEach(box => {
    box.addEventListener("dragover", e => { e.preventDefault(); box.classList.add("drop-target"); });
    box.addEventListener("dragleave", () => box.classList.remove("drop-target"));
    box.addEventListener("drop", e => onDrop(e, box));
  });
}
function onType(grid) { TYPING_GRID = grid; clearTimeout(typingTimer); typingTimer = setTimeout(() => { TYPING_GRID = ""; }, 2500); }

function reactionBar(card) {
  // Only emojis that already have a count show as chips; all the rest live behind
  // the React button's picker, so cards stay uncluttered.
  const mine = MY_REACTIONS[card.id] || new Set();
  const rx = card.reactions || {};
  const chips = EMOJIS.filter(e => rx[e]).map(e =>
    '<button class="chip' + (mine.has(e) ? " mine" : "") + '" data-card="' + card.id + '" data-emoji="' + e + '">' +
    e + '<span>' + rx[e] + '</span></button>').join("");
  return '<div class="rx">' + chips +
    '<button class="react-btn" data-react="' + card.id + '" title="Add a reaction">😊<span class="plus">+</span></button></div>';
}
/* Relative "age" of a card from its ISO-8601 created_at: a live "just now / 3m"
 * for fresh cards, falling back to a wall-clock HH:MM once past an hour (matches a
 * retro's short lifespan). Labels refresh for free on each ~1.2s poll re-render. */
function fmtAgo(iso) {
  const t = Date.parse(iso);
  if (!t) return null;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  const d = new Date(t);
  let label;
  if (s < 10) label = "just now";
  else if (s < 60) label = s + "s";
  else if (s < 3600) label = Math.floor(s / 60) + "m";
  else label = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return { label: label, title: d.toLocaleString() };
}
function cardHTML(card, avatarByName) {
  const isAI = card.origin === "ai";
  const badge = isAI ? "🤖 AI" : ((avatarByName[card.author] || "") + " " + esc(card.author)).trim();
  const t = fmtAgo(card.created_at);
  const ago = t ? '<span class="ago" title="' + esc(t.title) + '">' + esc(t.label) + "</span>" : "";
  let actions = '<span class="spacer"></span>';
  if (card.mine && !isAI && !LOCKED) {  // host lock hides the ✎/✕ controls
    actions += '<button class="act" data-edit="' + card.id + '" title="Edit">✎</button>' +
               '<button class="act danger" data-del="' + card.id + '" title="Delete">✕</button>';
  }
  const body = EDITING === card.id
    ? '<textarea class="editbox" id="edit-' + card.id + '" rows="2">' + esc(card.text) + '</textarea>' +
      '<div class="edit-actions">' +
      '<button class="btn-save" data-save="' + card.id + '">Save</button>' +
      '<button class="btn-cancel" data-cancel="1">Cancel</button></div>'
    : '<div class="body">' + esc(card.text) + '</div>';
  return '<div class="card ' + (isAI ? "ai" : "") + '" draggable="true" data-id="' + card.id + '">' +
         body + '<div class="who">' + badge + ago + actions + '</div>' + reactionBar(card) + '</div>';
}

async function addCard(grid) {
  const el = document.getElementById("in-" + grid);
  const text = (el.value || "").trim();
  if (!text) return;
  el.value = ""; TYPING_GRID = "";
  try { const r = await postJSON("/api/cards", { grid, text, author: NAME }); if (r.ok) render((await r.json()).state); } catch (e) {}
}
async function react(cardId, emoji) {
  try {
    const r = await postJSON("/api/react", { card_id: cardId, emoji });
    if (!r.ok) return;
    const d = await r.json();
    const set = MY_REACTIONS[cardId] || (MY_REACTIONS[cardId] = new Set());
    if (d.reacted) set.add(emoji); else set.delete(emoji);
    render(d.state);
  } catch (e) {}
}
/* React picker: a floating menu (survives the ~1.2s re-render) with every emoji. */
function openReactPicker(cardId, btn) {
  const p = document.getElementById("rx-picker");
  if (RX_CARD === cardId && !p.classList.contains("hidden")) { closeReactPicker(); return; }
  RX_CARD = cardId;
  const mine = MY_REACTIONS[cardId] || new Set();
  p.innerHTML = EMOJIS.map(e =>
    '<button class="pick' + (mine.has(e) ? " mine" : "") + '" data-emoji="' + e + '">' + e + '</button>').join("");
  p.querySelectorAll(".pick").forEach(b =>
    b.onclick = () => { react(cardId, b.getAttribute("data-emoji")); closeReactPicker(); });
  p.classList.remove("hidden");
  const r = btn.getBoundingClientRect();
  let left = Math.min(r.left, innerWidth - p.offsetWidth - 8);
  let top = r.bottom + 6;
  if (top + p.offsetHeight > innerHeight) top = r.top - p.offsetHeight - 6;
  p.style.left = Math.max(8, left) + "px";
  p.style.top = Math.max(8, top) + "px";
}
function closeReactPicker() { RX_CARD = null; document.getElementById("rx-picker").classList.add("hidden"); }
/* Floating emoji: a reaction everyone sees rise up the screen (broadcast via poll). */
function floatEmoji(emoji) {
  const fx = document.getElementById("rx-fx");
  if (!fx || fx.childElementCount > 60) return;   // guard against a flood
  for (let i = 0; i < 3; i++) {
    const s = document.createElement("span");
    s.className = "floater"; s.textContent = emoji;
    s.style.left = (8 + Math.random() * 84) + "vw";
    s.style.animationDelay = (Math.random() * 0.4) + "s";
    s.style.fontSize = (26 + Math.random() * 18) + "px";
    s.addEventListener("animationend", () => s.remove());
    fx.appendChild(s);
  }
}
async function saveEdit(cardId) {
  const box = document.getElementById("edit-" + cardId);
  const text = box ? (box.value || "").trim() : "";
  EDITING = null;
  if (text) { try { const r = await postJSON("/api/card/edit", { card_id: cardId, text }); if (r.ok) return render((await r.json()).state); } catch (e) {} }
  tick();
}
async function deleteCard(cardId) {
  try { const r = await postJSON("/api/card/delete", { card_id: cardId }); if (r.ok) render((await r.json()).state); } catch (e) {}
}

/* ── Carried-over action items (last sprint's actions review) ─── */
function renderCarried(state) {
  const wrap = document.getElementById("carried-wrap");
  if (!wrap) return;
  const items = state.carried || [];
  if (!items.length) { wrap.classList.add("hidden"); return; }
  // Don't clobber a <select> the user is mid-change on (poll fires every ~1.2s).
  const ae = document.activeElement;
  if (ae && ae.matches && ae.matches(".carried select")) return;
  wrap.classList.remove("hidden");
  const box = document.getElementById("carried-list");
  box.innerHTML = items.map(c => {
    const st = c.status || "pending";
    const opts = CARRIED_STATUSES.map(([v, label]) =>
      '<option value="' + v + '"' + (v === st ? " selected" : "") + '>' + esc(label) + '</option>').join("");
    return '<div class="ci" data-status="' + esc(st) + '"><div class="txt">' + esc(c.text) + '</div>' +
      '<select data-item="' + esc(c.id) + '">' + opts + '</select></div>';
  }).join("");
  box.querySelectorAll("select[data-item]").forEach(sel =>
    sel.onchange = () => setCarriedStatus(sel.getAttribute("data-item"), sel.value));
}
async function setCarriedStatus(itemId, status) {
  try { const r = await postJSON("/api/carried/status", { item_id: itemId, status }); if (r.ok) render((await r.json()).state); } catch (e) {}
}

/* ── Drag & drop ────────────────────────────────────────────── */
function onDrop(e, box) {
  e.preventDefault();
  box.classList.remove("drop-target");
  if (!DRAG_ID) return;
  const grid = box.getAttribute("data-grid");
  const cards = Array.prototype.slice.call(box.querySelectorAll(".card"));
  let index = cards.length;
  for (let i = 0; i < cards.length; i++) {
    const r = cards[i].getBoundingClientRect();
    if (e.clientY < r.top + r.height / 2) { index = i; break; }
  }
  moveCard(DRAG_ID, grid, index);
  DRAG_ID = null;
}
async function moveCard(cardId, grid, index) {
  try { const r = await postJSON("/api/card/move", { card_id: cardId, grid, index }); if (r.ok) render((await r.json()).state); } catch (e) {}
}

/* ── Timer ──────────────────────────────────────────────────── */
async function startTimer(secs) { try { const r = await postJSON("/api/timer", { action: "start", duration: secs }); if (r.ok) render((await r.json()).state); } catch (e) {} }
async function stopTimer() { try { const r = await postJSON("/api/timer", { action: "stop" }); if (r.ok) render((await r.json()).state); } catch (e) {} }
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

/* ── Focus one person / group by author (for turn-by-turn walkthroughs) ─ */
function gridInner(cards, avatarByName) {
  let list = FOCUS ? cards.filter(c => c.author === FOCUS) : cards;
  if (!GROUPED) return list.map(c => cardHTML(c, avatarByName)).join("");
  // Cluster by author, preserving first-seen order within the grid.
  const order = [], groups = {};
  list.forEach(c => { const a = c.origin === "ai" ? "🤖 AI" : c.author; if (!groups[a]) { groups[a] = []; order.push(a); } groups[a].push(c); });
  return order.map(a => {
    const av = a === "🤖 AI" ? "" : (avatarByName[a] || "");
    return '<div class="author-group"><div class="ag-head">' + (av ? esc(av) + " " : "") + esc(a) +
      "</div>" + groups[a].map(c => cardHTML(c, avatarByName)).join("") + "</div>";
  }).join("");
}
function updateFocusOptions(cards) {
  // Rebuild the picker only when the human-author set actually changes, so it never
  // clobbers an open selection mid-poll. Keep FOCUS if that author is still around.
  const authors = [];
  (cards || []).forEach(c => { if (c.origin !== "ai" && c.author && authors.indexOf(c.author) < 0) authors.push(c.author); });
  authors.sort();
  const sig = authors.join("");
  if (sig === focusSig) return;
  focusSig = sig;
  const sel = document.getElementById("focus-author");
  if (!sel) return;
  if (FOCUS && authors.indexOf(FOCUS) < 0) FOCUS = "";
  sel.innerHTML = '<option value="">Everyone</option>' +
    authors.map(a => '<option value="' + esc(a) + '">' + esc(a) + "</option>").join("");
  sel.value = FOCUS;
}
function markGroup() { const b = document.getElementById("group-toggle"); if (b) b.classList.toggle("open", GROUPED); }

/* ── Host broadcasts (theme / music) + board lock, applied by every browser ─ */
function applyBroadcast(state) {
  const b = state.broadcast || {};
  // Theme: apply only when the broadcast value *changes* — so a teammate who then
  // re-picks a theme locally isn't yanked back every poll.
  if (b.theme && b.theme !== lastBcastTheme) {
    lastBcastTheme = b.theme;
    if (b.theme !== THEME) { applyTheme(b.theme); markSwatch(); }
  }
  // Music: a fresh seq means a new host command — apply it exactly once.
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
  document.querySelectorAll(".add textarea, .add .addbtn").forEach(el => { el.disabled = locked; });
  const lb = document.getElementById("lock-btn");
  if (lb) { lb.classList.toggle("open", locked); lb.title = locked ? "Unlock the board" : "Lock the board"; }
}
function showMusicBanner() { const b = document.getElementById("music-banner"); if (b) b.classList.remove("hidden"); }
function hideMusicBanner() { const b = document.getElementById("music-banner"); if (b) b.classList.add("hidden"); }

/* ── Render live state ──────────────────────────────────────── */
function render(state) {
  if (!state) return;
  LAST_STATE = state;
  applyBroadcast(state);   // set LOCKED before cards render so cardHTML hides ✎/✕
  if (state.timer) { TIMER = state.timer; OFFSET = state.timer.now_epoch - Date.now() / 1000; }
  // Float any reaction events we haven't shown yet. On the first render we only
  // seed the high-water mark (no backlog burst for someone who just joined).
  if (state.reaction_events) {
    const maxId = state.reaction_events.reduce((m, e) => Math.max(m, e.id), lastRxEvent);
    if (seededRx) state.reaction_events.forEach(e => { if (e.id > lastRxEvent) floatEmoji(e.emoji); });
    seededRx = true;
    lastRxEvent = maxId;
  }
  const avatarByName = {};
  (state.presence || []).forEach(p => { if (p.avatar) avatarByName[p.name] = p.avatar; });

  const byGrid = {}; GRIDS.forEach(([k]) => byGrid[k] = []);
  (state.cards || []).forEach(c => { (byGrid[c.grid] || (byGrid[c.grid] = [])).push(c); });
  const typingByGrid = {}; (state.typing || []).forEach(t => { (typingByGrid[t.grid] || (typingByGrid[t.grid] = [])).push(t.name); });

  GRIDS.forEach(([k]) => {
    const box = document.getElementById("cards-" + k);
    if (box) {
      // Don't clobber an open inline editor (poll fires ~1.2s) — skip re-rendering the
      // grid that holds the card being edited until Save/Cancel clears EDITING. Keyed
      // on the id (not live focus) so clicking a toolbar control mid-edit can't wipe
      // the unsaved text on the next poll; other grids still refresh live.
      const editingHere = EDITING && (byGrid[k] || []).some(c => c.id === EDITING);
      if (!editingHere) box.innerHTML = gridInner(byGrid[k] || [], avatarByName);
    }
    const tl = document.getElementById("typing-" + k);
    if (tl) { const names = (typingByGrid[k] || []).filter(n => n !== NAME); tl.textContent = names.length ? (names.join(", ") + (names.length > 1 ? " are" : " is") + " typing…") : ""; }
  });
  updateFocusOptions(state.cards || []);
  wireCards();
  renderCarried(state);

  const pr = document.getElementById("presence");
  if (pr) {
    // Others only — you are already shown by the #me chip (avoids the duplicate).
    const others = (state.presence || []).filter(p => p.name !== NAME);
    const shown = others.slice(0, 5);
    pr.innerHTML = shown.map(p => '<span class="av-dot" title="' + esc(p.name) + '">' + (p.avatar || "🙂") + "</span>").join("") +
      (others.length > shown.length ? '<span class="more">+' + (others.length - shown.length) + "</span>" : "");
  }
  // Room count = everyone present (you + teammates); the roster lists them by name.
  document.getElementById("roomcount").textContent = Math.max(1, (state.presence || []).length);
  renderRoom(state);
  const n = (state.cards || []).length;
  document.getElementById("count").textContent = "· " + n + " card" + (n === 1 ? "" : "s");
}
function renderRoom(state) {
  const list = document.getElementById("room-list");
  if (!list) return;
  const typing = new Set((state.typing || []).map(t => t.name));
  const people = state.presence || [];
  list.innerHTML = people.length
    ? people.map(p => {
        const you = p.name === NAME;
        const tag = you ? '<span class="tag you">you</span>'
          : (typing.has(p.name) ? '<span class="tag typing">typing…</span>' : "");
        return '<div class="r"><span>' + (p.avatar || "🙂") + '</span><span class="nm">' + esc(p.name) + "</span>" + tag + "</div>";
      }).join("")
    : '<div class="empty">Just you so far — share the code to invite the team.</div>';
}
function wireCards() {
  document.querySelectorAll(".chip").forEach(ch => ch.onclick = () => react(ch.getAttribute("data-card"), ch.getAttribute("data-emoji")));
  document.querySelectorAll("[data-react]").forEach(b => b.onclick = e => { e.stopPropagation(); openReactPicker(b.getAttribute("data-react"), b); });
  document.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => {
    // Render the inline editor locally (not via a network tick), then focus it with
    // the caret at the end. render() then guards this grid from poll-clobbering.
    EDITING = b.getAttribute("data-edit");
    if (LAST_STATE) render(LAST_STATE);
    const t = document.getElementById("edit-" + EDITING);
    if (t) { t.focus(); t.setSelectionRange(t.value.length, t.value.length); }
  });
  document.querySelectorAll("[data-del]").forEach(b => b.onclick = () => deleteCard(b.getAttribute("data-del")));
  document.querySelectorAll("[data-save]").forEach(b => b.onclick = () => saveEdit(b.getAttribute("data-save")));
  document.querySelectorAll("[data-cancel]").forEach(b => b.onclick = () => { EDITING = null; tick(); });
  document.querySelectorAll(".card").forEach(c => {
    c.addEventListener("dragstart", () => { DRAG_ID = c.getAttribute("data-id"); c.classList.add("dragging"); });
    c.addEventListener("dragend", () => c.classList.remove("dragging"));
  });
}

/* ── Poll loop ──────────────────────────────────────────────── */
async function tick() {
  if (!TOKEN) return;
  try {
    let state;
    if (JOINED) {
      const r = await postJSON("/api/presence", { name: NAME, avatar: AVATAR, typing_grid: TYPING_GRID });
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

/* ── Toolbar popovers (one open at a time) ──────────────────── */
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
  if (!e.target.closest("#rx-picker") && !e.target.closest("[data-react]")) closeReactPicker();
  if (e.target.closest(".pop") || e.target.closest(".tbtn") || e.target.closest(".room-btn")) return;
  closePops();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") { closePops(); closeReactPicker(); } });

/* ── Theme swatches ─────────────────────────────────────────── */
const THEMES = __THEMES__;   // canonical set, injected from board.RETRO_THEMES
function buildSwatches() {
  const wrap = document.getElementById("swatches");
  wrap.innerHTML = THEMES.map(t =>
    '<button class="swatch" data-set-theme="' + t + '" title="' + t + '"></button>'
  ).join("");
  // Paint each swatch with its theme's bg + accent by momentarily reading the vars.
  wrap.querySelectorAll(".swatch").forEach(b => {
    const t = b.getAttribute("data-set-theme");
    const probe = document.createElement("div"); probe.setAttribute("data-theme", t);
    probe.style.display = "none"; document.body.appendChild(probe);
    const cs = getComputedStyle(probe);
    b.style.background = cs.getPropertyValue("--bg") || "#0d1117";
    b.insertAdjacentHTML("beforeend", '<span class="dot" style="background:' + (cs.getPropertyValue("--accent") || "#50bebe") + '"></span>');
    document.body.removeChild(probe);
    b.addEventListener("click", () => { applyTheme(t); markSwatch(); closePops(); });
  });
  markSwatch();
}
function markSwatch() { document.querySelectorAll(".swatch").forEach(s => s.classList.toggle("sel", s.getAttribute("data-set-theme") === THEME)); }

/* Populate the station dropdown from the injected channel library. */
function buildChannels() {
  const sel = document.getElementById("music-mood");
  sel.innerHTML = Music.channels().map((c, i) => '<option value="' + i + '">' + esc(c.name) + "</option>").join("");
}

/* ── Internet-radio music (same library as the TUI) + visualizer ─────
 * The TUI streams SomaFM/SRG MP3 URLs (yeaboi.music.CHANNELS); we play
 * the very same URLs with a plain <audio> element. We deliberately do NOT route
 * the stream through Web Audio (createMediaElementSource) — a cross-origin
 * stream without CORS headers would be silenced. So the visualizer is a light
 * decorative animation gated on "is it playing", not real frequency data. */
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
      const col = getComputedStyle(document.documentElement).getPropertyValue("--accent") || "#50bebe";
      cx.fillStyle = col.trim() || "#50bebe";
      phase += 0.18;
      for (let i = 0; i < bars; i++) {
        // Layered sines give a lively, music-like bounce without touching the stream.
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
    // Apply a host broadcast: play the given station, or stop. Returns the audio
    // play() promise so the caller can show a "tap to listen" banner if autoplay is
    // blocked (a browser rejects play() without a prior user gesture).
    cast(i, on) { if (!on) { stop(); return Promise.resolve(); } load(i); return audio.play(); },
    // Resume after the user taps the autoplay banner (their tap is the gesture).
    playNow() { if (!audio.src) load(channel); return audio.play(); },
  };
})();

/* ── Timer finish: confetti + alarm ─────────────────────────── */
function celebrate() { confetti(); alarm(); }
function confetti() {
  const cv = document.getElementById("confetti"); cv.width = innerWidth; cv.height = innerHeight;
  const cx = cv.getContext("2d"); const N = 140, cols = ["#50bebe", "#a371f7", "#ff5edb", "#4cc38a", "#ffcf5e", "#ff5e5e"];
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
    // Own short-lived Web-Audio context (music is now a plain <audio> element).
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
async function toggleLock() { try { const r = await postJSON("/api/admin/lock", { locked: !LOCKED }); if (r.ok) render((await r.json()).state); } catch (e) {} }
async function castTheme() { try { const r = await postJSON("/api/admin/broadcast", { theme: THEME }); if (r.ok) render((await r.json()).state); } catch (e) {} }
async function castMusic() {
  // Broadcast the host's current play state + station; teammates apply it on poll.
  try { const r = await postJSON("/api/admin/broadcast", { music: { playing: Music.playing(), channel: Music.channelIndex() } }); if (r.ok) render((await r.json()).state); } catch (e) {}
}

/* ── Wire up + start ────────────────────────────────────────── */
applyTheme(THEME);
buildCols();
buildAvatars();
buildSwatches();
buildChannels();
paintTimer();
markGroup();
// Host controls (music/theme/timer/lock) show only to the admin; teammates see the
// matching "host controls this" note instead.
document.querySelectorAll(".admin-only").forEach(el => el.classList.toggle("hidden", !IS_ADMIN));
document.querySelectorAll(".guest-only").forEach(el => el.classList.toggle("hidden", IS_ADMIN));
document.getElementById("focus-author").addEventListener("change", e => { FOCUS = e.target.value; if (LAST_STATE) render(LAST_STATE); });
document.getElementById("group-toggle").addEventListener("click", () => { GROUPED = !GROUPED; localStorage.setItem("retro_grouped", GROUPED ? "1" : "0"); markGroup(); if (LAST_STATE) render(LAST_STATE); });
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
// Toolbar: icon buttons toggle their popovers; controls live inside.
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

function afterToken() {
  // Token known: keep it in sessionStorage (per-tab, survives refresh) and strip
  // it (and the admin secret) from the address bar, so copying the URL never leaks
  // access — or admin — to others.
  if (ADMIN) sessionStorage.setItem("retro_admin", ADMIN);
  if (TOKEN) { sessionStorage.setItem("retro_token", TOKEN); history.replaceState(null, "", "/"); }
  paintMe();
  if (NAME && localStorage.getItem("retro_avatar")) { JOINED = true; document.getElementById("modal").classList.add("hidden"); startLoop(); }
  else { openProfile(); }
}
if (TOKEN) { document.getElementById("code-modal").classList.add("hidden"); afterToken(); }
else { document.getElementById("code-modal").classList.remove("hidden"); document.getElementById("code-in").focus(); }
"""


def build_board_html() -> str:
    """Return the complete self-contained retro board page (token-free)."""

    def _lit(v: object) -> str:
        # ensure_ascii=False keeps emojis literal (page is UTF-8); still escapes quotes.
        return json.dumps(v, ensure_ascii=False)

    js = (
        _JS.replace("__GRIDS__", _lit(_GRID_JS))
        .replace("__CARRIED_STATUSES__", _lit(_CARRIED_STATUS_JS))
        .replace("__EMOJIS__", _lit(list(REACTION_EMOJIS)))
        .replace("__AVATARS__", _lit(list(AVATARS)))
        .replace("__THEMES__", _lit(list(RETRO_THEMES)))
        .replace("__ADJS__", _lit(_ADJECTIVES))
        .replace("__NOUNS__", _lit(_NOUNS))
        # Same internet-radio library the TUI uses (yeaboi.music.CHANNELS),
        # so the browser plays real streams instead of synthesized tones.
        .replace("__MUSIC_CHANNELS__", _lit([{"name": c["name"], "url": c["url"]} for c in CHANNELS]))
    )
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Sprint Retro</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        "<header>\n"
        '  <div class="brand"><span class="title">Sprint Retro</span>'
        '<span class="count" id="count"></span></div>\n'
        '  <div class="presence"><button class="me-chip" id="me"></button>'
        '<div class="avatars" id="presence"></div>'
        '<button class="room-btn" id="room-btn" title="Who\'s in the room">'
        '👥 <span id="roomcount">1</span></button></div>\n'
        '  <span class="spacer"></span>\n'
        # View controls: focus one person's cards (their turn) / group cards by author.
        '  <div class="viewctl">\n'
        '    <select id="focus-author" title="Show only one person\'s cards"><option value="">Everyone</option></select>\n'
        '    <button class="tbtn" id="group-toggle" title="Group cards by author">⊞ Group</button>\n'
        "  </div>\n"
        '  <div class="toolbar">\n'
        '    <canvas id="viz" width="34" height="22" title="Now playing"></canvas>\n'
        '    <button class="tbtn admin-only hidden" id="lock-btn" title="Lock the board"><span class="ico">🔒</span></button>\n'
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
        # Timer popover — starting/stopping is admin-only; everyone still sees the readout.
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
        '  <p class="sub guest-only hidden" style="margin:6px 0 0">The host controls the timer.</p>\n'
        "</div>\n"
        # Theme popover
        '<div id="theme-pop" class="pop hidden">\n'
        "  <label>Theme</label>\n"
        '  <div class="swatches" id="swatches"></div>\n'
        '  <div class="row admin-only hidden" style="margin-top:10px">'
        '<button class="tbtn" id="theme-cast" title="Apply this theme for the whole team">📣 Apply to everyone</button></div>\n'
        "</div>\n"
        # Room roster popover (who's here) — left-anchored under the presence cluster
        '<div id="room-pop" class="pop left hidden">\n'
        "  <label>In the room</label>\n"
        '  <div class="roster" id="room-list"></div>\n'
        "</div>\n"
        # Last sprint's actions — a review column seeded from the previous retro;
        # hidden until the poll returns carried items (empty for a first-ever retro).
        '<div id="carried-wrap" class="carried hidden">\n'
        "  <h2>Last sprint's actions</h2>\n"
        '  <p class="sub">Review what happened to each — set a status to close the loop. '
        '"Carried Over" re-adds it to this sprint.</p>\n'
        '  <div class="items" id="carried-list"></div>\n'
        "</div>\n"
        '<div class="grids" id="grids"></div>\n'
        '<canvas id="confetti"></canvas>\n'
        '<div id="rx-fx"></div>\n'
        '<div id="rx-picker" class="rx-picker hidden"></div>\n'
        # Host-broadcast banners: tap-to-listen (autoplay fallback) + board-locked notice.
        '<div id="music-banner" class="banner hidden">▶ The host started music — tap to listen</div>\n'
        '<div id="lock-banner" class="banner lock hidden">🔒 The host locked the board</div>\n'
        # Code-entry gate (shown when the URL has no token)
        '<div id="code-modal" class="overlay hidden"><div class="box">\n'
        "  <h2>Join a retro</h2>\n"
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
        '  <div class="avatars" id="avatars"></div>\n'
        '  <button class="primary" id="save-profile">Save</button>\n'
        "</div></div>\n"
        # Invite popover (QR)
        '<div id="invite-modal" class="overlay hidden"><div class="box">\n'
        "  <h2>Invite the team</h2>\n"
        '  <p class="muted">Scan to open the retro, then enter the Share code from the host.</p>\n'
        '  <div class="qrwrap"><img id="invite-img" alt="join QR"></div>\n'
        '  <button class="primary" id="invite-close">Close</button>\n'
        "</div></div>\n"
        f"<script>{js}</script>\n</body>\n</html>"
    )
    logger.debug("retro: board page built (%d bytes)", len(html.encode("utf-8")))
    return html
