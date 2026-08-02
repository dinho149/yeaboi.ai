"""Every key the browser sends must be a key the server reads.

The wire fixtures in ``test_web_wire_shapes.py`` pin the *response* direction.
This is the other one, and it is the direction that fails silently.

A response the client mistypes blows up visibly — a board renders ``undefined``
and someone notices in the first minute. A **request** key the client mistypes
does not: ``payload.get("seconds", 90)`` simply returns its default, so the host
picks a 60-second turn, the room gets 90, and nothing anywhere reports a
problem. That bug was live in the React poker port for exactly as long as it
took to open the floor and read the clock.

So: parse the request bodies out of the TypeScript action layers, parse the keys
each route reads out of the Python handlers, and require the first to be a
subset of the second. Subset rather than equality, because a handler may
legitimately read keys the client never sends — ``/api/join`` reads ``admin``,
which only the host's own link carries.

``pid`` and ``admin`` are excluded: ``runtime/api.postJSON`` merges them into
every body, so they never appear in an action's own literal.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"

pytestmark = pytest.mark.skipif(not FRONTEND.is_dir(), reason="frontend sources are not part of an installed wheel")

# (action layer, the server module whose routes it posts to)
PAIRS = [
    ("poker/actions.ts", "src/yeaboi/poker/server.py"),
    ("retro/actions.ts", "src/yeaboi/retro/server.py"),
    # The editable shared document. Its presence heartbeat goes through an
    # actions.ts rather than through hooks/useHeartbeat precisely so this guard
    # can see it — the shared hook spells its wire keys inside itself, which is
    # why the boards' own heartbeat keys are invisible here.
    ("export/actions.ts", "src/yeaboi/sharing/server.py"),
    # The other thing an export can send back: a reader's verdict on a practice
    # signal. Same server, separate module — an edit answers with fresh state
    # through the board runtime, a vote is a bare token-carrying POST. Same
    # guard for the same reason: a mistyped key here would drop the note and
    # record an unexplained verdict.
    ("export/vote.ts", "src/yeaboi/sharing/server.py"),
]

# Merged into every body by postJSON, so no action names them itself.
IMPLICIT = {"pid", "admin"}

# Keys sent as a nested object; the handler reads the outer name and destructures
# the inside itself, so only the outer name is checkable here.
NESTED = {"music"}


def _server_keys(server_src: str) -> dict[str, set[str]]:
    """Map each POST route to every payload key its handler reads.

    A route's handler is the block from its ``if path == "..."`` line to the
    next one, plus the body of any ``self._method(payload, ...)`` it delegates
    to — which is where the interesting keys live (``_finalize``,
    ``_ticket_edit``, ``_duel_open`` all take the payload and read it).

    ``/api/timer`` is the awkward one on both servers: the same admin guard
    admits it as ``/api/admin/*``, and then it is handled by the *fall-through*
    at the end of the dispatch with no ``if path ==`` line of its own. So every
    route that guard admits gets the whole admin region's keys. That is an
    over-approximation, and it is the safe direction — this test only ever
    rejects a key that nothing reads.
    """
    methods = {
        match.group(1): match.group(2)
        for match in re.finditer(r"\n    def (_\w+)\(self, payload: dict.*?\n(.*?)(?=\n    def |\Z)", server_src, re.S)
    }

    def read_by(block: str) -> set[str]:
        found = set(re.findall(r'payload\.get\("([^"]+)"', block))
        for name in re.findall(r"self\.(_\w+)\(payload", block):
            found |= set(re.findall(r'payload\.get\("([^"]+)"', methods.get(name, "")))
        return found

    starts = sorted(
        [(m.start(), m.group(1)) for m in re.finditer(r'if path == "(/api/[^"]+)"', server_src)]
        + [(m.start(), m.group(1)) for m in re.finditer(r'if path\.startswith\("(/api/[^"]+)"', server_src)]
    )
    keys: dict[str, set[str]] = {}
    for index, (offset, path) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(server_src)
        keys[path] = read_by(server_src[offset:end])

    # The admin guard, in either of the two forms the servers write it.
    guard = re.search(r'if path(?:\.startswith\("/api/admin/"\)| in \([^)]*\)).*', server_src)
    if guard:
        region = read_by(server_src[guard.start() :])
        admitted = set(re.findall(r'"(/api/[^"]+)"', guard.group(0)))
        admitted |= {path for path in keys if path.startswith("/api/admin/")}
        admitted.add("/api/timer")
        for path in admitted:
            keys[path] = keys.get(path, set()) | region

    keys.pop("/api/admin/", None)
    return keys


def _action_bodies(ts_src: str) -> list[tuple[str, set[str]]]:
    """Every ``('/api/path', {keys...})`` an action layer posts.

    Handles the three literal forms in use: ``{ value }`` shorthand,
    ``{ action: 'start', duration: seconds }``, and a spread of a typed edit
    object, whose fields come from its own interface rather than the call site.
    """
    bodies: list[tuple[str, set[str]]] = []
    for match in re.finditer(r"""mutate\(\s*['"](/api/[^'"]+)['"]\s*(?:,\s*(\{.*?\}))?\s*\)""", ts_src, re.S):
        path, literal = match.group(1), match.group(2) or "{}"
        keys: set[str] = set()
        # `key:` and bare `key,` / `key }` shorthand, at the literal's top level.
        depth = 0
        token = ""
        for char in literal[1:-1] + ",":
            if char in "{[(":
                depth += 1
            elif char in "}])":
                depth -= 1
            if char == "," and depth == 0:
                part = token.strip()
                if part and not part.startswith("..."):
                    keys.add(part.split(":")[0].strip())
                token = ""
            else:
                token += char
        # A spread carries the fields of the type it spreads, so resolve the
        # variable to its declared interface. Matched by *name*: an earlier
        # version grabbed the file's first `interface`, which was the response
        # envelope, and so reported that the client sends `ok` and `state`.
        for spread in re.findall(r"\.\.\.(\w+)", literal):
            hint = re.search(rf"\b{re.escape(spread)}\s*:\s*(\w+)", ts_src)
            declared = re.search(rf"interface {hint.group(1)} \{{(.*?)^\}}", ts_src, re.S | re.M) if hint else None
            assert declared, f"cannot resolve the type spread as ...{spread}"
            keys |= set(re.findall(r"^\s*(\w+)\??:", declared.group(1), re.M))
        bodies.append((path, keys - IMPLICIT))
    return bodies


@pytest.mark.parametrize(("actions", "server"), PAIRS)
def test_every_key_the_client_sends_is_read_by_the_server(actions: str, server: str):
    ts_path = FRONTEND / actions
    ts_src = ts_path.read_text(encoding="utf-8")
    server_keys = _server_keys((ROOT / server).read_text(encoding="utf-8"))

    bodies = _action_bodies(ts_src)
    assert bodies, f"parsed no request bodies out of {actions} — the parser is broken, not the code"

    unread: list[str] = []
    for path, keys in bodies:
        known = server_keys.get(path)
        if known is None:
            unread.append(f"{actions}: POSTs to {path}, which {server} has no handler for")
            continue
        for key in sorted(keys - known - NESTED):
            unread.append(f"{actions}: sends {key!r} to {path}, which the handler never reads")

    assert not unread, "\n".join(unread)
