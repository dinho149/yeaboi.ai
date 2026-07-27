"""Provider-aware scheduling and telemetry for Analysis-mode LLM calls."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from contextlib import contextmanager

_lock = threading.Lock()
_started_at = 0.0
_stats: dict[str, object] = {}
_selected_model: str | None = None


class _AdaptiveGate:
    def __init__(self, limit: int) -> None:
        self.max_limit = limit
        self.limit = limit
        self.active = 0
        self.successes = 0
        self.condition = threading.Condition()

    def acquire(self) -> None:
        with self.condition:
            while self.active >= self.limit:
                self.condition.wait()
            self.active += 1

    def release(self, *, throttled: bool = False) -> None:
        with self.condition:
            self.active = max(0, self.active - 1)
            if throttled:
                self.limit = max(1, self.limit - 1)
                self.successes = 0
            else:
                self.successes += 1
                if self.successes >= 8 and self.limit < self.max_limit:
                    self.limit += 1
                    self.successes = 0
            self.condition.notify_all()


_cloud_gate: _AdaptiveGate | None = None
_cloud_limit = 0
_local_gate = _AdaptiveGate(1)


def reset_analysis_llm_execution(*, model: str | None = None) -> None:
    """Start a fresh process-local Analysis execution window."""
    global _selected_model, _started_at, _stats
    with _lock:
        _selected_model = model
        _started_at = time.monotonic()
        _stats = {
            "calls": 0,
            "cache_hits": 0,
            "input_records": 0,
            "completed_records": 0,
            "retries": 0,
            "failures": 0,
            "degraded_records": 0,
            "queue_seconds": 0.0,
            "call_seconds": 0.0,
            "tasks": defaultdict(lambda: {"calls": 0, "seconds": 0.0, "records": 0}),
            "models": set(),
        }
    for gate in (_cloud_gate, _local_gate):
        if gate is not None:
            with gate.condition:
                gate.limit = gate.max_limit
                gate.successes = 0
                gate.condition.notify_all()


def get_selected_analysis_model() -> str | None:
    return _selected_model


def get_ollama_analysis_preflight(db_path, *, estimated_records: int = 100) -> dict:
    """Predict a typical Deep run and identify a smaller installed model."""
    from yeaboi.config import (
        get_llm_model,
        get_llm_provider,
        get_ollama_base_url,
        get_team_analysis_llm_target_seconds,
    )

    if get_llm_provider() != "ollama":
        return {}
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(db_path) as store:
            perf = store.get_local_perf_summary()
    except Exception:
        return {}
    avg_ms = float(perf.get("avg_duration_ms", 0.0))
    if avg_ms <= 0:
        return {"model": get_llm_model(), "predicted_seconds": 0, "offer": False}
    predicted = (math.ceil(max(estimated_records, 1) / 24) + 2) * avg_ms / 1000
    target = get_team_analysis_llm_target_seconds()
    result = {
        "model": get_llm_model(),
        "predicted_seconds": round(predicted),
        "target_seconds": target,
        "offer": predicted > target,
        "recommended_model": "",
        "installed_models": [],
    }
    if not result["offer"]:
        return result
    try:
        import httpx

        response = httpx.get(f"{get_ollama_base_url().rstrip('/')}/api/tags", timeout=5)
        models = [
            item
            for item in (response.json().get("models") or [])
            if isinstance(item, dict) and item.get("name") and "embed" not in item["name"].lower()
        ]
        result["installed_models"] = [item["name"] for item in models]
        current = get_llm_model()
        current_size = next((int(item.get("size", 0)) for item in models if item["name"] == current), 0)
        candidates = [
            item
            for item in models
            if item["name"] != current
            and int(item.get("size", 0)) >= 2_000_000_000
            and (not current_size or int(item.get("size", 0)) < current_size)
        ]
        if candidates:
            result["recommended_model"] = min(candidates, key=lambda item: int(item.get("size", 0)))["name"]
    except Exception:
        pass
    result["offer"] = bool(result["recommended_model"])
    return result


def _ensure_cloud_gate() -> _AdaptiveGate:
    global _cloud_gate, _cloud_limit
    from yeaboi.config import get_team_analysis_llm_max_concurrency

    limit = get_team_analysis_llm_max_concurrency()
    with _lock:
        if _cloud_gate is None or _cloud_limit != limit:
            _cloud_gate = _AdaptiveGate(limit)
            _cloud_limit = limit
        return _cloud_gate


@contextmanager
def analysis_llm_slot(task: str, *, model: str = "", records: int = 0):
    """Bound provider concurrency and record queue/call wall time."""
    from yeaboi.config import get_llm_provider

    gate = _local_gate if get_llm_provider() == "ollama" else _ensure_cloud_gate()
    queued = time.monotonic()
    gate.acquire()
    acquired = time.monotonic()
    throttled = False
    try:
        yield
    except Exception as exc:
        message = str(exc).lower()
        throttled = any(marker in message for marker in ("429", "rate limit", "throttl", "too many requests"))
        with _lock:
            _stats["failures"] = int(_stats.get("failures", 0)) + 1
        raise
    finally:
        finished = time.monotonic()
        gate.release(throttled=throttled)
        with _lock:
            _stats["calls"] = int(_stats.get("calls", 0)) + 1
            _stats["queue_seconds"] = float(_stats.get("queue_seconds", 0.0)) + acquired - queued
            _stats["call_seconds"] = float(_stats.get("call_seconds", 0.0)) + finished - acquired
            _stats["completed_records"] = int(_stats.get("completed_records", 0)) + max(records, 0)
            tasks = _stats["tasks"]
            entry = tasks[task]
            entry["calls"] += 1
            entry["seconds"] += finished - acquired
            entry["records"] += max(records, 0)
            if model:
                _stats["models"].add(model)


def record_analysis_cache_hit(*, records: int = 1) -> None:
    with _lock:
        _stats["cache_hits"] = int(_stats.get("cache_hits", 0)) + 1
        _stats["completed_records"] = int(_stats.get("completed_records", 0)) + max(records, 0)


def record_analysis_input(*, records: int) -> None:
    with _lock:
        _stats["input_records"] = int(_stats.get("input_records", 0)) + max(records, 0)


def record_analysis_completed(*, records: int) -> None:
    with _lock:
        _stats["completed_records"] = int(_stats.get("completed_records", 0)) + max(records, 0)


def record_analysis_degraded(*, records: int) -> None:
    with _lock:
        count = max(records, 0)
        _stats["completed_records"] = int(_stats.get("completed_records", 0)) + count
        _stats["degraded_records"] = int(_stats.get("degraded_records", 0)) + count


def record_analysis_retry() -> None:
    with _lock:
        _stats["retries"] = int(_stats.get("retries", 0)) + 1


def get_analysis_llm_execution() -> dict:
    """Return a JSON-safe snapshot with an evidence-based ETA."""
    from yeaboi.config import get_team_analysis_llm_target_seconds

    with _lock:
        stats = dict(_stats)
        tasks = {key: dict(value) for key, value in (_stats.get("tasks") or {}).items()}
        models = sorted(_stats.get("models") or ())
        started = _started_at
    elapsed = max(0.0, time.monotonic() - started) if started else 0.0
    total = int(stats.get("input_records", 0))
    complete = int(stats.get("completed_records", 0))
    eta = 0.0
    if 0 < complete < total:
        eta = elapsed / complete * (total - complete)
    return {
        "calls": int(stats.get("calls", 0)),
        "cache_hits": int(stats.get("cache_hits", 0)),
        "input_records": total,
        "completed_records": complete,
        "retries": int(stats.get("retries", 0)),
        "failures": int(stats.get("failures", 0)),
        "degraded_records": int(stats.get("degraded_records", 0)),
        "queue_seconds": round(float(stats.get("queue_seconds", 0.0)), 2),
        "call_seconds": round(float(stats.get("call_seconds", 0.0)), 2),
        "elapsed_seconds": round(elapsed, 2),
        "eta_seconds": round(eta, 2),
        "target_seconds": get_team_analysis_llm_target_seconds(),
        "tasks": tasks,
        "models": models,
    }


reset_analysis_llm_execution()
