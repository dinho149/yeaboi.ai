/**
 * The card while it is in the air.
 *
 * A portal on `document.body`, because the column it came out of scrolls and
 * clips — a carried card that disappears behind the edge of its own column is
 * the reason this is not just the card with a transform on it. The original
 * stays in the list as an empty slot, so the stack does not close up and
 * reopen under the pointer.
 *
 * It is not the real CardView: the controls on a card are not usable while it
 * is being carried, and rendering live buttons under a pointer that is captured
 * elsewhere invites a click that lands on nothing.
 */

import { createPortal } from 'react-dom';
import type { MutableRefObject } from 'react';

import { Avatar, Icon } from '../design/primitives';
import { fmtAgo } from '../runtime/format';
import { cx } from '../runtime/cx';
import type { RetroCard } from '../types/board';
import { carriedTransform, type DragState } from './useCardDrag';
import styles from './retro.module.css';

export interface DragPreviewProps {
  /** Where the hook writes the transform, once per frame. */
  previewRef: MutableRefObject<HTMLElement | null>;
  /** The grab, as it stood when the card was picked up. */
  drag: DragState;
  card: RetroCard;
  /** The author's face, from the presence roster. */
  authorAvatar?: string | undefined;
}

export function DragPreview({ previewRef, drag, card, authorAvatar }: DragPreviewProps) {
  const isAI = card.origin === 'ai';
  const ago = fmtAgo(card.created_at);
  return createPortal(
    <div className={styles['dragLayer']} aria-hidden="true">
      <div
        ref={previewRef as MutableRefObject<HTMLDivElement | null>}
        className={cx(styles['card'], styles['dragCard'], isAI && styles['cardAI'])}
        // The starting position only. Every frame after this one is written
        // straight to `style.transform` by the hook — see `place`.
        style={{ width: `${drag.width}px`, transform: carriedTransform(drag), rotate: `${drag.tilt}deg` }}
      >
        <p className={styles['cardText']}>{card.text}</p>
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
          {ago ? <span className={styles['age']}>{ago.label}</span> : null}
        </div>
      </div>
    </div>,
    document.body
  );
}
