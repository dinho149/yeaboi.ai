/**
 * The roadmap export: what an ingested roadmap document turned into.
 *
 * The mix bars at the top are computed here, not sent. The payload carries the
 * projects; how many are large and how many land in Q3 is a fact *about* that
 * list, and deriving it on the server meant the Python exporter counting them
 * into a `dict` and then hand-assembling a bar and a legend that could disagree
 * about which colour meant which size. `countedSegments` returns both from one
 * zip, so they cannot.
 *
 * No trend line, deliberately: roadmap runs are ad-hoc ingestions, so a
 * run-over-run series would draw a cadence that does not exist.
 */

import {
  Card,
  Chip,
  countedSegments,
  Eyebrow,
  Legend,
  NoticeBlock,
  ProseBullets,
  SegmentBar,
} from '../../design/primitives';
import type { RoadmapProject } from '../boot';
import styles from './reports.module.css';

/** `large` is the only distinguished value; the engine treats everything else as small. */
function isLarge(project: RoadmapProject): boolean {
  return project.size === 'large';
}

function sizeLabel(project: RoadmapProject): string {
  return isLarge(project) ? 'Large' : 'Small';
}

/**
 * One counted breakdown: a caption, a bar, its key.
 *
 * The caption is not decoration. Size and quarter frequently split the same
 * way, and two captionless full-width stripes stacked on top of each other read
 * as one element repeated rather than as two different facts.
 */
function Mix({ caption, label, counts }: { caption: string; label: string; counts: Array<readonly [string, number]> }) {
  const { segments, legend } = countedSegments(counts);
  if (!segments.length) return null;
  return (
    <div className={styles['mix']}>
      <Eyebrow>{caption}</Eyebrow>
      <SegmentBar segments={segments} label={label} />
      <Legend items={legend} />
    </div>
  );
}

function Project({ project }: { project: RoadmapProject }) {
  const themes = project.themes ?? [];
  return (
    <Card
      className={styles['project']}
      title={
        <>
          <span className={styles['projectIndex']}>{project.index}</span>
          {project.name}
        </>
      }
      actions={
        <div className={styles['chips']}>
          {/* Large is `warn`, not `danger`: a big project is a scheduling fact
              worth flagging, not a problem. */}
          <Chip tone={isLarge(project) ? 'warn' : 'ok'}>{sizeLabel(project)}</Chip>
          {project.quarter ? <Chip>{project.quarter}</Chip> : null}
          {themes.map((theme) => (
            <Chip key={theme}>{theme}</Chip>
          ))}
        </div>
      }
    >
      {project.description ? <ProseBullets text={project.description} /> : null}
      {project.rationale ? (
        <p className={styles['why']}>
          <Chip tone="accent">Why now</Chip>
          {project.rationale}
        </p>
      ) : null}
    </Card>
  );
}

export function Roadmap({
  summary,
  projects,
  warnings,
}: {
  summary: string;
  projects: RoadmapProject[];
  warnings: string[];
}) {
  const bySize = new Map<string, number>();
  const byQuarter = new Map<string, number>();
  for (const project of projects) {
    const size = sizeLabel(project);
    bySize.set(size, (bySize.get(size) ?? 0) + 1);
    if (project.quarter) byQuarter.set(project.quarter, (byQuarter.get(project.quarter) ?? 0) + 1);
  }

  return (
    <>
      {summary ? <blockquote className={styles['lede']}>{summary}</blockquote> : null}

      {projects.length ? (
        <section id="projects">
          <h2 className={styles['h2']}>Recommended projects</h2>
          <div className={styles['mixes']}>
            <Mix caption="By size" label="Projects by size" counts={[...bySize.entries()]} />
            {/* One quarter is not a distribution — the bar would be a solid
                block captioned with the only value it could have. */}
            {byQuarter.size > 1 ? (
              <Mix caption="By quarter" label="Projects by quarter" counts={[...byQuarter.entries()]} />
            ) : null}
          </div>
          <div className={styles['projects']}>
            {projects.map((project) => (
              <Project key={`${project.index}-${project.name}`} project={project} />
            ))}
          </div>
        </section>
      ) : (
        <p className={styles['empty']}>No projects were extracted from the roadmap.</p>
      )}

      <NoticeBlock title="Notices" items={warnings} />
    </>
  );
}
