"""Document taxonomy (Gantt task 7) and fields to extract (task 9)."""
from __future__ import annotations

from enum import Enum


class DocType(str, Enum):
    DELIBERATION = "DELIBERATION"
    DETERMINATION = "DETERMINATION"
    ORDINANCE = "ORDINANCE"
    OTHER = "OTHER"
    UNCERTAIN = "UNCERTAIN"


# Types for which the four base fields are expected.
CORE_TYPES = (DocType.DELIBERATION, DocType.DETERMINATION, DocType.ORDINANCE)

FIELDS = ("municipality", "number", "date", "subject")

# Keywords found in the type label declared by the notice board.
_DECLARED_KEYWORDS = (
    ("deliber", DocType.DELIBERATION),
    ("determin", DocType.DETERMINATION),
    ("ordinanz", DocType.ORDINANCE),
)


def map_declared_type(label: str | None) -> DocType | None:
    """Map the free-text type declared by the board to the taxonomy (None if unknown)."""
    if not label:
        return None
    low = label.casefold()
    for keyword, doc_type in _DECLARED_KEYWORDS:
        if keyword in low:
            return doc_type
    return None
