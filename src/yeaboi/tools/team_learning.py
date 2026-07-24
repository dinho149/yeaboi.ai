"""Team learning tools — analyse historical sprint data and compare plans to actuals.

# See docs: "Tools" — tool types, @tool decorator, risk levels
# See docs: "Scrum Standards" — team learning, self-calibrating estimates
#
# These tools connect to Jira or Azure DevOps to pull historical sprint data,
# compute team calibration metrics, and compare generated plans against actual
# outcomes. The results feed into TeamProfile for future prompt injection.
#
# Risk level: low — read-only queries against Jira/AzDO APIs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from langchain_core.tools import tool

from yeaboi.team_profile import (
    DailyScopeSnapshot,
    DoDSignal,
    EpicPattern,
    ScopeChangeEvent,
    SpilloverStats,
    SprintScopeTimeline,
    StoryPointCalibration,
    StoryShapePattern,
    TeamProfile,
    WritingPatterns,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _llm_invoke(prompt: str, *, temperature: float = 0.0, max_reasks: int = 1):
    """Invoke the LLM expecting a JSON reply; tracks token usage internally.

    Every caller of this wrapper parses the response as JSON, so it routes
    through invoke_json — JSON mode on Ollama plus a one-shot repair re-ask.
    # See docs: "Local Mode (Ollama)" — reliability layer.
    """
    from yeaboi.agent.llm import invoke_json

    return invoke_json(prompt, temperature=temperature, max_reasks=max_reasks)


def _safe_float(val: object) -> float:
    """Convert a value to float, returning 0.0 on failure."""
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse an ISO-ish date string into a datetime, or None."""
    if not date_str:
        return None
    try:
        fmts = ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"]
        # Do not truncate: Jira uses e.g. ...000+0000 (28+ chars); [:26] breaks %z parsing
        # and yields None cycle times → misleading 0d averages in the TUI.
        s = date_str.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+0000"
        for fmt in fmts:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _cycle_time_days(start: str | None, end: str | None) -> float | None:
    """Compute time in days between two ISO-ish date strings.

    Returns None if either date is missing or unparseable.
    """
    s = _parse_date(start)
    e = _parse_date(end)
    if s and e:
        return max((e - s).total_seconds() / 86400, 0.0)
    return None


