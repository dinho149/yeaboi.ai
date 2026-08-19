/**
 * A button whose visible content is an emoji glyph.
 *
 * The boards are full of these — ♪ ⏱ ◑ 🔒 ☰ 🎲 ✎ ✕ — and every one of them
 * relied on `title` alone. `title` is not an accessible name: screen readers
 * treat it inconsistently, and on touch there is no hover to reveal it at all,
 * so on a phone those buttons are unlabelled in both senses.
 *
 * This component makes the correct pairing structural rather than remembered:
 * `label` becomes the accessible name **and** the tooltip, and the glyph is
 * `aria-hidden` so it is not read as "black right-pointing triangle".
 *
 * It also enforces the tap target. `.act` on the retro card is about 13×20 px
 * today, well under the WCAG 2.2 minimum of 24×24 and far under what a thumb
 * can hit — which is why moving a card on a phone is a lottery.
 *
 * It is a wrapper over `Button` now rather than a sixth button implementation:
 * everything below the accessible-name pairing is the same `.btn` block. The
 * props are unchanged — `compact` is kept rather than renamed to `size="s"`
 * because roughly twenty call sites read better saying what the button *is*
 * (an in-card action) than what size it happens to be.
 */

import { forwardRef, type ComponentProps, type ReactNode } from 'react';

import { cx } from '../runtime/cx';
import { buttonClass } from './Button';
import styles from './shared.module.css';

export interface IconButtonProps
  // `className` is re-declared below: preact types it as `Signalish<string>`
  // (a string *or* a signal), which nothing here uses and which will not pass
  // into a plain string helper like cx().
  extends Omit<ComponentProps<'button'>, 'aria-label' | 'title' | 'className'> {
  /** The glyph. Hidden from assistive tech. */
  icon: ReactNode;
  /** The accessible name, and the tooltip. Required — that is the whole point. */
  label: string;
  /** Text shown beside the glyph. When present, the button is no longer icon-only. */
  children?: ReactNode;
  /** A real toggle that is currently on (the board locked) — sets `aria-pressed`. */
  active?: boolean;
  /** The same look with no `aria-pressed`, for a control that is not a toggle. */
  emphasis?: boolean;
  /** Smaller variant for in-card actions. Still meets the 24px floor. */
  compact?: boolean;
  tone?: 'default' | 'primary' | 'danger';
  className?: string | undefined;
}

/** Forwards its ref for the same reason `Button` does — see the note there. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, children, active, emphasis, compact, tone = 'default', className, type = 'button', ...rest },
  ref
) {
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      aria-pressed={active === undefined ? undefined : active}
      className={buttonClass({
        tone,
        size: compact ? 's' : 'm',
        active,
        emphasis,
        className: cx(compact && styles['btnDim'], className),
      })}
    >
      <span aria-hidden="true" className={styles['iconGlyph']}>
        {icon}
      </span>
      {children ? <span className={styles['iconLabel']}>{children}</span> : null}
    </button>
  );
});
