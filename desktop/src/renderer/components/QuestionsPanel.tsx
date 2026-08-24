// The intake at a glance: every question this run touches, its answer, and
// which ones are still owed.
//
// One panel behind three terminal affordances — `/questions` (the checklist),
// `/form` (answer them in any order) and a bare `/edit` (which one do I want to
// change?). The terminal keeps them apart because a transcript note cannot show
// a question beside its answer and a full-screen takeover cannot stay open
// while the chat runs. Neither constraint applies to a panel.
//
// Re-asking is an ordinary turn: the intake node consumes `edit N` itself.

import { useEffect, useState } from 'react';
import { type QuestionPlan, loadQuestions } from '../chat';

export interface QuestionsPanelProps {
  projectId: string;
  /** Disabled while a turn is running — a re-ask is a turn of its own. */
  busy: boolean;
  onAsk: (number: number) => void;
  onClose: () => void;
}

export function QuestionsPanel({ projectId, busy, onAsk, onClose }: QuestionsPanelProps) {
  const [plan, setPlan] = useState<QuestionPlan | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    loadQuestions(projectId).then(setPlan, (e: Error) => setError(e.message));
  }, [projectId]);

  const rows = plan?.questions ?? [];
  const owed = rows.filter((row) => row.remaining).length;

  return (
    <div class="scrim" onClick={onClose}>
      <div
        class="modal questions-panel"
        role="dialog"
        aria-modal="true"
        aria-label="The questions"
        onClick={(event) => event.stopPropagation()}
      >
        <header class="modal-head">
          <h2>The questions</h2>
          <button type="button" class="link" onClick={onClose}>
            Close
          </button>
        </header>

        {error && <p class="chat-error">{error}</p>}
        {!plan && !error && <p>Loading…</p>}

        {plan && rows.length === 0 && (
          <p class="modal-sub">
            Nothing yet — describe the project and I'll work out which questions this plan actually needs.
          </p>
        )}

        {plan && rows.length > 0 && (
          <>
            <p class="modal-sub">
              {plan.derived
                ? `${rows.length - owed} answered · ${owed} to go`
                : 'I could not work out the remaining questions — these are the answers I have.'}
            </p>
            <ul class="question-rows">
              {rows.map((row) => (
                <li key={row.number} class={row.remaining ? 'question-row owed' : 'question-row'}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      onAsk(row.number);
                      onClose();
                    }}
                    title={row.remaining ? 'Answer this one next' : 'Change this answer'}
                  >
                    <span class="question-label">
                      {row.number}. {row.label}
                    </span>
                    <span class="question-answer">
                      {row.answer || (row.skipped ? 'skipped' : 'not answered yet')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <p class="modal-foot">Pick one to answer or change it — I'll ask it next.</p>
          </>
        )}
      </div>
    </div>
  );
}
