"""Prompt factory for the chat-planning project-size classifier.

# See docs: "Prompt Construction" — ARC framework (Action, Requirements,
# Context) and the flipped prompt: the model answers with structured JSON,
# the caller owns every downstream decision.

One tiny classification call: given the user's opening project description,
decide whether it reads as a Small plan (a ticket or two, one quick sprint)
or a Large one (epics, multiple sprints). "unclear" is a first-class answer —
the chat falls back to asking the user a deterministic numbered question, so
the classifier never has to guess.
"""

from __future__ import annotations

_JSON_SCHEMA = """{
  "size": "small" | "large" | "unclear"
}"""


def get_size_classifier_prompt(description: str) -> str:
    """Build the size-classification prompt for a project description."""
    return f"""You are a Scrum planning assistant sizing incoming work.

Classify the following project description:
- "small" — a tiny, well-bounded change: one or two tickets, a bug fix, a tweak, \
work one person finishes within a single short sprint.
- "large" — a real project: multiple features, epics, several sprints, or a team effort.
- "unclear" — the description does not clearly indicate either. When in doubt, say "unclear"; \
do NOT guess.

Respond with ONLY valid JSON matching this schema:
{_JSON_SCHEMA}

Project description:
{description}"""
