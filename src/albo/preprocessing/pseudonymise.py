"""Minimisation of personal data (Gantt task 18).

Heuristic and regex-based: it removes structured identifiers (tax code, IBAN, e-mail, phone
numbers) and names introduced by an honorific or by a legal cue ("a carico di ...").
It does NOT guarantee that all personal data is removed: sample the output by hand."""
from __future__ import annotations

import re

_WORD = r"(?:[A-ZÀ-Ý][a-zà-ÿ']+|[A-ZÀ-Ý]{2,})"
_NAME = rf"{_WORD}(?:\s+{_WORD}){{0,2}}"

_RULES: list[tuple[str, re.Pattern, str]] = [
    ("cf", re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", re.I), "[CF]"),
    ("iban", re.compile(r"\bIT\d{2}(?:\s?[0-9A-Z]){23}\b", re.I), "[IBAN]"),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[EMAIL]"),
    ("phone", re.compile(r"(?i)\b(?:tel|telefono|cell|cellulare|fax)\.?\s*:?\s*\+?\d[\d\s./-]{6,16}\d"), "[TEL]"),
    (
        "name",
        re.compile(
            r"\b(?P<cue>(?:Sig\.?ra|Sig\.?|Dott\.?ssa|Dott\.?|Ing\.?|Avv\.?|Geom\.?|Arch\.?|Rag\.?|Prof\.?ssa|Prof\.?"
            r"|a carico di|nei confronti di|intestat[oa] a|richiedente)\s+)" + _NAME
        ),
        r"\g<cue>[NOME]",
    ),
]


def pseudonymise(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for label, pattern, replacement in _RULES:
        text, n = pattern.subn(replacement, text)
        if n:
            counts[label] = n
    return text, counts
