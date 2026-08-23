"""Volatile-content detection for prompt-cache health signals.

Vendored and adapted from Headroom (https://github.com/headroomlabs-ai/headroom),
the detector half of ``headroom/transforms/cache_aligner.py`` at v0.35.0 —
Copyright Headroom contributors, licensed under the Apache License, Version 2.0
(see THIRD_PARTY_NOTICES.md). Changes from upstream: only the structural
classifiers were taken (no transform pipeline, no prefix-hash tracking), and
findings carry **label counts only** — upstream keeps a truncated sample per
finding; yeaboi's agentwatch invariant is that scanned content never leaves the
scan, so not even a truncated sample is returned.

Content that *looks* generated — UUIDs, timestamps, JWT-shaped strings, hex
digests — sitting in a file that feeds an agent's prompt prefix (CLAUDE.md and
friends) is an indicator that the prefix churns between sessions, which breaks
provider prompt-cache reuse. Detection is structural (stdlib parsers, no
regex): an indicator, not proof, and the advisor report says so.
"""

from __future__ import annotations

import base64
import binascii
import uuid as _uuid

from yeaboi.timeparse import parse_datetime

# Length profile for hex hash detection: MD5 = 32 hex chars, SHA1 = 40,
# SHA256 = 64.
_HEX_HASH_LENGTHS = frozenset({32, 40, 64})

# Canonical UUID (RFC 4122) with dashes is 36 chars. The 32-char dashless form
# is deliberately NOT accepted — it is structurally identical to an MD5 digest
# and would misclassify a hash as a UUID.
_UUID_CANONICAL_LEN = 36

# JWT shape: exactly three base64url segments joined by ".". Shape only —
# no signature verification (there is no key, and this is detection).
_JWT_SEGMENT_COUNT = 3
_JWT_MIN_SEGMENT_BYTES = 4

# Classification labels — keep stable; they are rendered and exported.
LABEL_UUID = "uuid"
LABEL_ISO8601 = "iso8601"
LABEL_JWT = "jwt"
LABEL_HEX_HASH = "hex_hash"

# Alignment-score weights. Upstream uses a flat 10 points per finding, which
# saturates to 0/100 the moment one file carries ten dated changelog lines —
# and a score that is always 0 stops being read. Per-file, capped: each file
# costs 5 points per finding up to 20, so one noisy file cannot claim the
# whole scale and a genuinely clean setup still scores 100.
_SCORE_PENALTY_PER_FINDING = 5
_SCORE_MAX_PENALTY_PER_FILE = 20


def _is_uuid(token: str) -> bool:
    """True when ``token`` parses as a canonical dashed UUID."""
    if len(token) != _UUID_CANONICAL_LEN or token.count("-") != 4:
        return False
    try:
        _uuid.UUID(token)
    except (ValueError, AttributeError):
        return False
    return True


def _is_iso8601(token: str) -> bool:
    """True when ``token`` parses as an ISO 8601 datetime."""
    if len(token) < 8:
        return False
    if "T" not in token and "-" not in token:
        return False
    candidate = token[:-1] + "+00:00" if token.endswith("Z") else token
    try:
        parse_datetime(candidate)
    except (ValueError, TypeError):
        return False
    return True


def _is_jwt_shape(token: str) -> bool:
    """True when ``token`` has the three-segment base64url shape of a JWT.

    Beyond upstream's segment checks, the first segment must decode to JSON
    (start with ``{``) — a real JWT header always does (the familiar ``eyJ``
    prefix IS base64 of ``{"``), and without it any dotted identifier whose
    parts happen to be base64url-decodable (``yeaboi.agentwatch.advisor``,
    ``make.test.scoped``) classifies as a JWT and the cache-health signal
    fires on every module path in a CLAUDE.md.
    """
    segments = token.split(".")
    if len(segments) != _JWT_SEGMENT_COUNT:
        return False
    decoded: list[bytes] = []
    for seg in segments:
        if len(seg) < _JWT_MIN_SEGMENT_BYTES:
            return False
        padded = seg + "=" * (-len(seg) % 4)
        try:
            decoded.append(base64.urlsafe_b64decode(padded.encode("ascii")))
        except (binascii.Error, ValueError, UnicodeEncodeError):
            return False
    return decoded[0].startswith(b"{")


def _is_hex_hash(token: str) -> bool:
    """True when ``token`` looks like an MD5/SHA1/SHA256 hex digest."""
    if len(token) not in _HEX_HASH_LENGTHS:
        return False
    try:
        int(token, 16)
    except ValueError:
        return False
    return True


def _classify_token(token: str) -> str | None:
    """Label a token if it matches a volatile pattern; order is most-specific first."""
    if _is_uuid(token):
        return LABEL_UUID
    if "." in token and _is_jwt_shape(token):
        return LABEL_JWT
    if _is_iso8601(token):
        return LABEL_ISO8601
    if _is_hex_hash(token):
        return LABEL_HEX_HASH
    return None


def _split_tokens(content: str) -> list[str]:
    """Whitespace-split, stripping the punctuation that commonly wraps a token."""
    if not content:
        return []
    tokens: list[str] = []
    for raw in content.split():
        cleaned = raw.strip(".,;:!?\"'()[]{}<>")
        if cleaned:
            tokens.append(cleaned)
    return tokens


def count_volatile(content: str) -> dict[str, int]:
    """Count volatile-shaped tokens in text, by label. Counts only — never content."""
    counts: dict[str, int] = {}
    for token in _split_tokens(content):
        label = _classify_token(token)
        if label is not None:
            counts[label] = counts.get(label, 0) + 1
    return counts


def alignment_score(per_file_totals: list[int] | tuple[int, ...]) -> int:
    """0-100 cache-alignment score from each scanned file's finding count.

    Per-file penalty, capped per file (see the weight constants) — a coarse
    dashboard signal, deliberately hard to saturate.
    """
    penalty = sum(min(_SCORE_MAX_PENALTY_PER_FILE, n * _SCORE_PENALTY_PER_FINDING) for n in per_file_totals)
    return max(0, min(100, 100 - penalty))
