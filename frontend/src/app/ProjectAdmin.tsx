/**
 * Managing a project: who is on it, what it is called, and destroying it.
 *
 * The store has had owner/editor/viewer since the substrate commit, with
 * membership scoping on every read and tests to match — and no way to add a
 * member from a browser. That is the third time this shape has appeared (sign
 * in, import, and now this): an API that works, and nothing that reaches it.
 *
 * Role gating is done here as well as on the server, and the server is the one
 * that matters — this only decides what to *offer*. Rendering a delete button
 * an editor cannot use teaches them the app is broken; hiding it teaches them
 * what their role is.
 *
 * TODO(design): three stacked rows and a confirm dialog. Where project
 * administration lives — this screen, a settings tab, a menu — is a product
 * decision none of this forecloses.
 */

import { useState } from 'react';

import { Button, Field, Input, Modal, Select } from '../design/primitives';
import { del, post } from './api';
import { navigate } from './router';
import type { ProjectDetail, Role } from './types';
import styles from './app.module.css';

const ROLES: Role[] = ['editor', 'viewer'];

export function ProjectAdmin({
  project,
  onChanged,
  notify,
}: {
  project: ProjectDetail;
  onChanged: () => void;
  notify: (message: string) => void;
}) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('editor');
  const [name, setName] = useState(project.name);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [confirming, setConfirming] = useState(false);

  const isOwner = project.role === 'owner';
  const canEdit = project.role !== 'viewer';

  async function invite(event: Event) {
    event.preventDefault();
    setBusy('invite');
    setError('');
    const result = await post(`/api/projects/${project.id}/members`, { email, role });
    setBusy('');
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setEmail('');
    notify(`Added ${email}`);
    onChanged();
  }

  async function rename(event: Event) {
    event.preventDefault();
    setBusy('rename');
    const result = await post(`/api/projects/${project.id}`, { name });
    setBusy('');
    if (!result.ok) {
      notify(result.error);
      return;
    }
    notify('Renamed');
    onChanged();
  }

  async function destroy() {
    setBusy('delete');
    const result = await del(`/api/projects/${project.id}`);
    setBusy('');
    setConfirming(false);
    if (!result.ok) {
      notify(result.error);
      return;
    }
    // Back to the list: staying on a project that no longer exists would show
    // an error where a completed action belongs.
    navigate('/projects');
  }

  if (!canEdit) return null;

  return (
    <section className={styles.settings}>
      <form className={styles.settingRow} onSubmit={rename}>
        <Field label="Project name">
          {(props) => (
            <Input {...props} value={name} onInput={(e) => setName((e.target as HTMLInputElement).value)} />
          )}
        </Field>
        <div>
          <Button type="submit" size="s" disabled={busy === 'rename' || name.trim() === project.name}>
            Rename
          </Button>
        </div>
      </form>

      {isOwner ? (
        <form className={styles.settingRow} onSubmit={invite}>
          <Field label="Add someone" error={error}>
            {(props) => (
              <Input
                {...props}
                type="email"
                placeholder="them@example.com"
                value={email}
                onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
              />
            )}
          </Field>
          <div className={styles.themeRow}>
            <Select value={role} onChange={(e) => setRole((e.target as HTMLSelectElement).value as Role)}>
              {ROLES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </Select>
            <Button type="submit" tone="primary" size="s" disabled={busy === 'invite'}>
              Add
            </Button>
          </div>
        </form>
      ) : null}

      {isOwner ? (
        <div className={styles.settingRow}>
          <span className={styles.label}>Danger</span>
          <div>
            <Button tone="danger" size="s" onClick={() => setConfirming(true)}>
              Delete this project
            </Button>
          </div>
        </div>
      ) : null}

      <Modal
        open={confirming}
        onClose={() => setConfirming(false)}
        title={`Delete ${project.name}?`}
        actions={
          <>
            <Button onClick={() => setConfirming(false)}>Cancel</Button>
            <Button tone="danger" disabled={busy === 'delete'} onClick={destroy}>
              Delete
            </Button>
          </>
        }
      >
        <p>
          Its documents and rooms go with it. This cannot be undone.
        </p>
      </Modal>
    </section>
  );
}
