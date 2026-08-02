/**
 * The entrance, which is the part that was missing.
 *
 * Every other affordance in this stack is gated on `enabled`, which is gated on
 * having a name — so if there is no obvious place to give one, the feature is
 * invisible no matter how well the rest of it works. That was the bug: the only
 * name field lived inside a collapsed panel at the foot of the document, behind
 * a button labelled "Edits". These tests pin the three states apart and pin the
 * honesty line to the surface where names are typed.
 */

import { fireEvent, render, screen } from '@testing-library/preact';
import { createRef } from 'preact';
import { describe, expect, it, vi } from 'vitest';

import { EditBar, SELF_DECLARED } from './EditBar';
import { AVATARS } from '../../types/enums';

function renderBar(over: Partial<Parameters<typeof EditBar>[0]> = {}) {
  const onIdentity = vi.fn();
  render(
    <EditBar
      editable
      name=""
      avatar={AVATARS[0]}
      count={0}
      onIdentity={onIdentity}
      inputRef={createRef<HTMLInputElement>()}
      {...over}
    />
  );
  return { onIdentity };
}

describe('EditBar', () => {
  it('invites a reader who has not named themselves', () => {
    renderBar();
    expect(screen.getByText('Something wrong? You can fix it.')).toBeTruthy();
    expect(screen.getByPlaceholderText('Your name')).toBeTruthy();
  });

  it('says the attribution is self-declared where the name is typed', () => {
    renderBar();
    // Not a nicety: this is the one claim the system cannot make, and it has to
    // sit next to the field rather than only in a panel nobody opens.
    expect(screen.getByText(SELF_DECLARED)).toBeTruthy();
  });

  it('starts editing on Enter, trimming the name', () => {
    const { onIdentity } = renderBar();
    const input = screen.getByPlaceholderText('Your name');
    fireEvent.input(input, { target: { value: '  Ada  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onIdentity).toHaveBeenCalledWith('Ada', AVATARS[0]);
  });

  it('refuses to start on a name that is only whitespace', () => {
    const { onIdentity } = renderBar();
    const input = screen.getByPlaceholderText('Your name');
    fireEvent.input(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onIdentity).not.toHaveBeenCalled();
  });

  it('tells a named reader how to actually change something', () => {
    renderBar({ name: 'Ada', avatar: '🦊' });
    expect(screen.getByText(/Editing as Ada/)).toBeTruthy();
    expect(screen.getByText(/beside anything underlined/)).toBeTruthy();
  });

  it('clears the name on Done, which is what turns the affordances off', () => {
    const { onIdentity } = renderBar({ name: 'Ada', avatar: '🦊' });
    fireEvent.click(screen.getByText('Done'));
    expect(onIdentity).toHaveBeenCalledWith('', '🦊');
  });

  it('offers no way in once the host has closed editing', () => {
    renderBar({ editable: false, count: 2 });
    expect(screen.queryByPlaceholderText('Your name')).toBeNull();
    expect(screen.getByText(/Editing is closed/)).toBeTruthy();
    expect(screen.getByText(/2 corrections were made/)).toBeTruthy();
  });

  it('counts one correction in the singular', () => {
    renderBar({ editable: false, count: 1 });
    expect(screen.getByText(/1 correction was made/)).toBeTruthy();
  });
});
