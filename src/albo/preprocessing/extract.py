"""Text extraction from PDFs (Gantt tasks 45-46): PyMuPDF first, Tesseract OCR as fallback."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from ..config import Settings

logger = logging.getLogger(__name__)


class OCRUnavailable(RuntimeError):
    """Tesseract (binary or language data) is not installed."""


@dataclass
class Extraction:
    text: str
    method: str  # "text" | "ocr" | "mixed"
    n_pages: int
    extractor: str
    warnings: list[str]


def ocr_page(page: "pymupdf.Page", lang: str) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise OCRUnavailable("pytesseract/Pillow not installed") from exc
    pix = page.get_pixmap(dpi=300)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    try:
        return pytesseract.image_to_string(image, lang=lang)
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as exc:
        raise OCRUnavailable(str(exc)) from exc


def extract_pdf(path: Path | str, settings: Settings) -> Extraction:
    pages: list[str] = []
    methods: list[str] = []
    warnings: list[str] = []
    ocr_ok = True
    with pymupdf.open(path) as doc:
        for page in doc:
            text = page.get_text("text", sort=True)
            if len(text.strip()) >= settings.ocr_min_chars_per_page or not ocr_ok:
                logger.debug("page %d: text layer (%d chars)", page.number + 1, len(text.strip()))
                pages.append(text)
                methods.append("text")
                continue
            logger.info("page %d: only %d chars in the text layer, running OCR...", page.number + 1, len(text.strip()))
            try:
                ocr_text = ocr_page(page, settings.ocr_lang)
            except OCRUnavailable as exc:
                ocr_ok = False
                warnings.append(f"OCR unavailable ({exc}); scanned pages kept as-is")
                pages.append(text)
                methods.append("text")
                continue
            if len(ocr_text.strip()) > len(text.strip()):
                pages.append(ocr_text)
                methods.append("ocr")
            else:
                pages.append(text)
                methods.append("text")
    kinds = set(methods)
    method = "mixed" if len(kinds) > 1 else (kinds.pop() if kinds else "text")
    extractor = "pymupdf" + ("+tesseract" if "ocr" in methods else "")
    return Extraction("\n\n".join(pages), method, len(pages), extractor, warnings)
