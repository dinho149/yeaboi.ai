/**
 * The float-up reaction overlay.
 *
 * The behaviour that matters is the seeding guard. The server keeps a bounded
 * ticker of the last 25 reactions and every snapshot carries all of it, so a
 * client that simply animated everything it received would launch twenty-five
 * emoji at anyone joining a lively board.
 */

import { render } from '@testing-library/preact';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { FloatingEmoji } from './FloatingEmoji';

const sprites = (container: Element) => container.querySelectorAll('span');

function reduceMotion(reduced: boolean): void {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reduced && query.includes('reduced-motion'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => reduceMotion(false));

describe('FloatingEmoji', () => {
  it('animates nothing on the first snapshot it sees', () => {
    // Joining a board mid-retro must not replay its backlog at you.
    const { container } = render(
      <FloatingEmoji events={[{ id: 1, emoji: '🎉' }, { id: 2, emoji: '👍' }]} />
    );
    expect(sprites(container)).toHaveLength(0);
  });

  it('animates only the events that arrived after it started watching', () => {
    const { container, rerender } = render(<FloatingEmoji events={[{ id: 5, emoji: '🎉' }]} />);
    rerender(<FloatingEmoji events={[{ id: 5, emoji: '🎉' }, { id: 6, emoji: '🚀' }]} />);

    const shown = [...sprites(container)].map((el) => el.textContent);
    // Three sprites per reaction, and only for id 6 — id 5 was the seed.
    expect(shown).toEqual(['🚀', '🚀', '🚀']);
  });

  it('does not replay an event it has already shown', () => {
    const events = [{ id: 5, emoji: '🎉' }];
    const { container, rerender } = render(<FloatingEmoji events={events} />);
    rerender(<FloatingEmoji events={[...events, { id: 6, emoji: '🚀' }]} />);
    // A snapshot with nothing new — the common case, since the ticker persists.
    rerender(<FloatingEmoji events={[...events, { id: 6, emoji: '🚀' }]} />);
    expect(sprites(container)).toHaveLength(3);
  });

  it('stays silent for a visitor who asked for reduced motion', () => {
    reduceMotion(true);
    const { container, rerender } = render(<FloatingEmoji events={[{ id: 1, emoji: '🎉' }]} />);
    rerender(<FloatingEmoji events={[{ id: 1, emoji: '🎉' }, { id: 2, emoji: '🚀' }]} />);
    // Nothing is lost: the counts on the cards are the durable record.
    expect(sprites(container)).toHaveLength(0);
  });

  it('caps how many sprites can be alive at once', () => {
    const { container, rerender } = render(<FloatingEmoji events={[{ id: 0, emoji: '🎉' }]} />);
    const burst = Array.from({ length: 40 }, (_, i) => ({ id: i + 1, emoji: '🔥' }));
    rerender(<FloatingEmoji events={burst} />);
    // 40 × 3 sprites would be 120. The cap keeps the most recent, because a
    // burst should show what just happened, not what started it.
    expect(sprites(container).length).toBeLessThanOrEqual(60);
  });

  it('is hidden from assistive tech', () => {
    // A reaction is announced by the count on the card it belongs to; the
    // float is decoration, and reading it would interrupt whoever is speaking.
    const { container, rerender } = render(<FloatingEmoji events={[{ id: 1, emoji: '🎉' }]} />);
    rerender(<FloatingEmoji events={[{ id: 1, emoji: '🎉' }, { id: 2, emoji: '🚀' }]} />);
    expect(container.querySelector('[aria-hidden="true"]')).toBeTruthy();
  });
});
