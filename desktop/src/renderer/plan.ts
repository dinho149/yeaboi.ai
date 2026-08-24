// The finished plan's wire: read it back, write it out, push it somewhere.
//
// All four are MCP tools, so there is no native route here — the dispatcher
// already gives this surface the same envelope, the same engine lock and the
// same `llm_mode` the other five surfaces get.

import { type Envelope, callTool } from './api';

export interface PlanStory {
  id?: string;
  title?: string;
  story_points?: number;
  feature_id?: string;
  description?: string;
}

export interface PlanSprint {
  name?: string;
  goal?: string;
  story_ids?: string[];
  total_points?: number;
}

export interface Plan {
  session_id?: string;
  project?: { name?: string; description?: string; type?: string; tech_stack?: string[]; team_size?: string };
  features?: { id?: string; title?: string; description?: string }[];
  stories?: PlanStory[];
  tasks?: { id?: string; title?: string; story_id?: string }[];
  sprints?: PlanSprint[];
}

export const loadPlan = (sessionId: string): Promise<Envelope<Plan>> => callTool('plan_get', { session_id: sessionId });

/** Formats a plan can be written as. `prd` costs one LLM call; the rest do not. */
export const PLAN_FORMATS = [
  { key: 'markdown', label: 'Markdown', note: 'the plan as a .md file' },
  { key: 'html', label: 'HTML', note: 'a self-contained page' },
  { key: 'prd', label: 'PRD', note: 'a full requirements document — one LLM call' },
] as const;

export const exportPlan = (sessionId: string, format: string): Promise<Envelope<Record<string, unknown>>> =>
  callTool('plan_export', { session_id: sessionId, format });

/** Where a plan can be published as a page. */
export const PLAN_DESTINATIONS = [
  { key: 'notion', label: 'Notion' },
  { key: 'confluence', label: 'Confluence' },
] as const;

export const publishPlan = (
  sessionId: string,
  destination: string,
  content: string,
): Promise<Envelope<Record<string, unknown>>> =>
  callTool('plan_publish', { session_id: sessionId, destination, content });

/** Where a plan can be pushed as real tickets. */
export const PLAN_TRACKERS = [
  { key: 'jira', label: 'Jira' },
  { key: 'azdevops', label: 'Azure DevOps' },
] as const;

export const syncPlan = (
  sessionId: string,
  destination: string,
  targetSprint: string,
): Promise<Envelope<Record<string, unknown>>> =>
  callTool('plan_sync', { session_id: sessionId, destination, target_sprint: targetSprint });

/** How far a plan got — what the page shows instead of an empty frame. */
export function planCounts(plan: Plan): { epics: number; stories: number; tasks: number; sprints: number } {
  return {
    epics: plan.features?.length ?? 0,
    stories: plan.stories?.length ?? 0,
    tasks: plan.tasks?.length ?? 0,
    sprints: plan.sprints?.length ?? 0,
  };
}

export function isEmptyPlan(plan: Plan): boolean {
  const counts = planCounts(plan);
  return counts.epics + counts.stories + counts.tasks + counts.sprints === 0;
}

/** The stories a sprint names, in the sprint's own order. */
export function storiesOf(plan: Plan, sprint: PlanSprint): PlanStory[] {
  const byId = new Map((plan.stories ?? []).map((story) => [story.id ?? '', story]));
  return (sprint.story_ids ?? []).map((id) => byId.get(id)).filter((story): story is PlanStory => Boolean(story));
}

/**
 * What a finished tool call has to say.
 *
 * A sync is the one that matters: it creates real tickets on someone's board,
 * so "it worked" is not enough — the counts are what tell them whether to go
 * and look, and an idempotent re-run that creates nothing has to say so rather
 * than reading as a failure.
 */
export function outcomeMessage(envelope: Envelope<Record<string, unknown>>): string {
  if (!envelope.ok) return envelope.error?.message ?? 'That did not work.';
  const data = envelope.data ?? {};
  if (typeof data.path === 'string' && data.path) return `Saved to ${data.path}`;
  if (typeof data.url === 'string' && data.url) return `Published: ${data.url}`;
  if (data.destination) {
    const counted: [string, string, unknown][] = [
      ['story', 'stories', data.stories_created],
      ['task', 'tasks', data.tasks_created],
      ['sprint', 'sprints', data.sprints_created],
    ];
    const parts = counted
      .map(([one, many, made]) => [one, many, Object.keys((made as object) ?? {}).length] as const)
      .filter(([, , count]) => count > 0)
      .map(([one, many, count]) => `${count} ${count === 1 ? one : many}`);
    const skipped = Number(data.skipped_existing ?? 0);
    if (!parts.length) {
      return skipped ? `Nothing new — all ${skipped} items were already on the board.` : 'Nothing was created.';
    }
    return `Created ${parts.join(', ')}${skipped ? ` · ${skipped} already existed` : ''}`;
  }
  return 'Done.';
}
