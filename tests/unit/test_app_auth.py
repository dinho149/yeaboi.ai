"""Bearer-token auth for the desktop backend."""

from __future__ import annotations

from yeaboi.app.auth import check_bearer, mint_token


class TestMintToken:
    def test_tokens_are_long_and_unique(self):
        a, b = mint_token(), mint_token()
        assert a != b
        assert len(a) >= 40  # 32 url-safe bytes encode to 43 chars


class TestCheckBearer:
    def test_correct_token_passes(self):
        assert check_bearer({"Authorization": "Bearer tok"}, "tok") is True

    def test_lowercase_header_passes(self):
        assert check_bearer({"authorization": "Bearer tok"}, "tok") is True

    def test_wrong_token_fails(self):
        assert check_bearer({"Authorization": "Bearer nope"}, "tok") is False

    def test_missing_header_fails(self):
        assert check_bearer({}, "tok") is False

    def test_non_bearer_scheme_fails(self):
        assert check_bearer({"Authorization": "Basic tok"}, "tok") is False

    def test_empty_configured_token_never_authenticates(self):
        # Two empty strings compare equal — "no token yet" must not be a pass.
        assert check_bearer({"Authorization": "Bearer "}, "") is False
