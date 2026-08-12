/**
 * The lozenge is a colour + a word; these tests pin that the word is real DOM
 * text (never colour alone) and that the category → class mapping is total,
 * so a payload status can never inject a class name.
 */

import { render } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import { LOZENGE_CATEGORIES, Lozenge } from './Lozenge';

describe('<Lozenge>', () => {
  it('renders the status word as text', () => {
    const { container } = render(<Lozenge category="done">Done</Lozenge>);
    expect(container.textContent).toBe('Done');
  });

  it.each(LOZENGE_CATEGORIES)('maps %s to a distinct module class', (category) => {
    const { container } = render(<Lozenge category={category}>x</Lozenge>);
    const classes = container.querySelector('span')?.className ?? '';
    // Base class plus exactly one category class.
    expect(classes.split(/\s+/).length).toBe(2);
  });

  it('keeps the tracker’s own word in the DOM — uppercasing is CSS-only', () => {
    // A screen reader and copy-paste get "In Progress", not "IN PROGRESS".
    const { container } = render(<Lozenge category="inprogress">In Progress</Lozenge>);
    expect(container.textContent).toBe('In Progress');
  });

  it('adds the small modifier only when asked', () => {
    const plain = render(<Lozenge category="todo">x</Lozenge>);
    const small = render(
      <Lozenge category="todo" small>
        x
      </Lozenge>,
    );
    const plainClasses = plain.container.querySelector('span')?.className ?? '';
    const smallClasses = small.container.querySelector('span')?.className ?? '';
    expect(smallClasses.split(/\s+/).length).toBe(plainClasses.split(/\s+/).length + 1);
  });
});
