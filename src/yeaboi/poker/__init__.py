"""Scrum Poker mode — collaborative planning poker over a LAN web page.

Teammates join from a browser (name + avatar), vote on tickets pulled from
Jira or Azure DevOps (a sprint or the backlog), the admin reveals votes and
writes the agreed story points back to the board. Follows the Retro mode
blueprint: LAN ThreadingHTTPServer + polling browser page + SQLite history.
"""

from yeaboi.poker.board import POKER_DECK, PokerBoard, board_to_report
from yeaboi.poker.engine import get_poker_perspective
from yeaboi.poker.export import export_poker
from yeaboi.poker.server import PokerServer
from yeaboi.poker.store import PokerStore
from yeaboi.poker.tickets import available_sources, demo_tickets, fetch_tickets, list_sprints, update_ticket

__all__ = [
    "POKER_DECK",
    "PokerBoard",
    "PokerServer",
    "PokerStore",
    "available_sources",
    "board_to_report",
    "demo_tickets",
    "export_poker",
    "fetch_tickets",
    "get_poker_perspective",
    "list_sprints",
    "update_ticket",
]
