# Website redesign + official docs site

Date: 2026-07-20
Status: Approved

## Problem

`docs/index.html` (the yeaboi.ai landing page, published via GitHub Pages) reads as
AI-generated: centered hero over a radial-gradient background, animated gradient
"shine" text, glow effects. There is no official documentation site — the README
(`README.md`, ~110KB / ~2340 lines / 26 sections) is the only reference, which makes
it unwieldy as a README and means casual visitors to the GitHub repo are faced with a
wall of text instead of a quick pitch.

Two shipped features — **Retro Mode** and **Performance Mode** (see `CLAUDE.md`) —
currently have **no documentation at all**, in the README or anywhere else.

## Goals

1. Redesign the landing page to a professional finish — remove every AI-generated
   visual tell (gradient blobs, shine animation, glow, centered-hero-over-gradient).
2. Build a proper multi-page docs site at `yeaboi.ai/docs/`, hand-rolled static HTML
   matching the landing page's design system, covering everything currently in the
   README plus the two undocumented modes.
3. Trim `README.md` to a banner, one-paragraph pitch, install/quickstart, a slim
   feature list, and a link to the docs site.
4. Migrate all 535 `# See README: "<section>"` code comments (across `src/yeaboi/`)
   to `# See docs: "<section>"`, and update `CLAUDE.md`'s Learning-First Development
   rule to reference the docs site instead of the README.

## Non-goals

- No build tooling (Node, static site generator). The repo is Python-only with a
  zero-build static site today; that constraint is preserved.
- No change to GitHub Pages configuration (still serves from `docs/`, still uses the
  `docs/CNAME` custom domain).
- No change to `CLAUDE.md`'s contributor-facing automation docs (worktrees, hooks,
  orchestration conventions) — those stay in `CLAUDE.md`, not the public docs site.
- Full-text search is out of scope; docs get a lightweight nav-data-driven filter
  instead (see below).

## Visual system

Applies to the landing page and every docs page — one shared design language.

- **Color**: near-black background (`#0a0a0a`), off-white text, grayscale UI
  elements, and exactly **one** desaturated slate-blue accent used sparingly (links,
  the active nav item, the single primary CTA). No purple/indigo, no amber, no
  neon, no glow — the current site's biggest "AI-generated" tell is its gradient
  palette, so the replacement is deliberately restrained.
- **Type**: system font stack throughout (no webfont loading — keeps the
  zero-dependency, zero-network-request ethos, and avoids the Inter/Roboto tell by
  not loading any webfont). Monospace (`ui-monospace` stack) is reserved for
  code/CLI/terminal chrome, not used as the primary display face. Hierarchy comes
  from weight and size, not typeface switching.
- **Borders/shape**: hairline 1px borders, small/sharp corners (no pill shapes).
  Interactive elements get a border-color shift on hover/focus instead of a glow.
- **Layout**: asymmetric hero (headline + CTA on one side, the real terminal demo
  GIF on the other) — not centered text over a gradient.
- **Motion**: near-static. Hover states and copy-button feedback only. No
  scroll-triggered reveals on docs pages (a reader is searching, not browsing); the
  landing page may use a single low-key fade-in on first load, nothing looping.

Design tokens live in one shared stylesheet (`docs/assets/site.css`) consumed by
both the landing page and every docs page, so the look only needs tuning in one
place.

## Site structure

```
docs/                           (GitHub Pages source — unchanged root)
  index.html                    (landing page, redesigned; adds nav bar → /docs/docs/)
  CNAME                         (unchanged: yeaboi.ai)
  assets/
    site.css                    (shared design tokens + component styles)
  docs/                         (new docs subsite, served at yeaboi.ai/docs/)
    index.html                  (docs home: overview + link cards into each section)
    getting-started.html
    cli-reference.html
    modes/
      index.html                (modes overview)
      planning.html
      standup.html
      retro.html                 (NEW — no prior README coverage)
      performance.html           (NEW — no prior README coverage)
      reporting.html
      team-analysis.html         (covers Team Analysis + Analysis-Calibrated Planning)
    integrations-exports.html
    session-management.html      (sessions, Usage page, Settings page)
    tools.html
    architecture.html            (architecture, prompt construction, guardrails,
                                   multi-provider LLM support, agentic blueprint ref)
    scrum-standards.html         (Scrum standards + intake questionnaire)
    deployment.html              (AWS Lightsail/OpenClaw + Slack)
    development.html             (build/test commands, tech stack — slim, contributor-
                                   facing; links back to CLAUDE.md for deep conventions)
    assets/
      nav.js                     (single source of truth for the sidebar tree; also
                                   drives the search filter)
      docs.css                   (docs-shell-specific styles: sidebar, TOC, code
                                   blocks — layered on top of site.css)
```

