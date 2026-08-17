/**
 * The batch: every ticket, what has been estimated, and where the room is.
 *
 * A list, not navigation. Clicking a row *previews* it — see Ticket.tsx — and
 * only the host can move the room, from an explicit button inside the preview
 * banner. That split exists because a rail click used to jump everyone and reset
 * the round, which meant a guest reading ahead could wipe a vote in progress.
 *
 * The progress eyebrow carries `estimated / total`, which is the number a host
 * is actually tracking during a session: how much of the batch is left before
 * the meeting can end.
 */

import { Eyebrow } from '../design/primitives';
import { cx } from '../runtime/cx';
import type { TicketMeta } from '../types/board';
import { fmtPoints } from './points';
import { boardStyles as kit } from '../shared/board';

export interface RailProps {
  tickets: readonly TicketMeta[];
  /** The ticket the room is voting on. */
  current: number;
  /** The ticket this browser is previewing, or null. */
  peeking: number | null;
  estimated: number;
  /** Narrow screens: the rail is a drawer. */
  /** The board's scope — the sprint or batch these tickets came from. */
  scope: string;
  open: boolean;
  onPick(index: number): void;
  onClose(): void;
}

export function Rail({ tickets, current, peeking, estimated, scope, open, onPick, onClose }: RailProps) {
  return (
    <aside
      className={cx(kit['rail'], open && kit['railOpen'])}
      // Hidden from assistive tech when the drawer is shut, so a phone user
      // tabbing the board does not walk through an off-screen ticket list.
      aria-hidden={open ? undefined : 'true'}
    >
      <div className={kit['railHead']}>
        {scope ? <p className={kit['railScope']}>{scope}</p> : null}
        <Eyebrow value={`${estimated}/${tickets.length}`}>Tickets</Eyebrow>
      </div>

      <ul className={kit['railList']}>
        {tickets.map((ticket, index) => (
          <li key={`${ticket.key}:${index}`}>
            <button
              type="button"
              className={cx(
                kit['railItem'],
                index === current && kit['railCurrent'],
                index === peeking && kit['railPeeking'],
                ticket.estimated && kit['railDone']
              )}
              aria-current={index === current ? 'true' : undefined}
              title={ticket.summary}
              onClick={() => {
                onPick(index);
                onClose();
              }}
            >
              <span className={kit['railDot']} aria-hidden="true" />
              <span className={kit['railText']}>
                {ticket.key ? <span className={kit['railKey']}>{ticket.key}</span> : null}
                {ticket.summary}
              </span>
              {ticket.estimated ? <span className={kit['railPts']}>{fmtPoints(ticket.final_points)}</span> : null}
              <span className={kit['srOnly']}>
                {index === current ? ' — being voted on now' : ticket.estimated ? ' — estimated' : ''}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
