/**
 * Turning an edit path into something a person can read.
 *
 * The server sends `member_updates[name=Ada%20Lovelace].blockers`, which is
 * exactly right for addressing and useless in a history panel. Formatting it
 * happens here and not on the server for the usual reason: a label is
 * presentation, and presentation does not cross the wire.
 */

/** Field names that read better as something other than themselves. */
const WORDS: Record<string, string> = {
  team_summary: 'team summary',
  confidence_rationale: 'confidence rationale',
  member_updates: 'member',
  executive_summary: 'executive summary',
  delivered_items: 'item',
  carried_action_items: 'carried action',
  areas_for_improvement: 'areas for improvement',
  activity_summary: 'sprint work',
  email_subject: 'email subject',
  email_summary: 'summary email',
  ticketing_summary: 'ticket work',
  code_summary: 'code work',
  documentation_summary: 'documentation work',
  progress_note: 'progress note',
  action_items: 'action item',
  talking_points: 'talking point',
  carried_action_item: 'carried action',
};

function word(name: string): string {
  return WORDS[name] ?? name.replace(/_/g, ' ');
}

/**
 * Render one path as a human phrase — `member Ada · blockers`.
 *
 * Deliberately lossy and deliberately not reversible: it is read by a person
 * deciding whether an edit is the one they are looking for, and `resolve` on
 * the server is what actually finds it.
 */
export function formatPath(path: string): string {
  if (!path) return 'the document';
  return path
    .split('.')
    .map((segment) => {
      const match = /^([a-z][a-z0-9_]*)(?:\[(.*)\])?$/.exec(segment);
      if (!match) return segment;
      const [, field = segment, selector] = match;
      if (selector === undefined) return word(field);
      if (selector === '-') return `new ${word(field)}`;
      if (selector.startsWith('#')) return `${word(field)} ${Number(selector.slice(1)) + 1}`;
      const value = selector.slice(selector.indexOf('=') + 1);
      // The server percent-encodes selector values; a malformed one must not
      // throw here and take the whole history panel down with it.
      try {
        return `${word(field)} ${decodeURIComponent(value)}`;
      } catch {
        return `${word(field)} ${value}`;
      }
    })
    .join(' · ');
}

/** The verb an op reads as in the history panel. */
export function formatOp(op: string): string {
  switch (op) {
    case 'set':
      return 'changed';
    case 'append':
      return 'added to';
    case 'remove':
      return 'removed from';
    case 'note':
      return 'noted on';
    case 'field':
      return 'added a field to';
    case 'revert':
      return 'reverted';
    default:
      return 'edited';
  }
}
