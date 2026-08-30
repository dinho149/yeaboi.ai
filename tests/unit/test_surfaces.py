"""Tests for the shared surface vocabulary (surfaces.py)."""

from yeaboi.surfaces import ALL_SURFACES, VALID_SURFACES


def test_all_surfaces_is_the_whole_vocabulary():
    assert set(ALL_SURFACES) == VALID_SURFACES


def test_all_surfaces_is_ordered_and_unique():
    # It is a tuple rather than a set because it is a dataclass default: the
    # order reaches a reader, and a duplicate would survive a set comparison.
    assert len(ALL_SURFACES) == len(set(ALL_SURFACES))
    assert ALL_SURFACES == ("tui", "desktop", "web")


def test_changelog_still_re_exports_the_vocabulary():
    # Moving these out of changelog.py must not break its importers.
    from yeaboi import changelog

    assert changelog.VALID_SURFACES is VALID_SURFACES
    assert changelog.ALL_SURFACES is ALL_SURFACES
