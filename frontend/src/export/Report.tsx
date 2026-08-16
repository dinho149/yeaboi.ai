/**
 * Which report to draw.
 *
 * The `never` in the default case is the guard: adding a member to
 * {@link ExportReport} without a case here fails `npm run typecheck`, so a
 * Python exporter cannot start emitting a `kind` that this bundle would render
 * as a blank page. That failure mode is why it matters — an export is a file,
 * so a blank one is discovered by whoever opened it, months later, with no
 * server and no log to look at.
 */

import type { ExportReport } from './boot';
import { Annotations } from './reports/Annotations';
import { Anonymize } from './reports/Anonymize';
import { Performance } from './reports/Performance';
import { Plan } from './reports/Plan';
import { Poker } from './reports/Poker';
import { Profile } from './reports/Profile';
import { Reporting } from './reports/Reporting';
import { Retro } from './reports/Retro';
import { Roadmap } from './reports/Roadmap';
import { Standup } from './reports/Standup';

export function Report({ report }: { report: ExportReport }) {
  return (
    <>
      <Body report={report} />
      <Annotations rows={report.annotations ?? []} />
    </>
  );
}

/**
 * The generated document itself. Split from {@link Report} so reader-added
 * notes are drawn once, after it, rather than by each of the nine components —
 * the ninth one forgetting is a note nobody ever sees.
 */
function Body({ report }: { report: ExportReport }) {
  switch (report.kind) {
    case 'anonymize':
      return <Anonymize markdown={report.markdown} warnings={report.warnings} />;
    case 'roadmap':
      return (
        <Roadmap
          summary={report.summary}
          projects={report.projects}
          warnings={report.warnings}
          {...(report.edit ? { edit: report.edit } : {})}
        />
      );
    case 'performance':
      return (
        <Performance
          engineer={report.engineer}
          {...(report.lead ? { lead: report.lead } : {})}
          sections={report.sections}
          {...(report.footnote ? { footnote: report.footnote } : {})}
          warnings={report.warnings}
          {...(report.edit ? { edit: report.edit } : {})}
        />
      );
    case 'poker':
      return <Poker tickets={report.tickets} participants={report.participants} trend={report.trend} />;
    case 'retro':
      return (
        <Retro
          columns={report.columns}
          participants={report.participants}
          carried={report.carried}
          trend={report.trend}
        />
      );
    case 'reporting':
      return (
        <Reporting
          {...(report.headline ? { headline: report.headline } : {})}
          metrics={report.metrics}
          {...(report.summary ? { summary: report.summary } : {})}
          themes={report.themes}
          highlights={report.highlights}
          items={report.items}
          breakdown={report.breakdown}
          emoji={report.emoji}
          trend={report.trend}
          warnings={report.warnings}
          {...(report.edit ? { edit: report.edit } : {})}
        />
      );
    case 'plan':
      return (
        <Plan
          questionnaire={report.questionnaire}
          analysis={report.analysis}
          capacity={report.capacity}
          epicKey={report.epicKey}
          features={report.features}
          storyGroups={report.storyGroups}
          pointsByDiscipline={report.pointsByDiscipline}
          taskGroups={report.taskGroups}
          sprints={report.sprints}
          velocity={report.velocity}
          images={report.images}
          priorArt={report.priorArt}
        />
      );
    case 'standup':
      return (
        <Standup
          sprint={report.sprint}
          confidence={report.confidence}
          summary={report.summary}
          members={report.members}
          quietMembers={report.quietMembers}
          activityCounts={report.activityCounts}
          activityWindow={report.activityWindow}
          coverage={report.coverage}
          skipped={report.skipped}
          practices={report.practices}
          conflicts={report.conflicts}
          images={report.images}
          trend={report.trend}
          warnings={report.warnings}
          {...(report.edit ? { edit: report.edit } : {})}
          correctable={report.correctable ?? false}
        />
      );
    case 'profile':
      return <Profile sections={report.sections} coverage={report.coverage} />;
    default: {
      const unreachable: never = report;
      throw new Error(`export: no renderer for ${JSON.stringify(unreachable)}`);
    }
  }
}
