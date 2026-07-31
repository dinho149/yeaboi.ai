/**
 * An initials circle for a member, deterministically coloured.
 *
 * Port of `html_theme.avatar`. The colour comes from a plain sum of the name's
 * code points — deliberately not a real hash, because the same person must get
 * the same colour in the browser *and* in an exported HTML report, and the two
 * runtimes have to agree on the arithmetic. (Python's built-in `hash()` is
 * salted per process and would not even agree with itself across runs.)
 */

import { cx } from '../../runtime/cx';
import { initials as toInitials, nameDigest } from '../../runtime/format';
import { AVATAR_TONES, toneMix, toneVar, type Tone } from '../tone';
import styles from './primitives.module.css';

/**
 * Note the explicit `| undefined` on the optional props.
 *
 * `tsconfig` runs with `exactOptionalPropertyTypes`, which distinguishes "absent"
 * from "present and undefined" — genuinely useful in the runtime layer, where
 * `{ signal: undefined }` would clobber a default. For a component prop the
 * distinction is meaningless, and callers forward optionals here constantly
 * (`emoji={person.avatar}`), so these say so rather than making every call site
 * spread conditionally.
 */
export interface AvatarProps {
  name: string;
  /**
   * An emoji the participant picked for themselves, shown instead of initials.
   * The live boards let people choose one; exports have only a name.
   */
  emoji?: string | undefined;
  size?: number | undefined;
  className?: string | undefined;
}

/** The tone a given name always maps to. Exported so a legend can match it. */
export function avatarTone(name: string): Tone {
  return AVATAR_TONES[nameDigest(name) % AVATAR_TONES.length] as Tone;
}

export function Avatar({ name, emoji, size, className }: AvatarProps) {
  const tone = avatarTone(name);
  const style: Record<string, string> = {
    background: toneMix(tone, 22, 'var(--panel)'),
    color: toneVar(tone),
  };
  if (size !== undefined) {
    style['width'] = `${size}px`;
    style['height'] = `${size}px`;
    style['fontSize'] = `${Math.round(size * 0.42)}px`;
  }

  return (
    <span
      className={cx(styles['avatar'], className)}
      style={style}
      // The circle is decoration beside a name that is already on screen in
      // every current usage; `title` gives it back on hover without making a
      // screen reader read every participant's name twice.
      title={name}
      aria-hidden="true"
    >
      {emoji || toInitials(name)}
    </span>
  );
}
