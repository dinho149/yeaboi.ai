/**
 * A labelled form control: label, the input itself, an optional hint, and an
 * error.
 *
 * The reason this is one primitive rather than a bare `Input` is the wiring
 * nobody does by hand consistently — `htmlFor`/`id`, `aria-describedby`
 * pointing at *both* hint and error, and `aria-invalid`. Left to call sites,
 * roughly half of any real form ends up with a label that is not associated
 * with its control, which is invisible to everyone except the people who most
 * need it.
 *
 * The id is generated when not supplied, so the common case needs no ceremony.
 */

import type { ComponentChildren, JSX } from 'preact';
import { useId } from 'preact/hooks';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface FieldProps {
  label: string;
  /** Omit to generate one. Supply when something else must reference it. */
  id?: string;
  hint?: string;
  error?: string;
  required?: boolean;
  /** Receives the wired-up props: id, aria-describedby, aria-invalid. */
  children: (props: {
    id: string;
    'aria-describedby': string | undefined;
    'aria-invalid': boolean | undefined;
    required: boolean;
  }) => ComponentChildren;
  className?: string | undefined;
}

export function Field({ label, id, hint, error, required = false, children, className }: FieldProps) {
  const generated = useId();
  const fieldId = id ?? generated;
  const hintId = hint ? `${fieldId}-hint` : undefined;
  const errorId = error ? `${fieldId}-error` : undefined;
  // Both, space-separated — an error must not silently replace the hint.
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;

  return (
    <div className={cx(styles['field'], className)}>
      <label className={styles['fieldLabel']} htmlFor={fieldId}>
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
      </label>
      {children({
        id: fieldId,
        'aria-describedby': describedBy,
        'aria-invalid': error ? true : undefined,
        required,
      })}
      {hint ? (
        <p className={styles['fieldHint']} id={hintId}>
          {hint}
        </p>
      ) : null}
      {error ? (
        // role="alert" so a validation failure is announced when it appears,
        // rather than sitting there silently for a screen-reader user.
        <p className={styles['fieldError']} id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

// `JSX.IntrinsicElements['input']` rather than `JSX.HTMLAttributes<…>`: the
// latter is the shared attribute set and carries no element-specific props, so
// `type`, `value` and `checked` are all missing from it.
//
// `className` is re-declared as a plain string because Preact types it as a
// `Signalish`, which `cx` (a plain string joiner) cannot take.
export type InputProps = Omit<JSX.IntrinsicElements['input'], 'className'> & {
  className?: string | undefined;
};

/** A text input. Pass through whatever `Field` hands you. */
export function Input({ className, ...props }: InputProps) {
  return <input {...props} className={cx(styles['input'], className)} />;
}

export type SelectProps = Omit<JSX.IntrinsicElements['select'], 'className'> & {
  className?: string | undefined;
};

export function Select({ className, children, ...props }: SelectProps) {
  return (
    <select {...props} className={cx(styles['input'], styles['select'], className)}>
      {children}
    </select>
  );
}
