// yeaboi duck desktop pet — behavior.
//
// A tiny state machine drives the duck along the bottom of the screen:
//   WANDER  — pick a spot, walk toward it, then idle a beat, repeat
//   FLEE    — cursor got close: bolt along the ground away from it
//   STARTLE — cursor got TOO close (or you clicked): hop with a yelp
//   DRAG    — you grabbed it: it follows the cursor until you let go
//
// The ground is DOCK-AWARE: the duck stands on the desktop floor (screen
// bottom) at the sides, and climbs UP onto the dock where the dock physically
// sits (its rect comes from main via Accessibility). One shared vertical
// physics (baseY + vy under gravity) handles walking, hops, climbs, drops and
// drag-release falls. Cursor position is fed from main so the duck reacts to
// your mouse anywhere on screen.

const walker = document.getElementById("duck-walker");
const rig = document.getElementById("duck-rig");
const body = document.getElementById("duck-body");
const bubble = document.getElementById("duck-bubble");
const footFront = rig.querySelector(".d-foot-front");
const footBack = rig.querySelector(".d-foot-back");

// --- geometry -------------------------------------------------------------
const DUCK_W = rig.offsetWidth || 72;
let RIGH = 72; // rig height, refined once the base sprite loads
const FEET_FRAC = 0.975; // sprite's feet-bottom as a fraction of rig height (measured: 496/509)
let SURFACE_RAISE = 20; // extra lift so it stands ON the surface, not sunk into it (tray-tunable)
const FLOOR_MARGIN = 2; // desktop floor: feet this far above the screen's bottom
const HIT_PAD = 8;

// dock geometry (window-local), from main; floor-only until it arrives
let dock = { present: false, x: 0, top: 0, w: 0, h: 0 };
let bottomInset = 0;

function floorSurfaceY() {
  return window.innerHeight - FLOOR_MARGIN;
}
// The contact surface (where the feet land) at a given horizontal center.
function surfaceAt(cx) {
  if (dock.present && cx >= dock.x && cx <= dock.x + dock.w) return dock.top;
  return floorSurfaceY();
}
// rig-top y whose feet rest on the surface at center cx (lifted so the duck
// stands on top of the surface rather than sinking its feet into it)
function groundBaseY(cx) {
  return surfaceAt(cx) - RIGH * FEET_FRAC - SURFACE_RAISE;
}

window.pet.onConfig((c) => {
  if (!c) return;
  if (typeof c.bottomInset === "number") bottomInset = c.bottomInset;
  if (c.dock) dock = c.dock.present ? c.dock : { present: false, x: 0, top: 0, w: 0, h: 0 };
});

// --- state ----------------------------------------------------------------
let x = window.innerWidth * 0.5 - DUCK_W / 2; // left edge of the rig
let baseY = 0; // rig top y (set in boot once RIGH known)
let vx = 0;
let vy = 0;
let dir = -1; // facing: see applyFacing()
let grounded = true;
let mode = "wander";
let targetX = x;
let idleUntil = 0;
let jumpCd = 0;
let dragDX = 0;
let dragDY = 0;
let dragging = false;
let tvx = 0; // smoothed drag velocity (for throwing)
let tvy = 0;
let tumbling = false; // mid-throw: physics-only until it settles
let gait = 0; // gait phase accumulator, advances with distance travelled

const STRIDE = 24; // px travelled per full step cycle (bigger = slower cadence)
const FOOT_LIFT = 5; // how high a foot lifts during its swing (px)
const THROW_MIN = 6; // release speed above which a drop becomes a throw

let mx = -9999;
let my = -9999;
let interactive = false;

// --- secondary motion springs (procedural "juice") ------------------------
// Each spring is {p: position, v: velocity}. `springTo` nudges it toward a
// target with stiffness k and damping d — low d = bouncier follow-through.
// We inject velocity impulses on discrete events (land, turn) and let the
// spring settle. NO squash/scale — the duck keeps its shape; the life comes
// from a gentle lean into motion, a jelly sway on turns, and a small vertical
// bounce when it lands.
const bnc = { p: 0, v: 0 }; // vertical bounce offset (px, +down)
const lean = { p: 0, v: 0 }; // body tilt (deg), leans into travel + fall
const sway = { p: 0, v: 0 }; // jelly follow-through (deg) on accel/turns
let prevVx = 0;
function springTo(s, target, k, d) {
  s.v += (target - s.p) * k;
  s.v *= d;
  s.p += s.v;
}

