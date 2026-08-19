"""Prompt template for the PRD prose sections.

# See docs: "Prompt Construction" — ARC framework
#
# The PRD exporter (prd_exporter.py) builds most of the document
# deterministically from the plan artifacts; ONE LLM call writes the prose
# sections that need synthesis rather than assembly. This module holds that
# prompt: an ARC-framed request with an embedded six-key JSON schema.
"""

# The six prose sections the LLM writes. Everything else in the PRD is
# assembled deterministically from the plan artifacts by prd_exporter.py.
PRD_PROSE_KEYS: tuple[str, ...] = (
    "executive_summary",
    "mission",
    "target_users",
    "success_criteria",
    "future_considerations",
    "risks_mitigations",
)

_JSON_SCHEMA = """\
{
  "executive_summary": "string — 2-3 paragraphs: product overview, core value proposition, MVP goal",
  "mission": "string — 1-2 sentences: the product mission statement",
  "target_users": [
    {"persona": "string — the user role", "description": "string — their needs, pain points, technical comfort"}
  ],
  "success_criteria": ["string array — measurable statements of what MVP success looks like"],
  "future_considerations": ["string array — post-MVP enhancements and opportunities"],
  "risks_mitigations": [
    {"risk": "string — a concrete project risk", "mitigation": "string — a specific mitigation strategy"}
  ]
}"""


def get_prd_prose_prompt(context_digest: str, *, has_architecture: bool = False) -> str:
    """Build the single-call PRD prose prompt.

    Args:
        context_digest: Compact markdown digest of the plan (analysis fields,
            features, story one-liners, sprint goals, key intake answers) —
            the grounding for every claim.
        has_architecture: True when the plan carries an architecture decision;
            the prose may then reference it but must not restate the options
            (the deterministic Architecture section covers those).

    Returns:
        The complete prompt string.
    """
    arch_note = (
        "The plan includes an architecture decision — you may reference it, but do NOT "
        "restate the options; a dedicated section covers them.\n"
        if has_architecture
        else ""
    )
    return (
        "You are a senior product manager writing a Product Requirements Document "
        "for engineers and stakeholders.\n\n"
        "## Plan Digest\n\n"
        f"{context_digest}\n\n"
        "## Task\n\n"
        "Write ONLY the prose sections of the PRD, as a JSON object matching this exact schema:\n\n"
        f"```json\n{_JSON_SCHEMA}\n```\n\n"
        "## Rules\n\n"
        "1. Ground every claim in the plan digest above — do NOT invent scope, users, or features.\n"
        "2. Professional, clear, action-oriented tone; adapt depth to how much the digest provides.\n"
        "3. success_criteria must be measurable (numbers, observable outcomes), 4-8 items.\n"
        "4. risks_mitigations: 3-5 risks, each with a specific mitigation — never 'monitor closely'.\n"
        "5. future_considerations come from the out-of-scope items and natural next steps.\n"
        f"{arch_note}\n"
        "Return ONLY the JSON object, no other text."
    )
