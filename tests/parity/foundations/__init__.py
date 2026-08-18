"""W8 foundations parity gate — golden-subprocess diff of the paths/config surface.

``matrix.py`` holds the environment fixtures, ``dump.py`` is the Python-side
dumper (run one subprocess per fixture — the surface resolves at import time),
and the committed goldens under ``tests/parity/goldens/foundations/`` are what
both sides must reproduce: the Python freeze test byte-for-byte, and
``go/internal/home``'s golden test structurally. See
``cowork/migration/program.md`` §7.
"""
