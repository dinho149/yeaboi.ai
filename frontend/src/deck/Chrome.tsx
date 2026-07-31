/**
 * The deck's persistent furniture: where you are, and how to move.
 *
 * All mono, all `--dim`, all sitting on the edges — a projected slide should be
 * the content and a thin frame that tells a latecomer what they are looking at.
 * The progress indicator is a flat accent rule rather than the two-stop
 * gradient it replaced: a gradient on a 4px bar reads as a smear at the back of
 * a room, and it was the only place in the deck that used one.
 */

import { Duck, Wordmark, type DuckState } from '../design/primitives';
import type { Theme } from '../runtime/theme';
import { Popover, ThemeSwitcher } from '../shared';
import styles from './deck.module.css';

export interface ControlsProps {
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  onTheme: () => void;
  /** Name of the palette the T key will switch to — the button's tooltip. */
  nextThemeName: string;
  /** The site palette in force, and how to change it. */
  siteTheme: Theme;
  onSiteTheme: (next: Theme) => void;
}

export function Controls({
  index,
  total,
  onPrev,
  onNext,
  onTheme,
  nextThemeName,
  siteTheme,
  onSiteTheme,
}: ControlsProps) {
  const progress = total > 1 ? (index / (total - 1)) * 100 : 100;
  return (
    <div className={styles['chrome']}>
      <div className={styles['progress']}>
        <div className={styles['bar']} style={{ width: `${progress}%` }} />
      </div>
      <div className={styles['controls']}>
        <button type="button" onClick={onPrev} disabled={index === 0} aria-label="Previous slide">
          ‹
        </button>
        {/* Not aria-live: the slide itself is announced, and a screen reader
            reading "4 / 11" on top of every slide change is pure noise. */}
        <span className={styles['counter']}>
          {index + 1} / {total}
        </span>
        <button type="button" onClick={onNext} disabled={index >= total - 1} aria-label="Next slide">
          ›
        </button>
        {/* Two pickers, because there are genuinely two choices now and they
            are not the same kind of thing. The site palette is the surface —
            the same one this viewer picked on a board or an export, and the
            reason the deck finally has a light mode. The deck palette is the
            accent the report's author chose, which T still cycles. */}
        <Popover trigger={<span aria-hidden="true">◑</span>} label="Theme" placement="above">
          <ThemeSwitcher value={siteTheme} onChange={onSiteTheme} />
          <button
            type="button"
            className={styles['deckThemeBtn']}
            onClick={onTheme}
            title={`Cycle deck palette (T) — next: ${nextThemeName}`}
          >
            Deck palette: {nextThemeName} →
          </button>
        </Popover>
      </div>
      <p className={styles['hint']}>← / → or Space to navigate · T for theme · F fullscreen</p>
    </div>
  );
}

/**
 * The credit line: the duck, the product's own block-glyph mark, and the date.
 *
 * The duck used to arrive as a 44 KB base64 PNG that Python inlined into every
 * exported deck. It comes from the bundle now — the same quantised 128px
 * sprites the boards use, ~7 KB for all three layers — so the deck is smaller
 * for having gained an animated mascot.
 */
export function Brand({ credit, generated, duck }: { credit: string; generated: string; duck: DuckState }) {
  return (
    <div className={styles['brand']}>
      <Duck state={duck} size={26} />
      {/* The credit string comes from `chrome.footer`, like every other
          surface's footer, rather than being assembled here. The wordmark stays
          a separate mark beside it: it is the *brand*, and gluing it into the
          middle of a server-supplied sentence would mean parsing that sentence.
          Text, never a link — `test_bundle_fetches_nothing` greps minified
          output for a URL and cannot tell an <a href> from a fetch. */}
      <span className={styles['brandMark']}>
        <Wordmark text="yeaboi" label="yeaboi.ai" className={styles['brandWordmark']} />
      </span>
      <span className={styles['brandCredit']}>{credit}</span>
      <span className={styles['brandDate']}>{generated}</span>
    </div>
  );
}
