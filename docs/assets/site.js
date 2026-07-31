// yeaboi.ai — shared behavior: smooth scroll, copy buttons, scroll reveals,
// and a small universal client-side router.
//
// The left rail (.navbar) is a PERSISTENT SHELL: it is never inside the
// swappable region and is therefore never rebuilt, reloaded, or re-animated
// by navigation — on ANY internal link click (landing → docs, docs → docs,
// docs → landing), only #page-content is fetched and swapped. This is what
// decouples the sidebar (motion) from the main content (fade).

// Own our scroll position: cross-page nav always lands at the top instead of
// the browser restoring the previous offset.
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

var reducedMotion = false;
var lenis = null;

// duck walker state: last x, facing direction (sprite faces LEFT, so moving
// right = flipped), and the idle timer that ends the waddle
var _duckX = -1;
var _duckY = -1;
var _duckLandX = 0;
var _duckLandY = 0;
var _duckDir = -1;
var _duckSpot = null;
var _duckTp = false;
var _duckIdleT = null;
// chase state: cursor position, flee offset along the footer hairline, and
// a lock while the cornered-escape poof is in flight
var _mX = -1;
var _mY = -1;
var _mmPend = false;
var _duckFlee = 0;
var _duckVel = 0;
var _duckAirY = 0;   // vertical offset while jumping (negative = airborne)
var _duckAirV = 0;   // vertical velocity
var _duckJumpCd = 0; // no re-launch before this timestamp (full animation plays out)
var _duckPoof = false;
var _duckChase = false;
var _duckPhysOn = false;
var _duckFootGeom = null;
var _duckRevealT = null;
var _updateScroll = null;   // assigned inside DOMContentLoaded; lets navigateTo reposition the duck

// The footer chase runs on real physics: every frame the cursor applies a
// repulsion force (quadratic falloff inside the comfort radius), friction
// bleeds velocity off, and the viewport edges are soft walls the duck
// BOUNCES off — unless the cursor has it truly cornered, in which case it
// poofs to the far side. Runs as its own rAF loop only while the footer
// perch is active.
function _duckPhysicsStep() {
  if (!_duckChase || !_duckFootGeom) { _duckPhysOn = false; return; }
  requestAnimationFrame(_duckPhysicsStep);
  var duck = document.getElementById('duck-walker');
  if (!duck || _duckPoof || _duckTp) return;
  var g = _duckFootGeom;
  var x = g.base + _duckFlee;
  var cx = x + 32, cy = g.y + _duckAirY + 35;
  if (_mX >= 0) {
    var ddx = cx - _mX, ddy = cy - _mY;
    var dist = Math.sqrt(ddx * ddx + ddy * ddy) || 1;
    if (dist < 170) {
      var s = (170 - dist) / 170;            // 0 at the radius edge → 1 on contact
      _duckVel += (ddx >= 0 ? 1 : -1) * (0.4 + 4.8 * s * s);
    }
    // an actual TOUCH (cursor inside the body) startles it into ONE full
    // jump sequence — leap, then a slow FLOAT back down with fast wing
    // flapping (.airborne) and the shades popped up its forehead
    // (.startled). A cooldown guarantees the whole animation plays out
    // instead of re-triggering every frame the cursor stays inside.
    var nowT = performance.now();
    if (Math.abs(cx - _mX) < 38 && Math.abs(cy - _mY) < 42 &&
        _duckAirY === 0 && _duckAirV === 0 && nowT > _duckJumpCd) {
      _duckJumpCd = nowT + 2300;
      _duckAirV = -12;
      _duckVel += (cx >= _mX ? 1 : -1) * 6;
      duck.classList.add('startled', 'airborne');
      _duckJumpSay();      // interrupt the taunt with a startled reaction line
      setTimeout(function () { duck.classList.remove('startled'); }, 1350);
    }
  }
  // vertical: snappy rise, then a properly FLOATY parachute descent — barely
  // any gravity while falling, low terminal velocity, wings doing the work
  if (_duckAirV !== 0 || _duckAirY !== 0) {
    _duckAirV += _duckAirV < 0 ? 0.9 : 0.12;
    if (_duckAirV > 1.4) _duckAirV = 1.4;
    _duckAirY += _duckAirV;
    if (_duckAirY >= 0) {                                  // touchdown
      _duckAirY = 0; _duckAirV = 0;
      duck.classList.remove('airborne');
    }
  }
  _duckVel *= 0.86;                          // friction
  if (Math.abs(_duckVel) < 0.05 && _duckAirY === 0 && _duckAirV === 0) {
    // At rest — but the surface may have just scrolled under us. When Lenis
    // settles at the very bottom it fires no further mouse events, so without
    // this the duck would freeze at its stale pre-settle spot (sitting UNDER
    // the divider) until the cursor nudged the physics back to life. Re-pin to
    // the current footer geometry every frame so it seats ON the divider the
    // instant the scroll settles — no mouse move required.
    var rrx = g.base + _duckFlee, rry = g.y;
    _duckX = rrx; _duckY = rry; _duckLandX = rrx; _duckLandY = rry;
    duck.style.transform = 'translate(' + rrx.toFixed(1) + 'px,' + rry.toFixed(1) + 'px) scaleX(' + _duckDir + ')';
    _positionDuckBubble(rrx, rry);
    return;
  }
  _duckFlee += _duckVel;
  x = g.base + _duckFlee;
  if (x < g.minX || x > g.maxX) {
    var wall = x < g.minX ? g.minX : g.maxX;
    x = wall; _duckFlee = wall - g.base;
    var d2x = x + 32 - _mX, d2y = cy - _mY;
    if (_mX >= 0 && Math.sqrt(d2x * d2x + d2y * d2y) < 95) {
      // pinned against the wall with the cursor closing in → escape poof
      _duckPoof = true; _duckVel = 0;
      duck.classList.add('teleporting');
      duck.classList.remove('walking');
      (function (atLeft) {
        setTimeout(function () {
          _duckFlee = (atLeft ? g.maxX : g.minX) - g.base;
          var nx = g.base + _duckFlee;
          var ny = g.y + _duckAirY;     // keep altitude if it poofed mid-jump
          _duckDir = atLeft ? 1 : -1;   // face back toward the page
          _duckX = nx; _duckY = ny; _duckLandX = nx; _duckLandY = ny;
          duck.style.transform = 'translate(' + nx.toFixed(1) + 'px,' + ny.toFixed(1) + 'px) scaleX(' + _duckDir + ')';
          duck.classList.remove('teleporting');
          _duckPoof = false;
        }, 160);
      })(wall === g.minX);
      return;
    }
    _duckVel = -_duckVel * 0.4;              // soft bounce off the edge
  }
  if (Math.abs(_duckVel) > 0.4) {
    _duckDir = _duckVel > 0 ? -1 : 1;
    duck.classList.add('walking');
    clearTimeout(_duckIdleT);
    _duckIdleT = setTimeout(function () { duck.classList.remove('walking'); }, 180);
  }
  var ry = g.y + _duckAirY;
  _duckX = x; _duckY = ry; _duckLandX = x; _duckLandY = ry;
  duck.style.transform = 'translate(' + x.toFixed(1) + 'px,' + ry.toFixed(1) + 'px) scaleX(' + _duckDir + ')';
  _positionDuckBubble(x, ry);
}

// ---- the duck's "catch me" speech bubble (landing footer only) ----
// A rotating one-liner that fades in ONCE the page is scrolled to the very
// bottom and the duck has settled on the footer, teasing the cursor-flee game.
// The line cycles on a timer, and a cursor-touch JUMP interrupts it with a
// reaction line. Only ever exists on the landing page (never the docs).
var _duckBubbleEl = null;
var _duckBubbleShown = false;
var _duckBubbleTimer = null;
var _duckBubbleIdx = 0;
var DUCK_TAUNTS = [
  'catch me if you can!',
  "you'll never catch me 🦆",
  'too slow!',
  'bet you can’t catch me',
  'nice try 😜',
  "gotta be quicker than that!",
  'over here! …nope 🦆',
];
var DUCK_JUMPS = ['whoa!', 'hey! 🦆', 'eek!', 'missed me!', 'nope!', 'rude! 🦆'];

