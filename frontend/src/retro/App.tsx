/**
 * The retro board application.
 *
 * Composes the shared library into the ceremony: identity, the live stream, the
 * columns, and the host controls. Almost nothing in here is retro-specific
 * plumbing — the transport, the popovers, the modal, the timer, the music and
 * the theme picker all arrive from `shared/` and `hooks/`, which is the whole
 * point of the migration. What is left is the retro's own shape.
 *
 * ## Three kinds of state, kept apart
 *
 * 1. **Server truth** — the snapshot, in the store, read through selectors.
 * 2. **Local UI** — focus author, grouping, which panel is open. (A card draft
 *    is more local still: it lives in the column composer that owns it.)
 *    `useState` here, never derived from a snapshot.
 * 3. **Identity** — name, avatar, palette. `localStorage`, so a reload mid-retro
 *    puts you back as yourself rather than as a new anonymous participant.
 *
 * Keeping (2) out of (1) is what removes the `editingHere` freeze; see CardView.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Duck, Icon, useDuckPulse, type DuckRest } from '../design/primitives';
import { useArrivals } from '../motion';
import { useAlarm } from '../hooks/useAlarm';
import { useBoardStream } from '../hooks/useBoardStream';
import { useConfetti } from '../hooks/useConfetti';
import { useCountdown } from '../hooks/useCountdown';
import { useHeartbeat } from '../hooks/useHeartbeat';
import { useHostBroadcast } from '../hooks/useHostBroadcast';
import { useInvite } from '../hooks/useInvite';
import { useMusic } from '../hooks/useMusic';
import { apiUrl, loadSession, stripCredentialsFromUrl, type Session } from '../runtime/api';
import { participantId, read, write } from '../runtime/storage';
import { applyTheme, setTheme, storedTheme, THEME_KEYS, type Theme } from '../runtime/theme';
import {
  Button,
  ConfettiCanvas,
  IconButton,
  InviteQR,
  JoinGate,
  Modal,
  MusicPlayer,
  PageShell,
  Popover,
  ProfileModal,
  ThemeSwitcher,
  TimerControls,
  TimerReadout,
  Toast,
  Toolbar,
  Visualizer,
} from '../shared';
import { cx } from '../runtime/cx';
import { boardStyles as kit, Room } from '../shared/board';
import { createBoardStore } from '../store/boardStore';
import { useBoardSelector, useBoardSnapshot } from '../store/useBoard';
import { AVATARS, type CarriedStatuses, type RetroGrids } from '../types/enums';
import type { Participant, RetroCard, RetroState, TypingEntry } from '../types/board';
import { createRetroActions } from './actions';
import { Board } from './Board';
import { CarriedStrip } from './CarriedStrip';
import { FocusControls } from './FocusBar';
import type { RetroBoot } from './boot';
import styles from './retro.module.css';

/** How long after the last keystroke the typing indicator lingers. */
const TYPING_LINGER_MS = 2500;

/** Storage keys. Unchanged from the legacy page so an in-flight retro survives the flip. */
const KEY = { pid: 'retro_pid', name: 'retro_name', avatar: 'retro_avatar' } as const;

// Stable empty defaults. A fresh `[]` in a selector would be a new reference
// every call, and `useSyncExternalStore` compares with Object.is — the board
// would re-render in a loop before the first snapshot ever arrived.
const NO_CARDS: readonly RetroCard[] = [];
const NO_PEOPLE: readonly Participant[] = [];
const NO_TYPING: readonly TypingEntry[] = [];

