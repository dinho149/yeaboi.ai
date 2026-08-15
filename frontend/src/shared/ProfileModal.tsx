/**
 * "Who are you?" — the name and avatar picker.
 *
 * Shown once on arrival and re-openable from the identity chip, so it is both
 * the join step and the rename step. That dual role is why it never blocks on
 * an empty name: leaving the field blank rolls a random one rather than
 * refusing to let someone into the ceremony that has already started.
 *
 * The avatar grid is a radio group for the same reason as the theme swatches —
 * a row of buttons with a `.sel` class tells assistive tech nothing about which
 * is chosen or that they are alternatives.
 */

import { useEffect, useState } from 'react';

import { cx } from '../runtime/cx';
import { randomName } from '../runtime/random';
import { Button } from './Button';
import { Modal } from './Modal';
import styles from './shared.module.css';
import { Icon } from '../design/primitives';

export interface ProfileModalProps {
  open: boolean;
  /** Blank on first open. */
  name: string;
  avatar: string;
  avatars: readonly string[];
  adjectives: readonly string[];
  nouns: readonly string[];
  onSave(profile: { name: string; avatar: string }): void;
  onClose(): void;
  /** True on the very first open: no dismissing without choosing. */
  required?: boolean;
}

export function ProfileModal({
  open,
  name,
  avatar,
  avatars,
  adjectives,
  nouns,
  onSave,
  onClose,
  required,
}: ProfileModalProps) {
  const [draftName, setDraftName] = useState(name);
  const [draftAvatar, setDraftAvatar] = useState(avatar || (avatars[0] ?? ''));

  // Re-seed from props each time it opens, not on every render: while it is
  // open the draft belongs to the user, and syncing continuously would fight
  // their typing on any incidental re-render.
  useEffect(() => {
    if (!open) return;
    setDraftName(name);
    setDraftAvatar(avatar || (avatars[0] ?? ''));
  }, [open, name, avatar, avatars]);

  const save = (): void => {
    // A blank name gets a random one. Refusing to save would strand someone
    // outside a ceremony that is already running, over a field nobody cares
    // about — and every board renames freely afterwards.
    onSave({ name: draftName.trim() || randomName(adjectives, nouns), avatar: draftAvatar });
  };

  return (
    <Modal
      open={open}
      // A required first-open has nothing to fall back to, so dismissal is a
      // no-op rather than a trapdoor into an unusable board.
      onClose={required ? () => {} : onClose}
      title="Choose a name"
      actions={
        <Button tone="primary" size="l" onClick={save}>
          {required ? 'Join' : 'Save'}
        </Button>
      }
    >
      <div className={styles['inputRow']}>
        <input
          className={styles['textInput']}
          value={draftName}
          placeholder="Your name"
          aria-label="Your name"
          autoComplete="nickname"
          onInput={(event) => setDraftName((event.target as HTMLInputElement).value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') save();
          }}
        />
        <Button
          className={styles['inputAction']}
          aria-label="Suggest a random name"
          title="Suggest a random name"
          onClick={() => setDraftName(randomName(adjectives, nouns))}
        >
          <Icon name="dices" />
        </Button>
      </div>

      <div className={styles['avatarGrid']} role="radiogroup" aria-label="Avatar">
        {avatars.map((option) => {
          const selected = option === draftAvatar;
          return (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={`Avatar ${option}`}
              tabIndex={selected ? 0 : -1}
              className={cx(styles['avatarChoice'], selected && styles['avatarChoiceOn'])}
              onClick={() => setDraftAvatar(option)}
            >
              <span aria-hidden="true">{option}</span>
            </button>
          );
        })}
      </div>
    </Modal>
  );
}
