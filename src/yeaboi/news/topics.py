"""Which duck reads which headline: a keyword classifier, no model.

A headline gets one topic (title first, then the summary, first match wins)
and the topic names a persona from the desktop's roster. Nothing matched means
the column's own duck.
"""

from __future__ import annotations

import re
from dataclasses import replace
from enum import Enum

from yeaboi.news.parse import NewsItem
from yeaboi.news.sources import NewsSource


class Topic(str, Enum):
    SECURITY = "security"
    POLICY = "policy"
    COMPUTE = "compute"
    MEDIA = "media"
    MODELS = "models"
    RESEARCH = "research"
    TOOLING = "tooling"
    HOWTO = "howto"
    GENERAL = "general"


# The desktop's roster (src/shared/personas.ts); a wire persona is always one of these.
PERSONAS = frozenset({"engineer", "teacher", "martial", "chef", "astronaut", "dj", "detective", "wizard"})

PERSONA_BY_TOPIC: dict[Topic, str] = {
    Topic.SECURITY: "detective",
    Topic.POLICY: "martial",
    Topic.COMPUTE: "astronaut",
    Topic.MEDIA: "dj",
    Topic.MODELS: "wizard",
    Topic.RESEARCH: "teacher",
    Topic.TOOLING: "engineer",
    Topic.HOWTO: "chef",
}

PERSONA_BY_COLUMN: dict[str, str] = {
    "yeaboi": "engineer",
    "ai": "wizard",
    "engineering": "engineer",
    "research": "teacher",
}

# Ordered: an earlier row wins over a later one on the same text.
KEYWORDS: tuple[tuple[Topic, tuple[str, ...]], ...] = (
    (
        Topic.SECURITY,
        (
            "security",
            "privacy",
            "breach",
            "vulnerability",
            "cve",
            "exploit",
            "jailbreak",
            "prompt injection",
            "malware",
            "leak",
            "phishing",
            "attack",
            "cyber",
            "scam",
            "fraud",
            "spam",
            "hack",
            "hacker",
            "hacked",
            "smuggling",
        ),
    ),
    (
        Topic.POLICY,
        (
            "funding",
            "raises",
            "valuation",
            "acquisition",
            "acquires",
            "policy",
            "regulation",
            "regulator",
            "lawsuit",
            "antitrust",
            "eu ai act",
            "ban",
            "executive order",
            "copyright",
            "ipo",
            "trillion",
            "billion",
            "investors",
            "court",
            "sues",
            "settlement",
            "government",
            "governments",
            "senate",
            "congress",
            "tariff",
            "tariffs",
        ),
    ),
    (
        Topic.COMPUTE,
        (
            "gpu",
            "tpu",
            "chip",
            "chips",
            "datacenter",
            "data center",
            "compute",
            "nvidia",
            "cluster",
            "supercomputer",
            "energy",
            "nuclear",
            "space",
            "satellite",
            "scaling",
            "hardware",
            "servers",
            "grid",
        ),
    ),
    (
        Topic.MEDIA,
        (
            "audio",
            "music",
            "voice",
            "speech",
            "podcast",
            "video",
            "film",
            "image generation",
            "text-to-",
            "sora",
            "veo",
            "tts",
            "photo",
            "photos",
            "camera",
        ),
    ),
    (
        Topic.MODELS,
        (
            "model",
            "models",
            "weights",
            "open-weight",
            "launch",
            "launches",
            "release",
            "releases",
            "gpt-",
            "claude",
            "gemini",
            "llama",
            "mistral",
            "preview",
            "ships",
        ),
    ),
    (
        Topic.RESEARCH,
        (
            "paper",
            "arxiv",
            "benchmark",
            "research",
            "study",
            "evaluation",
            "eval",
            "dataset",
            "reasoning",
            "alignment",
            "interpretability",
            "survey",
            "science",
            "scientists",
            "scientific",
            "protein",
            "weather",
            "mathematics",
        ),
    ),
    (
        Topic.TOOLING,
        (
            "agent",
            "agents",
            "agentic",
            "coding",
            "code",
            "developer",
            "developers",
            "sdk",
            "api",
            "tooling",
            "cli",
            "ide",
            "mcp",
            "open source",
            "github",
            "copilot",
            "cursor",
            "devops",
            "kubernetes",
            "rust",
            "python",
            "typescript",
            "terminal",
            "browser",
            "extension",
            "plugin",
            "npm",
        ),
    ),
    (
        Topic.HOWTO,
        ("how to", "how-to", "tutorial", "guide", "recipe", "walkthrough", "step-by-step", "tips", "lessons"),
    ),
)


def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    parts = []
    for word in words:
        escaped = re.escape(word)
        tail = r"\b" if word[-1].isalnum() else ""
        parts.append(rf"\b{escaped}{tail}")
    return re.compile("|".join(parts), re.IGNORECASE)


_PATTERNS: tuple[tuple[Topic, re.Pattern[str]], ...] = tuple((topic, _pattern(words)) for topic, words in KEYWORDS)


def classify(title: str, summary: str = "") -> Topic:
    """The first topic whose words appear in the title, then in the summary."""
    for text in (title, summary):
        if not text:
            continue
        for topic, pattern in _PATTERNS:
            if pattern.search(text):
                return topic
    return Topic.GENERAL


def persona_for(topic: Topic, column: str, kind: str = "article") -> str:
    """The duck for a headline: a release is a wizard, a video a DJ, else the topic's, else the column's."""
    if kind == "release":
        return "wizard"
    if kind == "video" and topic is not Topic.SECURITY:
        return "dj"
    return PERSONA_BY_TOPIC.get(topic) or PERSONA_BY_COLUMN.get(column, "engineer")


def tag(item: NewsItem, source: NewsSource) -> NewsItem:
    """The item with its column, source name, topic and persona filled in."""
    topic = classify(item.title, item.summary)
    return replace(
        item,
        column=source.column,
        source_name=source.name,
        topic=topic.value,
        persona=persona_for(topic, source.column, item.kind),
    )
