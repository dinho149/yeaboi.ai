/**
 * The optimistic overlay's two hard cases: a confirmation that agrees, and a
 * server that never answers.
 */

import { act, render, screen } from '@testing-library/preact';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { usePendingOverlay } from './usePendingOverlay';

function Probe({ serverValue, onTimeout }: { serverValue: string; onTimeout?: () => void }) {
  const overlay = usePendingOverlay(serverValue, onTimeout ? { onTimeout } : {});
  return (
    <div>
      <span data-testid="value">{overlay.value}</span>
      <span data-testid="pending">{String(overlay.pending)}</span>
      <button type="button" onClick={() => overlay.set('8')}>
        vote 8
      </button>
      <button type="button" onClick={() => overlay.clear()}>
        clear
      </button>
    </div>
  );
}

describe('usePendingOverlay', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const value = (): string | null => screen.getByTestId('value').textContent;
  const pending = (): string | null => screen.getByTestId('pending').textContent;

  it('shows the server value when nothing is pending', () => {
    render(<Probe serverValue="3" />);
    expect(value()).toBe('3');
    expect(pending()).toBe('false');
  });

  it('shows the optimistic value immediately', () => {
    render(<Probe serverValue="3" />);
    act(() => screen.getByText('vote 8').click());
    expect(value()).toBe('8');
    expect(pending()).toBe('true');
  });

  it('hands over silently when the server confirms', () => {
    const { rerender } = render(<Probe serverValue="3" />);
    act(() => screen.getByText('vote 8').click());
    act(() => rerender(<Probe serverValue="8" />));

    expect(value()).toBe('8'); // same value either side — no flicker
    expect(pending()).toBe('false');
  });

  it('lets the server win when it disagrees', () => {
    // The usual cause is a host action — a revote, a lock — that should
    // visibly override what this browser was showing.
    const { rerender } = render(<Probe serverValue="3" />);
    act(() => screen.getByText('vote 8').click());
    act(() => rerender(<Probe serverValue="" />));

    expect(value()).toBe('');
    expect(pending()).toBe('false');
  });

  it('reverts and reports when the server never answers', () => {
    const onTimeout = vi.fn();
    render(<Probe serverValue="3" onTimeout={onTimeout} />);
    act(() => screen.getByText('vote 8').click());

    act(() => void vi.advanceTimersByTime(5000));

    expect(value()).toBe('3');
    expect(pending()).toBe('false');
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });

  it('does not arm a timeout for a value the server already holds', () => {
    // Re-selecting your existing vote has nothing to show optimistically, and
    // an echo of an identical value would not resolve the wait — so this would
    // otherwise fire a spurious timeout five seconds later.
    const onTimeout = vi.fn();
    render(<Probe serverValue="8" onTimeout={onTimeout} />);
    act(() => screen.getByText('vote 8').click());

    expect(pending()).toBe('false');
    act(() => void vi.advanceTimersByTime(10000));
    expect(onTimeout).not.toHaveBeenCalled();
  });

  it('clear() abandons the overlay without firing the timeout', () => {
    const onTimeout = vi.fn();
    render(<Probe serverValue="3" onTimeout={onTimeout} />);
    act(() => screen.getByText('vote 8').click());
    act(() => screen.getByText('clear').click());

    expect(value()).toBe('3');
    act(() => void vi.advanceTimersByTime(10000));
    expect(onTimeout).not.toHaveBeenCalled();
  });
});
