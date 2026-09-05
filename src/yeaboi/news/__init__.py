"""The front page: the desktop home's paper, built from public headline feeds.

A curated registry of outlets (``sources``) is fetched on a thread pool
(``fetch``), parsed into one item shape (``parse``), tagged with a topic and a
persona duck (``topics``), and laid out as a paper (``paper``). ``desk`` owns
the disk cache and the stale-while-revalidate refresh the app route answers
from. No LLM call anywhere; the yeaboi column always works offline from the
bundled changelog (``local``).
"""

from yeaboi.news.desk import NewsDesk
from yeaboi.news.paper import Paper, Section, SourceStatus, build_paper
from yeaboi.news.parse import NewsItem
from yeaboi.news.sources import SOURCES, NewsSource

__all__ = [
    "SOURCES",
    "NewsDesk",
    "NewsItem",
    "NewsSource",
    "Paper",
    "Section",
    "SourceStatus",
    "build_paper",
]
