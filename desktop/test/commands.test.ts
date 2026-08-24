// The chat's slash commands: what a typed line means, and what the /-menu
// offers while it is being typed.

import { describe, expect, it } from 'vitest';
import {
  CHAT_COMMANDS,
  completionFor,
  matchingCommands,
  parseCommand,
  unknownCommandNotice,
} from '../src/renderer/commands';

describe('parseCommand', () => {
  it('leaves ordinary prose alone', () => {
    expect(parseCommand('four engineers')).toBeNull();
    // A slash that is not the first character is prose, not a command.
    expect(parseCommand('we use TypeScript and/or Go')).toBeNull();
    expect(parseCommand('see https://example.com/docs')).toBeNull();
  });

  it('turns the local verbs into local actions', () => {
    expect(parseCommand('/help')).toEqual({ kind: 'shortcuts' });
    expect(parseCommand('/export')).toEqual({ kind: 'export' });
    expect(parseCommand('/summary')).toEqual({ kind: 'summary' });
    expect(parseCommand('/duck')).toEqual({ kind: 'duck' });
  });

  it('sends the literals the intake node consumes itself', () => {
    expect(parseCommand('/skip')).toEqual({ kind: 'send', text: 'skip' });
    expect(parseCommand('/defaults')).toEqual({ kind: 'send', text: 'defaults' });
    // /finish is "answer the rest with defaults" — one deterministic turn.
    expect(parseCommand('/finish')).toEqual({ kind: 'send', text: 'defaults all' });
  });

  it('opens one panel for /questions, /form and a bare /edit', () => {
    expect(parseCommand('/questions')).toEqual({ kind: 'questions' });
    expect(parseCommand('/form')).toEqual({ kind: 'questions' });
    expect(parseCommand('/edit')).toEqual({ kind: 'questions' });
  });

  it('re-asks a numbered question as a turn', () => {
    expect(parseCommand('/edit 6')).toEqual({ kind: 'send', text: 'edit 6' });
    // Anything that is not a bare number is not a question number.
    expect(parseCommand('/edit the sprint length')).toEqual({ kind: 'questions' });
  });

  it('maps the size verbs to the modes the graph knows', () => {
    expect(parseCommand('/small')).toEqual({ kind: 'size', mode: 'small_project' });
    expect(parseCommand('/large')).toEqual({ kind: 'size', mode: 'smart' });
  });

  it('answers an unknown verb locally rather than sending it', () => {
    // The invariant: slash input never reaches the model.
    expect(parseCommand('/frobnicate')).toEqual({ kind: 'unknown', name: 'frobnicate' });
    expect(unknownCommandNotice('frobnicate')).toContain('/help');
  });

  it('ignores case and surrounding space', () => {
    expect(parseCommand('/HELP')).toEqual({ kind: 'shortcuts' });
    expect(parseCommand('/edit  6  ')).toEqual({ kind: 'send', text: 'edit 6' });
  });

  it('treats a lone slash as nothing yet', () => {
    expect(parseCommand('/')).toBeNull();
  });
});

describe('matchingCommands', () => {
  it('offers everything on a bare slash', () => {
    expect(matchingCommands('/')).toHaveLength(CHAT_COMMANDS.length);
  });

  it('narrows as the verb is typed', () => {
    expect(matchingCommands('/fo').map((c) => c.name)).toEqual(['form']);
    expect(matchingCommands('/s').map((c) => c.name)).toEqual(['skip', 'summary', 'small']);
  });

  it('offers nothing for prose or an unknown verb', () => {
    expect(matchingCommands('four engineers')).toEqual([]);
    expect(matchingCommands('/zzz')).toEqual([]);
  });
});

describe('the registry', () => {
  it('every command answers a terminal verb, and has help', () => {
    for (const command of CHAT_COMMANDS) {
      expect(command.tui).toBeTruthy();
      expect(command.help.length).toBeGreaterThan(8);
    }
  });

  it('every command parses to something other than unknown', () => {
    // A name in the registry with no branch above would be offered by the menu
    // and then refused when picked.
    for (const command of CHAT_COMMANDS) {
      expect(parseCommand(`/${command.name}`)).not.toEqual({ kind: 'unknown', name: command.name });
    }
  });
});

describe('completionFor', () => {
  it('finishes a half-typed verb once only one is left', () => {
    expect(completionFor('/fo')?.name).toBe('form');
  });

  it('leaves a finished verb alone, so Enter sends it', () => {
    expect(completionFor('/form')).toBeNull();
  });

  it('never eats an argument', () => {
    // "/edit 6" is a command with an argument, not a prefix of "/edit" —
    // completing it would throw the 6 away and open the panel instead.
    expect(completionFor('/edit 6')).toBeNull();
  });

  it('does nothing while more than one command still matches', () => {
    expect(completionFor('/s')).toBeNull();
  });

  it('does nothing for prose', () => {
    expect(completionFor('four engineers')).toBeNull();
  });
});