function _ensureDuckBubble() {
  if (_duckBubbleEl && document.body.contains(_duckBubbleEl)) return _duckBubbleEl;
  if (document.body.classList.contains('docs')) return null;   // docs duck ≠ mascot
  var b = document.createElement('div');
  b.className = 'duck-bubble';
  b.setAttribute('aria-hidden', 'true');
  document.body.appendChild(b);
  _duckBubbleEl = b;
  return b;
}
function _duckSay(line) {
  if (!_duckBubbleEl) return;
  _duckBubbleEl.textContent = line;
  _duckBubbleEl.classList.remove('say');
  void _duckBubbleEl.offsetWidth;              // reflow → restart the pop
  _duckBubbleEl.classList.add('say');
}
function _duckNextTaunt() {
  _duckSay(DUCK_TAUNTS[_duckBubbleIdx % DUCK_TAUNTS.length]);
  _duckBubbleIdx++;
}
function _startTauntCycle() {
  clearInterval(_duckBubbleTimer);
  _duckBubbleTimer = setInterval(function () {
    if (_duckBubbleShown && _duckBubbleEl) _duckNextTaunt();
  }, 3200);
}
function _hideDuckBubble() {
  if (_duckBubbleEl) _duckBubbleEl.classList.remove('show');
  _duckBubbleShown = false;
  clearInterval(_duckBubbleTimer); _duckBubbleTimer = null;
}
// a startle jump interrupts the taunt with a reaction line, then resumes cycling
function _duckJumpSay() {
  if (reducedMotion || !_duckBubbleShown || !_duckBubbleEl) return;
  _duckSay(DUCK_JUMPS[_duckBubbleIdx % DUCK_JUMPS.length]);
  _duckBubbleIdx++;
  _startTauntCycle();          // reset the timer so the reaction lingers
}
// Position the bubble IN LINE with the duck (level with its head, off to the
// side with room), tail pointing back at it. Reveals only once the page is at
// the very bottom; stays until the duck leaves the footer perch.
function _positionDuckBubble(dx, dy) {
  if (reducedMotion) return;
  var onFooter = _duckChase && _duckFootGeom;
  if (!onFooter) { _hideDuckBubble(); return; }
  var doc = document.documentElement;
  var atBottom = (window.scrollY || doc.scrollTop || 0) + window.innerHeight >= doc.scrollHeight - 6;
  if (!_duckBubbleShown && !atBottom) return;      // wait until we've reached the bottom
  var b = _ensureDuckBubble();
  if (!b) return;
  var vw = window.innerWidth, dw = 64;
  // place to the right of the duck by default; flip to the left if it'd overflow
  var toLeft = dx + dw + 230 > vw;
  b.classList.toggle('flip', toLeft);
  b.style.top = (dy + 6) + 'px';                   // level with the duck's head
  if (toLeft) { b.style.right = (vw - dx + 12) + 'px'; b.style.left = 'auto'; }
  else { b.style.left = (dx + dw + 12) + 'px'; b.style.right = 'auto'; }
  if (!_duckBubbleShown) {
    _duckBubbleShown = true;
    _duckBubbleIdx = 0;
    _duckNextTaunt();
    _startTauntCycle();
    requestAnimationFrame(function () { b.classList.add('show'); });
  }
}

// Reveal the duck only after the hero's staggered entrance has finished
// (~1.2s of rise animations) — it positions invisibly first, then fades in.
// Called on initial load AND after client-side nav back to the landing page
// (the swapped-in markup carries .unloaded again and the entrance replays).
function scheduleDuckReveal() {
  if (!document.getElementById('duck-walker')) return;
  // Fresh element after (re)load — clear any stale motion state so the duck
  // paints DIRECTLY on its perch (first-paint branch), instead of teleporting
  // from a previous position or sitting at its 0,0 CSS default until the first
  // scroll. This is what stranded it in the top-left after nav back from docs.
  _duckSpot = null; _duckX = -1; _duckY = -1; _duckTp = false; _duckPoof = false;
  _duckChase = false; _duckFlee = 0; _duckVel = 0; _duckAirY = 0; _duckAirV = 0;
  clearTimeout(_duckRevealT);
  _duckRevealT = setTimeout(function () {
    var el = document.getElementById('duck-walker');
    if (el) { el.classList.remove('unloaded'); el.classList.add('hatch'); }
  }, 900);
}

// Tap/click the mascot anywhere → the same startle jump it does when the cursor
// catches it on the footer. On the footer perch we drive it through the physics
// loop (real leap + parachute); everywhere else the physics loop is idle, so we
// play a self-contained CSS hop on the .duck-rig (doesn't fight the wrapper's
// position transform). A cooldown lets the whole animation finish.
var _duckHopCd = 0;
function _duckTapJump() {
  if (reducedMotion) return;
  var duck = document.getElementById('duck-walker');
  if (!duck || _duckPoof || _duckTp) return;
  var nowT = performance.now();
  if (nowT < _duckHopCd) return;
  _duckHopCd = nowT + 1400;
  duck.classList.add('startled');
  _duckJumpSay();                          // reaction line if the bubble's up
  if (_duckChase && _duckPhysOn) {
    // footer: hand off to the physics leap (parachute float back down)
    if (_duckAirY === 0 && _duckAirV === 0) { _duckAirV = -12; duck.classList.add('airborne'); }
  } else {
    // anywhere else: a quick CSS hop on the inner rig
    var rig = duck.querySelector('.duck-rig');
    if (rig) {
      rig.classList.remove('hop'); void rig.offsetWidth; rig.classList.add('hop');
      rig.addEventListener('animationend', function h() { rig.classList.remove('hop'); rig.removeEventListener('animationend', h); });
    }
  }
  setTimeout(function () { duck.classList.remove('startled'); }, 1350);
}
// bind once (document-level, survives content swaps that replace the sprite)
if (!window.__duckTapBound) {
  window.__duckTapBound = true;
  document.addEventListener('click', function (e) {
    var rig = e.target.closest && e.target.closest('#duck-walker .duck-rig');
    if (rig) _duckTapJump();
  });
}

// ============================================================================
// Docs duck — a small chatbot perched bottom-right of every docs page. The
// site is fully static (GitHub Pages, no backend), so it answers by SIFTING
// the docs client-side: on first open it lazily fetches every page in
// NAV_GROUPS, splits each into sections at its h2/h3 headings, and builds a
// lightweight in-memory index. A question is tokenised and scored against that
// index (term frequency + title/heading boosts); the best sections come back
// as answer cards with a snippet and a deep link to the exact heading.
// ============================================================================
var _docsIndex = null;         // [{path,pageTitle,heading,id,text,tokens}]
var _docsIndexing = null;      // in-flight promise (built once)
var _docsDuckBuilt = false;

var STOPWORDS = { the:1, a:1, an:1, and:1, or:1, of:1, to:1, in:1, on:1, for:1,
  is:1, are:1, be:1, how:1, do:1, i:1, my:1, with:1, it:1, that:1, this:1,
  can:1, does:1, what:1, when:1, where:1, which:1, you:1, your:1, me:1, from:1,
  by:1, as:1, at:1, s:1 };

function _tokenize(str) {
  return (str || '').toLowerCase().match(/[a-z0-9]+/g) || [];
}
function _contentTokens(str) {
  return _tokenize(str).filter(function (t) { return t.length > 1 && !STOPWORDS[t]; });
}

// Build the section index by fetching every docs page once and parsing it.
function _buildDocsIndex() {
  if (_docsIndexing) return _docsIndexing;
  var pages = _flatNav();
  _docsIndexing = Promise.all(pages.map(function (pg) {
    return fetch(pg.path)
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (html) {
        if (!html) return [];
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var art = doc.querySelector('.docs-content article') || doc.querySelector('article') || doc.body;
        var pageTitle = (doc.querySelector('h1') || {}).textContent || pg.title;
        var sections = [];
        var cur = { heading: pageTitle, id: '', parts: [] };
        // walk the article in document order; each h2/h3 starts a new section
        art.querySelectorAll('h2, h3, p, li, pre, td').forEach(function (el) {
          var tag = el.tagName.toLowerCase();
          if (tag === 'h2' || tag === 'h3') {
            if (cur.parts.length) sections.push(cur);
            cur = { heading: el.textContent.trim(), id: el.id || '', parts: [] };
          } else {
            var t = el.textContent.trim();
            if (t) cur.parts.push(t);
          }
        });
        if (cur.parts.length) sections.push(cur);
        return sections.map(function (s) {
          var text = s.parts.join(' ').replace(/\s+/g, ' ').slice(0, 900);
          return {
            path: pg.path, pageTitle: pageTitle.trim(),
            heading: s.heading, id: s.id, text: text,
            tokens: _contentTokens(s.heading + ' ' + text),
            headTokens: _contentTokens(s.heading + ' ' + pageTitle),
          };
        });
      })
      .catch(function () { return []; });
  })).then(function (all) {
    _docsIndex = all.reduce(function (acc, arr) { return acc.concat(arr); }, []);
    return _docsIndex;
  });
  return _docsIndexing;
}

// Score sections against the query and return the best few.
function _searchDocs(query) {
  if (!_docsIndex) return [];
  var qs = _contentTokens(query);
  if (!qs.length) return [];
  var scored = _docsIndex.map(function (sec) {
    var score = 0;
    qs.forEach(function (q) {
      var inText = 0, inHead = 0;
      sec.tokens.forEach(function (t) { if (t === q) inText++; else if (t.indexOf(q) === 0) inText += 0.4; });
      sec.headTokens.forEach(function (t) { if (t === q) inHead++; else if (t.indexOf(q) === 0) inHead += 0.4; });
      score += inText + inHead * 4;   // heading/title matches weigh heavily
    });
    return { sec: sec, score: score };
  }).filter(function (r) { return r.score > 0; });
  scored.sort(function (a, b) { return b.score - a.score; });
  return scored.slice(0, 4).map(function (r) { return r.sec; });
}