def _stddev(values: list[float]) -> float:
    """Compute population standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


# Patterns that indicate recurring/ceremony tickets — not real delivery work.
# These inflate spillover and skew calibration if counted as regular stories.
_RECURRING_PATTERNS = re.compile(
    r"\b("
    r"training|ceremony|retro(?:spective)?|standup|stand-up|stand up|"
    r"planning\s*meeting|sprint\s*review|sprint\s*demo|grooming|"
    r"refinement|backlog\s*review|1[:\s]*1|one.on.one|"
    r"admin|overhead|maintenance\s*window|on.?call|"
    r"interrupted\s*work|blocked\s*time|leave|holiday|"
    r"PTO|time.?off|sick|vacation|"
    r"KTLO|keep.the.lights?.on|BAU|business.as.usual|"
    r"toil|operational\s*support|support\s*rota|"
    r"tech.?debt\s*day|hack.?day|innovation\s*day"
    r")\b",
    re.IGNORECASE,
)


def _is_recurring(story: dict) -> bool:
    """Detect if a story is a recurring/ceremony ticket, not delivery work."""
    summary = story.get("summary", "") or ""
    if _RECURRING_PATTERNS.search(summary):
        return True
    # Detect exact duplicate titles across sprints (same title appears 3+ times)
    # This is checked at the batch level, not per-story — see _tag_recurring_batch
    return False


def _tag_recurring_batch(all_stories: list[dict]) -> None:
    """Tag stories as recurring based on pattern matching and duplicate detection.

    Mutates story dicts in-place by adding `"is_recurring": True/False`.
    A title appearing in 3+ different sprints is considered recurring even
    if it doesn't match the keyword patterns.
    """
    # Normalise titles for duplicate detection
    title_sprints: dict[str, set] = defaultdict(set)
    for s in all_stories:
        title = (s.get("summary", "") or "").strip().lower()
        sprint = s.get("sprint_name", "") or ""
        if title and sprint:
            title_sprints[title].add(sprint)

    # Titles appearing in 3+ distinct sprints → recurring
    recurring_titles = {title for title, sprints in title_sprints.items() if len(sprints) >= 3}

    for s in all_stories:
        title = (s.get("summary", "") or "").strip().lower()
        s["is_recurring"] = _is_recurring(s) or title in recurring_titles

    tagged = sum(1 for s in all_stories if s.get("is_recurring"))
    logger.debug(
        "Recurring detection: %d of %d stories tagged (%d by keyword, %d by duplicate title)",
        tagged,
        len(all_stories),
        sum(1 for s in all_stories if _is_recurring(s)),
        sum(
            1
            for s in all_stories
            if (s.get("summary", "") or "").strip().lower() in recurring_titles and not _is_recurring(s)
        ),
    )


def _azdo_assignee_name(fields: dict) -> str:
    """Extract display name from AzDO System.AssignedTo (can be str or dict)."""
    val = fields.get("System.AssignedTo")
    if not val:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("displayName", "") or val.get("uniqueName", "")
    return getattr(val, "display_name", "") or str(val)


def _azdo_work_item_link_target_id(link: object) -> int | None:
    """Resolve work item id from get_iteration_work_items / WorkItemLink target."""
    target = getattr(link, "target", None)
    if target is None:
        return None
    tid = getattr(target, "id", None)
    if tid is not None:
        try:
            return int(tid)
        except (TypeError, ValueError):
            pass
    if isinstance(target, dict):
        raw = target.get("id")
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return None


def _wit_get_work_items_batch(
    wit_client: object,
    project: str,
    batch_ids: list[int],
    fields: list[str],
    *,
    want_relations: bool = True,
) -> list:
    """Fetch work items for team analysis.

    Some Azure DevOps API configurations reject ``$expand=Relations`` together
    with an explicit ``fields`` list; that path used to fail silently and left
    every sprint with zero stories. We pass ``project`` (required for scoped
    PATs / some orgs) and fall back to a plain get when Relations expand fails.
    """
    if not batch_ids:
        return []
    if want_relations:
        try:
            return (
                wit_client.get_work_items(
                    batch_ids,
                    project=project,
                    fields=fields,
                    expand="Relations",
                )
                or []
            )
        except Exception as e:
            logger.warning(
                "AzDO get_work_items ($expand=Relations) failed for %d ids (e.g. %s): %s — retrying without expand",
                len(batch_ids),
                batch_ids[:3],
                e,
            )
    try:
        return (
            wit_client.get_work_items(
                batch_ids,
                project=project,
                fields=fields,
            )
            or []
        )
    except Exception as e:
        logger.error(
            "AzDO get_work_items failed for project=%r, %d ids: %s",
            project,
            len(batch_ids),
            e,
        )
        return []


def _build_profile_from_sprint_data(
    source: str,
    project_key: str,
    sprint_data: list[dict],
) -> TeamProfile:
    """Build a TeamProfile from collected sprint data.

    Args:
        source: "jira" or "azdevops"
        project_key: Project identifier
        sprint_data: List of sprint dicts, each with keys:
            - sprint_name, completed_points (float)
            - stories: list of dicts with keys: points, cycle_time_days,
              discipline, task_count, ac_count, epic_key, point_changed
    """
    from datetime import datetime

    team_id = f"{source}-{project_key}-{datetime.now(UTC).strftime('%Y%m%d%H%M')}"

    # Velocity stats
    velocities = [sd["completed_points"] for sd in sprint_data if sd.get("completed_points", 0) > 0]
    velocity_avg = sum(velocities) / len(velocities) if velocities else 0.0
    velocity_stddev = _stddev(velocities)

    # Collect all stories
    all_stories: list[dict] = []
    for sd in sprint_data:
        all_stories.extend(sd.get("stories", []))

    # Point calibrations — group stories by point value
    by_points: dict[int, list[dict]] = defaultdict(list)
    for s in all_stories:
        pts = int(_safe_float(s.get("points", 0)))
        if pts in (1, 2, 3, 5, 8):
            by_points[pts].append(s)

    calibrations = []
    for pts in (1, 2, 3, 5, 8):
        stories_at_pts = by_points.get(pts, [])
        if not stories_at_pts:
            calibrations.append(StoryPointCalibration(point_value=pts))
            continue
        cycle_times = [s["cycle_time_days"] for s in stories_at_pts if s.get("cycle_time_days") is not None]
        avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else 0.0
        task_counts = [s.get("task_count", 0) for s in stories_at_pts]
        avg_tasks = sum(task_counts) / len(task_counts) if task_counts else 0.0
        changed = sum(1 for s in stories_at_pts if s.get("point_changed", False))
        overshoot = (changed / len(stories_at_pts) * 100) if stories_at_pts else 0.0
        calibrations.append(
            StoryPointCalibration(
                point_value=pts,
                avg_cycle_time_days=round(avg_ct, 1),
                sample_count=len(stories_at_pts),
                typical_task_count=round(avg_tasks, 1),
                overshoot_pct=round(overshoot, 1),
            )
        )

    # Story shape patterns — group by discipline
    by_discipline: dict[str, list[dict]] = defaultdict(list)
    for s in all_stories:
        disc = s.get("discipline", "fullstack")
        by_discipline[disc].append(s)

    shapes = []
    for disc, stories_in_disc in sorted(by_discipline.items()):
        pts_vals = [_safe_float(s.get("points", 0)) for s in stories_in_disc]
        ac_vals = [_safe_float(s.get("ac_count", 0)) for s in stories_in_disc]
        task_vals = [_safe_float(s.get("task_count", 0)) for s in stories_in_disc]
        shapes.append(
            StoryShapePattern(
                discipline=disc,
                avg_points=round(sum(pts_vals) / len(pts_vals), 1) if pts_vals else 0.0,
                avg_ac_count=round(sum(ac_vals) / len(ac_vals), 1) if ac_vals else 0.0,
                avg_task_count=round(sum(task_vals) / len(task_vals), 1) if task_vals else 0.0,
                sample_count=len(stories_in_disc),
            )
        )

    # Epic patterns — group by epic key
    by_epic: dict[str, list[dict]] = defaultdict(list)
    for s in all_stories:
        ek = s.get("epic_key", "")
        if ek:
            by_epic[ek].append(s)

    if by_epic:
        epic_story_counts = [len(stories) for stories in by_epic.values()]
        epic_point_totals = [sum(_safe_float(s.get("points", 0)) for s in stories) for stories in by_epic.values()]
        epic_pattern = EpicPattern(
            avg_stories_per_epic=round(sum(epic_story_counts) / len(epic_story_counts), 1),
            avg_points_per_epic=round(sum(epic_point_totals) / len(epic_point_totals), 1),
            typical_story_count_range=(min(epic_story_counts), max(epic_story_counts)),
            sample_count=len(by_epic),
        )
    else:
        epic_pattern = EpicPattern()

    # Estimation accuracy — stories where points didn't change
    stories_with_pts = [s for s in all_stories if _safe_float(s.get("points", 0)) > 0]
    unchanged = sum(1 for s in stories_with_pts if not s.get("point_changed", False))
    estimation_accuracy = (unchanged / len(stories_with_pts) * 100) if stories_with_pts else 0.0

    # Sprint completion rate — stories completed vs planned
    completion_rates = []
    for sd in sprint_data:
        planned = sd.get("planned_count", 0)
        completed = sd.get("completed_count", 0)
        if planned > 0:
            completion_rates.append(completed / planned * 100)
    sprint_completion = sum(completion_rates) / len(completion_rates) if completion_rates else 0.0

    return TeamProfile(
        team_id=team_id,
        source=source,
        project_key=project_key,
        sample_sprints=len(sprint_data),
        sample_stories=len(all_stories),
        velocity_avg=round(velocity_avg, 1),
        velocity_stddev=round(velocity_stddev, 1),
        point_calibrations=tuple(calibrations),
        story_shapes=tuple(shapes),
        epic_pattern=epic_pattern,
        estimation_accuracy_pct=round(estimation_accuracy, 1),
        sprint_completion_rate=round(sprint_completion, 1),
    )


# ---------------------------------------------------------------------------
# Parallel deep analysis — 4 workers for richer profiling
# ---------------------------------------------------------------------------

_GWT_RE = re.compile(r"\b(given|when|then)\b", re.IGNORECASE)
_PR_RE = re.compile(r"(pull\s*request|PR\s*#?\d+|/pull/\d+|merge\s*request)", re.IGNORECASE)

# Patterns to extract repository names from PR/MR links in ticket text
_REPO_PATTERNS = [
    # GitHub: github.com/org/repo/pull/N or github.com/org/repo/pulls
    re.compile(r"github\.com/[\w.\-]+/([\w.\-]+)/pull", re.IGNORECASE),
    # AzDO: dev.azure.com/org/project/_git/repo/pullrequest/N
    re.compile(r"_git/([\w.\-]+)/pullrequest", re.IGNORECASE),
    # AzDO short: org.visualstudio.com/project/_git/repo
    re.compile(r"_git/([\w.\-]+)(?:/|$)", re.IGNORECASE),
    # Bitbucket: bitbucket.org/workspace/repo/pull-requests/N
    re.compile(r"bitbucket\.org/[\w.\-]+/([\w.\-]+)/pull-requests", re.IGNORECASE),
    # GitLab: gitlab.com/group/repo/-/merge_requests/N
    re.compile(r"gitlab\.com/(?:[\w.\-]+/)+([\w.\-]+)/-/merge_requests", re.IGNORECASE),
]


def _extract_repos(text: str) -> list[str]:
    """Extract repository names from PR/MR links in a text string."""
    if not text:
        return []
    repos: list[str] = []
    for pat in _REPO_PATTERNS:
        for m in pat.finditer(text):
            repo = m.group(1).strip()
            if repo and len(repo) > 1 and repo.lower() not in repos:
                repos.append(repo.lower())
    return repos


def _story_add_repos(story: dict, new_repos: list[str], source: str) -> None:
    """Merge unique repo slugs onto story['repos'] and record provenance in story['repo_sources']."""
    if not new_repos:
        return
    rs = story.setdefault("repos", [])
    for r in new_repos:
        if r and r not in rs:
            rs.append(r)
    srcs = story.setdefault("repo_sources", [])
    if source not in srcs:
        srcs.append(source)


def _collect_dev_panel_url_strings(obj: object, out: list[str]) -> None:
    """Walk Jira dev-status JSON and collect URL-like strings for _extract_repos."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.strip():
                if k in ("url", "uri", "href", "displayId"):
                    out.append(v)
                elif "http" in v or "_git/" in v.lower():
                    out.append(v)
            elif isinstance(v, (dict, list)):
                _collect_dev_panel_url_strings(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_dev_panel_url_strings(item, out)


def _jira_development_url_blob(jira: object, issue: object) -> str:
    """Fetch Jira Software dev-status detail and return text suitable for _extract_repos."""
    try:
        from yeaboi.config import is_team_analysis_jira_dev_links_enabled

        if not is_team_analysis_jira_dev_links_enabled():
            return ""
        server = (getattr(jira, "_options", {}) or {}).get("server") or ""
        server = str(server).rstrip("/")
        if not server:
            return ""
        issue_id = str(getattr(issue, "id", "") or "")
        if not issue_id:
            return ""
        paths = (
            "/rest/dev-status/1.0/issue/detail",
            "/rest/dev-status/latest/issue/detail",
        )
        session = getattr(jira, "_session", None)
        if session is None:
            return ""
        for path in paths:
            resp = session.get(f"{server}{path}", params={"issueId": issue_id}, timeout=30)
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            buf: list[str] = []
            _collect_dev_panel_url_strings(data, buf)
            if buf:
                return " ".join(buf)
    except Exception:
        logger.debug("Jira dev-status fetch failed", exc_info=True)
    return ""


def _extract_repos_from_azdo_relations(work_item: object) -> list[str]:
    """Parse Azure DevOps work item relations (linked PRs/commits) for repo slugs."""
    rels = getattr(work_item, "relations", None) or []
    chunks: list[str] = []
    for rel in rels:
        url = getattr(rel, "url", None) or ""
        if url:
            chunks.append(url)
        attrs = getattr(rel, "attributes", None)
        if isinstance(attrs, dict):
            for key in ("comment", "name", "resourceId"):
                val = attrs.get(key)
                if isinstance(val, str) and val.strip():
                    chunks.append(val)
    return _extract_repos(" ".join(chunks))


def _azdo_pr_matches_work_item(pr: object, work_item_id: str) -> bool:
    """True if PR is linked to the work item or mentions its id in branch/title/body."""
    if not work_item_id:
        return False
    refs = getattr(pr, "work_item_refs", None) or []
    for ref in refs:
        rid = getattr(ref, "id", None)
        if rid is not None and str(rid) == work_item_id:
            return True
    hay = " ".join(
        x
        for x in (
            getattr(pr, "source_ref_name", None) or "",
            getattr(pr, "title", None) or "",
            getattr(pr, "description", None) or "",
        )
        if x
    ).lower()
    return work_item_id.lower() in hay


def _azdo_enrich_repos_from_git_pull_requests(
    connection: object,
    project: str,
    sprint_data: list[dict],
) -> None:
    """Scan recent PRs in Git repos for work item links / id in branch (optional, env-gated)."""
    from yeaboi.config import (
        get_team_analysis_azdo_pr_search_max_repos,
        get_team_analysis_azdo_pr_search_top,
        get_team_analysis_azdo_repo_allowlist,
        is_team_analysis_azdo_pr_search_enabled,
    )

    if not is_team_analysis_azdo_pr_search_enabled():
        return

    try:
        from azure.devops.v7_1.git.models import GitPullRequestSearchCriteria
    except Exception:
        return

    git_client = connection.clients.get_git_client()
    allow = get_team_analysis_azdo_repo_allowlist()
    max_repos = get_team_analysis_azdo_pr_search_max_repos()
    pr_top = get_team_analysis_azdo_pr_search_top()

    try:
        all_git_repos = git_client.get_repositories(project) or []
    except Exception as e:
        logger.debug("get_repositories failed: %s", e)
        return

    grepos = [r for r in all_git_repos if getattr(r, "name", None)]
    if allow is not None:
        grepos = [r for r in grepos if (r.name or "").lower() in allow]
    grepos = grepos[:max_repos]

    pr_cache: dict[str, list] = {}
    for grepo in grepos:
        rid = grepo.id
        name_lower = (grepo.name or "").lower()
        seen_ids: set[int] = set()
        combined: list = []
        for status in ("completed", "active"):
            try:
                crit = GitPullRequestSearchCriteria(status=status)
                chunk = git_client.get_pull_requests(rid, crit, project=project, top=pr_top) or []
            except Exception:
                logger.debug("get_pull_requests failed for repo %s (status=%s)", name_lower, status, exc_info=True)
                chunk = []
            for pr in chunk:
                pid = getattr(pr, "pull_request_id", None)
                if pid is None or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                combined.append(pr)
        pr_cache[name_lower] = combined

    for sd in sprint_data:
        for s in sd.get("stories", []) or []:
            if s.get("is_recurring"):
                continue
            if s.get("repos"):
                continue
            ikey = str(s.get("issue_key", "") or "")
            if not ikey.isdigit():
                continue
            for rname, prs in pr_cache.items():
                for pr in prs:
                    if _azdo_pr_matches_work_item(pr, ikey):
                        _story_add_repos(s, [rname], "azdo_pr_work_items")
                        break

    if pr_cache:
        logger.info(
            "AzDO PR repo enrichment: scanned %d Git repos for work item ↔ PR links",
            len(pr_cache),
        )


_REVIEW_RE = re.compile(r"\b(reviewed|LGTM|approved|code\s*review)\b", re.IGNORECASE)
_TEST_RE = re.compile(r"\b(tested|QA\s*pass|verified|no\s*regressions|tests?\s*pass)\b", re.IGNORECASE)
_DEPLOY_RE = re.compile(r"\b(deployed|released|merged\s*to\s*main|shipped|staging)\b", re.IGNORECASE)
_DOC_RE = re.compile(
    r"\b(documented|readme|runbook|confluence|wiki|updated\s*docs?|documentation)\b",
    re.IGNORECASE,
)
_AC_VERIFY_RE = re.compile(
    r"\b(acceptance\s*criteria|AC\s*met|AC\s*verified|criteria\s*met|demo['']?d|stakeholder\s*sign.?off)\b",
    re.IGNORECASE,
)
_SECURITY_RE = re.compile(
    r"\b(security\s*review|vulnerability|pen\s*test|OWASP|threat\s*model|secret\s*scan)\b",
    re.IGNORECASE,
)
_PERF_RE = re.compile(
    r"\b(performance\s*test|load\s*test|benchmark|latency|throughput|perf\s*check)\b",
    re.IGNORECASE,
)
_MONITORING_RE = re.compile(
    r"\b(monitoring|alert|dashboard|observability|logging|metric|grafana|datadog)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# LLM-powered ticket structure parser
# ---------------------------------------------------------------------------

_TICKET_PARSE_SCHEMA = """\
[
  {
    "key": "ISSUE-123",
    "acceptance_criteria": ["AC item 1", "AC item 2"],
    "ac_specificity": "precise",
    "dod_signals": ["Code reviewed", "Deployed to staging"],
    "gwt": false,
    "discipline": "backend",
    "work_type": "create/build",
    "is_recurring": false,
    "spillover_risk": "none",
    "title_prefix": "",
    "description_template_sections": ["What is this about?", "Acceptance Criteria"]
  }
]"""

_TICKET_PARSE_PROMPT = """\
Analyse each work item below and extract structured sections.
Return a JSON array with one object per work item matching this schema:

{schema}

Section extraction — look for these patterns in the description:
- "What does done look like?" / "Acceptance criteria" / "AC" → acceptance_criteria
- Definition of done items (testing, review, deployment steps) → dod_signals

Classification fields:
- discipline: infer the primary discipline from the content. Common values include \
"backend", "frontend", "infrastructure", "security", "observability", "platform", \
"data", "devops", "developer-experience", "networking", "database", "ci-cd", \
"testing", "design", "fullstack" — but use whatever best describes the work
- work_type: classify the nature of work — one of "create/build", "fix/resolve", \
"update/modify", "investigate/research", "automate/script", "migrate", \
"refactor", "configure/setup", "monitor/observe", "review/audit"
- is_recurring: true if this is a ceremony, KTLO, sprint admin, training, standup, \
retro, BAU, on-call, or operational ticket — NOT delivery work
- ac_specificity: assess the acceptance criteria quality — one of "precise" \
(measurable, specific values/endpoints/thresholds), "moderate" (clear intent but \
not fully measurable), "vague" (subjective language like "should work correctly"), \
or "none" (no ACs found)
- spillover_risk: assess completion risk — one of "low" (clear scope, small), \
"medium" (some unknowns or dependencies), "high" (large scope, external \
dependencies, unclear requirements), or "none" (can't determine)
- title_prefix: if the title starts with a prefix like "[FE]", "SPIKE:", "TECH:", \
"bug:", "feat:", extract it. Empty string if no prefix.
- description_template_sections: list the section headings found in the description \
(e.g. "What is this about?", "Why does it matter?", "Acceptance Criteria", \
"Risks", "Done looks like"). Empty list if no structured sections.

Rules:
- acceptance_criteria should be individual testable items, not the full section text
- If ACs use Given/When/Then format, set gwt to true
- If a section is missing or empty, return empty string/list
- Return ONLY the JSON array, no other text

Work items:

{items}"""


def _strip_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace for LLM input."""
    clean = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", clean).strip()


def _parse_tickets_with_llm(
    stories: list[dict],
    progress: list[str],
    batch_size: int = 12,
    *,
    source: str = "",
    project_key: str = "",
    db_path=None,
    cache_updates: dict[str, tuple[str, dict]] | None = None,
) -> dict[str, dict]:
    """Parse tickets using LLM in batches. Returns {issue_key: parsed_dict}.

    Falls back to empty dict if LLM is unavailable or fails.
    """
    if not stories:
        return {}

    parser_version = "compact-v1"

    def _content_hash(story: dict) -> str:
        material = {
            "parser": parser_version,
            "summary": story.get("summary", ""),
            "description": _strip_html(story.get("description", "") or ""),
            "points": story.get("points", 0),
            "task_count": story.get("task_count", 0),
            "carried_over": bool(story.get("carried_over")),
        }
        raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    content_hashes = {str(s.get("issue_key", "")): _content_hash(s) for s in stories if str(s.get("issue_key", ""))}
    results: dict[str, dict] = {}
    if source and project_key and db_path and content_hashes:
        try:
            from yeaboi.team_profile import TeamProfileStore

            with TeamProfileStore(db_path) as store:
                results.update(store.load_ticket_parse_cache(source, project_key, content_hashes))
        except Exception:
            logger.debug("Ticket parse cache read failed", exc_info=True)

    uncached = [s for s in stories if str(s.get("issue_key", "")) not in results]
    if results:
        logger.info("Ticket parse cache: %d hit(s), %d miss(es)", len(results), len(uncached))
    if not uncached:
        if cache_updates is not None:
            cache_updates.update({key: (content_hashes[key], value) for key, value in results.items()})
        return results

    # Build batches from cache misses only.
    batches: list[list[dict]] = []
    for i in range(0, len(uncached), batch_size):
        batches.append(uncached[i : i + batch_size])

    def _parse_batch(batch: list[dict]) -> dict[str, dict]:
        """Parse a single batch of stories."""
        items_parts: list[str] = []
        for s in batch:
            key = s.get("issue_key", "?")
            title = s.get("summary", "")
            desc = _strip_html(s.get("description", "") or "")
            # Truncate to avoid token bloat
            if len(desc) > 800:
                desc = desc[:800] + "..."
            # Include metadata for better classification
            pts = s.get("points", 0)
            tasks = s.get("task_count", 0)
            carried = "yes" if s.get("carried_over") else "no"
            meta = f"[{pts}pts, {tasks} tasks, carried_over={carried}]"
            items_parts.append(f"--- {key}: {title} {meta} ---\n{desc}")

        items_block = "\n\n".join(items_parts)
        prompt = _TICKET_PARSE_PROMPT.format(schema=_TICKET_PARSE_SCHEMA, items=items_block)

        try:
            # A malformed batch falls back deterministically. Repair re-asks can
            # double the dominant cost on large boards, so ticket parsing does not
            # retry; narrative/insight calls retain the shared repair behaviour.
            response = _llm_invoke(prompt, temperature=0.0, max_reasks=0)
            text = response.content if hasattr(response, "content") else str(response)
            # Extract JSON from response (handle markdown fences)
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                return {}
            batch_results: dict[str, dict] = {}
            for item in parsed:
                if isinstance(item, dict) and item.get("key"):
                    batch_results[item["key"]] = item
            return batch_results
        except Exception as exc:
            logger.debug("LLM ticket parse batch failed: %s", exc)
            return {}

    # Run batches in parallel
    progress.append("Parsing ticket structure\u2026")
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(batches))) as executor:
            futures = {executor.submit(_parse_batch, b): i for i, b in enumerate(batches)}
            for future in as_completed(futures):
                try:
                    batch_result = future.result(timeout=30)
                    results.update(batch_result)
                except Exception:
                    logger.debug("LLM ticket-parse batch %d failed — skipping", futures[future], exc_info=True)
    except Exception as exc:
        logger.warning("LLM ticket parsing failed entirely: %s", exc)
        return {}

    if cache_updates is not None:
        cache_updates.update(
            {key: (content_hashes[key], value) for key, value in results.items() if key in content_hashes}
        )
    logger.info("Deep parser resolved %d/%d stories", len(results), len(stories))
    return results


def _enrich_stories_with_parsed(
    stories: list[dict],
    parsed: dict[str, dict],
) -> None:
    """Mutate story dicts in-place with LLM-parsed fields."""
    _valid_disciplines = {
        "backend",
        "frontend",
        "infrastructure",
        "design",
        "testing",
        "data",
        "devops",
        "fullstack",
    }
    for s in stories:
        key = s.get("issue_key", "")
        p = parsed.get(key)
        if p:
            # Acceptance criteria
            acs = p.get("acceptance_criteria", [])
            if isinstance(acs, list) and acs:
                s["ac_count"] = len(acs)
                s["parsed_ac"] = acs
            s["ac_specificity"] = p.get("ac_specificity", "none")

            # DoD and context
            s["parsed_risks"] = p.get("risks", [])
            s["parsed_dod_signals"] = p.get("dod_signals", [])
            s["parsed_justification"] = p.get("justification", "")
            s["parsed_personas"] = p.get("personas", [])
            if p.get("gwt"):
                s["uses_given_when_then"] = True

            # Discipline — override the tag-based default if LLM inferred one
            # Accept any non-empty discipline from LLM (not restricted to whitelist)
            llm_disc = (p.get("discipline") or "").lower().strip()
            if llm_disc and llm_disc != "fullstack" and s.get("discipline") == "fullstack":
                s["discipline"] = llm_disc

            # Work type classification
            work_type = p.get("work_type", "")
            if work_type:
                s["work_type"] = work_type

            # Recurring detection — override only if LLM says it's recurring
            # but regex didn't catch it (don't un-flag regex positives)
            if p.get("is_recurring") and not s.get("is_recurring"):
                s["is_recurring"] = True

            # Spillover risk
            s["spillover_risk"] = p.get("spillover_risk", "none")

            # Naming conventions
            tp = (p.get("title_prefix") or "").strip()
            if tp:
                s["title_prefix"] = tp
            dts = p.get("description_template_sections", [])
            if isinstance(dts, list) and dts:
                s["description_sections"] = dts

            s["parse_source"] = "llm"
        else:
            s["parse_source"] = "regex"


def _normalize_title(title: str) -> str:
    """Normalize a story title for duplicate detection across sprints.

    Strips sprint numbers, common prefixes, and normalizes whitespace
    so "Script the PR creation v2" matches "Script the PR creation".
    """
    t = title.lower().strip()
    # Strip sprint references like "Sprint 24", "S24", "Sprint-24"
    t = re.sub(r"\b(?:sprint|s)\s*[-_]?\s*\d+\b", "", t, flags=re.IGNORECASE)
    # Strip version suffixes like "v2", "(part 2)", "#2"
    t = re.sub(r"\s*(?:v\d+|\(part\s*\d+\)|#\d+)\s*", "", t)
    # Strip leading ticket-like prefixes
    t = re.sub(r"^[A-Z]+-\d+\s*[-:]\s*", "", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _detect_shadow_spillover(sprint_data: list[dict]) -> list[dict]:
    """Detect stories that were closed but re-created in the next sprint.

    Uses normalized title **exact** equality only (same normalized string in
    consecutive sprints), not word-overlap fuzzy matching.

    Each item: {title, from_sprint, to_sprint, issue_key, issue_url,
                from_issue_key, from_title, similarity}
    """
    confirmed: list[dict] = []
    logger.debug("Checking shadow spillover across %d consecutive sprint pairs", max(0, len(sprint_data) - 1))

    for i in range(len(sprint_data) - 1):
        curr = sprint_data[i]
        next_sp = sprint_data[i + 1]

        curr_done: list[tuple[str, dict]] = []
        for s in curr.get("stories", []):
            if s.get("is_recurring"):
                continue
            if not s.get("carried_over"):
                norm = _normalize_title(s.get("summary", ""))
                if norm and len(norm) > 5:
                    curr_done.append((norm, s))

        for s_next in next_sp.get("stories", []):
            if s_next.get("is_recurring"):
                continue
            n_next = _normalize_title(s_next.get("summary", ""))
            if not n_next or len(n_next) < 5:
                continue

            best_match: dict | None = None
            for n_prev, s_prev in curr_done:
                if n_next == n_prev:
                    best_match = s_prev
                    break

            if best_match is None:
                continue

            confirmed.append(
                {
                    "title": s_next.get("summary", "")[:55],
                    "from_title": best_match.get("summary", "")[:55],
                    "from_sprint": curr.get("sprint_name", ""),
                    "to_sprint": next_sp.get("sprint_name", ""),
                    "issue_key": s_next.get("issue_key", ""),
                    "issue_url": s_next.get("issue_url", ""),
                    "from_issue_key": best_match.get("issue_key", ""),
                    "from_issue_url": best_match.get("issue_url", ""),
                    "similarity": 1.0,
                }
            )

    if confirmed:
        logger.info("Shadow spillover (exact title match): %d stories", len(confirmed))
    return confirmed


def _worker_sprint_velocity(sprint_data: list[dict], progress: list[str]) -> dict:
    """Worker 1: Compute velocity stats, sprint completion, and per-sprint detail.

    All metrics exclude recurring/ceremony tickets (is_recurring=True) so
    velocity, completion rates, and sprint breakdowns reflect delivery work only.
    """
    progress.append("Fetching sprint history\u2026")

    # Detect shadow spillover first so we can mark affected sprints.
    confirmed_shadows = _detect_shadow_spillover(sprint_data)

    # Build a set of sprint names that had confirmed shadow spillover
    shadow_from_sprints: set[str] = set()
    shadow_by_sprint: dict[str, list[dict]] = defaultdict(list)
    for sh in confirmed_shadows:
        shadow_from_sprints.add(sh["from_sprint"])
        shadow_by_sprint[sh["from_sprint"]].append(sh)

    fully = 0
    partially = 0
    velocities = []
    sprint_details = []

    for sd in sprint_data:
        name = sd.get("sprint_name", "?")
        all_stories = sd.get("stories", [])

        # Split into delivery vs recurring
        delivery = [s for s in all_stories if not s.get("is_recurring")]
        done_delivery = [s for s in delivery if not s.get("carried_over")]
        planned = len(delivery)
        completed = len(done_delivery)
        pts = sum(_safe_float(s.get("points", 0)) for s in done_delivery)

        if pts > 0:
            velocities.append(pts)

        rate = round(completed / planned * 100) if planned > 0 else 0

        # A sprint is NOT truly done if it has shadow spillover —
        # stories were closed but re-created in the next sprint.
        has_shadow = name in shadow_from_sprints
        is_done = planned > 0 and completed >= planned and not has_shadow

        if is_done:
            fully += 1
        elif planned > 0:
            partially += 1

        # Incomplete delivery stories for drill-down
        incomplete = []
        for s in delivery:
            if s.get("carried_over"):
                incomplete.append(
                    {
                        "issue_key": s.get("issue_key", ""),
                        "issue_url": s.get("issue_url", ""),
                        "summary": (s.get("summary", "") or "")[:50],
                        "points": _safe_float(s.get("points", 0)),
                    }
                )
        # Add shadow spillover items to the incomplete list
        for sh in shadow_by_sprint.get(name, []):
            incomplete.append(
                {
                    "issue_key": sh.get("issue_key", ""),
                    "issue_url": sh.get("issue_url", ""),
                    "summary": sh.get("title", "")[:50],
                    "points": 0,
                    "shadow": True,
                }
            )

        sprint_details.append(
            {
                "name": name,
                "planned": planned,
                "completed": completed,
                "points": round(pts, 1),
                "rate": rate,
                "done": is_done,
                "has_shadow": has_shadow,
                "incomplete": incomplete[:5],
            }
        )

    velocity_avg = sum(velocities) / len(velocities) if velocities else 0.0
    velocity_stddev = _stddev(velocities)
    completion_rates = [sd["rate"] for sd in sprint_details if sd["planned"] > 0]
    logger.info(
        "Velocity worker: avg=%.1f, stddev=%.1f, %d fully done, %d partial, %d confirmed shadows",
        velocity_avg,
        velocity_stddev,
        fully,
        partially,
        len(confirmed_shadows),
    )
    for sd in sprint_details:
        logger.debug(
            "  Sprint %s: %.1f pts, %d/%d done (%d%%), shadow=%s",
            sd["name"],
            sd["points"],
            sd["completed"],
            sd["planned"],
            sd["rate"],
            sd.get("has_shadow", False),
        )

    return {
        "velocity_avg": round(velocity_avg, 1),
        "velocity_stddev": round(velocity_stddev, 1),
        "sprints_fully_completed": fully,
        "sprints_partially_completed": partially,
        "sprint_completion_rate": round(sum(completion_rates) / len(completion_rates), 1) if completion_rates else 0.0,
        "sprint_details": sprint_details,
        "shadow_spillover": confirmed_shadows,
    }


_ACTION_VERBS = {
    "create": "create/build",
    "build": "create/build",
    "implement": "create/build",
    "add": "create/build",
    "set up": "create/build",
    "setup": "create/build",
    "script": "automate/script",
    "automate": "automate/script",
    "pipeline": "automate/script",
    "update": "update/modify",
    "modify": "update/modify",
    "change": "update/modify",
    "upgrade": "update/modify",
    "migrate": "update/modify",
    "fix": "fix/resolve",
    "resolve": "fix/resolve",
    "bug": "fix/resolve",
    "patch": "fix/resolve",
    "hotfix": "fix/resolve",
    "investigate": "investigate/research",
    "research": "investigate/research",
    "spike": "investigate/research",
    "explore": "investigate/research",
    "review": "review/audit",
    "audit": "review/audit",
    "improve": "improve/optimise",
    "optimise": "improve/optimise",
    "optimize": "improve/optimise",
    "refactor": "improve/optimise",
    "enhance": "improve/optimise",
    "deploy": "deploy/release",
    "release": "deploy/release",
    "configure": "configure/setup",
    "config": "configure/setup",
    "monitor": "monitor/observe",
    "alert": "monitor/observe",
    "dashboard": "monitor/observe",
    "test": "test/verify",
    "e2e": "test/verify",
    "verify": "test/verify",
    "document": "document",
    "doc": "document",
    "runbook": "document",
}


def _extract_point_patterns(stories: list[dict]) -> tuple[str, ...]:
    """Extract common work patterns from story titles.

    Groups stories by action verb category and returns the top patterns
    like ("automate/script", "update/modify", "fix/resolve").
    """
    if not stories:
        return ()

    category_count: dict[str, int] = defaultdict(int)
    for s in stories:
        title = (s.get("summary", "") or "").lower()
        matched = False
        for keyword, category in _ACTION_VERBS.items():
            if keyword in title:
                category_count[category] += 1
                matched = True
                break
        if not matched:
            category_count["other"] += 1

    # Return top 3 categories (excluding "other" unless it's dominant)
    sorted_cats = sorted(category_count.items(), key=lambda x: -x[1])
    result = []
    for cat, count in sorted_cats:
        if cat == "other" and len(result) >= 2:
            continue
        pct = round(count / len(stories) * 100)
        if pct >= 10:
            result.append(f"{cat} ({pct}%)")
        if len(result) >= 3:
            break
    return tuple(result)


def _worker_point_calibration(all_stories: list[dict], sprint_data: list[dict], progress: list[str]) -> dict:
    """Worker 2: Point calibration, spillover stats, and pattern extraction."""
    progress.append("Analysing story point patterns\u2026")

    by_points: dict[int, list[dict]] = defaultdict(list)
    for s in all_stories:
        pts = int(_safe_float(s.get("points", 0)))
        if pts in (1, 2, 3, 5, 8):
            by_points[pts].append(s)

    calibrations = []
    for pts in (1, 2, 3, 5, 8):
        stories_at_pts = by_points.get(pts, [])
        if not stories_at_pts:
            calibrations.append(StoryPointCalibration(point_value=pts))
            continue
        cycle_times = [s["cycle_time_days"] for s in stories_at_pts if s.get("cycle_time_days") is not None]
        avg_ct = sum(cycle_times) / len(cycle_times) if cycle_times else 0.0
        task_counts = [s.get("task_count", 0) for s in stories_at_pts]
        avg_tasks = sum(task_counts) / len(task_counts) if task_counts else 0.0
        changed = sum(1 for s in stories_at_pts if s.get("point_changed", False))
        overshoot = (changed / len(stories_at_pts) * 100) if stories_at_pts else 0.0
        patterns = _extract_point_patterns(stories_at_pts)
        logger.debug(
            "  %dpt calibration: %.1fd avg cycle, %d samples, %.0f%% overshoot, ~%.1f tasks, patterns=%s",
            pts,
            avg_ct,
            len(stories_at_pts),
            overshoot,
            avg_tasks,
            patterns,
        )
        calibrations.append(
            StoryPointCalibration(
                point_value=pts,
                avg_cycle_time_days=round(avg_ct, 1),
                sample_count=len(stories_at_pts),
                common_patterns=patterns,
                typical_task_count=round(avg_tasks, 1),
                overshoot_pct=round(overshoot, 1),
            )
        )

    # Spillover: stories that carried over to next sprint
    carried = 0
    total = 0
    spillover_pts = []
    spillover_reasons: dict[str, int] = defaultdict(int)
    for sd in sprint_data:
        for s in sd.get("stories", []):
            total += 1
            if s.get("carried_over", False):
                carried += 1
                pts = _safe_float(s.get("points", 0))
                spillover_pts.append(pts)
                disc = s.get("discipline", "unknown")
                spillover_reasons[f"{disc} stories"] += 1

    carried_pct = (carried / total * 100) if total else 0.0
    avg_spill = sum(spillover_pts) / len(sprint_data) if sprint_data else 0.0
    top_reason = max(spillover_reasons, key=spillover_reasons.get) if spillover_reasons else ""

    spillover = SpilloverStats(
        carried_over_pct=round(carried_pct, 1),
        avg_spillover_pts=round(avg_spill, 1),
        most_common_spillover_reason=top_reason,
    )

    # Estimation accuracy
    stories_with_pts = [s for s in all_stories if _safe_float(s.get("points", 0)) > 0]
    unchanged = sum(1 for s in stories_with_pts if not s.get("point_changed", False))
    estimation_accuracy = (unchanged / len(stories_with_pts) * 100) if stories_with_pts else 0.0

    return {
        "calibrations": tuple(calibrations),
        "spillover": spillover,
        "estimation_accuracy_pct": round(estimation_accuracy, 1),
    }


def _fallback_point_descriptions(calibrations) -> dict[str, str]:
    """Build deterministic point descriptions from raw metrics (no LLM)."""
    result: dict[str, str] = {}
    for cal in calibrations:
        if cal.sample_count == 0:
            continue
        parts: list[str] = []
        parts.append(f"~{cal.avg_cycle_time_days:.0f}d cycle time")
        if cal.typical_task_count > 0:
            parts.append(f"~{cal.typical_task_count:.0f} subtasks")
        if cal.common_patterns:
            names = ", ".join(p.split(" (")[0] for p in cal.common_patterns[:2])
            parts.append(f"typically {names} work")
        if cal.overshoot_pct > 30:
            parts.append(f"overshoots {cal.overshoot_pct:.0f}% of time — consider splitting")
        conf = "high" if cal.sample_count >= 15 else ("medium" if cal.sample_count >= 5 else "low")
        parts.append(f"({conf} confidence, {cal.sample_count} samples)")
        result[str(cal.point_value)] = ". ".join(parts).capitalize() + "."
    return result


def _generate_point_descriptions(
    delivery_stories: list[dict],
    calibrations,
    discipline_calibration: dict,
    spillover_correlation: dict,
) -> dict[str, str]:
    """Use LLM to generate natural-language descriptions of what each point value means.

    Sends calibration metrics + actual story titles to the LLM and gets back
    one concise sentence per point value describing typical scope and risk.
    Falls back to deterministic descriptions on failure.
    """
    cals_with_data = [c for c in calibrations if c.sample_count > 0]
    if not cals_with_data:
        return {}

    # Group story titles by point value (cap at 10 per value)
    by_pts: dict[int, list[str]] = defaultdict(list)
    for s in delivery_stories:
        pts = int(_safe_float(s.get("points", 0)))
        if pts in (1, 2, 3, 5, 8) and len(by_pts[pts]) < 10:
            title = (s.get("summary", "") or "")[:80].strip()
            if title:
                by_pts[pts].append(title)

    # Build per-point stats block
    stats_lines: list[str] = []
    for cal in cals_with_data:
        conf = "high" if cal.sample_count >= 15 else ("medium" if cal.sample_count >= 5 else "low")
        patterns = ", ".join(cal.common_patterns) if cal.common_patterns else "mixed"
        stats_lines.append(
            f"- {cal.point_value} pt: {cal.avg_cycle_time_days:.1f}d avg cycle, "
            f"~{cal.typical_task_count:.0f} tasks, {cal.sample_count} samples, "
            f"overshoots {cal.overshoot_pct:.0f}%, patterns: {patterns}, confidence: {conf}"
        )

    # Build discipline differences
    disc_lines: list[str] = []
    for disc, entries in discipline_calibration.items():
        for e in entries:
            disc_lines.append(f"- {disc} {e['points']}pt: {e['avg_cycle_days']}d cycle, {e['spill_pct']}% spill")

    # Build story titles block
    title_lines: list[str] = []
    for pts in (1, 2, 3, 5, 8):
        titles = by_pts.get(pts, [])
        if titles:
            title_lines.append(f"### {pts} pt ({len(titles)} stories):")
            for t in titles:
                title_lines.append(f'- "{t}"')

    # Build spillover by size
    spill_by_size = spillover_correlation.get("by_size", {})
    spill_lines = [f"- {k}pt: {v}% spill" for k, v in sorted(spill_by_size.items())]

    prompt = (
        "Analyse these story point metrics and actual story titles from a team's sprint history.\n"
        "For each point value, write ONE sentence (max 30 words) describing what this size means "
        "in practice for THIS team.\n"
        "Focus on: typical scope/complexity, risk level, and what distinguishes it from adjacent sizes.\n"
        "Be specific and concrete — reference actual work types from the titles.\n\n"
        "## Calibration data\n" + "\n".join(stats_lines) + "\n\n"
    )
    if disc_lines:
        prompt += "## Discipline differences\n" + "\n".join(disc_lines[:15]) + "\n\n"
    if spill_lines:
        prompt += "## Spillover by size\n" + "\n".join(spill_lines) + "\n\n"
    prompt += "## Story titles by point value\n" + "\n".join(title_lines) + "\n\n"
    prompt += 'Return ONLY a JSON object: {"1": "description", "2": "description", ...}\n'
    prompt += "Do not include point values that have no data."

    try:
        response = _llm_invoke(prompt, temperature=0.0)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        if isinstance(result, dict) and all(isinstance(v, str) for v in result.values()):
            logger.info("LLM point descriptions generated for %d point values", len(result))
            return result
    except Exception as exc:
        logger.warning("LLM point description generation failed: %s", exc)

    return _fallback_point_descriptions(cals_with_data)


# ---------------------------------------------------------------------------
# Headline stats + recommendations (pure — shared by the TUI overview,
# the recommendations card, and the narrative digest below)
# ---------------------------------------------------------------------------


def compute_headline_stats(profile: TeamProfile, examples: dict | None) -> dict:
    """Derive the headline metrics shown on the analysis overview.

    Mirrors the screen's preference for current ``sprint_details`` over the
    merged profile (which accumulates historical data), so the overview numbers
    match the Velocity & Sprints card. Pure — no LLM, no I/O.
    """
    ex = examples or {}

    # Velocity from current sprint details when available (matches the table)
    sp_details = ex.get("sprint_details", [])
    vel = profile.velocity_avg
    std = profile.velocity_stddev
    if isinstance(sp_details, list) and sp_details:
        sp_pts = [sd["points"] for sd in sp_details if isinstance(sd, dict) and sd.get("points", 0) > 0]
        if sp_pts:
            vel = round(sum(sp_pts) / len(sp_pts), 1)
        if len(sp_pts) >= 2:
            mean = sum(sp_pts) / len(sp_pts)
            std = round(math.sqrt(sum((x - mean) ** 2 for x in sp_pts) / len(sp_pts)), 1)

    # Completion rate from current sprint details when available
    rate = profile.sprint_completion_rate
    if isinstance(sp_details, list) and sp_details:
        sp_rates = [sd["rate"] for sd in sp_details if isinstance(sd, dict) and sd.get("planned", 0) > 0]
        if sp_rates:
            rate = round(sum(sp_rates) / len(sp_rates), 1)

    # Committed vs delivered (scope timelines)
    committed_avg = delivered_avg = 0.0
    delivery_accuracy = 0
    scope = ex.get("scope_changes", {})
    if isinstance(scope, dict) and scope.get("totals"):
        committed_avg = scope["totals"].get("avg_committed_velocity", 0.0)
        delivered_avg = scope["totals"].get("avg_delivered_velocity", 0.0)
        if committed_avg > 0:
            delivery_accuracy = round(delivered_avg / committed_avg * 100)

    # Per-developer velocity — actual contributor avg when available
    per_dev = 0.0
    contrib = ex.get("contributor_stats", [])
    if isinstance(contrib, list) and contrib:
        vals = [c.get("per_sprint", 0) for c in contrib if c.get("per_sprint", 0) > 0]
        if vals:
            per_dev = round(sum(vals) / len(vals), 1)
    if not per_dev:
        pdv = ex.get("per_dev_velocity", 0)
        if isinstance(pdv, (int, float)) and pdv > 0:
            per_dev = pdv

    # Estimation accuracy — additional_patterns bias when sampled, else profile
    est_accuracy = profile.estimation_accuracy_pct
    addl = ex.get("additional_patterns", {})
    est = addl.get("estimation_bias", {}) if isinstance(addl, dict) else {}
    if isinstance(est, dict) and est.get("sample", 0) >= 5:
        est_accuracy = est.get("accurate_pct", est_accuracy)

    trend = ""
    vt = ex.get("velocity_trend", {})
    if isinstance(vt, dict) and vt.get("trend") and vt["trend"] != "insufficient_data":
        trend = vt["trend"]

    team_size = ex.get("team_size", 0)
    return {
        "team_size": team_size if isinstance(team_size, int) else 0,
        "velocity": vel,
        "stddev": std,
        "var_pct": round(std / vel * 100) if vel > 0 else 0,
        "completion_rate": rate,
        "committed_avg": committed_avg,
        "delivered_avg": delivered_avg,
        "delivery_accuracy": delivery_accuracy,
        "per_dev": per_dev,
        "spillover_pct": profile.spillover.carried_over_pct,
        "estimation_accuracy": est_accuracy,
        "trend": trend,
    }


def compute_recommendations(profile: TeamProfile, examples: dict | None) -> list[tuple[str, str]]:
    """Build the deterministic (icon+label, text) recommendation list.

    Pure port of the logic that lived inline in the results screen — shared by
    the Recommendations card, the overview warning count, the narrative digest,
    and the exporters.
    """
    ex = examples or {}
    stats = compute_headline_stats(profile, ex)
    vel, std = stats["velocity"], stats["stddev"]
    cals_with_data = [c for c in profile.point_calibrations if c.sample_count > 0]
    dod = profile.dod_signal
    rec_count = ex.get("recurring_count", 0)
    del_count = ex.get("delivery_count", 0)

    recs: list[tuple[str, str]] = []

    if vel > 0:
        var_pct = std / vel * 100
        if var_pct > 35:
            recs.append(
                (
                    "⚠ High velocity variance",
                    f"Velocity swings ±{var_pct:.0f}% sprint-to-sprint. "
                    "Consider smaller stories, stricter sprint commitments, "
                    "or capacity planning to stabilise throughput.",
                )
            )

    if profile.sprint_completion_rate > 0 and profile.sprint_completion_rate < 60:
        recs.append(
            (
                "⚠ Low sprint completion",
                f"Only {profile.sprint_completion_rate:.0f}% of planned work "
                "completes each sprint. Right-size sprint commitments to "
                "80–90% of historical velocity.",
            )
        )

    if profile.spillover.carried_over_pct > 15:
        recs.append(
            (
                "⚠ Frequent spillover",
                f"{profile.spillover.carried_over_pct:.0f}% of stories carry "
                "over. Break large stories into smaller slices and "
                "set WIP limits to improve flow.",
            )
        )

    for cal in cals_with_data:
        if cal.point_value >= 8 and cal.avg_cycle_time_days > 60:
            recs.append(
                (
                    f"⚠ {cal.point_value}-point stories too large",
                    f"8-point stories take {cal.avg_cycle_time_days:.0f}d "
                    "on average. Consider splitting into 3+5 or "
                    "two 5-point stories for faster feedback.",
                )
            )
            break

    if dod.stories_with_pr_link_pct < 20 and dod.stories_with_pr_link_pct > 0:
        recs.append(
            (
                "ℹ Low PR linkage",
                f"Only {dod.stories_with_pr_link_pct:.0f}% of stories "
                "reference a pull request. Link PRs to tickets for "
                "traceability and automated DoD.",
            )
        )

    if dod.stories_with_testing_mention_pct < 15 and dod.stories_with_testing_mention_pct > 0:
        recs.append(
            (
                "ℹ Testing rarely mentioned",
                f"Only {dod.stories_with_testing_mention_pct:.0f}% of stories "
                "mention testing. Add explicit test criteria to "
                "acceptance criteria for quality visibility.",
            )
        )

    if rec_count and isinstance(rec_count, int):
        total = rec_count + (del_count if isinstance(del_count, int) else 0)
        if total > 0 and rec_count / total > 0.3:
            recs.append(
                (
                    "ℹ High recurring overhead",
                    f"{rec_count} of {total} tickets "
                    f"({rec_count / total * 100:.0f}%) are recurring/ceremony. "
                    "This limits delivery capacity. Consider consolidating "
                    "or timeboxing recurring work.",
                )
            )

    # Per-developer output — use actual contributor stats when available
    cs_list = ex.get("contributor_stats", [])
    if isinstance(cs_list, list) and cs_list:
        cs_velocities = [c.get("per_sprint", 0) for c in cs_list if c.get("per_sprint", 0) > 0]
        if cs_velocities:
            cs_avg = round(sum(cs_velocities) / len(cs_velocities), 1)
            cs_low = [c for c in cs_list if 0 < c.get("per_sprint", 0) < 2 and c.get("sprints_active", 0) >= 2]
            if cs_avg < 3:
                recs.append(
                    (
                        "ℹ Low per-developer output",
                        f"Contributors average {cs_avg} pts/sprint. "
                        "Check for blockers, context-switching, or oversized stories.",
                    )
                )
            elif cs_low:
                low_names = ", ".join(c["name"] for c in cs_low[:3])
                recs.append(
                    (
                        "ℹ Uneven developer output",
                        f"{low_names} deliver below 2 pts/sprint. "
                        "May indicate blockers, onboarding, or heavy KTLO load.",
                    )
                )

    repos = ex.get("repositories", {})
    if isinstance(repos, dict):
        for sr in repos.get("spillover_repos", []):
            if isinstance(sr, dict) and sr.get("spill_rate", 0) >= 40:
                recs.append(
                    (
                        f"⚠ {sr['repo']} has high spillover",
                        f"{sr['spill_rate']}% of stories touching "
                        f"{sr['repo']} don't complete the sprint. "
                        "This repo may have long review cycles, "
                        "difficult deployments, or complex integration work.",
                    )
                )

    shadow = ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and len(shadow) >= 2:
        recs.append(
            (
                "⚠ Shadow spillover",
                f"{len(shadow)} stories were closed then re-created "
                "in the next sprint. This masks true spillover — "
                "consider keeping the original ticket open and moving "
                "it to the next sprint instead of cloning.",
            )
        )

    td = ex.get("task_decomposition", {})
    if isinstance(td, dict):
        if td.get("task_completion_rate", 100) < 60:
            recs.append(
                (
                    "⚠ Low task completion",
                    f"Only {td['task_completion_rate']}% of sub-tasks "
                    "are completed. Incomplete tasks indicate stories "
                    "are being closed prematurely or tasks are stale.",
                )
            )
        for cat, rate, count in td.get("bottlenecks", []):
            recs.append(
                (
                    f"⚠ {cat} bottleneck",
                    f"{cat} tasks have only {rate}% completion "
                    f"({count} tasks). This suggests {cat.lower()} "
                    "is being skipped or deprioritised.",
                )
            )
        sw = td.get("stories_with_tasks", 0)
        tot = td.get("total_stories", 0)
        if tot > 10 and sw > 0 and sw / tot < 0.3:
            recs.append(
                (
                    "ℹ Low task breakdown",
                    f"Only {sw} of {tot} stories "
                    f"({sw / tot * 100:.0f}%) have sub-tasks. "
                    "Breaking stories into tasks improves visibility "
                    "and helps the team track progress.",
                )
            )

    # AC pattern recommendation (single consolidated)
    ac_recs = ex.get("ac_patterns", {})
    if isinstance(ac_recs, dict) and ac_recs.get("recommendation"):
        recs.append(("ℹ Acceptance criteria gaps", ac_recs["recommendation"]))

    # Scope change recommendations
    scope = ex.get("scope_changes", {})
    if isinstance(scope, dict) and scope.get("totals"):
        sc_totals = scope["totals"]
        sc_total = sc_totals.get("total_stories", 0)
        sc_committed = sc_totals.get("avg_committed_velocity", 0.0)
        sc_delivered = sc_totals.get("avg_delivered_velocity", 0.0)
        if sc_committed > 0 and sc_delivered / sc_committed < 0.7:
            del_pct = round(sc_delivered / sc_committed * 100)
            recs.append(
                (
                    "⚠ Low delivery accuracy",
                    f"Team delivers only {del_pct}% of committed scope "
                    f"({sc_delivered} of {sc_committed} pts avg). "
                    "Reduce sprint commitments to match actual capacity.",
                )
            )
        if sc_total > 0:
            sc_added = sc_totals.get("added_mid_sprint", 0)
            sc_re_est = sc_totals.get("re_estimated", 0)
            if sc_added / sc_total > 0.15:
                recs.append(
                    (
                        "⚠ High mid-sprint scope additions",
                        f"{sc_added} of {sc_total} stories "
                        f"({sc_added / sc_total * 100:.0f}%) "
                        "were added after the sprint started. "
                        "Protect sprint commitments by locking scope after planning.",
                    )
                )
            if sc_re_est / sc_total > 0.15:
                recs.append(
                    (
                        "⚠ Frequent re-estimation",
                        f"{sc_re_est} of {sc_total} stories "
                        f"({sc_re_est / sc_total * 100:.0f}%) "
                        "had their points changed mid-sprint. "
                        "Improve estimation accuracy with team calibration sessions.",
                    )
                )
        # High scope churn
        scope_sprints = scope.get("per_sprint", [])
        high_churn = [s for s in scope_sprints if s.get("scope_churn", 0) > 0.3]
        if len(high_churn) >= 2:
            names = ", ".join(s.get("name", "?") for s in high_churn[:3])
            recs.append(
                (
                    "⚠ High scope churn",
                    f"{len(high_churn)} sprints had >30% scope churn ({names}). "
                    "Scope is volatile — enforce a sprint lock after planning.",
                )
            )
        chains = scope.get("carry_over_chains", [])
        if len(chains) >= 3:
            recs.append(
                (
                    "⚠ Carry-over chains",
                    f"{len(chains)} stories bounced across 3+ sprints. These are zombie stories — split or kill them.",
                )
            )

    # DoD recommendation
    pdod = ex.get("proposed_dod", {})
    if isinstance(pdod, dict) and pdod.get("health") == "weak":
        pdod_items = pdod.get("items", [])
        missing = [i["practice"] for i in pdod_items if i.get("status") == "missing"]
        missing_str = ", ".join(missing[:3]) if missing else "most practices"
        recs.append(
            (
                "⚠ No consistent Definition of Done",
                f"The analysis could not find a consistent DoD for this team. "
                f"{missing_str} show no evidence of being practiced. "
                "Create a team DoD checklist — even a simple one (code reviewed, "
                "tests passing, deployed to staging) dramatically improves quality.",
            )
        )
    elif isinstance(pdod, dict) and pdod.get("health") == "moderate":
        pdod_items = pdod.get("items", [])
        emerging = [i["practice"] for i in pdod_items if i.get("status") == "emerging"]
        if emerging:
            recs.append(
                (
                    "ℹ Create a formal Definition of Done",
                    f"The team does some quality checks but inconsistently. "
                    f"{', '.join(emerging[:3])} are practiced sometimes but not always. "
                    "Write a shared DoD checklist and enforce it on every story.",
                )
            )

    return recs


# ---------------------------------------------------------------------------
# Analysis narrative — one LLM call that explains what the numbers mean
# ---------------------------------------------------------------------------

# One key per section card on the analysis results screen.
_NARRATIVE_KEYS = ("velocity", "team", "estimation", "workflow", "writing", "trends", "recommendations")

# Plain-English definitions for the jargon metrics — shared by the TUI cards
# and the HTML/MD exporters so the wording never drifts.
ANALYSIS_GLOSSARY = {
    "churn": "Churn — % of committed points added or removed mid-sprint (scope change)",
    "delta": "Δ — net points added (+) or removed (−) after the sprint started",
    "spill": "Spill% — share of stories not finished, carried into the next sprint",
    "cycle": "Cycle — days from work starting to done",
    "variance": "± variance — how much velocity swings sprint-to-sprint",
    "confidence": "Confidence — how many samples back a number (low <5, high ≥15)",
}

# Coaching insight categories — key order is display order. Shared by the TUI
# (insights screen + results card) and the MD/HTML exporters so the wording
# never drifts (same pattern as ANALYSIS_GLOSSARY above).
INSIGHT_CATEGORIES = (
    ("start", "Start doing"),
    ("stop", "Stop doing"),
    ("keep", "Keep doing"),
    ("try", "Worth trying"),
)

_INSIGHT_KEYS = tuple(k for k, _ in INSIGHT_CATEGORIES)

# Cap items per category so the screen stays scannable.
_INSIGHT_MAX_ITEMS = 4


def _build_narrative_digest(profile: TeamProfile, examples: dict | None) -> str:
    """Compact per-section metric digest fed to the narrative LLM call.

    One short block per section key — a summary, not a raw data dump, to keep
    the prompt small (~1-2k tokens).
    """
    ex = examples or {}
    stats = compute_headline_stats(profile, ex)
    blocks: list[str] = []

    vel_parts = [
        f"velocity {stats['velocity']}±{stats['stddev']} pts/sprint ({stats['var_pct']}% variance)",
        f"completion {stats['completion_rate']:.0f}%",
    ]
    if stats["delivery_accuracy"]:
        vel_parts.append(
            f"committed {stats['committed_avg']:g} → delivered {stats['delivered_avg']:g} "
            f"({stats['delivery_accuracy']}% delivery accuracy)"
        )
    if stats["spillover_pct"] > 0:
        vel_parts.append(f"spillover {stats['spillover_pct']:.0f}%")
    scope = ex.get("scope_changes", {})
    if isinstance(scope, dict):
        churns = [s.get("scope_churn", 0) for s in scope.get("per_sprint", []) if s.get("committed_pts")]
        if churns:
            vel_parts.append(f"avg scope churn {sum(churns) / len(churns):.0%}")
    if stats["trend"]:
        vel_parts.append(f"trend {stats['trend']}")
    blocks.append("velocity: " + "; ".join(vel_parts))

    team_parts = []
    if stats["team_size"]:
        team_parts.append(f"{stats['team_size']} contributors")
    if stats["per_dev"]:
        team_parts.append(f"{stats['per_dev']} pts/dev/sprint")
    contrib = ex.get("contributor_stats", [])
    if isinstance(contrib, list) and contrib:
        tops = ", ".join(f"{c.get('name', '?')} {c.get('delivery_pts', 0)}pts" for c in contrib[:3])
        team_parts.append(f"top contributors: {tops}")
    blocks.append("team: " + ("; ".join(team_parts) if team_parts else "no contributor data"))

    est_parts = [f"estimation accuracy {stats['estimation_accuracy']:.0f}%"]
    for cal in profile.point_calibrations:
        if cal.sample_count > 0:
            est_parts.append(f"{cal.point_value}pt≈{cal.avg_cycle_time_days:.0f}d cycle ({cal.sample_count} samples)")
    blocks.append("estimation: " + "; ".join(est_parts))

    dod = profile.dod_signal
    wf_parts = []
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict) and td.get("total_stories"):
        wf_parts.append(
            f"{td.get('stories_with_tasks', 0)}/{td['total_stories']} stories have subtasks, "
            f"{td.get('task_completion_rate', 0)}% task completion"
        )
    wf_parts.append(
        f"DoD signals: testing {dod.stories_with_testing_mention_pct:.0f}%, "
        f"PR {dod.stories_with_pr_link_pct:.0f}%, review {dod.stories_with_review_mention_pct:.0f}%, "
        f"deploy {dod.stories_with_deploy_mention_pct:.0f}%"
    )
    pdod = ex.get("proposed_dod", {})
    if isinstance(pdod, dict) and pdod.get("health"):
        wf_parts.append(f"DoD health: {pdod['health']}")
    blocks.append("workflow: " + "; ".join(wf_parts))

    wp = profile.writing_patterns
    wr_parts = [
        f"Given/When/Then: {'yes' if wp.uses_given_when_then else 'no'}",
        f"median {wp.median_ac_count} ACs and {wp.median_task_count_per_story} tasks per story",
    ]
    ac_pat = ex.get("ac_patterns", {})
    if isinstance(ac_pat, dict) and ac_pat.get("stories_with_ac_pct") is not None:
        wr_parts.append(f"{ac_pat['stories_with_ac_pct']}% of stories have ACs")
    blocks.append("writing: " + "; ".join(wr_parts))

    tr_parts = []
    if stats["trend"]:
        tr_parts.append(f"velocity {stats['trend']}")
    addl = ex.get("additional_patterns", {})
    bugs = addl.get("bug_rate", {}) if isinstance(addl, dict) else {}
    if isinstance(bugs, dict) and bugs.get("bug_count", 0) > 0:
        tr_parts.append(f"bug rate {bugs.get('bug_pct', 0)}% of stories")
    repos = ex.get("repositories", {})
    if isinstance(repos, dict) and repos.get("top_repos"):
        tr_parts.append(f"{len(repos['top_repos'])} active repos")
    blocks.append("trends: " + ("; ".join(tr_parts) if tr_parts else "no trend data"))

    rec_labels = [label.lstrip("⚠ℹ ") for label, _ in compute_recommendations(profile, ex)]
    blocks.append("recommendations: " + ("; ".join(rec_labels) if rec_labels else "none triggered"))

    return "\n".join(blocks)


def _fallback_narrative(profile: TeamProfile, examples: dict | None) -> dict:
    """Deterministic narrative when the LLM is unavailable (no LLM, never raises)."""
    ex = examples or {}
    stats = compute_headline_stats(profile, ex)
    recs = compute_recommendations(profile, ex)
    comp = stats["completion_rate"]
    health = "healthy" if comp >= 80 else ("mixed" if comp >= 60 else "strained")
    sections = {
        "velocity": (
            f"The team delivers about {stats['velocity']} points per sprint and completes "
            f"{comp:.0f}% of what it plans. Higher variance or churn means sprint outcomes "
            "are harder to predict."
        ),
        "team": (
            f"{stats['team_size'] or 'Several'} people contributed to the analysed sprints. "
            "The table shows how delivery, spillover and cycle time are distributed across them."
        ),
        "estimation": (
            f"Estimates hold about {stats['estimation_accuracy']:.0f}% of the time. "
            "The per-point breakdown shows how long each story size really takes for this team."
        ),
        "workflow": (
            "These signals show how consistently the team breaks stories into tasks and "
            "follows quality practices like testing, code review and deployment."
        ),
        "writing": (
            "These patterns describe how the team writes tickets — acceptance criteria, "
            "naming and structure. Consistent tickets are easier to estimate and complete."
        ),
        "trends": (
            "Longer-term signals: how velocity moves over time, how much effort goes to bugs, "
            "and which repositories the work concentrates in."
        ),
        "recommendations": (
            f"{len(recs)} improvement areas were flagged from the metrics."
            if recs
            else "No significant issues were flagged from the metrics."
        ),
    }
    summary = (
        f"Overall the team looks {health}: about {stats['velocity']} points per sprint with "
        f"{comp:.0f}% sprint completion. "
        + (f"{len(recs)} areas were flagged for attention — see Recommendations." if recs else "")
    ).strip()
    return {"executive_summary": summary, "sections": sections}


def _generate_analysis_narrative(profile: TeamProfile, examples: dict | None) -> dict:
    """Use the LLM to explain what the analysis means in plain English.

    Returns ``{"executive_summary": str, "sections": {key: str}}`` with one
    entry per section card. Falls back to deterministic text on any failure —
    this must never raise (it runs at the end of every analysis).
    """
    ex = examples or {}
    digest = _build_narrative_digest(profile, ex)

    # ARC framework: Ask (explain the metrics), Requirements, Context (digest)
    prompt = (
        "You are an experienced Scrum coach. A team's sprint history was analysed; "
        "the key metrics are below. Explain them for a Scrum Master who is NOT a data person.\n\n"
        "Requirements:\n"
        '- "executive_summary": 3-4 sentences — overall health, the single biggest risk, '
        "and the single biggest strength.\n"
        "- One entry per section key (velocity, team, estimation, workflow, writing, trends, "
        "recommendations): 2-3 sentences (max 60 words) on what these numbers MEAN for this "
        "team and what to watch. Plain English, no jargon; refer to concrete numbers.\n\n"
        "## Metrics digest\n" + digest + "\n\n"
        'Return ONLY a JSON object: {"executive_summary": "...", '
        '"sections": {"velocity": "...", "team": "...", "estimation": "...", '
        '"workflow": "...", "writing": "...", "trends": "...", "recommendations": "..."}}'
    )

    fallback = _fallback_narrative(profile, ex)
    try:
        response = _llm_invoke(prompt, temperature=0.0)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        if isinstance(result, dict) and isinstance(result.get("sections"), dict):
            sections = {
                k: v for k, v in result["sections"].items() if k in _NARRATIVE_KEYS and isinstance(v, str) and v
            }
            # Back-fill any section the LLM skipped with the deterministic text
            for k in _NARRATIVE_KEYS:
                sections.setdefault(k, fallback["sections"][k])
            summary = result.get("executive_summary")
            if not isinstance(summary, str) or not summary:
                summary = fallback["executive_summary"]
            logger.info("LLM analysis narrative generated (%d sections)", len(sections))
            return {"executive_summary": summary, "sections": sections}
        logger.warning("LLM analysis narrative had unexpected shape; using fallback")
    except Exception as exc:
        logger.warning("LLM analysis narrative generation failed: %s", exc)

    return fallback


def _insight_item(title: str, detail: str, evidence: str = "") -> dict:
    """One coaching insight — small dict so it JSON-serializes with the examples."""
    return {"title": title, "detail": detail, "evidence": evidence}


def _fallback_team_insights(profile: TeamProfile, examples: dict | None) -> dict:
    """Deterministic coaching insights when the LLM is unavailable.

    Maps the existing recommendation engine + headline stats into the four
    coaching categories. Every category is guaranteed non-empty so the insights
    screen always has content. Pure — no LLM, no I/O, never raises.
    """
    ex = examples or {}
    stats = compute_headline_stats(profile, ex)
    recs = compute_recommendations(profile, ex)

    # ⚠ warnings read as "stop doing this"; ℹ notices read as "start doing this".
    stop = [_insight_item(label.lstrip("⚠ "), text) for label, text in recs if label.startswith("⚠")]
    start = [_insight_item(label.lstrip("ℹ "), text) for label, text in recs if label.startswith("ℹ")]

    keep: list[dict] = []
    if stats["completion_rate"] >= 80:
        keep.append(
            _insight_item(
                "Realistic sprint commitments",
                "The team finishes most of what it plans — keep sizing sprints the same way.",
                f"{stats['completion_rate']:.0f}% average sprint completion",
            )
        )
    if profile.writing_patterns.uses_given_when_then:
        keep.append(
            _insight_item(
                "Given/When/Then acceptance criteria",
                "Structured acceptance criteria make stories testable — keep writing them this way.",
                "Given/When/Then structure detected in story descriptions",
            )
        )
    if stats["estimation_accuracy"] >= 70:
        keep.append(
            _insight_item(
                "Current estimation practice",
                "Estimates hold up well against actual cycle times — keep the calibration you have.",
                f"{stats['estimation_accuracy']:.0f}% estimation accuracy",
            )
        )
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict) and td.get("total_stories", 0) > 0:
        sw, tot = td.get("stories_with_tasks", 0), td["total_stories"]
        if sw / tot >= 0.5:
            keep.append(
                _insight_item(
                    "Breaking stories into sub-tasks",
                    "Decomposed stories are easier to track and finish — keep the habit.",
                    f"{sw} of {tot} stories have sub-tasks",
                )
            )
    # Guaranteed grounded final item so `keep` is never empty.
    keep.append(
        _insight_item(
            "The current sprint cadence",
            "A steady cadence is the foundation the rest of the analysis builds on.",
            f"{profile.sample_sprints} sprints analysed",
        )
    )

    try_items: list[dict] = []
    if stats["spillover_pct"] > 15:
        try_items.append(
            _insight_item(
                "WIP limits",
                "Capping work-in-progress forces stories to finish before new ones start, cutting carry-over.",
                f"{stats['spillover_pct']:.0f}% of stories carry over between sprints",
            )
        )
    if stats["var_pct"] > 35:
        try_items.append(
            _insight_item(
                "Capacity-based sprint planning",
                "Plan the next sprint from the last 3 sprints' delivered points instead of gut feel.",
                f"Velocity swings ±{stats['var_pct']}% sprint-to-sprint",
            )
        )
    if isinstance(td, dict) and td.get("total_stories", 0) > 10:
        sw, tot = td.get("stories_with_tasks", 0), td["total_stories"]
        if sw / tot < 0.3:
            try_items.append(
                _insight_item(
                    "A task-breakdown template",
                    "A shared checklist (build / test / review / deploy) makes decomposition routine.",
                    f"Only {sw} of {tot} stories have sub-tasks",
                )
            )
    if not try_items:
        try_items.append(
            _insight_item(
                "A mid-sprint goal check-in",
                "A 10-minute halfway review catches at-risk stories while there is still time to react.",
                f"Based on {profile.sample_sprints} analysed sprints",
            )
        )

    # `start`/`stop` fall back to generic-but-useful coaching when no
    # recommendation triggered (a healthy team still gets a full screen).
    if not start:
        start.append(
            _insight_item(
                "Reviewing these metrics with the team",
                "Share the analysis in a retro — the numbers land better when the team owns them.",
                f"{profile.sample_sprints} sprints analysed",
            )
        )
    if not stop:
        stop.append(
            _insight_item(
                "Adding unplanned work mid-sprint",
                "Even healthy teams leak scope — route new requests to the backlog unless truly urgent.",
                "No major warning signals were triggered by the metrics",
            )
        )

    return {
        "start": start[:_INSIGHT_MAX_ITEMS],
        "stop": stop[:_INSIGHT_MAX_ITEMS],
        "keep": keep[:_INSIGHT_MAX_ITEMS],
        "try": try_items[:_INSIGHT_MAX_ITEMS],
    }


def _generate_team_insights(profile: TeamProfile, examples: dict | None) -> dict:
    """Use the LLM to coach the team lead: start / stop / keep / worth trying.

    Returns ``{"start": [...], "stop": [...], "keep": [...], "try": [...]}``
    where each item is ``{"title", "detail", "evidence"}``. Falls back to
    deterministic insights on any failure — this must never raise (it runs at
    the end of every analysis, like the narrative call above).
    """
    ex = examples or {}
    digest = _build_narrative_digest(profile, ex)

    # See docs: "Prompt Construction" — ARC framework: Ask (coach the lead),
    # Requirements (categories, item shape, evidence), Context (metrics digest).
    prompt = (
        "You are an experienced agile coach. A team's sprint history was analysed; "
        "the key metrics are below. Coach the team lead on how to improve the team — "
        "team setup, ways of working, estimation, process, and the mix of work.\n\n"
        "Requirements:\n"
        '- Four categories: "start" (things to start doing), "stop" (things to stop '
        'doing or avoid), "keep" (things working well to keep doing), "try" '
        "(experiments worth trying).\n"
        '- 2-4 items per category. Each item: "title" (imperative, max 10 words), '
        '"detail" (1-2 plain-English sentences of practical advice), "evidence" '
        "(one short phrase citing a concrete number from the digest).\n"
        "- Ground every item in the digest — no generic advice the data doesn't support.\n\n"
        "## Metrics digest\n" + digest + "\n\n"
        "Return ONLY a JSON object: "
        '{"start": [{"title": "...", "detail": "...", "evidence": "..."}], '
        '"stop": [...], "keep": [...], "try": [...]}'
    )

    fallback = _fallback_team_insights(profile, ex)
    try:
        response = _llm_invoke(prompt, temperature=0.0)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        if isinstance(result, dict):
            insights: dict = {}
            for key in _INSIGHT_KEYS:
                raw = result.get(key)
                items = []
                if isinstance(raw, list):
                    for it in raw:
                        if isinstance(it, dict) and isinstance(it.get("title"), str) and it["title"].strip():
                            items.append(
                                _insight_item(
                                    it["title"].strip(),
                                    it["detail"].strip() if isinstance(it.get("detail"), str) else "",
                                    it["evidence"].strip() if isinstance(it.get("evidence"), str) else "",
                                )
                            )
                # Back-fill any empty/missing category with deterministic items
                insights[key] = items[:_INSIGHT_MAX_ITEMS] if items else fallback[key]
            logger.info(
                "LLM team insights generated (%s)",
                ", ".join(f"{k}={len(v)}" for k, v in insights.items()),
            )
            return insights
        logger.warning("LLM team insights had unexpected shape; using fallback")
    except Exception as exc:
        logger.warning("LLM team insights generation failed: %s", exc)

    return fallback


def _worker_writing_patterns(all_stories: list[dict], progress: list[str]) -> WritingPatterns:
    """Worker 3: Work item shapes and writing pattern analysis."""
    progress.append("Computing velocity & spillover\u2026")

    ac_counts = [s.get("ac_count", 0) for s in all_stories if s.get("ac_count", 0) > 0]
    task_counts = [s.get("task_count", 0) for s in all_stories if s.get("task_count", 0) > 0]
    median_ac = statistics.median(ac_counts) if ac_counts else 0.0
    median_tasks = statistics.median(task_counts) if task_counts else 0.0

    # Sub-task label distribution
    label_counter: dict[str, int] = defaultdict(int)
    subtask_patterns: dict[str, int] = defaultdict(int)
    for s in all_stories:
        for label in s.get("subtask_labels", []):
            label_counter[label.lower().strip()] += 1
        for pattern in s.get("subtask_titles", []):
            subtask_patterns[pattern.strip()] += 1

    total_labels = sum(label_counter.values()) or 1
    label_dist = tuple(
        (lbl, round(cnt / total_labels, 2)) for lbl, cnt in sorted(label_counter.items(), key=lambda x: -x[1])[:10]
    )
    common_subtasks = tuple(p for p, _ in sorted(subtask_patterns.items(), key=lambda x: -x[1])[:5])

    # Naming consistency: check if subtask titles follow a pattern (>60% share prefixes)
    titles = list(subtask_patterns.keys())
    consistent = False
    if len(titles) >= 3:
        prefixes = defaultdict(int)
        for t in titles:
            words = t.split()
            if words:
                prefixes[words[0].lower()] += 1
        top_prefix_pct = max(prefixes.values()) / len(titles) if titles else 0
        consistent = top_prefix_pct > 0.6

    # Persona extraction from story summaries
    persona_counter: dict[str, int] = defaultdict(int)
    _persona_re = re.compile(r"\bas\s+(?:a|an)\s+(\w[\w\s]{0,20}?)(?:,|\s+I\b)", re.IGNORECASE)
    for s in all_stories:
        summary = s.get("summary", "")
        m = _persona_re.search(summary)
        if m:
            persona_counter[m.group(1).strip().lower()] += 1
    common_personas = tuple(p for p, _ in sorted(persona_counter.items(), key=lambda x: -x[1])[:5])

    # Given/When/Then detection
    gwt_count = sum(1 for s in all_stories if s.get("description") and len(_GWT_RE.findall(s["description"])) >= 2)
    uses_gwt = (gwt_count / len(all_stories) > 0.3) if all_stories else False

    # Stories with subtasks
    with_subtasks = sum(1 for s in all_stories if s.get("task_count", 0) > 0)
    subtasks_pct = (with_subtasks / len(all_stories) * 100) if all_stories else 0.0

    # Epic description analysis
    epic_desc_lengths = [len(s.get("epic_description", "")) for s in all_stories if s.get("epic_description")]
    epic_desc_avg = int(sum(epic_desc_lengths) / len(epic_desc_lengths)) if epic_desc_lengths else 0
    epics_with_desc_count = sum(1 for s in all_stories if s.get("epic_has_description", False))
    epics_total = sum(1 for s in all_stories if s.get("epic_key"))
    epics_with_desc_pct = (epics_with_desc_count / epics_total * 100) if epics_total else 0.0

    return WritingPatterns(
        median_ac_count=round(median_ac, 1),
        median_task_count_per_story=round(median_tasks, 1),
        subtask_label_distribution=label_dist,
        common_subtask_patterns=common_subtasks,
        subtasks_use_consistent_naming=consistent,
        common_personas=common_personas,
        uses_given_when_then=uses_gwt,
        epic_description_length_avg=epic_desc_avg,
        stories_with_subtasks_pct=round(subtasks_pct, 1),
        epics_with_description_pct=round(epics_with_desc_pct, 1),
    )


def _worker_dod_signals(all_stories: list[dict], progress: list[str]) -> DoDSignal:
    """Worker 4: Definition of Done signals from comments and descriptions."""
    progress.append("Building team profile\u2026")

    if not all_stories:
        return DoDSignal()

    n = len(all_stories)
    with_comments = 0
    with_pr = 0
    with_review = 0
    with_testing = 0
    with_deploy = 0
    comment_counts: list[int] = []
    checklist_counter: dict[str, int] = defaultdict(int)

    for s in all_stories:
        comments = s.get("comments", [])
        description = s.get("description", "") or ""
        all_text = description + " " + " ".join(comments)

        if comments:
            with_comments += 1
        comment_counts.append(len(comments))

        if _PR_RE.search(all_text):
            with_pr += 1
        if _REVIEW_RE.search(all_text):
            with_review += 1
        if _TEST_RE.search(all_text):
            with_testing += 1
        if _DEPLOY_RE.search(all_text):
            with_deploy += 1

        for _m in _PR_RE.finditer(all_text):
            checklist_counter["PR linked"] += 1
        for _m in _REVIEW_RE.finditer(all_text):
            checklist_counter["code reviewed"] += 1
        for _m in _TEST_RE.finditer(all_text):
            checklist_counter["tests passing"] += 1
        for _m in _DEPLOY_RE.finditer(all_text):
            checklist_counter["deployed"] += 1

        # Supplement with LLM-parsed DoD signals (if available)
        for sig in s.get("parsed_dod_signals", []):
            if isinstance(sig, str) and sig:
                checklist_counter[sig.lower()] += 1

    common_items = tuple(item for item, _ in sorted(checklist_counter.items(), key=lambda x: -x[1])[:6])
    avg_comments = sum(comment_counts) / n if n else 0.0

    return DoDSignal(
        common_checklist_items=common_items,
        stories_with_comments_pct=round(with_comments / n * 100, 1) if n else 0.0,
        stories_with_pr_link_pct=round(with_pr / n * 100, 1) if n else 0.0,
        stories_with_review_mention_pct=round(with_review / n * 100, 1) if n else 0.0,
        stories_with_testing_mention_pct=round(with_testing / n * 100, 1) if n else 0.0,
        stories_with_deploy_mention_pct=round(with_deploy / n * 100, 1) if n else 0.0,
        avg_comments_before_resolution=round(avg_comments, 1),
    )


def selected_member_velocity(contributor_stats: list[dict], members: list[str]) -> float:
    """Combined velocity from selected members' per-sprint contributions.

    Sprint velocity is a sprint-level aggregate, so a member subset's velocity is the
    sum of each selected member's ``per_sprint`` figure (matched case-insensitively by
    name). Shared by the analysis engine (re-scoping a run) and planning mode
    (``agent/nodes.py`` recomputes velocity when the lead picks a subset).
    """
    if not contributor_stats or not members:
        return 0.0
    norm = {m.strip().lower() for m in members if m and m.strip()}
    total = 0.0
    matched = 0
    for c in contributor_stats:
        name = (c.get("name", "") or "").strip()
        per_sprint = c.get("per_sprint", 0.0)
        if name and per_sprint > 0 and name.lower() in norm:
            total += per_sprint
            matched += 1
    logger.info("Velocity from %d/%d selected members: %.1f pts/sprint", matched, len(members), total)
    return round(total, 1)


def _run_ai_usage_component(
    source: str,
    project_key: str,
    delivery_stories: list[dict],
    all_stories: list[dict],
    members: list[str] | None,
    progress: list[str],
    sub_sources: list[str] | None = None,
    analysis_depth: str = "deep",
) -> tuple[object | None, dict | None]:
    """Run the AI-adoption ('code') sub-analysis over ``sub_sources`` (github/azdo;
    None = all). Returns ``(signal, blob)`` or ``(None, None)`` on failure. Best-effort
    — never raises. The engine runs this ONCE globally; it's also reused inline by the
    delivery pipeline for back-compat."""
    progress.append("Scanning AI-tool footprint…")
    try:
        from yeaboi.analysis.ai_usage import generate_ai_adoption_insights, run_ai_adoption

        signal, ai_examples = run_ai_adoption(
            source, project_key, delivery_stories, all_stories, members=members, sub_sources=sub_sources
        )
        # Only spend an LLM call on coaching when something was actually scanned;
        # an empty footprint (no repos/creds) coaches deterministically.
        if analysis_depth == "deep" and signal.scanned_commits + signal.scanned_prs > 0:
            ai_examples["insights"] = generate_ai_adoption_insights(signal, ai_examples)
        else:
            from yeaboi.analysis.ai_usage import _fallback_ai_adoption_insights

            ai_examples["insights"] = _fallback_ai_adoption_insights(signal, ai_examples.get("samples"))
        return signal, ai_examples
    except Exception:  # pragma: no cover - defensive; run_ai_adoption already guards
        logger.exception("AI-adoption analysis failed; continuing without it")
        return None, None


def _run_doc_quality_component(
    source: str,
    project_key: str,
    progress: list[str],
    sub_sources: list[str] | None = None,
    analysis_depth: str = "deep",
) -> tuple[object | None, dict | None]:
    """Run the documentation-quality ('docs') sub-analysis over ``sub_sources``
    (confluence/notion; None = all). Returns ``(signal, blob)`` or ``(None, None)`` on
    failure. Best-effort — never raises."""
    progress.append("Assessing documentation clarity…")
    try:
        from yeaboi.analysis.doc_quality import generate_doc_quality_insights, run_doc_quality

        dq_signal, dq_examples = run_doc_quality(source, project_key, sub_sources=sub_sources)
        # Only spend an LLM call on coaching when pages were actually read.
        if analysis_depth == "deep" and dq_signal.pages_scanned > 0:
            dq_examples["insights"] = generate_doc_quality_insights(dq_signal, dq_examples)
        else:
            from yeaboi.analysis.doc_quality import _fallback_doc_quality_insights

            dq_examples["insights"] = _fallback_doc_quality_insights(dq_signal, dq_examples.get("samples"))
        return dq_signal, dq_examples
    except Exception:  # pragma: no cover - defensive; run_doc_quality already guards
        logger.exception("Doc-quality analysis failed; continuing without it")
        return None, None


def _run_parallel_analysis(
    source: str,
    project_key: str,
    sprint_data: list[dict],
    progress: list[str] | None = None,
    include_ai_usage: bool = True,
    include_doc_quality: bool = True,
    members: list[str] | None = None,
    warnings: list[str] | None = None,
    analysis_depth: str = "deep",
    include_insights: bool = True,
    db_path=None,
    cache_updates: dict[str, tuple[str, dict]] | None = None,
) -> tuple[TeamProfile, dict]:
    """Run the 4-worker parallel analysis and merge results into a TeamProfile.

    ``progress`` is a shared list the TUI can read to display live status messages.
    Each worker appends a string when it starts (e.g. "Fetching sprint history\u2026").

    When ``include_ai_usage`` is set, an extra best-effort step scans the team's
    commits and PRs for AI-tool markers and attaches an ``AiAdoptionSignal`` to the
    profile plus an ``examples["ai_adoption"]`` blob (see ``analysis/ai_usage.py``).

    When ``include_doc_quality`` is set, another best-effort step reads the team's
    recently-changed Notion/Confluence pages, scores their clarity + a stylometric
    AI-likelihood estimate, and attaches a ``DocQualitySignal`` plus an
    ``examples["doc_quality"]`` blob (see ``analysis/doc_quality.py``).

    When ``members`` (a subset of assignee names) is given the analysis is
    **re-scoped** to those people: stories are filtered by assignee (so calibration,
    DoD, writing patterns and per-contributor stats reflect only them) and velocity
    is recomputed from their per-sprint contributions, and calibration/estimation
    accuracy reflect only their tickets. Sprint completion rate stays board-level (it
    is a sprint-level figure that can't be attributed per person) \u2014 the caveat is
    appended to ``warnings``. An empty match falls back to the whole team (a warning
    is recorded).
    """
    if progress is None:
        progress = []

    all_stories: list[dict] = []
    for sd in sprint_data:
        all_stories.extend(sd.get("stories", []))

    # Member subset \u2014 re-scope the analysis to the selected people. Filter here (once,
    # before tagging/enrichment) so every downstream derivation (calibration, DoD,
    # contributor stats) inherits the subset automatically. An empty match would make
    # for an empty analysis, so we fall back to the whole team with a warning.
    if members:
        _norm = {m.strip().lower() for m in members if m and m.strip()}
        _scoped = [s for s in all_stories if (s.get("assignee", "") or "").strip().lower() in _norm]
        if _scoped:
            logger.info("Member filter: %d\u2192%d stories for %s", len(all_stories), len(_scoped), members)
            all_stories = _scoped
            if warnings is not None:
                warnings.append(
                    "Velocity, calibration and estimation accuracy are scoped to the selected "
                    "members; sprint completion rate stays board-level (it's a sprint-level "
                    "figure that can't be attributed per person)."
                )
        else:
            logger.warning("Member filter %s matched 0 stories \u2014 analysing the whole team", members)
            if warnings is not None:
                warnings.append("Member filter matched no stories \u2014 analysed the whole team instead.")
            members = None

    # Tag recurring/ceremony tickets so they're excluded from calibration
    _tag_recurring_batch(all_stories)
    delivery_stories = [s for s in all_stories if not s.get("is_recurring")]
    recurring_stories = [s for s in all_stories if s.get("is_recurring")]
    logger.info(
        "Team analysis: %d total stories, %d delivery, %d recurring across %d sprints",
        len(all_stories),
        len(delivery_stories),
        len(recurring_stories),
        len(sprint_data),
    )

    # Deep mode enriches tickets with cached/batched LLM classification. Quick
    # mode deliberately stays zero-LLM and uses the existing deterministic fields.
    if analysis_depth == "deep":
        _parsed_tickets = _parse_tickets_with_llm(
            delivery_stories,
            progress,
            source=source,
            project_key=project_key,
            db_path=db_path,
            cache_updates=cache_updates,
        )
    else:
        progress.append("Using fast ticket parsing…")
        _parsed_tickets = {}
    _enrich_stories_with_parsed(delivery_stories, _parsed_tickets)

    # Re-filter: LLM may have flagged additional stories as recurring
    if _parsed_tickets:
        _newly_recurring = [s for s in delivery_stories if s.get("is_recurring")]
        if _newly_recurring:
            recurring_stories.extend(_newly_recurring)
            delivery_stories = [s for s in delivery_stories if not s.get("is_recurring")]
            logger.info("LLM flagged %d additional recurring stories", len(_newly_recurring))

    # Story shapes by discipline (computed in main thread — lightweight)
    by_discipline: dict[str, list[dict]] = defaultdict(list)
    for s in delivery_stories:
        disc = s.get("discipline", "fullstack")
        by_discipline[disc].append(s)

    shapes = []
    for disc, stories_in_disc in sorted(by_discipline.items()):
        pts_vals = [_safe_float(s.get("points", 0)) for s in stories_in_disc]
        ac_vals = [_safe_float(s.get("ac_count", 0)) for s in stories_in_disc]
        task_vals = [_safe_float(s.get("task_count", 0)) for s in stories_in_disc]
        shapes.append(
            StoryShapePattern(
                discipline=disc,
                avg_points=round(sum(pts_vals) / len(pts_vals), 1) if pts_vals else 0.0,
                avg_ac_count=round(sum(ac_vals) / len(ac_vals), 1) if ac_vals else 0.0,
                avg_task_count=round(sum(task_vals) / len(task_vals), 1) if task_vals else 0.0,
                sample_count=len(stories_in_disc),
            )
        )

    # Epic patterns (delivery stories only)
    by_epic: dict[str, list[dict]] = defaultdict(list)
    for s in delivery_stories:
        ek = s.get("epic_key", "")
        if ek:
            by_epic[ek].append(s)

    if by_epic:
        epic_story_counts = [len(stories) for stories in by_epic.values()]
        epic_point_totals = [sum(_safe_float(s.get("points", 0)) for s in stories) for stories in by_epic.values()]
        epic_pattern = EpicPattern(
            avg_stories_per_epic=round(sum(epic_story_counts) / len(epic_story_counts), 1),
            avg_points_per_epic=round(sum(epic_point_totals) / len(epic_point_totals), 1),
            typical_story_count_range=(min(epic_story_counts), max(epic_story_counts)),
            sample_count=len(by_epic),
        )
    else:
        epic_pattern = EpicPattern()

    # 4-worker parallel analysis
    results: dict = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_worker_sprint_velocity, sprint_data, progress): "velocity",
            executor.submit(_worker_point_calibration, delivery_stories, sprint_data, progress): "calibration",
            executor.submit(_worker_writing_patterns, delivery_stories, progress): "writing",
            executor.submit(_worker_dod_signals, delivery_stories, progress): "dod",
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception:
                logger.exception("Worker %s failed", key)
                results[key] = None

    vel = results.get("velocity") or {}
    cal = results.get("calibration") or {}
    writing = results.get("writing") or WritingPatterns()
    dod = results.get("dod") or DoDSignal()

    # Collect illustrative examples for each metric
    examples = _collect_examples(delivery_stories, sprint_data)

    # Include sprint-by-sprint details and shadow spillover for the report
    examples["sprint_details"] = vel.get("sprint_details", [])  # type: ignore[assignment]
    examples["shadow_spillover"] = vel.get("shadow_spillover", [])  # type: ignore[assignment]

    # Task decomposition analysis from subtask_details
    examples["task_decomposition"] = _analyse_subtasks(delivery_stories)  # type: ignore[assignment]

    # Proposed Definition of Done — cross-reference DoD signals with subtask patterns
    examples["proposed_dod"] = _analyse_proposed_dod(  # type: ignore[assignment]
        dod,
        examples.get("task_decomposition", {}),
        delivery_stories,
    )

    # Ticket naming and organisation analysis
    examples["naming_conventions"] = _analyse_naming_conventions(delivery_stories)  # type: ignore[assignment]

    # Story/epic/subtask structure analysis
    examples["story_structure"] = _analyse_story_structure(delivery_stories, all_stories)  # type: ignore[assignment]

    # Board column workflow analysis
    examples["workflow_style"] = _analyse_workflow_columns(delivery_stories)  # type: ignore[assignment]

    # Additional patterns: estimation bias, seasonal velocity, bug rate
    examples["additional_patterns"] = _analyse_additional_patterns(  # type: ignore[assignment]
        delivery_stories,
        sprint_data,
    )

    # Acceptance criteria pattern analysis
    examples["ac_patterns"] = _analyse_acceptance_criteria(delivery_stories)  # type: ignore[assignment]

    # LLM parse stats
    llm_count = sum(1 for s in delivery_stories if s.get("parse_source") == "llm")
    regex_count = sum(1 for s in delivery_stories if s.get("parse_source") == "regex")
    if llm_count > 0 or regex_count > 0:
        examples["parse_stats"] = {  # type: ignore[assignment]
            "llm_parsed": llm_count,
            "regex_fallback": regex_count,
            "total": len(delivery_stories),
        }

    # Repository analysis from PR links in story text
    examples["repositories"] = _analyse_repositories(delivery_stories)  # type: ignore[assignment]

    # Mid-sprint scope change analysis
    examples["scope_changes"] = _analyse_scope_changes(sprint_data)  # type: ignore[assignment]

    # Add recurring work examples so the report can show them
    if recurring_stories:
        # Deduplicate by title (show unique recurring patterns)
        seen_titles: set[str] = set()
        rec_examples = []
        for s in recurring_stories:
            title = (s.get("summary", "") or "").strip().lower()
            if title not in seen_titles and s.get("issue_key"):
                seen_titles.add(title)
                rec_examples.append(
                    {
                        "issue_key": s.get("issue_key", ""),
                        "issue_url": s.get("issue_url", ""),
                        "summary": (s.get("summary", "") or "")[:60],
                        "detail": "",
                    }
                )
            if len(rec_examples) >= 5:
                break
        examples["recurring"] = rec_examples
        examples["recurring_count"] = len(recurring_stories)  # type: ignore[assignment]
        examples["delivery_count"] = len(delivery_stories)  # type: ignore[assignment]

    # ── New metrics: discipline calibration, spillover correlation, velocity trend, confidence ──

    # Discipline-specific point calibration
    disc_cal: dict[str, dict[int, dict]] = defaultdict(lambda: defaultdict(dict))
    for s in delivery_stories:
        disc = s.get("discipline", "fullstack")
        pts = int(_safe_float(s.get("points", 0)))
        if pts not in (1, 2, 3, 5, 8):
            continue
        disc_cal[disc].setdefault(pts, {"cycle_times": [], "count": 0, "spill": 0})
        disc_cal[disc][pts]["count"] += 1
        ct = s.get("cycle_time_days")
        if ct is not None:
            disc_cal[disc][pts]["cycle_times"].append(ct)
        if s.get("carried_over", False):
            disc_cal[disc][pts]["spill"] += 1

    discipline_calibration: dict[str, list[dict]] = {}
    for disc, by_pts in sorted(disc_cal.items()):
        disc_entries = []
        for pts in (1, 2, 3, 5, 8):
            data = by_pts.get(pts)
            if not data or data["count"] == 0:
                continue
            cts = data["cycle_times"]
            avg_ct = round(sum(cts) / len(cts), 1) if cts else 0.0
            variance = round(_stddev(cts), 1) if len(cts) >= 2 else 0.0
            spill_pct = round(data["spill"] / data["count"] * 100, 1)
            disc_entries.append(
                {
                    "points": pts,
                    "avg_cycle_days": avg_ct,
                    "variance": variance,
                    "samples": data["count"],
                    "spill_pct": spill_pct,
                }
            )
        if disc_entries:
            discipline_calibration[disc] = disc_entries
    examples["discipline_calibration"] = discipline_calibration  # type: ignore[assignment]

    # Spillover root-cause correlation (by size, discipline, task count)
    spill_by_size: dict[int, dict[str, int]] = defaultdict(lambda: {"spill": 0, "total": 0})
    spill_by_disc: dict[str, dict[str, int]] = defaultdict(lambda: {"spill": 0, "total": 0})
    spill_by_tasks: dict[str, dict[str, int]] = defaultdict(lambda: {"spill": 0, "total": 0})
    for s in delivery_stories:
        pts = int(_safe_float(s.get("points", 0)))
        disc = s.get("discipline", "fullstack")
        tasks = s.get("task_count", 0)
        is_spill = s.get("carried_over", False)

        if pts in (1, 2, 3, 5, 8):
            spill_by_size[pts]["total"] += 1
            if is_spill:
                spill_by_size[pts]["spill"] += 1

        spill_by_disc[disc]["total"] += 1
        if is_spill:
            spill_by_disc[disc]["spill"] += 1

        bucket = "0-1 tasks" if tasks <= 1 else ("2-3 tasks" if tasks <= 3 else "4+ tasks")
        spill_by_tasks[bucket]["total"] += 1
        if is_spill:
            spill_by_tasks[bucket]["spill"] += 1

    def _spill_pcts(d: dict) -> dict[str, float]:
        return {k: round(v["spill"] / v["total"] * 100, 1) if v["total"] > 0 else 0.0 for k, v in d.items()}

    spillover_correlation = {
        "by_size": {str(k): v for k, v in sorted(_spill_pcts(spill_by_size).items())},
        "by_discipline": dict(sorted(_spill_pcts(spill_by_disc).items())),
        "by_task_count": dict(_spill_pcts(spill_by_tasks)),
    }
    examples["spillover_correlation"] = spillover_correlation  # type: ignore[assignment]

    # Inputs for point descriptions are retained until the final enrichment
    # phase, where Deep mode runs this alongside narrative and coaching.
    point_description_args = (
        delivery_stories,
        cal.get("calibrations", ()),
        discipline_calibration,
        spillover_correlation,
    )

    # Velocity trend — simple linear regression over sprint velocities
    sprint_velocities = [sd.get("completed_points", 0.0) for sd in sprint_data if sd.get("completed_points", 0) > 0]
    velocity_trend: dict[str, object] = {"trend": "insufficient_data", "slope": 0.0}
    if len(sprint_velocities) >= 3:
        n_sv = len(sprint_velocities)
        x_mean = (n_sv - 1) / 2
        y_mean = sum(sprint_velocities) / n_sv
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(sprint_velocities))
        den = sum((i - x_mean) ** 2 for i in range(n_sv))
        slope = num / den if den else 0.0
        if abs(slope) < 0.5:
            trend_label = "stable"
        elif slope > 0:
            trend_label = "improving"
        else:
            trend_label = "degrading"
        velocity_trend = {
            "trend": trend_label,
            "slope": round(slope, 2),
            "first_velocity": round(sprint_velocities[0], 1),
            "last_velocity": round(sprint_velocities[-1], 1),
        }
    examples["velocity_trend"] = velocity_trend  # type: ignore[assignment]

    # Confidence scoring — classify each point calibration
    confidence_levels: dict[int, str] = {}
    for c_cal in cal.get("calibrations", ()):
        if c_cal.sample_count >= 15:
            confidence_levels[c_cal.point_value] = "high"
        elif c_cal.sample_count >= 5:
            confidence_levels[c_cal.point_value] = "medium"
        elif c_cal.sample_count > 0:
            confidence_levels[c_cal.point_value] = "low"
    examples["confidence_levels"] = confidence_levels  # type: ignore[assignment]

    # Team size and per-contributor analysis
    all_assignees: set[str] = set()
    # Per-person accumulators
    _c_del_pts: dict[str, float] = defaultdict(float)
    _c_del_count: dict[str, int] = defaultdict(int)
    _c_rec_pts: dict[str, float] = defaultdict(float)
    _c_sprints: dict[str, set[str]] = defaultdict(set)
    _c_spilled: dict[str, int] = defaultdict(int)
    _c_total: dict[str, int] = defaultdict(int)
    _c_cycle_times: dict[str, list[float]] = defaultdict(list)
    _c_disciplines: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    _c_work_types: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    _c_task_count: dict[str, int] = defaultdict(int)
    _c_big_stories: dict[str, int] = defaultdict(int)  # stories >= 5pts

    for s in delivery_stories:
        a = (s.get("assignee", "") or "").strip()
        if not a:
            continue
        all_assignees.add(a)
        _c_total[a] += 1
        pts = _safe_float(s.get("points", 0))
        sprint_name = s.get("sprint_name", "")
        if sprint_name:
            _c_sprints[a].add(sprint_name)
        if s.get("carried_over"):
            _c_spilled[a] += 1
        else:
            _c_del_pts[a] += pts
            _c_del_count[a] += 1
        ct = s.get("cycle_time_days")
        if ct is not None and ct > 0:
            _c_cycle_times[a].append(ct)
        disc = s.get("discipline", "fullstack")
        _c_disciplines[a][disc] += 1
        wt = s.get("work_type", "")
        if wt:
            _c_work_types[a][wt] += 1
        _c_task_count[a] += s.get("task_count", 0)
        if pts >= 5:
            _c_big_stories[a] += 1

    for s in recurring_stories:
        a = (s.get("assignee", "") or "").strip()
        if a:
            all_assignees.add(a)
            _c_rec_pts[a] += _safe_float(s.get("points", 0))

    team_size = len(all_assignees)
    vel_avg = vel.get("velocity_avg", 0.0)
    per_dev_velocity = round(vel_avg / team_size, 1) if team_size > 0 else 0.0

    # Build per-contributor stats
    contributor_stats: list[dict] = []
    for name in sorted(all_assignees):
        d_pts = _c_del_pts.get(name, 0.0)
        r_pts = _c_rec_pts.get(name, 0.0)
        d_count = _c_del_count.get(name, 0)
        total = _c_total.get(name, 0)
        spilled = _c_spilled.get(name, 0)
        sprints_active = len(_c_sprints.get(name, set()))
        per_sprint = round(d_pts / sprints_active, 1) if sprints_active else 0.0
        cts = _c_cycle_times.get(name, [])
        avg_ct = round(sum(cts) / len(cts), 1) if cts else 0.0
        spill_rate = round(spilled / total * 100) if total else 0
        # Top discipline
        discs = _c_disciplines.get(name, {})
        top_disc = max(discs, key=discs.get) if discs else "fullstack"  # type: ignore[arg-type]
        # Top work type
        wts = _c_work_types.get(name, {})
        top_wt = max(wts, key=wts.get) if wts else ""  # type: ignore[arg-type]

        contributor_stats.append(
            {
                "name": name,
                "delivery_pts": round(d_pts, 1),
                "recurring_pts": round(r_pts, 1),
                "stories_completed": d_count,
                "stories_total": total,
                "stories_spilled": spilled,
                "spill_rate": spill_rate,
                "sprints_active": sprints_active,
                "per_sprint": per_sprint,
                "avg_cycle_time": avg_ct,
                "top_discipline": top_disc,
                "top_work_type": top_wt,
                "tasks_assigned": _c_task_count.get(name, 0),
                "big_stories": _c_big_stories.get(name, 0),
            }
        )
    contributor_stats.sort(key=lambda x: -x["delivery_pts"])

    # Member re-scope: sprint velocity is a sprint-level aggregate that can't be
    # split by assignee, so when a subset is selected we recompute it from those
    # members' per-sprint contributions (the same basis planning mode uses).
    if members:
        vel_avg = selected_member_velocity(contributor_stats, members)
        per_dev_velocity = round(vel_avg / team_size, 1) if team_size > 0 else 0.0

    examples["team_members"] = sorted(all_assignees)  # type: ignore[assignment]
    examples["team_size"] = team_size  # type: ignore[assignment]
    examples["per_dev_velocity"] = per_dev_velocity  # type: ignore[assignment]
    examples["contributor_stats"] = contributor_stats  # type: ignore[assignment]

    from datetime import datetime

    team_id = f"{source}-{project_key}-{datetime.now(UTC).strftime('%Y%m%d%H%M')}"
    profile = TeamProfile(
        team_id=team_id,
        source=source,
        project_key=project_key,
        sample_sprints=len(sprint_data),
        sample_stories=len(delivery_stories),
        velocity_avg=vel_avg,
        velocity_stddev=vel.get("velocity_stddev", 0.0),
        point_calibrations=cal.get("calibrations", ()),
        story_shapes=tuple(shapes),
        epic_pattern=epic_pattern,
        estimation_accuracy_pct=cal.get("estimation_accuracy_pct", 0.0),
        sprint_completion_rate=vel.get("sprint_completion_rate", 0.0),
        spillover=cal.get("spillover", SpilloverStats()),
        dod_signal=dod,
        writing_patterns=writing,
        sprints_fully_completed=vel.get("sprints_fully_completed", 0),
        sprints_partially_completed=vel.get("sprints_partially_completed", 0),
        sprints_analysed=len(sprint_data),
    )

    logger.info(
        "Profile built: %s — %d sprints, %d stories, "
        "velocity=%.1f±%.1f, completion=%.0f%%, "
        "%d team members, %.1f pts/dev/sprint",
        team_id,
        profile.sample_sprints,
        profile.sample_stories,
        profile.velocity_avg,
        profile.velocity_stddev,
        profile.sprint_completion_rate,
        team_size,
        per_dev_velocity,
    )
    logger.info(
        "  DoD signals: PR=%.0f%% review=%.0f%% testing=%.0f%% deploy=%.0f%%",
        dod.stories_with_pr_link_pct,
        dod.stories_with_review_mention_pct,
        dod.stories_with_testing_mention_pct,
        dod.stories_with_deploy_mention_pct,
    )
    logger.info(
        "  Examples collected: %d categories, %d total items",
        len(examples),
        sum(len(v) for v in examples.values() if isinstance(v, list)),
    )

    # AI-adoption footprint + documentation quality. The analysis engine runs these
    # as decoupled GLOBAL scans and always passes include_ai_usage/include_doc_quality
    # as False here, so this inline path is exercised only by direct callers of
    # _run_parallel_analysis (e.g. tests) that rely on the True defaults. The extracted
    # _run_ai_usage_component/_run_doc_quality_component helpers are shared with the
    # engine's global scans.
    if include_ai_usage:
        signal, ai_examples = _run_ai_usage_component(
            source,
            project_key,
            delivery_stories,
            all_stories,
            members,
            progress,
            analysis_depth=analysis_depth,
        )
        if signal is not None:
            from dataclasses import replace

            profile = replace(profile, ai_adoption=signal)
            examples["ai_adoption"] = ai_examples  # type: ignore[assignment]

    if include_doc_quality:
        dq_signal, dq_examples = _run_doc_quality_component(
            source,
            project_key,
            progress,
            analysis_depth=analysis_depth,
        )
        if dq_signal is not None:
            from dataclasses import replace

            profile = replace(profile, doc_quality=dq_signal)
            examples["doc_quality"] = dq_examples  # type: ignore[assignment]

    if analysis_depth == "quick":
        progress.append("Building fast summaries…")
        examples["point_descriptions"] = _fallback_point_descriptions(point_description_args[1])
        examples["narrative"] = _fallback_narrative(profile, examples)
        if include_insights:
            examples["insights"] = _fallback_team_insights(profile, examples)
    else:
        progress.append("Generating AI enrichments in parallel…")
        enrichment: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=3 if include_insights else 2) as executor:
            futures = {
                executor.submit(_generate_point_descriptions, *point_description_args): "point_descriptions",
                executor.submit(_generate_analysis_narrative, profile, examples): "narrative",
            }
            if include_insights:
                futures[executor.submit(_generate_team_insights, profile, examples)] = "insights"
            for future in as_completed(futures):
                key = futures[future]
                try:
                    enrichment[key] = future.result()
                except Exception:
                    logger.exception("Final analysis enrichment %s failed", key)
        examples["point_descriptions"] = enrichment.get(
            "point_descriptions",
            _fallback_point_descriptions(point_description_args[1]),
        )
        examples["narrative"] = enrichment.get("narrative", _fallback_narrative(profile, examples))
        if include_insights:
            examples["insights"] = enrichment.get("insights", _fallback_team_insights(profile, examples))

    return profile, examples


def _analyse_subtasks(delivery_stories: list[dict]) -> dict:
    """Analyse subtask/child work items for decomposition patterns."""
    stories_with_tasks = [s for s in delivery_stories if s.get("subtask_details") and len(s["subtask_details"]) > 0]
    if not stories_with_tasks:
        return {}

    all_tasks: list[dict] = []
    for s in stories_with_tasks:
        all_tasks.extend(s.get("subtask_details", []))

    total_tasks = len(all_tasks)
    done_tasks = sum(1 for t in all_tasks if t.get("done", False))
    completion_rate = round(done_tasks / total_tasks * 100) if total_tasks else 0

    # Task type distribution
    type_counter: dict[str, int] = defaultdict(int)
    for t in all_tasks:
        title = (t.get("title", "") or "").lower()
        if any(k in title for k in ("test", "qa", "verify", "validation")):
            type_counter["Testing"] += 1
        elif any(k in title for k in ("review", "pr", "code review", "approval")):
            type_counter["Review"] += 1
        elif any(k in title for k in ("deploy", "release", "staging", "production")):
            type_counter["Deploy"] += 1
        elif any(k in title for k in ("design", "ux", "ui", "mockup", "wireframe")):
            type_counter["Design"] += 1
        elif any(k in title for k in ("doc", "documentation", "readme", "runbook")):
            type_counter["Documentation"] += 1
        else:
            type_counter["Development"] += 1

    total_typed = sum(type_counter.values()) or 1
    type_dist = {k: round(v / total_typed * 100) for k, v in sorted(type_counter.items(), key=lambda x: -x[1])}

    # Completion by type — which types finish and which lag?
    type_done: dict[str, list[bool]] = defaultdict(list)
    for t in all_tasks:
        title = (t.get("title", "") or "").lower()
        done = t.get("done", False)
        if any(k in title for k in ("test", "qa", "verify", "validation")):
            type_done["Testing"].append(done)
        elif any(k in title for k in ("review", "pr", "code review", "approval")):
            type_done["Review"].append(done)
        elif any(k in title for k in ("deploy", "release", "staging", "production")):
            type_done["Deploy"].append(done)
        else:
            type_done["Development"].append(done)

    bottlenecks = []
    for cat, dones in type_done.items():
        if len(dones) >= 3:
            cat_rate = sum(dones) / len(dones) * 100
            if cat_rate < 60:
                bottlenecks.append((cat, round(cat_rate), len(dones)))

    # Task assignee concentration — is work spread or siloed?
    task_assignees: dict[str, int] = defaultdict(int)
    for t in all_tasks:
        a = (t.get("assignee", "") or "").strip()
        if a:
            task_assignees[a] += 1

    # Common task title patterns
    title_counter: dict[str, int] = defaultdict(int)
    for t in all_tasks:
        title = (t.get("title", "") or "").strip()
        if title:
            title_counter[title] += 1
    common_tasks = [(title, cnt) for title, cnt in sorted(title_counter.items(), key=lambda x: -x[1])[:5] if cnt >= 2]

    return {
        "stories_with_tasks": len(stories_with_tasks),
        "total_stories": len(delivery_stories),
        "total_tasks": total_tasks,
        "avg_tasks_per_story": round(total_tasks / len(stories_with_tasks), 1),
        "task_completion_rate": completion_rate,
        "type_distribution": type_dist,
        "bottlenecks": bottlenecks,
        "task_assignees": dict(sorted(task_assignees.items(), key=lambda x: -x[1])[:8]),
        "common_tasks": common_tasks,
    }


def _analyse_proposed_dod(
    dod_signal: object,
    task_decomp: dict,
    delivery_stories: list[dict],
) -> dict:
    """Generate a proposed Definition of Done by cross-referencing multiple signals.

    Scans stories for 8 quality practices using comment/description text,
    subtask patterns, and subtask completion rates. Each practice gets a
    status: established (strong evidence), emerging (some evidence), or
    missing (no evidence found).
    """
    items: list[dict] = []
    n = len(delivery_stories) or 1

    # ── Scan stories for additional signal percentages ─────────────
    doc_count = 0
    ac_count = 0
    security_count = 0
    perf_count = 0
    monitoring_count = 0
    for s in delivery_stories:
        text = (s.get("description", "") or "") + " " + " ".join(s.get("comments", []))
        if _DOC_RE.search(text):
            doc_count += 1
        if _AC_VERIFY_RE.search(text):
            ac_count += 1
        if _SECURITY_RE.search(text):
            security_count += 1
        if _PERF_RE.search(text):
            perf_count += 1
        if _MONITORING_RE.search(text):
            monitoring_count += 1

    extra_signals = {
        "Documentation": round(doc_count / n * 100, 1),
        "AC verification": round(ac_count / n * 100, 1),
        "Security review": round(security_count / n * 100, 1),
        "Performance testing": round(perf_count / n * 100, 1),
        "Monitoring & observability": round(monitoring_count / n * 100, 1),
    }

    # ── Board column evidence ──────────────────────────────────────
    # Count stories that passed through DoD-related board columns
    _col_dod_map = {
        "pr": "PR/merge request linked",
        "pull request": "PR/merge request linked",
        "code review": "Code review",
        "review": "Code review",
        "in review": "Code review",
        "testing": "Testing & QA",
        "qa": "Testing & QA",
        "test": "Testing & QA",
        "documentation": "Documentation updated",
        "doc": "Documentation updated",
        "staging": "Deployment verified",
        "deploy": "Deployment verified",
    }
    _col_evidence: dict[str, int] = defaultdict(int)
    _stories_with_cols = 0
    for s in delivery_stories:
        transitions = s.get("column_transitions", [])
        if transitions:
            _stories_with_cols += 1
            visited = {t.get("to", "").lower().strip() for t in transitions}
            for col in visited:
                for keyword, practice in _col_dod_map.items():
                    if keyword in col:
                        _col_evidence[practice] += 1
                        break
    _col_n = _stories_with_cols or 1
    column_pcts: dict[str, float] = {p: round(c / _col_n * 100, 1) for p, c in _col_evidence.items()}

    # ── Subtask analysis data ──────────────────────────────────────
    type_dist = task_decomp.get("type_distribution", {}) if task_decomp else {}
    overall_completion = task_decomp.get("task_completion_rate", 100) if task_decomp else 100
    type_completion: dict[str, float] = {}
    bottlenecks = task_decomp.get("bottlenecks", []) if task_decomp else []
    for cat, rate, _count in bottlenecks:
        type_completion[cat] = rate

    # ── All practices to evaluate ──────────────────────────────────
    # Each practice has: name, mention_pct (from regex), subtask_cat (if applicable)
    practices = [
        ("Code review", getattr(dod_signal, "stories_with_review_mention_pct", 0), "Review"),
        ("PR/merge request linked", getattr(dod_signal, "stories_with_pr_link_pct", 0), None),
        ("Testing & QA", getattr(dod_signal, "stories_with_testing_mention_pct", 0), "Testing"),
        ("Deployment verified", getattr(dod_signal, "stories_with_deploy_mention_pct", 0), "Deploy"),
        ("Documentation updated", extra_signals["Documentation"], "Documentation"),
        ("AC verification", extra_signals["AC verification"], None),
        ("Monitoring & observability", extra_signals["Monitoring & observability"], None),
    ]

    # Only include security/perf if there's any evidence (avoid noise for non-applicable teams)
    if extra_signals["Security review"] > 0:
        practices.append(("Security review", extra_signals["Security review"], None))
    if extra_signals["Performance testing"] > 0:
        practices.append(("Performance testing", extra_signals["Performance testing"], None))

    for name, mention_pct, sub_cat in practices:
        task_pct = type_dist.get(sub_cat, 0) if sub_cat else 0
        task_comp = type_completion.get(sub_cat, overall_completion) if sub_cat else 0
        col_pct = column_pcts.get(name, 0)
        # Combined evidence from 3 sources: text mentions, subtasks, board columns
        evidence = max(mention_pct, task_pct, col_pct)

        if evidence >= 40 or (mention_pct >= 20 and task_pct >= 10) or col_pct >= 40:
            status = "established"
        elif evidence >= 5:
            status = "emerging"
        else:
            status = "missing"

        signals: list[str] = []
        if mention_pct > 0:
            signals.append(f"{mention_pct:.0f}% mentioned in stories")
        if task_pct > 0:
            comp_note = f", {task_comp:.0f}% completed" if task_comp < 70 else ""
            signals.append(f"{task_pct}% have subtasks{comp_note}")
        if col_pct > 0:
            signals.append(f"{col_pct:.0f}% pass through board column")

        if status == "established":
            rec = "Consistently done. Include as required DoD step."
        elif status == "emerging":
            if mention_pct > 0 and task_pct > 0:
                rec = "Done sometimes. Formalise as a required step."
            elif mention_pct > 0:
                rec = "Mentioned but no subtask. Add explicit task."
            else:
                rec = "Subtasks exist but inconsistent. Standardise."
        else:
            rec = "No evidence found. Evaluate if the team should adopt this."

        items.append(
            {
                "practice": name,
                "coverage_pct": round(mention_pct, 1),
                "task_pct": task_pct,
                "task_completion_pct": round(task_comp, 1),
                "status": status,
                "signals": " · ".join(signals) if signals else "no evidence",
                "recommendation": rec,
            }
        )

    # Add subtask-only practices not already covered
    covered_cats = {sub_cat for _, _, sub_cat in practices if sub_cat}
    extra_subtask_map = {"Design": "Design review"}
    for sub_cat, practice_name in extra_subtask_map.items():
        if sub_cat in covered_cats:
            continue
        task_pct = type_dist.get(sub_cat, 0)
        if task_pct >= 5:
            task_comp = type_completion.get(sub_cat, overall_completion)
            status = "established" if task_pct >= 20 and task_comp >= 70 else "emerging"
            items.append(
                {
                    "practice": practice_name,
                    "coverage_pct": 0,
                    "task_pct": task_pct,
                    "task_completion_pct": round(task_comp, 1),
                    "status": status,
                    "signals": f"{task_pct}% of stories have subtasks",
                    "recommendation": f"Team creates {practice_name.lower()} tasks in {task_pct}% of stories.",
                }
            )

    # Sort: established → emerging → missing, then by evidence strength
    status_order = {"established": 0, "emerging": 1, "missing": 2}
    items.sort(key=lambda x: (status_order.get(x["status"], 3), -x["coverage_pct"]))

    established = sum(1 for i in items if i["status"] == "established")
    emerging = sum(1 for i in items if i["status"] == "emerging")
    total = len(items)

    if established >= 3:
        health = "strong"
        summary = (
            f"{established} of {total} practices are well-established. "
            "The team has a clear, consistent definition of done."
        )
    elif established + emerging >= 3:
        health = "moderate"
        parts = []
        if established:
            parts.append(f"{established} established")
        if emerging:
            parts.append(f"{emerging} emerging")
        summary = (
            f"{', '.join(parts)} out of {total} practices. "
            "The team has some quality gates but they're inconsistently applied."
        )
    else:
        health = "weak"
        summary = (
            f"Only {established + emerging} of {total} practices show any evidence. "
            "The team would benefit from defining explicit done criteria."
        )

    # ── DoD ordering: detect typical sequence from timestamped comments ──
    # For each story with timestamped comments, record when each DoD signal
    # FIRST appears. Then aggregate the typical order across all stories.
    signal_regexes = {
        "Code review": _REVIEW_RE,
        "PR linked": _PR_RE,
        "Testing": _TEST_RE,
        "Deployment": _DEPLOY_RE,
    }
    order_counts: dict[tuple[str, str], int] = defaultdict(int)  # (A, B) → A before B count
    for s in delivery_stories:
        timed = s.get("comments_timed", [])
        if not timed:
            continue
        # Find first occurrence timestamp for each signal
        first_seen: dict[str, str] = {}
        for c_date, body in timed:
            if not c_date:
                continue
            for sig_name, sig_re in signal_regexes.items():
                if sig_name not in first_seen and sig_re.search(body):
                    first_seen[sig_name] = c_date
        # Record pairwise ordering
        seen_list = sorted(first_seen.items(), key=lambda x: x[1])
        for i, (a, _) in enumerate(seen_list):
            for b, _ in seen_list[i + 1 :]:
                order_counts[(a, b)] += 1

    # Build ordering: find the most common sequence
    ordering: list[str] = []
    if order_counts:
        # Score each signal by how often it appears first in pairs
        first_score: dict[str, int] = defaultdict(int)
        for (a, _b), cnt in order_counts.items():
            first_score[a] += cnt
        ordering = [
            name
            for name, _ in sorted(first_score.items(), key=lambda x: -x[1])
            if any(i["status"] != "missing" for i in items if i["practice"].startswith(name[:6]))
        ]

    # ── Custom DoD fields: detect team-specific subtask patterns ──────
    # Look for recurring subtask titles that don't match standard categories
    # but appear in 20%+ of stories — these are team-specific DoD steps.
    custom_steps: list[dict] = []
    if task_decomp and task_decomp.get("common_tasks"):
        total_with_tasks = task_decomp.get("stories_with_tasks", 1) or 1
        standard_keywords = {
            "test",
            "qa",
            "verify",
            "review",
            "pr",
            "code review",
            "deploy",
            "release",
            "staging",
            "design",
            "ux",
            "doc",
            "documentation",
            "readme",
            "development",
            "implement",
            "build",
            "create",
            "update",
        }
        for title, count in task_decomp["common_tasks"]:
            title_lower = title.lower()
            # Skip if it matches a standard category
            if any(kw in title_lower for kw in standard_keywords):
                continue
            pct = round(count / total_with_tasks * 100)
            if pct >= 15 or count >= 3:
                custom_steps.append(
                    {
                        "title": title,
                        "count": count,
                        "pct": pct,
                    }
                )

    return {
        "items": items,
        "summary": summary,
        "health": health,
        "ordering": ordering,
        "custom_steps": custom_steps,
    }


def _analyse_naming_conventions(delivery_stories: list[dict]) -> dict:
    """Analyse ticket naming conventions, labels, epic patterns, and description templates.

    Returns a dict for the examples store and report rendering.
    """
    if not delivery_stories:
        return {}

    n = len(delivery_stories)

    # ── Title prefixes ─────────────────────────────────────────────
    _prefix_re = re.compile(r"^\s*(\[[^\]]{1,15}\]|[A-Z]{2,10}:|[a-z]+:)\s*")
    prefix_counter: dict[str, int] = defaultdict(int)
    for s in delivery_stories:
        title = s.get("summary", "")
        m = _prefix_re.match(title)
        if m:
            prefix_counter[m.group(1).strip()] += 1
        # Also check LLM-extracted prefix
        llm_prefix = s.get("title_prefix", "")
        if llm_prefix and not m:
            prefix_counter[llm_prefix.strip()] += 1

    # Only include prefixes appearing in >= 5% of stories
    prefix_dist = [
        (p, round(c / n * 100)) for p, c in sorted(prefix_counter.items(), key=lambda x: -x[1]) if c / n >= 0.05
    ]
    has_prefixes = len(prefix_dist) > 0

    # ── Label/tag distribution ─────────────────────────────────────
    label_counter: dict[str, int] = defaultdict(int)
    stories_with_labels = 0
    total_labels = 0
    for s in delivery_stories:
        lbls = s.get("labels", [])
        if isinstance(lbls, list) and lbls:
            stories_with_labels += 1
            total_labels += len(lbls)
            for lbl in lbls:
                if isinstance(lbl, str) and lbl:
                    label_counter[lbl.lower()] += 1

    label_dist = [(lbl, round(cnt / n * 100)) for lbl, cnt in sorted(label_counter.items(), key=lambda x: -x[1])[:10]]
    labels_per_story = round(total_labels / n, 1) if n else 0.0
    stories_with_labels_pct = round(stories_with_labels / n * 100) if n else 0

    # ── Epic naming patterns ───────────────────────────────────────
    _quarter_re = re.compile(r"\b(Q[1-4]|202[4-9]|H[12]|quarter)\b", re.IGNORECASE)
    epic_titles: dict[str, str] = {}  # epic_key → title
    for s in delivery_stories:
        ek = s.get("epic_key", "")
        et = s.get("epic_title", "")
        if ek and et and ek not in epic_titles:
            epic_titles[ek] = et

    epic_naming_style = "unknown"
    epic_examples: list[str] = []
    if epic_titles:
        quarter_count = sum(1 for t in epic_titles.values() if _quarter_re.search(t))
        quarter_pct = quarter_count / len(epic_titles) * 100
        if quarter_pct >= 50:
            epic_naming_style = "quarter-scoped"
        elif quarter_pct <= 10:
            epic_naming_style = "feature-scoped"
        else:
            epic_naming_style = "mixed"
        epic_examples = list(epic_titles.values())[:5]

    # ── Description template detection ─────────────────────────────
    # Look for recurring section headings (from LLM + regex)
    section_counter: dict[str, int] = defaultdict(int)
    for s in delivery_stories:
        # LLM-extracted sections
        secs = s.get("description_sections", [])
        if isinstance(secs, list):
            for sec in secs:
                if isinstance(sec, str) and sec:
                    section_counter[sec] += 1
        # Regex fallback on description
        desc = _strip_html(s.get("description", "") or "")
        for m in re.finditer(r"(?m)^\s*\*?\*?([A-Z][^:\n]{3,35})\*?\*?\s*[:?]\s*$", desc):
            heading = m.group(1).strip()
            if len(heading) > 3:
                section_counter[heading] += 1

    # Only include sections appearing in >= 20% of stories
    template_sections = [
        (sec, round(cnt / n * 100))
        for sec, cnt in sorted(section_counter.items(), key=lambda x: -x[1])
        if cnt / n >= 0.20
    ][:8]
    has_template = len(template_sections) >= 2

    # ── Recommendations ────────────────────────────────────────────
    recommendations: list[str] = []
    if not has_prefixes:
        recommendations.append("No title prefixes detected. Consider prefixes like [FE], [BE], SPIKE: for triage.")
    if stories_with_labels_pct < 30:
        recommendations.append(
            f"Only {stories_with_labels_pct}% of stories have labels. Consistent labelling improves filtering."
        )
    if epic_naming_style == "mixed":
        recommendations.append("Epic naming is inconsistent — mix of feature-scoped and quarter-scoped.")
    if not has_template:
        recommendations.append("No consistent description template. A shared template improves story quality.")

    return {
        "title_prefixes": prefix_dist,
        "has_prefixes": has_prefixes,
        "label_distribution": label_dist,
        "labels_per_story": labels_per_story,
        "stories_with_labels_pct": stories_with_labels_pct,
        "epic_naming_style": epic_naming_style,
        "epic_examples": epic_examples,
        "template_sections": template_sections,
        "has_template": has_template,
        "recommendations": recommendations,
    }


def _analyse_story_structure(delivery_stories: list[dict], all_stories: list[dict]) -> dict:
    """Analyse story/epic/subtask structure patterns.

    Detects:
    - Subtask ordering: consistent sequence (design → code → test → deploy)?
    - Skipped subtask types: which task types are missing from stories?
    - Epic completion patterns: are epics fully completed or do stories linger?
    - Story splitting: large stories that get broken down across sprints
    - Epic dependency patterns: stories within an epic that share sprints
    """
    if not delivery_stories:
        return {}

    # ── 1. Subtask ordering ────────────────────────────────────────
    # Detect if subtasks follow a consistent sequence
    _type_keywords = {
        "Design": ("design", "ux", "ui", "mockup", "wireframe"),
        "Development": ("implement", "create", "build", "develop", "code"),
        "Testing": ("test", "qa", "verify", "validation", "e2e"),
        "Review": ("review", "pr", "code review", "approval"),
        "Documentation": ("doc", "documentation", "readme", "runbook"),
        "Deploy": ("deploy", "release", "staging", "production"),
    }

    def _classify_task(title: str) -> str:
        t = title.lower()
        for cat, keywords in _type_keywords.items():
            if any(k in t for k in keywords):
                return cat
        return "Development"

    # Track ordering sequences across stories
    _order_counts: dict[tuple[str, str], int] = defaultdict(int)
    _stories_with_ordered_tasks = 0
    for s in delivery_stories:
        details = s.get("subtask_details", [])
        if len(details) < 2:
            continue
        cats = [_classify_task(d.get("title", "")) for d in details]
        # Remove consecutive duplicates
        deduped = [cats[0]]
        for c in cats[1:]:
            if c != deduped[-1]:
                deduped.append(c)
        if len(deduped) >= 2:
            _stories_with_ordered_tasks += 1
            for i in range(len(deduped) - 1):
                _order_counts[(deduped[i], deduped[i + 1])] += 1

    # Build most common sequence
    subtask_ordering: list[str] = []
    if _order_counts:
        # Find most common transitions to build the typical sequence
        _first_scores: dict[str, int] = defaultdict(int)
        for (a, _b), cnt in _order_counts.items():
            _first_scores[a] += cnt
        # Start with the category that appears first most often
        if _first_scores:
            current = max(_first_scores, key=_first_scores.get)  # type: ignore[arg-type]
            subtask_ordering.append(current)
            for _ in range(5):
                nexts = {b: cnt for (a, b), cnt in _order_counts.items() if a == current and b not in subtask_ordering}
                if not nexts:
                    break
                current = max(nexts, key=nexts.get)  # type: ignore[arg-type]
                subtask_ordering.append(current)

    # ── 2. Skipped subtask types ───────────────────────────────────
    # Which task types are commonly created vs missing?
    _type_presence: dict[str, int] = defaultdict(int)
    _stories_with_tasks = 0
    for s in delivery_stories:
        details = s.get("subtask_details", [])
        if not details:
            continue
        _stories_with_tasks += 1
        seen_types: set[str] = set()
        for d in details:
            cat = _classify_task(d.get("title", ""))
            seen_types.add(cat)
        for cat in seen_types:
            _type_presence[cat] += 1

    skipped_types: list[dict] = []
    if _stories_with_tasks >= 3:
        for cat in ("Testing", "Review", "Documentation", "Deploy"):
            pct = round(_type_presence.get(cat, 0) / _stories_with_tasks * 100)
            if pct < 30:
                skipped_types.append({"type": cat, "present_pct": pct})

    # ── 3. Epic completion patterns ────────────────────────────────
    # Group ALL stories (not just delivery) by epic to see completion rates
    _epic_stories: dict[str, list[dict]] = defaultdict(list)
    for s in all_stories:
        ek = s.get("epic_key", "")
        if ek:
            _epic_stories[ek].append(s)

    epic_completion: list[dict] = []
    for ek, stories in _epic_stories.items():
        if len(stories) < 2:
            continue
        completed = sum(1 for s in stories if not s.get("carried_over") and not s.get("is_recurring"))
        total = sum(1 for s in stories if not s.get("is_recurring"))
        if total == 0:
            continue
        rate = round(completed / total * 100)
        title = next((s.get("epic_title", "") for s in stories if s.get("epic_title")), "")
        epic_completion.append(
            {
                "epic_key": ek,
                "epic_title": title[:40] if title else ek,
                "completed": completed,
                "total": total,
                "rate": rate,
            }
        )
    epic_completion.sort(key=lambda x: x["rate"])

    lingering_epics = [e for e in epic_completion if e["rate"] < 80]
    avg_epic_completion = (
        round(sum(e["rate"] for e in epic_completion) / len(epic_completion)) if epic_completion else 0
    )

    # ── 4. Story splitting patterns ────────────────────────────────
    # Detect large stories (5+ pts) that appear alongside smaller siblings in same epic
    _splitting_signals: list[dict] = []
    for ek, stories in _epic_stories.items():
        delivery = [s for s in stories if not s.get("is_recurring")]
        if len(delivery) < 2:
            continue
        pts_list = [_safe_float(s.get("points", 0)) for s in delivery]
        max_pts = max(pts_list) if pts_list else 0
        min_pts = min(p for p in pts_list if p > 0) if any(p > 0 for p in pts_list) else 0
        if max_pts >= 5 and min_pts <= 2 and max_pts > min_pts * 2:
            title = next((s.get("epic_title", "") for s in delivery if s.get("epic_title")), ek)
            _splitting_signals.append(
                {
                    "epic": title[:40],
                    "story_count": len(delivery),
                    "point_range": f"{min_pts:g}-{max_pts:g}",
                }
            )
    _splitting_signals.sort(key=lambda x: -x["story_count"])

    # ── 5. Epic dependency patterns ────────────────────────────────
    # Detect stories within the same epic that span multiple sprints
    _epic_sprint_spread: list[dict] = []
    for ek, stories in _epic_stories.items():
        delivery = [s for s in stories if not s.get("is_recurring")]
        if len(delivery) < 2:
            continue
        sprints = {s.get("sprint_name", "") for s in delivery if s.get("sprint_name")}
        if len(sprints) >= 2:
            title = next((s.get("epic_title", "") for s in delivery if s.get("epic_title")), ek)
            _epic_sprint_spread.append(
                {
                    "epic": title[:40],
                    "sprints": len(sprints),
                    "stories": len(delivery),
                }
            )
    _epic_sprint_spread.sort(key=lambda x: -x["sprints"])

    return {
        "subtask_ordering": subtask_ordering,
        "stories_with_ordered_tasks": _stories_with_ordered_tasks,
        "skipped_types": skipped_types,
        "epic_completion": epic_completion[:8],
        "avg_epic_completion": avg_epic_completion,
        "lingering_epics": lingering_epics[:5],
        "splitting_signals": _splitting_signals[:5],
        "epic_sprint_spread": _epic_sprint_spread[:5],
    }


# ---------------------------------------------------------------------------
# Sample artifact generation for analysis preview
# ---------------------------------------------------------------------------


def _build_revision_block(feedback: str | None, previous) -> str:
    """Build the REVISION REQUEST prompt section for feedback-driven regeneration.

    When the user presses Regenerate on a sample artifact and types feedback,
    the previous artifact + their feedback are appended to the generation prompt
    so the LLM revises rather than starting from scratch. Returns "" when there
    is no feedback, keeping the prompt byte-identical to a plain regenerate.
    """
    if not feedback:
        return ""
    try:
        previous_json = json.dumps(previous, indent=2, default=str)
    except Exception:
        previous_json = str(previous)
    logger.info("Sample generation: applying revision feedback (%d chars)", len(feedback))
    return f"""

## REVISION REQUEST
The user reviewed a previous version and wants changes.

Previous version:
{previous_json}

User feedback: {feedback}

Apply the feedback. Keep everything the user did not ask to change.
Return the same JSON schema as above."""


def generate_sample_epic(
    calibration_text: str,
    examples: dict | None = None,
    *,
    feedback: str | None = None,
    previous: dict | None = None,
) -> dict:
    """Generate a sample epic matching the team's style using LLM.

    Uses the full team calibration text to guide the LLM in producing
    an epic that matches the team's naming conventions, sizing patterns,
    and description template.

    Returns a dict with: title, description, priority, stories_estimate,
    points_estimate, rationale.
    """
    _ex = examples or {}
    naming = _ex.get("naming_conventions", {})
    epic_style = naming.get("epic_naming_style", "feature-scoped")
    epic_examples = naming.get("epic_examples", [])
    template_sections = naming.get("template_sections", [])

    # Build context from analysis
    epic_ctx_parts: list[str] = []
    if epic_examples:
        epic_ctx_parts.append("Real epic titles from this team: " + ", ".join(f'"{e}"' for e in epic_examples[:5]))
    epic_ctx_parts.append(f"Epic naming style: {epic_style}")
    if template_sections:
        secs = ", ".join(f'"{s}"' for s, _ in template_sections[:5])
        epic_ctx_parts.append(f"Description template sections: {secs}")

    epic_context = "\n".join(epic_ctx_parts)

    prompt = f"""\
You are generating a SAMPLE epic to preview how the planning phase will create epics
for this team. The epic should match the team's actual style and conventions.

{calibration_text}

Team's epic context:
{epic_context}

Generate ONE sample epic as JSON matching this schema:
{{
  "title": "Epic title matching the team's naming convention",
  "description": "Epic description using the team's template structure if detected",
  "priority": "high",
  "stories_estimate": 5,
  "points_estimate": 18,
  "rationale": "Why this epic structure matches the team's patterns"
}}

Rules:
- Title MUST match the team's naming style (feature-scoped, quarter-scoped, etc.)
- Description MUST use the team's template sections if detected
- stories_estimate and points_estimate should match historical averages
- Make the epic realistic for an infrastructure/platform team
- Return ONLY the JSON object, no other text."""
    prompt += _build_revision_block(feedback, previous)

    try:
        response = _llm_invoke(prompt, temperature=0.3)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        return json.loads(text)
    except Exception as exc:
        logger.warning("Sample epic generation failed: %s", exc)
        # Deterministic fallback
        title = epic_examples[0] if epic_examples else "Platform Improvement Initiative"
        return {
            "title": title,
            "description": "Sample epic matching the team's conventions.",
            "priority": "high",
            "stories_estimate": 5,
            "points_estimate": 18,
            "rationale": "Fallback — LLM unavailable. Based on historical averages.",
        }


def generate_sample_stories(
    calibration_text: str,
    sample_epic: dict,
    examples: dict | None = None,
    *,
    feedback: str | None = None,
    previous: list[dict] | None = None,
) -> list[dict]:
    """Generate 2-3 sample user stories matching the team's style.

    Uses calibration text and the accepted sample epic as context to produce
    stories with realistic ACs, points, and discipline assignments.
    """
    _ex = examples or {}
    wp = _ex.get("ac_patterns", {})

    # Build context
    epic_title = sample_epic.get("title", "Sample Epic")
    epic_desc = sample_epic.get("description", "")
    stories_est = sample_epic.get("stories_estimate", 3)
    n_stories = min(3, max(2, stories_est))

    ac_info = ""
    median_ac = wp.get("median_ac", 0) if isinstance(wp, dict) else 0
    if median_ac:
        ac_info = f"Team averages {median_ac} acceptance criteria per story."

    # Build DoD context from proposed_dod
    dod_info = ""
    proposed_dod = _ex.get("proposed_dod", {})
    if isinstance(proposed_dod, dict):
        dod_items = proposed_dod.get("items", [])
        established = [it["practice"] for it in dod_items if isinstance(it, dict) and it.get("status") == "established"]
        emerging = [it["practice"] for it in dod_items if isinstance(it, dict) and it.get("status") == "emerging"]
        if established or emerging:
            dod_parts = []
            if established:
                dod_parts.append("Established DoD practices: " + ", ".join(established))
            if emerging:
                dod_parts.append("Emerging DoD practices: " + ", ".join(emerging))
            dod_info = "\n".join(dod_parts)

    prompt = f"""\
Generate {n_stories} sample user stories for this epic, matching the team's conventions.

{calibration_text}

Epic: {epic_title}
Description: {epic_desc}
{ac_info}
{dod_info}

Return a JSON array where each story has:
{{
  "id": "S1",
  "title": "Short story title",
  "persona": "developer",
  "goal": "what the user wants to do",
  "benefit": "why it matters",
  "story_points": 3,
  "priority": "high",
  "discipline": "infrastructure",
  "acceptance_criteria": [
    {{"given": "context", "when": "action", "then": "outcome"}}
  ],
  "definition_of_done": [
    "Code reviewed and approved",
    "Unit tests passing",
    "Deployed to staging"
  ],
  "rationale": "Why this story structure matches the team's patterns"
}}

Rules:
- Story points MUST be one of: 1, 2, 3, 5, 8
- Discipline should match the team's common disciplines
- ACs should use Given/When/Then if the team uses that format
- Titles should match the team's naming conventions
- definition_of_done MUST reflect the team's ACTUAL observed practices (established and emerging)
- Return ONLY the JSON array"""
    prompt += _build_revision_block(feedback, previous)

    try:
        response = _llm_invoke(prompt, temperature=0.3)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except Exception as exc:
        logger.warning("Sample stories generation failed: %s", exc)

    # Fallback
    return [
        {
            "id": "S1",
            "title": "Implement core functionality",
            "persona": "developer",
            "goal": "build the main feature",
            "benefit": "delivers value",
            "story_points": 3,
            "priority": "high",
            "discipline": "infrastructure",
            "acceptance_criteria": [{"given": "system is running", "when": "action taken", "then": "expected result"}],
            "definition_of_done": ["Code reviewed", "Tests passing", "Deployed to staging"],
            "rationale": "Fallback — LLM unavailable.",
        }
    ]


def generate_sample_tasks(
    calibration_text: str,
    sample_stories: list[dict],
    examples: dict | None = None,
    *,
    feedback: str | None = None,
    previous: list[dict] | None = None,
) -> list[dict]:
    """Generate sample tasks for the sample stories matching the team's patterns."""
    _ex = examples or {}
    td = _ex.get("task_decomposition", {})

    task_ctx_parts: list[str] = []
    avg_tasks = td.get("avg_tasks_per_story", 0) if isinstance(td, dict) else 0
    if avg_tasks:
        task_ctx_parts.append(f"Team averages {avg_tasks:.1f} tasks per story.")
    type_dist = td.get("type_distribution", {}) if isinstance(td, dict) else {}
    if type_dist:
        dist_str = ", ".join(f"{t} {p}%" for t, p in type_dist.items())
        task_ctx_parts.append(f"Task type distribution: {dist_str}")
    common = td.get("common_tasks", []) if isinstance(td, dict) else []
    if common:
        names = ", ".join(f'"{t}"' for t, _ in common[:5])
        task_ctx_parts.append(f"Common task patterns: {names}")
    task_context = "\n".join(task_ctx_parts)

    stories_block = ""
    for s in sample_stories:
        sid = s.get("id", "S1")
        title = s.get("title", "")
        pts = s.get("story_points", 3)
        stories_block += f"\n{sid}: {title} ({pts} pts)\n"
        for ac in s.get("acceptance_criteria", [])[:2]:
            if isinstance(ac, dict):
                stories_block += (
                    f"  Given {ac.get('given', '')}, When {ac.get('when', '')}, Then {ac.get('then', '')}\n"
                )

    prompt = (
        "Generate tasks for each story below, matching the team's task patterns.\n\n"
        f"{calibration_text}\n\n"
        f"Task patterns:\n{task_context}\n\n"
        f"Stories:\n{stories_block}\n\n"
        "Return a JSON array of tasks:\n"
        '[{{"id": "T-S1-01", "story_id": "S1", "title": "Implement X", '
        '"description": "Details", "label": "Code", '
        '"test_plan": "Unit test: verify X"}}]\n\n'
        "Rules:\n"
        "- Generate 2-5 tasks per story (match team's average)\n"
        "- Labels: Code, Testing, Documentation, Infrastructure\n"
        "- IDs: T-S{story_id}-01, T-S{story_id}-02, etc.\n"
        "- Titles must be imperative (verb-first)\n"
        "- Return ONLY the JSON array"
    )
    prompt += _build_revision_block(feedback, previous)

    try:
        response = _llm_invoke(prompt, temperature=0.3)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except Exception as exc:
        logger.warning("Sample tasks generation failed: %s", exc)

    # Fallback
    tasks = []
    for s in sample_stories:
        sid = s.get("id", "S1")
        tasks.append(
            {
                "id": f"T-{sid}-01",
                "story_id": sid,
                "title": "Implement core functionality",
                "description": "Build the main feature.",
                "label": "Code",
                "test_plan": "Unit test the implementation.",
            }
        )
    return tasks


def generate_sample_sprint(
    calibration_text: str,
    sample_stories: list[dict],
    sample_tasks: list[dict],
    examples: dict | None = None,
    *,
    feedback: str | None = None,
    previous: dict | None = None,
) -> dict:
    """Generate a sample sprint plan matching the team's velocity and capacity."""
    _ex = examples or {}
    scope = _ex.get("scope_changes", {})
    totals = scope.get("totals", {}) if isinstance(scope, dict) else {}
    delivered_vel = totals.get("avg_delivered_velocity", 0)
    committed_vel = totals.get("avg_committed_velocity", 0)

    total_pts = sum(s.get("story_points", 0) for s in sample_stories)
    story_ids = [s.get("id", "") for s in sample_stories]

    prompt = (
        "Generate a sample sprint plan for these stories and tasks.\n\n"
        f"{calibration_text}\n\n"
        f"Team velocity: {delivered_vel or 'unknown'} pts/sprint delivered\n"
        f"Committed velocity: {committed_vel or 'unknown'} pts/sprint\n\n"
        f"Stories ({total_pts} total pts): {', '.join(story_ids)}\n"
        f"Tasks: {len(sample_tasks)} total\n\n"
        "Return a JSON object:\n"
        "{\n"
        '  "sprint_name": "Sprint 1",\n'
        '  "velocity_target": 20,\n'
        '  "stories_included": ["S1", "S2"],\n'
        '  "total_points": 8,\n'
        '  "capacity_notes": "Based on team avg of 20 pts/sprint",\n'
        '  "risks": ["Story S2 has external dependency"],\n'
        '  "rationale": "Why this sprint allocation matches team patterns"\n'
        "}\n\n"
        "Rules:\n"
        "- Use delivered velocity (not committed) for capacity\n"
        "- Include realistic capacity notes and risks\n"
        "- Return ONLY the JSON object"
    )
    prompt += _build_revision_block(feedback, previous)

    try:
        response = _llm_invoke(prompt, temperature=0.3)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except Exception as exc:
        logger.warning("Sample sprint generation failed: %s", exc)

    return {
        "sprint_name": "Sprint 1",
        "velocity_target": delivered_vel or 20,
        "stories_included": story_ids,
        "total_points": total_pts,
        "capacity_notes": "Based on team delivered velocity.",
        "risks": [],
        "rationale": "Fallback — LLM unavailable.",
    }


def _analyse_workflow_columns(delivery_stories: list[dict]) -> dict:
    """Analyse board column workflow patterns from status/column transitions.

    Detects whether the team uses board columns as DoD steps (e.g. Documentation,
    PR, Testing columns) and computes pass-through rates per column.
    """
    if not delivery_stories:
        return {}

    # Collect all column names and per-story column visits
    _all_columns: dict[str, int] = defaultdict(int)  # column → stories that visited
    _stories_with_transitions = 0
    _column_sequences: list[list[str]] = []  # per-story ordered list of columns visited

    for s in delivery_stories:
        transitions = s.get("column_transitions", [])
        if not transitions:
            continue
        _stories_with_transitions += 1
        visited: list[str] = []
        for t in transitions:
            col = t.get("to", "")
            if col and (not visited or visited[-1] != col):
                visited.append(col)
                _all_columns[col] += 1
        if visited:
            _column_sequences.append(visited)

    if not _stories_with_transitions:
        return {}

    n = _stories_with_transitions

    # Determine which columns are DoD-related
    _dod_keywords = {
        "documentation": "Documentation",
        "doc": "Documentation",
        "pr": "PR/Code review",
        "pull request": "PR/Code review",
        "code review": "PR/Code review",
        "review": "PR/Code review",
        "in review": "PR/Code review",
        "testing": "Testing",
        "qa": "Testing",
        "test": "Testing",
        "staging": "Deployment",
        "deploy": "Deployment",
        "release": "Deployment",
        "uat": "Testing",
        "blocked": "Blocked",
    }

    # Map actual column names to DoD categories
    _col_to_dod: dict[str, str] = {}
    for col_name in _all_columns:
        col_lower = col_name.lower().strip()
        for keyword, category in _dod_keywords.items():
            if keyword in col_lower:
                _col_to_dod[col_name] = category
                break

    # Build ordered workflow from most common first column → last
    workflow: list[str] = []
    if _column_sequences:
        # Use the most common sequence pattern
        _pos_scores: dict[str, list[int]] = defaultdict(list)
        for seq in _column_sequences:
            for i, col in enumerate(seq):
                _pos_scores[col].append(i)
        # Sort by average position
        workflow = sorted(
            _pos_scores,
            key=lambda c: sum(_pos_scores[c]) / len(_pos_scores[c]),
        )

    # Per-column pass-through rates
    column_rates: dict[str, int] = {}
    for col, count in sorted(_all_columns.items(), key=lambda x: -x[1]):
        column_rates[col] = round(count / n * 100)

    # DoD column pass-through rates
    dod_columns: dict[str, int] = {}
    for col, category in _col_to_dod.items():
        if category != "Blocked":  # Blocked isn't a DoD step
            dod_columns[col] = column_rates.get(col, 0)

    # Detect workflow style
    has_dod_columns = len(dod_columns) >= 1
    if has_dod_columns:
        style = "columns-as-dod"
    else:
        style = "minimal"

    # Skip patterns: stories that didn't visit DoD columns
    skip_patterns: list[dict] = []
    for col, category in _col_to_dod.items():
        if category == "Blocked":
            continue
        rate = column_rates.get(col, 0)
        skip_pct = 100 - rate
        if skip_pct >= 20:
            skip_patterns.append({"column": col, "category": category, "skip_pct": skip_pct})
    skip_patterns.sort(key=lambda x: -x["skip_pct"])

    # Full workflow compliance: stories that passed through ALL DoD columns
    full_compliance = 0
    if dod_columns:
        dod_col_names = set(dod_columns.keys())
        for s in delivery_stories:
            transitions = s.get("column_transitions", [])
            visited = {t.get("to", "") for t in transitions}
            if dod_col_names.issubset(visited):
                full_compliance += 1
    full_workflow_pct = round(full_compliance / n * 100) if n else 0

    return {
        "style": style,
        "workflow": workflow,
        "all_columns": column_rates,
        "dod_columns": dod_columns,
        "full_workflow_pct": full_workflow_pct,
        "skip_patterns": skip_patterns[:5],
        "stories_analysed": n,
        "total_stories": len(delivery_stories),
    }


def _analyse_additional_patterns(
    delivery_stories: list[dict],
    sprint_data: list[dict],
) -> dict:
    """Analyse estimation bias, seasonal velocity, and bug rate patterns."""
    if not delivery_stories:
        return {}

    # ── Estimation bias ────────────────────────────────────────────
    # Compare actual cycle time to avg for same point value
    # Group completed stories by point tier
    _by_pts: dict[int, list[float]] = defaultdict(list)
    for s in delivery_stories:
        ct = s.get("cycle_time_days")
        pts = int(_safe_float(s.get("points", 0)))
        if ct and ct > 0 and pts in (1, 2, 3, 5, 8):
            _by_pts[pts].append(ct)

    _pt_avgs: dict[int, float] = {}
    for pts, cts in _by_pts.items():
        if cts:
            _pt_avgs[pts] = sum(cts) / len(cts)

    underestimated = 0
    overestimated = 0
    accurate = 0
    _under_by_pts: dict[int, int] = defaultdict(int)
    for s in delivery_stories:
        ct = s.get("cycle_time_days")
        pts = int(_safe_float(s.get("points", 0)))
        if not ct or ct <= 0 or pts not in _pt_avgs:
            continue
        avg = _pt_avgs[pts]
        if ct > avg * 2:
            underestimated += 1
            _under_by_pts[pts] += 1
        elif ct < avg * 0.5:
            overestimated += 1
        else:
            accurate += 1

    total_estimated = underestimated + overestimated + accurate
    estimation_bias: dict = {}
    if total_estimated >= 5:
        estimation_bias = {
            "underestimated_pct": round(underestimated / total_estimated * 100),
            "overestimated_pct": round(overestimated / total_estimated * 100),
            "accurate_pct": round(accurate / total_estimated * 100),
            "worst_sizes": [
                pts
                for pts in sorted(_under_by_pts, key=_under_by_pts.get, reverse=True)  # type: ignore[arg-type]
                if _under_by_pts[pts] >= 2
            ][:3],
            "sample": total_estimated,
        }

    # ── Seasonal velocity patterns ─────────────────────────────────
    _monthly_velocity: dict[str, list[float]] = defaultdict(list)
    for sd in sprint_data:
        start_str = sd.get("sprint_start", "")
        pts_val = sd.get("completed_points", 0)
        if not start_str or not pts_val:
            continue
        dt = _parse_date(start_str)
        if dt:
            month_key = dt.strftime("%b")  # "Jan", "Feb", etc.
            _monthly_velocity[month_key].append(pts_val)

    seasonal: dict = {}
    if len(_monthly_velocity) >= 3:
        _month_avgs = {m: round(sum(v) / len(v), 1) for m, v in _monthly_velocity.items()}
        overall_avg = sum(v for vals in _monthly_velocity.values() for v in vals) / max(
            1, sum(len(v) for v in _monthly_velocity.values())
        )
        # Find months significantly above/below average (>25% deviation)
        high_months = {m: avg for m, avg in _month_avgs.items() if avg > overall_avg * 1.25}
        low_months = {m: avg for m, avg in _month_avgs.items() if avg < overall_avg * 0.75}
        seasonal = {
            "monthly_avg": _month_avgs,
            "overall_avg": round(overall_avg, 1),
            "high_months": high_months,
            "low_months": low_months,
        }

    # ── Bug rate ───────────────────────────────────────────────────
    total_stories = len(delivery_stories)
    bug_count = sum(1 for s in delivery_stories if s.get("issue_type", "").lower() in ("bug", "defect"))
    bug_pts = sum(
        _safe_float(s.get("points", 0))
        for s in delivery_stories
        if s.get("issue_type", "").lower() in ("bug", "defect")
    )
    bug_rate: dict = {}
    if total_stories >= 5:
        bug_rate = {
            "bug_count": bug_count,
            "bug_pct": round(bug_count / total_stories * 100),
            "bug_pts": round(bug_pts, 1),
            "total_stories": total_stories,
        }

    return {
        "estimation_bias": estimation_bias,
        "seasonal": seasonal,
        "bug_rate": bug_rate,
    }


def _analyse_acceptance_criteria(delivery_stories: list[dict]) -> dict:
    """Analyse acceptance criteria patterns across stories.

    Scans story descriptions to detect:
    - AC content themes (error handling, validation, edge cases, performance, etc.)
    - AC coverage by discipline (do frontend stories have more ACs?)
    - AC specificity (vague vs precise language)
    - Correlation between AC count and spillover (fewer ACs → more spills?)

    Returns a dict for the examples store and TUI/export rendering.
    """
    if not delivery_stories:
        return {}

    # ── AC content themes ──────────────────────────────────────────
    # Scan description text for common AC topic patterns
    theme_regexes = {
        "Error handling": re.compile(
            r"\b(error|exception|fail|invalid|reject|timeout|retry|fallback)\b",
            re.IGNORECASE,
        ),
        "Validation": re.compile(
            r"\b(validat|required\s*field|input\s*check|format\s*check|constraint|boundary)\b",
            re.IGNORECASE,
        ),
        "Edge cases": re.compile(
            r"\b(edge\s*case|empty|null|zero|negative|overflow|concurrent|race\s*condition)\b",
            re.IGNORECASE,
        ),
        "Performance": re.compile(
            r"\b(performance|latency|throughput|response\s*time|load|scalab|cache|optimi[sz])\b",
            re.IGNORECASE,
        ),
        "Security": re.compile(
            r"\b(auth|permission|role|encrypt|token|secure|RBAC|CORS|XSS|injection)\b",
            re.IGNORECASE,
        ),
        "User experience": re.compile(
            r"\b(user\s*(can|should|sees|is\s*shown)|display|responsive|accessible|usab)\b",
            re.IGNORECASE,
        ),
        "Integration": re.compile(
            r"\b(API|endpoint|webhook|third.party|external\s*service|upstream|downstream)\b",
            re.IGNORECASE,
        ),
        "Data": re.compile(
            r"\b(database|migration|schema|storage|persist|CRUD|query|index)\b",
            re.IGNORECASE,
        ),
    }

    theme_counts: dict[str, int] = defaultdict(int)
    theme_examples: dict[str, dict] = {}  # theme → first example story {issue_key, issue_url, summary}
    stories_with_ac = [s for s in delivery_stories if s.get("ac_count", 0) > 0]
    n_with_ac = len(stories_with_ac) or 1

    for s in stories_with_ac:
        # Prefer LLM-parsed AC items for theme detection (more precise)
        parsed_ac = s.get("parsed_ac", [])
        if parsed_ac:
            ac_text = " ".join(parsed_ac)
        else:
            ac_text = re.sub(r"<[^>]+>", " ", s.get("description", "") or "")
        for theme, regex in theme_regexes.items():
            if regex.search(ac_text):
                theme_counts[theme] += 1
                if theme not in theme_examples and s.get("issue_key"):
                    theme_examples[theme] = {
                        "issue_key": s.get("issue_key", ""),
                        "issue_url": s.get("issue_url", ""),
                        "summary": (s.get("summary", "") or "")[:40],
                    }

    theme_pcts = {t: round(c / n_with_ac * 100) for t, c in sorted(theme_counts.items(), key=lambda x: -x[1]) if c > 0}

    # ── AC by discipline ───────────────────────────────────────────
    disc_ac: dict[str, list[int]] = defaultdict(list)
    for s in delivery_stories:
        ac = s.get("ac_count", 0)
        disc = s.get("discipline", "fullstack")
        disc_ac[disc].append(ac)

    ac_by_discipline = {}
    for disc, counts in sorted(disc_ac.items()):
        if len(counts) >= 3:
            avg = round(sum(counts) / len(counts), 1)
            with_ac = sum(1 for c in counts if c > 0)
            pct = round(with_ac / len(counts) * 100)
            ac_by_discipline[disc] = {
                "avg_ac": avg,
                "stories_with_ac_pct": pct,
                "sample": len(counts),
            }

    # ── AC specificity ─────────────────────────────────────────────
    # Vague: "it should work", "everything is correct", "as expected"
    # Precise: contains numbers, status codes, specific field names, measurable criteria
    _vague_re = re.compile(
        r"\b(should\s*work|as\s*expected|correct(ly)?|proper(ly)?|appropriate(ly)?|no\s*issues)\b",
        re.IGNORECASE,
    )
    _precise_re = re.compile(
        r"(\b\d{3}\b|returns?\s*\d|timeout\s*\d|\d+\s*(ms|seconds?|minutes?|MB|GB)|"
        r"field\s*['\"]?\w+['\"]?|endpoint\s*/\w|status\s*code|JSON|HTTP\s*\d|"
        r"must\s*(not\s*)?exceed|at\s*least\s*\d|no\s*more\s*than\s*\d)",
        re.IGNORECASE,
    )

    vague_count = 0
    precise_count = 0
    for s in stories_with_ac:
        # Prefer LLM specificity classification if available
        llm_spec = s.get("ac_specificity", "")
        if llm_spec in ("precise", "moderate", "vague"):
            if llm_spec == "precise":
                precise_count += 1
            elif llm_spec == "vague":
                vague_count += 1
            continue
        # Fallback to regex
        desc = re.sub(r"<[^>]+>", " ", s.get("description", "") or "")
        has_vague = bool(_vague_re.search(desc))
        has_precise = bool(_precise_re.search(desc))
        if has_precise:
            precise_count += 1
        elif has_vague:
            vague_count += 1

    specificity_pct = round(precise_count / n_with_ac * 100) if n_with_ac else 0
    vague_pct = round(vague_count / n_with_ac * 100) if n_with_ac else 0

    if specificity_pct >= 40:
        specificity_label = "precise"
    elif specificity_pct >= 15:
        specificity_label = "moderate"
    else:
        specificity_label = "vague"

    # ── AC count vs spillover correlation ──────────────────────────
    # Do stories with fewer ACs spill more often?
    low_ac_stories = [s for s in delivery_stories if s.get("ac_count", 0) <= 1]
    high_ac_stories = [s for s in delivery_stories if s.get("ac_count", 0) >= 3]
    low_ac_spill = (
        round(sum(1 for s in low_ac_stories if s.get("carried_over")) / len(low_ac_stories) * 100)
        if low_ac_stories
        else 0
    )
    high_ac_spill = (
        round(sum(1 for s in high_ac_stories if s.get("carried_over")) / len(high_ac_stories) * 100)
        if high_ac_stories
        else 0
    )

    # ── Summary ────────────────────────────────────────────────────
    total = len(delivery_stories)
    with_ac_pct = round(len(stories_with_ac) / total * 100) if total else 0
    median_ac = statistics.median([s.get("ac_count", 0) for s in stories_with_ac]) if stories_with_ac else 0

    # Build a single consolidated recommendation (not multiple separate ones)
    issues: list[str] = []
    if with_ac_pct < 50:
        issues.append(f"only {with_ac_pct}% of stories have ACs")
    if specificity_label == "vague" and vague_pct > 0:
        issues.append(f"{vague_pct}% use vague language instead of measurable criteria")
    if low_ac_spill > high_ac_spill + 10 and len(low_ac_stories) >= 5:
        issues.append(f"stories with fewer ACs spill {low_ac_spill}% vs {high_ac_spill}% for detailed ones")
    missing_themes = [t for t in ("Error handling", "Validation", "Edge cases") if t not in theme_pcts]
    if missing_themes:
        issues.append(f"ACs rarely cover {', '.join(missing_themes).lower()}")

    recommendation = ""
    if issues:
        recommendation = ". ".join(issues).capitalize() + "."

    return {
        "stories_with_ac_pct": with_ac_pct,
        "median_ac": median_ac,
        "themes": theme_pcts,
        "theme_examples": theme_examples,
        "by_discipline": ac_by_discipline,
        "specificity": {
            "label": specificity_label,
            "precise_pct": specificity_pct,
            "vague_pct": vague_pct,
        },
        "spillover_correlation": {
            "low_ac_spill_pct": low_ac_spill,
            "high_ac_spill_pct": high_ac_spill,
            "low_ac_count": len(low_ac_stories),
            "high_ac_count": len(high_ac_stories),
        },
        "recommendation": recommendation,
    }


def _analyse_repositories(delivery_stories: list[dict]) -> dict:
    """Extract and analyse repository patterns from story-linked repos.

    Repo slugs come from ticket text, AzDO work item relations, Jira dev-status,
    and optional Git PR scans. Returns counts, spillover correlation, optional
    avg cycle times, and human-readable ``detection_sources`` for the TUI.
    """
    repo_counter: dict[str, int] = defaultdict(int)
    repo_by_pts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    repo_by_discipline: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    repo_spillover: dict[str, int] = defaultdict(int)
    repo_cycle_times: dict[str, list[float]] = defaultdict(list)

    for s in delivery_stories:
        repos = s.get("repos", [])
        if not repos:
            continue
        pts = int(_safe_float(s.get("points", 0)))
        disc = s.get("discipline", "fullstack")
        ct = s.get("cycle_time_days")
        is_spill = s.get("carried_over", False)

        for repo in repos:
            repo_counter[repo] += 1
            if pts in (1, 2, 3, 5, 8):
                repo_by_pts[pts][repo] += 1
            repo_by_discipline[disc][repo] += 1
            if is_spill:
                repo_spillover[repo] += 1
            if ct is not None:
                repo_cycle_times[repo].append(ct)

    if not repo_counter:
        return {}

    total = sum(repo_counter.values())

    # Top repos overall
    top_repos = [
        {"repo": repo, "stories": cnt, "pct": round(cnt / total * 100)}
        for repo, cnt in sorted(repo_counter.items(), key=lambda x: -x[1])[:10]
    ]

    # Per-point-value: which repos appear most for each story size
    by_pts = {}
    for pts, repos in repo_by_pts.items():
        top = sorted(repos.items(), key=lambda x: -x[1])[:3]
        by_pts[pts] = [r for r, _ in top]

    # Spillover-prone repos (appear in carried-over stories)
    spillover_repos = [
        {"repo": repo, "spills": cnt, "spill_rate": round(cnt / repo_counter[repo] * 100)}
        for repo, cnt in sorted(repo_spillover.items(), key=lambda x: -x[1])[:5]
        if repo_counter[repo] >= 3
    ]

    # Avg cycle time per repo
    repo_avg_ct = {}
    for repo, cts in repo_cycle_times.items():
        if cts and repo_counter[repo] >= 3:
            repo_avg_ct[repo] = round(sum(cts) / len(cts), 1)

    logger.info(
        "Repository analysis: %d unique repos found across %d stories",
        len(repo_counter),
        len(delivery_stories),
    )
    for repo, cnt in sorted(repo_counter.items(), key=lambda x: -x[1])[:5]:
        logger.debug("  %s: %d stories", repo, cnt)

    source_tags: set[str] = set()
    for s in delivery_stories:
        if not s.get("repos"):
            continue
        rs_src = s.get("repo_sources")
        if rs_src:
            for t in rs_src:
                source_tags.add(str(t))
        else:
            source_tags.add("legacy_text")

    source_labels = (
        ("jira_text", "ticket text and comments"),
        ("description", "work item description and AC"),
        ("azdo_work_item_links", "linked PRs/commits on work items"),
        ("jira_development", "Jira Development panel"),
        ("azdo_pr_work_items", "Azure DevOps PR ↔ work item lookup"),
        ("legacy_text", "PR links in ticket text"),
    )
    detection_sources = [lbl for key, lbl in source_labels if key in source_tags]

    return {
        "top_repos": top_repos,
        "by_pts": {str(k): v for k, v in by_pts.items()},
        "spillover_repos": spillover_repos,
        "repo_avg_cycle_time": repo_avg_ct,
        "total_repos": len(repo_counter),
        "stories_with_repos": sum(1 for s in delivery_stories if s.get("repos")),
        "detection_sources": detection_sources,
    }


def _collect_examples(
    all_stories: list[dict],
    sprint_data: list[dict],
) -> dict[str, list[dict]]:
    """Pick illustrative example stories for each metric category.

    Returns a dict keyed by section name, each value a list of example dicts
    with keys: issue_key, issue_url, summary, detail.
    Max 3 examples per category to keep the display concise.
    """

    def _ex(s: dict, detail: str = "") -> dict:
        return {
            "issue_key": s.get("issue_key", ""),
            "issue_url": s.get("issue_url", ""),
            "summary": (s.get("summary", "") or "")[:60],
            "detail": detail,
        }

    examples: dict[str, list[dict]] = {}

    # Per point-value: pick diverse representative stories that show
    # what this point value typically means for the team.
    by_pts: dict[int, list[dict]] = defaultdict(list)
    for s in all_stories:
        pts = int(_safe_float(s.get("points", 0)))
        if pts in (1, 2, 3, 5, 8) and s.get("issue_key"):
            by_pts[pts].append(s)

    for pts in (1, 2, 3, 5, 8):
        stories_at = by_pts.get(pts, [])
        if not stories_at:
            continue

        # Pick up to 4 diverse stories: spread across different action types
        seen_categories: set[str] = set()
        picked: list[dict] = []
        for s in stories_at:
            title_lower = (s.get("summary", "") or "").lower()
            cat = "other"
            for keyword, category in _ACTION_VERBS.items():
                if keyword in title_lower:
                    cat = category
                    break
            if cat not in seen_categories or len(picked) < 2:
                seen_categories.add(cat)
                tc = s.get("task_count", 0)
                detail_parts = []
                if tc:
                    detail_parts.append(f"{tc} tasks")
                ct = s.get("cycle_time_days")
                if ct is not None:
                    detail_parts.append(f"{ct:.0f}d")
                picked.append(_ex(s, ", ".join(detail_parts)))
            if len(picked) >= 4:
                break
        examples[f"calibration_{pts}pt"] = picked

    # Spillover examples
    carried = [s for s in all_stories if s.get("carried_over") and s.get("issue_key")]
    if carried:
        examples["spillover"] = [_ex(s, f"from {s.get('sprint_name', '?')}") for s in carried[:3]]

    # DoD signal examples: stories with PR/review/test mentions
    for label, regex in [
        ("dod_pr", _PR_RE),
        ("dod_review", _REVIEW_RE),
        ("dod_testing", _TEST_RE),
        ("dod_deploy", _DEPLOY_RE),
    ]:
        matches = []
        for s in all_stories:
            if not s.get("issue_key"):
                continue
            text = (s.get("description", "") or "") + " " + " ".join(s.get("comments", []))
            if regex.search(text):
                matches.append(s)
            if len(matches) >= 2:
                break
        if matches:
            examples[label] = [_ex(s) for s in matches[:2]]

    return examples


# ---------------------------------------------------------------------------
# Tool: analyze_team_history
# ---------------------------------------------------------------------------


@tool
def analyze_team_history(
    project_key: str = "",
    source: str = "",
    sprint_count: int = 8,
) -> str:
    """Analyse the team's historical sprint data to build a calibration profile.

    Pulls data from the last 6-10 closed sprints in Jira or Azure DevOps.
    Computes: cycle time per point value, story shape patterns by discipline,
    epic sizing norms, velocity variance, and estimation accuracy.

    The result is a JSON object that can be stored as a TeamProfile for
    future plan calibration. Use this before planning to learn how the team
    actually works — what a 5-point story really means, how often estimates
    overshoot, and typical story shapes by discipline.

    project_key: Jira project key (e.g. "PROJ") or AzDO project name.
    source: "jira" or "azdevops". Auto-detected if only one is configured.
    sprint_count: Number of closed sprints to analyse (default 8, range 3-12).
    """
    # See docs: "Tools" — tool types, risk levels
    logger.debug(
        "analyze_team_history called: project_key=%r, source=%r, sprint_count=%d", project_key, source, sprint_count
    )

    sprint_count = max(3, min(12, sprint_count))

    # Auto-detect source if not specified
    if not source:
        source = _detect_source()
        if not source:
            return json.dumps({"error": "Cannot detect tracker. Set source='jira' or source='azdevops'."})

    try:
        if source == "jira":
            sprint_data = _fetch_jira_history(project_key, sprint_count)
        elif source == "azdevops":
            sprint_data = _fetch_azdevops_history(project_key, sprint_count)
        else:
            return json.dumps({"error": f"Unknown source: {source}. Use 'jira' or 'azdevops'."})
    except Exception as e:
        logger.error("Error fetching team history: %s", e)
        return json.dumps({"error": f"Failed to fetch history: {e}"})

    if not sprint_data:
        return json.dumps({"error": "No closed sprints found — cannot build team profile."})

    profile = _build_profile_from_sprint_data(source, project_key or "unknown", sprint_data)

    from dataclasses import asdict

    return json.dumps(asdict(profile), ensure_ascii=False)


def _detect_source() -> str:
    """Auto-detect which tracker is configured."""
    try:
        from yeaboi.config import get_jira_base_url, get_jira_token

        if get_jira_base_url() and get_jira_token():
            return "jira"
    except Exception:
        pass
    try:
        from yeaboi.config import get_azure_devops_org_url, get_azure_devops_token

        if get_azure_devops_org_url() and get_azure_devops_token():
            return "azdevops"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Daily sprint scope timeline builders
# ---------------------------------------------------------------------------


def _date_range(start: datetime, end: datetime) -> list[str]:
    """Return list of ISO date strings for each day from start to end (inclusive)."""
    days: list[str] = []
    cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= end_day:
        days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


def _normalize_iter_path(path: str) -> str:
    """Strip leading backslash from AzDO iteration path for consistent comparison.

    The SDK's ``TeamSettingsIteration.path`` returns paths like
    ``\\Project\\Sprint 1``, but ``System.IterationPath`` in work item fields
    uses ``Project\\Sprint 1`` (no leading backslash).
    """
    return path.lstrip("\\") if path else ""


def _build_azdo_sprint_scope_timeline(
    wit_client: object,
    project: str,
    stories: list[dict],
    iter_path: str,
    iter_start: str,
    iter_end: str,
    completed_pts: float,
) -> SprintScopeTimeline | None:
    """Walk AzDO work item revisions to build a day-by-day scope timeline.

    For each story currently in the sprint, fetches revisions and records:
    - When it entered this iteration (date + points at that time)
    - When its points changed (re-estimation events)
    - The daily scope total for every day of the sprint
    """
    s_dt = _parse_date(iter_start)
    e_dt = _parse_date(iter_end)
    if not s_dt or not e_dt:
        return None

    days = _date_range(s_dt, e_dt)
    if not days:
        return None

    # Normalize iter_path for comparison with System.IterationPath
    iter_path = _normalize_iter_path(iter_path)

    # Per-day scope map: date → {issue_key: points}
    # Start with empty — we'll reconstruct from revisions
    day_scope: dict[str, dict[str, float]] = {d: {} for d in days}
    change_events: list[ScopeChangeEvent] = []

    for story in stories:
        if story.get("is_recurring"):
            continue  # exclude ceremony/KTLO tickets from scope tracking
        wi_id_str = story.get("issue_key", "")
        if not wi_id_str:
            continue
        try:
            wi_id = int(wi_id_str)
        except (ValueError, TypeError):
            continue

        summary = (story.get("summary", "") or "")[:60]
        issue_url = story.get("issue_url", "")

        try:
            revisions = wit_client.get_revisions(wi_id, project=project)  # type: ignore[union-attr]
            if not revisions:
                continue
        except Exception:
            continue

        # Walk revisions to build a timeline of (date, in_iteration, points)
        # Each entry: (date_str, in_this_iter: bool, points: float)
        wi_timeline: list[tuple[str, bool, float]] = []
        for rev in revisions:
            fields = rev.fields if hasattr(rev, "fields") else {}
            rev_dt = _parse_date(fields.get("System.ChangedDate", ""))
            if not rev_dt:
                continue
            cur_iter = fields.get("System.IterationPath", "") or ""
            cur_pts = _safe_float(fields.get("Microsoft.VSTS.Scheduling.StoryPoints"))
            in_iter = bool(iter_path and iter_path in cur_iter)
            rev_day = rev_dt.strftime("%Y-%m-%d")
            wi_timeline.append((rev_day, in_iter, cur_pts))

        if not wi_timeline:
            continue

        # For each sprint day, determine if the story was in scope and its points.
        # Walk the timeline chronologically, carrying forward the last known state.
        last_in_iter = False
        last_pts = 0.0
        ti = 0  # timeline index

        for day in days:
            # Advance through revisions up to and including this day
            while ti < len(wi_timeline) and wi_timeline[ti][0] <= day:
                _, last_in_iter, last_pts = wi_timeline[ti]
                ti += 1

            if last_in_iter and last_pts > 0:
                day_scope[day][wi_id_str] = last_pts

        # Detect change events by comparing consecutive days
        prev_in = False
        prev_pts_val = 0.0
        for day in days:
            cur_in = wi_id_str in day_scope[day]
            cur_pts_val = day_scope[day].get(wi_id_str, 0.0)

            if cur_in and not prev_in and cur_pts_val > 0:
                # Story entered scope
                if day != days[0]:
                    change_events.append(
                        ScopeChangeEvent(
                            date=day,
                            issue_key=wi_id_str,
                            issue_url=issue_url,
                            summary=summary,
                            change_type="added",
                            from_pts=0.0,
                            to_pts=cur_pts_val,
                            delta_pts=cur_pts_val,
                        )
                    )
            elif not cur_in and prev_in:
                # Story left scope
                change_events.append(
                    ScopeChangeEvent(
                        date=day,
                        issue_key=wi_id_str,
                        issue_url=issue_url,
                        summary=summary,
                        change_type="removed",
                        from_pts=prev_pts_val,
                        to_pts=0.0,
                        delta_pts=-prev_pts_val,
                    )
                )
            elif cur_in and prev_in and cur_pts_val != prev_pts_val:
                # Re-estimation
                delta = cur_pts_val - prev_pts_val
                ctype = "re_estimated_up" if delta > 0 else "re_estimated_down"
                change_events.append(
                    ScopeChangeEvent(
                        date=day,
                        issue_key=wi_id_str,
                        issue_url=issue_url,
                        summary=summary,
                        change_type=ctype,
                        from_pts=prev_pts_val,
                        to_pts=cur_pts_val,
                        delta_pts=delta,
                    )
                )

            prev_in = cur_in
            prev_pts_val = cur_pts_val

    # Build daily snapshots
    snapshots: list[DailyScopeSnapshot] = []
    for day in days:
        items = day_scope[day]
        total = sum(items.values())
        snapshots.append(
            DailyScopeSnapshot(
                date=day,
                total_scope_pts=total,
                stories_in_sprint=tuple(sorted(items.items())),
            )
        )

    committed = snapshots[0].total_scope_pts if snapshots else 0.0
    final = snapshots[-1].total_scope_pts if snapshots else 0.0
    abs_churn = sum(abs(e.delta_pts) for e in change_events)
    churn_rate = round(abs_churn / committed, 2) if committed > 0 else 0.0

    # Sort events by date
    change_events.sort(key=lambda e: e.date)

    return SprintScopeTimeline(
        sprint_name=story.get("sprint_name", "") if stories else "",
        committed_pts=committed,
        final_pts=final,
        delivered_pts=completed_pts,
        scope_change_total=round(final - committed, 1),
        scope_churn=churn_rate,
        daily_snapshots=tuple(snapshots),
        change_events=tuple(change_events),
    )


def _build_jira_sprint_scope_timeline(
    jira_client: object,
    stories: list[dict],
    sprint_name: str,
    sprint_start: str,
    sprint_end: str,
    completed_pts: float,
) -> SprintScopeTimeline | None:
    """Walk Jira changelogs to build a day-by-day scope timeline for a sprint.

    For each story, fetches the changelog and reconstructs when it entered/left
    the sprint and when its points changed.
    """
    s_dt = _parse_date(sprint_start)
    e_dt = _parse_date(sprint_end)
    if not s_dt or not e_dt:
        return None

    days = _date_range(s_dt, e_dt)
    if not days:
        return None

    day_scope: dict[str, dict[str, float]] = {d: {} for d in days}
    change_events: list[ScopeChangeEvent] = []

    for story in stories:
        if story.get("is_recurring"):
            continue  # exclude ceremony/KTLO tickets from scope tracking
        issue_key = story.get("issue_key", "")
        if not issue_key:
            continue
        summary = (story.get("summary", "") or "")[:60]
        issue_url = story.get("issue_url", "")

        try:
            issue = jira_client.issue(issue_key, expand="changelog")  # type: ignore[union-attr]
            changelog = getattr(issue, "changelog", None)
        except Exception:
            # Fallback: use story data as-is, assume in scope for full sprint
            pts = _safe_float(story.get("points", 0))
            if pts > 0:
                for day in days:
                    day_scope[day][issue_key] = pts
            continue

        # Build timeline of (date, in_sprint, points) from changelog
        # Start with the story's current points and assume it was in sprint initially
        cur_pts = _safe_float(story.get("points", 0))
        created_raw = getattr(issue.fields, "created", None) or ""
        created_dt = _parse_date(created_raw)

        # Build revision events: list of (datetime, field, from_val, to_val)
        revs: list[tuple[datetime, str, str, str]] = []
        if changelog:
            for history in getattr(changelog, "histories", []):
                change_dt = _parse_date(getattr(history, "created", ""))
                if not change_dt:
                    continue
                for item in getattr(history, "items", []):
                    field_name = getattr(item, "field", "")
                    from_val = getattr(item, "fromString", "") or ""
                    to_val = getattr(item, "toString", "") or ""
                    if field_name in ("Story Points", "story_points", "Sprint"):
                        revs.append((change_dt, field_name, from_val, to_val))

        revs.sort(key=lambda r: r[0])

        # Walk backwards from current state to reconstruct initial state at sprint start
        pts_at_start = cur_pts
        in_sprint_at_start = True
        for rev_dt, field_name, from_val, to_val in reversed(revs):
            if rev_dt <= s_dt:
                break
            if field_name in ("Story Points", "story_points"):
                pts_at_start = _safe_float(from_val)
            if field_name == "Sprint":
                # If sprint was added during the sprint window, it wasn't there at start
                if sprint_name in to_val and sprint_name not in from_val and rev_dt > s_dt:
                    in_sprint_at_start = False

        # If story was created after sprint start, it wasn't in scope at start
        if created_dt and created_dt > s_dt:
            in_sprint_at_start = False

        # Now walk forward through days, applying changes
        tracking_pts = pts_at_start
        tracking_in = in_sprint_at_start
        rev_idx = 0

        for day in days:
            # Apply revisions for this day
            while rev_idx < len(revs):
                rev_dt, field_name, from_val, to_val = revs[rev_idx]
                rev_day = rev_dt.strftime("%Y-%m-%d")
                if rev_day > day:
                    break
                if field_name in ("Story Points", "story_points"):
                    tracking_pts = _safe_float(to_val)
                if field_name == "Sprint":
                    if sprint_name in to_val and sprint_name not in from_val:
                        tracking_in = True
                    elif sprint_name not in to_val and sprint_name in from_val:
                        tracking_in = False
                rev_idx += 1

            if tracking_in and tracking_pts > 0:
                day_scope[day][issue_key] = tracking_pts

        # Detect change events by comparing consecutive days
        prev_in = False
        prev_pts_val = 0.0
        for day in days:
            cur_in = issue_key in day_scope[day]
            cur_pts_val = day_scope[day].get(issue_key, 0.0)

            if cur_in and not prev_in and cur_pts_val > 0:
                if day != days[0]:
                    change_events.append(
                        ScopeChangeEvent(
                            date=day,
                            issue_key=issue_key,
                            issue_url=issue_url,
                            summary=summary,
                            change_type="added",
                            from_pts=0.0,
                            to_pts=cur_pts_val,
                            delta_pts=cur_pts_val,
                        )
                    )
            elif not cur_in and prev_in:
                change_events.append(
                    ScopeChangeEvent(
                        date=day,
                        issue_key=issue_key,
                        issue_url=issue_url,
                        summary=summary,
                        change_type="removed",
                        from_pts=prev_pts_val,
                        to_pts=0.0,
                        delta_pts=-prev_pts_val,
                    )
                )
            elif cur_in and prev_in and cur_pts_val != prev_pts_val:
                delta = cur_pts_val - prev_pts_val
                ctype = "re_estimated_up" if delta > 0 else "re_estimated_down"
                change_events.append(
                    ScopeChangeEvent(
                        date=day,
                        issue_key=issue_key,
                        issue_url=issue_url,
                        summary=summary,
                        change_type=ctype,
                        from_pts=prev_pts_val,
                        to_pts=cur_pts_val,
                        delta_pts=delta,
                    )
                )

            prev_in = cur_in
            prev_pts_val = cur_pts_val

    # Build daily snapshots
    snapshots: list[DailyScopeSnapshot] = []
    for day in days:
        items = day_scope[day]
        total = sum(items.values())
        snapshots.append(
            DailyScopeSnapshot(
                date=day,
                total_scope_pts=total,
                stories_in_sprint=tuple(sorted(items.items())),
            )
        )

    committed = snapshots[0].total_scope_pts if snapshots else 0.0
    final = snapshots[-1].total_scope_pts if snapshots else 0.0
    abs_churn = sum(abs(e.delta_pts) for e in change_events)
    churn_rate = round(abs_churn / committed, 2) if committed > 0 else 0.0

    change_events.sort(key=lambda e: e.date)

    return SprintScopeTimeline(
        sprint_name=sprint_name,
        committed_pts=committed,
        final_pts=final,
        delivered_pts=completed_pts,
        scope_change_total=round(final - committed, 1),
        scope_churn=churn_rate,
        daily_snapshots=tuple(snapshots),
        change_events=tuple(change_events),
    )


# ---------------------------------------------------------------------------
# Mid-sprint scope change detection helpers (legacy — kept for enrichment)
# ---------------------------------------------------------------------------


def _enrich_jira_scope_changes(
    jira_client: object,
    stories: list[dict],
    sprint_name: str,
    sprint_start: str,
    sprint_end: str,
) -> None:
    """Enrich story dicts with mid-sprint scope change data from Jira changelog.

    For each story, fetches the issue changelog and detects:
    - point_changed: True if story points were modified during the sprint
    - original_points: points value at sprint start (before any mid-sprint change)
    - added_mid_sprint: True if the story was added to this sprint after it started
    - point_changes: list of {date, from_pts, to_pts} dicts
    """
    s_dt = _parse_date(sprint_start)
    e_dt = _parse_date(sprint_end)
    if not s_dt:
        return

    for story in stories:
        issue_key = story.get("issue_key", "")
        if not issue_key:
            continue

        try:
            # Fetch issue with changelog expanded
            issue = jira_client.issue(issue_key, expand="changelog")
            changelog = getattr(issue, "changelog", None)
            if not changelog:
                continue

            point_changes: list[dict] = []
            was_added_mid_sprint = False
            original_pts = story.get("points", 0)

            # Check if issue was created after sprint start
            created_raw = getattr(issue.fields, "created", None) or ""
            created_dt = _parse_date(created_raw)
            if created_dt and created_dt > s_dt:
                was_added_mid_sprint = True

            _jira_col_transitions: list[dict] = []
            _jira_prev_status = ""

            for history in getattr(changelog, "histories", []):
                change_date = _parse_date(getattr(history, "created", ""))
                if not change_date:
                    continue

                for item in getattr(history, "items", []):
                    field = getattr(item, "field", "")
                    from_val = getattr(item, "fromString", "") or ""
                    to_val = getattr(item, "toString", "") or ""

                    # Detect point changes during sprint
                    if field in ("Story Points", "story_points"):
                        if s_dt <= change_date <= (e_dt or change_date):
                            from_pts = _safe_float(from_val)
                            to_pts = _safe_float(to_val)
                            if from_pts != to_pts:
                                point_changes.append(
                                    {
                                        "date": change_date.isoformat(),
                                        "from_pts": from_pts,
                                        "to_pts": to_pts,
                                    }
                                )
                                if from_pts > 0:
                                    original_pts = from_pts

                    # Detect sprint membership changes
                    if field == "Sprint":
                        if sprint_name in to_val and sprint_name not in from_val:
                            if s_dt and change_date > s_dt:
                                was_added_mid_sprint = True

                    # Track status/column transitions
                    if field in ("status", "Status"):
                        if to_val and to_val != _jira_prev_status:
                            _jira_col_transitions.append(
                                {
                                    "date": change_date.isoformat(),
                                    "from": from_val,
                                    "to": to_val,
                                }
                            )
                            _jira_prev_status = to_val

            if point_changes:
                story["point_changed"] = True
                story["original_points"] = original_pts
                story["point_changes"] = point_changes
            if was_added_mid_sprint:
                story["added_mid_sprint"] = True
            if _jira_col_transitions:
                story["column_transitions"] = _jira_col_transitions

        except Exception:
            # Non-fatal — skip changelog for this issue
            continue


def _enrich_azdo_scope_changes(
    wit_client: object,
    project: str,
    stories: list[dict],
    iter_path: str,
    iter_start: str,
    iter_end: str,
) -> None:
    """Enrich story dicts with mid-sprint scope change data from AzDO revisions.

    For each story, fetches work item revisions and detects:
    - point_changed: True if story points were modified during the iteration
    - original_points: points value at iteration start
    - added_mid_sprint: True if moved into this iteration after it started
    - point_changes: list of {date, from_pts, to_pts} dicts
    """
    s_dt = _parse_date(iter_start)
    e_dt = _parse_date(iter_end)
    if not s_dt:
        return

    # Normalize iter_path for comparison with System.IterationPath
    iter_path = _normalize_iter_path(iter_path)

    for story in stories:
        wi_id_str = story.get("issue_key", "")
        if not wi_id_str:
            continue
        try:
            wi_id = int(wi_id_str)
        except (ValueError, TypeError):
            continue

        try:
            revisions = wit_client.get_revisions(wi_id, project=project)
            if not revisions:
                continue

            point_changes: list[dict] = []
            was_added_mid_sprint = False
            original_pts = story.get("points", 0)
            prev_pts = 0.0
            prev_iter = ""
            prev_column = ""
            created_date = None
            _col_transitions: list[dict] = []

            for rev in revisions:
                fields = rev.fields if hasattr(rev, "fields") else {}
                rev_date = _parse_date(fields.get("System.ChangedDate", ""))
                if not rev_date:
                    continue

                cur_pts = _safe_float(fields.get("Microsoft.VSTS.Scheduling.StoryPoints"))
                cur_iter = fields.get("System.IterationPath", "") or ""

                # Track workflow transitions. AzDO Taskboard columns
                # (Documentation, PR, Blocked, etc.) are board-level config
                # not stored on work items — only System.State is available
                # in revisions. Prefer BoardColumn if present (rare), else State.
                cur_col = fields.get("System.BoardColumn", "") or ""
                if not cur_col:
                    for fk, fv in fields.items():
                        if "Kanban.Column" in fk and isinstance(fv, str) and fv:
                            cur_col = fv
                            break
                if not cur_col:
                    cur_col = fields.get("System.State", "") or ""
                if cur_col and cur_col != prev_column:
                    _col_transitions.append(
                        {
                            "date": rev_date.isoformat(),
                            "from": prev_column,
                            "to": cur_col,
                        }
                    )
                    prev_column = cur_col

                # Track created date from first revision
                if created_date is None:
                    created_date = _parse_date(fields.get("System.CreatedDate", ""))

                # Look at changes within the sprint window
                if s_dt <= rev_date <= (e_dt or rev_date):
                    # Point change: either re-estimation (prev > 0 → different)
                    # OR initial pointing mid-sprint (prev was 0, now has value)
                    if cur_pts != prev_pts and (prev_pts > 0 or cur_pts > 0):
                        point_changes.append(
                            {
                                "date": rev_date.isoformat(),
                                "from_pts": prev_pts,
                                "to_pts": cur_pts,
                            }
                        )
                        if prev_pts > 0 and not story.get("original_points"):
                            original_pts = prev_pts

                    # Iteration path change — moved INTO this iteration mid-sprint
                    if (
                        iter_path
                        and iter_path in cur_iter
                        and prev_iter
                        and iter_path not in prev_iter
                        and rev_date > s_dt
                    ):
                        was_added_mid_sprint = True

                prev_pts = cur_pts
                prev_iter = cur_iter

            # Story created after sprint start directly into this sprint
            if created_date and created_date > s_dt and iter_path and iter_path in (prev_iter or ""):
                was_added_mid_sprint = True

            if point_changes:
                story["point_changed"] = True
                story["original_points"] = original_pts
                story["point_changes"] = point_changes
            if was_added_mid_sprint:
                story["added_mid_sprint"] = True
            if _col_transitions:
                story["column_transitions"] = _col_transitions

        except Exception:
            continue


def _analyse_scope_changes(sprint_data: list[dict]) -> dict:
    """Compute aggregate scope change metrics across sprints.

    When sprint_data entries contain a ``scope_timeline`` (SprintScopeTimeline),
    uses the daily snapshot data for accurate committed-vs-delivered metrics.
    Falls back to the legacy story-level flags when no timeline is present.

    Returns a dict with:
    - per_sprint: list of sprint-level metrics including committed/final/delivered pts
    - timelines: list of SprintScopeTimeline objects (for TUI/export rendering)
    - re_estimation_by_size / re_estimation_by_discipline
    - carry_over_chains
    - totals: aggregate counts + committed/delivered velocity averages
    """
    per_sprint: list[dict] = []
    timelines: list[SprintScopeTimeline] = []
    re_est_by_size: dict[int, int] = defaultdict(int)
    re_est_by_disc: dict[str, int] = defaultdict(int)
    issue_sprints: dict[str, list[str]] = defaultdict(list)
    total_added = 0
    total_removed = 0
    total_re_estimated = 0
    total_stories = 0
    committed_velocities: list[float] = []
    delivered_velocities: list[float] = []

    for sd in sprint_data:
        stories = sd.get("stories", [])
        sprint_name = sd.get("sprint_name", "?")
        timeline: SprintScopeTimeline | None = sd.get("scope_timeline")  # type: ignore[assignment]

        added = 0
        removed = 0
        re_estimated = 0

        for s in stories:
            if s.get("is_recurring"):
                continue  # exclude ceremony/KTLO tickets from scope counts
            issue_key = s.get("issue_key", "")
            if issue_key:
                issue_sprints[issue_key].append(sprint_name)

            total_stories += 1
            if s.get("added_mid_sprint"):
                added += 1
                total_added += 1
            if s.get("carried_over"):
                removed += 1
                total_removed += 1
            if s.get("point_changed"):
                re_estimated += 1
                total_re_estimated += 1
                pts = int(_safe_float(s.get("original_points", s.get("points", 0))))
                if pts in (1, 2, 3, 5, 8):
                    re_est_by_size[pts] += 1
                disc = s.get("discipline", "fullstack")
                re_est_by_disc[disc] += 1

        planned = sd.get("planned_count", 0) or len(stories)
        scope_change_rate = (
            round(
                (added + removed) / planned * 100,
                1,
            )
            if planned > 0
            else 0.0
        )

        sprint_entry: dict = {
            "name": sprint_name,
            "added": added,
            "removed": removed,
            "re_estimated": re_estimated,
            "scope_change_rate": scope_change_rate,
        }

        if timeline:
            timelines.append(timeline)
            sprint_entry["committed_pts"] = timeline.committed_pts
            sprint_entry["final_pts"] = timeline.final_pts
            sprint_entry["delivered_pts"] = timeline.delivered_pts
            sprint_entry["scope_change_total"] = timeline.scope_change_total
            sprint_entry["scope_churn"] = timeline.scope_churn
            sprint_entry["change_event_count"] = len(timeline.change_events)
            committed_velocities.append(timeline.committed_pts)
            delivered_velocities.append(timeline.delivered_pts)

        per_sprint.append(sprint_entry)

    # Carry-over chains: issues that appeared in 3+ sprints
    carry_over_chains = [
        {"issue_key": key, "sprint_count": len(sprints), "sprints": sprints}
        for key, sprints in issue_sprints.items()
        if len(sprints) >= 3
    ]
    carry_over_chains.sort(key=lambda x: x["sprint_count"], reverse=True)

    # Aggregate velocity from timelines
    avg_committed = round(statistics.mean(committed_velocities), 1) if committed_velocities else 0.0
    avg_delivered = round(statistics.mean(delivered_velocities), 1) if delivered_velocities else 0.0

    return {
        "per_sprint": per_sprint,
        "timelines": timelines,
        "re_estimation_by_size": dict(sorted(re_est_by_size.items())),
        "re_estimation_by_discipline": dict(sorted(re_est_by_disc.items())),
        "carry_over_chains": carry_over_chains[:10],
        "totals": {
            "added_mid_sprint": total_added,
            "removed_mid_sprint": total_removed,
            "re_estimated": total_re_estimated,
            "total_stories": total_stories,
            "avg_committed_velocity": avg_committed,
            "avg_delivered_velocity": avg_delivered,
        },
    }


def _fetch_jira_history(project_key: str, sprint_count: int) -> list[dict]:
    """Fetch historical sprint data from Jira.

    Returns a list of sprint dicts with story-level detail for profile building.
    """
    from jira import JIRA, JIRAError

    from yeaboi.config import get_jira_base_url, get_jira_email, get_jira_project_key, get_jira_token

    base_url, email, token = get_jira_base_url(), get_jira_email(), get_jira_token()
    if not all([base_url, email, token]):
        raise ValueError("Jira is not configured. Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN.")

    key = project_key.strip() or (get_jira_project_key() or "")
    if not key:
        raise ValueError("No project key provided and JIRA_PROJECT_KEY is not set.")

    logger.info("Connecting to Jira: %s, project=%s", base_url, key)
    jira = JIRA(server=base_url, basic_auth=(email, token))

    # Discover custom fields for AC and DoD (varies by Jira project)
    _ac_field_id = ""
    _dod_field_id = ""
    try:
        all_fields = jira.fields()
        _ac_keywords = {"acceptance criteria", "acceptance criterion", "ac criteria"}
        _dod_keywords = {"definition of done", "dod", "done criteria", "done definition"}
        for f in all_fields:
            fname = (f.get("name", "") or "").lower().strip()
            fid = f.get("id", "")
            if fname in _ac_keywords or "acceptance" in fname:
                _ac_field_id = fid
                logger.info("Found Jira AC field: %s (%s)", f.get("name"), fid)
            if fname in _dod_keywords or "definition of done" in fname:
                _dod_field_id = fid
                logger.info("Found Jira DoD field: %s (%s)", f.get("name"), fid)
    except Exception:
        logger.debug("Could not discover Jira custom fields", exc_info=True)

    # Build fields string including custom AC/DoD if discovered
    _base_fields = (
        "customfield_10016,story_points,assignee,created,"
        "resolutiondate,issuetype,labels,summary,subtasks,"
        "customfield_10014,description"
    )
    _extra_fields = ""
    if _ac_field_id:
        _extra_fields += f",{_ac_field_id}"
    if _dod_field_id:
        _extra_fields += f",{_dod_field_id}"
    _fetch_fields = _base_fields + _extra_fields

    boards = jira.boards(projectKeyOrID=key)
    if not boards:
        raise ValueError(f"No Jira board found for project '{key}'.")

    board = boards[0]
    logger.info("Using board: %s (id=%s)", getattr(board, "name", "?"), board.id)
    closed_sprints = jira.sprints(board.id, state="closed")
    sample = list(closed_sprints)[-sprint_count:] if closed_sprints else []
    logger.info("Found %d closed sprints, analysing last %d", len(list(closed_sprints or [])), len(sample))
    if not sample:
        return []

    sprint_data = []
    for sp in sample:
        # Fetch completed points and sprint dates
        info = jira.sprint_info(board.id, sp.id)
        completed_pts = _safe_float(info.get("completedPoints", 0))
        sprint_start = getattr(sp, "startDate", None) or info.get("startDate", "")
        sprint_end = getattr(sp, "endDate", None) or info.get("endDate", "")

        # Fetch all issues in this sprint (done + not-done for spillover)
        try:
            done_issues = jira.search_issues(
                f'project = "{key}" AND sprint = {sp.id} AND status = Done',
                maxResults=500,
                fields=_fetch_fields,
            )
        except JIRAError:
            done_issues = []

        try:
            all_in_sprint = jira.search_issues(
                f'project = "{key}" AND sprint = {sp.id}',
                maxResults=500,
                fields="customfield_10016,story_points,status,summary,resolutiondate,issuetype",
            )
            planned_count = len(all_in_sprint)
        except JIRAError:
            all_in_sprint = []
            planned_count = len(done_issues)

        # Spillover detection: issues in this sprint that were NOT done
        done_keys = {i.key for i in done_issues}

        # JQL fallback for velocity
        if completed_pts <= 0:
            jql_total = 0.0
            for issue in done_issues:
                sp_val = getattr(issue.fields, "customfield_10016", None)
                if sp_val is None:
                    sp_val = getattr(issue.fields, "story_points", None)
                if sp_val is not None:
                    jql_total += _safe_float(sp_val)
            completed_pts = jql_total

        stories = []
        for issue in done_issues:
            issue_type = getattr(issue.fields, "issuetype", None)
            type_name = getattr(issue_type, "name", "").lower() if issue_type else ""
            if type_name in ("sub-task", "subtask"):
                continue

            pts = _safe_float(getattr(issue.fields, "customfield_10016", None))
            if pts == 0:
                pts = _safe_float(getattr(issue.fields, "story_points", None))

            resolved = getattr(issue.fields, "resolutiondate", None)
            # Cycle time = max(sprint start, created) → resolved.
            # Sprint start is a better proxy for "work began" than created,
            # which includes backlog sitting time before the sprint.
            created_raw = getattr(issue.fields, "created", None) or ""
            work_started = sprint_start or created_raw
            if created_raw and sprint_start:
                c_dt = _parse_date(created_raw)
                s_dt = _parse_date(sprint_start)
                if c_dt and s_dt:
                    work_started = created_raw if c_dt > s_dt else sprint_start
            ct = _cycle_time_days(work_started, resolved)

            subtasks = getattr(issue.fields, "subtasks", []) or []
            task_count = len(subtasks)
            subtask_titles = []
            subtask_labels = []
            subtask_details = []
            for st in subtasks:
                st_summary = getattr(st.fields, "summary", "") if hasattr(st, "fields") else str(st)
                if st_summary:
                    subtask_titles.append(st_summary)
                st_type = getattr(st.fields, "issuetype", None) if hasattr(st, "fields") else None
                if st_type:
                    subtask_labels.append(getattr(st_type, "name", ""))
                st_status = getattr(st.fields, "status", None) if hasattr(st, "fields") else None
                st_status_name = getattr(st_status, "name", "") if st_status else ""
                st_assignee = getattr(st.fields, "assignee", None) if hasattr(st, "fields") else None
                st_assignee_name = getattr(st_assignee, "displayName", "") if st_assignee else ""
                subtask_details.append(
                    {
                        "title": st_summary or "",
                        "type": getattr(st_type, "name", "") if st_type else "",
                        "status": st_status_name,
                        "assignee": st_assignee_name,
                        "done": st_status_name.lower() in ("done", "closed", "resolved"),
                    }
                )

            epic_key = getattr(issue.fields, "customfield_10014", "") or ""
            summary = getattr(issue.fields, "summary", "") or ""
            description = getattr(issue.fields, "description", "") or ""

            # Read custom AC and DoD fields if discovered
            _jira_ac_text = ""
            _jira_dod_text = ""
            if _ac_field_id:
                _jira_ac_text = getattr(issue.fields, _ac_field_id, "") or ""
            if _dod_field_id:
                _jira_dod_text = getattr(issue.fields, _dod_field_id, "") or ""

            # AC count: use custom AC field first, fall back to description bullets
            # Combine all text sources for downstream analysis
            _all_ac_sources = " ".join(filter(None, [_jira_ac_text, _jira_dod_text]))
            _ac_source = _all_ac_sources if _all_ac_sources.strip() else description
            _ac_clean = re.sub(r"<[^>]+>", " ", _ac_source).strip()
            ac_count = 0
            if _ac_clean:
                ac_count = len(re.findall(r"(?m)^[\s]*[-*●•]\s", _ac_clean))
                if ac_count == 0:
                    ac_count = len(re.findall(r"(?m)^\s*\d+[.)]\s", _ac_clean))
                # Non-empty AC/DoD field without list structure counts as 1
                if ac_count == 0 and len(_ac_clean) > 10:
                    ac_count = 1

            # Append AC/DoD text to description for downstream analysis
            if _all_ac_sources.strip():
                description = description + "\n" + _all_ac_sources

            # Fetch comments for DoD signal analysis (with timestamps for ordering)
            comments_text: list[str] = []
            comments_timed: list[tuple[str, str]] = []  # (iso_date, body)
            try:
                issue_comments = jira.comments(issue.key)
                for c in issue_comments or []:
                    body = getattr(c, "body", "") or ""
                    if body:
                        comments_text.append(body)
                        c_date = getattr(c, "created", "") or ""
                        comments_timed.append((c_date, body))
            except Exception:
                pass

            labels = [
                lbl.lower() if isinstance(lbl, str) else getattr(lbl, "name", "").lower()
                for lbl in (getattr(issue.fields, "labels", []) or [])
            ]
            discipline = "fullstack"
            for lbl in labels:
                if lbl in (
                    "frontend",
                    "backend",
                    "infrastructure",
                    "design",
                    "testing",
                    "security",
                    "observability",
                    "platform",
                    "data",
                    "devops",
                    "developer-experience",
                    "networking",
                    "database",
                    "ci-cd",
                ):
                    discipline = lbl
                    break

            base_text = description + " " + " ".join(comments_text)
            story_row: dict = {
                "points": pts,
                "cycle_time_days": ct,
                "discipline": discipline,
                "issue_type": type_name,
                "task_count": task_count,
                "ac_count": ac_count,
                "epic_key": epic_key,
                "labels": labels,
                "point_changed": False,
                "summary": summary,
                "description": description,
                "comments": comments_text,
                "comments_timed": comments_timed,
                "subtask_titles": subtask_titles,
                "subtask_labels": subtask_labels,
                "subtask_details": subtask_details,
                "carried_over": False,
                "issue_key": issue.key,
                "issue_url": f"{base_url}/browse/{issue.key}",
                "sprint_name": sp.name,
                "repos": [],
                "repo_sources": [],
                "assignee": getattr(
                    getattr(issue.fields, "assignee", None),
                    "displayName",
                    "",
                )
                or "",
            }
            _story_add_repos(story_row, _extract_repos(base_text), "jira_text")
            _story_add_repos(
                story_row,
                _extract_repos(_jira_development_url_blob(jira, issue)),
                "jira_development",
            )
            stories.append(story_row)

        # Add spillover entries for issues NOT completed in this sprint
        for issue in all_in_sprint:
            if issue.key in done_keys:
                continue
            issue_type = getattr(issue.fields, "issuetype", None)
            type_name = getattr(issue_type, "name", "").lower() if issue_type else ""
            if type_name in ("sub-task", "subtask"):
                continue
            status = getattr(issue.fields, "status", None)
            status_name = getattr(status, "name", "").lower() if status else ""
            if status_name in ("done", "closed"):
                continue
            pts = _safe_float(getattr(issue.fields, "customfield_10016", None))
            if pts == 0:
                pts = _safe_float(getattr(issue.fields, "story_points", None))
            stories.append(
                {
                    "points": pts,
                    "cycle_time_days": None,
                    "discipline": "fullstack",
                    "task_count": 0,
                    "ac_count": 0,
                    "epic_key": "",
                    "point_changed": False,
                    "summary": getattr(issue.fields, "summary", "") or "",
                    "description": "",
                    "comments": [],
                    "subtask_titles": [],
                    "subtask_labels": [],
                    "carried_over": True,
                    "issue_key": issue.key,
                    "issue_url": f"{base_url}/browse/{issue.key}",
                    "sprint_name": sp.name,
                    "repos": [],
                    "repo_sources": [],
                }
            )

        # Enrich stories with mid-sprint scope change data from changelog
        try:
            _enrich_jira_scope_changes(jira, stories, sp.name, sprint_start, sprint_end)
        except Exception as _sc_err:
            logger.debug("Scope change enrichment failed for %s: %s", sp.name, _sc_err)

        # Build daily scope timeline from changelogs
        scope_timeline: SprintScopeTimeline | None = None
        try:
            scope_timeline = _build_jira_sprint_scope_timeline(
                jira,
                stories,
                sp.name,
                sprint_start,
                sprint_end,
                completed_pts,
            )
        except Exception as _tl_err:
            logger.debug("Jira scope timeline failed for %s: %s", sp.name, _tl_err)

        logger.info(
            "  Sprint %s: %.1f pts, %d/%d done, %d stories fetched (%d with comments)",
            sp.name,
            completed_pts,
            len(done_issues),
            planned_count,
            len(stories),
            sum(1 for s in stories if s.get("comments")),
        )
        sd_entry: dict = {
            "sprint_name": sp.name,
            "sprint_start": sprint_start,
            "sprint_end": sprint_end,
            "completed_points": completed_pts,
            "stories": stories,
            "planned_count": planned_count,
            "completed_count": len(done_issues),
        }
        if scope_timeline:
            sd_entry["scope_timeline"] = scope_timeline
        sprint_data.append(sd_entry)

    # Batch-fetch epic titles for naming analysis
    _all_epic_keys = {s.get("epic_key", "") for sd in sprint_data for s in sd.get("stories", []) if s.get("epic_key")}
    if _all_epic_keys:
        try:
            _epic_titles: dict[str, str] = {}
            _batch = list(_all_epic_keys)[:50]
            _keys_str = ", ".join(_batch)
            _epics = jira.search_issues(f"key in ({_keys_str})", maxResults=len(_batch), fields="summary")
            for _ep in _epics or []:
                _epic_titles[_ep.key] = getattr(_ep.fields, "summary", "") or ""
            for sd in sprint_data:
                for s in sd.get("stories", []):
                    ek = s.get("epic_key", "")
                    if ek and ek in _epic_titles:
                        s["epic_title"] = _epic_titles[ek]
            logger.info("Fetched %d epic titles", len(_epic_titles))
        except Exception:
            logger.debug("Epic title fetch failed (non-fatal)", exc_info=True)

    logger.info(
        "Jira fetch complete: %d sprints, %d total stories",
        len(sprint_data),
        sum(len(sd["stories"]) for sd in sprint_data),
    )
    return sprint_data


def _fetch_azdevops_history(project_key: str, sprint_count: int) -> list[dict]:
    """Fetch historical sprint data from Azure DevOps.

    Returns a list of sprint dicts with story-level detail for profile building.
    """
    from azure.devops.connection import Connection
    from msrest.authentication import BasicAuthentication

    from yeaboi.config import (
        get_azure_devops_org_url,
        get_azure_devops_project,
        get_azure_devops_team,
        get_azure_devops_token,
    )

    org_url = get_azure_devops_org_url()
    token = get_azure_devops_token()
    project = project_key.strip() or get_azure_devops_project() or ""
    team = get_azure_devops_team() or ""

    if not all([org_url, token, project]):
        raise ValueError(
            "Azure DevOps is not configured. Set AZURE_DEVOPS_ORG_URL, AZURE_DEVOPS_TOKEN, AZURE_DEVOPS_PROJECT."
        )

    credentials = BasicAuthentication("", token)
    connection = Connection(base_url=org_url, creds=credentials)
    work_client = connection.clients.get_work_client()
    wit_client = connection.clients.get_work_item_tracking_client()

    from azure.devops.v7_1.work.models import TeamContext

    _team_name = team or f"{project} Team"
    logger.info("Connecting to AzDO: %s, project=%s, team=%s", org_url, project, _team_name)
    team_context = TeamContext(project=project, team=_team_name)
    all_iterations = work_client.get_team_iterations(team_context) or []
    logger.info("Found %d total iterations", len(all_iterations))
    if not all_iterations:
        return []

    # Filter to past iterations by date (timeframe param not reliable)
    from datetime import UTC
    from datetime import datetime as _dt

    now = _dt.now(UTC)
    past_iterations = []
    for it in all_iterations:
        attrs = getattr(it, "attributes", None)
        if attrs:
            end = getattr(attrs, "finish_date", None)
            if end and end < now:
                past_iterations.append(it)
    if not past_iterations:
        past_iterations = list(all_iterations)

    sample = past_iterations[-sprint_count:]
    sprint_data = []

    for iteration in sample:
        iter_id = iteration.id

        # Capture iteration dates for cycle time calculation and scope change detection
        iter_attrs = getattr(iteration, "attributes", None)
        iter_start_str = ""
        iter_end_str = ""
        iter_path = getattr(iteration, "path", "") or ""
        if iter_attrs:
            _s = getattr(iter_attrs, "start_date", None)
            if _s:
                iter_start_str = _s.isoformat() if hasattr(_s, "isoformat") else str(_s)
            _e = getattr(iter_attrs, "finish_date", None)
            if _e:
                iter_end_str = _e.isoformat() if hasattr(_e, "isoformat") else str(_e)

        # Get work items for this iteration
        try:
            work_items_refs = work_client.get_iteration_work_items(team_context, iter_id)
            wi_relations = work_items_refs.work_item_relations if work_items_refs else []
        except Exception as e:
            logger.warning(
                "AzDO get_iteration_work_items failed for %s (id=%s): %s",
                getattr(iteration, "name", "?"),
                iter_id,
                e,
            )
            wi_relations = []

        if not wi_relations:
            logger.info(
                "AzDO iteration %s: no work_item_relations from API (team=%r project=%r)",
                getattr(iteration, "name", iter_id),
                _team_name,
                project,
            )
            continue

        wi_ids: list[int] = []
        seen_id: set[int] = set()
        for rel in wi_relations:
            tid = _azdo_work_item_link_target_id(rel)
            if tid is not None and tid not in seen_id:
                seen_id.add(tid)
                wi_ids.append(tid)
        if not wi_ids:
            logger.info(
                "AzDO iteration %s: could not resolve work item ids from %d link(s)",
                getattr(iteration, "name", iter_id),
                len(wi_relations),
            )
            continue

        _fields_batch = [
            "System.WorkItemType",
            "Microsoft.VSTS.Scheduling.StoryPoints",
            "System.State",
            "System.CreatedDate",
            "Microsoft.VSTS.Common.ResolvedDate",
            "System.Tags",
            "System.Title",
            "System.Parent",
            "System.Description",
            "Microsoft.VSTS.Common.AcceptanceCriteria",
            "System.CommentCount",
            "System.AssignedTo",
            "Microsoft.VSTS.Common.ActivatedDate",
        ]

        # Fetch work item details in batches — collect both stories and tasks,
        # then attach tasks to their parent stories.
        stories = []
        tasks_by_parent: dict[int, list[dict]] = defaultdict(list)
        all_items_raw: list = []
        completed_pts = 0.0
        completed_count = 0
        planned_count = len(wi_ids)

        batch_size = 200
        for i in range(0, len(wi_ids), batch_size):
            batch_ids = wi_ids[i : i + batch_size]
            items = _wit_get_work_items_batch(
                wit_client,
                project,
                batch_ids,
                _fields_batch,
                want_relations=True,
            )
            all_items_raw.extend(items)

        if not all_items_raw and wi_ids:
            logger.error(
                "AzDO iteration %s: %d work item id(s) from iteration links but "
                "get_work_items returned no rows — check PAT scope (Work Items: Read), "
                "project name %r, team %r",
                getattr(iteration, "name", iter_id),
                len(wi_ids),
                project,
                _team_name,
            )

        # First pass: separate tasks from stories/bugs
        story_items = []
        for item in all_items_raw:
            fields = item.fields or {}
            wi_type = fields.get("System.WorkItemType", "").lower()
            if wi_type in ("task",):
                parent_id = fields.get("System.Parent")
                if parent_id:
                    state = fields.get("System.State", "")
                    tasks_by_parent[int(parent_id)].append(
                        {
                            "title": fields.get("System.Title", "") or "",
                            "type": "Task",
                            "status": state,
                            "assignee": _azdo_assignee_name(fields),
                            "done": state in ("Closed", "Done", "Resolved"),
                        }
                    )
            else:
                story_items.append(item)

        # Second pass: build story dicts with attached child tasks
        for item in story_items:
            fields = item.fields or {}
            state = fields.get("System.State", "")
            pts = _safe_float(fields.get("Microsoft.VSTS.Scheduling.StoryPoints"))
            title = fields.get("System.Title", "") or ""
            desc = fields.get("System.Description", "") or ""
            ac_text_raw = fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""
            # Strip HTML tags for text analysis (AzDO AC fields are often rich HTML)
            ac_text = re.sub(r"<[^>]+>", " ", ac_text_raw).strip()
            ac_text = re.sub(r"\s+", " ", ac_text)
            full_desc = f"{desc}\n{ac_text_raw}"

            ac_count = 0
            if ac_text:
                # Try bullet/numbered list patterns first
                ac_count = len(re.findall(r"(?m)^[\s]*[-*●•]\s", ac_text))
                if ac_count == 0:
                    ac_count = len(re.findall(r"(?m)^\s*\d+[.)]\s", ac_text))
                # If AC field has text but no list structure, count as 1 AC
                if ac_count == 0 and len(ac_text) > 10:
                    ac_count = 1

            is_done = state in ("Closed", "Done", "Resolved")
            ct = None
            if is_done:
                completed_count += 1
                completed_pts += pts

                # Cycle time: use the latest of (iteration start, activated,
                # created) as the "work started" signal. This avoids counting
                # backlog time (created months ago) or re-activation from a
                # previous sprint. The iteration start is the floor — work
                # on a sprint item can't meaningfully start before the sprint.
                activated = fields.get("Microsoft.VSTS.Common.ActivatedDate", "")
                created = fields.get("System.CreatedDate", "")
                resolved = fields.get("Microsoft.VSTS.Common.ResolvedDate", "")

                # Pick the best start signal
                candidates = [
                    _parse_date(iter_start_str),
                    _parse_date(activated),
                    _parse_date(created),
                ]
                valid_starts = [d for d in candidates if d is not None]
                if valid_starts and resolved:
                    # Use the LATEST start date — can't start before sprint
                    best_start = max(valid_starts)
                    r_dt = _parse_date(resolved)
                    if r_dt and best_start:
                        ct = max((r_dt - best_start).total_seconds() / 86400, 0.0)
                    else:
                        ct = None
                else:
                    ct = None

            tags = fields.get("System.Tags", "") or ""
            discipline = "fullstack"
            for tag in tags.lower().split(";"):
                tag = tag.strip()
                if tag in (
                    "frontend",
                    "backend",
                    "infrastructure",
                    "design",
                    "testing",
                    "security",
                    "observability",
                    "platform",
                    "data",
                    "devops",
                    "developer-experience",
                    "networking",
                    "database",
                    "ci-cd",
                ):
                    discipline = tag
                    break

            parent_id = fields.get("System.Parent")
            epic_key = str(parent_id) if parent_id else ""

            child_tasks = tasks_by_parent.get(item.id, [])
            st_titles = [t["title"] for t in child_tasks if t.get("title")]
            st_labels = [t.get("type", "Task") for t in child_tasks]

            _wi_type = (fields.get("System.WorkItemType", "") or "").lower()

            story_row = {
                "points": pts,
                "cycle_time_days": ct,
                "discipline": discipline,
                "issue_type": _wi_type,
                "task_count": len(child_tasks),
                "ac_count": ac_count,
                "epic_key": epic_key,
                "labels": [t.strip().lower() for t in tags.split(";") if t.strip()],
                "point_changed": False,
                "summary": title,
                "description": full_desc,
                "comments": [],
                "subtask_titles": st_titles,
                "subtask_labels": st_labels,
                "subtask_details": child_tasks,
                "carried_over": not is_done,
                "issue_key": str(item.id),
                "issue_url": f"{org_url}/{project}/_workitems/edit/{item.id}",
                "sprint_name": getattr(iteration, "name", str(iter_id)),
                "repos": [],
                "repo_sources": [],
                "assignee": _azdo_assignee_name(fields),
            }
            _story_add_repos(story_row, _extract_repos(full_desc), "description")
            _story_add_repos(
                story_row,
                _extract_repos_from_azdo_relations(item),
                "azdo_work_item_links",
            )
            stories.append(story_row)

        # Enrich stories with mid-sprint scope change data from revisions
        try:
            _enrich_azdo_scope_changes(
                wit_client,
                project,
                stories,
                iter_path,
                iter_start_str,
                iter_end_str,
            )
        except Exception as _sc_err:
            logger.debug("AzDO scope change enrichment failed for %s: %s", getattr(iteration, "name", "?"), _sc_err)

        # Build daily scope timeline from revisions
        _azdo_timeline: SprintScopeTimeline | None = None
        try:
            _azdo_timeline = _build_azdo_sprint_scope_timeline(
                wit_client,
                project,
                stories,
                iter_path,
                iter_start_str,
                iter_end_str,
                completed_pts,
            )
        except Exception as _tl_err:
            logger.debug("AzDO scope timeline failed for %s: %s", getattr(iteration, "name", "?"), _tl_err)

        _iter_name = getattr(iteration, "name", str(iter_id))
        logger.info(
            "  Iteration %s: %.1f pts, %d/%d done, %d stories, %d tasks attached",
            _iter_name,
            completed_pts,
            completed_count,
            planned_count,
            len(stories),
            sum(len(s.get("subtask_details", [])) for s in stories),
        )
        _azdo_sd_entry: dict = {
            "sprint_name": _iter_name,
            "sprint_start": iter_start_str,
            "sprint_end": iter_end_str,
            "completed_points": completed_pts,
            "stories": stories,
            "planned_count": planned_count,
            "completed_count": completed_count,
        }
        if _azdo_timeline:
            _azdo_sd_entry["scope_timeline"] = _azdo_timeline
        sprint_data.append(_azdo_sd_entry)

    try:
        _azdo_enrich_repos_from_git_pull_requests(connection, project, sprint_data)
    except Exception as e:
        logger.warning("AzDO PR repo enrichment failed: %s", e)

    # Batch-fetch epic/parent titles for naming analysis
    _all_parent_ids = {
        int(s.get("epic_key", "0"))
        for sd in sprint_data
        for s in sd.get("stories", [])
        if s.get("epic_key") and s["epic_key"].isdigit()
    }
    if _all_parent_ids:
        try:
            _parent_titles: dict[str, str] = {}
            _batch_ids = list(_all_parent_ids)[:50]
            _parents = wit_client.get_work_items(_batch_ids, project=project, fields=["System.Title"])
            for _pw in _parents or []:
                if _pw and hasattr(_pw, "fields"):
                    _parent_titles[str(_pw.id)] = (_pw.fields or {}).get("System.Title", "")
            for sd in sprint_data:
                for s in sd.get("stories", []):
                    ek = s.get("epic_key", "")
                    if ek and ek in _parent_titles:
                        s["epic_title"] = _parent_titles[ek]
            logger.info("Fetched %d epic/parent titles", len(_parent_titles))
        except Exception:
            logger.debug("AzDO epic title fetch failed (non-fatal)", exc_info=True)

    return sprint_data


# ---------------------------------------------------------------------------
# Helpers for compare_plan_to_actuals
# ---------------------------------------------------------------------------


def _fetch_jira_actuals(issue_keys: list[str]) -> dict[str, dict]:
    """Fetch actual status/points/cycle time for a set of Jira issue keys."""
    if not issue_keys:
        return {}

    from jira import JIRA

    from yeaboi.config import (
        get_jira_base_url,
        get_jira_email,
        get_jira_token,
    )

    base_url, email, token = (get_jira_base_url(), get_jira_email(), get_jira_token())
    if not all([base_url, email, token]):
        return {}

    jira = JIRA(server=base_url, basic_auth=(email, token))
    results: dict[str, dict] = {}

    # Fetch in batches of 50 (JQL IN clause limit)
    for i in range(0, len(issue_keys), 50):
        batch = issue_keys[i : i + 50]
        keys_str = ",".join(batch)
        try:
            issues = jira.search_issues(
                f"key in ({keys_str})",
                maxResults=len(batch),
                fields="customfield_10016,story_points,status,created,resolutiondate,summary",
            )
        except Exception:
            continue

        for issue in issues:
            pts = _safe_float(getattr(issue.fields, "customfield_10016", None))
            if pts == 0:
                pts = _safe_float(getattr(issue.fields, "story_points", None))
            status = getattr(issue.fields, "status", None)
            status_name = getattr(status, "name", "") if status else ""
            created = getattr(issue.fields, "created", None)
            resolved = getattr(issue.fields, "resolutiondate", None)
            ct = _cycle_time_days(created, resolved)

            results[issue.key] = {
                "points": pts,
                "status": status_name,
                "cycle_time_days": ct,
                "summary": getattr(issue.fields, "summary", "") or "",
            }

    return results


def _fetch_azdevops_actuals(work_item_ids: list[str], project_key: str) -> dict[str, dict]:
    """Fetch actual status/points/cycle time for AzDO work item IDs."""
    if not work_item_ids:
        return {}

    from azure.devops.connection import Connection
    from msrest.authentication import BasicAuthentication

    from yeaboi.config import (
        get_azure_devops_org_url,
        get_azure_devops_project,
        get_azure_devops_token,
    )

    org_url = get_azure_devops_org_url()
    token = get_azure_devops_token()
    if not all([org_url, token]):
        return {}

    credentials = BasicAuthentication("", token)
    connection = Connection(base_url=org_url, creds=credentials)
    wit_client = connection.clients.get_work_item_tracking_client()

    proj_scope = (project_key or "").strip() or (get_azure_devops_project() or "")

    int_ids = []
    for wid in work_item_ids:
        try:
            int_ids.append(int(wid))
        except (ValueError, TypeError):
            continue

    results: dict[str, dict] = {}
    for i in range(0, len(int_ids), 200):
        batch = int_ids[i : i + 200]
        try:
            items = wit_client.get_work_items(
                batch,
                project=proj_scope or None,
                fields=[
                    "Microsoft.VSTS.Scheduling.StoryPoints",
                    "System.State",
                    "System.CreatedDate",
                    "Microsoft.VSTS.Common.ResolvedDate",
                    "System.Title",
                ],
            )
        except Exception as e:
            logger.warning("AzDO get_work_items (actuals) failed: %s", e)
            continue

        for item in items or []:
            fields = item.fields or {}
            pts = _safe_float(fields.get("Microsoft.VSTS.Scheduling.StoryPoints"))
            state = fields.get("System.State", "")
            created = fields.get("System.CreatedDate", "")
            resolved = fields.get("Microsoft.VSTS.Common.ResolvedDate", "")
            ct = _cycle_time_days(created, resolved)

            results[str(item.id)] = {
                "points": pts,
                "status": state,
                "cycle_time_days": ct,
                "summary": fields.get("System.Title", "") or "",
            }

    return results


# ---------------------------------------------------------------------------
# Tool: compare_plan_to_actuals
# ---------------------------------------------------------------------------


@tool
def compare_plan_to_actuals(
    session_id: str = "",
    source: str = "",
    project_key: str = "",
) -> str:
    """Compare a previously generated plan to actual sprint outcomes.

    Loads a stored session's plan (stories, points, sprint assignments) and
    matches them to actual Jira/AzDO data. Returns a structured comparison
    showing: estimated vs actual points, planned vs actual sprint, stories
    added/removed, and cycle times.

    Use this after sprints have been completed to measure estimation accuracy
    and feed improvements back into the team profile.

    session_id: The session ID of the plan to compare. Use 'latest' for most recent.
    source: "jira" or "azdevops". Auto-detected if only one is configured.
    project_key: Jira project key or AzDO project name.
    """
    # See docs: "Tools" — tool types, risk levels
    logger.debug("compare_plan_to_actuals called: session_id=%r, source=%r", session_id, source)
    from pathlib import Path

    from yeaboi.sessions import SessionStore

    # Load the session's plan
    db_dir = Path.home() / ".scrum-agent"
    db_path = db_dir / "sessions.db"
    if not db_path.exists():
        return json.dumps({"error": "No sessions database found."})

    with SessionStore(db_path) as store:
        if session_id == "latest" or not session_id:
            sid = store.get_latest_session_id()
            if not sid:
                return json.dumps({"error": "No sessions found."})
        else:
            sid = session_id

        state = store.load_state(sid)
        if not state:
            return json.dumps({"error": f"Could not load state for session '{sid}'."})

    planned_stories = state.get("stories", [])
    planned_sprints = state.get("sprints", [])
    if not planned_stories:
        return json.dumps({"error": "Session has no stories to compare."})

    jira_keys = state.get("jira_story_keys", {})
    azdevops_keys = state.get("azdevops_story_keys", {})

    if not source:
        source = _detect_source()

    def _get_pts(s) -> float:
        if hasattr(s, "story_points"):
            sp = s.story_points
            return float(sp.value if hasattr(sp, "value") else sp)
        if isinstance(s, dict):
            return _safe_float(s.get("story_points", 0))
        return 0.0

    planned_total = sum(_get_pts(s) for s in planned_stories)

    comparison: dict = {
        "session_id": sid,
        "planned_story_count": len(planned_stories),
        "planned_sprint_count": len(planned_sprints),
        "planned_total_points": planned_total,
        "story_comparisons": [],
        "summary": {},
    }

    # Determine which tracker keys we have
    tracker_keys = {}
    if source == "jira" and jira_keys:
        tracker_keys = jira_keys
        comparison["tracker"] = "jira"
    elif source == "azdevops" and azdevops_keys:
        tracker_keys = azdevops_keys
        comparison["tracker"] = "azdevops"
    else:
        comparison["tracker"] = source or "none"
        comparison["matched_stories"] = 0
        comparison["note"] = "No tracker key mappings found \u2014 sync stories to Jira/AzDO first, then re-run retro."
        return json.dumps(comparison, ensure_ascii=False, default=str)

    # Fetch actual data from the tracker for matched stories
    actuals: dict[str, dict] = {}
    try:
        if source == "jira":
            actuals = _fetch_jira_actuals(list(tracker_keys.values()))
        elif source == "azdevops":
            actuals = _fetch_azdevops_actuals(list(tracker_keys.values()), project_key)
    except Exception as exc:
        logger.error("Failed to fetch actuals: %s", exc)
        comparison["note"] = f"Failed to fetch tracker data: {exc}"
        comparison["matched_stories"] = len(tracker_keys)
        return json.dumps(comparison, ensure_ascii=False, default=str)

    # Match planned stories to actuals
    matched = 0
    points_delta_sum = 0.0
    stories_on_target = 0
    stories_over = 0
    stories_under = 0
    actual_total_pts = 0.0

    for ps in planned_stories:
        title = getattr(ps, "title", "") or (ps.get("title", "") if isinstance(ps, dict) else "")
        story_id = getattr(ps, "id", "") or (ps.get("id", "") if isinstance(ps, dict) else "")
        planned_pts = _get_pts(ps)
        tracker_key = tracker_keys.get(story_id, "")

        actual = actuals.get(tracker_key, {}) if tracker_key else {}

        if not actual and tracker_key:
            # Try fuzzy match by title
            for ak, av in actuals.items():
                if av.get("summary", "").lower() == title.lower():
                    actual = av
                    break

        if actual:
            matched += 1
            actual_pts = _safe_float(actual.get("points", 0))
            actual_total_pts += actual_pts
            delta = actual_pts - planned_pts
            points_delta_sum += abs(delta)

            if abs(delta) < 0.5:
                stories_on_target += 1
            elif delta > 0:
                stories_over += 1
            else:
                stories_under += 1

            comparison["story_comparisons"].append(
                {
                    "title": title,
                    "planned_pts": planned_pts,
                    "actual_pts": actual_pts,
                    "delta": round(delta, 1),
                    "status": actual.get("status", "unknown"),
                    "cycle_time_days": actual.get("cycle_time_days"),
                }
            )
        else:
            comparison["story_comparisons"].append(
                {
                    "title": title,
                    "planned_pts": planned_pts,
                    "actual_pts": None,
                    "delta": None,
                    "status": "not found in tracker",
                    "cycle_time_days": None,
                }
            )

    comparison["matched_stories"] = matched
    comparison["summary"] = {
        "matched": matched,
        "not_matched": len(planned_stories) - matched,
        "planned_total_pts": round(planned_total, 1),
        "actual_total_pts": round(actual_total_pts, 1),
        "on_target": stories_on_target,
        "over_estimated": stories_under,
        "under_estimated": stories_over,
        "avg_point_delta": round(points_delta_sum / matched, 1) if matched else 0.0,
    }

    return json.dumps(comparison, ensure_ascii=False, default=str)
