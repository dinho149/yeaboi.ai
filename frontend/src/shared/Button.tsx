/**
 * The one button.
 *
 * There were four, and they had drifted the way four copies always do. Each
 * board grew its own: `castBtn/ghostBtn/saveBtn/addBtn/focusExit` in
 * `retro.module.css`, `cbtn/pkbtn/castBtn` in `poker.module.css`, the deck's
 * bare `.controls button` descendant selector, and `primaryBtn` in
 * `shared.module.css` — plus `IconButton`, which was already the fifth. They
 * agreed on the shape of the idea (min-height `--tap`, `--r-s`, `font:
 * inherit`, `cursor: pointer`) and disagreed on every number: `--panel` or
 * `--panel-2`, `--line` or `--line-strong`, `--fs-2xs` or `--fs-xs` or
 * `--fs-s`. None of the differences meant anything — they were the order the
 * files were written in.
 *
 * ## The axes are the ones the call sites actually use
 *
 * Not a general button API. Each prop below exists because two or more of the
 * deleted blocks needed it and expressed it differently:
 *
 * * `tone` — `default` / `primary` (the accent fill every board's confirm
 *   button had) / `danger` (retro's delete confirmation).
 * * `size` — `s` is the dense in-card register, `m` the ordinary board control,
 *   `l` the page's one call to action (the gate's Open, a modal's Save).
 * * `shape` — `box` is the default; `pill` is the deck's circular nav arrows;
 *   `bare` is retro's card actions, which sit on the card with no chrome of
 *   their own until you touch them.
 * * `active` — sets `aria-pressed`, which the raw buttons mostly forgot. A
 *   control that has opened a popover or locked the board is in a state, and
 *   that state was previously visual only.
 * * `attention` — the poker console's pulse, for the one moment in a round when
 *   everyone has voted and the host is the only thing missing.
 * * `block` — full width, for a button that is the whole row.
 *
 * ## Two deliberate normalisations
 *
 * Unifying five blocks means picking one value where they disagreed, so two
 * call-site registers shift by a hair. Both are recorded rather than hidden:
 *
 * 1. Retro's toolbar controls were `--fs-s` (13.5px) and are now `--fs-xs`
 *    (12.5px), matching poker's console. The smaller value won because poker's
 *    console is a fixed-width bottom sheet where a 1px *growth* can wrap a
 *    label, while retro's toolbar has room either way.
 * 2. `IconButton` was `--panel` / `--line`; the default tone is `--panel-2` /
 *    `--line`, which is what three of the five blocks used. `--panel-2` is
 *    `--panel` with 5% of `--text` mixed in.
 *
 * ## What stays outside
 *
 * Anything that is *mechanics* rather than styling keeps its own class, applied
 * through `className`. Retro's `.grip` is the case that matters: its
 * `touch-action: none` and `cursor: grab` are read by `useCardDrag`, not by a
 * designer, and folding them into a `shape` would make a drag interaction look
 * like a theme decision.
 */

import { forwardRef, type ComponentProps, type ReactNode } from 'react';

import { cx } from '../runtime/cx';
import styles from './shared.module.css';

export type ButtonTone = 'default' | 'primary' | 'danger';
export type ButtonSize = 's' | 'm' | 'l';
export type ButtonShape = 'box' | 'pill' | 'bare';

export interface ButtonProps
  // `className` is re-declared: preact types it as `Signalish<string>` (a
  // string *or* a signal), which nothing here uses and which will not pass into
  // a plain string helper like cx(). Same reason as IconButtonProps.
  extends Omit<ComponentProps<'button'>, 'className'> {
  tone?: ButtonTone;
  size?: ButtonSize;
  shape?: ButtonShape;
  /** Visually and semantically pressed — sets `aria-pressed`. */
  active?: boolean;
  /** Pulse, for the one control the room is waiting on. */
  attention?: boolean;
  /** Fill the available width. */
  block?: boolean;
  className?: string | undefined;
  children?: ReactNode;
}

// Explicit lookups rather than building class names from the prop value: the
// defaults (`m`, `default`, `box`) are the base rule and have no modifier
// class at all, and a computed `styles['btnM']` would silently be undefined.
const SIZES = { s: 'btnS', m: '', l: 'btnL' } as const;
const TONES = { default: '', primary: 'btnPrimary', danger: 'btnDanger' } as const;
const SHAPES = { box: '', pill: 'btnPill', bare: 'btnBare' } as const;

/**
 * The class list for a button, so `IconButton` can share it without nesting.
 *
 * Spelled out rather than `Pick<ButtonProps, …>`: under
 * `exactOptionalPropertyTypes` a `?:` property does not accept an explicit
 * `undefined`, and every caller here is forwarding its own optional props
 * straight through.
 */
export function buttonClass({
  tone = 'default',
  size = 'm',
  shape = 'box',
  active,
  attention,
  block,
  className,
}: {
  tone?: ButtonTone | undefined;
  size?: ButtonSize | undefined;
  shape?: ButtonShape | undefined;
  active?: boolean | undefined;
  attention?: boolean | undefined;
  block?: boolean | undefined;
  className?: string | undefined;
}): string {
  return cx(
    styles['btn'],
    styles[SIZES[size]],
    styles[TONES[tone]],
    styles[SHAPES[shape]],
    active && styles['btnActive'],
    attention && styles['btnAttention'],
    block && styles['btnBlock'],
    className
  );
}

/**
 * `forwardRef`, because a ref on a function component points at the component,
 * not at the DOM node — and three call sites need the node itself. Retro's
 * reaction tray returns focus to its trigger after you pick an emoji, which is
 * the only way out of the tray with a keyboard; without forwarding, that call
 * lands on a preact component instance and throws mid-handler, leaving focus
 * stranded on a button that no longer exists.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    tone,
    size,
    shape,
    active,
    attention,
    block,
    className,
    children,
    // Always explicit: a bare <button> inside a <form> submits it, and the gate
    // is a form.
    type = 'button',
    ...rest
  },
  ref
) {
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      aria-pressed={active === undefined ? undefined : active}
      className={buttonClass({ tone, size, shape, active, attention, block, className })}
    >
      {children}
    </button>
  );
});
