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

import { memo, useCallback, useEffect, useId, useRef, useState } from 'react';

import { Avatar, Icon } from '../design/primitives';
import { useDismiss } from '../hooks/useDismiss';
import { fmtAgo } from '../runtime/format';
import { cx } from '../runtime/cx';
import { Button } from '../shared';
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
  onEdit(cardId: string, text: string): void;
  onDelete(cardId: string): void;
  onReact(cardId: string, emoji: string): void;
  /** A press on the card body. Mice pick up at once, fingers hold first. */
  onCardPointerDown(cardId: string, event: PointerEvent): void;
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
  // Guards the blur that a save or a cancel causes from committing a second time.
  const done = useRef(false);

  const commit = (next: string): void => {
    if (done.current) return;
    done.current = true;
    const trimmed = next.trim();
    // An empty card is not a delete — the server refuses it, and ✕ is how you
    // mean it. Leaving the field empty is a change of mind.
    if (!trimmed || trimmed === initial.trim()) onCancel();
    else onSave(trimmed);
  };

  const grow = (el: HTMLTextAreaElement): void => {
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  };

  // Focused here rather than by `autoFocus`: the press that opened the editor is
  // still being handled by the drag machinery on the card above, and the caret
  // goes to the end so appending a word needs no click first.
  const box = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
    // Once, on open. Re-running would drag the caret back on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <textarea
      className={cx(styles['cardText'], styles['editBox'])}
      rows={1}
      value={text}
      aria-label="Edit card"
      ref={(el) => {
        box.current = el;
        if (el) grow(el);
      }}
      onInput={(event) => {
        const el = event.target as HTMLTextAreaElement;
        setText(el.value);
        grow(el);
      }}
      // Clicking away is a commit: there is no Save button to reach instead.
      onBlur={() => commit(text)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          done.current = true;
          onCancel();
        } else if (event.key === 'Enter' && !event.shiftKey) {
          // Enter saves; Shift-Enter is the newline. A card is a sentence.
          event.preventDefault();
          commit(text);
        }
      }}
    />
  );
}

function CardViewBase({
  card,
  authorAvatar,
  myReactions,
  locked,
  dragging,
  arrived,
  onEdit,
  onDelete,
  onReact,
  onCardPointerDown,
}: CardViewProps) {
  const [editing, setEditing] = useState(false);
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
  // Where the press that a click came from landed. A drag ends in a click too,
  // on whatever the pointer is over, and that must not open an editor.
  const pressAt = useRef<{ x: number; y: number } | null>(null);
  const confirmRef = useRef<HTMLSpanElement | null>(null);
  const trayTriggerRef = useRef<HTMLButtonElement>(null);
  const trayRef = useRef<HTMLDivElement | null>(null);
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
  useDismiss(trayOpen, cardRef, closeTray, trayRef);
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
        locked && styles['cardLocked'],
        dragging && styles['cardDragging'],
        arrived && motion['arrived']
      )}
      data-card-id={card.id}
      aria-label={`Card by ${isAI ? 'AI' : card.author}`}
      // The whole card is the handle; the hook decides what a press means from
      // the pointer type and skips one that landed on a control.
      onPointerDown={(event) => {
        if (!editing && !locked) onCardPointerDown(card.id, event as unknown as PointerEvent);
      }}
    >
      {editing ? (
        <CardEditor
          initial={card.text}
          onCancel={() => setEditing(false)}
          onSave={(text) => {
            setEditing(false);
            onEdit(card.id, text);
          }}
        />
      ) : (
        // pre-wrap, not a markdown or linkify pass: card text is whatever a
        // teammate typed and is rendered as a text child, so there is no path
        // by which it becomes markup. Newlines still survive.
        //
        // Your own text is the way into the editor — the card is the handle for
        // a drag, and a press that never moves is a click on what it landed on.
        // The pencil beside it is the same action for a keyboard.
        <p
          className={cx(styles['cardText'], canModify && styles['cardTextMine'])}
          {...(canModify
            ? {
                onPointerDown: (event: { clientX: number; clientY: number }) => {
                  pressAt.current = { x: event.clientX, y: event.clientY };
                },
                onClick: (event: { clientX: number; clientY: number }) => {
                  const at = pressAt.current;
                  pressAt.current = null;
                  if (!at || Math.hypot(event.clientX - at.x, event.clientY - at.y) > 4) return;
                  setEditing(true);
                },
              }
            : {})}
        >
          {card.text}
        </p>
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

        {/* In the row, not under it. Below the row it added a line to every card
            that anyone had reacted to, so the board reflowed the first time
            somebody pressed 👍 — and the row already has the height for it. */}
        <ReactionChips
          reactions={card.reactions}
          mine={myReactions}
          onReact={(emoji) => onReact(card.id, emoji)}
          disabled={locked}
        />

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
                onDelete(card.id);
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

      <ReactionTray
        open={trayOpen && !locked}
        id={trayId}
        anchorRef={trayTriggerRef}
        trayRef={trayRef}
        mine={myReactions}
        onPick={(emoji) => {
          onReact(card.id, emoji);
          setTrayOpen(false);
          trayTriggerRef.current?.focus();
        }}
      />
    </article>
  );
}

/**
 * Memoised: a drag re-renders the board on every slot the pointer crosses, and
 * without this that is every card on the board rather than the two whose drop
 * indicator moved.
 */
export const CardView = memo(CardViewBase);
