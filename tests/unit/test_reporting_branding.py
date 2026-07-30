"""Unit tests for reporting/branding — the packaged yeaboi duck mark.

``TestDuckDataUri`` went with the function: base64ing this PNG existed only to
inline it into the HTML slide deck, which now takes its duck from the bundle.
"""

import pytest

from yeaboi.reporting import branding


@pytest.fixture(autouse=True)
def _fresh_cache():
    # lru_cache would leak a monkeypatched failure (or success) across tests.
    branding.duck_png.cache_clear()
    yield
    branding.duck_png.cache_clear()


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

    def test_read_failure_also_returns_none(self, monkeypatch):
        # The other half of "cosmetics never break an export": the package is
        # there but the file is not readable.
        monkeypatch.setattr("yeaboi.reporting.branding.resources.files", lambda _pkg: (_ for _ in ()).throw(OSError()))
        assert branding.duck_png() is None
