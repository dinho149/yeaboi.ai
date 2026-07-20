# Website Redesign + Official Docs Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign `docs/index.html` to a professional, non-AI-generated finish, build an ~18-page docs site at `yeaboi.ai/docs/`, slim `README.md` down to install + a feature list, and migrate all 535 `# See README:` code comments to `# See docs:`.

**Architecture:** Zero-build static HTML site (matches the repo's existing GitHub Pages setup). One shared token stylesheet (`docs/assets/site.css`) drives the landing page and every docs page; a docs-only layer (`docs/docs/assets/docs.css`) adds the sidebar/TOC/code-block shell; a single JS data file (`docs/docs/assets/nav.js`) is the one source of truth for the docs page tree, rendering the sidebar + powering a lightweight title/heading search on every docs page.

**Tech Stack:** Plain HTML5, CSS custom properties, vanilla JS (no framework, no build step, no dependencies). Full source spec: `docs/superpowers/specs/2026-07-20-website-and-docs-site-design.md`.

## Global Constraints

- No build tooling (Node, bundlers, static site generators, markdown renderers). Every page is hand-written HTML.
- No webfonts loaded — use the system font stack (`var(--sans)` / `var(--mono)`) everywhere. This is a deliberate choice from the design spec, not an oversight.
- Every internal link and asset reference across the whole site uses an **absolute path from the domain root** (`/docs/...`), never a relative path — `docs/docs/modes/*.html` pages are nested one level deeper than `docs/docs/*.html`, and relative paths silently break when a page moves between those levels.
- Palette is grayscale + exactly one accent color (`--accent: #6a93bd`, a desaturated slate blue). No purple/indigo, no amber, no neon, no glow/box-shadow-as-glow. Borders are hairline (`--border`) and shape is sharp (`--radius: 6px`, never pill-shaped).
- Docs pages get **no scroll-triggered animation** — instant navigation only, per the spec's "Developer Docs" motion budget.
- Static-content tasks (every docs/HTML page) are "tested" via the **Definition of Done checklist** in that task, not an automated test suite — there is no test framework for static HTML in this repo. The one task with real automated tests is Task 22 (code comment migration), which is verified with `make lint` / `make test`.
- Content tasks that adapt `README.md` cite exact line ranges to read from — read the file at those lines before writing the page; do not invent content that isn't in the source range (except Tasks 10/11, which are new content for previously-undocumented modes, sourced from `CLAUDE.md` as cited).

---

## File Structure

```
docs/
  index.html                       # Task 3 — redesigned landing page
  assets/
    site.css                       # Task 1 — shared tokens + base components
    site.js                        # Task 1 — copy-button behavior (shared)
  docs/
    index.html                     # Task 4
    getting-started.html           # Task 5
    cli-reference.html             # Task 6
    modes/
      index.html                   # Task 7
      planning.html                # Task 8
      standup.html                 # Task 9
      retro.html                   # Task 10 (new content)
      performance.html             # Task 11 (new content)
      reporting.html               # Task 12
      team-analysis.html           # Task 13
    integrations-exports.html      # Task 14
    session-management.html        # Task 15
    tools.html                     # Task 16
    architecture.html              # Task 17
    scrum-standards.html           # Task 18
    deployment.html                # Task 19
    development.html               # Task 20
    assets/
      nav.js                       # Task 2 — page tree + sidebar/TOC/search renderer
      docs.css                     # Task 2 — sidebar/TOC/code-block layer
README.md                          # Task 21 — slimmed
src/yeaboi/**/*.py                 # Task 22 — comment migration (535 occurrences)
CLAUDE.md                          # Task 23 — Learning-First rule updated
```

---

### Task 1: Shared design tokens (`site.css` + `site.js`)

**Files:**
- Create: `docs/assets/site.css`
- Create: `docs/assets/site.js`

**Interfaces:**
- Produces: CSS custom properties (`--bg`, `--bg-raised`, `--border`, `--border-strong`, `--text`, `--text-muted`, `--text-dim`, `--accent`, `--accent-dim`, `--mono`, `--sans`, `--radius`, `--maxw`) and base classes (`.wrap`, `.navbar`, `.btn`, `.btn-primary`, `.btn-secondary`, `.card`, `.badge`, `.term`, `.term-bar`, `.term-body`, `.codeblock`, `.copy`) consumed by every later task.
- `site.js` produces one behavior: any element matching `.copy[data-copy]` copies its `data-copy` value to the clipboard and shows "copied ✓" for 1.6s (same UX the current landing page already has, generalized so docs code blocks can reuse it).

- [ ] **Step 1: Write `docs/assets/site.css`**

```css
/* yeaboi.ai — shared design tokens + base components
   Palette: grayscale + one accent. No gradients, no glow, no pill shapes. */
:root{
  --bg:#0a0a0a;
  --bg-raised:#111214;
  --bg-sunken:#070707;
  --border:rgba(255,255,255,.08);
  --border-strong:rgba(255,255,255,.16);
  --text:#e8e8e6;
  --text-muted:#9a9ea3;
  --text-dim:#6b6e73;
  --accent:#6a93bd;
  --accent-dim:rgba(106,147,189,.14);
  --mono:ui-monospace,"SFMono-Regular",Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --radius:6px;
  --maxw:1120px;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:var(--sans); line-height:1.6; -webkit-font-smoothing:antialiased;
}
a{color:var(--accent); text-decoration:none}
a:hover{text-decoration:underline}
::selection{background:var(--accent-dim)}
code,pre,kbd{font-family:var(--mono)}
.wrap{max-width:var(--maxw); margin:0 auto; padding:0 20px}

/* ---- top nav (site-wide) ---- */
.navbar{
  position:sticky; top:0; z-index:20; background:rgba(10,10,10,.92);
  backdrop-filter:blur(6px); border-bottom:1px solid var(--border);
}
.navbar .wrap{display:flex; align-items:center; justify-content:space-between; height:56px}
.navbar .brand{font-family:var(--mono); font-weight:600; color:var(--text); font-size:.95rem}
.navbar .brand:hover{text-decoration:none; color:var(--accent)}
.navbar nav{display:flex; gap:22px; align-items:center; font-size:.9rem}
.navbar nav a{color:var(--text-muted)}
.navbar nav a:hover{color:var(--text)}
.navbar nav a.current{color:var(--text); border-bottom:1px solid var(--accent)}

/* ---- buttons ---- */
.btn{
  display:inline-flex; align-items:center; gap:8px; border-radius:var(--radius);
  border:1px solid var(--border-strong); padding:9px 16px; font-size:.92rem;
  font-family:var(--sans); cursor:pointer; transition:border-color .15s,color .15s;
  background:transparent; color:var(--text);
}
.btn:hover{border-color:var(--accent); color:var(--accent); text-decoration:none}
.btn-primary{background:var(--text); color:var(--bg); border-color:var(--text)}
.btn-primary:hover{background:var(--accent); border-color:var(--accent); color:var(--bg)}

/* ---- cards ---- */
.card{
  background:var(--bg-raised); border:1px solid var(--border); border-radius:var(--radius);
  padding:20px 22px;
}
.badge{
  display:inline-block; font-family:var(--mono); font-size:.72rem; letter-spacing:.03em;
  text-transform:uppercase; color:var(--text-muted); border:1px solid var(--border-strong);
  border-radius:var(--radius); padding:2px 8px;
}

/* ---- terminal window chrome (used by hero demo + code samples) ---- */
.term{
  background:var(--bg-raised); border:1px solid var(--border); border-radius:var(--radius);
  overflow:hidden;
}
.term-bar{
  display:flex; align-items:center; gap:8px; padding:10px 14px;
  background:var(--bg-sunken); border-bottom:1px solid var(--border);
}
.dot{width:11px;height:11px;border-radius:50%; background:var(--border-strong)}
.term-title{
  margin-left:8px; font-family:var(--mono); font-size:.78rem; color:var(--text-dim);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.term-body{padding:22px clamp(16px,4vw,32px) 26px}

/* ---- code blocks + copy button (shared by landing + docs) ---- */
.codeblock{
  display:flex; align-items:center; justify-content:space-between; gap:12px;
  background:var(--bg-sunken); border:1px solid var(--border); border-radius:var(--radius);
  padding:12px 12px 12px 16px; font-family:var(--mono); font-size:.92rem;
}
.codeblock code{color:var(--text)}
.copy{
  border:1px solid var(--border-strong); background:var(--bg-raised); color:var(--text-muted);
  font-family:var(--mono); font-size:.76rem; border-radius:var(--radius); padding:6px 10px;
  cursor:pointer; transition:.15s; white-space:nowrap;
}
.copy:hover{color:var(--text); border-color:var(--accent)}
.copy.done{color:var(--accent); border-color:var(--accent)}

@media (max-width:640px){
  .navbar nav{gap:14px; font-size:.84rem}
}
```

- [ ] **Step 2: Write `docs/assets/site.js`**

```javascript
// Shared copy-button behavior for any `.copy[data-copy]` element.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.copy[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var text = btn.getAttribute('data-copy');
      navigator.clipboard.writeText(text).then(function () {
        var prev = btn.textContent;
        btn.textContent = 'copied ✓';
        btn.classList.add('done');
        setTimeout(function () {
          btn.textContent = prev;
          btn.classList.remove('done');
        }, 1600);
      });
    });
  });
});
```

- [ ] **Step 3: Verify**

Open `docs/assets/site.css` and `docs/assets/site.js` and confirm: no `linear-gradient`/`radial-gradient` on any background, no `@keyframes` named `shine` or similar looping animation, no color literal outside the `:root` token list is used for text/background/border (everything routes through a `var(--...)`).

- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/assets/site.css docs/assets/site.js
git commit -m "feat(site): add shared design tokens and copy-button behavior"
```

---

### Task 2: Docs shell foundation (`nav.js` + `docs.css`)

**Files:**
- Create: `docs/docs/assets/nav.js`
- Create: `docs/docs/assets/docs.css`

**Interfaces:**
- Consumes: `--bg`, `--bg-raised`, `--border`, `--border-strong`, `--text`, `--text-muted`, `--text-dim`, `--accent`, `--mono`, `--sans`, `--radius` from `docs/assets/site.css` (Task 1).
- Produces: a global `NAV` array and a `renderDocsShell()` function (called by every docs page's inline `<script>`) that (1) injects the sidebar into `#docs-sidebar`, (2) builds the right-hand TOC into `#docs-toc` from every `h2[id]`/`h3[id]` inside `<article>`, (3) wires the sidebar's `#docs-search` input to filter `NAV` entries by substring match (case-insensitive) against `title`. Every later docs-page task relies on this exact function name and these exact element IDs.

- [ ] **Step 1: Write `docs/docs/assets/nav.js`**

```javascript
// Single source of truth for the docs page tree. Sidebar, active-page
// highlighting, and the title search all read from this array — adding a
// docs page means adding one entry here, not editing every page.
const NAV = [
  { title: "Docs Home", path: "/docs/docs/index.html" },
  { title: "Getting Started", path: "/docs/docs/getting-started.html" },
  { title: "CLI Reference", path: "/docs/docs/cli-reference.html" },
  {
    title: "Modes",
    path: "/docs/docs/modes/index.html",
    children: [
      { title: "Planning", path: "/docs/docs/modes/planning.html" },
      { title: "Daily Standup", path: "/docs/docs/modes/standup.html" },
      { title: "Retro", path: "/docs/docs/modes/retro.html" },
      { title: "Performance", path: "/docs/docs/modes/performance.html" },
      { title: "Reporting", path: "/docs/docs/modes/reporting.html" },
      { title: "Team Analysis", path: "/docs/docs/modes/team-analysis.html" },
    ],
  },
  { title: "Integrations & Exports", path: "/docs/docs/integrations-exports.html" },
  { title: "Session Management", path: "/docs/docs/session-management.html" },
  { title: "Tools", path: "/docs/docs/tools.html" },
  { title: "Architecture & Concepts", path: "/docs/docs/architecture.html" },
  { title: "Scrum Standards", path: "/docs/docs/scrum-standards.html" },
  { title: "Deployment", path: "/docs/docs/deployment.html" },
  { title: "Development", path: "/docs/docs/development.html" },
];

function _flatNav() {
  const out = [];
  NAV.forEach((entry) => {
    out.push(entry);
    (entry.children || []).forEach((c) => out.push(c));
  });
  return out;
}

function _renderTree(entries, currentPath) {
  const ul = document.createElement("ul");
  ul.className = "docs-tree";
  entries.forEach((entry) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = entry.path;
    a.textContent = entry.title;
    if (entry.path === currentPath) a.classList.add("current");
    li.appendChild(a);
    if (entry.children && entry.children.length) {
      li.appendChild(_renderTree(entry.children, currentPath));
    }
    ul.appendChild(li);
  });
  return ul;
}

function _renderSidebar(currentPath) {
  const root = document.getElementById("docs-sidebar");
  if (!root) return;
  root.innerHTML = "";

  const search = document.createElement("input");
  search.type = "search";
  search.id = "docs-search";
  search.placeholder = "Search docs…";
  search.setAttribute("aria-label", "Search docs");
  root.appendChild(search);

  const treeWrap = document.createElement("div");
  treeWrap.id = "docs-tree-wrap";
  treeWrap.appendChild(_renderTree(NAV, currentPath));
  root.appendChild(treeWrap);

  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    if (!q) {
      treeWrap.innerHTML = "";
      treeWrap.appendChild(_renderTree(NAV, currentPath));
      return;
    }
    const matches = _flatNav().filter((e) => e.title.toLowerCase().includes(q));
    treeWrap.innerHTML = "";
    treeWrap.appendChild(_renderTree(matches, currentPath));
  });
}

function _renderTOC() {
  const tocRoot = document.getElementById("docs-toc");
  const article = document.querySelector("article");
  if (!tocRoot || !article) return;
  const headings = article.querySelectorAll("h2[id], h3[id]");
  if (!headings.length) {
    tocRoot.remove();
    return;
  }
  const ul = document.createElement("ul");
  headings.forEach((h) => {
    const li = document.createElement("li");
    li.className = h.tagName === "H3" ? "toc-sub" : "";
    const a = document.createElement("a");
    a.href = "#" + h.id;
    a.textContent = h.textContent;
    li.appendChild(a);
    ul.appendChild(li);
  });
  const label = document.createElement("p");
  label.className = "toc-label";
  label.textContent = "On this page";
  tocRoot.appendChild(label);
  tocRoot.appendChild(ul);
}

function renderDocsShell() {
  const currentPath = window.location.pathname;
  _renderSidebar(currentPath);
  _renderTOC();
}

document.addEventListener("DOMContentLoaded", renderDocsShell);
```

- [ ] **Step 2: Write `docs/docs/assets/docs.css`**

```css
/* Docs-only layer: sidebar + TOC + article typography + code blocks.
   Loaded after /docs/assets/site.css on every docs page. */
body.docs{ margin:0 }
.docs-shell{
  display:grid;
  grid-template-columns:240px minmax(0,1fr) 220px;
  gap:40px;
  max-width:1360px;
  margin:0 auto;
  padding:32px 24px 80px;
}
.docs-sidebar{ position:sticky; top:72px; align-self:start; max-height:calc(100vh - 96px); overflow-y:auto }
.docs-sidebar input#docs-search{
  width:100%; background:var(--bg-raised); border:1px solid var(--border);
  border-radius:var(--radius); color:var(--text); font-family:var(--sans);
  font-size:.86rem; padding:8px 10px; margin-bottom:14px;
}
.docs-sidebar input#docs-search:focus{ outline:none; border-color:var(--accent) }
ul.docs-tree{ list-style:none; margin:0; padding:0; font-size:.88rem }
ul.docs-tree ul.docs-tree{ margin-left:14px; margin-top:2px }
.docs-tree li{ margin:2px 0 }
.docs-tree a{ display:block; padding:5px 8px; border-radius:var(--radius); color:var(--text-muted) }
.docs-tree a:hover{ color:var(--text); text-decoration:none; background:var(--bg-raised) }
.docs-tree a.current{ color:var(--text); background:var(--accent-dim); border-left:2px solid var(--accent) }

.docs-content article{ max-width:74ch }
.docs-content h1{ font-size:1.9rem; letter-spacing:-.01em; margin:0 0 8px }
.docs-content h2{ font-size:1.35rem; margin:40px 0 12px; padding-top:8px; border-top:1px solid var(--border) }
.docs-content h2:first-of-type{ border-top:none; padding-top:0 }
.docs-content h3{ font-size:1.08rem; margin:26px 0 10px }
.docs-content p{ color:var(--text); margin:0 0 14px }
.docs-content p.lede{ color:var(--text-muted); font-size:1.02rem }
.docs-content ul,.docs-content ol{ color:var(--text); padding-left:1.3em; margin:0 0 14px }
.docs-content li{ margin:4px 0 }
.docs-content code{ background:var(--bg-raised); border:1px solid var(--border); border-radius:4px; padding:1px 5px; font-size:.88em }
.docs-content pre{
  background:var(--bg-sunken); border:1px solid var(--border); border-radius:var(--radius);
  padding:14px 16px; overflow-x:auto; margin:0 0 16px;
}
.docs-content pre code{ background:none; border:none; padding:0; font-size:.88rem }
.docs-content table{ width:100%; border-collapse:collapse; margin:0 0 20px; font-size:.9rem }
.docs-content th,.docs-content td{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--border) }
.docs-content th{ color:var(--text-muted); font-weight:600 }

.docs-toc{ position:sticky; top:72px; align-self:start; font-size:.82rem }
.docs-toc .toc-label{ color:var(--text-dim); font-family:var(--mono); text-transform:uppercase; letter-spacing:.04em; font-size:.72rem; margin:0 0 10px }
.docs-toc ul{ list-style:none; margin:0; padding:0; border-left:1px solid var(--border) }
.docs-toc li{ margin:0 }
.docs-toc li.toc-sub{ padding-left:12px }
.docs-toc a{ display:block; padding:4px 0 4px 12px; color:var(--text-muted); margin-left:-1px; border-left:1px solid transparent }
.docs-toc a:hover{ color:var(--text); text-decoration:none; border-left-color:var(--border-strong) }

@media (max-width:1000px){
  .docs-shell{ grid-template-columns:1fr; }
  .docs-toc{ display:none }
  .docs-sidebar{ position:static; max-height:none; margin-bottom:24px }
}
```

- [ ] **Step 3: Verify**

`node -c docs/docs/assets/nav.js` (or, if Node isn't available, visually re-read the file) to confirm it's syntactically valid JS. Confirm every `path` in `NAV` matches a file this plan creates (cross-check against the File Structure section above — there should be exactly 17 leaf entries: 1 home + 1 getting-started + 1 cli-reference + 1 modes index + 6 mode pages + 8 remaining top-level pages).

- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/assets/nav.js docs/docs/assets/docs.css
git commit -m "feat(docs): add docs shell — nav data, sidebar/TOC renderer, docs layer CSS"
```

---

### Task 3: Redesign the landing page

**Files:**
- Modify: `docs/index.html` (full rewrite of `<head>`/`<style>`/body — keep the file, replace contents)

**Interfaces:**
- Consumes: `docs/assets/site.css`, `docs/assets/site.js` (Task 1).
- Content source: current `docs/index.html` (read it first — it has the accurate copy for the hero tagline, six modes, install command, and footer links; only the *structure and styling* change, not the product copy) plus `docs/demo.gif` (existing asset, keep using it).

- [ ] **Step 1: Read the current file for copy to preserve**

Run: `cat docs/index.html` — note the hero tagline copy, the six mode descriptions (Planning/Analysis/Standup/Retro/Performance/Reporting), the install commands, and footer links. Note: the existing six-mode grid actually already covers Retro and Performance in its copy (mode cards 03–06 are Standup/Retro/Performance/Reporting) even though the README never did — reuse that existing accurate copy.

- [ ] **Step 2: Rewrite `docs/index.html`**

Replace the entire file with a version that:
- Links `docs/assets/site.css` and (deferred) `docs/assets/site.js` instead of an inline `<style>`/`<script>` block.
- Adds a `.navbar` (Task 1 provides the class) with the `🤙 yeaboi` wordmark on the left and `Docs` / `GitHub ↗` / `PyPI ↗` links on the right, `Docs` pointing to `/docs/docs/index.html`.
- Replaces the centered hero with an **asymmetric** two-column hero inside a `.wrap`: left column (~55%) has the tagline (as plain heading text, no gradient/shine), the lede paragraph, and the install `.codeblock` + `.copy` button (Task 1's shared component — drop the old page-local `.cmdbox`/`.install` CSS and inline copy-handler script, since `site.css`/`site.js` now provide it); right column (~45%) is the `.term` terminal-chrome frame containing `<img src="demo.gif">` (move the demo image here instead of its own later section — one strong hero visual beats a decorative ASCII wordmark).
- Removes the animated ASCII wordmark (`pre.wordmark`), the `shine` keyframe, and the `radial-gradient` page background entirely — replace the wordmark with a plain `<p class="badge">🤙 yeaboi</p>`-style eyebrow above the tagline, or omit it (the navbar wordmark is enough branding).
- Keeps the six-mode grid section, restyled with `.card` (Task 1) instead of the old `.mode` gradient-hover cards — same six mode descriptions, same 2-column responsive grid, drop the `transform:translateY` hover lift (keep only a border-color hover shift, consistent with "no glow" motion budget).
- Keeps the "Get started" 3-step section, restyled with `.card`/`.codeblock`.
- Footer: keep the same three links (GitHub / PyPI / Docs-note), but the "Docs ↗" link now points to `/docs/docs/index.html` instead of the GitHub README anchor, and add a note like `Full documentation → yeaboi.ai/docs`.
- Delete the standalone `<section id="demo">` (its content moved into the hero in this step) — do not leave a duplicate.

- [ ] **Step 3: Verify**

Open `docs/index.html` in a browser (or `python3 -m http.server` from the `docs/` directory and visit `localhost:8000`) and confirm: no console errors, the copy button works, the `Docs` nav link is present (it will 404 until Task 4 lands — that's expected at this point), no gradient/glow/shine is visible anywhere, hero is asymmetric not centered.

- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/index.html
git commit -m "redesign: rebuild landing page on shared design tokens, remove AI-generated visual tells"
```

---

### Task 4: Docs home page

**Files:**
- Create: `docs/docs/index.html`

**Interfaces:**
- Consumes: `/docs/assets/site.css`, `/docs/docs/assets/docs.css`, `/docs/docs/assets/nav.js` (calls `renderDocsShell()` on load via the shared `DOMContentLoaded` listener already registered inside `nav.js` — the page just needs to include the script tag, no manual call required).
- Content source: `README.md` lines 114–155 (`## ✨ Features`) for the summary blurbs; the six mode names/descriptions from Task 3's landing page copy.

- [ ] **Step 1: Read source content**

Run: `sed -n '114,155p' README.md` for the Features list to summarize into a short intro + section link cards.

- [ ] **Step 2: Write `docs/docs/index.html`**

Use this exact page shell (every subsequent docs-page task reuses this `<head>`/wrapper structure, swapping only `<title>`, the meta description, and the `<article>` contents):

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Docs — yeaboi</title>
<meta name="description" content="yeaboi documentation: getting started, CLI reference, all six modes, integrations, architecture, and deployment." />
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='20' fill='%230a0a0a'/%3E%3Ctext x='50' y='68' font-size='58' text-anchor='middle' fill='%236a93bd' font-family='monospace'%3EY%3C/text%3E%3C/svg%3E" />
<link rel="stylesheet" href="/docs/assets/site.css" />
<link rel="stylesheet" href="/docs/docs/assets/docs.css" />
</head>
<body class="docs">
<header class="navbar">
  <div class="wrap">
    <a class="brand" href="/docs/index.html">🤙 yeaboi</a>
    <nav>
      <a href="/docs/docs/index.html" class="current">Docs</a>
      <a href="https://github.com/omardin14/yeaboi.ai">GitHub ↗</a>
      <a href="https://pypi.org/project/yeaboi/">PyPI ↗</a>
    </nav>
  </div>
</header>
<div class="docs-shell">
  <aside class="docs-sidebar" id="docs-sidebar"></aside>
  <main class="docs-content">
    <article>
      <h1>yeaboi docs</h1>
      <p class="lede">A team lead's best friend — plans, standups, retros, performance &amp; reporting, right from your terminal.</p>

      <h2 id="start-here">Start here</h2>
      <p>New to yeaboi? Start with <a href="/docs/docs/getting-started.html">Getting Started</a>, then explore the <a href="/docs/docs/modes/index.html">six modes</a>.</p>

      <h2 id="sections">Sections</h2>
      <ul>
        <li><a href="/docs/docs/getting-started.html">Getting Started</a> — install, setup, API keys, intake modes</li>
        <li><a href="/docs/docs/cli-reference.html">CLI Reference</a> — every flag, headless mode, session flags</li>
        <li><a href="/docs/docs/modes/index.html">Modes</a> — Planning, Daily Standup, Retro, Performance, Reporting, Team Analysis</li>
        <li><a href="/docs/docs/integrations-exports.html">Integrations &amp; Exports</a> — Markdown, HTML, Notion, Confluence, Jira, Azure DevOps, JSON</li>
        <li><a href="/docs/docs/session-management.html">Session Management</a> — sessions, Usage page, Settings page</li>
        <li><a href="/docs/docs/tools.html">Tools</a> — all 35 tools and their risk levels</li>
        <li><a href="/docs/docs/architecture.html">Architecture &amp; Concepts</a> — LangGraph architecture, prompt construction, guardrails, multi-provider LLM support</li>
        <li><a href="/docs/docs/scrum-standards.html">Scrum Standards</a> — story format, acceptance criteria, DoD, the intake questionnaire</li>
        <li><a href="/docs/docs/deployment.html">Deployment</a> — AWS Lightsail via OpenClaw, Slack integration</li>
        <li><a href="/docs/docs/development.html">Development</a> — build/test commands, tech stack</li>
      </ul>
    </article>
  </main>
  <nav class="docs-toc" id="docs-toc"></nav>
</div>
<script src="/docs/docs/assets/nav.js"></script>
</body>
</html>
```

- [ ] **Step 3: Verify**

Serve `docs/` locally (`python3 -m http.server` from inside `docs/`) and open `/docs/index.html` — confirm the sidebar renders all 13 top-level entries plus the 6 Modes children, the current page (`Docs Home`) is highlighted, and the search box filters the list when you type e.g. "tool".

- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/index.html
git commit -m "feat(docs): add docs home page"
```

---

### Task 5: Getting Started page

**Files:** Create `docs/docs/getting-started.html`
**Source:** `README.md` lines 156–228 (`## 🏁 Getting Started` incl. Prerequisites/Installation/Setup wizard/API keys) and lines 693–774 (`## 🎯 Intake Modes` incl. Smart/Quick/Standard/Offline import).

- [ ] **Step 1:** Run `sed -n '156,228p;693,774p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/getting-started.html` using the Task 4 page shell (swap `<title>`Getting Started — yeaboi docs`</title>`, meta description, nav `current` class moves off "Docs" onto nothing — top nav only highlights on the docs home vs landing, leave `Docs` as `current` on every docs page). Body: `<h1>Getting Started</h1>`, then `<h2 id="prerequisites">`, `<h2 id="installation">`, `<h2 id="first-run-setup-wizard">`, `<h2 id="api-keys">` (from the Getting Started source range) followed by `<h2 id="intake-modes">` with `<h3>` for Smart/Quick/Standard/Offline (from the Intake Modes source range). Preserve every code block from the source as `<pre><code>`. Preserve the prerequisites/API-key table structure as an HTML `<table>` if the source uses a Markdown table.
- [ ] **Step 3: Verify (Definition of Done)**
  - [ ] `<h1>` is "Getting Started"; every `<h2>` has an `id` attribute (required for the TOC and for `# See docs:` anchors)
  - [ ] All code blocks from the source range are present, none summarized away
  - [ ] No relative links — every `<a href>` starts with `/docs/`, `https://`, or `#`
  - [ ] Page opens in the local server with sidebar highlighting "Getting Started" and TOC listing all `<h2>`/`<h3>` ids
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/getting-started.html
git commit -m "feat(docs): add Getting Started page"
```

---

### Task 6: CLI Reference page

**Files:** Create `docs/docs/cli-reference.html`
**Source:** `README.md` lines 555–692 (`## ⌨️ CLI Reference` through the end of the Music/ffplay subsection, i.e. everything before `## 🎯 Intake Modes`).

- [ ] **Step 1:** Run `sed -n '555,692p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/cli-reference.html` (Task 4 shell, title "CLI Reference — yeaboi docs"). Sections: `<h2 id="interactive-modes">`, `<h2 id="non-interactive-headless">`, `<h2 id="daily-standup-flags">`, `<h2 id="session-management-flags">`, `<h2 id="configuration">`, `<h2 id="questionnaire-export">`, `<h2 id="music-ffplay">`. Any flag table in the source (flag/description pairs) becomes an HTML `<table>` with `<th>Flag</th><th>Description</th>`.
- [ ] **Step 3: Verify (Definition of Done)** — same four checks as Task 5, plus: every CLI flag mentioned in the source (e.g. `--non-interactive`, `--description`, `--resume`, `--dry-run`) appears verbatim in a `<code>` tag on this page.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/cli-reference.html
git commit -m "feat(docs): add CLI Reference page"
```

---

### Task 7: Modes overview page

**Files:** Create `docs/docs/modes/index.html`
**Source:** the six mode descriptions already written for Task 3's landing-page mode grid (Planning/Analysis/Standup/Retro/Performance/Reporting — reuse that copy, it's already accurate and concise) — read `docs/index.html` after Task 3 lands to pull it.

**Note on paths:** this page lives one directory deeper (`docs/docs/modes/`) than Tasks 4–6, but every asset/link in the Task 4 shell already uses absolute `/docs/...` paths, so the exact same `<head>` and `<script src="/docs/docs/assets/nav.js">` block works unchanged — do not switch to relative paths.

- [ ] **Step 1:** Read `docs/index.html`'s six `.card` mode blocks for copy.
- [ ] **Step 2:** Write `docs/docs/modes/index.html` (Task 4 shell, title "Modes — yeaboi docs"). Body: `<h1>Modes</h1>`, a lede paragraph ("Everything a lead does between planning and delivery — six modes, one command."), then six `<h2 id="...">` sections (`planning`, `daily-standup`, `retro`, `performance`, `reporting`, `team-analysis`), each with 2–3 sentences (expand slightly on the landing-page card copy, since this page has more room) and a link to its dedicated page, e.g. `<p><a href="/docs/docs/modes/planning.html">Full Planning docs →</a></p>`.
- [ ] **Step 3: Verify (Definition of Done)** — all six mode names link to the correct dedicated-page paths from `nav.js`'s `Modes.children`; `<h1>`/`<h2>` ids present; no relative links.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/modes/index.html
git commit -m "feat(docs): add Modes overview page"
```

---

### Task 8: Planning mode page

**Files:** Create `docs/docs/modes/planning.html`
**Source:** `README.md` lines 775–837 (`## 🔄 Pipeline` incl. Task enrichment).

- [ ] **Step 1:** Run `sed -n '775,837p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/modes/planning.html` (Task 4 shell, title "Planning — yeaboi docs"). `<h1>Planning</h1>`, `<h2 id="pipeline">` covering the pipeline stages, `<h2 id="task-enrichment">`.
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/modes/planning.html
git commit -m "feat(docs): add Planning mode page"
```

---

### Task 9: Daily Standup mode page

**Files:** Create `docs/docs/modes/standup.html`
**Source:** `README.md` lines 838–889 (`## ☀️ Daily Standup`).

- [ ] **Step 1:** Run `sed -n '838,889p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/modes/standup.html` (Task 4 shell, title "Daily Standup — yeaboi docs"). `<h1>Daily Standup</h1>`, `<h2 id="what-it-does">`, `<h2 id="scheduling">`, `<h2 id="delivery-configuration">`, `<h2 id="exports">`, `<h2 id="try-it">`.
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/modes/standup.html
git commit -m "feat(docs): add Daily Standup mode page"
```

---

### Task 10: Retro mode page (new content)

**Files:** Create `docs/docs/modes/retro.html`
**Source:** no README coverage exists. Write from this accurate summary (drawn from `CLAUDE.md`'s "Retro Mode" section):

> Retro mode is a **collaborative sprint retrospective**. The host opens the Retro page in the yeaboi TUI, which starts a small local web server; teammates open a URL from any browser on the LAN (no install required) and add sticky cards to four grids — *What went well*, *What didn't go well*, *Action items*, *Demos*. Cards update live (polled every 2 seconds). Security: each session gets a random access token plus a short human-friendly join code; card text is length-capped and escaped everywhere it's rendered. After the retro, one LLM call turns the "didn't go well" cards into suggested action items (clearly marked as AI-added). The live board supports emoji reactions, drag-to-reorder, a shared countdown timer, a theme switcher, and ambient background music — all built with zero extra dependencies (Python's standard-library HTTP server). For remote teams, a "Share Remotely" button opens a free Cloudflare quick tunnel so the board is reachable outside the LAN, still token-gated. Every retro auto-exports to Markdown and a self-contained HTML report in `~/.yeaboi/exports/retro/<project>/`.

- [ ] **Step 1:** Write `docs/docs/modes/retro.html` (Task 4 shell, title "Retro — yeaboi docs"). `<h1>Retro</h1>`, then `<h2 id="how-it-works">` (board mechanics, LAN server, live cards), `<h2 id="joining">` (join code / URL / QR code), `<h2 id="ai-action-items">`, `<h2 id="remote-sharing">` (Cloudflare tunnel), `<h2 id="exports">`. Use the summary above as the factual basis — write full prose paragraphs from it, don't paste it verbatim as a blockquote.
- [ ] **Step 2: Verify (Definition of Done)** — same structural checks as Task 5, plus: page does not claim any capability not in the summary above (no invented flags/features).
- [ ] **Step 3: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/modes/retro.html
git commit -m "feat(docs): add Retro mode page (previously undocumented)"
```

---

### Task 11: Performance mode page (new content)

**Files:** Create `docs/docs/modes/performance.html`
**Source:** no README coverage exists. Write from this accurate summary (drawn from `CLAUDE.md`'s "Performance Mode" section):

> Performance mode helps a lead manage each engineer through three connected workflows. **1:1 Prep** pulls an engineer's current and prior-sprint tickets plus any open action items from their last 1:1 and produces talking points, feedback, goals, and gaps. **1:1 Completion** takes a transcript (file import or pasted inline) and produces an email summary plus tracked action items — those items feed back into the *next* prep, closing the loop. **6-Month Review** synthesizes past 1:1s, delivery history from Jira/Azure DevOps, and a competency framework (bundled by default, or a lead's own via `PERFORMANCE_FRAMEWORK_PATH`) into a structured review. The roster is derived from who actually did work in Jira/AzDO, not a headcount number. Every artifact auto-exports to Markdown and HTML in `~/.yeaboi/exports/performance/<engineer>/`, and 1:1 summaries can be emailed via SMTP.

- [ ] **Step 1:** Write `docs/docs/modes/performance.html` (Task 4 shell, title "Performance — yeaboi docs"). `<h1>Performance</h1>`, `<h2 id="roster">`, `<h2 id="one-on-one-prep">`, `<h2 id="one-on-one-completion">`, `<h2 id="six-month-review">`, `<h2 id="exports">`.
- [ ] **Step 2: Verify (Definition of Done)** — same as Task 10.
- [ ] **Step 3: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/modes/performance.html
git commit -m "feat(docs): add Performance mode page (previously undocumented)"
```

---

### Task 12: Reporting mode page

**Files:** Create `docs/docs/modes/reporting.html`
**Source:** `README.md` lines 890–911 (`## 📊 Reporting Mode`).

- [ ] **Step 1:** Run `sed -n '890,911p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/modes/reporting.html` (Task 4 shell, title "Reporting — yeaboi docs"). `<h1>Reporting</h1>` with sections covering the period types (last sprint / last month / whole quarter) and the slide-deck export.
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/modes/reporting.html
git commit -m "feat(docs): add Reporting mode page"
```

---

### Task 13: Team Analysis mode page

**Files:** Create `docs/docs/modes/team-analysis.html`
**Source:** `README.md` lines 912–1045 (`## 🔬 Team Analysis Mode` + `## 🎯 Analysis-Calibrated Planning`).

- [ ] **Step 1:** Run `sed -n '912,1045p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/modes/team-analysis.html` (Task 4 shell, title "Team Analysis — yeaboi docs"). `<h1>Team Analysis</h1>`, `<h2 id="what-gets-analyzed">`, `<h2 id="how-it-works">`, `<h2 id="per-developer-breakdown">`, `<h2 id="analysis-exports">`, then `<h2 id="analysis-calibrated-planning">` with `<h3>` for Intake auto-fill / Team member multi-select / Calibration banners / What gets calibrated / Epic review.
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/modes/team-analysis.html
git commit -m "feat(docs): add Team Analysis mode page"
```

---

### Task 14: Integrations & Exports page

**Files:** Create `docs/docs/integrations-exports.html`
**Source:** `README.md` lines 1046–1175 (`## 📤 Export Formats`).

- [ ] **Step 1:** Run `sed -n '1046,1175p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/integrations-exports.html` (Task 4 shell, title "Integrations & Exports — yeaboi docs"). `<h1>Integrations &amp; Exports</h1>` with `<h2 id="...">` for Markdown, HTML, Notion & Confluence, Chart images, JSON, Jira, Azure DevOps Boards.
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/integrations-exports.html
git commit -m "feat(docs): add Integrations & Exports page"
```

---

### Task 15: Session Management page

**Files:** Create `docs/docs/session-management.html`
**Source:** `README.md` lines 1176–1271 (`## 💾 Session Management` + `## 📊 Usage Page` + `## ⚙️ Settings Page`).

- [ ] **Step 1:** Run `sed -n '1176,1271p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/session-management.html` (Task 4 shell, title "Session Management — yeaboi docs"). `<h1>Session Management</h1>`, `<h2 id="directory-structure">`, `<h2 id="resume-a-session">`, `<h2 id="list-sessions">`, `<h2 id="delete-sessions">`, `<h2 id="auto-pruning">`, `<h2 id="usage-page">`, `<h2 id="settings-page">`.
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/session-management.html
git commit -m "feat(docs): add Session Management page"
```

---

### Task 16: Tools page

**Files:** Create `docs/docs/tools.html`
**Source:** `README.md` lines 1272–1386 (`## 🔧 Tools` + Tool risk levels).

- [ ] **Step 1:** Run `sed -n '1272,1386p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/tools.html` (Task 4 shell, title "Tools — yeaboi docs"). `<h1>Tools</h1>`, one `<h2 id="...">` per tool category as grouped in the source (GitHub, Azure DevOps, Jira, Confluence, Notion, Codebase, Calendar, LLM tools), ending with `<h2 id="tool-risk-levels">`. Render the tool list as a `<table>` if the source uses one.
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5, plus: all 35 tools mentioned in the source Features count are represented (spot-check the total count against `## 🔧 Tools`'s own tally).
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/tools.html
git commit -m "feat(docs): add Tools page"
```

---

### Task 17: Architecture & Concepts page

**Files:** Create `docs/docs/architecture.html`
**Source:** `README.md` lines 1387–1505 (`## 🏗️ Architecture`), 1937–2042 (`## 🧪 Prompt Construction` + `## 🛡️ Guardrails` + `## 🤖 Multi-Provider LLM Support`), 2224–2334 (`## 📘 Agentic Blueprint Reference`).

- [ ] **Step 1:** Run `sed -n '1387,1505p;1937,2042p;2224,2334p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/architecture.html` (Task 4 shell, title "Architecture & Concepts — yeaboi docs"). `<h1>Architecture &amp; Concepts</h1>` with, in this order: `<h2 id="four-layers">`, `<h2 id="design-principles">`, `<h2 id="agent-graph">`, `<h2 id="node-descriptions">`, `<h2 id="prompt-construction">`, `<h2 id="guardrails">`, `<h2 id="multi-provider-llm-support">`, `<h2 id="agentic-blueprint-reference">` (this last section is the longest — preserve its "Quick Reference — All APIs" subsection as `<h3>`).
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5. This is the largest single page in the site (~330 combined source lines) — double check no source subsection was silently dropped by diffing the `<h2>`/`<h3>` id list against every `###`/`##` heading in the three source ranges.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/architecture.html
git commit -m "feat(docs): add Architecture & Concepts page"
```

---

### Task 18: Scrum Standards page

**Files:** Create `docs/docs/scrum-standards.html`
**Source:** `README.md` lines 1506–1936 (`## 📝 Project Intake Questionnaire` + `## 📏 Scrum Standards`).

- [ ] **Step 1:** Run `sed -n '1506,1936p' README.md` and read the output (this is the largest source range — it includes detailed story-format and acceptance-criteria examples; preserve every example verbatim as a code block, they're the most-referenced part of this section).
- [ ] **Step 2:** Write `docs/docs/scrum-standards.html` (Task 4 shell, title "Scrum Standards — yeaboi docs"). `<h1>Scrum Standards</h1>`, `<h2 id="intake-questionnaire">` with `<h3>` for Questionnaire Flow / Adaptive Behavior / Intake Summary Output, then `<h2 id="issue-hierarchy">`, `<h2 id="user-stories">`, `<h2 id="acceptance-criteria">`, `<h2 id="definition-of-done-user-story">`, `<h2 id="definition-of-done-spike">`, `<h2 id="sprint-ceremonies">`, `<h2 id="backlog-health">`, `<h2 id="story-splitting-guidelines">`.
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5, plus: every fenced code example from the source (user story format, AC format) is present as a `<pre><code>` block, not paraphrased.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/scrum-standards.html
git commit -m "feat(docs): add Scrum Standards page"
```

---

### Task 19: Deployment page

**Files:** Create `docs/docs/deployment.html`
**Source:** `README.md` lines 229–554 (`## ☁️ Deploy on AWS Lightsail (OpenClaw)`, all 14 numbered subsections including Slack integration).

- [ ] **Step 1:** Run `sed -n '229,554p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/deployment.html` (Task 4 shell, title "Deployment — yeaboi docs"). `<h1>Deployment</h1>`, one `<h2 id="step-N-...">` per numbered subsection (Create the instance, Attach a static IP, Enable Bedrock access, ... through Next steps), preserving every shell command as a code block and every screenshot reference (`docs/lightsail-setup/*.png`) as an `<img>` with `src="/docs/lightsail-setup/<file>.png"` (absolute path — these existing images live at `docs/lightsail-setup/`, one level up from the new `docs/docs/` pages).
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5, plus: every `<img src>` resolves to a file that actually exists in `docs/lightsail-setup/` (cross-check filenames with `ls docs/lightsail-setup/`).
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/deployment.html
git commit -m "feat(docs): add Deployment page"
```

---

### Task 20: Development page

**Files:** Create `docs/docs/development.html`
**Source:** `README.md` lines 2043–2223 (`## 🛠️ Development` + `## 🧪 Evaluation & Testing` + `## ⚙️ Tech Stack`).

- [ ] **Step 1:** Run `sed -n '2043,2223p' README.md` and read the output.
- [ ] **Step 2:** Write `docs/docs/development.html` (Task 4 shell, title "Development — yeaboi docs"). `<h1>Development</h1>`, `<h2 id="commands">` (the `make` command table), `<h2 id="project-structure">`, `<h2 id="testing-conventions">`, `<h2 id="environment-variables">`, `<h2 id="git-conventions">`, `<h2 id="evaluation-testing">`, `<h2 id="tech-stack">`. Add one closing paragraph: "For contributor workflow — worktrees, hooks, CI conventions — see `CLAUDE.md` in the repo root." (this page is deliberately slimmer than `CLAUDE.md`, per the spec's non-goals).
- [ ] **Step 3: Verify (Definition of Done)** — same checks as Task 5.
- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add docs/docs/development.html
git commit -m "feat(docs): add Development page"
```

---

### Task 21: Slim down README.md

**Files:** Modify `README.md`

**Interfaces:**
- Consumes: nothing new (final state links to `https://yeaboi.ai/docs/`, which exists after Tasks 1–20).

- [ ] **Step 1: Read the current file's structure**

Run: `grep -n "^## " README.md` — confirm line numbers match what Tasks 5–20 already used (re-run if any earlier task's commit changed line numbers — it shouldn't have, since only `docs/` files and this file are touched, and this is the only task touching `README.md`).

- [ ] **Step 2: Rewrite `README.md`**

Keep: the `<div align="center">` banner block (lines 1–29, banner image + title + pitch + badges), the demo GIF block (lines 22–29), the `## 🚀 Quick Start` section in full (lines 31–83, install/pipx/from-source/headless instructions — this is exactly the install content the trimmed README should keep).

Replace everything from `## 📑 Table of Contents` (line 84) through the end of the file with:

```markdown
## ✨ Features

🖥️ **Full-screen TUI** — Animated splash, mode selection, pipeline progress, dark/light themes
🧠 **Smart Intake** — Extracts answers from your project description, asks only what's missing
🔄 **Six modes, one command** — Planning, Daily Standup, Retro, Performance, Reporting, Team Analysis
🔌 **35 tools** — GitHub, Azure DevOps, Jira, Confluence, Notion, local codebase scanning, and more
📤 **5 export formats** — Markdown, HTML, JSON, Jira sync, Azure DevOps Boards sync
🤖 **4 LLM providers** — Claude (default), GPT, Gemini, AWS Bedrock
💾 **Session persistence** — SQLite-backed sessions that survive terminal restarts
🛡️ **Guardrails** — Input/output validation, human-in-the-loop review at every stage

## 📖 Full Documentation

Getting started, the full CLI reference, every mode in depth, integrations, architecture,
and deployment guides: **[yeaboi.ai/docs](https://yeaboi.ai/docs/)**

## 📄 License

MIT — see [LICENSE](LICENSE).
```

(Keep the original License section's exact wording if it says more than "MIT — see LICENSE"; run `sed -n '2335,$p' README.md` first and preserve any extra content there verbatim instead of the placeholder above.)

- [ ] **Step 3: Verify**

Run: `wc -l README.md` — should now be roughly 100–120 lines (down from ~2340). Run: `grep -c "^## " README.md` — should be 3 (Quick Start, Features, Full Documentation) plus License, i.e. 4.

- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add README.md
git commit -m "docs: slim README to install + feature list, link to yeaboi.ai/docs"
```

---

### Task 22: Migrate `# See README:` code comments to `# See docs:`

**Files:**
- Modify: every file under `src/` matching `# See README:` (535 occurrences per the spec's count — re-verify the live count first, the repo may have drifted since the spec was written)

**Interfaces:**
- Consumes: nothing from earlier tasks (purely mechanical, could in principle run in parallel with Tasks 1–21, but run it after so the final commit sequence tells a clean story).

- [ ] **Step 1: Confirm the current count and pattern**

```bash
cd ~/yeaboi.ai
grep -rn "See README:" src/ | wc -l
grep -rhon '# See README: "[^"]*"' src/ | sort -u | head -30
```

Confirm every occurrence matches the pattern `# See README: "<Section Name>"` (optionally followed by ` — <note>`). If any occurrence doesn't match this shape (e.g. missing the `#` prefix, or phrased differently), note it — it needs a manual look in Step 3, not the blanket substitution.

- [ ] **Step 2: Run the substitution**

```bash
cd ~/yeaboi.ai
grep -rl "See README:" src/ | xargs sed -i '' 's/# See README:/# See docs:/g'
```

(macOS `sed -i ''` syntax — this repo's dev environment is macOS per `CLAUDE.md`.)

- [ ] **Step 3: Verify the substitution**

```bash
grep -rn "See README:" src/ | wc -l      # expect: 0
grep -rn "See docs:" src/ | wc -l        # expect: same count as Step 1's total
```

If Step 1 flagged any non-matching occurrences, fix those by hand now (open the file, edit the specific line) — the blanket `sed` above only touches the literal string `# See README:` so non-conforming lines are untouched, not corrupted.

- [ ] **Step 4: Run the test suite**

```bash
cd ~/yeaboi.ai
make lint
make test-fast
```

Expected: both pass. A comment-only change should never break either — if `make lint` fails, it means a line's length grew past the ruff line-length limit (120) after the `README` → `docs` swap shortened it (unlikely, but check); if `make test-fast` fails, the failure is unrelated to this change and must be investigated separately (do not proceed to Step 5 until both are green).

- [ ] **Step 5: Commit**

```bash
cd ~/yeaboi.ai
git add -u src/
git commit -m "refactor: migrate # See README comments to # See docs, pointing at yeaboi.ai/docs"
```

---

### Task 23: Update CLAUDE.md's Learning-First Development rule

**Files:** Modify `CLAUDE.md`

- [ ] **Step 1: Locate the rule**

Run: `grep -n "See README" CLAUDE.md`

- [ ] **Step 2: Edit the rule**

In the "REQUIRED: Learning-First Development" section, change:

```markdown
1. **ALWAYS add `# See README: <section name>` comments** when introducing a LangGraph or LangChain concept for the first time in a file. Cross-reference the relevant README section so the developer can look up the theory.
```

to:

```markdown
1. **ALWAYS add `# See docs: <section name>` comments** when introducing a LangGraph or LangChain concept for the first time in a file. Cross-reference the relevant page at https://yeaboi.ai/docs/ (or the local `docs/docs/` source) so the developer can look up the theory.
```

Also update the "Key README sections to reference" list immediately below it — rename to "Key docs sections to reference" and update each named section to match its new docs page if the name changed (e.g. "Architecture" stays "Architecture" on `architecture.html`; "The ReAct Loop" and "Agentic Blueprint Reference" both live under `architecture.html`'s Agentic Blueprint Reference section now).

- [ ] **Step 3: Verify**

```bash
grep -n "See README" CLAUDE.md   # expect: 0 matches
grep -n "See docs" CLAUDE.md     # expect: matches in the rule + section list
```

- [ ] **Step 4: Commit**

```bash
cd ~/yeaboi.ai
git add CLAUDE.md
git commit -m "docs: update Learning-First Development rule to reference docs site instead of README"
```

---

### Task 24: Whole-site verification pass

**Files:** none created — this is a verification-only task.

- [ ] **Step 1: Check every internal link resolves**

```bash
cd ~/yeaboi.ai
python3 - <<'EOF'
import re, pathlib

root = pathlib.Path("docs")
html_files = list(root.rglob("*.html"))
existing = {str(p.relative_to(".")) for p in pathlib.Path(".").rglob("*")}

broken = []
for f in html_files:
    text = f.read_text()
    for href in re.findall(r'href="(/docs/[^"#]+)"', text):
        target = "." + href  # href is absolute-from-root, e.g. /docs/docs/tools.html
        if not pathlib.Path(target).exists():
            broken.append((str(f), href))
    for src in re.findall(r'src="(/docs/[^"]+)"', text):
        target = "." + src
        if not pathlib.Path(target).exists():
            broken.append((str(f), src))

if broken:
    print(f"BROKEN LINKS: {len(broken)}")
    for f, link in broken:
        print(f"  {f} -> {link}")
else:
    print("All internal links resolve.")
EOF
```

Expected: `All internal links resolve.` If any are reported broken, fix the offending file (typo'd path, or a Task 4–20 page that forgot the leading `/docs/` prefix) and re-run.

- [ ] **Step 2: Check every docs page includes the shell script tag**

```bash
grep -L 'assets/nav.js' docs/docs/*.html docs/docs/modes/*.html
```

Expected: no output (every file matched `nav.js`). Any file listed here is missing the shell — go back to its task and add `<script src="/docs/docs/assets/nav.js"></script>` before `</body>`.

- [ ] **Step 3: Spot-check in a browser**

```bash
cd ~/yeaboi.ai/docs && python3 -m http.server 8000
```

Visit `http://localhost:8000/` (landing), click through to `Docs`, click through the sidebar to at least 3 different pages including one under `Modes`, confirm search filters the sidebar, confirm TOC populates per-page, confirm no gradient/glow/shine is visible anywhere on any page. Stop the server (`Ctrl+C`) when done.

- [ ] **Step 4: Final commit (if Step 1 or 2 required fixes)**

```bash
cd ~/yeaboi.ai
git add -A
git commit -m "fix(docs): resolve broken links / missing shell script found in verification pass"
```

(Skip this step if Steps 1–2 found nothing to fix.)

---

## Self-Review Notes

- **Spec coverage:** every spec goal (landing redesign, docs site, README slim-down, comment migration, CLAUDE.md update) maps to at least one task above (Goal 1 → Task 3; Goal 2 → Tasks 1,2,4–20; Goal 3 → Task 21; Goal 4 → Tasks 22–23). The spec's risk about Retro/Performance having no source content is handled explicitly in Tasks 10–11 with a written factual summary instead of a source line range.
- **Placeholder scan:** no task step says "add appropriate content" without a source (every content task cites exact `README.md` line ranges, or for Tasks 10/11 a written summary); no "TBD"/"similar to Task N" shortcuts.
- **Type/interface consistency:** `renderDocsShell()`, `NAV`, `#docs-sidebar`, `#docs-toc`, `#docs-search`, `.copy[data-copy]` are defined once (Tasks 1–2) and referenced identically by name in every later task; the `nav.js` `NAV` array (Task 2) is cross-checked against the File Structure section's full file list.
