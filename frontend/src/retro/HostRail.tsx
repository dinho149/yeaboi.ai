/**
 * The facilitator's column.
 *
 * Everything only the host does, in one place beside the board rather than
 * scattered across the bar, the notch and a strip above the columns. Four
 * things, in the order a retro uses them: which retro you are looking at, last
 * week's actions to close out, whose cards to show while you go round, and the
 * suggestions you ask for once the writing has stopped.
 *
 * Guests never see it — none of these are theirs — so the board is four columns
 * and nothing else for everyone but one person.
 */

import { Icon } from '../design/primitives';
import { Button } from '../shared';
import { CarriedStrip } from './CarriedStrip';
import { TimeSwitch } from './TimeSwitch';
import { Walkthrough, type Person } from './Walkthrough';
import type { History } from './useHistory';
import type { CarriedStatuses } from '../types/enums';
import type { RetroCard } from '../types/board';
import styles from './retro.module.css';

export interface HostRailProps {
  history: History;
  /** What today's board is called. */
  liveLabel: string;
  /** Last retro's action items, up for review. */
  carried: readonly RetroCard[];
  onSetCarriedStatus(itemId: string, status: CarriedStatuses): void;
  /** Everyone with a card, for the walkthrough. */
  people: readonly Person[];
  focus: string;
  onFocus(name: string): void;
  /** Asking for action items. Off while one is in flight or on a past board. */
  canSuggest: boolean;
  suggesting: boolean;
  onSuggest(): void;
  /** True when a past retro is open — most of the rail is inert then. */
  past: boolean;
}

export function HostRail({
  history,
  liveLabel,
  carried,
  onSetCarriedStatus,
  people,
  focus,
  onFocus,
  canSuggest,
  suggesting,
  onSuggest,
  past,
}: HostRailProps) {
  return (
    <aside className={styles['rail']} aria-label="Facilitator">
      <section className={styles['railBlock']}>
        <h2 className={styles['railTitle']}>Retro</h2>
        <TimeSwitch history={history} liveLabel={liveLabel} />
      </section>

      {carried.length ? (
        <section className={styles['railBlock']}>
          <CarriedStrip items={carried} locked={past} onSetStatus={onSetCarriedStatus} />
        </section>
      ) : null}

      <section className={styles['railBlock']}>
        <h2 className={styles['railTitle']}>Whose cards</h2>
        <Walkthrough people={people} current={focus} onPick={onFocus} onExit={() => onFocus('')} />
      </section>

      {past ? null : (
        <section className={styles['railBlock']}>
          <h2 className={styles['railTitle']}>Action items</h2>
          <p className={styles['railNote']}>
            Reads the feedback, weighted by what the room reacted to. They arrive as cards you can keep or
            delete.
          </p>
          <Button tone="primary" disabled={!canSuggest || suggesting} onClick={onSuggest}>
            <Icon name="sparkles" size={14} /> {suggesting ? 'Thinking…' : 'Suggest action items'}
          </Button>
        </section>
      )}
    </aside>
  );
}
