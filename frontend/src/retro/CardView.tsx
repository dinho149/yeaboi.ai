/**
 * One sticky card.
 *
 * ## The `editingHere` hack, and why it is gone
 *
 * The old render path was `box.innerHTML = …` per column, every poll. That
 * destroyed and rebuilt the `<textarea>` you were typing in, so `render()` had
 * to special-case it: work out whether the card being edited lives in this
 * column and, if so, skip re-rendering *the entire column* until Save or Cancel
 * (`retro/page.py:700`). One person editing froze three other people's cards.
 *
 * Here the draft is `useState` inside {@link CardEditor}, seeded once from the
 * card. Nothing the server sends can reach it, so nothing has to be frozen and
 * every other card in the column keeps updating live. That is the whole reason
 * the store/local-state split in `boardStore.ts` is drawn where it is.
 */

import { useState } from 'react';

import { Avatar } from '../design/primitives';
import { fmtAgo } from '../runtime/format';
import { cx } from '../runtime/cx';
import { RETRO_GRID_LABELS, RETRO_GRIDS, type RetroGrids } from '../types/enums';
import type { RetroCard } from '../types/board';
import { ReactionBar } from './ReactionBar';
import styles from './retro.module.css';

export interface CardViewProps {
  card: RetroCard;
  /** Avatar the author picked, from the presence roster. Absent once they leave. */
  authorAvatar?: string | undefined;
  /** Emoji this browser has reacted to this card with. */
  myReactions: ReadonlySet<string>;
  /** Host froze the board: hide every mutating control, not just disable it. */
  locked: boolean;
  /** True while this card is the one being dragged. */
  dragging?: boolean;
  onEdit(text: string): void;
  onDelete(): void;
  onReact(emoji: string): void;
  onMoveTo(grid: RetroGrids): void;
  onGripPointerDown(event: PointerEvent): void;
}

function CardEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave(text: string): void;
  onCancel(): void;
}) {
  // Seeded once. The lazy initialiser is not a micro-optimisation: it states
  // that `initial` is a starting value, not a binding — later snapshots for
  // this card change the prop and must not change what you have typed.
  const [text, setText] = useState(() => initial);

  return (
    <div className={styles['editor']}>
      <textarea
        className={styles['editBox']}
        rows={3}
        value={text}
        aria-label="Edit card"
        autoFocus
        // Caret at the end rather than selecting everything, so the common case
        // — appending a word — does not need a click first.
        ref={(el) => {
          if (el) el.setSelectionRange(el.value.length, el.value.length);
        }}
        onInput={(event) => setText((event.target as HTMLTextAreaElement).value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            onCancel();
          } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            onSave(text);
          }
        }}
      />
      <div className={styles['editActions']}>
        <button type="button" className={styles['ghostBtn']} onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className={styles['saveBtn']} onClick={() => onSave(text)}>
          Save
        </button>
      </div>
    </div>
  );
}

export function CardView({
  card,
  authorAvatar,
  myReactions,
  locked,
  dragging,
  onEdit,
  onDelete,
  onReact,
  onMoveTo,
  onGripPointerDown,
}: CardViewProps) {
  const [editing, setEditing] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);

  const isAI = card.origin === 'ai';
  const ago = fmtAgo(card.created_at);
  // AI cards belong to nobody, so nobody may edit them — including the host.
  const canModify = card.mine && !isAI && !locked;

  return (
    <article
      className={cx(styles['card'], isAI && styles['cardAI'], dragging && styles['cardDragging'])}
      data-card-id={card.id}
      aria-label={`Card by ${isAI ? 'AI' : card.author}`}
    >
      {editing ? (
        <CardEditor
          initial={card.text}
          onCancel={() => setEditing(false)}
          onSave={(text) => {
            setEditing(false);
            onEdit(text);
          }}
        />
      ) : (
        // pre-wrap, not a markdown or linkify pass: card text is whatever a
        // teammate typed and is rendered as a text child, so there is no path
        // by which it becomes markup. Newlines still survive.
        <p className={styles['cardText']}>{card.text}</p>
      )}

      <div className={styles['cardMeta']}>
        {isAI ? (
          <span className={styles['aiBadge']}>
            <span aria-hidden="true">🤖</span> AI
          </span>
        ) : (
          <span className={styles['author']}>
            <Avatar name={card.author} emoji={authorAvatar} size={20} />
            <span className={styles['authorName']}>{card.author}</span>
          </span>
        )}

        {ago ? (
          <time className={styles['age']} dateTime={card.created_at} title={ago.title}>
            {ago.label}
          </time>
        ) : null}

        <span className={styles['metaSpacer']} />

        {locked ? null : (
          <span className={styles['gripWrap']}>
            <button
              type="button"
              className={styles['grip']}
              aria-label={`Move card: ${card.text.slice(0, 40)}`}
              aria-haspopup="menu"
              aria-expanded={moveOpen}
              onPointerDown={(event) => onGripPointerDown(event as unknown as PointerEvent)}
              onClick={() => setMoveOpen((v) => !v)}
            >
              <span aria-hidden="true">⠿</span>
            </button>
            {moveOpen ? (
              // The keyboard path. Dragging with arrow keys is a worse
              // interaction than naming the destination, and this is also the
              // only way to move a card with a screen reader running.
              <div className={styles['moveMenu']} role="menu" aria-label="Move to column">
                {RETRO_GRIDS.filter((grid) => grid !== card.grid).map((grid) => (
                  <button
                    key={grid}
                    type="button"
                    role="menuitem"
                    className={styles['moveItem']}
                    onClick={() => {
                      setMoveOpen(false);
                      onMoveTo(grid);
                    }}
                  >
                    {RETRO_GRID_LABELS[grid]}
                  </button>
                ))}
              </div>
            ) : null}
          </span>
        )}

        {canModify && !editing ? (
          <>
            <button
              type="button"
              className={styles['act']}
              aria-label={`Edit card: ${card.text.slice(0, 40)}`}
              onClick={() => setEditing(true)}
            >
              <span aria-hidden="true">✎</span>
            </button>
            <button
              type="button"
              className={cx(styles['act'], styles['actDanger'])}
              aria-label={`Delete card: ${card.text.slice(0, 40)}`}
              onClick={onDelete}
            >
              <span aria-hidden="true">✕</span>
            </button>
          </>
        ) : null}
      </div>

      <ReactionBar reactions={card.reactions} mine={myReactions} onReact={onReact} disabled={locked} />
    </article>
  );
}
