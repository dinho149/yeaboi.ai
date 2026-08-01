/**
 * The same report, with a session behind it.
 *
 * Renders the *same* `Shell` and `Report` a file export does — the read view is
 * literally the same components — wrapped in a provider that turns the three
 * editing primitives on. That is the whole reason the edit stack lives inside
 * the `export` bundle instead of a second one: a document and its downloaded
 * copy cannot disagree if only one set of components draws them.
 */

import { useCallback, useMemo, useState } from 'react';

import type { Theme } from '../runtime/theme';

import { useBoardStream } from '../hooks/useBoardStream';
import { loadSession, stripCredentialsFromUrl } from '../runtime/api';
import { participantId, read, write } from '../runtime/storage';
import { createBoardStore } from '../store/boardStore';
import { useBoardSnapshot } from '../store/useBoard';
import { Report } from './Report';
import { Shell } from './Shell';
import { createEditActions } from './actions';
import type { EditBoot, ExportChrome, ExportReport } from './boot';
import { EditProvider, indexByPath, type Editing } from './editing/EditContext';
import { History } from './editing/History';
import type { EditDocState } from './editing/state';
import { useEditPresence } from './editing/useEditPresence';

const NAME_KEY = 'yeaboi_editor_name';
const AVATAR_KEY = 'yeaboi_editor_avatar';

export interface EditAppProps {
  chrome: ExportChrome;
  report: ExportReport;
  editing: EditBoot;
  theme: Theme;
}

export function EditApp({ chrome: bootChrome, report: bootReport, editing: boot, theme }: EditAppProps) {
  const pid = useMemo(() => participantId(), []);
  const session = useMemo(() => {
    const loaded = loadSession('doc', pid);
    stripCredentialsFromUrl();
    return loaded;
  }, [pid]);

  // Seeded from the boot payload so the first paint is the document, not a
  // spinner — an export that flashed empty before its own content arrived would
  // be a regression against the file it replaces.
  const store = useMemo(
    () =>
      createBoardStore<EditDocState>({
        revision: boot.revision,
        editable: boot.editable,
        chrome: bootChrome,
        report: bootReport,
        edits: boot.edits,
        people: boot.people,
      }),
    [boot, bootChrome, bootReport]
  );

  const snapshot = useBoardSnapshot(store);
  const state = snapshot ?? {
    revision: boot.revision,
    editable: boot.editable,
    chrome: bootChrome,
    report: bootReport,
    edits: boot.edits,
    people: boot.people,
  };

  const [name, setName] = useState(() => read('local', NAME_KEY) ?? '');
  const [avatar, setAvatar] = useState(() => read('local', AVATAR_KEY) ?? '🙂');
  const [focused, setFocused] = useState('');
  const [historyPath, setHistoryPath] = useState<string | null>(null);

  const identity = useCallback(() => ({ name, avatar }), [name, avatar]);
  const revision = useCallback(() => state.revision, [state.revision]);
  const actions = useMemo(
    () => createEditActions(session, store, identity, revision),
    [session, store, identity, revision]
  );

  useBoardStream({ session, store, enabled: Boolean(session.token) });
  useEditPresence(actions, { name, avatar }, focused, Boolean(session.token && name));

  const editing: Editing = useMemo(
    () => ({
      // A reader who has not said who they are can look but not write. Not a
      // security control — it is client-side and the server takes any name —
      // but an unattributed correction helps nobody read the history later.
      enabled: state.editable && Boolean(name),
      actions,
      me: { name, avatar },
      byPath: indexByPath(state.edits),
      revision: state.revision,
      showHistory: setHistoryPath,
      othersEditing: (path: string) =>
        state.people.filter((person) => person.pid !== pid && person.editing === path).map((person) => person.name),
    }),
    [state, actions, name, avatar, pid]
  );

  return (
    <EditProvider value={editing}>
      <Shell chrome={state.chrome} theme={theme}>
        <Report report={state.report} />
        <History
          rows={state.edits}
          people={state.people}
          filter={historyPath}
          onFilter={setHistoryPath}
          name={name}
          avatar={avatar}
          editable={state.editable}
          onIdentity={(nextName, nextAvatar) => {
            setName(nextName);
            setAvatar(nextAvatar);
            write('local', NAME_KEY, nextName);
            write('local', AVATAR_KEY, nextAvatar);
          }}
          onRevert={(id) => void actions.revert(id)}
          onFocusPath={setFocused}
        />
      </Shell>
    </EditProvider>
  );
}
