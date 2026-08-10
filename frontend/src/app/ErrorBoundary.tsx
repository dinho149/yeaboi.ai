/**
 * The last thing between a render-time throw and a white page.
 *
 * `useAsync` catches everything that happens while *fetching*, which covers the
 * common failures. It cannot catch a component that throws while *drawing* —
 * a report payload with a field the renderer did not expect, a null where a
 * list was assumed. When that happens Preact unmounts the tree, and the user
 * gets a blank window with the reason in a console they will never open.
 *
 * That risk is not hypothetical here: `Report.tsx` draws ten payload kinds from
 * data the server stored months earlier, and the app renders whatever an
 * exporter wrote at the time. A shape that has since drifted is exactly the
 * case this catches.
 *
 * Scoped rather than global on purpose. Wrapping the whole shell would mean a
 * broken report takes the navigation with it, leaving nowhere to go but a
 * reload. Wrapping the content region leaves the rail and the masthead alive,
 * so the answer to a broken screen is clicking somewhere else.
 *
 * TODO(design): the fallback is a heading, the message, and a button.
 */

import { Component, type ComponentChildren } from 'preact';

interface Props {
  children: ComponentChildren;
  /** Changing this resets the boundary — the router passes the path. */
  resetKey?: unknown;
  fallback?: (error: Error, reset: () => void) => ComponentChildren;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error): void {
    // Kept: the console is useless to the user and the only record for anyone
    // debugging a report that will not draw.
    console.error('yeaboi: a screen failed to render', error);
  }

  componentDidUpdate(previous: Props): void {
    // Without this a single broken screen poisons every later one: the boundary
    // stays in its error state, so navigating away renders the fallback again
    // for a page that is perfectly fine.
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const reset = () => this.setState({ error: null });
    if (this.props.fallback) return this.props.fallback(error, reset);
    return (
      <div role="alert">
        <p>This screen could not be drawn.</p>
        <p>{error.message}</p>
        <button type="button" onClick={reset}>
          Try again
        </button>
      </div>
    );
  }
}
