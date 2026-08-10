/**
 * What a role is allowed to be *offered*.
 *
 * The server decides what a role may actually do, and that is tested in
 * `test_app_substrate.py`. This pins the other half: rendering a delete button
 * an editor cannot use teaches them the app is broken, where hiding it teaches
 * them what their role is.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it, vi } from 'vitest';

import { ProjectAdmin } from './ProjectAdmin';
import type { ProjectDetail, Role } from './types';

function project(role: Role): ProjectDetail {
  return {
    id: 'prj_1',
    name: 'Payments',
    role,
    created_at: 0,
    updated_at: 0,
    members: [],
  };
}

function renderAs(role: Role) {
  return render(<ProjectAdmin project={project(role)} onChanged={vi.fn()} notify={vi.fn()} />);
}

describe('<ProjectAdmin>', () => {
  it('shows an owner everything', () => {
    renderAs('owner');
    expect(screen.getByRole('button', { name: 'Rename' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete this project' })).toBeTruthy();
  });

  it('lets an editor rename but not invite or destroy', () => {
    renderAs('editor');
    expect(screen.getByRole('button', { name: 'Rename' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Add' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete this project' })).toBeNull();
  });

  it('offers a viewer nothing at all', () => {
    const { container } = renderAs('viewer');
    expect(container.textContent).toBe('');
  });

  it('does not offer owner as a role to hand out', () => {
    // Promoting someone to owner is a transfer, not an invite, and the store
    // has no notion of demoting the previous one.
    renderAs('owner');
    const options = screen.getAllByRole('option').map((node) => node.textContent);
    expect(options).toEqual(['editor', 'viewer']);
  });

  it('keeps Rename disabled until the name actually changes', () => {
    // Otherwise the obvious first click is a no-op request.
    renderAs('owner');
    expect(screen.getByRole('button', { name: 'Rename' })).toHaveProperty('disabled', true);
  });
});