export function App({ boot }: { boot: RetroBoot }) {
  // ── Identity and session ───────────────────────────────────────────────
  const pid = useMemo(() => participantId(KEY.pid), []);
  // Fixed for the lifetime of the document. Arriving without a token renders
  // the gate, which navigates to `/?token=…` on success rather than mutating
  // this — so there is no in-place session change to model.
  const session = useMemo<Session>(() => loadSession('retro', pid), [pid]);
  useEffect(() => {
    if (session.token) stripCredentialsFromUrl();
  }, [session.token]);

  const [name, setName] = useState(() => read('local', KEY.name) ?? '');
  const [avatar, setAvatar] = useState(() => read('local', KEY.avatar) ?? (AVATARS[0] as string));
  const [profileOpen, setProfileOpen] = useState(false);
  const joined = Boolean(name);

  // Ask for a name as soon as there is a board to join, not before — the gate
  // is enough of a wall for someone who has not got in yet.
  useEffect(() => {
    if (session.token && !name) setProfileOpen(true);
  }, [session.token, name]);

  /**
   * Host powers are decided by this flag **for rendering only**.
   *
   * Every privileged endpoint re-checks the admin secret server-side with a
   * constant-time compare (`_admin_authed`), so flipping this in devtools
   * reveals buttons whose requests then come back 403.
   */
  const isHost = Boolean(session.admin);

  // ── Server truth ───────────────────────────────────────────────────────
  const store = useMemo(() => createBoardStore<RetroState>(), []);
  const actions = useMemo(() => createRetroActions(session, store), [session, store]);
  const status = useBoardStream({ session, store, enabled: joined });

  const snapshot = useBoardSnapshot(store);
  const cards = useBoardSelector(store, (s) => s?.cards ?? NO_CARDS);
  const carried = useBoardSelector(store, (s) => s?.carried ?? NO_CARDS);
  const locked = useBoardSelector(store, (s) => s?.locked ?? false);

  // ── Local UI state ─────────────────────────────────────────────────────
  const [theme, setLocalTheme] = useState<Theme>(() => storedTheme(THEME_KEYS.site) ?? 'midnight');
  const [focus, setFocus] = useState('');
  const [inviteOpen, setInviteOpen] = useState(false);
  // Fetched on open rather than read from the boot payload: the page is
  // served unauthenticated, so a join code in the island would be readable by
  // anyone who reaches the board, token or not. Also puts it on the clipboard.
  const invite = useInvite(session, inviteOpen);
  const [musicBlocked, setMusicBlocked] = useState(false);
  const [typingGrid, setTypingGrid] = useState('');
  // Which emoji *this browser* has reacted with, per card. The server does not
  // put raw pids on the wire, so "did I react" is only knowable from the
  // `reacted` flag each toggle answers with.
  const [myReactions, setMyReactions] = useState<ReadonlyMap<string, ReadonlySet<string>>>(new Map());

  const chooseTheme = useCallback((next: Theme) => {
    setLocalTheme(next);
    setTheme(next, THEME_KEYS.site);
  }, []);
  useEffect(() => applyTheme(theme), [theme]);

  // ── Presence, typing, and the ceremony devices ─────────────────────────
  useHeartbeat({ session, name, avatar, typingGrid, enabled: joined });

  const typingTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Which column you are writing into, reported by whichever column composer is
  // being typed in. The linger is what stops the ghost card flickering out
  // between two keystrokes.
  const onTyping = useCallback((grid: RetroGrids) => {
    setTypingGrid(grid);
    clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => setTypingGrid(''), TYPING_LINGER_MS);
  }, []);
  useEffect(() => () => clearTimeout(typingTimer.current), []);

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

  // ── Derived views of the snapshot ──────────────────────────────────────
  const presence = useBoardSelector(store, (s) => s?.presence ?? NO_PEOPLE);
  const avatarsByName = useMemo(() => {
    const map = new Map<string, string>();
    for (const person of presence) if (person.avatar) map.set(person.name, person.avatar);
    return map;
  }, [presence]);

  const typingByGrid = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const entry of snapshot?.typing ?? NO_TYPING) {
      if (entry.name === name) continue; // your own indicator is not news to you
      const bucket = map.get(entry.grid);
      if (bucket) bucket.push(entry.name);
      else map.set(entry.grid, [entry.name]);
    }
    return map;
  }, [snapshot?.typing, name]);

  /**
   * Cards that arrived from somebody else, for the entrance animation.
   *
   * `card.mine` is the filter that matters: your own card must appear the
   * instant you press Add, with no animation, or your own typing feels laggy.
   */
  const cardIds = useMemo(() => cards.map((card) => card.id), [cards]);
  const notMine = useMemo(() => {
    const mine = new Set(cards.filter((card) => card.mine).map((card) => card.id));
    return (id: string): boolean => !mine.has(id);
  }, [cards]);
  const arrivals = useArrivals(cardIds, notMine, Boolean(snapshot));

  /**
   * What the duck is doing.
   *
   * Ordered by how much the state matters, because only one can show. A dead
   * connection outranks a locked board outranks the last ten seconds on the
   * clock — and all three outrank the decorative flap, so a card landing just
   * as the tunnel drops cannot leave the board looking healthy while it is
   * stale. `useDuckPulse` enforces that; this only decides the resting state.
   */
  const duckRest: DuckRest =
    status === 'retrying' ? 'offline' : locked ? 'locked' : remaining !== null && remaining <= 10 ? 'urgent' : 'idle';
  const [duckState, duckPulse] = useDuckPulse(duckRest);

  const peopleHere = presence.length;
  const lastPeople = useRef(peopleHere);
  useEffect(() => {
    if (peopleHere > lastPeople.current) duckPulse('joined');
    lastPeople.current = peopleHere;
  }, [peopleHere, duckPulse]);

  const lastArrivalCount = useRef(0);
  useEffect(() => {
    if (arrivals.size > lastArrivalCount.current) duckPulse('card');
    lastArrivalCount.current = arrivals.size;
  }, [arrivals, duckPulse]);

  /** Human authors with at least one card, sorted — the walkthrough running order. */
  const authors = useMemo(() => {
    const set = new Set<string>();
    for (const card of cards) if (card.origin !== 'ai' && card.author) set.add(card.author);
    return [...set].sort();
  }, [cards]);

  // An author who has left mid-walkthrough would otherwise leave every column
  // filtered to nothing, with no obvious way back.
  useEffect(() => {
    if (focus && !authors.includes(focus)) setFocus('');
  }, [focus, authors]);

  const stepFocus = useCallback(
    (delta: number) => {
      if (!authors.length) return;
      const at = authors.indexOf(focus);
      const next = authors[(at + delta + authors.length) % authors.length];
      if (next) setFocus(next);
    },
    [authors, focus]
  );

  // Walkthrough keys, bound at the document so they work while reading the
  // cards rather than only while the bar itself holds focus. Ignored while a
  // text field is focused, or ← / → would steal the caret keys.
  useEffect(() => {
    if (!focus) return;
    const onKey = (event: KeyboardEvent): void => {
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('input, textarea, select')) return;
      if (event.key === 'ArrowRight') stepFocus(1);
      else if (event.key === 'ArrowLeft') stepFocus(-1);
      else if (event.key === 'Escape') setFocus('');
      else return;
      event.preventDefault();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [focus, stepFocus]);

  // ── Handlers ───────────────────────────────────────────────────────────
  const addCard = useCallback(
    (grid: RetroGrids, text: string) => {
      if (!text.trim()) return;
      setTypingGrid('');
      void actions.addCard(grid, text, name);
    },
    [actions, name]
  );

  const react = useCallback(
    async (cardId: string, emoji: string) => {
      const nowMine = await actions.react(cardId, emoji);
      setMyReactions((current) => {
        const next = new Map(current);
        const set = new Set(current.get(cardId) ?? []);
        if (nowMine) set.add(emoji);
        else set.delete(emoji);
        next.set(cardId, set);
        return next;
      });
    },
    [actions]
  );

  const saveProfile = useCallback(
    ({ name: newName, avatar: newAvatar }: { name: string; avatar: string }) => {
      setName(newName);
      setAvatar(newAvatar);
      write('local', KEY.name, newName);
      write('local', KEY.avatar, newAvatar);
      setProfileOpen(false);
    },
    []
  );

  // ── The gate ───────────────────────────────────────────────────────────
  if (!session.token) {
    // No `onJoined`: the default navigates to `/?token=…`, and the reload is
    // wanted here. The board then boots down exactly the path a host's link
    // takes, rather than a second one that only code-gate visitors exercise —
    // and the accepted code leaves the JS heap with the document.
    return (
      // The static-share gate names its mode too now, so this is no longer the
      // exception it once was — the props below just say it in the board's own
      // words. `sharing/gate.py` still owns the rule about what a gate withholds.
      <JoinGate
        wordmark="retro"
        eyebrow="Sprint retrospective"
        // From the island, not spelled again: `web.brand.frame_title` is the
        // one place this format lives, and a second copy here is how the frame
        // bar ends up reading differently on the gate than on the board behind
        // it.
        frameTitle={boot.chrome.frame}
        footer={boot.chrome.footer}
        heading="Join the retro"
        blurb="Enter the share code from the host's screen."
        cta="Join"
      />
    );
  }

  const cardCount = cards.length;

  const toolbar = (
    <Toolbar
      // No `brand`: the masthead above sets the wordmark, in the six-row face
      // where there is room for it. Two wordmarks sixty pixels apart is not
      // consistency, it is duplication.
      //
      // The duck rides in the toolbar, where it is in peripheral vision the
      // whole ceremony without ever being in the way.
      mark={<Duck state={duckState} size={30} />}
        subtitle={
          <>
            {boot.sprint ? `${boot.sprint} · ` : ''}
            {cardCount} {cardCount === 1 ? 'card' : 'cards'}
            {status === 'retrying' ? <span className={styles['offline']}> · reconnecting…</span> : null}
          </>
        }
    >
        <div className={styles['identity']}>
          <button type="button" className={styles['meChip']} onClick={() => setProfileOpen(true)}>
            <span aria-hidden="true">{avatar}</span>
            <span className={styles['meName']}>{name || 'Set your name'}</span>
            <span className={styles['pen']}>
              <Icon name="pencil" size={12} />
            </span>
          </button>

          {/* One roster, not two. The Room's count is the headline and its
              hover deals the room out; a row of the same faces beside it said
              the same thing twice and only fitted four of them. */}
          <Room people={presence} meName={name} />
        </div>
    </Toolbar>
  );

  return (
    <PageShell
      chrome={boot.chrome}
      variant="app"
      bar={toolbar}
      className={cx(kit['board'], styles['app'])}
      dock={
        <>
            <Visualizer playing={music.playing} />

            <Popover
              trigger={<Icon name="user" size={16} />}
              label="Walk through one person at a time"
              triggerClassName={cx(focus && styles['toolOn'])}
            >
              <FocusControls
                authors={authors}
                current={focus}
                avatars={avatarsByName}
                onStep={stepFocus}
                onStart={() => setFocus(authors[0] ?? '')}
                onExit={() => setFocus('')}
              />
            </Popover>

            {isHost ? (
              <IconButton
                icon={<Icon name={locked ? 'lock' : 'lock-open'} size={16} />}
                label={locked ? 'Unlock the board' : 'Lock the board'}
                active={locked}
                onClick={() => void actions.setLocked(!locked)}
              />
            ) : null}

            <Popover trigger={<Icon name="music" size={16} />} label="Music">
              <MusicPlayer
                music={music}
                channels={boot.musicChannels}
                footer={
                  isHost ? (
                    <Button onClick={() => void actions.castMusic(music.playing, music.channel)}>
                      <Icon name="megaphone" /> Play for everyone
                    </Button>
                  ) : null
                }
              />
            </Popover>

            <Popover
              trigger={
                <>
                  <Icon name="timer" size={16} />
                  <TimerReadout remaining={remaining} />
                </>
              }
              label="Timer"
            >
              {isHost ? (
                <TimerControls
                  running={Boolean(snapshot?.timer.running)}
                  onStart={(seconds) => void actions.startTimer(seconds)}
                  onStop={() => void actions.stopTimer()}
                />
              ) : (
                <p className={styles['popNote']}>The host controls the timer.</p>
              )}
            </Popover>

            <Popover trigger={<Icon name="contrast" size={16} />} label="Theme">
              <ThemeSwitcher
                value={theme}
                onChange={chooseTheme}
                footer={
                  isHost ? (
                    <Button onClick={() => void actions.castTheme(theme)}>
                      <Icon name="megaphone" /> Apply to everyone
                    </Button>
                  ) : null
                }
              />
            </Popover>

            <IconButton
              icon={<Icon name="mail" size={16} />}
              label="Invite the team"
              tone="primary"
              compact
              onClick={() => setInviteOpen(true)}
            >
              Invite
            </IconButton>
        </>
      }
    >
      {/* One flex column inside the shell's scroll row. The banners, the
          carried strip and the focus bar are auto-height siblings above the
          board, which takes the rest — the same relationship they had when
          `.app` itself was the flex column. */}
      <div className={styles['boardRegion']}>
      {/* No banner for the lock. The notch's padlock is lit, every composer is
          gone and every control is disabled — a stripe across the board says
          the same thing a fourth time, and costs a row to say it. It is still
          announced, once, to a screen reader. */}
      <p className={styles['srOnly']} role="status" aria-live="polite">
        {locked ? 'The host locked the board.' : ''}
      </p>

      {musicBlocked ? (
        <button
          type="button"
          className={styles['musicBanner']}
          onClick={() => void music.play().then(() => setMusicBlocked(false)).catch(() => {})}
        >
          <Icon name="play" size={14} /> The host started music — tap to listen
        </button>
      ) : null}

      <CarriedStrip
        items={carried}
        locked={locked}
        onSetStatus={(itemId, status_) => void actions.setCarriedStatus(itemId, status_ as CarriedStatuses)}
      />


      <Board
        cards={cards}
        avatars={avatarsByName}
        myReactions={myReactions}
        typing={typingByGrid}
        locked={locked}
        focus={focus}
        arrivals={arrivals}
        onAddCard={addCard}
        onTyping={onTyping}
        onEdit={(cardId, text) => void actions.editCard(cardId, text)}
        onDelete={(cardId) => void actions.deleteCard(cardId)}
        onReact={(cardId, emoji) => void react(cardId, emoji)}
        onMove={(cardId, grid, index) => void actions.moveCard(cardId, grid, index)}
      />
      </div>

      {/* Overlays and modals are fixed-position, so they take no part in the
          layout above and can sit anywhere in the tree. */}
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

      <Modal open={inviteOpen} onClose={() => setInviteOpen(false)} title="Invite the team">
        <p className={styles['popNote']}>
          Send the invite link — it carries the share code, so they land straight on the board.
          Scanning the QR does the same. Keep both off anywhere public.
        </p>
        <Toast message={invite.notice} onDismiss={invite.dismiss} />
        <InviteQR
          qrSrc={apiUrl(session, '/api/qr')}
          inviteUrl={invite.invite?.inviteUrl}
          joinCode={invite.invite?.joinCode}
        />
      </Modal>
    </PageShell>
  );
}
