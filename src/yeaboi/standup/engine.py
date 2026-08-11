"""Daily Standup engine — turns activity + sprint context into a delivered report.

This is a standalone pipeline (NOT a LangGraph node): the scheduled headless run
must be fast, cheap, and free of graph-checkpoint machinery, so it calls get_llm()
directly and follows the same parse → fallback → format convention the graph nodes
use (agent/nodes.py). Activity gathering and confidence are deterministic function
calls; the LLM is used only to synthesize prose, keeping a scheduled run to a
single cheap call.

Pipeline (run_standup):
  load session state + standup config
  → collect recent activity (collector)
  → gather sprint context + compute confidence (sprint_context, confidence)
  → per-member updates: one LLM call analyzes everyone's activity (alias-aware
    attribution); a typed self-report rides alongside as supporting context
  → assemble StandupReport → deliver → record run

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the standup summary prompt
# See docs: "Daily Standup" — engine
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from yeaboi import html_theme
from yeaboi.agent.state import ActivityEvidence, MemberUpdate, StandupReport

if TYPE_CHECKING:
    from yeaboi.agent.state import IssueFilingResult, TranscriptNudge, TranscriptReview, TranscriptSource
from yeaboi.standup import (
    adjudicate,
    aggregate,
    automation,
    categories,
    collector,
    confidence,
    habits,
    practice_feedback,
    references,
    sprint_context,
)
from yeaboi.standup.store import StandupStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _resolve_source_params(config: dict | None) -> dict:
    """Resolve collector source identifiers from config/env.

    Returns kwargs for collect_recent_activity: jira_project, azdo_project,
    github_repo, local_repo_path, confluence_space, notion_root.
    """
    from yeaboi.config import (
        get_azure_devops_project,
        get_confluence_space_key,
        get_jira_project_key,
        get_notion_root_page_id,
        get_notion_token,
    )

    params = {
        "jira_project": get_jira_project_key() or "",
        "azdo_project": get_azure_devops_project() or "",
        "confluence_space": get_confluence_space_key() or "",
        # Recent-page search is workspace-wide within the integration grants;
        # a root page is only needed for publishing.
        "notion_root": get_notion_root_page_id() or ("workspace" if get_notion_token() else ""),
        "github_repo": "",
        "local_repo_path": (config or {}).get("repo_path", "") or "",
    }
    # GitHub repo + optional overrides come from config getters added for standup.
    try:
        from yeaboi.config import get_standup_github_repo

        params["github_repo"] = get_standup_github_repo() or ""
    except Exception:
        logger.debug("standup: could not resolve GitHub repo config — skipping", exc_info=True)
    return params


def _resolve_tracker_sources(config: dict | None, override: list[str] | None, source_params: dict) -> list[str]:
    """Resolve the selected delivery tracker(s), with a safe first-run default."""
    from yeaboi.standup.roster import default_tracker_sources, validate_tracker_sources

    if override is not None:
        return validate_tracker_sources(override)
    if config and config.get("roster_configured") and config.get("tracker_sources"):
        return validate_tracker_sources(config["tracker_sources"])
    return default_tracker_sources(
        jira_project=source_params["jira_project"],
        azdo_project=source_params["azdo_project"],
    )


def _collector_sources(
    source_params: dict,
    tracker_sources: list[str],
    code_sources: list[str] | None = None,
    documentation_sources: list[str] | None = None,
) -> set[str]:
    """Build the exact activity-source set for this standup.

    Jira/Azure Boards follow the Team picker. Code and documentation sources
    remain independently useful, including Azure Repos when Azure Boards is not
    selected; the authoritative member filter below keeps their authors scoped.
    """
    enabled: set[str] = set()
    if "jira" in tracker_sources and source_params["jira_project"]:
        enabled.add(collector.SOURCE_JIRA)
    if "azure_devops" in tracker_sources and source_params["azdo_project"]:
        enabled.add(collector.SOURCE_AZDO)
    selected_code = set(code_sources or ())
    if "azure_devops" in selected_code and (
        source_params["azdo_project"] or source_params.get("azdo_projects") or source_params.get("azdo_repositories")
    ):
        enabled.add(collector.SOURCE_AZDO_REPOS)
    if "github" in selected_code:
        enabled.add(collector.SOURCE_GITHUB)
    if source_params["local_repo_path"]:
        enabled.add(collector.SOURCE_LOCAL_GIT)
    selected_docs = set(documentation_sources or ())
    if "confluence" in selected_docs and source_params["confluence_space"]:
        enabled.add(collector.SOURCE_CONFLUENCE)
    if "notion" in selected_docs and source_params["notion_root"]:
        enabled.add(collector.SOURCE_NOTION)
    return enabled


def _skipped_sources(
    source_params: dict,
    enabled: set[str],
    tracker_sources: list[str],
    code_sources: list[str] | None,
    documentation_sources: list[str] | None,
    dropped_code_sources: list[str] | None = None,
) -> tuple[list[tuple[str, str]], set[str]]:
    """Why each source we did NOT scan is missing, in the words the user needs.

    ``collect_recent_activity`` can auto-detect this only when it picks the source
    set itself; a standup always hands it an explicit set, so the reasons have to
    be worked out here — and here is also the only place that can tell the three
    cases apart:

    * the source was ticked in setup but has no repository/project behind it
      (``dropped_code_sources``, stripped by :func:`_resolve_code_scope`),
    * the integration is connected but the source was never ticked, a
      two-keypress fix rather than a ``.env`` one, and
    * nothing is configured at all, which is what ``SKIP_REASONS`` describes.

    Collapsing these into one line is what made a deselected GitHub look exactly
    like a GitHub that had nothing to report.

    Returns ``(skipped, unmet)``. ``skipped`` is every source that did not run,
    for the progress steps and the report's "Not scanned" panel. ``unmet`` is the
    subset the user actually asked for and did not get — the only ones worth a ⚠
    notice, because warning about a source nobody selected is a nag that repeats
    on every single run.
    """
    from yeaboi.config import get_github_token

    dropped = set(dropped_code_sources or ())
    selected_code = set(code_sources or ()) | dropped
    selected_docs = set(documentation_sources or ())
    selected = {
        collector.SOURCE_JIRA: "jira" in tracker_sources,
        collector.SOURCE_AZDO: "azure_devops" in tracker_sources,
        collector.SOURCE_GITHUB: "github" in selected_code,
        collector.SOURCE_CONFLUENCE: "confluence" in selected_docs,
        collector.SOURCE_NOTION: "notion" in selected_docs,
    }
    # Whether the integration behind the source is reachable at all, independent
    # of the picker. GitHub counts a bare token: the setup wizard offers it on the
    # token alone, so "no repo" is a scope problem, not a credentials one.
    configured = {
        collector.SOURCE_JIRA: bool(source_params["jira_project"]),
        collector.SOURCE_AZDO: bool(source_params["azdo_project"]),
        collector.SOURCE_GITHUB: bool(
            source_params.get("github_repositories") or source_params["github_repo"] or get_github_token()
        ),
        collector.SOURCE_LOCAL_GIT: bool(source_params["local_repo_path"]),
        collector.SOURCE_CONFLUENCE: bool(source_params["confluence_space"]),
        collector.SOURCE_NOTION: bool(source_params["notion_root"]),
    }
    unconfigured = dict(collector.SKIP_REASONS)
    unconfigured[collector.SOURCE_GITHUB] = "GITHUB_TOKEN not set"
    unconfigured[collector.SOURCE_AZDO_REPOS] = unconfigured[collector.SOURCE_AZDO]
    selected[collector.SOURCE_AZDO_REPOS] = "azure_devops" in selected_code
    configured[collector.SOURCE_AZDO_REPOS] = bool(
        source_params["azdo_project"] or source_params.get("azdo_projects") or source_params.get("azdo_repositories")
    )
    # A code source stripped for an empty scope, keyed by the collector source it
    # feeds — Azure Repos is "azure_devops" in the picker but ``azdo_repos`` here.
    empty_scope = {
        collector.SOURCE_GITHUB: ("github", "selected, but no repositories chosen"),
        collector.SOURCE_AZDO_REPOS: ("azure_devops", "selected, but no Azure projects chosen"),
    }
    skipped: list[tuple[str, str]] = []
    unmet: set[str] = set()
    azdo_deduped = False
    for source in collector.ALL_SOURCES:
        if source in enabled:
            continue
        # Azure tickets and Azure code share one .env block: when neither ran and
        # neither was asked for, say so once rather than twice.
        if (
            source == collector.SOURCE_AZDO_REPOS
            and collector.SOURCE_AZDO not in enabled
            and not selected[collector.SOURCE_AZDO_REPOS]
        ):
            azdo_deduped = True
            continue
        picker_key, empty_reason = empty_scope.get(source, ("", ""))
        if picker_key and picker_key in dropped:
            skipped.append((source, empty_reason))
            unmet.add(source)
        elif selected.get(source):
            # Ticked but unreachable: the .env reason is the honest one, and this
            # is a real disappointment — the user asked and did not get it.
            skipped.append((source, unconfigured.get(source, "not configured")))
            unmet.add(source)
        elif configured.get(source):
            skipped.append((source, "not selected in setup"))
        else:
            skipped.append((source, unconfigured.get(source, "not configured")))
    if azdo_deduped:
        # The surviving row stands for BOTH Azure surfaces, but its label reads
        # "Azure DevOps tickets" — which invites the reader to wonder what happened
        # to the code side. Say that the one row covers both.
        skipped = [
            (src, f"{reason} — tickets and code" if src == collector.SOURCE_AZDO else reason) for src, reason in skipped
        ]
    return skipped, unmet


def _resolve_code_scope(
    config: dict | None,
    code_sources: list[str] | None,
    github_repositories: list[str] | None,
    azdo_projects: list[str] | None,
    azdo_repositories: list[str] | None,
) -> tuple[list[str], list[str], list[str], list[str] | None, list[str]]:
    """Resolve GitHub repositories and Azure project scope.

    The fifth element is the sources dropped for having an empty scope — selected
    in setup, but with no repository or project behind them. That used to happen
    silently, which is indistinguishable from the source having found nothing.
    """
    from yeaboi.standup.code_scope import default_code_scope, validate_code_sources

    default_sources, default_github, default_azdo_projects = default_code_scope()
    configured = bool((config or {}).get("code_scope_configured"))
    sources = validate_code_sources(
        code_sources
        if code_sources is not None
        else ((config or {}).get("code_sources", []) if configured else default_sources)
    )
    github = list(
        dict.fromkeys(
            github_repositories
            if github_repositories is not None
            else ((config or {}).get("github_repositories", []) if configured else default_github)
        )
    )
    legacy_repositories = list(
        dict.fromkeys(
            azdo_repositories
            if azdo_repositories is not None
            else ((config or {}).get("azdo_repositories", []) if configured else [])
        )
    )
    projects = (
        []
        if azdo_repositories is not None and azdo_projects is None
        else list(
            dict.fromkeys(
                azdo_projects
                if azdo_projects is not None
                else ((config or {}).get("azdo_projects", []) if configured else default_azdo_projects)
            )
        )
    )
    if azdo_projects is None and not projects and legacy_repositories:
        projects = list(
            dict.fromkeys(
                project
                for repository in legacy_repositories
                for project, separator, _name in [str(repository).partition("/")]
                if separator and project
            )
        )
    dropped: list[str] = []
    if configured:
        if "github" in sources and not github:
            sources.remove("github")
            dropped.append("github")
        if "azure_devops" in sources and not projects and not legacy_repositories:
            sources.remove("azure_devops")
            dropped.append("azure_devops")
    # Explicit project scope wins. Legacy repositories remain available only
    # when callers supply them and do not supply projects.
    if azdo_projects is not None or projects:
        legacy_repositories = None
    return sources, github, projects, legacy_repositories, dropped


def _resolve_documentation_sources(config: dict | None, override: list[str] | None, source_params: dict) -> list[str]:
    """Resolve explicit documentation providers, defaulting to configured integrations."""
    from yeaboi.standup.documentation_scope import (
        default_documentation_sources,
        validate_documentation_sources,
    )

    if override is not None:
        return validate_documentation_sources(override)
    if config and config.get("documentation_scope_configured"):
        return validate_documentation_sources(config.get("documentation_sources", []))
    return default_documentation_sources(
        confluence_space=source_params["confluence_space"],
        notion_root=source_params["notion_root"],
    )


# ---------------------------------------------------------------------------
# LLM summarization (parse → fallback → format)
# ---------------------------------------------------------------------------


def _parse_standup_response(raw: str) -> dict:
    """Extract the summary JSON from an LLM response, tolerating markdown fences."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("standup: could not parse LLM JSON response")
        return {}


