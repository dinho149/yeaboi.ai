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


def get_advisor_insights_prompt(
    *,
    period_start: str,
    period_end: str,
    total_cost_usd: float,
    recoverable_usd: float,
    line_items: list[tuple[str, int, float, float, bool]],
    residency_median: int,
    gaps_over_5m: int,
    volatile_files: list[tuple[str, int]],
    alignment_score: int,
) -> str:
    """Build the advisor-insights prompt.

    Args:
        line_items: (label, calls, est_usd, share_of_read_bytes, recoverable)
            rows for every audited waste mechanism with any volume.
        volatile_files: (file name, finding count) rows, worst first.
    """
    item_lines = (
        "\n".join(
            f"- {label}: {calls} call(s), ~${usd:,.2f} ({share:.0%} of Read bytes)"
            + ("" if recoverable else " [context only — not counted as recoverable]")
            for label, calls, usd, share, recoverable in line_items
        )
        or "(no measurable Read waste)"
    )
    volatile_lines = (
        "\n".join(f"- {name}: {count} volatile-shaped token(s)" for name, count in volatile_files)
        or "(no volatile content found in prompt-prefix files)"
    )

    ask = (
        "You are advising an engineering lead on their team's AI-agent spend efficiency "
        f"between {period_start} and {period_end}. Estimated spend: ${total_cost_usd:,.2f}, of which "
        f"~${recoverable_usd:,.2f} looks mechanically recoverable. Write short, concrete insights about "
        "where the waste comes from and recommendations to reclaim it."
    )
    requirements = (
        "Requirements:\n"
        "- Ground every statement in the figures below — never invent or recompute numbers.\n"
        "- Insights describe what IS (waste patterns, cache behaviour, prefix churn).\n"
        "- Recommendations describe what to DO (session habits, CLAUDE.md hygiene, caching), each actionable.\n"
        "- Every figure is an estimate from local session logs — keep that framing; never promise exact savings.\n"
        "- 2-4 items per list, one sentence each. No preamble, no headings.\n"
        'Return STRICT JSON: {"insights": ["..."], "recommendations": ["..."]}'
    )
    context = (
        "Audit figures (computed locally from agent session logs; tokens ≈ bytes/4, priced at the window's "
        "blended input rate):\n"
        f"Waste by mechanism:\n{item_lines}\n"
        f"Context residency: a Read stays in context for a median of {residency_median} assistant turn(s).\n"
        f"Cache-death windows: {gaps_over_5m} gap(s) over 5 minutes between messages.\n"
        f"Prompt-prefix volatility (cache alignment score {alignment_score}/100):\n{volatile_lines}"
    )
    return f"{ask}\n\n{requirements}\n\n{context}"


def get_security_summary_prompt(
    *,
    scan_date: str,
    posture: str,
    issues: list[tuple[str, str, str, str, str, int]],
    verdict_counts: dict[str, int],
    mcp_count: int,
    sessions_scanned: int,
) -> str:
    """Build the security-summary prompt.

    Args:
        issues: (severity, category, title, pattern, verdict, signals) rows, worst first.
        verdict_counts: findings per verdict word (needs-decision, unsure, test-data, handled, info).
    """
    issue_lines = (
        "\n".join(
            f"- [{verdict}] {title} ({pattern}; {sev}/{cat}; {signals} signal(s))"
            for sev, cat, title, pattern, verdict, signals in issues
        )
        or "(none)"
    )
    counts = ", ".join(f"{n} {verdict}" for verdict, n in verdict_counts.items() if n)

    ask = (
        f"You are summarising a local AI-agent security scan from {scan_date} for the one engineer who runs "
        f"these agents (computed posture: {posture}; {sessions_scanned} session(s) scanned, {mcp_count} MCP "
        "server(s) configured). Write a short plain-language summary and prioritised recommendations."
    )
    requirements = (
        "Requirements:\n"
        "- Ground everything in the issues below — never invent an issue.\n"
        "- Each issue carries a verdict. 'needs-decision' means the command ran or a live-looking key was in a "
        "command or prompt; 'unsure' means a generic shape worth a look; 'test-data' means the text was written "
        "into or read from a test, fixture or docs file and is NOT a risk; 'handled' and 'info' need nothing.\n"
        "- Be proportionate: lead with what needs a decision; if nothing does, say so in the first sentence. "
        "Do not call test data critical, and do not tell the reader to rotate a key that only appeared in a test.\n"
        "- summary: 2-3 sentences. recommendations: max 5, only for needs-decision and unsure issues, each one "
        "concrete action (block the command family with a guard hook, rotate the key, narrow the rule).\n"
        "- These are deterministic pattern matches — call them indicators, not a security audit.\n"
        'Return STRICT JSON: {"summary": "...", "recommendations": ["..."]}'
    )
    context = f"Verdict counts: {counts or 'none'}\n\nIssues (worst first):\n{issue_lines}"
    return f"{ask}\n\n{requirements}\n\n{context}"
