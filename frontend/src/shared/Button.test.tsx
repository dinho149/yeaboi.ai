/**
 * The one button, and the two things a shared primitive can quietly break.
 *
 * Most of this file is not about appearance — CSS Modules hash their class
 * names, so asserting on them measures the build, not the design. What it does
 * measure is the *semantics* the five deleted blocks each got slightly wrong,
 * and which are now everyone's problem the moment they regress:
 *
 * * `type="button"`. Three of the migrated call sites live inside the gate's
 *   `<form>`, where a bare `<button>` submits it. Forgetting the attribute once
 *   in a shared component is forgetting it everywhere.
 * * `aria-pressed`. `active` was previously a colour on four of the five
 *   blocks, so a host who had locked the board was the only person who could
 *   tell — a screen reader was told nothing at all. It must be absent, not
 *   `false`, when the button is not a toggle: `aria-pressed="false"` announces
 *   an unpressed toggle, which a one-shot action is not.
 */

import { render, screen } from '@testing-library/preact';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { Button, buttonClass } from './Button';

describe('Button', () => {
  it('defaults to type="button"', () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole('button').getAttribute('type')).toBe('button');
  });

  it('still lets a form own its submit control', () => {
    render(<Button type="submit">Open</Button>);
    expect(screen.getByRole('button').getAttribute('type')).toBe('submit');
  });

  it('omits aria-pressed entirely when it is not a toggle', () => {
    render(<Button>Reveal votes</Button>);
    expect(screen.getByRole('button').hasAttribute('aria-pressed')).toBe(false);
  });

  it.each([
    [true, 'true'],
    [false, 'false'],
  ])('reports active=%s as aria-pressed="%s"', (active, expected) => {
    render(<Button active={active}>Lock</Button>);
    expect(screen.getByRole('button').getAttribute('aria-pressed')).toBe(expected);
  });

  it('forwards its ref to the DOM node, not the component', () => {
    // Retro's reaction tray returns focus to its trigger after you pick an
    // emoji — the only keyboard exit from the tray. Without forwardRef the ref
    // holds a preact component instance, `.focus()` is not a function, and the
    // handler throws with focus stranded on a button that has just unmounted.
    const ref = createRef<HTMLButtonElement>();
    render(<Button ref={ref}>☺</Button>);
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
    ref.current?.focus();
    expect(document.activeElement).toBe(ref.current);
  });

  it('forwards the events and attributes a raw button carried', () => {
    const onClick = vi.fn();
    render(
      <Button onClick={onClick} disabled aria-label="Next ticket" title="Next">
        ›
      </Button>
    );
    const button = screen.getByRole('button', { name: 'Next ticket' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('title')).toBe('Next');
  });
});

describe('buttonClass', () => {
  // The defaults are the base rule and have no modifier class, so a naive
  // `btn${size}` lookup would emit the literal string "undefined" into the
  // className. Length is the honest check: the hashes themselves are build
  // output.
  it('emits only the base class for the default variant', () => {
    expect(buttonClass({}).split(' ')).toHaveLength(1);
  });

  it('adds one class per non-default axis', () => {
    const parts = buttonClass({ tone: 'primary', size: 's', shape: 'bare' }).split(' ');
    expect(parts).toHaveLength(4);
    expect(parts).not.toContain('undefined');
  });

  it('keeps a caller class, which is how .grip keeps its drag mechanics', () => {
    expect(buttonClass({ className: 'grip-hash' })).toContain('grip-hash');
  });

  it('drops a falsy caller class rather than interpolating it', () => {
    expect(buttonClass({ className: undefined })).not.toContain('undefined');
  });
});