def _normalize_author(s: str) -> set[str]:
    """Return the normalized alias strings for one raw author/name string.

    Lowercased + stripped; emails additionally yield their local part so
    "Omar@x.com" matches a member whose alias is just "omar". Deliberately
    conservative — exact normalized strings only, no fuzzy/substring matching
    (so "Sam" never absorbs "Samantha").
    """
    s = (s or "").strip().lower()
    if not s:
        return set()
    out = {s}
    if "@" in s:
        local = s.split("@", 1)[0].strip()
        if local:
            out.add(local)
    return out


def _detect_git_identity(repo_path: str) -> list[str]:
    """Best-effort git identity (user.name + user.email) for alias matching.

    Reads the configured local repo's git config when a repo path is set, plus
    the GLOBAL git config either way — so the standup user's commits attach to
    them with zero configuration. Never raises — skips whatever fails (no git,
    no repo, timeout).
    """
    import subprocess

    from yeaboi.tools.local_git import git_subprocess_env

    commands: list[list[str]] = []
    if (repo_path or "").strip():
        # Sandbox: a configured-but-not-whitelisted repo_path contributes no
        # repo-local identity (global git config still applies below).
        from yeaboi.fs_policy import is_allowed

        if is_allowed(repo_path, mode="read"):
            commands += [["git", "-C", repo_path, "config", key] for key in ("user.name", "user.email")]
        else:
            logger.warning("standup: repo_path %s is outside the sandbox whitelist — skipping repo identity", repo_path)
    commands += [["git", "config", "--global", key] for key in ("user.name", "user.email")]

    identities: list[str] = []
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2, env=git_subprocess_env())
            value = (result.stdout or "").strip()
            if result.returncode == 0 and value and value not in identities:
                identities.append(value)
        except Exception:
            logger.debug("standup: git identity lookup failed (%s)", " ".join(cmd), exc_info=True)
    return identities


def _detect_tracker_identity() -> tuple[str, list[str]]:
    """Best-effort (display_name, identities) for the current user from configured trackers.

    Jira: the authenticated account's displayName + emailAddress (``myself()``).
    GitHub: the token's login (only when a token is configured — the lookup is a
    network call). Everything is guarded: unconfigured/failed sources contribute
    nothing and the standup proceeds. The display name lets the report present
    the user by their real name instead of the "Me" placeholder.
    """
    display = ""
    identities: list[str] = []
    try:
        from yeaboi.tools.jira import _make_jira_client

        client = _make_jira_client()
        if client is not None:
            me = client.myself()
            display = (me.get("displayName") or "").strip()
            for value in (display, (me.get("emailAddress") or "").strip()):
                if value:
                    identities.append(value)
    except Exception as e:
        logger.debug("standup: Jira identity lookup failed: %s", e, exc_info=True)
    try:
        from yeaboi.config import get_github_token

        if get_github_token():
            from yeaboi.tools.github import _get_github_client

            login = (_get_github_client().get_user().login or "").strip()
            if login:
                identities.append(login)
    except Exception as e:
        logger.debug("standup: GitHub identity lookup failed: %s", e, exc_info=True)
    return display, identities


def _build_alias_map(
    members: list[str],
    *,
    my_name: str = "",
    my_aliases: str = "",
    repo_path: str = "",
    extra_identities: tuple[str, ...] = (),
) -> dict[str, set[str]]:
    """Map each member to the set of normalized alias strings that identify them.

    Every member's own name is always an alias (so exact-name matching still
    works). The standup user (``my_name``) additionally gets their configured
    comma-separated ``my_aliases`` (GitHub handle, Jira display name, …), the
    auto-detected git identity, and any ``extra_identities`` (tracker-detected
    display name/email/login) — this is what lets the user's card claim
    activity authored under real tracker/VCS handles.
    """
    alias_map = {m: _normalize_author(m) for m in members}
    if my_name and my_name in alias_map:
        extras = [a.strip() for a in (my_aliases or "").split(",") if a.strip()]
        extras += _detect_git_identity(repo_path)
        extras += [x for x in extra_identities if x]
        for alias in extras:
            alias_map[my_name] |= _normalize_author(alias)
    return alias_map


def _enrich_aliases_from_items(alias_map: dict[str, set[str]], items: list[dict]) -> None:
    """Grow every member's alias set with emails observed on activity items.

    Sources attach ``author_email`` when the API exposes it (Jira/AzDO
    identities, git commit emails, Confluence editors). Whenever an item's
    author NAME already matches a member, that item's email (and its local
    part, via _normalize_author) becomes an alias of the member too — so a
    git commit authored as "omar.din@corp.com" attaches to the Jira member
    "Omar Din" once any tracker item exposed that email. Two passes reach the
    name → email → email-local-part closure. Strictly best-effort: emails are
    often hidden (GDPR) and their absence changes nothing.
    """
    # alias → emails seen alongside it on the same item.
    email_index: dict[str, set[str]] = {}
    for item in items:
        email = (item.get("author_email") or "").strip().lower()
        if not email or "@" not in email:
            continue
        for alias in _normalize_author(item.get("author", "")):
            email_index.setdefault(alias, set()).add(email)
    if not email_index:
        return
    for _ in range(2):  # second pass closes name → email → local-part chains
        for member, aliases in alias_map.items():
            for alias in list(aliases):
                for email in email_index.get(alias, ()):
                    aliases |= _normalize_author(email)


def _projected_item(item: dict) -> dict:
    """The per-item shape the rest of the pipeline sees — one place, two callers.

    Both the grouped activity and the open-ticket matching context go through
    here, so a field one of them needs can never quietly exist on only one.
    """
    return {
        "kind": item.get("kind", ""),
        "title": item.get("title", ""),
        "summary": item.get("summary", ""),
        "status": item.get("status", ""),
        "source": item.get("source", ""),
        "key": item.get("key", ""),
        "url": item.get("url", ""),
        "repository": item.get("repository", ""),
        "timestamp": item.get("timestamp", ""),
        # PR items carry these; commits match against them so a
        # PR's commits can fold under it (_nest_pr_commits).
        "pr_id": item.get("pr_id", ""),
        "branch": item.get("branch", ""),
        # Practice detection (habits.py) reads a change's own words
        # to decide whether it names a ticket — the branch and title
        # above, plus the commit/PR description here. On a TICKET item
        # this is instead the ticket's description + acceptance criteria
        # + definition of done, which is what relatedness matches against.
        "body": item.get("body", ""),
        # Deliberately NOT named `changed_files`: categories.split_activity
        # runs over these dicts and has never seen that key, so
        # introducing it here would silently reclassify docs-only
        # repository events out of Code and into Documentation.
        # Empty means UNKNOWN — the collectors cap detail lookups —
        # so every rule over it must be one-sided.
        "changed_paths": tuple(item.get("changed_files") or ()),
        # Azure Repos only: work items linked through the PR UI, and
        # whether that lookup actually ran (see azure_devops.py).
        "work_item_ids": tuple(item.get("work_item_ids") or ()),
        "work_items_known": item.get("work_items_known", True),
        # Story/subtask facts from the tracker (jira._issue_hierarchy,
        # azure_devops._work_item_hierarchy) — rendering-only, the web page
        # nests subtasks under their story from these.
        "issue_type": item.get("issue_type", ""),
        "parent_key": item.get("parent_key", ""),
        "subtask": bool(item.get("subtask", False)),
    }


