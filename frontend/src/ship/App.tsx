/**
 * The ship board application — a read-only view of one supervised run.
 *
 * Teammates *watch*: the phase checklist, the agent's live activity, the
 * scrubbed diff, and the validation verdict, streamed over `/api/state`. There
 * are no write controls here on purpose — the approval gate is driven from the
 * host's terminal, and a browser tier that can approve or steer is a later
 * step (a remote approve pushes to origin; a remote steer becomes the agent's
 * prompt). All this component does is render server truth and announce presence.
 *
 * The three-kinds-of-state discipline from the other boards still holds: server
 * truth lives in the store and is read through selectors; identity (pid, name)
 * lives in `localStorage`; there is no local UI state worth a snapshot here.
 */
import { useEffect, useMemo, useState } from 'react';

import { useBoardStream } from '../hooks/useBoardStream';
import { useHeartbeat } from '../hooks/useHeartbeat';
import { useInvite } from '../hooks/useInvite';
import { apiUrl, loadSession, stripCredentialsFromUrl, type Session } from '../runtime/api';
import { participantId, read } from '../runtime/storage';
import { InviteQR, JoinGate, Modal, PageShell, Toast, Toolbar } from '../shared';
import { createBoardStore } from '../store/boardStore';
import { useBoardSelector, useBoardSnapshot } from '../store/useBoard';
import type { ShipActivity, ShipPhaseEvent, ShipState, ShipValidationView, ShipWatcher } from '../types/board';
import type { ShipBoot } from './boot';
import styles from './ship.module.css';

const KEY = { pid: 'ship_pid', name: 'ship_name' } as const;

const NO_PHASES: readonly ShipPhaseEvent[] = [];
const NO_ACTIVITY: readonly ShipActivity[] = [];
const NO_WATCHERS: readonly ShipWatcher[] = [];

/** Human labels for the run status, so a watcher does not read the enum. */
const STATUS_LABEL: Record<string, string> = {
  starting: 'Starting…',
  planned: 'Planned',
  running: 'Agent working',
  awaiting_approval: 'Awaiting the host',
  approved: 'Approved — PR opened',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
  failed: 'Failed',
};

export function App({ boot }: { boot: ShipBoot }) {
  const pid = useMemo(() => participantId(KEY.pid), []);
  const session = useMemo<Session>(() => loadSession('ship', pid), [pid]);
  useEffect(() => {
    if (session.token) stripCredentialsFromUrl();
  }, [session.token]);

  const store = useMemo(() => createBoardStore<ShipState>(), []);
  const joined = Boolean(session.token);
  const status = useBoardStream({ session, store, enabled: joined });

  const [name] = useState(() => read('local', KEY.name) ?? '');
  useHeartbeat({ session, name, avatar: '', enabled: joined });

  const snapshot = useBoardSnapshot(store);
  const runStatus = useBoardSelector(store, (s) => s?.status ?? 'starting');
  const phases = useBoardSelector(store, (s) => s?.phases ?? NO_PHASES);
  const activity = useBoardSelector(store, (s) => s?.activity ?? NO_ACTIVITY);
  const watchers = useBoardSelector(store, (s) => s?.presence ?? NO_WATCHERS);

  const [inviteOpen, setInviteOpen] = useState(false);
  const inviteState = useInvite(session, inviteOpen);

  if (!joined) {
    return (
      <JoinGate
        wordmark="ship"
        heading="Watch a ship run"
        blurb="Enter the code your host shared to watch this story ship — the agent, the diff, and the verdict, live."
        cta="Watch"
      />
    );
  }

  return (
    <PageShell
      chrome={boot.chrome}
      variant="app"
      bar={
        <Toolbar
          subtitle={STATUS_LABEL[runStatus] ?? runStatus}
          tools={
            <button type="button" className={styles['inviteBtn']} onClick={() => setInviteOpen(true)}>
              Invite watchers
            </button>
          }
        >
          <span className={styles['pip']} data-status={status} aria-label={`connection ${status}`} />
          {watchers.length > 0 && (
            <span className={styles['watchers']}>
              {watchers.length} watching{name ? '' : ' · you are anonymous'}
            </span>
          )}
        </Toolbar>
      }
    >
      {snapshot === null ? (
        <p className={styles['empty']}>Connecting to the run…</p>
      ) : (
        <div className={styles['grid']}>
          <PhaseChecklist phases={phases} />
          <StatusBanner state={snapshot} />
          <ActivityFeed activity={activity} />
          <ValidationCard validation={snapshot.validation} />
          <DiffPane statLine={snapshot.diff_stat} patch={snapshot.diff_text} />
          <SideColumn state={snapshot} />
        </div>
      )}

      <Modal open={inviteOpen} onClose={() => setInviteOpen(false)} title="Invite watchers">
        <p className={styles['popNote']}>
          Send the invite link — it carries the watch code, so they land straight on the board. Anyone with the
          link can see the diff, so keep it off anywhere public.
        </p>
        <Toast message={inviteState.notice} onDismiss={inviteState.dismiss} />
        <InviteQR
          qrSrc={apiUrl(session, '/api/qr')}
          inviteUrl={inviteState.invite?.inviteUrl}
          shareUrl={inviteState.invite?.shareUrl}
          joinCode={inviteState.invite?.joinCode}
        />
      </Modal>
    </PageShell>
  );
}

