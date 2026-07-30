"""Unit tests for reporting/branding — the packaged yeaboi duck mark."""

import pytest

from yeaboi.reporting import branding


@pytest.fixture(autouse=True)
def _fresh_cache():
    # lru_cache would leak a monkeypatched failure (or success) across tests.
    branding.duck_png.cache_clear()
    branding.duck_data_uri.cache_clear()
    yield
    branding.duck_png.cache_clear()
    branding.duck_data_uri.cache_clear()


class TestDuckPng:
    def test_returns_packaged_png_bytes(self):
        data = branding.duck_png()
        assert data is not None
        assert data.startswith(b"\x89PNG")  # PNG magic — a real image, not junk

    def test_missing_asset_returns_none_never_raises(self, monkeypatch):
        def _boom(_pkg):
            raise FileNotFoundError("no assets in this install")

        monkeypatch.setattr("yeaboi.reporting.branding.resources.files", _boom)
        assert branding.duck_png() is None


class TestDuckDataUri:
    def test_data_uri_prefix_and_payload(self):
        uri = branding.duck_data_uri()
        assert uri is not None
        assert uri.startswith("data:image/png;base64,")
        assert len(uri) > 1000  # actual payload, not an empty shell

    def test_missing_asset_propagates_none(self, monkeypatch):
        monkeypatch.setattr("yeaboi.reporting.branding.resources.files", lambda _pkg: (_ for _ in ()).throw(OSError()))
        assert branding.duck_data_uri() is None
