"""One reader-authored correction, and how a log of them becomes an artifact.

Two halves, deliberately separated:

* :func:`validate` runs **once**, when an edit arrives from a browser. It is the
  only place untrusted text is inspected — length caps, control characters,
  prompt-injection patterns, allowlist membership. What comes out is safe to
  store.
* :func:`apply_edits` runs **every time** the artifact is materialised, over
  edits that were validated when they arrived. It re-derives rather than
  mutating, so a document is always ``base + the log``, and the log is the
  version history rather than a description of one.

Materialisation is ``asdict`` → ordered patch → the registry's reconstructor.
That is the same three steps :func:`yeaboi.anonymize.apply.mask_artifact` takes,
which is not a coincidence — masking and editing are both "transform a frozen
artifact, get a frozen artifact back", and the repo already had exactly one way
to do it.

Compare-and-swap, and what it is actually for
---------------------------------------------

Every ``set`` and ``remove`` carries ``base``: the value the editor could see
when they started typing. It answers two different questions with one field.

*Live*, it catches a concurrent editor — two people fixing the same sentence,
the second one overwriting a correction they never saw.

*On replay*, it catches something more dangerous. The log outlives the artifact
it was written against: a standup can be re-run, an artifact regenerated, and
the same paths then point at different prose. Without the check, an edit that
said "fix this typo" would silently overwrite a sentence nobody ever read. With
it, the edit is marked stale and reported as such — the correction is not lost,
it is *shown as unapplied*, which is the honest outcome.

Reverting
---------

A revert targets a non-revert edit, and a revert cannot itself be reverted. That
is a real limitation and it buys a real thing: deadness is one set comprehension
over the log rather than a fixed-point over a graph that a malicious client
could make cyclic. Undoing a revert is what the UI already offers for everything
else — type the value back, which is a new edit, attributed to whoever did it.

# See docs: "Guardrails" — input guardrails and untrusted browser input
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from yeaboi.artifacts.paths import PathError, parse_path, render_path, resolve
from yeaboi.artifacts.registry import FIELD_ITEMS, ArtifactSpec

logger = logging.getLogger(__name__)

OP_SET = "set"
OP_APPEND = "append"
OP_REMOVE = "remove"
OP_NOTE = "note"
OP_FIELD = "field"
OP_REVERT = "revert"

EDIT_OPS = (OP_SET, OP_APPEND, OP_REMOVE, OP_NOTE, OP_FIELD, OP_REVERT)
"""The complete op vocabulary, validated server-side.

Mirrored into ``frontend/src/types/enums.ts`` by ``scripts/gen_web_types.py``
and deliberately **not** shipped in any boot payload: a payload would win at
runtime, so a stale bundle could offer an op the server rejects.
"""

MAX_AUTHOR = 60
"""Matches the retro board's author cap. Same gesture, same limit."""

MAX_NEWLINES = 40
"""A correction is prose, not a document. Past this it is a layout attack."""

MAX_LABEL = 80
"""Longest name for a reader-added field. A label, not a sentence."""

MAX_ANNOTATION = 2000
MAX_ANNOTATIONS = 100
"""How many notes and fields one document may carry, in total. High enough that
a real team never meets it, low enough that a joiner with the link cannot turn a
standup into an unreadable wall."""

# C0 controls minus \n and \t, plus DEL. A browser cannot type these; something
# that sends them is either broken or probing what the log renderer does with
# them.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class EditError(ValueError):
    """An edit was refused. Always a client error, and always safe to return."""


@dataclass(frozen=True)
class Edit:
    """One correction. Frozen, defaulted, and stored as JSON — the house shape.

    ``base`` is the compare-and-swap value; ``target`` names the edit a revert
    undoes. Both are empty for the ops that do not use them, rather than None,
    so the wire shape has one type per field.
    """

    edit_id: str = ""
    seq: int = 0
    op: str = ""
    path: str = ""
    value: str = ""
    base: str = ""
    label: str = ""
    target: str = ""
    author: str = ""
    avatar: str = ""
    pid: str = ""
    at: str = ""


@dataclass(frozen=True)
class EditResult:
    """What became of one edit during materialisation."""

    edit_id: str
    applied: bool
    reason: str = ""

    @property
    def stale(self) -> bool:
        """True when the edit was well-formed but no longer fits the artifact."""
        return not self.applied and self.reason in ("missing", "conflict")


