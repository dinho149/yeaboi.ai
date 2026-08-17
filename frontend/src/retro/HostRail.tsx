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

import { Avatar, Dropdown, Eyebrow, Icon } from '../design/primitives';
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

const STATUS_LABELS = CARRIED_STATUSES.map((value) => CARRIED_STATUS_LABELS[value]);

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
  // The live board is an option in the list, not a separate control: going back
  // to today is the same kind of move as going anywhere else.
  const options = [liveLabel, ...runs.map(labelFor)];

  return (
    <aside className={cx(kit['rail'], styles['rail'])} aria-label="Facilitator">
      <section>
        <Eyebrow value={at === 0 ? 'now' : (run?.retro_date ?? '')}>Retro</Eyebrow>

        {/* The picker between the arrows: one control, read as one thing. The
            arrows are for "the one before this", which is the step people take;
            the picker is for the one they remember by name. Same pair poker
            pages its tickets with. */}
        <div className={styles['timeSwitch']} role="group" aria-label="Which retro">
          <Button
            size="s"
            aria-label="The retro before this one"
            disabled={!canBack || loading}
            onClick={() => history.step(1)}
          >
            <Icon name="chevron-left" size={14} />
          </Button>

          <Dropdown
            label="Which retro"
            value={options[at] ?? liveLabel}
            options={options}
            className={cx(styles['timePick'], at > 0 && styles['railPast'])}
            onChange={(next) => history.go(options.indexOf(next))}
          />

          <Button
            size="s"
            aria-label={at === 1 ? 'Back to this retro' : 'The retro after this one'}
            disabled={at === 0}
            onClick={() => history.step(-1)}
          >
            <Icon name="chevron-right" size={14} />
          </Button>
        </div>
      </section>

      {carried.length ? (
        <section key={`carried-${at}`}>
          <Eyebrow value={`${reviewed}/${carried.length}`}>Last retro</Eyebrow>
          <ul className={kit['railList']}>
            {carried.map((item) => {
              const status = (item.status || 'pending') as CarriedStatuses;
              return (
                <li key={item.id} className={styles['carriedRow']}>
                  <span className={cx(kit['railDot'], styles[`dot_${status}`])} aria-hidden="true" />
                  <span className={styles['carriedText']}>{item.text}</span>
                  <Dropdown
                    label={`Status for: ${item.text.slice(0, 60)}`}
                    className={styles['carriedPick']}
                    value={CARRIED_STATUS_LABELS[status]}
                    options={STATUS_LABELS}
                    onChange={(next) => {
                      const picked = CARRIED_STATUSES.find((value) => CARRIED_STATUS_LABELS[value] === next);
                      if (picked && !past) onSetCarriedStatus(item.id, picked);
                    }}
                  />
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {/* One of N, with the neutral option in the list rather than implied by
          nothing being lit — otherwise the only clue the rows do anything is
          clicking one. The role is on the section, not the `<ul>`: a role there
          replaces list semantics and orphans every `<li>`. */}
      {people.length ? (
        <section key={`who-${at}`} role="group" aria-label="Show one person's cards">
          <Eyebrow value={focus || 'everyone'}>Showing</Eyebrow>
          <ul className={kit['railList']}>
            <li>
              <button
                type="button"
                className={cx(kit['railItem'], !focus && kit['railCurrent'])}
                aria-pressed={!focus}
                onClick={() => onFocus('')}
              >
                <span className={styles['everyoneMark']} aria-hidden="true">
                  <Icon name="users" size={13} />
                </span>
                <span className={styles['railName']}>Everyone</span>
                <span className={styles['railCount']}>{people.reduce((sum, p) => sum + p.cards, 0)}</span>
              </button>
            </li>
            {people.map((person) => (
              <li key={person.name}>
                <button
                  type="button"
                  className={cx(kit['railItem'], person.name === focus && kit['railCurrent'])}
                  aria-label={`Only ${person.name}, ${person.cards} ${person.cards === 1 ? 'card' : 'cards'}`}
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