def _group_activity_by_author(
    items: list[dict], members: list[str], alias_map: dict[str, set[str]] | None = None
) -> dict[str, list[dict]]:
    """Group activity items by author, matching via each member's alias set.

    Falls back to name-only aliases when no alias_map is given (the degenerate
    case is the old exact-match behavior, made case-insensitive).
    """
    alias_map = alias_map or {m: _normalize_author(m) for m in members}
    # Reverse index: normalized alias -> member (first member wins on collision).
    rev: dict[str, str] = {}
    for m in members:
        for alias in alias_map.get(m, _normalize_author(m)):
            rev.setdefault(alias, m)
    grouped: dict[str, list[dict]] = {m: [] for m in members}
    for item in items:
        author = (item.get("author") or "").strip()
        member = next((rev[a] for a in _normalize_author(author) if a in rev), None)
        if member is not None:
            grouped[member].append(_projected_item(item))
    return grouped


def _rebuild_bundle(bundle: collector.ActivityBundle, items: list[dict]) -> collector.ActivityBundle:
    """Return a copy of ``bundle`` holding only ``items``, with per-source counts recomputed.

    Carries errors, skipped, AND partial_sources — dropping partial_sources here
    used to silently swallow partial-coverage warnings downstream. Carries
    reference_tickets for the same reason: they are matching context, so
    filtering them alongside activity would quietly weaken the practice rules'
    ability to exonerate.
    """
    per_source: dict[str, int] = {}
    for item in items:
        source = item.get("source", "")
        per_source[source] = per_source.get(source, 0) + 1
    counts = [(source, per_source.get(source, 0)) for source, _count in bundle.counts]
    return collector.ActivityBundle(
        items=items,
        counts=counts,
        errors=list(bundle.errors),
        skipped=list(bundle.skipped),
        partial_sources=list(bundle.partial_sources),
        reference_tickets=list(bundle.reference_tickets),
    )


def _filter_bundle_to_members(
    bundle: collector.ActivityBundle,
    alias_map: dict[str, set[str]],
) -> collector.ActivityBundle:
    """Return only activity attributable to the authoritative saved roster."""
    known_aliases: set[str] = set().union(*alias_map.values()) if alias_map else set()
    items = [item for item in bundle.items if _normalize_author(item.get("author", "")) & known_aliases]
    logger.info(
        "standup: roster filter retained %d/%d activity item(s) for %d member(s)",
        len(items),
        len(bundle.items),
        len(alias_map),
    )
    return _rebuild_bundle(bundle, items)


def _drop_automated_activity(
    bundle: collector.ActivityBundle,
    config: dict | None,
) -> tuple[collector.ActivityBundle, list[str]]:
    """Remove service-hook/bot activity posted under members' identities.

    The motivating case: a Wiz scanner hook posting PR review comments with the
    user's PAT, which pure author matching credits to the human. Detection is
    content/metadata/burst-based (standup/automation.py); anything excluded is
    reported via the returned notice lines — never silent. ``automation_handling
    = "off"`` disables the filter entirely.
    """
    handling = (config or {}).get("automation_handling", "exclude")
    if handling == "off":
        return bundle, []
    kept, clusters = automation.partition_automated(
        bundle.items,
        custom_markers=automation.parse_custom_markers((config or {}).get("automation_markers", "")),
    )
    if not clusters:
        return bundle, []
    logger.info(
        "standup: excluded %d automated item(s) in %d cluster(s): %s",
        sum(c.count for c in clusters),
        len(clusters),
        "; ".join(f"{c.reason} author={c.author!r} n={c.count} keys={list(c.keys[:3])}…" for c in clusters),
    )
    return _rebuild_bundle(bundle, kept), automation.notice_lines(clusters)


def _member_links(acts: list[dict]) -> tuple[tuple[str, str], ...]:
    """Distinct (label, url) references from a member's grouped activity.

    Label is the item key (ticket id / PR number / sha) when present, else a
    truncated title. Deduped by URL preserving order, capped so a busy member's
    card stays readable.
    """
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for a in acts:
        url = (a.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        label = (a.get("key") or "").strip() or (a.get("title") or "")[:40]
        links.append((label, url))
        if len(links) >= 6:
            break
    return tuple(links)


# Transcript-review findings shown in the standup's own notices before the list
# is elided — enough to act on, few enough that the notices stay scannable.
_MAX_TRANSCRIPT_NOTICES = 3

# Commit → PR association lives in title text only: collectors emit pr_id and
# branch on PR items, but a commit names its PR solely via its subject. The
# real-world formats, one pattern each: GitHub/AzDO merge commits
# ("Merge pull request #91 …" / "Merge pull request 48806 …"), AzDO squash
# merges ("Merged PR 123: Title"), and parenthesised references — GitHub squash
# merges end in "(#91)" and the collector's own PR-branch scan appends
# "(PR #91)".
#
# The patterns themselves live in `references` because habits.py needs the same
# reading of a commit subject. All are gated *here* on a matching PR existing in
# the same repository's window, so a stray "(#12)" in prose cannot fold a commit
# under nothing. (The habit rules deliberately use the *ungated* reading — see
# references.claims_pull_request.)
_PR_NUMBER_RES = references.PR_NUMBER_RES
_MERGE_BRANCH_RE = references.MERGE_BRANCH_RE


def _nest_pr_commits(acts: list[dict]) -> list[dict]:
    """Fold commits under the PR they belong to; unmatched commits stay put.

    A merged PR arrives as one ``pr`` item plus its merge/branch commits — as
    flat rows they read as N separate pieces of work. Matching is per
    repository, by PR number from the commit title, falling back to the merge
    subject's source branch against the PR's ``branch``. Copies the PR dicts
    (adding ``children``) rather than mutating the caller's items — the same
    acts list also feeds ``_member_links`` and the counts.
    """
    out: list[dict] = []
    prs_by_number: dict[tuple[str, str], dict] = {}
    prs_by_branch: dict[tuple[str, str], dict] = {}
    for a in acts:
        if a.get("kind") == "pr":
            a = {**a, "children": []}
            repo = str(a.get("repository") or "")
            if a.get("pr_id"):
                prs_by_number[(repo, str(a["pr_id"]))] = a
            if a.get("branch"):
                prs_by_branch[(repo, str(a["branch"]))] = a
        out.append(a)
    if not prs_by_number and not prs_by_branch:
        return acts

    kept: list[dict] = []
    for a in out:
        if a.get("kind") != "commit":
            kept.append(a)
            continue
        repo = str(a.get("repository") or "")
        title = str(a.get("title") or "")
        parent = None
        for pattern in _PR_NUMBER_RES:
            if (match := pattern.search(title)) and (parent := prs_by_number.get((repo, match.group(1)))):
                break
            parent = None
        if parent is None and (match := _MERGE_BRANCH_RE.search(title)):
            # GitHub merge subjects say "from <owner>/<branch>" while the PR
            # item's branch is the bare head ref — try both spellings.
            ref = match.group(1)
            parent = prs_by_branch.get((repo, ref))
            if parent is None and "/" in ref:
                parent = prs_by_branch.get((repo, ref.split("/", 1)[1]))
        if parent is None:
            kept.append(a)
        else:
            parent["children"].append(a)
    return kept


def _member_evidence(
    acts: list[dict],
    cap: int = 8,
    *,
    prefixes: Collection[str] = frozenset(),
    work_item_ids: Collection[str] = frozenset(),
) -> tuple[ActivityEvidence, ...]:
    """Structured evidence rows from a member's grouped activity.

    Unlike ``_member_links`` this keeps the title, kind, repository, and status
    the collectors fetched, and keeps URL-less items (an in-progress ticket with
    no link still says something). Rows are ordered newest-first — the day's
    movement (a ticket landing in Done) belongs in the visible top rows, while
    carried WIP (which Jira stamps with an empty timestamp) folds. Deduped in
    that order — by URL when there is one, else by (kind, key, title) — so the
    latest event for a ticket is the one that survives.

    ``prefixes``/``work_item_ids`` are the report-wide reference gates
    (references.tracker_prefixes / tracker_work_item_ids): with them, each
    code/doc row gets ``ticket_keys`` — the exact tracker keys its own
    title/branch/body (or first-party AzDO links) name, which is what lets the
    web page file a PR under its story. The defaults keep the gates closed, so
    a caller that doesn't pass them gets no keys rather than ungated ones.
    """
    # Timestamps are ISO-8601 strings, so string comparison is chronological.
    # Descending puts the empty string — timestamp-less carried WIP — last, and
    # the sort is stable, so equal stamps keep collector order.
    ordered = sorted(acts, key=lambda a: str(a.get("timestamp") or ""), reverse=True)
    seen: set[str] = set()
    rows: list[ActivityEvidence] = []
    for a in ordered:
        url = (a.get("url") or "").strip()
        # One PR merge = one row. The same merge lands as two commits with
        # different SHAs (the branch-side and target-side merge commits carry
        # the same "Merge pull request N…" subject), so URL-first dedupe kept
        # both; keying merge commits on the PR number keeps only the newest.
        # Gated on the subject being an actual merge — an authored commit
        # wearing a "(PR #91)" provenance tail is distinct work, not a merge.
        # Any merge subject naming the same PR shares the key, so a branch-sync
        # merge tagged with the PR's tail folds into the PR merge too: all of
        # them are plumbing, and one row of it is enough.
        title = str(a.get("title") or "")
        pr_number = (
            references.pr_reference(title) if a.get("kind") == "commit" and references.is_merge_subject(title) else ""
        )
        if pr_number:
            dedupe = f"pr-merge:{a.get('repository', '')}:{pr_number}"
        else:
            dedupe = url or f"{a.get('kind', '')}:{a.get('key', '')}:{a.get('title', '')}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        rows.append(
            ActivityEvidence(
                kind=str(a.get("kind") or ""),
                key=str(a.get("key") or "").strip(),
                # Jira update/comment titles are action phrases ("updated KEY
                # '…'"); the clean ticket summary travels separately and wins.
                title=str(a.get("summary") or a.get("title") or "").strip(),
                url=url,
                repository=str(a.get("repository") or "").strip(),
                status=str(a.get("status") or "").strip(),
                timestamp=str(a.get("timestamp") or "").strip(),
                # Commits folded under a PR (_nest_pr_commits) become one level
                # of children — same ordering/dedupe rules, tighter cap. Child
                # dicts never carry children themselves, so this terminates.
                children=(
                    _member_evidence(a["children"], cap=6, prefixes=prefixes, work_item_ids=work_item_ids)
                    if a.get("children")
                    else ()
                ),
                issue_type=str(a.get("issue_type") or "").strip(),
                parent_key=str(a.get("parent_key") or "").strip(),
                subtask=bool(a.get("subtask")),
                # Tracker rows ARE tickets; naming keys is for the changes
                # (commits, PRs, pages) that reference them.
                ticket_keys=(
                    references.display_ticket_keys(
                        str(a.get("title") or ""),
                        str(a.get("branch") or ""),
                        str(a.get("body") or ""),
                        prefixes=prefixes,
                        work_item_ids=work_item_ids,
                        linked_ids=a.get("work_item_ids") or (),
                    )
                    if not references.is_tracker_kind(str(a.get("kind") or ""))
                    else ()
                ),
            )
        )
        if len(rows) >= cap:
            break
    return tuple(rows)


