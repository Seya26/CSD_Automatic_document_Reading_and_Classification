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

DELIBERATION_FIELDS = ("classifica", "organo_deliberante", "esito_votazione", "firmatari")

DETERMINATION_FIELDS = ("ufficio_responsabile", "firmatario")

ORDINANCE_FIELDS = ("organo_emittente", "luoghi", "valida_dal", "valida_al", "prescrizioni",
                    "firmatario", "responsabile_procedimento")
DETAIL_FIELDS = set(DELIBERATION_FIELDS + DETERMINATION_FIELDS + ORDINANCE_FIELDS)


def record_fields(doc_type: str) -> tuple[str, ...]:
    common = FIELDS
    if doc_type == DocType.DELIBERATION:
        return (*common, *DELIBERATION_FIELDS)
    if doc_type == DocType.DETERMINATION:
        return (*common, *DETERMINATION_FIELDS)
    if doc_type == DocType.ORDINANCE:
        return (*common, *ORDINANCE_FIELDS)
    return common


# Keywords found in the type label declared by the notice board.
_DECLARED_KEYWORDS = (
    ("deliber", DocType.DELIBERATION),
    ("determin", DocType.DETERMINATION),
    ("ordinanz", DocType.ORDINANCE),
    # labels of documents outside the three core types
    ("avvis", DocType.OTHER),
    ("bando", DocType.OTHER),
    ("bandi", DocType.OTHER),
    ("decret", DocType.OTHER),
    ("graduator", DocType.OTHER),
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
