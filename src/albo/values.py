"""Normalisation of extracted values, shared by the validator and the evaluation."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})\b")
_WORDS = re.compile(r"\b(\d{1,2})\s*(?:°|º)?\s+([A-Za-zàèéìòù]+)\s+(\d{4})\b")
_NUMBER = re.compile(r"\d+(?:\s*[/\-]\s*\d+)?")


def norm_text(value: str | None) -> str:
    """Casefolded text with punctuation and whitespace collapsed (for comparisons)."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_municipality(value: str | None) -> str:
    text = norm_text(value)
    return re.sub(r"^(comune|citta|città)\s+(di\s+|del\s+|della\s+|dei\s+)?", "", text).strip()


def norm_number(value: str | None) -> str:
    """'N. 045 / 2025' -> '45/2025'. Empty string if no digits."""
    if not value:
        return ""
    match = _NUMBER.search(value)
    if not match:
        return ""
    parts = re.split(r"\s*[/\-]\s*", match.group(0))
    return "/".join(str(int(p)) for p in parts if p)


def _iso(y: int, mo: int, d: int) -> str | None:
    if y < 100:
        y += 2000
    if not 1990 <= y <= 2100:
        return None
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def all_dates(value: str | None) -> list[str]:
    """Every parsable date in the text, as ISO strings, in order of appearance."""
    if not value:
        return []
    text = unicodedata.normalize("NFKC", value)
    found: list[tuple[int, str | None]] = []
    for m in _ISO.finditer(text):
        y, mo, d = (int(g) for g in m.groups())
        found.append((m.start(), _iso(y, mo, d)))
    for m in _NUMERIC.finditer(text):
        d, mo, y = (int(g) for g in m.groups())
        found.append((m.start(), _iso(y, mo, d)))
    for m in _WORDS.finditer(text):
        found.append((m.start(), _iso(int(m.group(3)), _MONTHS.get(m.group(2).casefold(), 0), int(m.group(1)))))
    return [iso for _, iso in sorted(found, key=lambda x: x[0]) if iso]


def number_tokens(value: str | None) -> set[int]:
    """Every integer written in the text ('N. 045 del 12/03/2025' -> {45, 12, 3, 2025})."""
    return {int(t) for t in re.findall(r"\d+", value or "")}


def parse_date(value: str | None) -> str | None:
    """Return an ISO date (YYYY-MM-DD) for the common Italian formats, else None."""
    if not value:
        return None
    text = unicodedata.normalize("NFKC", value)
    try:
        if m := _ISO.search(text):
            y, mo, d = (int(g) for g in m.groups())
        elif m := _NUMERIC.search(text):
            d, mo, y = (int(g) for g in m.groups())
            if y < 100:
                y += 2000
        elif m := _WORDS.search(text):
            d, y = int(m.group(1)), int(m.group(3))
            mo = _MONTHS.get(m.group(2).casefold(), 0)
        else:
            return None
        if not 1990 <= y <= 2100:
            return None
        return date(y, mo, d).isoformat()
    except ValueError:
        return None
