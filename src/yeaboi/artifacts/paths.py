"""The path grammar for addressing one editable place inside an artifact.

A browser correcting a shared report has to name *what* it is correcting, and
the name has to survive being written down, sent over a tunnel, stored in a log,
and replayed later against an artifact that may have been regenerated in the
meantime. This module is the only place in the codebase that parses such a name.

The grammar
-----------

::

    path     := segment ("." segment)*
    segment  := field | field "[" selector "]"
    field    := [a-z_][a-z0-9_]*
    selector := key "=" value        # identity   member_updates[name=Ada]
              | "#" integer          # positional highlights[#2]
              | "-"                  # append     highlights[-]

Why identity selectors and not plain indices
--------------------------------------------

``member_updates[2]`` is a promise that the third element is still the same
person it was when the editor clicked. Nothing keeps that promise: two people
editing at once, or a replay against a re-generated report, can reorder a list
between the click and the write, and a positional path would then quietly edit
somebody else's update. That is the worst failure this feature can have — a
correction attributed to the right author, landing on the wrong person.

So every list with a natural key is addressed by it (``member_updates`` by
``name``, ``delivered_items`` by ``key``, ``themes`` by ``title``), and the keys
live in the registry beside the artifact rather than being guessed here.

``[#n]`` remains for lists of plain strings — ``warnings``, ``highlights``, a
section's bullets — which have no key to use. It is never trusted on its own:
:mod:`yeaboi.artifacts.edits` pairs every positional write with a
compare-and-swap on the value the editor expected to find, so an index that
drifted is caught rather than obeyed.

Why ``.``/``[]`` and not RFC 6901 JSON Pointer
-----------------------------------------------

6901 has no identity selector, so we would be bolting one on regardless, and its
``/``-joined form reads badly next to snake_case field names — ``/member_updates/2``
against ``member_updates[name=Ada]``. The dotted form is also what the browser
already shows a reader in the edit history, so it wants to be legible.

Escaping
--------

Only selector *values* can contain awkward characters, so only they are encoded,
with :func:`urllib.parse.quote`/:func:`~urllib.parse.unquote` over ``%``, ``.``,
``]`` and ``[``. Field names never need it: they come from a fixed allowlist and
are rejected here if they do not match ``field``.

# See docs: "Guardrails" — untrusted browser input
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote

# A field name is an attribute of a frozen dataclass, so it is already
# constrained to a Python identifier. Requiring it to *start with a letter*
# additionally means a path can never name a class, a dunder or a private —
# `__class__` and `_lock` are both unmatchable, which they would not be if a
# leading underscore were allowed.
_FIELD_RE = re.compile(r"[a-z][a-z0-9_]*")

MAX_PATH_LENGTH = 400
"""Longest accepted path string.