const WALK_SPEED = 0.9;
const FLEE_SPEED = 4.4;
const FLEE_RADIUS = 165;
const TOUCH_RADIUS = 62;
const G = 1.1; // gravity (px/frame^2)
const VMAX = 18; // terminal fall speed
const LOOK = 26; // how far ahead the duck looks for a step-up
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
let tauntIdx = 0;
let bubbleShown = false;
let bubbleHideT = null;

function say(line) {
  bubble.textContent = line;
  bubble.classList.remove("say");
  void bubble.offsetWidth;
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
  const headY = baseY + 6;
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
// Sprite is drawn facing LEFT. scaleX(1) keeps that; scaleX(-1) faces right.
function applyFacing() {
  if (vx > 0.2) dir = -1;
  else if (vx < -0.2) dir = 1;
}

// --- feet (procedural gait) -----------------------------------------------
// Phase advances with DISTANCE travelled, not time, so the planted foot moves
// backward at exactly the body's forward speed → it looks planted on the
// ground while the other foot swings forward in an arc. The two feet are half a
// cycle apart. `front` is the direction of travel; multiplying the screen-x by
// `dir` cancels the walker's scaleX(dir) flip.
function footOffset(p, front) {
  if (p < 0.5) {
    const s = p / 0.5; // stance: planted, slides front → back
    return { x: front * (STRIDE / 2 - s * STRIDE), y: 0 };
  }
  const s = (p - 0.5) / 0.5; // swing: back → front, lifted in an arc
  return { x: front * (-STRIDE / 2 + s * STRIDE), y: -FOOT_LIFT * Math.sin(s * Math.PI) };
}
function driveFeet() {
  const spd = Math.abs(vx);
  const walkGait = grounded && !tumbling;
  const amt = walkGait ? Math.min(1, spd / 0.8) : 0; // neutral feet when stopped / airborne
  if (walkGait && spd > 0.15) gait += spd / STRIDE;
  const front = vx >= 0 ? 1 : -1;
  const fF = footOffset(gait % 1, front);
  const fB = footOffset((gait + 0.5) % 1, front);
  footFront.style.transform = `translate(${(fF.x * dir * amt).toFixed(2)}px, ${(fF.y * amt).toFixed(2)}px)`;
  footBack.style.transform = `translate(${(fB.x * dir * amt).toFixed(2)}px, ${(fB.y * amt).toFixed(2)}px)`;
}

// --- interaction ----------------------------------------------------------
function overDuck() {
  const cx = x + DUCK_W / 2;
  const cy = baseY + RIGH / 2;
  return Math.abs(mx - cx) <= DUCK_W / 2 - HIT_PAD && Math.abs(my - cy) <= RIGH / 2 - HIT_PAD;
}
function setInteractive(on) {
  if (on === interactive) return;
  interactive = on;
  window.pet.setInteractive(on);
}
window.pet.onCursor((p) => {
  mx = p.x;
  my = p.y;
  setInteractive(dragging || overDuck());
});

rig.addEventListener("mousedown", (e) => {
  e.preventDefault();
  dragging = true;
  tumbling = false;
  mode = "drag";
  vx = 0;
  vy = 0;
  tvx = 0;
  tvy = 0;
  dragDX = mx - x;
  dragDY = my - baseY;
  rig.classList.add("grabbing");
  walker.classList.remove("walking");
});
window.addEventListener("mouseup", () => {
  if (!dragging) return;
  dragging = false;
  rig.classList.remove("grabbing");
  const speed = Math.hypot(tvx, tvy);
  if (speed > THROW_MIN) {
    // Throw: launch with the release velocity and let physics tumble it to a
    // stop (it arcs, hits the ground, bounces, bounces off the side walls).
    tumbling = true;
    mode = "throw";
    vx = Math.max(-42, Math.min(42, tvx));
    vy = Math.max(-42, Math.min(42, tvy));
    grounded = false;
    sway.v += Math.max(-16, Math.min(16, tvx)); // spin flair in the throw direction
    walker.classList.add("airborne");
    if (Math.random() < 0.85) say(["wheee!", "yeaboi!", "wooo 🦆", "aaah!", "again!"][Math.floor(Math.random() * 5)]);
  } else {
    // Gentle drop → place: the release height becomes the resting height above
    // the surface underneath, applied everywhere (dock + desktop floor).
    mode = "wander";
    const cx = x + DUCK_W / 2;
    const feetY = baseY + RIGH * FEET_FRAC;
    SURFACE_RAISE = Math.max(-12, Math.min(160, surfaceAt(cx) - feetY));
    console.error(`DBG SURFACE_RAISE=${SURFACE_RAISE.toFixed(1)} (drag-set)`);
    vy = 0;
    idleUntil = now() + rand(150, 500);
    pickTarget();
  }
});
rig.addEventListener("click", () => {
  if (dragging) return;
  startle(mx > x + DUCK_W / 2 ? -1 : 1);
});

// --- hop / climb ----------------------------------------------------------
function startle(pushDir) {
  if (!grounded || now() < jumpCd) return;
  jumpCd = now() + 2000;
  vy = -13;
  vx += pushDir * 5;
  grounded = false;
  walker.classList.add("startled", "airborne");
  if (bubbleShown || Math.random() < 0.9) say(REACTIONS[sayIdx++ % REACTIONS.length]);
  setTimeout(() => walker.classList.remove("startled"), 1350);
}
// jump sized to clear a step of height `h`, with clearance
function hopTo(h, pushDir) {
  vy = -Math.sqrt(2 * G * (h + 18));
  vx += pushDir * 2.6;
  grounded = false;
  jumpCd = now() + 700;
  walker.classList.add("airborne");
}

// --- wander ---------------------------------------------------------------
function pickTarget() {
  const margin = 20;
  targetX = rand(margin, window.innerWidth - DUCK_W - margin);
  idleUntil = now() + rand(400, 1800);
}
pickTarget();

setInterval(() => {
  if (!dragging && mode !== "flee" && !bubbleShown && Math.random() < 0.5) {
    say(IDLE_LINES[Math.floor(Math.random() * IDLE_LINES.length)]);
  }
}, 7000);

// --- main loop ------------------------------------------------------------
function step() {
  requestAnimationFrame(step);
  const t = now();
  const center = x + DUCK_W / 2;
  const gap = mx - center;
  const near = Math.abs(gap) < FLEE_RADIUS && my > baseY - 120;

  if (dragging) {
    const nx = Math.max(0, Math.min(window.innerWidth - DUCK_W, mx - dragDX));
    const ny = my - dragDY;
    tvx = 0.5 * tvx + 0.5 * (nx - x); // smoothed pointer velocity → throw impulse
    tvy = 0.5 * tvy + 0.5 * (ny - baseY);
    x = nx;
    baseY = ny;
  } else {
    // ---- horizontal intent ----
    if (tumbling) {
      vx *= grounded ? 0.84 : 0.995; // air keeps momentum; ground drags it down
    } else {
      let desired = 0;
      if (near) {
        mode = "flee";
        const closeness = 1 - Math.abs(gap) / FLEE_RADIUS;
        const away = gap >= 0 ? -1 : 1;
        desired = away * FLEE_SPEED * (0.45 + 0.55 * closeness);
        const atWall = (away < 0 && x < 8) || (away > 0 && x > window.innerWidth - DUCK_W - 8);
        if (Math.abs(gap) < TOUCH_RADIUS || atWall) startle(away);
      } else if (t < idleUntil) {
        mode = "wander";
        desired = 0;
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

      // ---- step-up: climb onto the dock when there's a higher surface ahead ----
      if (grounded && Math.abs(vx) > 0.2 && t > jumpCd) {
        const mvDir = vx >= 0 ? 1 : -1;
        const curSurf = surfaceAt(center);
        const aheadSurf = surfaceAt(center + mvDir * LOOK);
        if (aheadSurf < curSurf - 6) hopTo(curSurf - aheadSurf, mvDir);
      }
    }

    x += vx;

    // ---- soft walls (bouncier while tumbling) ----
    if (x < 0) {
      x = 0;
      vx *= tumbling ? -0.62 : -0.5;
      if (!tumbling && mode === "wander") pickTarget();
    } else if (x > window.innerWidth - DUCK_W) {
      x = window.innerWidth - DUCK_W;
      vx *= tumbling ? -0.62 : -0.5;
      if (!tumbling && mode === "wander") pickTarget();
    }

    // ---- vertical physics (gravity + landing on the surface under us) ----
    const gnd = groundBaseY(x + DUCK_W / 2);
    vy += G;
    if (vy > VMAX) vy = VMAX;
    baseY += vy;
    if (baseY >= gnd && vy >= 0) {
      const impact = vy; // fall speed at the moment of touchdown
      baseY = gnd;
      vy = 0;
      if (!grounded) {
        grounded = true;
        bnc.v += Math.min(9, 1.5 + impact * 0.5); // small downward bounce, scaled by impact
        if (tumbling && impact > 6) {
          vy = -impact * 0.42; // bounce back up
          grounded = false;
          sway.v += (vx >= 0 ? 1 : -1) * 6;
        } else {
          walker.classList.remove("airborne");
          if (tumbling) {
            tumbling = false; // settled
            mode = "wander";
            idleUntil = now() + rand(200, 700);
            pickTarget();
          }
        }
      }
    } else {
      grounded = false;
    }
  }

  applyFacing();
  const moving = !dragging && !tumbling && grounded && Math.abs(vx) > 0.18;
  walker.classList.toggle("walking", moving);
  driveFeet();

  // ---- secondary motion (no squash — bounce + lean + jelly sway) ----
  // turn/accel wobble: an impulse opposite the change in horizontal velocity
  if (!dragging) sway.v += -(vx - prevVx) * 1.7;
  prevVx = vx;
  springTo(bnc, 0, 0.2, 0.7); // vertical bounce settles back to rest
  const leanTarget = dragging ? 0 : -vx * 2.3 + (grounded ? 0 : Math.max(-9, Math.min(9, vy * 0.55)));
  springTo(lean, leanTarget, 0.2, 0.75);
  springTo(sway, 0, 0.16, 0.78);

  body.style.transform = `translateY(${bnc.p.toFixed(2)}px) rotate(${(lean.p + sway.p).toFixed(2)}deg)`;

  walker.style.transform = `translate(${x.toFixed(1)}px, ${baseY.toFixed(1)}px) scaleX(${dir})`;
  positionBubble();
}

// --- boot -----------------------------------------------------------------
function boot() {
  RIGH = rig.offsetHeight || RIGH;
  x = window.innerWidth * 0.5 - DUCK_W / 2;
  baseY = groundBaseY(x + DUCK_W / 2);
  walker.classList.remove("unloaded");
  walker.classList.add("hatch");
  say("yeaboi! 🦆");
  const dbg = () =>
    console.error(
      `DBG inner=${window.innerWidth}x${window.innerHeight} RIGH=${RIGH} baseImgH=${baseImg.offsetHeight} ` +
        `floor=${floorSurfaceY()} dock=${JSON.stringify(dock)} baseY=${baseY.toFixed(1)} feetY=${(baseY + RIGH * FEET_FRAC).toFixed(1)}`
    );
  dbg();
  setTimeout(dbg, 2600); // again once the dock config has arrived
  requestAnimationFrame(step);
}

const baseImg = rig.querySelector(".d-base");
if (baseImg.complete) boot();
else baseImg.addEventListener("load", boot);

window.addEventListener("resize", () => {
  x = Math.min(x, window.innerWidth - DUCK_W);
});

window.pet.onNudge((d) => {
  SURFACE_RAISE += d;
  if (grounded) baseY = groundBaseY(x + DUCK_W / 2); // re-snap to the new height
  console.error(`DBG SURFACE_RAISE=${SURFACE_RAISE}`);
});

window.pet.onRecenter(() => {
  dragging = false;
  mode = "wander";
  x = window.innerWidth * 0.5 - DUCK_W / 2;
  baseY = groundBaseY(x + DUCK_W / 2);
  vx = 0;
  vy = 0;
  say("yeaboi!");
  pickTarget();
});
