// Cmd+K. Type a few letters, press Enter, land there.

import { useEffect, useMemo, useRef, useState } from 'react';
import { matchEntries, moveSelection, paletteEntries } from '../palette';

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const entries = useMemo(() => paletteEntries(), []);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const box = useRef<HTMLInputElement>(null);
  const matches = matchEntries(entries, query);

  useEffect(() => {
    box.current?.focus();
  }, []);

  function go(path: string): void {
    window.location.hash = path;
    onClose();
  }

  return (
    <div class="scrim" onClick={onClose}>
      <div
        class="modal palette"
        role="dialog"
        aria-modal="true"
        aria-label="Go to"
        onClick={(event) => event.stopPropagation()}
      >
        <input
          ref={box}
          class="palette-input"
          type="text"
          placeholder="Go to…"
          value={query}
          onInput={(event) => {
            setQuery((event.target as HTMLInputElement).value);
            setSelected(0);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') onClose();
            else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              setSelected((at) => moveSelection(at, event.key === 'ArrowDown' ? 1 : -1, matches.length));
            } else if (event.key === 'Enter') {
              const match = matches[selected];
              if (match) go(match.path);
            }
          }}
        />
        <ul class="palette-list">
          {matches.map((entry, index) => (
            <li key={entry.path}>
              <button
                type="button"
                class={index === selected ? 'palette-row selected' : 'palette-row'}
                onMouseEnter={() => setSelected(index)}
                onClick={() => go(entry.path)}
              >
                <span>{entry.title}</span>
                {entry.group && <span class="palette-group">{entry.group}</span>}
              </button>
            </li>
          ))}
        </ul>
        {matches.length === 0 && <p class="modal-foot">Nothing by that name.</p>}
      </div>
    </div>
  );
}
