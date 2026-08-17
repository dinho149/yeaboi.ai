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

import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { Avatar, Icon } from '../design/primitives';
import { useDismiss } from '../hooks/useDismiss';
import { fmtAgo } from '../runtime/format';
import { cx } from '../runtime/cx';
import { Button } from '../shared';
import { RETRO_GRID_LABELS, RETRO_GRIDS, type RetroGrids } from '../types/enums';
import type { RetroCard } from '../types/board';
import { ReactionChips, ReactionTray, ReactionTrigger } from './ReactionBar';
import motion from '../motion/motion.module.css';
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
  /**
   * This card just arrived from a peer over the long-poll.
   *
   * Decays an accent edge over ~700ms so a facilitator looking at another
   * column can still find what moved. Never set for a card you added
   * yourself — yours should feel instant, and animating it makes your own
   * typing feel laggy.
   */
  arrived?: boolean;
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
        <Button onClick={onCancel}>Cancel</Button>
        <Button tone="primary" onClick={() => onSave(text)}>
          Save
        </Button>
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
  arrived,
  onEdit,
  onDelete,
  onReact,
  onMoveTo,
  onGripPointerDown,
}: CardViewProps) {
  const [editing, setEditing] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [trayOpen, setTrayOpen] = useState(false);
  /**
   * A delete waiting to be confirmed.
   *
   * The ✕ used to delete on the click, and `board.delete_card` is a hard
   * delete: no tombstone, no undo, and the card is gone from every other
   * browser inside a poll. On a board where the ✕ sits a few pixels from the ✎,
   * that is one slip away from losing someone's card mid-ceremony.
   */
  const [confirming, setConfirming] = useState(false);

  const cardRef = useRef<HTMLElement | null>(null);
  const confirmRef = useRef<HTMLSpanElement | null>(null);
  const trayTriggerRef = useRef<HTMLButtonElement>(null);
  const trayId = useId();

  const isAI = card.origin === 'ai';
  const ago = fmtAgo(card.created_at);
  // AI cards belong to nobody, so nobody may edit them — including the host.
  const canModify = card.mine && !isAI && !locked;

  const closeTray = useCallback((reason: 'escape' | 'outside') => {
    setTrayOpen(false);
    // Escape came from the keyboard, so focus has to go somewhere deliberate;
    // an outside pointer press has already put it where the person meant.
    if (reason === 'escape') trayTriggerRef.current?.focus();
  }, []);
  const cancelDelete = useCallback(() => setConfirming(false), []);
  // The card, not the trigger, is the tray's boundary: the trigger and the tray
  // are no longer one element, and anything else in the action row is a click
  // you meant to make on this card.
  useDismiss(trayOpen, cardRef, closeTray);
  useDismiss(confirming, confirmRef, cancelDelete);

  // A pending delete must not survive into a state where the card can no longer
  // be deleted, or the ✓ would sit there confirming nothing.
  useEffect(() => {
    if (editing || locked) setConfirming(false);
  }, [editing, locked]);

  return (
    <article
      ref={cardRef}
      className={cx(
        styles['card'],
        isAI && styles['cardAI'],
        dragging && styles['cardDragging'],
        arrived && motion['arrived']
      )}
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
            <Icon name="sparkles" size={14} /> AI
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

        {confirming ? (
          // The whole control cluster is replaced rather than added to: a
          // pending destructive action wants the row saying one thing, and
          // leaving the ✎ and the grip beside it invites the wrong click again.
          <span ref={confirmRef} className={styles['confirmRow']} role="group" aria-label="Confirm delete">
            <span className={styles['confirmLabel']}>Delete?</span>
            <Button
              shape="bare"
              tone="danger"
              // `emphasis`, not `active`: this is a one-shot answer to a
              // question, not a toggle that is on, and `aria-pressed` on it
              // announces a state that never existed.
              emphasis
              aria-label={`Confirm delete card: ${card.text.slice(0, 40)}`}
              // Focused on appearance, so the keyboard path is ✕, Enter — and
              // so a screen reader is told the row has become a question.
              autoFocus
              onClick={() => {
                setConfirming(false);
                onDelete();
              }}
            >
              <Icon name="check" size={16} />
            </Button>
            <Button shape="bare" aria-label="Keep the card" onClick={cancelDelete}>
              <Icon name="undo" size={16} />
            </Button>
          </span>
        ) : (
          <>
          {locked ? null : (
            <span className={styles['gripWrap']}>
              {/* `.grip` is not styling: its `touch-action: none` and
                  `cursor: grab` are the drag mechanics useCardDrag reads. That
                  is why it stays a class rather than becoming a `shape`. */}
              <Button
                shape="bare"
                className={styles['grip']}
                aria-label={`Move card: ${card.text.slice(0, 40)}`}
                aria-haspopup="menu"
                aria-expanded={moveOpen}
                onPointerDown={(event) => onGripPointerDown(event as unknown as PointerEvent)}
                onClick={() => setMoveOpen((v) => !v)}
              >
                <Icon name="grip" size={16} />
              </Button>
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

          {locked ? null : (
            <ReactionTrigger
              open={trayOpen}
              trayId={trayId}
              buttonRef={trayTriggerRef}
              onToggle={() => setTrayOpen((v) => !v)}
              disabled={locked}
            />
          )}

          {canModify && !editing ? (
            <>
              <Button
                shape="bare"
                aria-label={`Edit card: ${card.text.slice(0, 40)}`}
                onClick={() => setEditing(true)}
              >
                <Icon name="pencil" size={16} />
              </Button>
              <Button
                shape="bare"
                tone="danger"
                aria-label={`Delete card: ${card.text.slice(0, 40)}`}
                onClick={() => setConfirming(true)}
              >
                <Icon name="trash" size={16} />
              </Button>
            </>
          ) : null}
          </>
        )}
      </div>

      {/* In the flow, below the row that opened it — see the note in
          ReactionBar.tsx about the column clipping a floating panel. */}
      {trayOpen && !locked ? (
        <ReactionTray
          id={trayId}
          mine={myReactions}
          onPick={(emoji) => {
            onReact(emoji);
            setTrayOpen(false);
            trayTriggerRef.current?.focus();
          }}
        />
      ) : null}

      <ReactionChips reactions={card.reactions} mine={myReactions} onReact={onReact} disabled={locked} />
    </article>
  );
}
