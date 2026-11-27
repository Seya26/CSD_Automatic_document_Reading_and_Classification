"""Human review queue and final JSON record (Gantt task 35)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .db import Database
from .schema import FieldValue, OutputRecord
from .taxonomy import DETAIL_FIELDS, record_fields


def apply_review(db: Database, *, doc_id: str, prediction_id: int, reviewer: str, action: str,
                 doc_type: str | None = None, values: dict[str, str | None] | None = None) -> None:
    """Store a reviewer decision. action: 'approve' | 'correct' | 'discard'."""
    if action == "discard":
        db.add_review(doc_id=doc_id, prediction_id=prediction_id, reviewer=reviewer, action=action, record=None)
        return
    prediction = db.latest_prediction(doc_id)
    record = json.loads(prediction["record_json"])
    if action == "correct":
        record["doc_type"] = doc_type or record["doc_type"]
        for name in DETAIL_FIELDS.difference(record_fields(record["doc_type"])):
            record.pop(name, None)
        for name in record_fields(record["doc_type"]):
            if name not in (values or {}):
                continue
            new_value = (values or {}).get(name)
            new_value = new_value.strip() if new_value else None
            field = FieldValue.model_validate(record.get(name, {}))
            if new_value != field.value:  # the reviewer changed the value: the old evidence no longer applies
                field = FieldValue(value=new_value, evidence=None, confidence=0.0)
            if field.value:
                field.confidence = 1.0  # confirmed by a human
            record[name] = field.model_dump()
        record["type_confidence"] = 1.0
    record["status"] = "reviewed"
    record["fields_to_review"] = []
    record["provenance"]["reviewer"] = reviewer
    record["provenance"]["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    validated = OutputRecord.model_validate(record)  # guarantees the final record matches the schema
    db.add_review(doc_id=doc_id, prediction_id=prediction_id, reviewer=reviewer, action=action,
                  record=validated.model_dump(mode="json"))


def final_records(db: Database) -> list[dict]:
    """One JSON record per document: reviewed version, or auto-accepted prediction.
    Documents still waiting for review, discarded ones and unpredicted ones are left out."""
    records = []
    for doc in db.list_documents():
        prediction = db.latest_prediction(doc["doc_id"])
        if prediction is None:
            continue
        review = db.latest_review(doc["doc_id"])
        if review and review["prediction_id"] == prediction["id"]:
            if review["action"] == "discard":
                continue
            source = review
        elif prediction["status"] == "auto_accepted":
            source = prediction
        else:
            continue
        record = json.loads(source["record_json"])
        for obsolete in ("numero_documento", "numero_proposta"):
            record.pop(obsolete, None)
        records.append(record)
    return records
