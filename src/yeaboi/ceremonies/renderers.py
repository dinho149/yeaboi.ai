"""One artifact → ``Dispatch`` renderer per catalogued mode.

Delivery used to be typed on ``StandupReport``, which is the whole reason
nothing but the standup could be delivered. These are the adapters that made
the channels mode-neutral: each takes its mode's artifact and produces the
title, the one-line summary a desktop notification can hold, and the plaintext
body Slack, email and the terminal all send.

Two rules the whole file follows:

- **The standup's body is the existing renderer, untouched.** Its bytes already
  go to real Slack channels and inboxes; a refactor that reformats them is a
  behaviour change wearing a refactor's clothes.
- **A body is a readout, not the report.** These land in a chat message, so
  they lead with the number somebody scrolls for and stop; the full artifact is
  a mode away, and the ledger records where the run is.
"""

from __future__ import annotations

import logging

from yeaboi.agent.state import (
    AgentAdvisorReport,
    AgentSecurityReport,
    AgentStandupDigest,
    AgentUsageReport,
    DeliveryReport,
    Dispatch,
    StandupReport,
)

logger = logging.getLogger(__name__)

# A chat message nobody reads is the same as one nobody sent. Bodies are capped
# and the tail says so, rather than being silently truncated mid-sentence.
_BODY_CAP = 3500
_LIST_CAP = 6


def _bullets(items, cap: int = _LIST_CAP) -> str:
    """``items`` as '- ' lines, capped, with an explicit remainder count."""
    rows = [str(item).strip() for item in items if str(item).strip()]
    if not rows:
        return ""
    shown = rows[:cap]
    text = "\n".join(f"- {row}" for row in shown)
    if len(rows) > cap:
        text += f"\n- …and {len(rows) - cap} more"
    return text


def _assemble(*blocks: str) -> str:
    """Join the non-empty blocks, then cap with a visible marker."""
    body = "\n\n".join(block.strip() for block in blocks if block and block.strip())
    if len(body) > _BODY_CAP:
        body = body[:_BODY_CAP].rstrip() + "\n\n… truncated — open the mode for the full report."
    return body


def _money(value: float) -> str:
    return f"${value:,.2f}"


# ---------------------------------------------------------------------------
# Human ceremonies
# ---------------------------------------------------------------------------


def standup_dispatch(report: StandupReport) -> Dispatch:
    """The daily standup, in the exact shape it was already being delivered in.

    Body is the existing plaintext renderer, verbatim. The title and subject are
    the two strings the pre-``Dispatch`` channels built for themselves, kept
    apart because they were never the same string: the desktop banner led with
    the confidence label, and the email subject carried date *and* label. Inbox
    filters and mail threading are built on the second one.
    """
    from yeaboi.standup.render import format_standup_plaintext

    label = report.confidence_label or report.date
    subject = f"Daily Standup — {report.date} ({report.confidence_label})" if report.date else "Daily Standup"
    return Dispatch(
        title=f"Daily Standup — {label}" if label else "Daily Standup",
        summary=report.team_summary or report.confidence_rationale or "Standup ready.",
        body=format_standup_plaintext(report),
        subject=subject,
    )


def report_dispatch(report: DeliveryReport) -> Dispatch:
    """The stakeholder delivery report, as its headline plus the numbers."""
    metrics = "\n".join(f"- {label}: {value}" for label, value in report.metrics if str(label).strip())
    return Dispatch(
        title=f"Delivery report — {report.period_label or report.period_start}",
        summary=report.headline or f"{report.project_name} delivery report ready.",
        body=_assemble(
            report.headline,
            report.executive_summary,
            metrics,
            _bullets(report.highlights),
        ),
    )


# ---------------------------------------------------------------------------
# The Agents family
# ---------------------------------------------------------------------------


def agent_usage_dispatch(report: AgentUsageReport) -> Dispatch:
    """What the coding agents spent, led by the total."""
    total = _money(report.total_cost_usd)
    by_model = "\n".join(
        f"- {row.model}: {_money(row.cost_usd)} over {row.calls} call{'s' if row.calls != 1 else ''}"
        for row in report.by_model[:_LIST_CAP]
    )
    window = f"{report.period_start} → {report.period_end}".strip(" →")
    return Dispatch(
        title=f"Agent cost — {window}" if window else "Agent cost",
        summary=f"{total} across {report.session_count} agent session{'s' if report.session_count != 1 else ''}.",
        body=_assemble(f"Total: {total} over {report.session_count} sessions ({window}).", by_model),
    )


def agent_advisor_dispatch(report: AgentAdvisorReport) -> Dispatch:
    """Recoverable spend — the one number this mode exists to produce."""
    recoverable = _money(report.recoverable_usd)
    share = f"{report.recoverable_share * 100:.0f}%" if report.recoverable_share else "0%"
    return Dispatch(
        title="Agent advisor — recoverable spend",
        summary=f"{recoverable} recoverable ({share} of {_money(report.total_cost_usd)}).",
        body=_assemble(
            f"Recoverable: {recoverable} — {share} of the window's {_money(report.total_cost_usd)}, "
            f"across {report.session_count} sessions and {report.files_audited} transcripts.",
            _bullets(report.recommendations),
            "Every figure is an estimate and every count is a floor.",
        ),
    )


def agent_standup_dispatch(digest: AgentStandupDigest) -> Dispatch:
    """What the agents did — the same shape as the human standup."""
    attention = _bullets(digest.attention_items)
    return Dispatch(
        title=f"Agent standup — {digest.digest_date}" if digest.digest_date else "Agent standup",
        summary=(
            digest.narrative.split("\n")[0]
            if digest.narrative
            else f"{digest.sessions_worked} agent session(s), {_money(digest.total_cost_usd)}."
        ),
        body=_assemble(
            digest.narrative,
            _bullets(digest.highlights),
            f"Needs attention:\n{attention}" if attention else "",
            _bullets(digest.in_flight),
        ),
    )


def agent_security_dispatch(report: AgentSecurityReport) -> Dispatch:
    """Posture first, because that is the part that changes a decision."""
    high = [f for f in report.findings if f.severity in ("critical", "high")]
    posture = report.posture or "unknown"
    return Dispatch(
        title=f"Agent security — {posture}",
        summary=(
            f"{posture}: {len(high)} high/critical finding{'s' if len(high) != 1 else ''}, "
            f"{report.secrets_found} secret{'s' if report.secrets_found != 1 else ''}."
        ),
        body=_assemble(
            report.summary,
            _bullets(f"[{f.severity}] {f.title} — {f.location}" for f in high),
            _bullets(report.recommendations),
            "Deterministic pattern scan — an indicator, not a security audit.",
        ),
    )
