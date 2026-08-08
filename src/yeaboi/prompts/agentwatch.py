"""Prompt construction for the agentwatch (Agents) family.

One factory per pipeline, each a single LLM call that returns a strict JSON
object the engine parses (parse → fallback convention). All use the ARC
framework (Ask · Requirements · Context) like every other prompt in this
package.

The audience is a team lead who pays the agent bill: the tone is concrete and
decision-oriented. Every number the prompt cites was computed deterministically
by the engine — the model writes prose *about* the aggregates and must never
restate arithmetic of its own.

# See docs: "Prompt Construction" — ARC framework, JSON output
"""

from __future__ import annotations


def get_usage_insights_prompt(
    *,
    period_start: str,
    period_end: str,
    total_cost_usd: float,
    by_model: list[tuple[str, float, int, int]],
    by_project: list[tuple[str, float, int]],
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> str:
    """Build the usage-insights prompt.

    Args:
        by_model: (model, cost_usd, input_tokens, output_tokens) rows, top first.
        by_project: (project, cost_usd, sessions) rows, top first.
    """
    model_lines = "\n".join(f"- {m}: ${c:,.2f} ({i:,} in / {o:,} out)" for m, c, i, o in by_model) or "(none)"
    project_lines = "\n".join(f"- {p}: ${c:,.2f} across {s} session(s)" for p, c, s in by_project) or "(none)"

    ask = (
        "You are advising an engineering lead on their team's AI-agent spend "
        f"between {period_start} and {period_end}. Total estimated cost: ${total_cost_usd:,.2f}. "
        "Write short, concrete insights about where the spend went and recommendations to get more value per dollar."
    )
    requirements = (
        "Requirements:\n"
        "- Ground every statement in the aggregates below — never invent or recompute numbers.\n"
        "- Insights describe what IS (patterns, concentrations, cache behaviour).\n"
        "- Recommendations describe what to DO (model choice, caching, session habits), each actionable.\n"
        "- 2-4 items per list, one sentence each. No preamble, no headings.\n"
        'Return STRICT JSON: {"insights": ["..."], "recommendations": ["..."]}'
    )
    context = (
        "Aggregates (computed locally from agent session logs — costs are estimates at public rates):\n"
        f"Spend by model:\n{model_lines}\n"
        f"Spend by project:\n{project_lines}\n"
        f"Prompt-cache traffic: {cache_read_tokens:,} tokens read, {cache_write_tokens:,} written."
    )
    return f"{ask}\n\n{requirements}\n\n{context}"