// Split a block of text into readable prose sentences. Splits on sentence-
// ending punctuation, then drops anything that reads like a command list / code
// dump (several "make/uv/pip/git" verbs or shell symbols) so the answer stays
// conversational instead of regurgitating a whole Makefile as one giant line.
function _splitSentences(text) {
  var raw = (text || '').replace(/\s+/g, ' ').trim();
  // protect abbreviation dots (e.g. i.e. etc. vs.) so they aren't read as
  // sentence ends — swap EVERY dot in them for \x01 (never in docs prose),
  // split, then restore.
  raw = raw.replace(/\b(e\.g\.|i\.e\.|etc\.|vs\.|approx\.|no\.)/gi, function (m) {
    return m.replace(/\./g, '\x01');
  });
  var parts = (raw.match(/[^.!?]+[.!?]+/g) || []).map(function (s) {
    return s.replace(/\x01/g, '.');
  });   // COMPLETE sentences only —
  // dropping the trailing "[^.!?]+$" alternative means a section truncated
  // mid-sentence by the 900-char cap never surfaces a "…observab." fragment
  return parts
    .map(function (s) { return s.trim(); })
    .filter(function (s) {
      if (s.length < 30 || s.length > 260) return false;     // too short / runaway
      // must READ like prose: begin with a capital letter or digit (not a comma,
      // dash, closing bracket, or lower-case scrap left over from splitting a
      // list) so leads never start mid-fragment like ", Anthropic keys start…"
      if (!/^[A-Z0-9"“]/.test(s)) return false;
      // command/code smell: several shell verbs or symbols, or many " x — y"
      // list separators packed together (a definition list, not a sentence)
      var codey = (s.match(/(^|\s)(make|uv|pip|npm|git|yeaboi)\s|[#|$`=]/g) || []).length;
      if (codey >= 2) return false;
      var seps = (s.match(/—|\bor skip\b|\bwith\b.*\bor\b/g) || []).length;
      if (seps >= 3) return false;                            // packed list row
      return true;
    });
}

// Conversational small-talk / meta handling — greetings, thanks, capability
// questions — so the duck feels like it's talking, not just searching. Returns
// a reply object or null (null → fall through to the doc-answer composer).
function _smallTalk(query) {
  var q = query.toLowerCase().trim();
  var has = function (re) { return re.test(q); };
  if (has(/^(hi|hey|hello|yo|sup|howdy|hiya|quack)\b/) || has(/good (morning|afternoon|evening)/)) {
    return { text: "🦆 Hey! I'm the yeaboi docs duck. Ask me how to install it, what the modes do, how integrations work — anything in the docs and I'll talk you through it.", cards: [] };
  }
  if (has(/\b(thanks|thank you|cheers|ta|appreciate)\b/)) {
    return { text: "🦆 Anytime! Quack if you need anything else.", cards: [] };
  }
  if (has(/\b(bye|goodbye|see ya|later)\b/)) {
    return { text: "🦆 See you around! Close me with the ✕ or Esc whenever.", cards: [] };
  }
  if (has(/who are you|what are you|are you (a )?(bot|duck|ai|real)|your name/)) {
    return { text: "🦆 I'm the yeaboi docs duck — a little helper that reads the whole documentation and answers your questions in plain English, with links to the exact page.", cards: [] };
  }
  if (has(/what can you (do|help)|help me|what do you know|how (can|do) you (help|work)/)) {
    return { text: "🦆 I can explain anything covered in the docs: installation & setup, the seven modes (planning, standup, retro, poker, performance, reporting, analysis), integrations & exports, tools, session management, architecture, and deployment. What are you trying to do?", cards: [] };
  }
  if (has(/^\s*(what is|what's|whats) yeaboi/)) {
    return null; // real question — let the composer answer it from the docs
  }
  return null;
}

// Compose a conversational answer: take the top matching sections, pull the
// sentences within them that best match the question, stitch them into a short
// natural-language reply phrased for the question, and cite the sources. This
// is extractive (no LLM — the site is fully static) but reads as prose, not a
// pile of keyword snippets.
function _composeAnswer(query, hits) {
  var qtok = _contentTokens(query);
  var qset = {}; qtok.forEach(function (t) { qset[t] = 1; });
  var top = hits[0];

  // Anchor on the single best-matching section (search already weights heading
  // + title matches heavily, so hits[0] is the most on-topic). Read that
  // section's sentences IN DOCUMENT ORDER — coherent prose, not scattered
  // keyword fragments. Take the opening 1–2 sentences (they introduce the
  // topic) and, if a later sentence matches the query better, append it.
  function scoreSent(sent) {
    var toks = _contentTokens(sent), sc = 0;
    toks.forEach(function (t) {
      if (qset[t]) sc += 1;
      else { for (var k in qset) { if (t.indexOf(k) === 0 || k.indexOf(t) === 0) { sc += 0.3; break; } } }
    });
    return sc;
  }
  // Gather candidate sentences from the top sections and rank them by how
  // well they answer the QUESTION — NOT by document position. Leading with a
  // section's intro sentence is exactly what produced answers unrelated to
  // the query (e.g. "how do I get started" → the Quick-mode intro blurb).
  var cands = [];
  for (var h = 0; h < Math.min(hits.length, 3); h++) {
    _splitSentences(hits[h].text).forEach(function (s) {
      cands.push({ s: s, sec: hits[h], sc: scoreSent(s) });
    });
  }
  cands.sort(function (a, b) { return b.sc - a.sc; });

  var anchor, body, weak = false;
  if (cands.length && cands[0].sc >= 1.5) {
    // a sentence genuinely matches the question — quote it (plus one more
    // strong, non-duplicate sentence for a fuller answer)
    anchor = cands[0].sec;
    var picked = [cands[0].s];
    for (var i = 1; i < cands.length && picked.length < 2; i++) {
      if (cands[i].sc >= 1 && picked.indexOf(cands[i].s) === -1) picked.push(cands[i].s);
    }
    body = picked.map(function (s) { return /[.!?]$/.test(s) ? s : s + '.'; }).join(' ');
  } else {
    // nothing clearly answers the question — DON'T fabricate a prose reply
    // from an off-topic lead sentence; point at the best section honestly and
    // let the source card do the work.
    anchor = top;
    weak = true;
    body = "the " + top.pageTitle + " page covers this, under “" + top.heading + "” — tap below to jump straight there.";
  }

  // question-shaped lead-in (note the trailing space — the join fix). A weak
  // answer always uses the neutral lead so it never over-promises.
  var q = query.trim();
  var lead = "🦆 ";
  if (!weak && /^(how\b|how do|how can|how to)/i.test(q)) lead = "🦆 Here's how: ";

  // sources: lead with the section we actually quoted, then other top hits
  var srcSecs = [], srcSeen = {};
  [anchor].concat(hits.slice(0, 3)).forEach(function (sec) {
    var href = sec.path + (sec.id ? '#' + sec.id : '');
    if (srcSeen[href]) return; srcSeen[href] = 1;
    srcSecs.push({ title: sec.heading, page: sec.pageTitle, href: href });
  });

  return {
    text: (lead + body).replace(/\s{2,}/g, ' ').trim(),
    cards: srcSecs.slice(0, 3),
    sources: true,
  };
}

function _docsDuckReply(query) {
  var small = _smallTalk(query);
  if (small) return small;
  var hits = _searchDocs(query);
  if (!hits.length) {
    return { text: "🦆 Hmm, I couldn't find that in the docs. Try rephrasing — I know about installation & setup, the seven modes, integrations & exports, tools, session management, architecture, and deployment.", cards: [] };
  }
  return _composeAnswer(query, hits);
}

function _appendDocsMsg(log, who, node) {
  var row = document.createElement('div');
  row.className = 'dd-msg dd-' + who + ' dd-enter';   // dd-enter → CSS slide-in
  row.appendChild(node);
  log.appendChild(row);
  // next frame: drop .dd-enter so it transitions from offset+faded → in place
  requestAnimationFrame(function () {
    requestAnimationFrame(function () { row.classList.remove('dd-enter'); });
  });
  log.scrollTop = log.scrollHeight;
  return row;
}

// Build the bottom-right duck FAB + chat panel on docs pages (once).
function initDocsDuck() {
  var onDocs = document.body.classList.contains('docs');
  var existing = document.getElementById('docs-duck');
  if (!onDocs) { if (existing) existing.remove(); _docsDuckBuilt = false; return; }
  // arriving on docs: the landing mascot's speech bubble lives on <body>
  // (outside the swapped #page-content), so tear it down and stop its timer
  // — otherwise its "catch me" line lingers over the docs.
  _hideDuckBubble();
  if (_duckBubbleEl) { _duckBubbleEl.remove(); _duckBubbleEl = null; }
  if (existing) return;             // already present, survives content swaps
  _docsDuckBuilt = true;

  var root = document.createElement('div');
  root.id = 'docs-duck';
  // Bare duck sprite (no circle). Click → it slides left + flips to face the
  // other way, the conversation console expands outward from it (CSS transform)
  // and a greeting bubble pops from the duck.
  root.innerHTML =
    '<div class="dd-backdrop" aria-hidden="true"></div>' +
    '<div class="dd-console" role="dialog" aria-label="Ask the docs">' +
      '<div class="dd-log" id="dd-log" data-lenis-prevent></div>' +
      '<form class="dd-form" id="dd-form">' +
        '<input type="text" class="dd-input" id="dd-input" placeholder="Ask about yeaboi…" autocomplete="off" aria-label="Ask about yeaboi" />' +
        '<button type="submit" class="dd-send" aria-label="Send">→</button>' +
      '</form>' +
    '</div>' +
    '<div class="dd-prompt" aria-hidden="true"></div>' +
    '<button type="button" class="dd-duck" aria-label="Ask the docs duck" aria-expanded="false">' +
      '<span class="dd-duck-rig">' +
        '<img class="d-base" src="/assets/duck-base.png" alt="" />' +
        '<img class="d-wing" src="/assets/duck-wing.png" alt="" />' +
        '<img class="d-glasses" src="/assets/duck-glasses.png" alt="" />' +
      '</span>' +
    '</button>';
  document.body.appendChild(root);

  var duck = root.querySelector('.dd-duck');
  var console_ = root.querySelector('.dd-console');
  var form = root.querySelector('#dd-form');
  var input = root.querySelector('#dd-input');
  var log = root.querySelector('#dd-log');
  var promptEl = root.querySelector('.dd-prompt');
  var greeted = false;

  // idle-prompt bubble: the duck spits out an inviting line while it's closed,
  // rotating through a few. Hidden the moment the console opens; resumes if
  // the user closes it again without ever asking.
  var DD_PROMPTS = ['Ask me about yeaboi 🦆', 'Curious how it works?', 'Need a hand? Quack.', 'Ask the docs →', 'What can I help with?'];
  var _promptIdx = 0, _promptTimer = null;
  function showPrompt() {
    if (root.classList.contains('open')) return;
    promptEl.textContent = DD_PROMPTS[_promptIdx % DD_PROMPTS.length];
    _promptIdx++;
    promptEl.classList.remove('say'); void promptEl.offsetWidth; promptEl.classList.add('say', 'show');
  }
  function hidePrompt() { promptEl.classList.remove('show'); }
  function startPrompts() {
    clearInterval(_promptTimer);
    // first nudge shortly after arriving, then rotate every ~9s
    setTimeout(showPrompt, 2600);
    _promptTimer = setInterval(showPrompt, 9000);
  }
  if (!reducedMotion) startPrompts();

  function renderReply(reply) {
    var wrap = document.createElement('div');
    var p = document.createElement('p');
    p.className = 'dd-text';
    p.textContent = reply.text;
    wrap.appendChild(p);
    if (reply.cards && reply.cards.length) {
      if (reply.sources) {
        var lbl = document.createElement('span');
        lbl.className = 'dd-src-label';
        lbl.textContent = reply.cards.length > 1 ? 'Sources' : 'Source';
        wrap.appendChild(lbl);
      }
      reply.cards.forEach(function (c) {
        var a = document.createElement('a');
        a.className = 'dd-card';
        a.href = c.href;
        a.innerHTML = '<span class="dd-card-h"></span><span class="dd-card-page"></span>';
        a.querySelector('.dd-card-h').textContent = c.title;
        a.querySelector('.dd-card-page').textContent = c.page;
        // tapping a source jumps to that section → close the chat so it doesn't
        // sit over the page you asked to see (esp. the full-screen mobile panel)
        a.addEventListener('click', function () { closePanel(); });
        wrap.appendChild(a);
      });
    }
    _appendDocsMsg(log, 'bot', wrap);
  }

  function ask(q) {
    var u = document.createElement('p');
    u.className = 'dd-text';
    u.textContent = q;
    _appendDocsMsg(log, 'user', u);
    var typing = document.createElement('p');
    typing.className = 'dd-text dd-typing';
    typing.textContent = '🦆 sifting the docs…';
    var trow = _appendDocsMsg(log, 'bot', typing);
    var t0 = performance.now();
    _buildDocsIndex().then(function () {
      var reply = _docsDuckReply(q);
      // keep the "sifting…" beat visible for a moment so the answer doesn't
      // pop instantly (feels more like the duck is reading the docs)
      var wait = Math.max(0, 650 - (performance.now() - t0));
      setTimeout(function () { trow.remove(); renderReply(reply); }, wait);
    });
  }

  function openPanel() {
    duck.setAttribute('aria-expanded', 'true');
    root.classList.add('open');                // CSS: duck slides left + flips, console expands
    hidePrompt(); clearInterval(_promptTimer);
    _buildDocsIndex();                         // warm the index on first open
    if (!greeted) {
      greeted = true;
      var g = document.createElement('p');
      g.className = 'dd-text';
      g.textContent = "🦆 Quack! Ask me anything about yeaboi — I'll read the docs and talk you through it.";
      _appendDocsMsg(log, 'bot', g);
    }
    setTimeout(function () { input.focus(); }, 260);
  }
  function closePanel() {
    duck.setAttribute('aria-expanded', 'false');
    root.classList.remove('open');
    if (!reducedMotion) startPrompts();        // resume idle nudges
  }

  duck.addEventListener('click', function () {
    if (root.classList.contains('open')) closePanel(); else openPanel();
  });
  // capture-phase so that when the chat is open, Esc closes ONLY the chat —
  // stopping the event before the rail's own Esc handler collapses the sidebar
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && root.classList.contains('open')) {
      e.stopImmediatePropagation();
      closePanel();
    }
  }, true);
  // clicking the blurred backdrop, or anywhere outside the widget, closes it
  root.querySelector('.dd-backdrop').addEventListener('click', closePanel);
  document.addEventListener('click', function (e) {
    if (!root.classList.contains('open')) return;
    if (e.target.closest && e.target.closest('#docs-duck')) return;
    closePanel();
  });
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q) return;
    input.value = '';
    ask(q);
  });
}

document.addEventListener('DOMContentLoaded', function () {
  reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- Lenis smooth scrolling (site-wide) ----
  if (window.Lenis && !reducedMotion) {
    lenis = new Lenis({ duration: 1.1, smoothWheel: true });
    window.__lenis = lenis;
    var rafTicks = 0;
    (function raf(time) {
      rafTicks++;
      if (lenis) lenis.raf(time);
      requestAnimationFrame(raf);
    })(0);
    // Watchdog: Lenis routes ALL wheel scrolling through rAF. If rAF is
    // throttled or frozen (background tabs, battery saver, some embedded
    // browsers), that would leave the page unscrollable — so fall back to
    // native scrolling when rAF isn't ticking.
    setTimeout(function () {
      if (rafTicks < 5 && lenis) {
        lenis.destroy();
        lenis = null;
        window.__lenis = null;
      }
    }, 800);
  }

  // ---- delegated click handling (copy buttons + all internal links) ----
  document.addEventListener('click', function (e) {
    var copyBtn = e.target.closest && e.target.closest('.copy[data-copy]');
    if (copyBtn) {
      var text = copyBtn.getAttribute('data-copy');
      navigator.clipboard.writeText(text).then(function () {
        var prev = copyBtn.textContent;
        copyBtn.textContent = 'copied ✓';
        copyBtn.classList.add('done');
        setTimeout(function () {
          copyBtn.textContent = prev;
          copyBtn.classList.remove('done');
        }, 1600);
      });
      return;
    }

    var a = e.target.closest ? e.target.closest('a') : null;
    if (!a || a.target === '_blank' || a.hasAttribute('download')) return;
    var href = a.getAttribute('href');
    if (!href) return;

    if (href.charAt(0) === '#') {
      var hashTarget = document.querySelector(href);
      if (hashTarget) {
        e.preventDefault();
        if (window.__lenis) window.__lenis.scrollTo(hashTarget, { offset: -24 });
        else hashTarget.scrollIntoView({ behavior: 'smooth' });
      }
      return;
    }

    var url;
    try { url = new URL(a.href, window.location.href); } catch (e2) { return; }
    if (url.origin !== window.location.origin) return; // external — normal nav
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;

    e.preventDefault();
    navigateTo(url.href, true);
  });

  // ---- subtle grain-textured aura that reveals as you scroll ----
  var auraEl = document.querySelector('.scroll-aura');
  if (!auraEl) {
    auraEl = document.createElement('div');
    auraEl.className = 'scroll-aura';
    auraEl.setAttribute('aria-hidden', 'true');
    document.body.insertBefore(auraEl, document.body.firstChild);
  }
  function updateScrollProgress() {
    var doc = document.documentElement;
    var st = window.scrollY || doc.scrollTop || 0;
    // mobile top bar: transparent at rest, frosted once scrolled (no-op on
    // desktop — the .scrolled styles live inside the 900px media query)
    var navbar = document.querySelector('.navbar');
    if (navbar) navbar.classList.toggle('scrolled', st > 8);
    var max = doc.scrollHeight - window.innerHeight;
    var p = max > 0 ? Math.min(1, Math.max(0, st / max)) : 0;
    if (auraEl && !reducedMotion) {
      auraEl.style.opacity = (p * 0.95).toFixed(3);
      auraEl.style.transform = 'translateY(' + ((1 - p) * 3).toFixed(2) + '%)';
    }
    // "see it in action" hint blurs + fades out quickly as soon as you start
    // scrolling — a short, local effect, independent of the page-wide aura
    // above. Re-queried live since the hero gets swapped back in by the
    // router after any landing ↔ docs navigation.
    var hint = document.querySelector('.scroll-hint');
    if (hint) {
      var hp = Math.min(1, st / 160); // fully, heavily blurred out within ~160px of scroll
      if (reducedMotion) {
        hint.style.opacity = st > 4 ? '0' : '1';
      } else {
        hint.style.opacity = (1 - hp).toFixed(3);
        hint.style.filter = hp > 0.01 ? 'blur(' + (hp * 16).toFixed(1) + 'px)' : 'none';
      }
    }
    // duck walker: a fixed set of PERCHES, each ON a surface (a component's
    // top edge or empty margin — never floating over content). Within a
    // perch it can walk along its surface (terminal top L→R, modes grid top
    // R→L, tracked from live rects); BETWEEN perches it TELEPORTS (quick
    // fade out/in) instead of gliding — so it can never be caught mid-air
    // over the page. Mobile keeps the simple bottom-edge stroll.
    var duck = document.getElementById('duck-walker');
    if (duck) {
      var vw = window.innerWidth, vh = window.innerHeight;
      var dw = 64, dh = 70;
      var dx, dy, spotIdx;
      var clamp01 = function (t) { return Math.min(1, Math.max(0, t)); };
      var lerp = function (a, b, t) { return a + (b - a) * clamp01(t); };
      var rectOf = function (sel) { var e = document.querySelector(sel); return e ? e.getBoundingClientRect() : null; };
      if (vw <= 900) {
        spotIdx = -1;
        _duckChase = false;
        dx = 12 + p * Math.max(0, vw - dw - 24);
        dy = vh - dh - 6;
      } else {
        var spots = [];
        var sc = rectOf('.scrolly'), fb = rectOf('.app-frame');
        // hero: standing just LEFT of the install codeblock, feet level
        // with its bottom edge, riding with it
        var cb = rectOf('.hero-cta .codeblock');
        spots.push({ s: -1e9, pos: function () {
          if (cb) return [cb.left - dw - 12, cb.bottom - dh + 2];
          return [0.07 * (vw - dw), vh - dh - 8]; // fallback: grounded left margin
        } });
        // terminal: stands on the pinned frame's chrome and walks LEFT→RIGHT
        // across it as the scrollytelling steps go by
        if (sc && fb) {
          (function (s0, span) {
            spots.push({ s: s0, pos: function () {
              var q = (st - s0) / span;
              return [lerp(fb.left + 6, fb.right - dw - 6, q), fb.top - dh + 9];
            } });
          })(sc.top + st - vh * 0.5, Math.max(1, sc.height - vh * 0.55));
        }
        // modes grid: walks its top edge RIGHT→LEFT as the grid rides up
        var mg = rectOf('.modes');
        if (mg) {
          spots.push({ s: mg.top + st - vh * 0.85, pos: function () {
            var q = (vh * 0.85 - mg.top) / (vh * 0.85);
            return [lerp(mg.right - dw - 8, mg.left + 8, q), mg.top - dh + 13];
          } });
        }
        // pipeline: walks its top edge LEFT→RIGHT
        var pl = rectOf('.pipeline');
        if (pl) {
          spots.push({ s: pl.top + st - vh * 0.85, pos: function () {
            var q = (vh * 0.85 - pl.top) / (vh * 0.85);
            return [lerp(pl.left + 8, pl.right - dw - 8, q), pl.top - dh + 13];
          } });
        }
        // footer: stands on the footer's top hairline — and FLEES the cursor
        // along it with real physics (see _duckPhysicsStep). This pos() just
        // publishes the surface geometry; the physics loop owns the motion.
        var ft = rectOf('footer');
        if (ft) {
          spots.push({ s: ft.top + st - vh * 0.85, chase: true, pos: function () {
            var base = 0.74 * (vw - dw);
            _duckFootGeom = { base: base, minX: 10, maxX: vw - dw - 10, y: ft.top - dh + 4 };
            return [
              Math.min(_duckFootGeom.maxX, Math.max(_duckFootGeom.minX, base + _duckFlee)),
              _duckFootGeom.y,
            ];
          } });
        }
        spots.sort(function (a, b) { return a.s - b.s; });
        spotIdx = 0;
        for (var i = 0; i < spots.length; i++) if (st >= spots[i].s) spotIdx = i;
        var xy = spots[spotIdx].pos();
        dx = xy[0]; dy = xy[1];
        // chase physics runs only while the flee perch is active
        _duckChase = !!spots[spotIdx].chase;
        if (_duckChase && !_duckPhysOn) { _duckPhysOn = true; requestAnimationFrame(_duckPhysicsStep); }
        // scrolled up off the footer → tuck the speech bubble away again
        if (!_duckChase && _duckBubbleEl) { _duckBubbleEl.classList.remove('show'); _duckBubbleShown = false; }
      }

      if (_duckSpot === null || _duckTp) {
        // first paint: place directly; mid-teleport: hold until it lands
        if (_duckSpot === null) {
          _duckSpot = spotIdx; _duckX = dx; _duckY = dy;
          _duckDir = dx + dw / 2 < vw / 2 ? -1 : 1; // face away from the near edge
          duck.style.transform = 'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px) scaleX(' + _duckDir + ')';
        }
      } else if (spotIdx !== _duckSpot) {
        // area change → teleport: fade out here, reappear there
        _duckTp = true;
        _duckSpot = spotIdx;
        _duckFlee = 0; _duckVel = 0; _duckAirY = 0; _duckAirV = 0; // fresh perch, no leftover chase state
        duck.classList.add('teleporting');
        duck.classList.remove('walking');
        duck.classList.remove('airborne');
        setTimeout(function () {
          // land at the CURRENT position for the new spot (recomputed by the
          // next scroll frame; use last computed as the landing point),
          // facing away from the nearest viewport edge — never staring
          // off-page with a stale direction from the previous walk
          _duckDir = _duckLandX + dw / 2 < vw / 2 ? -1 : 1;
          duck.style.transform = 'translate(' + _duckLandX.toFixed(1) + 'px,' + _duckLandY.toFixed(1) + 'px) scaleX(' + _duckDir + ')';
          _duckX = _duckLandX; _duckY = _duckLandY;
          duck.classList.remove('teleporting');
          _duckTp = false;
        }, 160);
      } else if (!(_duckChase && _duckPhysOn)) {
        // in-spot movement: walk the surface, waddling while moving.
        // While the chase physics loop is live it is the ONLY writer —
        // writing here too would stamp the grounded position over the
        // airborne one every mousemove and split the duck in two.
        if (Math.abs(dx - _duckX) > 0.5) _duckDir = dx > _duckX ? -1 : 1;
        if (Math.abs(dx - _duckX) > 0.5 || Math.abs(dy - _duckY) > 0.5) {
          duck.classList.add('walking');
          clearTimeout(_duckIdleT);
          _duckIdleT = setTimeout(function () { duck.classList.remove('walking'); }, 180);
        }
        _duckX = dx; _duckY = dy;
        duck.style.transform = 'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px) scaleX(' + _duckDir + ')';
      }
      _duckLandX = dx; _duckLandY = dy;
    }
  }
  window.addEventListener('scroll', updateScrollProgress, { passive: true });
  window.addEventListener('resize', updateScrollProgress, { passive: true });
  // cursor tracking for the footer chase — rAF-throttled re-render so the
  // duck reacts while the page itself isn't scrolling
  window.addEventListener('mousemove', function (e) {
    _mX = e.clientX; _mY = e.clientY;
    if (!_mmPend) {
      _mmPend = true;
      requestAnimationFrame(function () { _mmPend = false; updateScrollProgress(); });
    }
  }, { passive: true });
  if (lenis) lenis.on('scroll', updateScrollProgress);
  _updateScroll = updateScrollProgress;   // expose so navigateTo can reposition after a page swap
  updateScrollProgress();
  // re-render once the hero's staggered entrance animations finish — rects
  // measured mid-entrance (content rises 22px) would leave the duck perched
  // slightly below its surface until the first scroll/mouse event — then
  // fade the duck in (it positions while still invisible)
  setTimeout(updateScrollProgress, 860);
  scheduleDuckReveal();

  // ---- persistent rail shell ----
  buildRail();

  // ---- on-page TOC (docs) + scroll reveals — re-run after every nav too ----
  renderTOC();
  rescanReveals();
  initHeroDemo();
  initPipeCarousel();
  initDocsDuck();

  // give the current history entry a state object so the first Back works
  try { history.replaceState({}, '', window.location.href); } catch (e) {}
});

window.addEventListener('popstate', function () {
  navigateTo(window.location.href, false);
});

// ============================================================================
// Universal client-side navigation: fetch → swap #page-content → re-init.
// Works for ANY internal page (landing or docs) in either direction. The rail
// (.navbar) lives outside #page-content and is never touched.
// ============================================================================
function navigateTo(url, push) {
  var target;
  try { target = new URL(url, window.location.href); } catch (e) { window.location.href = url; return; }

  fetch(target.href)
    .then(function (r) {
      // Check r.ok. Without it a 404 body gets swapped in as though it were a
      // real page, leaving a dead URL in the address bar under pushState (and,
      // since a page_view fires below, counted as a successful view).
      // Throwing lands in the .catch at the bottom, which hands the URL to the
      // browser for a real 404 with a real status code. (_buildDocsIndex
      // already checks r.ok; this path never did.)
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.text();
    })
    .then(function (html) {
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var newPage = doc.getElementById('page-content');
      var curPage = document.getElementById('page-content');
      if (!newPage || !curPage) { window.location.href = url; return; }

      if (push) history.pushState({}, '', target.href);
      document.title = doc.title || document.title;
      document.body.className = doc.body.className; // carries the 'docs' scope class

      // GA4: the <head> snippet fires exactly ONE page_view, on the real page
      // load. Every navigation after that is this function — a pushState swap —
      // which gtag cannot observe, so without this the entire session would be
      // attributed to whichever page the visitor happened to land on. Sent
      // after document.title and pushState above, so it carries the NEW title
      // and URL. Requires "Page changes based on browser history events" to be
      // OFF in the GA4 stream's Enhanced Measurement, or every nav counts
      // twice. No-ops when gtag is absent (not configured, blocked, file://).
      if (typeof window.gtag === 'function') {
        window.gtag('event', 'page_view', {
          page_location: window.location.href,
          page_path: window.location.pathname,
          page_title: document.title,
        });
      }

      curPage.outerHTML = newPage.outerHTML;
      var imported = document.getElementById('page-content');

      if (window.__lenis) window.__lenis.scrollTo(0, { immediate: true });
      else window.scrollTo(0, 0);

      renderTOC();
      rescanReveals();
      initHeroDemo();
      initPipeCarousel();
      initDocsDuck();
      scheduleDuckReveal();
      if (_updateScroll) _updateScroll();   // place the fresh duck on its perch NOW (still invisible), not at 0,0
      if (window.YB && window.YB.setCurrent) window.YB.setCurrent(target.pathname);
      syncRailToPage();

      // Content side = pure FADE (no motion), scoped to #page-content only —
      // the rail is outside this element and is never part of the animation.
      if (imported && imported.animate && !reducedMotion) {
        imported.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 260, easing: 'ease' });
      }
    })
    .catch(function () { window.location.href = url; });
}

