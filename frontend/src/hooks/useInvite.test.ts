/**
 * Fetching the invite, and the auto-copy on open.
 *
 * Two properties are worth pinning. The toast must only claim a copy that
 * actually happened — a browser is entitled to refuse, and "copied" over an
 * empty clipboard stops the reader reaching for the buttons that would have
 * worked. And the fetch must repeat on every open, because the host can start a
 * tunnel mid-session and the link a remote teammate needs changes underneath a
 * board that is already running.
 */

import { act, renderHook, waitFor } from '@testing-library/preact';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { copyText } from '../runtime/clipboard';
import type { Session } from '../runtime/api';
import { useInvite } from './useInvite';

vi.mock('../runtime/clipboard', () => ({ copyText: vi.fn() }));
const mockCopy = vi.mocked(copyText);

const SESSION: Session = { token: 'tok', admin: '', pid: 'p1' };
const INVITE = { shareUrl: 'https://x.trycloudflare.com/', joinCode: 'K3P9-2QXA' };

function answerWith(body: unknown, ok = true): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => ({ ok, json: async () => body }) as unknown as Response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

beforeEach(() => {
  mockCopy.mockReset();
  mockCopy.mockResolvedValue(true);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useInvite', () => {
  it('fetches nothing until the panel opens', () => {
    const fetchMock = answerWith(INVITE);
    renderHook(() => useInvite(SESSION, false));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends the token — the endpoint is gated', async () => {
    const fetchMock = answerWith(INVITE);
    renderHook(() => useInvite(SESSION, true));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('token=tok');
  });

  it('copies the link and code together, and says so', async () => {
    answerWith(INVITE);
    const { result } = renderHook(() => useInvite(SESSION, true));

    await waitFor(() => expect(result.current.invite).toEqual(INVITE));
    expect(mockCopy).toHaveBeenCalledWith('https://x.trycloudflare.com/\nAccess code: K3P9-2QXA');
    await waitFor(() => expect(result.current.notice).toBe('Invite copied to your clipboard'));
  });

  it('stays silent when the browser refused the copy', async () => {
    mockCopy.mockResolvedValue(false);
    answerWith(INVITE);
    const { result } = renderHook(() => useInvite(SESSION, true));

    await waitFor(() => expect(result.current.invite).toEqual(INVITE));
    expect(result.current.notice).toBeNull();
  });

  it('refetches on reopen, so a tunnel started mid-session is picked up', async () => {
    const fetchMock = answerWith(INVITE);
    const { rerender } = renderHook<void, { open: boolean }>(({ open }) => void useInvite(SESSION, open), {
      initialProps: { open: true },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    rerender({ open: false });
    rerender({ open: true });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('survives the host closing the board while the panel opens', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new Error('connection refused')))
    );
    const { result } = renderHook(() => useInvite(SESSION, true));

    await act(async () => void (await Promise.resolve()));
    expect(result.current.invite).toBeNull();
    expect(result.current.notice).toBeNull();
  });

  it('does not raise a toast over a panel that has already closed', async () => {
    // The fetch resolving after unmount would otherwise set state on a dead
    // component and announce a copy nobody can see.
    answerWith(INVITE);
    const { result, unmount } = renderHook(() => useInvite(SESSION, true));
    unmount();

    await act(async () => void (await Promise.resolve()));
    expect(result.current.notice).toBeNull();
  });

  it('clears its toast on dismiss', async () => {
    answerWith(INVITE);
    const { result } = renderHook(() => useInvite(SESSION, true));
    await waitFor(() => expect(result.current.notice).toBeTruthy());

    act(() => result.current.dismiss());
    expect(result.current.notice).toBeNull();
  });
});
