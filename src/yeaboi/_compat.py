"""Names the 3.10 floor still needs. Delete this module when the floor rises to 3.11.

Deliberately import-free beyond the stdlib, and deliberately tiny: everything else
that 3.11 added and this codebase used has been rewritten away rather than aliased.
"""

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum

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


__all__ = ["StrEnum"]