def _reference_gates(grouped: dict[str, list[dict]]) -> tuple[frozenset[str], frozenset[str]]:
    """The report-wide ticket-reference gates, computed once per report.

    Built from every member's activity, not one member's: the gate answers
    "did the trackers produce this prefix/id today at all", and a PR may name a
    ticket assigned to a teammate.

    Deliberately NARROWER than habits.py's gate, which also folds in the open
    reference tickets: a named key only matters here when its story row is on
    the card, and a visible row's prefix/id is in ``grouped`` by definition —
    widening the gate could only admit keys that attach to nothing.
    """
    gate_items = [a for acts in grouped.values() for a in acts]
    prefixes = references.tracker_prefixes(gate_items)
    work_item_ids = references.tracker_work_item_ids(gate_items)
    logger.info(
        "standup: evidence hierarchy gates — %d tracker prefix(es), %d work-item id(s)",
        len(prefixes),
        len(work_item_ids),
    )
    return prefixes, work_item_ids


def _member_source(has_self_report: bool, has_activity: bool) -> str:
    """Classify a MemberUpdate's provenance for rendering (✍ tags etc.)."""
    if has_self_report:
        return "combined" if has_activity else "self-reported"
    return "inferred"


def _is_code_activity(item: dict) -> bool:
    """Backward-compatible wrapper for tests and callers."""
    return categories.is_code_activity(item)


def _fallback_code_summary(acts: list[dict], coverage: str = categories.COVERED) -> str:
    """Concise evidence summary without equating event volume with productivity."""
    code = [a for a in acts if _is_code_activity(a)]
    if not code:
        return categories.empty_summary(categories.CATEGORY_CODE, coverage)
    titles = "; ".join(str(a.get("title") or "") for a in code if a.get("title"))[:400]
    return titles or "Code activity detected in the selected repositories."


def _fallback_category_summary(category: str, acts: list[dict], coverage: str) -> str:
    if category == categories.CATEGORY_CODE:
        return _fallback_code_summary(acts, coverage)
    if not acts:
        return categories.empty_summary(category, coverage)
    fresh = "; ".join(str(a.get("title") or "") for a in acts if a.get("title") and a.get("kind") != "wip")[:400]
    if fresh:
        return fresh
    wip = "; ".join(str(a.get("title") or "") for a in acts if a.get("title") and a.get("kind") == "wip")[:400]
    return f"Continuing work on: {wip}"[:400] if wip else categories.empty_summary(category, coverage)


def _fallback_summary(acts: list[dict]) -> str:
    """Deterministic summary from grouped items: fresh activity first, then WIP.

    The member summary renders as the card's headline, so it stays a one-liner:
    the first two titles plus a count, with the full detail left to the
    category summaries and evidence rows. A member whose only signal is
    in-progress tickets (kind="wip") reads "Continuing work on: …" — being
    quiet in the window is not "no activity" when they have assigned in-flight
    work.
    """
    fresh = [a["title"] for a in acts if a.get("title") and a.get("kind") != "wip"]
    if fresh:
        head = "; ".join(fresh[:2])
        more = len(fresh) - 2
        return (f"{head}; and {more} more" if more > 0 else head)[:400]
    wip = "; ".join(a["title"] for a in acts if a.get("title") and a.get("kind") == "wip")[:400]
    if wip:
        return f"Continuing work on: {wip}"[:400]
    return "No activity detected."


def _fallback_progress_note(yesterday_entry: dict, acts: list[dict]) -> str:
    """Carried-over-work note without an LLM: yesterday's ticket keys ∩ today's.

    Only states what the data proves (the same ticket keys appear on both
    days); anything more interpretive is left to the LLM path.

    No prefix gate needed here (unlike ``export._ticket_key_map``): the
    intersection with today's *activity keys* is itself the gate, so a "UTF-8"
    in yesterday's prose can only survive if it is literally a ticket key the
    collectors produced today.
    """
    yesterday_text = " ".join(str(v) for v in yesterday_entry.values())
    yesterday_keys = set(references.find_ticket_keys(yesterday_text))
    today_keys = {str(a.get("key") or "") for a in acts}
    carried = sorted(yesterday_keys & today_keys)
    if not carried:
        return ""
    return f"Still on {', '.join(carried[:3])} (carried over from the last standup)."


def _fallback_outlook(acts: list[dict]) -> str:
    """Deterministic day-ahead prediction: assigned in-progress tickets only."""
    wip_titles = [str(a.get("title") or "") for a in acts if a.get("kind") == "wip" and a.get("title")]
    if not wip_titles:
        return ""
    return f"Likely continuing: {'; '.join(wip_titles[:2])}."[:300]


def _build_fallback_member_updates(
    grouped: dict[str, list[dict]],
    self_reported: dict[str, str],
    coverage: dict[str, str] | None = None,
    blocker_signals: dict[str, tuple[str, ...]] | None = None,
    yesterday: dict[str, dict] | None = None,
    practices: dict[str, tuple] | None = None,
) -> list[MemberUpdate]:
    """Deterministic per-member updates when the LLM is unavailable.

    Every member gets an activity-derived summary (a plain join of their
    activity titles); a self-report is carried alongside as supporting context,
    never replacing the activity view. Detected blocker signals become the
    blockers text directly, so blocker highlighting works without an LLM.

    Kept with this signature for direct callers/tests; ``run_standup`` goes
    through the aggregate seam instead. Both routes build the same skeletons
    (``aggregate._member_skeletons``) and assemble through
    ``_updates_from_result``, so there is exactly one assembly implementation.
    """
    coverage = coverage or {category: categories.COVERED for category in categories.CATEGORIES}
    skeletons = aggregate._member_skeletons(
        grouped,
        coverage=coverage,
        yesterday=yesterday or {},
        self_reported_names=set(self_reported),
    )
    return _updates_from_result(
        skeletons,
        self_reported=self_reported,
        blocker_signals=blocker_signals or {},
        yesterday_names=set(yesterday or {}),
        practices=practices or {},
        coverage=coverage,
        llm_members=None,
    )


def _updates_from_result(
    skeletons: list[dict],
    *,
    self_reported: dict[str, str],
    blocker_signals: dict[str, tuple[str, ...]],
    yesterday_names: Collection[str],
    practices: dict[str, tuple],
    coverage: dict[str, str],
    llm_members: dict[str, dict] | None,
) -> list[MemberUpdate]:
    """Assemble MemberUpdates from aggregate skeletons, overlaying LLM prose.

    ``llm_members`` None means the deterministic fallback path: every prose
    field comes from the skeleton's ``fallback_*`` strings. With a parsed LLM
    response, the model's words win where they exist, under the deterministic
    clamps the old inline assembly always applied: a category with no evidence
    keeps its coverage wording (the model can never turn absent evidence into
    claimed work), no progress note without an actual previous standup (the
    model can never invent a "yesterday"), and detected blocker signals surface
    even if the model dropped them from its 'blockers'.

    Practice signals are deterministic to begin with, so unlike the summaries
    they are identical on both paths — nothing degrades when the LLM is gone.
    """
    llm = llm_members is not None
    updates: list[MemberUpdate] = []
    for sk in skeletons:
        name = str(sk.get("name") or "")
        m = (llm_members or {}).get(name, {})
        summary = ((m.get("summary") or "").strip() if llm else "") or str(sk.get("fallback_summary") or "")
        # Deliberate: the self-report overlay keys on the TEXT, not on which
        # path produced it — an LLM that literally answers "No activity
        # detected." is saying the same thing the fallback says, and a member
        # who typed a self-report is a better source than either.
        if summary == "No activity detected." and self_reported.get(name):
            summary = self_reported[name]
        joined_signals = "; ".join(blocker_signals.get(name, ()))
        if llm:
            blockers = (m.get("blockers") or "").strip() or joined_signals
            progress_note = (m.get("progress_note") or "").strip() if name in yesterday_names else ""
            outlook = (m.get("outlook") or "").strip()
        else:
            blockers = joined_signals
            progress_note = str(sk.get("fallback_progress_note") or "")
            outlook = str(sk.get("fallback_outlook") or "")

        def _category(block: dict, llm_field: str) -> tuple:
            fallback = str(block.get("summary") or "")
            with_llm = (m.get(llm_field) or "").strip() if llm and block.get("count") else ""  # noqa: B023
            return (
                with_llm or fallback,
                tuple((str(label), str(url)) for label, url in block.get("links") or ()),
                int(block.get("count") or 0),
                tuple(aggregate.evidence_from_wire(row) for row in block.get("evidence") or ()),
            )

        code_summary, code_links, code_count, code_evidence = _category(sk.get("code") or {}, "code_summary")
        documentation_summary, documentation_links, documentation_count, documentation_evidence = _category(
            sk.get("documentation") or {}, "documentation_summary"
        )
        ticketing_summary, ticketing_links, ticketing_count, ticketing_evidence = _category(
            sk.get("ticketing") or {}, "ticketing_summary"
        )
        updates.append(
            MemberUpdate(
                name=name,
                summary=summary,
                blockers=blockers,
                progress_note=progress_note,
                outlook=outlook,
                self_report=self_reported.get(name, ""),
                source=str(sk.get("source") or "inferred"),
                links=tuple((str(label), str(url)) for label, url in sk.get("links") or ()),
                activity_count=int(sk.get("activity_count") or 0),
                code_summary=code_summary,
                code_links=code_links,
                code_activity_count=code_count,
                documentation_summary=documentation_summary,
                documentation_links=documentation_links,
                documentation_activity_count=documentation_count,
                ticketing_summary=ticketing_summary,
                ticketing_links=ticketing_links,
                ticketing_activity_count=ticketing_count,
                ticketing_evidence=ticketing_evidence,
                code_evidence=code_evidence,
                documentation_evidence=documentation_evidence,
                practices=practices.get(name, ()),
            )
        )
    if llm_members is None:
        # Self-reporters missing from the grouping (shouldn't happen — run_standup
        # adds them to the roster) still surface rather than silently dropping.
        skeleton_names = {str(sk.get("name") or "") for sk in skeletons}
        for name, text in self_reported.items():
            if name not in skeleton_names:
                updates.append(
                    MemberUpdate(
                        name=name,
                        summary=text or "No activity detected.",
                        self_report=text,
                        source="self-reported",
                        code_summary=categories.empty_summary(
                            categories.CATEGORY_CODE, coverage.get(categories.CATEGORY_CODE, categories.COVERED)
                        ),
                        documentation_summary=categories.empty_summary(
                            categories.CATEGORY_DOCUMENTATION,
                            coverage.get(categories.CATEGORY_DOCUMENTATION, categories.COVERED),
                        ),
                        ticketing_summary=categories.empty_summary(
                            categories.CATEGORY_TICKETING,
                            coverage.get(categories.CATEGORY_TICKETING, categories.COVERED),
                        ),
                    )
                )
    return updates