~18 docs pages total. Each page is hand-written static HTML sharing a common shell
(sidebar nav + content pane + sticky right-hand TOC), per the "Developer Docs"
archetype: no hero, no scroll animation, instant navigation, search as the primary
interaction.

### Shared sidebar / search, without a build step

`nav.js` holds the page tree (title, path, section headings) as a plain JS array.
Every docs page includes it via `<script src="/docs/docs/assets/nav.js">` and calls
a small render function that injects the sidebar `<nav>` and populates a search
input that filters that same array by substring match across page titles and
headings. Adding a new docs page means adding one entry to `nav.js`, not editing 18
files. This is browser-side templating, not a build step — no compiler, no Node.

## README changes

`README.md` keeps: banner image, title, one-paragraph pitch, badges, the "Quick
Start" section (install/setup/run), and a slim feature bullet list (condensed from
today's ~20-item list to the essentials). Everything else — CLI reference, all mode
deep-dives, export formats, session management, tools, architecture, deployment,
development — is replaced with a single "Full documentation" section linking to
`https://yeaboi.ai/docs/`.

## Code comment migration

All 535 occurrences of `# See README: "<Section Name>" — <note>` across `src/`
become `# See docs: "<Section Name>" — <note>` — a mechanical text substitution
(the `# See README:` prefix is textually consistent per the CLAUDE.md convention).
Section names referenced in comments must exist as headings somewhere in the new
docs site; any comment whose section no longer has a home (fully removed content)
gets its section name updated to point at the new page/heading that covers that
concept.

`CLAUDE.md`'s "REQUIRED: Learning-First Development" rule 1 changes from:
> ALWAYS add `# See README: <section name>` comments...

to:
> ALWAYS add `# See docs: <section name>` comments, cross-referencing the relevant
> page at https://yeaboi.ai/docs/...

## Section → page mapping (README → docs)

| README section(s) | Docs page |
|---|---|
| Getting Started, Intake Modes | `getting-started.html` |
| CLI Reference | `cli-reference.html` |
| Pipeline (Planning) | `modes/planning.html` |
| Daily Standup | `modes/standup.html` |
| *(none — new)* | `modes/retro.html` |
| *(none — new)* | `modes/performance.html` |
| Reporting Mode | `modes/reporting.html` |
| Team Analysis Mode, Analysis-Calibrated Planning | `modes/team-analysis.html` |
| Export Formats | `integrations-exports.html` |
| Session Management, Usage Page, Settings Page | `session-management.html` |
| Tools | `tools.html` |
| Architecture, Prompt Construction, Guardrails, Multi-Provider LLM Support, Agentic Blueprint Reference | `architecture.html` |
| Scrum Standards, Project Intake Questionnaire | `scrum-standards.html` |
| Deploy on AWS Lightsail (OpenClaw) | `deployment.html` |
| Development, Evaluation & Testing, Tech Stack | `development.html` |

## Risks

- **Content migration scale**: ~2300 lines of README content being rewritten into
  ~18 styled HTML pages is the bulk of the work. Mitigated by a shared page shell
  (sidebar/TOC/code-block styles built once) so per-page work is content-only.
- **535 comment migration**: purely mechanical (consistent `# See README: "X"`
  format), but must be verified with `make lint`/`make test` after, since these are
  comments inside otherwise-untouched Python files — a bad substitution (e.g.
  breaking a docstring) would be a regression.
- **New content for Retro/Performance modes**: these sections have no prior README
  text to adapt, so they're written fresh from `CLAUDE.md`'s existing (accurate,
  detailed) descriptions of those packages.
