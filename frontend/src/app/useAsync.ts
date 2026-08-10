/**
 * The four states every remote read actually has.
 *
 * Loading / empty / error are usually bolted on per screen and then drift: one
 * table spins forever on a 500, another renders "0 results" for a failed fetch.
 * Making them a single typed union means a screen cannot forget one — the
 * exhaustive switch in `AsyncView` will not compile if it does.
 *
 * `empty` is deliberately distinct from `ready`. "The request worked and there
 * is nothing" is a different thing to say than "here are your results", and
 * collapsing them is how an empty state ends up looking like a bug.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: string; retry: () => void }
  | { status: 'empty' }
  | { status: 'ready'; data: T };

export interface AsyncOptions<T> {
  /** Called with the loaded value; return true when it counts as empty. */
  isEmpty?: (data: T) => boolean;
}

export function useAsync<T>(
  load: () => Promise<{ ok: true; data: T } | { ok: false; status: number; error: string }>,
  deps: unknown[],
  { isEmpty }: AsyncOptions<T> = {},
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' });
  const [nonce, setNonce] = useState(0);
  // Guards against a slow first response overwriting a fast second one.
  const generation = useRef(0);

  const run = useCallback(() => {
    const mine = ++generation.current;
    setState({ status: 'loading' });
    const fail = (error: string) =>
      setState({ status: 'error', error, retry: () => setNonce((n) => n + 1) });

    void load()
      .then((result) => {
        if (mine !== generation.current) return;
        if (!result.ok) {
          fail(result.error);
          return;
        }
        // `isEmpty` reads into the payload, so a response of an unexpected
        // shape throws here. Caught rather than left to become an unhandled
        // rejection: the four states are this hook's whole contract, and a
        // throw escaping it leaves a screen stuck on 'loading' forever with
        // the reason only in the console.
        let empty = false;
        try {
          empty = isEmpty?.(result.data) ?? false;
        } catch {
          fail('the server sent something unexpected');
          return;
        }
        setState(empty ? { status: 'empty' } : { status: 'ready', data: result.data });
      })
      .catch(() => {
        if (mine !== generation.current) return;
        fail('something went wrong loading this');
      });
    // `load` is a fresh closure every render; deps are the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    run();
    return () => {
      // Abandon in-flight work on unmount so a late resolve cannot setState.
      generation.current++;
    };
  }, [run, nonce]);

  return state;
}
