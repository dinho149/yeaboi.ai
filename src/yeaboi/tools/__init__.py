"""Tools module — factory that returns all available tools.

# See docs: "Tools" — tool registration pattern
"""

from langchain_core.tools import BaseTool


def detect_platform(url: str) -> str | None:
    """Detect the code hosting platform from a repo URL.

    Returns one of the Q16 option strings — "GitHub", "Azure DevOps",
    "GitLab", or "Bitbucket" — or None if the URL is unrecognised.
    Used by the intake node to auto-sync Q16 when the user provides a repo URL.
    """
    from urllib.parse import urlparse

    # Parse the URL and check the hostname exactly — substring checks on the raw
    # URL string are unsafe (e.g. "github.com.evil.com" contains "github.com").
    try:
        host = urlparse(url.strip()).hostname or ""
    except ValueError:
        return None

    # Allow subdomains (e.g. raw.githubusercontent.com) but require the hostname
    # to end with the known domain, not merely contain it somewhere in the string.
    if host == "github.com" or host.endswith(".github.com"):
        return "GitHub"
    if host == "dev.azure.com" or host.endswith(".visualstudio.com"):
        return "Azure DevOps"
    if host == "gitlab.com" or host.endswith(".gitlab.com"):
        return "GitLab"
    if host == "bitbucket.org" or host.endswith(".bitbucket.org"):
        return "Bitbucket"
    return None


def get_tools() -> list[BaseTool]:
    """Return all available tools for the scrum agent.

    GitHub and Azure DevOps tools are always included — they degrade gracefully
    without a token (public repos work unauthenticated; private resources require
    GITHUB_TOKEN or AZURE_DEVOPS_TOKEN in .env).

    Why lazy import?
    Importing at module level would fail if PyGithub or azure-devops are not
    installed yet (e.g. during a fresh `uv pip install -e .` before dependencies
    resolve). Lazy import inside the function means `from yeaboi.tools import
    get_tools` always succeeds, and the ImportError surfaces only when get_tools()
    is called.
    # See docs: "Tools" — tool types, @tool decorator
    """
    from yeaboi.tools.azure_devops import (
        azdevops_create_epic,
        azdevops_create_iteration,
        azdevops_create_story,
        azdevops_fetch_active_iteration,
        azdevops_fetch_velocity,
        azdevops_list_work_items,
        azdevops_read_board,
        azdevops_read_file,
        azdevops_read_repo,
    )
    from yeaboi.tools.calendar_tools import detect_bank_holidays
    from yeaboi.tools.codebase import load_project_context, read_codebase, read_local_file
    from yeaboi.tools.confluence import (
        confluence_create_page,
        confluence_read_page,
        confluence_read_space,
        confluence_search_docs,
        confluence_update_page,
    )
    from yeaboi.tools.github import (
        github_list_issues,
        github_read_file,
        github_read_readme,
        github_read_repo,
    )
    from yeaboi.tools.jira import (
        jira_create_epic,
        jira_create_sprint,
        jira_create_story,
        jira_fetch_active_sprint,
        jira_fetch_velocity,
        jira_read_board,
    )
    from yeaboi.tools.llm_tools import estimate_complexity, generate_acceptance_criteria
    from yeaboi.tools.notion import (
        notion_create_page,
        notion_read_database,
        notion_read_page,
        notion_search_pages,
        notion_update_page,
    )
    from yeaboi.tools.team_learning import analyze_team_history, compare_plan_to_actuals

    return [
        github_read_repo,
        github_read_file,
        github_list_issues,
        github_read_readme,
        azdevops_read_repo,
        azdevops_read_file,
        azdevops_list_work_items,
        azdevops_read_board,
        azdevops_fetch_velocity,
        azdevops_fetch_active_iteration,
        azdevops_create_epic,
        azdevops_create_story,
        azdevops_create_iteration,
        read_codebase,
        read_local_file,
        detect_bank_holidays,
        estimate_complexity,
        generate_acceptance_criteria,
        jira_read_board,
        jira_fetch_velocity,
        jira_fetch_active_sprint,
        jira_create_epic,
        jira_create_story,
        jira_create_sprint,
        load_project_context,
        confluence_search_docs,
        confluence_read_page,
        confluence_read_space,
        confluence_create_page,
        confluence_update_page,
        notion_search_pages,
        notion_read_page,
        notion_read_database,
        notion_create_page,
        notion_update_page,
        analyze_team_history,
        compare_plan_to_actuals,
    ]
