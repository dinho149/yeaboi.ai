/**
 * The sprint-plan export — the longest report yeaboi writes.
 *
 * It is a *checkpoint* artifact: the pipeline can be exported after intake,
 * after analysis, after stories, at any point. So every section here renders
 * from an empty list without special-casing, and the header badges say which
 * artifacts exist. A plan with no tasks yet is a normal plan.
 *
 * **The priority and confidence colours are tone lookups with a fallback**, the
 * same arrangement as standup's. `html_exporter.py` held them as CSS class
 * names (`badge-critical`, `disc-frontend`) which meant the stylesheet and the
 * exporter each had to know the whole vocabulary; here the exporter sends the
 * word and one `Record` decides what it looks like.
 */

import {
  Card,
  Chip,
  countedSegments,
  DataTable,
  Eyebrow,
  Legend,
  Prose,
  SegmentBar,
  StatBar,
  StatGrid,
  StatTile,
} from '../../design/primitives';
import type { Tone } from '../../design/tone';
import type { PlanFeature, PlanSprint, PlanStory, PlanTask } from '../boot';
import styles from './reports.module.css';

/** The plan's own priority ramp. Anything else renders neutral. */
const PRIORITY_TONE: Record<string, Tone> = {
  critical: 'critical',
  high: 'high',
  medium: 'medium',
  low: 'low',
};

/** How sure the estimate is. Low confidence is a warning, not a failure. */
const CONFIDENCE_TONE: Record<string, Tone> = {
  high: 'ok',
  medium: 'warn',
  low: 'danger',
};

function priorityTone(priority: string): Tone | undefined {
  return PRIORITY_TONE[priority.toLowerCase()];
}

