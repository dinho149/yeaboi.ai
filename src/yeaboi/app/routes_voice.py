"""Native routes for dictation — status, setup, and one transcript at a time.

Dictation is two halves. In the terminal Python owns both: PortAudio opens the
microphone, Whisper turns the frames into text. Here the window owns capture —
``getUserMedia`` picks the device, draws the level meter and hands back an
encoded blob — and Python owns only transcription. That split is why this module
asks :func:`yeaboi.voice.transcription_state` rather than ``voice_state``, and
why its installer fetches :data:`~yeaboi.voice.TRANSCRIBE_PACKAGES` rather than
the whole ``voice`` extra: a surface with its own microphone should not be made
to install one.

What crosses the wire is a container the stdlib cannot read (webm/opus, or mp4
from a Safari-flavoured engine), so it goes to ``transcribe_media`` — the
documented PyAV path — rather than the WAV path the terminal records into.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import queue
import threading
from collections.abc import Iterator

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)

#: The largest recording accepted, decoded. Chosen against the server's own
#: 2 MB body cap with room for base64's third: past this the body arrives empty
#: and the take would look like silence rather than like a limit.
MAX_AUDIO_BYTES = 1_200_000

#: The stream is over. Never reaches the wire.
_END = object()

#: One transcription at a time. Two windows dictating at once would put two
#: CTranslate2 runs on the same CPU and the same cached model; serialising costs
#: a wait nobody will notice and removes the question entirely. Deliberately not
#: ``_ENGINE_LOCK`` — dictating into the composer while a standup runs is an
#: ordinary thing to do, and that lock would make it impossible.
_transcribe_lock = threading.Lock()


def status(app, request: Request) -> Response:
    """``GET /api/voice`` — whether this machine can transcribe, and what it costs.

    ``device`` is the ``VOICE_DEVICE`` preference, passed through unresolved:
    the terminal resolves it against PortAudio's device list and the window
    resolves it against Chromium's, so the *name* is the shared part and neither
    surface may resolve it for the other.
    """
    from yeaboi import voice, voice_install
    from yeaboi.config import get_voice_device, get_voice_model

    state = voice.transcription_state()
    size = get_voice_model()
    plan = voice_install.install_plan(packages=voice.TRANSCRIBE_PACKAGES)
    return json_response(
        {
            "state": state,
            "detail": _detail(state, size),
            "model": size,
            "model_cached": voice_install.model_is_cached(size),
            "device": get_voice_device(),
            "install": {
                "available": state == "installable" and not plan.blocked,
                "blocked": plan.blocked,
                "size_mb": voice_install.size_estimate_mb(),
                "command": voice.voice_install_command(),
            },
            "max_bytes": MAX_AUDIO_BYTES,
        }
    )


def _detail(state: str, size: str) -> str:
    """One sentence about this machine, worded from the shared vocabulary."""
    from yeaboi import voice, voice_install

    if state == "ready":
        return f"available — local Whisper ({size})"
    if state == "unsupported":
        return f"unavailable — {voice_install.unsupported_reason()}"
    if state == "declined":
        return f"not installed — offer dismissed; {voice.voice_install_command()}"
    return "not installed — about two minutes, once"


def offer(app, request: Request) -> Response:
    """``POST /api/voice/offer`` — take or withdraw the standing install offer.

    ``{"enabled": false}`` is the desktop's "never": the same persisted answer
    the terminal writes when someone presses ``n``, so declining in one surface
    stops the other asking too. Re-enabling is the way back, which is why this
    is a switch rather than a one-way dismissal.
    """
    from yeaboi.config import set_voice_install_offer

    enabled = bool(request.json().get("enabled", True))
    set_voice_install_offer(enabled)
    logger.info("voice install offer %s", "restored" if enabled else "declined permanently")
    return json_response({"enabled": enabled})


def transcribe(app, request: Request) -> Response:
    """``POST /api/voice/transcribe`` — one recording in, one transcript out.

    Body ``{"audio": "<base64>", "mime": "audio/webm"}``. ``mime`` is recorded
    for the log only: the decoder sniffs the container itself, and trusting a
    client-declared type would just be a second thing that can be wrong.
    """
    from yeaboi import voice

    available, reason = voice.can_transcribe()
    if not available:
        raise HTTPError(409, reason)

    payload = _audio_payload(request)
    logger.info("voice transcribe: %d bytes (%s)", len(payload[0]), payload[1] or "unknown type")
    with _transcribe_lock:
        try:
            text = voice.transcribe_media(payload[0])
        except Exception as exc:  # noqa: BLE001 — reported to the window, not raised at it
            logger.warning("voice transcription failed", exc_info=True)
            raise HTTPError(500, f"Could not transcribe that recording — {exc.__class__.__name__}") from exc
    logger.info("voice transcribe: %d chars", len(text))
    return json_response({"text": text})


def _audio_payload(request: Request) -> tuple[bytes, str]:
    """The decoded recording and its declared type, or a 400 saying why not."""
    if not request.body and request.headers.get("Content-Length", "0") not in {"0", ""}:
        # The server drops an over-cap body rather than reading it, so this is
        # the one place a too-long take is distinguishable from a silent one.
        raise HTTPError(413, "That recording is too long to send — keep a take under a couple of minutes")
    body = request.json()
    raw = str(body.get("audio", ""))
    if not raw:
        raise HTTPError(400, "audio is required (base64)")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPError(400, "audio must be base64") from exc
    if not data:
        raise HTTPError(400, "the recording was empty")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPError(413, f"that recording is {len(data) // 1000} kB — the limit is {MAX_AUDIO_BYTES // 1000} kB")
    return data, str(body.get("mime", ""))


def install(app, request: Request) -> Response:
    """``POST /api/voice/install`` — set dictation up, streamed as NDJSON.

    ``stage`` lines carry the same four stages the terminal animates (install,
    download, load, then done), because the two surfaces are driving the same
    installer and a person who has seen one should recognise the other.
    Cancellable: the op is announced first, and both children die on a cancel.

    A standing "never" does not block this — the same latitude ``--install-voice``
    takes. Nothing reaches here without someone pressing a button that says what
    it does, and refusing them on the strength of an old answer leaves no way back.
    """
    from yeaboi import voice, voice_install

    if voice.can_transcribe()[0]:
        raise HTTPError(409, "dictation is already set up")
    plan = voice_install.install_plan(packages=voice.TRANSCRIBE_PACKAGES)
    if plan.blocked:
        raise HTTPError(409, f"Dictation can't be installed here — {plan.blocked}")

    op = app.ops.create()
    logger.info("voice install accepted: %s", plan.display_command)
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_install(app, op, plan)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def _install(app, op, plan) -> Iterator[dict]:
    from yeaboi.config import get_voice_model, mark_voice_extra_installed
    from yeaboi.voice_install import download_model, install_packages, warm_model

    size = get_voice_model()
    events: queue.Queue = queue.Queue()
    outcome: dict = {"ok": False, "message": ""}

    def worker() -> None:
        try:
            ok, message = install_packages(
                lambda phrase: events.put({"type": "stage", "stage": "install", "detail": phrase}),
                op.cancel,
                plan=plan,
            )
            if not ok:
                outcome.update(ok=False, message=message)
                return
            mark_voice_extra_installed()

            events.put({"type": "stage", "stage": "download", "detail": "", "fraction": None})
            model_ok, model_message = download_model(
                size,
                lambda detail, fraction: events.put(
                    {"type": "stage", "stage": "download", "detail": detail, "fraction": fraction}
                ),
                op.cancel,
            )
            if not model_ok and op.cancel.is_set():
                outcome.update(ok=False, message=model_message)
                return
            if model_ok:
                # Only ever warm a model already on disk: warm_model loads it
                # in-process, and a missing one would download with no progress
                # and no cancel — and take this process down with it on a CPU
                # without AVX, which is exactly what the child contains.
                events.put({"type": "stage", "stage": "load", "detail": "", "fraction": None})
                warm_model(size)
            # A failed model fetch is a warning: the packages are in, so the
            # weights simply arrive lazily on the first dictation.
            outcome.update(ok=True, message="" if model_ok else model_message)
        except Exception:  # noqa: BLE001 — a worker thread must not kill the stream
            logger.exception("voice install failed unexpectedly")
            outcome.update(ok=False, message="Dictation setup hit an unexpected error — see the log")
        finally:
            events.put(_END)

    thread = threading.Thread(target=worker, name="voice-install", daemon=True)
    thread.start()
    try:
        yield {"type": "op", "op_id": op.op_id}
        while (event := events.get()) is not _END:
            yield event
        thread.join()
        if outcome["ok"]:
            logger.info(
                "voice install finished%s", f" with a warning: {outcome['message']}" if outcome["message"] else ""
            )
            yield {"type": "done", "warning": outcome["message"]}
        else:
            logger.warning("voice install failed: %s", outcome["message"])
            yield {"type": "error", "message": outcome["message"] or "Dictation setup failed — see the log"}
    finally:
        app.ops.remove(op.op_id)


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
