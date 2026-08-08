/**
 * The standup export.
 *
 * The behaviour worth pinning here is what moved off the Python side with the
 * render layer: the two tone lookups that used to be three colour dicts, the
 * adaptive category grid, and the count-chip pluralisation — which is exactly
 * the kind of detail a port loses silently, since "1 tickets" renders fine.
 */

import { fireEvent, render, screen } from '@testing-library/preact';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { axe } from 'vitest-axe';

import type { EvidenceItem, StandupMember } from '../boot';
import { groupStories, Standup } from './Standup';

function member(over: Partial<StandupMember> = {}): StandupMember {
  return {
    name: 'Ada Lovelace',
    summary: [[{ s: 'Shipped the SSO flow.' }]],
    categories: [],
    footnotes: [],
    counts: [0, 0, 0],
    links: [],
    ...over,
  };
}

const BASE = {
  sprint: { name: 'Sprint 42', day: 7, total: 10 },
  confidence: { label: 'At risk', pct: 68, text: 'At risk (68%)', trend: '', trendText: '', rationale: '' },
  summary: [],
  members: [],
  activityCounts: [] as Array<[string, number]>,
  activityWindow: '',
  coverage: [] as Array<[string, string]>,
  skipped: [] as Array<[string, string]>,
  practices: [] as Array<{ rule: string; count: number; title: string }>,
  images: [],
  trend: null,
  warnings: [],
};

describe('Standup', () => {
  it('pluralises the count chips, and leaves "code" uncountable', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ counts: [1, 3, 2] })]} />
    );
    const chips = [...container.querySelectorAll('.chips .chip')].map((c) => c.textContent);
    expect(chips).toEqual(['1 ticket', '3 code', '2 docs']);
  });

  it('shows no chip for a category with no activity', () => {
    const { container } = render(<Standup {...BASE} members={[member({ counts: [0, 0, 0] })]} />);
    expect(container.querySelectorAll('.chips .chip')).toHaveLength(0);
  });

  it('colours the confidence by its label, and falls back for an unknown one', () => {
    // Second tile: sprint day, confidence, members.
    const value = () => container.querySelector('.stat:nth-child(2) .statValue');
    const { container, rerender } = render(<Standup {...BASE} />);
    expect(value()?.getAttribute('style')).toContain('var(--warn)');

    // The engine produces these strings; it is not a validated union, so an
    // unfamiliar one must degrade rather than break the page.
    rerender(<Standup {...BASE} confidence={{ ...BASE.confidence, label: 'Whatever' }} />);
    expect(value()?.getAttribute('style')).toContain('var(--low)');
  });

  it('shows an em dash rather than a percentage when confidence is unknown', () => {
    const { container } = render(
      <Standup
        {...BASE}
        confidence={{ ...BASE.confidence, label: 'Insufficient data', pct: 0, text: 'Insufficient data' }}
      />
    );
    expect(container.querySelector('.stat:nth-child(2) .statValue')?.textContent).toBe('—');
  });

  it('puts the sprint bar inside its own tile, not under the whole grid', () => {
    // A full-width bar under four tiles reads as page progress, not "day 7 of 10".
    const { container } = render(<Standup {...BASE} />);
    const tile = container.querySelector('.stat');
    expect(tile?.querySelector('[aria-label="Sprint day 7 of 10"]')).not.toBeNull();
  });

  it('marks a blocked member and shows what they are blocked on', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ blockers: [{ s: 'waiting on review' }] })]} />
    );
    expect(container.querySelector('.blocked')).not.toBeNull();
    // Jira's flag idiom: the word beside the glyph, and the status lozenge agrees.
    const flag = container.querySelector('.impediment');
    expect(flag?.textContent).toContain('Flagged');
    expect(flag?.textContent).toContain('waiting on review');
    expect(container.querySelector('.issueHead .lozenge')?.textContent).toBe('Blocked');
  });

  it('renders ticket keys in prose as links and the rest as text', () => {
    render(
      <Standup
        {...BASE}
        members={[
          member({
            summary: [[{ s: 'Shipped ' }, { s: 'ACME-101', href: 'https://x/browse/ACME-101' }, { s: '.' }]],
          }),
        ]}
      />
    );
    expect(screen.getByRole('link', { name: 'ACME-101' }).getAttribute('href')).toBe('https://x/browse/ACME-101');
  });

  it('drops a link whose URL the exporter rejected, keeping the label', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ links: [['see this', '']] })]} />
    );
    expect(container.querySelector('.chipRow a')).toBeNull();
    expect(container.querySelector('.chipRow .chip')?.textContent).toBe('see this');
  });

  it('gives a quiet category a footnote instead of a column', () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[
          member({
            categories: [{ label: 'Ticketing', items: [[{ s: 'ACME-1 moved.' }]], links: [], evidence: [] }],
            footnotes: [{ label: 'Documentation', runs: [{ s: 'Documentation sources are not configured.' }] }],
          }),
        ]}
      />
    );
    // The Description section always leads; Ticketing renders in Jira's clothes.
    expect([...container.querySelectorAll('.issueSection .eyebrow')].map((e) => e.textContent)).toEqual([
      'Description',
      'Ticket status changes',
    ]);
    // The wording survives: "not configured" must stay distinguishable from
    // "no activity detected".
    expect(container.querySelector('.footnote')?.textContent).toBe(
      'Documentation — Documentation sources are not configured.'
    );
  });

  it('renders a one-sentence team summary as a paragraph and several as a list', () => {
    const { container, rerender } = render(<Standup {...BASE} summary={[[{ s: 'Steady progress.' }]]} />);
    expect(container.querySelector('#summary ul')).toBeNull();
    expect(container.querySelector('#summary p')?.textContent).toBe('Steady progress.');

    rerender(<Standup {...BASE} summary={[[{ s: 'One.' }], [{ s: 'Two.' }]]} />);
    expect(container.querySelectorAll('#summary li')).toHaveLength(2);
  });

  it('scales the activity bars against the busiest member', () => {
    // Every bar filling the track would be four pictures of 100%.
    const { container } = render(
      <Standup
        {...BASE}
        members={[member({ name: 'Ada', counts: [4, 0, 0] }), member({ name: 'Bo', counts: [1, 0, 0] })]}
      />
    );
    const widths = [...container.querySelectorAll('.activityRow .segTrack')].map(
      (bar) => (bar as HTMLElement).style.width
    );
    expect(widths).toEqual(['100%', '25%']);
  });

  it('leaves an all-quiet member out of the activity bars but keeps their card', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ name: 'Ada', counts: [2, 0, 0] }), member({ name: 'Quiet' })]} />
    );
    expect([...container.querySelectorAll('.activityName')].map((n) => n.textContent)).toEqual(['Ada']);
    // Two names on the page now — the jump strip and the card — so ask for the card.
    expect(container.querySelector('#m-quiet .memberHead')).not.toBeNull();
  });

  it('draws no activity block at all when nobody has counts', () => {
    const { container } = render(<Standup {...BASE} members={[member()]} />);
    expect(container.querySelector('.activity')).toBeNull();
  });

  it('names each coverage status beside its dot, never colour alone', () => {
    const { container } = render(
      <Standup {...BASE} coverage={[['ticketing', 'covered'], ['documentation', 'not_configured']]} />
    );
    const entries = [...container.querySelectorAll('.coverage')].map((c) => c.textContent);
    expect(entries).toEqual(['ticketing covered', 'documentation not configured']);
    const dots = [...container.querySelectorAll('.dot')].map((d) => (d as HTMLElement).style.background);
    expect(dots).toEqual(['var(--ok)', 'var(--low)']);
  });

  it('tells a reader when there were no individual updates', () => {
    render(<Standup {...BASE} />);
    expect(screen.getByText('No individual updates.').tagName).toBe('P');
  });
});