function Story({ story }: { story: PlanStory }) {
  const tone = priorityTone(story.priority);
  return (
    <Card
      className={styles['story']}
      // `as never`, the same cast the live board's Column uses: a custom
      // property is not in `CSSProperties`, and this is the one way to hand the
      // stylesheet a value that came from the typed tone map.
      style={tone ? ({ '--story-tone': `var(--${tone})` } as never) : undefined}
      title={
        <>
          <span className={styles['cardId']}>{story.id}</span>
          {story.title}
        </>
      }
      actions={
        <div className={styles['chips']}>
          <Chip {...(tone ? { tone } : {})}>{story.priority}</Chip>
          <Chip tone="accent">{story.points} pts</Chip>
          <Chip>{story.discipline}</Chip>
        </div>
      }
    >
      <p className={styles['storyText']}>{story.text}</p>

      {story.rationale ? (
        <p className={styles['why']}>
          <Chip {...(story.confidence && CONFIDENCE_TONE[story.confidence.toLowerCase()]
            ? { tone: CONFIDENCE_TONE[story.confidence.toLowerCase()] as Tone }
            : {})}
          >
            {story.confidence ? `${story.confidence} confidence` : 'Points rationale'}
          </Chip>
          {story.rationale}
        </p>
      ) : null}

      {story.acceptanceCriteria.length ? (
        <ol className={styles['criteria']}>
          {story.acceptanceCriteria.map((ac, index) => (
            <li key={index}>
              <Eyebrow>AC {index + 1}</Eyebrow>
              <span>
                <b>Given</b> {ac.given} <b>When</b> {ac.when} <b>Then</b> {ac.then}
              </span>
            </li>
          ))}
        </ol>
      ) : null}

      {story.dod.length ? (
        <div className={styles['dod']}>
          <Eyebrow>Definition of Done</Eyebrow>
          <ul>
            {story.dod.map(([item, applicable]) => (
              // Struck through rather than removed: which DoD items a story is
              // *exempt* from is a decision worth showing, not an absence.
              <li key={item} className={applicable ? undefined : styles['exempt']}>
                <span aria-hidden="true">{applicable ? '✓' : '✗'}</span> {item}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

function Sprint({ sprint, velocity }: { sprint: PlanSprint; velocity: number }) {
  const fill = sprint.capacity ? Math.min((sprint.used / sprint.capacity) * 100, 100) : 0;
  // Over capacity is a planning error; near it is a normal, tight sprint.
  const tone: Tone = sprint.used > sprint.capacity ? 'danger' : fill > 80 ? 'warn' : 'accent';
  const reduced = sprint.capacity < velocity;

  return (
    <Card
      className={reduced ? `${styles['sprint']} ${styles['reduced']}` : styles['sprint']}
      title={sprint.name}
      actions={
        <Chip tone={tone}>
          {sprint.used} / {sprint.capacity} pts
        </Chip>
      }
    >
      <p className={styles['sprintGoal']}>{sprint.goal}</p>
      <StatBar pct={fill} tone={tone} label={`${sprint.used} of ${sprint.capacity} points planned`} />
      {reduced ? (
        <p className={styles['reducedNote']}>Reduced from {velocity} pts (bank holidays / deductions)</p>
      ) : null}
      <div className={styles['chipRow']}>
        {sprint.storyIds.map((id) => (
          <Chip key={id}>{id}</Chip>
        ))}
      </div>
    </Card>
  );
}

export function Plan({
  questionnaire,
  analysis,
  capacity,
  epicKey,
  features,
  storyGroups,
  pointsByDiscipline,
  taskGroups,
  sprints,
  velocity,
  images,
}: {
  questionnaire: Array<[string, string, string]>;
  analysis: {
    name: string;
    description: string;
    targetState: string;
    projectType: string;
    sprintWeeks: number;
    targetSprints: number;
    fields: Array<{ label: string; items: string[] }>;
  } | null;
  capacity: {
    teamSize: number;
    sprintWeeks: number;
    targetSprints: number;
    velocity: number;
    netVelocity: number;
    deductions: string[];
  } | null;
  epicKey: string;
  features: PlanFeature[];
  storyGroups: Array<{ featureId: string; featureTitle: string; stories: PlanStory[] }>;
  pointsByDiscipline: Array<[string, number]>;
  taskGroups: Array<{ storyId: string; storyText: string; tasks: PlanTask[] }>;
  sprints: PlanSprint[];
  velocity: number;
  images: string[];
}) {
  const nothing =
    !questionnaire.length && !analysis && !features.length && !storyGroups.length && !taskGroups.length && !sprints.length;
  if (nothing) return <p className={styles['empty']}>No artifacts to export yet.</p>;

  const mix = countedSegments(pointsByDiscipline);
  const totalPoints = sprints.reduce((sum, sprint) => sum + sprint.used, 0);

  return (
    <>
      {questionnaire.length ? (
        <section id="questionnaire">
          <h2 className={styles['h2']}>Intake Questionnaire</h2>
          <DataTable
            rows={questionnaire}
            rowKey={([label]) => label}
            columns={[
              { key: 'Q', header: '', width: '3rem', cell: ([label]) => <span className={styles['cardId']}>{label}</span> },
              { key: 'Question', cell: ([, question]) => question },
              { key: 'Answer', cell: ([, , answer]) => answer },
            ]}
          />
        </section>
      ) : null}

      {analysis ? (
        <section id="analysis">
          <h2 className={styles['h2']}>Project Analysis</h2>
          <Card
            title={analysis.name}
            actions={
              <div className={styles['chips']}>
                <Chip>{analysis.projectType}</Chip>
                <Chip>
                  {analysis.sprintWeeks}-week × {analysis.targetSprints} sprints
                </Chip>
                {epicKey ? <Chip tone="accent">{epicKey}</Chip> : null}
              </div>
            }
          >
            <Prose text={analysis.description} />
            <p className={styles['target']}>
              <Eyebrow>Target state</Eyebrow> {analysis.targetState}
            </p>
          </Card>

          <div className={styles['fields']}>
            {analysis.fields.map((field) => (
              <div key={field.label} className={styles['field']}>
                <Eyebrow>{field.label}</Eyebrow>
                <ul className={styles['bullets']}>
                  {field.items.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          {capacity ? (
            <div className={styles['capacity']}>
              <Eyebrow>Capacity</Eyebrow>
              <StatGrid>
                <StatTile
                  value={capacity.netVelocity}
                  label="Net velocity"
                  hint={`of ${capacity.velocity} pts/sprint`}
                  tone="ok"
                />
                <StatTile value={capacity.teamSize} label="Engineers" />
                <StatTile
                  value={`${capacity.sprintWeeks}w × ${capacity.targetSprints}`}
                  label="Sprint plan"
                  hint={`~${capacity.sprintWeeks * capacity.targetSprints} weeks`}
                />
              </StatGrid>
              {capacity.velocity > 0 && capacity.netVelocity > 0 && capacity.netVelocity <= capacity.velocity ? (
                <div className={styles['split']}>
                  <SegmentBar
                    segments={[
                      { value: capacity.netVelocity, tone: 'ok' },
                      { value: capacity.velocity - capacity.netVelocity, tone: 'muted' },
                    ]}
                    label={`Net ${capacity.netVelocity} of ${capacity.velocity} pts/sprint`}
                  />
                  <Legend
                    items={[
                      { label: 'Net', count: capacity.netVelocity, tone: 'ok' },
                      { label: 'Deducted', count: capacity.velocity - capacity.netVelocity, tone: 'muted' },
                    ]}
                  />
                </div>
              ) : null}
              {capacity.deductions.length ? (
                <p className={styles['footnote']}>Deductions — {capacity.deductions.join(', ')}</p>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {features.length ? (
        <section id="features">
          <h2 className={styles['h2']}>Features</h2>
          {features.map((feature) => (
            <Card
              key={feature.id}
              className={styles['feature']}
              title={
                <>
                  <span className={styles['cardId']}>{feature.id}</span>
                  {feature.title}
                </>
              }
              actions={<Chip {...(priorityTone(feature.priority) ? { tone: priorityTone(feature.priority) as Tone } : {})}>{feature.priority}</Chip>}
            >
              <Prose text={feature.description} />
            </Card>
          ))}
        </section>
      ) : null}

      {storyGroups.length ? (
        <section id="stories">
          <h2 className={styles['h2']}>User Stories</h2>
          {mix.segments.length ? (
            <div className={styles['split']}>
              <SegmentBar segments={mix.segments} label="Story points by discipline" />
              <Legend items={mix.legend} />
            </div>
          ) : null}
          {storyGroups.map((group) => (
            <div key={group.featureId} className={styles['storyGroup']}>
              <Eyebrow>
                {group.featureId}: {group.featureTitle}
              </Eyebrow>
              {group.stories.map((story) => (
                <Story key={story.id} story={story} />
              ))}
            </div>
          ))}
        </section>
      ) : null}

      {taskGroups.length ? (
        <section id="tasks">
          <h2 className={styles['h2']}>Tasks</h2>
          {taskGroups.map((group) => (
            <div key={group.storyId} className={styles['taskGroup']}>
              {/* The id is the label; the story sentence is *prose* and stays
                  in its own case. An eyebrow uppercases, and "As a support
                  engineer, I want…" set in tracked mono caps across two lines
                  is furniture nobody can read. */}
              <Eyebrow>{group.storyId}</Eyebrow>
              <p className={styles['groupSubject']}>{group.storyText}</p>
              <DataTable
                rows={group.tasks}
                rowKey={(task) => task.id}
                columns={[
                  { key: 'ID', cell: (task) => <span className={styles['cardId']}>{task.id}</span> },
                  { key: 'Label', cell: (task) => <Chip>{task.label}</Chip> },
                  { key: 'Title', cell: (task) => task.title },
                  {
                    key: 'Description',
                    cell: (task) => (
                      <>
                        {task.description}
                        {task.testPlan ? (
                          <span className={styles['taskNote']}>
                            <b>Test plan:</b> {task.testPlan}
                          </span>
                        ) : null}
                        {task.aiPrompt ? (
                          <span className={styles['taskNote']}>
                            <b>AI prompt:</b> {task.aiPrompt}
                          </span>
                        ) : null}
                      </>
                    ),
                  },
                ]}
              />
            </div>
          ))}
        </section>
      ) : null}

      {sprints.length ? (
        <section id="sprints">
          <h2 className={styles['h2']}>Sprint Plan</h2>
          <p className={styles['footnote']}>
            {sprints.length} sprint{sprints.length === 1 ? '' : 's'} · {totalPoints} total story points ·{' '}
            {velocity} pts/sprint velocity
          </p>
          {sprints.map((sprint) => (
            <Sprint key={sprint.name} sprint={sprint} velocity={velocity} />
          ))}
        </section>
      ) : null}

      {images.length ? (
        <section id="attachments">
          <h2 className={styles['h2']}>Attachments</h2>
          {images.map((src, index) => (
            <img key={index} className={styles['screenshot']} src={src} alt={`Screenshot ${index + 1}`} />
          ))}
        </section>
      ) : null}
    </>
  );
}
