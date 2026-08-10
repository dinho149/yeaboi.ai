/**
 * Accessibility floor for the app's own screens.
 *
 * `shared/a11y.test.tsx` does this for the component library and stops at its
 * edge, so the surfaces assembled *out of* those components — the shell, the
 * settings page, project administration, the two dialogs — had no such floor.
 * That is the wrong way round: a primitive with a correct label can still be
 * composed into a screen with two <h1>s, a form with no accessible name, or a
 * dialog that traps nothing.
 *
 * Same contract as the shared file: this is a floor, not a substitute for the
 * behavioural assertions beside it. axe cannot tell you that Escape closes the
 * right dialog; it can tell you the dialog has no name.
 */

import { render } from '@testing-library/preact';
import { describe, expect, it, vi } from 'vitest';
import { axe } from 'vitest-axe';

import { ImportDialog } from './Import';
import { NewProjectDialog } from './NewProject';
import { ProjectAdmin } from './ProjectAdmin';
import { Settings } from './Settings';
import { EmptyState, ErrorState, Loading } from './Slots';
import type { ProjectDetail, User } from './types';

const user: User = { id: 'usr_1', email: 'ada@example.com', name: 'Ada' };

const project: ProjectDetail = {
  id: 'prj_1',
  name: 'Payments',
  role: 'owner',
  created_at: 0,
  updated_at: 0,
  members: [{ id: 'usr_1', email: 'ada@example.com', name: 'Ada', role: 'owner' }],
};

const noop = () => {};

const CASES: [string, preact.ComponentChildren][] = [
  ['Loading', <Loading />],
  ['EmptyState', <EmptyState title="Nothing yet" hint="It will show up here." />],
  ['ErrorState', <ErrorState error="could not reach the server" retry={noop} />],
  ['Settings', <Settings user={user} onSignedOut={noop} notify={noop} />],
  ['ProjectAdmin', <ProjectAdmin project={project} onChanged={noop} notify={noop} />],
  ['NewProjectDialog', <NewProjectDialog open onClose={noop} onCreated={noop} />],
  ['ImportDialog', <ImportDialog open onClose={noop} onImported={noop} notify={noop} />],
];

describe('app screens have no axe violations', () => {
  it.each(CASES)('%s', async (_name, node) => {
    // The dialogs fetch on mount. The body is the real shape rather than {}:
    // a stub that does not match what the server sends tests the error path by
    // accident, which is how this file first surfaced a crash in useAsync.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ projects: [], artifacts: [], rooms: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    const { container } = render(<>{node}</>);
    expect(await axe(container)).toHaveNoViolations();
    vi.unstubAllGlobals();
  });
});