// ---- on-page "On this page" TOC + scroll-spy (docs only; no-ops elsewhere) ----
var _tocObserver = null;
function renderTOC() {
  var tocRoot = document.getElementById('docs-toc');
  var article = document.querySelector('article');
  if (_tocObserver) { _tocObserver.disconnect(); _tocObserver = null; }
  if (!tocRoot || !article) return;
  tocRoot.innerHTML = '';

  var headings = article.querySelectorAll('h2[id], h3[id]');
  if (!headings.length) { tocRoot.style.display = 'none'; return; }
  tocRoot.style.display = '';

  var ul = document.createElement('ul');
  var links = new Map();
  headings.forEach(function (h) {
    var li = document.createElement('li');
    li.className = h.tagName === 'H3' ? 'toc-sub' : '';
    var a = document.createElement('a');
    a.href = '#' + h.id;
    a.textContent = h.textContent;
    links.set(h.id, a);
    li.appendChild(a);
    ul.appendChild(li);
  });
  var label = document.createElement('p');
  label.className = 'toc-label';
  label.textContent = 'On this page';
  tocRoot.appendChild(label);
  tocRoot.appendChild(ul);

  if ('IntersectionObserver' in window) {
    var activeId = null;
    var visible = new Set();
    function setActive(id) {
      if (id === activeId) return;
      activeId = id;
      links.forEach(function (a, hid) { a.classList.toggle('active', hid === id); });
    }
    _tocObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) visible.add(e.target.id);
        else visible.delete(e.target.id);
      });
      for (var i = 0; i < headings.length; i++) {
        if (visible.has(headings[i].id)) { setActive(headings[i].id); return; }
      }
    }, { rootMargin: '-80px 0px -60% 0px' });
    headings.forEach(function (h) { _tocObserver.observe(h); });
  }
}

