"""The random-name word lists every browser join screen offers.

Cosmetic and client-only: the 🎲 button on the profile modal pairs one of each
so a teammate who does not want to type their real name into a shared board
still ends up as somebody rather than as "anon". Both live boards ship these in
their boot island, and the TUI uses the same pair, so a session reads the same
whichever surface someone joined from.

A leaf module for the same reason :mod:`yeaboi.music` is one — it is shared
data with no behaviour, and it belongs to neither mode. It lived in
``retro/page.py`` while that file was a 1178-line browser page, which meant
``poker/page.py`` imported a private name out of another mode's *renderer* to
get at it. That was the last cross-mode page coupling, and deleting the
hand-written pages is what made it removable.
"""

from __future__ import annotations

# Kept tasteful-but-silly. Order is not meaningful; the client picks uniformly.
ADJECTIVES: tuple[str, ...] = (
    "Sexy",
    "Ghost",
    "Cosmic",
    "Sneaky",
    "Turbo",
    "Feral",
    "Velvet",
    "Rogue",
    "Disco",
    "Thunder",
    "Silent",
    "Funky",
    "Midnight",
    "Wild",
    "Neon",
    "Grumpy",
    "Cyber",
    "Lonesome",
    "Radical",
    "Mystic",
    "Spicy",
    "Chrome",
    "Groovy",
    "Danger",
)
NOUNS: tuple[str, ...] = (
    "Cowboy",
    "Dude",
    "Llama",
    "Wizard",
    "Raccoon",
    "Pirate",
    "Ninja",
    "Yeti",
    "Goblin",
    "Falcon",
    "Panda",
    "Viking",
    "Phantom",
    "Otter",
    "Bandit",
    "Comet",
    "Walrus",
    "Samurai",
    "Gecko",
    "Nomad",
    "Badger",
    "Wombat",
    "Sphinx",
    "Hologram",
)