function evidence(over: Partial<EvidenceItem> = {}): EvidenceItem {
  return {
    kind: 'commit',
    key: '78e4201d',
    title: 'Fix login redirect',
    url: 'https://g/c1',
    repo: 'yeaboi/web',
    status: '',
    time: '2026-07-30T09:15:00',
    children: [],
    type: '',
    parent: '',
    subtask: false,
    tickets: [],
    ...over,
  };
}

function memberWithEvidence(rows: EvidenceItem[]): StandupMember {
  return member({
    counts: [0, rows.length, 0],
    categories: [{ label: 'Code', items: [[{ s: 'Merged the login fix.' }]], links: [], evidence: rows }],
  });
}

describe('Standup evidence rows', () => {
  it('says what each item is: kind word, linked key, title, repo', () => {
    const { container } = render(<Standup {...BASE} members={[memberWithEvidence([evidence()])]} />);
    const row = container.querySelector('.evidenceRow');
    expect(row?.querySelector('.chip')?.textContent).toBe('commit');
    const key = screen.getByRole('link', { name: '78e4201d' });
    expect(key.getAttribute('href')).toBe('https://g/c1');
    expect(row?.querySelector('.evidenceTitle')?.textContent).toBe('Fix login redirect');
    expect(row?.querySelector('.evidenceMeta')?.textContent).toBe('yeaboi/web');
  });

  it('degrades an unknown kind to its own word rather than failing', () => {
    const { container } = render(
      <Standup {...BASE} members={[memberWithEvidence([evidence({ kind: 'mystery' })])]} />
    );
    expect(container.querySelector('.evidenceRow .chip')?.textContent).toBe('mystery');
  });

  it('labels every collector-produced ticketing and doc kind with a plain word', () => {
    // issue/work_item (assigned-ticket rows) and page-created come straight
    // from the collectors — a raw "work_item" chip is jargon, not a label.
    const rows = [
      evidence({ kind: 'issue', key: 'PSOT-1' }),
      evidence({ kind: 'work_item', key: '4211' }),
      evidence({ kind: 'page-created', key: 'runbook' }),
    ];
    const { container } = render(<Standup {...BASE} members={[memberWithEvidence(rows)]} />);
    const chips = [...container.querySelectorAll('.evidenceRow .chip')].map((c) => c.textContent);
    expect(chips).toEqual(['ticket', 'ticket', 'doc']);
  });

  it('keeps a row whose URL the exporter rejected, without a link', () => {
    const { container } = render(
      <Standup {...BASE} members={[memberWithEvidence([evidence({ url: '' })])]} />
    );
    expect(container.querySelector('.evidenceRow a')).toBeNull();
    expect(container.querySelector('.evidenceRef')?.textContent).toBe('78e4201d');
  });

  it('folds everything after the third row behind an accessible toggle', () => {
    const rows = [0, 1, 2, 3, 4].map((i) => evidence({ key: `#${i}`, url: `https://g/pr/${i}` }));
    const { container } = render(<Standup {...BASE} members={[memberWithEvidence(rows)]} />);

    expect(container.querySelectorAll('.evidenceRow')).toHaveLength(5);
    const folded = container.querySelector('ul[hidden]');
    expect(folded?.querySelectorAll('.evidenceRow')).toHaveLength(2);

    const toggle = screen.getByRole('button', { name: '+ 2 more' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-controls')).toBe(folded?.getAttribute('id'));
    fireEvent.click(toggle);
    expect(container.querySelector('ul[hidden]')).toBeNull();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('shows no toggle for three rows or fewer', () => {
    const { container } = render(
      <Standup {...BASE} members={[memberWithEvidence([evidence(), evidence({ key: 'a1b2c3d4' })])]} />
    );
    expect(container.querySelector('.moreToggle')).toBeNull();
  });

  it('falls back to the legacy chip row when a category has no evidence', () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[
          member({
            categories: [
              { label: 'Code', items: [], links: [['78E4201D', 'https://g/c1']], evidence: [] },
            ],
          }),
        ]}
      />
    );
    expect(container.querySelector('.evidenceRow')).toBeNull();
    expect(container.querySelector('.chipRow .chip')?.textContent).toBe('78E4201D');
  });

  it('colour-codes the category section and its count chip alike', () => {
    const { container } = render(<Standup {...BASE} members={[memberWithEvidence([evidence()])]} />);
    // The section's dot carries the tone now; the Description section has none.
    const dot = container.querySelector('.issueSection .dot') as HTMLElement;
    expect(dot.getAttribute('style')).toContain('var(--accent2)');
    const chip = container.querySelector('.chips .chip') as HTMLElement;
    expect(chip.getAttribute('style')).toContain('var(--accent2)');
  });
});

describe('Standup member strip', () => {
  const two = [
    member({ name: 'Ada Lovelace' }),
    member({ name: 'Grace Hopper', blockers: [{ s: 'waiting on review' }] }),
  ];

  it('jumps to each member card, and marks blocked members with the word', () => {
    const { container } = render(<Standup {...BASE} members={two} />);
    const strip = screen.getByRole('navigation', { name: 'Jump to member' });
    const links = [...strip.querySelectorAll('a')];
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['#m-ada-lovelace', '#m-grace-hopper']);
    expect(container.querySelector('#m-grace-hopper')).not.toBeNull();
    expect(links[1]?.textContent).toContain('blocked');
    expect(links[0]?.textContent).not.toContain('blocked');
  });

  it('does not render for a single member', () => {
    render(<Standup {...BASE} members={[member()]} />);
    expect(screen.queryByRole('navigation', { name: 'Jump to member' })).toBeNull();
  });

  it('has no axe violations with the strip, evidence rows, and fold visible', async () => {
    const rows = [0, 1, 2, 3].map((i) => evidence({ key: `#${i}`, url: `https://g/pr/${i}` }));
    const { container } = render(
      <Standup {...BASE} members={[memberWithEvidence(rows), ...two]} />
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('Standup intro bullets', () => {
  it('renders each summary clause as its own bullet', () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[member({ summary: [[{ s: 'Closed the login work' }], [{ s: 'continuing the audit fix' }]] })]}
      />
    );
    const items = [...container.querySelectorAll('ul.memberSummary li')].map((li) => li.textContent);
    expect(items).toEqual(['Closed the login work', 'continuing the audit fix']);
  });

  it('falls back to the empty-state paragraph with no clauses', () => {
    const { container } = render(<Standup {...BASE} members={[member({ summary: [] })]} />);
    expect(container.querySelector('p.memberSummary')?.textContent).toBe('No activity detected.');
  });

  it('splits a clause enumerating titled tickets into one bullet per ticket', () => {
    // The engine writes "Edited KEY title, KEY title, and KEY title" as one
    // clause; a wall of comma-joined links is exactly what the card replaced.
    const { container } = render(
      <Standup
        {...BASE}
        members={[
          member({
            summary: [
              [
                { s: 'Edited ' },
                { s: 'PSOT-1638 Barbican dev env', href: 'https://x/browse/PSOT-1638' },
                { s: ', ' },
                { s: 'PSOT-1633 LinearB comments', href: 'https://x/browse/PSOT-1633' },
                { s: ', and ' },
                { s: 'PSOT-1634 Pulumi update', href: 'https://x/browse/PSOT-1634' },
                { s: '.' },
              ],
            ],
          }),
        ]}
      />
    );
    const items = [...container.querySelectorAll('ul.memberSummary li')].map((li) => li.textContent);
    // Intro words stay glued to their first ticket; the list glue is trimmed.
    expect(items).toEqual([
      'Edited PSOT-1638 Barbican dev env',
      'PSOT-1633 LinearB comments',
      'PSOT-1634 Pulumi update.',
    ]);
  });

  it('keeps a bare-key enumeration as a single bullet', () => {
    // "across six tickets: KEY, KEY, KEY" split apart would be bullets of
    // naked keys — the feed rows below already give each ticket its line.
    const { container } = render(
      <Standup
        {...BASE}
        members={[
          member({
            summary: [
              [
                { s: 'Groomed six tickets: ' },
                { s: 'PSOT-1638', href: 'https://x/browse/PSOT-1638' },
                { s: ', ' },
                { s: 'PSOT-1633', href: 'https://x/browse/PSOT-1633' },
                { s: '.' },
              ],
            ],
          }),
        ]}
      />
    );
    const items = [...container.querySelectorAll('ul.memberSummary li')].map((li) => li.textContent);
    expect(items).toEqual(['Groomed six tickets: PSOT-1638, PSOT-1633.']);
  });
});

describe('Standup status lozenges', () => {
  it('renders a done status as the green lozenge and keeps the word', () => {
    const { container } = render(
      <Standup {...BASE} members={[memberWithEvidence([evidence({ kind: 'issue', status: 'Done' })])]} />
    );
    const status = container.querySelector('.evidenceStatus') as HTMLElement;
    expect(status.textContent).toBe('Done');
    expect(status.className).toContain('lozengeDone');
  });

  it('degrades an unknown status to the grey lozenge with the plain word', () => {
    const { container } = render(
      <Standup {...BASE} members={[memberWithEvidence([evidence({ kind: 'issue', status: 'Mystery Lane' })])]} />
    );
    const status = container.querySelector('.evidenceStatus') as HTMLElement;
    expect(status.textContent).toBe('Mystery Lane');
    expect(status.className).toContain('lozengeTodo');
  });
});

describe('Standup doc evidence rows', () => {
  it('links the page title and drops the machine id', () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[
          memberWithEvidence([
            evidence({ kind: 'page', key: '1892385692', title: 'MFA Runbook', url: 'https://c/p', repo: '' }),
          ]),
        ]}
      />
    );
    const link = screen.getByRole('link', { name: 'MFA Runbook' });
    expect(link.getAttribute('href')).toBe('https://c/p');
    expect(container.textContent).not.toContain('1892385692');
  });
});

