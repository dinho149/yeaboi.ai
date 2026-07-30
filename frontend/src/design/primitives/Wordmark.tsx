/**
 * A word set in the product's own display typeface.
 *
 * yeaboi's title face is not a font file — it is a table of block characters
 * (`█ ▀ ▄ ░`) that the TUI has always used to set mode titles, and that
 * yeaboi.ai recreates in its hero. Reusing it here is what makes a tunnel page
 * recognisable as the same product the host is running in their terminal, and
 * it is the one display treatment nothing else on the web has.
 *
 * It costs no bytes. There is no webfont: the glyphs are literal characters in
 * the shipped HTML, so they are selectable, searchable, translatable by the
 * browser's find-in-page, and they scale with the type scale rather than with a
 * raster asset. The table comes from `types/enums.ts`, generated out of
 * `ui/shared/_ascii_font.py` — see `scripts/gen_web_types.py`.
 *
 * Two things are load-bearing in the CSS (`.wordmark` in primitives.module.css)
 * and will silently ruin it if changed: the font must be monospace, and
 * `letter-spacing` must be zero. The glyphs are drawn as a grid of cells, so any
 * tracking at all opens hairline gaps straight through the middle of a letter.
 */

import { cx } from '../../runtime/cx';
import { BLOCK_GLYPHS } from '../../types/enums';
import styles from './primitives.module.css';

export interface WordmarkProps {
  /** The word to set. Case-insensitive — the table is uppercase only. */
  text: string;
  /**
   * Accessible name. Defaults to `text`.
   *
   * The rendered glyphs are decorative geometry to a screen reader — "RETRO"
   * comes out as a stream of block characters — so the `<pre>` carries a real
   * label and hides its contents.
   */
  label?: string;
  /** Font size for one glyph cell. Anything in the type scale, or a length. */
  size?: string;
  className?: string | undefined;
}

/**
 * Render `text` as the two block-glyph rows.
 *
 * Mirrors `render_ascii_text()` exactly, including the fallback: a character
 * with no glyph becomes a three-cell gap rather than being dropped, so a word
 * containing one still lines up.
 */
/**
 * Blank the shade characters for display.
 *
 * `░` is the font's *background* cell. A terminal draws it dim against the
 * panel, so it reads as the space around a letter. A `<pre>` cannot dim one
 * character inside a text node, so it comes out at the full weight of the
 * accent and the word looks damaged — and only on the letters that use it,
 * which in "yeaboi" is the Y alone, so it reads as a defect rather than a
 * texture.
 *
 * Applied at render time only. {@link renderWordmark} stays byte-identical to
 * `render_ascii_text()`, because that is what the cross-language parity test
 * measures and what a caller inspecting the glyphs would expect.
 */
function blankShades(row: string): string {
  // A /g regex rather than replaceAll: the bundles target ES2020, which
  // older phones on a tunnel still are, and replaceAll is ES2021.
  return row.replace(/░/g, ' ');
}

export function renderWordmark(text: string): [string, string] {
  let top = '';
  let bottom = '';
  for (const ch of text.toUpperCase()) {
    const glyph = BLOCK_GLYPHS[ch];
    if (glyph) {
      top += `${glyph[0]} `;
      bottom += `${glyph[1]} `;
    } else {
      top += '   ';
      bottom += '   ';
    }
  }
  return [top.trimEnd(), bottom.trimEnd()];
}

export function Wordmark({ text, label, size, className }: WordmarkProps) {
  const [top, bottom] = renderWordmark(text);
  return (
    <pre
      className={cx(styles['wordmark'], className)}
      style={size ? { fontSize: size } : undefined}
      role="img"
      aria-label={label ?? text}
    >
      {blankShades(top)}
      {'\n'}
      {blankShades(bottom)}
    </pre>
  );
}
