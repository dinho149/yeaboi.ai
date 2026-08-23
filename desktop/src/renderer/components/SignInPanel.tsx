// The subscription sign-in bubble: drives `claude setup-token` on the backend
// a poll at a time. The token never reaches this renderer — the backend
// persists it and only says that it did.

import { useEffect, useRef, useState } from 'react';
import { type SignInStatus, signInCancel, signInCode, signInStart, signInStatus } from '../settings';

const POLL_MS = 1000;

export function SignInPanel({ onClose }: { onClose: (saved: boolean, message: string) => void }) {
  const [status, setStatus] = useState<SignInStatus | null>(null);
  const [startError, setStartError] = useState('');
  const [code, setCode] = useState('');
  const [copied, setCopied] = useState(false);
  const closed = useRef(false);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    signInStart().then(
      (result) => {
        if (!result.started) {
          setStartError(result.message || 'Sign-in could not start');
          return;
        }
        timer = setInterval(() => {
          signInStatus().then(
            (s) => {
              if (closed.current) return;
              setStatus(s);
              if (s.done && timer) clearInterval(timer);
            },
            () => undefined,
          );
        }, POLL_MS);
      },
      (e: Error) => setStartError(e.message),
    );
    return () => {
      closed.current = true;
      if (timer) clearInterval(timer);
    };
  }, []);

  const cancel = () => {
    void signInCancel();
    onClose(false, 'Sign-in cancelled');
  };

  if (startError) {
    return (
      <div class="signin-panel">
        <p class="signin-error">{startError}</p>
        <button onClick={() => onClose(false, startError)}>Close</button>
      </div>
    );
  }

  if (status?.done) {
    return (
      <div class="signin-panel">
        <p class={status.ok ? 'signin-ok' : 'signin-error'}>{status.message}</p>
        <button onClick={() => onClose(Boolean(status.saved), status.message ?? '')}>Done</button>
      </div>
    );
  }

  return (
    <div class="signin-panel">
      <p>
        Sign in with your Claude subscription. A browser window opens; approve the request and paste the code back here
        if asked.
      </p>
      {status?.url ? (
        <p class="signin-url">
          <a href={status.url} target="_blank" rel="noreferrer">
            {status.url}
          </a>{' '}
          <button
            onClick={() => {
              void navigator.clipboard.writeText(status.url ?? '').then(() => setCopied(true));
            }}
          >
            {copied ? 'copied' : 'copy'}
          </button>
        </p>
      ) : (
        <p class="signin-waiting">starting…</p>
      )}
      {status?.awaiting_code && (
        <form
          class="signin-code"
          onSubmit={(event) => {
            event.preventDefault();
            if (code.trim()) {
              void signInCode(code.trim());
              setCode('');
            }
          }}
        >
          <input
            value={code}
            placeholder="paste the authorization code"
            onInput={(event) => setCode((event.target as HTMLInputElement).value)}
          />
          <button type="submit">Submit</button>
        </form>
      )}
      <button onClick={cancel}>Cancel</button>
    </div>
  );
}
