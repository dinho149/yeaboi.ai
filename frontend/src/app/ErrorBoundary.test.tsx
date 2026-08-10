/**
 * The boundary between a render-time throw and a white page.
 *
 * `useAsync` covers failures while fetching. This covers the other kind — a
 * component that throws while drawing — which otherwise unmounts the tree and
 * leaves a blank window with the reason in a console nobody opens.
 */

import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ErrorBoundary } from './ErrorBoundary';

function Boom({ throws }: { throws: boolean }): preact.JSX.Element {
  if (throws) throw new Error('payload shape drifted');
  return <p>drew fine</p>;
}

beforeEach(() => {
  // The boundary logs deliberately; keep the suite output readable.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe('<ErrorBoundary>', () => {
  it('renders its children when nothing throws', () => {
    render(
      <ErrorBoundary>
        <Boom throws={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('drew fine')).toBeTruthy();
  });

  it('shows a fallback instead of unmounting to a blank page', () => {
    render(
      <ErrorBoundary>
        <Boom throws />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText('payload shape drifted')).toBeTruthy();
  });

  it('records the error somewhere a developer can find it', () => {
    render(
      <ErrorBoundary>
        <Boom throws />
      </ErrorBoundary>,
    );
    expect(console.error).toHaveBeenCalled();
  });

  it('recovers when the reset key changes', () => {
    // Otherwise one broken screen poisons every later one: the boundary stays
    // in its error state and renders the fallback for pages that are fine.
    const { rerender } = render(
      <ErrorBoundary resetKey="/a">
        <Boom throws />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeTruthy();
    rerender(
      <ErrorBoundary resetKey="/b">
        <Boom throws={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('drew fine')).toBeTruthy();
  });

  it('lets the fallback retry in place', async () => {
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error('once');
      return <p>recovered</p>;
    }
    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    shouldThrow = false;
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(screen.getByText('recovered')).toBeTruthy();
  });

  it('uses a caller-supplied fallback when given one', () => {
    render(
      <ErrorBoundary fallback={(error) => <p>custom: {error.message}</p>}>
        <Boom throws />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/custom: payload shape drifted/)).toBeTruthy();
  });
});
