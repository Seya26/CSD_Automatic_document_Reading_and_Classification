"""Validator of the model output (Gantt task 34).

Every check that fails produces an Issue; the affected field is added to `fields_to_review`
and the document goes to the human review queue."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from rapidfuzz import fuzz

from .schema import LLMExtraction
from .taxonomy import CORE_TYPES, DocType, map_declared_type
from .values import norm_municipality, norm_number, norm_text, parse_date


@dataclass
class Issue:
    field: str  # "doc_type" or one of FIELDS
    code: str
    message: str


@dataclass
class ValidationResult:
    issues: list[Issue]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def fields_to_review(self) -> list[str]:
        seen: list[str] = []
        for issue in self.issues:
            if issue.field not in seen:
                seen.append(issue.field)
        return seen

    def as_dicts(self) -> list[dict]:
        return [asdict(i) for i in self.issues]


# Minimum rapidfuzz partial_ratio (0-100) for an evidence excerpt to count as "found in the text".
# Chosen on a handful of examples: OCR-noisy copies score ~89, altered quotes ~83, invented ones < 55.
# Tune it on the real development set (see the evaluation, task 38).
EVIDENCE_MIN_SIMILARITY = 85


def evidence_in_text(evidence: str, text_norm: str, threshold: int = EVIDENCE_MIN_SIMILARITY) -> bool:
    """Is the evidence (almost) verbatim in the document? Tolerates OCR noise and spacing."""
    ev = norm_text(evidence)
    if not ev:
        return False
    if ev in text_norm:
        return True
    return fuzz.partial_ratio(ev, text_norm) >= threshold


def canonicalize(extraction: LLMExtraction) -> LLMExtraction:
    """Clean values in place: trimmed strings, ISO dates when parsable."""
    for name in ("municipality", "number", "date", "subject"):
        field = getattr(extraction, name)
        if field.value is not None:
            field.value = re.sub(r"\s+", " ", field.value).strip() or None
        if field.evidence is not None:
            field.evidence = field.evidence.strip() or None
    if extraction.date.value:
        iso = parse_date(extraction.date.value)
        if iso:
            extraction.date.value = iso
    return extraction


def validate(extraction: LLMExtraction, text: str, *, municipality: str | None,
             declared_type: str | None, conf_threshold: float) -> ValidationResult:
    issues: list[Issue] = []
    text_norm = norm_text(text)

    def add(field: str, code: str, message: str) -> None:
        issues.append(Issue(field, code, message))

    # --- type ---
    if extraction.doc_type == DocType.UNCERTAIN:
        add("doc_type", "uncertain_type", "The model could not decide the document type")
    if extraction.type_confidence < conf_threshold:
        add("doc_type", "low_confidence", f"Type confidence {extraction.type_confidence:.2f} < {conf_threshold}")
    if extraction.doc_type != DocType.UNCERTAIN:
        if not extraction.type_evidence:
            add("doc_type", "missing_evidence", "No evidence for the document type")
        elif not evidence_in_text(extraction.type_evidence, text_norm):
            add("doc_type", "evidence_not_found", "Type evidence not found in the text")
    declared = map_declared_type(declared_type)
    if declared and extraction.doc_type not in (DocType.UNCERTAIN, declared):
        add("doc_type", "declared_type_mismatch",
            f"Predicted {extraction.doc_type.value}, the notice board declares {declared_type!r}")

    # --- fields (not required for OTHER / UNCERTAIN) ---
    required = extraction.doc_type in CORE_TYPES
    for name in ("municipality", "number", "date", "subject"):
        field = getattr(extraction, name)
        if field.value is None:
            if required:
                add(name, "missing_value", f"{name} is required for {extraction.doc_type.value} but was not found")
            continue
        if field.confidence < conf_threshold:
            add(name, "low_confidence", f"Confidence {field.confidence:.2f} < {conf_threshold}")
        if not field.evidence:
            add(name, "missing_evidence", "Value without evidence")
        elif not evidence_in_text(field.evidence, text_norm):
            add(name, "evidence_not_found", "Evidence not found in the text (possible hallucination)")

    # --- format / consistency checks ---
    number, date_, subject, muni = extraction.number, extraction.date, extraction.subject, extraction.municipality
    if number.value and not norm_number(number.value):
        add("number", "bad_format", f"No digits in number {number.value!r}")
    if date_.value:
        iso = parse_date(date_.value)
        if iso is None:
            add("date", "bad_format", f"Unparsable date {date_.value!r}")
    if subject.value and not 10 <= len(subject.value) <= 800:
        add("subject", "bad_length", f"Subject length {len(subject.value)} outside 10-800 characters")
    if muni.value and municipality:
        if fuzz.ratio(norm_municipality(muni.value), norm_municipality(municipality)) < 85:
            add("municipality", "municipality_mismatch",
                f"Extracted {muni.value!r}, the notice board is {municipality!r}")
    return ValidationResult(issues)
