/**
 * The structural slots: loading, empty, error, and the switch that picks one.
 *
 * TODO(design): everything here is deliberately unstyled beyond tokens. These
 * are the *positions* a state occupies, not its appearance — the design pass
 * replaces the internals and the call sites do not change. Nothing in this file
 * may carry a colour, a font stack, or a spacing literal; it composes tokens or
 * it composes nothing.
 */

import type { ComponentChildren } from 'preact';
import { Button } from '../design/primitives';
import type { AsyncState } from './useAsync';
import styles from './app.module.css';

export function Loading({ label = 'Loading' }: { label?: string }) {
  // aria-busy rather than a spinner: a screen reader should hear the state, and
  // the visual treatment is the design pass's call.
  return (
    <div className={styles.slot} role="status" aria-busy="true">
      <span className={styles.slotLabel}>{label}</span>
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ComponentChildren;
}) {
  return (
    <div className={styles.slot}>
      <p className={styles.slotTitle}>{title}</p>
      {hint ? <p className={styles.slotHint}>{hint}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: string; retry?: () => void }) {
  return (
    <div className={styles.slot} role="alert">
      <p className={styles.slotTitle}>Something did not load</p>
      <p className={styles.slotHint}>{error}</p>
      {retry ? <Button onClick={retry}>Try again</Button> : null}
    </div>
  );
}

/**
 * Render one of the four states.
 *
 * The `empty` branch is required rather than optional: a caller that has not
 * decided what "nothing here" looks like has not finished the screen.
 */
export function AsyncView<T>({
  state,
  empty,
  children,
}: {
  state: AsyncState<T>;
  empty: ComponentChildren;
  children: (data: T) => ComponentChildren;
}) {
  switch (state.status) {
    case 'loading':
      return <Loading />;
    case 'error':
      return <ErrorState error={state.error} retry={state.retry} />;
    case 'empty':
      return <>{empty}</>;
    case 'ready':
      return <>{children(state.data)}</>;
  }
}
