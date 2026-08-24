"""``python -m yeaboi`` — the same CLI as the ``yeaboi`` console script.

The desktop app spawns its backend this way. A console script carries an
absolute shebang written when it was installed, and the bundle it lives in
moves — to /Applications, to wherever the user dragged it — so the only
reliable way to start it is through the interpreter that is already running.
"""

from yeaboi.cli import main

if __name__ == "__main__":
    main()
