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
var _duckPoof = false;

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
        // along it. Chase it to a viewport edge and it poofs to the far side.
        var ft = rectOf('footer');
        if (ft) {
          spots.push({ s: ft.top + st - vh * 0.85, pos: function () {
            var base = 0.74 * (vw - dw);
            var fy = ft.top - dh + 4;
            var fx = base + _duckFlee;
            var minX = 10, maxX = vw - dw - 10;
            if (_mX >= 0 && !_duckPoof) {
              var cx = fx + dw / 2, cy = fy + dh / 2;
              var ddx = cx - _mX, ddy = cy - _mY;
              var dist = Math.sqrt(ddx * ddx + ddy * ddy);
              if (dist < 150) {
                _duckFlee += (ddx >= 0 ? 1 : -1) * (150 - dist) * 0.4;
                fx = Math.min(maxX, Math.max(minX, base + _duckFlee));
                _duckFlee = fx - base;
                // cornered with the cursor still closing in → escape poof
                if ((fx === minX || fx === maxX) && dist < 85) {
                  _duckPoof = true;
                  var el = document.getElementById('duck-walker');
                  if (el) el.classList.add('teleporting');
                  (function (corneredLeft) {
                    setTimeout(function () {
                      _duckFlee = (corneredLeft ? maxX : minX) - base;
                      if (el) el.classList.remove('teleporting');
                      _duckPoof = false;
                      updateScrollProgress();
                    }, 160);
                  })(fx === minX);
                }
              }
            }
            return [fx, fy];
          } });
        }
        spots.sort(function (a, b) { return a.s - b.s; });
        spotIdx = 0;
        for (var i = 0; i < spots.length; i++) if (st >= spots[i].s) spotIdx = i;
        var xy = spots[spotIdx].pos();
        dx = xy[0]; dy = xy[1];
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
        _duckFlee = 0; // fresh perch, no leftover chase offset
        duck.classList.add('teleporting');
        duck.classList.remove('walking');
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
      } else {
        // in-spot movement: walk the surface, waddling while moving
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
  updateScrollProgress();
  // re-render once the hero's staggered entrance animations finish — rects
  // measured mid-entrance (content rises 22px) would leave the duck perched
  // slightly below its surface until the first scroll/mouse event
  setTimeout(updateScrollProgress, 1500);

  // ---- persistent rail shell ----
  buildRail();

  // ---- on-page TOC (docs) + scroll reveals — re-run after every nav too ----
  renderTOC();
  rescanReveals();
  initHeroDemo();
  initPipeCarousel();

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
    .then(function (r) { return r.text(); })
    .then(function (html) {
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var newPage = doc.getElementById('page-content');
      var curPage = document.getElementById('page-content');
      if (!newPage || !curPage) { window.location.href = url; return; }

      if (push) history.pushState({}, '', target.href);
      document.title = doc.title || document.title;
      document.body.className = doc.body.className; // carries the 'docs' scope class

      curPage.outerHTML = newPage.outerHTML;
      var imported = document.getElementById('page-content');

      if (window.__lenis) window.__lenis.scrollTo(0, { immediate: true });
      else window.scrollTo(0, 0);

      renderTOC();
      rescanReveals();
      initHeroDemo();
      initPipeCarousel();
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
// six modes the visitor can step through by hand, same as arrow-key
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
