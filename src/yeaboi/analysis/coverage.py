"""Shared exhaustive-scan coverage accounting.

Analysis collectors use this instead of silently applying provider caps.  Every
discovered asset receives one terminal state and the overall run is only
``complete`` when no eligible asset failed, was inaccessible, or was truncated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# One mid-scan network loss produces dozens of identical exceptions differing
# only by URL/sha/id. Grouping must key on the *shape* of the error, not its
# raw text, or every failure renders as its own coverage note.
_DETAIL_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"/[\w][\w./~%-]*_apis/\S+"), "<api-path>"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<id>"),
    (re.compile(r"\b\d{4,}\b"), "<n>"),
)
_DETAIL_MAX_LEN = 200


def _normalize_detail(detail: str) -> str:
    """Strip volatile parts (URLs, shas, ids) so repeated errors group as one."""
    text = detail or ""
    for pattern, replacement in _DETAIL_SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _DETAIL_MAX_LEN:
        text = text[: _DETAIL_MAX_LEN - 1] + "…"
    return text


@dataclass
class CoverageTracker:
    component: str
    window_days: int
    assets: list[dict] = field(default_factory=list)

    def add(
        self,
        provider: str,
        container: str,
        asset: str,
        status: str,
        detail: str = "",
        *,
        eligible: bool = True,
    ) -> None:
        self.assets.append(
            {
                "provider": provider,
                "container": container,
                "asset": asset,
                "status": status,
                "detail": detail,
                "eligible": eligible,
            }
        )

    def as_dict(self) -> dict:
        counts = {
            "discovered": len(self.assets),
            "eligible": sum(bool(a["eligible"]) for a in self.assets),
            "attempted": sum(a["status"] in {"succeeded", "failed", "truncated"} for a in self.assets),
            "succeeded": sum(a["status"] == "succeeded" for a in self.assets),
            "cached": sum(a["status"] == "cached" for a in self.assets),
            "failed": sum(a["status"] == "failed" for a in self.assets),
            "unchanged": sum(a["status"] == "unchanged" for a in self.assets),
            "inaccessible": sum(a["status"] == "inaccessible" for a in self.assets),
            "truncated": sum(a["status"] == "truncated" for a in self.assets),
        }
        counts["completed"] = counts["succeeded"] + counts["cached"]
        gap_count = counts["failed"] + counts["inaccessible"] + counts["truncated"]
        if gap_count and counts["completed"] == 0:
            status = "failed"
        elif gap_count:
            status = "partial"
        elif counts["completed"] == 0:
            status = "no_data"
        else:
            status = "complete"
        per_container: dict[str, dict[str, int]] = {}
        for asset in self.assets:
            key = f"{asset['provider']}:{asset['container']}"
            bucket = per_container.setdefault(
                key,
                {"discovered": 0, "succeeded": 0, "cached": 0, "failed": 0, "unchanged": 0},
            )
            bucket["discovered"] += 1
            asset_status = asset["status"]
            if asset_status == "succeeded":
                bucket["succeeded"] += 1
            elif asset_status == "cached":
                bucket["cached"] += 1
            elif asset_status == "unchanged":
                bucket["unchanged"] += 1
            elif asset_status in {"failed", "inaccessible", "truncated"}:
                bucket["failed"] += 1
        grouped: dict[tuple[str, str, str], dict] = {}
        for asset in self.assets:
            if asset["status"] not in {"failed", "inaccessible", "truncated"}:
                continue
            detail = _normalize_detail(asset["detail"]) or asset["status"]
            key = (asset["provider"], asset["status"], detail)
            group = grouped.setdefault(
                key,
                {
                    "provider": asset["provider"],
                    "status": asset["status"],
                    "detail": detail,
                    "count": 0,
                    "containers": set(),
                    "examples": [],
                },
            )
            group["count"] += 1
            group["containers"].add(asset["container"])
            if len(group["examples"]) < 3:
                group["examples"].append(asset["asset"])
        grouped_errors = [
            {
                **group,
                "containers": sorted(group["containers"]),
            }
            for group in grouped.values()
        ]
        return {
            "component": self.component,
            "status": status,
            "has_data": counts["completed"] > 0,
            "completion_pct": round(counts["completed"] / counts["eligible"] * 100, 1) if counts["eligible"] else 100.0,
            "window_days": self.window_days,
            **counts,
            "per_container": per_container,
            "grouped_errors": grouped_errors,
            "assets": list(self.assets),
        }


def coverage_notes(coverage: dict) -> list[str]:
    """Human-readable gaps for legacy renderers."""
    notes: list[str] = []
    grouped = coverage.get("grouped_errors")
    if not isinstance(grouped, list):
        grouped = CoverageTracker(
            str(coverage.get("component", "")),
            int(coverage.get("window_days", 0) or 0),
            list(coverage.get("assets", [])),
        ).as_dict()["grouped_errors"]
    for error in grouped:
        status = error.get("status")
        label = "error" if status in {"failed", "inaccessible"} else "truncated"
        count = int(error.get("count", 1) or 1)
        containers = error.get("containers") or []
        scope = f" across {len(containers)} container(s)" if containers else ""
        notes.append(
            f"{error.get('provider', '')}: {label} ({count:,} item(s){scope}: {error.get('detail') or status})"
        )
    return notes
