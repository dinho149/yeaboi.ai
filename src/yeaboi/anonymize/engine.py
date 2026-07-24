"""Anonymize engine — mask PII & company-specific data before public sharing.

Like the reporting / standup / roadmap engines, this is a standalone pipeline (NOT a
LangGraph node): a deterministic seed-mask pass + a single LLM call following the same
parse → fallback → format convention the graph nodes use (agent/nodes.py). An LLM
auth/billing failure is never re-raised — it becomes a user-facing *warning* and the
seed-masked text is returned, so the caller always gets something safe(r) to review.

Pipeline (``run_anonymize(text)``):
  collect seed terms (config) → literal-replace them (always) → one LLM "generalize
  the masking" call → parse → on failure fall back to the seed-masked text → return
  an ``AnonymizedOutput`` carrying the masked text, the replacement map, and warnings.

Why seed first, then LLM: the seed pass guarantees the obvious company identifiers
(Jira project key, team/org names, the ANONYMIZE_MASK_TERMS list) are always redacted
even offline; the LLM then catches the PII a static list can't know (people's names,
other project names, internal tools).

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the anonymize prompt (untrusted-data framing)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from yeaboi.agent.state import AnonymizedOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared LLM helpers (parse → fallback) — mirrors reporting/engine.py
# ---------------------------------------------------------------------------


def _parse_json_response(raw: str) -> dict:
    """Extract a JSON object from an LLM response, tolerating markdown fences."""
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
        logger.warning("anonymize: could not parse LLM JSON response")
        return {}


def _invoke_llm(prompt: str) -> tuple[dict, list[str]]:
    """Run one LLM call for ``prompt``; return (parsed_json, warnings).

    Returns ({}, [warning]) on any non-configured / auth / request failure so the
    caller falls back to the deterministic seed-masked text — the engine never
    crashes on LLM issues.
    """
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("anonymize: LLM not configured (%s)", why)
        return {}, [f"AI masking unavailable — {why}. Only known company terms were masked; review manually."]

    # invoke_json tracks usage + turns on JSON mode + re-asks once on bad JSON.
    # See docs: "Local Mode (Ollama)" — reliability layer.
    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint

    try:
        logger.info("anonymize: invoking LLM masking pass")
        response = invoke_json(prompt, temperature=0.2)
        return _parse_json_response(response.content), []
    except Exception as exc:  # noqa: BLE001 — turn any LLM failure into a warning + fallback
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("anonymize: LLM auth/billing error: %s", exc)
            return {}, [
                "AI masking unavailable — API key invalid or billing issue. "
                "Only known company terms were masked; review manually."
            ]
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("anonymize: local Ollama failure: %s", exc)
            return {}, [f"AI masking unavailable — {local_hint} Only known company terms were masked; review manually."]
        logger.warning("anonymize: LLM request failed: %s", exc)
        return {}, [
            "AI masking unavailable — LLM request failed (see logs). "
            "Only known company terms were masked; review manually."
        ]


# ---------------------------------------------------------------------------
# Seed masking (deterministic) — always runs, LLM or not
# ---------------------------------------------------------------------------


def _host_of(url: str | None) -> str:
    """Return the bare host of a URL (e.g. 'acme.atlassian.net'), or ''."""
    if not url:
        return ""
    m = re.search(r"https?://([^/]+)", url.strip())
    return m.group(1) if m else url.strip()


def _collect_seed_terms(
    *,
    extra_mask_terms: tuple[str, ...] = (),
    keep_terms: tuple[str, ...] = (),
    project_name: str = "",
) -> tuple[str, ...]:
    """Gather the known company identifiers to literal-mask before the LLM runs.

    Sources: the tracker/wiki config (Jira project key + host, Azure DevOps org /
    project / team, Confluence space key), the active session's project name, the
    ANONYMIZE_MASK_TERMS env list, and any caller-supplied ``extra_mask_terms``.
    Anything in ``keep_terms`` (the user marked it public/safe) is excluded.
    """
    from yeaboi import config

    raw: list[str] = [
        config.get_jira_project_key() or "",
        _host_of(config.get_jira_base_url()),
        config.get_azure_devops_project() or "",
        config.get_azure_devops_team() or "",
        _host_of(config.get_azure_devops_org_url()),
        config.get_confluence_space_key() or "",
        project_name or "",
    ]
    raw.extend(config.get_anonymize_mask_terms())
    raw.extend(extra_mask_terms)

    keep_lower = {k.strip().lower() for k in keep_terms if k.strip()}
    seen: set[str] = set()
    terms: list[str] = []
    for term in raw:
        term = (term or "").strip()
        # Skip empties, too-short (single chars mangle unrelated text), kept, and dupes.
        if len(term) < 2 or term.lower() in keep_lower or term.lower() in seen:
            continue
        seen.add(term.lower())
        terms.append(term)
    # Longest first so "Acme Payments" is masked before the substring "Acme".
    terms.sort(key=len, reverse=True)
    return tuple(terms)


def _seed_placeholder(term: str) -> str:
    """A neutral placeholder for a literal-masked company term."""
    return "[COMPANY]" if " " not in term and term.isalpha() and len(term) <= 12 else "[REDACTED]"


def _apply_seed_mask(text: str, seed_terms: tuple[str, ...]) -> tuple[str, list[tuple[str, str]]]:
    """Literal-replace each seed term (case-insensitive, word-ish boundaries).

    Returns the masked text and the (original, placeholder) pairs actually applied.
    Uses a boundary-aware regex so a term embedded in a larger identifier isn't half
    replaced; matching is case-insensitive because trackers echo mixed casing.
    """
    replacements: list[tuple[str, str]] = []
    for term in seed_terms:
        placeholder = _seed_placeholder(term)
        # (?<!\w) / (?!\w) approximate word boundaries but also fire around punctuation
        # in hostnames/keys (acme.atlassian.net, ACME-123) where \b would not.
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        new_text, n = pattern.subn(placeholder, text)
        if n:
            text = new_text
            replacements.append((term, placeholder))
    return text, replacements


# ---------------------------------------------------------------------------
# Parse the LLM replacement map
# ---------------------------------------------------------------------------


def _parse_replacements(value) -> tuple[tuple[str, str], ...]:
    """Coerce the LLM 'replacements' field into ((original, placeholder), ...)."""
    if not isinstance(value, list):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original", "")).strip()
        placeholder = str(item.get("placeholder", "")).strip()
        if original and placeholder:
            pairs.append((original, placeholder))
    return tuple(pairs)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_anonymize(
    text: str,
    *,
    instruction: str = "",
    extra_mask_terms: tuple[str, ...] = (),
    keep_terms: tuple[str, ...] = (),
    project_name: str = "",
    source_mode: str = "",
    db_path=None,
    today: date | None = None,
    on_progress=None,
) -> AnonymizedOutput:
    """Mask PII & company-specific data in ``text`` for public sharing.

    Runs a deterministic seed-mask of known company identifiers first (always), then a
    single LLM call to generalize the masking to PII a static list can't know. On any
    LLM failure the seed-masked text is returned with a warning — never raises.

    Args:
        text: the already-generated Markdown to anonymize (from a mode's Export doc).
        instruction: optional free-text adjustment — "also mask X" / "don't mask Y".
        extra_mask_terms: caller-supplied terms to always mask (seed pass).
        keep_terms: terms the user marked public/safe — excluded from seed masking and
            passed to the LLM via the instruction so it leaves them alone.
        project_name: active project name, seeded into the mask list.
        source_mode: which mode produced the input (recorded on the artifact).
        db_path / today: injection seams for tests.
        on_progress: optional callable(str) for the TUI loading screen.
    """

    def _report(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:  # noqa: BLE001 — a progress-UI bug must never break the pipeline
                logger.debug("anonymize: on_progress callback raised", exc_info=True)

    today = today or date.today()
    generated_at = today.isoformat()
    logger.info("run_anonymize: %d chars, mode=%s, instruction=%s", len(text or ""), source_mode, bool(instruction))

    if not (text or "").strip():
        return AnonymizedOutput(
            source_mode=source_mode,
            warnings=("Nothing to anonymize — the output was empty.",),
            generated_at=generated_at,
        )

    _report("Masking known company terms…")
    seed_terms = _collect_seed_terms(
        extra_mask_terms=extra_mask_terms, keep_terms=keep_terms, project_name=project_name
    )
    seeded_text, seed_replacements = _apply_seed_mask(text, seed_terms)

    _report("Masking sensitive data with the AI…")
    from yeaboi.prompts.anonymize import get_anonymize_prompt

    # Fold keep_terms into the instruction so the LLM knows not to re-mask them.
    llm_instruction = instruction.strip()
    if keep_terms:
        keep_note = "Leave these terms UNMASKED (they are public/safe): " + ", ".join(keep_terms) + "."
        llm_instruction = f"{llm_instruction}\n{keep_note}".strip() if llm_instruction else keep_note

    prompt = get_anonymize_prompt(seeded_text, seed_terms=seed_terms, instruction=llm_instruction)
    parsed, warnings = _invoke_llm(prompt)

    anonymized = str(parsed.get("anonymized_text", "")).strip() if parsed else ""
    if not anonymized:
        # LLM unavailable or returned nothing usable → the seed-masked text is our best,
        # always-safe fallback. A partial mask beats raising or exposing the original.
        if parsed and not warnings:
            warnings = ["AI returned no usable masking — showing the deterministic company-term mask only."]
        result = AnonymizedOutput(
            anonymized_text=seeded_text,
            replacements=tuple(seed_replacements),
            source_mode=source_mode,
            warnings=tuple(warnings),
            generated_at=generated_at,
        )
        logger.info("run_anonymize fallback: seed-only mask (%d terms)", len(seed_replacements))
        return result

    llm_replacements = _parse_replacements(parsed.get("replacements"))
    # Deduplicate seed + LLM replacements by original, preserving order.
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for original, placeholder in list(seed_replacements) + list(llm_replacements):
        if original.lower() in seen:
            continue
        seen.add(original.lower())
        merged.append((original, placeholder))

    result = AnonymizedOutput(
        anonymized_text=anonymized,
        replacements=tuple(merged),
        source_mode=source_mode,
        warnings=tuple(warnings),
        generated_at=generated_at,
    )
    logger.info("run_anonymize complete: %d replacements, %d warnings", len(merged), len(warnings))
    return result