Not a guess about real paths — the deepest real one is around 60 characters —
but a bound on what an untrusted client can make the parser chew on before it is
rejected. Parsing is linear, so this is about the log and the wire, not CPU.
"""

MAX_SEGMENTS = 8
"""Deepest accepted path. The deepest real artifact nesting is 3."""


@dataclass(frozen=True)
class Segment:
    """One ``field`` or ``field[selector]`` step of a path.

    Exactly one of the three selector forms is active, or none at all:

    * plain field — ``key``/``value`` empty, ``index`` ``-1``, ``append`` false
    * identity — ``key`` and ``value`` set
    * positional — ``index`` >= 0
    * append slot — ``append`` true
    """

    field: str
    key: str = ""
    value: str = ""
    index: int = -1
    append: bool = False

    @property
    def selects(self) -> bool:
        """True when this segment addresses an element *inside* its list."""
        return bool(self.key) or self.index >= 0 or self.append


class PathError(ValueError):
    """A path was malformed. Always a client error, never a server bug."""


def _parse_segment(raw: str) -> Segment:
    head, bracket, rest = raw.partition("[")
    if not _FIELD_RE.fullmatch(head):
        raise PathError(f"bad field name: {head!r}")
    if not bracket:
        return Segment(field=head)
    if not rest.endswith("]"):
        raise PathError(f"unterminated selector in {raw!r}")
    selector = rest[:-1]
    if selector == "-":
        return Segment(field=head, append=True)
    if selector.startswith("#"):
        digits = selector[1:]
        # `str.isdigit` rather than a try/except int(): it also rejects the
        # signs, the underscores and the unicode digit forms that int() accepts,
        # none of which should round-trip through render_path unchanged.
        if not digits.isdigit():
            raise PathError(f"bad index selector: {selector!r}")
        return Segment(field=head, index=int(digits))
    key, equals, value = selector.partition("=")
    if not equals or not _FIELD_RE.fullmatch(key):
        raise PathError(f"bad selector: {selector!r}")
    return Segment(field=head, key=key, value=unquote(value))


def parse_path(text: str) -> tuple[Segment, ...]:
    """Parse a path string into segments, or raise :class:`PathError`.

    Every rejection is a client error with a message safe to return: it names the
    malformed piece and nothing about the artifact.
    """
    if not text:
        raise PathError("empty path")
    if len(text) > MAX_PATH_LENGTH:
        raise PathError("path too long")
    # Splitting on "." is safe because a "." inside a selector value is escaped
    # by render_path, and an unescaped one is a malformed path we want rejected.
    parts = text.split(".")
    if len(parts) > MAX_SEGMENTS:
        raise PathError("path too deep")
    return tuple(_parse_segment(part) for part in parts)


def render_path(segments: tuple[Segment, ...] | list[Segment]) -> str:
    """Render segments back to a path string.

    ``parse_path(render_path(s)) == s`` for every well-formed ``s``. The log
    stores the rendered form, so this is what a reader sees in the edit history.
    """
    out: list[str] = []
    for seg in segments:
        if seg.append:
            out.append(f"{seg.field}[-]")
        elif seg.index >= 0:
            out.append(f"{seg.field}[#{seg.index}]")
        elif seg.key:
            out.append(f"{seg.field}[{seg.key}={escape_value(seg.value)}]")
        else:
            out.append(seg.field)
    return ".".join(out)


def escape_value(value: str) -> str:
    """Encode a selector value for inclusion in a path.

    Exposed because the registry-driven path *builders* live elsewhere, and a
    caller that assembles a path by hand must not have to know which characters
    are grammar.

    ``quote(safe="")`` handles ``%``, ``[``, ``]`` and ``=``, but **not** ``.`` —
    the dot is in urllib's always-safe set and is never escaped no matter what
    ``safe`` says. It is also our path separator, so a project called
    "Release 1.0" would split into two segments. Hence the explicit pass.
    """
    return quote(value, safe="").replace(".", "%2E")


@dataclass(frozen=True)
class Target:
    """Where a resolved path points: a container and a key into it.

    Returning the container rather than the value is what lets a caller read,
    replace, append and remove through one resolution, and it keeps the mutation
    itself in :mod:`yeaboi.artifacts.edits` where the validation is.

    ``key`` is a ``str`` for a mapping and an ``int`` for a list. ``append`` says
    the path named the slot *after* the last element, in which case ``key`` is
    the length of the list and reading it is an error.
    """

    container: Any
    key: str | int
    append: bool = False

    def get(self) -> Any:
        """Return the addressed value. Raises for an append slot — nothing is there yet."""
        if self.append:
            raise PathError("append slot has no current value")
        return self.container[self.key]

    def exists(self) -> bool:
        """True when the addressed value is present and readable."""
        if self.append:
            return False
        if isinstance(self.key, int):
            return 0 <= self.key < len(self.container)
        return self.key in self.container


def _select(items: list, seg: Segment, list_keys: dict[str, str]) -> int | None:
    """Return the index a selector picks out of ``items``, or None if absent."""
    if seg.append:
        return len(items)
    if seg.index >= 0:
        return seg.index if 0 <= seg.index < len(items) else None
    natural = list_keys.get(seg.field)
    if not natural:
        # An identity selector on a list the registry gave no key for. Refusing
        # rather than falling back to a scan: a list without a declared key has
        # no field we are willing to promise is unique, and a "helpful" guess
        # here is exactly how an edit lands on the wrong row.
        raise PathError(f"{seg.field} has no identity key")
    for i, item in enumerate(items):
        if isinstance(item, dict) and str(item.get(natural, "")) == seg.value:
            return i
    return None


def resolve(tree: Any, segments: tuple[Segment, ...], list_keys: dict[str, str]) -> Target | None:
    """Walk ``segments`` into ``tree``, returning where they point, or None.

    ``tree`` is an ``asdict`` tree — nested dicts, lists and scalars — never the
    frozen dataclasses themselves. ``list_keys`` maps a list field name to the
    natural key its elements are addressed by, and comes from the artifact's
    registry row.

    None means "the path is well-formed but names nothing here", which is a
    different answer from :class:`PathError` ("this is not a path") and is
    treated differently by the caller: the first is a stale edit, the second is
    a malformed request.
    """
    node = tree
    for depth, seg in enumerate(segments):
        last = depth == len(segments) - 1
        if not isinstance(node, dict) or seg.field not in node:
            return None
        if not seg.selects:
            if last:
                return Target(container=node, key=seg.field)
            node = node[seg.field]
            continue
        items = node[seg.field]
        if not isinstance(items, list):
            return None
        index = _select(items, seg, list_keys)
        if index is None:
            return None
        if last:
            return Target(container=items, key=index, append=seg.append)
        if seg.append or index >= len(items):
            # A non-terminal append slot would mean "descend into the element
            # that does not exist yet". There is nothing sensible to return.
            return None
        node = items[index]
    return None
