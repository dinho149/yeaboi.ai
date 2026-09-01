"""Prompt construction for the Daily Standup summary.

One LLM call turns raw activity + sprint context into (a) a concise per-member
update for EVERY team member, derived from their tracked activity, and (b) a
team-level narrative. A member's typed self-report is passed as supporting
context for their entry — it enriches the analysis (extra intent, blockers)
but the summary must stay grounded in the listed activity, so the user still
learns what their activity shows even when they typed an update themselves.

Uses the ARC framework (Ask · Requirements · Context) like every other prompt in
this package.

# See docs: "Prompt Construction" — ARC framework, chain-of-thought, JSON output
"""

from __future__ import annotations

import json


def get_standup_summary_prompt(
    *,
    sprint_name: str,
    sprint_day: int,
    sprint_total_days: int,
    confidence_label: str,
    confidence_rationale: str,
    members: list[dict],
    activity_counts: list[tuple[str, int]],
    production: list[dict] | None = None,
    production_window: str = "",
) -> str:
    """Build the standup-summary prompt.

    Args:
        members: [{"name": str,
            "ticketing_activity": [ {kind,title,status,source}, ... ],
            "code_activity": [ {kind,title,status,source,repository}, ... ],
            "documentation_activity": [ {kind,title,status,source,repository}, ... ],
            "in_progress": [ {kind,title,status,source}, ... ],
            "self_report": str, "coverage": dict,
            "yesterday": {"summary","blockers","outlook","corrected"?,"corrections"?} | {},
            "blocker_signals": [str, ...]}] — one entry per team member.
            The three activity lists are already classified; "in_progress" holds tickets
            currently assigned to them and in progress (possibly untouched in
            the window); "self_report" is their own typed update ("" when they
            didn't type one), used as supporting context, never as a
            replacement for the activity analysis; "yesterday" is the member's
            entry from the previous standup ({} when there is none) for the
            day-over-day progress note, optionally carrying "corrections" —
            work stated in the last standup MEETING that the last report missed
            (standup/transcript_review.py); "blocker_signals" are deterministic
            blocker evidence strings from insights.detect_blocker_signals that
            MUST be reflected in the member's 'blockers'.
        activity_counts: (source, count) pairs for the "what we looked at" line.
        production: rolled-up ops signals — [{kind, source, count, resolved,
            worst_severity, services, examples}] — from the connected
            observability/incident/error vendors, over their own wider window.
            A TOP-LEVEL argument, never folded into a member: nothing here is
            attributable to a person, and an alert firing is not anyone's work.
            None or empty adds NOTHING to the prompt — with no ops vendor
            connected the bytes are identical to before this existed.
        production_window: how far back `production` looked, e.g. "the last 14
            days", so the model never implies it covers the standup's own day.
    """
    # --- Context block: everything the model needs to reason over ------------
    counts_str = ", ".join(f"{src}: {n}" for src, n in activity_counts) or "no activity sources reported"
    members_json = json.dumps(members, ensure_ascii=False, indent=2)

    # Ops enters as its own block or not at all. An empty list adds NOTHING:
    # told about a section that is empty, models reliably narrate the absence
    # ("no production issues were reported"), which is the nag this whole layer
    # exists to avoid — and a standup with no ops vendor connected must be
    # byte-identical to one from before ops existed.
    production_rows = production or []
    production_context = ""
    production_rule = ""
    if production_rows:
        window = production_window or "the ops window"
        production_context = (
            f"- PRODUCTION over {window} (team-wide, NOT attributable to anyone):\n"
            f"{json.dumps(production_rows, ensure_ascii=False, indent=2)}\n"
        )
        production_rule = (
            "- PRODUCTION lists incidents, alerts and error spikes from the connected monitoring "
            f"tools over {window} — a WIDER window than this standup covers, and team-wide: never "
            "attribute any of it to a person, never put it in anyone's 'summary', 'blockers' or "
            "'outlook', and never treat it as their work. Mention it in 'team_summary' ONLY when it "
            "bears on delivery, in at most one clause, naming the count and the service. Say nothing "
            "about production being quiet, calm or incident-free: silence is the correct output when "
            "there is nothing there, and only the listed sources exist — do not name a tool that is "
            "not in the list.\n"
        )

    # ARC: Ask
    ask = (
        "You are an experienced Scrum Master writing the notes for today's daily standup. "
        "Summarize what each team member did since the last standup, and write a short "
        "team-level progress narrative."
    )

    # ARC: Requirements
    requirements = (
        "Requirements:\n"
        "- For EACH person in MEMBERS, write 'summary' as 2-4 TERSE CLAUSES separated by "
        "semicolons, each starting with a verb and at most 10 words (e.g. 'Closed PSOT-14; "
        "shipped PSOT-9 to Done; continuing PSOT-21'). They render as bullets, one per clause, "
        "covering the shape of the day across ticketing, code, documentation, in-progress, and "
        "self-reported evidence. Do not enumerate every item — the category summaries below "
        "carry the detail. Mention each ticket key in AT MOST ONE clause: 'Edited PSOT-14' and "
        "'continuing PSOT-14' is the same fact twice — pick the clause that says more. "
        "Do not invent work that isn't in the data.\n"
        "- In every summary field, refer to tickets by key only (e.g. 'PSOT-14'); NEVER restate a "
        "ticket's title text — titles are rendered alongside as evidence, so repeating them is "
        "pure noise.\n"
        "- Write a separate one-sentence 'ticketing_summary' grounded only in 'ticketing_activity' "
        "and 'in_progress'. Describe ticket movement and continuing work accurately — the state and "
        "direction of the work, not a key-by-key enumeration; the items render alongside it as "
        "evidence, so a sentence that just lists their keys says nothing the reader can't see.\n"
        "- Also write a separate one-sentence 'code_summary' grounded only in 'code_activity'. "
        "Describe concrete outcomes from commits, pull requests, and reviews; never score productivity "
        "or imply that event volume measures effort. If it is empty, use exactly "
        "'No code activity detected in the selected repositories.'\n"
        "- Write a separate one-sentence 'documentation_summary' grounded only in "
        "'documentation_activity'. This includes Confluence/Notion pages and repository documentation changes.\n"
        "- When a category has no evidence, copy the explicit empty-state wording implied by its "
        "'coverage' value: do not claim no activity when the source was unconfigured or failed.\n"
        "- When a person has a non-empty 'self_report', treat it as supporting context: "
        "cross-reference it with their activity, still describe what their activity shows, and fold in "
        "anything the self-report adds (intent, progress, blockers). Do NOT simply repeat the "
        "self-report — their own words are shown separately.\n"
        "- Item 'kind' tells you what the person actually did — phrase it accordingly: 'commit'/'pr' "
        "(wrote/shipped code), 'update' (moved/edited a ticket, e.g. 'moved X to In Review'), "
        "'comment' (engaged in a discussion), 'page'/'page-created' (wrote documentation), "
        "'issue'/'work_item' (a ticket assigned to them was updated).\n"
        "- 'in_progress' lists tickets currently assigned to the person. Distinguish completed vs "
        "ongoing work: fold in-progress tickets into the summary as what they are (still) working on. "
        "The words 'continuing'/'carrying' are RESERVED for 'in_progress' items: a generic 'update' "
        "or 'comment' on a ticket is an edit (grooming, triage, discussion) — describe it as such, "
        "never as work the person is continuing or carrying.\n"
        "- If a person has tracked activity but no self_report, infer their overview from that activity alone. "
        "If a person has NO fresh activity and NO self_report but has 'in_progress' items, summarize them as "
        'continuing work on those tickets (e.g. "Continuing work on X") — never say \'No activity '
        "detected' for them. Only when all three are empty use 'No activity detected.' as the summary.\n"
        "- A member's 'yesterday' object is their entry from the previous standup — comparison "
        "context, not evidence of new work. When it is non-empty, write a one-sentence "
        "'progress_note' relating today's evidence to it: what continued, what got finished, what "
        "appears stalled (e.g. 'Wrapped up the login work from yesterday; PSOT-14 is still in "
        "review.'). When 'yesterday' is empty, 'progress_note' MUST be an empty string.\n"
        "- A 'yesterday' object may carry a 'corrected' list naming fields the TEAM edited on that "
        "previous standup by hand. Those fields are what the team says is true; the text beside them "
        "is already their correction, not the original. Treat that as a signal you got something "
        "wrong about this person before and be correspondingly careful — do not restate the version "
        "they replaced, and do not describe the correction itself as new activity.\n"
        "- A member's 'yesterday.corrections' are work they stated in the last standup MEETING that "
        "the last report missed. Treat them as ESTABLISHED FACT about yesterday when writing "
        "'progress_note' — never as new activity today, and never repeat them in 'summary'.\n"
        "- Write a one-sentence 'outlook' predicting the member's likely focus for the day ahead, "
        "grounded ONLY in their in-progress tickets, open pull requests, and self-report. Phrase it "
        "as an expectation ('Likely to continue …'), never as fact. Use an empty string when there "
        "is nothing concrete to predict from.\n"
        "- 'blocker_signals' are verified signals detected in the data (blocked ticket statuses, "
        "PRs unmerged across standups, heavy ticket discussions). Reflect EVERY provided signal in "
        "'blockers' — you may rephrase and merge them, and add blockers you infer yourself (e.g. a "
        "PR stuck in review, a ticket flipped back to 'Blocked') — but never omit or soften a "
        "provided signal. With no signals and nothing suggesting a blocker, use an empty string.\n"
        "- Write 'team_summary' as 2-4 sentences: overall momentum, notable progress, and any risks "
        "— name members with blockers explicitly. "
        f"Factor in the sprint status (currently '{confidence_label}': {confidence_rationale}) "
        "but do NOT restate it — it is displayed beside the summary; write only what the team did "
        "and what is at risk.\n"
        f"{production_rule}"
        "- Be concrete and concise. No filler, no preamble.\n"
        "- Return ONLY a JSON object, no markdown fences, of the exact shape:\n"
        '  {"members": [{"name": "...", "summary": "...", "ticketing_summary": "...", '
        '"code_summary": "...", "documentation_summary": "...", "blockers": "...", '
        '"progress_note": "...", "outlook": "..."}], "team_summary": "..."}'
    )

    # ARC: Context
    context = (
        "Context:\n"
        f"- Sprint: {sprint_name or 'unknown'} — day {sprint_day} of {sprint_total_days}.\n"
        f"- Activity sources examined ({counts_str}).\n"
        f"{production_context}"
        f"- MEMBERS (one summary each):\n{members_json}"
    )

    return f"{ask}\n\n{requirements}\n\n{context}"
