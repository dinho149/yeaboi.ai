/**
 * The landing: two families of modes, the way the terminal app opens.
 *
 * yeaboi is twelve modes and the app was showing an empty project list, which
 * described a different product. This is the shape the TUI has — Humans and
 * Agents as two groups of cards, each carrying its accent — and it is what a
 * person should see on arrival.
 *
 * A mode's `support` is rendered rather than hidden. A card that does nothing
 * with no explanation reads as broken; a card that says *why* reads as a
 * roadmap, and it is honest about how much of the product is here.
 *
 * TODO(design): cards in a grid. The TUI's landing is a split with keyboard
 * navigation and a mascot; this is the information, not the staging.
 */

import { Button } from '../design/primitives';
import { get } from './api';
import { navigate } from './router';
import { AsyncView, EmptyState } from './Slots';
import { useAsync } from './useAsync';
import type { ModeCard } from './types';
import styles from './app.module.css';

const FAMILY_LABEL: Record<ModeCard['family'], string> = {
  humans: 'For your team',
  agents: 'For your agents',
};

function SupportTag({ mode }: { mode: ModeCard }) {
  if (mode.support === 'run') return null;
  return (
    <span className={styles.role}>{mode.support === 'view' ? 'read only' : 'not yet'}</span>
  );
}

function Card({ mode }: { mode: ModeCard }) {
  const openable = mode.support !== 'soon';
  return (
    <li
      className={styles.modeCard}
      // The accent is the one piece of presentation the payload carries, and
      // it is a key from an allowlist rather than a colour — same rule as
      // every other surface.
      {...(mode.accent ? { 'data-mode': mode.accent } : {})}
    >
      <div className={styles.modeHead}>
        <h3 className={styles.modeTitle}>{mode.title}</h3>
        {mode.beta ? <span className={styles.beta}>beta</span> : null}
        <SupportTag mode={mode} />
      </div>
      <p className={styles.modeDescription}>{mode.description}</p>
      {mode.note ? <p className={styles.modeNote}>{mode.note}</p> : null}
      {openable ? (
        <Button size="s" onClick={() => navigate(`/modes/${mode.key}`)}>
          Open
        </Button>
      ) : null}
    </li>
  );
}

export function Landing() {
  const state = useAsync(() => get<{ modes: ModeCard[] }>('/api/modes'), [], {
    isEmpty: (data) => data.modes.length === 0,
  });

  return (
    <AsyncView state={state} empty={<EmptyState title="No modes" />}>
      {(data) => (
        <>
          {(['humans', 'agents'] as const).map((family) => (
            <section key={family} className={styles.modeSection}>
              <h2 className={styles.eyebrow}>{FAMILY_LABEL[family]}</h2>
              <ul className={styles.modeGrid}>
                {data.modes
                  .filter((mode) => mode.family === family)
                  .map((mode) => (
                    <Card key={mode.key} mode={mode} />
                  ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </AsyncView>
  );
}
