/**
 * An artifact, drawn by the same component the static exports use.
 *
 * This is the point of storing a *payload* rather than rendered HTML. The
 * `Report` switch in `export/Report.tsx` already knows all ten report kinds and
 * carries the `never` guard that makes an unrenderable kind a compile error.
 * Reimplementing any of it here would mean two renderers drifting apart, and
 * the drift would surface as an app page that disagrees with the file a user
 * downloaded of the same run.
 *
 * The export's page chrome is deliberately *not* reused: a masthead, wordmark
 * and theme switcher are the furniture of a document that has to stand alone on
 * someone's disk. Inside the app the shell already provides all of that, so the
 * report renders bare into the content region.
 */

import type { ExportReport } from '../export/boot';
import { Report } from '../export/Report';
import { get } from './api';
import { AsyncView, EmptyState } from './Slots';
import { useAsync } from './useAsync';

interface ArtifactResponse {
  id: string;
  kind: string;
  title: string;
  created_at: number;
  project_id: string;
  payload: ExportReport;
}

export function ArtifactView({ id }: { id: string }) {
  const state = useAsync(() => get<ArtifactResponse>(`/api/artifacts/${id}`), [id]);
  return (
    <AsyncView state={state} empty={<EmptyState title="Nothing here" />}>
      {(artifact) => <Report report={artifact.payload} />}
    </AsyncView>
  );
}