describe('Standup PR commit breakdown', () => {
  const pr = evidence({
    kind: 'pr',
    key: '#91',
    title: 'Enable SSO',
    status: 'merged',
    url: 'https://g/pr/91',
    children: [
      evidence({ key: 'aaa1', title: 'Wire the callback', url: 'https://g/c/1' }),
      evidence({ key: 'aaa2', title: 'Add the SAML config', url: 'https://g/c/2' }),
    ],
  });

  it('folds the commits behind a per-PR toggle', () => {
    const { container } = render(<Standup {...BASE} members={[memberWithEvidence([pr])]} />);
    const toggle = screen.getByRole('button', { name: '▸ 2 commits' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    const region = container.querySelector(`#${toggle.getAttribute('aria-controls')}`) as HTMLElement;
    expect(region.hidden).toBe(true);

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(region.hidden).toBe(false);
    const commits = [...region.querySelectorAll('.evidenceTitle')].map((s) => s.textContent);
    expect(commits).toEqual(['Wire the callback', 'Add the SAML config']);
  });

  it('renders no toggle for a childless row', () => {
    const { container } = render(<Standup {...BASE} members={[memberWithEvidence([evidence()])]} />);
    expect(container.querySelector('.commitToggle')).toBeNull();
  });

  it('has no axe violations with a breakdown open', async () => {
    const { container } = render(<Standup {...BASE} members={[memberWithEvidence([pr])]} />);
    fireEvent.click(screen.getByRole('button', { name: '▸ 2 commits' }));
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('Standup practices', () => {
  const untracked = {
    rule: 'untracked-work',
    title: 'Untracked work',
    detail: [{ s: 'PR #91 carries no ticket reference.' }],
    evidence: [['#91', 'https://example.invalid/pull/91']] as Array<[string, string]>,
  };

  it('hoists an untracked-work signal into its own card section, evidence intact', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ practices: [untracked] })]} />
    );
    const section = [...container.querySelectorAll('.issueSection')].find(
      (s) => s.querySelector('.eyebrow')?.textContent === 'Untracked work'
    );
    expect(section?.textContent).toContain('carries no ticket reference');
    expect(section?.querySelector('a')?.getAttribute('href')).toBe('https://example.invalid/pull/91');
    // And the generic practices block does not render it twice.
    expect(container.querySelector('.practices')).toBeNull();
  });

  it('renders nothing when the member has no practices', () => {
    const { container } = render(<Standup {...BASE} members={[member()]} />);
    expect(container.querySelector('.practices')).toBeNull();
  });

  it('renders nothing when the field is absent entirely (legacy report)', () => {
    const { practices: _dropped, ...legacy } = member({ practices: [untracked] });
    const { container } = render(<Standup {...BASE} members={[legacy as StandupMember]} />);
    expect(container.querySelector('.practices')).toBeNull();
  });

  it('renders an unknown rule id muted rather than throwing', () => {
    // Rule ids are engine-produced, not validated: a server that ships a new
    // rule must not break an older bundle.
    const { container } = render(
      <Standup
        {...BASE}
        members={[member({ practices: [{ ...untracked, rule: 'invented-tomorrow' }] })]}
      />
    );
    const chip = container.querySelector('.practices .chip');
    expect(chip?.textContent).toBe('Untracked work');
    expect(chip?.getAttribute('style')).toContain('var(--low)');
  });

  it('colours a known rule by its own tone', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ practices: [untracked] })]} />
    );
    expect(container.querySelector('.practiceList .chip')?.getAttribute('style')).toContain('var(--warn)');
  });

  it('keeps a coached rule in the practices block, not the untracked section', () => {
    const coached = { ...untracked, rule: 'commit-messages', title: 'Commit messages' };
    const { container } = render(
      <Standup {...BASE} members={[member({ practices: [coached] })]} />
    );
    expect(container.querySelector('.practices')?.textContent).toContain('Commit messages');
    const sections = [...container.querySelectorAll('.issueSection .eyebrow')].map((e) => e.textContent);
    expect(sections).not.toContain('Untracked work');
  });

  it('marks a repeat with the word, not colour alone', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ practices: [{ ...untracked, repeat: true }] })]} />
    );
    expect(container.querySelector('.practiceRepeat')?.textContent).toBe('again today');
  });

  it('spells the rollup count as members, and hides it when empty', () => {
    const { container, rerender } = render(
      <Standup {...BASE} practices={[{ rule: 'untracked-work', count: 2, title: 'Untracked work' }]} />
    );
    expect(container.querySelector('.practiceRollup')?.textContent).toContain('Untracked work · 2 members');

    rerender(<Standup {...BASE} />);
    expect(container.querySelector('.practiceRollup')).toBeNull();
  });

  it('has no axe violations with practices rendered', async () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[member({ practices: [untracked] })]}
        practices={[{ rule: 'untracked-work', count: 1, title: 'Untracked work' }]}
      />
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('Standup issue card header', () => {
  const ticketing = (rows: EvidenceItem[], over: Partial<StandupMember> = {}): StandupMember =>
    member({
      counts: [rows.length, 0, 0],
      categories: [{ label: 'Ticketing', items: [], links: [], evidence: rows }],
      ...over,
    });

  it('uses the first tracker-keyed ticket as the issue key, linked', () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[
          ticketing([
            // A PR number is not a tracker key — the header must skip it.
            evidence({ kind: 'pr', key: '#91' }),
            evidence({ kind: 'issue', key: 'YB-12', url: 'https://x/browse/YB-12', status: 'In Progress' }),
          ]),
        ]}
      />
    );
    const key = container.querySelector('.issueKey');
    expect(key?.textContent).toBe('YB-12');
    expect(key?.getAttribute('href')).toBe('https://x/browse/YB-12');
    expect(key?.getAttribute('rel')).toContain('noreferrer');
  });

  it('renders no issue key when the member touched no tracker ticket, rather than inventing one', () => {
    const { container } = render(<Standup {...BASE} members={[member()]} />);
    expect(container.querySelector('.issueKey')).toBeNull();
    // The lozenge still anchors the header.
    expect(container.querySelector('.issueHead .lozenge')).not.toBeNull();
  });

  it('derives the lozenge: all-done reads Done, otherwise In Progress, no activity reads the honest words', () => {
    const lozenge = (container: Element) => container.querySelector('.issueHead .lozenge')?.textContent;

    const done = ticketing([evidence({ kind: 'issue', key: 'YB-1', status: 'Done' })]);
    expect(lozenge(render(<Standup {...BASE} members={[done]} />).container)).toBe('Done');

    const doing = ticketing([evidence({ kind: 'issue', key: 'YB-1', status: 'In Progress' })]);
    expect(lozenge(render(<Standup {...BASE} members={[doing]} />).container)).toBe('In Progress');

    // Activity with no ticketing evidence at all is still in progress.
    const codeOnly = member({ counts: [0, 2, 0] });
    expect(lozenge(render(<Standup {...BASE} members={[codeOnly]} />).container)).toBe('In Progress');

    expect(lozenge(render(<Standup {...BASE} members={[member()]} />).container)).toBe('No activity');
  });

  it('renders ticket rows as a feed: no kind chip, status as a lozenge', () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[ticketing([evidence({ kind: 'issue', key: 'YB-1', status: 'In Progress' })])]}
      />
    );
    const row = container.querySelector('.evidenceFeedRow');
    expect(row).not.toBeNull();
    expect(row?.querySelector('.chip')).toBeNull();
    const status = row?.querySelector('.evidenceStatus');
    expect(status?.textContent).toBe('In Progress');
    expect(status?.className).toContain('lozengeProgress');
  });

  it('has no axe violations with the full issue card rendered', async () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[
          ticketing([evidence({ kind: 'issue', key: 'YB-1', status: 'In Progress' })], {
            counts: [1, 1, 1],
            blockers: [{ s: 'waiting on infra' }],
            outlook: [{ s: 'Likely to wrap up YB-1.' }],
            practices: [
              {
                rule: 'untracked-work',
                title: 'Untracked work',
                detail: [{ s: 'PR #91 carries no ticket reference.' }],
                evidence: [],
              },
            ],
          }),
        ]}
      />
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});

