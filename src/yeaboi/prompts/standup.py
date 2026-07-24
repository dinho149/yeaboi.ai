"""Prompt construction for the Daily Standup summary.

One LLM call turns raw activity + sprint context into (a) a concise per-member
update for EVERY team member, derived from their tracked activity, and (b) a
team-level narrative. A member's typed self-report is passed as supporting
context for their entry — it enriches the analysis (extra intent, blockers)
but the summary must stay grounded in the listed activity, so the user still
learns what their activity shows even when they typed an update themselves.

Uses the ARC framework (Ask · Requirements · Context) like every other prompt in
this package.

# See README: "Prompt Construction" — ARC framework, chain-of-thought, JSON output
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
) -> str:
    """Build the standup-summary prompt.

    Args:
        members: [{"name": str,
            "ticketing_activity": [ {kind,title,status,source}, ... ],
            "code_activity": [ {kind,title,status,source,repository}, ... ],
            "documentation_activity": [ {kind,title,status,source,repository}, ... ],
            "in_progress": [ {kind,title,status,source}, ... ],
            "self_report": str, "coverage": dict}] — one entry per team member.
            The three activity lists are already classified; "in_progress" holds tickets
            currently assigned to them and in progress (possibly untouched in
            the window); "self_report" is their own typed update ("" when they
            didn't type one), used as supporting context, never as a
            replacement for the activity analysis.
        activity_counts: (source, count) pairs for the "what we looked at" line.
    """
    # --- Context block: everything the model needs to reason over ------------
    counts_str = ", ".join(f"{src}: {n}" for src, n in activity_counts) or "no activity sources reported"
    members_json = json.dumps(members, ensure_ascii=False, indent=2)

    # ARC: Ask
    ask = (
        "You are an experienced Scrum Master writing the notes for today's daily standup. "
        "Summarize what each team member did since the last standup, and write a short "
        "team-level progress narrative."
    )

    # ARC: Requirements
    requirements = (
        "Requirements:\n"
        "- For EACH person in MEMBERS, write a one- to two-sentence 'summary' as a General Overview "
        "synthesizing their ticketing, code, documentation, in-progress, and self-reported evidence. "
        "Do not invent work that isn't in the data.\n"
        "- Write a separate one-sentence 'ticketing_summary' grounded only in 'ticketing_activity' "
        "and 'in_progress'. Describe ticket movement and continuing work accurately.\n"
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
        "ongoing work: fold in-progress tickets into the summary as what they are (still) working on.\n"
        "- If a person has tracked activity but no self_report, infer their overview from that activity alone. "
        "If a person has NO fresh activity and NO self_report but has 'in_progress' items, summarize them as "
        'continuing work on those tickets (e.g. "Continuing work on X") — never say \'No activity '
        "detected' for them. Only when all three are empty use 'No activity detected.' as the summary.\n"
        "- If their activity or self-report suggests a blocker (e.g. a PR stuck in review, a ticket "
        "flipped back to 'Blocked'), note it in 'blockers'; otherwise use an empty string.\n"
        "- Write 'team_summary' as 2-4 sentences: overall momentum, notable progress, and any risks. "
        f"Factor in the sprint status (currently '{confidence_label}': {confidence_rationale}).\n"
        "- Be concrete and concise. No filler, no preamble.\n"
        "- Return ONLY a JSON object, no markdown fences, of the exact shape:\n"
        '  {"members": [{"name": "...", "summary": "...", "ticketing_summary": "...", '
        '"code_summary": "...", "documentation_summary": "...", '
        '"blockers": "..."}], "team_summary": "..."}'
    )

    # ARC: Context
    context = (
        "Context:\n"
        f"- Sprint: {sprint_name or 'unknown'} — day {sprint_day} of {sprint_total_days}.\n"
        f"- Activity sources examined ({counts_str}).\n"
        f"- MEMBERS (one summary each):\n{members_json}"
    )

    return f"{ask}\n\n{requirements}\n\n{context}"
