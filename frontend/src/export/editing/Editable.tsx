/**
 * The three affordances a report component adds to become correctable.
 *
 * All three return their children untouched when there is no editing context —
 * which is every export written to disk — so adopting them in a report is
 * additive and its existing render tests keep passing unchanged.
 *
 * **Never `contentEditable`.** It is a markup-producing surface, and this whole
 * layout exists so that no markup crosses the wire in either direction;
 * `test_web_frontend_guards.py` bans the family it belongs to. A `<textarea>`
 * produces a string, which is what an artifact field is.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';

import { Button } from '../../shared/Button';
import { useEditing } from './EditContext';
import styles from './editing.module.css';

interface Common {
  /** The artifact path this affordance writes to. */
  path: string;
  /**
   * Accessible name, composed at the call site so a screen reader hears
   * "Edit Ada's blocker" rather than "Edit". Required for that reason.
   */
  label: string;
}

// ---------------------------------------------------------------------------
// Editing one value
// ---------------------------------------------------------------------------

export interface EditableProps extends Common {
  /**
   * The **raw artifact value**, which is not the same thing as what `children`
   * draws. A standup summary renders as sentences of link-runs and is stored as
   * one string; the editor has to open on the string, because the string is
   * what the server can be asked to replace.
   */
  value: string;
  children: ReactNode;
  /** Multi-line editor. Default true — most editable fields are prose. */
  multiline?: boolean;
  /**
   * Whether `children` is a run of text rather than blocks.
   *
   * Almost every editable region is a `<p>`, a `<ul>` or a `<blockquote>`, so
   * the default is block: the wrapper is a block box and the pencil is pinned to
   * its top corner, because a trailing inline button after block content lands
   * on a line of its own with nothing to attach it to. A retro card's text is
   * the exception — it sits inline next to its attribution — and gets the
   * pencil in the flow after it.
   *
   * A prop and not a CSS guess: `display` cannot be chosen from the outside
   * without knowing what was passed in, and getting it wrong is either an
   * orphaned control or a broken line box.
   */
  inline?: boolean;
}

export function Editable({ path, label, value, children, multiline = true, inline = false }: EditableProps) {
  const editing = useEditing();
  if (!editing) return <>{children}</>;
  return (
    <EditableLive path={path} label={label} value={value} multiline={multiline} inline={inline}>
      {children}
    </EditableLive>
  );
}

