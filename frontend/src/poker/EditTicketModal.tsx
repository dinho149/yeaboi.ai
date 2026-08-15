/**
 * Editing the ticket — on the real tracker, immediately.
 *
 * This is the only control on either board that writes outside yeaboi, so it
 * says so plainly rather than looking like a local edit that syncs later. It
 * exists because the most common reason a room cannot estimate a ticket is that
 * the ticket is wrong, and the alternative is everyone waiting while the host
 * alt-tabs to Jira.
 *
 * Only *changed* fields are sent. A no-op save would otherwise re-write the
 * description on every open, and on Azure DevOps re-writing a description
 * flattens its formatting — hence the warning, shown only for that source.
 */

import { useEffect, useState } from 'react';

import { Modal } from '../shared';
import type { PokerTicket } from '../types/board';
import type { TicketEdit } from './actions';
import { fmtPoints } from './points';
import styles from './poker.module.css';
import { Icon } from '../design/primitives';

export interface EditTicketModalProps {
  open: boolean;
  ticket: PokerTicket | null;
  onSave(edit: TicketEdit): void;
  onClose(): void;
}

export function EditTicketModal({ open, ticket, onSave, onClose }: EditTicketModalProps) {
  const [summary, setSummary] = useState('');
  const [description, setDescription] = useState('');
  const [points, setPoints] = useState('');

  // Reload from the ticket each time the modal opens, so re-opening after a
  // cancel shows what is on the board rather than the abandoned draft.
  useEffect(() => {
    if (!open || !ticket) return;
    setSummary(ticket.summary);
    setDescription(ticket.description_text);
    setPoints(ticket.story_points === null ? '' : fmtPoints(ticket.story_points));
  }, [open, ticket]);

  if (!ticket) return null;

  const submit = (): void => {
    const edit: TicketEdit = {};
    const trimmed = summary.trim();
    if (trimmed && trimmed !== ticket.summary) edit.summary = trimmed;
    if (description !== ticket.description_text) edit.description = description;
    const parsed = Number.parseFloat(points);
    if (points.trim() !== '' && !Number.isNaN(parsed) && parsed !== ticket.story_points) edit.points = parsed;
    onClose();
    // Nothing changed is not an error; it is a cancel that went through the
    // Save button, and pushing an empty edit to a tracker would still bump the
    // ticket's history.
    if (Object.keys(edit).length) onSave(edit);
  };

  return (
    <Modal open={open} onClose={onClose} title="Edit ticket">
      <label className={styles['flabel']} htmlFor="poker-edit-summary">
        Summary
      </label>
      <input
        id="poker-edit-summary"
        className={styles['field']}
        value={summary}
        onInput={(event) => setSummary((event.target as HTMLInputElement).value)}
      />

      <label className={styles['flabel']} htmlFor="poker-edit-desc">
        Description
      </label>
      <textarea
        id="poker-edit-desc"
        className={styles['field']}
        rows={6}
        value={description}
        onInput={(event) => setDescription((event.target as HTMLTextAreaElement).value)}
      />

      <label className={styles['flabel']} htmlFor="poker-edit-pts">
        Story points
      </label>
      <input
        id="poker-edit-pts"
        className={styles['field']}
        type="number"
        min="0"
        step="1"
        value={points}
        onInput={(event) => setPoints((event.target as HTMLInputElement).value)}
      />

      {ticket.source === 'azdevops' ? (
        <p className={styles['editWarn']} role="alert">
          <Icon name="triangle-alert" /> Saving replaces this ticket&rsquo;s rich formatting on the board with plain
          text.
        </p>
      ) : null}
      <p className={styles['editNote']}>Saving updates the ticket on the real board immediately.</p>

      <button type="button" className={styles['editSave']} onClick={submit}>
        Save
      </button>
    </Modal>
  );
}
