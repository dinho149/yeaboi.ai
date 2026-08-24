// The chat's slash commands — the desktop half of the terminal's registry.
//
// routes.json names them (and which terminal verb each one answers, which is
// what tests/unit/test_tui_parity.py checks two-way); this module gives each
// one an intent the chat page acts on. Handlers do not live here: what
// `/export` means is the page's business, and a registry that reached into a
// page would need the page to exist to be testable.
//
// Four terminal verbs are deliberately absent — /image, /paste, /voice and
// /quit. Each exists because a terminal cannot do the ordinary thing; a window
// can, so the ordinary thing is what this surface offers instead.

import registry from './routes.json';

export interface ChatCommand {
  name: string;
  /** The terminal verb this answers. */
  tui: string;
  help: string;
}

export const CHAT_COMMANDS: readonly ChatCommand[] = registry.commands;

/** What running a command asks the page to do. Args are whatever followed the verb. */
export type CommandIntent =
  | { kind: 'shortcuts' }
  | { kind: 'export' }
  | { kind: 'questions' }
  | { kind: 'summary' }
  | { kind: 'duck' }
  | { kind: 'size'; mode: 'small_project' | 'smart' }
  // A literal the intake node consumes itself — "skip", "defaults",
  // "defaults all", "edit 6". These are turns, not local actions.
  | { kind: 'send'; text: string }
  | { kind: 'unknown'; name: string };

/**
 * Parse a composer line into an intent, or null when it is not a command.
 *
 * Only a leading slash on the first line counts, the same rule the terminal's
 * /-menu uses: a slash anywhere else is prose ("http://…", "and/or").
 */
export function parseCommand(line: string): CommandIntent | null {
  if (!line.startsWith('/')) return null;
  const [verb, ...rest] = line.slice(1).trim().split(/\s+/);
  const args = rest.join(' ');
  const name = (verb ?? '').toLowerCase();
  if (!name) return null;
  if (!CHAT_COMMANDS.some((command) => command.name === name)) return { kind: 'unknown', name };
  switch (name) {
    case 'help':
      return { kind: 'shortcuts' };
    case 'export':
      return { kind: 'export' };
    case 'summary':
      return { kind: 'summary' };
    case 'duck':
      return { kind: 'duck' };
    case 'questions':
    case 'form':
      return { kind: 'questions' };
    case 'small':
      return { kind: 'size', mode: 'small_project' };
    case 'large':
      return { kind: 'size', mode: 'smart' };
    case 'skip':
      return { kind: 'send', text: 'skip' };
    case 'defaults':
      return { kind: 'send', text: 'defaults' };
    case 'finish':
      // The literal the intake node answers every remaining question with.
      return { kind: 'send', text: 'defaults all' };
    case 'edit':
      // A numbered edit is a turn the node consumes; a bare one is the panel,
      // because "what can I change?" is answered by seeing the answers.
      return /^\d+$/.test(args) ? { kind: 'send', text: `edit ${args}` } : { kind: 'questions' };
    default:
      return { kind: 'unknown', name };
  }
}

/** The commands the /-menu offers for a partial verb, in registry order. */
export function matchingCommands(line: string): ChatCommand[] {
  if (!line.startsWith('/')) return [];
  const typed = line.slice(1).split(/\s+/)[0]?.toLowerCase() ?? '';
  return CHAT_COMMANDS.filter((command) => command.name.startsWith(typed));
}

/** What an unknown /word gets told. Never a graph turn — slash input is local. */
export function unknownCommandNotice(name: string): string {
  return `/${name} isn't a command — /help lists them.`;
}
