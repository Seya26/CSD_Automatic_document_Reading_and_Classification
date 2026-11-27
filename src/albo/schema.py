"""Output record (Gantt task 10): type, fields, evidence, confidence, fields to review."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .taxonomy import DocType

SCHEMA_VERSION = "1.4"


class FieldValue(BaseModel):
    value: str | None = None
    evidence: str | None = Field(None, description="Verbatim excerpt of the document containing the value")
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class LLMExtraction(BaseModel):
    """What the LLM must return (see EXTRACTION_TOOL)."""

    doc_type: DocType
    type_evidence: str | None = None
    type_confidence: float = Field(ge=0.0, le=1.0)
    municipality: FieldValue
    number: FieldValue
    date: FieldValue
    subject: FieldValue
    ufficio_responsabile: FieldValue = Field(default_factory=FieldValue)
    firmatario: FieldValue = Field(default_factory=FieldValue)
    organo_emittente: FieldValue = Field(default_factory=FieldValue)
    luoghi: FieldValue = Field(default_factory=FieldValue)
    valida_dal: FieldValue = Field(default_factory=FieldValue)
    valida_al: FieldValue = Field(default_factory=FieldValue)
    prescrizioni: FieldValue = Field(default_factory=FieldValue)
    responsabile_procedimento: FieldValue = Field(default_factory=FieldValue)
    firmatari: FieldValue = Field(default_factory=FieldValue)
    classifica: FieldValue = Field(default_factory=FieldValue)
    organo_deliberante: FieldValue = Field(default_factory=FieldValue)
    esito_votazione: FieldValue = Field(default_factory=FieldValue)


class Provenance(BaseModel):
    model: str | None = None
    prompt_version: str | None = None
    text_version: int | None = None
    created_at: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None


class OutputRecord(LLMExtraction):
    """Final JSON record stored for each document."""

    schema_version: str = SCHEMA_VERSION
    doc_id: str
    fields_to_review: list[str] = Field(default_factory=list)
    status: Literal["auto_accepted", "needs_review", "reviewed"]
    provenance: Provenance = Field(default_factory=Provenance)


def _field_schema(description: str) -> dict:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": {"type": ["string", "null"]},
            "evidence": {"type": ["string", "null"], "description": "Verbatim excerpt, max 200 characters"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["value", "evidence", "confidence"],
    }


EXTRACTION_TOOL = {
    "name": "record_extraction",
    "description": "Record the document type and the extracted fields for the document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": [t.value for t in DocType]},
            "type_evidence": {"type": ["string", "null"], "description": "Verbatim excerpt supporting the type"},
            "type_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "municipality": _field_schema("Municipality that issued the act, as written in the header"),
            "number": _field_schema("Number of the act (not the protocol or page number)"),
            "date": _field_schema("Date of the act (adoption/signature), as written; not the publication date"),
            "ufficio_responsabile": _field_schema("Determinations only: issuing office/service in the header."),
            "firmatario": _field_schema("Determinations and ordinances: actual signatory name in the signature block, not the certificate provider or a cited person."),
            "classifica": _field_schema("Deliberations only: exact archival classification code following Classifica, preserving punctuation."),
            "organo_deliberante": _field_schema("Deliberations only: adopting assembly, verbatim from the act."),
            "esito_votazione": _field_schema("Deliberations only: exact wording of the main adoption vote, excluding separate votes on immediate enforceability."),
            "organo_emittente": _field_schema("Ordinances: office directly below the municipality in the header."),
            "luoghi": _field_schema("Ordinances: one complete operative location per line, including street segments."),
            "valida_dal": _field_schema("Ordinances: explicit first operative date, ISO YYYY-MM-DD."),
            "valida_al": _field_schema("Ordinances: explicit last operative date, ISO YYYY-MM-DD."),
            "prescrizioni": _field_schema("Ordinances: operative measures including their locations, times and exceptions."),
            "responsabile_procedimento": _field_schema("Ordinances: person explicitly named as responsabile del procedimento."),
            "firmatari": _field_schema("Deliberations: one actual signer per line, in the format name | role. Omit the separator if role is absent."),
            "subject": _field_schema("The 'Oggetto' of the act, verbatim"),
        },
        "required": [
            "doc_type", "type_evidence", "type_confidence",
            "ufficio_responsabile", "firmatario", "classifica", "organo_deliberante", "esito_votazione",
            "organo_emittente", "luoghi", "valida_dal", "valida_al", "prescrizioni",
            "responsabile_procedimento", "firmatari",
            "municipality", "number", "date", "subject",
        ],
    },
}
