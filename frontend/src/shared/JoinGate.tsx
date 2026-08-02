/**
 * The access-code gate: swap an ``XXXX-XXXX`` code for a session token.
 *
 * Shared deliberately. The static-output share server (``sharing/server.py``),
 * the retro board and the poker board all put the same gate in front of the
 * same ``POST /api/join`` contract, and today each one carries its own copy.
 * Everything mode-specific arrives as a prop.
 *
 * Nothing here is a security boundary — it is a keypad in front of one. Codes
 * are checked server-side with ``compare_digest`` and failures are throttled
 * per-IP by ``sharing.access.JoinLimiter``; this component just collects eight
 * characters and reports what the server said.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { Duck, Eyebrow, TerminalFrame, useDuckPulse, Wordmark } from '../design/primitives';
import { stripCredentialsFromUrl } from '../runtime/api';
import { cx } from '../runtime/cx';
import { read, write } from '../runtime/storage';
import { Button } from './Button';
import { Credit } from './Credit';
import styles from './JoinGate.module.css';

/** Codes are 8 characters from an unambiguous alphabet — see access.py. */
const CODE_LENGTH = 8;

/**
 * The invite code this browser has already had refused, so a re-click is free.
 *
 * `local`, not `session`, and that is the whole point of it. Clicking a link in
 * Slack or in mail opens a *fresh tab*, which is precisely the case a per-tab
 * memo cannot see — six colleagues clicking one dead link once each would spend
 * six real `JoinLimiter` attempts, and eight behind one office NAT is a
 * five-minute lockout that then rejects the *working* link the host sends to fix
 * it. `localStorage` is shared across tabs, so the second click is the one that
 * costs nothing.
 *
 * Outliving the tab is safe here in a way it would not be for a credential: the
 * value is a code the server has already answered 403 to, and a host who
 * restarts issues a different one — so this can only ever suppress the exact
 * string that already failed. It is a negative cache, not a secret, which is why
 * it sits in the "who you are" store despite being neither (see `storage.ts`).
 */
const DEAD_CODE_KEY = 'yeaboi_dead_invite';

export interface JoinGateProps {
  /** Endpoint that exchanges a code for a token. */
  endpoint?: string;
  /**
   * Word set in the six-row display face — the hero of this page.
   *
   * Every consumer names its mode here now. The static share gate used to
   * default to the brand, on the rule that an unauthenticated visitor must
   * learn *nothing* about what was behind the door; the rule is narrower today.
   * One word from a fixed vocabulary is a fair trade for a teammate knowing
   * what they are being asked to join, and it tells a stranger nothing the
   * host's message did not. `sharing/gate.py` lists what is still withheld —
   * the title, the host, the sprint, the contents, the code — and names the one
   * mode (`performance`) that stays anonymous because its word *is* the
   * disclosure.
   */
  wordmark?: string;
  /** Small mono label above the heading. */
  eyebrow?: string;
  /** Title-bar text for the terminal frame. */
  frameTitle?: string;
  heading?: string;
  /** One line under the heading explaining what the visitor is joining. */
  blurb?: string;
  /** Submit-button label. Keeps its name through the whole flow. */
  cta?: string;
  /**
   * Credit line under the card. Matches the footer every other surface wears.
   *
   * Rendered *outside* the `<main>` the frame wraps: a `contentinfo` landmark
   * nested inside `main` is an axe violation, and `shared/a11y.test.tsx` runs
   * axe over this component bare.
   */
  footer?: string;
  /**
   * Called with the token once the server accepts the code. Defaults to
   * reloading at ``/?token=…`` — a full navigation, so the browser drops the
   * gate document entirely rather than leaving the code in a live JS heap.
   */
  onJoined?: (token: string) => void;
}

type Phase = 'idle' | 'checking' | 'error';

/**
 * Keep only characters a code can contain, uppercase, and re-insert the dash.
 *
 * Runs on every keystroke *and* on paste, which is the case that matters: the
 * host reads the code out over a call, or sends it in a chat message that gets
 * pasted with surrounding whitespace, a lowercase spelling, or no dash at all.
 */
export function normalizeCode(raw: string): string {
  const body = raw.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, CODE_LENGTH);
  return body.length > 4 ? `${body.slice(0, 4)}-${body.slice(4)}` : body;
}

/**
 * The eight code characters, dash removed — for length checks only.
 *
 * NOT what goes on the wire. ``make_join_code`` issues ``XXXX-XXXX`` and all
 * three servers compare against that string with ``compare_digest``, so the
 * dash is part of the code, not decoration. Sending the stripped form made
 * every join fail with 403.
 */
const digits = (value: string): string => value.replace('-', '');

