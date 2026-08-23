"""One thread reply per answerable thing in a delivered post.

A standup post carries every member's practice signals in one block of
plaintext, so a 👍 on it cannot say *which* signal it means — and inferring one
is exactly the guess ``habits.py`` exists to refuse ("a missed signal is always
preferred to a wrong one"). So the post becomes a **thread**: the dispatch goes
up, then one short reply per votable signal, each with its own anchor carrying
``(session_id, run_id, member, rule)``. That tuple *is*
``practice_feedback.apply_verdict``'s signature, which is what removes the only
place in the inbound design where yeaboi would have had to guess.

Three things about the shape of the replies:

* **They are one line each.** The post above already renders each signal with
  its detail (``standup/render.py``); repeating it here would double the length
  of a post that a team reads on a phone. The reply carries the *anchor*, not
  the prose.
* **They are capped**, and the remainder is named rather than dropped. A
  ten-person standup can carry fifteen votable signals, and a thread that long
  stops being read.
* **Nothing here raises.** A thread that could not be posted costs the team the
  ability to vote from Slack; it must never cost them the standup.
"""

from __future__ import annotations

import logging

from yeaboi.slack.store import KIND_SIGNAL, SlackAnchor, SlackStore

logger = logging.getLogger(__name__)

#: How many signals get their own reply before the rest are summarised.
#: Twelve keeps the thread scannable while covering every standup that is not
#: pathological — and the ones past it are *named*, never silently dropped.
MAX_SIGNAL_REPLIES = 12


def _standup_signals(artifact) -> list[tuple[str, str, str]]:
    """(member, rule, title) for every signal a verdict could be recorded for.

    ``votable`` drops the signals written before handles existed: offering a
    thumbs-down on one would hide it today and remember nothing, so it would
    come straight back tomorrow looking answered.
    """
    from yeaboi.standup.practice_feedback import votable

    rows: list[tuple[str, str, str]] = []
    for update in getattr(artifact, "member_updates", ()) or ():
        for signal in votable(getattr(update, "practices", ()) or ()):
            rows.append((update.name, signal.rule, signal.title or signal.rule))
    return rows


#: Artifact kind → how to find its answerable items. A second votable mode is
#: one entry here and nothing else.
_SIGNALS = {"standup": _standup_signals}


def signal_line(member: str, title: str) -> str:
    """The whole reply. Short on purpose — see the module docstring."""
    return f"{member} · {title} — 👍 if that's right, 👎 if it isn't"


def post_signal_anchors(
    ref,
    artifact,
    *,
    session_id: str = "",
    ceremony: str = "",
    mode: str = "",
    artifact_kind: str = "",
    run_id: int = 0,
    db_path=None,
) -> int:
    """Post one reply per votable signal under ``ref``; return how many landed.

    Called from the delivering engine's ``on_receipt``, which only fires for a
    channel that came back with a durable address — today only Slack posting as
    a bot. A webhook delivery never reaches here, so there is nothing to guard
    against: without a ``ts`` there is no thread to hang anything under.
    """
    if not artifact_kind or not run_id or not getattr(ref, "ts", ""):
        return 0
    finder = _SIGNALS.get(artifact_kind)
    if finder is None:
        return 0

    from yeaboi import config
    from yeaboi.slack import allowlist as allow
    from yeaboi.tools import slack as api

    token = config.get_slack_bot_token()
    if not token:
        return 0

    # A signal reply says "👍 if that's right, 👎 if it isn't", so posting one
    # is a promise that the gesture lands somewhere. With an empty or voided
    # allowlist the poll never calls Slack at all, and every one of these is a
    # gesture with no consequence — which is the thing this package refuses to
    # make anywhere else, and it would say it twelve times per standup. The
    # post itself still goes out; only the invitation is withheld.
    if not allow.load():
        logger.info("slack: no allowlist, so no signal replies — a vote could not be actioned")
        return 0

    try:
        signals = finder(artifact)
    except Exception:  # noqa: BLE001 — a report shape we did not expect is not a delivery failure
        logger.warning("slack: could not read the signals out of a %s artifact", artifact_kind, exc_info=True)
        return 0
    if not signals:
        return 0

    channel = ref.channel
    posted = 0
    held = max(0, len(signals) - MAX_SIGNAL_REPLIES)
    try:
        with SlackStore(db_path) as store:
            for member, rule, title in signals[:MAX_SIGNAL_REPLIES]:
                resp = api.post_message(channel, signal_line(member, title), thread_ts=ref.ts, token=token)
                if not resp.ok:
                    # One reply that would not post must not cost the rest of
                    # the thread; the signal is still in the post above.
                    logger.warning("slack: could not post the %s signal for %s (%s)", rule, member, resp.error)
                    continue
                store.record_anchor(
                    SlackAnchor(
                        channel=str(resp.data.get("channel", channel)),
                        ts=str(resp.data.get("ts", "")),
                        root_ts=ref.ts,
                        kind=KIND_SIGNAL,
                        session_id=session_id,
                        ceremony=ceremony,
                        mode=mode,
                        artifact_kind=artifact_kind,
                        run_id=run_id,
                        member=member,
                        rule=rule,
                    )
                )
                posted += 1
            if held and posted:
                # Named, not dropped. A thread that quietly stops at twelve
                # reads as "these are all of them", which is the one thing it
                # must not say.
                noun = "signal" if held == 1 else "signals"
                api.post_message(
                    channel,
                    f"{held} more {noun} {'is' if held == 1 else 'are'} in the report above — answer them in the app.",
                    thread_ts=ref.ts,
                    token=token,
                )
    except Exception:  # noqa: BLE001 — the standup landed; the thread is a bonus
        logger.warning("slack: could not build the signal thread under %s", ref.ts, exc_info=True)

    logger.info("slack: posted %d signal repl%s under %s", posted, "y" if posted == 1 else "ies", ref.ts)
    return posted