/**
 * The one place an export talks back.
 *
 * Every other bundle is inert by policy (`connect-src 'none'`), so these
 * controls must appear only when the payload says a share server is behind the
 * page — a button that silently does nothing is worse than no button.
 */
describe('Standup — correcting a practice signal', () => {
  const untracked = {
    rule: 'untracked-work',
    title: 'Untracked work',
    detail: [{ s: 'PR #91 carries no ticket reference.' }],
    evidence: [] as Array<[string, string]>,
  };

  function withPractice(correctable: boolean) {
    return render(
      <Standup {...BASE} members={[member({ practices: [untracked] })]} correctable={correctable} />
    );
  }

  function mockFetch(body: unknown, ok = true) {
    const spy = vi.fn().mockResolvedValue({
      ok,
      json: async () => body,
    });
    vi.stubGlobal('fetch', spy);
    return spy;
  }

  /** The [url, init] of the nth fetch, asserted present so strict mode is happy. */
  function call(spy: ReturnType<typeof vi.fn>, index = 0): [string, { body: string }] {
    const args = spy.mock.calls[index];
    expect(args).toBeDefined();
    return args as [string, { body: string }];
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('offers no controls on a written export', () => {
    const { container } = withPractice(false);
    expect(container.querySelector('.practiceVotes')).toBeNull();
  });

  it('offers no controls when the flag is absent entirely', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ practices: [untracked] })]} />
    );
    expect(container.querySelector('.practiceVotes')).toBeNull();
  });

  it('offers both answers on a correctable share', () => {
    const { container } = withPractice(true);
    expect(container.querySelector('.practiceVotes')?.textContent).toContain('Yes');
    expect(container.querySelector('.practiceVotes')?.textContent).toContain('No, and hide it');
  });

  it('sends a thumbs-up immediately, with no note', async () => {
    const spy = mockFetch({ ok: true, applied: true, reason: '' });
    const { container } = withPractice(true);
    fireEvent.click(screen.getByText(/Yes/));
    await screen.findByText(/confirmed/);
    const body = JSON.parse(call(spy)[1].body);
    expect(body).toEqual({
      member: 'Ada Lovelace',
      rule: 'untracked-work',
      verdict: 'up',
      note: '',
    });
    // The signal was right, so it stays on the page.
    expect(container.querySelector('.practice')?.textContent).toContain('Untracked work');
  });

  it('asks why before sending a thumbs-down, then hides the signal', async () => {
    // One request, not two: the first call removes the signal from the run, so
    // a follow-up carrying the note would find nothing to attach it to.
    const spy = mockFetch({ ok: true, applied: true, reason: '' });
    const { container } = withPractice(true);

    fireEvent.click(screen.getByText(/No, and hide it/));
    expect(spy).not.toHaveBeenCalled();

    const input = container.querySelector('.practiceInput') as HTMLInputElement;
    fireEvent.input(input, { target: { value: 'that PR is the spike ticket' } });
    fireEvent.click(screen.getByText('Send'));

    await screen.findByText(/Hidden/);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(JSON.parse(call(spy)[1].body).note).toBe('that PR is the spike ticket');
    expect(container.querySelector('.practice')?.textContent).not.toContain('carries no ticket');
  });

  it('sends an empty note when the reader skips the reason', async () => {
    const spy = mockFetch({ ok: true, applied: true, reason: '' });
    withPractice(true);
    fireEvent.click(screen.getByText(/No, and hide it/));
    fireEvent.click(screen.getByText('Send'));
    await screen.findByText(/Hidden/);
    expect(JSON.parse(call(spy)[1].body).note).toBe('');
  });

  it('lets the reader back out of a thumbs-down', () => {
    const spy = mockFetch({ ok: true, applied: true, reason: '' });
    const { container } = withPractice(true);
    fireEvent.click(screen.getByText(/No, and hide it/));
    fireEvent.click(screen.getByText('Cancel'));
    expect(container.querySelector('.practiceInput')).toBeNull();
    expect(container.querySelector('.practiceVotes')).not.toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it('carries the gate token so the vote authenticates', () => {
    const spy = mockFetch({ ok: true, applied: true, reason: '' });
    const search = window.location.search;
    window.history.replaceState({}, '', '/?token=abc123');
    try {
      withPractice(true);
      fireEvent.click(screen.getByText(/Yes/));
      expect(call(spy)[0]).toContain('token=abc123');
    } finally {
      window.history.replaceState({}, '', search || '/');
    }
  });

  it('says so when someone else answered first', async () => {
    // Two people reading the same page is an ordinary race, not a failure —
    // the signal must not silently disappear as though this reader removed it.
    mockFetch({ ok: true, applied: false, reason: 'that signal has already been answered' });
    const { container } = withPractice(true);
    fireEvent.click(screen.getByText(/Yes/));
    await screen.findByText(/already been answered/);
    expect(container.querySelector('.practice')?.textContent).toContain('Untracked work');
  });

  it('reports a dropped tunnel as unreachable rather than as a refusal', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')));
    withPractice(true);
    fireEvent.click(screen.getByText(/Yes/));
    await screen.findByText(/stopped sharing/);
  });

  it('has no axe violations with the controls rendered', async () => {
    const { container } = withPractice(true);
    expect(await axe(container)).toHaveNoViolations();
  });
});