// ---- scroll reveals (any [data-reveal]) — re-scanned after every nav so
// freshly-swapped-in content picks up the same sweep-based reveal system ----
var _revealPending = [];
var _revealSweepTimer = null;
function _revealSweep() {
  var line = window.innerHeight * 0.92;
  _revealPending = _revealPending.filter(function (el) {
    if (el.getBoundingClientRect().top <= line) {
      el.classList.add('is-visible');
      return false;
    }
    return true;
  });
  if (!_revealPending.length && _revealSweepTimer) {
    window.removeEventListener('scroll', _revealSweep);
    window.removeEventListener('resize', _revealSweep);
    clearInterval(_revealSweepTimer);
    _revealSweepTimer = null;
  }
}
function rescanReveals() {
  var found = Array.prototype.slice.call(document.querySelectorAll('[data-reveal]:not(.is-visible)'));
  found.forEach(function (el, i) {
    if (_revealPending.indexOf(el) === -1) {
      el.style.transitionDelay = (Math.min(i % 6, 4) * 60) + 'ms';
      _revealPending.push(el);
    }
  });
  if (_revealPending.length && !_revealSweepTimer) {
    window.addEventListener('scroll', _revealSweep, { passive: true });
    window.addEventListener('resize', _revealSweep, { passive: true });
    _revealSweepTimer = setInterval(_revealSweep, 250);
  }
  _revealSweep();
}

