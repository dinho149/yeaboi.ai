// New analysis — the setup wizard, then the run.
//
// Which steps apply is asked of the backend (POST /api/analysis/steps), not
// decided here: the terminal walks the same wizard, and a second copy of the
// rules is a second thing to drift.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import { quip } from '../ambience';
import {
  type AnalysisOptions,
  type RunLine,
  type RunRequest,
  type StepPlan,
  cancelRun,
  emptyRun,
  loadAnalysisOptions,
  loadRoster,
  planSteps,
  runAnalysis,
  reduceRun,
} from '../dashboards';

const STEP_TITLES: Record<string, string> = {
  features: 'What should I look at?',
  sources: 'Where should I read it from?',
  github_owners: 'Which GitHub owners?',
  azdo_projects: 'Which Azure DevOps projects?',
  depth: 'How deep?',
  model: 'Which model for the small jobs?',
  window: 'How far back?',
  members: 'Whose work?',
  review: 'Ready',
};

const COMPONENT_TITLES: Record<string, string> = {
  delivery: 'Delivery',
  code: 'Code',
  docs: 'Docs',
};

interface Answers {
  features: string[];
  components: Record<string, string[]>;
  github_owners: string[];
  azdo_projects: string[];
  depth: string;
  model: string | null;
  window_days: number;
  members: string[] | null;
  source: string;
}

