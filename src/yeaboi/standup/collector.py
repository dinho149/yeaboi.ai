"""Recent-activity collector for Daily Standup mode.

Fans out to every configured source (Jira, Azure DevOps, GitHub, local git,
Confluence), normalizes results into a single stream of activity items, and
tallies per-source counts. Every source is best-effort: an unconfigured or
failing source contributes zero items and never aborts the standup.

The per-source tool helpers are imported LAZILY inside each branch — their SDKs
(PyGithub, jira, azure-devops, atlassian) are optional extras that may not be
installed, exactly like tools/__init__.py:get_tools(). A missing SDK degrades
that one source to empty, same as a missing credential.

# See docs: "Daily Standup" — recent-activity collection
# See docs: "Tools" — lazy imports for optional integration SDKs
"""

from __future__ import annotations

import logging
import threading
import time as time_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

logger = logging.getLogger(__name__)

# Canonical source identifiers (also used as the "source" tag on each item).
SOURCE_JIRA = "jira"
SOURCE_AZDO = "azure_devops"
SOURCE_AZDO_REPOS = "azdo_repos"  # AzDO git commits/PRs — separate key so a repo-API failure never hides work items
SOURCE_GITHUB = "github"
SOURCE_LOCAL_GIT = "local_git"
SOURCE_CONFLUENCE = "confluence"
SOURCE_NOTION = "notion"

ALL_SOURCES = (
    SOURCE_JIRA,
    SOURCE_AZDO,
    SOURCE_AZDO_REPOS,
    SOURCE_GITHUB,
    SOURCE_LOCAL_GIT,
    SOURCE_CONFLUENCE,
    SOURCE_NOTION,
)

_SOURCE_LABELS = {
    SOURCE_JIRA: "Jira",
    SOURCE_AZDO: "Azure DevOps tickets",
    SOURCE_AZDO_REPOS: "Azure DevOps code",
    SOURCE_GITHUB: "GitHub",
    SOURCE_LOCAL_GIT: "Local Git",
    SOURCE_CONFLUENCE: "Confluence",
    SOURCE_NOTION: "Notion",
}


def source_label(source: str) -> str:
    """The name a user sees for a collector source ("azdo_repos" → "Azure DevOps code").

    Public because the report renderers show the same skip list the progress steps
    do, and ``"azdo_repos".title()`` reads as "Azdo Repos" in all three.
    """
    return _SOURCE_LABELS.get(source, source.replace("_", " ").title())


# The one reason string the collector appends at run time rather than being told.
# Shared because the engine keys its "worth chasing" rule off it, and a reword
# here would otherwise silently stop that rule firing.
SKIP_SDK_MISSING = "SDK not installed"

# Human-readable reason shown when a source is auto-disabled (config missing).
# Public for the same reason ``source_label`` is: the engine builds the skip
# list for an explicit source set and needs the identical wording.
SKIP_REASONS = {
    SOURCE_JIRA: "JIRA_PROJECT_KEY not set",
    SOURCE_AZDO: "AZURE_DEVOPS_PROJECT not set",
    SOURCE_GITHUB: "STANDUP_GITHUB_REPO not set",
    SOURCE_LOCAL_GIT: "no repo path configured",
    SOURCE_CONFLUENCE: "CONFLUENCE_SPACE_KEY not set",
    SOURCE_NOTION: "NOTION_ROOT_PAGE_ID not set",
}


def previous_working_day_start(today: date) -> datetime:
    """Local midnight at the start of the last working day (Mon-Fri) before today.

    This is the standup activity-window start: a Monday (or weekend) run reaches
    back to Friday 00:00 so weekend standups still capture Friday's work, and a
    midweek run covers the FULL previous day plus today so far — not just the
    last 24 hours. Same Mon-Fri convention as confidence.working_days_between.
    """
    d = today - timedelta(days=1)
    while d.weekday() >= 5:  # Sat/Sun → keep stepping back to Friday
        d -= timedelta(days=1)
    # tz-aware local midnight, so client-side helpers can compare against UTC.
    return datetime.combine(d, time.min).astimezone()


