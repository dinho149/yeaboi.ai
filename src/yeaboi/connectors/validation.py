"""The custom-connection validator — the spec-suite invariants, at runtime.

``test_connectors_spec.py`` guards the built-in descriptors at build time; a
user-created descriptor arrives at runtime, where a test cannot stand. This
module restates the same rules as data checks, plus the custom-only rules a
user-supplied HTTP shape needs (path hygiene, header denylist, no query-param
auth). It is the single gate: the form, the LLM draft and ``--from-json`` all
pass through :func:`descriptor_problems`, and nothing is saved while it returns
anything.
"""

from __future__ import annotations

import re

from yeaboi.connectors.spec import ACCENT_RE, FAMILIES
from yeaboi.ops.events import EVENT_KINDS

#: What a custom connection may be. "webhook" arrives with the receiver.
CUSTOM_KINDS: tuple[str, ...] = ("api",)

AUTH_SCHEMES: tuple[str, ...] = ("bearer", "basic", "header")

#: Header names a custom auth header may never claim: hop-by-hop and
#: transport-shaping headers, and the cookie jar.
_HEADER_DENYLIST = frozenset({"host", "content-length", "transfer-encoding", "connection", "cookie", "set-cookie"})

_KEY_RE = re.compile(r"^custom_[a-z][a-z0-9_]*$")
_HEADER_RE = re.compile(r"^[A-Za-z0-9-]+$")

SUMMARY_MAX = 90


def _path_problems(path: str, what: str) -> list[str]:
    """A request path must be a path — never a URL, a traversal, or markup."""
    problems = []
    if not path.startswith("/"):
        problems.append(f"{what} must start with '/'")
    if "://" in path:
        problems.append(f"{what} must be a path, not a URL")
    if ".." in path:
        problems.append(f"{what} must not traverse ('..')")
    if any(ch.isspace() for ch in path) or any(ord(ch) < 32 for ch in path):
        problems.append(f"{what} must not contain whitespace or control characters")
    return problems


def descriptor_problems(
    spec,
    *,
    existing_keys: frozenset[str],
    existing_envs: frozenset[str],
    existing_accents: frozenset[str],
) -> list[str]:
    """Everything wrong with one CustomSpec, as user-facing lines. Empty = save it.

    ``existing_*`` are the built-in + legacy + other-custom rosters the new
    descriptor must not collide with — identity beats styling, so a key clash
    is fatal while callers may choose to warn on an accent clash at load time.
    """
    problems: list[str] = []

    if not _KEY_RE.fullmatch(spec.key or ""):
        problems.append("key must be a custom_-prefixed slug (e.g. custom_statuspage)")
    elif spec.key in existing_keys:
        problems.append(f"key {spec.key!r} is already taken")

    if not (spec.label or "").strip():
        problems.append("a label is required")

    if spec.family not in FAMILIES:
        problems.append(f"family must be one of: {', '.join(FAMILIES)}")

    summary = (spec.summary or "").strip()
    if not summary:
        problems.append("a one-line summary is required — a name alone is a thing to look up elsewhere")
    elif len(summary) > SUMMARY_MAX or "\n" in summary:
        problems.append(f"the summary must be one line of at most {SUMMARY_MAX} characters")

    accent = spec.accent or ""
    m = ACCENT_RE.match(accent)
    if not m or not all(0 <= int(part) <= 255 for part in m.groups()):
        problems.append("the accent must be rgb(r,g,b)")
    elif accent in existing_accents:
        problems.append("that accent already belongs to another connection")

    if spec.docs_url and not spec.docs_url.startswith("https://"):
        problems.append("the docs link must be https")

    if spec.kind not in CUSTOM_KINDS:
        problems.append(f"kind must be one of: {', '.join(CUSTOM_KINDS)}")

    if spec.auth_scheme not in AUTH_SCHEMES:
        problems.append(f"the auth scheme must be one of: {', '.join(AUTH_SCHEMES)}")
    if spec.auth_scheme == "header":
        name = spec.header_name or ""
        if not _HEADER_RE.fullmatch(name):
            problems.append("the header name must be letters, digits and hyphens")
        elif name.lower() in _HEADER_DENYLIST:
            problems.append(f"the header name may not be {name!r}")
    elif spec.header_name:
        problems.append("a header name only makes sense with the header scheme")

    problems.extend(_path_problems(spec.probe_path or "", "the probe path"))
    if not (200 <= int(spec.probe_ok_status or 0) <= 299):
        problems.append("the probe's expected status must be a 2xx")

    if spec.events is not None:
        problems.extend(_path_problems(spec.events.path or "", "the events path"))
        if spec.events.kind not in EVENT_KINDS:
            problems.append(f"the event kind must be one of: {', '.join(EVENT_KINDS)}")
        if not (spec.events.title_path or "").strip():
            problems.append("the events mapping needs a title_path")

    # The derived envs are the masking and write-allowlist identity — a clash
    # with any existing env would let one connection read or unmask another's.
    clashes = sorted(set(spec.derived_envs()) & set(existing_envs))
    if clashes:
        problems.append(f"derived env(s) already in use: {', '.join(clashes)}")

    return problems