export function AnalysisSetup() {
  const [options, setOptions] = useState<AnalysisOptions | null>(null);
  const [answers, setAnswers] = useState<Answers | null>(null);
  const [plan, setPlan] = useState<StepPlan | null>(null);
  const [index, setIndex] = useState(0);
  const [roster, setRoster] = useState<string[] | null>(null);
  const [run, setRun] = useState(emptyRun());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    loadAnalysisOptions().then(
      (opts) => {
        setOptions(opts);
        setAnswers({
          features: Object.keys(opts.features_available).filter((key) => opts.features_available[key]),
          components: { ...opts.grid },
          github_owners: [],
          azdo_projects: [],
          depth: opts.default_depth,
          model: null,
          window_days: opts.default_window_days,
          members: null,
          source: '',
        });
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  // The applicable steps change whenever a selection does, so the plan is
  // re-asked rather than patched.
  useEffect(() => {
    if (!answers || !options) return;
    planSteps({ ...answers, grid: options.grid, model_offered: false }).then(setPlan, (e: Error) =>
      setError(e.message),
    );
  }, [answers, options]);

  if (error && !options) return <NoticeBlock title="Could not open the setup" items={[error]} />;
  if (!options || !answers || !plan) return <p>Loading…</p>;

  const step = plan.steps[Math.min(index, plan.steps.length - 1)] ?? 'review';
  const set = (patch: Partial<Answers>) => setAnswers({ ...answers, ...patch });
  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  async function fetchRoster() {
    const trackers = answers!.components.delivery ?? [];
    const envelope = await loadRoster(trackers.length === 1 ? trackers[0]! : 'both');
    setRoster(envelope.ok ? envelope.data.members.map((m) => m.name) : []);
    if (!envelope.ok) setError(envelope.error?.message ?? 'team_roster failed');
  }

  async function start() {
    if (busy) return;
    setBusy(true);
    setError('');
    let state = emptyRun();
    setRun(state);
    try {
      await runAnalysis(plan!.run as RunRequest, (line: RunLine) => {
        state = reduceRun(state, line);
        setRun(state);
      });
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
    if (state.error) setError(state.error);
    else quip('analysis_done');
  }

  if (busy || run.finished) {
    return (
      <div class="dash">
        <h1 class="page-title">{run.finished ? 'Analysis finished' : 'Analysing…'}</h1>
        <Card title="Progress">
          <ol class="phase-list">
            {run.phases.map((phase, i) => (
              <li key={`${i}-${phase}`}>{phase}</li>
            ))}
            {!run.phases.length && <li>Starting…</li>}
          </ol>
        </Card>
        {run.cancelled && <NoticeBlock title="Cancelled" items={['Nothing was saved.']} />}
        {error && <NoticeBlock title="That run did not finish" items={[error]} />}
        <div class="dash-actions">
          {busy && run.opId && (
            <button type="button" onClick={() => void cancelRun(run.opId)}>
              Stop
            </button>
          )}
          {run.finished && !run.error && (
            <a class="button primary" href="#/humans/analysis">
              See the results
            </a>
          )}
          {run.finished && <a href="#/humans/analysis">Back to saved analyses</a>}
        </div>
      </div>
    );
  }

  return (
    <div class="dash">
      <h1 class="page-title">New analysis</h1>
      <div class="stage-rail">
        {plan.steps.map((key, i) => (
          <span key={key} class={i === index ? 'stage active' : 'stage'}>
            {STEP_TITLES[key] ?? key}
          </span>
        ))}
      </div>

      <Card title={STEP_TITLES[step] ?? step}>
        {step === 'features' && (
          <div class="chip-row">
            {options.features.map((feature) => (
              <label key={feature.key} class="check-row">
                <input
                  type="checkbox"
                  disabled={!options.features_available[feature.key]}
                  checked={answers.features.includes(feature.key)}
                  onChange={() => set({ features: toggle(answers.features, feature.key) })}
                />
                <span>
                  <strong>{feature.label}</strong>
                  {!options.features_available[feature.key] && ' — nothing configured for it'}
                </span>
              </label>
            ))}
          </div>
        )}

        {step === 'sources' &&
          Object.entries(plan.grid).map(([component, sources]) =>
            sources.length ? (
              <div key={component}>
                <h3>{COMPONENT_TITLES[component] ?? component}</h3>
                <div class="chip-row">
                  {sources.map((source) => (
                    <label key={source} class="check-row">
                      <input
                        type="checkbox"
                        checked={(answers.components[component] ?? []).includes(source)}
                        onChange={() =>
                          set({
                            components: {
                              ...answers.components,
                              [component]: toggle(answers.components[component] ?? [], source),
                            },
                          })
                        }
                      />
                      <span>{source}</span>
                    </label>
                  ))}
                </div>
              </div>
            ) : null,
          )}

        {(step === 'github_owners' || step === 'azdo_projects') && (
          <div class="field-row">
            <label for="scope-list">One per line</label>
            <textarea
              id="scope-list"
              rows={4}
              value={(step === 'github_owners' ? answers.github_owners : answers.azdo_projects).join('\n')}
              onInput={(e) => {
                const values = (e.target as HTMLTextAreaElement).value
                  .split('\n')
                  .map((v) => v.trim())
                  .filter(Boolean);
                set(step === 'github_owners' ? { github_owners: values } : { azdo_projects: values });
              }}
            />
          </div>
        )}

        {step === 'depth' && (
          <div class="chip-row">
            {options.depths.map((depth) => (
              <label key={depth} class="check-row">
                <input type="radio" checked={answers.depth === depth} onChange={() => set({ depth })} />
                <span>
                  <strong>{depth}</strong>
                  {depth === 'quick' ? ' — no LLM calls, deterministic explanations' : ' — reads and explains tickets'}
                </span>
              </label>
            ))}
          </div>
        )}

        {step === 'window' && (
          <div class="chip-row">
            {options.window_presets.map((days) => (
              <label key={days} class="check-row">
                <input
                  type="radio"
                  checked={answers.window_days === days}
                  onChange={() => set({ window_days: days })}
                />
                <span>{days} days</span>
              </label>
            ))}
          </div>
        )}

        {step === 'members' && (
          <>
            {roster === null ? (
              <button type="button" onClick={() => void fetchRoster()}>
                Load the roster
              </button>
            ) : roster.length ? (
              <div class="chip-row">
                {roster.map((name) => (
                  <label key={name} class="check-row">
                    <input
                      type="checkbox"
                      checked={(answers.members ?? []).includes(name)}
                      onChange={() => set({ members: toggle(answers.members ?? [], name) })}
                    />
                    <span>{name}</span>
                  </label>
                ))}
              </div>
            ) : (
              <p>Nobody came back — the analysis will cover the whole team.</p>
            )}
            <p class="dash-note">Leave everyone unpicked to analyse the whole team.</p>
          </>
        )}

        {step === 'review' && (
          <ul class="review-list">
            <li>
              <strong>Looking at</strong> {answers.features.join(', ') || 'nothing selected'}
            </li>
            <li>
              <strong>Reading</strong>{' '}
              {Object.entries(plan.run.components)
                .filter(([, v]) => v.length)
                .map(([k, v]) => `${k}: ${v.join('/')}`)
                .join(' · ') || 'nothing selected'}
            </li>
            <li>
              <strong>Depth</strong> {plan.run.depth}
            </li>
            <li>
              <strong>Window</strong> {plan.run.window_days} days
            </li>
            <li>
              <strong>People</strong> {answers.members?.length ? answers.members.join(', ') : 'the whole team'}
            </li>
          </ul>
        )}
      </Card>

      {error && <NoticeBlock title="Something went wrong" items={[error]} />}

      <div class="dash-actions">
        {index > 0 && (
          <button type="button" onClick={() => setIndex(index - 1)}>
            Back
          </button>
        )}
        {step === 'review' ? (
          <button type="button" class="primary" disabled={!answers.features.length} onClick={() => void start()}>
            Run the analysis
          </button>
        ) : (
          <button type="button" class="primary" onClick={() => setIndex(index + 1)}>
            Next
          </button>
        )}
        <a href="#/humans/analysis">Cancel</a>
      </div>
    </div>
  );
}
