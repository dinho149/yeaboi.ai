"""Tests for the screensaver's duck yard.

The interesting properties are all invariants of the simulation rather than
outputs anyone can eyeball, and every one of them broke at least once while it
was being built: ducks escaping the frame during a pile-up, the hero drifting,
the whole thing silently becoming non-reproducible.
"""

from __future__ import annotations

import math
import random

import pytest

from yeaboi.ui.shared import _mayhem


@pytest.fixture(autouse=True)
def _reset_scale():
    """The module keeps sprite geometry in globals, so a test that changes the
    scale would otherwise leak into every test after it."""
    _mayhem.configure(1)
    yield
    _mayhem.configure(1)


def _yard(width=120, height=80, count=8, seed=3):
    return _mayhem.make_ducks(count, width, height, random.Random(seed))


def _run(ducks, width=120, height=80, steps=600):
    now, dt = 0.0, 1 / 60
    for _ in range(steps):
        _mayhem.step(ducks, dt, now, width, height)
        now += dt
    return ducks


class TestSimulation:
    def test_same_seed_gives_the_same_yard(self):
        a = _run(_yard())
        b = _run(_yard())
        assert [(round(d.x, 6), round(d.y, 6), round(d.angle, 6)) for d in a] == [
            (round(d.x, 6), round(d.y, 6), round(d.angle, 6)) for d in b
        ]

    def test_different_seeds_differ(self):
        a = _run(_yard(seed=1))
        b = _run(_yard(seed=2))
        assert [(d.x, d.y) for d in a] != [(d.x, d.y) for d in b]

    def test_nobody_leaves_the_frame(self):
        """Collision separation runs after the wall clamp, so a duck shoved by a
        neighbour can be pushed through the edge. It is clamped again last."""
        width, height = 120, 80
        ducks = _yard(width, height)
        margin = _mayhem.SPRITE_SIZE / 2
        now, dt = 0.0, 1 / 60
        for _ in range(900):
            _mayhem.step(ducks, dt, now, width, height)
            now += dt
            for duck in ducks:
                if duck.anchored:
                    continue
                assert margin - 0.001 <= duck.x <= width - margin + 0.001
                assert margin - 0.001 <= duck.y <= height - margin + 0.001

    def test_the_hero_does_not_move(self):
        ducks = _yard()
        hero = next(d for d in ducks if d.is_hero)
        before = (hero.x, hero.y)
        _run(ducks)
        assert (hero.x, hero.y) == before

    def test_energy_is_not_lost(self):
        """Zero gravity and elastic bounces: nothing puts energy back, so any
        damping anywhere makes the yard visibly wind down inside a clip."""
        ducks = _yard()
        loose = [d for d in ducks if not d.anchored]
        before = sum(math.hypot(d.vx, d.vy) for d in loose)
        _run(ducks)
        after = sum(math.hypot(d.vx, d.vy) for d in loose)
        assert after == pytest.approx(before, rel=0.2)

    def test_ducks_start_spread_out(self):
        """Stratified placement, not uniform: twelve uniform samples clump, and
        the first version left a third of the yard empty."""
        width = 200
        loose = [d for d in _yard(width=width, count=12) if not d.anchored]
        left = sum(1 for d in loose if d.x < width / 2)
        assert 4 <= left <= 8

    def test_ducks_head_towards_the_middle(self):
        """A corner duck with a random heading rattles around in its corner for
        the whole session and never meets anything."""
        width, height = 200, 120
        loose = [d for d in _yard(width, height, count=10) if not d.anchored]
        for duck in loose:
            towards = (width / 2 - duck.x) * duck.vx + (height / 2 - duck.y) * duck.vy
            assert towards > 0


class TestImpacts:
    def test_a_collision_opens_beaks_and_squashes(self):
        a = _mayhem.Duck(x=50.0, y=50.0, vx=40.0, vy=0.0)
        b = _mayhem.Duck(x=50.0 + _mayhem.DUCK_RADIUS, y=50.0, vx=-40.0, vy=0.0)
        _mayhem.step([a, b], 1 / 60, 10.0, 200, 200)
        assert a.quack_until > 10.0 and b.quack_until > 10.0
        assert a.squish_until > 10.0 and b.squish_until > 10.0

    def test_squash_flattens_along_the_contact_normal(self):
        """A ball hitting a wall goes flat against the wall, not along its own
        axis — so the two must record opposite normals."""
        a = _mayhem.Duck(x=50.0, y=50.0, vx=40.0, vy=0.0)
        b = _mayhem.Duck(x=50.0 + _mayhem.DUCK_RADIUS, y=50.0, vx=-40.0, vy=0.0)
        _mayhem.step([a, b], 1 / 60, 10.0, 200, 200)
        half = _mayhem.NORMAL_STEPS // 2
        assert abs(a.squish_normal - b.squish_normal) == half

    def test_the_hero_never_squashes(self):
        """An immovable thing that visibly gives on impact reads as wrong."""
        ducks = _yard()
        hero = next(d for d in ducks if d.is_hero)
        _run(ducks)
        assert hero.squish_until < 0


