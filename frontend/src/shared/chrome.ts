/**
 * The page furniture every yeaboi browser surface wears.
 *
 * Mirrors `build_chrome` in `src/yeaboi/web/brand.py` field for field. It began
 * as `ExportChrome`, describing only the static reports; it moved here when the
 * live boards, the share gate and the slide deck stopped each inventing their
 * own header. One shape, one builder on the Python side, one component drawing
 * it — which is the whole point: a masthead that drifts is a masthead that was
 * defined in five places.
 *
 * The wire-shape guard (`tests/unit/test_web_wire_shapes.py`) asserts every
 * surface's snapshot `satisfies` this, so a field dropped in Python fails
 * `npm run typecheck` rather than blanking a header months later.
 */

/** Page furniture, identical for every surface. */
export interface PageChrome {
  /**
   * Mode key, set as `[data-mode]` on `<html>` by the server and driving
   * `--accent`. Not every surface owns a distinct TUI accent — roadmap borrows
   * planning's, anonymize analysis' — so this is the *accent* to wear, not a
   * claim about which mode produced the page. `accent_mode()` in `web/brand.py`
   * is the one place that knows the mapping.
   */
  mode: string;
  /** Terminal title-bar text, conventionally `yeaboi — <mode>`. */
  frame: string;
  /** Word set in the display face. Kept short: it is six rows tall and wide. */
  wordmark: string;
  title: string;
  subtitle?: string;
  /** Header eyebrows, `[label, value]`. Each must say something true about the run. */
  facts?: Array<[string, string]>;
  badges?: string[];
  /** Sticky contents links, `[sectionId, label]`. Omit for a single-screen page. */
  nav?: Array<[string, string]>;
  footer: string;
}