def _clean(text: str, limit: int) -> str:
    """Normalise one untrusted string, or raise :class:`EditError`."""
    text = _CONTROL_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > limit:
        raise EditError(f"too long (max {limit} characters)")
    if text.count("\n") > MAX_NEWLINES:
        raise EditError("too many line breaks")
    return text.strip()


def _clean_name(text: str) -> str:
    """Normalise a display name, truncating rather than refusing.

    The asymmetry with :func:`_clean` is deliberate. A too-long *value* is the
    author's own prose and they should be told it did not fit, so they can cut
    it themselves. A too-long *name* is a field nobody is looking at while they
    type; refusing the whole correction over it would throw away the work to
    protect a label. Same call the retro board makes about card authors.
    """
    return _CONTROL_RE.sub("", text).replace("\n", " ").strip()[:MAX_AUTHOR]


def _mutable(value: Any) -> Any:
    """Deep-convert an ``asdict`` tree so its sequences can be written to.

    ``dataclasses.asdict`` rebuilds each container with ``type(obj)(...)``, so a
    ``tuple[MemberUpdate, ...]`` comes back as a *tuple* — immutable, and
    therefore unpatchable. Every artifact here uses tuples for its collections
    (that is the house rule that keeps them hashable and frozen), so without
    this every list edit resolves to "missing" and the whole feature silently
    does nothing.

    Lists are also what the reconstructors already expect: they are written
    against JSON, where a tuple has never survived the round trip.
    """
    if isinstance(value, dict):
        return {k: _mutable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mutable(v) for v in value]
    return value


def _check_injection(text: str) -> None:
    from yeaboi.input_guardrails import check_prompt_injection

    # Regex-only, no LLM and no network, so it is safe to run inside a request
    # handler. It matters here specifically because an edited standup becomes
    # *tomorrow's* prompt context — an injected instruction would be read back
    # by a model that has no way to tell it from the team's own words.
    if warning := check_prompt_injection(text):
        raise EditError(warning)


def validate(edit: Edit, spec: ArtifactSpec) -> Edit:
    """Return a normalised, storable edit, or raise :class:`EditError`.

    Runs once, on arrival. Everything downstream — the store, the materialiser,
    the exporters — trusts what this returns.
    """
    if edit.op not in EDIT_OPS:
        raise EditError(f"unknown op: {edit.op!r}")
    author = _clean_name(edit.author)

    if edit.op == OP_REVERT:
        if not edit.target:
            raise EditError("revert needs a target")
        return replace(edit, author=author, path="", value="", base="", label="")

    if edit.op in (OP_NOTE, OP_FIELD):
        if not spec.annotatable:
            raise EditError(f"{spec.label} does not take notes")
        # An empty path means the document as a whole; anything else is an
        # anchor, and only its *shape* can be checked here — whether it points
        # at a row that still exists is a question about an artifact this
        # function has never seen, and is answered at materialisation.
        anchor = render_path(parse_path(edit.path)) if edit.path else ""
        text = _clean(edit.value, MAX_ANNOTATION)
        if not text:
            raise EditError("value is empty")
        _check_injection(text)
        label = _clean(edit.label, MAX_LABEL) if edit.op == OP_FIELD else ""
        if edit.op == OP_FIELD:
            if not label:
                raise EditError("a field needs a name")
            _check_injection(label)
        return replace(edit, author=author, path=anchor, value=text, label=label, base="", target="")

    segments = parse_path(edit.path)  # PathError is an EditError to the caller: both are ValueError
    chain = tuple(seg.field for seg in segments)
    field = spec.field_for(chain)
    if field is None:
        # One message for "no such field" and "that field is not editable".
        # Distinguishing them would let a prober map the artifact.
        raise EditError(f"{render_path(segments)} is not editable")

    last = segments[-1]
    if field.kind == FIELD_ITEMS:
        if edit.op == OP_APPEND and not last.append:
            raise EditError("append needs an append slot, e.g. highlights[-]")
        if edit.op in (OP_SET, OP_REMOVE) and last.index < 0:
            raise EditError(f"{edit.op} on a list needs an index, e.g. highlights[#0]")
    elif edit.op != OP_SET:
        raise EditError(f"{edit.op} is only valid on a list")
    elif last.selects:
        raise EditError("cannot select into a plain field")

    value = "" if edit.op == OP_REMOVE else _clean(edit.value, field.limit())
    if value:
        _check_injection(value)
    if edit.op in (OP_SET, OP_APPEND) and not value:
        raise EditError("value is empty")

    return replace(
        edit,
        author=author,
        path=render_path(segments),  # canonical form: the log stores what it can re-parse
        value=value,
        base=_clean(edit.base, field.limit()) if edit.base else "",
    )