/**
 * The code carried by the invite link, or `''`.
 *
 * `sharing.access.invite_url` emits `https://host/#code=XXXX-XXXX` and only ever
 * that. The code rides in the **fragment** because a fragment is never sent to
 * the origin: it stays out of cloudflared's access log, out of the server's
 * request line, and out of the `Referer` when the visitor clicks the credit link
 * below.
 *
 * `?code=` is accepted here and emitted nowhere. It rescues a link mangled in
 * transit by a client that relocates or drops fragments (Outlook Safe Links,
 * some shorteners). Do not "simplify" by generating it: by the time this
 * function runs, a query code is already in the tunnel's log, so the two forms
 * are equivalent for the reader and are not equivalent for the host.
 *
 * `URLSearchParams` handles the percent-decoding and `normalizeCode` absorbs the
 * rest, so a lowercase, dashless, or partially-escaped code all land on the same
 * eight characters. Wrapped: `location` is well-defined everywhere this runs,
 * but a malformed hash must not take the gate down with it.
 */
export function readInviteCode(): string {
  try {
    const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
    const query = new URLSearchParams(location.search);
    return normalizeCode(hash.get('code') ?? query.get('code') ?? '');
  } catch {
    return '';
  }
}

/** True when this browser already holds a token — the auto-submit must not run. */
function hasToken(): boolean {
  try {
    return Boolean(new URLSearchParams(location.search).get('token'));
  } catch {
    return false;
  }
}

/**
 * Turn a response into something worth reading.
 *
 * The distinction that earns its keep is 429 vs 403: after eight wrong
 * attempts the limiter locks the IP out for five minutes, and a visitor told
 * only "that code did not match" will retype the *correct* code repeatedly and
 * conclude the host sent them a broken link.
 */
function messageFor(status: number, source: Source): string {
  if (status === 429) return 'Too many attempts. Wait a few minutes, then try again.';
  if (status === 403) {
    // Split by source, because the same 403 means two different things. Typed by
    // hand it is a typo; carried by a link it means the host restarted the board
    // and the reader has nothing to fix by trying again.
    return source === 'auto'
      ? 'That invite link is out of date — the host restarted the board, so the code in it no longer works. Ask them for a new link, or type the current code below.'
      : 'That code did not match. Check it with the host.';
  }
  return `Something went wrong (HTTP ${status}). Ask the host to re-share.`;
}

/** Where a submission came from. Only affects messaging and the dead-code memo. */
type Source = 'auto' | 'manual';

