"""Structured progress events shared by analysis engines and presentation layers."""

from __future__ import annotations

from typing import Any

_EVENT_KIND = "analysis_component"
_VALID_STATES = {"running", "completed", "partial", "failed", "no_data", "fallback"}


def append_component_progress(
    progress: list | None,
    *,
    component_id: str,
    label: str,
    status: str,
    detail: str = "",
    phase: str = "",
    current: int | None = None,
    total: int | None = None,
    unit: str = "",
    secondary_count: int | None = None,
    secondary_unit: str = "",
    read_only: bool = False,
) -> None:
    """Append an explicit lifecycle event to a shared progress list."""
    if progress is None:
        return
    if status not in _VALID_STATES:
        raise ValueError(f"unknown analysis progress status: {status}")
    event = {
        "kind": _EVENT_KIND,
        "component_id": component_id,
        "label": label,
        "status": status,
        "detail": detail,
    }
    if phase:
        event["phase"] = phase
    if current is not None:
        event["current"] = max(0, int(current))
    if total is not None:
        event["total"] = max(0, int(total))
    if unit:
        event["unit"] = unit
    if secondary_count is not None:
        event["secondary_count"] = max(0, int(secondary_count))
    if secondary_unit:
        event["secondary_unit"] = secondary_unit
    if read_only:
        event["read_only"] = True
    progress.append(event)


def is_component_progress(item: Any) -> bool:
    """Return whether ``item`` is a well-formed component lifecycle event."""
    return (
        isinstance(item, dict)
        and item.get("kind") == _EVENT_KIND
        and isinstance(item.get("component_id"), str)
        and isinstance(item.get("label"), str)
        and item.get("status") in _VALID_STATES
    )


def format_analysis_progress(item: Any) -> str:
    """Turn either a lifecycle event or a legacy string into user-facing text."""
    if not is_component_progress(item):
        return str(item)
    label = item["label"]
    status = item["status"]
    detail = str(item.get("detail", "") or "")
    suffix = f": {detail}" if detail else ""
    return f"{label} — {status}{suffix}"