def _apply_annotation(tree: dict, edit: Edit, spec: ArtifactSpec) -> EditResult:
    """Attach a note or a named field to the document or to one row inside it."""

    def failed(reason: str) -> EditResult:
        return EditResult(edit_id=edit.edit_id, applied=False, reason=reason)

    if edit.path:
        # The anchor has to still point at something. A note left on a member
        # who is no longer in the report is not rendered anywhere, so recording
        # it as applied would be a lie the history then repeats.
        try:
            target = resolve(tree, parse_path(edit.path), dict(spec.list_keys))
        except PathError:
            return failed("malformed")
        if target is None or not target.exists() or not isinstance(target.get(), dict):
            return failed("missing")

    existing = tree.setdefault("annotations", [])
    if not isinstance(existing, list):
        return failed("not editable")
    if len(existing) >= MAX_ANNOTATIONS:
        return failed("full")
    existing.append(
        {
            "kind": edit.op,
            "anchor": edit.path,
            "label": edit.label,
            "text": edit.value,
            "author": edit.author,
            "avatar": edit.avatar,
            "at": edit.at,
        }
    )
    return EditResult(edit_id=edit.edit_id, applied=True)


def _apply_one(tree: dict, edit: Edit, spec: ArtifactSpec) -> EditResult:
    def failed(reason: str) -> EditResult:
        return EditResult(edit_id=edit.edit_id, applied=False, reason=reason)

    if edit.op in (OP_NOTE, OP_FIELD):
        return _apply_annotation(tree, edit, spec)

    try:
        segments = parse_path(edit.path)
    except PathError:
        # Only reachable for a log row written by an older, laxer validator.
        return failed("malformed")

    field = spec.field_for(tuple(seg.field for seg in segments))
    if field is None:
        # The allowlist shrank after this edit was accepted — a field was made
        # uneditable. Honour that retroactively rather than replaying a write we
        # would now refuse.
        return failed("not editable")

    target = resolve(tree, segments, dict(spec.list_keys))
    if target is None:
        return failed("missing")

    if edit.op == OP_APPEND:
        if not target.append:
            return failed("missing")
        if len(target.container) >= field.max_items:
            return failed("full")
        target.container.append(edit.value)
        return EditResult(edit_id=edit.edit_id, applied=True)

    if not target.exists():
        return failed("missing")
    current = target.get()
    if not isinstance(current, str):
        return failed("not editable")
    # The compare-and-swap. An empty `base` means the edit was written before
    # the editor had a value to compare (a first correction to an empty field),
    # so there is nothing to check.
    if edit.base and current != edit.base:
        return failed("conflict")

    if edit.op == OP_REMOVE:
        del target.container[target.key]
    else:
        target.container[target.key] = edit.value
    return EditResult(edit_id=edit.edit_id, applied=True)


def apply_edits(artifact: Any, edits: tuple[Edit, ...], spec: ArtifactSpec) -> tuple[Any, tuple[EditResult, ...]]:
    """Return ``(corrected artifact, one result per edit)``.

    Deterministic: the same base and the same ordered log always produce the same
    artifact, which is what makes "version N" a meaningful thing to ask for —
    it is this function over the first N entries.

    Never raises for a bad edit. A correction that no longer fits comes back as
    an unapplied result with a reason, because the alternative — dropping the
    whole document because one sentence moved — is worse for everybody.
    """
    tree = _mutable(asdict(artifact))
    reverted = {e.target for e in edits if e.op == OP_REVERT and e.target}
    results: list[EditResult] = []

    for edit in edits:
        if edit.op == OP_REVERT:
            results.append(EditResult(edit_id=edit.edit_id, applied=True))
            continue
        if edit.edit_id and edit.edit_id in reverted:
            results.append(EditResult(edit_id=edit.edit_id, applied=False, reason="reverted"))
            continue
        results.append(_apply_one(tree, edit, spec))

    return spec.reconstruct(tree), tuple(results)


def summarise(results: tuple[EditResult, ...]) -> str:
    """A one-line count for a log message. Never includes an edited value."""
    applied = sum(1 for r in results if r.applied)
    stale = sum(1 for r in results if r.stale)
    return f"{applied} applied, {stale} stale, {len(results)} total"
