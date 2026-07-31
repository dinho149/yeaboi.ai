/**
 * Who is in the room — the overlapping avatar row, and the full roster.
 *
 * The row is decorative shorthand for the roster; only the roster is a real
 * list. Announcing eight overlapping circles individually would be noise, so
 * the row carries one summarising label and the detail lives one click away.
 *
 * The `aria-live="polite"` on the summary is the fix for a real gap: nothing on
 * either board announced anything, ever, so a screen reader user had no way to
 * know somebody had joined or left the ceremony they were in.
 */

import { Avatar } from '../design/primitives';
import { cx } from '../runtime/cx';
import styles from './shared.module.css';

export interface Participant {
  name: string;
  avatar?: string;
}

export interface PresenceRowProps {
  people: readonly Participant[];
  /** Faces to show before collapsing into "+N". */
  max?: number;
  className?: string | undefined;
}

function summarise(people: readonly Participant[]): string {
  if (!people.length) return 'nobody here yet';
  if (people.length === 1) return `${people[0]?.name} is here`;
  return `${people.length} people here: ${people.map((p) => p.name).join(', ')}`;
}

export function PresenceRow({ people, max = 5, className }: PresenceRowProps) {
  const shown = people.slice(0, max);
  const overflow = people.length - shown.length;

  return (
    <div className={cx(styles['presence'], className)}>
      <span className={styles['avatarStack']} aria-hidden="true">
        {shown.map((person) => (
          <Avatar key={person.name} name={person.name} emoji={person.avatar} className={styles['stacked']} />
        ))}
        {overflow > 0 ? <span className={styles['presenceMore']}>+{overflow}</span> : null}
      </span>
      {/* The only announced form. `srOnly` rather than absent, so the visual
          row stays compact while the information is still available. */}
      <span className={styles['srOnly']} aria-live="polite" aria-atomic="true">
        {summarise(people)}
      </span>
    </div>
  );
}

export interface RosterProps {
  people: readonly Participant[];
  /** Marks one entry as "you". */
  meName?: string;
  className?: string | undefined;
}

/** The full list, for the room popover. */
export function Roster({ people, meName, className }: RosterProps) {
  if (!people.length) {
    return <p className={cx(styles['rosterEmpty'], className)}>Nobody else is here yet.</p>;
  }
  return (
    <ul className={cx(styles['roster'], className)}>
      {people.map((person) => (
        <li key={person.name} className={styles['rosterRow']}>
          <Avatar name={person.name} emoji={person.avatar} />
          <span className={styles['rosterName']}>{person.name}</span>
          {person.name === meName ? <span className={styles['rosterYou']}>you</span> : null}
        </li>
      ))}
    </ul>
  );
}

export interface TypingIndicatorProps {
  /** Names currently typing. */
  names: readonly string[];
  className?: string | undefined;
}

/** "Alice is typing…" — announced politely, never interrupting. */
export function TypingIndicator({ names, className }: TypingIndicatorProps) {
  const text = !names.length
    ? ''
    : names.length === 1
      ? `${names[0]} is typing…`
      : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]} are typing…`;

  return (
    <span className={cx(styles['typing'], className)} aria-live="polite" aria-atomic="true">
      {text}
    </span>
  );
}