class TestSprites:
    def test_every_variant_is_the_same_size(self):
        """Collision geometry is derived once, so a sprite whose footprint
        changed with its angle or squash would drift out of its own hitbox."""
        sizes = {
            len(_mayhem.squashed(angle, level, normal, quack))
            for angle in (0, 7, 23, 41)
            for level in range(len(_mayhem.SQUISH_CURVE) + 1)
            for normal in (0, 5, 11)
            for quack in (False, True)
        }
        assert sizes == {_mayhem.SPRITE_SIZE}

    def test_rotation_keeps_the_duck(self):
        """Nearest-neighbour rotation drops pixels; it must not drop most of
        them. The sunglasses vanishing is what made 1x unusable."""
        upright = sum(row.count(ch) for row in _mayhem.squashed(0, 0, 0, False) for ch in set(row) if ch != ".")
        for angle in (6, 12, 18, 24, 36):
            turned = sum(row.count(ch) for row in _mayhem.squashed(angle, 0, 0, False) for ch in set(row) if ch != ".")
            assert turned > upright * 0.7

    def test_the_hero_holds_completely_still(self):
        """He is hit several times a second, so a reacting hero flaps
        constantly — and beside a yard that is already all motion there is
        nowhere for the eye to rest."""
        hero = next(d for d in _yard() if d.is_hero)
        hero.quack_until = 1e9  # pretend he is being hit without pause
        frames = {hero.sprite(t / 60) for t in range(240)}
        assert len(frames) == 1

    def test_the_gag_still_works_when_switched_back_on(self):
        """HERO_STATIC is a setting, not a deletion."""
        _mayhem.HERO_STATIC = False
        try:
            lifts = {_mayhem.hero_lift(t / 60) for t in range(int(_mayhem.HERO_SHADES_EVERY * 60))}
            assert max(lifts) >= 3 and 0 in lifts
        finally:
            _mayhem.HERO_STATIC = True

    def test_hero_frames_are_all_one_size(self):
        """compose() centres a sprite on its duck, so a grid that changed height
        would bob him up and down on every impact."""
        frames = [
            _mayhem.hero_grid(t / 60, quacking=q)
            for t in range(int(_mayhem.HERO_SHADES_EVERY * 60))
            for q in (False, True)
        ]
        assert len({(len(f), len(f[0])) for f in frames}) == 1


class TestRender:
    def test_renders_and_advances(self):
        _mayhem.render(120, 40, 0.0)
        _mayhem.render(120, 40, 1.0)
        assert _mayhem._yard_at == pytest.approx(1.0, abs=0.05)

    def test_rewinding_starts_a_new_session(self):
        """Every screensaver session restarts its clock at zero — backwards is
        the ordinary case here, not an error."""
        _mayhem.render(120, 40, 2.0)
        _mayhem.render(120, 40, 0.0)
        assert _mayhem._yard_at < 0.5

    def test_a_long_gap_does_not_simulate_it_all(self):
        """A session resumed after the machine slept would otherwise try to
        catch up hours of simulation in one frame and hang the UI."""
        _mayhem.render(120, 40, 0.0)
        _mayhem.render(120, 40, 100_000.0)
        assert _mayhem._yard_at < 10

    def test_resizing_rebuilds(self):
        _mayhem.render(120, 40, 1.0)
        _mayhem.render(60, 20, 1.0)
        assert _mayhem._yard_key[0] == 60

    def test_bigger_terminals_hold_more_ducks(self):
        assert _mayhem.fits(240, 160) > _mayhem.fits(80, 40)

    def test_scale_changes_the_sprite_but_not_the_geometry_contract(self):
        _mayhem.configure(2)
        assert len(_mayhem.SOURCE[0]) == 32
        assert _mayhem.SPRITE_SIZE == max(len(g) for g in _mayhem.squashed(3, 2, 4, False))
