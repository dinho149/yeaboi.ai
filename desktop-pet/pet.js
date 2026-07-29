// yeaboi duck desktop pet — behavior.
//
// A tiny state machine drives the duck along the bottom of the screen:
//   WANDER  — pick a spot, waddle toward it, then idle a beat, repeat
//   FLEE    — cursor got close: bolt along the ground away from it
//   STARTLE — cursor got TOO close (or you clicked): hop with a yelp
//   DRAG    — you grabbed it: it follows the cursor until you let go
//
// Cursor position comes from main (polled OS cursor → window-local coords), so
// the duck reacts to your mouse anywhere on screen, not just over the window.

const walker = document.getElementById("duck-walker");
const rig = document.getElementById("duck-rig");
const bubble = document.getElementById("duck-bubble");

// --- geometry -------------------------------------------------------------
const DUCK_W = rig.offsetWidth || 72;
let DUCK_H = 72; // refined once the base sprite reports its height
let dockInset = 74; // dock height; the feet rest on TOP of the dock (main overrides via config)
const GROUND_MARGIN = 2; // tiny lift so the feet sit just on the dock's top edge
const HIT_PAD = 10; // shrink the grab hitbox slightly vs the sprite bounds

function groundTop() {
  return window.innerHeight - DUCK_H - dockInset - GROUND_MARGIN;
}

window.pet.onConfig((c) => {
  if (c && typeof c.bottomInset === "number") dockInset = c.bottomInset;
});

// --- state ----------------------------------------------------------------
let x = window.innerWidth * 0.5 - DUCK_W / 2; // left edge of the rig
let vx = 0; // horizontal velocity (px/frame)
let airY = 0; // vertical hop offset (<= 0 means airborne)
let airV = 0; // vertical velocity
let dir = -1; // facing: see applyFacing()
let mode = "wander"; // wander | flee | drag
let targetX = x; // wander destination
let idleUntil = 0; // wander pause timer
let jumpCd = 0; // no re-hop before this time
let dragDX = 0; // grab offset (x) so the duck doesn't snap to the cursor
let dragDY = 0; // grab offset (y)
let dragging = false;

// cursor (window-local), fed from main
let mx = -9999;
let my = -9999;
let interactive = false; // is the window currently solid (grab-able)?

const WALK_SPEED = 1.15;
const FLEE_SPEED = 4.4;
const FLEE_RADIUS = 165; // start fleeing when the cursor is this close
const TOUCH_RADIUS = 62; // hop when the cursor is this close
const now = () => performance.now();
const rand = (a, b) => a + Math.random() * (b - a);

// --- personality (ported from the landing mascot) -------------------------
const TAUNTS = [
  "catch me if you can!",
  "you'll never catch me 🦆",
  "too slow!",
  "bet you can't catch me",
  "nice try 😜",
  "gotta be quicker than that!",
  "over here! …nope 🦆",
];
const REACTIONS = ["whoa!", "hey! 🦆", "eek!", "missed me!", "nope!", "rude! 🦆"];
const IDLE_LINES = ["yeaboi!", "just vibing 🦆", "nice dock", "🦆", "quack.", "brb, waddling"];
let sayIdx = 0;
let bubbleShown = false;
let bubbleHideT = null;

function say(line) {
  bubble.textContent = line;
  bubble.classList.remove("say");
  void bubble.offsetWidth; // reflow → restart the pop
  bubble.classList.add("say", "show");
  bubbleShown = true;
  clearTimeout(bubbleHideT);
  bubbleHideT = setTimeout(hideBubble, 2600);
}
function hideBubble() {
  bubble.classList.remove("show");
  bubbleShown = false;
}
function positionBubble() {
  if (!bubbleShown) return;
  const cx = x;
  const headY = groundTop() + airY + 6;
  const vw = window.innerWidth;
  const toLeft = cx + DUCK_W + 230 > vw;
  bubble.classList.toggle("flip", toLeft);
  bubble.style.top = headY + "px";
  if (toLeft) {
    bubble.style.right = vw - cx + 12 + "px";
    bubble.style.left = "auto";
  } else {
    bubble.style.left = cx + DUCK_W + 12 + "px";
    bubble.style.right = "auto";
  }
}

// --- facing ---------------------------------------------------------------
// The sprite is drawn facing LEFT. scaleX(1) keeps that; scaleX(-1) mirrors it
// to face right. We flip toward the direction of travel.
function applyFacing() {
  if (vx > 0.2) dir = -1; // moving right → mirror
  else if (vx < -0.2) dir = 1; // moving left → natural
}

// --- interaction ----------------------------------------------------------
function overDuck() {
  const cx = x + DUCK_W / 2;
  const cy = groundTop() + airY + DUCK_H / 2;
  return Math.abs(mx - cx) <= DUCK_W / 2 - HIT_PAD && Math.abs(my - cy) <= DUCK_H / 2 - HIT_PAD;
}

function setInteractive(on) {
  if (on === interactive) return;
  interactive = on;
  window.pet.setInteractive(on);
  rig.style.cursor = on ? "grab" : "default";
}

window.pet.onCursor((p) => {
  mx = p.x;
  my = p.y;
  // Keep the window solid while dragging; otherwise solid only over the duck.
  setInteractive(dragging || overDuck());
});

