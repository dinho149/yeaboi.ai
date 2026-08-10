/**
 * Creating a project.
 *
 * This replaces a `window.prompt`, which was left as a placeholder and is worse
 * than a placeholder for three reasons that are not taste:
 *
 * * It is **not guaranteed to exist**. `prompt` is only available if the host
 *   webview implements the text-input panel; WKWebView requires the embedder to
 *   do that and Tauri does not by default. So the one way to create a project
 *   was the one control likely to do nothing at all in the desktop shell.
 * * It **cannot validate**. A blank or whitespace-only name went to the server
 *   to be rejected, and the answer came back as a toast rather than next to the
 *   field it was about.
 * * It **blocks the page** and ignores the theme entirely, which on a surface
 *   whose whole design story is a token layer is a hole straight through it.
 *
 * TODO(design): a dialog with one field. Whether creating a project should ask
 * for more than a name is a product decision this does not foreclose.
 */

import { useState } from 'react';

import { Button, Field, Input, Modal } from '../design/primitives';
import { post } from './api';

export function NewProjectDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  function close() {
    setName('');
    setError('');
    onClose();
  }

  async function submit(event: Event) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      // Caught here rather than at the server: the message belongs next to the
      // field it is about, not in a toast in the corner.
      setError('A project needs a name.');
      return;
    }
    setBusy(true);
    const result = await post('/api/projects', { name: trimmed });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onCreated(trimmed);
    close();
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="New project"
      actions={
        <>
          <Button onClick={close}>Cancel</Button>
          <Button
            tone="primary"
            disabled={busy}
            aria-busy={busy || undefined}
            onClick={(event) => submit(event as unknown as Event)}
          >
            Create
          </Button>
        </>
      }
    >
      <form onSubmit={submit}>
        <Field label="Name" error={error} required>
          {(props) => (
            <Input
              {...props}
              value={name}
              placeholder="Payments platform"
              onInput={(e) => {
                setName((e.target as HTMLInputElement).value);
                if (error) setError('');
              }}
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
