/**
 * Driving the deck: navigation, the palette cycle, and the style knobs.
 *
 * The one behavioural regression worth naming is the last block. The Python
 * renderer resolved a chosen heading colour against whichever palette the deck
 * was exported with and wrote it into a stylesheet, so pressing T re-themed
 * everything except the colour the user had actually picked.
 */

import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import type { DeckBoot } from './boot';
import { DECK_WIRE } from '../test/fixtures/wire';
import { App } from './App';

function boot(over: Partial<DeckBoot> = {}): DeckBoot {
  // The committed fixture is written with sorted keys for a readable diff; the
  // served payload is insertion-ordered, built-ins first. Cycle order is that
  // order, so it has to be restored here rather than inherited.
  const ordered = ['midnight', 'aurora', 'sunset', 'mono'];
  const palettes = Object.fromEntries(ordered.map((name) => [name, DECK_WIRE.palettes[name]!]));
  return { ...DECK_WIRE, palettes, theme: 'midnight', ...over };
}

function count(): string {
  return screen.getByText(/^\d+ \/ \d+$/).textContent!;
}

beforeEach(() => {
  document.documentElement.removeAttribute('style');
  document.documentElement.removeAttribute('data-deck-theme');
});

describe('App', () => {
  it('opens on the first slide and says where you are', () => {
    const deck = boot();
    render(<App boot={deck} />);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Acme Portal');
    expect(count()).toBe(`1 / ${deck.slides.length}`);
  });

  it('advances and retreats with the arrow keys', async () => {
    const user = userEvent.setup();
    render(<App boot={boot()} />);

    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Executive summary');
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Acme Portal');
  });

  it('jumps to either end with Home and End', async () => {
    const user = userEvent.setup();
    const deck = boot();
    render(<App boot={deck} />);

    await user.keyboard('{End}');
    expect(count()).toBe(`${deck.slides.length} / ${deck.slides.length}`);
    await user.keyboard('{Home}');
    expect(count()).toBe(`1 / ${deck.slides.length}`);
  });

  it('stops at the ends rather than wrapping', async () => {
    const user = userEvent.setup();
    render(<App boot={boot()} />);

    const prev = screen.getByRole('button', { name: 'Previous slide' });
    expect(prev.hasAttribute('disabled')).toBe(true);
    await user.keyboard('{ArrowLeft}');
    expect(count()).toMatch(/^1 \//);

    await user.keyboard('{End}');
    expect(screen.getByRole('button', { name: 'Next slide' }).hasAttribute('disabled')).toBe(true);
  });

  it('advances once per Space, even from a focused nav button', async () => {
    const user = userEvent.setup();
    render(<App boot={boot()} />);

    // Clicking "›" leaves it focused, and Space is *also* the native activation
    // key for a focused button — so this could plausibly fire twice and jump
    // two slides. It does not, and the reason is one line: `preventDefault()`
    // on the keydown cancels the button's activation, which happens on keyup.
    // That is the whole mechanism, it is invisible at the call site, and this
    // is how a deck actually gets driven — mouse to start, spacebar for the
    // rest — so it is worth a case.
    await user.click(screen.getByRole('button', { name: 'Next slide' }));
    expect(count()).toMatch(/^2 \//);
    await user.keyboard(' ');
    expect(count()).toMatch(/^3 \//);
  });

  it('paints the opening palette onto the document', () => {
    render(<App boot={boot({ theme: 'aurora' })} />);
    expect(document.documentElement.getAttribute('data-deck-theme')).toBe('aurora');
    expect(document.documentElement.style.getPropertyValue('--bg')).toBe(DECK_WIRE.palettes['aurora']!.bg1);
  });

  it('cycles palettes with T, in the payload’s own order', async () => {
    const user = userEvent.setup();
    render(<App boot={boot()} />);

    await user.keyboard('t');
    expect(document.documentElement.getAttribute('data-deck-theme')).toBe('aurora');
    await user.keyboard('T');
    expect(document.documentElement.getAttribute('data-deck-theme')).toBe('sunset');
  });

  it('falls back to the first palette when the named one is gone', () => {
    render(<App boot={boot({ theme: 'a-custom-theme-since-deleted' })} />);
    expect(document.documentElement.style.getPropertyValue('--bg')).toBe(DECK_WIRE.palettes['midnight']!.bg1);
  });

  it('shows the custom footer and slide number only when asked', () => {
    const plain = boot({ style: { ...DECK_WIRE.style, footer: '', slideNumbers: false } });
    const { container, rerender } = render(<App boot={plain} />);
    expect(container.textContent).not.toContain('ACME Corp');

    rerender(<App boot={boot()} />);
    expect(screen.getByText('ACME Corp')).toBeTruthy();
  });

  describe('style colours', () => {
    it('resolve against the palette that is showing, not the one it opened with', async () => {
      const user = userEvent.setup();
      const deck = boot({ style: { ...DECK_WIRE.style, headingColor: 'accent2' } });
      const { container } = render(<App boot={deck} />);
      const app = container.firstElementChild as HTMLElement;

      expect(app.style.getPropertyValue('--deck-heading')).toBe(DECK_WIRE.palettes['midnight']!.accent2);
      await user.keyboard('t');
      expect(app.style.getPropertyValue('--deck-heading')).toBe(DECK_WIRE.palettes['aurora']!.accent2);
    });

    it('are left unset when the user chose none', () => {
      const deck = boot({ style: { ...DECK_WIRE.style, headingColor: '', titleColor: '' } });
      const { container } = render(<App boot={deck} />);
      const app = container.firstElementChild as HTMLElement;
      // Unset, not defaulted: each use site has its own fallback, because "no
      // heading colour" means the accent on a bullet and plain text on an <h2>.
      expect(app.style.getPropertyValue('--deck-heading')).toBe('');
      expect(app.style.getPropertyValue('--deck-title')).toBe('');
    });

    it('scale every size from one multiplier', () => {
      const { container } = render(<App boot={boot()} />);
      const app = container.firstElementChild as HTMLElement;
      expect(app.style.getPropertyValue('--deck-scale')).toBe('1.15');
    });
  });
});
