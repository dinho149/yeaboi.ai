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

import base64
import re

from yeaboi.connectors.spec import ACCENT_RE, FAMILIES
from yeaboi.ops.events import EVENT_KINDS

#: What a custom connection may be.
CUSTOM_KINDS: tuple[str, ...] = ("api", "webhook", "mcp")

WEBHOOK_VERIFY_MODES: tuple[str, ...] = ("token", "hmac")

AUTH_SCHEMES: tuple[str, ...] = ("bearer", "basic", "header")

#: Header names a custom auth header may never claim: hop-by-hop and
#: transport-shaping headers, and the cookie jar.
_HEADER_DENYLIST = frozenset({"host", "content-length", "transfer-encoding", "connection", "cookie", "set-cookie"})

_KEY_RE = re.compile(r"^custom_[a-z][a-z0-9_]*$")
_HEADER_RE = re.compile(r"^[A-Za-z0-9-]+$")

SUMMARY_MAX = 90

#: Extra fields beyond the auth scheme — enough for an app key and some config.
EXTRA_FIELDS_MAX = 4

_ENV_SUFFIX_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,29}$")

#: Suffixes the auth schemes and other kinds already derive.
_RESERVED_SUFFIXES = frozenset({"BASE_URL", "TOKEN", "USERNAME", "PASSWORD", "WEBHOOK_SECRET", "URL"})

#: Headers the auth scheme owns — an extra claiming one would silently replace
#: the scheme's credential on every request.
_AUTH_HEADERS = frozenset({"authorization", "proxy-authorization"})

#: Raster only — SVG can script, so it never crosses this gate.
_ICON_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")
ICON_MAX_BYTES = 64 * 1024

_ICON_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "webp": (b"RIFF",),
}


def _icon_problems(icon_data: str) -> list[str]:
    """An icon is empty, or a size-capped raster data URI whose bytes match its mime."""
    if not isinstance(icon_data, str):
        return ["the icon must be a data:image/png, jpeg or webp base64 URI — SVG is not accepted"]
    if len(icon_data) > ICON_MAX_BYTES * 4 // 3 + 64:  # refuse oversize before decoding it
        return [f"the icon must be at most {ICON_MAX_BYTES // 1024}KB — downscale it first"]
    m = _ICON_RE.fullmatch(icon_data)
    if not m:
        return ["the icon must be a data:image/png, jpeg or webp base64 URI — SVG is not accepted"]
    try:
        blob = base64.b64decode(m.group(2), validate=True)
    except (ValueError, TypeError):
        return ["the icon's base64 payload is not decodable"]
    if len(blob) > ICON_MAX_BYTES:
        return [f"the icon must be at most {ICON_MAX_BYTES // 1024}KB — downscale it first"]
    kind = m.group(1)
    if not blob.startswith(_ICON_MAGIC[kind]) or (kind == "webp" and blob[8:12] != b"WEBP"):
        return ["the icon's bytes do not match its declared image type"]
    return []


def _extra_field_problems(spec) -> list[str]:
    """The extra-fields rules: derived-env hygiene and header hygiene."""
    problems: list[str] = []
    extras = spec.extra_fields
    if len(extras) > EXTRA_FIELDS_MAX:
        problems.append(f"at most {EXTRA_FIELDS_MAX} extra fields")
    seen_suffixes: set[str] = set()
    seen_headers = {(spec.header_name or "").lower()} - {""}
    for extra in extras:
        suffix = extra.env_suffix or ""
        if not _ENV_SUFFIX_RE.fullmatch(suffix):
            problems.append("an extra field's env suffix must be UPPER_SNAKE (e.g. APP_KEY)")
        elif suffix in _RESERVED_SUFFIXES:
            problems.append(f"the env suffix {suffix!r} is reserved by the auth scheme")
        elif suffix in seen_suffixes:
            problems.append(f"duplicate extra-field env suffix {suffix!r}")
        seen_suffixes.add(suffix)
        if not (extra.label or "").strip():
            problems.append("every extra field needs a label")
        name = extra.header_name or ""
        if name:
            if not _HEADER_RE.fullmatch(name):
                problems.append("an extra field's header name must be letters, digits and hyphens")
            elif name.lower() in _HEADER_DENYLIST:
                problems.append(f"an extra field's header name may not be {name!r}")
            elif name.lower() in _AUTH_HEADERS:
                problems.append(f"an extra field may not carry {name!r} — the auth scheme owns that header")
            elif name.lower() in seen_headers:
                problems.append(f"duplicate header name {name!r}")
            seen_headers.add(name.lower())
    return problems


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

    if spec.icon_data:
        problems.extend(_icon_problems(spec.icon_data))

    if spec.kind not in CUSTOM_KINDS:
        problems.append(f"kind must be one of: {', '.join(CUSTOM_KINDS)}")

    if spec.kind != "api" and spec.extra_fields:
        problems.append("extra fields belong to the api kind — the webhook and mcp shapes are fixed")

    if spec.kind == "webhook":
        # Inbound-only: no host, no probe, no outbound auth — what matters is
        # how a delivery authenticates and how its rows become events.
        if spec.webhook_verify not in WEBHOOK_VERIFY_MODES:
            problems.append(f"the webhook verify mode must be one of: {', '.join(WEBHOOK_VERIFY_MODES)}")
        if spec.events is None:
            problems.append("a webhook connection needs an events mapping — a delivery it cannot map is noise")
        else:
            if spec.events.kind not in EVENT_KINDS:
                problems.append(f"the event kind must be one of: {', '.join(EVENT_KINDS)}")
            if not (spec.events.title_path or "").strip():
                problems.append("the events mapping needs a title_path")
        clashes = sorted(set(spec.derived_envs()) & set(existing_envs))
        if clashes:
            problems.append(f"derived env(s) already in use: {', '.join(clashes)}")
        return problems

    if spec.kind == "mcp":
        # A server URL and an optional bearer token — the HTTP shape and the
        # events mapping belong to the api and webhook kinds.
        if spec.events is not None:
            problems.append("an MCP connection gathers nothing yet — events belong to the api and webhook kinds")
        if spec.header_name:
            problems.append("a header name only makes sense with the api kind's header scheme")
        clashes = sorted(set(spec.derived_envs()) & set(existing_envs))
        if clashes:
            problems.append(f"derived env(s) already in use: {', '.join(clashes)}")
        return problems

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

    problems.extend(_extra_field_problems(spec))

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
