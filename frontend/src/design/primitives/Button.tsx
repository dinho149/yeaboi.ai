/**
 * The button — and the first primitive in this vocabulary that *does* something
 * rather than displays something.
 *
 * Three variants and no more, on purpose. A palette of eight button styles is
 * how a design system stops meaning anything: `primary` is the one action a
 * screen is about, `secondary` is everything else, `ghost` is a control that
 * must not compete with content (a toolbar, a table row). If a fourth is ever
 * genuinely needed it should replace one of these, not join them.
 *
 * `type="button"` is the default rather than the HTML default of `submit`,
 * because the HTML default is a footgun: a button inside a form with no explicit
 * type submits it, which is invisible until someone puts a "Cancel" next to an
 * input and it starts saving.
 *
 * TODO(design): geometry and weight are provisional. The variants are the
 * contract; how they look is the design pass.
 */

import type { ComponentChildren, JSX } from 'preact';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost';
export type ButtonSize = 'small' | 'medium';

export interface ButtonProps {
  children: ComponentChildren;
  variant?: ButtonVariant;
  size?: ButtonSize;
  type?: 'button' | 'submit' | 'reset';
  disabled?: boolean;
  /** Shows a busy state and blocks activation, without changing the layout. */
  busy?: boolean;
  onClick?: (event: JSX.TargetedMouseEvent<HTMLButtonElement>) => void;
  /** Required when the label is not text a screen reader can use. */
  'aria-label'?: string;
  className?: string | undefined;
}

export function Button({
  children,
  variant = 'secondary',
  size = 'medium',
  type = 'button',
  disabled = false,
  busy = false,
  onClick,
  className,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(styles['btn'], styles[`btn-${variant}`], styles[`btn-${size}`], className)}
      // Disabled *and* busy both block the click. `aria-busy` rather than
      // swapping the label to "Loading…", so the accessible name is stable and
      // the button does not change width mid-action.
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      onClick={onClick}
      aria-label={rest['aria-label']}
    >
      {children}
    </button>
  );
}