@dataclass
class ActivityBundle:
    """Normalized recent activity plus per-source counts and surfaced errors.

    items: each dict has {source, author, kind, title, timestamp, key}
        (+optional status, +optional author_email — best-effort, often hidden by
        Atlassian privacy settings, never rely on it being present).
    counts: (source, count) pairs for every source that was attempted.
    errors: (source, message) pairs for auth/other failures the user must see
        (e.g. a 401/403 that would otherwise look like "no activity").
    partial_sources: (source, message) pairs for a source whose authoritative
        base activity succeeded but optional enrichment was incomplete.
    skipped: (source, reason) pairs for sources that were NOT attempted — missing
        config or SDK — so absent coverage is visible instead of silent.
    reference_tickets: open tickets nobody necessarily touched today, carried
        purely as matching CONTEXT for the practice rules (kind="ticket_context").
        Deliberately NOT in `items`: they are not activity, and putting them
        there would give them evidence rows, category counts and prompt tokens.
        Nothing but habits.detect_practices may read them.
    """

    items: list[dict] = field(default_factory=list)
    counts: list[tuple[str, int]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    partial_sources: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    reference_tickets: list[dict] = field(default_factory=list)

    def total(self, *, exclude_kinds: tuple[str, ...] = ()) -> int:
        if not exclude_kinds:
            return len(self.items)
        return sum(1 for i in self.items if i.get("kind") not in exclude_kinds)

    def authors(self) -> list[str]:
        """Distinct non-empty author names seen across all activity, preserving order."""
        seen: dict[str, None] = {}
        for item in self.items:
            name = (item.get("author") or "").strip()
            if name and name not in seen:
                seen[name] = None
        return list(seen)


def _resolve_sources(
    explicit: set[str] | None,
    *,
    jira_project: str,
    azdo_project: str,
    github_repo: str,
    local_repo_path: str,
    confluence_space: str,
    notion_root: str = "",
) -> set[str]:
    """Decide which sources to attempt.

    When ``explicit`` is given, use it verbatim. Otherwise auto-enable a source
    when its identifying parameter is present (repo path, project key, etc.).
    """
    if explicit is not None:
        return set(explicit)
    auto: set[str] = set()
    if jira_project:
        auto.add(SOURCE_JIRA)
    if azdo_project:
        auto.add(SOURCE_AZDO)
        auto.add(SOURCE_AZDO_REPOS)  # same credential/project unlocks repo activity too
    if github_repo:
        auto.add(SOURCE_GITHUB)
    if local_repo_path:
        auto.add(SOURCE_LOCAL_GIT)
    if confluence_space:
        auto.add(SOURCE_CONFLUENCE)
    if notion_root:
        auto.add(SOURCE_NOTION)
    return auto


def collect_recent_activity(
    *,
    days: int = 1,
    since: datetime | None = None,
    sources: set[str] | None = None,
    jira_project: str = "",
    azdo_project: str = "",
    github_repo: str = "",
    github_repositories: list[str] | None = None,
    azdo_projects: list[str] | None = None,
    azdo_repositories: list[str] | None = None,
    local_repo_path: str = "",
    confluence_space: str = "",
    notion_root: str = "",
    on_progress=None,
    cache_db_path=None,
    ticket_context: bool = True,
    skipped: list[tuple[str, str]] | None = None,
) -> ActivityBundle:
    """Gather and normalize recent activity from all enabled sources.

    The window is ``since → now``. Prefer passing ``since`` (the engine passes
    previous_working_day_start so weekend/Monday runs still cover Friday);
    ``days`` is the legacy now-minus-N-days fallback when ``since`` is None.

    Each source's helper already degrades to [] on error; this function adds the
    ``source`` tag, tallies counts, and guards the lazy import so a missing SDK
    (ImportError) simply skips that source.

    ``ticket_context`` buys the two things only practice detection reads: each
    ticket's description/acceptance-criteria/definition-of-done, and a search for
    the open tickets nobody touched today (``bundle.reference_tickets``). It is
    real money — a second Jira search over 200 issues with full text, a second
    Azure WIQL plus batch fetch — so a team that has turned practice detection
    off must not pay it. Off means the standup is exactly as expensive as it was
    before this existed.

    ``skipped`` is how a caller that passes an explicit ``sources`` set still gets
    a coverage story. Auto-detection below can work out why a source is off, but a
    caller with an explicit set knows more than we do — it can tell "no token" from
    "the user unticked it in setup" — so it hands the reasons in rather than having
    them guessed. Without this a deselected source is invisible: no skip line, no
    progress step, and a report that reads as though GitHub had nothing to say.
    """
    enabled = _resolve_sources(
        sources,
        jira_project=jira_project,
        azdo_project=azdo_project,
        github_repo=github_repo,
        local_repo_path=local_repo_path,
        confluence_space=confluence_space,
        notion_root=notion_root,
    )
    logger.info(
        "collect_recent_activity: since=%s days=%d enabled sources=%s",
        since.isoformat() if since else None,
        days,
        sorted(enabled),
    )

    bundle = ActivityBundle()
    metadata_cache = None
    if cache_db_path is not None:
        try:
            from yeaboi.standup.cache import StandupMetadataCache

            metadata_cache = StandupMetadataCache(cache_db_path)
        except Exception:
            logger.warning("standup metadata cache unavailable", exc_info=True)
    if skipped:
        # The caller worked out the reasons; keep only sources that really are off,
        # so a stale list can never claim we skipped something we just collected.
        bundle.skipped.extend((src, reason) for src, reason in skipped if src not in enabled)
    elif sources is None:
        # Record WHY each source was auto-disabled so the report can show what
        # wasn't covered (a silently-skipped source reads as "no activity").
        for src in ALL_SOURCES:
            if src in enabled or src == SOURCE_AZDO_REPOS:
                continue  # azdo_repos shares azure_devops config — one skip line, not two
            bundle.skipped.append((src, SKIP_REASONS.get(src, "not configured")))

    bundle_lock = threading.Lock()
    completed_sources = 0
    total_sources = 0

    def _progress(message: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress(message)
        except Exception:
            logger.debug("standup collector progress callback failed", exc_info=True)

    def _source_label(source: str) -> str:
        return source_label(source)

    def _run(source: str, fetcher) -> None:
        """Call one source fetcher with bounded retry, then merge atomically."""
        from yeaboi.standup.errors import StandupSourceError

        nonlocal completed_sources

        def _finished(detail: str) -> None:
            nonlocal completed_sources
            with bundle_lock:
                completed_sources += 1
                completed = completed_sources
            _progress(f"Sources {completed}/{total_sources} · {_source_label(source)} {detail}")

        started = time_module.monotonic()
        try:
            raw = None
            for attempt in range(3):
                try:
                    raw = fetcher()
                    break
                except StandupSourceError:
                    raise
                except Exception as exc:
                    status = int(getattr(exc, "status", 0) or getattr(getattr(exc, "response", None), "status_code", 0))
                    if status not in (429, 500, 502, 503, 504) or attempt == 2:
                        raise
                    delay = 0.25 * (2**attempt)
                    logger.warning("Source %s transient error %s; retrying in %.2fs", source, status, delay)
                    time_module.sleep(delay)
            raw = raw or []
        except StandupSourceError as e:
            # Auth/other failure the user must see — record it as a warning.
            logger.warning("Source %s error surfaced: %s", source, e.message)
            with bundle_lock:
                bundle.errors.append((e.source, e.message))
            _finished("failed")
            return
        except ImportError as e:
            logger.warning("Source %s skipped — SDK not installed: %s", source, e)
            with bundle_lock:
                bundle.skipped.append((source, SKIP_SDK_MISSING))
            _finished("skipped")
            return
        except Exception as e:  # defensive — helpers already guard, but never let one source abort
            logger.warning("Source %s failed unexpectedly: %s", source, e)
            _finished("failed")
            return
        for item in raw:
            item["source"] = source
        with bundle_lock:
            bundle.items.extend(raw)
            bundle.counts.append((source, len(raw)))
        elapsed = time_module.monotonic() - started
        logger.info("Source %s contributed %d item(s) in %.2fs", source, len(raw), elapsed)
        _finished(f"complete ({len(raw)})")

    def _add_reference_tickets(fetch, source: str) -> None:
        """Merge open-ticket matching context, stamped and locked like activity.

        Kept off ``bundle.items`` on purpose — see ActivityBundle.reference_tickets.

        Takes the fetcher rather than its result so the call is inside the guard:
        this runs within a source fetcher whose activity has already succeeded,
        and a raise would make ``_run_source`` discard that activity entirely.
        Losing the context only costs a suppression; losing the activity costs
        the standup. Silent for the same reason — there is no notice worth
        showing a user about matching context that did not load.
        """
        try:
            tickets = fetch() or []
        except Exception as e:
            logger.warning("%s open-ticket context unavailable (%s) — practice matching runs without it", source, e)
            return
        for ticket in tickets:
            ticket["source"] = source
        with bundle_lock:
            bundle.reference_tickets.extend(tickets)

    fetchers: dict[str, object] = {}

    if SOURCE_JIRA in enabled:

        def _jira() -> list[dict]:
            from yeaboi.tools.jira import jira_open_tickets, jira_recent_activity

            items = jira_recent_activity(jira_project, days=days, since=since, include_ticket_text=ticket_context)
            if ticket_context:
                _add_reference_tickets(lambda: jira_open_tickets(jira_project), SOURCE_JIRA)
            return items

        fetchers[SOURCE_JIRA] = _jira

    if SOURCE_AZDO in enabled:

        def _azdo() -> list[dict]:
            from yeaboi.tools.azure_devops import azdevops_open_work_items, azdevops_recent_activity

            items = azdevops_recent_activity(azdo_project, days=days, since=since, include_ticket_text=ticket_context)
            if ticket_context:
                _add_reference_tickets(lambda: azdevops_open_work_items(azdo_project), SOURCE_AZDO)
            return items

        fetchers[SOURCE_AZDO] = _azdo

    if SOURCE_AZDO_REPOS in enabled:

        def _azdo_repos() -> list[dict]:
            from concurrent.futures import ThreadPoolExecutor

            from yeaboi.standup.errors import StandupSourceError
            from yeaboi.tools.azure_devops import (
                azdevops_recent_commits,
                azdevops_recent_prs,
                azdevops_recent_reviews,
            )

            source_errors: list[tuple[str, str]] = []

            def _safe(call) -> list[dict]:
                try:
                    return call()
                except StandupSourceError as exc:
                    source_errors.append((exc.source, exc.message))
                    return []

            projects = list(dict.fromkeys(azdo_projects or ()))
            repository_kwargs = {} if azdo_repositories is None else {"repositories": azdo_repositories}
            cache_kwargs = {"metadata_cache": metadata_cache} if metadata_cache is not None else {}

            selected_projects = projects or [azdo_project]
            calls = []
            for project in selected_projects:
                kwargs = repository_kwargs if not projects else {}
                calls.extend(
                    (
                        lambda project=project, kwargs=kwargs: azdevops_recent_commits(
                            project, days=days, since=since, **cache_kwargs, **kwargs
                        ),
                        lambda project=project, kwargs=kwargs: azdevops_recent_prs(
                            project, days=days, since=since, **cache_kwargs, **kwargs
                        ),
                        lambda project=project, kwargs=kwargs: azdevops_recent_reviews(
                            project, days=days, since=since, **cache_kwargs, **kwargs
                        ),
                    )
                )
            stage_names = ("commits", "pull requests", "reviews") * len(selected_projects)
            with ThreadPoolExecutor(
                max_workers=min(6, max(1, len(calls))), thread_name_prefix="standup-azdo-code"
            ) as pool:
                stage_futures = {
                    pool.submit(_safe, call): stage for call, stage in zip(calls, stage_names, strict=True)
                }
                items = []
                completed_stages = 0
                for stage_future in as_completed(stage_futures):
                    items.extend(stage_future.result())
                    completed_stages += 1
                    _progress(
                        "Azure DevOps code · "
                        f"{stage_futures[stage_future]} complete "
                        f"({completed_stages}/{len(stage_futures)})"
                    )
            with bundle_lock:
                bundle.errors.extend(error for error in dict.fromkeys(source_errors) if error not in bundle.errors)
            return items

        fetchers[SOURCE_AZDO_REPOS] = _azdo_repos

    if SOURCE_GITHUB in enabled:

        def _github() -> list[dict]:
            from concurrent.futures import ThreadPoolExecutor

            from yeaboi.standup.errors import StandupSourceError
            from yeaboi.tools.github import github_recent_commits, github_recent_prs, github_recent_reviews

            source_errors: list[tuple[str, str]] = []

            def _safe(call) -> list[dict]:
                try:
                    return call()
                except StandupSourceError as exc:
                    source_errors.append((exc.source, exc.message))
                    return []

            repositories = [
                repo for repo in (github_repositories if github_repositories is not None else [github_repo]) if repo
            ]
            cache_kwargs = {"metadata_cache": metadata_cache} if metadata_cache is not None else {}

            calls = []
            for repository in repositories:
                calls.extend(
                    (
                        (
                            repository,
                            lambda repository=repository: github_recent_commits(
                                repository, days=days, since=since, **cache_kwargs
                            ),
                        ),
                        (
                            repository,
                            lambda repository=repository: github_recent_prs(
                                repository, days=days, since=since, **cache_kwargs
                            ),
                        ),
                        (
                            repository,
                            lambda repository=repository: github_recent_reviews(
                                repository, days=days, since=since, **cache_kwargs
                            ),
                        ),
                    )
                )

            def _fetch(call) -> list[dict]:
                repository, fetch = call
                items = _safe(fetch)
                for item in items:
                    item.setdefault("repository", repository)
                return items

            with ThreadPoolExecutor(
                max_workers=min(4, max(1, len(calls))), thread_name_prefix="standup-github"
            ) as pool:
                items = [item for batch in pool.map(_fetch, calls) for item in batch]
            with bundle_lock:
                bundle.errors.extend(error for error in dict.fromkeys(source_errors) if error not in bundle.errors)
            return items

        fetchers[SOURCE_GITHUB] = _github

    if SOURCE_LOCAL_GIT in enabled:

        def _local() -> list[dict]:
            from yeaboi.tools.local_git import local_git_recent_commits

            return local_git_recent_commits(local_repo_path, days=days, since=since)

        fetchers[SOURCE_LOCAL_GIT] = _local

    if SOURCE_CONFLUENCE in enabled:

        def _conf() -> list[dict]:
            from yeaboi.tools.confluence import confluence_recent_pages

            cache_kwargs = {"metadata_cache": metadata_cache} if metadata_cache is not None else {}

            def _partial(message: str) -> None:
                with bundle_lock:
                    notice = (SOURCE_CONFLUENCE, message)
                    if notice not in bundle.partial_sources:
                        bundle.partial_sources.append(notice)

            return confluence_recent_pages(
                confluence_space,
                days=days,
                since=since,
                on_partial=_partial,
                **cache_kwargs,
            )

        fetchers[SOURCE_CONFLUENCE] = _conf

    if SOURCE_NOTION in enabled:

        def _notion() -> list[dict]:
            from yeaboi.tools.notion import notion_recent_pages

            return notion_recent_pages(notion_root, days=days, since=since)

        fetchers[SOURCE_NOTION] = _notion

    collection_started = time_module.monotonic()
    total_sources = len(fetchers)
    running_labels = ", ".join(_source_label(source) for source in fetchers)
    _progress(f"Running concurrently · {running_labels}")
    # Name what we are NOT scanning, one step per source. The progress list is the
    # only place a user watches a standup being built, and a source that never
    # appears there is indistinguishable from a source that found nothing.
    for source, reason in bundle.skipped:
        _progress(f"{_source_label(source)} · skipped — {reason}")
    with ThreadPoolExecutor(
        max_workers=min(7, max(1, len(fetchers))),
        thread_name_prefix="standup-source",
    ) as pool:
        futures = {pool.submit(_run, source, fetcher): source for source, fetcher in fetchers.items()}
        pending_sources = set(fetchers)
        for future in as_completed(futures):
            future.result()
            pending_sources.discard(futures[future])
            if pending_sources:
                pending = ", ".join(_source_label(source) for source in fetchers if source in pending_sources)
                _progress(f"Still running · {pending}")
    if metadata_cache is not None:
        metadata_cache.close()

    source_order = {source: index for index, source in enumerate(ALL_SOURCES)}
    bundle.items.sort(
        key=lambda item: (
            str(item.get("timestamp", "")),
            source_order.get(str(item.get("source", "")), len(source_order)),
            str(item.get("kind", "")),
            str(item.get("key", "")),
            str(item.get("title", "")),
        )
    )
    bundle.counts.sort(key=lambda pair: source_order.get(pair[0], len(source_order)))
    bundle.errors[:] = list(dict.fromkeys(bundle.errors))
    bundle.partial_sources[:] = list(dict.fromkeys(bundle.partial_sources))
    bundle.skipped[:] = list(dict.fromkeys(bundle.skipped))
    bundle.errors.sort(key=lambda pair: (source_order.get(pair[0], len(source_order)), pair[1]))
    bundle.partial_sources.sort(key=lambda pair: (source_order.get(pair[0], len(source_order)), pair[1]))
    bundle.skipped.sort(key=lambda pair: (source_order.get(pair[0], len(source_order)), pair[1]))
    _dedupe_items(bundle)
    logger.info(
        "collect_recent_activity: %d total item(s) across %d source(s) in %.2fs",
        bundle.total(),
        len(bundle.counts),
        time_module.monotonic() - collection_started,
    )
    return bundle


def _dedupe_items(bundle: ActivityBundle) -> None:
    """Drop repeated items in place, keeping first occurrence.

    Identity is (source, kind, key, title-sans-annotation, lowercased author).
    Protects against the same commit arriving twice (e.g. once from the default
    branch scan and once via a PR's commit list — the sha key matches even though
    the PR variant's title carries a " (PR #N)" suffix) and a WIP ticket that
    also appeared in the changed-in-window query. Title stays part of the
    identity because local_git uses a constant key for every commit.
    """
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[dict] = []
    for item in bundle.items:
        key = str(item.get("key") or "")
        title = str(item.get("title") or "")
        kind = str(item.get("kind", ""))
        # A commit's sha key is discriminating on its own; drop the title so the
        # "(PR #N)"-annotated duplicate of a branch commit still collapses.
        # Every other kind keeps the title: ticket keys repeat across events
        # (two status moves of PROJ-1 are distinct items), and local_git uses a
        # constant "local" key for every commit.
        ident = (
            str(item.get("source", "")),
            kind,
            key,
            "" if (kind == "commit" and key and key != "local") else title,
            str(item.get("author", "")).strip().lower(),
        )
        if ident in seen:
            continue
        seen.add(ident)
        unique.append(item)
    if len(unique) != len(bundle.items):
        logger.info("collect_recent_activity: deduped %d repeated item(s)", len(bundle.items) - len(unique))
        bundle.items[:] = unique
