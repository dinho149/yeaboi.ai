/**
 * Live rooms: the retro and poker boards, which run somewhere else.
 *
 * The app lists them and hands over. It does not embed or proxy them — see
 * `docs/app-plan.md` for why that is a product decision rather than a route,
 * and why a registry is the step all three options need first.
 *
 * Every link goes through `safeUrl` even though the server already refuses a
 * non-http(s) scheme on write. Two checks rather than one because they guard
 * different moments: the server guards what enters the database, this guards
 * what a browser is told to open, and rows predating the server's check would
 * otherwise walk straight past it.
 */

import { Button } from '../design/primitives';
import { safeUrl } from '../runtime/url';
import { del, get } from './api';
import { AsyncView, EmptyState } from './Slots';
import { useAsync } from './useAsync';
import type { Room } from './types';
import styles from './app.module.css';

export function RoomList({ projectId, notify }: { projectId: string; notify: (message: string) => void }) {
  const state = useAsync(
    () => get<{ rooms: Room[] }>(`/api/projects/${projectId}/rooms`),
    [projectId],
    { isEmpty: (data) => data.rooms.length === 0 },
  );

  async function close(room: Room) {
    const result = await del(`/api/rooms/${room.id}`);
    notify(result.ok ? 'Room closed' : result.error);
  }

  return (
    <AsyncView
      state={state}
      empty={
        <EmptyState
          title="No live rooms"
          hint="Start a retro or a planning-poker round from the terminal, and it appears here for the team to join."
        />
      }
    >
      {(data) => (
        <ul className={styles.projectList}>
          {data.rooms.map((room) => {
            const href = safeUrl(room.invite_url);
            return (
              <li key={room.id} className={styles.projectRow}>
                {href ? (
                  <a
                    className={styles.projectName}
                    href={href}
                    // A board is a different origin behind a tunnel; without
                    // noreferrer the target learns this app's URL.
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {room.title || room.kind}
                  </a>
                ) : (
                  <span className={styles.projectName}>{room.title || room.kind}</span>
                )}
                <span className={styles.role}>{room.join_code || room.kind}</span>
                <Button shape="bare" size="s" onClick={() => close(room)}>
                  Close
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </AsyncView>
  );
}
