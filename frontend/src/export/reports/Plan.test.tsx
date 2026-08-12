/**
 * The sprint-plan export.
 *
 * A plan is a *checkpoint* artifact, so the case worth pinning hardest is the
 * partial one: exported after intake, or after analysis, with everything below
 * it still empty. Then the two tone lookups that used to be CSS class names.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import type { PlanSprint, PlanStory } from '../boot';
import { Plan } from './Plan';

const EMPTY = {
  questionnaire: [] as Array<[string, string, string]>,
  analysis: null,
  capacity: null,
  epicKey: '',
  features: [],
  storyGroups: [],
  pointsByDiscipline: [] as Array<[string, number]>,
  taskGroups: [],
  sprints: [],
  velocity: 40,
  images: [],
  priorArt: [],
};

function story(over: Partial<PlanStory> = {}): PlanStory {
  return {
    id: 'S-1',
    title: 'Okta sign-in',
    text: 'As a tenant admin, I want to sign in with Okta.',
    priority: 'critical',
    discipline: 'backend',
    points: 8,
    acceptanceCriteria: [],
    dod: [],
    ...over,
  };
}

function sprint(over: Partial<PlanSprint> = {}): PlanSprint {
  return { name: 'Sprint 1', goal: 'Ship SSO', capacity: 40, used: 20, storyIds: ['S-1'], ...over };
}

function withStories(stories: PlanStory[]) {
  return { ...EMPTY, storyGroups: [{ featureId: 'F-1', featureTitle: 'Single sign-on', stories }] };
}

describe('Plan', () => {
  it('says so plainly when nothing has been generated yet', () => {
    render(<Plan {...EMPTY} />);
    expect(screen.getByText('No artifacts to export yet.').tagName).toBe('P');
  });

  it('renders a checkpoint export with only the sections that exist', () => {
    const { container } = render(<Plan {...withStories([story()])} />);
    expect([...container.querySelectorAll('section')].map((s) => s.id)).toEqual(['stories']);
  });

  it('colours a story by its priority, and leaves an unknown one neutral', () => {
    const { container, rerender } = render(<Plan {...withStories([story()])} />);
    expect((container.querySelector('.story') as HTMLElement).style.getPropertyValue('--story-tone')).toBe(
      'var(--critical)'
    );

    rerender(<Plan {...withStories([story({ priority: 'whenever' })])} />);
    expect((container.querySelector('.story') as HTMLElement).style.getPropertyValue('--story-tone')).toBe('');
  });

  it('labels the points rationale with its confidence', () => {
    const { container } = render(
      <Plan {...withStories([story({ rationale: 'Unfamiliar provider.', confidence: 'low' })])} />
    );
    expect(container.querySelector('.why .chip')?.textContent).toBe('low confidence');
  });

  it('falls back to a neutral label when the rationale has no confidence', () => {
    const { container } = render(<Plan {...withStories([story({ rationale: 'Straightforward.' })])} />);
    expect(container.querySelector('.why .chip')?.textContent).toBe('Points rationale');
  });

  it('numbers the acceptance criteria and keeps Given/When/Then legible', () => {
    const { container } = render(
      <Plan
        {...withStories([
          story({
            acceptanceCriteria: [
              { given: 'a configured tenant', when: 'they log in', then: 'they reach Okta' },
              { given: 'an unconfigured tenant', when: 'they log in', then: 'they see the form' },
            ],
          }),
        ])}
      />
    );
    const items = [...container.querySelectorAll('.criteria li')];
    expect(items).toHaveLength(2);
    expect(items[0]?.textContent).toBe(
      'AC 1Given a configured tenant When they log in Then they reach Okta'
    );
  });

  it('strikes through a DoD item the story is exempt from rather than dropping it', () => {
    // Which items a story is exempt from is a decision worth showing.
    const { container } = render(
      <Plan {...withStories([story({ dod: [['Documentation', true], ['Released via SDLC', false]] })])} />
    );
    const items = [...container.querySelectorAll('.dod li')];
    expect(items.map((li) => li.textContent)).toEqual(['✓ Documentation', '✗ Released via SDLC']);
    expect(items[0]?.className).toBe('');
    expect(items[1]?.className).toContain('exempt');
  });

  it('draws no DoD block when the story carried no pairs', () => {
    const { container } = render(<Plan {...withStories([story()])} />);
    expect(container.querySelector('.dod')).toBeNull();
  });

  it('marks a sprint that lost capacity, and says what it lost it from', () => {
    const { container } = render(<Plan {...EMPTY} sprints={[sprint({ capacity: 30 })]} velocity={40} />);
    expect(container.querySelector('.reduced')).not.toBeNull();
    expect(container.querySelector('.reducedNote')?.textContent).toBe(
      'Reduced from 40 pts (bank holidays / deductions)'
    );
  });

  it('leaves a full-capacity sprint unmarked', () => {
    const { container } = render(<Plan {...EMPTY} sprints={[sprint({ capacity: 40 })]} velocity={40} />);
    expect(container.querySelector('.reduced')).toBeNull();
    expect(container.querySelector('.reducedNote')).toBeNull();
  });

  it('warns on an over-committed sprint but not on a merely tight one', () => {
    const over = render(<Plan {...EMPTY} sprints={[sprint({ used: 50, capacity: 40 })]} />);
    expect(over.container.querySelector('.chip')?.textContent).toBe('50 / 40 pts');
    expect((over.container.querySelector('.chip') as HTMLElement).style.color).toBe('var(--danger)');

    const tight = render(<Plan {...EMPTY} sprints={[sprint({ used: 36, capacity: 40 })]} />);
    expect((tight.container.querySelector('.chip') as HTMLElement).style.color).toBe('var(--warn)');
  });

  it('puts the story sentence in its own case, not in the eyebrow', () => {
    // An eyebrow uppercases. "As a support engineer, I want…" in tracked mono
    // caps across two lines is furniture nobody reads.
    const { container } = render(
      <Plan
        {...EMPTY}
        taskGroups={[
          {
            storyId: 'S-2',
            storyText: 'As a support engineer, I want to see which tenants use SSO.',
            tasks: [{ id: 'T-1', title: 'Add the column', description: 'Render it.', label: 'Code' }],
          },
        ]}
      />
    );
    expect(container.querySelector('.taskGroup .eyebrow')?.textContent).toBe('S-2');
    expect(container.querySelector('.groupSubject')?.textContent).toBe(
      'As a support engineer, I want to see which tenants use SSO.'
    );
  });

  it('gives a task’s test plan and AI prompt their own lines', () => {
    const { container } = render(
      <Plan
        {...EMPTY}
        taskGroups={[
          {
            storyId: 'S-1',
            storyText: 'x',
            tasks: [
              {
                id: 'T-1',
                title: 'Wire it',
                description: 'Add the endpoint.',
                label: 'Code',
                testPlan: 'Round-trip against the dev tenant.',
                aiPrompt: 'Write the validator.',
              },
            ],
          },
        ]}
      />
    );
    expect([...container.querySelectorAll('.taskNote')].map((n) => n.textContent)).toEqual([
      'Test plan: Round-trip against the dev tenant.',
      'AI prompt: Write the validator.',
    ]);
  });

  it('shows the capacity split and the deductions behind it', () => {
    const { container } = render(
      <Plan
        {...EMPTY}
        capacity={{
          teamSize: 4,
          sprintWeeks: 2,
          targetSprints: 3,
          velocity: 40,
          netVelocity: 30,
          deductions: ['bank holidays: 2d', 'discovery: 5%'],
        }}
        analysis={{
          name: 'Acme',
          description: 'A portal.',
          targetState: 'Self-serve.',
          projectType: 'greenfield',
          sprintWeeks: 2,
          targetSprints: 3,
          fields: [],
        }}
      />
    );
    expect(container.querySelector('.capacity .legend')?.textContent).toBe('Net 30Deducted 10');
    expect(container.querySelector('.capacity .footnote')?.textContent).toBe(
      'Deductions — bank holidays: 2d, discovery: 5%'
    );
  });
});

describe('Plan prior art', () => {
  const repo = {
    name: 'acme/platform-auth',
    url: 'https://github.com/acme/platform-auth',
    platform: 'github',
    pitch: ['OIDC login and session refresh', 'Role mapping against the HR directory'],
    stack: ['Python', 'FastAPI'],
  };

  it('renders the accepted repositories', () => {
    const { container } = render(<Plan {...EMPTY} priorArt={[repo]} />);
    expect(container.textContent).toContain('Prior Art');
    expect(container.textContent).toContain('acme/platform-auth');
    expect(container.textContent).toContain('OIDC login and session refresh');
  });

  it('renders the stack as chips', () => {
    const { container } = render(<Plan {...EMPTY} priorArt={[repo]} />);
    expect(container.textContent).toContain('FastAPI');
  });

  it('is absent when nothing was accepted', () => {
    const { container } = render(<Plan {...EMPTY} priorArt={[]} />);
    expect(container.textContent).not.toContain('Prior Art');
  });

  it('a plan that is only prior art still renders rather than reading as empty', () => {
    const { container } = render(<Plan {...EMPTY} priorArt={[repo]} />);
    expect(container.textContent).not.toContain('No artifacts to export yet');
  });

  it('survives a repo with no pitch and no stack', () => {
    const bare = { name: 'acme/thin', url: '', platform: '', pitch: [], stack: [] };
    const { container } = render(<Plan {...EMPTY} priorArt={[bare]} />);
    expect(container.textContent).toContain('acme/thin');
  });
});
