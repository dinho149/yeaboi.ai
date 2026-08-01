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
from datetime import date

from yeaboi.agent.state import ActivityEvidence, MemberUpdate, StandupReport
from yeaboi.standup import automation, categories, collector, confidence, insights, sprint_context
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


def _resolve_code_scope(
    config: dict | None,
    code_sources: list[str] | None,
    github_repositories: list[str] | None,
    azdo_projects: list[str] | None,
    azdo_repositories: list[str] | None,
) -> tuple[list[str], list[str], list[str], list[str] | None]:
    """Resolve GitHub repositories and Azure project scope."""
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
    if configured:
        if "github" in sources and not github:
            sources.remove("github")
        if "azure_devops" in sources and not projects and not legacy_repositories:
            sources.remove("azure_devops")
    # Explicit project scope wins. Legacy repositories remain available only
    # when callers supply them and do not supply projects.
    if azdo_projects is not None or projects:
        legacy_repositories = None
    return sources, github, projects, legacy_repositories


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
            grouped[member].append(
                {
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
                }
            )
    return grouped


def _rebuild_bundle(bundle: collector.ActivityBundle, items: list[dict]) -> collector.ActivityBundle:
    """Return a copy of ``bundle`` holding only ``items``, with per-source counts recomputed.

    Carries errors, skipped, AND partial_sources — dropping partial_sources here
    used to silently swallow partial-coverage warnings downstream.
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


# Commit → PR association lives in title text only: collectors emit pr_id and
# branch on PR items, but a commit names its PR solely via its subject. The
# real-world formats, one pattern each: GitHub/AzDO merge commits
# ("Merge pull request #91 …" / "Merge pull request 48806 …"), AzDO squash
# merges ("Merged PR 123: Title"), and parenthesised references — GitHub squash
# merges end in "(#91)" and the collector's own PR-branch scan appends
# "(PR #91)". All are gated on a matching PR existing in the same repository's
# window, so a stray "(#12)" in prose cannot fold a commit under nothing.
_PR_NUMBER_RES = (
    re.compile(r"Merge pull request #?(\d+)"),
    re.compile(r"Merged PR (\d+):"),
    re.compile(r"\((?:PR )?#(\d+)\)"),
)
_MERGE_BRANCH_RE = re.compile(r"Merge pull request .*? from (\S+)")


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


def _member_evidence(acts: list[dict], cap: int = 8) -> tuple[ActivityEvidence, ...]:
    """Structured evidence rows from a member's grouped activity.

    Unlike ``_member_links`` this keeps the title, kind, repository, and status
    the collectors fetched, and keeps URL-less items (an in-progress ticket with
    no link still says something). Rows are ordered newest-first — the day's
    movement (a ticket landing in Done) belongs in the visible top rows, while
    carried WIP (which Jira stamps with an empty timestamp) folds. Deduped in
    that order — by URL when there is one, else by (kind, key, title) — so the
    latest event for a ticket is the one that survives.
    """
    # Timestamps are ISO-8601 strings, so string comparison is chronological.
    # Descending puts the empty string — timestamp-less carried WIP — last, and
    # the sort is stable, so equal stamps keep collector order.
    ordered = sorted(acts, key=lambda a: str(a.get("timestamp") or ""), reverse=True)
    seen: set[str] = set()
    rows: list[ActivityEvidence] = []
    for a in ordered:
        url = (a.get("url") or "").strip()
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
                children=_member_evidence(a["children"], cap=6) if a.get("children") else (),
            )
        )
        if len(rows) >= cap:
            break
    return tuple(rows)


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


_TICKET_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def _fallback_progress_note(yesterday_entry: dict, acts: list[dict]) -> str:
    """Carried-over-work note without an LLM: yesterday's ticket keys ∩ today's.

    Only states what the data proves (the same ticket keys appear on both
    days); anything more interpretive is left to the LLM path.
    """
    yesterday_text = " ".join(str(v) for v in yesterday_entry.values())
    yesterday_keys = set(_TICKET_KEY_RE.findall(yesterday_text))
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
) -> list[MemberUpdate]:
    """Deterministic per-member updates when the LLM is unavailable.

    Every member gets an activity-derived summary (a plain join of their
    activity titles); a self-report is carried alongside as supporting context,
    never replacing the activity view. Detected blocker signals become the
    blockers text directly, so blocker highlighting works without an LLM.
    """
    updates: list[MemberUpdate] = []
    coverage = coverage or {category: categories.COVERED for category in categories.CATEGORIES}
    blocker_signals = blocker_signals or {}
    yesterday = yesterday or {}
    for name, acts in grouped.items():
        split = categories.split_activity(acts)
        summary = _fallback_summary(acts)
        if summary == "No activity detected." and self_reported.get(name):
            summary = self_reported[name]
        updates.append(
            MemberUpdate(
                name=name,
                summary=summary,
                blockers="; ".join(blocker_signals.get(name, ())),
                progress_note=_fallback_progress_note(yesterday.get(name, {}), acts),
                outlook=_fallback_outlook(acts),
                self_report=self_reported.get(name, ""),
                source=_member_source(name in self_reported, bool(acts)),
                links=_member_links(acts),
                activity_count=len(acts),
                code_summary=_fallback_category_summary(
                    categories.CATEGORY_CODE, split[categories.CATEGORY_CODE], coverage[categories.CATEGORY_CODE]
                ),
                code_links=_member_links(split[categories.CATEGORY_CODE]),
                code_activity_count=len(split[categories.CATEGORY_CODE]),
                documentation_summary=_fallback_category_summary(
                    categories.CATEGORY_DOCUMENTATION,
                    split[categories.CATEGORY_DOCUMENTATION],
                    coverage[categories.CATEGORY_DOCUMENTATION],
                ),
                documentation_links=_member_links(split[categories.CATEGORY_DOCUMENTATION]),
                documentation_activity_count=len(split[categories.CATEGORY_DOCUMENTATION]),
                ticketing_summary=_fallback_category_summary(
                    categories.CATEGORY_TICKETING,
                    split[categories.CATEGORY_TICKETING],
                    coverage[categories.CATEGORY_TICKETING],
                ),
                ticketing_links=_member_links(split[categories.CATEGORY_TICKETING]),
                ticketing_activity_count=len(split[categories.CATEGORY_TICKETING]),
                ticketing_evidence=_member_evidence(split[categories.CATEGORY_TICKETING]),
                code_evidence=_member_evidence(_nest_pr_commits(split[categories.CATEGORY_CODE])),
                documentation_evidence=_member_evidence(split[categories.CATEGORY_DOCUMENTATION]),
            )
        )
    # Self-reporters missing from the grouping (shouldn't happen — run_standup
    # adds them to the roster) still surface rather than silently dropping.
    for name, text in self_reported.items():
        if name not in grouped:
            updates.append(
                MemberUpdate(
                    name=name,
                    summary=text or "No activity detected.",
                    self_report=text,
                    source="self-reported",
                    code_summary=categories.empty_summary(categories.CATEGORY_CODE, coverage[categories.CATEGORY_CODE]),
                    documentation_summary=categories.empty_summary(
                        categories.CATEGORY_DOCUMENTATION, coverage[categories.CATEGORY_DOCUMENTATION]
                    ),
                    ticketing_summary=categories.empty_summary(
                        categories.CATEGORY_TICKETING, coverage[categories.CATEGORY_TICKETING]
                    ),
                )
            )
    return updates


def _build_fallback_team_summary(bundle: collector.ActivityBundle, progress: confidence.SprintProgress) -> str:
    """Deterministic team summary when the LLM is unavailable."""
    counts = ", ".join(f"{src}: {n}" for src, n in bundle.counts) or "no sources"
    return (
        f"{bundle.total()} activity item(s) detected ({counts}). "
        f"Sprint status: {progress.confidence_label}. {progress.confidence_rationale}"
    ).strip()


def _summarize_members(
    *,
    bundle: collector.ActivityBundle,
    progress: confidence.SprintProgress,
    members: list[str],
    self_reported: dict[str, str],
    sprint_name: str,
    self_reported_images: dict[str, list[str]] | None = None,
    alias_map: dict[str, set[str]] | None = None,
    category_coverage: tuple[tuple[str, str], ...] = (),
    grouped: dict[str, list[dict]] | None = None,
    blocker_signals: dict[str, tuple[str, ...]] | None = None,
    yesterday: dict[str, dict] | None = None,
) -> tuple[list[MemberUpdate], str, list[str]]:
    """Produce (member_updates, team_summary, warnings) via one LLM call + deterministic fallback.

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
    grouped/blocker_signals/yesterday: precomputed by run_standup (grouping is
        shared with insights); all default to None so direct callers/tests keep
        working — grouped is recomputed here when absent.
    """
    if grouped is None:
        grouped = _group_activity_by_author(bundle.items, members, alias_map)
    blocker_signals = blocker_signals or {}
    yesterday = yesterday or {}
    coverage = dict(category_coverage) or {category: categories.COVERED for category in categories.CATEGORIES}

    def _for_llm(acts: list[dict]) -> list[dict]:
        # URLs (and the keys they duplicate — titles already carry ticket ids,
        # as does the standalone summary field) are for rendering links, not
        # reasoning; strip them to keep the prompt lean. pr_id/branch/timestamp
        # exist only so evidence rows can fold and sort — same treatment.
        rendering_only = ("url", "key", "summary", "pr_id", "branch", "timestamp")
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
            _build_fallback_member_updates(
                grouped, self_reported, coverage, blocker_signals=blocker_signals, yesterday=yesterday
            ),
            _build_fallback_team_summary(bundle, progress),
            extra_warnings,
        )

    # Nothing to reason over (no activity anywhere and no self-reports) →
    # deterministic fallback only; don't spend an LLM call saying "no activity".
    if (not member_payload or not bundle.items) and not self_reported:
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
        activity_counts=bundle.counts,
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
    # alongside on self_report (shown as "their words" by the renderers).
    updates: list[MemberUpdate] = []
    llm_members = {m.get("name", ""): m for m in parsed.get("members", []) if isinstance(m, dict)}
    for name in members:
        m = llm_members.get(name, {})
        summary = (m.get("summary") or "").strip()
        acts = grouped.get(name, [])
        split = categories.split_activity(acts)
        if not summary:
            summary = _fallback_summary(acts)
            if summary == "No activity detected." and self_reported.get(name):
                summary = self_reported[name]

        def _structured_summary(category: str, field: str) -> str:
            evidence = split[category]
            # The model is never allowed to turn absent/unavailable evidence
            # into claimed work; deterministic coverage wording wins when empty.
            if not evidence:
                return _fallback_category_summary(category, evidence, coverage[category])
            return (m.get(field) or "").strip() or _fallback_category_summary(category, evidence, coverage[category])

        code_summary = _structured_summary(categories.CATEGORY_CODE, "code_summary")
        documentation_summary = _structured_summary(categories.CATEGORY_DOCUMENTATION, "documentation_summary")
        ticketing_summary = _structured_summary(categories.CATEGORY_TICKETING, "ticketing_summary")
        # Deterministic clamps on the day-over-day fields: no progress note
        # without an actual previous standup to compare against (the model can
        # never invent a "yesterday"), and detected blocker signals surface
        # even if the model dropped them from its 'blockers'.
        progress_note = (m.get("progress_note") or "").strip() if name in yesterday else ""
        blockers = (m.get("blockers") or "").strip()
        if not blockers and blocker_signals.get(name):
            blockers = "; ".join(blocker_signals[name])
        updates.append(
            MemberUpdate(
                name=name,
                summary=summary,
                blockers=blockers,
                progress_note=progress_note,
                outlook=(m.get("outlook") or "").strip(),
                self_report=self_reported.get(name, ""),
                source=_member_source(name in self_reported, bool(acts)),
                links=_member_links(acts),
                activity_count=len(acts),
                code_summary=code_summary,
                code_links=_member_links(split[categories.CATEGORY_CODE]),
                code_activity_count=len(split[categories.CATEGORY_CODE]),
                documentation_summary=documentation_summary,
                documentation_links=_member_links(split[categories.CATEGORY_DOCUMENTATION]),
                documentation_activity_count=len(split[categories.CATEGORY_DOCUMENTATION]),
                ticketing_summary=ticketing_summary,
                ticketing_links=_member_links(split[categories.CATEGORY_TICKETING]),
                ticketing_activity_count=len(split[categories.CATEGORY_TICKETING]),
                ticketing_evidence=_member_evidence(split[categories.CATEGORY_TICKETING]),
                code_evidence=_member_evidence(_nest_pr_commits(split[categories.CATEGORY_CODE])),
                documentation_evidence=_member_evidence(split[categories.CATEGORY_DOCUMENTATION]),
            )
        )

    team_summary = (parsed.get("team_summary") or "").strip() or _build_fallback_team_summary(bundle, progress)
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
        previous_report = previous_run[2] if previous_run else None
        prior_history = store.get_history(session_id, limit=10)

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
                corrections = edit_store.list_edits("standup", artifact_ref("standup", run_id=previous_run[0]))
        except Exception:  # noqa: BLE001 — a missing hint is not a failed standup
            logger.warning("Could not read previous standup corrections", exc_info=True)

    resolved_channels = channels or (config or {}).get("delivery_channels") or ["terminal"]
    source_params = _resolve_source_params(config)
    selected_trackers = _resolve_tracker_sources(config, tracker_sources, source_params)
    selected_code_sources, selected_github_repos, selected_azdo_projects, selected_azdo_repos = _resolve_code_scope(
        config, code_sources, github_repositories, azdo_projects, azdo_repositories
    )
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

    # 2. Collect recent activity across all resolved sources. Window: start of
    #    the previous working day → now (or an explicit now − days override).
    _notify("Collecting recent activity")

    def collection_progress(message: str) -> None:
        _notify(f"Collecting · {message}")

    if days is None:
        since = collector.previous_working_day_start(today)
        activity_window = f"{since:%a %Y-%m-%d} 00:00 → now"
        bundle = collector.collect_recent_activity(
            since=since,
            sources=enabled_sources,
            on_progress=collection_progress,
            cache_db_path=db_path,
            **source_params,
        )
    else:
        activity_window = f"last {days} day(s)"
        bundle = collector.collect_recent_activity(
            days=days,
            sources=enabled_sources,
            on_progress=collection_progress,
            cache_db_path=db_path,
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
    alias_map = _build_alias_map(
        members,
        my_name=my_name,
        my_aliases=(config or {}).get("my_aliases", ""),
        repo_path=(config or {}).get("repo_path", ""),
        # "Me" stays an alias so legacy self-reports/config still match.
        extra_identities=(*tracker_identities, "Me"),
    )
    # Every member (not just the user) learns the emails the sources exposed for
    # them, so cross-source work (git commits vs tracker display names) attaches
    # to the right card instead of spawning a phantom member below.
    _enrich_aliases_from_items(alias_map, bundle.items)
    # Drop roster/plan entries that are actually the standup user under another
    # name (e.g. their Jira displayName) — one person, one card.
    my_alias_set = alias_map.get(my_name, set())
    for dupe in [m for m in members if m != my_name and _normalize_author(m) & my_alias_set]:
        members.remove(dupe)
        alias_map.pop(dupe, None)
        logger.info("standup: merged roster entry %r into the standup user's card", dupe)
    selected_names = set(members)
    self_reported = {name: text for name, text in self_reported.items() if name in selected_names}
    self_reported_images = {name: paths for name, paths in self_reported_images.items() if name in selected_names}
    # The Team selection is authoritative. Unlike the legacy behavior, an
    # unmatched activity author is never promoted into a new standup member.
    bundle = _filter_bundle_to_members(bundle, alias_map)
    # Service hooks (e.g. a Wiz scanner using a member's PAT) post as the human;
    # strip that noise BEFORE coverage/confidence/summaries so it never counts
    # as personal work. Exclusions surface as Notices below.
    bundle, automation_notices = _drop_automated_activity(bundle, config)
    category_coverage = categories.coverage_states(enabled_sources, bundle)

    # Day-over-day insights over the final (roster-filtered, de-botted) view:
    # deterministic blocker evidence + each member's previous-standup context.
    grouped = _group_activity_by_author(bundle.items, members, alias_map)
    blocker_signals = insights.detect_blocker_signals(grouped, previous_report=previous_report)
    yesterday = insights.yesterday_context(previous_report, corrections=corrections)
    if blocker_signals:
        logger.info("standup: blocker signals detected for %d member(s)", len(blocker_signals))

    # Confidence must use the roster-filtered activity, otherwise work by an
    # excluded outsider can make this team's sprint appear healthier. Prior
    # runs feed the trend (delta + sustained-decline damping).
    progress = confidence.compute(
        sprint_name=ctx.sprint_name,
        start_date=ctx.start_date,
        sprint_length_weeks=ctx.sprint_length_weeks,
        capacity_points=ctx.capacity_points if ctx.have_burn else 0.0,
        completed_points=ctx.completed_points,
        activity_count=bundle.total(exclude_kinds=("wip",)),
        today=today,
        history=prior_history,
    )

    # 5. Per-member + team summary (one LLM call, deterministic fallback).
    _notify("Writing summaries with AI")
    member_updates, team_summary, llm_warnings = _summarize_members(
        bundle=bundle,
        progress=progress,
        members=members,
        self_reported=self_reported,
        sprint_name=ctx.sprint_name,
        self_reported_images=self_reported_images,
        alias_map=alias_map,
        category_coverage=category_coverage,
        grouped=grouped,
        blocker_signals=blocker_signals,
        yesterday=yesterday,
    )

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
    elif bundle.skipped:
        # Partial coverage is advised, not silent: one combined line (last — auth/LLM
        # problems above are more urgent) naming each unscanned source and its fix.
        skipped = ", ".join(f"{src.replace('_', ' ').title()} ({reason})" for src, reason in bundle.skipped)
        warnings.append(f"Not scanned: {skipped} — connect these in .env to include their activity in the standup.")

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
        activity_counts=tuple(bundle.counts),
        activity_window=activity_window,
        skipped_sources=tuple(bundle.skipped),
        category_coverage=category_coverage,
        my_name=my_name,
        warnings=tuple(warnings),
        # Screenshots pasted into "My Update" — carried on the report so the
        # Markdown/HTML/Notion/Confluence exports can embed them.
        images=tuple(p for paths in self_reported_images.values() for p in paths),
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
