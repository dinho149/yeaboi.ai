/**
 * The planning-poker board.
 *
 * Same composition as the retro board — identity, the live stream, the shared
 * toolbar, the ceremony devices — with poker's own shape in the middle: a rail
 * of tickets, the one under discussion, the table, your hand, and the host's
 * console.
 *
 * ## The moment this port was for
 *
 * **The reveal used to be silent.** Values simply appeared on the next poll:
 * nothing moved, nothing was announced, and on a phone with the deck in view
 * you could miss it entirely and sit waiting for a round that had already
 * finished. It is now the loudest thing the board does — the cards turn over
 * around the table, the duck flaps, and it is announced to assistive tech. That
 * is poker's version of the liveness gap the retro board had.
 *
 * ## Optimistic voting
 *
 * A tap over a tunnel is a round trip plus up to a change-watcher tick before
 * anything moves, which is long enough that people tap again and double-vote.
 * `usePendingOverlay` shows your card as chosen immediately and hands back to
 * the server the moment it answers — including when it disagrees, because a
 * host revote should visibly override you.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Duck, useDuckPulse, type DuckRest } from '../design/primitives';
import { useAlarm } from '../hooks/useAlarm';
import { useBoardStream } from '../hooks/useBoardStream';
import { useConfetti } from '../hooks/useConfetti';
import { useCountdown } from '../hooks/useCountdown';
import { useHeartbeat } from '../hooks/useHeartbeat';
import { useHostBroadcast } from '../hooks/useHostBroadcast';
import { useMusic } from '../hooks/useMusic';
import { usePendingOverlay } from '../hooks/usePendingOverlay';
import { apiUrl, loadSession, pollState, stripCredentialsFromUrl, type Session } from '../runtime/api';
import { participantId, read, write } from '../runtime/storage';
import { applyTheme, setTheme, storedTheme, THEME_KEYS, type Theme } from '../runtime/theme';
import {
  ConfettiCanvas,
  IconButton,
  InviteQR,
  JoinGate,
  Modal,
  MusicPlayer,
  Popover,
  PresenceRow,
  ProfileModal,
  Roster,
  ThemeSwitcher,
  TimerControls,
  TimerReadout,
  Toolbar,
  Visualizer,
} from '../shared';
import { createBoardStore } from '../store/boardStore';
import { useBoardSelector, useBoardSnapshot } from '../store/useBoard';
import { AVATARS } from '../types/enums';
import type { Participant, PokerVote, TicketMeta, TicketView } from '../types/board';
import { createPokerActions, type TicketEdit } from './actions';
import type { PokerBoot } from './boot';
import { Console } from './Console';
import { Deck } from './Deck';
import { Duel } from './Duel';
import { EditTicketModal } from './EditTicketModal';
import { Rail } from './Rail';
import { Results } from './Results';
import { Table } from './Table';
import { TicketPanel } from './Ticket';
import { useDuelMic } from './useDuelMic';
import styles from './poker.module.css';

/** Storage keys. Unchanged from the page this replaces, so a live session survives. */
const KEY = { pid: 'poker_pid', name: 'poker_name', avatar: 'poker_avatar' } as const;

const NO_VOTES: readonly PokerVote[] = [];
const NO_TICKETS: readonly TicketMeta[] = [];
const NO_PEOPLE: readonly Participant[] = [];

