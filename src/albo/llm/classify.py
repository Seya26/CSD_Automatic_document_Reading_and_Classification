"""Classification and extraction with the LLM (Gantt task 33) + validation + routing (task 35)."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError

from ..config import Settings
from ..db import Database
from ..schema import EXTRACTION_TOOL, LLMExtraction, OutputRecord, Provenance
from ..validator import canonicalize, validate
from .client import LLMClient
from .prompts import PROMPT_VERSION, build_system_prompt, build_user_message, load_fewshot


def classify_document(db: Database, settings: Settings, client: LLMClient, doc_id: str,
                      system_prompt: str | None = None) -> str:
    """Returns 'auto_accepted', 'needs_review' or 'error'."""
    doc = db.get_document(doc_id)
    current = db.current_text(doc_id)
    if doc is None or current is None:
        db.log(doc_id, "classify", "error", "No document or no preprocessed text")
        return "error"
    system_prompt = system_prompt or build_system_prompt(load_fewshot(settings.fewshot_file))
    try:
        result = client.extract(system_prompt, build_user_message(current["text"], settings.llm_max_chars), EXTRACTION_TOOL)
        extraction = canonicalize(LLMExtraction.model_validate(result.data))
    except ValidationError as exc:
        db.log(doc_id, "classify", "error", f"Invalid model output: {exc.error_count()} errors")
        return "error"
    except Exception as exc:  # API errors, missing tool call, ...
        db.log(doc_id, "classify", "error", f"{type(exc).__name__}: {exc}")
        return "error"

    validation = validate(
        extraction, current["text"], municipality=doc["municipality"],
        declared_type=doc["declared_type"], conf_threshold=settings.conf_threshold,
    )
    status = "auto_accepted" if validation.ok else "needs_review"
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
    db.log(doc_id, "classify", status, f"type={record.doc_type.value} issues={len(validation.issues)}")
    return status


def classify_all(db: Database, settings: Settings, client: LLMClient, *, force: bool = False,
                 limit: int | None = None) -> dict[str, int]:
    docs = db.documents_to_classify(client.model, PROMPT_VERSION, force=force)
    if limit:
        docs = docs[:limit]
    system_prompt = build_system_prompt(load_fewshot(settings.fewshot_file))
    results = {"auto_accepted": 0, "needs_review": 0, "error": 0}
    for doc in docs:
        results[classify_document(db, settings, client, doc["doc_id"], system_prompt)] += 1
    return results
