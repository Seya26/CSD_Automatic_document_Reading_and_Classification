"""Text normalisation (Gantt task 28). Bump NORMALIZER_VERSION whenever the rules change."""
from __future__ import annotations

import re
import unicodedata

NORMALIZER_VERSION = "1.0"

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-"})
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "").replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text).translate(_QUOTES)
    # word split across lines by hyphenation: "amministra-\nzione" -> "amministrazione"
    text = re.sub(r"(\w)-\n(?=[a-zà-ÿ])", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
