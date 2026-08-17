/**
 * The card, and the one behaviour this whole migration was for.
 *
 * The legacy board re-rendered a column with `innerHTML =` on every poll, which
 * destroyed any open `<textarea>`. It compensated by freezing the entire column
 * — every other person's cards included — for as long as one person had an
 * editor open (`retro/page.py:700`, `editingHere`). The first test here is the
 * assertion that makes that machinery unnecessary.
 */

import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { card } from '../test/retroState';
import { REACTION_EMOJIS } from '../types/enums';
import { CardView } from './CardView';

function renderCard(overrides: Parameters<typeof card>[0] = {}, props: Partial<Parameters<typeof CardView>[0]> = {}) {
  const handlers = {
    onEdit: vi.fn(),
    onDelete: vi.fn(),
    onReact: vi.fn(),
    onCardPointerDown: vi.fn(),
  };
  const subject = card(overrides);
  const view = render(
    <CardView card={subject} myReactions={new Set()} locked={false} {...handlers} {...props} />
  );
  return { ...view, ...handlers, subject };
}

describe('CardView', () => {
  it('keeps an open draft when a new snapshot changes the card underneath it', async () => {
    const user = userEvent.setup();
    const { rerender, subject, onEdit } = renderCard({ text: 'original', mine: true });

    await user.click(screen.getByRole('button', { name: /^Edit card/ }));
    const box = screen.getByRole('textbox', { name: 'Edit card' });
    await user.type(box, ' — half typed');

    // A snapshot lands: someone else reacted, so the card object is new and its
    // `reactions` changed. On the old board this wiped the textarea.
    rerender(
      <CardView
        card={{ ...subject, reactions: { '👍': 1 } }}
        myReactions={new Set()}
        locked={false}
        onEdit={onEdit}
        onDelete={vi.fn()}
        onReact={vi.fn()}
        onCardPointerDown={vi.fn()}
      />
    );

    expect(screen.getByRole('textbox', { name: 'Edit card' })).toHaveProperty(
      'value',
      'original — half typed'
    );
    // …and the rest of the card did update, which is the half the freeze cost.
    expect(screen.getByRole('button', { name: 'Add 👍 reaction (1)' })).toBeTruthy();
  });

  it('saves on ⌘-Enter and cancels on Escape without saving', async () => {
    const user = userEvent.setup();
    const { onEdit, subject } = renderCard({ text: 'first', mine: true });

    await user.click(screen.getByRole('button', { name: /^Edit card/ }));
    await user.type(screen.getByRole('textbox', { name: 'Edit card' }), ' second{Escape}');
    expect(onEdit).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox', { name: 'Edit card' })).toBeNull();

    await user.click(screen.getByRole('button', { name: /^Edit card/ }));
    await user.type(screen.getByRole('textbox', { name: 'Edit card' }), ' third{Meta>}{Enter}{/Meta}');
    expect(onEdit).toHaveBeenCalledWith(subject.id, 'first third');
  });

  it("offers edit and delete only on your own cards", () => {
    renderCard({ mine: false });
    expect(screen.queryByRole('button', { name: /^Edit card/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Delete card/ })).toBeNull();
  });

  it('never offers edit on an AI card, even to its nominal owner', () => {
    // `mine` is computed from the requesting pid; an AI card has no owner, so
    // this is belt-and-braces against the server ever answering `mine:true`.
    renderCard({ mine: true, origin: 'ai' });
    expect(screen.queryByRole('button', { name: /^Edit card/ })).toBeNull();
  });

  it('hides every mutating control while the host has the board locked', () => {
    // Including the reaction trigger, which used to be the one exception —
    // rendered but disabled, while edit and move were removed outright. The
    // `locked` prop has always been documented as "hide, not just disable";
    // moving the trigger out of the reaction row was the point at which the
    // inconsistency became visible.
    renderCard({ mine: true }, { locked: true });
    expect(screen.queryByRole('button', { name: /^Edit card/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Move card/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add a reaction' })).toBeNull();
  });

  it('drops the reaction row entirely when nothing has been reacted to', () => {
    // The row used to be unconditional, because it also held the "add" trigger
    // — so most cards showed a lone 🙂 hanging in empty space under the author
    // line, which read as a rendering fault rather than a control.
    const { container } = renderCard({ reactions: {} });
    expect(container.querySelector(`.${'reactions'}`)).toBeNull();
    // The trigger is still reachable; it just lives with the other actions now.
    expect(screen.getByRole('button', { name: 'Add a reaction' })).toBeTruthy();
  });

  it('still shows the row once a reaction lands', () => {
    renderCard({ reactions: { '👍': 2 } });
    expect(screen.getByRole('button', { name: /Add 👍 reaction \(2\)/ })).toBeTruthy();
  });

  it('asks before deleting, and only deletes when the confirmation is answered', async () => {
    const user = userEvent.setup();
    const { onDelete } = renderCard({ text: 'hard won', mine: true });

    await user.click(screen.getByRole('button', { name: /^Delete card/ }));
    // The ✕ is the question, not the answer: `delete_card` is a hard delete
    // with no tombstone and no undo, and the ✕ sits pixels from the ✎.
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole('group', { name: 'Confirm delete' })).toBeTruthy();

    await user.click(screen.getByRole('button', { name: /^Confirm delete card/ }));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it('backs out of a pending delete on Escape and on the keep button', async () => {
    const user = userEvent.setup();
    const { onDelete } = renderCard({ mine: true });

    await user.click(screen.getByRole('button', { name: /^Delete card/ }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('group', { name: 'Confirm delete' })).toBeNull();

    await user.click(screen.getByRole('button', { name: /^Delete card/ }));
    await user.click(screen.getByRole('button', { name: 'Keep the card' }));
    expect(screen.queryByRole('group', { name: 'Confirm delete' })).toBeNull();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('offers every reaction emoji in a tray outside the card', async () => {
    const user = userEvent.setup();
    const { container, onReact, subject } = renderCard();

    await user.click(screen.getByRole('button', { name: 'Add a reaction' }));
    const tray = screen.getByRole('menu', { name: 'Reactions' });
    expect([...tray.querySelectorAll('[role="menuitem"]')]).toHaveLength(REACTION_EMOJIS.length);

    // On the body, not in the card. `.cards` is a scroll box on both axes, so
    // a panel positioned inside a card is clipped to the column — which showed
    // the last two emoji of sixteen. The ancestry is the assertion.
    expect(container.querySelector('article')?.contains(tray)).toBe(false);
    expect(document.body.contains(tray)).toBe(true);

    await user.click(screen.getByRole('menuitem', { name: '🧠' }));
    expect(onReact).toHaveBeenCalledWith(subject.id, '🧠');
    expect(screen.queryByRole('menu', { name: 'Reactions' })).toBeNull();
  });

  it('renders card text as a text node, never as markup', () => {
    const { container } = renderCard({ text: '<img src=x onerror=alert(1)>' });
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});