export function JoinGate({
  endpoint = '/api/join',
  wordmark = 'yeaboi',
  eyebrow = 'Shared from a terminal',
  frameTitle = 'yeaboi',
  heading = 'Someone shared this with you',
  blurb = 'Enter the access code they gave you. The link stops working when they close the share.',
  cta = 'Open',
  footer = 'Generated by yeaboi.ai',
  onJoined,
}: JoinGateProps) {
  const [code, setCode] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [message, setMessage] = useState('');
  const [duck, startle] = useDuckPulse('idle');
  const inputRef = useRef<HTMLInputElement | null>(null);

  // useId, not a hardcoded string: the boards will mount this alongside their
  // own markup, and duplicate ids would break the label/input association.
  const inputId = useId();
  const statusId = useId();

  const complete = digits(code).length === CODE_LENGTH;
  const busy = phase === 'checking';

  // The in-flight flag lives in a ref as well as in `phase`, because `attempt`
  // must not close over state that changes on every keystroke: a `useCallback`
  // whose deps churn would re-create it, and the mount effect below — which
  // depends on it — would fire the auto-submit again.
  const busyRef = useRef(false);

  const attempt = useCallback(
    async (value: string, source: Source) => {
      if (busyRef.current || digits(value).length !== CODE_LENGTH) return;

      busyRef.current = true;
      setPhase('checking');
      setMessage('Checking…');
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          // `value` is already normalized to XXXX-XXXX, which is exactly the
          // string the server holds. See the note on `digits` above.
          body: JSON.stringify({ code: value }),
        });
        if (!response.ok) {
          if (source === 'auto' && response.status === 403) {
            // Remember this exact code as dead, so re-clicking the same link in
            // the chat message it came from costs no request at all. The address
            // bar was already cleaned before this fetch, which covers a reload —
            // but a fresh navigation brings the fragment back, and that (a new
            // tab, opened from a chat client) is the path a stale link is
            // actually used on. Only 403: a 429 or a transport failure is
            // transient, and remembering one would strand a visitor whose code
            // is fine.
            write('local', DEAD_CODE_KEY, value);
          }
          setPhase('error');
          setMessage(messageFor(response.status, source));
          // The duck loses its sunglasses. This is the docs site's cursor-dodge
          // reaction reused on the one screen where something has actually gone
          // wrong — a rejected code is the most likely moment a visitor has with
          // this product, so it may as well be the one they remember. Not on the
          // auto path: the visitor did nothing, and startling them over a link
          // the host let go stale is blaming the wrong person.
          if (source === 'manual') startle('startled');
          // Select rather than clear: a mistyped character is far more likely
          // than a wholly wrong code, and retyping all eight is a tax.
          inputRef.current?.select();
          return;
        }
        const { token } = (await response.json()) as { token?: string };
        if (!token) throw new Error('no token');
        if (onJoined) onJoined(token);
        else location.replace(`/?token=${encodeURIComponent(token)}`);
      } catch {
        // fetch() rejects only on transport failure — the tunnel dropping, or
        // the host closing the share while someone is typing.
        setPhase('error');
        setMessage('Could not reach the host. They may have stopped sharing.');
        if (source === 'manual') startle('startled');
        inputRef.current?.select();
      } finally {
        busyRef.current = false;
      }
    },
    [endpoint, onJoined, startle],
  );

  const submit = useCallback(
    (event: Event) => {
      event.preventDefault();
      void attempt(code, 'manual');
    },
    [attempt, code],
  );

  // ── The invite link ────────────────────────────────────────────────────
  // A code in the link prefills the field and submits itself. The whole point
  // of `invite_url` is that one URL is the entire invite; stopping at a filled
  // box and a button to press would give that back.
  //
  // The order below is the guard against `JoinLimiter`, which locks an IP out
  // for five minutes after eight failures — and is per-IP, so a NATed office
  // sharing one dead link can reach that between them:
  //
  //   1. strip the credentials from the URL *before* the request, so a reload
  //      after any failure carries no code and asks nothing;
  //   2. a ref latch, so one mount can only ever fire once (independent of
  //      whether StrictMode double-invokes — this build aliases it away, and
  //      the latch must not depend on that);
  //   3. never with a token present;
  //   4. never a code this browser has already had refused — the one guard that
  //      covers a fresh tab, which is how a link in a chat client opens.
  const fired = useRef(false);
  useEffect(() => {
    if (fired.current) return;
    fired.current = true;

    const invited = readInviteCode();
    if (!invited) return;
    // Read before the strip removes it.
    const alreadyAuthed = hasToken();
    setCode(invited);
    // Unconditional from here: once the code is on screen the URL has done its
    // job, and every path that leaves it in the address bar — including the two
    // that ask nothing — leaves it in the next screenshot too.
    stripCredentialsFromUrl();
    if (alreadyAuthed) return;
    if (read('local', DEAD_CODE_KEY) === invited) {
      setPhase('error');
      setMessage(messageFor(403, 'auto'));
      return;
    }
    void attempt(invited, 'auto');
  }, [attempt]);

  return (
    <div className={styles.shell}>
      <TerminalFrame title={frameTitle} className={styles.card}>
        <main>
          <Wordmark text={wordmark} variant="shadow" className={styles.wordmark} />
          <Eyebrow className={styles.eyebrow}>{eyebrow}</Eyebrow>
          <h1 className={styles.heading}>{heading}</h1>
          <p className={styles.blurb}>{blurb}</p>

          <form onSubmit={submit} noValidate>
            <div className={styles.labelRow}>
              <label className={styles.label} htmlFor={inputId}>
                Access code
              </label>
              {/* Perched on the field, watching you type. Purely decorative until
                  the code is refused, which is the moment it earns its place. */}
              <Duck state={duck} size={44} className={styles.duck} />
            </div>
            <input
              ref={inputRef}
              id={inputId}
              className={cx(styles.code, phase === 'error' && styles.wrong)}
              value={code}
              onInput={(event) => {
                setCode(normalizeCode((event.target as HTMLInputElement).value));
                if (phase === 'error') {
                  setPhase('idle');
                  setMessage('');
                }
              }}
              // maxLength counts the dash. inputMode/autocapitalize matter on
              // phones, which is where most people open a tunnel link.
              maxLength={CODE_LENGTH + 1}
              placeholder="XXXX-XXXX"
              autoComplete="one-time-code"
              autoCapitalize="characters"
              autoCorrect="off"
              // Lowercase `spellcheck`: preact's JSX types accept React's camelCase
              // for every other attribute here, but not this one. Both work at
              // runtime — it is a typing gap, not a behaviour difference.
              spellcheck={false}
              aria-describedby={statusId}
              aria-invalid={phase === 'error'}
              // readOnly rather than disabled: disabling blurs the field, so the
              // caret would be gone by the time an error comes back.
              readOnly={busy}
              autoFocus
            />
            <Button
              tone="primary"
              size="l"
              block
              className={styles.go}
              type="submit"
              disabled={!complete || busy}
            >
              {busy ? 'Checking…' : cta}
            </Button>
            {/* Reserved height so the card does not jump when a message appears. */}
            <p id={statusId} className={cx(styles.status, phase === 'error' && styles.bad)} role="alert">
              {message}
            </p>
          </form>
        </main>
      </TerminalFrame>
      {/* Sibling of the frame, not a child of the <main> inside it: a
          contentinfo landmark nested in main is an axe violation, and this
          component is rendered bare in the a11y suite. */}
      <footer className={styles.foot}>
        {/* Opens in a new tab, which matters more here than anywhere else: the
            visitor is mid-way through typing a share code, and a same-tab
            navigation would throw the code away. */}
        <Credit>{footer}</Credit>
      </footer>
    </div>
  );
}
