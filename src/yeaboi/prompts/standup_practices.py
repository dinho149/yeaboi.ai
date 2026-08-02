"""Prompt construction for standup practice adjudication.

One narrow question, asked once per standup about the handful of changes the
deterministic matcher could not place: *does this change belong to one of these
tickets?* Deterministic matching is blind to wording gaps — a ticket reading
"Customers can't check out" and a commit reading "Fix the cart total rounding"
share no vocabulary at all — and that is the largest remaining source of
changes being reported as untracked when they are not.

**The model can only ever remove a report.** It answers with the ids of changes
to drop, so there is no channel through which it could invent one, sharpen one,
or name a ticket in a message someone will read. That is a property of the
return shape rather than of the wording below, which is why the wording can
afford to ask for a judgement call rather than hedging.

That property is also what makes it safe to feed the team's own thumbs up/down
in as few-shot calibration. Confirmations can only make the model drop *less*
and corrections only more; neither gives it a way to accuse anyone. The notes
are user-written text, but they arrive JSON-encoded and the reply is a fixed
list of ids intersected with the batch we sent, so the worst a hostile note can
do is change which of this team's own changes get excused.

Uses the ARC framework (Ask · Requirements · Context) like every other prompt in
this package.

# See docs: "Prompt Construction" — ARC framework, chain-of-thought, JSON output
"""

from __future__ import annotations

import json


def get_practice_adjudication_prompt(cases: list[dict], corrections: list[dict] | None = None) -> str:
    """Build the adjudication prompt.

    Args:
        cases: [{"id": str, "subject": str, "branch": str, "paths": [str],
                 "candidates": [{"key","title","text"}]}]
        corrections: past verdicts this team recorded on reports of this kind —
            [{"verdict": "up"|"down", "subject": str, "note": str}] — where
            ``down`` means "you were wrong to report this" and ``up`` means "no,
            that one was genuinely untracked". Few-shot calibration, not rules:
            they describe changes that are not in this batch.

    Returns:
        The prompt string. The expected reply is
        ``{"belongs": ["<id>", ...]}`` — ids of changes that DO belong to one of
        their candidate tickets, and so should not be reported.
    """
    # ARC: Ask
    ask = (
        "You are reviewing a daily standup before it is shown to an engineering team. "
        "Each change below is about to be reported as work with no ticket behind it. "
        "For each one, decide whether it in fact belongs to one of the candidate tickets listed with it."
    )

    # ARC: Requirements
    requirements = (
        "Requirements:\n"
        "- A change belongs to a ticket when it plausibly implements, tests, documents, refactors or "
        "fixes part of what that ticket describes — including work its acceptance criteria or its "
        "definition of done imply. Documentation is a definition-of-done item on most teams: docs "
        "that describe a candidate ticket's feature belong to it.\n"
        "- Judge meaning, not vocabulary. A ticket phrased in customer terms ('customers cannot "
        "check out') and a change phrased in code terms ('fix cart total rounding') can be the same "
        "work. This is the judgement the automated check could not make, and the reason you are here.\n"
        "- Read exclusions literally. A ticket that says something is explicitly out of its scope "
        "does NOT own a change doing that thing.\n"
        "- Shared technology is not shared purpose. Two changes touching the same framework, module "
        "or file are not related unless the ticket's actual goal covers the change.\n"
        "- When genuinely unsure, LEAVE THE CHANGE OUT of your answer. Being listed is a mild nudge "
        "to link a ticket; being wrongly excused hides real scope creep from the team.\n"
        "- Do not explain, rank, or say which ticket matched. Only the ids are used.\n"
        "- Return ONLY a JSON object, no markdown fences, of the exact shape:\n"
        '  {"belongs": ["<id>", "<id>"]}\n'
        "  Use an empty list when none of them belong to their candidates."
    )
    if corrections:
        # Few-shot, and framed as calibration rather than instruction: these
        # describe OTHER changes, and a model told to "follow" them would start
        # matching on their surface features. The uncertainty bullet above stays
        # the tie-breaker, which is what keeps a bad note from silencing a rule.
        requirements += (
            "\n- TEAM FEEDBACK below is this team's own judgement on earlier reports of this kind. "
            "'down' means they told us a change like that did belong to a ticket and should not have "
            "been reported; 'up' means they confirmed it was genuinely untracked. Use it to calibrate "
            "how this team draws the line — how loosely they expect a ticket to cover related work — "
            "and not as a list of changes to match against. It describes changes that are not in this "
            "batch, and it never overrides the instruction to leave a change out when you are unsure."
        )

    # ARC: Context
    context = "Context:\n- CHANGES:\n" + json.dumps(cases, indent=2, ensure_ascii=False)
    if corrections:
        context += "\n- TEAM FEEDBACK:\n" + json.dumps(corrections, indent=2, ensure_ascii=False)

    return f"{ask}\n\n{requirements}\n\n{context}"
