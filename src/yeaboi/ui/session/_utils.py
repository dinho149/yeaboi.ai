"""Text rendering and threading utilities for the TUI session.

# See docs: "Architecture" — utility layer supporting session screens and phases.
# Contains text wrapping, Rich-to-lines rendering, graph invocation threading,
# and rate-limit retry logic.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from io import StringIO
from typing import Any

import anthropic
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from yeaboi.ui.shared._animations import FRAME_TIME_30FPS, loading_border_color
from yeaboi.ui.shared._screensaver import suppress_during_call

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 5  # seconds


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _wrap_text(text: str, max_width: int) -> list[str]:
    """Simple word-wrap that respects existing newlines."""
    result: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            result.append("")
            continue
        words = paragraph.split()
        current_line = ""
        for word in words:
            if current_line and len(current_line) + 1 + len(word) > max_width:
                result.append(current_line)
                current_line = word
            elif current_line:
                current_line += " " + word
            else:
                current_line = word
        if current_line:
            result.append(current_line)
    return result


def _pad_left(renderable, pad: int = 4) -> Any:
    """Wrap a renderable with left padding matching _PAD."""
    from rich.padding import Padding

    return Padding(renderable, (0, 0, 0, pad))


def _render_tui_intake_summary(qs, table_width: int) -> Group:
    """Render intake summary for the TUI — cleaner than the REPL version.

    Differences from formatters.render_intake_summary():
    - No stats line (answered/extracted/defaults counts)
    - Phase titles are left-aligned, without "Phase N:" prefix
    - All tables share a uniform width
    """
    from yeaboi.agent.state import PHASE_QUESTION_RANGES
    from yeaboi.formatters import _source_tag, _truncate
    from yeaboi.prompts.intake import PHASE_LABELS, QUESTION_SHORT_LABELS

    # Strip "Phase N: " prefix from labels → just "Project Context" etc.
    _clean_label = {k: re.sub(r"^Phase \d+a?: ", "", v) for k, v in PHASE_LABELS.items()}

    # Section titles sit at 2-space indent (left of the Q# column) to
    # visually distinguish them from question labels which start after the
    # Q# column (width=4 + 1 padding = ~6 chars in).
    title_indent = "  "

    tables: list = []
    for phase, (start, end) in PHASE_QUESTION_RANGES.items():
        label = _clean_label.get(phase, str(phase))
        # Section title: dimmer colour + underline-like appearance to contrast
        # with bold white question labels in the table rows.
        tables.append(Text(title_indent + label, style="bold rgb(70,100,180)"))

        table = Table(
            show_lines=False,
            show_edge=False,
            show_header=False,
            box=None,
            padding=(0, 1),
            width=table_width,
        )
        table.add_column("Q#", style="dim", justify="right", no_wrap=True, width=4)
        table.add_column("Question", style="bold", width=20)
        table.add_column("Answer", ratio=2)
        table.add_column("Source", no_wrap=True, width=18)

        for q_num in range(start, end + 1):
            answer = qs.answers.get(q_num, "")
            short_label = QUESTION_SHORT_LABELS.get(q_num, f"Q{q_num}")
            table.add_row(
                str(q_num),
                short_label,
                _truncate(answer) if answer else Text("(not answered)", style="dim"),
                _source_tag(q_num, qs),
            )

        tables.append(table)
        tables.append(Text(""))  # blank line between sections

    # Remove trailing blank after the last section
    if tables and isinstance(tables[-1], Text):
        tables.pop()

    return Group(*tables)


def _render_to_lines(console: Console, renderable, width: int) -> list[str]:
    """Render a Rich renderable to plain text lines for scrollable viewport.

    # See docs: "Architecture" — scrollable viewport pattern from provider_select.py
    # Renders to a string buffer console, then splits into lines.
    """
    buf = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
    buf.print(renderable)
    return buf.file.getvalue().splitlines()


# ---------------------------------------------------------------------------
# API error classification
# ---------------------------------------------------------------------------


def _extract_status_code(err: Exception) -> int | None:
    """Best-effort extraction of an HTTP status code from any exception.

    Works across SDKs without importing them: checks common attributes
    (``status_code``, ``status``), a nested ``response`` object, and finally
    parses an ``HTTP <code>`` token from the message (e.g. JIRAError's text).
    """
    for attr in ("status_code", "status"):
        val = getattr(err, attr, None)
        if isinstance(val, int) and not isinstance(val, bool):
            return val
    resp = getattr(err, "response", None)
    if resp is not None:
        for attr in ("status_code", "status"):
            val = getattr(resp, attr, None)
            if isinstance(val, int) and not isinstance(val, bool):
                return val
    match = re.search(r"\bHTTP[\s:]+(\d{3})\b", str(err))
    if match:
        return int(match.group(1))
    return None


def _classify_api_error(err: Exception) -> str:
    """Return a short, user-friendly message for any API/integration error.

    Used by TUI error handlers so users see actionable, one-line feedback
    instead of a raw exception dump. This is the single place that turns SDK
    exceptions (Anthropic, Jira, Azure DevOps, GitHub, OpenAI, …) into human
    text — call it everywhere an external-service error can reach the screen,
    never render ``str(exc)`` directly (a JIRAError, for example, stringifies to
    its entire HTTP response including every header).

    Optional-dependency SDKs are matched by class/module name + status code
    rather than isinstance, so this module never has to import them.
    """
    # Local Ollama first — its failures carry generic shapes (httpx.ConnectError,
    # a 404 ResponseError) that the branches below would mis-classify. The hint
    # helper is provider-gated, so this is a no-op for every cloud provider.
    # See README: "Local Mode (Ollama)" — reliability layer.
    from yeaboi.agent.nodes import _local_llm_hint

    local_hint = _local_llm_hint(err)
    if local_hint:
        return local_hint

    # Anthropic — the default provider, always installed.
    if isinstance(err, anthropic.AuthenticationError):
        return "Authentication failed — check your ANTHROPIC_API_KEY in .env (may be missing, expired, or invalid)."
    if isinstance(err, anthropic.RateLimitError):
        return "Rate limited — too many requests. Wait a moment and try again."
    if isinstance(err, anthropic.APIConnectionError):
        return "Network error — check your internet connection and try again."
    if isinstance(err, anthropic.APIStatusError):
        msg = getattr(err, "message", str(err))
        if "credit balance" in msg.lower() or "billing" in msg.lower():
            return "Insufficient API credits — visit Plans & Billing at console.anthropic.com to add credits."
        return f"API error (status {err.status_code}): {msg}"

    name = type(err).__name__
    module = (type(err).__module__ or "").lower()
    status = _extract_status_code(err)
    is_auth = status in (401, 403)

    # Jira (jira.exceptions.JIRAError) — its str() is a full HTTP dump, so we
    # build the message from the status code and never fall through to str(err).
    if "jira" in module or name == "JIRAError":
        if is_auth:
            return (
                "Jira authentication failed — check your Jira URL, email, and API token "
                "(Settings ▸ Issue Tracking, or JIRA_* in .env)."
            )
        if status == 404:
            return "Jira project or board not found — check your JIRA_PROJECT_KEY."
        return f"Jira request failed{f' (HTTP {status})' if status else ''} — check your Jira configuration."

    # Azure DevOps (azure.devops.exceptions.*) — often lacks a status code.
    if "azure" in module or "azuredevops" in name.lower():
        if is_auth:
            return (
                "Azure DevOps authentication failed — check your personal access token and organisation URL "
                "(Settings ▸ Issue Tracking, or AZURE_DEVOPS_* in .env)."
            )
        return "Azure DevOps request failed — check your Azure DevOps configuration."

    # GitHub (github.GithubException / BadCredentialsException).
    if "github" in module or name in ("GithubException", "BadCredentialsException"):
        if is_auth:
            return "GitHub authentication failed — check your GITHUB_TOKEN."
        return f"GitHub request failed{f' (HTTP {status})' if status else ''} — check your GitHub configuration."

    # Any other SDK, matched purely by status code (OpenAI, Google, requests…).
    if is_auth:
        return "Authentication failed — check the API key/token for this service in Settings."
    if status == 429:
        return "Rate limited — too many requests. Wait a moment and try again."
    if "connection" in name.lower() or "timeout" in name.lower():
        return "Network error — check your internet connection and try again."

    # Fallback — bound the length and keep only the first line so a stray dump
    # (headers, HTML body) can never flood or break the TUI layout.
    text = str(err).strip()
    first_line = text.splitlines()[0] if text else name
    if len(first_line) > 200:
        first_line = first_line[:197] + "…"
    return f"Unexpected error: {first_line}"


# ---------------------------------------------------------------------------
# Threading helpers
# ---------------------------------------------------------------------------


def _invoke_graph_thread(graph, state: dict, result_box: list) -> None:
    """Run graph.invoke() in a background thread.

    result_box is a 2-element list [result, error]. On success, result_box[0]
    is set to the graph result. On error, result_box[1] is set to the exception.
    """
    logger.debug("_invoke_graph_thread: starting graph.invoke, msgs=%d", len(state.get("messages", [])))
    try:
        result_box[0] = graph.invoke(state)
        logger.debug("_invoke_graph_thread: completed successfully")
    except Exception as e:
        logger.error("_invoke_graph_thread: error %s", type(e).__name__)
        result_box[1] = e


def _handle_rate_limit_tui(graph, invoke_state: dict, result_box: list) -> bool:
    """Retry graph.invoke() with exponential backoff after rate-limit.

    Returns True if a retry succeeded (result in result_box[0]), False if exhausted.
    """
    for attempt in range(1, _RATE_LIMIT_MAX_RETRIES + 1):
        delay = _RATE_LIMIT_BASE_DELAY * (2 ** (attempt - 1))
        logger.warning("Rate limit retry %d/%d, delay=%ds", attempt, _RATE_LIMIT_MAX_RETRIES, delay)
        time.sleep(delay)
        try:
            result_box[0] = graph.invoke(invoke_state)
            result_box[1] = None
            logger.info("Rate limit retry %d succeeded", attempt)
            return True
        except anthropic.RateLimitError:
            continue
    logger.warning("Rate limit retries exhausted")
    return False


# ---------------------------------------------------------------------------
# Graph invocation with pulsing border animation
# ---------------------------------------------------------------------------


@suppress_during_call
def _invoke_with_animation(
    live,
    console: Console,
    graph,
    invoke_state: dict,
    stage_label: str,
    progress: str,
    *,
    question_screen_kwargs: dict | None = None,
    step: int = 0,
    total: int = 5,
) -> dict | None:
    """Invoke graph in a background thread with pulsing animation.

    If question_screen_kwargs is provided, the loading animation is rendered
    on the *input box* of the question screen (green/white cycling border)
    instead of showing a separate pipeline processing screen.

    Returns the graph result dict, or None on unrecoverable error.
    """
    from yeaboi.ui.session.screens._screens_input import _build_question_screen
    from yeaboi.ui.session.screens._screens_pipeline import _build_pipeline_screen

    logger.info("_invoke_with_animation: stage=%s progress=%s", stage_label, progress)
    result_box: list = [None, None]
    thread = threading.Thread(
        target=_invoke_graph_thread,
        args=(graph, invoke_state, result_box),
        daemon=True,
    )
    thread.start()

    start = time.monotonic()
    while thread.is_alive():
        tick = time.monotonic() - start
        w, h = console.size
        if question_screen_kwargs is not None:
            # Animate the input box border with green/white cycling.
            # Use accordion screen if questionnaire state is provided.
            if "questionnaire" in question_screen_kwargs:
                from yeaboi.ui.session.screens._accordion import _build_accordion_question_screen

                live.update(
                    _build_accordion_question_screen(
                        **question_screen_kwargs,
                        width=w,
                        height=h,
                        border_override=loading_border_color(tick),
                        shimmer_tick=tick,
                    )
                )
            else:
                live.update(
                    _build_question_screen(
                        **question_screen_kwargs,
                        width=w,
                        height=h,
                        border_override=loading_border_color(tick),
                        shimmer_tick=tick,
                    )
                )
        else:
            live.update(
                _build_pipeline_screen(
                    stage_label,
                    progress,
                    [],
                    0,
                    0,
                    status="processing",
                    width=w,
                    height=h,
                    tick=tick,
                    step=step,
                    total=total,
                    shimmer_tick=tick,
                )
            )
        time.sleep(FRAME_TIME_30FPS)

    thread.join()

    if result_box[1] is not None:
        err = result_box[1]
        logger.error("_invoke_with_animation error: %s", type(err).__name__)
        if isinstance(err, anthropic.RateLimitError):
            if _handle_rate_limit_tui(graph, invoke_state, result_box):
                return result_box[0]
        # Show a user-friendly error message before returning None.
        # Without this, the TUI silently returns to the project select screen
        # on auth, billing, or network errors — leaving the user confused.
        error_msg = _classify_api_error(err)
        from rich.panel import Panel

        w, _ = console.size
        error_panel = Panel(
            Text(error_msg, style="bold red"),
            title="[bold red]Error[/bold red]",
            border_style="red",
            width=min(w - 4, 80),
            padding=(1, 2),
        )
        live.update(error_panel)
        # Hold the error screen for 4 seconds so the user can read it
        time.sleep(4)
        return None

    return result_box[0]
