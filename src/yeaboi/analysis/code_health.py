"""Deterministic repository-health analysis and prioritized actions."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath

_MANIFESTS = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "requirements.txt",
}

_BINARY_SUFFIXES = {
    ".7z",
    ".avi",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".pptx",
    ".pyc",
    ".so",
    ".tar",
    ".woff",
    ".woff2",
    ".xlsx",
    ".zip",
}
_GENERATED_PARTS = {
    ".next",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "target",
    "vendor",
}
_TEST_PARTS = {"test", "tests", "__tests__", "spec", "specs"}
_DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}


def _finding(
    repo: dict,
    category: str,
    title: str,
    detail: str,
    evidence: str,
    *,
    priority: str,
    effort: str,
    confidence: str = "high",
) -> dict:
    return {
        "id": f"{repo.get('provider', '')}:{repo.get('name', '')}:{category}:{title}".lower().replace(" ", "-"),
        "category": category,
        "title": title,
        "detail": detail,
        "priority": priority,
        "impact": "Improves delivery safety and maintainability",
        "confidence": confidence,
        "evidence": evidence,
        "link": repo.get("url", ""),
        "affected_scope": [repo.get("name", "")],
        "next_steps": [detail],
        "owner_role": "Repository maintainer",
        "effort": effort,
        "completion_check": f"Confirm {title.lower()} for {repo.get('name', 'the repository')}.",
    }


def analyse_repository_health(
    inventory: list[dict], active_names: set[tuple[str, str]]
) -> tuple[list[dict], list[dict]]:
    """Analyse every recently active repository from its complete file inventory."""
    reports: list[dict] = []
    findings: list[dict] = []
    for repo in inventory:
        if repo.get("discovery_error"):
            continue
        key = (str(repo.get("provider", "")), str(repo.get("name", "")).lower())
        # GitHub names are owner/repo while activity is tagged with the same slug.
        active = bool(repo.get("active")) if repo.get("provider") == "github" else key in active_names
        if not active:
            reports.append({**repo, "status": "unchanged", "findings": []})
            continue
        paths = [str(p) for p in repo.get("paths", [])]
        lower = {p.lower() for p in paths}
        basenames = {p.rsplit("/", 1)[-1] for p in lower}
        repo_findings: list[dict] = []
        if not any(name.startswith("readme") for name in basenames):
            repo_findings.append(
                _finding(
                    repo,
                    "documentation",
                    "Add a repository README",
                    "Document the repository purpose, setup, test, and release workflow.",
                    "No README was found on the default branch.",
                    priority="high",
                    effort="small",
                )
            )
        if not any(
            "architecture" in path or "/adr/" in f"/{path}" or path.startswith(("adr/", "docs/adr/")) for path in lower
        ):
            repo_findings.append(
                _finding(
                    repo,
                    "architecture",
                    "Record architecture decisions",
                    "Add a short architecture overview and ADRs for consequential design choices.",
                    "No architecture overview or ADR directory was found.",
                    priority="medium",
                    effort="small",
                    confidence="medium",
                )
            )
        if not any(p.startswith(("test/", "tests/", "__tests__/")) or "/test" in p for p in lower):
            repo_findings.append(
                _finding(
                    repo,
                    "testing",
                    "Establish an automated test suite",
                    "Add executable tests for the highest-risk paths and run them in CI.",
                    "No conventional test directory was found.",
                    priority="high",
                    effort="medium",
                    confidence="medium",
                )
            )
        has_ci = any(p.startswith((".github/workflows/", ".azure-pipelines/")) for p in lower) or any(
            p in lower for p in ("azure-pipelines.yml", ".gitlab-ci.yml", "jenkinsfile")
        )
        if not has_ci:
            repo_findings.append(
                _finding(
                    repo,
                    "delivery",
                    "Add continuous integration",
                    "Run build, test, and static checks for every proposed change.",
                    "No supported CI workflow was found.",
                    priority="high",
                    effort="medium",
                )
            )
        if not any(name in basenames for name in _MANIFESTS):
            repo_findings.append(
                _finding(
                    repo,
                    "maintainability",
                    "Document dependency management",
                    "Add or document the canonical build and dependency manifest.",
                    "No recognised dependency/build manifest was found.",
                    priority="medium",
                    effort="small",
                    confidence="medium",
                )
            )
        if not any("codeowners" in p for p in lower):
            repo_findings.append(
                _finding(
                    repo,
                    "ownership",
                    "Define code ownership",
                    "Add CODEOWNERS entries for critical areas and review routing.",
                    "No CODEOWNERS file was found.",
                    priority="medium",
                    effort="small",
                )
            )
        if not any("dependabot" in p or "renovate" in p for p in lower):
            repo_findings.append(
                _finding(
                    repo,
                    "security",
                    "Automate dependency updates",
                    "Configure a dependency update service and require validation before merge.",
                    "No Dependabot or Renovate configuration was found; this is an indicator, not a security audit.",
                    priority="medium",
                    effort="small",
                    confidence="medium",
                )
            )
        operational = any(
            p.endswith((".tf", "dockerfile", "docker-compose.yml", "docker-compose.yaml"))
            or "k8s/" in p
            or "kubernetes/" in p
            for p in lower
        )
        has_runbook = any("runbook" in p or "playbook" in p for p in lower)
        if operational and not has_runbook:
            repo_findings.append(
                _finding(
                    repo,
                    "operations",
                    "Add an operational runbook",
                    "Document deployment, rollback, verification, alert response, and ownership.",
                    "Deployment/infrastructure files exist but no runbook or playbook was found.",
                    priority="high",
                    effort="medium",
                    confidence="medium",
                )
            )
        reports.append(
            {
                "provider": repo.get("provider", ""),
                "container": repo.get("container", ""),
                "repository": repo.get("name", ""),
                "url": repo.get("url", ""),
                "default_branch": repo.get("default_branch", ""),
                "files_scanned": len(paths),
                "status": "succeeded" if not repo.get("error") else "truncated",
                "findings": repo_findings,
            }
        )
        findings.extend(repo_findings)
    return reports, findings


def prioritize_actions(findings: list[dict]) -> list[dict]:
    """Deduplicate estate findings into cross-repository, inspectable actions."""
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    grouped: dict[tuple[str, str], list[dict]] = {}
    for finding in findings:
        grouped.setdefault((finding.get("category", ""), finding.get("title", "")), []).append(finding)
    actions: list[dict] = []
    for (_category, _title), group in grouped.items():
        action = dict(group[0])
        scopes = sorted({s for item in group for s in item.get("affected_scope", []) if s})
        action["affected_scope"] = scopes
        action["breadth"] = len(scopes)
        if len(scopes) > 1:
            action["evidence"] = f"{action['evidence']} Affects {len(scopes)} repositories."
        actions.append(action)
    return sorted(
        actions,
        key=lambda a: (
            order.get(str(a.get("priority", "low")), 9),
            -int(a.get("breadth", 1)),
            str(a.get("title", "")),
        ),
    )


def repository_health_summary(reports: list[dict], findings: list[dict]) -> dict:
    return {
        "repositories_analysed": sum(r.get("status") in {"succeeded", "truncated"} for r in reports),
        "repositories_unchanged": sum(r.get("status") == "unchanged" for r in reports),
        "files_inventoried": sum(int(r.get("files_scanned", 0)) for r in reports),
        "findings": len(findings),
        "by_category": dict(sorted(Counter(f.get("category", "") for f in findings).items())),
    }


def _file_scope(change: dict) -> str:
    return f"{change.get('repository', '')}:{change.get('path', '')}"


def _is_test_path(path: str) -> bool:
    parts = {part.lower() for part in PurePosixPath(path).parts}
    name = PurePosixPath(path).name.lower()
    return bool(parts & _TEST_PARTS) or name.startswith("test_") or ".test." in name or ".spec." in name


def _eligibility(change: dict) -> tuple[bool, str]:
    path = str(change.get("path", ""))
    suffix = PurePosixPath(path).suffix.lower()
    parts = {part.lower() for part in PurePosixPath(path).parts}
    status = str(change.get("status", "")).lower()
    if status == "failed":
        return False, str(change.get("error", "") or "change lookup failed")
    if status in {"delete", "deleted"}:
        return False, "deleted file"
    if suffix in _BINARY_SUFFIXES:
        return False, "binary file"
    if parts & _GENERATED_PARTS:
        return False, "generated or vendored file"
    return True, ""


def analyse_changed_files(changes: list[dict], window_days: int = 120) -> tuple[list[dict], list[dict], dict]:
    """Analyse only files attributable to selected-user commits or authored PRs."""
    from yeaboi.analysis.coverage import CoverageTracker

    tracker = CoverageTracker("code", window_days)
    reports: list[dict] = []
    findings: list[dict] = []
    eligible: list[dict] = []
    for change in changes:
        ok, reason = _eligibility(change)
        provider = str(change.get("provider", ""))
        container = f"{change.get('container', '')}/{change.get('repository', '')}".strip("/")
        asset = str(change.get("path", ""))
        if not ok:
            status = "failed" if str(change.get("status", "")).lower() == "failed" else "unchanged"
            tracker.add(provider, container, asset, status, reason, eligible=status == "failed")
            reports.append(
                {
                    **change,
                    "analysis_status": "failed" if status == "failed" else "excluded",
                    "reason": reason,
                }
            )
            continue
        coverage_status = "truncated" if change.get("truncated") else "succeeded"
        tracker.add(provider, container, asset, coverage_status, str(change.get("error", "")))
        report = {**change, "analysis_status": coverage_status, "reason": str(change.get("error", ""))}
        reports.append(report)
        eligible.append(report)

    # Aggregate repeat touches into file-level hotspots.
    touches = Counter(_file_scope(change) for change in eligible)
    first_by_scope = {_file_scope(change): change for change in eligible}
    for scope, count in touches.items():
        change = first_by_scope[scope]
        churn = sum(
            int(item.get("additions", 0) or 0) + int(item.get("deletions", 0) or 0)
            for item in eligible
            if _file_scope(item) == scope
        )
        if count >= 3:
            findings.append(
                {
                    "id": f"{scope}:hotspot",
                    "category": "hotspot",
                    "title": "Stabilise a frequently changed file",
                    "detail": (
                        "Review why this file changes repeatedly and split responsibilities "
                        "if it has become a bottleneck."
                    ),
                    "priority": "high" if count >= 5 else "medium",
                    "impact": "Reduces regression risk in a concentrated change hotspot.",
                    "confidence": change.get("confidence", "high"),
                    "evidence": f"{scope} changed in {count} selected-user changes during the window.",
                    "link": change.get("url", ""),
                    "affected_scope": [scope],
                    "next_steps": [
                        "Review recent changes together.",
                        "Extract unstable responsibilities or add focused tests.",
                    ],
                    "owner_role": "Selected contributor",
                    "effort": "medium",
                    "completion_check": (
                        "The file has a clear responsibility and regression coverage for its frequently changed paths."
                    ),
                }
            )
        if churn >= 500:
            findings.append(
                {
                    "id": f"{scope}:large-change",
                    "category": "change-size",
                    "title": "Break down a large code change",
                    "detail": "Split large changes into independently reviewable units with targeted validation.",
                    "priority": "high",
                    "impact": "Makes review and rollback safer.",
                    "confidence": change.get("confidence", "high"),
                    "evidence": f"{scope} accumulated {churn} added/deleted lines.",
                    "link": change.get("url", ""),
                    "affected_scope": [scope],
                    "next_steps": ["Separate behavioural and mechanical changes.", "Add focused tests for each unit."],
                    "owner_role": "Selected contributor",
                    "effort": "small",
                    "completion_check": "Future changes are split into reviewable units with explicit validation.",
                }
            )

    # A selected user's production change should normally carry a test change in
    # the same commit or PR. Whole-PR attribution remains medium confidence.
    by_change: dict[tuple[str, str, str], list[dict]] = {}
    for change in eligible:
        key = (
            str(change.get("provider", "")),
            str(change.get("repository", "")),
            str(change.get("change_id", "")),
        )
        by_change.setdefault(key, []).append(change)
    for (_provider, repository, change_id), group in by_change.items():
        production = [
            item
            for item in group
            if not _is_test_path(str(item.get("path", "")))
            and PurePosixPath(str(item.get("path", ""))).suffix.lower() not in _DOC_SUFFIXES
        ]
        if production and not any(_is_test_path(str(item.get("path", ""))) for item in group):
            scopes = sorted({_file_scope(item) for item in production})
            exemplar = production[0]
            findings.append(
                {
                    "id": f"{repository}:{change_id}:tests",
                    "category": "testing",
                    "title": "Add tests alongside production changes",
                    "detail": "Add or update focused tests in the same commit or PR as the behavioural change.",
                    "priority": "high",
                    "impact": "Makes selected-user changes safer to review and release.",
                    "confidence": exemplar.get("confidence", "high"),
                    "evidence": f"{len(scopes)} production file(s) changed without a test-file change.",
                    "link": exemplar.get("url", ""),
                    "affected_scope": scopes,
                    "next_steps": [
                        "Identify the changed behaviour.",
                        "Add a regression test that fails without the change.",
                    ],
                    "owner_role": "Selected contributor",
                    "effort": "small",
                    "completion_check": "The change has an automated test covering its intended behaviour.",
                }
            )

    coverage = tracker.as_dict()
    return reports, findings, coverage


def changed_file_summary(reports: list[dict], findings: list[dict]) -> dict:
    eligible = [r for r in reports if r.get("analysis_status") in {"succeeded", "truncated"}]
    return {
        "files_analysed": len(eligible),
        "files_excluded": sum(r.get("analysis_status") == "excluded" for r in reports),
        "files_failed": sum(r.get("analysis_status") == "failed" for r in reports),
        "repositories_touched": len({r.get("repository") for r in eligible if r.get("repository")}),
        "authored_commit_files": sum(r.get("attribution") == "authored_commit" for r in eligible),
        "authored_pr_files": sum(r.get("attribution") == "authored_pr" for r in eligible),
        "findings": len(findings),
        "by_category": dict(sorted(Counter(f.get("category", "") for f in findings).items())),
    }
