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
const submit = (): HTMLElement => screen.getByRole('button', { name: /open/i });

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

  it('sends the code in the XXXX-XXXX form the server issued', async () => {
    // Regression: this sent the de-hyphenated code, so every join 403'd. The
    // servers compare with compare_digest against make_join_code()'s output,
    // which contains the dash — see TestJoinCodeWireFormat in
    // tests/unit/test_sharing_gate.py, which pins the same contract from the
    // side that owns it.
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true, token: 't' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<JoinGate onJoined={vi.fn()} />);
    type('K3P92QXA');
    fireEvent.click(submit());

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchBody(fetchMock)).toEqual({ code: 'K3P9-2QXA' });
  });

  it('normalizes a code pasted without the dash back into the wire form', async () => {
    // The host reads it out over a call and the visitor types eight bare
    // characters. normalizeCode re-inserts the dash, so this still matches.
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true, token: 't' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<JoinGate onJoined={vi.fn()} />);
    type('k3p92qxa');
    fireEvent.click(submit());

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchBody(fetchMock)).toEqual({ code: 'K3P9-2QXA' });
  });
});

describe('JoinGate identity', () => {
  it('defaults to the brand, never a mode', async () => {
    // `sharing/gate.py` serves a document with no per-share information in it:
    // an unauthenticated visitor must not learn what is behind the gate. The
    // block wordmark is the most tempting place to put "RETRO", so the default
    // is pinned here rather than left to whoever next edits the component.
    render(<JoinGate />);
    expect(screen.getByRole('img', { name: 'yeaboi' })).toBeTruthy();
    expect(screen.queryByRole('img', { name: /retro|poker|standup/i })).toBeNull();
  });

  it('lets a board name its own mode', () => {
    // The boards may: their page title already says it.
    render(<JoinGate wordmark="retro" heading="Join the retro" cta="Join" />);
    expect(screen.getByRole('img', { name: 'retro' })).toBeTruthy();
  });

  it('sets the duck off when the code is refused', async () => {
    // The gate's worst moment, turned into its most memorable one.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 403 })));
    const { container } = render(<JoinGate />);
    expect(container.querySelector('[data-state]')?.getAttribute('data-state')).toBe('idle');

    fireEvent.input(field(), { target: { value: 'K3P92QXA' } });
    fireEvent.click(screen.getByRole('button', { name: /open/i }));

    await waitFor(() =>
      expect(container.querySelector('[data-state]')?.getAttribute('data-state')).toBe('startled')
    );
  });

  it('leaves the duck alone when the code is right', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, token: 't' }), { status: 200 }))
    );
    const onJoined = vi.fn();
    const { container } = render(<JoinGate onJoined={onJoined} />);
    fireEvent.input(field(), { target: { value: 'K3P92QXA' } });
    fireEvent.click(screen.getByRole('button', { name: /open/i }));

    await waitFor(() => expect(onJoined).toHaveBeenCalled());
    expect(container.querySelector('[data-state]')?.getAttribute('data-state')).toBe('idle');
  });
});
