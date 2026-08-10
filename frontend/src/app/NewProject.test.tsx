/**
 * Creating a project.
 *
 * The behaviour worth pinning is the validation that `window.prompt` could not
 * do: a blank name never reaches the server, and the message appears next to
 * the field rather than as a toast in the corner.
 */

import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { NewProjectDialog } from './NewProject';

afterEach(() => vi.restoreAllMocks());

function open(onCreated = vi.fn()) {
  return {
    onCreated,
    ...render(<NewProjectDialog open onClose={vi.fn()} onCreated={onCreated} />),
  };
}

describe('<NewProjectDialog>', () => {
  it('names the dialog', () => {
    open();
    expect(screen.getByRole('dialog', { name: 'New project' })).toBeTruthy();
  });

  it('refuses a blank name without asking the server', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    open();
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(screen.getByRole('alert').textContent).toContain('needs a name');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('refuses whitespace, which the server would also have rejected', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    open();
    await userEvent.type(screen.getByLabelText(/Name/), '   ');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('clears the error as soon as typing starts', async () => {
    open();
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(screen.queryByRole('alert')).toBeTruthy();
    await userEvent.type(screen.getByLabelText(/Name/), 'P');
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });

  it('sends the trimmed name and reports it back', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'prj_1', name: 'Payments' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const onCreated = vi.fn();
    open(onCreated);
    await userEvent.type(screen.getByLabelText(/Name/), '  Payments  ');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('Payments'));
    // `noUncheckedIndexedAccess` is on, so the call record is possibly
    // undefined until it is checked — asserting on it IS the check.
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ name: 'Payments' });
  });

  it('shows a server error in the field rather than swallowing it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: 'that name is taken' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    open();
    await userEvent.type(screen.getByLabelText(/Name/), 'Payments');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('taken'));
  });
});