def _build_fallback_team_summary(bundle: collector.ActivityBundle, progress: confidence.SprintProgress) -> str:
    """Deterministic team summary when the LLM is unavailable.

    Deliberately spare: the confidence chip already states the label and
    rationale, and the Details footer already itemises the per-source counts —
    a fallback that restated both rendered the same three facts twice on a page
    with no LLM to say anything else.
    """
    if not bundle.total():
        return f"No activity detected in the collection window. Sprint status: {progress.confidence_label}."
    return f"Sprint status: {progress.confidence_label}."


_WORD_RE = re.compile(r"[a-z0-9]+")


def _strip_rationale_echo(team_summary: str, rationale: str) -> str:
    """Drop team-summary sentences that restate the confidence rationale.

    The rationale renders beside the confidence chip, directly above the team
    summary; an LLM told the sprint status as context tends to open by repeating
    it ("Day 2 of 10: 0 of ~3 ideal points burned" → "The sprint is on day 2 of
    10 with no points burned yet…"). The test is what fraction of the
    *rationale's* words the sentence covers — the echo sentence pads itself with
    prose, so measuring against the sentence would let every echo through. A
    sentence goes only on ≥70% coverage of a rationale with enough words to make
    that meaningful, so a first sentence that says something of its own always
    survives.

    Generation-time only, on the LLM's own words — deliberately NOT applied by
    the exporters or renderers: ``team_summary`` is host-editable on a share,
    and a fuzzy strip downstream could silently delete a sentence a human
    wrote. Reports stored before this existed keep their echo; history is
    history.
    """
    rationale_words = set(_WORD_RE.findall((rationale or "").lower()))
    if len(rationale_words) < 4:
        return team_summary
    kept: list[str] = []
    for sentence in html_theme.split_sentences(team_summary):
        words = set(_WORD_RE.findall(sentence.lower()))
        if len(rationale_words & words) / len(rationale_words) >= 0.7:
            logger.info("standup: dropped a team-summary sentence that echoed the confidence rationale")
            continue
        kept.append(sentence)
    return " ".join(kept)


