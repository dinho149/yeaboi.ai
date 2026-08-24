// What `?` shows, and what `/help` opens from the chat.
//
// The chat's commands are listed here too rather than in a second sheet: a
// person asking "what can I do" does not know yet whether the answer is a key
// or a slash verb.

import { CHAT_COMMANDS } from '../commands';
import { shortcuts } from '../palette';

export function ShortcutsSheet({ platform, onClose }: { platform: string; onClose: () => void }) {
  return (
    <div class="scrim" onClick={onClose}>
      <div
        class="modal shortcuts"
        role="dialog"
        aria-modal="true"
        aria-label="Shortcuts"
        onClick={(event) => event.stopPropagation()}
      >
        <header class="modal-head">
          <h2>Shortcuts</h2>
          <button type="button" class="link" onClick={onClose}>
            Close
          </button>
        </header>
        <dl class="shortcut-list">
          {shortcuts(platform).map((row) => (
            <div key={row.keys}>
              <dt>{row.keys}</dt>
              <dd>{row.what}</dd>
            </div>
          ))}
        </dl>
        <h3 class="shortcut-heading">In the planning chat</h3>
        <dl class="shortcut-list">
          {CHAT_COMMANDS.map((command) => (
            <div key={command.name}>
              <dt>/{command.name}</dt>
              <dd>{command.help}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