// ---- hero demo: the real app startup — splash, then the mode-select menu --
// Recreates src/yeaboi/ui/splash.py's wordmark fade, then the actual
// mode-select screen (src/yeaboi/ui/mode_select/screens/_screens.py) —
// modes the visitor can step through by hand, same as arrow-key
// navigation in the real TUI. No-op on any page without #hero-demo (docs).
var _heroTimer = null;
var _heroTypeTimer = null;
var _heroRevealTimer = null;
var _heroTipTimer = null;
var _heroTipSwapTimer = null;
var _heroStepIO = null;
var _heroSizeHandler = null;

// The real welcome-screen tip rotation (src/yeaboi/ui/shared/_tips.py):
// voice tip first, the general tips, music tip last; one rotates in every
// TIP_ROTATE_SECONDS (6s), cross-fading, with position dots underneath.
var HERO_TIPS = [
  '\u{1F3A4} Tip: double-tap Space in any text field to dictate',
  '\u{1F4A1} Tip: resume your last session any time with --resume',
  '\u{1F4A1} Tip: push epics & stories straight to Jira or Azure DevOps',
  '\u{1F4A1} Tip: export a plan to HTML or JSON for sharing and CI/CD',
  '\u{1F4A1} Tip: import a filled-in questionnaire with --questionnaire',
  '\u{1F4A1} Tip: switch between --theme dark and --theme light',
  '\u{1F4A1} Tip: run headless with --non-interactive for scripts & pipelines',
  '\u{1F3B5} Tip: press Ctrl+P for focus music · Ctrl+O to switch channel',
];

function initHeroDemo() {
  if (_heroTimer) { clearInterval(_heroTimer); _heroTimer = null; }
  if (_heroTypeTimer) { clearInterval(_heroTypeTimer); _heroTypeTimer = null; }
  if (_heroRevealTimer) { clearTimeout(_heroRevealTimer); _heroRevealTimer = null; }
  if (_heroTipTimer) { clearInterval(_heroTipTimer); _heroTipTimer = null; }
  if (_heroTipSwapTimer) { clearTimeout(_heroTipSwapTimer); _heroTipSwapTimer = null; }
  if (_heroStepIO) { _heroStepIO.disconnect(); _heroStepIO = null; }
  var root = document.getElementById('hero-demo');
  if (!root) return;

  var splash = document.getElementById('tui-splash');
  var menu = document.getElementById('tui-menu');
  var modes = Array.prototype.slice.call(root.querySelectorAll('.tui-mode'));
  if (!modes.length) return;

  // Phone single-mode showcase: scale each block title to fill the panel
  // width. Titles range 18–46 columns, so one shared font-size leaves the
  // short ones lost in the space sized for the longest; per-title sizing
  // (panel width / columns / 0.6em char width, clamped) fills the frame.
  function sizePhoneTitles() {
    var phone = window.matchMedia && window.matchMedia('(max-width:600px)').matches;
    var panel = root.querySelector('.tui-panel');
    var w = panel ? panel.clientWidth - 32 : 300;
    modes.forEach(function (m) {
      var pre = m.querySelector('.tui-mode-title');
      if (!phone) { pre.style.fontSize = ''; return; }
      var cols = Math.max.apply(null, pre.textContent.split('\n').map(function (l) { return l.length; }));
      pre.style.fontSize = Math.max(10, Math.min(16, w / (cols * 0.6))) + 'px';
    });
  }
  sizePhoneTitles();
  // re-size on viewport changes (e.g. phone rotation) — the 900px matchMedia
  // re-init doesn't fire for width changes that stay on one side of it
  if (_heroSizeHandler) window.removeEventListener('resize', _heroSizeHandler);
  _heroSizeHandler = sizePhoneTitles;
  window.addEventListener('resize', _heroSizeHandler, { passive: true });

  var current = 0;

  // Typewriter-reveals the selected mode's description, mirroring the real
  // screen's per-character desc_reveal fade.
  function typeDesc(el, text) {
    if (_heroTypeTimer) clearInterval(_heroTypeTimer);
    if (reducedMotion) { el.textContent = text; return; }
    el.textContent = '';
    var i = 0;
    _heroTypeTimer = setInterval(function () {
      i++;
      el.textContent = text.slice(0, i);
      if (i >= text.length) { clearInterval(_heroTypeTimer); _heroTypeTimer = null; }
    }, 16);
  }

  function show(i, opts) {
    var userInitiated = !opts || opts.userInitiated !== false;
    current = ((i % modes.length) + modes.length) % modes.length;
    modes.forEach(function (m, idx) {
      var active = idx === current;
      m.classList.toggle('is-active', active);
      var descEl = m.querySelector('.tui-mode-desc');
      if (active) typeDesc(descEl, m.getAttribute('data-desc') || '');
      else descEl.textContent = '';
    });
    if (userInitiated) restartAuto();
  }

  modes.forEach(function (m) {
    m.addEventListener('click', function () {
      show(parseInt(m.getAttribute('data-mode'), 10) || 0);
    });
  });

  // Scrollytelling: when the step rail is present AND visible (it's
  // display:none on mobile, where the terminal auto-cycles instead),
  // scrolling a step into the middle of the viewport selects its mode —
  // the selection is scroll-driven, so the auto-advance timer stays off.
  var steps = Array.prototype.slice.call(document.querySelectorAll('.scrolly-step'));
  // getClientRects, not computed display: the mobile breakpoint hides the
  // PARENT rail (.scrolly-steps), and a child of a display:none ancestor
  // still reports its own specified display value.
  var scrollDriven = steps.length > 0 &&
    steps[0].getClientRects().length > 0 &&
    'IntersectionObserver' in window;

  // Auto-advance through the modes so the demo has life at rest — paused on
  // hover/focus, and off when the scroll rail is driving selection or under
  // reduced motion.
  function restartAuto() {
    if (_heroTimer) clearInterval(_heroTimer);
    if (reducedMotion || scrollDriven) return;
    _heroTimer = setInterval(function () {
      show(current + 1, { userInitiated: false });
    }, 3400);
  }

  if (scrollDriven) {
    _heroStepIO = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var i = parseInt(en.target.getAttribute('data-step'), 10) || 0;
        steps.forEach(function (s) { s.classList.toggle('is-active', s === en.target); });
        show(i, { userInitiated: false });
      });
    }, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });
    steps.forEach(function (s) { _heroStepIO.observe(s); });
  }
  root.addEventListener('pointerenter', function () { if (_heroTimer) clearInterval(_heroTimer); });
  root.addEventListener('pointerleave', restartAuto);
  root.addEventListener('focusin', function () { if (_heroTimer) clearInterval(_heroTimer); });
  root.addEventListener('focusout', restartAuto);

  // ---- tip block rotation (mirrors _build_tip_rows + current_tip) ----
  var tipEl = document.getElementById('tui-tip');
  var dotsEl = document.getElementById('tui-tip-dots');
  var tipIdx = 0;
  function renderTip() {
    if (tipEl) tipEl.textContent = HERO_TIPS[tipIdx];
    if (dotsEl) {
      dotsEl.textContent = '';
      for (var d = 0; d < HERO_TIPS.length; d++) {
        if (d) dotsEl.appendChild(document.createTextNode(' '));
        var dot = document.createElement('span');
        dot.textContent = d === tipIdx ? '●' : '○';
        if (d === tipIdx) dot.className = 'on';
        dotsEl.appendChild(dot);
      }
    }
  }
  function rotateTip() {
    tipIdx = (tipIdx + 1) % HERO_TIPS.length;
    if (reducedMotion || !tipEl) { renderTip(); return; }
    tipEl.classList.add('fading');
    _heroTipSwapTimer = setTimeout(function () {
      renderTip();
      tipEl.classList.remove('fading');
    }, 900);
  }

  function revealMenu() {
    if (splash) splash.style.display = 'none';
    if (menu) menu.hidden = false;
    show(0, { userInitiated: false });
    restartAuto();
    renderTip();
    _heroTipTimer = setInterval(rotateTip, 6000); // TIP_ROTATE_SECONDS
  }
  if (reducedMotion) {
    revealMenu();
  } else {
    _heroRevealTimer = setTimeout(revealMenu, 2300); // matches splash.py's ~2.4s fade in/shine/fade out
  }
}