export function App({ boot }: { boot: PokerBoot }) {
  // ── Identity and session ───────────────────────────────────────────────
  const pid = useMemo(() => participantId(KEY.pid), []);
  const session = useMemo<Session>(() => loadSession('poker', pid), [pid]);
  useEffect(() => {
    if (session.token) stripCredentialsFromUrl();
  }, [session.token]);

  const [name, setName] = useState(() => read('local', KEY.name) ?? '');
  const [avatar, setAvatar] = useState(() => read('local', KEY.avatar) ?? (AVATARS[0] as string));
  const [profileOpen, setProfileOpen] = useState(false);
  const joined = Boolean(name);

  useEffect(() => {
    if (session.token && !name) setProfileOpen(true);
  }, [session.token, name]);

  /** Renders the console. Every endpoint behind it re-checks server-side. */
  const isHost = Boolean(session.admin);

  // ── Server truth ───────────────────────────────────────────────────────
  const store = useMemo(() => createBoardStore<import('../types/board').PokerState>(), []);
  const actions = useMemo(() => createPokerActions(session, store), [session, store]);
  const status = useBoardStream({ session, store, enabled: joined });

  const snapshot = useBoardSnapshot(store);
  const phase = useBoardSelector(store, (s) => s?.phase ?? 'voting');
  const locked = useBoardSelector(store, (s) => s?.locked ?? false);
  const votes = useBoardSelector(store, (s) => s?.votes ?? NO_VOTES);
  const tickets = useBoardSelector(store, (s) => s?.tickets_meta ?? NO_TICKETS);
  const presence = useBoardSelector(store, (s) => s?.presence ?? NO_PEOPLE);
  const serverVote = useBoardSelector(store, (s) => s?.mine_value ?? '');

  const revealed = phase !== 'voting';
  const ticket = snapshot?.ticket ?? null;
  const duel = snapshot?.duel ?? null;
  const ticketIndex = snapshot?.ticket_index ?? 0;
  const ticketCount = snapshot?.ticket_count ?? 0;

  // ── Local UI state ─────────────────────────────────────────────────────
  const [theme, setLocalTheme] = useState<Theme>(() => storedTheme(THEME_KEYS.poker) ?? 'midnight');
  const [inviteOpen, setInviteOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [musicBlocked, setMusicBlocked] = useState(false);
  const [notice, setNotice] = useState('');

  const chooseTheme = useCallback((next: Theme) => {
    setLocalTheme(next);
    setTheme(next, THEME_KEYS.poker);
  }, []);
  useEffect(() => applyTheme(theme), [theme]);

  // A tracker write failure is the host's to see and to keep seeing; anything
  // the host themselves just did replaces it.
  useEffect(() => {
    if (snapshot?.notice) setNotice(snapshot.notice);
  }, [snapshot?.notice]);

  // ── Ceremony devices ───────────────────────────────────────────────────
  useHeartbeat({ session, name, avatar, enabled: joined });

  const music = useMusic(boot.musicChannels);
  const [confettiRef, fireConfetti] = useConfetti();
  const fireAlarm = useAlarm();
  const onTimerFinish = useCallback(() => {
    fireConfetti();
    fireAlarm();
  }, [fireConfetti, fireAlarm]);
  const { remaining } = useCountdown(snapshot?.timer, onTimerFinish);

  useHostBroadcast(snapshot?.broadcast, {
    onTheme: chooseTheme,
    onMusic: (command) => music.cast(command.channel, command.playing),
    onAutoplayBlocked: setMusicBlocked,
  });

  const mic = useDuelMic(session, duel);

  // ── Peek: reading a ticket the room is not voting on ───────────────────
  const [peekIndex, setPeekIndex] = useState<number | null>(null);
  const [peek, setPeek] = useState<TicketView | null>(null);

  // Fall back to live when the peeked ticket became the live one, or when the
  // batch shrank under it.
  useEffect(() => {
    if (peekIndex === null) return;
    if (peekIndex === ticketIndex || peekIndex >= ticketCount) setPeekIndex(null);
  }, [peekIndex, ticketIndex, ticketCount]);

  const peekRev = peekIndex === null ? -1 : (tickets[peekIndex]?.rev ?? 0);
  useEffect(() => {
    if (peekIndex === null) {
      setPeek(null);
      return;
    }
    let live = true;
    setPeek(null);
    void pollState<TicketView>(session, { path: `/api/ticket?i=${peekIndex}` }).then((result) => {
      // The index can change while the request is in flight. Without this, a
      // slow fetch for ticket 3 lands after you have moved to ticket 5 and
      // paints 3's body under 5's banner.
      if (live && result.changed) setPeek(result.data);
    });
    return () => {
      live = false;
    };
    // `peekRev` is in here on purpose: it moves when the host edits or finalizes
    // the ticket being previewed, and refetching is how the preview stops being
    // stale within one poll cycle.
  }, [session, peekIndex, peekRev]);

  // ── Voting ─────────────────────────────────────────────────────────────
  const vote = usePendingOverlay(serverVote, {
    onTimeout: () => setNotice('Your vote did not reach the board — try again.'),
  });

  const deckClosed = locked || phase !== 'voting' || !ticket || peekIndex !== null;
  const deckReason =
    peekIndex !== null
      ? 'Previewing a ticket — “Back to live” to vote'
      : locked
        ? '🔒 Voting locked by the host'
        : !ticket
          ? 'No tickets loaded'
          : phase !== 'voting'
            ? 'Voting closed — waiting for the host'
            : '';

  const castVote = useCallback(
    (value: string) => {
      const next = value === vote.value ? '' : value;
      vote.set(next);
      void actions.vote(value, vote.value).then((error) => {
        if (error) {
          vote.clear();
          setNotice(error);
        }
      });
    },
    [actions, vote]
  );

  // ── The reveal, made loud ──────────────────────────────────────────────
  const [announcement, setAnnouncement] = useState('');
  const lastPhase = useRef(phase);
  useEffect(() => {
    if (phase === lastPhase.current) return;
    const before = lastPhase.current;
    lastPhase.current = phase;
    if (phase === 'revealed' && before === 'voting') {
      const cast = votes.filter((seat) => seat.value !== undefined).length;
      setAnnouncement(`Votes revealed — ${cast} ${cast === 1 ? 'vote' : 'votes'} in.`);
    } else if (phase === 'duel') {
      setAnnouncement(`The floor is open: ${duel?.low.name ?? 'low'} versus ${duel?.high.name ?? 'high'}.`);
    } else if (phase === 'voting') {
      setAnnouncement('New round — the deck is open.');
    }
  }, [phase, votes, duel]);

  /**
   * What the duck is doing.
   *
   * Ordered by how much the state matters, since only one can show: a dead
   * connection outranks a locked board outranks the last ten seconds of a
   * speaking turn. `useDuckPulse` enforces that a decorative pulse cannot
   * mask any of them.
   */
  const duckRest: DuckRest =
    status === 'retrying' ? 'offline' : locked ? 'locked' : remaining !== null && remaining <= 10 ? 'urgent' : 'idle';
  const [duckState, duckPulse] = useDuckPulse(duckRest);

  useEffect(() => {
    if (phase === 'revealed' && lastPhase.current === 'revealed') return;
    if (phase === 'revealed') duckPulse('startled');
  }, [phase, duckPulse]);

  const peopleHere = presence.length;
  const lastPeople = useRef(peopleHere);
  useEffect(() => {
    if (peopleHere > lastPeople.current) duckPulse('joined');
    lastPeople.current = peopleHere;
  }, [peopleHere, duckPulse]);

  // A vote landing is the board's other sign of life while the round is open —
  // the seats themselves only change from a face to a face-with-a-tick.
  const votedCount = votes.filter((seat) => seat.voted).length;
  const lastVoted = useRef(votedCount);
  useEffect(() => {
    if (votedCount > lastVoted.current) duckPulse('card');
    lastVoted.current = votedCount;
  }, [votedCount, duckPulse]);

  // ── Handlers ───────────────────────────────────────────────────────────
  const run = useCallback((promise: Promise<string>) => {
    void promise.then((error) => {
      if (error) setNotice(error);
    });
  }, []);

  const saveProfile = useCallback(({ name: newName, avatar: newAvatar }: { name: string; avatar: string }) => {
    setName(newName);
    setAvatar(newAvatar);
    write('local', KEY.name, newName);
    write('local', KEY.avatar, newAvatar);
    setProfileOpen(false);
  }, []);

  const saveEdit = useCallback(
    (edit: TicketEdit) => {
      if (!ticket) return;
      run(actions.editTicket(ticket.key, edit));
    },
    [actions, run, ticket]
  );

  // ── The gate ───────────────────────────────────────────────────────────
  if (!session.token) {
    return (
      <JoinGate
        wordmark="poker"
        eyebrow="Planning poker"
        frameTitle="yeaboi — poker"
        heading="Join the session"
        blurb="Enter the share code from the host's screen."
        cta="Join"
      />
    );
  }

  const allIn = phase === 'voting' && votes.length > 0 && votes.every((seat) => seat.voted);
  const estimated = snapshot?.progress.estimated ?? 0;

  return (
    // `data-host` drives one CSS rule: lifting the sticky deck clear of the
    // console's bottom sheet on a phone. Read from the same flag that renders
    // the console, so a guest never gets the offset for a bar they do not have.
    <div className={styles['app']} data-host={isHost ? 'true' : undefined}>
      <Toolbar
        brand="poker"
        mark={<Duck state={duckState} size={30} />}
        subtitle={
          <>
            {boot.scope ? `${boot.scope} · ` : ''}
            {ticketCount ? `${estimated}/${ticketCount} estimated` : 'no tickets'}
            {status === 'retrying' ? <span className={styles['offline']}> · reconnecting…</span> : null}
          </>
        }
        tools={
          <>
            <Visualizer playing={music.playing} />

            <IconButton
              icon="☰"
              label={railOpen ? 'Hide the ticket list' : 'Show the ticket list'}
              active={railOpen}
              className={styles['railToggle']}
              onClick={() => setRailOpen(!railOpen)}
            />

            {isHost ? (
              <IconButton
                icon="🔒"
                label={locked ? 'Unlock voting' : 'Lock voting'}
                active={locked}
                onClick={() => run(actions.setLocked(!locked))}
              />
            ) : null}

            <Popover trigger={<span aria-hidden="true">♪</span>} label="Music">
              <MusicPlayer
                music={music}
                channels={boot.musicChannels}
                footer={
                  isHost ? (
                    <button
                      type="button"
                      className={styles['castBtn']}
                      onClick={() => run(actions.castMusic(music.playing, music.channel))}
                    >
                      <span aria-hidden="true">📣</span> Play for everyone
                    </button>
                  ) : null
                }
              />
            </Popover>

            <Popover
              trigger={
                <>
                  <span aria-hidden="true">⏱</span>
                  <TimerReadout remaining={remaining} />
                </>
              }
              label="Timer"
            >
              {isHost ? (
                <TimerControls
                  running={Boolean(snapshot?.timer.running)}
                  onStart={(seconds) => run(actions.startTimer(seconds))}
                  onStop={() => run(actions.stopTimer())}
                />
              ) : (
                <p className={styles['popNote']}>The host controls the timer.</p>
              )}
            </Popover>

            <Popover trigger={<span aria-hidden="true">◑</span>} label="Theme">
              <ThemeSwitcher
                value={theme}
                onChange={chooseTheme}
                footer={
                  isHost ? (
                    <button type="button" className={styles['castBtn']} onClick={() => run(actions.castTheme(theme))}>
                      <span aria-hidden="true">📣</span> Apply to everyone
                    </button>
                  ) : null
                }
              />
            </Popover>

            <IconButton icon="✉" label="Invite the team" tone="primary" onClick={() => setInviteOpen(true)}>
              Invite
            </IconButton>
          </>
        }
      >
        <div className={styles['identity']}>
          <button type="button" className={styles['meChip']} onClick={() => setProfileOpen(true)}>
            <span aria-hidden="true">{avatar}</span>
            <span className={styles['meName']}>{name || 'Set your name'}</span>
            <span aria-hidden="true" className={styles['pen']}>
              ✎
            </span>
          </button>

          <PresenceRow people={presence.filter((person) => person.name !== name)} />

          <Popover
            align="left"
            trigger={
              <>
                <span aria-hidden="true">👥</span>
                <span className={styles['roomCount']}>{Math.max(1, presence.length)}</span>
              </>
            }
            label="Who is in the room"
          >
            <Roster people={presence} meName={name} />
          </Popover>
        </div>
      </Toolbar>

      {locked ? (
        <p className={styles['lockBanner']} role="alert">
          <span aria-hidden="true">🔒</span> The host locked voting.
        </p>
      ) : null}

      {musicBlocked ? (
        <button
          type="button"
          className={styles['musicBanner']}
          onClick={() =>
            void music
              .play()
              .then(() => setMusicBlocked(false))
              .catch(() => {})
          }
        >
          <span aria-hidden="true">▶</span> The host started music — tap to listen
        </button>
      ) : null}

      <div className={styles['layout']}>
        <Rail
          tickets={tickets}
          current={ticketIndex}
          peeking={peekIndex}
          estimated={estimated}
          open={railOpen}
          onPick={setPeekIndex}
          onClose={() => setRailOpen(false)}
        />
        {/* Tapping outside the drawer closes it. A div rather than a button
            because it is a dismissal surface, not a control — the drawer's own
            toggle in the toolbar is the keyboard path. */}
        {railOpen ? <div className={styles['railBackdrop']} onClick={() => setRailOpen(false)} /> : null}

        <main className={styles['main']}>
          <TicketPanel
            ticket={ticket}
            phase={phase}
            index={ticketIndex}
            count={ticketCount}
            peek={peek}
            peekIndex={peekIndex}
            liveKey={tickets[ticketIndex]?.key ?? ''}
            isHost={isHost}
            onBackToLive={() => setPeekIndex(null)}
            onGotoPeek={() => {
              const target = peekIndex;
              setPeekIndex(null);
              if (target !== null) run(actions.goto(target));
            }}
          />

          <Results
            distribution={snapshot?.distribution ?? {}}
            median={snapshot?.median ?? null}
            suggestion={snapshot?.suggestion ?? null}
            ai={snapshot?.ai ?? { pending: false, note: '', suggested: null, confidence: '', evidence: [] }}
            revealed={revealed}
          />

          {duel ? <Duel duel={duel} mic={mic} /> : null}

          <Table votes={votes} revealed={revealed} />

          <Deck
            mine={vote.value}
            pending={vote.pending}
            disabled={deckClosed}
            reason={deckReason}
            onVote={castVote}
          />
        </main>

        {isHost && snapshot ? (
          <Console
            state={snapshot}
            allIn={allIn}
            notice={notice}
            onReveal={() => run(actions.reveal())}
            onRevote={() => run(actions.revote())}
            onAskAi={() => run(actions.askAi())}
            onOpenDuel={(seconds) => run(actions.openDuel(seconds))}
            onNextTurn={() => run(actions.nextTurn())}
            onCloseDuel={() => run(actions.closeDuel())}
            onFinalize={(points) => run(actions.finalize(points))}
            onGoto={(index) => run(actions.goto(index))}
            onEdit={() => setEditOpen(true)}
          />
        ) : null}
      </div>

      {/* Phase changes are visual everywhere else on this page. */}
      <div className={styles['srOnly']} role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>

      <ConfettiCanvas canvasRef={confettiRef} />

      <ProfileModal
        open={profileOpen}
        name={name}
        avatar={avatar}
        avatars={AVATARS}
        adjectives={boot.adjectives}
        nouns={boot.nouns}
        onSave={saveProfile}
        onClose={() => setProfileOpen(false)}
        required={!name}
      />

      <EditTicketModal open={editOpen} ticket={ticket} onSave={saveEdit} onClose={() => setEditOpen(false)} />

      <Modal open={inviteOpen} onClose={() => setInviteOpen(false)} title="Invite the team">
        <p className={styles['popNote']}>
          Scan to open the session, then enter the share code from the host&rsquo;s screen.
        </p>
        <InviteQR qrSrc={apiUrl(session, '/api/qr')} />
      </Modal>
    </div>
  );
}
