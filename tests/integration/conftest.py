"""Integration test fixtures — shared autouse setup for all integration tests.

# See docs: "Guardrails" — three lines of defence (Input layer)

Why this file exists
--------------------
The REPL calls ``validate_input()`` on every user message *before* invoking the
graph.  ``validate_input`` runs four checks cheapest-first; the last one
(``check_off_topic``) can make a real Anthropic/OpenAI/Google LLM call for
inputs that don't match the static allowlist (e.g. "hello world").

``config.py`` calls ``load_dotenv()`` at module-import time, so
``ANTHROPIC_API_KEY`` is in ``os.environ`` for the entire pytest session.  When
the full suite runs, the LLM classifier is reachable and may return OFF_TOPIC
for test inputs like "hello world", blocking them before they reach the mocked
graph.  This causes 18 test_repl.py tests to fail in the full suite but pass in
isolation (where the LLM call either returns RELEVANT or raises and fails open).

The fix: patch ``validate_input`` to a no-op for every integration test so no
real LLM call can escape.  Integration tests exercise REPL/graph behaviour, not
guardrail logic — that is covered by tests/unit/guardrails/test_input_guardrails.py.
"""

import pytest


def pytest_collection_modifyitems(items):
    """Auto-apply the 'slow' marker to every test in tests/integration/.

    This lets developers run ``pytest -m 'not slow'`` (or ``make test-fast``)
    to skip integration tests during tight edit-test loops. The unit tests
    alone give < 3s feedback without needing graph compilation or multi-node
    mocking.
    """
    for item in items:
        if "/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.slow)


@pytest.fixture(autouse=True)
def _bypass_input_guardrails(monkeypatch):
    """Patch validate_input to always pass so tests never call the real LLM classifier.

    Applies automatically to every test in tests/integration/ via autouse=True.
    Uses monkeypatch so the original is restored after each test — no cross-test
    contamination.

    Why patch the *repl* namespace (not the source)?
    ``yeaboi/repl/__init__.py`` imports validate_input with
    ``from yeaboi.input_guardrails import validate_input``, which binds the
    name into the ``yeaboi.repl`` module namespace.  Patching
    ``yeaboi.repl.validate_input`` replaces *that* binding, so every call
    inside run_repl() sees the stub.  Patching the original module
    (``yeaboi.input_guardrails.validate_input``) would not affect the
    already-imported reference in the repl module.
    """
    monkeypatch.setattr("yeaboi.repl.validate_input", lambda text: None)
