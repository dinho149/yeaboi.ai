"""Feedback engine — turn an in-TUI bug report / feature request into a GitHub issue.

Like the retro engine, this is a standalone helper (NOT a LangGraph node): the
optional "AI Polish" step calls ``get_llm()`` directly and follows the same
**parse → fallback** convention the graph nodes use (agent/nodes.py) — here the
fallback is simply "keep the user's original draft".

Submission is two-path and **never raises**:

- ``GITHUB_TOKEN`` set → create the issue via the GitHub API (PyGithub, already
  a dependency for the read-only repo tools).
- No token → open a pre-filled ``issues/new`` URL in the user's browser; the
  target repo is public, so anyone logged into GitHub can file the issue.

Screenshots pasted into the description (Ctrl+V chips, see
``ui/shared/_attachments.py``) cannot be uploaded through the REST API — GitHub
only supports image upload via its web UI — so the issue body lists the local
file paths with a "drag onto the issue in your browser" hint. The AI Polish call
*does* see the images (``invoke_with_images``) so they can inform the rewrite.

Labels: both the API and the ``issues/new?labels=`` URL silently drop labels for
users without push/triage rights, so nothing here depends on them. The type is
in the title prefix (``[Bug] …``) and type+area are the first body line. The
maintainer should still pre-create ``type:bug|feature|improvement|other`` and
``area:<the 9 areas>`` on the repo so maintainer-filed feedback auto-labels.

# See docs: "Prompt Construction" — the AI Polish prompt (ARC framework)
"""

from __future__ import annotations

import json
import logging
import platform
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from yeaboi.ui.shared._attachments import CHIP_RE

logger = logging.getLogger(__name__)

# The public repo feedback issues are filed against.
FEEDBACK_REPO = "omardin14/yeaboi.ai"

FEEDBACK_TYPES: tuple[str, ...] = ("Bug", "Feature", "Improvement", "Other")

# Ordered to match the mode-select grid; colors come from changelog.AREA_COLORS.
FEEDBACK_AREAS: tuple[str, ...] = (
    "general",
    "analysis",
    "planning",
    "standup",
    "retro",
    "performance",
    "reporting",
    "usage",
    "settings",
)

# GitHub 414s around ~8 KB URLs and some OS browser-open handlers choke earlier;
# cap the pre-filled issues/new URL well below that.
_MAX_URL_CHARS = 6_000
_MAX_TITLE_CHARS = 250

_TRUNCATION_NOTE = "\n\n_…truncated — the full text was longer; paste the rest after opening._"


@dataclass(frozen=True)
class FeedbackResult:
    """Outcome of one submission attempt (API or browser path)."""

    ok: bool = False
    via: str = ""  # "api" | "browser"
    url: str = ""  # issue.html_url (api) or the pre-filled issues/new URL (browser)
    message: str = ""  # human status line for the result screen


def _relativize_home(path: str) -> str:
    """Replace the user's home directory with ``~`` so public issues never leak the username."""
    home = str(Path.home())
    return path.replace(home, "~") if path.startswith(home) else path


def _replace_chips(text: str) -> str:
    """Rewrite ``[image #N]`` chips as ``(screenshot N)`` — readable on GitHub."""
    return CHIP_RE.sub(lambda m: f"(screenshot {m.group(1)})", text)


def issue_title(kind: str, title: str) -> str:
    """Prefix the title with the type so triage works even when labels are dropped."""
    return f"[{kind}] {title.strip()}"[:_MAX_TITLE_CHARS]


def issue_labels(kind: str, area: str) -> list[str]:
    """Labels passed on both paths — harmless when GitHub silently drops them."""
    return [f"type:{kind.lower()}", f"area:{area}"]


def build_issue_body(kind: str, area: str, description: str, image_paths: list[str] | None = None) -> str:
    """Render the issue body markdown: metadata line, description, screenshots, footer."""
    from yeaboi import __version__

    lines = [f"**Type:** {kind} · **Area:** {area}", "", _replace_chips(description.strip())]

    paths = image_paths or []
    if paths:
        lines += ["", f"### Screenshots ({len(paths)})", "Saved locally on the reporter's machine:"]
        lines += [f"- `{_relativize_home(p)}`" for p in paths]
        lines += [
            "",
            "> GitHub's API can't upload images — drag these files onto this issue in your browser to attach them.",
        ]

    lines += [
        "",
        "---",
        f"_Sent from yeaboi v{__version__} · Python {platform.python_version()} "
        f"· {platform.system()} {platform.machine()}_",
    ]
    return "\n".join(lines)


