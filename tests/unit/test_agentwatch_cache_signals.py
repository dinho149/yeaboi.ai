"""Tests for src/yeaboi/agentwatch/cache_signals.py — volatile-content detection."""

import base64

from yeaboi.agentwatch import cache_signals


def _fake_jwt() -> str:
    """A structurally valid JWT built at runtime — a literal would trip gitleaks."""

    def seg(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return ".".join((seg(b'{"alg":"HS256"}'), seg(b'{"sub":"x"}'), seg(b"signature")))


class TestClassifiers:
    def test_canonical_uuid(self):
        assert cache_signals._classify_token("3f2b8a9e-1c4d-4e6f-9a0b-2c3d4e5f6a7b") == "uuid"

    def test_dashless_uuid_reads_as_hex_hash(self):
        # 32 hex chars are structurally an MD5 digest — deliberately not a UUID.
        assert cache_signals._classify_token("d41d8cd98f00b204e9800998ecf8427e") == "hex_hash"

    def test_iso8601_variants(self):
        assert cache_signals._classify_token("2026-08-16T10:00:00Z") == "iso8601"
        assert cache_signals._classify_token("2026-08-16") == "iso8601"

    def test_jwt_shape(self):
        assert cache_signals._classify_token(_fake_jwt()) == "jwt"

    def test_sha_lengths(self):
        assert cache_signals._classify_token("a" * 40) == "hex_hash"
        assert cache_signals._classify_token("0" * 64) == "hex_hash"
        assert cache_signals._classify_token("a" * 41) is None

    def test_plain_words_are_not_volatile(self):
        for token in ("hello", "make", "test-scoped", "v1.2.3", "12345"):
            assert cache_signals._classify_token(token) is None

    def test_dotted_identifiers_are_not_jwts(self):
        # Regression: any three base64url-decodable segments used to classify
        # as a JWT, so every module path in a CLAUDE.md fired the signal. A
        # real JWT's first segment decodes to JSON; these do not.
        for token in ("yeaboi.agentwatch.advisor", "make.test.scoped", "one.two.three"):
            assert cache_signals._classify_token(token) is None


class TestCountVolatile:
    def test_counts_by_label_and_strips_punctuation(self):
        text = "Run id: 3f2b8a9e-1c4d-4e6f-9a0b-2c3d4e5f6a7b, at (2026-08-16T10:00:00Z)."
        assert cache_signals.count_volatile(text) == {"uuid": 1, "iso8601": 1}

    def test_returns_counts_never_content(self):
        # The privacy adaptation vs upstream: no samples, not even truncated ones.
        secret_ish = _fake_jwt()
        counts = cache_signals.count_volatile(f"token {secret_ish}")
        flattened = "".join(str(k) + str(v) for k, v in counts.items())
        assert secret_ish not in flattened
        assert counts == {"jwt": 1}

    def test_empty_text(self):
        assert cache_signals.count_volatile("") == {}


class TestAlignmentScore:
    def test_per_file_penalty(self):
        assert cache_signals.alignment_score([]) == 100
        assert cache_signals.alignment_score([2]) == 90
        assert cache_signals.alignment_score([2, 1]) == 85

    def test_one_noisy_file_cannot_claim_the_whole_scale(self):
        # A single file with 50 dated lines caps at 20 points — the score must
        # not saturate to 0 over one changelog, or it stops being read.
        assert cache_signals.alignment_score([50]) == 80
        assert cache_signals.alignment_score([50, 50, 50, 50, 50]) == 0
