"""Minimisation of personal data (Gantt task 18).

Mask structured identifiers (tax code, IBAN, e-mail and phone numbers) in extracted text."""
from __future__ import annotations

import re

_RULES: list[tuple[str, re.Pattern, str]] = [
    ("cf", re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", re.I), "[CF]"),
    ("iban", re.compile(r"\bIT\d{2}(?:\s?[0-9A-Z]){23}\b", re.I), "[IBAN]"),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[EMAIL]"),
    ("phone", re.compile(r"(?i)\b(?:tel|telefono|cell|cellulare|fax)\.?\s*:?\s*\+?\d[\d\s./-]{6,16}\d"), "[TEL]"),
]


def pseudonymise(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for label, pattern, replacement in _RULES:
        text, n = pattern.subn(replacement, text)
        if n:
            counts[label] = n
    return text, counts
