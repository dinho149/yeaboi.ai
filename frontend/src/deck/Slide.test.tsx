/**
 * Slide rendering, one case per slide kind plus the two rules the eyebrow follows.
 *
 * Slides are built from the real payload (`DECK_WIRE`) rather than hand-rolled
 * objects, so a test cannot quietly describe a deck the exporter never writes.
 */

import { render, screen, within } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import type { DeckSlide } from './boot';
import { DECK_WIRE } from '../test/fixtures/wire';
import { Slide } from './Slide';

/** The first slide of `type` from the real payload. */
function wire(type: DeckSlide['type']): DeckSlide {
  const found = DECK_WIRE.slides.find((s) => s.type === type);
  if (!found) throw new Error(`the deck fixture has no ${type} slide — regenerate it`);
  return found;
}

describe('Slide', () => {
  it('opens on the project name and the headline', () => {
    render(<Slide slide={wire('title')} />);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Acme Portal');
    expect(screen.getByText('Two sprints of strong delivery.')).toBeTruthy();
  });

  it('gives the title slide no eyebrow — it belongs to neither act', () => {
    const { container } = render(<Slide slide={wire('title')} />);
    expect(container.textContent).not.toContain('Overview');
  });

  it('draws the summary as one paragraph per point', () => {
    const { container } = render(<Slide slide={wire('summary')} />);
    const paragraphs = [...container.querySelectorAll('p')].map((p) => p.textContent);
    expect(paragraphs).toEqual(['We shipped SSO.', 'Checkout got materially faster for everyone.']);
  });

  it('still renders a whole-paragraph summary from an older export', () => {
    // Decks are files on disks that get re-opened, not a server response that
    // is always current.
    render(<Slide slide={{ type: 'summary', title: 'Executive summary', body: 'One long blob.' }} />);
    expect(screen.getByText('One long blob.')).toBeTruthy();
  });

  it('pairs each metric value with its label, and carries the footnote', () => {
    render(<Slide slide={wire('metrics')} />);
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.getByText('Items delivered')).toBeTruthy();
    expect(screen.getByText('Corroborated by 24 merged PRs')).toBeTruthy();
  });

  it('lists a theme’s outcomes', () => {
    const security = DECK_WIRE.slides.find((s) => s.title === 'Security');
    render(<Slide slide={security!} />);
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Security');
    expect([...screen.getAllByRole('listitem')].map((li) => li.textContent)).toEqual(['SSO', 'MFA']);
  });

  it('renders outcome cards with their own bullets', () => {
    const slide: DeckSlide = {
      type: 'cards',
      section: 'Delivery',
      title: 'Outcomes',
      page: [1, 1],
      cards: [
        ['Security', ['SSO', 'MFA']],
        ['Performance', ['Faster checkout']],
      ],
    };
    render(<Slide slide={slide} />);
    const security = screen.getByRole('heading', { level: 3, name: 'Security' }).parentElement!;
    expect([...within(security).getAllByRole('listitem')].map((li) => li.textContent)).toEqual(['SSO', 'MFA']);
  });

  describe('the eyebrow', () => {
    it('names the section the slide belongs to', () => {
      render(<Slide slide={wire('summary')} />);
      expect(screen.getByText('Overview')).toBeTruthy();
    });

    it('carries the page position when a run was split', () => {
      render(<Slide slide={{ type: 'list', section: 'Delivery', title: 'Big', page: [2, 3], items: ['a'] }} />);
      expect(screen.getByText('2/3')).toBeTruthy();
      // …and the heading stays the theme's own name. Suffixing it "(2/3)" put
      // a piece of bookkeeping in the largest type on a projected slide.
      expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Big');
    });

    it('says nothing about a run of one', () => {
      const { container } = render(
        <Slide slide={{ type: 'list', section: 'Delivery', title: 'Big', page: [1, 1], items: ['a'] }} />
      );
      expect(container.textContent).not.toContain('1/1');
    });
  });

  describe('the thank-you slide', () => {
    it('sets its title in the block-glyph face', () => {
      render(<Slide slide={{ type: 'thanks', title: 'Thank you', subtitle: 'Acme Portal' }} />);
      const mark = screen.getByRole('img', { name: 'Thank you' });
      // The compact face is one SVG path now, not a grid of block characters.
      expect(mark.tagName).toBe('svg');
      expect(screen.getByText('Acme Portal')).toBeTruthy();
    });

    it('falls back to a plain heading when the text is too wide for glyphs', () => {
      // Three cells per character and two rows that cannot line-break, so a
      // long string would run off a projected slide rather than wrap.
      render(<Slide slide={{ type: 'thanks', title: 'Thank you very much indeed' }} />);
      expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Thank you very much indeed');
      expect(screen.queryByRole('img')).toBeNull();
    });

    it('renders no mark at all rather than an unlabelled one', () => {
      // An empty `<pre role="img">` is announced as an image with no name,
      // which is worse than the heading it would be standing in for.
      render(<Slide slide={{ type: 'thanks', title: '', subtitle: 'Acme Portal' }} />);
      expect(screen.queryByRole('img')).toBeNull();
      expect(screen.queryByRole('heading')).toBeNull();
      expect(screen.getByText('Acme Portal')).toBeTruthy();
    });
  });

  it('renders tracker text as text, never as markup', () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const { container } = render(
      <Slide slide={{ type: 'list', section: 'Delivery', title: 'T', page: [1, 1], items: [hostile] }} />
    );
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(hostile)).toBeTruthy();
  });
});