/* One member with a story, its subtask, and code that names (or doesn't name)
 * the story — the fixture every story-view test below starts from. */
function storiedMember(over: Partial<StandupMember> = {}): StandupMember {
  const story = evidence({
    kind: 'issue', key: 'YB-1', title: 'SSO login flow', url: 'https://j/browse/YB-1',
    status: 'In Progress', type: 'Story', repo: '',
  });
  const subtask = evidence({
    kind: 'issue', key: 'YB-3', title: 'SSO error states', url: 'https://j/browse/YB-3',
    status: 'Done', type: 'Sub-task', parent: 'YB-1', subtask: true, repo: '',
  });
  const referencedPr = evidence({
    kind: 'pr', key: '#91', title: 'YB-1 enable SSO', url: 'https://g/pr/91', tickets: ['YB-1'],
  });
  const looseCommit = evidence();
  return member({
    counts: [2, 2, 0],
    categories: [
      { label: 'Ticketing', items: [], links: [], evidence: [story, subtask] },
      { label: 'Code', items: [[{ s: 'Merged the login fix.' }]], links: [], evidence: [referencedPr, looseCommit] },
    ],
    ...over,
  });
}

describe('Standup story groups', () => {
  it('nests a subtask under its story, each on its own line', () => {
    const { container } = render(<Standup {...BASE} members={[storiedMember()]} />);
    const card = container.querySelector('.storyCard');
    expect(card?.textContent).toContain('YB-1');
    const subtaskRows = card?.querySelectorAll('.storySubtasks > .evidenceRow');
    expect(subtaskRows).toHaveLength(1);
    // Its own <li>, never text appended to the story's line.
    expect(subtaskRows?.[0]?.textContent).toContain('SSO error states');
    expect(card?.querySelector('.storySubtasks')?.getAttribute('aria-label')).toBe('Subtasks of YB-1');
  });

  it('files code that names the story inside its group, and only that code', () => {
    const { container } = render(<Standup {...BASE} members={[storiedMember()]} />);
    const linked = container.querySelector('.storyLinkedWork');
    expect(linked?.textContent).toContain('#91');
    // The unreferenced commit stays in the plain Code section below.
    const codeSection = [...container.querySelectorAll('.issueSection')].find(
      (s) => s.querySelector('.eyebrow')?.textContent === 'Code',
    );
    expect(codeSection?.textContent).toContain('78e4201d');
    expect(codeSection?.textContent).not.toContain('#91');
  });

  it('labels the section Stories only when there is hierarchy to draw', () => {
    const { container } = render(<Standup {...BASE} members={[storiedMember()]} />);
    const labels = [...container.querySelectorAll('.issueSection .eyebrow')].map((e) => e.textContent);
    expect(labels).toContain('Stories');
    expect(labels).not.toContain('Ticket status changes');
  });

  it('keeps the flat ticket feed for payloads without hierarchy', () => {
    // All-default fields — exactly what a legacy report or keyless board sends.
    const flat = member({
      counts: [1, 0, 0],
      categories: [{ label: 'Ticketing', items: [], links: [], evidence: [
        evidence({ kind: 'issue', key: 'YB-9', title: 'Old row', url: 'https://j/browse/YB-9', status: 'Done', repo: '' }),
      ] }],
    });
    const { container } = render(<Standup {...BASE} members={[flat]} />);
    const labels = [...container.querySelectorAll('.issueSection .eyebrow')].map((e) => e.textContent);
    expect(labels).toContain('Ticket status changes');
    expect(container.querySelector('.storyCard')).toBeNull();
  });

  it('promotes an orphan subtask to its own group and names its parent in words', () => {
    const orphan = member({
      counts: [1, 0, 0],
      categories: [{ label: 'Ticketing', items: [], links: [], evidence: [
        evidence({
          kind: 'issue', key: 'YB-3', title: 'SSO error states', url: 'https://j/browse/YB-3',
          status: 'Done', type: 'Sub-task', parent: 'YB-1', subtask: true, repo: '',
        }),
      ] }],
    });
    const { container } = render(<Standup {...BASE} members={[orphan]} />);
    const card = container.querySelector('.storyCard');
    expect(card?.textContent).toContain('YB-3');
    const note = card?.querySelector('.storyParentNote');
    expect(note?.textContent).toBe('under YB-1');
    // Words, not a minted link — the parent row is not in the evidence.
    expect(note?.querySelector('a')).toBeNull();
  });

  it('never nests a story under its epic', () => {
    const epic = evidence({ kind: 'issue', key: 'YB-100', title: 'Auth epic', url: 'https://j/browse/YB-100', type: 'Epic', repo: '' });
    // A team-managed Jira Story carries its epic in `parent` with subtask false.
    const story = evidence({
      kind: 'issue', key: 'YB-1', title: 'SSO login flow', url: 'https://j/browse/YB-1',
      type: 'Story', parent: 'YB-100', repo: '',
    });
    const { groups } = groupStories([epic, story], [], []);
    expect(groups.map((g) => g.story.key)).toEqual(['YB-100', 'YB-1']);
    expect(groups.every((g) => g.subtasks.length === 0)).toBe(true);
  });

  it('attaches a change naming a subtask to that subtask\'s story group', () => {
    const story = evidence({ kind: 'issue', key: 'YB-1', title: 'SSO', url: 'https://j/1', type: 'Story', repo: '' });
    const subtask = evidence({ kind: 'issue', key: 'YB-3', title: 'Errors', url: 'https://j/3', type: 'Sub-task', parent: 'YB-1', subtask: true, repo: '' });
    const pr = evidence({ kind: 'pr', key: '#7', title: 'YB-3 handle errors', url: 'https://g/7', tickets: ['YB-3'] });
    const { groups, looseCode } = groupStories([story, subtask], [pr], []);
    expect(groups[0]?.code.map((c) => c.key)).toEqual(['#7']);
    expect(looseCode).toEqual([]);
  });

  it('folds story groups past the first three behind an accessible toggle', () => {
    const rows = [1, 2, 3, 4, 5].map((n) =>
      evidence({ kind: 'issue', key: `YB-${n}`, title: `Story ${n}`, url: `https://j/${n}`, type: 'Story', repo: '' }),
    );
    // One subtask so the story view engages at all.
    rows.push(evidence({ kind: 'issue', key: 'YB-9', title: 'Sub', url: 'https://j/9', type: 'Sub-task', parent: 'YB-1', subtask: true, repo: '' }));
    const m = member({ counts: [6, 0, 0], categories: [{ label: 'Ticketing', items: [], links: [], evidence: rows }] });
    const { container } = render(<Standup {...BASE} members={[m]} />);
    expect(container.querySelectorAll('.storyList:not([hidden]) .storyCard')).toHaveLength(3);
    const toggle = screen.getByRole('button', { name: '+ 2 more' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);
    expect(container.querySelectorAll('.storyList .storyCard')).toHaveLength(5);
  });

  it('headlines the story, not a fresher subtask', () => {
    const { container } = render(<Standup {...BASE} members={[storiedMember()]} />);
    // Subtask YB-3 sorts fresher, but the header names the unit of work.
    expect(container.querySelector('.issueKey')?.textContent).toBe('YB-1');
  });

  it('headlines an Azure DevOps work item — kind decides, not key shape', () => {
    // An AzDO key is '#123', which is also how a PR number is spelled; the
    // header filters on kind so AzDO teams get their issue key too.
    const azdo = member({
      counts: [1, 0, 0],
      categories: [
        {
          label: 'Ticketing',
          items: [],
          links: [],
          evidence: [
            evidence({ kind: 'work_item', key: '#7', title: 'Build API', url: 'https://a/wi/7', status: 'Active', repo: '' }),
          ],
        },
      ],
    });
    const { container } = render(<Standup {...BASE} members={[azdo]} />);
    expect(container.querySelector('.issueKey')?.textContent).toBe('#7');
  });

  it('drops the Code section entirely when the stories claimed every row and there is no prose', () => {
    const m = storiedMember();
    const code = m.categories.find((c) => c.label === 'Code');
    if (code) {
      code.items = [];
      code.evidence = code.evidence.filter((row) => row.tickets.length); // only the claimed PR remains
    }
    const { container } = render(<Standup {...BASE} members={[m]} />);
    // Section headings only — the story group's own little "Code" eyebrow
    // (inside .storyLinkedWork) is exactly where the PR should still render.
    const labels = [...container.querySelectorAll('.categoryHead .eyebrow')].map((e) => e.textContent);
    expect(container.querySelector('.storyLinkedWork')?.textContent).toContain('#91');
    expect(labels).not.toContain('Code');
  });

  it('has no axe violations with story groups rendered', async () => {
    const { container } = render(<Standup {...BASE} members={[storiedMember()]} />);
    expect(await axe(container)).toHaveNoViolations();
  });
});