def _summarize_members(
    *,
    result: dict,
    self_reported: dict[str, str],
    sprint_name: str,
    self_reported_images: dict[str, list[str]] | None = None,
) -> tuple[list[MemberUpdate], str, list[str]]:
    """Produce (member_updates, team_summary, warnings) via one LLM call + deterministic fallback.

    ``result`` is the ``standup.aggregate`` wire dict — from
    ``aggregate.aggregate_standup`` or the Go sidecar, byte-identical by
    contract — carrying the grouping, insights, practices, confidence and the
    per-member skeletons. This function only overlays prose on that scaffold,
    so which backend served the aggregation can never change what a report
    shows.

    An LLM auth/billing failure is NOT re-raised — it's turned into a
    user-facing *warning* and the deterministic fallback is used, so the standup
    still renders with a clear reason instead of crashing or looking empty.

    Every member — including those who typed their own update — gets an
    activity-derived summary; a self-report is passed to the LLM as supporting
    context and carried verbatim on ``MemberUpdate.self_report``, so typing an
    update never suppresses the analysis of what you actually did.

    self_reported_images: per-member screenshot paths pasted (Ctrl+V) into "My
        Update" — attached to the summary LLM call as multimodal image blocks so
        the model can fold what they show into the team summary.
    """
    members = [str(m) for m in result.get("members") or ()]
    grouped: dict[str, list[dict]] = result.get("grouped") or {}
    blocker_signals = {
        str(name): tuple(str(s) for s in signals) for name, signals in (result.get("blocker_signals") or {}).items()
    }
    yesterday: dict[str, dict] = result.get("yesterday") or {}
    # Practices never enter the prompt (they are already deterministic, and a
    # PR description per item would double it) — they only ride along to be set
    # on the MemberUpdates both paths build.
    practices = aggregate.practices_from_wire(result.get("practices") or {})
    progress = aggregate.progress_from_wire(result.get("progress") or {})
    skeletons: list[dict] = result.get("member_skeletons") or []
    activity_counts = [(str(s), int(n)) for s, n in result.get("counts") or ()]
    fallback_team_summary = str(result.get("fallback_team_summary") or "")
    coverage = dict((str(c), str(s)) for c, s in result.get("category_coverage") or ()) or {
        category: categories.COVERED for category in categories.CATEGORIES
    }

    def _for_llm(acts: list[dict]) -> list[dict]:
        # URLs (and the keys they duplicate — titles already carry ticket ids,
        # as does the standalone summary field) are for rendering links, not
        # reasoning; strip them to keep the prompt lean. pr_id/branch/timestamp
        # exist only so evidence rows can fold and sort — same treatment, as do
        # body/changed_paths/work_item_* which exist only for habits.py and
        # would otherwise put a full PR description and 100 file paths per item
        # into the prompt. This is a blacklist, so anything added to
        # _group_activity_by_author lands in the prompt until it is named here —
        # test_standup_engine pins the resulting key set for exactly that reason.
        rendering_only = (
            "url",
            "key",
            "summary",
            "pr_id",
            "branch",
            "timestamp",
            "body",
            "changed_paths",
            "work_item_ids",
            "work_items_known",
            # Story/subtask hierarchy is deterministic and drawn by the web
            # page; the model restating it would be structure, not insight.
            "issue_type",
            "parent_key",
            "subtask",
        )
        return [{k: v for k, v in a.items() if k not in rendering_only} for a in acts]

    # WIP (assigned in-progress tickets, possibly untouched in the window) is a
    # separate payload list so the LLM can distinguish "did" from "is doing".
    member_payload = [
        {
            "name": name,
            "ticketing_activity": _for_llm(
                [
                    a
                    for a in categories.split_activity(grouped.get(name, []))[categories.CATEGORY_TICKETING]
                    if a.get("kind") != "wip"
                ]
            ),
            "code_activity": _for_llm(categories.split_activity(grouped.get(name, []))[categories.CATEGORY_CODE]),
            "documentation_activity": _for_llm(
                categories.split_activity(grouped.get(name, []))[categories.CATEGORY_DOCUMENTATION]
            ),
            "in_progress": _for_llm(
                [
                    a
                    for a in categories.split_activity(grouped.get(name, []))[categories.CATEGORY_TICKETING]
                    if a.get("kind") == "wip"
                ]
            ),
            "self_report": self_reported.get(name, ""),
            "coverage": coverage,
            # Previous-standup context + deterministic blocker evidence — the
            # prompt instructs the model to write a day-over-day progress_note
            # from "yesterday" and to reflect every signal in "blockers".
            "yesterday": yesterday.get(name, {}),
            "blocker_signals": list(blocker_signals.get(name, ())),
        }
        for name in members
    ]

    def _fallback(extra_warnings: list[str]) -> tuple[list[MemberUpdate], str, list[str]]:
        return (
            _updates_from_result(
                skeletons,
                self_reported=self_reported,
                blocker_signals=blocker_signals,
                yesterday_names=set(yesterday),
                practices=practices,
                coverage=coverage,
                llm_members=None,
            ),
            fallback_team_summary,
            extra_warnings,
        )

    # Nothing to reason over (no activity anywhere and no self-reports) →
    # deterministic fallback only; don't spend an LLM call saying "no activity".
    if (not member_payload or not result.get("total_items")) and not self_reported:
        return _fallback([])

    # No LLM credentials → don't attempt the call; say so plainly.
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("standup: LLM not configured (%s) — using deterministic fallback", why)
        return _fallback([f"AI summary unavailable — {why}."])

    # invoke_json tracks usage + turns on JSON mode + re-asks once on bad JSON.
    # See docs: "Local Mode (Ollama)" — reliability layer.
    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint
    from yeaboi.prompts.standup import get_standup_summary_prompt

    prompt = get_standup_summary_prompt(
        sprint_name=sprint_name,
        sprint_day=progress.sprint_day,
        sprint_total_days=progress.sprint_total_days,
        confidence_label=progress.confidence_label,
        confidence_rationale=progress.confidence_rationale,
        members=member_payload,
        activity_counts=activity_counts,
    )

    # Screenshots pasted into "My Update" — flattened across members and attached
    # as multimodal image blocks (see agent/llm.py; degrades text-only on failure).
    images = [p for paths in (self_reported_images or {}).values() for p in paths]

    try:
        logger.info(
            "standup: invoking LLM to summarize %d member(s) (%d pasted image(s))",
            len(member_payload),
            len(images),
        )
        response = invoke_json(prompt, image_paths=images)
        parsed = _parse_standup_response(response.content)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("standup: LLM auth/billing error — surfacing as warning: %s", exc)
            return _fallback(["AI summary unavailable — API key invalid or billing issue."])
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("standup: local Ollama failure: %s", exc)
            return _fallback([f"AI summary unavailable — {local_hint}"])
        logger.warning("standup: LLM summarization failed, using fallback: %s", exc)
        return _fallback(["AI summary unavailable — LLM request failed (see logs)."])

    # Assemble: every member gets an activity-derived summary; self-reports ride
    # alongside on self_report (shown as "their words" by the renderers). The
    # deterministic fields all come from the skeletons; only prose is overlaid.
    llm_members = {m.get("name", ""): m for m in parsed.get("members", []) if isinstance(m, dict)}
    updates = _updates_from_result(
        skeletons,
        self_reported=self_reported,
        blocker_signals=blocker_signals,
        yesterday_names=set(yesterday),
        practices=practices,
        coverage=coverage,
        llm_members=llm_members,
    )

    team_summary = _strip_rationale_echo((parsed.get("team_summary") or "").strip(), progress.confidence_rationale)
    team_summary = team_summary or fallback_team_summary
    return updates, team_summary, []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_standup(
    session_id: str,
    *,
    channels: list[str] | None = None,
    days: int | None = None,
    tracker_sources: list[str] | None = None,
    team_members: list[str] | None = None,
    code_sources: list[str] | None = None,
    github_repositories: list[str] | None = None,
    azdo_projects: list[str] | None = None,
    azdo_repositories: list[str] | None = None,
    documentation_sources: list[str] | None = None,
    review_transcripts: bool = True,
    deliver: bool = True,
    dry_run: bool = False,
    db_path=None,
    today: date | None = None,
    on_progress=None,
) -> StandupReport:
    """Run a full standup for ``session_id`` and return the StandupReport.

    Args:
        channels: delivery channels override; falls back to saved config, then ["terminal"].
        days: explicit look-back window in days (now − N). Default None uses the
            working-day window instead: previous working day 00:00 → now, so a
            weekend/Monday run still captures Friday and a midweek run covers
            the FULL previous day plus today so far.
        tracker_sources: Jira/Azure DevOps delivery tracker override. ``None``
            uses the saved Team selection.
        team_members: authoritative member-name override. ``None`` uses the
            saved Team selection; an explicit empty list makes a self-only run.
        code_sources/repositories: optional code-scope overrides; GitHub is
            repository-scoped and Azure Repos is project-scoped.
        documentation_sources: optional Confluence/Notion provider override;
            repository documentation follows the selected code repositories.
        review_transcripts: when True (default), sweep the transcript folders
            for unreviewed meeting transcripts covering EARLIER dates and review
            them first, so yesterday's corrections inform today's report. Never
            writes to GitHub — issues are drafted, and filing is a separate act.
        deliver: when True, fan out to delivery channels (skipped if dry_run).
        dry_run: build the report but do not deliver (used by the TUI "Generate" preview).
        db_path: override sessions.db path (tests); defaults to paths.get_db_path().
        today: override for the current date (tests).
        on_progress: optional ``callable(str)`` invoked (best-effort) as each
            pipeline phase starts — lets the TUI show live progress while the
            network + LLM calls run on a worker thread.
    """
    from yeaboi.paths import get_db_path
    from yeaboi.sessions import SessionStore

    def _notify(phase: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress(phase)
        except Exception:  # progress display must never break the run
            logger.debug("standup: on_progress callback failed", exc_info=True)

    today = today or date.today()
    date_str = today.isoformat()
    db_path = db_path or get_db_path()
    logger.info("run_standup: session=%s date=%s days=%s dry_run=%s", session_id, date_str, days, dry_run)

    # 1. Load session state + standup config.
    with SessionStore(db_path) as sessions:
        state = sessions.load_state(session_id) or {}
    with StandupStore(db_path) as store:
        config = store.load_config(session_id)
        self_reported = store.get_my_updates(session_id, date_str)
        self_reported_images = store.get_my_update_images(session_id, date_str)
        # Day-over-day context: the previous standup's full report (for the
        # per-member progress notes + cross-standup blocker signals) and prior
        # run metadata (for the confidence trend). Both are date-scoped so a
        # same-day rerun never compares today against itself.
        previous_run = store.get_previous_run(session_id, date_str)
        previous_report = previous_run[3] if previous_run else None
        prior_history = store.get_history(session_id, limit=10)
        # The team's own thumbs up/down on earlier practice signals. Read here
        # rather than taken as a parameter: it belongs to the session, so every
        # surface that runs a standup gets it without having to remember to.
        feedback_ledger = practice_feedback.load(store, session_id)

    # What the team corrected on the previous standup, if they corrected it.
    # The corrected *text* already reaches this run for free — a corrected row
    # supersedes its parent above — but that alone only stops one wrong fact
    # being repeated. Knowing the team looked at a member's summary and
    # disagreed is the part worth carrying forward.
    corrections: tuple = ()
    if previous_run is not None and previous_run[1] == "edited":
        from yeaboi.artifacts.store import ArtifactEditStore, artifact_ref

        try:
            with ArtifactEditStore(db_path) as edit_store:
                # `edited_from_id`, not this row's own id: the log is filed
                # against the artifact it was written on, which is the parent.
                corrections = edit_store.list_edits(
                    "standup", artifact_ref("standup", run_id=previous_run[2] or previous_run[0])
                )
        except Exception:  # noqa: BLE001 — a missing hint is not a failed standup
            logger.warning("Could not read previous standup corrections", exc_info=True)

    # 1b. Transcript sweep. Reviews unreviewed transcripts covering EARLIER
    #     dates BEFORE today's activity is collected, so a correction the team
    #     made out loud yesterday ("yes, but I also did 4 and 5") is in hand
    #     when today's summaries are written. Never fatal: this sits on the
    #     standup critical path, including the timed --standup-interactive one.
    transcript_corrections: dict[str, list[str]] = {}
    transcript_warnings: list[str] = []
    if review_transcripts and (config or {}).get("transcript_review_enabled", True):
        _notify("Reviewing meeting transcripts")
        try:
            from yeaboi.standup import transcript_review as _transcript_review

            reviews = _transcript_review.sweep_and_review(
                session_id, config=config, before_date=date_str, db_path=db_path, today=today
            )
            transcript_corrections, transcript_warnings = _transcript_review.carry_forward(reviews, previous_report)
        except Exception as e:  # belt and braces; the sweep already never raises
            logger.warning("standup: transcript review failed (non-fatal): %s", e)
            transcript_warnings = ["Transcript review skipped — see logs."]

    resolved_channels = channels or (config or {}).get("delivery_channels") or ["terminal"]
    source_params = _resolve_source_params(config)
    selected_trackers = _resolve_tracker_sources(config, tracker_sources, source_params)
    (
        selected_code_sources,
        selected_github_repos,
        selected_azdo_projects,
        selected_azdo_repos,
        dropped_code_sources,
    ) = _resolve_code_scope(config, code_sources, github_repositories, azdo_projects, azdo_repositories)
    source_params["github_repositories"] = selected_github_repos
    source_params["azdo_projects"] = selected_azdo_projects
    source_params["azdo_repositories"] = selected_azdo_repos
    selected_documentation_sources = _resolve_documentation_sources(config, documentation_sources, source_params)
    enabled_sources = _collector_sources(
        source_params,
        selected_trackers,
        selected_code_sources,
        selected_documentation_sources,
    )
    # Worked out here, not in the collector: only this scope knows the difference
    # between "you never ticked GitHub" and "GITHUB_TOKEN is missing".
    skipped_sources, unmet_sources = _skipped_sources(
        source_params,
        enabled_sources,
        selected_trackers,
        selected_code_sources,
        selected_documentation_sources,
        dropped_code_sources,
    )
    # The one line that answers "why is there no GitHub in this run?" after the fact.
    logger.info(
        "run_standup: collecting %s; skipping %s",
        ", ".join(sorted(enabled_sources)) or "nothing",
        ", ".join(f"{src} ({reason})" for src, reason in skipped_sources) or "nothing",
    )

    # 2. Collect recent activity across all resolved sources. Window: start of
    #    the previous working day → now (or an explicit now − days override).
    _notify("Collecting recent activity")

    def collection_progress(message: str) -> None:
        _notify(f"Collecting · {message}")

    # Only practice detection reads ticket prose and the open-ticket pool, and
    # both cost an extra round trip per tracker. Asked once, here, so turning the
    # feature off makes the standup cheaper and not merely quieter.
    practices_wanted = habits.enabled(config)

    if days is None:
        since = collector.previous_working_day_start(today)
        activity_window = f"{since:%a %Y-%m-%d} 00:00 → now"
        bundle = collector.collect_recent_activity(
            since=since,
            sources=enabled_sources,
            on_progress=collection_progress,
            cache_db_path=db_path,
            ticket_context=practices_wanted,
            skipped=skipped_sources,
            **source_params,
        )
    else:
        activity_window = f"last {days} day(s)"
        bundle = collector.collect_recent_activity(
            days=days,
            sources=enabled_sources,
            on_progress=collection_progress,
            cache_db_path=db_path,
            ticket_context=practices_wanted,
            skipped=skipped_sources,
            **source_params,
        )

    # 3. Sprint context + deterministic confidence.
    _notify("Reading sprint progress")
    ctx = sprint_context.gather(
        state,
        jira_project=source_params["jira_project"] if "jira" in selected_trackers else "",
        azdo_project=source_params["azdo_project"] if "azure_devops" in selected_trackers else "",
    )

    # 4. Team members & identity.
    #    Roster: the plan's selected members, or — when the plan has none — the
    #    tracker roster (assignees who did work in the last ~30 days, reusing
    #    performance/roster.fetch_roster), so teammates found in Jira/AzDO
    #    appear even when they have no activity in today's window. Anyone who
    #    self-reported and any unmatched activity author is added too — nobody's
    #    work is silently dropped.
    #    Identity: the standup user's tracker identity is auto-detected (Jira
    #    displayName/email, GitHub login) and merged with configured my_aliases
    #    + git identity, so their activity attaches to THEIR card instead of
    #    appearing as a separate person; with the default "Me" name, the
    #    detected display name replaces the placeholder entirely.
    from yeaboi.config import get_standup_user_name

    _notify("Resolving team & identities")
    my_name = get_standup_user_name()
    display_name, tracker_identities = _detect_tracker_identity()
    if my_name == "Me" and display_name:
        # Default placeholder + a real detected identity → present the user by
        # name. Re-key any "Me" self-report so it stays theirs.
        for mapping in (self_reported, self_reported_images):
            if "Me" in mapping:
                mapping[display_name] = mapping.pop("Me")
        my_name = display_name
        logger.info("standup: resolved standup user to %r via tracker identity", my_name)

    plan_members = [str(name).strip() for name in (state.get("selected_team_members") or ()) if str(name).strip()]
    roster_configured = bool((config or {}).get("roster_configured"))
    if team_members is not None:
        roster_members = list(dict.fromkeys(str(name).strip() for name in team_members if str(name).strip()))
        roster_configured = True
    elif roster_configured:
        roster_members = list(
            dict.fromkeys(str(name).strip() for name in (config or {}).get("team_members", ()) if str(name).strip())
        )
    else:
        roster_members = []
        try:
            from yeaboi.standup.roster import discover_team_members

            roster_members = discover_team_members(
                selected_trackers,
                jira_project=source_params["jira_project"],
                azdo_project=source_params["azdo_project"],
            )
        except Exception as e:  # roster is best-effort — never blocks the standup
            logger.warning("standup: tracker roster lookup failed: %s", e)
        if not roster_members:
            roster_members = plan_members

    # The user's card first, then the rest of the team.
    members = [my_name] + [m for m in roster_members if m != my_name]

    # 4b. Deterministic aggregation: identity closure → roster filter → bot
    #     filter → coverage → grouping → day-over-day insights → practice
    #     detection → confidence. One pure function of the collected inputs
    #     (aggregate.aggregate_standup), served byte-identically by the Go
    #     sidecar when one is discovered (standup.aggregate — see
    #     contracts/v1/rpc.md; YEABOI_GO=0 opts out), silent Python fallback.
    #
    #     The practice adjudicator is the one LLM interleave in that block, so
    #     it is hoisted out by protocol: pass 1 returns the still-unattributed
    #     changes as cases, the model may only DROP some (suppress-only seam —
    #     it returns ids to remove, so there is no channel through which it
    #     could invent or sharpen a report), and a second identical pass applies
    #     the drops. The ledger's deterministic half suppresses the exact
    #     changes the team excused; the model half generalises from the reasons
    #     they gave (feedback_ledger.corrections()).
    _notify("Scoring activity & practices")
    adjudicator = adjudicate.build_adjudicator(config, feedback_ledger.corrections())
    inputs = aggregate.build_aggregate_inputs(
        bundle=bundle,
        members=members,
        my_name=my_name,
        my_aliases=(config or {}).get("my_aliases", ""),
        repo_path=(config or {}).get("repo_path", ""),
        tracker_identities=tracker_identities,
        self_reported_names=list(self_reported),
        config=config,
        previous_report=previous_report,
        transcript_corrections=transcript_corrections,
        corrections=corrections,
        feedback_excused=feedback_ledger.excused,
        enabled_sources=enabled_sources,
        sprint=ctx,
        history=prior_history,
        today=date_str,
        want_adjudication=adjudicator is not None,
    )
    result = aggregate.go_aggregate(inputs) or aggregate.aggregate_standup(inputs)
    cases = aggregate.cases_from_wire(result.get("adjudication_cases") or ())
    if cases and adjudicator is not None:
        try:
            dropped = sorted({str(case_id) for case_id in adjudicator(cases)})
        except Exception:  # an adjudicator failing must never cost the whole report
            logger.warning("standup: practice adjudication failed — keeping every deterministic verdict", exc_info=True)
            dropped = []
        if dropped:
            inputs = {**inputs, "dropped_case_ids": dropped}
            result = aggregate.go_aggregate(inputs) or aggregate.aggregate_standup(inputs)

    members = list(result["members"])
    for dupe in result.get("merged") or ():
        logger.info("standup: merged roster entry %r into the standup user's card", dupe)
    selected_names = set(members)
    self_reported = {name: text for name, text in self_reported.items() if name in selected_names}
    self_reported_images = {name: paths for name, paths in self_reported_images.items() if name in selected_names}
    automation_notices = list(result.get("automation_notices") or ())
    category_coverage = tuple((str(c), str(s)) for c, s in result["category_coverage"])
    if result.get("blocker_signals"):
        logger.info("standup: blocker signals detected for %d member(s)", len(result["blocker_signals"]))
    progress = aggregate.progress_from_wire(result["progress"])

    # 5. Per-member + team summary (one LLM call, deterministic fallback).
    _notify("Writing summaries with AI")
    member_updates, team_summary, llm_warnings = _summarize_members(
        result=result,
        self_reported=self_reported,
        sprint_name=ctx.sprint_name,
        self_reported_images=self_reported_images,
    )

    # One rule, two audiences. A skip is worth chasing when the user asked for the
    # source and did not get it, or when the collector itself gave up mid-run on a
    # source it was told to read (a missing SDK). Everything else is a deliberate
    # non-choice: listed on the diagnostic surfaces, never warned or broadcast about.
    advisable_sources = {
        src for src, reason in bundle.skipped if src in unmet_sources or reason == collector.SKIP_SDK_MISSING
    }

    # Warnings the user must see: source auth failures (from the collector) first,
    # then any LLM/config issue. These render as a "Notices" section, never silent.
    warnings = (
        [f"{src.replace('_', ' ').title()}: {msg}" for src, msg in (*bundle.errors, *bundle.partial_sources)]
        + llm_warnings
        + automation_notices
    )
    if not bundle.counts and not bundle.errors:
        warnings.insert(
            0,
            "No activity sources configured — set a local repo path via Configure, or connect "
            "GitHub/Jira/Azure DevOps/Confluence/Notion in .env, so updates can be inferred from real activity.",
        )
    else:
        # Partial coverage is advised, not silent: one combined line (last — auth/LLM
        # problems above are more urgent) naming each unscanned source and its fix.
        # Only sources the user ASKED for get a notice; the rest are reported in the
        # report's "Not scanned" panel and the progress steps, because a warning
        # about a source nobody selected would repeat on every run forever.
        advisable = [(src, reason) for src, reason in bundle.skipped if src in advisable_sources]
        if advisable:
            skipped = ", ".join(f"{collector.source_label(src)} ({reason})" for src, reason in advisable)
            warnings.append(
                f"Not scanned: {skipped} — pick sources in Standup → Sources, or connect them in .env, "
                "to include their activity in the standup."
            )

    # Transcript-review findings, capped so the notices stay scannable. They go
    # last: an auth failure or a missing source is more urgent than a diagnosis
    # of a standup that already happened.
    if transcript_warnings:
        warnings.extend(transcript_warnings[:_MAX_TRANSCRIPT_NOTICES])
        if len(transcript_warnings) > _MAX_TRANSCRIPT_NOTICES:
            warnings.append(
                f"…and {len(transcript_warnings) - _MAX_TRANSCRIPT_NOTICES} more transcript-review "
                "finding(s) — run `yeaboi standup-review --list-gaps` to see them all."
            )

    # AFTER the cap above, deliberately: a day with three findings must not
    # truncate away the reason the fourth one was never checked at all.
    #
    # Gated on level, not on existence. report.warnings is a BROADCAST surface —
    # it reaches Slack, email and the exports — and "you forgot a file" does not
    # belong in a team channel over a single miss. A persistent one does, because
    # by then the feature is quietly doing nothing.
    try:
        from yeaboi.standup import transcripts as _transcripts

        # before_date excludes TODAY: this run is about to be recorded, and a
        # second run on the same day would otherwise count it as a standup whose
        # meeting went untranscribed — for a meeting that has only just happened.
        nudge = _transcripts.transcript_nudge(
            session_id, config=config, db_path=db_path, before_date=date_str, today=today
        )
        if nudge and nudge.level != "invite":
            warnings.append(nudge.message)
    except Exception as e:  # a nudge must never break a standup
        logger.warning("standup: transcript nudge failed: %s", e)

    report = StandupReport(
        date=date_str,
        session_id=session_id,
        sprint_name=ctx.sprint_name,
        sprint_day=progress.sprint_day,
        sprint_total_days=progress.sprint_total_days,
        confidence_pct=progress.confidence_pct,
        confidence_label=progress.confidence_label,
        confidence_rationale=progress.confidence_rationale,
        confidence_delta=progress.confidence_delta,
        confidence_trend=progress.confidence_trend,
        team_summary=team_summary,
        member_updates=tuple(member_updates),
        # The aggregate's counts, not the raw collector's: the roster and
        # automation filters recompute per-source numbers.
        activity_counts=tuple((str(s), int(n)) for s, n in result["counts"]),
        activity_window=activity_window,
        skipped_sources=tuple(bundle.skipped),
        # Carried so the broadcast renderers can tell a disappointment from a
        # deliberate non-choice long after the run that classified them.
        unmet_sources=tuple(sorted(advisable_sources)),
        category_coverage=category_coverage,
        my_name=my_name,
        warnings=tuple(warnings),
        # Screenshots pasted into "My Update" — carried on the report so the
        # Markdown/HTML/Notion/Confluence exports can embed them.
        images=tuple(p for paths in self_reported_images.values() for p in paths),
        # Rebuilt from the updates, not from `practices`, so a member dropped
        # between detection and the report can never inflate the team count.
        practice_rollup=habits.rollup({m.name: m.practices for m in member_updates if m.practices}),
    )

    # 6. Deliver, then record the run (so delivery status is captured).
    _notify("Saving & exporting")
    delivery_status: dict[str, bool] = {}
    status = "success"
    if deliver and not dry_run:
        try:
            from yeaboi.standup import delivery

            delivery_status = delivery.deliver(report, resolved_channels)
            if delivery_status and not all(delivery_status.values()):
                status = "partial"
        except Exception as e:
            logger.error("standup delivery raised: %s", e)
            status = "partial"

    with StandupStore(db_path) as store:
        store.record_run(report, delivery_status=delivery_status, status=status)
        # Fetched after record_run so today's confidence is part of the trend
        # the HTML export draws.
        run_history = store.get_history(session_id, limit=30)

    # Persist readable output (Markdown + HTML) alongside the logs, so a standup's
    # result is a shareable document — not something you can only reconstruct from
    # a log file. Best-effort: never fail the run over an export I/O error.
    try:
        from yeaboi.standup.export import export_standup

        export_standup(report, project_name=state.get("project_name", "") or session_id, history=run_history)
    except Exception as e:
        logger.warning("standup export failed: %s", e)

    logger.info(
        "run_standup complete: session=%s day=%d/%d confidence=%d%% status=%s",
        session_id,
        report.sprint_day,
        report.sprint_total_days,
        report.confidence_pct,
        status,
    )
    return report


def transcript_nudge(session_id: str, *, db_path=None, today: date | None = None) -> TranscriptNudge:
    """Which standups were never checked against their meeting, and how loudly to say so.

    Deterministic and offline — two indexed queries, no LLM — because the TUI
    calls it on every hub refresh. Nothing is stored: the answer is a set
    difference between the dates a standup ran and the dates a transcript was
    reviewed for, both already in the database, so there is no "last nudged"
    state to migrate or get wrong.

    Returns a falsy ``TranscriptNudge`` when there is nothing to say, which is
    the normal case.
    """
    from yeaboi.paths import get_db_path
    from yeaboi.standup import transcripts as _transcripts

    resolved_db = db_path or get_db_path()
    with StandupStore(resolved_db) as store:
        config = store.load_config(session_id) or {}
    return _transcripts.transcript_nudge(session_id, config=config, db_path=resolved_db, today=today)


def import_transcript(
    text: str,
    *,
    covered_date: str = "",
    label: str = "",
    today: date | None = None,
) -> TranscriptSource:
    """Save transcript TEXT into the managed folder and describe what landed.

    The intake half of the review pipeline, for material that never was a file —
    a clipboard paste, a piped stdin, an MCP argument. Everything downstream
    still sees an ordinary file in ``~/.yeaboi/transcripts``, so nothing in the
    sweep, the content-hash ledger or the date attribution needs to know a paste
    happened.

    Returns the same ``TranscriptSource`` a discovered file produces, so the
    caller can confirm what actually landed ("covers 2026-08-01 · 4 speakers ·
    labelled") rather than just "saved". Attribution is the part worth showing:
    an unlabelled transcript narrows what the review is then allowed to conclude,
    and the user can still fix that by re-copying with speaker names.

    Raises ``ValueError`` on empty or oversized text, or a malformed
    ``covered_date`` — an intake primitive with a clear input contract, whose
    callers want the message.
    """
    from yeaboi.standup import transcripts as _transcripts

    path = _transcripts.import_text(text, covered_date=covered_date, label=label, today=today)
    source, _turns = _transcripts.read_transcript(path, today=today)
    logger.info(
        "import_transcript: %s covers %s (%s, %d speaker(s), %d chars)",
        source.filename,
        source.covered_date,
        source.attribution,
        len(source.speakers),
        source.char_count,
    )
    return source


def run_transcript_review(
    session_id: str,
    *,
    transcript_paths: list[str] | None = None,
    transcript_text: str = "",
    transcript_dir: str = "",
    standup_date: str = "",
    max_transcripts: int = 5,
    include_reviewed: bool = False,
    db_path=None,
    today: date | None = None,
    on_progress=None,
) -> TranscriptReview:
    """Review standup meeting transcripts against the reports they discussed.

    Absorbs a transcript of the standup, checks each claim people made against
    the evidence the report actually had, and diagnoses WHY anything missing was
    invisible — a missing integration, an unconfigured source, a capability the
    collectors lack, or a summary that dropped what it collected.

    This function ALWAYS drafts and NEVER writes to GitHub. It has no
    ``file_issues`` parameter by design: filing is a separate, explicit act
    (``file_transcript_issues``), so a scheduled run cannot publish to a public
    repository. Product-level gaps are staged in the local ledger; config-level
    ones stay as suggestions with an exact remedy.

    Args:
        transcript_paths: explicit files to review, bypassing the folder sweep.
        transcript_text: raw transcript text to import and review — a paste, a
            pipe, an agent's argument. Saved via ``import_transcript`` first, so
            it is reviewed exactly like a file the user dropped in themselves.
        transcript_dir: an external transcript folder for this run only; the
            managed ~/.yeaboi/transcripts folder is always swept as well.
        standup_date: the date to attribute transcripts to when their own date
            cannot be inferred. For ``transcript_text`` it is stronger than that:
            somebody who passes both a date and the text means that date, so it
            wins over a date found inside the text.
        max_transcripts: cap on distinct standup DATES reviewed in one call —
            each date costs one LLM call.
        include_reviewed: re-review transcripts already in the ledger.
        db_path/today/on_progress: injection seams (see run_standup).

    Returns the newest review; when several dates were reviewed the rest are
    persisted and readable through ``StandupStore.get_reviews``.
    """
    from yeaboi.agent.state import TranscriptReview
    from yeaboi.paths import get_db_path
    from yeaboi.standup import transcript_review as _review
    from yeaboi.standup.store import StandupStore

    def _notify(phase: str) -> None:
        if on_progress:
            try:
                on_progress(phase)
            except Exception:
                logger.debug("standup review: on_progress callback failed", exc_info=True)

    resolved_db = db_path or get_db_path()
    today = today or date.today()
    logger.info(
        "run_transcript_review: session=%s date=%s paths=%d",
        session_id,
        standup_date or "-",
        len(transcript_paths or []),
    )

    with StandupStore(resolved_db) as store:
        config = store.load_config(session_id) or {}
    if transcript_dir:
        config = {**config, "transcript_dir": transcript_dir}

    if transcript_text.strip():
        _notify("Saving the pasted transcript")
        try:
            imported = import_transcript(transcript_text, covered_date=standup_date, label="pasted", today=today)
        except (ValueError, OSError) as exc:
            # A bad paste is a user-input problem, not a pipeline failure — this
            # surface never raises, so it comes back as a warning like every
            # other reason a review found nothing to say. OSError is in here for
            # the read-BACK: import_transcript writes the file and then reads it
            # to report what landed, and read_transcript raises by contract, so a
            # disk that filled between the two would otherwise reach an MCP
            # client as a traceback instead of a warning.
            logger.warning("run_transcript_review: import failed: %s", exc)
            return TranscriptReview(
                session_id=session_id,
                standup_date=standup_date,
                reviewed_at=datetime.now(UTC).isoformat(),
                llm_mode="deterministic",
                warnings=(str(exc),),
            )
        # Explicit paths bypass the folder sweep, which is what we want: an
        # import the user just made is reviewed now, not queued behind a backlog.
        transcript_paths = [imported.path, *(transcript_paths or [])]

    _notify("Reading transcripts")
    reviews = _review.sweep_and_review(
        session_id,
        config=config,
        db_path=resolved_db,
        today=today,
        transcript_paths=transcript_paths,
        standup_date=standup_date,
        max_dates=max_transcripts,
        include_reviewed=include_reviewed,
    )
    _notify("Diagnosing gaps")

    if not reviews:
        logger.info("run_transcript_review: nothing to review for session=%s", session_id)
        return TranscriptReview(
            session_id=session_id,
            standup_date=standup_date,
            reviewed_at=datetime.now(UTC).isoformat(),
            llm_mode="deterministic",
            warnings=("No unreviewed transcripts found. Drop one in ~/.yeaboi/transcripts and try again.",),
        )
    return reviews[-1]


def file_transcript_issues(
    review_id: int,
    *,
    session_id: str = "",
    gap_ids: list[str] | None = None,
    db_path=None,
) -> IssueFilingResult:
    """File a review's product gaps as GitHub issues. The explicit confirm act.

    This is the ONLY path in the feature that writes to GitHub, and only a
    deliberate user action reaches it. A gap already filed gets fresh evidence as
    a comment rather than a duplicate issue; a gap whose issue was CLOSED gets a
    new one referencing the old. Config-level suggestions are never filed —
    those are the user's to fix.

    Never raises: per-gap failures come back as GapIssueLink states.
    """
    from yeaboi.agent.state import IssueFilingResult
    from yeaboi.paths import get_db_path
    from yeaboi.standup import gap_issues
    from yeaboi.standup.store import StandupStore

    resolved_db = db_path or get_db_path()
    with StandupStore(resolved_db) as store:
        review = store.get_review(review_id) if review_id else store.get_latest_review(session_id)
    if review is None:
        logger.warning("file_transcript_issues: no review found (id=%s session=%s)", review_id, session_id)
        return IssueFilingResult(
            review_id=review_id,
            warnings=("No transcript review found to file — run a review first.",),
        )

    report = None
    if review.run_id:
        with StandupStore(resolved_db) as store:
            report = store.get_run_by_id(review.run_id)

    logger.info("file_transcript_issues: filing %d gap(s) from review %s", len(review.gaps), review.review_id)
    return gap_issues.file_review_gaps(review, report=report, gap_ids=gap_ids, db_path=resolved_db)
