"""The keyword classifier and the persona it names (src/yeaboi/news/topics.py)."""

from __future__ import annotations

import pytest

from yeaboi.news import topics
from yeaboi.news.parse import NewsItem
from yeaboi.news.sources import NewsSource
from yeaboi.news.topics import Topic


class TestClassify:
    @pytest.mark.parametrize(
        ("title", "topic"),
        [
            ("A prompt injection attack on browsing agents", Topic.SECURITY),
            ("Startup raises $200M at a $2B valuation", Topic.POLICY),
            ("Nvidia unveils a new GPU for datacenters", Topic.COMPUTE),
            ("Text-to-speech gets a new voice", Topic.MEDIA),
            ("Sora makes a short film from one prompt", Topic.MEDIA),
            ("Gemini 3 preview ships to developers", Topic.MODELS),
            ("Gemini 3 launches today", Topic.MODELS),
            ("A benchmark for long-horizon reasoning", Topic.RESEARCH),
            ("An open source coding agent for the terminal", Topic.TOOLING),
            ("How to run a retro that people like", Topic.HOWTO),
            ("Proactive cyber defense for governments", Topic.SECURITY),
            ("A $2 trillion IPO puts trustees in the spotlight", Topic.POLICY),
            ("WeatherNext 3, our most accurate weather forecaster", Topic.RESEARCH),
            ("Tomorrow looks fine", Topic.GENERAL),
        ],
    )
    def test_first_matching_row_wins(self, title, topic):
        assert topics.classify(title) is topic

    def test_title_beats_summary(self):
        assert topics.classify("A tutorial for beginners", "covers prompt injection") is Topic.HOWTO

    def test_summary_is_read_when_the_title_says_nothing(self):
        assert topics.classify("Something happened", "the lab released new model weights") is Topic.MODELS

    def test_word_boundaries(self):
        # "ban" must not fire on "banana"; "api" must not fire on "rapid".
        assert topics.classify("Banana bread, rapid and tasty") is Topic.GENERAL
        assert topics.classify("GPT-6 announced") is Topic.MODELS

    def test_every_keyword_row_names_a_persona(self):
        for topic, _words in topics.KEYWORDS:
            assert topics.PERSONA_BY_TOPIC[topic] in topics.PERSONAS

    def test_general_has_no_keywords(self):
        assert Topic.GENERAL not in dict(topics.KEYWORDS)


class TestPersonaFor:
    @pytest.mark.parametrize(
        ("column", "persona"),
        [("yeaboi", "engineer"), ("ai", "wizard"), ("engineering", "engineer"), ("research", "teacher")],
    )
    def test_general_falls_back_to_the_column(self, column, persona):
        assert topics.persona_for(Topic.GENERAL, column) == persona

    def test_a_topic_names_its_duck(self):
        assert topics.persona_for(Topic.SECURITY, "ai") == "detective"
        assert topics.persona_for(Topic.HOWTO, "research") == "chef"

    def test_a_release_is_always_a_wizard(self):
        assert topics.persona_for(Topic.SECURITY, "yeaboi", kind="release") == "wizard"

    def test_a_video_is_a_dj_unless_it_is_about_security(self):
        assert topics.persona_for(Topic.GENERAL, "yeaboi", kind="video") == "dj"
        assert topics.persona_for(Topic.SECURITY, "yeaboi", kind="video") == "detective"

    def test_the_roster_is_the_desktops_eight(self):
        assert topics.PERSONAS == {"engineer", "teacher", "martial", "chef", "astronaut", "dj", "detective", "wizard"}
        assert set(topics.PERSONA_BY_TOPIC.values()) <= topics.PERSONAS
        assert set(topics.PERSONA_BY_COLUMN.values()) <= topics.PERSONAS


class TestTag:
    def test_tag_fills_the_engines_fields(self):
        source = NewsSource(id="ars", name="Ars Technica", url="https://a.example/feed", column="ai")
        item = NewsItem(id="x", title="A CVE in a popular SDK", url="https://a.example/x")
        tagged = topics.tag(item, source)
        assert tagged.column == "ai"
        assert tagged.source_name == "Ars Technica"
        assert tagged.topic == "security"
        assert tagged.persona == "detective"
        assert tagged.title == item.title
