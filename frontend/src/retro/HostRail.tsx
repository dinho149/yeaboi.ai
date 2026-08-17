/**
 * The facilitator's column.
 *
 * Everything only the host does, in one place beside the board rather than
 * scattered across the bar, the notch and a strip above the columns. Four
 * things, in the order a retro uses them: which retro you are looking at, last
 * week's actions to close out, whose cards to show while you go round, and the
 * suggestions you ask for once the writing has stopped.
 *
 * Built out of the kit's rail — an eyebrow over a list of borderless rows, the
 * same one poker's ticket list is. Nothing in here is a card: a rail of framed
 * boxes beside four columns of framed cards is twice the furniture for a
 * quarter of the content.
 *
 * Guests never see it, so for everyone but one person the board is four columns
 * and nothing else.
 */

import { Avatar, Eyebrow, Icon } from '../design/primitives';
import { boardStyles as kit } from '../shared/board';
import { Button } from '../shared';
import { cx } from '../runtime/cx';
import { CARRIED_STATUS_LABELS, CARRIED_STATUSES, type CarriedStatuses } from '../types/enums';
import type { RetroCard } from '../types/board';
import type { Person } from './Walkthrough';
import type { History } from './useHistory';
import styles from './retro.module.css';

export interface HostRailProps {
  history: History;
  /** What today's board is called. */
  liveLabel: string;
  carried: readonly RetroCard[];
  onSetCarriedStatus(itemId: string, status: CarriedStatuses): void;
  people: readonly Person[];
  focus: string;
  onFocus(name: string): void;
  suggesting: boolean;
  onSuggest(): void;
  /** True when a past retro is open — most of the rail is inert then. */
  past: boolean;
}

/** Labels have to be unique — two retros in one sprint would collide. */
function labelFor(run: { retro_date: string; sprint_name?: string }): string {
  return run.sprint_name?.trim() || run.retro_date || '—';
}

export function HostRail({
  history,
  liveLabel,
  carried,
  onSetCarriedStatus,
  people,
  focus,
  onFocus,
  suggesting,
  onSuggest,
  past,
}: HostRailProps) {
  const { runs, at, loading } = history;
  // Back is open until the list says otherwise: it is what fetches the list, so
  // disabling it before then would mean nobody could ever ask.
  const canBack = runs.length === 0 ? at === 0 : at < runs.length;
  const run = at > 0 ? runs[at - 1] : undefined;
  const reviewed = carried.filter((item) => item.status && item.status !== 'pending').length;

  return (
    <aside className={cx(kit['rail'], styles['rail'])} aria-label="Facilitator">
      <section>
        <div className={kit['railHead']}>
          <p className={cx(kit['railScope'], at > 0 && styles['railPast'])}>
            {at === 0 ? liveLabel : run ? labelFor(run) : '…'}
          </p>
          <Eyebrow value={at === 0 ? 'now' : (run?.retro_date ?? '')}>Retro</Eyebrow>
        </div>

        <div className={styles['timeSwitch']} role="group" aria-label="Which retro">
          <button
            type="button"
            className={styles['timeStep']}
            aria-label="The retro before this one"
            disabled={!canBack || loading}
            onClick={() => history.step(1)}
          >
            <Icon name="chevron-left" size={14} />
          </button>
          <button
            type="button"
            className={styles['timeStep']}
            aria-label={at === 1 ? 'Back to this retro' : 'The retro after this one'}
            disabled={at === 0}
            onClick={() => history.step(-1)}
          >
            <Icon name="chevron-right" size={14} />
          </button>
        </div>
      </section>

      {carried.length ? (
        <section>
          <Eyebrow value={`${reviewed}/${carried.length}`}>Last retro</Eyebrow>
          <ul className={kit['railList']}>
            {carried.map((item) => {
              const status = (item.status || 'pending') as CarriedStatuses;
              return (
                <li key={item.id} className={styles['carriedRow']}>
                  <span className={cx(kit['railDot'], styles[`dot_${status}`])} aria-hidden="true" />
                  <span className={styles['carriedText']}>{item.text}</span>
                  <select
                    className={styles['carriedSelect']}
                    value={status}
                    disabled={past}
                    aria-label={`Status for: ${item.text.slice(0, 60)}`}
                    onChange={(event) =>
                      onSetCarriedStatus(item.id, (event.target as HTMLSelectElement).value as CarriedStatuses)
                    }
                  >
                    {CARRIED_STATUSES.map((value) => (
                      <option key={value} value={value}>
                        {CARRIED_STATUS_LABELS[value]}
                      </option>
                    ))}
                  </select>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {/* The role is on the section, not the list: a `role="group"` on a `<ul>`
          replaces list semantics and orphans every `<li>` in it. */}
      {people.length ? (
        <section role="group" aria-label="Walkthrough">
          <Eyebrow value={focus || 'everyone'}>Whose cards</Eyebrow>
          <ul className={kit['railList']}>
            {people.map((person) => (
              <li key={person.name}>
                <button
                  type="button"
                  className={cx(kit['railItem'], person.name === focus && kit['railCurrent'])}
                  aria-label={`${person.name}, ${person.cards} ${person.cards === 1 ? 'card' : 'cards'}`}
                  aria-pressed={person.name === focus}
                  onClick={() => onFocus(person.name === focus ? '' : person.name)}
                >
                  <Avatar name={person.name} emoji={person.avatar} size={20} />
                  <span className={styles['railName']}>{person.name}</span>
                  <span className={styles['railCount']}>{person.cards}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {past ? null : (
        <section className={styles['railAction']}>
          <Eyebrow>Action items</Eyebrow>
          <Button tone="primary" disabled={suggesting} onClick={onSuggest}>
            <Icon name="sparkles" size={14} /> {suggesting ? 'Thinking…' : 'Suggest from the feedback'}
          </Button>
        </section>
      )}
    </aside>
  );
}
