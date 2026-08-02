/**
 * The join gate — the only page an unauthenticated stranger reaches.
 *
 * Two things are being pinned here, and both are about the same limiter.
 *
 * The error mapping: `JoinLimiter` locks an IP out for five minutes after eight
 * failures, and a visitor told only "that code did not match" will retype the
 * *correct* code, fail again, and conclude the host sent them a broken link.
 *
 * And the auto-submit. An invite link carries its code in the fragment, so a
 * *stale* link — the host restarted, the code changed — is a wrong code that
 * re-submits itself every time someone clicks it. Left alone that walks an IP
 * to 8/8 with nobody typing anything, so the tests below count `fetch` calls as
 * carefully as they read messages.
 */

import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchBody } from '../test/calls';
import { JoinGate, normalizeCode, readInviteCode } from './JoinGate';

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  history.replaceState(null, '', '/');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const field = (): HTMLInputElement => screen.getByLabelText(/access code/i) as HTMLInputElement;
const submit = (): HTMLElement => screen.getByRole('button', { name: /open/i });

/** Land on the page as if the invite link had just been clicked. */
function arriveWith(url: string): void {
  history.replaceState(null, '', url);
}

/** A fetch that always answers `status`, recording every call. */
function answering(status: number, body = ''): ReturnType<typeof vi.fn> {
  const mock = vi.fn().mockResolvedValue(new Response(body, { status }));
  vi.stubGlobal('fetch', mock);
  return mock;
}

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
  it('falls back to the brand when no mode is given', async () => {
    // The neutral rendition still has to work: `performance` shares stay
    // anonymous by policy, and a gate served before branding — or `dev/gate.html`
    // — has no island to read a mode out of.
    render(<JoinGate />);
    expect(screen.getByRole('img', { name: 'yeaboi' })).toBeTruthy();
    expect(screen.queryByRole('img', { name: /retro|poker|standup/i })).toBeNull();
  });

  it('names the mode it was given', () => {
    render(<JoinGate wordmark="retro" heading="Join the retro" cta="Join" />);
    expect(screen.getByRole('img', { name: 'retro' })).toBeTruthy();
  });

  it('carries the same credit as every other surface', () => {
    render(<JoinGate />);
    expect(screen.getByText('Generated by yeaboi.ai')).toBeTruthy();
  });

  it('keeps the credit out of the main landmark', () => {
    // contentinfo nested inside main is an axe violation, and this component is
    // rendered bare in the a11y suite.
    const { container } = render(<JoinGate />);
    expect(container.querySelector('main footer')).toBeNull();
    expect(container.querySelector('footer')).toBeTruthy();
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

describe('readInviteCode', () => {
  it('reads the fragment invite_url emits', () => {
    arriveWith('/#code=K3P9-2QXA');
    expect(readInviteCode()).toBe('K3P9-2QXA');
  });

  it('normalizes whatever survived the trip', () => {
    // A link can be lowercased by a chat client, or lose its dash to a
    // percent-encoder. Same eight characters either way.
    arriveWith('/#code=k3p92qxa');
    expect(readInviteCode()).toBe('K3P9-2QXA');
    arriveWith('/#code=K3P9%2D2QXA');
    expect(readInviteCode()).toBe('K3P9-2QXA');
  });

  it('accepts the query form nothing here emits', () => {
    // The fallback for a client that relocates or drops fragments. Read-only:
    // a query code has already reached cloudflared's log by now, which is why
    // invite_url never produces one.
    arriveWith('/?code=K3P9-2QXA');
    expect(readInviteCode()).toBe('K3P9-2QXA');
  });

  it('finds the code among other fragment params', () => {
    arriveWith('/#theme=midnight&code=K3P9-2QXA');
    expect(readInviteCode()).toBe('K3P9-2QXA');
  });

  it('is empty for an ordinary visit', () => {
    arriveWith('/');
    expect(readInviteCode()).toBe('');
    arriveWith('/#section');
    expect(readInviteCode()).toBe('');
  });
});

describe('JoinGate, arrived on an invite link', () => {
  it('joins with no click at all', async () => {
    arriveWith('/#code=K3P9-2QXA');
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true, token: 't' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const onJoined = vi.fn();

    render(<JoinGate onJoined={onJoined} />);

    await waitFor(() => expect(onJoined).toHaveBeenCalledWith('t'));
    // The dashed form is the code — see the note on `digits`.
    expect(fetchBody(fetchMock)).toEqual({ code: 'K3P9-2QXA' });
  });

  it('shows the code it is using, so a failure is diagnosable', async () => {
    arriveWith('/#code=K3P9-2QXA');
    answering(403);
    render(<JoinGate />);
    await waitFor(() => expect(field().value).toBe('K3P9-2QXA'));
  });

  it('takes the query form too', async () => {
    arriveWith('/?code=K3P9-2QXA');
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true, token: 't' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const onJoined = vi.fn();

    render(<JoinGate onJoined={onJoined} />);
    await waitFor(() => expect(onJoined).toHaveBeenCalled());
  });

  it('clears the code from the address bar before it asks', async () => {
    // Before, not after. This is the guard that makes a reload after a
    // rejection cost nothing — captured inside the fetch so a strip that
    // happened in the response handler would fail here.
    arriveWith('/#code=K3P9-2QXA');
    let hashDuringRequest = 'not captured';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        hashDuringRequest = location.hash;
        return new Response('', { status: 403 });
      })
    );

    render(<JoinGate />);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBeTruthy());
    expect(hashDuringRequest).toBe('');
    expect(location.hash).toBe('');
  });

  it('keeps the rest of the fragment', async () => {
    arriveWith('/#theme=midnight&code=K3P9-2QXA');
    answering(403);
    render(<JoinGate />);
    await waitFor(() => expect(location.hash).toBe('#theme=midnight'));
  });

  it('says the link is stale, not that the code was mistyped', async () => {
    // Two different problems behind one 403. Nobody typed this code, so telling
    // the reader to check their typing sends them to fix the wrong thing.
    arriveWith('/#code=K3P9-2QXA');
    answering(403);
    render(<JoinGate />);

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/out of date/i));
    expect(screen.getByRole('alert').textContent).not.toMatch(/did not match/i);
  });

  it('does not startle the duck at a visitor who did nothing', async () => {
    arriveWith('/#code=K3P9-2QXA');
    answering(403);
    const { container } = render(<JoinGate />);

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/out of date/i));
    expect(container.querySelector('[data-state]')?.getAttribute('data-state')).toBe('idle');
  });

  it('still reports a lockout as a lockout', async () => {
    arriveWith('/#code=K3P9-2QXA');
    answering(429);
    render(<JoinGate />);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/wait a few minutes/i));
  });

  it('asks once per mount, even under StrictMode', async () => {
    // The latch must not depend on StrictMode being aliased away in this build.
    arriveWith('/#code=K3P9-2QXA');
    const fetchMock = answering(403);
    render(
      <StrictMode>
        <JoinGate />
      </StrictMode>
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('costs nothing when the same dead link is clicked again in a new tab', async () => {
    // The vector the URL strip cannot cover, and the one that actually happens:
    // clicking a link in Slack opens a *fresh tab*, so the fragment comes back
    // and nothing per-tab remembers the last attempt. Six colleagues clicking
    // one dead link once each is six real attempts, and eight behind an office
    // NAT is a five-minute lockout that then rejects the working link the host
    // sends to fix it. `sessionStorage.clear()` below is that new tab — the memo
    // has to survive it, which is why it lives in localStorage.
    arriveWith('/#code=K3P9-2QXA');
    const first = answering(403);
    const { unmount } = render(<JoinGate />);
    await waitFor(() => expect(first).toHaveBeenCalledTimes(1));
    unmount();

    sessionStorage.clear();
    arriveWith('/#code=K3P9-2QXA');
    const second = answering(403);
    render(<JoinGate />);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/out of date/i));
    expect(second).not.toHaveBeenCalled();
    // Cleaned even on the path that asks nothing — a dead code left in the
    // address bar just rides into the next screenshot.
    expect(location.hash).toBe('');
  });

  it('remembers only the code that failed, not invites in general', async () => {
    // The memo outlives the tab, so it must be unable to block a good link. A
    // host who restarts issues a different code, which is the case here.
    arriveWith('/#code=K3P9-2QXA');
    answering(403);
    const { unmount } = render(<JoinGate />);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/out of date/i));
    unmount();

    sessionStorage.clear();
    arriveWith('/#code=M4T7-8WPD');
    const onJoined = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true, token: 't' }), { status: 200 }))
    );
    render(<JoinGate onJoined={onJoined} />);
    await waitFor(() => expect(onJoined).toHaveBeenCalledWith('t'));
  });

  it('does not remember a code that was only rate-limited', async () => {
    // A 429 says nothing about the code. Poisoning the tab over one would
    // strand a visitor whose link is perfectly good.
    arriveWith('/#code=K3P9-2QXA');
    const first = answering(429);
    const { unmount } = render(<JoinGate />);
    await waitFor(() => expect(first).toHaveBeenCalledTimes(1));
    unmount();

    arriveWith('/#code=K3P9-2QXA');
    const second = answering(429);
    render(<JoinGate />);
    await waitFor(() => expect(second).toHaveBeenCalledTimes(1));
  });

  it('stays out of the way when the browser already has a token', async () => {
    arriveWith('/?token=already&code=K3P9-2QXA');
    const fetchMock = answering(403);
    render(<JoinGate />);

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetchMock).not.toHaveBeenCalled();
    // Still cleaned. This is the static gate served to a visitor whose token
    // was rejected; leaving a live code in their address bar for the whole
    // visit is the thing the fragment was chosen to avoid.
    await waitFor(() => expect(location.search).toBe(''));
    expect(location.hash).toBe('');
  });

  it('prefills but does not submit a truncated code', async () => {
    arriveWith('/#code=K3P9');
    const fetchMock = answering(403);
    render(<JoinGate />);

    await waitFor(() => expect(field().value).toBe('K3P9'));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(submit()).toHaveProperty('disabled', true);
  });

  it('lets the visitor type over a stale code', async () => {
    arriveWith('/#code=K3P9-2QXA');
    const fetchMock = answering(403);
    render(<JoinGate />);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/out of date/i));

    fireEvent.input(field(), { target: { value: 'M4T7-8WPD' } });
    fireEvent.click(submit());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    // The manual retry is a manual attempt: a wrong one is a typo again.
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/did not match/i));
  });
});
