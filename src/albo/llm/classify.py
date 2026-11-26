"""Classification and extraction with the LLM (Gantt task 33) + validation + routing (task 35)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from ..config import Settings
from ..db import Database
from ..schema import EXTRACTION_TOOL, LLMExtraction, OutputRecord, Provenance
from ..validator import canonicalize, validate
from .client import LLMClient
from .prompts import PROMPT_VERSION, build_system_prompt, build_user_message, load_fewshot

logger = logging.getLogger(__name__)


def _short(value: str | None, width: int = 120) -> str:
    if value is None:
        return "-"
    value = " ".join(value.split())
    return value if len(value) <= width else value[: width - 3] + "..."


def log_extraction(doc_id: str, extraction: LLMExtraction) -> None:
    """What the model classified and extracted: values at INFO, evidence excerpts at DEBUG."""
    logger.info("%s:   %-12s %-22s (conf %.2f)", doc_id, "doc_type", extraction.doc_type.value,
                extraction.type_confidence)
    logger.debug("%s:   %-12s evidence: %s", doc_id, "", _short(extraction.type_evidence, 200))
    for name in ("municipality", "number", "date", "subject"):
        field = getattr(extraction, name)
        logger.info("%s:   %-12s %-22s (conf %.2f)", doc_id, name, _short(field.value), field.confidence)
        logger.debug("%s:   %-12s evidence: %s", doc_id, "", _short(field.evidence, 200))


def classify_document(db: Database, settings: Settings, client: LLMClient, doc_id: str,
                      system_prompt: str | None = None) -> str:
    """Returns 'auto_accepted', 'needs_review' or 'error'."""
    doc = db.get_document(doc_id)
    current = db.current_text(doc_id)
    if doc is None or current is None:
        db.log(doc_id, "classify", "error", "No document or no preprocessed text")
        return "error"
    system_prompt = system_prompt or build_system_prompt(load_fewshot(settings.fewshot_file))
    user_message = build_user_message(current["text"], settings.llm_max_chars)
    logger.info("%s: sending text v%s (%d chars, %d sent) to %s...", doc_id, current["version"],
                len(current["text"]), len(user_message), client.model)
    try:
        result = client.extract(system_prompt, user_message, EXTRACTION_TOOL)
        logger.debug("%s: raw model output %s", doc_id, result.data)
        extraction = canonicalize(LLMExtraction.model_validate(result.data))
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']} (got {_short(repr(err.get('input')), 60)})"
            for err in exc.errors()
        )
        db.log(doc_id, "classify", "error", f"Invalid model output: {exc.error_count()} errors: {details}")
        return "error"
    except Exception as exc:  # API errors, missing tool call, ...
        db.log(doc_id, "classify", "error", f"{type(exc).__name__}: {exc}")
        return "error"

    log_extraction(doc_id, extraction)

    validation = validate(
        extraction, current["text"], municipality=doc["municipality"],
        declared_type=doc["declared_type"], conf_threshold=settings.conf_threshold,
        published_at=doc["published_at"],
    )
    status = "auto_accepted" if validation.ok else "needs_review"
    for issue in validation.issues:
        logger.info("%s:   review %-14s [%s] %s", doc_id, issue.field, issue.code, issue.message)
    record = OutputRecord(
        **extraction.model_dump(), doc_id=doc_id, fields_to_review=validation.fields_to_review, status=status,
        provenance=Provenance(
            model=client.model, prompt_version=PROMPT_VERSION, text_version=current["version"],
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    db.add_prediction(
        doc_id=doc_id, text_version=current["version"], model=client.model, prompt_version=PROMPT_VERSION,
        status=status, record=record.model_dump(mode="json"), issues=validation.as_dicts(),
        usage=result.usage, latency_ms=result.latency_ms,
    )
    tokens = "/".join(str(result.usage.get(k, "?")) for k in ("input_tokens", "output_tokens"))
    db.log(doc_id, "classify", status,
           f"type={record.doc_type.value} issues={len(validation.issues)}"
           f" latency={result.latency_ms / 1000:.1f}s tokens(in/out)={tokens}")
    return status


def classify_all(db: Database, settings: Settings, client: LLMClient, *, force: bool = False,
                 limit: int | None = None) -> dict[str, int]:
    docs = db.documents_to_classify(client.model, PROMPT_VERSION, force=force)
    if limit:
        docs = docs[:limit]
    system_prompt = build_system_prompt(load_fewshot(settings.fewshot_file))
    results = {"auto_accepted": 0, "needs_review": 0, "error": 0}
    logger.info("Classifying %d documents with %s (prompt %s)", len(docs), client.model, PROMPT_VERSION)
    started = time.monotonic()
    for i, doc in enumerate(docs, 1):
        logger.info("[%d/%d] %s (%s)", i, len(docs), doc["doc_id"], doc["municipality"])
        results[classify_document(db, settings, client, doc["doc_id"], system_prompt)] += 1
    logger.info("Classification finished in %.1fs: %s", time.monotonic() - started, results)
    return results
