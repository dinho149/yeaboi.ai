/**
 * The join gate — the only page an unauthenticated stranger reaches.
 *
 * The error mapping is the substance here. `JoinLimiter` locks an IP out for
 * five minutes after eight failures, and a visitor told only "that code did not
 * match" will retype the *correct* code, fail again, and conclude the host sent
 * them a broken link.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchBody } from '../test/calls';
import { JoinGate, normalizeCode } from './JoinGate';

afterEach(() => vi.restoreAllMocks());

const field = (): HTMLInputElement => screen.getByLabelText(/access code/i) as HTMLInputElement;
const submit = (): HTMLElement => screen.getByRole('button', { name: /view output/i });

function type(value: string): void {
  fireEvent.input(field(), { target: { value } });
}

describe('normalizeCode', () => {
  it('uppercases and inserts the dash as you type', () => {
    expect(normalizeCode('k3p9')).toBe('K3P9');
    expect(normalizeCode('k3p92qxa')).toBe('K3P9-2QXA');
  });

  it('survives a paste from a chat message', () => {
    // The real path: the code arrives in Slack, lowercase, with surrounding
    // whitespace and whatever separator the host happened to type.
    expect(normalizeCode('  k3p9-2qxa\n')).toBe('K3P9-2QXA');
    expect(normalizeCode('k3p9 2qxa')).toBe('K3P9-2QXA');
    expect(normalizeCode('K3P9_2QXA')).toBe('K3P9-2QXA');
  });

  it('stops at the full length', () => {
    expect(normalizeCode('K3P92QXAEXTRA')).toBe('K3P9-2QXA');
  });
});

describe('JoinGate', () => {
  it('keeps submit disabled until the code is complete', () => {
    render(<JoinGate />);
    expect(submit()).toHaveProperty('disabled', true);
    type('K3P92QXA');
    expect(submit()).toHaveProperty('disabled', false);
  });

  it('distinguishes a wrong code from a rate-limit lockout', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 429 })));
    render(<JoinGate />);
    type('K3P92QXA');
    fireEvent.click(submit());

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/wait a few minutes/i));
  });

  it('says the code did not match on a 403', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 403 })));
    render(<JoinGate />);
    type('K3P92QXA');
    fireEvent.click(submit());

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/did not match/i));
  });

  it('reports a network failure as its own thing', async () => {
    // "The host stopped sharing" and "you typed it wrong" need different
    // reactions from the visitor, so they must not share a message.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    render(<JoinGate />);
    type('K3P92QXA');
    fireEvent.click(submit());

    await waitFor(() => expect(screen.getByRole('alert').textContent).toBeTruthy());
  });

  it('hands the token to the caller on success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, token: 'tok-1' }), { status: 200 }))
    );
    const onJoined = vi.fn();
    render(<JoinGate onJoined={onJoined} />);
    type('K3P92QXA');
    fireEvent.click(submit());

    await waitFor(() => expect(onJoined).toHaveBeenCalledWith('tok-1'));
  });

  it('sends the code without the display dash', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true, token: 't' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<JoinGate onJoined={vi.fn()} />);
    type('K3P92QXA');
    fireEvent.click(submit());

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // The dash is a readability affordance for the human; the server compares
    // against the raw eight characters.
    expect(fetchBody(fetchMock)).toEqual({ code: 'K3P92QXA' });
  });
});
