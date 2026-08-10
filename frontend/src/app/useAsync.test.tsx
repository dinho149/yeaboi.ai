/**
 * `useAsync` promises four states. These pin that nothing escapes them.
 */

import { renderHook, waitFor } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import { useAsync } from './useAsync';


describe('useAsync error containment', () => {
  it('turns a throw inside isEmpty into the error state, not an unhandled rejection', async () => {
    // The four states are this hook's whole contract. A throw escaping it
    // leaves the screen on 'loading' forever with the reason only in a console
    // nobody is reading.
    const { result } = renderHook(() =>
      useAsync(
        () => Promise.resolve({ ok: true as const, data: {} as { rows: number[] } }),
        [],
        { isEmpty: (data) => data.rows.length === 0 },
      ),
    );
    await waitFor(() => expect(result.current.status).toBe('error'));
  });

  it('turns a rejected load into the error state', async () => {
    const { result } = renderHook(() => useAsync(() => Promise.reject(new Error('boom')), []));
    await waitFor(() => expect(result.current.status).toBe('error'));
  });

  it('still reports empty when isEmpty says so', async () => {
    const { result } = renderHook(() =>
      useAsync(() => Promise.resolve({ ok: true as const, data: { rows: [] } }), [], {
        isEmpty: (data) => data.rows.length === 0,
      }),
    );
    await waitFor(() => expect(result.current.status).toBe('empty'));
  });
});
