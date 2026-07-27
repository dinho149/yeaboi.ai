from types import SimpleNamespace


def test_execution_snapshot_tracks_calls_cache_and_eta(monkeypatch):
    from yeaboi.analysis.llm_runtime import (
        analysis_llm_slot,
        get_analysis_llm_execution,
        record_analysis_cache_hit,
        record_analysis_input,
        reset_analysis_llm_execution,
    )

    monkeypatch.setattr("yeaboi.config.get_llm_provider", lambda: "anthropic")
    reset_analysis_llm_execution()
    record_analysis_input(records=10)
    record_analysis_cache_hit(records=4)
    with analysis_llm_slot("ticket_classification", model="fast", records=3):
        pass

    result = get_analysis_llm_execution()
    assert result["calls"] == 1
    assert result["cache_hits"] == 1
    assert result["completed_records"] == 7
    assert result["models"] == ["fast"]
    assert result["tasks"]["ticket_classification"]["records"] == 3


def test_adaptive_gate_reduces_after_throttle():
    from yeaboi.analysis.llm_runtime import _AdaptiveGate

    gate = _AdaptiveGate(6)
    gate.acquire()
    gate.release(throttled=True)
    assert gate.limit == 5
    for _ in range(8):
        gate.acquire()
        gate.release()
    assert gate.limit == 6


def test_ollama_preflight_offers_smaller_installed_model(tmp_path, monkeypatch):
    from yeaboi.analysis.llm_runtime import get_ollama_analysis_preflight
    from yeaboi.sessions import SessionStore

    db = tmp_path / "sessions.db"
    with SessionStore(db) as store:
        store.record_token_usage(
            100,
            50,
            model="qwen3:14b",
            provider="ollama",
            duration_ms=180_000,
            eval_duration_ms=170_000,
            tokens_per_sec=5,
        )
    monkeypatch.setattr("yeaboi.config.get_llm_provider", lambda: "ollama")
    monkeypatch.setattr("yeaboi.config.get_llm_model", lambda: "qwen3:14b")
    monkeypatch.setattr("yeaboi.config.get_ollama_base_url", lambda: "http://ollama")
    monkeypatch.setattr(
        "httpx.get",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {
                "models": [
                    {"name": "qwen3:14b", "size": 9_000_000_000},
                    {"name": "qwen3:4b", "size": 3_000_000_000},
                ]
            }
        ),
    )

    result = get_ollama_analysis_preflight(db, estimated_records=100)

    assert result["offer"] is True
    assert result["recommended_model"] == "qwen3:4b"
    assert result["predicted_seconds"] > 600
