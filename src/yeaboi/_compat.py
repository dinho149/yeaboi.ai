"""Names the 3.10 floor still needs. Delete this module when the floor rises to 3.11.

Deliberately import-free beyond the stdlib, and deliberately tiny: everything else
that 3.11 added and this codebase used has been rewritten away rather than aliased.
"""

import sys

if sys.version_info >= (3, 11):
    from enum import IntEnum, StrEnum
else:
    from enum import Enum
    from enum import IntEnum as _StdIntEnum

    class StrEnum(str, Enum):
        """3.11's ``enum.StrEnum``, reproduced exactly.

        Both dunders are load-bearing: ``class X(str, Enum)`` alone renders a member
        as ``"X.CODE"`` under ``str()`` *and* ``format()``, so a member serialized
        into an artifact or export would carry its qualified name instead of its
        value. Assigning only ``__str__`` still leaves ``f"{member}"`` wrong.
        """

        __str__ = str.__str__
        __format__ = str.__format__

        @staticmethod
        def _generate_next_value_(name, start, count, last_values):
            return name.lower()

    class IntEnum(_StdIntEnum):
        """3.11's ``enum.IntEnum``, which renders as its value rather than its name.

        3.11 routed ``str()`` and ``format()`` to the mixed-in type; on 3.10
        ``str(member)`` still yields ``"StoryPointValue.THREE"``, which reaches a
        rendered table and a serialized artifact — visible to a user, not internal.

        ``int.__repr__``, not ``int.__str__``: int defines no ``__str__``, so
        ``int.__str__`` is ``object.__str__`` and delegates straight back to Enum's
        own ``__repr__`` — leaving ``str(member)`` as ``"<StoryPointValue.THREE: 3>"``,
        which is worse than the bug it was meant to fix.
        """

        __str__ = int.__repr__
        __format__ = int.__format__


__all__ = ["IntEnum", "StrEnum"]