rig.addEventListener("mousedown", (e) => {
  e.preventDefault();
  dragging = true;
  mode = "drag";
  vx = 0;
  airV = 0;
  airY = 0;
  dragDX = mx - x; // grab offsets keep the duck under the grab point
  dragDY = my - (groundTop() + airY);
  rig.classList.add("grabbing");
  walker.classList.remove("walking");
});

window.addEventListener("mouseup", () => {
  if (!dragging) return;
  dragging = false;
  rig.classList.remove("grabbing");
  mode = "wander";
  // drop from wherever it was let go: convert the held height into a fall
  airY = Math.min(0, my - dragDY - groundTop());
  airV = 0;
  idleUntil = now() + rand(150, 500);
  pickTarget();
});

// A click that didn't turn into a drag → a startled hop.
rig.addEventListener("click", () => {
  if (dragging) return;
  startle(mx > x + DUCK_W / 2 ? -1 : 1);
});

// --- hop ------------------------------------------------------------------
function startle(pushDir) {
  if (airY !== 0 || airV !== 0 || now() < jumpCd) return;
  jumpCd = now() + 2000;
  airV = -13;
  vx += pushDir * 5;
  walker.classList.add("startled", "airborne");
  if (bubbleShown || Math.random() < 0.9) say(REACTIONS[sayIdx++ % REACTIONS.length]);
  setTimeout(() => walker.classList.remove("startled"), 1350);
}

// --- wander ---------------------------------------------------------------
function pickTarget() {
  const margin = 20;
  targetX = rand(margin, window.innerWidth - DUCK_W - margin);
  idleUntil = now() + rand(400, 1800); // pause before setting off
}
pickTarget();

// idle chatter every so often when the duck is calm
setInterval(() => {
  if (!dragging && mode !== "flee" && !bubbleShown && Math.random() < 0.5) {
    say(IDLE_LINES[Math.floor(Math.random() * IDLE_LINES.length)]);
  }
}, 7000);

// --- main loop ------------------------------------------------------------
function step() {
  requestAnimationFrame(step);
  const t = now();
  const cx = x + DUCK_W / 2;
  const gap = mx - cx; // >0 → cursor to the right
  const near = Math.abs(gap) < FLEE_RADIUS && my > groundTop() - 140;

  if (dragging) {
    // follow the cursor in both axes (airY carries the vertical offset from the
    // ground line so the same translate handles carried + grounded states)
    x = Math.max(0, Math.min(window.innerWidth - DUCK_W, mx - dragDX));
    airY = Math.min(0, my - dragDY - groundTop());
  } else {
    // decide desired horizontal velocity
    let desired = 0;
    if (near && t > jumpCd - 0) {
      mode = "flee";
      const closeness = 1 - Math.abs(gap) / FLEE_RADIUS; // 0..1
      const away = gap >= 0 ? -1 : 1; // move opposite the cursor
      desired = away * FLEE_SPEED * (0.45 + 0.55 * closeness);
      // cornered? if fleeing would run us into a wall, hop over instead
      const atWall = (away < 0 && x < 8) || (away > 0 && x > window.innerWidth - DUCK_W - 8);
      if (Math.abs(gap) < TOUCH_RADIUS || atWall) startle(away);
    } else if (t < idleUntil) {
      mode = "wander";
      desired = 0; // paused
    } else {
      mode = "wander";
      const d = targetX - x;
      if (Math.abs(d) < 3) {
        pickTarget();
        desired = 0;
      } else {
        desired = Math.sign(d) * WALK_SPEED;
      }
    }
    vx += (desired - vx) * 0.15;
    x += vx;

    // soft walls
    if (x < 0) {
      x = 0;
      vx *= -0.5;
      if (mode === "wander") pickTarget();
    } else if (x > window.innerWidth - DUCK_W) {
      x = window.innerWidth - DUCK_W;
      vx *= -0.5;
      if (mode === "wander") pickTarget();
    }

    // vertical hop physics
    if (airV !== 0 || airY !== 0) {
      airV += airV < 0 ? 0.9 : 0.12; // rise fast, float down slow
      if (airV > 1.4) airV = 1.4;
      airY += airV;
      if (airY >= 0) {
        airY = 0;
        airV = 0;
        walker.classList.remove("airborne");
      }
    }
  }

  applyFacing();
  const moving = !dragging && Math.abs(vx) > 0.18 && airY === 0;
  walker.classList.toggle("walking", moving);

  const ry = groundTop() + airY;
  walker.style.transform = `translate(${x.toFixed(1)}px, ${ry.toFixed(1)}px) scaleX(${dir})`;
  positionBubble();
}

// --- boot -----------------------------------------------------------------
function boot() {
  DUCK_H = rig.offsetHeight || DUCK_H;
  x = window.innerWidth * 0.5 - DUCK_W / 2;
  walker.classList.remove("unloaded");
  walker.classList.add("hatch");
  say("yeaboi! 🦆");
  requestAnimationFrame(step);
}

// wait for the base sprite so DUCK_H is correct
const baseImg = rig.querySelector(".d-base");
if (baseImg.complete) boot();
else baseImg.addEventListener("load", boot);

window.addEventListener("resize", () => {
  x = Math.min(x, window.innerWidth - DUCK_W);
});

window.pet.onRecenter(() => {
  dragging = false;
  mode = "wander";
  x = window.innerWidth * 0.5 - DUCK_W / 2;
  airY = 0;
  airV = 0;
  vx = 0;
  say("yeaboi!");
  pickTarget();
});
