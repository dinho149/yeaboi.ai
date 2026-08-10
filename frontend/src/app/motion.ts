/**
 * The motion layer.
 *
 * "Fluid" is the brief, and what makes an interface feel fluid is not more
 * animation — it is that state *changes* rather than being replaced. A route
 * swap that fades one view out and another in reads as two screens; the same
 * swap where content moves reads as one screen rearranging.
 *
 * Three rules, all enforced here rather than at each call site:
 *
 * 1. **prefers-reduced-motion is a branch, not a multiplier.** Under `reduce`
 *    there is no tween at all — the element is simply placed. A zero-duration
 *    tween still runs the engine and still fires callbacks, which is not the
 *    same promise. Vestibular disorders are the reason.
 * 2. **Durations and eases are tokens** (`MOTION`), so the design pass retunes
 *    the whole app's feel in one edit — the property the colour layer has.
 * 3. **Transform and opacity only.** Animating width, top or margin re-lays out
 *    the page every frame; transform and opacity are composited. See the
 *    gsap-performance skill.
 *
 * ## Why the vars are a pure function
 *
 * `entranceVars` decides *what* to animate and `enter`/`enterList` merely hand
 * that to GSAP. The split is not ceremony: GSAP's `matchMedia` handler does not
 * run under jsdom, so with the decision inlined the reduced-motion branch —
 * the one part of an animation system that is invisible in use, easy to break
 * and actively harmful when broken — would be untestable. As a function it is
 * asserted directly, and `motion.test.ts` pins it.
 */

import { gsap } from 'gsap';

/**
 * The feel of the app, in one place.
 *
 * TODO(design): provisional. These numbers are the biggest single lever on
 * whether the product feels brisk or laboured, and that is a taste decision
 * nobody has made yet.
 */
export const MOTION = {
  /** Entrances. Long enough to be seen, short enough not to be waited on. */
  enter: 0.32,
  /** Exits are faster than entrances — nobody wants to watch something leave. */
  exit: 0.18,
  /** Between items in a stagger. */
  stagger: 0.035,
  /** Decelerating: quick off the mark, settles gently. */
  ease: 'power2.out',
  /** How far a thing travels on entry, in pixels. */
  rise: 12,
} as const;

const REDUCE_QUERY = '(prefers-reduced-motion: reduce)';

/** True when the user has asked for less movement. */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.(REDUCE_QUERY).matches === true;
}

export interface EntranceVars {
  from: Record<string, number>;
  to: Record<string, number | string>;
}

/**
 * The tween for an entrance, or `null` when there should not be one.
 *
 * `null` rather than a zero-duration tween is the whole point — see the module
 * header. `stagger` is only present for a list.
 */
export function entranceVars(reduced: boolean, stagger = false): EntranceVars | null {
  if (reduced) return null;
  const to: Record<string, number | string> = {
    opacity: 1,
    y: 0,
    duration: MOTION.enter,
    ease: MOTION.ease,
  };
  if (stagger) to['stagger'] = MOTION.stagger;
  return { from: { opacity: 0, y: MOTION.rise }, to };
}

/** Where a thing rests. Applied instead of a tween under reduced motion. */
export const RESTING = { opacity: 1, y: 0 } as const;

/**
 * Animate a view in.
 *
 * Returns a cleanup function so a caller in an effect can hand it straight
 * back. `gsap.context()` is what makes that cleanup total: it reverts every
 * tween created inside it, so a route change mid-animation cannot leave a
 * half-faded node behind.
 */
export function enter(target: Element | null): () => void {
  if (!target) return () => {};
  const vars = entranceVars(prefersReducedMotion());
  const context = gsap.context(() => {
    if (!vars) {
      gsap.set(target, RESTING);
      return;
    }
    gsap.fromTo(target, vars.from, vars.to);
  });
  return () => context.revert();
}

/**
 * Animate a list in, one item after another.
 *
 * The stagger is deliberately small. A long one turns a list into a performance
 * the reader has to sit through, which is the opposite of fluid — and it is the
 * commonest way this effect is overdone.
 */
export function enterList(container: Element | null, selector: string): () => void {
  if (!container) return () => {};
  const items = container.querySelectorAll(selector);
  if (!items.length) return () => {};
  const vars = entranceVars(prefersReducedMotion(), true);
  const context = gsap.context(() => {
    if (!vars) {
      gsap.set(items, RESTING);
      return;
    }
    gsap.fromTo(items, vars.from, vars.to);
  }, container);
  return () => context.revert();
}