function EditableLive({ path, label, value, children, multiline, inline }: Required<EditableProps>) {
  const editing = useEditing()!;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  const history = editing.byPath.get(path) ?? [];
  const others = editing.othersEditing(path);

  function close() {
    setOpen(false);
    setError('');
    trigger.current?.focus();
  }

  async function save() {
    setBusy(true);
    // `value` and not the last draft: the compare-and-swap has to carry what
    // this editor could actually see, or it would agree with itself.
    const outcome = await editing.actions.setText(path, draft, value);
    setBusy(false);
    if (outcome.ok) close();
    else setError(outcome.message);
  }

  if (!open) {
    return (
      <span
        className={styles['wrap']}
        data-edit-path={path}
        // Drives the dotted seam that shows what is correctable. Only once the
        // reader has named themselves, so reading a document is not the same
        // experience as correcting one.
        data-editable={editing.enabled ? '1' : undefined}
        data-edited={history.length ? '1' : undefined}
        data-inline={inline ? '1' : undefined}
      >
        {children}
        {/* One box for all the markers. Individually placed they each landed on
            a line of their own after block children — a lone ✎ and a lone dot
            floating under the passage, attached to nothing. */}
        <span className={styles['marks']}>
          {editing.enabled ? (
            <button
              ref={trigger}
              type="button"
              className={styles['trigger']}
              aria-label={`Edit ${label}`}
              onClick={() => {
                setDraft(value);
                setOpen(true);
              }}
            >
              ✎
            </button>
          ) : null}
          {history.length ? (
            <button
              type="button"
              className={styles['edited']}
              onClick={() => editing.showHistory(path)}
              aria-label={`${label} was edited — show history`}
            >
              {/* Never colour alone: the word is what a screen reader and a
                  monochrome print both get. */}
              <span aria-hidden="true">•</span>
              <span className={styles['srOnly']}> (edited)</span>
            </button>
          ) : null}
          {others.length ? <span className={styles['busy']}>{others.join(', ')} editing…</span> : null}
        </span>
      </span>
    );
  }

  return (
    <span className={styles['editor']}>
      <Field label={label} multiline={multiline} value={draft} onChange={setDraft} onCancel={close} onSave={save} />
      <span className={styles['actions']}>
        <Button onClick={save} disabled={busy || !draft.trim()} tone="primary" size="s">
          Save
        </Button>
        <Button onClick={close} size="s" shape="bare">
          Cancel
        </Button>
      </span>
      {error ? <span className={styles['error']}>{error}</span> : null}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Adding to a list
// ---------------------------------------------------------------------------

export interface EditableListProps extends Common {
  children: ReactNode;
}

/** Wrap a list to gain an "add" row. `path` is the list, not the slot. */
export function EditableList({ path, label, children }: EditableListProps) {
  const editing = useEditing();
  if (!editing?.enabled) return <>{children}</>;
  return (
    <div className={styles['list']} data-edit-path={path}>
      {children}
      <AddRow
        label={`Add a ${label}`}
        onSubmit={(text) => editing.actions.appendItem(`${path}[-]`, text)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Adding something the schema never had
// ---------------------------------------------------------------------------

export interface EditableSlotProps {
  /** The row this hangs off, or `''` for the document as a whole. */
  anchor: string;
  label: string;
}

/**
 * The place a reader adds a note or a named field.
 *
 * Renders nothing at all without a session, which is what lets it be dropped
 * into any report without changing a single exported byte.
 */
export function EditableSlot({ anchor, label }: EditableSlotProps) {
  const editing = useEditing();
  const [mode, setMode] = useState<'' | 'note' | 'field'>('');
  const [name, setName] = useState('');
  if (!editing?.enabled) return null;

  if (!mode) {
    return (
      <div className={styles['slot']}>
        <button type="button" className={styles['add']} onClick={() => setMode('note')}>
          ＋ Add a note to {label}
        </button>
        <button type="button" className={styles['add']} onClick={() => setMode('field')}>
          ＋ Add a field to {label}
        </button>
      </div>
    );
  }

  return (
    <div className={styles['slot']}>
      {mode === 'field' ? (
        <input
          // Naming the field is the first thing to do, so it takes the caret;
          // the value row below stays unfocused until it is tabbed into.
          autoFocus
          className={styles['name']}
          value={name}
          placeholder="Field name"
          aria-label={`Name of the field to add to ${label}`}
          onInput={(event) => setName((event.target as HTMLInputElement).value)}
        />
      ) : null}
      <AddRow
        autoFocus={mode === 'note'}
        label={mode === 'field' ? `Value for ${name || 'the field'}` : `Note on ${label}`}
        onSubmit={async (text) => {
          const outcome =
            mode === 'field'
              ? await editing.actions.addField(anchor, name, text)
              : await editing.actions.addNote(anchor, text);
          if (outcome.ok) {
            setMode('');
            setName('');
          }
          return outcome;
        }}
        onCancel={() => {
          setMode('');
          setName('');
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

function Field({
  label,
  multiline,
  value,
  onChange,
  onCancel,
  onSave,
}: {
  label: string;
  multiline: boolean;
  value: string;
  onChange(next: string): void;
  onCancel(): void;
  onSave(): void;
}) {
  const ref = useRef<HTMLTextAreaElement & HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const shared = {
    ref,
    value,
    'aria-label': label,
    className: styles['input'],
    onInput: (event: Event) => onChange((event.target as HTMLInputElement).value),
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCancel();
      }
      // Enter alone inserts a newline in prose; the deliberate save gesture is
      // the modifier, matching every other multi-line editor people use.
      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        onSave();
      }
    },
  };
  // Cast at the boundary: the two elements take the same handlers but preact
  // types them per-element, and spreading one object into both is the point.
  const props = shared as Record<string, unknown>;
  return multiline ? <textarea {...props} rows={4} /> : <input {...props} type="text" />;
}

function AddRow({
  label,
  onSubmit,
  onCancel,
  autoFocus = false,
}: {
  label: string;
  onSubmit(text: string): Promise<{ ok: boolean; message?: string }>;
  onCancel?(): void;
  /**
   * Take focus on mount.
   *
   * True only where this row appeared *because* someone asked for it — the note
   * and field slots, which are a click away. A list's add row is on screen from
   * the start, and focusing that on load would steal the caret from a reader who
   * only wanted to read.
   */
  autoFocus?: boolean;
}) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus) input.current?.focus();
  }, [autoFocus]);

  async function submit() {
    if (!text.trim()) return;
    setBusy(true);
    const outcome = await onSubmit(text.trim());
    setBusy(false);
    if (outcome.ok) setText('');
    else setError(outcome.message ?? 'That could not be saved.');
  }

  return (
    <div className={styles['addRow']}>
      <input
        ref={input}
        className={styles['input']}
        type="text"
        value={text}
        aria-label={label}
        placeholder={label}
        onInput={(event) => setText((event.target as HTMLInputElement).value)}
        onKeyDown={(event: KeyboardEvent) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            void submit();
          }
          if (event.key === 'Escape' && onCancel) {
            event.preventDefault();
            onCancel();
          }
        }}
      />
      <Button onClick={submit} disabled={busy || !text.trim()} tone="primary" size="s">
        Add
      </Button>
      {onCancel ? (
        <Button onClick={onCancel} size="s" shape="bare">
          Cancel
        </Button>
      ) : null}
      {error ? <span className={styles['error']}>{error}</span> : null}
    </div>
  );
}
