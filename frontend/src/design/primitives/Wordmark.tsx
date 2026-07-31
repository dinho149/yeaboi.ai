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
import { BLOCK_GLYPHS, SHADOW_GLYPHS } from '../../types/enums';
import styles from './primitives.module.css';

/**
 * Which of the product's two display faces to set the word in.
 *
 * `block` is the compact two-row face — the default, and the only one that fits
 * furniture like the board app bar or the deck's footer credit.
 *
 * `shadow` is the six-row ANSI Shadow face the splash screen and the mode intros
 * use in the terminal. It is three times the height, so it belongs only where the
 * wordmark *is* the thing on the page: the join gate and an export's masthead.
 * If the word cannot be set in it (see {@link renderShadowWordmark}) the
 * component falls back to `block` rather than rendering nothing.
 */
export type WordmarkVariant = 'block' | 'shadow';

export interface WordmarkProps {
  /** The word to set. Case-insensitive — the table is uppercase only. */
  text: string;
  /** Display face. Defaults to the compact two-row one. */
  variant?: WordmarkVariant;
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

const BLANK = ' ';

/**
 * Ceiling on a shadow wordmark's cell size.
 *
 * Six rows at this size is roughly a 72px block. Without a cap a short word in a
 * wide column — `plan` is only 32 cells — would scale up until it dwarfed the
 * heading under it.
 *
 * It was `1.1rem` and a ~105px block, which on a 1440×900 laptop put the
 * masthead at a shade over 280px: a quarter of the window spent on the word
 * `RETRO`. The face is *display* type — its whole job is to be recognised, and
 * it is recognised at a glance well below the size at which it starts competing
 * with the page. Six rows still read cleanly at 12px a cell; the rows are solid
 * blocks, not strokes, and they hold long after ordinary type would go muddy.
 *
 * A custom property rather than a constant so a surface can tune the ceiling
 * from CSS. It is the *only* knob: the size itself is computed inline (see
 * below), which beats any class a caller could write, so a `font-size` in a
 * consumer's stylesheet would be silently ignored.
 */
const SHADOW_MAX = 'var(--wordmark-max, 0.75rem)';

/**
 * How far `right` may slide left before a cell of one touches a cell of the other.
 *
 * Mirrors `_fit()` in `ui/shared/_ansi_font.py`. This is figlet's "fitting"
 * layout, and it is not cosmetic: an `L` followed by a `Y` nests by three columns
 * because the L's low bar sits under the Y's open arms. Without it most words
 * come out byte-identical and a handful come out wider than the terminal draws
 * them — which is why `SHADOW_SAMPLES` pins `analysis` specifically.
 *
 * `left` is the word assembled so far rather than just the previous glyph, so a
 * narrow letter like `I` can be nested past by its successor.
 */
function fit(left: readonly string[], right: readonly string[]): number {
  const limit = Math.min(left[0]?.length ?? 0, right[0]?.length ?? 0);
  for (let shift = limit; shift > 0; shift--) {
    let clear = true;
    for (let row = 0; row < left.length && clear; row++) {
      const tail = (left[row] ?? '').slice((left[row] ?? '').length - shift);
      const head = (right[row] ?? '').slice(0, shift);
      for (let i = 0; i < shift; i++) {
        if (tail[i] !== BLANK && head[i] !== BLANK) {
          clear = false;
          break;
        }
      }
    }
    if (clear) return shift;
  }
  return 0;
}

/**
 * Set `text` in the six-row ANSI Shadow face, or return `null`.
 *
 * Mirrors `render_shadow_text()`. `null` — rather than a gap or a dropped letter
 * — is the answer for any character the face has no glyph for, because the two
 * faces are interchangeable at the call site: half a word in the tall face would
 * read as damage, where the whole word in the small one just reads as smaller.
 */
export function renderShadowWordmark(text: string): string[] | null {
  let rows: string[] = [];
  let previous = '';

  for (const ch of text.toUpperCase()) {
    const glyph = SHADOW_GLYPHS[ch];
    if (!glyph) return null;
    if (rows.length === 0) {
      rows = [...glyph];
      previous = ch;
      continue;
    }

    // Nothing fits across a space: the space glyph is all blanks, so `fit` would
    // slide its neighbour straight through it and close the gap between words.
    const shift = ch === BLANK || previous === BLANK ? 0 : fit(rows, glyph);
    previous = ch;

    rows = rows.map((row, i) => {
      const cell = glyph[i] ?? '';
      const head = row.slice(0, row.length - shift);
      const overlap = row.slice(row.length - shift);
      let zone = '';
      for (let j = 0; j < shift; j++) {
        // `fit` guarantees at most one side has ink in any overlapping cell.
        zone += cell[j] === BLANK ? (overlap[j] ?? BLANK) : (cell[j] ?? BLANK);
      }
      return head + zone + cell.slice(shift);
    });
  }

  return rows;
}

/**
 * The face's shadow characters, as opposed to the letter bodies.
 *
 * `ansi_shadow` draws each letter as a solid block body (`█`) with a box-drawing
 * drop shadow down and to the right. A terminal renders both in one colour and
 * gets away with it, because at a 7px cell the strokes read as an edge. In a
 * browser at hero size they are the same saturated accent as the body, and the
 * word reads as a wire outline rather than as letters with a shadow behind them.
 */
const SHADOW_STROKES = /^[\u2550-\u256c]+$/;

/**
 * Split a row into body and shadow runs so the shadow can be tinted back.
 *
 * Display-time only, exactly like {@link blankShades}: `renderShadowWordmark`
 * stays byte-identical to Python's, because that is what the parity samples
 * measure. The characters themselves are untouched and still live in the DOM, so
 * the word remains selectable and findable — the whole reason this face is text
 * and not an image.
 */
function tintShadows(row: string, keyPrefix: string) {
  const runs: { text: string; shadow: boolean }[] = [];
  for (const ch of row) {
    const shadow = SHADOW_STROKES.test(ch);
    const last = runs[runs.length - 1];
    if (last && last.shadow === shadow) last.text += ch;
    else runs.push({ text: ch, shadow });
  }
  return runs.map((run, i) =>
    run.shadow ? (
      <span key={`${keyPrefix}-${i}`} className={styles['wordmarkStroke']}>
        {run.text}
      </span>
    ) : (
      run.text
    )
  );
}

export function Wordmark({ text, variant = 'block', label, size, className }: WordmarkProps) {
  // A shadow word that has no setting falls back rather than disappearing. The
  // known callers all pass words the face covers (there is a Python test listing
  // them), so this is the safety net for an export that picks a new one.
  const shadow = variant === 'shadow' ? renderShadowWordmark(text) : null;

  if (shadow) {
    // Sized inline, against the wrapper's inline size, for two reasons. The face
    // is a fixed grid of cells, so the only way six rows of a long word fit a
    // phone is to derive the size from the space available and the cell count —
    // and doing that in CSS would need the count anyway. Because an inline style
    // beats any class, a consumer cannot size this face from a stylesheet at
    // all; `--wordmark-max` is the supported knob.
    //
    // 1.55 ≈ 1 / 0.645, the widest character advance in the --font-mono stack.
    // It under-fills slightly, which is what keeps a hairline off the last column.
    const cells = shadow[0]?.length ?? 1;
    const fontSize = size ?? `min(${SHADOW_MAX}, calc(100cqi * 1.55 / ${cells}))`;
    return (
      <span className={styles['wordmarkFit']}>
        <pre
          className={cx(styles['wordmark'], styles['wordmarkShadow'], className)}
          style={{ fontSize }}
          role="img"
          aria-label={label ?? text}
        >
          {shadow.map((row, i) => (
            <>
              {i > 0 ? '\n' : null}
              {tintShadows(row, `r${i}`)}
            </>
          ))}
        </pre>
      </span>
    );
  }

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
