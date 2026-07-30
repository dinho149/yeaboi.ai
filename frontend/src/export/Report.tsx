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
import { Anonymize } from './reports/Anonymize';
import { Performance } from './reports/Performance';
import { Poker } from './reports/Poker';
import { Reporting } from './reports/Reporting';
import { Retro } from './reports/Retro';
import { Roadmap } from './reports/Roadmap';

export function Report({ report }: { report: ExportReport }) {
  switch (report.kind) {
    case 'anonymize':
      return <Anonymize markdown={report.markdown} warnings={report.warnings} />;
    case 'roadmap':
      return <Roadmap summary={report.summary} projects={report.projects} warnings={report.warnings} />;
    case 'performance':
      return (
        <Performance
          engineer={report.engineer}
          {...(report.lead ? { lead: report.lead } : {})}
          sections={report.sections}
          {...(report.footnote ? { footnote: report.footnote } : {})}
          warnings={report.warnings}
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
        />
      );
    default: {
      const unreachable: never = report;
      throw new Error(`export: no renderer for ${JSON.stringify(unreachable)}`);
    }
  }
}
