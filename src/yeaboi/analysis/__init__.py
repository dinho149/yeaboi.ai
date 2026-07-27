"""Team-analysis mode — headless engine over the team_learning compute primitives.

Public API:
    from yeaboi.analysis import run_team_analysis, get_team_roster
"""

from yeaboi.analysis.engine import get_team_roster, get_team_roster_result, run_team_analysis

__all__ = ["get_team_roster", "get_team_roster_result", "run_team_analysis"]