// ---- mobile pipeline carousel indicator -----------------------------------
// Position dots (the TUI's ● ○ language) + a "swipe →" hint under the
// horizontally-scrolling pipeline, and an .at-end class that drops the
// right-edge fade once fully scrolled. Listeners live on the elements
// themselves, which are replaced on client-side nav — no leak.
function initPipeCarousel() {
  var pipe = document.querySelector('.pipeline');
  var dots = document.querySelector('.pipe-dots');
  if (!pipe || !dots) return;
  var stages = pipe.querySelectorAll('.stage');
  dots.textContent = '';
  var spans = [];
  for (var i = 0; i < stages.length; i++) {
    var s = document.createElement('span');
    s.textContent = i === 0 ? '●' : '○';
    if (i === 0) s.className = 'on';
    dots.appendChild(s);
    spans.push(s);
  }
  var hint = document.createElement('span');
  hint.className = 'pipe-hint';
  hint.textContent = 'swipe →';
  dots.appendChild(hint);
  function update() {
    var max = pipe.scrollWidth - pipe.clientWidth;
    pipe.classList.toggle('at-end', max <= 0 || max - pipe.scrollLeft < 8);
    if (max <= 0) return;
    if (pipe.scrollLeft > 20) dots.classList.add('swiped');
    var idx = Math.min(spans.length - 1, Math.round((pipe.scrollLeft / max) * (spans.length - 1)));
    spans.forEach(function (sp, j) {
      sp.textContent = j === idx ? '●' : '○';
      sp.classList.toggle('on', j === idx);
    });
  }
  pipe.addEventListener('scroll', update, { passive: true });
  update();
}

// Re-evaluate the hero demo's driving mode when the layout crosses the mobile
// breakpoint — the step rail is display:none there, so the demo must switch
// between scroll-driven selection and the self-running auto-cycle.
if (window.matchMedia) {
  var _heroMQ = window.matchMedia('(max-width:900px)');
  var _heroMQHandler = function () { initHeroDemo(); };
  if (_heroMQ.addEventListener) _heroMQ.addEventListener('change', _heroMQHandler);
  else if (_heroMQ.addListener) _heroMQ.addListener(_heroMQHandler);
}

// ---- shared docs navigation ------------------------------------------------
// Single source of truth for the docs page tree, rendered into the rail's
// DOCS dropdown (built once, persists across all navigation).
//
// ALSO READ BY scripts/gen_site_seo.py, which generates the crawlable footer
// and the JSON-LD breadcrumbs from this literal. Keep it JSON-shaped — plain
// string/array/object literals only, no computed values, no comments inside
// the array — or generation fails loudly. Adding a page here without adding
// the HTML file (or vice versa) fails tests/unit/test_site_seo.py.
var NAV_GROUPS = [
  { label: "Start", entries: [
    { title: "Documentation", path: "/docs/index.html" },
    { title: "Getting Started", path: "/docs/getting-started.html" },
    { title: "CLI Reference", path: "/docs/cli-reference.html" },
  ] },
  { label: "Modes", entries: [
    { title: "Overview", path: "/docs/modes/index.html", children: [
      { title: "Planning", path: "/docs/modes/planning.html" },
      { title: "Daily Standup", path: "/docs/modes/standup.html" },
      { title: "Retro", path: "/docs/modes/retro.html" },
      { title: "Planning Poker", path: "/docs/modes/poker.html" },
      { title: "Performance", path: "/docs/modes/performance.html" },
      { title: "Reporting", path: "/docs/modes/reporting.html" },
      { title: "Team Analysis", path: "/docs/modes/team-analysis.html" },
    ] },
  ] },
  { label: "Guides", entries: [
    { title: "Integrations & Exports", path: "/docs/integrations-exports.html" },
    { title: "Session Management", path: "/docs/session-management.html" },
    { title: "Tools", path: "/docs/tools.html" },
  ] },
  { label: "Reference", entries: [
    { title: "Architecture & Concepts", path: "/docs/architecture.html" },
    { title: "Scrum Standards", path: "/docs/scrum-standards.html" },
    { title: "Deployment", path: "/docs/deployment.html" },
    { title: "Development", path: "/docs/development.html" },
  ] },
];

