// Roadmap intake — point yeaboi at the quarterly roadmap, pick a project, plan it.
//
// A Planning sub-page, like the terminal's intake card: Plan This hands the
// chosen project's description to the chat rather than opening a mode of its own.

import { Card, Lozenge, NoticeBlock } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { maskText } from '../boards';
import { createChat } from '../chat';
import {
  type ModeRunState,
  type RoadmapAnalysisView,
  type RoadmapSourceOption,
  type SavedRoadmap,
  analyzeRoadmap,
  emptyModeRun,
  loadRoadmapOptions,
  loadSavedRoadmaps,
  planProject,
  reduceModeRun,
} from '../modes';
import { ResultActions } from '../components/ResultActions';

export function Roadmap() {
  const [sources, setSources] = useState<RoadmapSourceOption[] | null>(null);
  const [saved, setSaved] = useState<SavedRoadmap[]>([]);
  const [kind, setKind] = useState('confluence');
  const [locator, setLocator] = useState('');
  const [analysis, setAnalysis] = useState<RoadmapAnalysisView | null>(null);
  const [roadmapId, setRoadmapId] = useState(0);
  const [run, setRun] = useState<ModeRunState>(emptyModeRun());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [mask, setMask] = useState<[string, string][]>([]);
  const [anonNote, setAnonNote] = useState('');

  useEffect(() => {
    loadRoadmapOptions().then(
      (body) => {
        setSources(body.sources);
        setKind(body.sources[0]?.key ?? 'confluence');
      },
      (e: Error) => setError(e.message),
    );
    loadSavedRoadmaps().then((body) => setSaved(body.roadmaps), () => undefined);
  }, []);

  if (error && !sources) return <NoticeBlock title="Could not open roadmap intake" items={[error]} />;
  if (!sources) return <p>Loading…</p>;

  const chosen = sources.find((source) => source.key === kind) ?? sources[0]!;

  async function analyze(reuseId = roadmapId) {
    if (busy || !locator.trim()) return;
    setBusy(true);
    setError('');
    let state = emptyModeRun();
    setRun(state);
    try {
      await analyzeRoadmap({ source_type: kind, locator: locator.trim(), roadmap_id: reuseId }, (line) => {
        state = reduceModeRun(state, line);
        setRun(state);
      });
      if (state.done) {
        setAnalysis((state.done.analysis as RoadmapAnalysisView) ?? null);
        setRoadmapId(Number(state.done.roadmap_id ?? 0));
      }
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  async function plan(index: number) {
    try {
      // The backend decides both halves: the description a project plans from,
      // and whether it is large enough for the full intake.
      const picked = await planProject(roadmapId, index);
      const view = await createChat(picked.description, picked.intake_mode);
      window.location.hash = `#/humans/planning/chat?id=${encodeURIComponent(view.project_id)}`;
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Roadmap intake</h1>
          <p class="dash-sub">Point yeaboi at the quarterly roadmap and it proposes what to plan next.</p>
        </div>
        <div class="dash-actions">
          <a class="button" href="#/humans/planning">
            Back
          </a>
        </div>
      </header>

      {anonNote && <NoticeBlock title={anonNote} items={['Review before sharing.']} />}
      {error && <NoticeBlock title="That did not work" items={[error]} />}
      {run.error && <NoticeBlock title="The analysis failed" items={[run.error]} />}

      <Card title="Where does the roadmap live?">
        <div class="chip-row">
          {sources.map((source) => (
            <label key={source.key} class="check-row">
              <input type="radio" name="source" checked={kind === source.key} onChange={() => setKind(source.key)} />
              <span>
                <strong>{source.label}</strong>
                <span class="dash-note">{source.hint}</span>
              </span>
            </label>
          ))}
        </div>
        <div class="field-row">
          <label for="roadmap-locator">{chosen.prompt}</label>
          <input
            id="roadmap-locator"
            type="text"
            value={locator}
            onInput={(e) => setLocator((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="dash-actions">
          <button type="button" class="primary" disabled={busy || !locator.trim()} onClick={() => void analyze()}>
            {busy ? 'Analyzing…' : analysis ? 'Re-analyze' : 'Analyze'}
          </button>
        </div>
      </Card>

      {run.phases.length > 0 && !analysis && (
        <Card title="Progress">
          <ol class="phase-list">
            {run.phases.map((phase, i) => (
              <li key={`${phase}-${i}`}>{phase}</li>
            ))}
          </ol>
        </Card>
      )}

      {analysis && (
        <>
          <Card title={maskText(analysis.source_label || 'Roadmap', mask)}>
            <p>{maskText(analysis.summary, mask)}</p>
            <ResultActions
              refer={{ kind: 'roadmap', session_id: '', run_id: roadmapId }}
              mode="roadmap"
              anonNote={anonNote}
              onAnonymize={(replacements, note) => {
                setMask(replacements);
                setAnonNote(note);
              }}
            />
          </Card>
          {analysis.warnings?.length > 0 && <NoticeBlock title="Notices" items={analysis.warnings} />}
          <div class="profile-list">
            {analysis.projects.map((project, index) => (
              <Card key={`${project.name}-${index}`} title={maskText(project.name, mask)}>
                <p>
                  <Lozenge category={project.size === 'large' ? 'inprogress' : 'todo'}>{project.size}</Lozenge>{' '}
                  {maskText(project.description, mask)}
                </p>
                <div class="dash-actions">
                  <button type="button" class="primary" onClick={() => void plan(index)}>
                    Plan this
                  </button>
                </div>
              </Card>
            ))}
          </div>
          {analysis.projects.length === 0 && (
            <Card title="Nothing to plan">
              <p>
                <Duck state="idle" size={28} /> No concrete projects came out of that document — check its content,
                or try another source.
              </p>
            </Card>
          )}
        </>
      )}

      {!analysis && saved.length > 0 && (
        <Card title="Saved roadmaps">
          <ul class="review-list">
            {saved.map((row) => (
              <li key={row.id}>
                <strong>{row.label}</strong> · {row.project_count} project(s) · {String(row.analyzed_at).slice(0, 10)}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