def build_issue_url(kind: str, area: str, title: str, body: str) -> str:
    """Pre-filled ``issues/new`` URL for the no-token browser path, capped in length."""
    base = f"https://github.com/{FEEDBACK_REPO}/issues/new"

    def _url(b: str) -> str:
        params = urllib.parse.urlencode(
            {"title": issue_title(kind, title), "body": b, "labels": ",".join(issue_labels(kind, area))}
        )
        return f"{base}?{params}"

    url = _url(body)
    if len(url) <= _MAX_URL_CHARS:
        return url

    # Trim the body at a word boundary until the encoded URL fits. Encoding
    # expands unpredictably (newlines → %0A etc.), so shrink iteratively.
    overhead = len(_url("")) + len(urllib.parse.quote(_TRUNCATION_NOTE))
    budget = max(200, _MAX_URL_CHARS - overhead)
    truncated = body
    while True:
        truncated = truncated[:budget]
        cut = truncated.rsplit(" ", 1)[0] if " " in truncated else truncated
        url = _url(cut + _TRUNCATION_NOTE)
        if len(url) <= _MAX_URL_CHARS or budget <= 200:
            logger.info("feedback: body truncated for browser URL (%d -> %d chars)", len(body), len(cut))
            return url
        budget = int(budget * 0.8)


def submit_feedback(
    kind: str, area: str, title: str, description: str, image_paths: list[str] | None = None
) -> FeedbackResult:
    """Create the GitHub issue (token path) or open a pre-filled browser URL. Never raises."""
    from yeaboi.config import get_github_token

    body = build_issue_body(kind, area, description, image_paths)
    browser_url = build_issue_url(kind, area, title, body)
    token = get_github_token()
    logger.info(
        "feedback: submitting (type=%s area=%s, %d chars, %d image(s), via=%s)",
        kind,
        area,
        len(description),
        len(image_paths or []),
        "api" if token else "browser",
    )

    if token:
        try:
            from yeaboi.tools.github import _get_github_client

            repo = _get_github_client().get_repo(FEEDBACK_REPO)
            issue = repo.create_issue(title=issue_title(kind, title), body=body, labels=issue_labels(kind, area))
            logger.info("feedback: issue created #%s", issue.number)
            return FeedbackResult(ok=True, via="api", url=issue.html_url, message=f"Issue #{issue.number} created!")
        except Exception as exc:
            logger.warning("feedback: GitHub API submission failed: %s", exc)
            return FeedbackResult(
                ok=False,
                via="api",
                url=browser_url,
                message="GitHub API submission failed (check GITHUB_TOKEN) — you can file it in your browser instead.",
            )

    try:
        opened = webbrowser.open(browser_url)
    except Exception as exc:  # webbrowser can raise on exotic platforms
        logger.warning("feedback: browser open failed: %s", exc)
        opened = False
    if opened:
        msg = "Opened your browser with a pre-filled issue — review and press Submit there."
        if image_paths:
            msg += " Drag the screenshot files listed in the body onto the issue to attach them."
        return FeedbackResult(ok=True, via="browser", url=browser_url, message=msg)
    logger.warning("feedback: no browser available — showing URL for manual copy")
    return FeedbackResult(
        ok=False,
        via="browser",
        url=browser_url,
        message="Couldn't open a browser — copy this URL to file the issue:",
    )


def _parse_polish_response(raw: str) -> tuple[str, str] | None:
    """Extract ``(title, description)`` from the LLM response, tolerating markdown fences."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    try:
        parsed = json.loads(raw.strip())
    except (json.JSONDecodeError, TypeError):
        logger.warning("feedback: could not parse AI polish JSON response")
        return None
    if not isinstance(parsed, dict):
        return None
    title = str(parsed.get("title", "")).strip()
    description = str(parsed.get("description", "")).strip()
    if not title or not description:
        return None
    return title, description


def polish_feedback(
    kind: str, area: str, title: str, description: str, image_paths: list[str] | None = None
) -> tuple[tuple[str, str] | None, str]:
    """One LLM call that rewrites the draft into a clear issue. Never raises.

    Returns ``((polished_title, polished_description), status)`` on success or
    ``(None, why)`` when AI is unavailable/failed — the fallback is simply
    keeping the user's original draft.
    """
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("feedback: LLM not configured (%s) — keeping original draft", why)
        return None, f"AI unavailable ({why}) — keeping your original."

    logger.info("feedback: polish requested (%d chars draft, %d image(s))", len(description), len(image_paths or []))

    from yeaboi.agent.llm import get_llm, invoke_with_images, track_usage
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error
    from yeaboi.prompts.feedback import get_feedback_polish_prompt

    prompt = get_feedback_polish_prompt(kind, area, title, description)
    try:
        # invoke_with_images sends the pasted screenshots as image blocks so they
        # can inform the rewrite; non-vision models auto-retry text-only inside it.
        # See docs: "Agentic Blueprint Reference" — invoking the LLM directly
        response = invoke_with_images(get_llm(temperature=0.2), prompt, image_paths)
        track_usage(response)
        polished = _parse_polish_response(response.content)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("feedback: LLM auth/billing error — keeping original: %s", exc)
            return None, "AI unavailable (API key/billing) — keeping your original."
        logger.warning("feedback: AI polish failed, keeping original: %s", exc)
        return None, "AI request failed — keeping your original (see logs)."

    if polished is None:
        return None, "AI returned nothing usable — keeping your original."
    logger.info("feedback: polish produced %d-char description", len(polished[1]))
    return polished, "AI polished your draft — review below."