function _flatNav() {
  var out = [];
  NAV_GROUPS.forEach(function (g) {
    g.entries.forEach(function (e) {
      out.push(e);
      (e.children || []).forEach(function (c) { out.push(c); });
    });
  });
  return out;
}
// depth 0 (top-level) items are individually revealable — each gets an
// .item-wrap/.item-inner pair so it can grid-row-collapse on its own and be
// staggered open/closed. Nested children (depth > 0) reveal together with
// their parent, not as their own stagger step.
function _renderNavTree(entries, cur, depth) {
  depth = depth || 0;
  var ul = document.createElement("ul");
  ul.className = "nav-tree";
  entries.forEach(function (e) {
    var li = document.createElement("li");
    var a = document.createElement("a");
    a.href = e.path;
    a.textContent = e.title;
    if (e.path === cur) a.classList.add("current");
    var kids = e.children && e.children.length ? _renderNavTree(e.children, cur, depth + 1) : null;
    if (depth === 0) {
      var wrap = document.createElement("div");
      wrap.className = "item-wrap";
      var inner = document.createElement("div");
      inner.className = "item-inner";
      inner.appendChild(a);
      if (kids) inner.appendChild(kids);
      wrap.appendChild(inner);
      li.appendChild(wrap);
    } else {
      li.appendChild(a);
      if (kids) li.appendChild(kids);
    }
    ul.appendChild(li);
  });
  return ul;
}
function _renderNavGroups(cur) {
  var frag = document.createDocumentFragment();
  NAV_GROUPS.forEach(function (g) {
    var wrap = document.createElement("div");
    wrap.className = "nav-group";
    var label = document.createElement("p");
    label.className = "nav-group-label";
    label.textContent = g.label;
    wrap.appendChild(label);
    wrap.appendChild(_renderNavTree(g.entries, cur));
    frag.appendChild(wrap);
  });
  return frag;
}
// Stagger the top-level .item-wrap elements open (top→bottom) or closed
// (bottom→top — the reverse). Motion only: each wrap reveals via its own
// grid-row height, not opacity.
function _staggerNavItems(root, open) {
  var wraps = root.querySelectorAll(".nav-group > ul.nav-tree > li > .item-wrap");
  var STEP = 26;
  var n = wraps.length;
  wraps.forEach(function (w, i) {
    w.style.transitionDelay = (open ? i : (n - 1 - i)) * STEP + "ms";
  });
  root.classList.toggle("items-open", open);
}
// Render the full docs nav into root: a fixed search header (.nav-search-wrap)
// above a SEPARATELY scrolling, edge-faded list (.nav-scroll) — split into two
// elements so the search header can never be faded/overlapped by the list
// scrolling behind it (a mask-image on a shared ancestor would affect both).
function buildDocsNav(root, cur, withSearch) {
  root.innerHTML = "";
  var scroll = document.createElement("div");
  scroll.className = "nav-scroll";
  var treeWrap = document.createElement("div");
  if (withSearch) {
    var sWrap = document.createElement("div");
    sWrap.className = "nav-search-wrap";
    var s = document.createElement("input");
    s.type = "search";
    s.className = "nav-search";
    s.placeholder = "Search docs…";
    s.setAttribute("aria-label", "Search docs");
    sWrap.appendChild(s);
    root.appendChild(sWrap);
    s.addEventListener("input", function () {
      var q = s.value.trim().toLowerCase();
      treeWrap.innerHTML = "";
      if (!q) { treeWrap.appendChild(_renderNavGroups(cur)); return; }
      var matches = _flatNav()
        .filter(function (e) { return e.title.toLowerCase().indexOf(q) !== -1; })
        .map(function (e) { return { title: e.title, path: e.path }; });
      treeWrap.appendChild(_renderNavTree(matches, cur));
    });
  }
  treeWrap.appendChild(_renderNavGroups(cur));
  scroll.appendChild(treeWrap);
  root.appendChild(scroll);
}
// Update which sidebar link is marked current (used by client-side nav).
function setDocsCurrent(cur) {
  document.querySelectorAll(".rail-menu-inner a").forEach(function (a) {
    a.classList.toggle("current", a.getAttribute("href") === cur);
  });
}
window.YB = { buildDocsNav: buildDocsNav, setCurrent: setDocsCurrent };

// Retrigger the click-pulse animation on an element (restart if mid-animation).
function railPulse(el) {
  el.classList.remove("rail-pulse");
  void el.offsetWidth; // force reflow so the animation restarts
  el.classList.add("rail-pulse");
  el.addEventListener("animationend", function handler() {
    el.classList.remove("rail-pulse");
    el.removeEventListener("animationend", handler);
  });
}

var _railSetOpen = null;

// Mobile = the rail renders as the compact top bar + full-bleed sheet. The
// sheet behaves like a menu (opens on demand, closes on selection), unlike
// the desktop rail, which is a persistent sidebar that stays open while
// browsing docs.
function _isMobileNav() {
  return window.matchMedia && window.matchMedia("(max-width:900px)").matches;
}

// Re-sync the rail's "active" state + dropdown default-open behaviour to
// whichever page is CURRENTLY showing — called after every client-side nav.
function syncRailToPage() {
  var toggle = document.getElementById("rail-docs");
  var rail = document.getElementById("rail");
  var onDocs = document.body.classList.contains("docs");
  if (toggle) toggle.classList.toggle("active", onDocs);
  if (!rail || !_railSetOpen) return;
  // Arriving at ANY docs page via ANY link (not just the DOCS button) opens
  // the sidebar by default — same rule as the initial direct-load check:
  // open unless the user has explicitly closed it earlier this session.
  if (onDocs && !rail.classList.contains("expanded") && !_isMobileNav()) {
    try {
      var pref = sessionStorage.getItem("yb-rail-open");
      if (pref === null || pref === "1") {
        // Defer to the next frame: this runs in the same tick as a large
        // outerHTML content swap, and triggering the item-wraps' own
        // 0fr→1fr reveal synchronously alongside that heavy reflow left
        // every item's computed height stuck at 0 (the browser's grid
        // intrinsic-size measurement got interrupted mid-flush). Giving the
        // content swap's layout a frame to settle first fixes it.
        requestAnimationFrame(function () { _railSetOpen(true, false); });
      }
    } catch (e) {}
  }
  // The reverse: arriving at a NON-docs page (e.g. YEABOI back to landing)
  // must never leave the dropdown sitting open over that content. Normally
  // the brand-click handler already closes it before navigating, but this is
  // a hard backstop for every OTHER way you can end up on a non-docs page
  // (back/forward, a stray link) — without it, an already-open rail from
  // browsing docs just stays open, showing whatever the dropdown had at the
  // moment of navigation (which, if that itself got caught by an interrupted
  // transition, is exactly the "open but empty" bug this recovers from).
  if (!onDocs && rail.classList.contains("expanded")) {
    _railSetOpen(false, false);
  }
}

function buildRail() {
  var rail = document.getElementById("rail");
  var toggle = document.getElementById("rail-docs");
  var menu = document.getElementById("rail-menu");
  if (!rail || !toggle) return;

  var brand = rail.querySelector(".brand");
  var inner = menu ? menu.querySelector(".rail-menu-inner") : null;

  function setOpen(open, remember) {
    rail.classList.toggle("expanded", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (remember) { try { sessionStorage.setItem("yb-rail-open", open ? "1" : "0"); } catch (e) {} }
    // Sidebar items stagger open top→bottom and close bottom→top (the
    // reverse) — this IS the entrance/exit motion, no separate container fade.
    if (inner) _staggerNavItems(inner, open);
  }
  _railSetOpen = setOpen;

  // YEABOI: always a client-side trip home. If the dropdown is open, let it
  // shrink back (its own motion) — fully decoupled from the content swap,
  // which starts immediately and fades in on its own independent timeline.
  if (brand) {
    brand.addEventListener("mousedown", function () { railPulse(brand); });
    brand.addEventListener("click", function (e) {
      e.preventDefault();
      try { sessionStorage.removeItem("yb-rail-open"); } catch (e2) {}
      if (rail.classList.contains("expanded")) setOpen(false, true);
      if (document.body.classList.contains("docs")) {
        navigateTo("/", true);
      } else {
        if (window.__lenis) window.__lenis.scrollTo(0, { duration: 0.9 });
        else window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });
  }

  // DOCS: from anywhere outside the docs, it's a client-side trip INTO the
  // docs (the dropdown opens itself once there, no reload). From inside the
  // docs, it just toggles the persistent dropdown open/closed.
  toggle.addEventListener("mousedown", function () { railPulse(toggle); });
  toggle.addEventListener("click", function () {
    if (!document.body.classList.contains("docs")) {
      try { sessionStorage.setItem("yb-rail-open", "1"); } catch (e) {}
      navigateTo("/docs/index.html", true);
      return;
    }
    setOpen(!rail.classList.contains("expanded"), true);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && rail.classList.contains("expanded")) setOpen(false, true);
  });

  // Mobile: selecting a page collapses the sheet (not remembered as a
  // user-close — the desktop pref is untouched).
  if (menu) {
    menu.addEventListener("click", function (e) {
      if (_isMobileNav() && e.target.closest && e.target.closest("a")) {
        setOpen(false, false);
      }
    });
  }

  // Mobile: tapping anywhere outside the open sheet (the blurred, inert page
  // area) closes it — the bar's own controls are excluded.
  document.addEventListener("click", function (e) {
    if (!_isMobileNav() || !rail.classList.contains("expanded")) return;
    var t = e.target;
    if (t.closest && (t.closest("#rail-menu") || t.closest("#rail-docs") || t.closest(".brand"))) return;
    setOpen(false, false);
  });

  // The dropdown's CONTENTS are page-independent (the same tree everywhere) —
  // build it once. Only which link is "current" changes, per navigation.
  if (inner) {
    buildDocsNav(inner, window.location.pathname, true);
    var scroll = inner.querySelector(".nav-scroll");
    if (scroll) {
      scroll.setAttribute("data-lenis-prevent", ""); // let it scroll over Lenis
      // Fade both scroll edges of the LIST only — the search header lives
      // outside .nav-scroll entirely, so it's never faded or overlapped.
      var updateFade = function () {
        var atTop = scroll.scrollTop <= 1;
        var atBottom = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 1;
        scroll.style.setProperty("--ft", atTop ? "0px" : "24px");
        scroll.style.setProperty("--fb", atBottom ? "0px" : "28px");
      };
      scroll.addEventListener("scroll", updateFade, { passive: true });
      window.addEventListener("resize", updateFade, { passive: true });
      requestAnimationFrame(updateFade);
      setTimeout(updateFade, 620);
    }
  }

  toggle.classList.toggle("active", document.body.classList.contains("docs"));

  // On a fresh direct load of a docs URL (bookmark, search engine, refresh),
  // the sidebar opens by default and plays its stagger-in — but this initial
  // check only ever runs once, here; subsequent client-side navigation is
  // governed by syncRailToPage()'s stricter check instead. Mobile skips it:
  // the sheet is a menu, not a persistent sidebar.
  if (document.body.classList.contains("docs") && !_isMobileNav()) {
    try {
      var pref = sessionStorage.getItem("yb-rail-open");
      if (pref === null || pref === "1") setOpen(true, false);
    } catch (e) {}
  }
}
