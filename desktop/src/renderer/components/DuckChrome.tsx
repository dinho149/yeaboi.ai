// The duck in the sidebar, and the bubble beside him.
//
// One duck for the whole window, exactly as the terminal has one in the corner
// of every page. Pages do not draw him and do not write to the bubble; they
// offer a line to the shared voice (`duckVoice().say(...)`) and the arbiter
// decides whether it wins. Direct writes are how the terminal's bubble ended up
// fighting itself, and the ladder is the fix that was kept.

import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { duckVoice } from '../ambience';

/** Frame cadence for the bubble. The line changes on human timescales, so this
 *  is a slow tick, not an animation loop. */
const TICK_MS = 200;

export interface DuckChromeProps {
  /** Muted — the bubble stays shut. The duck himself always stays. */
  muted: boolean;
  /** Music is playing; he dances to it. */
  jamming: boolean;
  /** Backend down: he sleeps rather than pretending to know anything. */
  offline: boolean;
  /** A click on a duck holding a question opens the page that answers it. */
  onAnswer?: () => void;
}

export function DuckChrome({ muted, jamming, offline, onAnswer }: DuckChromeProps) {
  const voice = duckVoice();
  const [line, setLine] = useState('');
  const [sticky, setSticky] = useState(false);

  useEffect(() => {
    voice.mute(muted);
  }, [muted, voice]);

  useEffect(() => {
    const timer = setInterval(() => {
      const current = voice.tick();
      setLine(current?.text ?? '');
      setSticky(voice.sticky);
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [voice]);

  const answerable = sticky && onAnswer !== undefined;

  return (
    <div class="duck-chrome">
      {line && (
        <button
          type="button"
          class={sticky ? 'duck-bubble sticky' : 'duck-bubble'}
          // A fading quip is decoration; a sticky line is a question, and the
          // only one of the two worth handing to a screen reader.
          aria-live={sticky ? 'polite' : 'off'}
          disabled={!answerable}
          onClick={() => {
            if (!answerable) return;
            voice.clearSticky();
            setLine('');
            onAnswer?.();
          }}
        >
          {line}
        </button>
      )}
      <Duck state={offline ? 'offline' : 'idle'} jamming={jamming} size={36} />
    </div>
  );
}