function PhaseChecklist({ phases }: { phases: readonly ShipPhaseEvent[] }) {
  const ICON: Record<string, string> = {
    running: '◐',
    completed: '●',
    partial: '◑',
    failed: '✕',
    no_data: '○',
    fallback: '◌',
  };
  return (
    <section className={styles['card']} aria-label="Pipeline phases">
      <h2 className={styles['h']}>Pipeline</h2>
      <ol className={styles['phases']}>
        {phases.length === 0 && <li className={styles['muted']}>Waiting for the first phase…</li>}
        {phases.map((p) => (
          <li key={p.component_id} className={styles['phase']} data-status={p.status}>
            <span className={styles['phaseIcon']} aria-hidden="true">
              {ICON[p.status] ?? '○'}
            </span>
            <span className={styles['phaseLabel']}>{p.label}</span>
            {p.detail && <span className={styles['phaseDetail']}>{p.detail}</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}

function StatusBanner({ state }: { state: ShipState }) {
  const label = STATUS_LABEL[state.status] ?? state.status;
  return (
    <section className={styles['banner']} data-status={state.status}>
      <div>
        <div className={styles['bannerStatus']}>{label}</div>
        <div className={styles['bannerStory']}>{state.story || state.run_id}</div>
      </div>
      {state.pr_url && (
        <a className={styles['prLink']} href={state.pr_url} target="_blank" rel="noreferrer noopener">
          View pull request →
        </a>
      )}
      {state.gate_resolution === 'rejected' && state.rejection_count > 0 && (
        <span className={styles['rejects']}>rework {state.rejection_count}/3</span>
      )}
    </section>
  );
}

function ActivityFeed({ activity }: { activity: readonly ShipActivity[] }) {
  // Newest first, bounded to what a watcher can scan.
  const recent = activity.slice(-40).reverse();
  return (
    <section className={styles['card']} aria-label="Agent activity">
      <h2 className={styles['h']}>Agent activity</h2>
      {recent.length === 0 ? (
        <p className={styles['muted']}>No activity yet.</p>
      ) : (
        <ul className={styles['feed']}>
          {recent.map((a, i) => (
            <li key={`${i}-${a.kind}`} className={styles['feedItem']} data-kind={a.kind}>
              {a.kind === 'tool' ? (
                <>
                  <span className={styles['toolTag']}>{a.name}</span>
                </>
              ) : (
                <span className={styles['feedText']}>{a.text}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ValidationCard({ validation }: { validation: ShipValidationView }) {
  if (!validation.configured) {
    return (
      <section className={styles['card']} aria-label="Validation">
        <h2 className={styles['h']}>Validation</h2>
        <p className={styles['muted']}>No check command was configured for this run.</p>
      </section>
    );
  }
  return (
    <section className={styles['card']} aria-label="Validation">
      <h2 className={styles['h']}>
        Validation <span data-verdict={validation.passed ? 'pass' : 'fail'}>{validation.passed ? 'passed' : 'failed'}</span>
      </h2>
      <code className={styles['cmd']}>{validation.command}</code>
      {validation.output_tail && <pre className={styles['tail']}>{validation.output_tail}</pre>}
    </section>
  );
}

function DiffPane({ statLine, patch }: { statLine: string; patch: string }) {
  return (
    <section className={styles['diffCard']} aria-label="Diff">
      <h2 className={styles['h']}>
        Diff {statLine && <span className={styles['muted']}>{statLine.trim()}</span>}
      </h2>
      {patch ? (
        // Rendered as text content — NEVER dangerouslySetInnerHTML. Highlighting
        // via innerHTML would undo the CSP that makes an escaped char inert.
        <pre className={styles['diff']}>{colorizeDiff(patch)}</pre>
      ) : (
        <p className={styles['muted']}>No diff yet.</p>
      )}
    </section>
  );
}

/** Split a patch into per-line spans so add/remove lines can be tinted by CSS. */
function colorizeDiff(patch: string) {
  return patch.split('\n').map((line, i) => {
    const kind = line.startsWith('+') && !line.startsWith('+++') ? 'add' : line.startsWith('-') && !line.startsWith('---') ? 'del' : line.startsWith('@@') ? 'hunk' : 'ctx';
    return (
      <span key={i} className={styles['diffLine']} data-kind={kind}>
        {line}
        {'\n'}
      </span>
    );
  });
}

function SideColumn({ state }: { state: ShipState }) {
  return (
    <aside className={styles['side']}>
      <div className={styles['stat']}>
        <span className={styles['statLabel']}>Cost</span>
        <span className={styles['statValue']}>${state.cost_usd.toFixed(2)}</span>
      </div>
      {state.branch && (
        <div className={styles['stat']}>
          <span className={styles['statLabel']}>Branch</span>
          <code className={styles['statValue']}>{state.branch}</code>
        </div>
      )}
      {state.findings.length > 0 && (
        <div className={styles['findings']}>
          <span className={styles['statLabel']}>Findings</span>
          <ul>
            {state.findings.map((f, i) => (
              <li key={i} data-sev={f[1]}>
                {f[2]} <em>{f[1]}</em>
              </li>
            ))}
          </ul>
        </div>
      )}
      {state.warnings.length > 0 && (
        <div className={styles['warnings']}>
          <span className={styles['statLabel']}>Warnings</span>
          <ul>
            {state.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}
