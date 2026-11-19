"""Versioned storage of normalised text with processing logs (Gantt task 29)."""
from __future__ import annotations

import hashlib

from ..config import Settings
from ..db import Database
from .extract import extract_pdf
from .normalize import NORMALIZER_VERSION, normalize_text
from .pseudonymise import pseudonymise


def preprocess_document(db: Database, settings: Settings, doc_id: str) -> str:
    """Extract, normalise, pseudonymise and store the text. Returns 'new', 'unchanged' or 'error'."""
    doc = db.get_document(doc_id)
    if doc is None:
        raise KeyError(doc_id)
    try:
        extraction = extract_pdf(doc["raw_path"], settings)
        text = normalize_text(extraction.text)
        counts: dict[str, int] = {}
        if settings.pseudonymise:
            text, counts = pseudonymise(text)
    except Exception as exc:
        db.log(doc_id, "preprocess", "error", f"{type(exc).__name__}: {exc}")
        return "error"

    for warning in extraction.warnings:
        db.log(doc_id, "preprocess", "warning", warning)
    if not text:
        db.log(doc_id, "preprocess", "error", "No text extracted")
        return "error"

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    current = db.current_text(doc_id)
    if current and current["text_sha256"] == digest and current["normalizer_version"] == NORMALIZER_VERSION:
        db.log(doc_id, "preprocess", "unchanged", f"text identical to version {current['version']}")
        return "unchanged"

    version = db.add_text_version(
        doc_id, extractor=extraction.extractor, extraction_method=extraction.method,
        normalizer_version=NORMALIZER_VERSION, pseudonymised=settings.pseudonymise,
        n_pages=extraction.n_pages, n_chars=len(text), text_sha256=digest, text=text,
    )
    detail = f"v{version} method={extraction.method} pages={extraction.n_pages} chars={len(text)}"
    if counts:
        detail += " pseudonymised=" + ",".join(f"{k}:{v}" for k, v in counts.items())
    db.log(doc_id, "preprocess", "ok", detail)
    return "new"


def preprocess_all(db: Database, settings: Settings, force: bool = False) -> dict[str, int]:
    results = {"new": 0, "unchanged": 0, "error": 0}
    for doc in db.documents_to_preprocess(force=force):
        results[preprocess_document(db, settings, doc["doc_id"])] += 1
    return results
