/**
 * The inert path, which is the one that ships in every exported file.
 *
 * Nine report components will adopt these primitives, and all nine of their
 * existing test files render with no provider. So the property that keeps those
 * passing — and keeps a file on disk byte-identical to what it was — is that a
 * primitive with no editing context adds nothing at all to the DOM.
 */

import { fireEvent, render, screen } from '@testing-library/preact';
import { describe, expect, it, vi } from 'vitest';

import { Editable, EditableList, EditableSlot } from './Editable';
import { EditProvider, indexByPath, type Editing } from './EditContext';
import type { EditRow } from './state';

function row(over: Partial<EditRow> = {}): EditRow {
  return {
    id: 'e1',
    seq: 1,
    op: 'set',
    path: 'team_summary',
    value: 'corrected',
    label: '',
    target: '',
    author: 'Ada',
    avatar: '🦊',
    at: '2026-01-01T00:00:00+00:00',
    mine: false,
    applied: true,
    reason: '',
    ...over,
  };
}

function editing(over: Partial<Editing> = {}): Editing {
  return {
    enabled: true,
    me: { name: 'Ada', avatar: '🦊' },
    byPath: new Map(),
    revision: 0,
    showHistory: vi.fn(),
    othersEditing: () => [],
    actions: {
      setText: vi.fn().mockResolvedValue({ ok: true }),
      appendItem: vi.fn().mockResolvedValue({ ok: true }),
      removeItem: vi.fn().mockResolvedValue({ ok: true }),
      addNote: vi.fn().mockResolvedValue({ ok: true }),
      addField: vi.fn().mockResolvedValue({ ok: true }),
      revert: vi.fn().mockResolvedValue({ ok: true }),
      presence: vi.fn().mockResolvedValue(undefined),
      setLocked: vi.fn().mockResolvedValue(undefined),
      dropLast: vi.fn().mockResolvedValue(undefined),
    },
    ...over,
  };
}

describe('with no editing context — a file on disk', () => {
  it('Editable renders its children and nothing else', () => {
    const { container } = render(
      <Editable path="team_summary" label="team summary" value="raw">
        <p>drawn</p>
      </Editable>
    );
    expect(container.innerHTML).toBe('<p>drawn</p>');
  });

  it('EditableList renders its children and nothing else', () => {
    const { container } = render(
      <EditableList path="highlights" label="highlight">
        <ul>
          <li>one</li>
        </ul>
      </EditableList>
    );
    expect(container.innerHTML).toBe('<ul><li>one</li></ul>');
  });

  it('EditableSlot renders nothing at all', () => {
    const { container } = render(<EditableSlot anchor="" label="this report" />);
    expect(container.innerHTML).toBe('');
  });
});

describe('with a session', () => {
  it('offers an edit control named for what it edits', () => {
    render(
      <EditProvider value={editing()}>
        <Editable path="member_updates[name=Ada].blockers" label="Ada's blocker" value="staging db">
          <p>staging db</p>
        </Editable>
      </EditProvider>
    );
    // Composed at the call site so a screen reader hears the member's name
    // rather than "Edit".
    expect(screen.getByLabelText("Edit Ada's blocker")).toBeTruthy();
  });

  it('opens the editor on the raw artifact value, not on what is drawn', async () => {
    // The whole reason `value` is a separate prop: a standup summary draws as
    // linked sentences and is stored as one string, and the editor has to hand
    // back the string.
    render(
      <EditProvider value={editing()}>
        <Editable path="team_summary" label="team summary" value="The team shipped PSOT-12.">
          <p>The team shipped (a link).</p>
        </Editable>
      </EditProvider>
    );
    fireEvent.click(screen.getByLabelText('Edit team summary'));
    const field = await screen.findByLabelText<HTMLTextAreaElement>('team summary');
    expect(field.value).toBe('The team shipped PSOT-12.');
  });

  it('marks an edited region in words, not only with a border', () => {
    const context = editing({ byPath: indexByPath([row()]) });
    render(
      <EditProvider value={context}>
        <Editable path="team_summary" label="team summary" value="corrected">
          <p>corrected</p>
        </Editable>
      </EditProvider>
    );
    expect(screen.getByLabelText('team summary was edited — show history')).toBeTruthy();
  });

  it('hides the affordances once the host closes editing', () => {
    render(
      <EditProvider value={editing({ enabled: false })}>
        <Editable path="team_summary" label="team summary" value="x">
          <p>x</p>
        </Editable>
      </EditProvider>
    );
    expect(screen.queryByLabelText('Edit team summary')).toBeNull();
  });

  it('appends to the list slot rather than to the list', async () => {
    const context = editing();
    render(
      <EditProvider value={context}>
        <EditableList path="highlights" label="highlight">
          <ul />
        </EditableList>
      </EditProvider>
    );
    const input = screen.getByLabelText<HTMLInputElement>('Add a highlight');
    fireEvent.input(input, { target: { value: 'shipped auth' } });
    fireEvent.click(screen.getByText('Add'));
    await vi.waitFor(() => {
      expect(context.actions.appendItem).toHaveBeenCalledWith('highlights[-]', 'shipped auth');
    });
  });
});
