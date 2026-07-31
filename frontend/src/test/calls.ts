/**
 * Reading a mock's recorded arguments without fighting the type checker.
 *
 * `tsconfig` runs with `noUncheckedIndexedAccess`, so `mock.calls[0][1]` is
 * `T | undefined` — correct in general, and pure noise in a test that has just
 * asserted the call happened. These helpers narrow it once, and throw with a
 * useful message instead of the `Object is possibly 'undefined'` you would
 * otherwise silence with a non-null assertion.
 */

/**
 * Anything with recorded calls.
 *
 * Structural rather than vitest's `Mock`: the fetch fakes here carry extra
 * fields (`maxConcurrent`), and the nominal type rejects the intersection.
 */
interface Recorded {
  mock: { calls: unknown[][] };
}

/** The arguments of the nth recorded call (0-based). Throws if it never happened. */
export function callArgs(mock: Recorded, index = 0): unknown[] {
  const call = mock.mock.calls[index];
  if (!call) throw new Error(`expected at least ${index + 1} call(s), got ${mock.mock.calls.length}`);
  return call;
}

/** One argument of one recorded call, typed by the caller. */
export function callArg<T = unknown>(mock: Recorded, callIndex: number, argIndex: number): T {
  const args = callArgs(mock, callIndex);
  if (argIndex >= args.length) {
    throw new Error(`call ${callIndex} has ${args.length} argument(s), wanted index ${argIndex}`);
  }
  return args[argIndex] as T;
}

/** The `RequestInit` a fetch mock was called with. */
export function fetchInit(mock: Recorded, callIndex = 0): RequestInit {
  return callArg<RequestInit>(mock, callIndex, 1);
}

/** The URL a fetch mock was called with. */
export function fetchUrl(mock: Recorded, callIndex = 0): string {
  return callArg<string>(mock, callIndex, 0);
}

/** The parsed JSON body a fetch mock was called with. */
export function fetchBody<T = unknown>(mock: Recorded, callIndex = 0): T {
  return JSON.parse(String(fetchInit(mock, callIndex).body)) as T;
}
