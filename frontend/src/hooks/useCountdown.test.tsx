/**
 * The shared timer readout.
 *
 * The clock-offset behaviour is the interesting part: the countdown is driven
 * by the *server's* clock, which is what lets `state_etag` exclude
 * `timer.now_epoch`. If it did not, the ETag would change on every request and
 * long-polling would collapse back into a busy poll.
 */

import { act, render, screen } from '@testing-library/preact';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCountdown, type TimerState } from './useCountdown';

function Probe({ timer, onFinish }: { timer: TimerState | null; onFinish?: () => void }) {
  const { remaining, finished } = useCountdown(timer, onFinish);
  return (
    <div>
      <span data-testid="remaining">{remaining === null ? 'none' : String(remaining)}</span>
      <span data-testid="finished">{String(finished)}</span>
    </div>
  );
}

const NOW = 1_700_000_000_000; // fixed wall clock, ms
const remaining = (): string | null => screen.getByTestId('remaining').textContent;

describe('useCountdown', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it('shows nothing when no timer is running', () => {
    render(<Probe timer={{ running: false }} />);
    expect(remaining()).toBe('none');
  });

  it('counts down from the server end time', () => {
    render(
      <Probe timer={{ running: true, end_epoch: NOW / 1000 + 90, now_epoch: NOW / 1000 }} />
    );
    expect(remaining()).toBe('90');

    act(() => void vi.advanceTimersByTime(5000));
    expect(remaining()).toBe('85');
  });

  it('uses the server clock, not the browser clock', () => {
    // A laptop two minutes fast must still show the same remaining time as
    // everyone else's — that is the whole point of a shared ceremony timer.
    const serverNow = NOW / 1000 - 120; // browser is 2 minutes ahead of the server
    render(<Probe timer={{ running: true, end_epoch: serverNow + 60, now_epoch: serverNow }} />);
    expect(remaining()).toBe('60');
  });

  it('floors at zero rather than counting into negatives', () => {
    render(<Probe timer={{ running: true, end_epoch: NOW / 1000 + 2, now_epoch: NOW / 1000 }} />);
    act(() => void vi.advanceTimersByTime(10_000));

    expect(remaining()).toBe('0');
    expect(screen.getByTestId('finished').textContent).toBe('true');
  });

  it('fires onFinish exactly once per timer', () => {
    // Keyed on end_epoch, not on "remaining hit zero" — otherwise the confetti
    // and the alarm re-fire four times a second for as long as the finished
    // timer stays on screen.
    const onFinish = vi.fn();
    render(<Probe timer={{ running: true, end_epoch: NOW / 1000 + 1, now_epoch: NOW / 1000 }} onFinish={onFinish} />);

    act(() => void vi.advanceTimersByTime(10_000));
    expect(onFinish).toHaveBeenCalledTimes(1);
  });

  it('fires again for a new timer', () => {
    const onFinish = vi.fn();
    const { rerender } = render(
      <Probe timer={{ running: true, end_epoch: NOW / 1000 + 1, now_epoch: NOW / 1000 }} onFinish={onFinish} />
    );
    act(() => void vi.advanceTimersByTime(3000));

    rerender(
      <Probe timer={{ running: true, end_epoch: NOW / 1000 + 5, now_epoch: NOW / 1000 }} onFinish={onFinish} />
    );
    act(() => void vi.advanceTimersByTime(10_000));

    expect(onFinish).toHaveBeenCalledTimes(2);
  });

  it('clears when the host stops the timer', () => {
    const { rerender } = render(
      <Probe timer={{ running: true, end_epoch: NOW / 1000 + 90, now_epoch: NOW / 1000 }} />
    );
    rerender(<Probe timer={{ running: false }} />);
    expect(remaining()).toBe('none');
  });
});
